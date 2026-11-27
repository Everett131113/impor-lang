# -*- coding: utf-8 -*-
"""Impor (I++) 泛型单态化（v4.6）。

语言层面 Impor 的泛型语义是“声明期约束 + 运行期擦除”，C 后端过去把所有函数
统一生成为 `static Val f(Val, Val, ...)`。本模块补上**单态化**：

  - 只处理 `def f < T >(a : T, ...)` 这类单类型参数泛型；
  - 按调用点的**静态类型**（当前以字面量为准：整数 → i64、小数 → f64）
    生成专用函数 `gp_<name>_i64 / gp_<name>_f64`，参数用原生 C 类型
    （`long long` / `double`），函数体内部的算术、比较、逻辑全走 C 原生运算，
    不再经过 `Val` 分派；只在返回处装箱一次。
  - 无法静态定型的调用点、函数体超出原生可表达范围（字符串拼接、内建调用、
    打印、循环迭代器等）一律**回退**到原有的擦除版，行为不变。

因此生成物里同时存在：擦除版 `f_<n>`（保底）与专用版 `gp_<name>_<ty>`（命中时使用）。
"""

import os

from . import ast_nodes as A
from . import rangeproof

ARITH = {"+", "-", "*"}
ARITH_F = {"+", "-", "*", "/"}
CMP = {">", "<", ">=", "<=", "==", "!="}
LOGIC = {"and": "&&", "or": "||"}

CTYPE = {"i64": "long long", "f64": "double"}
BOX = {"i64": "v_int", "f64": "v_float"}


class NativeUnsupported(Exception):
    pass


def _stype(node):
    """调用点实参的静态类型：当前只认字面量，够用且不会误判。"""
    if isinstance(node, A.Num):
        return "f64" if isinstance(node.val, float) else "i64"
    if isinstance(node, A.UnaryOp) and node.op in ("-", "+"):
        return _stype(node.child)
    return None


def _int_ck():
    """原生实例是否保留逐运算溢出检查（默认保留，`--no-int-ck` 关闭）。

    保留 = 与擦除版逐字一致的 E1012 语义，代价约 +25ms/1e8 次加法；
    关闭 = 裸 C 算术（与手写 C 同级），溢出按 C 的补码回绕，不报错。
    """
    import os
    return os.environ.get("IMPOR_NO_INT_CK", "").strip().lower() not in (
        "1", "true", "yes", "on")


def _walk(node):
    """深度遍历所有 AST 节点。"""
    yield node
    for v in vars(node).values():
        if isinstance(v, A.AST):
            for x in _walk(v):
                yield x
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, A.AST):
                    for x in _walk(item):
                        yield x
                elif isinstance(item, tuple):
                    for y in item:
                        if isinstance(y, A.AST):
                            for x in _walk(y):
                                yield x


def _reassigned_names(defn):
    """函数体里被重新赋值的变量名（常量特化时这些参数不能内联）。"""
    out = set()
    for n in _walk(defn):
        if isinstance(n, (A.Assign, A.CompoundAssign)):
            out.add(n.name)
    return out


def _has_self_call(defn):
    """函数体里是否出现自调用（含尾调用）。

    自递归函数的形参每轮都会被改写成新值，所以「常量特化」把字面量内联进函数体
    就不再成立：`f(30)` 会被内联成 `p_n == 30` 的常量，递归一步也走不动。
    """
    for n in _walk(defn):
        if (isinstance(n, A.Call) and isinstance(n.callee, A.Var)
                and n.callee.name == defn.name):
            return True
    return False


def _generic_defs(ast_node):
    """name → Def（只收单类型参数 T 且全部参数都是 T 的泛型函数）。"""
    out = {}
    for n in _walk(ast_node):
        if not isinstance(n, A.Def):
            continue
        if list(getattr(n, "type_params", []) or []) != ["T"]:
            continue
        pt = list(getattr(n, "param_types", []) or [])
        if pt and any(t != "T" for t in pt):
            continue
        if not n.params:
            continue
        out[n.name] = n
    return out


