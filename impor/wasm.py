# -*- coding: utf-8 -*-
"""4.8.0 WebAssembly 后端：Impor → .wasm（手写二进制，不需要 LLVM / wabt / 任何外部编译器）。

定位：与 `cfast` 同一套「纯整数、单返回值」子集，产出标准 WASM 二进制，
可直接在浏览器或 Node 里执行（官网交互 demo 走这条路径）。
C 后端证明性能，WASM 后端证明可移植性 —— 这是第二个后端，不是替代品。

契约（逐条对齐 C 后端，不静默改语义）：
- 整数是 i64；`+ - * 一元-` 经 helper 做 64 位溢出检查，溢出即 trap
  （对应 C 后端的 E1012 退出码 3；WASM 侧 trap 可被 JS 捕获，不会静默给错结果）。
- `%` 结果符号跟随除数（与 Python/Impor 一致）；除数为 0 直接 trap。
- 超出子集的写法抛 `WasmUnsupported`，由调用方明确报错：
  字符串运算、容器、类、闭包、异常、`break`/`continue`、浮点、`and`/`or` 短路。
"""
from . import ast_nodes as A
import struct

VT_I32 = 0x7F
VT_I64 = 0x7E
VT_F64 = 0x7C
EMPTY_BT = 0x40

I64_MIN = -(1 << 63)

# import 函数索引（固定最前面的 6 个）
IMP_BEGIN, IMP_I64, IMP_STR, IMP_NL, IMP_OVF, IMP_F64 = 0, 1, 2, 3, 4, 5
# helper 函数索引（4.9：字符串三件套；4.9.1：分配 + 容器读取）
H_IADD, H_ISUB, H_IMUL, H_INEG, H_IMOD = 6, 7, 8, 9, 10
H_SCAT, H_SREP, H_SEQ = 11, 12, 13
H_ALC, H_LGET, H_DGET = 14, 15, 16     # 8 字节对齐 bump 分配 / 列表读 / 字典取
FIRST_USER = 17
STR_BASE = 1024        # 字符串字面量起点
HEAP_BASE = 131072     # 运行期分配区起点（字面量区之后）
MEM_PAGES = 16         # 16 页 = 1 MiB；列表/字典/对象都从这里分配
# 值域约定（4.9）：WASM 里所有值都放在 i64 里，类型由编译期推断决定解释方式
#   int   → 原样 i64
#   float → f64 的位模式（i64.reinterpret_f64）
#   str   → (ptr << 32) | len，两段各 32 位
#   4.9.1 list/dict → (ptr << 32) | 元素个数；对象 → (ptr << 32)
T_INT, T_FLOAT, T_STR = "int", "float", "str"
T_LIST, T_DICT = "list", "dict"
T_CLS = "cls:"                  # 类实例的类型标签是 "cls:<类名>"

_CMP = {"==": 0x51, "!=": 0x52, "<": 0x53, ">": 0x55, "<=": 0x57, ">=": 0x59}


class WasmUnsupported(Exception):
    def __init__(self, msg, lineno=0, col=0):
        super().__init__(msg)
        self.msg = msg
        self.lineno = lineno
        self.col = col


# ---------- LEB128 / 段编码 ----------
def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if (n == 0 and not (b & 0x40)) or (n == -1 and (b & 0x40)):
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


def vec(items):
    return uleb(len(items)) + b"".join(items)


def name(s):
    """WASM 的字符串（import/export 名字）是 uleb 长度 + UTF-8 字节。"""
    b = s.encode("utf-8")
    return uleb(len(b)) + b


def section(sid, payload):
    return bytes([sid]) + uleb(len(payload)) + payload


def i64c(v):
    return b"\x42" + sleb(v)


def i32c(v):
    return b"\x41" + sleb(v)


def lg(i):
    return b"\x20" + uleb(i)


def ls(i):
    return b"\x21" + uleb(i)


def gg(i):
    return b"\x23" + uleb(i)


def gs(i):
    return b"\x24" + uleb(i)


def call(i):
    return b"\x10" + uleb(i)


def _stmts(n):
    if n is None:
        return []
    if isinstance(n, A.Block):
        return n.stmts
    return [n]


def _walk(node):
    """深度优先遍历 AST 子树（含 list / tuple / 嵌套节点），产出每个语句或表达式节点。"""
    if node is None:
        return
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
        return
    if isinstance(node, tuple):
        for x in node:
            yield from _walk(x)
        return
    if isinstance(node, A.AST):
        yield node
        for v in vars(node).values():
            if isinstance(v, (A.AST, list, tuple)):
                yield from _walk(v)


def _collect_assigns(node, out):
    """收集作用域内被赋值的名字（不进入嵌套 Def/Class）。"""
    if node is None:
        return
    if isinstance(node, list):
        for s in node:
            _collect_assigns(s, out)
        return
    if isinstance(node, (A.Assign, A.CompoundAssign)):
        out.add(node.name)
    elif isinstance(node, A.Block):
        for s in node.stmts:
            _collect_assigns(s, out)
    elif isinstance(node, A.If):
        _collect_assigns(node.body, out)
        for _c, b in node.elseifs:
            _collect_assigns(b, out)
        _collect_assigns(node.elseb, out)
    elif isinstance(node, A.While):
        _collect_assigns(node.body, out)
    elif isinstance(node, A.For):
        if node.var:
            out.add(node.var)
        _collect_assigns(node.body, out)


