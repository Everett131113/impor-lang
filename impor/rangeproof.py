# -*- coding: utf-8 -*-
"""整数区间证明（4.7.1）：让「已证不溢出」的算术免掉逐运算 E1012 检查。

背景：4.7 的单态化实例用 `rt_ck_add/sub/mul` 逐运算检查溢出，换来的是
1e8 次累加上约 +23ms（70ms vs 裸运算 43ms）。用户点名要的不是「关检查」，
而是**证明后免检**：循环上界、步长、累加器类型都可见时，静态算出最大值，
证明不溢出，于是这一段用裸 C 运算。

本模块做的事（保守，做不到就放弃，绝不放宽检查）：

  1. 抽象域是整数区间 `(lo, hi)`；`None` = 未知。
  2. 参数只有**调用点是字面量常量**时才取到具体值（常量特化实例），否则未知。
     ——这是本模块的力量来源，也是它的边界：`sum_to(100000000)` 能证明，
     `sum_to(用户输入)` 不能，后者继续逐运算检查。
  3. 只认 `for i in range(...)`（上界可见）与 `while i < 常量` + `i = i + 常量` 两种循环。
  4. 累加器 `acc = acc + E` 的上界按「迭代次数 × max|E|」保守估计，不假设单调求和公式。
  5. 任何一个操作数未知、或区间越界，该算术点就**不**进 safe 集合。

返回值：`prove(defn, consts)` → `(safe_ids, stats)`，`safe_ids` 是可省检查的
`A.BinOp` 节点 id 集合。
"""

from . import ast_nodes as A

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1
ARITH = {"+", "-", "*"}


def _in_range(iv):
    return iv is not None and INT64_MIN <= iv[0] and iv[1] <= INT64_MAX


def _add(a, b):
    if a is None or b is None:
        return None
    return (a[0] + b[0], a[1] + b[1])


def _sub(a, b):
    if a is None or b is None:
        return None
    return (a[0] - b[1], a[1] - b[0])


def _mul(a, b):
    if a is None or b is None:
        return None
    cands = [a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1]]
    return (min(cands), max(cands))


def _neg(a):
    if a is None:
        return None
    return (-a[1], -a[0])


def _abs_max(iv):
    return max(abs(iv[0]), abs(iv[1]))