def _call_sites(ast_node, gens):
    """收集需要的 (name, ty) 专用版本。"""
    need = set()
    for n in _walk(ast_node):
        if not isinstance(n, A.Call):
            continue
        cal = n.callee
        if not isinstance(cal, A.Var) or cal.name not in gens:
            continue
        defn = gens[cal.name]
        if len(n.args) != len(defn.params):
            continue
        tys = [_stype(a) for a in n.args]
        if any(t is None for t in tys) or len(set(tys)) != 1:
            continue
        need.add((cal.name, tys[0]))
    return need


# --- 4.7：按「类型标注」定型 ------------------------------------------------
# 4.6 的单态化只认泛型 `def f<T>(a:T)`，且只有调用点写的是**字面量**才能定型。
# 这让绝大多数普通函数（`def sum_to(n: int) -> int`）完全没有机会走原生路径，
# 也正是 1e8 长循环比 C 慢 1.87 倍的根源（每个 `+=` 都要经 Val 装箱/rt_add）。
#
# 这里把「全部参数与返回是同一个标量标注」的函数也纳入单态化候选：
#   def sum_to(n: int) -> int   →   gp_sum_to_i64(long long n)
# 判据严格到近乎保守：标注必须自洽（同一种 int/float）、函数体必须整段落在
# 原生子集内（否则 _NativeGen 抛 NativeUnsupported，直接跳过，回退擦除版）。
# 擦除版始终保留，所以这是纯增量优化，语义不变。
SCALAR = {"int": "i64", "float": "f64"}


def _labeled_defs(ast_node):
    """name → (Def, ty)：非泛型、参数与返回标注为同一种 int/float 的函数。"""
    out = {}
    for n in _walk(ast_node):
        if not isinstance(n, A.Def):
            continue
        if list(getattr(n, "type_params", []) or []):
            continue
        if not n.params:
            continue
        pt = [str(t or "").strip() for t in (getattr(n, "param_types", []) or [])]
        if len(pt) < len(n.params) or len(set(pt)) != 1:
            continue
        ty = SCALAR.get(pt[0])
        if ty is None:
            continue
        if str(getattr(n, "ret_type", "") or "").strip() != pt[0]:
            continue
        out.setdefault(n.name, (n, ty))
    return out


def _labeled_call_sites(ast_node, labs):
    """实参静态类型与标注一致时，才登记该标注函数的原生实例。

    4.7.1：额外返回「实参全是字面量常量」的签名 `sig`，用于常量特化实例
    （只有常量实参才能让区间证明拿到循环上界，从而免掉逐运算 E1013 检查）。
    """
    need = set()
    for n in _walk(ast_node):
        if not isinstance(n, A.Call):
            continue
        cal = n.callee
        if not isinstance(cal, A.Var) or cal.name not in labs:
            continue
        defn, ty = labs[cal.name]
        if len(n.args) != len(defn.params):
            continue
        tys = [_stype(a) for a in n.args]
        if any(t is None for t in tys) or set(tys) != {ty}:
            continue
        need.add((cal.name, ty, _const_sig(n.args)))
    return need


def _const_sig(args):
    """实参全为字面量时返回常量元组，否则 None。"""
    vals = []
    for a in args:
        if isinstance(a, A.Num) and not isinstance(a.val, bool):
            vals.append(a.val if isinstance(a.val, float) else int(a.val))
        elif isinstance(a, A.UnaryOp) and a.op in ("-", "+") and isinstance(a.child, A.Num) \
                and not isinstance(a.child.val, bool):
            v = a.child.val
            v = float(v) if isinstance(v, float) else int(v)
            vals.append(-v if a.op == "-" else v)
        else:
            return None
    return tuple(vals)


