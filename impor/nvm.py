# -*- coding: utf-8 -*-
"""
Impor (I++) 自有字节码后端（NVM = Native bytecode Virtual Machine）。

这是 Impor 第一个**不依赖任何外部编译器（gcc / tcc）**的自有后端：
  源码 AST  →  栈式字节码（本模块编译）  →  本模块自带的虚拟机执行
也可以把字节码序列化成 .ibc（JSON）落盘，之后零编译直接加载执行。

为什么是字节码 VM 而不是直接生成机器码：
  脚本语言（CPython / Lua / JVM）的自有后端主流形态就是「自定义指令集 + 虚拟机」，
  变量按槽位访问、指令线性执行，比树遍历解释器少大量 isinstance 与字典查找，
  同时保持可移植、可调试、可安全序列化。直接生成机器码 + 寄存器分配是另一个量级的工程。

当前覆盖（与解释器同语义）：
  字面量（int/float/str/bool/none）、变量与作用域、算术/比较/逻辑/一元、
  if/elif/else、while、for-in、break/continue、def/return/递归、闭包（cell）、
  lambda、三元、assert、f-string、list/dict 字面量、索引、切片、属性、内建调用。
明确不支持（编译期报错，不静默改语义）：
  class、try/catch、import、raise/exception、match、defer、yield、await。
  遇到这些会给出 BackendError 并提示改用 `impor run`。
"""

import json

from . import ast_nodes as A
from .interpreter import (
    parse_source, strip_header, ImporException,
    I64_MIN, I64_MAX, I64_MSG,
)
from .rt_concurrency import CONCURRENCY_BUILTINS

# NVM 不支持的内建：并发原语依赖解释器运行副本与原生线程，字节码 VM 无法承载，
# 编译期直接拒绝（与 class/import 同路径），避免运行到内部类型错误再写 crash.log。
UNSUPPORTED_BUILTINS = frozenset(CONCURRENCY_BUILTINS)

# ============================================================
# 操作码（opcode）。跳转地址 = code 列表下标（每条指令占一个位置）。
# 每条指令写成 [op] 或 [op, arg]。
# ============================================================
(OP_LOAD_CONST, OP_LOAD_FAST, OP_STORE_FAST, OP_LOAD_GLOBAL,
 OP_LOAD_DEREF, OP_STORE_DEREF, OP_BINARY, OP_COMPARE, OP_UNARY,
 OP_POP_TOP, OP_DUP_TOP, OP_JUMP, OP_POP_JUMP_IF_FALSE,
 OP_POP_JUMP_IF_TRUE, OP_GET_ITER, OP_FOR_ITER, OP_CALL, OP_RETURN,
 OP_MAKE_FUNCTION, OP_BUILD_LIST, OP_BUILD_MAP, OP_BINARY_SUBSCR,
 OP_STORE_SUBSCR, OP_BUILD_SLICE, OP_LOAD_ATTR, OP_NOP,
 OP_STORE_GLOBAL, OP_STORE_ATTR, OP_JUMP_IF_TRUE_OR_POP,
 OP_JUMP_IF_FALSE_OR_POP) = range(30)

OPCODE_NAMES = {
    OP_LOAD_CONST: "LOAD_CONST", OP_LOAD_FAST: "LOAD_FAST", OP_STORE_FAST: "STORE_FAST",
    OP_LOAD_GLOBAL: "LOAD_GLOBAL", OP_LOAD_DEREF: "LOAD_DEREF", OP_STORE_DEREF: "STORE_DEREF",
    OP_BINARY: "BINARY", OP_COMPARE: "COMPARE", OP_UNARY: "UNARY",
    OP_POP_TOP: "POP_TOP", OP_DUP_TOP: "DUP_TOP", OP_JUMP: "JUMP",
    OP_POP_JUMP_IF_FALSE: "POP_JUMP_IF_FALSE", OP_POP_JUMP_IF_TRUE: "POP_JUMP_IF_TRUE",
    OP_GET_ITER: "GET_ITER", OP_FOR_ITER: "FOR_ITER", OP_CALL: "CALL",
    OP_RETURN: "RETURN", OP_MAKE_FUNCTION: "MAKE_FUNCTION", OP_BUILD_LIST: "BUILD_LIST",
    OP_BUILD_MAP: "BUILD_MAP", OP_BINARY_SUBSCR: "BINARY_SUBSCR",
    OP_STORE_SUBSCR: "STORE_SUBSCR", OP_BUILD_SLICE: "BUILD_SLICE",
    OP_LOAD_ATTR: "LOAD_ATTR", OP_NOP: "NOP",
    OP_STORE_GLOBAL: "STORE_GLOBAL",
    OP_STORE_ATTR: "STORE_ATTR",
    OP_JUMP_IF_TRUE_OR_POP: "JUMP_IF_TRUE_OR_POP",
    OP_JUMP_IF_FALSE_OR_POP: "JUMP_IF_FALSE_OR_POP",
}

BINARY_OPS = ("+", "-", "*", "/", "//", "%", "**")
COMPARE_OPS = ("==", "!=", "<", ">", "<=", ">=")
UNARY_OPS = ("-", "+", "not")

IBC_MAGIC = "IMPOR-IBC"
IBC_VERSION = 1


# ============================================================
# 运行时对象
# ============================================================
class Cell:
    """闭包单元：被多层作用域共享的一个值盒子。"""
    __slots__ = ("value",)

    def __init__(self, value=None):
        self.value = value