class _Compiler:
    def __init__(self, ast):
        self.ast = ast
        self.types = []           # [(params, results)]
        self.type_map = {}
        self.funcs = []           # [{type, locals, body}]
        self.globals = {}         # name -> global idx
        self.strings = []         # [(text, offset)]
        self.str_off = STR_BASE
        self.defs = []
        self.func_index = {}
        # 4.9.1：类登记表。classes[name] = {
        #   "node": 定义节点, "fields": [名字...], "ftypes": {字段: 类型},
        #   "methods": {方法名: Def}, "midx": {方法名: 函数索引} }
        self.classes = {}
        self.class_order = []
        self.in_func = False
        self.locals = {}
        self.nparams = 0
        self.locals_decl = []
        self.tmp_count = 0
        # 块嵌套栈：'brk'（break 目标）/'cont'（continue 目标）/'if'/'blk'
        # br 的立即数是「相对当前位置的嵌套深度」，所以 emit 时必须知道
        # 每一层开着的块是什么 —— 靠这个栈算深度，不靠人肉数缩进。
        self.ctrl = []
        # 类型推断产物（4.9）
        self.vtype = {}
        self.sig = {}
        self.fixed = set()
        self.cur_params = {}
        # 4.9.1：列表/字典变量的元素类型（下标读靠它决定槽的解释方式）
        self.seq_elem = {}

    # ---------- 类型 ----------
    def type_idx(self, params, results):
        key = (tuple(params), tuple(results))
        if key not in self.type_map:
            self.type_map[key] = len(self.types)
            self.types.append(key)
        return self.type_map[key]

    # ---------- 预扫描 ----------
    def scan(self):
        root = _stmts(self.ast)
        for s in root:
            if isinstance(s, A.Def):
                if getattr(s, "is_async", False):
                    raise WasmUnsupported("async 函数不在 WASM 后端内", s.lineno, s.col)
                if getattr(s, "type_params", None):
                    # 泛型要按调用点单态化（同一函数可能同时有 int 实例和 str 实例），
                    # WASM 的单签名 i64 表达不了多态；这里明确拒绝，交给 C 后端。
                    raise WasmUnsupported(
                        "泛型函数 %s<%s> 不在 WASM 后端内（需单态化，请用 C 后端）"
                        % (getattr(s, "name", "?"), ", ".join(list(s.type_params))),
                        s.lineno, s.col)
                self.defs.append(s)
            elif isinstance(s, A.ClassDef):
                self._collect_class(s)
            self._scan_strings(s)
        gnames = set()
        for s in root:
            if isinstance(s, (A.Def, A.ClassDef)):
                continue
            _collect_assigns(s, gnames)
        self.globals = {n: i for i, n in enumerate(sorted(gnames))}
        for i, fn in enumerate(self.defs):
            self.func_index[fn.name] = FIRST_USER + i
        # 4.9.1：方法占尾部索引（顶层函数之后、main 之前）
        base = FIRST_USER + len(self.defs)
        for cn in self.class_order:
            info = self.classes[cn]
            for mname in info["morder"]:
                info["midx"][mname] = base
                base += 1

    def _collect_class(self, s):
        """登记一个类：字段顺序（按 __init__ 里 set self.x 的书写顺序）+ 方法表。

        WASM 的对象是「裸内存块」：字段按下标固定布局，每个字段 8 字节。
        没有运行期字段表，所以字段集合必须编译期确定 —— 这也是要求所有字段
        都在 __init__ 里出现的原因。
        """
        name = s.cls_name
        if name in self.classes:
            raise WasmUnsupported("重复的类定义：%s" % name, s.lineno, s.col)
        if getattr(s, "base_name", None):
            raise WasmUnsupported(
                "WASM 后端不支持继承（class %s 继承 %s）" % (name, s.base_name),
                s.lineno, s.col)
        if getattr(s, "interfaces", None):
            raise WasmUnsupported(
                "WASM 后端不支持接口实现（class %s）" % name, s.lineno, s.col)
        fields = []
        methods = {}
        morder = []
        body = s.body.stmts if isinstance(s.body, A.Block) else (s.body or [])
        for m in body:
            if isinstance(m, A.Def):
                if getattr(m, "type_params", None):
                    raise WasmUnsupported(
                        "方法 %s.%s 是泛型（需单态化，请用 C 后端）" % (name, m.name),
                        m.lineno, m.col)
                methods[m.name] = m
                morder.append(m.name)
                for node in _walk(m.body):
                    if (isinstance(node, A.AttrSet)
                            and isinstance(node.obj_expr, A.Var)
                            and node.obj_expr.name == "self"):
                        if node.attr_name not in fields:
                            fields.append(node.attr_name)
            elif isinstance(m, (A.Assign, A.CompoundAssign)):
                raise WasmUnsupported(
                    "WASM 后端不支持类体里的类属性（%s.%s），请改成 self 字段"
                    % (name, getattr(m, "name", "?")), s.lineno, s.col)
            elif isinstance(m, A.Block):
                continue
        self.classes[name] = {
            "node": s, "fields": fields, "ftypes": {f: T_INT for f in fields},
            "methods": methods, "morder": morder, "midx": {},
        }
        self.class_order.append(name)

    def _scan_strings(self, node):
        """通用遍历：所有 Str 节点（含字典键、下标、f-string 片段）都要登记。"""
        if node is None:
            return
        for n in _walk(node):
            if isinstance(n, A.Str):
                if n.val not in [t for t, _o in self.strings]:
                    self.strings.append((n.val, self.str_off))
                    self.str_off += len(n.val.encode("utf-8"))

    def str_offset(self, text):
        for t, off in self.strings:
            if t == text:
                return off
        raise WasmUnsupported("内部错误：字符串未收集：%r" % text)

    # ---------- 类型推断（4.9：float / str 需要）----------
    def infer_scan(self):
        """两遍推断：变量类型 + 用户函数签名。

        WASM 只有一个 i64 值槽，float 存位模式、str 存 (ptr<<32)|len，
        所以「这个槽按什么解释」必须在编译期定死。策略是保守的：
        同名变量全文件必须同类型、同一函数在所有调用点的实参类型必须一致，
        否则明确拒绝（而不是猜一个然后算错）。
        """
        root = _stmts(self.ast)
        defs = [s for s in root if isinstance(s, A.Def)]
        top_body = [s for s in root if not isinstance(s, (A.Def, A.ClassDef))]
        # 函数签名先给一个「整数占位」，让递归调用（fib）在第一轮就能推断；
        # 后续轮若真身是 float/str，允许一次性升级，再之后定型。
        self.sig = {d.name: ([T_INT] * len(d.params), T_INT) for d in defs}
        self.fixed = set()
        self.cur_params = {}

        # for 循环变量恒为 int
        for s in root:
            if isinstance(s, A.Def):
                for node in _walk(s.body):
                    if isinstance(node, A.For) and node.var:
                        self._set_type(node.var, T_INT)
            elif not isinstance(s, A.ClassDef):
                for node in _walk(s):
                    if isinstance(node, A.For) and node.var:
                        self._set_type(node.var, T_INT)

        # 迭代到不动点：赋值类型 + 返回类型 + 调用点实参类型
        for _round in range(6):
            changed = False
            self.cur_params = {}
            for b in top_body:
                changed |= self._collect_types(b)
            for d in defs:
                self.cur_params = dict(zip(d.params, self.sig[d.name][0]))
                changed |= self._collect_types(d.body)
                t = None
                for node in _walk(d.body):
                    if isinstance(node, A.Return) and node.expr is not None:
                        t2 = self._infer(node.expr)
                        if t2:
                            t = t2 if t is None else (t if t == t2 else None)
                if t:
                    changed |= self._set_sig(d.name, (self.sig[d.name][0], t))
            # 调用点的实参类型 → 形参类型
            self.cur_params = {}
            for b in list(top_body) + [d.body for d in defs]:
                for node in _walk(b):
                    if isinstance(node, A.Call) and isinstance(node.callee, A.Var):
                        fn = node.callee.name
                        if fn not in self.sig:
                            continue
                        ts = [self._infer(a) for a in node.args]
                        if all(ts):
                            changed |= self._set_sig(
                                fn, (ts, self.sig[fn][1]))
            if not changed:
                break
        # 4.9.1：类成员（字段类型 / 方法返回类型）。每个方法用独立作用域推断，
        # 不同方法里的同名局部变量不会互相污染全局 vtype。
        self._infer_class_members()
        self.cur_params = {}
        self.fixed = set(self.vtype) | set(self.sig)

    def _infer_class_members(self):
        for cn in self.class_order:
            info = self.classes[cn]
            info.setdefault("ret", {})
            info.setdefault("params", {})
            for mname in info["morder"]:
                m = info["methods"][mname]
                saved_v, saved_p = self.vtype, self.cur_params
                scope = {"self": T_CLS + cn}
                for p in m.params:
                    scope[p] = T_INT
                self.vtype, self.cur_params = scope, dict(scope)
                self._collect_types(m.body)
                if mname == "__init__":
                    for node in _walk(m.body):
                        if (isinstance(node, A.AttrSet)
                                and isinstance(node.obj_expr, A.Var)
                                and node.obj_expr.name == "self"):
                            t = self._infer(node.value)
                            if t is None:
                                raise WasmUnsupported(
                                    "字段 %s.%s 的类型推断不出来（WASM 后端要求可定类型）"
                                    % (cn, node.attr_name), node.lineno, node.col)
                            info["ftypes"][node.attr_name] = t
                rets = []
                for node in _walk(m.body):
                    if isinstance(node, A.Return) and node.expr is not None:
                        rets.append(self._infer(node.expr))
                if rets:
                    rt = self._unify(rets)
                    if rt is None:
                        raise WasmUnsupported(
                            "方法 %s.%s 的多个 return 类型不一致，WASM 后端要求单态"
                            % (cn, mname), m.lineno, m.col)
                else:
                    rt = T_INT
                info["ret"][mname] = rt
                info["params"][mname] = [T_INT for _p in m.params]
                self.vtype, self.cur_params = saved_v, saved_p

    def _collect_types(self, b):
        changed = False
        for node in _walk(b):
            if isinstance(node, (A.Assign, A.CompoundAssign)):
                if isinstance(node, A.CompoundAssign):
                    t = self.vtype.get(node.name) or self._infer(node.expr)
                else:
                    t = self._infer(node.expr)
                if t:
                    changed |= self._set_type(node.name, t)
                if (isinstance(node, A.Assign) and t in (T_LIST, T_DICT)
                        and isinstance(node.expr, (A.ListLit, A.DictLit))):
                    e = self._elem_type(node.expr)
                    if e is not None:
                        self.seq_elem.setdefault(node.name, e)
        return changed

    def _set_type(self, name, t):
        old = self.vtype.get(name)
        if old is None:
            self.vtype[name] = t
            return True
        if old != t:
            # 占位推断出的 int 允许一次性升级成 float/str；定型后不再容忍
            if old == T_INT and t in (T_FLOAT, T_STR) and name not in self.fixed:
                self.vtype[name] = t
                return True
            # int 可以提升进 float 槽（生成侧会自动 convert）
            if old == T_FLOAT and t == T_INT:
                return False
            raise WasmUnsupported(
                "变量 %s 在 WASM 后端里必须类型稳定（%s → %s）；"
                "请在 C 后端跑，或拆成两个变量" % (name, old, t))
        return False

    def _set_sig(self, name, sig):
        old = self.sig.get(name)
        if old is None:
            self.sig[name] = sig
            return True
        p_old, r_old = old
        p_new, r_new = sig
        if len(p_old) != len(p_new):
            raise WasmUnsupported("函数 %s 的实参个数在 WASM 后端里不一致" % name)
        merged = []
        changed = False
        for a, b in zip(p_old, p_new):
            if a == b:
                merged.append(a)
            elif a == T_INT and b in (T_FLOAT, T_STR):
                merged.append(b)
                changed = True
            elif {a, b} == {T_FLOAT, T_INT}:
                merged.append(T_FLOAT)
            else:
                raise WasmUnsupported(
                    "函数 %s 的参数类型冲突（%s vs %s），WASM 后端要求单态"
                    % (name, a, b))
        if r_old == r_new:
            r = r_old
        elif r_old == T_INT and r_new in (T_FLOAT, T_STR):
            r = r_new
            changed = True
        elif {r_old, r_new} == {T_FLOAT, T_INT}:
            r = T_FLOAT
        else:
            raise WasmUnsupported(
                "函数 %s 的返回类型冲突（%s vs %s）" % (name, r_old, r_new))
        self.sig[name] = (merged, r)
        return changed

    def _infer(self, e):
        if isinstance(e, A.Num):
            return T_FLOAT if isinstance(e.val, float) else T_INT
        if isinstance(e, (A.Bool,)):
            return T_INT
        if isinstance(e, A.Str):
            return T_STR
        if isinstance(e, A.Var):
            if e.name in self.cur_params:
                return self.cur_params[e.name]
            return self.vtype.get(e.name)
        if isinstance(e, A.BinOp):
            if e.op in ("and", "or", "==", "!=", "<", ">", "<=", ">="):
                return T_INT
            l, r = self._infer(e.left), self._infer(e.right)
            if e.op == "+":
                if l == T_STR or r == T_STR:
                    return T_STR if (l == T_STR and r == T_STR) else None
            elif e.op == "*":
                if l == T_STR and r == T_INT:
                    return T_STR
                if l == T_STR or r == T_STR:
                    return None
            if l is None or r is None:
                return None
            if l == T_FLOAT or r == T_FLOAT:
                return T_FLOAT
            if l == T_INT and r == T_INT:
                return T_INT
            return None
        if isinstance(e, A.UnaryOp):
            if e.op == "not":
                return T_INT
            return self._infer(e.child)
        if isinstance(e, A.TernaryExpr):
            a, b = self._infer(e.true_expr), self._infer(e.false_expr)
            return a if a == b else None
        if isinstance(e, A.Call):
            name = e.callee.name if isinstance(e.callee, A.Var) else None
            if name in ("abs", "min", "max"):
                ts = [self._infer(x) for x in e.args]
                return T_FLOAT if T_FLOAT in ts else (
                    T_INT if ts and all(t == T_INT for t in ts) else None)
            if name == "len":
                return T_INT
            if name == "str":
                return T_STR
            if name == "int":
                return T_INT
            if name in self.sig:
                return self.sig[name][1]
            return None
        # 4.9.1：容器、类实例、have
        if isinstance(e, A.ListLit):
            return T_LIST
        if isinstance(e, A.DictLit):
            return T_DICT
        if isinstance(e, A.NewInstance):
            return T_CLS + e.cls_name
        if isinstance(e, A.TypeTest):
            return T_INT          # bool 在 WASM 里就是 int
        if isinstance(e, A.IndexGet):
            ct = self._infer(e.container)
            if ct == T_LIST:
                return self._elem_type(e.container)
            if ct == T_DICT:
                return self._elem_type(e.container)
            return None
        if isinstance(e, A.AttrGet):
            ot = self._infer(e.obj_expr)
            if ot and ot.startswith(T_CLS):
                cn = ot[len(T_CLS):]
                info = self.classes.get(cn)
                if info is None:
                    return None
                if e.attr_name in info["methods"]:
                    return info["ret"].get(e.attr_name, T_INT)
                if e.attr_name in info["ftypes"]:
                    return info["ftypes"][e.attr_name]
            return None
        if isinstance(e, A.Call) and isinstance(e.callee, A.AttrGet):
            ot = self._infer(e.callee.obj_expr)
            if ot and ot.startswith(T_CLS):
                info = self.classes.get(ot[len(T_CLS):])
                if info and e.callee.attr_name in info["methods"]:
                    return info["ret"].get(e.callee.attr_name, T_INT)
        return None

    def _elem_type(self, container):
        """容器的元素/值类型：字面量直接算，变量看赋值时记下的序列元素类型。"""
        if isinstance(container, A.ListLit):
            return self._unify([self._infer(x) for x in container.items])
        if isinstance(container, A.DictLit):
            return self._unify([self._infer(v) for _k, v in container.kv])
        if isinstance(container, A.Var):
            return self.seq_elem.get(container.name, T_INT)
        return T_INT

    def _unify(self, ts):
        if not ts:
            return T_INT
        if any(t is None for t in ts):
            return None
        if all(t == ts[0] for t in ts):
            return ts[0]
        if set(ts) <= {T_INT, T_FLOAT}:
            return T_FLOAT
        return None

    def _type_of(self, e):
        t = self._infer(e)
        if t is None:
            raise WasmUnsupported("无法推断表达式的类型（WASM 后端要求可定类型）",
                                  getattr(e, "lineno", 0), getattr(e, "col", 0))
        return t

    # ---------- 变量 ----------
    def _get(self, name):
        if self.in_func and name in self.locals:
            return lg(self.locals[name])
        if name in self.globals:
            return gg(self.globals[name])
        raise WasmUnsupported("变量不在当前作用域：%s" % name)

    def _set(self, name, scope=None):
        if self.in_func and scope != "global" and name in self.locals:
            return ls(self.locals[name])
        if name in self.globals:
            return gs(self.globals[name])
        raise WasmUnsupported("变量不在当前作用域：%s" % name)

    def _promote(self, e, want):
        """按目标槽类型做隐式提升：int → float 槽时转成 f64 位模式。"""
        b = self.expr(e)
        if want == T_FLOAT and self._infer(e) == T_INT:
            return b + b"\xb9" + b"\xbd"
        return b

    def _store(self, name, e, scope=None):
        return self._promote(e, self.vtype.get(name)) + self._set(name, scope)

    def _new_tmp(self):
        idx = self.nparams + sum(c for c, _t in self.locals_decl) + self.tmp_count
        self.tmp_count += 1
        return idx

    def _br(self, tag):
        """跳到最近的 tag 块：深度 = 从栈顶往下数第几个。"""
        depth = 0
        for t in reversed(self.ctrl):
            if t == tag:
                return b"\x0c" + uleb(depth)
            depth += 1
        raise WasmUnsupported("break/continue 不在循环体内" if tag == "brk"
                              else "continue 不在循环体内")

    # ---------- 表达式（压 i64：int 原样 / float 位模式 / str 打包）----------
    def expr(self, e):
        if isinstance(e, A.Num):
            if isinstance(e.val, float):
                return i64c(struct.unpack("<Q", struct.pack("<d", float(e.val)))[0])
            return i64c(int(e.val))
        if isinstance(e, A.Bool):
            return i64c(1 if e.val else 0)
        if isinstance(e, A.Var):
            return self._get(e.name)
        if isinstance(e, A.Str):
            b = e.val.encode("utf-8")
            return i64c((self.str_offset(e.val) << 32) | len(b))
        if isinstance(e, A.BinOp):
            if e.op in ("and", "or"):
                # 短路：左操作数为真(and)/为假(or) 时不再求右操作数。
                # 结果按 Impor 语义是布尔，落回 i64 的 0/1。
                left = self.expr(e.left)
                t = self._new_tmp()
                probe = left + ls(t) + lg(t) + i64c(0) + b"\x52"
                if e.op == "and":
                    return (probe + b"\x04" + bytes([VT_I64])
                            + self.cond(e.right) + b"\xac"
                            + b"\x05" + i64c(0) + b"\x0b")
                return (probe + b"\x04" + bytes([VT_I64])
                        + i64c(1)
                        + b"\x05" + self.cond(e.right) + b"\xac" + b"\x0b")
            t = self._type_of(e)
            if e.op in _CMP:
                tl = self._type_of(e.left)
                tr = self._type_of(e.right)
                if T_FLOAT in (tl, tr):
                    return self._float_binop(e)
                if T_STR in (tl, tr):
                    if e.op not in ("==", "!="):
                        raise WasmUnsupported("字符串只支持 == / != 比较",
                                              getattr(e, "lineno", 0), getattr(e, "col", 0))
                    q = self.expr(e.left) + self.expr(e.right) + call(H_SEQ)
                    return q if e.op == "==" else q + b"\x50" + b"\xac"
                return (self.expr(e.left) + self.expr(e.right)
                        + bytes([_CMP[e.op]]) + b"\xac")
            if t == T_STR:
                return self._str_binop(e)
            if t == T_FLOAT:
                return self._float_binop(e)
            l = self.expr(e.left)
            r = self.expr(e.right)
            if e.op == "+":
                return l + r + call(H_IADD)
            if e.op == "-":
                return l + r + call(H_ISUB)
            if e.op == "*":
                return l + r + call(H_IMUL)
            if e.op == "/":
                return l + r + b"\x7f"
            if e.op == "%":
                return l + r + call(H_IMOD)
            raise WasmUnsupported("运算符不在 WASM 后端内：%s" % e.op,
                                  getattr(e, "lineno", 0), getattr(e, "col", 0))
        if isinstance(e, A.UnaryOp):
            t = self._type_of(e)
            c = self.expr(e.child)
            if e.op == "-":
                if t == T_FLOAT:
                    return c + b"\xbf" + b"\x9a" + b"\xbd"     # f64.neg
                return c + call(H_INEG)
            if e.op == "not":
                return self.cond(e.child) + b"\x45" + b"\xac"
            if e.op == "+":
                return c
            raise WasmUnsupported("一元运算不在 WASM 后端内：%s" % e.op,
                                  getattr(e, "lineno", 0), getattr(e, "col", 0))
        if isinstance(e, A.TernaryExpr):
            return (self.cond(e.cond) + b"\x04" + bytes([VT_I64])
                    + self.expr(e.true_expr) + b"\x05"
                    + self.expr(e.false_expr) + b"\x0b")
        if isinstance(e, A.Call):
            name = e.callee.name if isinstance(e.callee, A.Var) else None
            if name == "abs":
                x = self.expr(e.args[0])
                t = self._new_tmp()
                return (x + ls(t) + lg(t) + i64c(0) + b"\x53"
                        + b"\x04" + bytes([VT_I64]) + lg(t) + call(H_INEG)
                        + b"\x05" + lg(t) + b"\x0b")
            if name in ("min", "max"):
                a, b2 = self.expr(e.args[0]), self.expr(e.args[1])
                t = self._new_tmp()
                op = b"\x53" if name == "min" else b"\x55"
                return (a + ls(t) + lg(t) + b2 + op
                        + b"\x04" + bytes([VT_I64]) + lg(t) + b"\x05" + b2 + b"\x0b")
            if name == "len":
                # str 值的低 32 位就是长度，直接掩出来即可
                tlen = self._new_tmp()
                return (self.expr(e.args[0]) + ls(tlen)
                        + lg(tlen) + i64c(0xFFFFFFFF) + b"\x83")
            if isinstance(e.callee, A.AttrGet):
                ot = self._infer(e.callee.obj_expr)
                if ot and ot.startswith(T_CLS):
                    return self._method_call(e, ot[len(T_CLS):])
                raise WasmUnsupported(
                    "只支持对象的实例方法调用（obj.方法(...)）；"
                    "其余属性调用不在 WASM 后端内", e.lineno, e.col)
            if name is None or name not in self.func_index:
                raise WasmUnsupported("调用目标不在 WASM 后端内：%s"
                                      % (name or type(e.callee).__name__),
                                      getattr(e, "lineno", 0), getattr(e, "col", 0))
            wants = (self.sig.get(name) or ([T_INT] * len(e.args), T_INT))[0]
            parts = []
            for i, a in enumerate(e.args):
                parts.append(self._promote(a, wants[i] if i < len(wants) else None))
            return b"".join(parts) + call(self.func_index[name])
        # 4.9.1：容器 / 对象 / have
        if isinstance(e, A.ListLit):
            return self._list_lit(e)
        if isinstance(e, A.DictLit):
            return self._dict_lit(e)
        if isinstance(e, A.IndexGet):
            return self._index_get(e)
        if isinstance(e, A.NewInstance):
            return self._new_instance(e)
        if isinstance(e, A.AttrGet):
            return self._attr_get(e)
        if isinstance(e, A.TypeTest):
            return i64c(self._have(e))
        raise WasmUnsupported("无法生成表达式：%s" % type(e).__name__,
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    # ---------- 4.9.1：容器与对象 ----------
    # 布局约定（都是 8 字节一格，内存里就是 i64 数组）：
    #   list  : [elem0, elem1, ...]              句柄 = (ptr<<32) | 元素个数
    #   dict  : [(key_str, value), ...]          句柄 = (ptr<<32) | 键值对数
    #   object: [field0, field1, ...]            句柄 = (ptr<<32) | 0
    # 分配只 bump 不回收 —— probe 级别的短生命周期程序够用；长循环里反复
    # 建容器会耗尽 1 MiB 堆，这是明确写在文档里的限制。
    def _alloc(self, nbytes):
        t = self._new_tmp()
        # 临时量里存的是「句柄形态」（ptr<<32），这样 _addr/_pack 直接可用
        return (i32c(int(nbytes)) + call(H_ALC) + b"\xac" + i64c(32) + b"\x86"
                + ls(t), t)

    def _pack(self, ptr_tmp, n):
        """(ptr<<32) | n —— 指针已经在高 32 位，直接或上计数。"""
        return lg(ptr_tmp) + i64c(n) + b"\x84"

    def _ptr(self, t):
        """把临时量里的句柄取成 i32 指针（压栈 i32）。"""
        return lg(t) + i64c(32) + b"\x88" + b"\xa7"

    def _addr_of(self, t, off):
        """句柄临时量 → 元素地址（压栈 i32）：ptr + off。"""
        return self._ptr(t) + i32c(off) + b"\x6a"

    def _addr(self, ptr_tmp, off):
        """把栈上的 i64（对象句柄/指针临时量）的高 32 位取出来当 i32 地址。"""
        base = ptr_tmp
        if isinstance(base, bytes):
            return base + i64c(32) + b"\x88" + b"\xa7" + i32c(off) + b"\x6a"
        raise TypeError("_addr 需要已压栈的句柄字节")

    def _list_lit(self, e):
        n = len(e.items)
        elem = self._unify([self._infer(x) for x in e.items])
        if n and elem is None:
            raise WasmUnsupported("列表元素类型不一致（WASM 后端要求同构列表）",
                                  e.lineno, e.col)
        pre, t = self._alloc(8 * max(n, 1))
        for i, it in enumerate(e.items):
            pre += (self._addr_of(t, 8 * i)
                    + self._promote(it, elem) + b"\x37\x03\x00")
        return pre + self._pack(t, n)

    def _dict_lit(self, e):
        n = len(e.kv)
        for k, _v in e.kv:
            if not isinstance(k, A.Str):
                raise WasmUnsupported("WASM 后端的字典键只支持字符串字面量",
                                      e.lineno, e.col)
        valt = self._unify([self._infer(v) for _k, v in e.kv])
        if n and valt is None:
            raise WasmUnsupported("字典的值类型不一致（WASM 后端要求同构字典）",
                                  e.lineno, e.col)
        pre, t = self._alloc(16 * max(n, 1))
        for i, (k, v) in enumerate(e.kv):
            key = i64c((self.str_offset(k.val) << 32) | len(k.val.encode("utf-8")))
            pre += self._addr_of(t, 16 * i) + key + b"\x37\x03\x00"
            pre += (self._addr_of(t, 16 * i + 8)
                    + self._promote(v, valt) + b"\x37\x03\x00")
        return pre + self._pack(t, n)

    def _index_get(self, e):
        ct = self._infer(e.container)
        if ct == T_LIST:
            if self._infer(e.idx) == T_STR:
                raise WasmUnsupported("列表下标必须是整数", e.lineno, e.col)
            return self.expr(e.container) + self.expr(e.idx) + call(H_LGET)
        if ct == T_DICT:
            if self._infer(e.idx) != T_STR:
                raise WasmUnsupported(
                    "WASM 后端的字典只支持字符串键（d[\"k\"]）", e.lineno, e.col)
            return self.expr(e.container) + self.expr(e.idx) + call(H_DGET)
        raise WasmUnsupported("下标访问只支持 list / dict（WASM 后端）",
                              e.lineno, e.col)

    def _field_off(self, cn, attr):
        info = self.classes.get(cn)
        if info is None:
            raise WasmUnsupported("类 %s 不在 WASM 后端内" % cn)
        if attr not in info["ftypes"]:
            raise WasmUnsupported("类 %s 没有字段 %s" % (cn, attr))
        return 8 * info["fields"].index(attr)

    def _attr_get(self, e):
        ot = self._infer(e.obj_expr)
        if not (ot and ot.startswith(T_CLS)):
            raise WasmUnsupported("属性读取只支持对象字段（WASM 后端）",
                                  e.lineno, e.col)
        cn = ot[len(T_CLS):]
        info = self.classes.get(cn) or {}
        if e.attr_name in info.get("methods", {}):
            raise WasmUnsupported(
                "方法 %s 不能当值取；WASM 后端只支持 obj.%s(...) 直接调用"
                % (e.attr_name, e.attr_name), e.lineno, e.col)
        off = self._field_off(cn, e.attr_name)
        return (self.expr(e.obj_expr) + i64c(32) + b"\x88" + b"\xa7"
                + i32c(off) + b"\x6a" + b"\x29\x03\x00")

    def _new_instance(self, e):
        cn = e.cls_name
        info = self.classes.get(cn)
        if info is None:
            raise WasmUnsupported("类 %s 不在 WASM 后端内（未定义，或用了继承/接口）"
                                  % cn, e.lineno, e.col)
        nf = len(info["fields"])
        pre, t = self._alloc(8 * max(nf, 1))
        for i in range(nf):
            pre += (self._addr_of(t, 8 * i)
                    + i64c(0) + b"\x37\x03\x00")
        inst = lg(t)                     # t 里已经是 ptr<<32 的句柄
        init = info["methods"].get("__init__")
        if init is None:
            if e.args:
                raise WasmUnsupported("类 %s 没有 __init__，但 new 传了参数" % cn,
                                      e.lineno, e.col)
            return pre + inst
        if len(e.args) != len(init.params):
            raise WasmUnsupported(
                "new %s 的实参个数不对：要 %d 个，给了 %d 个"
                % (cn, len(init.params), len(e.args)), e.lineno, e.col)
        wants = info["params"].get("__init__") or [T_INT] * len(e.args)
        parts = [inst]
        for i, a in enumerate(e.args):
            parts.append(self._promote(a, wants[i] if i < len(wants) else None))
        parts.append(call(info["midx"]["__init__"]))
        parts.append(b"\x1a")                 # init 返回 void，丢弃
        parts.append(lg(t))                   # 句柄（ptr<<32）
        return pre + b"".join(parts)

    def _method_call(self, e, cn):
        info = self.classes.get(cn)
        if info is None:
            raise WasmUnsupported("类 %s 不在 WASM 后端内" % cn, e.lineno, e.col)
        name = e.callee.attr_name
        if name not in info["methods"]:
            raise WasmUnsupported("类 %s 没有方法 %s" % (cn, name), e.lineno, e.col)
        m = info["methods"][name]
        if len(e.args) != len(m.params):
            raise WasmUnsupported(
                "%s.%s 的实参个数不对：要 %d 个，给了 %d 个"
                % (cn, name, len(m.params), len(e.args)), e.lineno, e.col)
        wants = info["params"].get(name) or [T_INT] * len(e.args)
        parts = [self.expr(e.callee.obj_expr)]
        for i, a in enumerate(e.args):
            parts.append(self._promote(a, wants[i] if i < len(wants) else None))
        parts.append(call(info["midx"][name]))
        return b"".join(parts)

    def _have(self, e):
        """`x have <类型名>`：WASM 的槽不带运行期 tag，只能按编译期类型折叠。

        这是与解释器的口径差（解释器是运行期真判）：在 WASM 后端里，
        凡是编译期已定类型的表达式，have 就是常量 —— 但类型是**精确**推断的
        （int 不会冒充 float，str 不会冒充 int），所以对同一份源码结论一致。
        """
        t = self._infer(e.expr)
        want = e.type_name
        if want == "bool":
            return 1 if self._is_bool_expr(e.expr) else 0
        if want in (T_INT, T_FLOAT, T_STR, T_LIST, T_DICT):
            if want == T_INT:
                return 1 if (t == T_INT and not self._is_bool_expr(e.expr)) else 0
            return 1 if t == want else 0
        if want in ("object", "instance"):
            return 1 if (t and t.startswith(T_CLS)) else 0
        if want in self.classes:
            return 1 if t == T_CLS + want else 0
        raise WasmUnsupported("have 的类型名未知：%s" % want,
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    _F64_OPS = {"+": b"\xa0", "-": b"\xa1", "*": b"\xa2", "/": b"\xa3"}
    _F64_CMP = {"==": b"\x61", "!=": b"\x62", "<": b"\x63",
                ">": b"\x64", "<=": b"\x65", ">=": b"\x66"}

    def _f64(self, e):
        """把表达式求值成 f64（栈上 f64）：int 自动提升，float 解位模式。"""
        t = self._type_of(e)
        b = self.expr(e)
        if t == T_FLOAT:
            return b + b"\xbf"          # f64.reinterpret_i64
        if t == T_INT:
            return b + b"\xb9"          # f64.convert_i64_s
        raise WasmUnsupported("字符串不能参与算术运算",
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    def _float_binop(self, e):
        if e.op in self._F64_OPS:
            return (self._f64(e.left) + self._f64(e.right)
                    + self._F64_OPS[e.op] + b"\xbd")     # 结果回 i64 位模式
        if e.op in self._F64_CMP:
            return (self._f64(e.left) + self._f64(e.right) + self._F64_CMP[e.op]
                    + b"\xac")                               # 0/1 抬成 i64
        raise WasmUnsupported("浮点运算符不在 WASM 后端内：%s" % e.op,
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    def _str_binop(self, e):
        if e.op == "+":
            return self.expr(e.left) + self.expr(e.right) + call(H_SCAT)
        if e.op == "*":
            tl = self._type_of(e.left)
            tr = self._type_of(e.right)
            if tl == T_STR and tr == T_INT:
                return self.expr(e.left) + self.expr(e.right) + call(H_SREP)
            if tl == T_INT and tr == T_STR:
                return self.expr(e.right) + self.expr(e.left) + call(H_SREP)
            raise WasmUnsupported("字符串重复只支持 str * int",
                                  getattr(e, "lineno", 0), getattr(e, "col", 0))
        raise WasmUnsupported("字符串运算符不在 WASM 后端内：%s" % e.op,
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    # ---------- 条件（压 i32）----------
    def cond(self, e):
        if isinstance(e, A.Bool):
            return i32c(1 if e.val else 0)
        if isinstance(e, A.Num):
            return i32c(1 if e.val else 0)
        if isinstance(e, A.BinOp) and e.op in ("and", "or"):
            # 条件位置的短路同样是短路（否则 and 的右边会被无条件求值，
            # 里面若含除零就会提前 trap —— 语义不能靠运气）。
            if e.op == "and":
                return (self.cond(e.left) + b"\x04" + bytes([VT_I32])
                        + self.cond(e.right)
                        + b"\x05" + i32c(0) + b"\x0b")
            return (self.cond(e.left) + b"\x04" + bytes([VT_I32])
                    + i32c(1)
                    + b"\x05" + self.cond(e.right) + b"\x0b")
        if isinstance(e, A.BinOp) and e.op in _CMP:
            tl = self._infer(e.left)
            tr = self._infer(e.right)
            if T_FLOAT in (tl, tr):
                return (self._f64(e.left) + self._f64(e.right)
                        + self._F64_CMP[e.op])
            if T_STR in (tl, tr):
                if e.op not in ("==", "!="):
                    raise WasmUnsupported("字符串只支持 == / != 比较",
                                          getattr(e, "lineno", 0), getattr(e, "col", 0))
                q = self.expr(e.left) + self.expr(e.right) + call(H_SEQ)
                return q + b"\xa7" if e.op == "==" else q + b"\x50"
            return self.expr(e.left) + self.expr(e.right) + bytes([_CMP[e.op]])
        if isinstance(e, A.UnaryOp) and e.op == "not":
            return self.cond(e.child) + b"\x45"
        if isinstance(e, A.TypeTest):
            return i32c(self._have(e))
        t = self._infer(e)
        if t == T_FLOAT:
            return self.expr(e) + b"\xbf" + b"\x44" + struct.pack("<d", 0.0) + b"\x62"
        if t == T_STR:
            tmp = self._new_tmp()
            return (self.expr(e) + ls(tmp) + lg(tmp) + i64c(32) + b"\x88"
                    + i64c(0) + b"\x52")     # 高 32 位是 ptr，非 0 即非空串
        return self.expr(e) + i64c(0) + b"\x52"

    @staticmethod
    def _is_bool_expr(e):
        """解释器把布尔结果打印成 True/False，WASM 侧得跟着走。"""
        if isinstance(e, A.Bool):
            return True
        if isinstance(e, A.BinOp) and (e.op in _CMP or e.op in ("and", "or")):
            return True
        if isinstance(e, A.UnaryOp) and e.op == "not":
            return True
        if isinstance(e, A.TypeTest):
            return True
        return False

    # ---------- print ----------
    def _print(self, items):
        out = call(IMP_BEGIN)
        for it in items:
            t = self._infer(it)
            if t == T_STR:
                tmp = self._new_tmp()
                out += self.expr(it) + ls(tmp)
                out += lg(tmp) + i64c(32) + b"\x88" + b"\xa7"          # ptr
                out += lg(tmp) + i64c(0xFFFFFFFF) + b"\x83" + b"\xa7"  # len
                out += call(IMP_STR)
            elif t == T_FLOAT:
                out += self.expr(it) + b"\xbf" + call(IMP_F64)
            elif self._is_bool_expr(it):
                out += (self.cond(it) + b"\x04" + bytes([EMPTY_BT])
                        + i32c(self.str_offset("True")) + i32c(4) + call(IMP_STR)
                        + b"\x05" + i32c(self.str_offset("False")) + i32c(5)
                        + call(IMP_STR) + b"\x0b")
            elif t and t.startswith(T_CLS):
                raise WasmUnsupported(
                    "print 对象不在 WASM 后端内（解释器打印的是 <类 实例> 这类文本）",
                    getattr(it, "lineno", 0), getattr(it, "col", 0))
            elif t in (T_LIST, T_DICT):
                raise WasmUnsupported(
                    "print %s 不在 WASM 后端内（解释器按元素展开打印）" % t,
                    getattr(it, "lineno", 0), getattr(it, "col", 0))
            else:
                out += self.expr(it) + call(IMP_I64)
        return out + call(IMP_NL)

    # ---------- 语句 ----------
    def stmt(self, s):
        if s is None:
            return b""
        if isinstance(s, list):
            return b"".join(self.stmt(x) for x in s)
        if isinstance(s, A.Block):
            return b"".join(self.stmt(x) for x in s.stmts)
        if isinstance(s, A.Assign):
            return self._store(s.name, s.expr, getattr(s, "scope", None))
        if isinstance(s, A.CompoundAssign):
            # 解析器把 op 存成 "+=" / "-=" 这种带等号的形式
            op = s.op[:-1] if s.op.endswith("=") else s.op
            t = self.vtype.get(s.name)
            sc = getattr(s, "scope", None)
            if t == T_FLOAT:
                if op not in self._F64_OPS:
                    raise WasmUnsupported("浮点复合赋值运算符不支持：%s" % s.op,
                                          getattr(s, "lineno", 0), getattr(s, "col", 0))
                return (self._get(s.name) + b"\xbf" + self._f64(s.expr)
                        + self._F64_OPS[op] + b"\xbd" + self._set(s.name, sc))
            if t == T_STR:
                if op != "+":
                    raise WasmUnsupported("字符串只支持 += 复合赋值",
                                          getattr(s, "lineno", 0), getattr(s, "col", 0))
                return (self._get(s.name) + self.expr(s.expr) + call(H_SCAT)
                        + self._set(s.name, sc))
            ops = {"+": call(H_IADD), "-": call(H_ISUB), "*": call(H_IMUL),
                   "%": call(H_IMOD)}
            if op not in ops:
                raise WasmUnsupported("复合赋值运算符不在 WASM 后端内：%s" % s.op,
                                      getattr(s, "lineno", 0), getattr(s, "col", 0))
            return (self._get(s.name) + self.expr(s.expr) + ops[op]
                    + self._set(s.name, sc))
        if isinstance(s, A.Print):
            return self._print(s.items)
        # 4.9.1：字段赋值 set self.x = ... / obj.x = ...
        if isinstance(s, A.AttrSet):
            ot = self._infer(s.obj_expr)
            if not (ot and ot.startswith(T_CLS)):
                raise WasmUnsupported("属性赋值只支持对象字段（WASM 后端）",
                                      s.lineno, s.col)
            cn = ot[len(T_CLS):]
            off = self._field_off(cn, s.attr_name)
            want = self.classes[cn]["ftypes"].get(s.attr_name, T_INT)
            return (self.expr(s.obj_expr) + i64c(32) + b"\x88" + b"\xa7"
                    + i32c(off) + b"\x6a"
                    + self._promote(s.value, want) + b"\x37\x03\x00")
        if isinstance(s, A.IndexSet):
            ct = self._infer(s.container)
            if ct != T_LIST:
                raise WasmUnsupported("只有 list 支持下标赋值（WASM 后端）",
                                      s.lineno, s.col)
            elem = self._elem_type(s.container)
            return (self.expr(s.container) + i64c(32) + b"\x88" + b"\xa7"
                    + self.expr(s.idx) + i64c(3) + b"\x86" + b"\xa7"
                    + b"\x6a"
                    + self._promote(s.val, elem) + b"\x37\x03\x00")
        if isinstance(s, A.Call):
            if isinstance(s.callee, A.Var) and s.callee.name == "print":
                return self._print(s.args)
            name = s.callee.name if isinstance(s.callee, A.Var) else None
            if name is None or name not in self.func_index:
                raise WasmUnsupported("语句调用不在 WASM 后端内：%s"
                                      % (name or type(s.callee).__name__),
                                      getattr(s, "lineno", 0), getattr(s, "col", 0))
            return self.expr(s) + b"\x1a"
        if isinstance(s, A.If):
            return self._if(s.cond, s.body, s.elseifs, s.elseb)
        if isinstance(s, A.While):
            return self._while(s)
        if isinstance(s, A.For):
            return self._for(s)
        if isinstance(s, A.Break):
            return self._br("brk")
        if isinstance(s, A.Continue):
            return self._br("cont")
        if isinstance(s, A.Return):
            return self.expr(s.expr) + b"\x0f"
        raise WasmUnsupported("无法生成语句：%s" % type(s).__name__,
                              getattr(s, "lineno", 0), getattr(s, "col", 0))

    def _while(self, s):
        out = b"\x02" + bytes([EMPTY_BT])          # block  → break 的落点
        self.ctrl.append("brk")
        out += b"\x03" + bytes([EMPTY_BT])         # loop   → continue 的落点
        self.ctrl.append("cont")
        out += self.cond(s.cond) + b"\x45" + b"\x0d" + uleb(1)
        out += self.stmt(s.body)
        out += b"\x0c" + uleb(0)
        out += b"\x0b"
        self.ctrl.pop()
        out += b"\x0b"
        self.ctrl.pop()
        return out

    def _if(self, cond, body, elseifs, elseb, i=0):
        self.ctrl.append("if")
        out = self.cond(cond) + b"\x04" + bytes([EMPTY_BT]) + self.stmt(body)
        if i < len(elseifs):
            c2, b2 = elseifs[i]
            out += b"\x05" + self._if(c2, b2, elseifs, elseb, i + 1)
        elif elseb is not None:
            out += b"\x05" + self.stmt(elseb)
        out += b"\x0b"
        self.ctrl.pop()
        return out

    def _for(self, s):
        if not getattr(s, "var", None):
            raise WasmUnsupported("for 缺少循环变量", getattr(s, "lineno", 0), 0)
        args = getattr(getattr(s, "iter_expr", None), "args", None)
        if not args:
            raise WasmUnsupported("for 只支持 range(...) 迭代",
                                  getattr(s, "lineno", 0), 0)
        if len(args) == 1:
            e_start, e_stop, e_step = i64c(0), self.expr(args[0]), i64c(1)
        elif len(args) == 2:
            e_start, e_stop, e_step = self.expr(args[0]), self.expr(args[1]), i64c(1)
        else:
            e_start = self.expr(args[0])
            e_stop = self.expr(args[1])
            e_step = self.expr(args[2])
        t_start, t_stop, t_step, t_i = (self._new_tmp(), self._new_tmp(),
                                        self._new_tmp(), self._new_tmp())
        out = (e_start + ls(t_start) + e_stop + ls(t_stop)
               + e_step + ls(t_step) + lg(t_start) + ls(t_i))
        # step == 0 → trap（与解释器「range 的步长不能为 0」同义）
        out += lg(t_step) + i64c(0) + b"\x51"
        out += b"\x04" + bytes([EMPTY_BT]) + b"\x00" + b"\x0b"
        out += b"\x02" + bytes([EMPTY_BT])                 # block → break 落点
        self.ctrl.append("brk")
        out += b"\x03" + bytes([EMPTY_BT])                 # loop
        self.ctrl.append("blk")
        out += (lg(t_step) + i64c(0) + b"\x55"
                + b"\x04" + bytes([VT_I32])
                + lg(t_i) + lg(t_stop) + b"\x53"
                + b"\x05"
                + lg(t_i) + lg(t_stop) + b"\x55"
                + b"\x0b")
        out += b"\x45" + b"\x0d" + uleb(1)
        out += lg(t_i) + self._set(s.var, None)
        out += b"\x02" + bytes([EMPTY_BT])                 # block → continue 落点
        self.ctrl.append("cont")
        out += self.stmt(s.body)
        out += b"\x0b"
        self.ctrl.pop()
        out += lg(t_i) + lg(t_step) + call(H_IADD) + ls(t_i)
        out += b"\x0c" + uleb(0)
        out += b"\x0b"
        self.ctrl.pop()
        out += b"\x0b"
        self.ctrl.pop()
        return out

    # ---------- 函数 ----------
    def function(self, fn, cls=None):
        self.in_func = True
        self.ctrl = []
        names = set(fn.params)
        _collect_assigns(fn.body, names)
        if cls:
            # 方法：局部 0 是隐式的 self，形参从 1 开始
            info = self.classes[cls]
            ptypes = info["params"].get(fn.name) or [T_INT] * len(fn.params)
            self.cur_params = {"self": T_CLS + cls}
            for i, p in enumerate(fn.params):
                self.cur_params[p] = ptypes[i] if i < len(ptypes) else T_INT
            order = ["self"] + list(fn.params) + sorted(names - set(fn.params))
            nparams = len(fn.params) + 1
        else:
            # 生成阶段也要知道形参类型：float/str 形参的算术要靠它决定指令
            self.cur_params = dict(zip(fn.params, (self.sig.get(fn.name)
                                                   or ([T_INT] * len(fn.params), T_INT))[0]))
            order = list(fn.params) + sorted(names - set(fn.params))
            nparams = len(fn.params)
        self.locals = {n: i for i, n in enumerate(order)}
        self.nparams = nparams
        self.locals_decl = [(1, VT_I64) for _ in order[self.nparams:]]
        self.tmp_count = 0
        body = self.stmt(fn.body) + i64c(0) + b"\x0f"
        self.funcs.append({"type": self.type_idx([VT_I64] * nparams, [VT_I64]),
                           "locals": list(self.locals_decl),
                           "tmp": self.tmp_count,
                           "body": body + b"\x0b"})
        self.in_func = False
        self.cur_params = {}
        self.locals = {}
        self.nparams = 0
        self.locals_decl = []
        self.tmp_count = 0

    def top(self):
        self.in_func = False
        self.ctrl = []
        self.locals = {}
        self.nparams = 0
        self.locals_decl = []
        self.tmp_count = 0
        body = self.stmt([s for s in _stmts(self.ast)
                          if not isinstance(s, (A.Def, A.ClassDef))]) + b"\x0b"
        self.funcs.append({"type": self.type_idx([], []), "locals": [],
                           "tmp": self.tmp_count, "body": body})

    # ---------- helper 函数（索引 5..9）----------
    @staticmethod
    def _ovf_if(check):
        return (check + b"\x04" + bytes([EMPTY_BT])
                + call(IMP_OVF) + b"\x00" + b"\x0b")

    def helpers(self):
        # iadd：sum<0 且 a>0 且 b>0，或 sum>0 且 a<0 且 b<0 → 溢出
        body = (lg(0) + lg(1) + b"\x7c" + ls(2)
                + self._ovf_if(lg(2) + i64c(0) + b"\x53"
                               + lg(0) + i64c(0) + b"\x55" + b"\x71"
                               + lg(1) + i64c(0) + b"\x55" + b"\x71")
                + self._ovf_if(lg(2) + i64c(0) + b"\x55"
                               + lg(0) + i64c(0) + b"\x53" + b"\x71"
                               + lg(1) + i64c(0) + b"\x53" + b"\x71")
                + lg(2) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I64)], "tmp": 0, "body": body})

        # isub：sum<0 且 a>0 且 b<0，或 sum>0 且 a<0 且 b>0 → 溢出
        body = (lg(0) + lg(1) + b"\x7d" + ls(2)
                + self._ovf_if(lg(2) + i64c(0) + b"\x53"
                               + lg(0) + i64c(0) + b"\x53" + b"\x71"
                               + lg(1) + i64c(0) + b"\x55" + b"\x71")
                + self._ovf_if(lg(2) + i64c(0) + b"\x55"
                               + lg(0) + i64c(0) + b"\x55" + b"\x71"
                               + lg(1) + i64c(0) + b"\x53" + b"\x71")
                + lg(2) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I64)], "tmp": 0, "body": body})

        # imul：sum = a*b；b==0 不必查；a==-1 且 b==min 直接判溢出；否则回除校验
        body = (lg(0) + lg(1) + b"\x7e" + ls(2)
                + lg(1) + b"\x50"
                + b"\x04" + bytes([EMPTY_BT]) + b"\x05"
                + self._ovf_if(lg(0) + i64c(-1) + b"\x51"
                               + lg(1) + i64c(I64_MIN) + b"\x51" + b"\x71")
                + self._ovf_if(lg(2) + lg(1) + b"\x7f" + lg(0) + b"\x52")
                + b"\x0b"
                + lg(2) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I64)], "tmp": 0, "body": body})

        # ineg：a == min → 溢出
        body = (lg(0) + i64c(0) + b"\x7d" + ls(1)
                + self._ovf_if(lg(0) + i64c(I64_MIN) + b"\x51")
                + lg(1) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64], [VT_I64]),
                           "locals": [(1, VT_I64)], "tmp": 0, "body": body})

        # imod：b==0 trap；结果符号跟随除数
        body = (lg(1) + b"\x50"
                + b"\x04" + bytes([EMPTY_BT]) + b"\x00" + b"\x0b"
                + lg(0) + lg(1) + b"\x81" + ls(2)
                + lg(2) + i64c(0) + b"\x52"
                + lg(2) + i64c(0) + b"\x53"
                + lg(1) + i64c(0) + b"\x53" + b"\x73" + b"\x71"
                + b"\x04" + bytes([EMPTY_BT])
                + lg(2) + lg(1) + b"\x7c" + ls(2) + b"\x0b"
                + lg(2) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I64)], "tmp": 0, "body": body})

        # str_concat（索引 11）：a/b 都是 (ptr<<32)|len，拼到 bump 区
        body = (lg(0) + i64c(32) + b"\x88" + b"\xa7" + ls(2)       # pa
                + lg(0) + b"\xa7" + ls(3)                          # la
                + lg(1) + i64c(32) + b"\x88" + b"\xa7" + ls(4)     # pb
                + lg(1) + b"\xa7" + ls(5)                          # lb
                + lg(3) + lg(5) + b"\x6a" + ls(6)                  # n = la+lb
                + gg(0) + ls(7)                                    # h = heap
                + lg(7) + lg(2) + lg(3) + b"\xfc\x0a\x00\x00"      # copy(h, pa, la)
                + lg(7) + lg(3) + b"\x6a" + lg(4) + lg(5) + b"\xfc\x0a\x00\x00"
                + lg(7) + lg(6) + b"\x6a" + gs(0)                  # heap = h+n
                + lg(7) + b"\xac" + i64c(32) + b"\x86"             # (i64)h << 32
                + lg(6) + b"\xac" + b"\x84"                        # | (i64)n
                + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(6, VT_I32)], "tmp": 0, "body": body})

        # str_repeat（索引 12）：str * int；非正次数返回空串
        body = (lg(0) + i64c(32) + b"\x88" + b"\xa7" + ls(2)       # p
                + lg(0) + b"\xa7" + ls(3)                          # l
                + lg(1) + b"\xa7" + ls(6)                          # cnt
                + lg(6) + i32c(0) + b"\x4c"                        # cnt <= 0 ?
                + b"\x04" + bytes([EMPTY_BT]) + i64c(0) + b"\x0f" + b"\x0b"
                + lg(3) + lg(6) + b"\x6c" + ls(4)                  # total = l*cnt
                + gg(0) + ls(5)                                    # h
                + lg(5) + lg(4) + b"\x6a" + gs(0)                  # heap = h+total
                + i32c(0) + ls(7)                                  # i = 0
                + b"\x02" + bytes([EMPTY_BT])
                + b"\x03" + bytes([EMPTY_BT])
                + lg(7) + lg(6) + b"\x4e" + b"\x0d" + uleb(1)      # i >= cnt → 跳出
                + lg(5) + lg(7) + lg(3) + b"\x6c" + b"\x6a"        # h + i*l
                + lg(2) + lg(3) + b"\xfc\x0a\x00\x00"
                + lg(7) + i32c(1) + b"\x6a" + ls(7)
                + b"\x0c" + uleb(0)
                + b"\x0b" + b"\x0b"
                + lg(5) + b"\xac" + i64c(32) + b"\x86"
                + lg(4) + b"\xac" + b"\x84"
                + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(6, VT_I32)], "tmp": 0, "body": body})

        # str_eq（索引 13）：逐字节比较，返回 i64 0/1
        body = (lg(0) + i64c(32) + b"\x88" + b"\xa7" + ls(2)
                + lg(0) + b"\xa7" + ls(3)
                + lg(1) + i64c(32) + b"\x88" + b"\xa7" + ls(4)
                + lg(1) + b"\xa7" + ls(5)
                + lg(3) + lg(5) + b"\x47"                          # la != lb
                + b"\x04" + bytes([EMPTY_BT]) + i64c(0) + b"\x0f" + b"\x0b"
                + i32c(0) + ls(6)
                + b"\x02" + bytes([EMPTY_BT])
                + b"\x03" + bytes([EMPTY_BT])
                + lg(6) + lg(3) + b"\x4e" + b"\x0d" + uleb(1)
                + lg(2) + lg(6) + b"\x6a" + b"\x2d\x00\x00"
                + lg(4) + lg(6) + b"\x6a" + b"\x2d\x00\x00"
                + b"\x47"
                + b"\x04" + bytes([EMPTY_BT]) + i64c(0) + b"\x0f" + b"\x0b"
                + lg(6) + i32c(1) + b"\x6a" + ls(6)
                + b"\x0c" + uleb(0)
                + b"\x0b" + b"\x0b"
                + i64c(1)
                + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(5, VT_I32)], "tmp": 0, "body": body})

        # ---- 4.9.1：内存分配与容器读取（索引 14/15/16）----
        # bump 分配（索引 14）：param nbytes(i32) → ptr(i32)。不回收、不对齐检查，
        # 调用方一律传 8 的倍数；堆用尽时 i32 加法绕回 → 越界访问直接 trap。
        body = (gg(0) + ls(1)
                + gg(0) + lg(0) + b"\x6a" + gs(0)
                + lg(1) + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I32], [VT_I32]),
                           "locals": [(1, VT_I32)], "tmp": 0, "body": body})

        # 列表读（索引 15）：param handle(i64), idx(i64) → elem(i64)
        #   越界 / 负数下标一律 trap（解释器这时抛 IndexError，两边都不会给错值）
        body = (lg(0) + i64c(32) + b"\x88" + b"\xa7" + ls(2)          # ptr
                + lg(0) + i64c(0xFFFFFFFF) + b"\x83" + ls(3)          # n
                + lg(1) + i64c(0) + b"\x53"
                + b"\x04" + bytes([EMPTY_BT]) + b"\x00" + b"\x0b"     # idx < 0 → trap
                + lg(1) + lg(3) + b"\x59"
                + b"\x04" + bytes([EMPTY_BT]) + b"\x00" + b"\x0b"     # idx >= n → trap
                + lg(2) + lg(1) + i64c(3) + b"\x86" + b"\xa7" + b"\x6a"
                + b"\x29\x03\x00"                                     # i64.load
                + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I32), (1, VT_I64)], "tmp": 0,
                           "body": body})

        # 字典取（索引 16）：param handle(i64), key(str) → value(i64)
        #   线性扫描 + 复用 str_eq（H_SEQ）比较键；未命中 trap（解释器抛 KeyError）
        body = (lg(0) + i64c(32) + b"\x88" + b"\xa7" + ls(2)          # ptr
                + lg(0) + i64c(0xFFFFFFFF) + b"\x83" + ls(3)          # n
                + i64c(0) + ls(4)                                     # i = 0
                + b"\x02" + bytes([EMPTY_BT])
                + b"\x03" + bytes([EMPTY_BT])
                + lg(4) + lg(3) + b"\x59" + b"\x0d" + uleb(1)         # i >= n → 出循环
                + lg(2) + lg(4) + i64c(4) + b"\x86" + b"\xa7" + b"\x6a" + ls(5)
                + lg(5) + b"\x29\x03\x00" + ls(6)                     # 该条的 key
                + lg(6) + lg(1) + call(H_SEQ) + b"\xa7"             # → i32 条件
                + b"\x04" + bytes([EMPTY_BT])
                + lg(5) + i32c(8) + b"\x6a" + b"\x29\x03\x00" + b"\x0f"  # 取 value 返回
                + b"\x0b"
                + lg(4) + i64c(1) + b"\x7c" + ls(4)
                + b"\x0c" + uleb(0)
                + b"\x0b" + b"\x0b"
                + b"\x00"                                             # 未命中 → trap
                + b"\x0b")
        self.funcs.append({"type": self.type_idx([VT_I64, VT_I64], [VT_I64]),
                           "locals": [(1, VT_I32), (1, VT_I64), (1, VT_I64),
                                      (1, VT_I32), (1, VT_I64)], "tmp": 0,
                           "body": body})

    # ---------- 拼装 ----------
    def compile(self):
        self.scan()
        # 运行期堆指针固定占全局索引 0（三个字符串 helper 里硬编码 gg(0)/gs(0)）；
        # 用户全局整体后移一位，索引要重编号，不能沿用 scan 里的旧值。
        user_globals = [k for k in self.globals if k != "__heap"]
        self.globals = dict([("__heap", 0)]
                            + [(k, i + 1) for i, k in enumerate(user_globals)])
        # 布尔打印用的两个字面量由编译器自己产生，源里没有，得手工登记
        for lit in ("True", "False"):
            if lit not in [t for t, _o in self.strings]:
                self.strings.append((lit, self.str_off))
                self.str_off += len(lit)
        self.infer_scan()
        self.helpers()                     # 先占索引 6..16
        for fn in self.defs:
            self.function(fn)
        nmeth = 0
        for cn in self.class_order:
            info = self.classes[cn]
            for mname in info["morder"]:
                self.function(info["methods"][mname], cls=cn)
                nmeth += 1
        self.top()
        top_idx = FIRST_USER + len(self.defs) + nmeth

        # import 的类型要在 types 段之前登记
        imp_spec = [("env", "print_begin", self.type_idx([], [])),
                    ("env", "print_i64", self.type_idx([VT_I64], [])),
                    ("env", "print_str", self.type_idx([VT_I32, VT_I32], [])),
                    ("env", "print_nl", self.type_idx([], [])),
                    ("env", "overflow", self.type_idx([], [])),
                    ("env", "print_f64", self.type_idx([VT_F64], []))]

        sec_type = section(1, vec([b"\x60" + vec([bytes([p]) for p in ps])
                                   + vec([bytes([r]) for r in rs])
                                   for ps, rs in self.types]))
        sec_imp = section(2, vec([name(m) + name(n) + b"\x00" + uleb(t)
                                  for (m, n, t) in imp_spec]))
        sec_func = section(3, vec([uleb(f["type"]) for f in self.funcs]))
        sec_mem = section(5, vec([b"\x00" + uleb(MEM_PAGES)]))
        globs = []
        for i in range(len(self.globals)):
            if i == 0:
                globs.append(bytes([VT_I32]) + b"\x01" + i32c(HEAP_BASE) + b"\x0b")
            else:
                globs.append(bytes([VT_I64]) + b"\x01" + i64c(0) + b"\x0b")
        sec_glob = section(6, vec(globs))
        sec_exp = section(7, vec([name("_start") + b"\x00" + uleb(top_idx),
                                  name("memory") + b"\x02" + uleb(0)]))

        codes = []
        for f in self.funcs:
            decls = list(f["locals"])
            if f["tmp"]:
                decls.append((f["tmp"], VT_I64))
            lb = vec([uleb(c) + bytes([t]) for (c, t) in decls])
            body = lb + f["body"]
            codes.append(uleb(len(body)) + body)
        sec_code = section(10, vec(codes))

        data_blob = b"".join(s.encode("utf-8") for s, _o in self.strings)
        sec_data = section(11, vec([b"\x00" + i32c(STR_BASE) + b"\x0b"
                                    + uleb(len(data_blob)) + data_blob]))

        return (b"\x00asm\x01\x00\x00\x00" + sec_type + sec_imp + sec_func
                + sec_mem + sec_glob + sec_exp + sec_code + sec_data,
                {"defs": len(self.defs), "strings": len(self.strings),
                 "globals": len(self.globals), "funcs": len(self.funcs)})


def wasm_transpile(ast):
    """AST → (wasm_bytes, meta)。超出子集抛 WasmUnsupported。"""
    return _Compiler(ast).compile()
