# -*- coding: utf-8 -*-
"""Impor (I++) 项目级符号索引。

给编辑器和 LSP 共用：声明、作用域、类型推断、引用位置、重命名编辑。
不依赖完整 AST，按行扫描 + 缩进作用域，保证大项目也能快速重建。
"""
import os
import re
import threading

SKIP_DIRS = {"__pycache__", ".git", "node_modules", "build", "dist", ".venv", "venv", ".impor_recovery"}

_DECL = re.compile(
    r"^(?P<indent>\s*)(?P<kind>func|class|exception|struct|var|const|let|set|import)\s+"
    r"(?P<name>[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)"
)
_FROM = re.compile(
    r"^(?P<indent>\s*)from\s+(?P<mod>[A-Za-z_][\w.]*)\s+import\s+"
    r"(?P<name>[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)"
)
_FOR = re.compile(r"^(?P<indent>\s*)for\s+(?P<name>[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s+in\b")
_TOKEN = re.compile(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*")


class Symbol:
    __slots__ = ("name", "kind", "path", "line", "col", "scope", "typ", "text")

    def __init__(self, name, kind, path, line, col, scope="", typ="", text=""):
        self.name = name
        self.kind = kind
        self.path = path
        self.line = line
        self.col = col
        self.scope = scope
        self.typ = typ
        self.text = text


class ProjectIndex:
    def __init__(self, root, roots=None, ignore=None):
        self.root = os.path.abspath(root) if root else os.getcwd()
        self.roots = [os.path.abspath(r) for r in (roots or [self.root])]
        self.ignore = set(ignore or []) | SKIP_DIRS
        self.by_name = {}
        self.refs = {}
        self.by_file = {}
        self._stamp = {}
        self._texts = {}
        self._loaded = False
        self._lock = threading.RLock()

    # ---------- 构建 ----------
    def refresh(self, force=False):
        # 3.2.2：索引可在后台线程重建，读操作仍拿到一致快照。
        with self._lock:
            return self._refresh(force)

    def _refresh(self, force=False):
        files = []
        seen = set()
        for root in self.roots:
            for base, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if d not in self.ignore and not d.startswith(".")]
                for fn in names:
                    if fn.endswith(".impor"):
                        path = os.path.join(base, fn)
                        key = os.path.normcase(os.path.abspath(path))
                        if key in seen:
                            continue
                        seen.add(key)
                        files.append(path)
        changed = False
        for path in files:
            try:
                stamp = os.path.getmtime(path)
            except OSError:
                continue
            if not force and self._stamp.get(path) == stamp and path in self.by_file:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue
            self._drop_file(path)
            self._scan_file(path, text)
            self._stamp[path] = stamp
            changed = True
        removed = [p for p in self.by_file if p not in files]
        for path in removed:
            self._drop_file(path)
            changed = True
        self._loaded = True
        return changed

    def update_text(self, path, text):
        path = os.path.abspath(path)
        with self._lock:
            self._drop_file(path)
            self._scan_file(path, text)
            try:
                self._stamp[path] = os.path.getmtime(path)
            except OSError:
                self._stamp[path] = 0
            self._loaded = True

    def _drop_file(self, path):
        self._texts.pop(path, None)
        for sym in self.by_file.pop(path, []):
            lst = self.by_name.get(sym.name, [])
            self.by_name[sym.name] = [s for s in lst if s.path != path]
        for name in list(self.refs):
            self.refs[name] = [r for r in self.refs[name] if r[0] != path]
            if not self.refs[name]:
                self.refs.pop(name, None)

    @staticmethod
    def _code_part(line):
        """去掉注释与字符串，保留列号（用空格替换）。"""
        out = list(line)
        i, n = 0, len(line)
        while i < n:
            if line.startswith("//", i) or line[i] == "#":
                for j in range(i, n):
                    out[j] = " "
                break
            if line[i] in ('"', "'"):
                q = line[i]
                out[i] = " "
                i += 1
                while i < n:
                    if line[i] == "\\":
                        out[i] = " "
                        if i + 1 < n:
                            out[i + 1] = " "
                        i += 2
                        continue
                    if line[i] == q:
                        out[i] = " "
                        i += 1
                        break
                    out[i] = " "
                    i += 1
                continue
            i += 1
        return "".join(out)

    def _scan_file(self, path, text):
        self._texts[path] = text
        lines = text.split("\n")
        head = 1 if lines and lines[0].strip().startswith("important<<") else 0
        ast_syms = self._ast_symbols(path, text, lines, head)
        if ast_syms is None:
            syms = self._line_symbols(path, lines, head)
        else:
            syms = ast_syms
        for ln, raw in enumerate(lines, 1):
            if ln <= head:
                continue
            for tm in _TOKEN.finditer(self._code_part(raw)):
                self.refs.setdefault(tm.group(0), []).append((path, ln, tm.start()))
        self.by_file[path] = syms

    def _ast_symbols(self, path, text, lines, head):
        """优先按语法树建索引；解析失败返回 None，由调用方回退行扫描。"""
        try:
            from .parser import Parser
            from .lexer import tokenize
            from .interpreter import strip_header
            from .ast_nodes import (Def, ClassDef, ExceptionDef, Assign, For,
                                    ImportNode, FromImportNode, Block, If, While,
                                    TryCatch, MatchNode, AST as ASTNode)
            body = strip_header(text)
            tree = Parser(tokenize(body)).program()
        except Exception:
            return None
        syms = []
        base_line = head

        def add(name, kind, lineno, col, scope, typ, src):
            sym = Symbol(name, kind, path, lineno, max(0, (col or 1) - 1), scope, typ, src.strip())
            syms.append(sym)
            self.by_name.setdefault(name, []).append(sym)
            return sym

        def walk(node, scope):
            if isinstance(node, (list, tuple)):
                for st in node:
                    walk(st, scope)
                return
            if isinstance(node, Block):
                for st in getattr(node, "stmts", []):
                    walk(st, scope)
                return
            if isinstance(node, Def):
                ln = node.lineno + base_line
                col = node.col
                add(node.name, "func", ln, col, scope, "func", lines[ln - 1] if 0 < ln <= len(lines) else "")
                inner = scope + "." + node.name
                raw_sig = lines[ln - 1] if 0 < ln <= len(lines) else ""
                lpar = raw_sig.find("(")
                rpar = raw_sig.rfind(")")
                parts = [p.strip() for p in raw_sig[lpar + 1:rpar].split(",")] if lpar >= 0 and rpar > lpar else []
                for pi, p in enumerate(node.params or []):
                    pl = node.lineno + base_line
                    pc = 1
                    raw = lines[pl - 1] if 0 < pl <= len(lines) else ""
                    idx = raw.find(p)
                    if idx >= 0:
                        pc = idx + 1
                    add(p, "param", pl, pc, inner, "?", parts[pi] if pi < len(parts) else p)
                walk(node.body, inner)
                return
            if isinstance(node, ClassDef):
                ln = node.lineno + base_line
                add(node.cls_name, "class", ln, node.col, scope, "class", "")
                walk(node.body, scope + "." + node.cls_name)
                return
            if isinstance(node, ExceptionDef):
                ln = node.lineno + base_line
                add(node.exc_name, "exception", ln, node.col, scope, "exception", "")
                return
            if isinstance(node, Assign):
                ln = node.lineno + base_line
                typ = self._expr_type(node.expr, scope)
                add(node.name, "var", ln, node.col or (lines[ln - 1].find(node.name) + 1 if 0 < ln <= len(lines) else 1), scope, typ, "")
                return
            if isinstance(node, For):
                ln = node.lineno + base_line
                add(node.var, "for", ln, node.col, scope, "?", "")
                walk(node.body, scope)
                return
            if isinstance(node, ImportNode):
                ln = node.lineno + base_line
                name = node.alias or node.modname.split(".")[-1]
                add(name, "import", ln, node.col, scope, "module", "")
                return
            if isinstance(node, FromImportNode):
                ln = node.lineno + base_line
                for orig, alias in node.names or []:
                    add(alias or orig, "import", ln, node.col, scope, "?", "")
                return
            if isinstance(node, If):
                walk(node.body, scope)
                for _, b in getattr(node, "elseifs", []) or []:
                    walk(b, scope)
                if getattr(node, "elseb", None):
                    walk(node.elseb, scope)
                return
            if isinstance(node, While):
                walk(node.body, scope)
                return
            if isinstance(node, TryCatch):
                for b in (getattr(node, "body", None), getattr(node, "catch_body", None),
                          getattr(node, "finally_body", None)):
                    if b:
                        walk(b, scope)
                return
            if isinstance(node, MatchNode):
                for _, b in getattr(node, "cases", []) or []:
                    walk(b, scope)
                if getattr(node, "elseb", None):
                    walk(node.elseb, scope)
                return
            # 其余节点递归查找块语句（保守，不重复声明）
            try:
                values = vars(node).values()
            except TypeError:
                return
            for val in values:
                if isinstance(val, Block):
                    walk(val, scope)
                elif isinstance(val, list):
                    for x in val:
                        if isinstance(x, Block) or isinstance(x, ASTNode):
                            walk(x, scope)

        walk(tree, "<module>")
        return syms

    @staticmethod
    def _expr_type(expr, scope):
        from .ast_nodes import Num, Str, Bool, ListLit, DictLit, Var, Call
        if isinstance(expr, Num):
            return "float" if isinstance(expr.val, float) else "int"
        if isinstance(expr, Str):
            return "str"
        if isinstance(expr, Bool):
            return "bool"
        if isinstance(expr, ListLit):
            return "list"
        if isinstance(expr, DictLit):
            return "dict"
        if isinstance(expr, Call):
            return "?"
        return "?"

    def _line_symbols(self, path, lines, head):
        scopes = []  # (indent, name)
        syms = []
        for ln, raw in enumerate(lines, 1):
            if ln <= head:
                continue
            code = self._code_part(raw)
            if not code.strip():
                continue
            stripped = code.lstrip()
            indent = len(code) - len(stripped)
            while scopes and scopes[-1][0] >= indent:
                scopes.pop()
            scope = ".".join(s[1] for s in scopes) or "<module>"
            m = _FROM.match(code) or _DECL.match(code)
            kind = name = None
            if m and "name" in m.groupdict():
                name = m.group("name")
                kind = m.groupdict().get("kind") or "import"
            else:
                fm = _FOR.match(code)
                if fm:
                    name, kind = fm.group("name"), "for"
            if name:
                col = code.find(name)
                typ = self._infer_type(code, name, kind)
                sym = Symbol(name, kind, path, ln, col, scope, typ, raw.strip())
                syms.append(sym)
                self.by_name.setdefault(name, []).append(sym)
                if kind == "func":
                    inner = code[code.find("(", col) + 1:code.rfind(")")] if "(" in code else ""
                    for part in inner.split(","):
                        pname = part.strip().split("=")[0].split(":")[0].strip()
                        if re.match(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*$", pname):
                            pcol = code.find(pname)
                            psym = Symbol(pname, "param", path, ln, pcol, scope + "." + name, "?", part.strip())
                            syms.append(psym)
                            self.by_name.setdefault(pname, []).append(psym)
                if kind in ("func", "class", "exception", "struct"):
                    scopes.append((indent, name))
        return syms


    @staticmethod
    def _infer_type(code, name, kind):
        if kind in ("func", "class", "exception", "struct"):
            return {"func": "func", "class": "class", "exception": "exception", "struct": "struct"}[kind]
        rhs = ""
        if "=" in code:
            rhs = code.split("=", 1)[1].strip()
        if not rhs:
            return "?"
        first = rhs[:1]
        if first in ('"', "'"):
            return "str"
        if rhs in ("true", "false", "True", "False"):
            return "bool"
        if re.match(r"-?\d+$", rhs):
            return "int"
        if re.match(r"-?\d+\.\d+", rhs):
            return "float"
        if first == "[":
            return "list"
        if first == "{":
            return "dict"
        if re.match(r"(func|lambda)\b", rhs):
            return "func"
        return "?"

    # ---------- 查询 ----------
    def ensure(self):
        if not self._loaded:
            self.refresh()

    def symbols(self, path=None):
        with self._lock:
            self.ensure()
            if path:
                return list(self.by_file.get(os.path.abspath(path), []))
            return [s for lst in self.by_file.values() for s in lst]

    def definition(self, name, prefer_path=None):
        with self._lock:
            self.ensure()
            syms = list(self.by_name.get(name, []))
            if prefer_path:
                p = os.path.abspath(prefer_path)
                syms.sort(key=lambda s: 0 if s.path == p else 1)
            return syms

    def references(self, name):
        with self._lock:
            self.ensure()
            return list(self.refs.get(name, []))

    def rename_edits(self, name, new_name, prefer_path=None):
        with self._lock:
            self.ensure()
            if name not in self.by_name:
                return {}
            changes = {}
            for path, line, col in self.references(name):
                changes.setdefault(path, []).append({
                    "line": line, "col": col, "old": name, "new": new_name})
            return changes

    # ---------- 3.3.0：作用域解析 / 精确高亮 / 作用域重命名 ----------
    def scope_at_line(self, path, line):
        """返回 path 第 line 行所在的最内层 func/class 作用域；模块级返回 <module>。"""
        path = os.path.abspath(path)
        text = self._texts.get(path)
        syms = [s for s in self.by_file.get(path, [])
                if s.kind in ("func", "class", "exception", "struct")]
        if not text:
            best = max((s for s in syms if s.line <= line), key=lambda s: s.line, default=None)
            return (best.scope + "." + best.name) if best else "<module>"
        lines = text.split("\n")
        best = None
        for s in syms:
            if s.line > line:
                continue
            base = len(lines[s.line - 1]) - len(lines[s.line - 1].lstrip(" \t"))
            end = len(lines)
            for i in range(s.line, len(lines)):
                raw = lines[i]
                if raw.strip() and (len(raw) - len(raw.lstrip(" \t"))) <= base:
                    end = i
                    break
            if s.line <= line <= end and (best is None or s.line >= best.line):
                best = s
        return (best.scope + "." + best.name) if best else "<module>"

    def symbol_at(self, name, path=None, line=None, prefer_path=None):
        syms = self.definition(name, prefer_path or path)
        if line is not None and path:
            path = os.path.abspath(path)
            same = [s for s in syms if s.path == path]
            if same:
                return min(same, key=lambda s: abs(s.line - line))
        return syms[0] if syms else None

    def rename_edits_scoped(self, name, new_name, path=None, line=None):
        """只改同一作用域的引用：局部变量不会误改别的函数里的同名变量。"""
        with self._lock:
            self.ensure()
            if name not in self.by_name:
                return {}
            sym = self.symbol_at(name, path, line, prefer_path=path)
            target_scope = (self.scope_at_line(path, line)
                            if path and line else (sym.scope if sym else "<module>"))
            cross_file = sym is not None and sym.kind in ("func", "class", "exception", "struct", "import") \
                and target_scope == "<module>"
            changes = {}
            for p, ln, col in self.references(name):
                if p != os.path.abspath(path or ""):
                    if not cross_file:
                        continue
                else:
                    if self.scope_at_line(p, ln) != target_scope:
                        continue
                changes.setdefault(p, []).append({
                    "line": ln, "col": col, "old": name, "new": new_name})
            return changes

    def semantic_positions(self, path, text):
        """返回 {(line, col0): kind}，按作用域给每个出现位置定色。"""
        path = os.path.abspath(path)
        tmp = ProjectIndex(os.path.dirname(path) or os.getcwd(), roots=[os.path.dirname(path) or os.getcwd()])
        tmp.update_text(path, text)
        syms = tmp.symbols(path)
        by_scope = {}
        for s in syms:
            by_scope.setdefault((s.name, s.scope), []).append(s)
        out = {}
        for name, refs in tmp.refs.items():
            for p, line, col in refs:
                if p != path:
                    continue
                scope = tmp.scope_at_line(path, line)
                cands = by_scope.get((name, scope)) or []
                if not cands:
                    # 变量在嵌套块中声明但作用域仍是函数；再按同行最近声明兜底
                    cands = [s for s in syms if s.name == name and s.line <= line]
                if not cands:
                    continue
                s = min(cands, key=lambda x: abs(x.line - line))
                out[(line, col)] = s.kind
        return out

    def completion(self, prefix=""):
        with self._lock:
            self.ensure()
            names = set(self.by_name) | set(self.refs)
            if prefix:
                names = {n for n in names if n.startswith(prefix)}
            out = []
            for name in sorted(names):
                syms = self.by_name.get(name)
                if syms:
                    sym = syms[-1]
                    out.append((name, sym.kind, sym.typ, sym.path, sym.line))
                else:
                    out.append((name, "ref", "?", "", 0))
            return out

    def stats(self):
        with self._lock:
            self.ensure()
            return len(self.by_file), len(self.by_name), sum(len(v) for v in self.refs.values())