class PyFunction:
    """VM 运行时的函数值：代码对象 + 闭包 cells。"""
    __slots__ = ("code", "closure", "name")

    def __init__(self, code, closure):
        self.code = code
        self.closure = closure
        self.name = code["name"]

    def __repr__(self):
        return "<nvm function %s>" % self.name


class Frame:
    __slots__ = ("code", "ip", "locals", "cells", "stack", "vm")

    def __init__(self, code, cells, vm):
        self.code = code
        self.ip = 0
        self.locals = [None] * code["nlocals"]
        self.cells = cells
        self.stack = []
        self.vm = vm


# ============================================================
# 阶段一：作用域分析（确定 local / cell / free，不发指令）
# ============================================================
class Scope:
    def __init__(self, node, name, parent, params=None):
        self.node = node
        self.name = name
        self.parent = parent
        self.local = set(params or ())
        self.cells = set()
        self.free = set()
        self.children = []
        self.own_cell_names = []
        self.free_names = []
        self.cell_index = {}
        self.local_index = {}

    def is_top(self):
        return self.parent is None


def _block_stmts(node):
    """body 可能是 Block 或 list，统一成语句列表。"""
    if isinstance(node, A.Block):
        return node.stmts
    return node if node is not None else []


def _collect_local_names(stmts, scope):
    """只收集本作用域直接产生的局部名（不进入嵌套 def/lambda）。"""
    for st in stmts:
        t = type(st)
        if t is A.Assign or t is A.CompoundAssign:
            # nonlocal/global 显式声明的名字不属于本作用域局部，不进 local
            if getattr(st, "scope", "local") == "local":
                scope.local.add(st.name)
        elif t is A.For:
            scope.local.add(st.var)
            _collect_local_names(_block_stmts(st.body), scope)
        elif t is A.If:
            _collect_local_names(_block_stmts(st.body), scope)
            for _, b in st.elseifs:
                _collect_local_names(_block_stmts(b), scope)
            if st.elseb:
                _collect_local_names(_block_stmts(st.elseb), scope)
        elif t is A.While:
            _collect_local_names(_block_stmts(st.body), scope)
        elif t is A.TryCatch:
            _collect_local_names(_block_stmts(st.body), scope)
        elif t is A.Block:
            _collect_local_names(st.stmts, scope)


def _collect_referenced(stmts):
    """收集本作用域语句（不进入嵌套函数）里出现的所有 Var 名字。"""
    out = set()

    def walk_expr(e):
        t = type(e)
        if t is A.Var:
            out.add(e.name)
        elif t is A.BinOp or t is A.Pipe:
            walk_expr(e.left); walk_expr(e.right)
        elif t is A.UnaryOp:
            walk_expr(e.child)
        elif t is A.TernaryExpr:
            walk_expr(e.cond); walk_expr(e.true_expr); walk_expr(e.false_expr)
        elif t is A.Call:
            walk_expr(e.callee)
            for a in e.args:
                walk_expr(a)
        elif t is A.IndexGet or t is A.IndexSet:
            walk_expr(e.container); walk_expr(e.idx)
            if t is A.IndexSet:
                walk_expr(e.val)
        elif t is A.Slice:
            for x in (e.start, e.end, e.step):
                if x:
                    walk_expr(x)
        elif t is A.AttrGet:
            walk_expr(e.obj_expr)
        elif t is A.AttrSet:
            walk_expr(e.obj_expr); walk_expr(e.value)
        elif t is A.ListLit:
            for x in e.items:
                walk_expr(x)
        elif t is A.DictLit:
            for k, v in e.kv:
                walk_expr(k); walk_expr(v)
        elif t is A.FStr:
            for x in e.exprs:
                walk_expr(x)
        elif t is A.TypeTest:
            walk_expr(e.expr)

    def walk_stmt(s):
        t = type(s)
        if t is A.Def or t is A.Lambda:
            return
        if t is A.Assign:
            walk_expr(s.expr)
        elif t is A.CompoundAssign:
            walk_expr(s.expr)
        elif t is A.Print:
            for x in s.items:
                walk_expr(x)
        elif t is A.ExprStmt:
            walk_expr(s.expr)
        elif t is A.If:
            walk_expr(s.cond)
            for x in _block_stmts(s.body):
                walk_stmt(x)
            for c, b in s.elseifs:
                walk_expr(c)
                for x in _block_stmts(b):
                    walk_stmt(x)
            if s.elseb:
                for x in _block_stmts(s.elseb):
                    walk_stmt(x)
        elif t is A.While:
            walk_expr(s.cond)
            for x in _block_stmts(s.body):
                walk_stmt(x)
        elif t is A.For:
            walk_expr(s.iter_expr)
            for x in _block_stmts(s.body):
                walk_stmt(x)
        elif t is A.Return and s.expr:
            walk_expr(s.expr)
        elif t is A.AssertNode:
            walk_expr(s.cond)
        elif t is A.Block:
            for x in s.stmts:
                walk_stmt(x)
        elif t is A.TryCatch:
            for x in _block_stmts(s.body):
                walk_stmt(x)
        else:
            for attr in ("expr", "cond", "value"):
                e = getattr(s, attr, None)
                if isinstance(e, A.AST):
                    walk_expr(e)

    for s in stmts:
        walk_stmt(s)
    return out


