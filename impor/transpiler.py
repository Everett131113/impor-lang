# -*- coding: utf-8 -*-
"""
Impor (I++) 编译器 —— 把 Impor 源码编译成等价 Python 源码。

用法（由 CLI 封装）：
    from impor.transpiler import transpile
    py_code = transpile(ast, source_name="main.impor")

生成代码顶部引用 impor.runtime（内置函数、Vec2、Color、异常体系）。
编译出的 .py 可直接运行，也可用 PyInstaller 打包成 exe。

行号映射：每条语句首行追加 `# L{impor行号}` 注释；编译产物末尾调用
runtime.install_tb_map 安装行号映射表，运行时未捕获异常会显示原始
.impor 源码行号，而不是生成 .py 的行号。
"""

import os
import re

from .ast_nodes import (
    Num, Str, FStr, Bool, Var, Pipe, ExprStmt, AttrGet, AttrSet, BinOp, UnaryOp, TernaryExpr,
    Assign, CompoundAssign, Print, If, While, For, Def, Return, Call,
    NewInstance, SuperCall, Break, Continue, ExitScript, ClsOut,
    ListLit, DictLit, IndexGet, IndexSet, Slice, ImportNode, FromImportNode,
    ExportNode, AssertNode, Block, Defer, TryCatch,
    ClassDef, ExceptionDef, Raise, MatchNode, BindingPattern,
)

_INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_header():
    """生成转译产物头部。

    v3.8.1：产物不再要求调用方先把仓库塞进 sys.path。依次尝试
    IMPOR_HOME、编译时安装根、产物所在目录及其父目录、当前目录，找到
    含 `impor/runtime.py` 的安装根后前置到 sys.path；全部找不到时给出
    带修复提示的 SystemExit，而不是裸 ModuleNotFoundError。
    """
    root = _INSTALL_ROOT
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 本文件由 Impor 编译器生成，请勿手改。\n"
        "import os\n"
        "import sys\n"
        "_IMPOR_HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "_IMPOR_CANDIDATES = [\n"
        "    os.environ.get(\"IMPOR_HOME\", \"\"),\n"
        "    %r,\n"
        "    _IMPOR_HERE,\n"
        "    os.path.dirname(_IMPOR_HERE),\n"
        "    os.getcwd(),\n"
        "]\n"
        "_IMPOR_FOUND = \"\"\n"
        "for _p in _IMPOR_CANDIDATES:\n"
        "    if not _p:\n"
        "        continue\n"
        "    try:\n"
        "        _p = os.path.abspath(_p)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if os.path.isdir(os.path.join(_p, \"impor\")):\n"
        "        _IMPOR_FOUND = _p\n"
        "        break\n"
        "if _IMPOR_FOUND and _IMPOR_FOUND not in sys.path:\n"
        "    sys.path.insert(0, _IMPOR_FOUND)\n"
        "try:\n"
        "    from impor.runtime import *\n"
        "    from impor.runtime import tick_frame, err_name, err_attr\n"
        "    import impor.runtime as _rt\n"
        "except ModuleNotFoundError as _e:\n"
        "    sys.stderr.write(\n"
        "        \"Impor 编译产物找不到运行时 impor.runtime。\\n\"\n"
        "        \"  已尝试: \" + \", \".join([p for p in _IMPOR_CANDIDATES if p]) + \"\\n\"\n"
        "        \"  修复: 设置环境变量 IMPOR_HOME 指向 Impor 安装目录，或把产物放回安装目录下运行。\\n\"\n"
        "    )\n"
        "    raise SystemExit(3) from _e\n"
    ) % root


HEADER = _make_header()

_ASSIGN_OPS = {"+=": "+=", "-=": "-=", "*=": "*=", "/=": "/=", "%=": "%="}


class TranspilerError(Exception):
    pass


# ---------------- 作用域分析 ----------------

