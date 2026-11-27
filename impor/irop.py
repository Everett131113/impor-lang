# -*- coding: utf-8 -*-
"""IR 优化 pass（4.7.0）：常量折叠 / 死代码消除 / 循环不变量外提。

为什么在 IR 上做而不是 AST：AST 的求值顺序里藏着副作用（`f() + g()` 谁先谁后
在 Python 语义下不保证从左到右的副作用顺序可交换），而 IR 已经是显式的三地址码：
每条指令的读写集合是清楚的，优化可以在「不改变可见行为」的前提下做。

三项 pass 的分工（都只做**可证明安全**的形态，宁少不多）：

  1. 常量折叠：`BINOP dst, op, Lit, Lit` → `CONST dst, v`。只在结果能落在
     int64 内、且不涉及除零时折叠 —— 否则保留原指令，让运行时的 E1012 照常报。
     `JUMP_IF_FALSE Lit, L` 也按真值简化成「删除」或「无条件跳转」。

  2. 死代码消除：删除「目的量在全函数从未被读」的 `CONST` 与 `MOVE`。
     这两类没有副作用、也不会抛错，删掉最安全；算术类即使结果没人用也可能抛
     溢出/除零，所以**不删**（宁可留一条无用指令，也不改错误行为）。
     另外删掉「JUMP 到下一条 LABEL」的空跳转。

  3. 循环不变量外提：识别 `LABEL L ... JUMP L` 的回边区间，把区间内
     「读取一个在循环内从未被写的名字」的 `LOAD`、以及常量 `CONST` 提到循环前。
     `LOAD` 一定会成功或一定在第一次迭代抛错，外提与内联的可见行为一致。

优化是按函数做的，纯结构化改写，不新增指令种类；因此 IR 求值器、cir、对拍
三端不需要任何新分支。
"""
from . import ir as _ir


class _None(object):
    pass


_NONE = _None()

ARITH = {"+", "-", "*", "/", "//", "%", "**"}
CMP = {"==", "!=", "<", ">", "<=", ">="}
LOGIC = {"and", "or"}
INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1


class OptStats(object):
    __slots__ = ("folded", "dead", "hoisted", "jumps", "before", "after")

    def __init__(self):
        self.folded = 0
        self.dead = 0
        self.hoisted = 0
        self.jumps = 0
        self.before = 0
        self.after = 0

    def __repr__(self):
        return ("folded=%d dead=%d hoisted=%d jumps=%d (%d→%d)"
                % (self.folded, self.dead, self.hoisted, self.jumps,
                   self.before, self.after))


def _lit(x):
    return x if isinstance(x, _ir.Lit) else None


def _fold(op, a, b):
    """折叠二元运算；不可折叠（除零 / 越 int64 / 非字面量）返回 _NONE。"""
    if isinstance(a, bool) or isinstance(b, bool):
        if op in LOGIC:
            return (a and b) if op == "and" else (a or b)
        if op in CMP and type(a) is type(b):
            try:
                return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b,
                        "==": a == b, "!=": a != b}[op]
            except KeyError:
                return _NONE
    if op in ARITH:
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            if op == "+" and isinstance(a, str) and isinstance(b, str):
                return a + b
            return _NONE
        try:
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            elif op == "/":
                r = a / b
            elif op == "//":
                r = a // b
            elif op == "%":
                r = a % b
            elif op == "**":
                if isinstance(b, float) or (isinstance(b, int) and b > 4096):
                    return _NONE
                r = a ** b
            else:
                return _NONE
        except (ZeroDivisionError, OverflowError, ValueError, TypeError):
            return _NONE
        if isinstance(r, int) and not isinstance(r, bool):
            if r < INT64_MIN or r > INT64_MAX:
                return _NONE      # 让运行时的 E1012 照常报，不静默折叠成大数
        return r
    if op in CMP:
        try:
            return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b,
                    "==": a == b, "!=": a != b}[op]
        except (KeyError, TypeError):
            return _NONE
    return _NONE