def _find_nested_functions(stmts):
    out = []

    def walk_expr(e):
        # 表达式里的 lambda 是本作用域的嵌套函数；其内部由它自己的子作用域分析
        if type(e) is A.Lambda:
            out.append(e)
            return
        t = type(e)
        if t is A.BinOp or t is A.Pipe:
            walk_expr(e.left); walk_expr(e.right)
        elif t is A.UnaryOp:
            walk_expr(e.child)
        elif t is A.TernaryExpr:
            walk_expr(e.cond); walk_expr(e.true_expr); walk_expr(e.false_expr)
        elif t is A.Call:
            walk_expr(e.callee)
            for a in e.args:
                walk_expr(a)
        elif t is A.IndexGet or t is A.IndexSet:
            walk_expr(e.container); walk_expr(e.idx)
            if t is A.IndexSet:
                walk_expr(e.val)
        elif t is A.Slice:
            for x in (e.start, e.end, e.step):
                if x:
                    walk_expr(x)
        elif t is A.AttrGet:
            walk_expr(e.obj_expr)
        elif t is A.AttrSet:
            walk_expr(e.obj_expr); walk_expr(e.value)
        elif t is A.ListLit:
            for x in e.items:
                walk_expr(x)
        elif t is A.DictLit:
            for k, v in e.kv:
                walk_expr(k); walk_expr(v)
        elif t is A.FStr:
            for x in e.exprs:
                walk_expr(x)
        elif t is A.TypeTest:
            walk_expr(e.expr)

    def walk(s):
        t = type(s)
        if t is A.Def or t is A.Lambda:
            out.append(s)
            return
        # 先扫描语句内的表达式（捕获赋值/调用/返回/迭代里的 lambda）
        if t is A.Assign or t is A.CompoundAssign or t is A.ExprStmt or t is A.Return:
            if getattr(s, "expr", None) is not None:
                walk_expr(s.expr)
        elif t is A.Print:
            for x in s.items:
                walk_expr(x)
        elif t is A.Call:
            walk_expr(s.callee)
            for a in s.args:
                walk_expr(a)
        elif t is A.For:
            walk_expr(s.iter_expr)
        elif t is A.AssertNode:
            walk_expr(s.cond)
        # 再递归语句块
        for b in (_block_stmts(getattr(s, "body", None)),
                 _block_stmts(getattr(s, "elseb", None))):
            for x in b:
                walk(x)
        if t is A.If:
            for _, b in s.elseifs:
                for x in _block_stmts(b):
                    walk(x)
        if t is A.Block:
            for x in s.stmts:
                walk(x)

    for s in stmts:
        walk(s)
    return out


def analyze_scope(node, name, parent, stmts, params=None):
    scope = Scope(node, name, parent, params)
    _collect_local_names(stmts, scope)
    nested = _find_nested_functions(stmts)
    child_scopes = []
    for fn in nested:
        if type(fn) is A.Def:
            cs = analyze_scope(fn, fn.name, scope, _block_stmts(fn.body), fn.params)
        else:
            cs = analyze_scope(fn, "<lambda>", scope,
                               [A.Return(fn.expr, fn.lineno)], fn.params)
        child_scopes.append(cs)
    scope.children = child_scopes

    wanted = set(x for x in _collect_referenced(stmts) if x not in scope.local)
    for cs in child_scopes:
        for x in cs.free:
            wanted.add(x)

    if scope.is_top():
        scope.free = set()
    else:
        scope.free = set(x for x in wanted if x not in scope.local)
    return scope


def resolve_free(scope):
    """后处理：把每个 free 名字解析到外层函数的局部（建 cell）；
    只能解析到模块顶层 / 内建的名字不是 free，留给 LOAD_GLOBAL。"""
    if not scope.is_top():
        kept = set()
        for x in sorted(scope.free):
            p = scope.parent
            hit = False
            while p is not None and not p.is_top():
                if x in p.local:
                    p.cells.add(x)
                    hit = True
                    break
                p = p.parent
            if hit:
                kept.add(x)
        scope.free = kept
    for ch in scope.children:
        resolve_free(ch)


