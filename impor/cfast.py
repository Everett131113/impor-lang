# -*- coding: utf-8 -*-
"""v3.7.3 原生数值快路径（v4.1 起为 build 默认路径）。

只接受“纯整数、单返回值”的脚本子集，生成不装箱的 C 代码：
变量与参数都是 ``long long``，算术直接落到机器整数，不经过 ``Val*`` 堆分配。
任何超出子集的写法都抛 ``FastUnsupported``，由 CLI 回退到原有后端；
绝不静默改变语义。

v4.1 的两处变化：
1. ``impor build`` 默认先尝试这条路径（``--no-fast`` 可关闭），
   因此计算密集脚本默认就能拿到不装箱的速度。
2. 溢出不再静默回绕：``iadd/isub/imul/ineg`` 做 64 位边界检查，
   越界时报清楚的中文错误并以退出码 3 结束，提示改用 ``--no-fast``
   走 Impor 的任意精度整数语义。C 的 64 位整数终究不是 Impor 的大整数，
   这条检查保证“不会算出一个悄悄错了的结果”。
"""

from . import ast_nodes as A

AST_BASE = A.AST


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
    elif isinstance(node, A.For):
        # v4.1：for 变量 in range(N) 也进快路径；循环变量本身是这一层的变量。
        if node.var:
            out.add(node.var)
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


def _has_float(node):
    """脚本里是否出现浮点字面量（出现即整脚本走 double 数值域）。"""
    if node is None:
        return False
    if isinstance(node, list):
        return any(_has_float(x) for x in node)
    if isinstance(node, A.Num):
        return isinstance(node.val, float)
    if isinstance(node, A.Block):
        return any(_has_float(x) for x in node.stmts)
    if isinstance(node, A.If):
        if _has_float(node.cond):
            return True
        if _has_float(node.body):
            return True
        for c, b in node.elseifs:
            if _has_float(c) or _has_float(b):
                return True
        return _has_float(node.elseb)
    for attr in ("expr", "child", "left", "right", "cond", "true_expr", "false_expr",
                 "iter_expr", "body", "elseb", "catch_body", "finally_body"):
        v = getattr(node, attr, None)
        if v is not None and _has_float(v):
            return True
    args = getattr(node, "args", None) or []
    if any(_has_float(a) for a in args):
        return True
    items = getattr(node, "items", None) or []
    if any(_has_float(a) for a in items):
        return True
    stmts = getattr(node, "stmts", None) or []
    return any(_has_float(x) for x in stmts)


