# -*- coding: utf-8 -*-
"""v3.7.3 原生数值快路径。

只接受“纯整数、单返回值”的脚本子集，生成不装箱的 C 代码：
变量与参数都是 ``long long``，算术直接落到机器整数，不经过 ``Val*`` 堆分配。
任何超出子集的写法都抛 ``FastUnsupported``，由 CLI 回退到原有后端；
绝不静默改变语义。

这个路径是显式 opt-in（``impor build --fast``），因为 C 的 64 位整数
不具备 Impor 解释器的任意精度整数语义。
"""

from . import ast_nodes as A


class FastUnsupported(Exception):
    def __init__(self, msg, lineno=0, col=0):
        super().__init__(msg)
        self.msg = msg
        self.lineno = lineno
        self.col = col


_INT_BIN = {"+", "-", "*", "%"}
_CMP_BIN = {"==", "!=", "<", "<=", ">", ">="}
_LOGIC_BIN = {"and", "or"}


def _is_bool_expr(e):
    """静态判断表达式是否产生布尔值（用于拒绝 print 布尔值的输出差异）。"""
    if isinstance(e, A.Bool):
        return True
    if isinstance(e, A.BinOp):
        return e.op in _CMP_BIN or e.op in _LOGIC_BIN
    if isinstance(e, A.UnaryOp) and e.op == "not":
        return True
    return False


def _is_block(node):
    return isinstance(node, A.Block)


def _stmts(node):
    if node is None:
        return []
    if _is_block(node):
        return node.stmts
    return [node]


def _collect_assigns(node, out):
    """收集一个作用域内所有被赋值的名字（不进入嵌套 Def/Class）。"""
    if node is None:
        return
    if isinstance(node, list):
        for s in node:
            _collect_assigns(s, out)
        return
    if isinstance(node, A.Assign):
        out.add(node.name)
    elif isinstance(node, A.CompoundAssign):
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
    elif isinstance(node, A.Def):
        # 嵌套函数不是快路径子集，但先收集外层赋值时不进入它。
        return
    elif isinstance(node, A.TryCatch):
        _collect_assigns(node.body, out)
        _collect_assigns(node.catch_body, out)
        _collect_assigns(node.finally_body, out)