class _NativeGen:
    """把泛型/标注函数体生成为原生 C（参数与返回都是原生类型，装箱在调用点）。"""

    def __init__(self, defn, ty, consts=None, safe=None):
        self.defn = defn
        self.ty = ty
        self.ctype = CTYPE[ty]
        self.params = list(defn.params)
        self.consts = consts or None   # 常量特化：参数名 → 常量值
        self.safe = safe or set()      # 区间证明可省溢出检查的 BinOp id
        self.reassigned = _reassigned_names(defn) if consts else set()
        self.tail = False              # 4.7.1：自尾调用是否已转成循环
        self.locals = []          # 声明顺序
        self.lines = []

    def _p(self, name):
        return "p_" + name

    def _v(self, name):
        return "v_" + name

    def _decl(self, name):
        if name not in self.locals:
            self.locals.append(name)

    # ---- 表达式 ----
    def expr(self, n):
        if isinstance(n, A.Num):
            if isinstance(n.val, float):
                if self.ty != "f64":
                    raise NativeUnsupported("小数在整型实例里")
                return repr(float(n.val))
            return "%dLL" % int(n.val)
        if isinstance(n, A.Bool):
            return "1" if n.val else "0"
        if isinstance(n, A.Var):
            if n.name in self.params:
                if self.consts and n.name in self.consts and n.name not in self.reassigned:
                    v = self.consts[n.name]
                    if isinstance(v, float):
                        return repr(float(v))
                    return "%dLL" % int(v)
                return self._p(n.name)
            self._decl(n.name)
            return self._v(n.name)
        if isinstance(n, A.BinOp):
            op = n.op
            l = self.expr(n.left)
            r = self.expr(n.right)
            if op in LOGIC:
                return "(%s %s %s)" % (self._truth(l), LOGIC[op], self._truth(r))
            if op in CMP:
                return "(%s %s %s)" % (l, op, r)
            if op in ARITH:
                if self.ty == "i64":
                    if _int_ck() and id(n) not in self.safe:
                        return "%s(%s, %s)" % (
                            {"+": "rt_ck_add", "-": "rt_ck_sub", "*": "rt_ck_mul"}[op], l, r)
                    return "(%s %s %s)" % (l, op, r)
                return "(%s %s %s)" % (l, op, r)
            if op in ARITH_F and self.ty == "f64":
                return "(%s %s %s)" % (l, op, r)
            raise NativeUnsupported("运算符 %s 不在原生子集里" % op)
        if isinstance(n, A.UnaryOp):
            if n.op == "-":
                return "(-%s)" % self.expr(n.child)
            if n.op == "not":
                return "(!%s)" % self._truth(self.expr(n.child))
            if n.op == "+":
                return self.expr(n.child)
            raise NativeUnsupported("一元运算 %s 不在原生子集里" % n.op)
        if isinstance(n, A.TernaryExpr):
            return "(%s ? %s : %s)" % (self._truth(self.expr(n.cond)),
                                       self.expr(n.true_expr),
                                       self.expr(n.false_expr))
        if isinstance(n, A.Call) and isinstance(n.callee, A.Var):
            name = n.callee.name
            if name == self.defn.name:
                if len(n.args) != len(self.params):
                    raise NativeUnsupported("自调用参数个数不匹配")
                return "%s(%s)" % (self._cname(),
                                   ", ".join(self.expr(a) for a in n.args))
            raise NativeUnsupported("函数体里调用了 %s（内建/其它函数）" % name)
        raise NativeUnsupported("表达式 %s 不在原生子集里" % type(n).__name__)

    @staticmethod
    def _truth(e):
        return "((%s) != 0)" % e

    def _is_self_tail(self, n):
        """`return self(...)` 且实参个数匹配 → 可转成「赋值参数 + continue」。"""
        if not isinstance(n, A.Return) or n.expr is None:
            return False
        call = n.expr
        return (isinstance(call, A.Call) and isinstance(call.callee, A.Var)
                and call.callee.name == self.defn.name
                and len(call.args) == len(self.params))

    def _has_self_tail(self):
        if os.environ.get("IMPOR_NO_TAIL") == "1":
            return False   # 仅供对照实验：关掉尾调用优化，观察栈行为
        for n in _walk(self.defn):
            if self._is_self_tail(n):
                return True
        return False

    def _cname(self):
        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_"
                       for ch in self.defn.name)
        name = "gp_%s_%s" % (safe, self.ty)
        if self.consts:
            parts = []
            for k in self.params:
                v = self.consts.get(k)
                if v is None:
                    parts.append("x")
                elif isinstance(v, float):
                    parts.append("f" + ("%g" % v).replace("-", "n").replace(".", "d"))
                else:
                    parts.append(str(v).replace("-", "n"))
            suffix = "_k" + "_".join(parts)
            if len(suffix) > 28:
                import hashlib
                suffix = "_k" + hashlib.sha1(suffix.encode()).hexdigest()[:10]
            name += suffix
        return name

    # ---- 语句 ----
    def stmt(self, n, depth):
        ind = "    " * depth
        if isinstance(n, A.Assign):
            self._decl(n.name)
            self.lines.append("%s%s = %s;" % (ind, self._v(n.name), self.expr(n.expr)))
            return
        if isinstance(n, A.CompoundAssign):
            if n.op not in ("+=", "-=", "*="):
                raise NativeUnsupported("复合赋值 %s 不在原生子集里" % n.op)
            self._decl(n.name)
            if self.ty == "i64" and _int_ck():
                fn = {"+=": "rt_ck_add", "-=": "rt_ck_sub", "*=": "rt_ck_mul"}[n.op]
                self.lines.append("%s%s = %s(%s, %s);" % (ind, self._v(n.name), fn,
                                                           self._v(n.name), self.expr(n.expr)))
            else:
                self.lines.append("%s%s %s %s;" % (ind, self._v(n.name), n.op,
                                                   self.expr(n.expr)))
            return
        if isinstance(n, A.Return):
            if self.tail and self._is_self_tail(n):
                # 只有外层真的建了 while(1) 才能安全生成 continue；
                # IMPOR_NO_TAIL=1 关掉尾优化时要走回普通递归调用。
                tmps = []
                for i, a in enumerate(n.expr.args):
                    t = "__tail%d" % i
                    self.lines.append("%s%s %s = %s;" % (ind, self.ctype, t, self.expr(a)))
                    tmps.append(t)
                for p, t in zip(self.params, tmps):
                    self.lines.append("%s%s = %s;" % (ind, self._p(p), t))
                self.lines.append("%scontinue;" % ind)
                return
            if n.expr is None:
                self.lines.append("%sreturn %s;" % (ind, "0LL" if self.ty == "i64" else "0.0"))
                return
            self.lines.append("%sreturn %s;" % (ind, self.expr(n.expr)))
            return
        if isinstance(n, A.If):
            self.lines.append("%sif (%s) {" % (ind, self._truth(self.expr(n.cond))))
            for s in n.body:
                self.stmt(s, depth + 1)
            self.lines.append("%s}" % ind)
            for cond, body in (n.elseifs or []):
                self.lines.append("%selse if (%s) {" % (ind, self._truth(self.expr(cond))))
                for s in body:
                    self.stmt(s, depth + 1)
                self.lines.append("%s}" % ind)
            if n.elseb:
                self.lines.append("%selse {" % ind)
                for s in n.elseb:
                    self.stmt(s, depth + 1)
                self.lines.append("%s}" % ind)
            return
        if isinstance(n, A.While):
            self.lines.append("%swhile (%s) {" % (ind, self._truth(self.expr(n.cond))))
            for s in n.body:
                self.stmt(s, depth + 1)
            self.lines.append("%s}" % ind)
            return
        if isinstance(n, A.For):
            # 4.7：支持 `for i in range(...)`，这是「整段长循环不装箱」的前提：
            # 循环变量与累加器都在函数开头的原生类型里，循环体内不再出现 Val。
            if self.ty != "i64":
                raise NativeUnsupported("浮点实例不做 range 循环")
            it = n.iter_expr
            if not (isinstance(it, A.Call) and isinstance(it.callee, A.Var)
                    and it.callee.name == "range"):
                raise NativeUnsupported("for 只支持 range(...)")
            args = list(it.args)
            if len(args) == 1:
                start, stop, step = "0", self.expr(args[0]), "1"
            elif len(args) == 2:
                start, stop, step = self.expr(args[0]), self.expr(args[1]), "1"
            elif len(args) == 3:
                start, stop = self.expr(args[0]), self.expr(args[1])
                step = self.expr(args[2])
            else:
                raise NativeUnsupported("range 参数个数 %d" % len(args))
            # 步长为负（字面量负号）时用 >=；非字面量步长不做原生（判不出方向）
            if len(args) == 3 and not isinstance(args[2], (A.Num, A.UnaryOp)):
                raise NativeUnsupported("步长不是字面量")
            neg = len(args) == 3 and (
                (isinstance(args[2], A.Num) and args[2].val < 0) or
                (isinstance(args[2], A.UnaryOp) and args[2].op == "-"))
            self._decl(n.var)
            self.lines.append("%sfor (%s = %s; %s %s %s; %s += %s) {"
                              % (ind, self._v(n.var), start, self._v(n.var),
                                 ">=" if neg else "<", stop, self._v(n.var), step))
            for s in n.body:
                self.stmt(s, depth + 1)
            self.lines.append("%s}" % ind)
            return
        raise NativeUnsupported("语句 %s 不在原生子集里" % type(n).__name__)

    def build(self):
        rt = str(getattr(self.defn, "ret_type", "") or "").strip()
        if rt not in ("T", "int", "float"):
            raise NativeUnsupported("返回类型标注不是 T/int/float")
        if rt == "int" and self.ty != "i64":
            raise NativeUnsupported("int 标注与实例类型不一致")
        if rt == "float" and self.ty != "f64":
            raise NativeUnsupported("float 标注与实例类型不一致")
        self.tail = self._has_self_tail()
        if self.tail:
            # 自尾调用已转成 `continue`，整段包进 while(1)，栈深度不再随递归增长。
            self.lines.append("    while (1) {")
            for s in self.defn.body:
                self.stmt(s, 2)
            self.lines.append("    }")
        else:
            for s in self.defn.body:
                self.stmt(s, 1)
        self.lines.append("    return %s;" % ("0LL" if self.ty == "i64" else "0.0"))
        # 4.7.1：返回原生类型（不是 Val）。这样 `fib(n-1) + fib(n-2)` 这类
        # 「返回值直接参与算术」的写法在实例内部全程是 long long，不再出现
        # rt_ck_add(Val, Val) 的编译错误；装箱只发生在实例的**调用点**。
        sig = "static %s %s(%s)" % (
            self.ctype,
            self._cname(),
            ", ".join("%s %s" % (self.ctype, self._p(p)) for p in self.params) or "void")
        body = []
        for name in self.locals:
            body.append("    %s %s = 0;" % (self.ctype, self._v(name)))
        body.extend(self.lines)
        return sig + " {\n" + "\n".join(body) + "\n}"