# ============================================================
# 阶段二：代码生成（AST → CodeObject）
# ============================================================
class Compiler:
    def __init__(self, scope, stmts, name, params):
        self.scope = scope
        self.stmts = stmts
        self.name = name
        self.params = list(params or ())
        self.code = []
        self.consts = []
        self.const_index = {}
        self.names = []
        self.name_index = {}
        self.child_funcs = []
        self.child_node_index = {}
        self.loop_stack = []

        idx = 0
        for p in self.params:
            scope.local_index[p] = idx
            idx += 1
        for ln in sorted(scope.local):
            if ln in scope.local_index or ln in scope.cells:
                continue
            scope.local_index[ln] = idx
            idx += 1
        self.nlocals = idx

        ci = 0
        scope.own_cell_names = [x for x in sorted(scope.cells)]
        for cn in scope.own_cell_names:
            scope.cell_index[cn] = ci
            ci += 1
        scope.free_names = [x for x in sorted(scope.free)]
        for fn in scope.free_names:
            scope.cell_index[fn] = ci
            ci += 1

    def add_const(self, v):
        key = ("c", type(v).__name__, v)
        if key in self.const_index:
            return self.const_index[key]
        i = len(self.consts)
        self.consts.append(v)
        self.const_index[key] = i
        return i

    def add_name(self, n):
        if n in self.name_index:
            return self.name_index[n]
        i = len(self.names)
        self.names.append(n)
        self.name_index[n] = i
        return i

    def emit(self, op, arg=None, lineno=None):
        if arg is None:
            self.code.append([op])
        else:
            instr = [op, arg]
            if lineno is not None:
                instr.append(lineno)
            self.code.append(instr)
        return len(self.code) - 1

    def here(self):
        return len(self.code)

    def emit_load(self, name):
        sc = self.scope
        if name in sc.cell_index:
            self.emit(OP_LOAD_DEREF, sc.cell_index[name])
        elif not sc.is_top() and name in sc.local_index:
            self.emit(OP_LOAD_FAST, sc.local_index[name])
        else:
            self.emit(OP_LOAD_GLOBAL, self.add_name(name))

    def emit_store(self, name):
        sc = self.scope
        if name in sc.cell_index:
            self.emit(OP_STORE_DEREF, sc.cell_index[name])
        elif sc.is_top():
            self.emit(OP_STORE_GLOBAL, self.add_name(name))
        else:
            if name not in sc.local_index:
                sc.local_index[name] = self.nlocals
                self.nlocals += 1
            self.emit(OP_STORE_FAST, sc.local_index[name])

    def compile_expr(self, e):
        t = type(e)
        if t is A.Num:
            self.emit(OP_LOAD_CONST, self.add_const(e.val))
        elif t is A.Str:
            self.emit(OP_LOAD_CONST, self.add_const(e.val))
        elif t is A.Bool:
            self.emit(OP_LOAD_CONST, self.add_const(e.val))
        elif t is A.Null:
            self.emit(OP_LOAD_CONST, self.add_const(None))
        elif t is A.Var:
            self.emit_load(e.name)
        elif t is A.FStr:
            first = True
            ei = 0
            for kind, payload in e.parts:
                if kind == "text":
                    from .lexer import unescape_impor
                    self.emit(OP_LOAD_CONST, self.add_const(unescape_impor(payload)))
                else:
                    # 先压 callee 再压参数，与普通 Call 的栈布局一致
                    self.emit(OP_LOAD_GLOBAL, self.add_name("str"))
                    self.compile_expr(e.exprs[ei])
                    ei += 1
                    self.emit(OP_CALL, 1)
                if not first:
                    self.emit(OP_BINARY, BINARY_OPS.index("+"), e.lineno)
                first = False
            if first:
                self.emit(OP_LOAD_CONST, self.add_const(""))
        elif t is A.BinOp:
            if e.op == "and":
                # a 假则结果为 a（短路）；否则结果为 b
                self.compile_expr(e.left)
                jf = self.emit(OP_JUMP_IF_FALSE_OR_POP, 0)
                self.compile_expr(e.right)
                self.code[jf][1] = self.here()
            elif e.op == "or":
                # a 真则结果为 a（短路）；否则结果为 b
                self.compile_expr(e.left)
                jt = self.emit(OP_JUMP_IF_TRUE_OR_POP, 0)
                self.compile_expr(e.right)
                self.code[jt][1] = self.here()
            else:
                self.compile_expr(e.left)
                self.compile_expr(e.right)
                if e.op in BINARY_OPS:
                    self.emit(OP_BINARY, BINARY_OPS.index(e.op), e.lineno)
                elif e.op in COMPARE_OPS:
                    self.emit(OP_COMPARE, COMPARE_OPS.index(e.op))
                else:
                    self._unsupported("运算符 %s" % e.op, e.lineno)
        elif t is A.Pipe:
            if type(e.right) is A.Call:
                self.compile_expr(e.right.callee)
                self.compile_expr(e.left)
                for a in e.right.args:
                    self.compile_expr(a)
                self.emit(OP_CALL, 1 + len(e.right.args))
            else:
                self.compile_expr(e.right)
                self.compile_expr(e.left)
                self.emit(OP_CALL, 1)
        elif t is A.UnaryOp:
            self.compile_expr(e.child)
            if e.op not in UNARY_OPS:
                self._unsupported("一元运算符 %s" % e.op, e.lineno)
            self.emit(OP_UNARY, UNARY_OPS.index(e.op), e.lineno)
        elif t is A.TernaryExpr:
            self.compile_expr(e.cond)
            jf = self.emit(OP_POP_JUMP_IF_FALSE, 0)
            self.compile_expr(e.true_expr)
            je = self.emit(OP_JUMP, 0)
            false_at = self.here()
            self.compile_expr(e.false_expr)
            end = self.here()
            self.code[jf][1] = false_at
            self.code[je][1] = end
        elif t is A.Call:
            if type(e.callee) is A.Var and e.callee.name in UNSUPPORTED_BUILTINS:
                self._unsupported(
                    "内建 %s（NVM 不支持并发，请改用 impor run）" % e.callee.name,
                    e.lineno)
            self.compile_expr(e.callee)
            for a in e.args:
                self.compile_expr(a)
            self.emit(OP_CALL, len(e.args))
        elif t is A.TypeTest:
            self._unsupported("have 类型判定", e.lineno)
        elif t is A.Lambda:
            self._emit_nested_function(e)
        elif t is A.ListLit:
            for x in e.items:
                self.compile_expr(x)
            self.emit(OP_BUILD_LIST, len(e.items))
        elif t is A.DictLit:
            for k, v in e.kv:
                self.compile_expr(k)
                self.compile_expr(v)
            self.emit(OP_BUILD_MAP, len(e.kv))
        elif t is A.IndexGet:
            self.compile_expr(e.container)
            if type(e.idx) is A.Slice:
                self._compile_slice(e.idx)
                self.emit(OP_BINARY_SUBSCR)
            else:
                self.compile_expr(e.idx)
                self.emit(OP_BINARY_SUBSCR)
        elif t is A.Slice:
            self._compile_slice(e)
        elif t is A.AttrGet:
            self.compile_expr(e.obj_expr)
            self.emit(OP_LOAD_ATTR, self.add_name(e.attr_name))
        elif t is A.NewInstance:
            self._unsupported("new 实例化", e.lineno)
        else:
            self._unsupported("表达式 %s" % t.__name__, getattr(e, "lineno", 0))

    def _compile_slice(self, sl):
        def one(x):
            if x is None:
                self.emit(OP_LOAD_CONST, self.add_const(None))
            else:
                self.compile_expr(x)
        if sl.step is not None:
            one(sl.start); one(sl.end); one(sl.step)
            self.emit(OP_BUILD_SLICE, 3)
        else:
            one(sl.start); one(sl.end)
            self.emit(OP_BUILD_SLICE, 2)

    def _emit_nested_function(self, fn_node):
        idx = self.child_node_index.get(id(fn_node))
        if idx is None:
            idx = len(self.child_funcs)
            self.child_node_index[id(fn_node)] = idx
            child_scope = next(c for c in self.scope.children if c.node is fn_node)
            if type(fn_node) is A.Def:
                child = build_code_object(child_scope, _block_stmts(fn_node.body),
                                         fn_node.name, fn_node.params)
            else:
                child = build_code_object(child_scope,
                                         [A.Return(fn_node.expr, fn_node.lineno)],
                                         "<lambda>", fn_node.params)
            self.child_funcs.append(child)
        self.emit(OP_MAKE_FUNCTION, idx)

    def compile_stmt(self, s):
        t = type(s)
        if t is A.Call:
            # 裸调用语句（如 print(...)）：求值后丢弃
            self.compile_expr(s)
            self.emit(OP_POP_TOP)
        elif t is A.Assign:
            if s.scope == "nonlocal":
                self.compile_expr(s.expr)
                if s.name not in self.scope.cell_index:
                    self._unsupported("nonlocal %s" % s.name, s.lineno)
                self.emit(OP_STORE_DEREF, self.scope.cell_index[s.name])
            elif s.scope == "global":
                self.compile_expr(s.expr)
                self.emit(OP_STORE_GLOBAL, self.add_name(s.name))
            else:
                self.compile_expr(s.expr)
                self.emit_store(s.name)
        elif t is A.CompoundAssign:
            sc = self.scope
            resolves_global = sc.is_top() or (
                s.name not in sc.local_index and s.name not in sc.cell_index)
            self.emit_load(s.name)
            self.compile_expr(s.expr)
            op = s.op[:-1] if s.op.endswith("=") else s.op
            if op not in BINARY_OPS:
                self._unsupported("复合赋值 %s" % s.op, s.lineno)
            self.emit(OP_BINARY, BINARY_OPS.index(op), s.lineno)
            if resolves_global:
                self.emit(OP_STORE_GLOBAL, self.add_name(s.name))
            else:
                self.emit_store(s.name)
        elif t is A.Print:
            self.emit(OP_LOAD_GLOBAL, self.add_name("print"))
            for x in s.items:
                self.compile_expr(x)
            self.emit(OP_CALL, len(s.items))
        elif t is A.ExprStmt:
            self.compile_expr(s.expr)
            self.emit(OP_POP_TOP)
        elif t is A.If:
            self.compile_if(s)
        elif t is A.While:
            loop = self.here()
            self.loop_stack.append({"break": [], "continue": loop})
            self.compile_expr(s.cond)
            jf = self.emit(OP_POP_JUMP_IF_FALSE, 0)
            for x in _block_stmts(s.body):
                self.compile_stmt(x)
            self.emit(OP_JUMP, loop)
            end = self.here()
            self.code[jf][1] = end
            ctx = self.loop_stack.pop()
            for j in ctx["break"]:
                self.code[j][1] = end
        elif t is A.For:
            self.compile_expr(s.iter_expr)
            self.emit(OP_GET_ITER)
            loop = self.here()
            self.loop_stack.append({"break": [], "continue": loop})
            jf = self.emit(OP_FOR_ITER, 0)
            self.emit_store(s.var)
            for x in _block_stmts(s.body):
                self.compile_stmt(x)
            self.emit(OP_JUMP, loop)
            end = self.here()
            self.code[jf][1] = end
            ctx = self.loop_stack.pop()
            for j in ctx["break"]:
                self.code[j][1] = end
        elif t is A.Def:
            self._emit_nested_function(s)
            if self.scope.is_top():
                self.emit(OP_STORE_GLOBAL, self.add_name(s.name))
            else:
                self.emit_store(s.name)
        elif t is A.Return:
            if s.expr is None:
                self.emit(OP_LOAD_CONST, self.add_const(None))
            else:
                self.compile_expr(s.expr)
            self.emit(OP_RETURN)
        elif t is A.IndexSet:
            # dp[i] = v
            self.compile_expr(s.container)
            self.compile_expr(s.idx)
            self.compile_expr(s.val)
            self.emit(OP_STORE_SUBSCR)
        elif t is A.AttrSet:
            # obj.x = v
            self.compile_expr(s.obj_expr)
            self.compile_expr(s.value)
            self.emit(OP_STORE_ATTR, self.add_name(s.attr_name))
        elif t is A.Break:
            if not self.loop_stack:
                self._unsupported("break（循环外）", s.lineno)
            self.loop_stack[-1]["break"].append(self.emit(OP_JUMP, 0))
        elif t is A.Continue:
            if not self.loop_stack:
                self._unsupported("continue（循环外）", s.lineno)
            self.emit(OP_JUMP, self.loop_stack[-1]["continue"])
        elif t is A.AssertNode:
            self.compile_expr(s.cond)
            jt = self.emit(OP_POP_JUMP_IF_TRUE, 0)
            self.emit(OP_LOAD_GLOBAL, self.add_name("_assert_fail"))
            self.emit(OP_CALL, 0)
            self.code[jt][1] = self.here()
        elif t is A.Block:
            for x in s.stmts:
                self.compile_stmt(x)
        elif t is A.ClassDef:
            self._unsupported("class 定义", s.lineno)
        elif t is A.TryCatch:
            self._unsupported("try/catch", s.lineno)
        elif t in (A.ImportNode, A.FromImportNode):
            self._unsupported("import", s.lineno)
        elif t is A.Raise:
            self._unsupported("raise", s.lineno)
        elif t is A.ExceptionDef:
            self._unsupported("exception 声明", s.lineno)
        elif t is A.MatchNode:
            self._unsupported("match", s.lineno)
        elif t is A.Defer:
            self._unsupported("defer", s.lineno)
        else:
            self._unsupported("语句 %s" % t.__name__, getattr(s, "lineno", 0))

    def compile_if(self, s):
        self.compile_expr(s.cond)
        jf = self.emit(OP_POP_JUMP_IF_FALSE, 0)
        for x in _block_stmts(s.body):
            self.compile_stmt(x)
        end_jumps = []
        if s.elseifs or s.elseb:
            end_jumps.append(self.emit(OP_JUMP, 0))
        next_at = self.here()
        self.code[jf][1] = next_at
        for c, b in s.elseifs:
            self.compile_expr(c)
            jf2 = self.emit(OP_POP_JUMP_IF_FALSE, 0)
            for x in _block_stmts(b):
                self.compile_stmt(x)
            end_jumps.append(self.emit(OP_JUMP, 0))
            at = self.here()
            self.code[jf2][1] = at
        if s.elseb:
            for x in _block_stmts(s.elseb):
                self.compile_stmt(x)
        end = self.here()
        for j in end_jumps:
            self.code[j][1] = end

    def _unsupported(self, what, ln):
        raise ImporException("BackendError",
                             "VM 后端暂不支持 %s（第 %s 行）；请改用 `impor run` 执行" % (what, ln),
                             ln or 0)

    def finish(self):
        self.emit(OP_LOAD_CONST, self.add_const(None))
        self.emit(OP_RETURN)
        return {
            "name": self.name,
            "params": self.params,
            "nlocals": self.nlocals,
            "code": self.code,
            "consts": self.consts,
            "names": self.names,
            "own_cells": self.scope.own_cell_names,
            "free_cells": self.scope.free_names,
            "funcs": self.child_funcs,
        }