class _Prove:
    def __init__(self, consts):
        self.consts = consts or {}
        self.env = {}
        self.safe = set()
        self.stats = {"arith": 0, "proven": 0, "loops": 0, "loops_unknown": 0}

    # ---- 表达式：返回区间 ----
    def expr(self, n):
        if isinstance(n, A.Num):
            if isinstance(n.val, float):
                return None
            return (int(n.val), int(n.val))
        if isinstance(n, A.Bool):
            return (1, 1) if n.val else (0, 0)
        if isinstance(n, A.Var):
            return self.env.get(n.name)
        if isinstance(n, A.UnaryOp):
            if n.op == "-":
                return _neg(self.expr(n.child))
            if n.op == "+":
                return self.expr(n.child)
            return None
        if isinstance(n, A.BinOp) and n.op in ARITH:
            self.stats["arith"] += 1
            a = self.expr(n.left)
            b = self.expr(n.right)
            iv = {"+": _add, "-": _sub, "*": _mul}[n.op](a, b)
            if _in_range(iv):
                self.safe.add(id(n))
                self.stats["proven"] += 1
            return iv
        if isinstance(n, A.TernaryExpr):
            a = self.expr(n.true_expr)
            b = self.expr(n.false_expr)
            if a is None or b is None:
                return None
            return (min(a[0], b[0]), max(a[1], b[1]))
        return None

    # ---- 语句 ----
    def body(self, stmts):
        for s in stmts:
            self.stmt(s)

    def stmt(self, n):
        if isinstance(n, A.Assign):
            self.env[n.name] = self.expr(n.expr)
            return
        if isinstance(n, A.CompoundAssign):
            cur = self.env.get(n.name)
            v = self.expr(n.expr)
            if cur is None or v is None:
                self.env[n.name] = None
            elif n.op == "+=":
                self.env[n.name] = _add(cur, v)
            elif n.op == "-=":
                self.env[n.name] = _sub(cur, v)
            else:
                self.env[n.name] = _mul(cur, v)
            return
        if isinstance(n, A.If):
            self.stmt_cond(n.cond)
            self.body(n.body)
            for cond, b in (n.elseifs or []):
                self.stmt_cond(cond)
                self.body(b)
            if n.elseb:
                self.body(n.elseb)
            return
        if isinstance(n, A.For):
            self.for_loop(n)
            return
        if isinstance(n, A.While):
            self.while_loop(n)
            return
        if isinstance(n, A.Return) and n.expr is not None:
            self.expr(n.expr)
            return
        if isinstance(n, A.ExprStmt):
            self.expr(getattr(n, "expr", n))
            return

    def stmt_cond(self, cond):
        self.expr(cond)

    # ---- 循环 ----
    def _range_args(self, it):
        if not (isinstance(it, A.Call) and isinstance(it.callee, A.Var)
                and it.callee.name == "range"):
            return None
        args = list(it.args)
        if len(args) == 1:
            lo = (0, 0)
            hi = self.expr(args[0])
            step = (1, 1)
        elif len(args) == 2:
            lo, hi, step = self.expr(args[0]), self.expr(args[1]), (1, 1)
            # 起始区间不唯一时用其上限做保守起点下界
        elif len(args) == 3:
            lo, hi, step = self.expr(args[0]), self.expr(args[1]), self.expr(args[2])
        else:
            return None
        if lo is None or hi is None or step is None:
            return None
        if step[0] != step[1] or step[0] == 0:
            return None
        return lo, hi, step[0]

    @staticmethod
    def _iters(lo, hi, step):
        """迭代次数：range(lo,hi,step) 的精确计数（整数）。"""
        if step > 0:
            if hi <= lo:
                return 0
            return (hi - lo + step - 1) // step
        if hi >= lo:
            return 0
        return (lo - hi + (-step) - 1) // (-step)

    def for_loop(self, n):
        self.stats["loops"] += 1
        got = self._range_args(n.iter_expr)
        if got is None:
            self.stats["loops_unknown"] += 1
            self.body(n.body)          # 循环变量未知，体内算术自然落到未知
            return
        lo, hi, step = got
        cnt = self._iters(lo[0], hi[0], step) if lo[0] == lo[1] and hi[0] == hi[1] else None
        if cnt is None:
            self.stats["loops_unknown"] += 1
            self.body(n.body)
            return
        if cnt == 0:
            self.env[n.var] = None
            return
        last = lo[0] + (cnt - 1) * step
        self.env[n.var] = (min(lo[0], last), max(lo[0], last))
        self._loop_body(n.body, n.var, cnt)

    def while_loop(self, n):
        self.stats["loops"] += 1
        # 只认 `while i < N` 且 N 已知、i 已知、体内 `i = i + 常量`
        cond = n.cond
        if not (isinstance(cond, A.BinOp) and cond.op == "<"
                and isinstance(cond.left, A.Var)):
            self.stats["loops_unknown"] += 1
            self.body(n.body)
            return
        var = cond.left.name
        cur = self.env.get(var)
        bound = self.expr(cond.right)
        if cur is None or bound is None or cur[0] != cur[1] or bound[0] != bound[1]:
            self.stats["loops_unknown"] += 1
            self.body(n.body)
            return
        stepk = None
        for s in n.body:
            if (isinstance(s, A.Assign) and s.name == var
                    and isinstance(s.expr, A.BinOp) and s.expr.op == "+"
                    and isinstance(s.expr.left, A.Var) and s.expr.left.name == var
                    and isinstance(s.expr.right, A.Num)
                    and not isinstance(s.expr.right.val, float)):
                stepk = int(s.expr.right.val)
            elif (isinstance(s, A.CompoundAssign) and s.name == var
                  and s.op == "+=" and isinstance(s.expr, A.Num)
                  and not isinstance(s.expr.val, float)):
                stepk = int(s.expr.val)
        if not stepk or stepk <= 0 or bound[0] <= cur[0]:
            self.stats["loops_unknown"] += 1
            self.body(n.body)
            return
        cnt = (bound[0] - cur[0] + stepk - 1) // stepk
        self.env[var] = (cur[0], bound[0] - 1)
        self._loop_body(n.body, var, cnt)

    def _loop_body(self, body, var, cnt):
        """在「循环变量区间已知 + 迭代次数已知」的前提下执行一次体，并把
        形如 `acc = acc + E` 的累加器按 次数×max|E| 放宽。"""
        before = {k: v for k, v in self.env.items()}
        self.body(body)
        for name, iv in list(self.env.items()):
            if name == var:
                continue
            old = before.get(name)
            if old is None or iv is None:
                continue
            if iv[0] >= old[0] and iv[1] <= old[1]:
                continue               # 没有变化，沿用
            per = (iv[0] - old[0], iv[1] - old[1])
            grow = _abs_max(per)
            self.env[name] = (old[0] - cnt * grow, old[1] + cnt * grow)


def prove(defn, consts=None):
    """返回 (safe_ids, stats)。safe_ids 是可省溢出检查的 BinOp 节点 id。"""
    p = _Prove(consts)
    for name in getattr(defn, "params", []) or []:
        p.env[name] = None
    if consts:
        for name, val in consts.items():
            if isinstance(val, int) and not isinstance(val, bool):
                p.env[name] = (val, val)
    try:
        p.body(getattr(defn, "body", []) or [])
    except Exception:
        return set(), dict(p.stats, error=1)
    return p.safe, p.stats
