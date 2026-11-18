# -*- coding: utf-8 -*-
"""Impor (I++) 跨文件轻量类型检查。

不追求完整类型系统，只做可解释的传播：字面量、变量、函数参数标注、
跨文件函数签名。输出可被 Quick Fix 直接消费的修复建议。
"""
from .ast_nodes import (Num, Str, Bool, Null, ListLit, DictLit, Var, BinOp, UnaryOp,
                        Call, Assign, Def, ClassDef, Block, If, While, For,
                        TryCatch, MatchNode, Return, NewInstance, Lambda, BindingPattern, AdtPattern,
                        AttrGet, AttrSet, IndexGet, Slice, Pipe, AST)
from .parser import Parser
from .lexer import tokenize
from .interpreter import strip_header

_TYPE_WORDS = {"int", "float", "str", "bool", "list", "dict", "func", "any", "?"}
_TYPE_ALIASES = {
    "整数": "int", "浮点": "float", "小数": "float", "字符串": "str", "文本": "str",
    "布尔": "bool", "列表": "list", "数组": "list", "字典": "dict", "函数": "func",
    "任意": "any", "空": "null",
}



class TypeIssue:
    __slots__ = ("line", "col", "severity", "code", "msg", "fix")

    def __init__(self, line, col, code, msg, fix=None):
        self.line = line
        self.col = col
        self.severity = "error"
        self.code = code
        self.msg = msg
        self.fix = fix  # (line, new_line_text) 或 None


def _literal_for(typ):
    return {"int": "0", "float": "0.0", "str": '""', "bool": "false",
            "list": "[]", "dict": "{}"}.get(typ)


def _lit_type(node):
    if isinstance(node, Num):
        return "float" if isinstance(node.val, float) else "int"
    if isinstance(node, Str):
        return "str"
    if isinstance(node, Bool):
        return "bool"
    if isinstance(node, ListLit):
        return "list"
    if isinstance(node, DictLit):
        return "dict"
    return ""


def _param_types(sym):
    """从 param 符号文本里读 `a: int` / `a int`，无标注返回 ?。"""
    text = (sym.text or "").strip()
    for sep in (":", " "):
        if sep in text:
            tail = text.split(sep, 1)[1].strip().split("=")[0].strip()
            if tail in _TYPE_WORDS:
                return tail
    return "?"


def normalize_type(text):
    """规范化类型文本：中文别名、空白、nullable。"""
    text = "".join(str(text or "").split())
    if not text:
        return ""
    if text in _TYPE_ALIASES:
        return _TYPE_ALIASES[text]
    # list<int> / List<Int> 这类泛型：只保留外层类型做轻量检查
    if "<" in text and text.endswith(">"):
        outer = text.split("<", 1)[0]
        return normalize_type(outer) or "?"
    if text.endswith("?"):
        base = normalize_type(text[:-1])
        return (base + "?") if base else "?"
    low = text.lower()
    if low in ("none", "nil", "null"):
        return "null"
    if low in _TYPE_WORDS:
        return low
    return text


def _compatible(expected, actual):
    """返回 True 表示静态检查可以接受；?/any/空类型不拦截。"""
    expected = normalize_type(expected)
    actual = normalize_type(actual)
    if not expected or not actual:
        return True
    if expected in ("?", "any") or actual in ("?", "any"):
        return True
    if expected.endswith("?"):
        if actual in ("null", "?"):
            return True
        expected = expected[:-1]
    if actual == "null":
        return False
    if expected == actual:
        return True
    # 数值提升 / 泛型容器宽松匹配（Impor 的 int/float 同属数值，允许双向赋值）
    if expected in ("float", "int") and actual in ("float", "int"):
        return True
    if expected in ("list", "dict", "func") and actual == expected:
        return True
    if expected == "str" and actual == "str":
        return True
    return False


def _hint_for(expected):
    return "把值改成 %s 类型，或去掉/放宽左侧类型标注" % expected