def _walk_statements(stmts):
    """遍历语句（不进入嵌套 def/class 内部）。"""
    for s in stmts:
        yield s
        if isinstance(s, (If, While, For)):
            for sub in _walk_statements(s.body):
                yield sub
            if isinstance(s, If):
                for _, eb in s.elseifs:
                    for sub in _walk_statements(eb):
                        yield sub
                for sub in _walk_statements(s.elseb):
                    yield sub
        elif isinstance(s, TryCatch):
            for sub in _walk_statements(s.body):
                yield sub
            for sub in _walk_statements(s.catch_body):
                yield sub
            for sub in _walk_statements(s.finally_body):
                yield sub


def _local_names(stmts):
    """函数内局部绑定名：参数之外的局部赋值、复合赋值、for 变量、嵌套 def 名。"""
    names = set()
    for s in _walk_statements(stmts):
        if isinstance(s, Assign) and s.scope == 'local':
            names.add(s.name)
        elif isinstance(s, CompoundAssign) and s.scope == 'local':
            names.add(s.name)
        elif isinstance(s, For):
            names.add(s.var)
        elif isinstance(s, Def):
            names.add(s.name)
    return names


def _top_names(program):
    """程序顶层绑定名（模块全局）。"""
    names = set()
    for s in program.stmts:
        if isinstance(s, Assign):
            names.add(s.name)
        elif isinstance(s, CompoundAssign):
            names.add(s.name)
        elif isinstance(s, For):
            names.add(s.var)
        elif isinstance(s, (Def, ClassDef, ExceptionDef)):
            names.add(s.name if isinstance(s, Def) else (s.cls_name if isinstance(s, ClassDef) else s.exc_name))
    return names


def _scope_decls(stmts, top_names, enclosing_locals):
    """收集一个函数体需要声明的 global / nonlocal 名字。"""
    globals_needed, nonlocals_needed = set(), set()
    for s in _walk_statements(stmts):
        if isinstance(s, (Assign, CompoundAssign)):
            if s.scope == 'global':
                globals_needed.add(s.name)
            elif s.scope == 'nonlocal':
                nonlocals_needed.add(s.name)
            elif isinstance(s, CompoundAssign) and s.scope == 'local':
                if s.name in top_names:
                    globals_needed.add(s.name)
                elif s.name in enclosing_locals:
                    nonlocals_needed.add(s.name)
    return sorted(globals_needed), sorted(nonlocals_needed)


# ---------------- 代码生成 ----------------