def _srcs(ins):
    """这条指令读取了哪些名字（dst 不算）。不认识的一律保守当作「读全部」。"""
    op, a = ins.op, ins.args
    if op in ("CONST", "LABEL", "JUMP", "TRY_BEGIN", "TRY_END", "CATCH_BEGIN",
              "CATCH_END", "TRY_CLOSE", "RETHROW"):
        if op == "TRY_BEGIN":
            return set()      # 三个参数都是标签
        if op == "TRY_CLOSE":
            return {a[1]} if len(a) > 1 and isinstance(a[1], str) else set()
        return set()
    if op == "LOAD":
        return {a[1]}
    if op == "MOVE":
        return {a[1]}
    if op == "BINOP":
        return {x for x in (a[2], a[3]) if isinstance(x, str)}
    if op == "UNOP":
        return {a[2]} if isinstance(a[2], str) else set()
    if op == "STORE":
        return {a[2]} if isinstance(a[2], str) else set()
    if op == "JUMP_IF_FALSE":
        return {a[0]} if isinstance(a[0], str) else set()
    if op == "ASSERT":
        return {a[0]} if isinstance(a[0], str) else set()
    if op == "RETURN":
        return {a[0]} if isinstance(a[0], str) else set()
    if op == "PRINT":
        return {x for x in a if isinstance(x, str)}
    return {x for x in a if isinstance(x, str)}


def _defined(ins):
    """这条指令定义了哪些名字（临时量 / 变量）。"""
    op, a = ins.op, ins.args
    if op in ("CONST", "LOAD", "MOVE", "BINOP", "UNOP", "CALL", "LIST", "DICT",
              "INDEX", "SLICE", "TOSTR", "CONCAT", "ITER", "ITER_NEXT",
              "ERR_MATCH", "NEW", "GETATTR", "CALLMETHOD", "SUPERCALL"):
        return {a[0]}
    if op == "STORE":
        return {a[0]}
    return set()


def _has_side_effect(ins):
    """有副作用的指令：即使结果没人用也不能删。"""
    return ins.op not in ("CONST", "MOVE", "LOAD")


def _fold_body(body, st):
    """常量折叠 + 条件跳转简化，保持指令顺序。"""
    out = []
    consts = {}
    for ins in body:
        op = ins.op
        if op == "BINOP" and len(ins.args) == 4:
            dst, bop, l, r = ins.args
            lv = consts.get(l, l) if isinstance(l, str) else l
            rv = consts.get(r, r) if isinstance(r, str) else r
            ll, rr = _lit(lv), _lit(rv)
            if ll is not None and rr is not None:
                v = _fold(bop, ll.value, rr.value)
                if v is not _NONE:
                    ins = _ir.Instr("CONST", (dst, _ir.Lit(v)), ins.lineno)
                    st.folded += 1
                    consts[dst] = _ir.Lit(v)
                    out.append(ins)
                    continue
        if op == "UNOP" and len(ins.args) == 3:
            dst, uop, x = ins.args
            xv = consts.get(x, x) if isinstance(x, str) else x
            xl = _lit(xv)
            if xl is not None:
                if uop == "-" and isinstance(xl.value, (int, float)):
                    v = -xl.value
                elif uop == "not":
                    v = not xl.value
                elif uop == "+" and isinstance(xl.value, (int, float)):
                    v = xl.value
                else:
                    v = _NONE
                if v is not _NONE and not (isinstance(v, int) and
                                           not isinstance(v, bool) and
                                           (v < INT64_MIN or v > INT64_MAX)):
                    ins = _ir.Instr("CONST", (dst, _ir.Lit(v)), ins.lineno)
                    st.folded += 1
                    consts[dst] = _ir.Lit(v)
                    out.append(ins)
                    continue
        if op == "JUMP_IF_FALSE" and len(ins.args) == 2 and isinstance(ins.args[0], _ir.Lit):
            truth = bool(ins.args[0].value)
            if truth:
                st.jumps += 1
                continue                    # 恒真：不跳，直接去掉
            out.append(_ir.Instr("JUMP", (ins.args[1],), ins.lineno))
            st.jumps += 1
            continue
        if op == "CONST" and len(ins.args) == 2 and _lit(ins.args[1]) is not None:
            consts[ins.args[0]] = ins.args[1]
        else:
            for d in _defined(ins):
                consts.pop(d, None)
        out.append(ins)
    return out