def _c_string(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


class _FastCompiler:
    def __init__(self, ast):
        self.ast = ast
        self.funcs = {}
        self.globals = set()
        self.current_locals = None
        self.current_params = set()
        self.loop_depth = 0
        self.in_func = False

    # ---------- 校验与收集 ----------
    def prepare(self):
        root = _stmts(self.ast)
        for s in root:
            if isinstance(s, A.Def):
                if s.is_async:
                    raise FastUnsupported("async 函数不在原生数值快路径内", s.lineno, s.col)
                self.funcs[s.name] = s
            elif isinstance(s, A.ClassDef):
                raise FastUnsupported("class 不在原生数值快路径内", s.lineno, s.col)
            else:
                _collect_assigns(s, self.globals)
        # 函数体里被赋值的名字是局部变量；函数内嵌套 Def 直接拒绝。
        for fn in self.funcs.values():
            if len(fn.params) != len(set(fn.params)):
                raise FastUnsupported("函数参数重名", fn.lineno, fn.col)
            loc = set(fn.params)
            _collect_assigns(fn.body, loc)
            self._check_nested_defs(fn.body)
            for name in loc:
                if not name:
                    raise FastUnsupported("空变量名", fn.lineno, fn.col)
        self._validate_top(root)
        # 函数体也必须逐句校验；只靠生成阶段的异常会漏掉不可达分支。
        for fn in self.funcs.values():
            self.in_func = True
            self.current_locals = set(fn.params)
            _collect_assigns(fn.body, self.current_locals)
            self.current_params = set(fn.params)
            self.loop_depth = 0
            try:
                self._validate_stmt(fn.body)
            finally:
                self.in_func = False
                self.current_locals = set()
                self.current_params = set()
                self.loop_depth = 0

    def _check_nested_defs(self, node):
        if node is None:
            return
        if isinstance(node, list):
            for s in node:
                self._check_nested_defs(s)
            return
        if isinstance(node, A.Def):
            raise FastUnsupported("嵌套函数不在原生数值快路径内", node.lineno, node.col)
        if isinstance(node, A.ClassDef):
            raise FastUnsupported("类不在原生数值快路径内", node.lineno, node.col)
        if isinstance(node, A.Block):
            for s in node.stmts:
                self._check_nested_defs(s)
        elif isinstance(node, A.If):
            self._check_nested_defs(node.body)
            for _c, b in node.elseifs:
                self._check_nested_defs(b)
            self._check_nested_defs(node.elseb)
        elif isinstance(node, A.While):
            self._check_nested_defs(node.body)
        elif isinstance(node, A.TryCatch):
            self._check_nested_defs(node.body)
            self._check_nested_defs(node.catch_body)
            self._check_nested_defs(node.finally_body)

    def _validate_top(self, root):
        self.current_locals = set()
        self.in_func = False
        for s in root:
            if isinstance(s, A.Def):
                continue
            if isinstance(s, A.Return):
                raise FastUnsupported("顶层 return 不在原生数值快路径内", s.lineno, s.col)
            self._validate_stmt(s)

    def _validate_stmt(self, s):
        if s is None:
            return
        if isinstance(s, list):
            for x in s:
                self._validate_stmt(x)
            return
        if isinstance(s, A.Block):
            for x in s.stmts:
                self._validate_stmt(x)
        elif isinstance(s, A.Assign):
            self._check_var_name(s.name, s)
            self._validate_expr(s.expr)
        elif isinstance(s, A.CompoundAssign):
            if s.op not in ("+", "-", "*", "%"):
                raise FastUnsupported("复合赋值的运算符不在快路径内：%s" % s.op,
                                      s.lineno, s.col)
            self._check_var_name(s.name, s)
            self._validate_expr(s.expr)
        elif isinstance(s, A.Print):
            if len(s.items) != 1:
                raise FastUnsupported("print 只支持一个参数", s.lineno, s.col)
            if _is_bool_expr(s.items[0]):
                raise FastUnsupported("print 布尔值不在快路径内（输出 True/False 与 C 的 1/0 不一致）",
                                      s.lineno, s.col)
            self._validate_expr(s.items[0], allow_str=True)
        elif isinstance(s, A.Call):
            self._validate_expr(s)
        elif isinstance(s, A.ExprStmt):
            raise FastUnsupported("裸表达式语句不在原生数值快路径内", s.lineno, s.col)
        elif isinstance(s, A.If):
            self._validate_expr(s.cond, bool_ctx=True)
            self._validate_stmt(s.body)
            for c, b in s.elseifs:
                self._validate_expr(c, bool_ctx=True)
                self._validate_stmt(b)
            self._validate_stmt(s.elseb)
        elif isinstance(s, A.While):
            self._validate_expr(s.cond, bool_ctx=True)
            self.loop_depth += 1
            self._validate_stmt(s.body)
            self.loop_depth -= 1
        elif isinstance(s, A.Return):
            if not self.in_func:
                raise FastUnsupported("函数外 return", s.lineno, s.col)
            if s.expr is None:
                raise FastUnsupported("return 必须返回整数", s.lineno, s.col)
            self._validate_expr(s.expr)
        elif isinstance(s, A.Break):
            if self.loop_depth <= 0:
                raise FastUnsupported("循环外 break", s.lineno, s.col)
        elif isinstance(s, A.Continue):
            if self.loop_depth <= 0:
                raise FastUnsupported("循环外 continue", s.lineno, s.col)
        elif isinstance(s, A.Def):
            raise FastUnsupported("嵌套函数不在原生数值快路径内", s.lineno, s.col)
        elif isinstance(s, A.ClassDef):
            raise FastUnsupported("类不在原生数值快路径内", s.lineno, s.col)
        else:
            raise FastUnsupported("语句不在原生数值快路径内：%s" % type(s).__name__,
                                  getattr(s, "lineno", 0), getattr(s, "col", 0))

    def _check_var_name(self, name, node):
        if name in self.current_locals:
            return
        if name in self.globals:
            return
        raise FastUnsupported("变量未在快路径作用域内声明：%s" % name,
                              getattr(node, "lineno", 0), getattr(node, "col", 0))

    def _validate_expr(self, e, allow_str=False, bool_ctx=False):
        if e is None:
            raise FastUnsupported("空表达式")
        if isinstance(e, A.Num):
            if isinstance(e.val, bool) or not isinstance(e.val, int):
                raise FastUnsupported("原生数值快路径只支持整数，不支持浮点数",
                                      e.lineno, e.col)
        elif isinstance(e, A.Bool):
            if not bool_ctx:
                raise FastUnsupported("布尔字面量只能用于条件，不能作为整数存储或打印",
                                      e.lineno, e.col)
            return
        elif isinstance(e, A.Str):
            if not allow_str:
                raise FastUnsupported("字符串只能出现在 print 中", e.lineno, e.col)
        elif isinstance(e, A.Var):
            if e.name in self.current_locals or e.name in self.globals:
                return
            raise FastUnsupported("变量未在快路径作用域内声明：%s" % e.name,
                                  e.lineno, e.col)
        elif isinstance(e, A.BinOp):
            if e.op in _LOGIC_BIN:
                if not bool_ctx:
                    raise FastUnsupported(
                        "and/or 只能用于条件：Impor 返回操作数，C 的 &&/|| 只返回 1/0",
                        e.lineno, e.col)
                self._validate_expr(e.left, bool_ctx=True)
                self._validate_expr(e.right, bool_ctx=True)
            elif e.op in _CMP_BIN:
                if not bool_ctx:
                    raise FastUnsupported("比较只能用于条件，不能作为整数存储或打印",
                                          e.lineno, e.col)
                self._validate_expr(e.left)
                self._validate_expr(e.right)
            elif e.op in _INT_BIN:
                self._validate_expr(e.left)
                self._validate_expr(e.right)
            else:
                raise FastUnsupported("运算符不在原生数值快路径内：%s" % e.op,
                                      e.lineno, e.col)
        elif isinstance(e, A.UnaryOp):
            if e.op not in ("-", "+", "not"):
                raise FastUnsupported("一元运算符不在原生数值快路径内：%s" % e.op,
                                      e.lineno, e.col)
            if e.op == "not" and not bool_ctx:
                raise FastUnsupported("not 只能用于条件", e.lineno, e.col)
            self._validate_expr(e.child, bool_ctx=(e.op == "not"))
        elif isinstance(e, A.TernaryExpr):
            self._validate_expr(e.cond, bool_ctx=True)
            self._validate_expr(e.true_expr)
            self._validate_expr(e.false_expr)
        elif isinstance(e, A.Call):
            if not isinstance(e.callee, A.Var):
                raise FastUnsupported("只支持直接函数调用", e.lineno, e.col)
            name = e.callee.name
            if name == "print":
                if len(e.args) != 1:
                    raise FastUnsupported("print 只支持一个参数", e.lineno, e.col)
                if _is_bool_expr(e.args[0]):
                    raise FastUnsupported("print 布尔值不在快路径内（输出 True/False 与 C 的 1/0 不一致）",
                                          e.lineno, e.col)
                self._validate_expr(e.args[0], allow_str=True)
                return
            if name in ("abs", "min", "max"):
                need = 1 if name == "abs" else 2
                if len(e.args) != need:
                    raise FastUnsupported("%s 参数个数不对" % name, e.lineno, e.col)
                for a in e.args:
                    self._validate_expr(a)
                return
            fn = self.funcs.get(name)
            if fn is None:
                raise FastUnsupported("未知函数或内建不在快路径内：%s" % name,
                                      e.lineno, e.col)
            if len(e.args) != len(fn.params):
                raise FastUnsupported("函数参数个数不匹配：%s" % name, e.lineno, e.col)
            for a in e.args:
                self._validate_expr(a)
        else:
            raise FastUnsupported("表达式不在原生数值快路径内：%s" % type(e).__name__,
                                  getattr(e, "lineno", 0), getattr(e, "col", 0))

    # ---------- 名称与表达式 ----------
    def _var(self, name):
        if self.in_func and name in self.current_locals:
            return "v_" + name
        if name in self.globals:
            return "g_" + name
        if self.in_func and name in self._extra_globals:
            return "g_" + name
        raise FastUnsupported("变量不在当前作用域：%s" % name)

    def _expr(self, e):
        if isinstance(e, A.Num):
            return str(e.val)
        if isinstance(e, A.Bool):
            return "1" if e.val else "0"
        if isinstance(e, A.Str):
            return _c_string(e.val)
        if isinstance(e, A.Var):
            return self._var(e.name)
        if isinstance(e, A.BinOp):
            l, r = self._expr(e.left), self._expr(e.right)
            if e.op == "%":
                return "imod(%s, %s)" % (l, r)
            op = {"and": "&&", "or": "||"}.get(e.op, e.op)
            return "(%s %s %s)" % (l, op, r)
        if isinstance(e, A.UnaryOp):
            c = self._expr(e.child)
            if e.op == "not":
                return "(!%s)" % c
            return "(%s%s)" % (e.op, c)
        if isinstance(e, A.TernaryExpr):
            return "(%s ? %s : %s)" % (self._expr(e.cond),
                                       self._expr(e.true_expr),
                                       self._expr(e.false_expr))
        if isinstance(e, A.Call):
            if isinstance(e.callee, A.Var) and e.callee.name == "print":
                a = e.args[0]
                if isinstance(a, A.Str):
                    return 'printf("%%s\\n", %s)' % _c_string(a.val)
                return 'printf("%%lld\\n", (long long)(%s))' % self._expr(a)
            if isinstance(e.callee, A.Var) and e.callee.name == "abs":
                x = self._expr(e.args[0])
                return "((%s) < 0 ? -(%s) : (%s))" % (x, x, x)
            if isinstance(e.callee, A.Var) and e.callee.name in ("min", "max"):
                a, b = self._expr(e.args[0]), self._expr(e.args[1])
                op = "<" if e.callee.name == "min" else ">"
                return "((%s) %s (%s) ? (%s) : (%s))" % (a, op, b, a, b)
            return "f_%s(%s)" % (e.callee.name, ", ".join(self._expr(a) for a in e.args))
        raise FastUnsupported("无法生成表达式：%s" % type(e).__name__,
                              getattr(e, "lineno", 0), getattr(e, "col", 0))

    # ---------- 语句 ----------
    def _line(self, s):
        return "\n".join("    " + x if x else x for x in s.splitlines())

    def _stmt(self, s, indent):
        pad = " " * indent
        if s is None:
            return ""
        if isinstance(s, list):
            return "".join(self._stmt(x, indent) for x in s)
        if isinstance(s, A.Block):
            return "".join(self._stmt(x, indent) for x in s.stmts)
        if isinstance(s, A.Assign):
            name = ("g_" if (not self.in_func or s.scope == "global") else "v_") + s.name
            return pad + "%s = %s;\n" % (name, self._expr(s.expr))
        if isinstance(s, A.CompoundAssign):
            name = ("g_" if (not self.in_func or s.scope == "global") else "v_") + s.name
            return pad + "%s %s= %s;\n" % (name, s.op, self._expr(s.expr))
        if isinstance(s, A.Print):
            a = s.items[0]
            if isinstance(a, A.Str):
                return pad + 'printf("%%s\\n", %s);\n' % _c_string(a.val)
            return pad + 'printf("%%lld\\n", (long long)(%s));\n' % self._expr(a)
        if isinstance(s, A.Call):
            return pad + "(void)(%s);\n" % self._expr(s)
        if isinstance(s, A.If):
            out = pad + "if (%s) {\n" % self._expr(s.cond)
            out += self._stmt(s.body, indent + 4)
            out += pad + "}"
            for c, b in s.elseifs:
                out += " else if (%s) {\n" % self._expr(c)
                out += self._stmt(b, indent + 4)
                out += pad + "}"
            if s.elseb is not None:
                out += " else {\n"
                out += self._stmt(s.elseb, indent + 4)
                out += pad + "}"
            out += "\n"
            return out
        if isinstance(s, A.While):
            out = pad + "while (%s) {\n" % self._expr(s.cond)
            out += self._stmt(s.body, indent + 4)
            out += pad + "}\n"
            return out
        if isinstance(s, A.Return):
            return pad + "return (long long)(%s);\n" % self._expr(s.expr)
        if isinstance(s, A.Break):
            return pad + "break;\n"
        if isinstance(s, A.Continue):
            return pad + "continue;\n"
        raise FastUnsupported("无法生成语句：%s" % type(s).__name__,
                              getattr(s, "lineno", 0), getattr(s, "col", 0))

    def _function(self, fn):
        self.in_func = True
        self.current_locals = set(fn.params)
        _collect_assigns(fn.body, self.current_locals)
        self.current_params = set(fn.params)
        self._extra_globals = self.globals - self.current_locals
        decls = []
        for name in sorted(self.current_locals - set(fn.params)):
            decls.append("    long long v_%s = 0;" % name)
        body = self._stmt(fn.body, 4)
        self.in_func = False
        self.current_locals = set()
        return ("static long long f_%s(%s) {\n%s\n%s    return 0;\n}\n" %
                (fn.name,
                 ", ".join("long long v_" + p for p in fn.params),
                 "\n".join(decls),
                 body))

    def compile(self):
        self.prepare()
        parts = ["#include <stdio.h>", "#include <stdlib.h>", "#include <stdint.h>", "",
                 "/* Python/Impor 取模符号跟随除数；C 的 %% 跟随被除数，必须校正。 */",
                 "static long long imod(long long a, long long b) {",
                 "    if (b == 0) { fprintf(stderr, \"division by zero\\n\"); exit(1); }",
                 "    long long r = a % b;",
                 "    if (r != 0 && ((r < 0) != (b < 0))) r += b;",
                 "    return r;",
                 "}", ""]
        for fn in self.funcs.values():
            parts.append("static long long f_%s(%s);" %
                         (fn.name, ", ".join("long long" for _ in fn.params)))
        parts.append("")
        for name in sorted(self.globals):
            parts.append("static long long g_%s = 0;" % name)
        parts.append("")
        for fn in self.funcs.values():
            parts.append(self._function(fn))
        parts.append("int main(void) {")
        self.in_func = False
        self.current_locals = set()
        self._extra_globals = set()
        top = _stmts(self.ast)
        for s in top:
            if isinstance(s, A.Def):
                continue
            parts.append(self._stmt(s, 4).rstrip("\n"))
        parts.append("    return 0;")
        parts.append("}")
        return "\n".join(parts) + "\n"


def c_fast_transpile(ast):
    """把数值脚本 AST 转成纯 C；超出子集抛 FastUnsupported。"""
    return _FastCompiler(ast).compile()
