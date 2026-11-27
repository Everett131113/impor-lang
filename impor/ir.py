# -*- coding: utf-8 -*-
"""Impor (I++) 4.4.0 —— IR 层（三地址码中间表示 / 文本序列化 / 参考求值器）。

设计参考 xlang 的编译阶段分层：Lex → Parse → Semantic → **IR** → Lowing(各后端)。
本模块只负责「单一中间表示」这一层，不替换现有的解释器 / pytrans / ctrans / cfast，
也不改任何既有语义：

  1. `Instr` 是最小指令单元（三地址码风格：目的操作数 + 源操作数 + 行号）；
  2. `render()` 把模块序列化成可 diff 的文本（对齐 xlang `--debug` 里的 `Ir.txt` 思路）；
  3. `Evaluator` 是**独立的参考求值器**，直接执行 IR，用来和树遍历解释器对拍，
     暴露“AST 改动后某个后端悄悄走样”的那类问题（见 tests/test_ir.py）。

不支持的语法由 `impor/ir_lower.py` 在 lowering 阶段明确拒绝，不静默降级。
"""

import json

# 指令集：新增指令时必须同步三处——本文件的 Evaluator、ir_lower.py、render 的格式说明。
#   CONST   dst, 字面量
#   LOAD    dst, 名字
#   STORE   名字, 作用域(local|update|global|const|loop), 值
#   MOVE    dst, 值
#   BINOP   dst, 运算符, 左, 右
#   UNOP    dst, 运算符, 值
#   CALL    dst, 被调用者, 参数...
#   PRINT   值...
#   LIST    dst, 元素...
#   DICT    dst, 键, 值, 键, 值 ...
#   INDEX   dst, 容器, 下标
#   SLICE   dst, 容器, 起, 止, 步长
#   SETINDEX 容器, 下标, 值
#   TOSTR   dst, 值
#   CONCAT  dst, 片段...
#   ITER    dst, 可迭代对象
#   ITER_NEXT 元素, 迭代器, 结束标签
#   LABEL   标签名
#   JUMP    标签名
#   JUMP_IF_FALSE 条件, 标签名
#   ASSERT  条件
#   RETURN  值
#   --- 4.6：异常控制流（异常边在 IR 里显式表示，不让各后端各自实现） ---
#   TRY_BEGIN   try标签, catch标签|nil, finally标签|nil
#   TRY_END     try标签
#   CATCH_BEGIN try标签, 错误变量名
#   CATCH_END   try标签
#   TRY_CLOSE   try标签, 重抛标志变量
#   RAISE       异常类型, 消息
#   ERR_MATCH   dst, 异常类型（catch 内判定当前异常类型）
#   RETHROW     无参数（catch 内主动重抛当前异常）
#   --- 4.6：类模型 ---
#   NEW         dst, 类名, 参数...
#   GETATTR     dst, 对象, 属性名
#   SETATTR     对象, 属性名, 值
#   CALLMETHOD  dst, 对象, 方法名, 参数...
#   SUPERCALL   dst, 起始类名, 方法名, self, 参数...

IR_VERSION = "5.6.0"


class _IRThrow(Exception):
    """RAISE 在参考求值器里产生的异常；只在本模块内部流转，不外泄。"""

    __slots__ = ("etype", "emsg", "lineno")

    def __init__(self, etype, emsg, lineno=0):
        self.etype = etype
        self.emsg = emsg
        self.lineno = lineno
        super().__init__("%s: %s" % (etype, emsg))


class IRError(Exception):
    """IR 层错误：lowering 拒绝、求值期运行错误（除零/未定义变量等）。"""

    def __init__(self, message, lineno=0, col=0, filename=None, code="IR0001"):
        self.message = message
        self.lineno = lineno
        self.col = col
        self.filename = filename
        self.code = code
        where = ""
        if filename:
            where = "%s:%d:%d: " % (filename, lineno or 0, col or 0)
        elif lineno:
            where = "第 %d 行: " % lineno
        super().__init__(where + message)


class UnsupportedNode(IRError):
    """lowering 阶段明确拒绝的语法（不静默降级，对齐 Impor 的诚实口径）。"""

    def __init__(self, node_name, lineno=0, col=0, filename=None, reason=""):
        self.node_name = node_name
        msg = "IR 暂不支持 %s" % node_name
        if reason:
            msg += "（%s）" % reason
        super().__init__(msg, lineno, col, filename, code="IR4001")


