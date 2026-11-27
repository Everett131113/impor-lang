# -*- coding: utf-8 -*-
"""
Impor (I++) 静态分析器：作用域符号表 + lint 检查。

供两处使用：
  - `impor lint`：报告未定义变量 / 未使用变量 / 未使用导入 / 不可达代码
  - LSP 语言服务器：符号表（跳转定义 / 悬停提示）与诊断

不执行代码，不做类型检查；纯静态语义检查（低误报优先）。
"""

import os
import re

from . import ast_nodes as A
from .lexer import (BUILTIN_NAMES, KEYWORDS, tokenize,
                    TOK_OP, TOK_KEY, TOK_ID, TOK_NEWLINE, TOK_INDENT, TOK_DEDENT, TOK_EOF, TOK_DOC)

# 永不报"未定义"的特殊名字
IMPLICIT_NAMES = frozenset(("self", "super", "_"))

# 行级抑制：`# impor-lint-ignore`（忽略该行全部）或 `# impor-lint-ignore: rule1,rule2`
_LINE_IGNORE_RE = re.compile(r"#\s*impor-lint-ignore\s*(?::\s*([\w,\s\-]+))?")
# 文件级抑制：`# impor-lint-ignore-file`（忽略整个文件）
_FILE_IGNORE_RE = re.compile(r"#\s*impor-lint-ignore-file")


class Symbol:
    """一个符号声明。kind: var/param/def/class/exception/import/for/catch/match/field"""
    __slots__ = ("name", "kind", "line", "col", "scope", "doc")

    def __init__(self, name, kind, line, col, scope, doc=None):
        self.name = name
        self.kind = kind
        self.line = line      # 1 起
        self.col = col        # 0 起
        self.scope = scope    # 所在作用域名（"<module>" / 函数名 / 类名）
        self.doc = doc        # v3.5 文档注释（`##` 紧邻声明），供 hover / impor doc 使用

    def __repr__(self):
        return f"Symbol({self.name!r}, {self.kind}, {self.line}:{self.col}, {self.scope})"


class Issue:
    """一条 lint 问题。severity: 'error'/'warning'"""
    __slots__ = ("line", "col", "severity", "code", "msg")

    def __init__(self, line, col, severity, code, msg):
        self.line = line
        self.col = col
        self.severity = severity
        self.code = code
        self.msg = msg

    def __repr__(self):
        return f"{self.line}:{self.col} [{self.code}] {self.msg}"


class _Scope:
    def __init__(self, kind, name, parent=None):
        self.kind = kind
        self.name = name
        self.parent = parent
        self.decls = {}     # name -> (Symbol, is_written)
        self.reads = set()
        self.never_return = False  # 当前已出现 return/break/continue/exit

    def lookup(self, name):
        s = self
        while s is not None:
            if name in s.decls:
                return s.decls[name][0]
            s = s.parent
        return None