class TypeChecker:
    def __init__(self, index=None):
        self.index = index
        self.issues = []
        self._src_lines = []
        self._sigs = {}
        self._ret_stack = []
        self._enums = {}
        self._enum_var_types = {}
        self._classes = {}
        self._class_stack = []

    def check(self, source, path=""):
        self.issues = []
        self._sigs = {}
        self._ret_stack = []
        self._enums = {}
        self._enum_var_types = {}
        self._classes = {}
        self._class_stack = []
        try:
            body = strip_header(source)
            tree = Parser(tokenize(body)).program()
        except Exception:
            return self.issues
        self._src_lines = body.split("\n")
        self._collect(tree)
        env = {}
        if self.index:
            pre = {}
            for s in sorted(self.index.symbols(path), key=lambda x: x.line):
                if s.kind in ("var", "param", "for") and s.typ not in ("", "?"):
                    if s.name in pre:
                        continue
                    pre[s.name] = normalize_type(s.typ)
            env = {k: v for k, v in pre.items() if v}
        self._walk(tree, env, {})
        return self.issues

    def _collect(self, node):
        """收集本文件函数签名与 enum 成员（跨文件仍交给 ProjectIndex）。"""
        if isinstance(node, (list, tuple)):
            for x in node:
                self._collect(x)
            return
        if isinstance(node, Block):
            for st in node.stmts:
                self._collect(st)
            return
        if isinstance(node, Def):
            ptypes = [normalize_type(x) for x in (getattr(node, "param_types", []) or [])]
            if len(ptypes) < len(node.params or []):
                ptypes += [""] * (len(node.params or []) - len(ptypes))
            self._sigs.setdefault(node.name, {
                "params": ptypes,
                "ret": normalize_type(getattr(node, "ret_type", "")),
            })
            self._collect(node.body)
            return
        if isinstance(node, ClassDef):
            info = self._classes.setdefault(node.cls_name, {
                "private": set(), "protected": set(), "base": normalize_type(node.base_name),
                "fields": {}, "methods": {}})
            for st in node.body:
                if isinstance(st, Def):
                    vis = getattr(st, "visibility", "")
                    if vis == "private":
                        info["private"].add(st.name)
                    elif vis == "protected":
                        info["protected"].add(st.name)
                    ptypes = [normalize_type(x) for x in (getattr(st, "param_types", []) or [])]
                    info["methods"][st.name] = {
                        "ret": normalize_type(getattr(st, "ret_type", "")),
                        "params": ptypes,
                    }
                    for inner in _iter_nodes(st.body):
                        if isinstance(inner, AttrSet):
                            obj = getattr(inner, "obj_expr", None)
                            if isinstance(obj, Var) and obj.name == "self":
                                local = {}
                                for i, p in enumerate(st.params or []):
                                    if i < len(ptypes) and ptypes[i]:
                                        local[p] = ptypes[i]
                                info["fields"][inner.attr_name] = self._type_of(inner.value, local)
                elif isinstance(st, Assign):
                    vis = getattr(st, "visibility", "")
                    if vis == "private":
                        info["private"].add(st.name)
                    elif vis == "protected":
                        info["protected"].add(st.name)
                    ann = normalize_type(getattr(st, "type_ann", ""))
                    info["fields"][st.name] = ann or self._type_of(st.expr, {})
                elif isinstance(st, AttrSet):
                    obj = getattr(st, "obj_expr", None)
                    if isinstance(obj, Var) and obj.name == "self":
                        info["fields"][st.attr_name] = self._type_of(st.value, {})
            self._collect(node.body)
            return
        if isinstance(node, Assign) and getattr(node, "is_enum", False):
            et = getattr(node, "enum_type", "")
            self._enums.setdefault(et, []).append(getattr(node, "enum_member", ""))
            self._enum_var_types[node.name] = et
        if isinstance(node, Def) and getattr(node, "is_enum", False):
            et = getattr(node, "enum_type", "")
            self._enums.setdefault(et, []).append(getattr(node, "enum_member", ""))
            self._enum_var_types[node.name] = et
        if not isinstance(node, object) or not hasattr(node, "__dict__"):
            return
        if not hasattr(node, "__dict__"):
            return
        for val in vars(node).values():
            if isinstance(val, Block):
                self._collect(val)
            elif isinstance(val, (list, tuple)):
                for x in val:
                    if isinstance(x, Block):
                        self._collect(x)
                    elif isinstance(x, (list, tuple)):
                        for y in x:
                            if isinstance(y, Block):
                                self._collect(y)

    def _type_of(self, node, env):
        lit = _lit_type(node)
        if lit:
            return lit
        if isinstance(node, Null):
            return "null"
        if isinstance(node, Var):
            if node.name in self._enum_var_types:
                return self._enum_var_types[node.name]
            return normalize_type(env.get(node.name)) or "?"
        if isinstance(node, UnaryOp):
            return self._type_of(node.child, env)
        if isinstance(node, BinOp):
            lt = self._type_of(node.left, env)
            rt = self._type_of(node.right, env)
            if node.op in ("+",) and (lt == "str" or rt == "str"):
                return "str"
            if "float" in (lt, rt):
                return "float"
            if lt == rt and lt in ("int", "str", "list"):
                return lt
            return "?"
        if isinstance(node, Call):
            if isinstance(node.callee, AttrGet):
                base = self._type_of(node.callee.obj_expr, env)
                info = self._classes.get(base) or {}
                meth = (info.get("methods") or {}).get(node.callee.attr_name)
                if meth:
                    return meth.get("ret") or "?"
            name = getattr(node.callee, "name", "")
            sig = self._signature(name)
            return (sig or {}).get("ret", "?")
        if isinstance(node, NewInstance):
            return normalize_type(node.cls_name)
        if isinstance(node, Lambda):
            return "func"
        if isinstance(node, Slice):
            return self._type_of(node.container, env)
        if isinstance(node, IndexGet):
            base = self._type_of(node.container, env)
            return "str" if base == "str" else "?"
        if isinstance(node, AttrGet):
            base = self._type_of(node.obj_expr, env)
            info = self._classes.get(base) or {}
            if node.attr_name in (info.get("fields") or {}):
                return info["fields"][node.attr_name] or "?"
            if node.attr_name in (info.get("methods") or {}):
                return "func"
            return "?"
        return "?"

    def member_type(self, base_type, attr):
        info = self._classes.get(normalize_type(base_type)) or {}
        if attr in (info.get("fields") or {}):
            return info["fields"][attr] or "?"
        if attr in (info.get("methods") or {}):
            return (info["methods"][attr] or {}).get("ret") or "func"
        return ""

    def class_members(self, base_type):
        info = self._classes.get(normalize_type(base_type)) or {}
        return sorted(list((info.get("fields") or {}).keys()) +
                      list((info.get("methods") or {}).keys()))

    def _signature(self, name):
        local = self._sigs.get(name)
        if local:
            return local
        if not self.index:
            return None
        for sym in self.index.definition(name):
            if sym.kind != "func":
                continue
            scope = (sym.scope + "." + sym.name) if not sym.scope.endswith(sym.name) else sym.scope
            params = [p for p in self.index.symbols()
                      if p.kind == "param" and p.scope == scope]
            return {"params": [_param_types(p) for p in params], "ret": "?"}
        return None

    def _walk(self, node, env, seen):
        if node is None:
            return
        if isinstance(node, (list, tuple)):
            for x in node:
                self._walk(x, env, seen)
            return
        if id(node) in seen:
            return
        seen[id(node)] = True
        if isinstance(node, Block):
            for st in node.stmts:
                self._walk(st, env, seen)
            return
        if isinstance(node, Def):
            ptypes = [normalize_type(x) for x in (getattr(node, "param_types", []) or [])]
            if len(ptypes) < len(node.params or []):
                ptypes += [""] * (len(node.params or []) - len(ptypes))
            params = []
            for i, p in enumerate(node.params or []):
                typ = ptypes[i] if i < len(ptypes) else ""
                if not typ and self.index:
                    for s in self.index.definition(p):
                        if s.kind == "param" and s.scope.endswith(node.name):
                            typ = normalize_type(_param_types(s))
                            break
                params.append((p, typ))
            child = {p: t for p, t in params if t}
            self._ret_stack.append(normalize_type(getattr(node, "ret_type", "")))
            try:
                self._walk(node.body, child, seen)
            finally:
                self._ret_stack.pop()
            return
        if isinstance(node, ClassDef):
            self._class_stack.append(node.cls_name)
            try:
                self._walk(node.body, dict(env), seen)
            finally:
                self._class_stack.pop()
            return
        if isinstance(node, If):
            self._walk(node.body, dict(env), seen)
            for _, b in node.elseifs or []:
                self._walk(b, dict(env), seen)
            if node.elseb:
                self._walk(node.elseb, dict(env), seen)
            return
        if isinstance(node, While):
            self._walk(node.body, dict(env), seen)
            return
        if isinstance(node, For):
            env[node.var] = ""
            self._walk(node.body, env, seen)
            return
        if isinstance(node, TryCatch):
            for b in (node.body, node.catch_body, node.finally_body):
                if b:
                    self._walk(b, dict(env), seen)
            return
        if isinstance(node, (AttrGet, AttrSet)):
            self._check_visibility(node, env)
            self._walk(node.obj_expr, env, seen)
            if isinstance(node, AttrSet):
                self._walk(node.value, env, seen)
            return
        if isinstance(node, MatchNode):
            self._check_match(node, env)
            for _, b in node.cases or []:
                self._walk(b, dict(env), seen)
            if node.elseb:
                self._walk(node.elseb, dict(env), seen)
            return
        if isinstance(node, Assign):
            if getattr(node, "is_enum", False):
                env[node.name] = getattr(node, "enum_type", "") or "int"
                return
            actual = self._type_of(node.expr, env)
            annotated = normalize_type(getattr(node, "type_ann", ""))
            expected = annotated or normalize_type(env.get(node.name, ""))
            if expected and actual and actual != "?" and not _compatible(expected, actual):
                if not (isinstance(node.expr, Null) and not getattr(node, "has_init", True)):
                    fix = self._literal_fix(node, expected)
                    self.issues.append(TypeIssue(
                        node.lineno, node.col or 1, "type-mismatch",
                        "变量 %s 是 %s，不能赋 %s 值" % (node.name, expected, actual), fix))
            if annotated:
                env[node.name] = annotated
            else:
                env[node.name] = actual if actual != "?" else expected
            self._walk(node.expr, env, seen)
            return
        if isinstance(node, Pipe):
            # x |> f(a) 等价于 f(x, a)：右侧调用实际多一个隐式参数，不能按普通调用计参数。
            self._walk(node.left, env, seen)
            if isinstance(node.right, Call):
                self._check_call(node.right, env, extra_args=1)
                self._walk(node.right.callee, env, seen)
                for a in node.right.args or []:
                    self._walk(a, env, seen)
            elif isinstance(node.right, Var):
                sig = self._signature(node.right.name)
                if sig and sig["params"] and len(sig["params"]) != 1:
                    self.issues.append(TypeIssue(
                        node.lineno, node.col or 1, "arg-count",
                        "%s 经管道需要 1 个参数，但声明了 %d 个" % (
                            node.right.name, len(sig["params"]))))
            else:
                self._walk(node.right, env, seen)
            return
        if isinstance(node, Call):
            self._check_call(node, env)
            self._walk(node.callee, env, seen)
            for a in node.args or []:
                self._walk(a, env, seen)
            return
        if isinstance(node, Return):
            ret = self._ret_stack[-1] if self._ret_stack else ""
            if node.expr:
                actual = self._type_of(node.expr, env)
                if ret and actual and actual != "?" and not _compatible(ret, actual):
                    self.issues.append(TypeIssue(
                        node.lineno, node.col or 1, "return-type",
                        "函数声明返回 %s，实际返回 %s" % (ret, actual)))
                self._walk(node.expr, env, seen)
            elif ret and ret not in ("?", "any"):
                self.issues.append(TypeIssue(
                    node.lineno, node.col or 1, "return-type",
                    "函数声明返回 %s，但 return 没有返回值" % ret))
            return
        if not hasattr(node, "__dict__"):
            return
        for val in vars(node).values():
            if isinstance(val, Block):
                self._walk(val, env, seen)
            elif isinstance(val, (list, tuple)):
                for x in val:
                    if isinstance(x, (Block, list, tuple)):
                        self._walk(x, env, seen)
                    elif x is not None and hasattr(x, "__dict__") and \
                            x.__class__.__module__.startswith("impor.ast_nodes"):
                        self._walk(x, env, seen)
            elif val is not None and hasattr(val, "__dict__") and \
                    val.__class__.__module__.startswith("impor.ast_nodes"):
                self._walk(val, env, seen)

    def _check_call(self, node, env, extra_args=0):
        name = getattr(node.callee, "name", "")
        sig = self._signature(name)
        if not sig:
            return
        params = sig["params"]
        if params and len(node.args) + extra_args != len(params):
            self.issues.append(TypeIssue(
                node.lineno, node.col or 1, "arg-count",
                "%s 需要 %d 个参数，实际 %d 个" % (
                    name, len(params), len(node.args) + extra_args)))
        for i, arg in enumerate(node.args):
            idx = i + extra_args
            if idx >= len(params):
                break
            expected = normalize_type(params[idx])
            actual = self._type_of(arg, env)
            if _compatible(expected, actual):
                continue
            ln = getattr(arg, "lineno", node.lineno)
            col = getattr(arg, "col", node.col or 1)
            fix = self._node_fix(arg, expected, ln)
            self.issues.append(TypeIssue(
                ln, col, "type-mismatch",
                "%s 参数 %d 需要 %s，实际 %s" % (name, i + 1, expected, actual), fix))

    def _owner_class(self, cls_name, attr):
        cur = cls_name
        seen = set()
        while cur and cur not in seen:
            seen.add(cur)
            info = self._classes.get(cur)
            if not info:
                return None
            if attr in info["private"] or attr in info["protected"]:
                return cur
            cur = info.get("base") or ""
        return None

    def _is_sub(self, child, base):
        cur = child
        seen = set()
        while cur and cur not in seen:
            if cur == base:
                return True
            seen.add(cur)
            cur = (self._classes.get(cur) or {}).get("base") or ""
        return False

    def _check_visibility(self, node, env):
        """同文件静态访问控制：private 仅类内，protected 仅类内或子类。"""
        obj = getattr(node, "obj_expr", None)
        if not isinstance(obj, Var):
            return
        cur = self._class_stack[-1] if self._class_stack else ""
        if obj.name in ("self", "this"):
            obj_cls = cur
        else:
            obj_cls = normalize_type(env.get(obj.name))
        owner = self._owner_class(obj_cls, node.attr_name)
        if not owner:
            return
        info = self._classes.get(owner) or {}
        if node.attr_name in info.get("private", ()):
            if cur != owner:
                self.issues.append(TypeIssue(
                    node.lineno, node.col or 1, "access-private",
                    "%s 是 %s 的 private 成员，只能在类内部访问" % (node.attr_name, owner)))
        elif node.attr_name in info.get("protected", ()):
            if not cur or not self._is_sub(cur, owner):
                self.issues.append(TypeIssue(
                    node.lineno, node.col or 1, "access-protected",
                    "%s 是 %s 的 protected 成员，只能在本类或子类中访问" % (node.attr_name, owner)))

    def _check_match(self, node, env):
        """枚举 match 穷尽性：没有 else、也不是绑定/通配时，列出缺失成员。"""
        if node.elseb is not None:
            return
        subject = node.subject
        enum_name = getattr(node, "enum_name", None)
        if not enum_name and isinstance(subject, Call):
            callee = getattr(subject, "callee", None)
            if isinstance(callee, Var) and callee.name.startswith("__adt_"):
                enum_name = callee.name[len("__adt_"):].rsplit("_", 1)[0]
        if not enum_name and isinstance(subject, Var):
            typ = normalize_type(env.get(subject.name, ""))
            enum_name = typ[:-1] if typ.endswith("?") else typ
        members = self._enums.get(enum_name or "") or []
        if not members:
            return
        covered = set()
        covered_tags = set()
        irrefutable = False
        for pat, _body in node.cases or []:
            if isinstance(pat, BindingPattern):
                irrefutable = True
                break
            if isinstance(pat, AdtPattern):
                covered_tags.add(pat.tag)
            if isinstance(pat, Var):
                covered.add(pat.name)
        if irrefutable:
            return
        missing = []
        for m in members:
            if "__enum_%s_%s" % (enum_name, m) in covered:
                continue
            if "%s.%s" % (enum_name, m) in covered_tags:
                continue
            missing.append(m)
        if missing:
            self.issues.append(TypeIssue(
                node.lineno, node.col or 1, "match-exhaustive",
                "match 缺少枚举 %s 的分支：%s（补 case 或 else）" % (
                    enum_name, ", ".join(missing))))


    def _literal_fix(self, assign_node, expected):
        expr = assign_node.expr
        ln = assign_node.lineno
        if ln <= 0 or ln > len(self._src_lines):
            return None
        line = self._src_lines[ln - 1]
        rhs = _lit_type(expr)
        if not rhs or rhs == expected:
            return None
        repl = _literal_for(expected)
        if repl is None:
            return None
        # 只替换赋值号之后的第一个字面量
        eq = line.find("=")
        if eq < 0:
            return None
        tail = line[eq + 1:]
        stripped = tail.strip()
        if stripped.startswith(('"', "'")):
            quote = stripped[0]
            end = stripped.find(quote, 1)
            lit_len = end + 1 if end >= 0 else len(stripped)
        else:
            m = __import__("re").match(r"-?\d+(?:\.\d+)?", stripped)
            lit_len = len(m.group(0)) if m else 0
        if not lit_len:
            return None
        start = len(line) - len(tail) + (len(tail) - len(tail.lstrip()))
        new_line = line[:start] + repl + line[start + lit_len:]
        return (ln - 1, new_line)

    def _node_fix(self, node, expected, ln):
        if ln <= 0 or ln > len(self._src_lines):
            return None
        actual = _lit_type(node)
        if not actual or actual == expected:
            return None
        repl = _literal_for(expected)
        if repl is None:
            return None
        line = self._src_lines[ln - 1]
        col = getattr(node, "col", 0)
        if col <= 0:
            return None
        text = line[col - 1:]
        start = col - 1
        if text.startswith(('"', "'")):
            quote = text[0]
            end = text.find(quote, 1)
            lit_len = end + 1 if end >= 0 else 0
        else:
            m = __import__("re").match(r"-?\d+(?:\.\d+)?", text)
            lit_len = len(m.group(0)) if m else 0
        if not lit_len:
            return None
        return (ln - 1, line[:start] + repl + line[start + lit_len:])