def plan(ast_node):
    """扫描整棵 AST，返回单态化计划：{"specs": {(name, ty, sig): spec}, "c": [C 行]}。

    三类来源合并到同一张 specs 表（key 都是 (函数名, 实例类型, 常量签名)）：
      1. 泛型 `def f<T>(a:T)` —— 按调用点字面量定型（4.6 的行为，未变）；
      2. 全标注标量函数 `def f(n: int) -> int` —— 按标注定型（4.7 新增）；
      3. 同一函数被**字面量常量**调用 —— 额外生成常量特化实例（4.7.1 新增）：
         常量写进函数体 + `rangeproof` 用它算出区间，从而省掉逐运算溢出检查。
    """
    gens = _generic_defs(ast_node)
    labs = _labeled_defs(ast_node)
    want = [(gens[name], ty, "泛型", None) for (name, ty) in _call_sites(ast_node, gens)]
    for (name, ty, sig) in sorted(_labeled_call_sites(ast_node, labs),
                                  key=lambda x: (x[0], x[1], repr(x[2]))):
        if name in gens:
            continue
        d = labs[name][0]
        if sig is not None and _has_self_call(d):
            # 自递归函数的形参会被递归调用改写（尾调用写回、非尾调用传新值），
            # 常量因此不是不变量：内联进去会让 `f(30)` 里的 p_n 永远等于 30，
            # 尾递归版本直接变成死循环。这类函数只保留通用实例。
            sig = None
        want.append((d, ty, "标注" if sig is None else "标注+常量", sig))
        if sig is not None:
            # 保底：非字面量的调用点仍需一个通用实例
            want.append((d, ty, "标注", None))
    specs = {}
    code = []
    tail_count = 0
    proof_stats = {"arith": 0, "proven": 0}
    for (defn, ty, kind, sig) in want:
        if (defn.name, ty, sig) in specs:
            continue
        consts = dict(zip(defn.params, sig)) if sig else None
        safe = set()
        if ty == "i64" and consts and os.environ.get("IMPOR_NO_RANGEPROOF", "") not in ("1", "true", "yes"):
            try:
                safe, pstats = rangeproof.prove(defn, consts)
                proof_stats["arith"] += pstats.get("arith", 0)
                proof_stats["proven"] += len(safe)
            except Exception:
                safe = set()
        try:
            gen = _NativeGen(defn, ty, consts, safe)
            text = gen.build()
        except NativeUnsupported:
            continue
        if gen.tail:
            tail_count += 1
        specs[(defn.name, ty, sig)] = {"name": defn.name, "ty": ty,
                                       "argc": len(defn.params), "sig": sig,
                                       "cname": gen._cname(),
                                       "unchecked": len(safe)}
        code.append("")
        code.append("/* 单态化（%s）：%s → %s 实例%s（擦除版仍保留作回退） */"
                    % (kind, defn.name, ty,
                       "" if not safe else "，区间证明免检 %d 处" % len(safe)))
        code.append(text)
    return {"specs": specs, "c": code, "proof": proof_stats, "tail": tail_count}