def build_code_object(scope, stmts, name, params):
    comp = Compiler(scope, stmts, name, params)
    for s in stmts:
        comp.compile_stmt(s)
    return comp.finish()


def compile_module(ast):
    top_scope = analyze_scope(ast, "<module>", None, ast.stmts, [])
    resolve_free(top_scope)
    code = build_code_object(top_scope, ast.stmts, "<module>", [])
    return {
        "magic": IBC_MAGIC,
        "ibc": IBC_VERSION,
        "main": code,
    }


# ============================================================
# 虚拟机
# ============================================================
class VM:
    def __init__(self, globals_dict=None, max_depth=3200, int64_mode=True):
        self.globals = globals_dict if globals_dict is not None else {}
        self.max_depth = max_depth
        self.depth = 0
        # 默认与交付层 `impor run` 同一 int64 语义：加减乘越界报 E1012，不静默回绕。
        # int64_mode=False 对齐 `impor run --big` 的任意精度。
        self.int64_mode = bool(int64_mode)

    def run_module(self, program):
        frame = self._make_frame(program["main"], ())
        return self._run(frame)

    def _make_frame(self, code, closure):
        cells = []
        for _ in code["own_cells"]:
            cells.append(Cell())
        for fname in code["free_cells"]:
            cell = self._find_closure_cell(closure, fname)
            if cell is None:
                cell = Cell()
            cells.append(cell)
        return Frame(code, cells, self)

    @staticmethod
    def _find_closure_cell(closure, fname):
        for cname, cell in closure:
            if cname == fname:
                return cell
        return None

    def _run(self, frame):
        code = frame.code
        instrs = code["code"]
        consts = code["consts"]
        names = code["names"]
        stack = frame.stack
        locals_ = frame.locals
        cells = frame.cells
        while True:
            instr = instrs[frame.ip]
            op = instr[0]
            frame.ip += 1

            # 分支顺序按 fib 等热点工作负载的实测频率排列，降低平均比较次数。
            if op == OP_LOAD_FAST:
                stack.append(locals_[instr[1]])
            elif op == OP_LOAD_CONST:
                stack.append(consts[instr[1]])
            elif op == OP_BINARY:
                r = stack.pop()
                l = stack.pop()
                bop = BINARY_OPS[instr[1]]
                res = self._binary(bop, l, r)
                if self.int64_mode and bop in ("+", "-", "*") \
                        and type(l) is int and type(r) is int \
                        and (res < I64_MIN or res > I64_MAX):
                    raise ImporException("OverflowError", I64_MSG,
                                         instr[2] if len(instr) > 2 else 0)
                stack.append(res)
            elif op == OP_RETURN:
                return stack.pop()
            elif op == OP_LOAD_GLOBAL:
                stack.append(self.globals[names[instr[1]]])
            elif op == OP_COMPARE:
                r = stack.pop()
                l = stack.pop()
                stack.append(self._compare(COMPARE_OPS[instr[1]], l, r))
            elif op == OP_POP_JUMP_IF_FALSE:
                if not bool(stack.pop()):
                    frame.ip = instr[1]
            elif op == OP_CALL:
                stack.append(self._do_call(instr[1], stack))
            elif op == OP_STORE_FAST:
                locals_[instr[1]] = stack.pop()
            elif op == OP_STORE_GLOBAL:
                self.globals[names[instr[1]]] = stack.pop()
            elif op == OP_LOAD_DEREF:
                stack.append(cells[instr[1]].value)
            elif op == OP_STORE_DEREF:
                cells[instr[1]].value = stack.pop()
            elif op == OP_UNARY:
                v = stack.pop()
                u = UNARY_OPS[instr[1]]
                ln = instr[2] if len(instr) > 2 else 0
                if u == "not":
                    stack.append(not bool(v))
                elif u == "-":
                    res = -v
                    if self.int64_mode and type(v) is int \
                            and (res < I64_MIN or res > I64_MAX):
                        raise ImporException("OverflowError", I64_MSG, ln)
                    stack.append(res)
                else:
                    stack.append(+v)
            elif op == OP_POP_TOP:
                stack.pop()
            elif op == OP_DUP_TOP:
                stack.append(stack[-1])
            elif op == OP_JUMP:
                frame.ip = instr[1]
            elif op == OP_POP_JUMP_IF_TRUE:
                if bool(stack.pop()):
                    frame.ip = instr[1]
            elif op == OP_JUMP_IF_TRUE_OR_POP:
                if bool(stack[-1]):
                    frame.ip = instr[1]   # 保留栈顶作为结果
                else:
                    stack.pop()
            elif op == OP_JUMP_IF_FALSE_OR_POP:
                if not bool(stack[-1]):
                    frame.ip = instr[1]
                else:
                    stack.pop()
            elif op == OP_GET_ITER:
                stack.append(iter(stack.pop()))
            elif op == OP_FOR_ITER:
                it = stack[-1]
                try:
                    stack.append(next(it))
                except StopIteration:
                    stack.pop()
                    frame.ip = instr[1]
            elif op == OP_MAKE_FUNCTION:
                child = code["funcs"][instr[1]]
                closure = self._build_closure(frame, child)
                stack.append(PyFunction(child, closure))
            elif op == OP_BUILD_LIST:
                n = instr[1]
                if n:
                    vals = stack[-n:]
                    del stack[-n:]
                else:
                    vals = []
                stack.append(list(vals))
            elif op == OP_BUILD_MAP:
                n = instr[1]
                if n:
                    pairs = stack[-2 * n:]
                    del stack[-2 * n:]
                else:
                    pairs = []
                d = {}
                for i in range(n):
                    d[pairs[2 * i]] = pairs[2 * i + 1]
                stack.append(d)
            elif op == OP_BINARY_SUBSCR:
                idx = stack.pop()
                cont = stack.pop()
                if idx is None and isinstance(cont, (list, tuple, str)):
                    stack.append(cont[:])
                else:
                    stack.append(cont[idx])
            elif op == OP_STORE_SUBSCR:
                val = stack.pop()
                key = stack.pop()
                cont = stack.pop()
                cont[key] = val
            elif op == OP_BUILD_SLICE:
                n = instr[1]
                if n == 3:
                    step = stack.pop(); end = stack.pop(); start = stack.pop()
                else:
                    step = None; end = stack.pop(); start = stack.pop()
                stack.append(slice(start, end, step))
            elif op == OP_LOAD_ATTR:
                obj = stack.pop()
                attr = names[instr[1]]
                # dict 方法与解释器对齐：keys/values/items 统一返回 list，
                # get 带默认值，has/contains 返回 bool；其余走原生 getattr
                if isinstance(obj, dict):
                    if attr == "keys":
                        stack.append(lambda: list(obj.keys()))
                    elif attr == "values":
                        stack.append(lambda: list(obj.values()))
                    elif attr == "items":
                        stack.append(lambda: [[k, v] for k, v in obj.items()])
                    elif attr == "get":
                        stack.append(lambda k, d=None: obj.get(k, d))
                    elif attr in ("has", "contains"):
                        stack.append(lambda k: k in obj)
                    else:
                        stack.append(getattr(obj, attr))
                else:
                    stack.append(getattr(obj, attr))
            elif op == OP_STORE_ATTR:
                val = stack.pop()
                obj = stack.pop()
                setattr(obj, names[instr[1]], val)
            elif op == OP_NOP:
                pass
            else:
                raise ImporException("BackendError", "未知操作码 %s" % op, 0)

    def _build_closure(self, frame, child_code):
        out = []
        allnames = frame.code["own_cells"] + frame.code["free_cells"]
        for fname in child_code["free_cells"]:
            cell = None
            for i, cn in enumerate(allnames):
                if cn == fname:
                    cell = frame.cells[i]
                    break
            out.append((fname, cell if cell is not None else Cell()))
        return tuple(out)

    def _do_call(self, nargs, stack):
        # 取参数与被调函数；单参数（递归最常见）走快速路径，省一次切片与删除。
        if nargs == 1:
            a = stack.pop()
            callee = stack.pop()
            args = (a,)
        elif nargs:
            args = tuple(stack[-nargs:])
            del stack[-nargs:]
            callee = stack.pop()
        else:
            args = ()
            callee = stack.pop()
        if isinstance(callee, PyFunction):
            self.depth += 1
            if self.depth > self.max_depth:
                self.depth -= 1
                raise ImporException("RecursionError",
                                     "超过最大调用深度 %s" % self.max_depth, 0)
            try:
                fcode = callee.code
                new_frame = self._make_frame(fcode, callee.closure)
                own = fcode["own_cells"]
                if not own:
                    # 最常见：参数全是普通局部，按位直接赋值，无需逐名查找 cell。
                    loc = new_frame.locals
                    for i, val in enumerate(args):
                        loc[i] = val
                else:
                    for i, val in enumerate(args):
                        pname = fcode["params"][i]
                        if pname in own:
                            new_frame.cells[own.index(pname)].value = val
                        else:
                            new_frame.locals[i] = val
                return self._run(new_frame)
            finally:
                self.depth -= 1
        return callee(*args)

    @staticmethod
    def _binary(op, l, r):
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            return l / r
        if op == "//":
            return l // r
        if op == "%":
            return l % r
        return l ** r

    @staticmethod
    def _compare(op, l, r):
        if op == "==":
            return l == r
        if op == "!=":
            return l != r
        if op == "<":
            return l < r
        if op == ">":
            return l > r
        if op == "<=":
            return l <= r
        return l >= r