class _FastCompiler:
    def __init__(self, ast):
        self.ast = ast
        self.funcs = {}
        self.globals = set()
        self.current_locals = None
        self.current_params = set()
        self.loop_depth = 0
        self.in_func = False
        self.double = False   # v4.2：脚本里出现浮点字面量时，整脚本走 double 数值域

    # ---------- 校验与收集 ----------
    def _assign_map(self):
        """扫描整棵 AST，收集 变量 -> 赋给它的表达式列表。"""
        pairs = []

        def walk(node):
            if node is None:
                return
            # 只下钻 AST 节点与容器；字符串插值等节点里会存裸 str/int，
            # 原来直接 vars(裸值) 会抛 TypeError（impor build 内部错误）
            if not isinstance(node, (AST_BASE, list, tuple)):
                return
            if isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)
                return
            if isinstance(node, A.Assign):
                pairs.append((node.name, node.expr))
            elif isinstance(node, A.CompoundAssign):
                pairs.append((node.name, node.expr))
            for value in vars(node).values():
                if isinstance(value, AST_BASE) or isinstance(value, (list, tuple)):
                    walk(value)

        walk(self.ast)
        return pairs

    def _expr_ty(self, e):
        """静态数值类型：'int' / 'float'。只在 double 数值域下有意义。"""
        if isinstance(e, A.Num):
            return "float" if isinstance(e.val, float) else "int"
        if isinstance(e, A.Bool):
            return "int"
        if isinstance(e, A.Str):
            return "int"
        if isinstance(e, A.Var):
            return self.var_types.get(e.name, "int")
        if isinstance(e, A.BinOp):
            if e.op in _CMP_BIN or e.op in _LOGIC_BIN:
                return "int"
            if "float" in (self._expr_ty(e.left), self._expr_ty(e.right)):
                return "float"
            return "int"
        if isinstance(e, A.UnaryOp):
            return self._expr_ty(e.child)
        if isinstance(e, A.TernaryExpr):
            if "float" in (self._expr_ty(e.true_expr), self._expr_ty(e.false_expr)):
                return "float"
            return "int"
        if isinstance(e, A.Call):
            if isinstance(e.callee, A.Var) and e.callee.name == "print":
                return "int"
            if any(self._expr_ty(a) == "float" for a in e.args):
                return "float"
            return "int"
        return "int"

    def _infer_types(self):
        """v4.2：double 数值域下推断每个变量是 int 还是 float。

        规则很朴素：变量只要有一次被赋成浮点表达式就整变量按 double 处理。
        迭代到不动点，保证 `var a = 1` 之后 `set a = a + 0.5` 这类脚本也正确。
        """
        self.var_types = {}
        if not self.double:
            return
        for name in self.globals:
            self.var_types[name] = "int"
        pairs = self._assign_map()
        for _ in range(64):
            changed = False
            for name, expr in pairs:
                if expr is None:
                    continue
                if self._expr_ty(expr) == "float" and self.var_types.get(name) != "float":
                    self.var_types[name] = "float"
                    changed = True
            if not changed:
                break
        # 一个变量若在某些位置是整数、在另一些位置是浮点（动态类型允许，静态 C 无法同时表示），
        # 拒绝并把脚本交给完整后端——宁可慢一点，也不输出一个悄悄不同的结果。
        seen = {}
        for name, expr in pairs:
            if expr is None:
                continue
            seen.setdefault(name, set()).add(self._expr_ty(expr))
        for name, kinds in seen.items():
            if "int" in kinds and "float" in kinds:
                raise FastUnsupported(
                    "变量 %s 在不同位置既是整数又是浮点（动态类型），"
                    "数值快路径无法同时表示；请用 --no-fast" % name)

    def prepare(self):
        root = _stmts(self.ast)
        self.double = _has_float(root)
        for s in root:
            if isinstance(s, A.Def):
                if s.is_async:
                    raise FastUnsupported("async 函数不在原生数值快路径内", s.lineno, s.col)
                self.funcs[s.name] = s
            elif isinstance(s, A.ClassDef):
                raise FastUnsupported("class 不在原生数值快路径内", s.lineno, s.col)
            else:
                _collect_assigns(s, self.globals)
        if self.double and self.funcs:
            fn = list(self.funcs.values())[0]
            raise FastUnsupported(
                "浮点脚本在数值快路径内暂不支持自定义函数；"
                "请用 impor build 脚本.impor --no-fast（完整后端同样走优化编译器）",
                fn.lineno, fn.col)
        self._infer_types()
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
        elif isinstance(node, A.For):
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
            if len(s.items) < 1:
                raise FastUnsupported("print 至少需要一个参数", s.lineno, s.col)
            for it in s.items:
                if _is_bool_expr(it):
                    raise FastUnsupported("print 布尔值不在快路径内（输出 True/False 与 C 的 1/0 不一致）",
                                          s.lineno, s.col)
                self._validate_expr(it, allow_str=True, print_ctx=True)
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
        elif isinstance(s, A.For):
            it = s.iter_expr
            if not (isinstance(it, A.Call) and isinstance(it.callee, A.Var)
                    and it.callee.name == "range"):
                raise FastUnsupported("for 只支持 for 变量 in range(...)（原生数值快路径）",
                                      s.lineno, s.col)
            if not (1 <= len(it.args) <= 3):
                raise FastUnsupported("range 在数值快路径内只支持 1~3 个参数：range(N) / range(A, B) / range(A, B, STEP)",
                                      s.lineno, s.col)
            if not s.var:
                raise FastUnsupported("for 缺少循环变量", s.lineno, s.col)
            for a in it.args:
                self._validate_expr(a)
                if self.double and self._expr_ty(a) == "float":
                    raise FastUnsupported(
                        "range 的界与步长必须是整数（浮点循环边界请用 --no-fast）",
                        s.lineno, s.col)
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

    def _validate_expr(self, e, allow_str=False, bool_ctx=False, print_ctx=False):
        if e is None:
            raise FastUnsupported("空表达式")
        if isinstance(e, A.Num):
            if isinstance(e.val, bool):
                raise FastUnsupported("布尔字面量只能用于条件", e.lineno, e.col)
            if isinstance(e.val, float):
                if not self.double:
                    raise FastUnsupported("原生数值快路径只支持整数，不支持浮点数",
                                          e.lineno, e.col)
            elif not isinstance(e.val, int):
                raise FastUnsupported("不支持的数值字面量", e.lineno, e.col)
        elif isinstance(e, A.Bool):
            if not bool_ctx:
                raise FastUnsupported("布尔字面量只能用于条件，不能作为整数存储或打印",
                                      e.lineno, e.col)
            return
        elif isinstance(e, A.Str):
            if not (allow_str or print_ctx):
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
                if len(e.args) < 1:
                    raise FastUnsupported("print 至少需要一个参数", e.lineno, e.col)
                for a in e.args:
                    if _is_bool_expr(a):
                        raise FastUnsupported("print 布尔值不在快路径内（输出 True/False 与 C 的 1/0 不一致）",
                                              e.lineno, e.col)
                    self._validate_expr(a, print_ctx=True)
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

    def _num_c(self, v):
        """数值字面量：整数域直接写字面量，double 域统一提升为 double。"""
        if isinstance(v, float):
            rep = repr(v)
            if rep in ("inf", "-inf", "nan"):
                raise FastUnsupported("不支持 inf / nan 字面量")
            return "(%s)" % rep
        return "((double)%d)" % v if self.double else str(v)

    def _print_c(self, items):
        """print 的 C 生成：解释器用空格连接各参数，这里用同一个格式串一次输出。"""
        fmt = []
        args = []
        for it in items:
            if isinstance(it, A.Str):
                fmt.append("%s")
                args.append(_c_string(it.val))
            elif self.double and self._expr_ty(it) == "float":
                fmt.append("%s")
                args.append("impor_dstr(%s)" % self._expr(it))
            else:
                fmt.append("%lld")
                args.append("(long long)(%s)" % self._expr(it))
        return 'printf("%s\\n", %s)' % (" ".join(fmt), ", ".join(args))

    def _expr(self, e):
        if isinstance(e, A.Num):
            return self._num_c(e.val)
        if isinstance(e, A.Bool):
            return "1" if e.val else "0"
        if isinstance(e, A.Str):
            return _c_string(e.val)
        if isinstance(e, A.Var):
            return self._var(e.name)
        if isinstance(e, A.BinOp):
            l, r = self._expr(e.left), self._expr(e.right)
            is_float = self.double and "float" in (self._expr_ty(e.left), self._expr_ty(e.right))
            if e.op == "%":
                return ("impor_dmod(%s, %s)" if is_float else "imod(%s, %s)") % (l, r)
            helper = {"+": "iadd", "-": "isub", "*": "imul"}.get(e.op)
            if helper and not is_float:
                return "%s(%s, %s)" % (helper, l, r)
            op = {"and": "&&", "or": "||"}.get(e.op, e.op)
            return "(%s %s %s)" % (l, op, r)
        if isinstance(e, A.UnaryOp):
            c = self._expr(e.child)
            if e.op == "not":
                return "(!%s)" % c
            if e.op == "-":
                is_float = self.double and self._expr_ty(e.child) == "float"
                return "(-%s)" % c if is_float else "ineg(%s)" % c
            return "(%s%s)" % (e.op, c)
        if isinstance(e, A.TernaryExpr):
            return "(%s ? %s : %s)" % (self._expr(e.cond),
                                       self._expr(e.true_expr),
                                       self._expr(e.false_expr))
        if isinstance(e, A.Call):
            if isinstance(e.callee, A.Var) and e.callee.name == "print":
                return self._print_c(e.args)
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
            is_float = self.double and (self.var_types.get(s.name) == "float"
                                       or self._expr_ty(s.expr) == "float")
            if is_float:
                op = "fmod" if s.op == "%" else s.op
                if s.op == "%":
                    return pad + "%s = impor_dmod(%s, %s);\n" % (name, name, self._expr(s.expr))
                return pad + "%s = (%s %s %s);\n" % (name, name, op, self._expr(s.expr))
            helper = {"+": "iadd", "-": "isub", "*": "imul", "%": "imod"}[s.op]
            return pad + "%s = %s(%s, %s);\n" % (name, helper, name, self._expr(s.expr))
        if isinstance(s, A.Print):
            return pad + "(void)(%s);\n" % self._print_c(s.items)
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
        if isinstance(s, A.For):
            # v4.2：range(N) / range(A, B) / range(A, B, STEP) 全支持。
            # 三个界都在进入循环时求值一次（解释器先把 range 物化成列表，语义等价）；
            # 循环变量在循环体开始处赋值为当次的值，所以循环结束后它保留“最后一个值”，
            # 空循环则保持原值——这两点都与解释器一致。
            var = self._var(s.var)
            args = s.iter_expr.args
            if len(args) == 1:
                start_c, stop_c, step_c = "0", self._expr(args[0]), "1"
            elif len(args) == 2:
                start_c, stop_c, step_c = self._expr(args[0]), self._expr(args[1]), "1"
            else:
                start_c = self._expr(args[0])
                stop_c = self._expr(args[1])
                step_c = self._expr(args[2])
            out = pad + "{\n"
            out += pad + "    long long _impor_start = (%s);\n" % start_c
            out += pad + "    long long _impor_stop = (%s);\n" % stop_c
            out += pad + "    long long _impor_step = (%s);\n" % step_c
            out += pad + "    long long _impor_i = _impor_start;\n"
            out += pad + "    if (_impor_step == 0) { fprintf(stderr, \"ImporRuntimeError: range 的步长不能为 0。\\n\"); exit(3); }\n"
            out += pad + "    for (; (_impor_step > 0) ? (_impor_i < _impor_stop) : (_impor_i > _impor_stop);\n"
            out += pad + "           _impor_i = iadd(_impor_i, _impor_step)) {\n"
            out += pad + "        %s = _impor_i;\n" % var
            out += self._stmt(s.body, indent + 8)
            out += pad + "    }\n"
            out += pad + "}\n"
            return out
        if isinstance(s, A.Return):
            return pad + "return (%s)(%s);\n" % ("double" if self.double else "long long",
                                              self._expr(s.expr))
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

    def compile(self, entry="main"):
        self.prepare()
        parts = ["#include <stdio.h>", "#include <stdlib.h>", "#include <stdint.h>",
                 "#include <limits.h>", "#include <string.h>", "#include <math.h>", "",
                 "/* Python/Impor 取模符号跟随除数；C 的 %% 跟随被除数，必须校正。 */",
                 "static long long imod(long long a, long long b) {",
                 "    if (b == 0) { fprintf(stderr, \"除数为 0：数值快路径无法完成取模。\\n\"",
                 "                       \"  需要 Impor 的错误语义请用：impor build 脚本.impor --no-fast\\n\"); exit(3); }",
                 "    long long r = a % b;",
                 "    if (r != 0 && ((r < 0) != (b < 0))) r += b;",
                 "    return r;",
                 "}", "",
                 "/* v4.1：64 位整数溢出检查。C 的 long long 不是 Impor 的任意精度整数，",
                 "   所以宁可报错退出，也不静默回绕成一个错的结果。",
                 "   文案与 c_runtime.c / 解释器 --int64 三处逐字统一，便于比对。 */",
                 "static void iovf(const char *op) {",
                 "    fprintf(stderr, \"ImporRuntimeError: 整数溢出：超出 64 位有符号整数范围（%s）[E1012]\\n\"",
                 "                    \"  整数是 int64（数值快路径与 C 后端一致）；要任意精度请用：impor run 脚本.impor --big\\n\"",
                 "                    \"  交付前核对同一份源码：impor run 脚本.impor --int64\\n\", op);",
                 "    exit(3);",
                 "}",
                 "static long long iadd(long long a, long long b) {",
                 "    if ((b > 0 && a > LLONG_MAX - b) || (b < 0 && a < LLONG_MIN - b)) iovf(\"加法\");",
                 "    return a + b;",
                 "}",
                 "static long long isub(long long a, long long b) {",
                 "    if ((b < 0 && a > LLONG_MAX + b) || (b > 0 && a < LLONG_MIN + b)) iovf(\"减法\");",
                 "    return a - b;",
                 "}",
                 "static long long imul(long long a, long long b) {",
                 "    long long r;",
                 "    if (a == 0 || b == 0) return 0;",
                 "    if ((a == -1 && b == LLONG_MIN) || (b == -1 && a == LLONG_MIN)) iovf(\"乘法\");",
                 "    r = (long long)((unsigned long long)a * (unsigned long long)b);",
                 "    if (r / b != a) iovf(\"乘法\");",
                 "    return r;",
                 "}",
                 "static long long ineg(long long a) {",
                 "    if (a == LLONG_MIN) iovf(\"取负\");",
                 "    return -a;",
                 "}", "",
                 "/* v4.2：浮点的 %% 与 Python 一致（结果符号跟随除数）。 */",
                 "static double impor_dmod(double a, double b) {",
                 "    double r;",
                 "    if (b == 0.0) { fprintf(stderr, \"ImporRuntimeError: 浮点取模的除数为 0。\\n\"); exit(3); }",
                 "    r = fmod(a, b);",
                 "    if (r != 0.0 && ((r < 0.0) != (b < 0.0))) r += b;",
                 "    return r;",
                 "}", "",
                 "/* v4.2：按“最短且能往返”的规则格式化浮点，与 Python repr 对齐。",
                 "   1.5 → 1.5；1.0 → 1.0；1e16 → 1e+16；1e-5 → 1e-05；-10.0 → -10.0 */",
                 "static const char *impor_dstr(double v) {",
                 "    static char out[96];",
                 "    char tmp[96];",
                 "    char digits[40];",
                 "    const char *s;",
                 "    int p, i, ndig = 0, exp10 = 0, neg = 0, pos = 0;",
                 "    if (v != v) return \"nan\";",
                 "    if (v > 1.7976931348623157e308) return \"inf\";",
                 "    if (v < -1.7976931348623157e308) return \"-inf\";",
                 "    if (v == 0.0) return (1.0 / v < 0.0) ? \"-0.0\" : \"0.0\";",
                 "    for (p = 1; p <= 17; p++) {",
                 "        snprintf(tmp, sizeof(tmp), \"%.*e\", p - 1, v);",
                 "        if (strtod(tmp, NULL) == v) break;",
                 "    }",
                 "    s = tmp;",
                 "    if (*s == '-') { neg = 1; s++; }",
                 "    while (*s && *s != 'e' && *s != 'E') {",
                 "        if (*s >= '0' && *s <= '9') digits[ndig++] = *s;",
                 "        s++;",
                 "    }",
                 "    if (*s == 'e' || *s == 'E') exp10 = atoi(s + 1);",
                 "    while (ndig > 1 && digits[ndig - 1] == '0') ndig--;",
                 "    digits[ndig] = '\\0';",
                 "    if (neg) out[pos++] = '-';",
                 "    if (exp10 >= -4 && exp10 < 16) {",
                 "        /* 定点形式 */",
                 "        if (exp10 >= 0) {",
                 "            for (i = 0; i <= exp10; i++) out[pos++] = (i < ndig) ? digits[i] : '0';",
                 "            out[pos++] = '.';",
                 "            if (ndig > exp10 + 1) { for (i = exp10 + 1; i < ndig; i++) out[pos++] = digits[i]; }",
                 "            else out[pos++] = '0';",
                 "        } else {",
                 "            out[pos++] = '0';",
                 "            out[pos++] = '.';",
                 "            for (i = 0; i < -exp10 - 1; i++) out[pos++] = '0';",
                 "            for (i = 0; i < ndig; i++) out[pos++] = digits[i];",
                 "        }",
                 "    } else {",
                 "        /* 指数形式：d[.ddd]e±XX（指数至少两位，与 Python 一致） */",
                 "        out[pos++] = digits[0];",
                 "        if (ndig > 1) {",
                 "            out[pos++] = '.';",
                 "            for (i = 1; i < ndig; i++) out[pos++] = digits[i];",
                 "        }",
                 "        out[pos++] = 'e';",
                 "        out[pos++] = (exp10 < 0) ? '-' : '+';",
                 "        i = (exp10 < 0) ? -exp10 : exp10;",
                 "        if (i < 10) { out[pos++] = '0'; out[pos++] = (char)('0' + i); }",
                 "        else if (i < 100) { out[pos++] = (char)('0' + i / 10); out[pos++] = (char)('0' + i % 10); }",
                 "        else { out[pos++] = (char)('0' + i / 100); out[pos++] = (char)('0' + (i / 10) % 10); out[pos++] = (char)('0' + i % 10); }",
                 "    }",
                 "    out[pos] = '\\0';",
                 "    return out;",
                 "}", ""]
        for fn in self.funcs.values():
            parts.append("static long long f_%s(%s);" %
                         (fn.name, ", ".join("long long" for _ in fn.params)))
        parts.append("")
        for name in sorted(self.globals):
            ty = "double" if self.var_types.get(name) == "float" else "long long"
            parts.append("static %s g_%s = 0;" % (ty, name))
        parts.append("")
        for fn in self.funcs.values():
            parts.append(self._function(fn))
        if entry == "main":
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
        else:
            # DLL 入口：宿主 LoadLibrary 后直接调用 impor_entry。
            # 返回前 fflush，保证宿主进程退出前 stdout 已落地（不经 CRT 退出流程）。
            #
            # patch8：入口多收一个 out_path。宿主（`impor serve`）在进程内调用时把输出
            # 收进文件——DLL 与宿主可能连着不同的 CRT，宿主的 fd 重定向（dup2）在
            # DLL 那边不一定生效，实测会出现“输出晚一次调用才落地”。`freopen`
            # 是在 DLL 自己的 CRT 里改自己的 stdout，确定可靠；传 NULL 则照旧写控制台。
            parts.append("#ifdef _WIN32")
            parts.append("__declspec(dllexport)")
            parts.append("#endif")
            parts.append("int %s(const char *out_path) {" % entry)
            parts.append("    if (out_path && out_path[0]) freopen(out_path, \"w\", stdout);")
            self.in_func = False
            self.current_locals = set()
            self._extra_globals = set()
            top = _stmts(self.ast)
            for s in top:
                if isinstance(s, A.Def):
                    continue
                parts.append(self._stmt(s, 4).rstrip("\n"))
            parts.append("    fflush(stdout);")
            parts.append("    return 0;")
            parts.append("}")
        return "\n".join(parts) + "\n"


def c_fast_transpile(ast, entry="main"):
    """把数值脚本 AST 转成纯 C；超出子集抛 FastUnsupported。

    entry="main" 产出可独立运行的 exe；entry 换成其它名字时产出“宿主可加载”的
    DLL 入口（`__declspec(dllexport)` + 返回前 fflush），供 impor-fast.exe 在
    自己进程里 LoadLibrary 调用——省掉第二次进程创建（patch7）。
    """
    return _FastCompiler(ast).compile(entry=entry)