def check_source(source, index=None, path=""):
    return TypeChecker(index).check(source, path)


def _iter_nodes(node, seen=None):
    seen = seen if seen is not None else set()
    if node is None or id(node) in seen:
        return
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from _iter_nodes(x, seen)
        return
    if isinstance(node, AST) or hasattr(node, "__dict__"):
        seen.add(id(node))
        yield node
        for val in vars(node).values():
            if isinstance(val, (list, tuple)) or isinstance(val, AST):
                yield from _iter_nodes(val, seen)


def _node_at(tree, line, col):
    """找覆盖 (line, col) 的最内层表达式节点；列按 1 基处理。"""
    best, best_key = None, None
    for n in _iter_nodes(tree):
        ln = getattr(n, "lineno", 0)
        cl = getattr(n, "col", 0)
        if not ln or ln != line:
            continue
        if cl and cl > col + 1:
            continue
        name = type(n).__name__
        if name in ("Block", "Program", "Def", "ClassDef", "Return", "Assign", "AttrSet"):
            continue
        key = (cl or 0, 1 if name in ("AttrGet", "Call", "Var", "NewInstance") else 0)
        if best_key is None or key >= best_key:
            best, best_key = n, key
    return best


def _env_before(checker, tree, line):
    env = {}
    for n in _iter_nodes(tree):
        ln = getattr(n, "lineno", 0)
        if not ln or ln > line:
            continue
        if isinstance(n, Assign):
            ann = normalize_type(getattr(n, "type_ann", ""))
            env[n.name] = ann or checker._type_of(n.expr, env)
        elif isinstance(n, Def):
            ptypes = [normalize_type(x) for x in (getattr(n, "param_types", []) or [])]
            for i, p in enumerate(n.params or []):
                if i < len(ptypes) and ptypes[i]:
                    env[p] = ptypes[i]
    return env