# ============================================================
# 公共入口
# ============================================================
def _build_builtin_globals(filename=None, argv=None, log_cb=None, sandbox=False):
    from .builtins import make_builtins

    class _Shim:
        pass

    shim = _Shim()
    shim.gui_root = None
    shim.sandbox_mode = sandbox
    # 默认走内置 print（写 sys.stdout + 换行），与 CLI 的 Interpreter(log_cb=print) 一致
    shim.log_cb = log_cb if log_cb is not None else print
    shim.clear_cb = None
    shim.snapshot_cb = None
    shim.stab = None
    shim.argv = list(argv or [])
    shim.filename = filename
    shim.fast_mode = False
    shim.int64_mode = False
    shim.max_depth = 3200
    shim.call_stack = []
    shim.global_env = None
    gl = {}
    gl.update(make_builtins(shim))

    def _assert_fail():
        raise ImporException("AssertionError", "断言失败", 0)

    gl["_assert_fail"] = _assert_fail
    return gl


def compile_source(source, filename=None):
    body = strip_header(source)
    ast = parse_source(body, filename)
    return compile_module(ast)


def run_source(source, filename=None, argv=None, log_cb=None,
               globals_dict=None, int64_mode=True):
    gl = globals_dict if globals_dict is not None else _build_builtin_globals(
        filename, argv, log_cb)
    program = compile_source(source, filename)
    return VM(gl, int64_mode=int64_mode).run_module(program)


