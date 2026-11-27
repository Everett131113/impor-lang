# -*- coding: utf-8 -*-
"""Impor (I++) 4.7.0 —— AST → IR（lowering）。

这一层是 xlang 那条“IR 必须独立于后端”的落点：AST 只在这里被翻译一次，
之后的解释、转译、C 后端、将来的第四后端都应当从同一份 IR 下降。

本版**不替换任何现有后端**（替换是破坏性变更，需要 RFC），只提供：
  · `lower_ast()` / `lower_source()`：把 AST 降成 `IRModule`；
  · 明确的拒绝清单：不支持就抛 `UnsupportedNode`（带行号列号），不静默降级；
  · 支撑 `impor ir`（dump）与 `impor ir --run`（参考求值器执行）。

4.6.0 起，类 / 异常 / 模块导入（经 `clink` 在 AST 层拼平）都已进入覆盖范围；
4.7.0 起，`evaluate` / `cir.to_c` / `cli ir` 都会先跑 `impor/irop.py` 的三项优化。
并发内建与闭包明确拒绝。

覆盖范围见 docs/编译管线-IR-4.6.md 的“覆盖矩阵”；优化见 docs/编译管线-IR-4.7-优化.md。
"""

from . import ast_nodes as A
from .ir import IRModule, IRFunction, IRClass, Instr, Lit, UnsupportedNode
from .lexer import unescape_impor

# 明确不支持的节点：名字 → 原因（用于报错文案与文档一致性）
# 并发运行时依赖真实线程调度；参考求值器是单线程，IR 后端口径不含这部分。
IR_CONCURRENCY_BUILTINS = {
    "thread_spawn", "thread_join", "thread_alive", "thread_id",
    "mutex_new", "mutex_lock", "mutex_unlock", "mutex_trylock", "mutex_free",
    "cond_new", "cond_wait", "cond_notify", "cond_notify_all", "cond_free",
}

UNSUPPORTED_REASONS = {
    "ImportNode": "模块系统在 IR 之前处理（IR 只描述单文件函数体）",
    "FromImportNode": "模块系统在 IR 之前处理（IR 只描述单文件函数体）",
    "ExportNode": "模块系统在 IR 之前处理（IR 只描述单文件函数体）",
    "MatchNode": "模式匹配等待后续 IR 建模",
    "Lambda": "闭包对象暂无 IR 表示（函数值目前只有具名函数引用）",
    "Await": "协程/调度器暂无 IR 表示",
    "Defer": "延迟块与作用域退出语义暂无 IR 表示",
    "Pipe": "管道语法尚未下降（可脱糖为 CALL，后续补）",
    "ExitScript": "exit 会直接终止进程，IR 层不做进程级控制",
    "ClsOut": "清屏依赖宿主终端，不属于纯函数语义",
}

# 复合赋值运算符 → 二元运算符
_COMPOUND_OPS = {
    "+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
    "**=": "**", "|=": "|", "&=": "&",
}