def call_spec(mono, name, args):
    """调用点查询：命中则返回实例规格，否则 None（回退擦除版）。

    先找「实参全是字面量」的常量特化实例，再退回通用实例。
    """
    if not mono or not mono["specs"]:
        return None
    tys = [_stype(a) for a in args]
    if any(t is None for t in tys) or len(set(tys)) != 1:
        return None
    sig = _const_sig(args)
    for key in ((name, tys[0], sig), (name, tys[0], None)):
        spec = mono["specs"].get(key)
        if spec is not None and spec["argc"] == len(args):
            return spec
    return None


def call_code(spec, arg_exprs, lineno):
    """生成对专用实例的调用：实参从 Val 解箱，**返回值在调用点装箱**。

    4.7.1 起实例本身返回原生类型（`long long` / `double`），所以装箱移到这里；
    好处是实例内部再调用同类实例时拿到的是原生值，能直接参与算术。
    """
    if spec["ty"] == "i64":
        conv = [("(long long)(%s).i" % a) for a in arg_exprs]
        box = "v_int"
    else:
        conv = [("rt_num(%s)" % a) for a in arg_exprs]
        box = "v_float"
    return ("({ rt_trace_push(\"%s\", %d); %s _tr = %s(%s); rt_trace_pop(); "
            "%s(_tr); })"
            % (spec["name"], lineno, CTYPE[spec["ty"]], spec["cname"],
               ", ".join(conv), box))