def infer_expression(source, line, col=1, index=None, path=""):
    """3.4：给定源码位置，返回表达式推断类型与成员信息。

    返回 {"type": str, "expr": str, "node": str, "members": [str]}；
    解析失败返回 {}，不抛异常。
    """
    try:
        body = strip_header(source)
        tree = Parser(tokenize(body)).program()
    except Exception:
        return {}
    checker = TypeChecker(index)
    checker._src_lines = body.split("\n")
    checker._collect(tree)
    node = _node_at(tree, line, col)
    if node is None:
        return {}
    env = {}
    if index:
        try:
            for s in index.symbols(path):
                if s.kind in ("var", "param", "for") and s.typ not in ("", "?"):
                    env[s.name] = normalize_type(s.typ)
        except Exception:
            env = {}
    env.update(_env_before(checker, tree, line))
    typ = normalize_type(checker._type_of(node, env)) or "?"
    members = []
    if isinstance(node, AttrGet):
        base = checker._type_of(node.obj_expr, env)
        members = checker.class_members(base)
        typ = checker.member_type(base, node.attr_name) or typ
    elif isinstance(node, Var):
        members = checker.class_members(typ)
    text = ""
    lines = body.split("\n")
    if 0 < line <= len(lines):
        text = lines[line - 1].strip()
    return {"type": typ, "expr": text, "node": type(node).__name__, "members": members}



def _param_types_from(params):
    return [_param_types(p) for p in params]