class Analyzer:
    """把 AST 转成符号表 + 问题列表。"""

    def __init__(self, ast, source_lines, disabled_rules=None, script_dir=None):
        self.ast = ast
        # v2.12：use 星导入静态解析的搜索基准（脚本目录优先）
        self._base_dirs = [os.path.abspath(script_dir) if script_dir else os.getcwd(),
                           os.getcwd()]
        self.lines = source_lines
        self.symbols = {}      # name -> [Symbol, ...]
        self.references = {}   # name -> [(line, col), ...] 定义+读取+写入全部位置（1 起行/0 起列）
        self.issues = []
        self._scope_stack = []
        self._scopes = []      # 全部作用域（含模块）
        self._all_reads = set()   # 整个程序读过的名字（跨作用域算使用）
        self._imported_names = {} # name -> Symbol(import)
        self._imported_mods = set()
        self._func_arities = {}   # v2.5 lint：(scope_name, name) -> 参数个数
        # lint 抑制
        self._disabled = set(disabled_rules or ())   # 全局禁用的规则 code
        self._file_ignored = False                   # # impor-lint-ignore-file
        self._line_ignores = {}                      # line -> set of code（None=全部）
        self._collect_ignores()

    # ---------- 工具 ----------
    def _collect_ignores(self):
        """扫描源码行，收集 lint 抑制指令。"""
        for i, ln in enumerate(self.lines, start=1):
            if _FILE_IGNORE_RE.search(ln):
                self._file_ignored = True
            m = _LINE_IGNORE_RE.search(ln)
            if m:
                spec = (m.group(1) or "").strip()
                codes = {c.strip() for c in spec.split(",") if c.strip()} if spec else None
                self._line_ignores[i] = codes  # None = 忽略该行全部

    def _ignored(self, issue):
        if self._file_ignored or issue.code in self._disabled:
            return True
        codes = self._line_ignores.get(issue.line)
        if codes is None and issue.line in self._line_ignores:
            return True
        return codes is not None and issue.code in codes

    def _col_of(self, name, line):
        """在源码行中找 name 的列（word 边界，0 起）。找不到返回 0。"""
        if 1 <= line <= len(self.lines):
            text = self.lines[line - 1]
            idx = text.find(name)
            if idx >= 0:
                return idx
        return 0

    def _cur_scope(self):
        return self._scope_stack[-1]

    def _module_scope(self):
        return self._scope_stack[0]

    def _add_symbol(self, name, kind, line, kind_hint=""):
        sym = Symbol(name, kind, line, self._col_of(name, line), self._cur_scope().name)
        self.symbols.setdefault(name, []).append(sym)
        self.references.setdefault(name, []).append((line, sym.col))
        scope = self._cur_scope()
        if name in scope.decls:
            return sym  # 重复声明：保留首个（不覆盖，避免误删首个定义）
        scope.decls[name] = (sym, False)
        return sym

    def _mark_write(self, name, lineno=0):
        if lineno:
            self.references.setdefault(name, []).append((lineno, self._col_of(name, lineno)))
        s = self._cur_scope()
        while s is not None:
            if name in s.decls:
                s.decls[name] = (s.decls[name][0], True)
                return
            s = s.parent

    def _read(self, name, line, col=0):
        if name in IMPLICIT_NAMES or name in KEYWORDS:
            return
        self._all_reads.add(name)
        self.references.setdefault(name, []).append((line, col))
        if self._cur_scope().lookup(name) is None and name not in BUILTIN_NAMES:
            self.issues.append(Issue(line, col, "error", "undefined-name",
                                     f"未定义变量: {name}"))

    # ---------- 遍历 ----------
    def _predeclare_module(self):
        """预声明模块级名字：使后置函数/变量被前向引用时不误报未定义。"""
        scope = self._module_scope()
        for stmt in self.ast.stmts:
            names = self._decl_names(stmt)
            for name in names:
                if name not in scope.decls:
                    scope.decls[name] = (Symbol(name, "pre", 0, 0, "module"), False)

    def _decl_names(self, stmt):
        """语句在模块级绑定的名字（用于预声明）。"""
        if isinstance(stmt, A.Assign):
            if getattr(stmt, "is_enum", False):
                return [stmt.name]
            if getattr(stmt, "scope", "") in ("local", "const"):
                return [stmt.name]
            return []
        if isinstance(stmt, A.CompoundAssign):
            return [stmt.name]
        if isinstance(stmt, A.For):
            return [stmt.var]
        if isinstance(stmt, A.Def):
            return [stmt.name]
        if isinstance(stmt, A.ClassDef):
            return [stmt.cls_name]
        if isinstance(stmt, A.ExceptionDef):
            return [stmt.exc_name]
        if isinstance(stmt, A.ImportNode):
            if stmt.alias:
                return [stmt.alias]
            return [stmt.modname.split(".")[0]]  # import pkg.sub → pkg
        if isinstance(stmt, A.FromImportNode):
            if stmt.star:
                # v2.12：use 模块 → 静态解析目标模块导出名（读源码，不执行）
                return list(_collect_star_names(stmt.modname, self._base_dirs))
            return [alias or orig for orig, alias in stmt.names]
        return []

    def run(self):
        self._scope_stack.append(_Scope("module", "<module>"))
        self._predeclare_module()
        for stmt in self.ast.stmts:
            self._stmt(stmt)
        self._finalize()
        self._apply_docs()
        self.issues = [i for i in self.issues if not self._ignored(i)]
        return self

    def _apply_docs(self):
        """v3.5：把 AST 上的 `##` 文档注释挂到对应符号（同名同起始行优先）。"""
        docs = {}
        for node in _walk_nodes(self.ast):
            text = getattr(node, "doc", None)
            if not text:
                continue
            name = getattr(node, "name", None) or getattr(node, "cls_name", None) \
                or getattr(node, "exc_name", None)
            if not name:
                continue
            docs[(name, getattr(node, "lineno", 0))] = text
        for name, syms in self.symbols.items():
            for sym in syms:
                text = docs.get((name, sym.line))
                if text and not sym.doc:
                    sym.doc = text

    def _push(self, kind, name):
        s = _Scope(kind, name, self._cur_scope())
        self._scope_stack.append(s)
        self._scopes.append(s)

    def _pop(self):
        self._scope_stack.pop()

    def _stmt(self, n):
        m = {
            A.Assign: self._stmt_assign,
            A.CompoundAssign: self._stmt_compound,
            A.Print: self._stmt_print,
            A.If: self._stmt_if,
            A.While: self._stmt_while,
            A.For: self._stmt_for,
            A.Def: self._stmt_def,
            A.Return: self._stmt_return,
            A.Call: self._expr_call,
            A.NewInstance: self._expr_new,
            A.SuperCall: self._stmt_super,
            A.Break: self._stmt_jump,
            A.Continue: self._stmt_jump,
            A.ExitScript: self._stmt_jump,
            A.ImportNode: self._stmt_import,
            A.FromImportNode: self._stmt_fromimport,
            A.ExportNode: self._stmt_export,
            A.TryCatch: self._stmt_try,
            A.ClassDef: self._stmt_class,
            A.ExceptionDef: self._stmt_exception,
            A.Raise: self._stmt_raise,
            A.MatchNode: self._stmt_match,
            A.Block: self._stmt_block,
            A.Defer: self._stmt_defer,
            A.ExprStmt: lambda n: self._expr(n.expr),
            A.AttrSet: self._stmt_attrset,
            A.IndexSet: self._stmt_indexset,
        }
        if not isinstance(n, (A.Return, A.Break, A.Continue, A.ExitScript)):
            self._dead_switch(n.lineno)
        fn = m.get(type(n))
        if fn is not None:
            fn(n)
        else:
            self._expr(n)

    def _stmt_attrset(self, n):
        self._expr(n.obj_expr)
        self._expr(n.value)

    def _stmt_indexset(self, n):
        self._expr(n.container)
        self._expr(n.idx)
        self._expr(n.val)

    def _decl_and_expr(self, name, expr, kind, line):
        """声明一个变量并分析初始化表达式。"""
        self._add_symbol(name, kind, line)
        self._expr(expr)
        self._mark_write(name, line)

    def _stmt_assign(self, n):
        if getattr(n, "is_enum", False):
            self._decl_and_expr(n.name, n.expr, "var", n.lineno)
            return
        if getattr(n, "scope", "") in ("update", "global", "nonlocal"):
            # 更新/跨作用域赋值不产生新绑定；名字必须已存在。
            self._all_reads.add(n.name)
            self.references.setdefault(n.name, []).append(
                (n.lineno, self._col_of(n.name, n.lineno)))
            if self._cur_scope().lookup(n.name) is None and n.name not in BUILTIN_NAMES:
                self.issues.append(Issue(
                    n.lineno, self._col_of(n.name, n.lineno), "error",
                    "undeclared-assign",
                    f"变量 {n.name} 未声明；首次赋值请写 `var {n.name} = ...`"))
            self._mark_write(n.name, n.lineno)
            self._expr(n.expr)
            return
        self._decl_and_expr(n.name, n.expr, "var", n.lineno)

    def _stmt_compound(self, n):
        # 复合赋值 = 读 + 写；不产生新绑定
        self._expr(n.expr)
        self._read(n.name, n.lineno, self._col_of(n.name, n.lineno))
        self._mark_write(n.name, n.lineno)

    def _stmt_print(self, n):
        for e in n.items:
            self._expr(e)

    def _stmt_if(self, n):
        self._expr(n.cond)
        self._push("if", "if")
        for s in n.body:
            self._stmt(s)
        self._pop()
        for cond, bd in n.elseifs:
            self._expr(cond)
            self._push("elseif", "elseif")
            for s in bd:
                self._stmt(s)
            self._pop()
        if n.elseb:
            self._push("else", "else")
            for s in n.elseb:
                self._stmt(s)
            self._pop()

    def _stmt_while(self, n):
        self._expr(n.cond)
        self._push("while", "while")
        for s in n.body:
            self._stmt(s)
        self._pop()

    def _stmt_for(self, n):
        self._expr(n.iter_expr)
        self._add_symbol(n.var, "for", n.lineno)
        self._mark_write(n.var, n.lineno)
        self._push("for", "for")
        for s in n.body:
            self._stmt(s)
        self._pop()

    def _stmt_def(self, n):
        self._func_arities[(self._cur_scope().name, n.name)] = len(n.params)
        self._add_symbol(n.name, "def", n.lineno)
        self._mark_write(n.name, n.lineno)
        self._push("func", n.name)
        for p in n.params:
            self._add_symbol(p, "param", n.lineno)
        for s in n.body:
            self._stmt(s)
        self._pop()

    def _stmt_return(self, n):
        if n.expr is not None:
            self._expr(n.expr)
        self._cur_scope().never_return = True

    def _stmt_jump(self, n):
        self._cur_scope().never_return = True

    def _stmt_super(self, n):
        pass

    def _dead_switch(self, line):
        """当前作用域已 return/break 后继续出现的语句 → 不可达。"""
        if self._cur_scope().never_return:
            self.issues.append(Issue(line, 0, "warning", "unreachable",
                                     "不可达代码（之前的语句已跳出当前块）"))
        self._cur_scope().never_return = False

    def _stmt_import(self, n):
        mod = n.modname
        self._imported_mods.add(mod)
        local = n.alias or mod.split(".")[-1]
        sym = self._add_symbol(local, "import", n.lineno)
        self._imported_names[local] = sym
        self._mark_write(local, n.lineno)

    def _stmt_fromimport(self, n):
        self._imported_mods.add(n.modname)
        if n.star:
            return
        for orig, alias in n.names:
            local = alias or orig
            if local == "_":
                continue
            sym = self._add_symbol(local, "import", n.lineno)
            self._imported_names[local] = sym
            self._mark_write(local, n.lineno)

    def _stmt_export(self, n):
        # 被导出的名字视为已使用
        for nm in n.names:
            self._all_reads.add(nm)

    def _stmt_try(self, n):
        self._push("try", "try")
        for s in n.body:
            self._stmt(s)
        self._pop()
        if n.err_var:
            self._add_symbol(n.err_var, "catch", n.lineno)
            self._mark_write(n.err_var, n.lineno)
        if n.catch_body:
            self._push("catch", "catch")
            for s in n.catch_body:
                self._stmt(s)
            self._pop()
        if n.finally_body:
            self._push("finally", "finally")
            for s in n.finally_body:
                self._stmt(s)
            self._pop()

    def _stmt_class(self, n):
        self._add_symbol(n.cls_name, "class", n.lineno)
        self._mark_write(n.cls_name, n.lineno)
        if n.base_name:
            self._read(n.base_name, n.lineno, self._col_of(n.base_name, n.lineno))
        self._push("class", n.cls_name)
        # 类体：方法名 = 声明；字段 = 声明
        for stmt in n.body:
            if isinstance(stmt, A.Def):
                self._add_symbol(stmt.name, "def", stmt.lineno)
                self._mark_write(stmt.name, stmt.lineno)
                self._func_arities[(self._cur_scope().name, stmt.name)] = len(stmt.params)
                self._push("func", stmt.name)
                # 方法隐式持有 self
                self._add_symbol("self", "param", stmt.lineno)
                for p in stmt.params:
                    self._add_symbol(p, "param", stmt.lineno)
                for s in stmt.body:
                    self._stmt(s)
                self._pop()
            elif isinstance(stmt, A.Assign):
                self._add_symbol(stmt.name, "field", stmt.lineno)
                self._expr(stmt.expr)
            else:
                self._stmt(stmt)
        self._pop()

    def _stmt_exception(self, n):
        self._add_symbol(n.exc_name, "exception", n.lineno)
        self._mark_write(n.exc_name, n.lineno)

    def _stmt_raise(self, n):
        if n.exc_type_name not in IMPLICIT_NAMES:
            self._read(n.exc_type_name, n.lineno, self._col_of(n.exc_type_name, n.lineno))
        if n.msg_expr is not None:
            self._expr(n.msg_expr)
        if n.cause_expr is not None:
            self._expr(n.cause_expr)

    def _stmt_match(self, n):
        self._expr(n.subject)
        for pattern, body in n.cases:
            self._push("case", "case")
            if isinstance(pattern, A.BindingPattern):
                if pattern.name is not None:
                    self._add_symbol(pattern.name, "match", pattern.lineno)
            else:
                self._expr(pattern)
            for s in body:
                self._stmt(s)
            self._pop()

    def _stmt_block(self, n):
        for s in n.stmts:
            self._stmt(s)

    def _stmt_defer(self, n):
        # defer 体与所在函数同作用域，不新建变量作用域。
        for s in n.body:
            self._stmt(s)

    # ---------- 表达式 ----------
    def _expr_fstr(self, n):
        # v2.9 字符串插值：内联表达式计入变量使用
        for e in n.exprs:
            self._expr(e)

    def _expr(self, n):
        m = {
            A.Num: lambda x: None,
            A.Str: lambda x: None,
            A.FStr: self._expr_fstr,
            A.Bool: lambda x: None,
            A.Var: self._expr_var,
            A.AttrGet: self._expr_attrget,
            A.BinOp: self._expr_binop,
            A.Pipe: self._expr_pipe,
            A.UnaryOp: self._expr_unary,
            A.TernaryExpr: self._expr_ternary,
            A.Call: self._expr_call,
            A.NewInstance: self._expr_new,
            A.SuperCall: lambda x: None,
            A.ListLit: self._expr_list,
            A.DictLit: self._expr_dict,
            A.IndexGet: self._expr_indexget,
            A.Slice: self._expr_slice,
            A.BindingPattern: lambda x: None,
        }
        fn = m.get(type(n))
        if fn is not None:
            fn(n)

    def _expr_var(self, n):
        self._read(n.name, n.lineno, self._col_of(n.name, n.lineno))

    def _expr_attrget(self, n):
        self._expr(n.obj_expr)

    def _expr_binop(self, n):
        self._expr(n.left)
        self._expr(n.right)

    def _expr_pipe(self, n):
        # v2.9 管道：左右两侧都分析（左侧数据源 + 右侧调用/函数）
        self._expr(n.left)
        self._expr(n.right)

    def _expr_unary(self, n):
        self._expr(n.child)

    def _expr_ternary(self, n):
        self._expr(n.cond)
        self._expr(n.true_expr)
        self._expr(n.false_expr)

    # v2.5 lint：内建函数参数个数（min, max；可变参数不列入）
    _BUILTIN_ARITIES = {
        "len": (1, 1), "to_num": (1, 1), "to_str": (1, 1), "str": (1, 1), "int": (1, 1), "type": (1, 1),
        "type_of": (1, 1),
        "list": (1, 1), "clamp": (3, 3), "lerp": (3, 3), "remap": (5, 5),
        "sign": (1, 1), "distance": (4, 4), "angle": (4, 4),
        "randi": (2, 2), "randstr": (1, 1), "seed": (1, 1),
        "trim": (1, 1), "upper": (1, 1), "lower": (1, 1),
        "strpad": (3, 3), "strcut": (3, 3), "strjoin": (2, 2), "hash": (1, 1),
        "arrappend": (2, 2), "arrpop": (1, 1), "arrfind": (2, 2),
        "arrfill": (2, 2), "arrdup": (1, 1), "arrrev": (1, 1), "shuffle": (1, 1),
        "max_arr": (1, 1), "min_arr": (1, 1), "sum_arr": (1, 1),
        "rgb": (3, 3), "sleep": (1, 1), "reload": (1, 1),
        "readln": (1, 1), "writeln": (2, 2), "filemv": (2, 2), "filecat": (2, 2),
        "input": (0, 1), "inputstr": (0, 1), "range": (1, 3), "open": (1, 2),
        "vec2": (0, 2),
    }

    def _find_func_arity(self, name):
        """按作用域链找最近同名函数的参数个数，找不到返回 None。"""
        s = self._cur_scope()
        while s is not None:
            arity = self._func_arities.get((s.name, name))
            if arity is not None:
                return arity
            s = s.parent
        return None

    def _check_call_arity(self, n):
        """v2.5 lint：调用参数个数与函数定义/内建签名比对。"""
        callee = n.callee
        if not isinstance(callee, A.Var) or callee.name in IMPLICIT_NAMES:
            return
        name = callee.name
        arity = self._find_func_arity(name)
        if arity is not None:
            if len(n.args) == arity:
                return
            self.issues.append(Issue(n.lineno, self._col_of(name, n.lineno), "warning",
                                     "arg-count",
                                     f"函数 {name} 需要 {arity} 个参数，实际传入 {len(n.args)} 个"))
            return
        bound = self._BUILTIN_ARITIES.get(name)
        if bound is None:
            return
        lo, hi = bound
        if lo <= len(n.args) <= hi:
            return
        expect = f"{lo}" if lo == hi else f"{lo}-{hi}"
        self.issues.append(Issue(n.lineno, self._col_of(name, n.lineno), "warning",
                                 "arg-count",
                                 f"内建函数 {name} 需要 {expect} 个参数，实际传入 {len(n.args)} 个"))

    def _expr_call(self, n):
        self._expr(n.callee)
        for a in n.args:
            self._expr(a)
        self._check_call_arity(n)

    def _expr_new(self, n):
        self._read(n.cls_name, n.lineno, self._col_of(n.cls_name, n.lineno))
        for a in n.args:
            self._expr(a)

    def _expr_list(self, n):
        for i in n.items:
            self._expr(i)

    def _expr_dict(self, n):
        for k, v in n.kv:
            self._expr(k)
            self._expr(v)

    def _expr_indexget(self, n):
        self._expr(n.container)
        self._expr(n.idx)

    def _expr_slice(self, n):
        self._expr(n.container)
        if n.start is not None:
            self._expr(n.start)
        if n.end is not None:
            self._expr(n.end)
        if n.step is not None:
            self._expr(n.step)

    # ---------- 收尾 ----------
    def _finalize(self):
        # 1) 未使用变量：函数作用域内的声明（参数/局部/for/catch/match）从未被读
        for scope in self._scopes:
            if scope.kind not in ("func", "for", "catch", "case"):
                continue
            for name, (sym, written) in scope.decls.items():
                if name in IMPLICIT_NAMES:
                    continue
                if name in self._all_reads:
                    continue
                if name in self._imported_names:
                    continue
                self.issues.append(Issue(sym.line, sym.col, "info", "unused-variable",
                                         f"未使用变量: {name}"))
        # 2) 未使用导入：导入名从未被读
        for name, sym in self._imported_names.items():
            if name not in self._all_reads and name != "_":
                self.issues.append(Issue(sym.line, sym.col, "info", "unused-import",
                                         f"未使用导入: {name}"))