class Lit:
    """字面量操作数。与“名字操作数”（普通 str）区分，渲染时可打印成源码形态。"""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, Lit) and other.value == self.value

    def __hash__(self):
        return hash((type(self.value).__name__, repr(self.value)))

    def __repr__(self):
        return "Lit(%r)" % (self.value,)


class Instr:
    """一条三地址码指令。args 里的元素只允许是 str（名字/标签）或 Lit（字面量）。"""

    __slots__ = ("op", "args", "lineno")

    def __init__(self, op, args=(), lineno=0):
        self.op = op
        self.args = tuple(args)
        self.lineno = int(lineno or 0)

    def __repr__(self):
        return "Instr(%s, %r)" % (self.op, self.args)


class IRFunction:
    __slots__ = ("name", "params", "body", "lineno", "is_main", "is_method")

    def __init__(self, name, params=None, body=None, lineno=0, is_main=False,
                 is_method=False):
        self.name = name
        self.params = list(params or [])
        self.body = list(body or [])
        self.lineno = int(lineno or 0)
        self.is_main = is_main
        self.is_method = is_method


class IRClass:
    """类：方法即 `IRFunction`（params 首位固定为该类实例 `self`），字段初始化是独立指令段。"""

    __slots__ = ("name", "base", "methods", "field_body", "lineno")

    def __init__(self, name, base=None, methods=None, field_body=None, lineno=0):
        self.name = name
        self.base = base
        self.methods = list(methods or [])
        self.field_body = list(field_body or [])
        self.lineno = int(lineno or 0)

    def method(self, name):
        for m in self.methods:
            if m.name == name:
                return m
        return None


class IRModule:
    __slots__ = ("filename", "funcs", "main", "classes")

    def __init__(self, filename=None):
        self.filename = filename
        self.funcs = []          # 除 __main__ 外的函数（声明顺序）
        self.main = IRFunction("__main__", [], [], 0, is_main=True)
        self.classes = []        # 4.6 类（声明顺序）

    # ---- 统计 ----
    def labels(self):
        n = 0
        for fn in [self.main] + self.funcs:
            n += sum(1 for i in fn.body if i.op == "LABEL")
        return n

    def instruction_count(self):
        n = 0
        for fn in [self.main] + self.funcs:
            n += sum(1 for i in fn.body if i.op != "LABEL")
        return n

    def class_by_name(self, name):
        for c in self.classes:
            if c.name == name:
                return c
        return None

    def find_method(self, cls, name):
        """沿继承链找方法（子类优先，与运行时 rt_method 一致）。"""
        while cls is not None:
            m = cls.method(name)
            if m is not None:
                return m
            cls = self.class_by_name(cls.base) if cls.base else None
        return None

    def stats(self):
        return {
            "classes": len(self.classes),
            "functions": len(self.funcs) + 1,
            "instructions": self.instruction_count(),
            "labels": self.labels(),
        }

    # ---- 文本序列化 ----
    def render(self, header=True):
        out = []
        if header:
            st = self.stats()
            out.append("; Impor IR %s  %s" % (
                IR_VERSION, self.filename or "<string>"))
            out.append("; 函数 %d / 指令 %d / 标签 %d" % (
                st["functions"], st["instructions"], st["labels"]))
        for c in self.classes:
            out.append("")
            out.append("class %s%s:" % (c.name, (" < " + c.base) if c.base else ""))
            if c.field_body:
                out.append("  ; 字段初始化 %d 条指令" % len(c.field_body))
            for m in c.methods:
                out.extend("  " + ln for ln in _render_function(m))
        for fn in self.funcs:
            out.append("")
            out.extend(_render_function(fn))
        out.append("")
        out.extend(_render_function(self.main))
        return "\n".join(out) + "\n"

    def __repr__(self):
        return "<IRModule %s funcs=%d>" % (self.filename, len(self.funcs) + 1)


def _render_operand(x):
    if isinstance(x, Lit):
        v = x.value
        if v is None:
            return "nil"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        return repr(v)
    return str(x)


def _render_function(fn):
    body = list(fn.body)
    lines = ["func %s(%s):" % (fn.name, ", ".join(fn.params))]
    if not body:
        lines.append("    ; 空函数体")
        return lines
    for ins in body:
        if ins.op == "LABEL":
            lines.append("%s:" % ins.args[0])
            continue
        head = "    %-10s " % ins.op
        tail = ", ".join(_render_operand(a) for a in ins.args)
        line = (head + tail).rstrip()
        if ins.lineno:
            line = "%-58s ; L%d" % (line, ins.lineno)
        lines.append(line.rstrip())
    return lines