class PyGen:
    def __init__(self, top_names, skip_tick=False):
        self.top_names = top_names
        self._catch_vars = set()
        self.skip_tick = bool(skip_tick)
        self._defer_seq = 0

    def _next_defer_name(self, prefix="_impor_defer"):
        self._defer_seq += 1
        return "%s_%d" % (prefix, self._defer_seq)

    def _has_defer(self, stmts):
        """判断当前函数/模块作用域内是否有 defer；不进入嵌套 def/class。"""
        stack = list(stmts or [])
        while stack:
            s = stack.pop()
            if isinstance(s, Defer):
                return True
            if isinstance(s, (Def, ClassDef, ExceptionDef)):
                continue
            if isinstance(s, Block):
                stack.extend(s.stmts)
            elif isinstance(s, If):
                stack.extend(s.body)
                for _, eb in s.elseifs:
                    stack.extend(eb)
                if s.elseb:
                    stack.extend(s.elseb)
            elif isinstance(s, (While, For)):
                stack.extend(s.body)
            elif isinstance(s, TryCatch):
                stack.extend(s.body)
                stack.extend(s.catch_body)
                if s.finally_body:
                    stack.extend(s.finally_body)
        return False

    def _wrap_defer_scope(self, body, seq_prefix):
        """把一个作用域的函数体包进 defer 进入/退出；返回新行列表。"""
        dvar = self._next_defer_name(seq_prefix)
        return ([f"{dvar} = _rt.defer_enter()", "try:"] + self.indent(body)
                + ["finally:", "    _rt.defer_exit(%s)" % dvar])

    def indent(self, lines, n=1):
        pad = "    " * n
        return [pad + ln if ln else ln for ln in lines]

    def gen_program(self, node):
        out = [HEADER.rstrip("\n")]
        body = []
        for s in node.stmts:
            self._emit_stmt(body, s)
        if self._has_defer(node.stmts):
            body = self._wrap_defer_scope(body, "_impor_defer_mod")
        out.extend(body)
        return "\n".join(out) + "\n"

    def _emit_stmt(self, out, stmt):
        """生成一条语句；首行追加 `# L{impor行号}` 注释供行号映射。"""
        lines = self.gen_stmt(stmt)
        if lines and stmt.lineno and not lines[0].lstrip().startswith("#"):
            lines[0] = lines[0] + f"  # L{stmt.lineno}"
        out.extend(lines)

    # ---- 表达式 ----
    def gen_expr(self, node):
        m = {
            Num: self.expr_Num, Str: self.expr_Str, FStr: self.expr_FStr, Bool: self.expr_Bool,
            Var: self.expr_Var, AttrGet: self.expr_AttrGet, BinOp: self.expr_BinOp, Pipe: self.expr_Pipe,
            UnaryOp: self.expr_UnaryOp, TernaryExpr: self.expr_TernaryExpr,
            Call: self.expr_Call, NewInstance: self.expr_NewInstance,
            SuperCall: self.expr_SuperCall, ListLit: self.expr_ListLit,
            DictLit: self.expr_DictLit, IndexGet: self.expr_IndexGet,
            Slice: self.expr_Slice,
        }
        f = m.get(type(node))
        if f is None:
            raise TranspilerError(f"无法编译的表达式节点 {type(node).__name__}")
        return f(node)

    def expr_Num(self, n):
        return repr(n.val)

    def expr_Str(self, n):
        return repr(n.val)

    def expr_FStr(self, n):
        # v2.9 字符串插值 → Python f-string：文本段原样，插值段转表达式
        from .lexer import unescape_impor
        parts = []
        ei = 0
        for kind, payload in n.parts:
            if kind == "text":
                # v3.6.16：Impor 用 \{ \} 表示字面花括号，但生成的是 Python
                # f-string；字面 { } 在 f-string 里会被再次当成插值。这里把
                # 文本段的每个花括号翻倍，Tcl 字体 "{{Segoe UI}} 9" 才能原样
                # 到达控件，而不是变成 Python 表达式或语法错误。
                text = unescape_impor(payload)
                parts.append(text.replace("{", "{{").replace("}", "}}"))
            else:
                parts.append("{" + self.gen_expr(n.exprs[ei]) + "}")
                ei += 1
        return "f" + repr("".join(parts))

    def expr_Bool(self, n):
        return "True" if n.val else "False"

    def expr_Var(self, n):
        return n.name

    def expr_AttrGet(self, n):
        obj = self.gen_expr(n.obj_expr)
        if isinstance(n.obj_expr, Var) and n.obj_expr.name in self._catch_vars:
            return f"err_attr({obj}, {n.attr_name!r})"
        return f"{obj}.{n.attr_name}"

    def expr_Pipe(self, n):
        # v2.9 管道：x |> f(1) == f(x, 1)；x |> f == f(x)
        rhs = n.right
        if isinstance(rhs, Call):
            args = [self.gen_expr(n.left)] + [self.gen_expr(a) for a in rhs.args]
            return self.gen_expr(rhs.callee) + "(" + ", ".join(args) + ")"
        return self.gen_expr(rhs) + "(" + self.gen_expr(n.left) + ")"

    def expr_BinOp(self, n):
        return f"({self.gen_expr(n.left)} {n.op} {self.gen_expr(n.right)})"

    def expr_UnaryOp(self, n):
        op = "not" if n.op in ("not", "!") else n.op
        return f"({op} {self.gen_expr(n.child)})"

    def expr_TernaryExpr(self, n):
        return f"({self.gen_expr(n.true_expr)} if {self.gen_expr(n.cond)} else {self.gen_expr(n.false_expr)})"

    def expr_Call(self, n):
        # v4.2：字典 keys()/values() 统一返回数组（与解释器/C 后端一致）
        if isinstance(n.callee, AttrGet) and not n.args:
            attr = n.callee.attr_name
            if attr in ("keys", "values"):
                obj = self.gen_expr(n.callee.obj_expr)
                return f"list({obj}.{attr}())"
        # v3.7.1：d.has(k) / d.contains(k) 在 Python 字典上没有同名方法，
        # 直接调用会在运行期 AttributeError；改走运行时桥接。
        if isinstance(n.callee, AttrGet) and n.callee.attr_name in ("has", "contains") \
                and len(n.args) == 1:
            obj = self.gen_expr(n.callee.obj_expr)
            key = self.gen_expr(n.args[0])
            return f"_rt.dict_has({obj}, {key})"
        callee = self.gen_expr(n.callee)
        args = ", ".join(self.gen_expr(a) for a in n.args)
        return f"{callee}({args})"

    def expr_NewInstance(self, n):
        args = ", ".join(self.gen_expr(a) for a in n.args)
        return f"{n.cls_name}({args})"

    def expr_SuperCall(self, n):
        return "super()"

    def expr_ListLit(self, n):
        return "[" + ", ".join(self.gen_expr(i) for i in n.items) + "]"

    def expr_DictLit(self, n):
        return "{" + ", ".join(f"{self.gen_expr(k)}: {self.gen_expr(v)}" for k, v in n.kv) + "}"

    def expr_IndexGet(self, n):
        return f"{self.gen_expr(n.container)}[{self.gen_expr(n.idx)}]"

    def expr_Slice(self, n):
        parts = []
        parts.append(self.gen_expr(n.start) if n.start is not None else "")
        parts.append(self.gen_expr(n.end) if n.end is not None else "")
        if n.step is not None:
            parts.append(self.gen_expr(n.step))
        return f"{self.gen_expr(n.container)}[{':'.join(parts)}]"

    # ---- 语句 ----
    def gen_stmt(self, node):
        m = {
            Assign: self.stmt_Assign, CompoundAssign: self.stmt_CompoundAssign,
            Print: self.stmt_Print, ExprStmt: self.stmt_ExprStmt, If: self.stmt_If, While: self.stmt_While,
            For: self.stmt_For, Def: self.stmt_Def, Return: self.stmt_Return,
            Break: self.stmt_Break, Continue: self.stmt_Continue,
            ExitScript: self.stmt_Exit, ClsOut: self.stmt_Cls,
            IndexSet: self.stmt_IndexSet, AttrSet: self.stmt_AttrSet,
            ImportNode: self.stmt_Import, Block: self.stmt_Block,
            Defer: self.stmt_Defer,
            FromImportNode: self.stmt_FromImport, ExportNode: self.stmt_Export,
            TryCatch: self.stmt_TryCatch, ClassDef: self.stmt_ClassDef,
            ExceptionDef: self.stmt_ExceptionDef, Raise: self.stmt_Raise,
            AssertNode: self.stmt_Assert,
            Call: self.stmt_Call, MatchNode: self.stmt_Match,
        }
        f = m.get(type(node))
        if f is None:
            raise TranspilerError(f"无法编译的语句节点 {type(node).__name__}")
        return f(node)

    def stmt_Assign(self, n):
        # global/nonlocal 声明由 stmt_Def 在函数头统一生成（_scope_decls），
        # 这里不再重复输出——重复声明在 Python 中若该名已赋值会报
        # "assigned to before global declaration"
        return [f"{n.name} = {self.gen_expr(n.expr)}"]

    def stmt_CompoundAssign(self, n):
        op = _ASSIGN_OPS[n.op]
        return [f"{n.name} {op} {self.gen_expr(n.expr)}"]

    def stmt_ExprStmt(self, n):
        # v2.9 裸表达式语句 → 表达式行
        return [self.gen_expr(n.expr)]

    def stmt_Print(self, n):
        return [f"print({', '.join(self.gen_expr(e) for e in n.items)})"]

    def stmt_If(self, n):
        lines = [f"if {self.gen_expr(n.cond)}:"]
        lines.extend(self.indent(self.gen_block(n.body)))
        for cond, eb in n.elseifs:
            lines.append(f"elif {self.gen_expr(cond)}:")
            lines.extend(self.indent(self.gen_block(eb)))
        if n.elseb:
            lines.append("else:")
            lines.extend(self.indent(self.gen_block(n.elseb)))
        return lines

    def stmt_While(self, n):
        lines = [f"while {self.gen_expr(n.cond)}:"]
        body = ([] if self.skip_tick else ["tick_frame()"]) + self.gen_block(n.body)
        lines.extend(self.indent(body))
        return lines

    def stmt_For(self, n):
        lines = [f"for {n.var} in {self.gen_expr(n.iter_expr)}:"]
        body = ([] if self.skip_tick else ["tick_frame()"]) + self.gen_block(n.body)
        lines.extend(self.indent(body))
        return lines

    def stmt_Def(self, n):
        globals_needed, nonlocals_needed = _scope_decls(
            n.body, self.top_names, self._enclosing_locals)
        lines = [f"def {n.name}({', '.join(n.params)}):"]
        preamble = []
        if globals_needed:
            preamble.append("global " + ", ".join(globals_needed))
        if nonlocals_needed:
            preamble.append("nonlocal " + ", ".join(nonlocals_needed))
        gb = self.gen_block(n.body, extra_locals=_local_names(n.body))
        # v2.9 隐式返回：函数末行为表达式语句（调用/裸表达式）时自动 return 其结果
        if n.body and isinstance(n.body[-1], (Call, ExprStmt)) and gb:
            gb[-1] = "return " + gb[-1]
        body = preamble + gb
        if self._has_defer(n.body):
            body = self._wrap_defer_scope(body, "_impor_defer_f")
        lines.extend(self.indent(body))
        return lines

    def stmt_Return(self, n):
        if n.expr is None:
            return ["return"]
        return [f"return {self.gen_expr(n.expr)}"]

    def stmt_Break(self, n):
        return ["break"]

    def stmt_Continue(self, n):
        return ["continue"]

    def stmt_Exit(self, n):
        return ["sys.exit(0)"]

    def stmt_Cls(self, n):
        return ["_rt.cls()"]

    def stmt_IndexSet(self, n):
        return [f"{self.gen_expr(n.container)}[{self.gen_expr(n.idx)}] = {self.gen_expr(n.val)}"]

    def stmt_AttrSet(self, n):
        obj = self.gen_expr(n.obj_expr)
        if isinstance(n.obj_expr, Var) and n.obj_expr.name in self._catch_vars:
            return [f"setattr({obj}, {n.attr_name!r}, {self.gen_expr(n.value)})"]
        return [f"{obj}.{n.attr_name} = {self.gen_expr(n.value)}"]

    def stmt_Import(self, n):
        if n.is_python:
            stmt = f"import {n.modname}"
            if n.alias:
                stmt += f" as {n.alias}"
            return [stmt]
        if n.modname in ("math", "random", "time", "os", "json", "string", "sys",
                         "re", "datetime", "collections", "itertools", "functools",
                         "statistics"):
            stmt = f"import {n.modname}"
            if n.alias:
                stmt += f" as {n.alias}"
            return [stmt]
        if n.alias:
            target = n.alias  # import mymod as mm / import pkg.sub as x：绑完整逻辑名
        elif "." in n.modname:
            # import pkg.sub：绑定顶层包名（与解释器 visit_ImportNode 语义一致）
            top = n.modname.split(".")[0]
            return [f"# import {n.modname}（绑定顶层包名）",
                    f"{top} = _rt.load_module({top!r})"]
        else:
            target = n.modname
        return [f"{target} = _rt.load_module({n.modname!r})"]

    def stmt_FromImport(self, n):
        """from [. ..] 模块 import 名字 [as 别名], ... | from 模块 import *"""
        if n.rel_level > 0:
            mod_var = f"_rt_from{n.lineno}"
            lines = [f"{mod_var} = _rt.load_rel_module({n.rel_level}, {n.modname!r})"]
        else:
            mod_var = f"_rt_from{n.lineno}"
            lines = [f"{mod_var} = _rt.load_module({n.modname!r})"]
        if n.star:
            lines.append(f"_rt.star_import({mod_var}, globals())")
            return lines
        for orig, alias in n.names:
            if not n.modname and n.rel_level > 0:
                # v2.12 修复：from . import x / from .. import y —— 加载包内子模块
                lines.append(f"{alias or orig} = _rt.load_rel_module({n.rel_level}, {orig!r})")
            else:
                lines.append(f"{alias or orig} = {mod_var}.{orig}")
        return lines

    def stmt_Export(self, n):
        # export 语义由解释器/运行时模块加载时执行；编译产物中为无操作
        return ["# export " + ", ".join(n.names)]

    def stmt_Block(self, n):
        return self.gen_block(n.stmts)

    def stmt_Defer(self, n):
        # v3.7.8：把 defer 块编成一个闭包，注册到当前作用域的延迟栈。
        fn = self._next_defer_name("_impor_defer_fn")
        lines = [f"def {fn}():"]
        body = self.gen_block(n.body)
        lines.extend(self.indent(body or ["pass"]))
        lines.append(f"_rt.defer({fn})")
        return lines

    def stmt_Call(self, n):
        return [self.gen_expr(n) ]

    def stmt_TryCatch(self, n):
        lines = ["try:"]
        lines.extend(self.indent(self.gen_block(n.body)))
        ev = n.err_var
        if n.err_type is None:
            lines.append(f"except Exception as {ev}:")
        else:
            lines.append(f"except Exception as {ev}:")
            body = [f"if err_name({ev}) == {n.err_type!r}:"]
            catch_body = self.gen_block(n.catch_body, catch_var=ev)
            body.extend(self.indent(catch_body))
            body.append("else:")
            body.extend(self.indent(["raise"]))
        if n.err_type is None:
            catch_body = self.gen_block(n.catch_body, catch_var=ev)
            lines.extend(self.indent(catch_body))
        else:
            lines.extend(self.indent(body))
        if n.finally_body:
            lines.append("finally:")
            lines.extend(self.indent(self.gen_block(n.finally_body)))
        return lines

    def stmt_ClassDef(self, n):
        base = f"({n.base_name})" if n.base_name else ""
        lines = [f"class {n.cls_name}{base}:"]
        body = []
        for s in n.body:
            if isinstance(s, Def):
                params = ["self"] + s.params
                fn_lines = [f"def {s.name}({', '.join(params)}):"]
                globals_needed, nonlocals_needed = _scope_decls(s.body, self.top_names, set())
                fb = []
                if globals_needed:
                    fb.append("global " + ", ".join(globals_needed))
                if nonlocals_needed:
                    fb.append("nonlocal " + ", ".join(nonlocals_needed))
                fb.extend(self.gen_block(s.body, extra_locals={"self"} | _local_names(s.body)))
                if self._has_defer(s.body):
                    fb = self._wrap_defer_scope(fb, "_impor_defer_m")
                fn_lines.extend(self.indent(fb))
                body.extend(fn_lines)
            elif isinstance(s, Assign):
                body.append(f"{s.name} = {self.gen_expr(s.expr)}")
        lines.extend(self.indent(body))
        return lines

    def stmt_ExceptionDef(self, n):
        return [f"class {n.exc_name}(UserError):", "    pass"]

    def stmt_Raise(self, n):
        if n.cause_expr is not None:
            return [f"raise _rt.raise_from({n.exc_type_name}({self.gen_expr(n.msg_expr)}), "
                    f"{self.gen_expr(n.cause_expr)})"]
        return [f"raise {n.exc_type_name}({self.gen_expr(n.msg_expr)})"]

    def stmt_Assert(self, n):
        # assert 条件：为假抛 Python AssertionError（解释器对应抛 ImporException AssertionError）
        return [f"if not ({self.gen_expr(n.cond)}):",
                "    raise AssertionError('断言失败')"]

    def stmt_Match(self, n):
        """match 表达式 转译为 if/elif 链；绑定模式在块内先赋值再无条件命中。"""
        var = f"_match{n.lineno}"
        lines = [f"{var} = {self.gen_expr(n.subject)}"]
        first = True
        for pattern, body in n.cases:
            kw = "if" if first else "elif"
            first = False
            if isinstance(pattern, BindingPattern):
                cond = "True"
                pre = []
                if pattern.name is not None:
                    pre = [f"{pattern.name} = {var}"]
            else:
                cond = f"{var} == {self.gen_expr(pattern)}"
                pre = []
            lines.append(f"{kw} {cond}:")
            for b in pre + self.gen_block(body):
                lines.append("    " + b)
        if n.elseb is not None:
            kw = "if" if first else "else"
            if kw == "if":
                lines.append("if True:")
            else:
                lines.append("else:")
            for b in self.gen_block(n.elseb):
                lines.append("    " + b)
        return lines

    # ---- 块 ----
    def gen_block(self, stmts, catch_var=None, extra_locals=None):
        """生成一组语句；catch_var 注入异常变量集合，extra_locals 供嵌套函数作用域分析。"""
        old_catch = self._catch_vars
        old_encl = getattr(self, "_enclosing_locals", frozenset())
        if catch_var:
            self._catch_vars = old_catch | {catch_var}
        if extra_locals:
            self._enclosing_locals = old_encl | frozenset(extra_locals)
        out = []
        for s in stmts:
            self._emit_stmt(out, s)
        self._catch_vars = old_catch
        self._enclosing_locals = old_encl
        return out