import re as _re


_TOP_DECL_RE = _re.compile(r'^(?:func|def|var|class|exception)\s+([A-Za-z_\u4e00-\u9fa5][A-Za-z0-9_\u4e00-\u9fa5]*)', _re.M)


def _find_module_file(modname, base_dirs):
    """静态定位 .impor 模块/包文件（use 静态解析用）。"""
    parts = modname.split(".")
    dirs = list(base_dirs)
    if modname == "std" or modname.startswith("std."):
        std_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")
        dirs.insert(0, std_dir)
        parts = parts[1:] if len(parts) > 1 else []
    for d in dirs:
        b = os.path.join(d, *parts)
        if os.path.isfile(os.path.join(b, "__pkg__.impor")):
            return os.path.join(b, "__pkg__.impor")
        if os.path.isfile(b + ".impor"):
            return b + ".impor"
    return None


def _collect_star_names(modname, base_dirs):
    """读取 use 目标模块源码，返回其对外导出符号名集合（纯静态，不执行代码）。

    有 export 语句 → 导出集 = export 声明的名字；
    无 export（旧脚本兼容）→ 全部顶层 func/var/class 名。
    """
    path = _find_module_file(modname, base_dirs)
    if not path:
        return set()          # 找不到模块：不报未定义（放水，避免误报）
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return set()
    exports = _re.findall(r'^\s*export\s+(?:\{\s*)?([A-Za-z_\u4e00-\u9fa5][A-Za-z0-9_\u4e00-\u9fa5]*)', src, _re.M)
    if exports:
        return set(exports)
    return set(_TOP_DECL_RE.findall(src))