# ---------------------------------------------------------------------------
# 参考求值器：直接执行 IR。与树遍历解释器是两条独立路径，用于语义对拍。
# ---------------------------------------------------------------------------

class _Frame:
    __slots__ = ("locals",)
    def __init__(self):
        self.locals = {}


class _ErrObj:
    """catch 绑定的异常对象（IR 层只要求可打印；属性访问归类模型那一层）。"""
    __slots__ = ("type", "msg")

    def __init__(self, etype, emsg):
        self.type = etype
        self.msg = emsg

    def __str__(self):
        return "%s: %s" % (self.type, self.msg)


class _Instance:
    """参考求值器的实例：类引用 + 字段表；属性读取回落到字段。"""

    __slots__ = ("cls", "fields")

    def __init__(self, cls):
        self.cls = cls
        self.fields = {}

    def __getattr__(self, name):
        try:
            return self.fields[name]
        except KeyError:
            raise AttributeError(name)

    def __str__(self):
        return "<%s 实例>" % self.cls.name


class _FunctionRef:
    __slots__ = ("fn",)

    def __init__(self, fn):
        self.fn = fn


_ITER_END = object()

# Impor 的纯函数内建（不依赖解释器状态）直接从 builtins.py 取，
# 避免 IR 求值器另写一套导致“数字对不上”的假分歧；
# 需要宿主状态的（input/sleep/frame/文件 IO 等）不在此表，IR 里会当作未定义变量报错。
_EXTRA_BUILTIN_NAMES = (
    "strcut", "strpad", "strjoin", "trim", "upper", "lower",
    "to_str", "to_num", "clamp", "sign", "hash",
    "sum_arr", "max_arr", "min_arr", "arrappend", "arrpop",
    "arrfind", "arrfill", "arrrev", "arrdup", "list", "type_of",
)

_extra_builtins_cache = None


def _extra_builtins():
    """惰性加载 builtins.py 里的纯函数内建；缺失时静默跳过（不改变覆盖范围之外的行为）。

    builtins.py 的内建是 `make_builtins(interp)` 里的闭包，没有模块级函数，
    所以这里建一个**空壳解释器**只为取表，不执行任何脚本、不写 stdout。
    """
    global _extra_builtins_cache
    if _extra_builtins_cache is not None:
        return _extra_builtins_cache
    table = {}
    try:
        from .builtins import make_builtins
        from .interpreter import Interpreter
        probe = Interpreter()
        full = make_builtins(probe)
        for name in _EXTRA_BUILTIN_NAMES:
            fn = full.get(name)
            if callable(fn):
                table[name] = fn
    except Exception:
        table = {}
    _extra_builtins_cache = table
    return table


def _norm_binop(op):
    """源码里的 `&&`/`||`/`!` 在 Impor 里是 `and`/`or`/`not` 的别名（对齐 lexer 归一结果）。"""
    return {"&&": "and", "||": "or", "!": "not"}.get(op, op)