class Lowerer:
    def __init__(self, filename=None, cls=None):
        self.filename = filename
        self.cur_class = cls          # 方法体内的 super() 需要知道起始类
        self.instrs = []
        self._tmp = 0
        self._label = 0
        self._loop_stack = []     # [(continue_label, break_label)]

    # ---------- 基础 ----------
    def emit(self, op, *args, **kw):
        ins = Instr(op, args, kw.get("lineno", 0))
        self.instrs.append(ins)
        return ins

    def temp(self):
        self._tmp += 1
        return "t%d" % self._tmp

    def new_label(self, tag):
        self._label += 1
        return "L_%s_%d" % (tag, self._label)

    def unsupported(self, node, reason=""):
        name = type(node).__name__
        raise UnsupportedNode(name, getattr(node, "lineno", 0),
                              getattr(node, "col", 0), self.filename,
                              reason or UNSUPPORTED_REASONS.get(name, ""))

    # ---------- 表达式 ----------
    def expr(self, n):
        t = type(n)
        if t is A.Num:
            return Lit(n.val)
        if t is A.Str:
            return Lit(n.val)
        if t is A.Bool:
            return Lit(bool(n.val))
        if t is A.Null:
            return Lit(None)
        if t is A.Var:
            if n.name in IR_CONCURRENCY_BUILTINS:
                raise UnsupportedNode(n.name, n.lineno, n.col, self.filename,
                                      "IR 后端口径不含线程/锁/条件变量运行时"
                                      "（参考求值器单线程、无调度语义）；"
                                      "并发请走解释器或 ctrans 后端")
            dst = self.temp()
            self.emit("LOAD", dst, n.name, lineno=n.lineno)
            return dst
        if t is A.BinOp:
            return self._binop(n)
        if t is A.UnaryOp:
            return self._unary(n)
        if t is A.TernaryExpr:
            return self._ternary(n)
        if t is A.NewInstance:
            args = tuple(self.expr(x) for x in n.args)
            dst = self.temp()
            self.emit("NEW", dst, Lit(n.cls_name), *args, lineno=n.lineno)
            return dst
        if t is A.AttrGet:
            obj = self.expr(n.obj_expr)
            dst = self.temp()
            self.emit("GETATTR", dst, obj, Lit(n.attr_name), lineno=n.lineno)
            return dst
        if t is A.SuperCall:
            self.unsupported(n, "super 只能出现在 super().方法(...) 的接收者位置")
        if t is A.Call:
            return self._call(n)
        if t is A.ListLit:
            items = tuple(self.expr(x) for x in n.items)
            dst = self.temp()
            self.emit("LIST", dst, *items, lineno=n.lineno)
            return dst
        if t is A.DictLit:
            flat = []
            for k, v in n.kv:
                flat.append(self.expr(k))
                flat.append(self.expr(v))
            dst = self.temp()
            self.emit("DICT", dst, *flat, lineno=n.lineno)
            return dst
        if t is A.IndexGet:
            base = self.expr(n.container)
            idx = self.expr(n.idx)
            dst = self.temp()
            self.emit("INDEX", dst, base, idx, lineno=n.lineno)
            return dst
        if t is A.Slice:
            base = self.expr(n.container)
            start = self.expr(n.start) if n.start is not None else Lit(None)
            end = self.expr(n.end) if n.end is not None else Lit(None)
            step = self.expr(n.step) if n.step is not None else Lit(None)
            dst = self.temp()
            self.emit("SLICE", dst, base, start, end, step, lineno=n.lineno)
            return dst
        if t is A.FStr:
            parts = []
            ei = 0
            for kind, payload in n.parts:
                if kind == "text":
                    parts.append(Lit(unescape_impor(payload)))
                else:
                    val = self.expr(n.exprs[ei])
                    ei += 1
                    s = self.temp()
                    self.emit("TOSTR", s, val, lineno=n.lineno)
                    parts.append(s)
            dst = self.temp()
            self.emit("CONCAT", dst, *tuple(parts), lineno=n.lineno)
            return dst
        self.unsupported(n)

    def _binop(self, n):
        op = n.op
        if op in ("and", "or", "&&", "||"):
            return self._short_circuit(n)
        lhs = self.expr(n.left)
        rhs = self.expr(n.right)
        dst = self.temp()
        self.emit("BINOP", dst, op, lhs, rhs, lineno=n.lineno)
        return dst

    def _short_circuit(self, n):
        """`and` / `or` 走控制流，保证短路语义与解释器一致（不预先求值右操作数）。"""
        op = {"&&": "and", "||": "or"}.get(n.op, n.op)
        dst = self.temp()
        label_rhs = self.new_label(op + "_rhs")
        label_end = self.new_label(op + "_end")
        lhs = self.expr(n.left)
        if op == "and":
            # 左值为假 → 结果即左值
            label_false = self.new_label("and_false")
            self.emit("JUMP_IF_FALSE", lhs, label_false, lineno=n.lineno)
            rhs = self.expr(n.right)
            self.emit("MOVE", dst, rhs, lineno=n.lineno)
            self.emit("JUMP", label_end, lineno=n.lineno)
            self.emit("LABEL", label_false, lineno=n.lineno)
            self.emit("MOVE", dst, lhs, lineno=n.lineno)
            self.emit("LABEL", label_end, lineno=n.lineno)
            return dst
        # or：左值为真 → 结果即左值，否则求右值
        self.emit("JUMP_IF_FALSE", lhs, label_rhs, lineno=n.lineno)
        self.emit("MOVE", dst, lhs, lineno=n.lineno)
        self.emit("JUMP", label_end, lineno=n.lineno)
        self.emit("LABEL", label_rhs, lineno=n.lineno)
        rhs = self.expr(n.right)
        self.emit("MOVE", dst, rhs, lineno=n.lineno)
        self.emit("LABEL", label_end, lineno=n.lineno)
        return dst

    def _unary(self, n):
        val = self.expr(n.child)
        dst = self.temp()
        self.emit("UNOP", dst, n.op, val, lineno=n.lineno)
        return dst

    def _ternary(self, n):
        dst = self.temp()
        label_else = self.new_label("ternary_else")
        label_end = self.new_label("ternary_end")
        cond = self.expr(n.cond)
        self.emit("JUMP_IF_FALSE", cond, label_else, lineno=n.lineno)
        self.emit("MOVE", dst, self.expr(n.true_expr), lineno=n.lineno)
        self.emit("JUMP", label_end, lineno=n.lineno)
        self.emit("LABEL", label_else, lineno=n.lineno)
        self.emit("MOVE", dst, self.expr(n.false_expr), lineno=n.lineno)
        self.emit("LABEL", label_end, lineno=n.lineno)
        return dst

    def _call(self, n, want_value=True):
        callee = n.callee
        if isinstance(callee, A.AttrGet):
            args = tuple(self.expr(a) for a in n.args)
            dst = self.temp()
            if isinstance(callee.obj_expr, A.SuperCall):
                if not self.cur_class:
                    raise UnsupportedNode("super()", n.lineno, n.col, self.filename,
                                          "super() 只能用在类方法里")
                self.emit("SUPERCALL", dst, Lit(self.cur_class),
                          Lit(callee.attr_name), *args, lineno=n.lineno)
            else:
                obj = self.expr(callee.obj_expr)
                self.emit("CALLMETHOD", dst, obj, Lit(callee.attr_name),
                          *args, lineno=n.lineno)
            return dst if want_value else Lit(None)
        args = tuple(self.expr(a) for a in n.args)
        if isinstance(callee, A.Var) and callee.name == "print":
            # 4.x 起 print(...) 是内建调用而不是独立语句节点；IR 里落成 PRINT 指令。
            self.emit("PRINT", *args, lineno=n.lineno)
            if not want_value:
                return Lit(None)
            dst = self.temp()
            self.emit("CONST", dst, Lit(None), lineno=n.lineno)
            return dst
        if isinstance(callee, A.Var):
            callee_op = callee.name
        else:
            callee_op = self.expr(callee)
        dst = self.temp()
        self.emit("CALL", dst, callee_op, *args, lineno=n.lineno)
        return dst if want_value else Lit(None)

    # ---------- 语句 ----------
    def stmt(self, n):
        t = type(n)
        if t is A.Assign:
            self._assign(n)
            return
        if t is A.CompoundAssign:
            self._compound_assign(n)
            return
        if t is A.Print:
            self.emit("PRINT", *tuple(self.expr(x) for x in n.items),
                      lineno=n.lineno)
            return
        if t is A.Call:
            # 裸调用语句：求值后丢弃返回值（与解释器 visit_ExprStmt 一致）
            self._call(n, want_value=False)
            return
        if t is A.ExprStmt:
            if isinstance(n.expr, A.Call):
                self._call(n.expr, want_value=False)
            else:
                self.expr(n.expr)
            return
        if t is A.If:
            self._if(n)
            return
        if t is A.While:
            self._while(n)
            return
        if t is A.For:
            self._for(n)
            return
        if t is A.Return:
            if n.expr is None:
                self.emit("RETURN", Lit(None), lineno=n.lineno)
            else:
                self.emit("RETURN", self.expr(n.expr), lineno=n.lineno)
            return
        if t is A.Break:
            if not self._loop_stack:
                raise UnsupportedNode("Break", n.lineno, n.col, self.filename,
                                      "break 不在循环内")
            self.emit("JUMP", self._loop_stack[-1][1], lineno=n.lineno)
            return
        if t is A.Continue:
            if not self._loop_stack:
                raise UnsupportedNode("Continue", n.lineno, n.col, self.filename,
                                      "continue 不在循环内")
            self.emit("JUMP", self._loop_stack[-1][0], lineno=n.lineno)
            return
        if t is A.AssertNode:
            self.emit("ASSERT", self.expr(n.cond), lineno=n.lineno)
            return
        if t is A.AttrSet:
            obj = self.expr(n.obj_expr)
            val = self.expr(n.value)
            self.emit("SETATTR", obj, Lit(n.attr_name), val, lineno=n.lineno)
            return
        if t is A.IndexSet:
            container = self.expr(n.container)
            idx = self.expr(n.idx)
            val = self.expr(n.val)
            self.emit("SETINDEX", container, idx, val, lineno=n.lineno)
            return
        if t is A.TryCatch:
            self._try(n)
            return
        if t is A.Raise:
            self._raise(n)
            return
        if t is A.ExceptionDef:
            # 异常类型声明在运行期没有实体：类型名就是 raise 的字面量，
            # 这里显式降成空操作（而不是假装不支持）。
            return
        if t is A.Block:
            self._stmts(n.stmts)
            return
        if t is A.Def:
            raise UnsupportedNode("嵌套 def", n.lineno, n.col, self.filename,
                                  "IR 只支持顶层函数；嵌套函数需要闭包表示")
        self.unsupported(n)

    def _try(self, n):
        """try/catch/finally → TRY_BEGIN / CATCH_BEGIN / TRY_CLOSE 区间。

        异常边在 IR 里是显式的：后端不需要再自己猜 catch 的边界。
        类型过滤用 ERR_MATCH + JUMP_IF_FALSE；不匹配时把重抛标志置 1，
        等 finally 跑完再由 TRY_CLOSE 统一重抛（与 ctrans 的 rh 机制同构）。
        """
        tag = self.new_label("try")
        catch_lbl = self.new_label("catch")
        fin_lbl = self.new_label("fin")
        rh = "__rh_%s" % tag
        self.emit("CONST", rh, Lit(0), lineno=n.lineno)
        self.emit("TRY_BEGIN", tag,
                  Lit(catch_lbl if n.catch_body else None),
                  Lit(fin_lbl), rh, lineno=n.lineno)
        self._stmts(n.body)
        self.emit("TRY_END", tag, lineno=n.lineno)
        if n.catch_body:
            self.emit("LABEL", catch_lbl, lineno=n.lineno)
            self.emit("CATCH_BEGIN", tag, n.err_var or "__err", lineno=n.lineno)
            if n.err_type:
                dst = self.temp()
                self.emit("ERR_MATCH", dst, Lit(n.err_type), lineno=n.lineno)
                nomatch = self.new_label("catch_nomatch")
                self.emit("JUMP_IF_FALSE", dst, nomatch, lineno=n.lineno)
                self._stmts(n.catch_body)
                self.emit("JUMP", fin_lbl, lineno=n.lineno)
                self.emit("LABEL", nomatch, lineno=n.lineno)
                self.emit("STORE", rh, "local", Lit(1), lineno=n.lineno)
            else:
                self._stmts(n.catch_body)
                self.emit("JUMP", fin_lbl, lineno=n.lineno)
            self.emit("CATCH_END", tag, lineno=n.lineno)
        self.emit("LABEL", fin_lbl, lineno=n.lineno)
        if n.finally_body:
            self._stmts(n.finally_body)
        self.emit("TRY_CLOSE", tag, rh, lineno=n.lineno)

    def _raise(self, n):
        if n.cause_expr is not None:
            raise UnsupportedNode("Raise(from)", n.lineno, n.col, self.filename,
                                  "IR 暂不支持 raise ... from 的因果链")
        typ = Lit(n.exc_type_name or "Error")
        msg = self.expr(n.msg_expr) if n.msg_expr is not None else Lit("")
        self.emit("RAISE", typ, msg, lineno=n.lineno)

    def _assign(self, n):
        scope = getattr(n, "scope", "local") or "local"
        if scope == "const":
            scope = "local"     # const 的只读校验留在语义层，IR 里等价于声明
        value = self.expr(n.expr)
        self.emit("STORE", n.name, scope, value, lineno=n.lineno)

    def _compound_assign(self, n):
        if n.op not in _COMPOUND_OPS:
            raise UnsupportedNode("复合赋值 %s" % n.op, n.lineno, n.col,
                                  self.filename, "未知复合运算符")
        dst = self.temp()
        self.emit("LOAD", dst, n.name, lineno=n.lineno)
        rhs = self.expr(n.expr)
        out = self.temp()
        self.emit("BINOP", out, _COMPOUND_OPS[n.op], dst, rhs, lineno=n.lineno)
        self.emit("STORE", n.name, "update", out, lineno=n.lineno)

    def _if(self, n):
        label_end = self.new_label("if_end")
        branches = [(n.cond, n.body)] + list(n.elseifs or [])
        for cond, body in branches:
            label_next = self.new_label("if_next")
            self.emit("JUMP_IF_FALSE", self.expr(cond), label_next,
                      lineno=getattr(cond, "lineno", n.lineno))
            self._stmts(body)
            self.emit("JUMP", label_end, lineno=n.lineno)
            self.emit("LABEL", label_next, lineno=n.lineno)
        if n.elseb:
            self._stmts(n.elseb)
        self.emit("LABEL", label_end, lineno=n.lineno)

    def _while(self, n):
        label_cond = self.new_label("while_cond")
        label_end = self.new_label("while_end")
        self.emit("LABEL", label_cond, lineno=n.lineno)
        self.emit("JUMP_IF_FALSE", self.expr(n.cond), label_end, lineno=n.lineno)
        self._loop_stack.append((label_cond, label_end))
        try:
            self._stmts(n.body)
        finally:
            self._loop_stack.pop()
        self.emit("JUMP", label_cond, lineno=n.lineno)
        self.emit("LABEL", label_end, lineno=n.lineno)

    def _for(self, n):
        label_cond = self.new_label("for_cond")
        label_end = self.new_label("for_end")
        iterable = self.expr(n.iter_expr)
        it = self.temp()
        self.emit("ITER", it, iterable, lineno=n.lineno)
        self.emit("LABEL", label_cond, lineno=n.lineno)
        item = self.temp()
        self.emit("ITER_NEXT", item, it, label_end, lineno=n.lineno)
        self.emit("STORE", n.var, "loop", item, lineno=n.lineno)
        self._loop_stack.append((label_cond, label_end))
        try:
            self._stmts(n.body)
        finally:
            self._loop_stack.pop()
        self.emit("JUMP", label_cond, lineno=n.lineno)
        self.emit("LABEL", label_end, lineno=n.lineno)

    def _stmts(self, stmts, implicit_return=False):
        stmts = list(stmts or [])
        for i, s in enumerate(stmts):
            if (implicit_return and i == len(stmts) - 1
                    and isinstance(s, A.ExprStmt)):
                # 对齐解释器：函数末行的裸表达式即隐式返回值
                self.emit("RETURN", self.expr(s.expr), lineno=s.lineno)
                continue
            self.stmt(s)