def analyze(ast, source_lines, disabled_rules=None, script_dir=None):
    """便捷入口：返回 Analyzer（含 .symbols 与 .issues）。

    disabled_rules：全局禁用的规则 code 集合（例如来自 impor.toml [lint] disable）。
    """
    return Analyzer(ast, source_lines, disabled_rules,
                     script_dir=script_dir).run()


def _walk_nodes(root):
    """深度遍历所有 AST 节点（含列表字段），顺序不保证。"""
    stack = [root]
    while stack:
        n = stack.pop()
        if not isinstance(n, A.AST):
            continue
        yield n
        for v in getattr(n, "__dict__", {}).values():
            if isinstance(v, A.AST):
                stack.append(v)
            elif isinstance(v, list):
                stack.extend([x for x in v if isinstance(x, A.AST)])


# v3.5：宽容写法 → 推荐写法（`impor lint --style` 的建议文本）
_STYLE_ADVICE = {
    "style-cn-alias": "改用英文写法（中文软别名只作兼容，不进入推荐写法表）",
    "style-print": "print 加括号：print(x)",
    "style-semicolon": "一行一条语句，不用 ; 分隔",
    "style-then": "删除 then / 则，直接写块：if x { ... }",
    "style-unless": "改用 if not 条件",
    "style-foreach": "用 for x in 表（each 只作兼容）",
    "style-forof": "用 for x in 表（of 只作兼容）",
    "style-range": "用 for i in range(a, b + 1)",
    "style-incdec": "用 x += 1 / x -= 1",
    "style-operator": "用 and / or / != 代替 && / || / <>",
    "style-walrus": "用 = 代替 :=",
    "style-extends": "用 class 子(父) { ... }",
    "style-new": "new 后写括号：new A()",
    "style-catch": "catch 写明异常变量：catch e { ... }",
    "style-params": "函数参数加括号：def f(a, b)",
    "style-cfor": "用 for i in range(...) 或 while",
    "style-dountil": "用 while 循环（do..until 只作兼容）",
    "style-naming": "用能表达含义的名字；循环计数可用 i / j / k",
    "style-magic-number": "把重复出现的数字提取为命名常量（const）",
    "style-long-function": "按职责拆成多个小函数，保持单个函数可一屏读完",
}


