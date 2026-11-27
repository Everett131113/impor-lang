"""语言层一般内联（4.7.2）。

目标：把「单表达式小函数」在调用点展开，省掉一次 Val 装箱 / 函数调用 / 参数数组分配。
这是一个 AST → AST 的 pass，在 `link_modules` 与 `_reject_const_mutation` 之后、
`CGen.gen_program` 之前执行，对完整后端（ctrans）与 IR 后端共用同一份 AST。

判据（可复核，全部显式写在这份文档里，不是"大概"）：

候选函数（必须同时满足）
  1. `Def`，且 `body` 恰好 1 条语句、该语句是 `Return(expr)`，`expr` 非空；
  2. 参数个数 <= 4；
  3. 非 async、无 type_params（泛型交给 monomorph，不在这里掺）；
  4. `expr` 的 AST 节点数 <= IMPOR_INLINE_MAX_NODES（默认 40）；
  5. `expr` 内不含任何「有副作用 / 有控制流 / 会再调用」的节点：
     Call / Await / Lambda / NewInstance / SuperCall / AssertNode / Raise / Print /
     Assign / CompoundAssign / AttrSet / IndexSet / ImportNode / FromImportNode；
  6. `expr` 不引用函数自身名字（自递归不内联）。

调用点（必须同时满足）
  1. `Call.callee` 是 `Var(name)`，且 name 命中候选表；
  2. 实参个数 == 形参个数；
  3. 对每个形参 p，令 k = p 在候选 expr 中出现次数：
       k == 0  -> 允许，但实参必须是「纯」的（Num/Str/Bool/Null/Var），否则跳过（不能把副作用丢掉）；
       k == 1  -> 允许（实参求值一次，与原来一致）；
       k >  1  -> 仅当实参是纯的才允许（否则会重复求值，语义改变）；
  4. 内联层数 <= IMPOR_INLINE_DEPTH（默认 2），防止炸开。

错误位置判据（这是内联最容易出事的地方，因此单独定死）
  - 内联进来的整棵子树，`lineno` / `col` 一律改写为**调用点**的行列；
  - 于是 E1012（整数溢出）、除零、类型错误、越界等运行期报错，指向的是**源码里写着调用的那一行**，
    而不是被调函数定义内部那一行 —— 与内联前的用户体验一致（内联前栈顶也是调用点）；
  - `IMPOR_INLINE_TRACE=1` 逐条打印「函数@行 -> 调用点行」，可人工核对。

开关
  - IMPOR_NO_INLINE=1        关闭整个 pass（回归对照用）
  - IMPOR_INLINE_MAX_NODES  候选体积上限，默认 40
  - IMPOR_INLINE_DEPTH      最多叠几层，默认 2
  - IMPOR_INLINE_TRACE=1    打印每次内联

统计
  - stats["inline"]      成功内联次数
  - stats["inline_skip"] {"体积": n, "副作用": n, "自递归": n, "实参不纯": n, "层数": n}
"""
from __future__ import annotations

import copy
import os

from . import ast_nodes as A


# 出现在候选 expr 里就一律放弃的节点类型（有副作用 / 控制流 / 会再调用）
_BLOCKING = (
    A.Call, A.Await, A.Lambda, A.NewInstance, A.SuperCall, A.AssertNode, A.Raise,
    A.Assign, A.CompoundAssign, A.AttrSet, A.IndexSet,
    A.ImportNode, A.FromImportNode, A.Print, A.ExitScript,
)
# 纯实参：无副作用、可重复求值
_PURE_ARGS = (A.Num, A.Str, A.Bool, A.Null, A.Var)


def _enabled() -> bool:
    return os.environ.get("IMPOR_NO_INLINE", "") not in ("1", "true", "yes")


def _max_nodes() -> int:
    try:
        return max(4, int(os.environ.get("IMPOR_INLINE_MAX_NODES", "40")))
    except ValueError:
        return 40


def _max_depth() -> int:
    try:
        return max(1, int(os.environ.get("IMPOR_INLINE_DEPTH", "2")))
    except ValueError:
        return 2


def _trace() -> bool:
    return os.environ.get("IMPOR_INLINE_TRACE", "") in ("1", "true", "yes")


def _children(node):
    """产出 node 的直接子 AST 节点（含 list / tuple 容器里的）。"""
    for key, val in vars(node).items():
        if key in ("lineno", "col"):
            continue
        if isinstance(val, A.AST):
            yield key, None, val
        elif isinstance(val, (list, tuple)):
            for i, item in enumerate(val):
                if isinstance(item, A.AST):
                    yield key, i, item


def _count_nodes(node) -> int:
    n = 1
    for _k, _i, ch in _children(node):
        n += _count_nodes(ch)
    return n


def _collect_names(node):
    """收集节点里出现的所有 Var.name。"""
    names = []
    if isinstance(node, A.Var):
        names.append(node.name)
    for _k, _i, ch in _children(node):
        names.extend(_collect_names(ch))
    return names


