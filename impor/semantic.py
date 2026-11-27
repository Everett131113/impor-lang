# -*- coding: utf-8 -*-
"""4.7.0 渐进类型检查（semantic 层）。

背景：`impor/typecheck.py` 从 3.8.1 起就在 run/build/compile 入口做硬门禁，
覆盖「字面量赋值 / 返回值 / 实参类型 / 参数个数 / 可见性 / match 穷尽性」。
本模块补的是它**没有覆盖、且代价最低**的三类，并把新增诊断默认降为 WARN：

  1. `arith-type`   算术两侧类型不可能成立（`"a" - 1`、`[1] * 2`、`str % int`）。
                     这类表达式原来会传播成 `?` 静默放过，运行时由 E1012 兜底；
  2. `int-range`    整数字面量超出 int64（`2**63` 越界）。原来要到运行/编译期
                     才由 `rt_ck_*` 报 E1012，现在在解析后立刻能指出行号；
  3. `missing-return` 标注了返回类型、函数体里却完全没有 `return`（且不是以
                     `raise` 结尾的情形）。

分级原则（保证老脚本零破坏）：
  - typecheck.py 的既有诊断：severity="error"，行为完全不变；
  - 本模块新增诊断：severity="warn"；`--strict-types` 时才升为 "error"。
  - 只有「确定成立」才报，类型推不出（`?`/`any`/未知）一律跳过。

另外提供 `function_annotations(tree)`，给单态化按**标注**定型用（4.7.0 的
monomorph 升级点）：把 `def f(a: int) -> int` 变成可专用化的候选。
"""
from .ast_nodes import (Num, Str, Bool, BinOp, UnaryOp, Def, ClassDef, Raise,
                        Return, Block, Assign, For, Var, Call, Null, TypeTest)
from .typecheck import (TypeChecker, normalize_type, _iter_nodes, _lit_type)


INT64_MAX = 2 ** 63 - 1
INT64_MIN = -(2 ** 63)
ARITH = ("+", "-", "*", "/", "%")
NUMERIC = ("int", "float")
VOIDISH = ("", "?", "any", "void", "null", "none")


class SemDiag(object):
    """与 typecheck.TypeIssue 同构，便于 CLI 统一渲染。"""

    __slots__ = ("line", "col", "severity", "code", "msg", "fix", "why")

    def __init__(self, line, col, code, msg, severity="warn", why=None, fix=None):
        self.line = line
        self.col = col
        self.code = code
        self.msg = msg
        self.severity = severity
        self.why = why
        self.fix = fix