def serialize(program):
    return json.dumps(program, ensure_ascii=False, separators=(",", ":"))


def dump_ibc(program, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(serialize(program))


def load_ibc(path):
    with open(path, encoding="utf-8") as f:
        program = json.load(f)
    if program.get("magic") != IBC_MAGIC:
        raise ImporException("BackendError", "不是有效的 Impor 字节码文件(.ibc)", 0)
    return program


def run_ibc(path, argv=None, log_cb=None, int64_mode=True):
    program = load_ibc(path)
    gl = _build_builtin_globals(path, argv, log_cb)
    return VM(gl, int64_mode=int64_mode).run_module(program)


def disassemble(program):
    lines = []

    def render(code, indent=0):
        pad = "  " * indent
        lines.append("%s[function %s] params=%s nlocals=%s"
                     % (pad, code["name"], code["params"], code["nlocals"]))
        if code["own_cells"]:
            lines.append("%s  own_cells=%s" % (pad, code["own_cells"]))
        if code["free_cells"]:
            lines.append("%s  free_cells=%s" % (pad, code["free_cells"]))
        for i, ins in enumerate(code["code"]):
            op = ins[0]
            arg = ins[1] if len(ins) > 1 else ""
            extra = ""
            if op == OP_LOAD_CONST:
                extra = repr(code["consts"][arg])
            elif op in (OP_LOAD_GLOBAL, OP_LOAD_ATTR, OP_STORE_GLOBAL):
                extra = code["names"][arg]
            elif op == OP_BINARY:
                extra = BINARY_OPS[arg]
            elif op == OP_COMPARE:
                extra = COMPARE_OPS[arg]
            elif op == OP_UNARY:
                extra = UNARY_OPS[arg]
            lines.append("%s  %4d  %-20s %s %s"
                         % (pad, i, OPCODE_NAMES[op], arg, extra))
        for fn in code["funcs"]:
            render(fn, indent + 1)

    render(program["main"])
    return "\n".join(lines)