def lower_function(node, filename=None, cls=None):
    """把顶层 `def`（或类方法）降成 `IRFunction`。

    `cls` 非空时是类方法：参数表首位补 `self`，并标记 is_method，
    让 C 后端按 (Obj *self, Val *args) 包装。
    """
    lw = Lowerer(filename, cls=cls)
    params = list(node.params or [])
    if cls:
        params = ["self"] + params
    body = list(node.body or [])
    lw._stmts(body, implicit_return=True)
    lw.emit("RETURN", Lit(None), lineno=getattr(node, "lineno", 0))
    return IRFunction(node.name, params, lw.instrs,
                      getattr(node, "lineno", 0), is_method=bool(cls))


def lower_class(node, filename=None):
    """类定义 → IRClass：方法进 methods，`set self.x = ...` 进字段初始化。"""
    cls = node.cls_name
    methods = []
    field_body = []
    for s in node.body:
        if isinstance(s, A.Def):
            methods.append(lower_function(s, filename, cls=cls))
        elif isinstance(s, A.AttrSet) and isinstance(s.obj_expr, A.Var) \
                and s.obj_expr.name == "self":
            lw = Lowerer(filename, cls=cls)
            val = lw.expr(s.value)
            lw.emit("SETATTR", "self", Lit(s.attr_name), val, lineno=s.lineno)
            field_body.extend(lw.instrs)
        else:
            raise UnsupportedNode(type(s).__name__, getattr(s, "lineno", 0),
                                  getattr(s, "col", 0), filename,
                                  "类体只支持方法定义与 `set self.字段 = 值`")
    return IRClass(cls, node.base_name or None, methods, field_body, node.lineno)