def _dead_body(body, st):
    """删「从未被读」的 CONST/MOVE，以及跳到下一条的空 JUMP。"""
    used = set()
    for ins in body:
        used |= _srcs(ins)
    out = []
    for i, ins in enumerate(body):
        if ins.op in ("CONST", "MOVE") and ins.args and ins.args[0] not in used:
            st.dead += 1
            continue
        if ins.op == "JUMP" and i + 1 < len(body) and body[i + 1].op == "LABEL" \
                and body[i + 1].args[0] == ins.args[0]:
            st.dead += 1
            continue
        out.append(ins)
    return out


def _loops(body):
    """返回所有回边区间的 (label_idx, back_jump_idx, label_name)。"""
    labels = {}
    for i, ins in enumerate(body):
        if ins.op == "LABEL":
            labels[ins.args[0]] = i
    out = []
    for j, ins in enumerate(body):
        if ins.op == "JUMP" and ins.args[0] in labels and labels[ins.args[0]] < j:
            i = labels[ins.args[0]]
            # 只取最内层：区间内不得再含同标签的 LABEL
            if not any(body[k].op == "LABEL" and body[k].args[0] == ins.args[0]
                       for k in range(i + 1, j)):
                out.append((i, j, ins.args[0]))
    return out


def _outer_only(loops):
    """只保留互不重叠的区间，避免嵌套循环外提后索引错位。"""
    out = []
    for t in sorted(loops, key=lambda x: x[0]):
        if all(t[1] <= i2 or t[0] >= j2 for (i2, j2, _n) in out):
            out.append(t)
    return out


def _hoist_body(body, st):
    """把循环里「读循环外变量」的 LOAD 与常量 CONST 提到循环前。"""
    loops = _outer_only(_loops(body))
    if not loops:
        return body
    # 从最外层开始处理会打乱索引，这里按起点从后往前处理（不影响前面的区间）
    for (i, j, name) in sorted(loops, key=lambda t: -t[0]):
        written = set()
        for k in range(i + 1, j):
            written |= _defined(body[k])
        hoist, keep = [], []
        in_loop = body[i + 1:j]
        defs = {}
        for ins in in_loop:
            for d in _defined(ins):
                defs[d] = defs.get(d, 0) + 1
        for ins in in_loop:
            if ins.op == "LOAD" and len(ins.args) == 2:
                dst, src = ins.args
                if isinstance(src, str) and src not in written and defs.get(dst) == 1:
                    hoist.append(ins)
                    st.hoisted += 1
                    continue
            if ins.op == "CONST" and len(ins.args) == 2 and defs.get(ins.args[0]) == 1:
                hoist.append(ins)
                st.hoisted += 1
                continue
            keep.append(ins)
        body = body[:i] + hoist + [body[i]] + keep + body[j:]
    return body


def optimize_body(body, stats=None):
    st = stats or OptStats()
    st.before += len(body)
    body = _fold_body(body, st)
    body = _dead_body(body, st)
    body = _hoist_body(body, st)
    body = _dead_body(body, st)          # 外提后可能又多出可删的
    st.after += len(body)
    return body


def optimize_module(module, stats=None):
    """就地优化一个 IRModule 的全部函数（含类方法与字段初始化段）。"""
    st = stats or OptStats()
    targets = [module.main] + list(module.funcs)
    for c in module.classes:
        targets.extend(list(c.methods))
        if c.field_body:
            c.field_body = optimize_body(c.field_body, st)
    for fn in targets:
        fn.body = optimize_body(fn.body, st)
    return st


def summary(module):
    st = OptStats()
    optimize_module(module, st)
    return st