class SemanticChecker(TypeChecker):
    """在 typecheck 的遍历之上，补算术 / 范围 / 缺失 return 三类检查。"""

    def __init__(self, strict=False):
        TypeChecker.__init__(self, index=None)
        self.strict = strict
        self.diags = []
        self._skip_num = set()

    # ---- 入口 -------------------------------------------------------
    def run(self, source, path=""):
        self.diags = []
        # 先跑一遍父类的 check：它会填好 self._sigs / self._classes / self._generics，
        # 供下面的 _type_of 使用（它自己的诊断由 check() 负责收集，这里丢弃）。
        try:
            TypeChecker.check(self, source, path)
        except Exception:
            return self.diags
        try:
            from .parser import Parser
            from .lexer import tokenize
            from .interpreter import strip_header
            body = strip_header(source)
            tree = Parser(tokenize(body)).program()
        except Exception:
            return self.diags
        self._ret_ann = {}
        for fn in _functions(tree):
            ret = normalize_type(getattr(fn, "ret_type", "") or "")
            if ret and ret not in ("?", "any", "void"):
                self._ret_ann[fn.name] = ret
        self._scan_arithmetic(tree)
        self._scan_ranges(tree)
        self._scan_returns(tree)
        return self.diags

    # ---- 0) 只信「确定」的类型 ---------------------------------------
    def _strict_type(self, node, env):
        """只返回能确定下来的类型；推不出就返回 "?"。

        和父类 `_type_of` 的区别：父类为了给出更好的提示，会对无标注函数的返回值
        **猜**一个类型（例如按函数体 push 的顺序默认 int）。那套猜测用于「提示」可以，
        用于「报警告」就是假类型 —— 自举编译器 `imporc.impor` 里同名变量
        `ev` 在别的函数里是 int，本函数里是 str，照父类猜法会一次报 8 条假警告。
        所以这里只认三件东西：字面量、本函数作用域里带标注的变量、带返回标注的调用。
        """
        lit = _lit_type(node)
        if lit:
            return normalize_type(lit)
        if isinstance(node, Null):
            return "null"
        if isinstance(node, Var):
            return normalize_type(env.get(node.name, "")) if node.name in env else "?"
        if isinstance(node, UnaryOp):
            return self._strict_type(node.child, env)
        if isinstance(node, BinOp):
            lt = self._strict_type(node.left, env)
            rt = self._strict_type(node.right, env)
            if node.op == "+" and lt == rt and lt in ("str", "list"):
                return lt
            if lt in NUMERIC and rt in NUMERIC:
                return "float" if "float" in (lt, rt) else lt
            return "?"
        if isinstance(node, TypeTest):
            # 4.9.1：`x have <类型名>` 恒为 bool，右侧不是表达式，不参与类型推断
            return "bool"
        if isinstance(node, Call):
            name = getattr(node.callee, "name", "")
            return self._ret_ann.get(name, "?")
        return "?"

    # ---- 1) 算术两侧类型 --------------------------------------------
    def _scan_arithmetic(self, tree):
        for fn in _functions(tree):
            env = {}
            ptypes = [normalize_type(x) for x in (getattr(fn, "param_types", []) or [])]
            for i, p in enumerate(fn.params or []):
                if i < len(ptypes) and ptypes[i] not in ("", "?"):
                    env[p] = ptypes[i]
            self._walk_arith(fn.body, env)

    def _walk_arith(self, node, env):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                self._walk_arith(x, env)
            return
        if isinstance(node, Block):
            for st in node.stmts:
                self._walk_arith(st, env)
            return
        if isinstance(node, Def):
            self._walk_arith(node.body, dict(env))
            return
        if isinstance(node, ClassDef):
            self._walk_arith(node.body, dict(env))
            return
        if isinstance(node, BinOp) and node.op in ARITH:
            self._check_arith(node, env)
        # 赋值 / for 变量把类型带进作用域，让后面的算术能判（只认标注与字面量，
        # 不采信父类对无标注函数返回值的猜测——那会产出假警告）
        if isinstance(node, Assign):
            ann = normalize_type(getattr(node, "type_ann", ""))
            if ann and ann not in ("?", "any"):
                env[node.name] = ann
            elif node.name not in env:
                env[node.name] = self._strict_type(node.expr, env)
        if isinstance(node, For):
            env[node.var] = ""
        if isinstance(node, object) and hasattr(node, "__dict__"):
            for val in vars(node).values():
                if isinstance(val, (Block, list, tuple)) or \
                        (val is not None and hasattr(val, "__dict__")
                         and val.__class__.__module__.startswith("impor.ast_nodes")):
                    self._walk_arith(val, env)

    def _check_arith(self, node, env):
        lt = self._strict_type(node.left, env)
        rt = self._strict_type(node.right, env)
        if lt in ("", "?") or rt in ("", "?"):
            return
        if lt in ("any",) or rt in ("any",):
            return
        # 字符串拼接 / 列表拼接是合法的
        if node.op == "+" and lt == rt and lt in ("str", "list"):
            return
        if lt in NUMERIC and rt in NUMERIC:
            return
        # C ABI 的手柄/尺寸类型（size_t / handle / ptr）与整数混算在 lib 场景是常规写法
        if {lt, rt} & {"size_t", "handle", "ptr", "iq"}:
            return
        # bool 参与算术：保留（Python 语义里 bool 是 int，历史脚本依赖过）
        if lt == "bool" or rt == "bool":
            return
        if lt in ("func", "dict") and rt in ("func", "dict"):
            return
        line = getattr(node, "lineno", 0) or 0
        col = getattr(node, "col", 1) or 1
        sev = "error" if self.strict else "warn"
        self.diags.append(SemDiag(
            line, col, "arith-type",
            "算术 %r 两侧类型不匹配：左边 %s，右边 %s" % (node.op, lt, rt),
            severity=sev,
            why="Impor 的算术在运行期统一走 rt_ck_* 检查，类型不可能成立时会在"
                "执行到这一行才报 E1012；静态能判出来的，这里提前指出。",
            fix="把一侧转成所需类型（to_str / int / float），或去掉会造成混型的标注"))

    # ---- 2) int64 字面量范围 ----------------------------------------
    def _scan_ranges(self, tree):
        for node in _iter_nodes(tree):
            if isinstance(node, Num) and isinstance(node.val, int) \
                    and not isinstance(node.val, bool):
                if id(node) in self._skip_num:
                    continue
                if node.val > INT64_MAX or node.val < INT64_MIN:
                    self.diags.append(SemDiag(
                        getattr(node, "lineno", 0), getattr(node, "col", 1) or 1,
                        "int-range",
                        "整数字面量 %s 超出 int64 范围" % _short(node.val),
                        severity="error" if self.strict else "warn",
                        why="4.5.0 起 Impor 只有 int64 一档整数；超出即无法表示，"
                            "运行/编译期会报 E1012。",
                        fix="改用 float（会损失精度）或拆成两个数分别运算"))
            if isinstance(node, UnaryOp) and node.op in ("-", "+") \
                    and isinstance(node.child, Num) and isinstance(node.child.val, int):
                self._skip_num.add(id(node.child))
                if node.op == "-" and node.child.val > 2 ** 63:
                    self.diags.append(SemDiag(
                        getattr(node, "lineno", 0), getattr(node, "col", 1) or 1,
                        "int-range",
                        "负整数字面量 -%s 超出 int64 范围" % _short(node.child.val),
                        severity="error" if self.strict else "warn",
                        why="int64 的下界是 -9223372036854775808；再小无法表示。"))

    # ---- 3) 标注了返回类型却没有 return ------------------------------
    def _scan_returns(self, tree):
        for fn in _functions(tree):
            ret = normalize_type(getattr(fn, "ret_type", ""))
            if ret in VOIDISH:
                continue
            if _has_any(fn.body, (Return, Raise)):
                continue
            self.diags.append(SemDiag(
                getattr(fn, "lineno", 0), getattr(fn, "col", 1) or 1, "missing-return",
                "函数 %s 标注返回 %s，但函数体里没有任何 return" % (fn.name, ret),
                severity="error" if self.strict else "warn",
                why="标注是给编译期看的：既写了返回类型，就应该有可达的 return；"
                    "这里只报「完全没有 return」这一确定情形，分支覆盖不做流分析。",
                fix="补一个 return，或把返回标注去掉/改成 void"))