def style_lint(source, filename=None):
    """v3.5 推荐写法检查（token 级，不构建 AST，不改变语义）。

    只报告「v3.4 兼容写法」，每条都给出唯一推荐写法；全部为 info 级，不影响退出码。
    """
    issues = []
    try:
        toks = tokenize(source)
    except Exception:
        return issues

    by_line = {}
    for tk in toks:
        if tk.type in (TOK_NEWLINE, TOK_INDENT, TOK_DEDENT, TOK_EOF, TOK_DOC):
            continue
        by_line.setdefault(tk.line, []).append(tk)

    seen = set()

    def report(rule, tk):
        key = (rule, tk.line, tk.col)
        if key in seen:
            return
        seen.add(key)
        desc = _style_desc(rule)
        if rule == "style-cn-alias" and tk.src:
            desc = f"中文软别名「{tk.src}」"
        issues.append(Issue(tk.line, max(1, tk.col), "info", rule,
                            f"不推荐写法：{desc}；推荐：{_STYLE_ADVICE[rule]}"))

    for line in sorted(by_line):
        ts = by_line[line]
        # C 风格 for 单独判定，避免把 header 里的 ; 重复报成"分号分隔"
        cfor = bool(ts and ts[0].type == TOK_KEY and ts[0].value == "for"
                    and any(t.type == TOK_OP and t.value == "=" for t in ts[1:]))
        for i, tk in enumerate(ts):
            nxt = ts[i + 1] if i + 1 < len(ts) else None
            prv = ts[i - 1] if i > 0 else None

            # v5.2：中文关键字与中文软别名已移除，不再做中文别名风格提示
            if tk.type == TOK_ID:
                if tk.value == "print" and nxt is not None and nxt.value != "(" \
                        and not (prv is not None and prv.value in ("(", "[", ",", "=")):
                    report("style-print", tk)
                elif tk.value == "then" and any(t.type == TOK_KEY and t.value in ("if", "elseif", "while") for t in ts[:i]):
                    report("style-then", tk)
                elif tk.value == "unless" and i == 0:
                    report("style-unless", tk)
                elif tk.value == "extends" and any(t.type == TOK_KEY and t.value == "class" for t in ts[:i]):
                    report("style-extends", tk)
                elif tk.value == "each" and prv is not None and prv.value == "for":
                    report("style-foreach", tk)
                elif tk.value == "of" and any(t.type == TOK_KEY and t.value == "for" for t in ts[:i]):
                    report("style-forof", tk)
                elif tk.value == "do":
                    report("style-dountil", tk)
                continue

            if tk.type != TOK_OP:
                if tk.type in (TOK_KEY, TOK_ID):
                    if tk.value == "new" and nxt is not None and nxt.type == TOK_ID \
                            and (i + 2 >= len(ts) or ts[i + 2].value != "("):
                        report("style-new", tk)
                    elif tk.value == "catch" and nxt is not None and nxt.value == "{":
                        report("style-catch", tk)
                    elif tk.value in ("func", "def") and nxt is not None and nxt.type == TOK_ID \
                            and i + 2 < len(ts) and ts[i + 2].type == TOK_ID:
                        report("style-params", tk)
                    elif tk.value == "each" and prv is not None and prv.value == "for":
                        report("style-foreach", tk)
                    elif tk.value == "of" and any(t.type == TOK_KEY and t.value == "for" for t in ts[:i]):
                        report("style-forof", tk)
                    elif tk.value == "do":
                        report("style-dountil", tk)
                continue

            if tk.value == ";" and not cfor:
                report("style-semicolon", tk)
            elif tk.value == "..":
                report("style-range", tk)
            elif tk.value in ("++", "--"):
                if not cfor:  # C 风格 for 已经整体报过，不重复
                    report("style-incdec", tk)
            elif tk.value in ("&&", "||", "<>"):
                report("style-operator", tk)
            elif tk.value == ":=":
                report("style-walrus", tk)

        if cfor:
            report("style-cfor", ts[0])

    return issues