def transpile(ast_node, source_name=None, src_lines=None, skip_tick=False):
    """Impor AST → Python 源码字符串。

    source_name：原始 .impor 文件名（可选），用于行号映射提示；
    src_lines：原始源码行列表（可选），产物内嵌后运行时错误渲染源码片段+波浪线；
    产物会在头部之后安装运行时行号映射，未捕获异常显示 Impor 风格报错。
    """
    top_names = _top_names(ast_node)
    gen = PyGen(top_names, skip_tick=skip_tick)
    gen._enclosing_locals = frozenset()
    code = gen.gen_program(ast_node)

    def _scan_map(text):
        m = {}
        for idx, ln in enumerate(text.split("\n"), start=1):
            mm = re.search(r"# L(\d+)\s*$", ln)
            if mm:
                m[idx] = int(mm.group(1))
        return m

    if not _scan_map(code):
        return code
    head_text = HEADER.rstrip("\n")
    head_len = head_text.count("\n") + 1
    src_name = (source_name or "<impor>").replace("\\", "\\\\")
    # 先插入占位行保证后续行号正确，再重扫映射并填入真实 install 行
    placeholder = "_rt.install_tb_map({}, __file__, 'x')\n"
    lines = code.split("\n")
    code = "\n".join(lines[:head_len]) + "\n" + placeholder + "\n".join(lines[head_len:])
    final_map = _scan_map(code)
    map_repr = "{" + ", ".join(f"{k}: {v}" for k, v in sorted(final_map.items())) + "}"
    src_lines_repr = "None" if not src_lines else repr(list(src_lines))
    install_line = f"_rt.install_tb_map({map_repr}, __file__, {src_name!r}, {src_lines_repr})\n"
    code = code.replace(placeholder, install_line, 1)
    return code