def _has_blocking(node, self_name):
    """True 表示这棵 expr 不能当内联候选（有副作用/控制流/自递归）。"""
    if isinstance(node, _BLOCKING):
        return True
    if isinstance(node, A.Var) and node.name == self_name:
        return True
    for _k, _i, ch in _children(node):
        if _has_blocking(ch, self_name):
            return True
    return False


def _set_pos(node, lineno, col):
    node.lineno = lineno
    node.col = col
    for _k, _i, ch in _children(node):
        _set_pos(ch, lineno, col)


def _subst(node, mapping):
    """把 Var(p) 替换成 mapping[p] 的副本。原地改容器，返回（可能新的）节点。"""
    if isinstance(node, A.Var) and node.name in mapping:
        return copy.deepcopy(mapping[node.name])
    for key, idx, ch in list(_children(node)):
        new = _subst(ch, mapping)
        if new is not ch:
            if idx is None:
                setattr(node, key, new)
            else:
                getattr(node, key)[idx] = new
    return node


class _InlinePass:
    def __init__(self, root):
        self.root = root
        self.cands = {}
        self.hits = 0
        self.skips = {"体积": 0, "副作用": 0, "自递归": 0, "实参不纯": 0, "层数": 0}
        self.max_nodes = _max_nodes()
        self.max_depth = _max_depth()
        self.trace = _trace()

    # ---- 候选收集 ----
    def collect(self):
        for node in self._defs(self.root):
            name = node.name
            if getattr(node, "is_async", False):
                continue
            if getattr(node, "type_params", None):
                continue
            body = getattr(node, "body", None)
            stmts = getattr(body, "statements", None) or getattr(body, "stmts", None)
            if stmts is None and isinstance(body, list):
                stmts = body
            if not stmts or len(stmts) != 1:
                continue
            stmt = stmts[0]
            if not isinstance(stmt, A.Return) or getattr(stmt, "expr", None) is None:
                continue
            expr = stmt.expr
            params = [p if isinstance(p, str) else getattr(p, "name", str(p))
                      for p in (node.params or [])]
            if len(params) > 4:
                continue
            if _count_nodes(expr) > self.max_nodes:
                self.skips["体积"] += 1
                continue
            if _has_blocking(expr, name):
                if any(isinstance(x, A.Call) for x in self._iter(expr)):
                    self.skips["副作用"] += 1
                else:
                    self.skips["自递归"] += 1
                continue
            self.cands[name] = (params, expr)

    def _defs(self, node):
        if isinstance(node, A.Def):
            yield node
        for _k, _i, ch in _children(node):
            yield from self._defs(ch)

    def _iter(self, node):
        yield node
        for _k, _i, ch in _children(node):
            yield from self._iter(ch)

    # ---- 调用点展开 ----
    def run(self):
        self.collect()
        if not self.cands:
            return self
        self._rewrite(self.root, 0)
        return self

    def _rewrite(self, node, depth):
        """递归重写 node 的所有子位置；返回替换后的节点。"""
        for key, idx, ch in list(_children(node)):
            new = self._try_inline(ch, depth)
            if new is not ch:
                if idx is None:
                    setattr(node, key, new)
                else:
                    getattr(node, key)[idx] = new
        return node

    def _try_inline(self, node, depth):
        if not isinstance(node, A.Call):
            return self._rewrite(node, depth)
        cand = self.cands.get(node.callee.name) if isinstance(node.callee, A.Var) else None
        if cand is None:
            return self._rewrite(node, depth)
        if depth >= self.max_depth:
            self.skips["层数"] += 1
            return self._rewrite(node, depth)
        params, expr = cand
        args = node.args or []
        if len(args) != len(params):
            return self._rewrite(node, depth)
        mapping = {}
        for p, a in zip(params, args):
            k = _collect_names(expr).count(p)
            if k == 0:
                if not isinstance(a, _PURE_ARGS):
                    self.skips["实参不纯"] += 1
                    return self._rewrite(node, depth)
                continue
            if k > 1 and not isinstance(a, _PURE_ARGS):
                self.skips["实参不纯"] += 1
                return self._rewrite(node, depth)
            mapping[p] = a
        body = copy.deepcopy(expr)
        body = _subst(body, mapping)
        # 报错位置判据：整棵内联子树的行列 = 调用点
        _set_pos(body, getattr(node, "lineno", 0), getattr(node, "col", 0))
        self.hits += 1
        if self.trace:
            import sys
            print("[inline] %s -> 第 %d 行" % (node.callee.name, getattr(node, "lineno", 0)),
                  file=sys.stderr)
        # 允许内联出来的子树再被内联（受 depth 限制）
        return self._try_inline(body, depth + 1)


def inline_program(ast_node, stats=None, source_name=None):
    """对整棵 AST 做内联。返回 AST（可能被原地改写）。"""
    if not _enabled():
        if stats is not None:
            stats["inline"] = 0
            stats["inline_off"] = True
        return ast_node
    p = _InlinePass(ast_node).run()
    if stats is not None:
        stats["inline"] = p.hits
        stats["inline_skip"] = dict(p.skips)
        stats["inline_cands"] = len(p.cands)
    return ast_node