class Evaluator:
    """IR 参考求值器。只实现 IR 覆盖到的语义，故意不复用解释器的 visit 路径。"""

    def __init__(self, module, emit=None):
        self.module = module
        self.emit = emit
        self.output = []
        self.globals = {}
        self.frames = []
        self.executed = 0
        self.cur_lineno = 0
        self._cur_err = None
        self.funcs = {}
        for fn in module.funcs:
            self.funcs[fn.name] = _FunctionRef(fn)
        self.builtins = {
            "abs": abs,
            "div": self._div,
            "len": len,
            "str": lambda v: str(v),
            "int": int,
            "float": float,
            "bool": bool,
            "max": max,
            "min": min,
            "sum": sum,
            "sorted": sorted,
            "range": range,
            "ord": ord,
            "chr": chr,
            "round": round,
        }
        self.builtins.update(_extra_builtins())

    # ---- 公共入口 ----
    def run(self):
        self._call_function(self.module.main, [])
        return self.output

    # ---- 工具 ----
    @staticmethod
    def _div(a, b):
        if b == 0:
            raise IRError("div() 除数为 0", code="IR3001")
        return a // b

    def _resolve(self, operand):
        if isinstance(operand, Lit):
            return operand.value
        if operand in self.frames[-1].locals:
            return self.frames[-1].locals[operand]
        for fr in reversed(self.frames[:-1]):
            if operand in fr.locals:
                return fr.locals[operand]
        if operand in self.globals:
            return self.globals[operand]
        if operand in self.funcs:
            return self.funcs[operand]
        if operand in self.builtins:
            return self.builtins[operand]
        raise IRError("变量 %s 未定义" % operand, self.cur_lineno, 0,
                      self.module.filename, code="IR3002")

    def _store(self, name, mode, value):
        if mode in ("local", "loop"):
            self.frames[-1].locals[name] = value
            return
        if mode == "global":
            self.globals[name] = value
            return
        # update / const：向内查找已存在的绑定
        for fr in reversed(self.frames):
            if name in fr.locals:
                fr.locals[name] = value
                return
        if name in self.globals:
            self.globals[name] = value
            return
        raise IRError("变量 %s 未定义（set 只能更新已声明变量）" % name,
                      self.cur_lineno, 0, self.module.filename, code="IR3003")

    def _call(self, callee, args):
        fn = self._resolve(callee) if isinstance(callee, str) else callee
        if isinstance(fn, _FunctionRef):
            return self._call_function(fn.fn, args)
        if callable(fn):
            try:
                return fn(*args)
            except TypeError as e:
                raise IRError("调用参数不匹配：%s" % e, code="IR3004")
            except ZeroDivisionError:
                raise IRError("除数为 0", code="IR3001")
        raise IRError("对象不可调用：%r" % (fn,), code="IR3005")

    def _call_function(self, fn, args):
        if len(args) != len(fn.params):
            raise IRError("函数 %s 需要 %d 个参数，收到 %d 个" % (
                fn.name, len(fn.params), len(args)), code="IR3006",
                filename=self.module.filename, lineno=fn.lineno)
        frame = _Frame()
        for name, val in zip(fn.params, args):
            frame.locals[name] = val
        self.frames.append(frame)
        try:
            return self._exec_body(fn.body)
        finally:
            self.frames.pop()

    def _exec_body(self, body):
        labels = {}
        for idx, ins in enumerate(body):
            if ins.op == "LABEL":
                labels[ins.args[0]] = idx
        cur_frame = self.frames[-1]
        try_stack = []          # [{"tag","catch","fin","entered"}]
        pc = 0
        while True:
            try:
                kind, val, pc = self._run_range(body, pc, labels, cur_frame, try_stack)
            except _IRThrow as th:
                self._cur_err = th
                target = self._catch_pc(try_stack, labels)
                if target is None:
                    raise
                pc = target
                continue
            if kind == "return":
                return val
            return None

    def _catch_pc(self, try_stack, labels):
        """异常落地：找最近一层能接住它的 try 的位置；接不住返回 None（继续向外冒泡）。

        已进入 catch 体的那一层（entered=True）不再接住自己的异常 —— 与 Python/解释器
        语义一致：catch 块里抛出的异常交给外层 try。
        """
        while try_stack:
            h = try_stack[-1]
            if h["entered"]:
                try_stack.pop()
                continue
            if h["catch"]:
                h["entered"] = True
                return labels[h["catch"]]
            if h["fin"]:
                try_stack.pop()
                return labels[h["fin"]]
            try_stack.pop()
        return None

    def _run_range(self, body, pc, labels, cur_frame, try_stack):
        """线性执行指令，直到 RETURN / 结束 / 抛出 _IRThrow。"""
        n = len(body)
        while pc < n:
            ins = body[pc]
            pc += 1
            self.executed += 1
            op = ins.op
            a = ins.args
            self.cur_lineno = ins.lineno
            if op == "LABEL":
                continue
            if op == "CONST":
                cur_frame.locals[a[0]] = a[1].value
            elif op == "LOAD":
                cur_frame.locals[a[0]] = self._resolve(a[1])
            elif op == "STORE":
                self._store(a[0], a[1], self._resolve(a[2]))
            elif op == "MOVE":
                cur_frame.locals[a[0]] = self._resolve(a[1])
            elif op == "BINOP":
                lhs = self._resolve(a[2])
                rhs = self._resolve(a[3])
                cur_frame.locals[a[0]] = self._binop(_norm_binop(a[1]), lhs, rhs)
            elif op == "UNOP":
                v = self._resolve(a[2])
                opname = _norm_binop(a[1])
                if opname == "-":
                    cur_frame.locals[a[0]] = -v
                elif opname == "not":
                    cur_frame.locals[a[0]] = not v
                elif opname == "+":
                    cur_frame.locals[a[0]] = +v
                else:
                    raise IRError("未知一元运算符 %s" % opname, code="IR3007")
            elif op == "CALL":
                callee = a[1]
                cargs = [self._resolve(x) for x in a[2:]]
                cur_frame.locals[a[0]] = self._call(callee, cargs)
            elif op == "PRINT":
                text = " ".join(str(self._resolve(x)) for x in a)
                self.output.append(text)
                if self.emit is not None:
                    self.emit(text)
            elif op == "LIST":
                cur_frame.locals[a[0]] = [self._resolve(x) for x in a[1:]]
            elif op == "DICT":
                d = {}
                for i in range(1, len(a), 2):
                    d[self._resolve(a[i])] = self._resolve(a[i + 1])
                cur_frame.locals[a[0]] = d
            elif op == "INDEX":
                cur_frame.locals[a[0]] = self._index(self._resolve(a[1]),
                                                      self._resolve(a[2]))
            elif op == "SLICE":
                cur_frame.locals[a[0]] = self._resolve(a[1])[
                    self._opt(a[2]):self._opt(a[3]):self._opt(a[4])]
            elif op == "SETINDEX":
                self._index_set(self._resolve(a[0]), self._resolve(a[1]),
                                self._resolve(a[2]))
            elif op == "TOSTR":
                cur_frame.locals[a[0]] = str(self._resolve(a[1]))
            elif op == "CONCAT":
                cur_frame.locals[a[0]] = "".join(str(self._resolve(x)) for x in a[1:])
            elif op == "ITER":
                cur_frame.locals[a[0]] = iter(self._resolve(a[1]))
            elif op == "ITER_NEXT":
                try:
                    val = next(self._resolve(a[1]))
                except StopIteration:
                    pc = labels[a[2]]
                    continue
                cur_frame.locals[a[0]] = val
            elif op == "JUMP":
                pc = labels[a[0]]
            elif op == "JUMP_IF_FALSE":
                if not self._resolve(a[0]):
                    pc = labels[a[1]]
            elif op == "ASSERT":
                if not self._resolve(a[0]):
                    raise _IRThrow("AssertionError", "断言失败", ins.lineno)
            # ---- 4.6 类模型 ----
            elif op == "NEW":
                clsname = a[1].value if isinstance(a[1], Lit) else self._resolve(a[1])
                cur_frame.locals[a[0]] = self._new(str(clsname),
                                                   [self._resolve(x) for x in a[2:]])
            elif op == "GETATTR":
                cur_frame.locals[a[0]] = self._getattr(self._resolve(a[1]),
                                                       self._name(a[2]))
            elif op == "SETATTR":
                self._setattr(self._resolve(a[0]), self._name(a[1]),
                              self._resolve(a[2]))
            elif op == "CALLMETHOD":
                obj = self._resolve(a[1])
                cur_frame.locals[a[0]] = self._call_method(
                    obj, self._name(a[2]), [self._resolve(x) for x in a[3:]])
            elif op == "SUPERCALL":
                cur_frame.locals[a[0]] = self._supercall(
                    cur_frame, self._name(a[1]), self._name(a[2]),
                    [self._resolve(x) for x in a[3:]])
            # ---- 4.6 异常控制流 ----
            elif op == "TRY_BEGIN":
                tag = a[0]
                c = a[1].value if isinstance(a[1], Lit) else a[1]
                f = a[2].value if isinstance(a[2], Lit) else a[2]
                try_stack.append({"tag": tag, "catch": c, "fin": f, "entered": False})
            elif op == "TRY_END":
                continue
            elif op == "CATCH_BEGIN":
                cur_frame.locals[a[1]] = self._cur_err_obj()
            elif op == "CATCH_END":
                continue
            elif op == "ERR_MATCH":
                typ = a[1].value if isinstance(a[1], Lit) else self._resolve(a[1])
                e = self._cur_err
                cur_frame.locals[a[0]] = bool(e is not None and e.etype == str(typ))
            elif op == "RAISE":
                typ = a[0].value if isinstance(a[0], Lit) else self._resolve(a[0])
                msg = a[1].value if isinstance(a[1], Lit) else self._resolve(a[1])
                raise _IRThrow(str(typ), "" if msg is None else str(msg), ins.lineno)
            elif op == "RETHROW":
                if self._cur_err is None:
                    raise IRError("RETHROW 没有可重抛的异常", code="IR3011")
                raise self._cur_err
            elif op == "TRY_CLOSE":
                if try_stack and try_stack[-1]["tag"] == a[0]:
                    try_stack.pop()
                flag = self._resolve(a[1])
                if flag:
                    if self._cur_err is not None:
                        raise self._cur_err
                    raise IRError("TRY_CLOSE 要求重抛，但没有异常可重抛", code="IR3011")
            elif op == "RETURN":
                return "return", self._resolve(a[0]), pc
            else:
                raise IRError("未知 IR 指令 %s" % op, code="IR3009",
                              filename=self.module.filename, lineno=ins.lineno)
        return "end", None, pc

    def _new(self, clsname, args):
        cls = self.module.class_by_name(clsname)
        if cls is None:
            raise IRError("未定义的类 %s" % clsname, code="IR3012")
        inst = _Instance(cls)
        init = self.module.find_method(cls, "__init__")
        if init is not None:
            self._call_function(init, [inst] + list(args))
        return inst

    def _getattr(self, obj, name):
        if isinstance(obj, _Instance):
            if name in obj.fields:
                return obj.fields[name]
            raise IRError("实例 %s 没有属性 %s" % (obj.cls.name, name), code="IR3013")
        try:
            return getattr(obj, name)
        except AttributeError:
            raise IRError("值没有属性 %s" % name, code="IR3013")

    def _setattr(self, obj, name, value):
        if isinstance(obj, _Instance):
            obj.fields[name] = value
            return
        try:
            setattr(obj, name, value)
        except AttributeError:
            raise IRError("值没有属性 %s" % name, code="IR3013")

    def _call_method(self, obj, name, args):
        if isinstance(obj, _Instance):
            fn = self.module.find_method(obj.cls, name)
            if fn is not None:
                return self._call_function(fn, [obj] + list(args))
            f = obj.fields.get(name)
            if callable(f):
                return f(*args)
            raise IRError("实例 %s 没有方法 %s" % (obj.cls.name, name), code="IR3014")
        meth = getattr(obj, name, None)
        if callable(meth):
            return meth(*args)
        raise IRError("值没有方法 %s" % name, code="IR3014")

    def _name(self, operand):
        """属性名/方法名/类名：Lit 直接取值，否则当表达式求值。"""
        if isinstance(operand, Lit):
            return str(operand.value)
        return str(self._resolve(operand))

    def _supercall(self, frame, from_cls, name, args):
        """super().name(...)：从 from_cls 的父类起沿继承链找方法。"""
        self_obj = frame.locals.get("self")
        cls = self.module.class_by_name(from_cls)
        if self_obj is None or cls is None:
            raise IRError("super() 只能用在 %s 的方法里" % from_cls, code="IR3014")
        base = self.module.class_by_name(cls.base) if cls.base else None
        fn = self.module.find_method(base, name) if base is not None else None
        if fn is None:
            raise IRError("父类链上找不到方法 %s" % name, code="IR3014")
        return self._call_function(fn, [self_obj] + list(args))

    def _cur_err_obj(self):
        e = self._cur_err
        if e is None:
            return None
        return _ErrObj(e.etype, e.emsg)

    def _opt(self, operand):
        v = self._resolve(operand)
        return None if v is None else v

    @staticmethod
    def _index(container, idx):
        try:
            return container[idx]
        except LookupError:
            raise IRError("下标越界或键不存在：%r" % (idx,), code="IR3010")

    @staticmethod
    def _index_set(container, idx, value):
        try:
            container[idx] = value
        except LookupError:
            raise IRError("下标越界或键不存在：%r" % (idx,), code="IR3010")

    @staticmethod
    def _binop(op, lhs, rhs):
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if op == "/":
            if rhs == 0:
                raise IRError("除数为 0", code="IR3001")
            return lhs / rhs
        if op == "%":
            if rhs == 0:
                raise IRError("除数为 0", code="IR3001")
            return lhs % rhs
        if op == "**":
            return lhs ** rhs
        if op == "<":
            return lhs < rhs
        if op == "<=":
            return lhs <= rhs
        if op == ">":
            return lhs > rhs
        if op == ">=":
            return lhs >= rhs
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op == "and":
            return lhs and rhs
        if op == "or":
            return lhs or rhs
        raise IRError("未知运算符 %s" % op, code="IR3007")


def evaluate(module, emit=None):
    """执行 IR 模块，返回 (输出行列表, 求值器)。"""
    # 4.7：三项优化 pass（常量折叠 / 死代码消除 / 循环不变量外提）。
    # 与 cir.to_c 走同一套优化，保证「求值器」与「IR→C 产物」对拍时两边等价。
    from . import irop as _irop
    _irop.optimize_module(module)
    ev = Evaluator(module, emit=emit)
    out = ev.run()
    return out, ev