_IMPORT_NODES = ("ImportNode", "FromImportNode")


def _has_imports(node):
    """递归检查 AST 里是否还有 import / export 语句。"""
    for v in vars(node).values():
        if isinstance(v, A.AST):
            if type(v).__name__ in _IMPORT_NODES:
                return True
            if _has_imports(v):
                return True
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, A.AST):
                    if type(x).__name__ in _IMPORT_NODES:
                        return True
                    if _has_imports(x):
                        return True
                elif isinstance(x, tuple):
                    for y in x:
                        if isinstance(y, A.AST) and _has_imports(y):
                            return True
    return False


def link_ast(ast, filename=None):
    """4.6：IR 的模块导入下沉——复用 C 后端的 clink，把 use/import 在 AST 层面拼平。"""
    from .clink import link_modules, LinkError
    try:
        return link_modules(ast, source_name=filename or "<ir>")
    except LinkError as e:
        raise UnsupportedNode("import", 0, 0, filename, "模块链接失败：%s" % e)


def lower_ast(ast, filename=None):
    """把模块级 AST（`Block`）降成 `IRModule`。"""
    if _has_imports(ast):
        ast = link_ast(ast, filename)
    module = IRModule(filename)
    stmts = list(getattr(ast, "stmts", []) or [])
    for s in stmts:
        if isinstance(s, A.Def):
            module.funcs.append(lower_function(s, filename))
        elif isinstance(s, A.ClassDef):
            module.classes.append(lower_class(s, filename))
    lw = Lowerer(filename)
    main_body = [s for s in stmts if not isinstance(s, (A.Def, A.ClassDef))]
    lw._stmts(main_body)
    lw.emit("RETURN", Lit(None), lineno=0)
    module.main = IRFunction("__main__", [], lw.instrs, 0, is_main=True)
    return module


def lower_source(body, filename=None):
    """解析 + lowering 一步到位；`body` 应为已去掉文件头声明的源码正文。"""
    from .interpreter import parse_source
    return lower_ast(parse_source(body, filename), filename)