def readability_lint(ast, filename=None, config=None):
    """v3.8 可读性提示（在 `impor lint --style` 下与推荐写法一起输出）。

    规则刻意保守，全部为 info 级，不影响退出码：
      - style-naming：函数/类名只有 1 个字符，或变量名只有 1 个字符
        （i/j/k/x/y/n/m/t 这类常见短名放行，避免循环计数误报）
      - style-magic-number：同一个非小整数在文件中出现 N 次以上
        （常量声明与 enum 成员不计；单次出现不报，避免把普通常量误判）
      - style-long-function：函数跨越 N 行以上或直接语句超过 M 条
      - enum-exhaustive：v3.9 对 enum 值的 match 未覆盖全部成员且无 default
    阈值可由 impor.toml [lint] 的 max_function_lines / max_function_statements /
    magic_number_repeats / short_name_whitelist / enum_exhaustive 调整。
    """
    if ast is None:
        return []
    cfg = config or {}

    def cfg_int(key, default):
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    short_names = set(cfg.get("short_name_whitelist") or
                      ["i", "j", "k", "x", "y", "n", "m", "t", "e", "_"])
    magic_repeats = max(2, cfg_int("magic_number_repeats", 3))
    max_span = max(1, cfg_int("max_function_lines", 50))
    max_stmts = max(1, cfg_int("max_function_statements", 30))
    enum_exhaustive = bool(cfg.get("enum_exhaustive", True))

    issues = []
    seen = set()

    def report(n, code, msg):
        key = (code, getattr(n, "lineno", 0), getattr(n, "col", 0))
        if key in seen:
            return
        seen.add(key)
        issues.append(Issue(getattr(n, "lineno", 1), max(1, getattr(n, "col", 0)),
                           "info", code, msg))

    enum_values = set()
    enum_members = {}
    enum_vars = {}
    for node in _walk_nodes(ast):
        if isinstance(node, A.Assign) and getattr(node, "is_enum", False):
            if isinstance(node.expr, A.Num):
                enum_values.add(node.expr.val)
            etype = getattr(node, "enum_type", None)
            member = getattr(node, "enum_member", None)
            if etype and member:
                enum_members.setdefault(etype, set()).add(member)
        elif isinstance(node, A.Assign) and isinstance(node.expr, A.Var):
            src = node.expr.name
            if src.startswith("__enum_"):
                for etype in enum_members:
                    prefix = "__enum_%s_" % etype
                    if src.startswith(prefix):
                        enum_vars[node.name] = etype
                        break

    # 1) 命名：只抓明显不可读的单字符声明，不强制大小写/下划线风格
    for node in _walk_nodes(ast):
        if isinstance(node, (A.Def, A.ClassDef)):
            name = getattr(node, "name", None) or getattr(node, "cls_name", None)
            if name and len(name) == 1 and name not in short_names \
                    and not name.startswith("_"):
                report(node, "style-naming",
                       f"命名过短：{name}；建议改成能表达用途的名字")
        elif isinstance(node, A.Assign):
            name = node.name
            if getattr(node, "is_enum", False) or name.startswith("__enum_"):
                continue
            if node.scope in ("global", "local") and len(name) == 1 \
                    and name not in short_names:
                report(node, "style-naming",
                       f"命名过短：{name}；建议改成能表达含义的变量名")

    # 2) 魔法数字：重复 N 次以上的非小整数才提示（阈值刻意高，避免误报）
    counts = {}
    first = {}
    for node in _walk_nodes(ast):
        if not isinstance(node, A.Num) or type(node.val) is not int:
            continue
        v = node.val
        if v in enum_values or abs(v) <= 3 or v in (10, 100, 1000):
            continue
        counts[v] = counts.get(v, 0) + 1
        first.setdefault(v, node)
    for v, c in counts.items():
        if c >= magic_repeats:
            report(first[v], "style-magic-number",
                   f"魔法数字 {v} 在文件中出现 {c} 次；建议提取为 const 命名常量")

    # 3) 函数过长：按真实源码跨度与直接语句数双重判断
    for node in _walk_nodes(ast):
        if not isinstance(node, A.Def):
            continue
        end = node.lineno
        for stmt in node.body:
            for sub in _walk_nodes(stmt):
                end = max(end, getattr(sub, "lineno", end))
        span = end - node.lineno + 1
        if span > max_span or len(node.body) > max_stmts:
            report(node, "style-long-function",
                   f"函数过长：{node.name}（{span} 行 / {len(node.body)} 条语句）；建议按职责拆分")

    # 4) enum 穷尽性：match 一个 enum 变量时，必须覆盖全部成员或有 default/case _
    if enum_exhaustive:
        for node in _walk_nodes(ast):
            if not isinstance(node, A.MatchNode):
                continue
            subj = node.subject
            etype = None
            if isinstance(subj, A.Var):
                etype = enum_vars.get(subj.name)
                if etype is None:
                    for t in enum_members:
                        if subj.name.startswith("__enum_%s_" % t):
                            etype = t
                            break
            if etype is None:
                # 函数参数等没有显式赋值来源时，从 case 的枚举成员反推类型
                for pattern, _body in node.cases:
                    if isinstance(pattern, A.Var) and pattern.name.startswith("__enum_"):
                        for t in enum_members:
                            if pattern.name.startswith("__enum_%s_" % t):
                                etype = t
                                break
                    if etype:
                        break
            if etype is None or etype not in enum_members:
                continue
            covered = set()
            exhaustive = False
            for pattern, _body in node.cases:
                if isinstance(pattern, A.BindingPattern):
                    exhaustive = True
                    continue
                if isinstance(pattern, A.Var):
                    prefix = "__enum_%s_" % etype
                    if pattern.name.startswith(prefix):
                        covered.add(pattern.name[len(prefix):])
            if exhaustive or covered >= enum_members[etype]:
                continue
            missing = sorted(enum_members[etype] - covered)
            report(node, "enum-exhaustive",
                   f"match 未覆盖 enum {etype} 的全部成员（缺 {', '.join(missing)}）；"
                   f"建议补全 case 或加 default")
    _ = filename
    return issues


def _style_desc(rule):
    return {
        "style-cn-alias": "中文软别名",
        "style-print": "print 省略括号",
        "style-semicolon": "分号作语句分隔",
        "style-then": "then / 则 作块引导",
        "style-unless": "unless / 除非 取反条件",
        "style-foreach": "for each",
        "style-forof": "for ... of",
        "style-range": ".. 闭区间",
        "style-incdec": "++ / -- 自增自减",
        "style-operator": "&& / || / <> 符号别名",
        "style-walrus": ":= 赋值别名",
        "style-extends": "extends 继承写法",
        "style-new": "new 省略空括号",
        "style-catch": "catch 省略异常变量",
        "style-params": "函数参数省略括号",
        "style-cfor": "C 风格 for",
        "style-dountil": "do..until 循环",
        "style-naming": "命名过短或不可读",
        "style-magic-number": "魔法数字重复出现",
        "style-long-function": "函数过长",
        "enum-exhaustive": "enum 分支未穷尽",
    }.get(rule, rule)