def _short(v):
    s = str(v)
    return s if len(s) <= 24 else s[:18] + "…"


def _functions(tree):
    """收本文件的全部 Def（含类方法）：用统一的 AST 遍历，兼容 Program/Block/list。"""
    return [n for n in _iter_nodes(tree) if isinstance(n, Def)]


def _has_any(node, kinds):
    for n in _iter_nodes(node):
        if isinstance(n, kinds):
            return True
    return False


def check(source, path="", strict=False):
    """返回诊断列表：typecheck 的既有诊断（error）+ 本模块的（warn/strict→error）。"""
    legacy = []
    try:
        legacy = TypeChecker().check(source, path)
    except Exception:
        legacy = []
    try:
        extra = SemanticChecker(strict).run(source, path)
    except Exception:
        extra = []
    return list(legacy) + list(extra)


def function_annotations(source_or_tree, is_source=False):
    """给单态化用：返回 {函数名: {"params": [类型...], "ret": 类型, "generic": bool}}。

    只收「所有参数都有标注且不是类型参数」的函数——这正是可以按标注做原生
    专用的候选；擦除版仍在，所以这份表只增不减。
    """
    tree = source_or_tree
    if is_source:
        from .parser import Parser
        from .lexer import tokenize
        from .interpreter import strip_header
        tree = Parser(tokenize(strip_header(source_or_tree))).program()
    table = {}
    for fn in _functions(tree):
        tparams = set(getattr(fn, "type_params", []) or [])
        raw = [normalize_type(x) for x in (getattr(fn, "param_types", []) or [])]
        ret = normalize_type(getattr(fn, "ret_type", ""))
        if not raw or len(raw) < len(fn.params or []):
            continue
        if any(not t or t in ("?", "any") or t in tparams for t in raw):
            continue
        if ret in ("", "?", "any", "void") or ret in tparams:
            continue
        table.setdefault(fn.name, {"params": raw, "ret": ret, "node": fn})
    return table
