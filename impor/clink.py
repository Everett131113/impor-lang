# -*- coding: utf-8 -*-
"""C 后端模块链接：把 import 的 .impor 模块拼平成单文件 AST。

只做代码级链接，不执行模块。为控制产物规模，未被引用的模块函数/类会被裁掉；
模块顶层赋值与可执行语句保留（它们相当于模块初始化）。
符号暂不做前缀重命名：跨模块同名会明确报错，避免静默改义。
"""
import os
import re

from .ast_nodes import (
    AST, Assign, AttrGet, BindingPattern, Call, ClassDef, CompoundAssign, Def,
    DictLit, ExceptionDef, ExportNode, For, FromImportNode, If, ImportNode,
    MatchNode, NewInstance, Str, TryCatch, Var, While,
)
from .lexer import tokenize
from .parser import Parser

_HEADER_MARK = 'important<<DAY<<english("English"):'


class LinkError(Exception):
    pass


def _parse_file(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    stripped = src.lstrip()
    body = stripped[len(_HEADER_MARK):] if stripped.startswith(_HEADER_MARK) else stripped
    return Parser(tokenize(body)).program()


def _iter_nodes(node):
    """通用 AST 遍历（语句列表、表达式、元组均覆盖）。"""
    yield node
    for _k, v in vars(node).items():
        if isinstance(v, AST):
            yield from _iter_nodes(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, AST):
                    yield from _iter_nodes(x)
                elif isinstance(x, tuple):
                    for y in x:
                        if isinstance(y, AST):
                            yield from _iter_nodes(y)


def _walk_imports(node):
    for n in _iter_nodes(node):
        if isinstance(n, (ImportNode, FromImportNode)):
            yield n


class _Module:
    def __init__(self, name, path, ast):
        self.name = name
        self.path = path
        self.ast = ast
        self.symbols = {}
        self.exports = set()
        self.used = set()
        self.cnames = {}


class Linker:
    def __init__(self, source_name=None, search_paths=None):
        self.modules = {}
        self.order = []
        self.aliases = {}          # owner(None=main) -> {local: binding}
        self.sites = []
        here = os.path.dirname(os.path.abspath(source_name)) if source_name else os.getcwd()
        paths = [here, os.getcwd()] + list(search_paths or [])
        self.search_paths = []
        for p in paths:
            ap = os.path.abspath(p)
            if ap not in self.search_paths:
                self.search_paths.append(ap)

    # ---------- 定位与加载 ----------
    def find(self, logical):
        parts = logical.split(".")
        std_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")
        if logical == "std" or logical.startswith("std."):
            sub = parts[1:]
            bases = [std_dir] + self.search_paths
        else:
            sub = parts
            bases = self.search_paths
        for base in bases:
            p = os.path.join(base, *sub)
            pkg = os.path.join(p, "__pkg__.impor")
            if os.path.isfile(pkg):
                return pkg
            f = p + ".impor"
            if os.path.isfile(f):
                return f
        return None

    def load(self, name):
        if name in self.modules:
            return self.modules[name]
        path = self.find(name)
        if path is None:
            raise LinkError(f"C 后端找不到模块 {name}（搜索路径: "
                            f"{', '.join(self.search_paths)}）")
        ast = _parse_file(path)
        rec = _Module(name, path, ast)
        rec.is_pkg = os.path.basename(path) == "__pkg__.impor"
        self.modules[name] = rec
        for st in ast.stmts:
            if isinstance(st, Def):
                self._add_symbol(rec, st.name, st)
            elif isinstance(st, ClassDef):
                self._add_symbol(rec, st.cls_name, st)
            elif isinstance(st, ExceptionDef):
                self._add_symbol(rec, st.exc_name, st)
            elif isinstance(st, Assign):
                self._add_symbol(rec, st.name, st)
            elif isinstance(st, ExportNode):
                rec.exports.update(st.names)
        # 先递归加载依赖，保证被依赖模块先拼平
        for n in _walk_imports(ast):
            if isinstance(n, ImportNode):
                if n.is_python:
                    raise LinkError(f"C 后端不支持 Python 模块导入：{n.modname}")
                self.load(n.modname)
            else:
                target = self.resolve(n.modname, n.rel_level, self.pkg_ctx(rec), n.lineno)
                self.load(target)
        self.sites.append((name, ast))
        self.order.append(rec)
        return rec

    def _add_symbol(self, rec, name, node):
        if name in rec.symbols:
            raise LinkError(f"模块 {rec.name} 内符号重复：{name}")
        rec.symbols[name] = node

    def _pkg_of(self, logical):
        return logical.rsplit(".", 1)[0] if "." in logical else None

    def pkg_ctx(self, rec):
        return rec.name if rec.is_pkg else self._pkg_of(rec.name)

    def resolve(self, modname, rel_level, pkg, lineno):
        if rel_level == 0:
            return modname
        if pkg is None:
            raise LinkError(f"相对导入只能在包内使用（第 {lineno} 行）")
        parts = pkg.split(".")
        if len(parts) < rel_level - 1:
            raise LinkError(f"相对导入超出包层级（第 {lineno} 行）")
        parts = parts[:len(parts) - (rel_level - 1)]
        if modname:
            parts.extend(modname.split("."))
        return ".".join(parts)

    # ---------- 别名 ----------
    def build_aliases(self, owner, ast, pkg):
        amap = {}
        for n in _walk_imports(ast):
            if isinstance(n, ImportNode):
                target = n.modname
                if n.alias:
                    amap[n.alias] = ("module", target)
                else:
                    root = target.split(".")[0]
                    amap[root] = ("package", root)
            else:
                target = self.resolve(n.modname, n.rel_level, pkg, n.lineno)
                if n.star:
                    rec = self.modules[target]
                    names = rec.exports or set(rec.symbols)
                    for nm in names:
                        amap[nm] = ("symbol", target, nm)
                else:
                    for orig, alias in n.names:
                        local = alias or orig
                        amap[local] = ("symbol", target, orig)
        self.aliases[owner] = amap

    def _external(self, target, name, lineno=0):
        rec = self.modules.get(target)
        if rec is None:
            raise LinkError(f"模块 {target} 未加载")
        if rec.exports and name not in rec.exports:
            raise LinkError(f"模块 {target} 未导出符号 {name}（第 {lineno} 行）")
        if name not in rec.symbols:
            raise LinkError(f"模块 {target} 没有符号 {name}（第 {lineno} 行）")
        return rec.symbols[name]

    # ---------- 改写 ----------
    def rewrite(self, node, owner, shadow=None, in_class=False):
        rec = self.modules.get(owner) if owner else None
        amap = self.aliases.get(owner, {})
        if isinstance(node, Var):
            if shadow and node.name in shadow:
                return node
            b = amap.get(node.name)
            if b and b[0] == "symbol":
                self._external(b[1], b[2], node.lineno)
                self.modules[b[1]].used.add(b[2])
                return Var(self.modules[b[1]].cnames[b[2]], node.lineno, node.col)
            if b and b[0] in ("module", "package"):
                target = b[1]
                if target not in self.modules:
                    raise LinkError(f"模块 {target} 未加载（第 {node.lineno} 行）")
                return self._module_value(self.modules[target], node.lineno)
            if rec and node.name in rec.cnames:
                return Var(rec.cnames[node.name], node.lineno, node.col)
            return node
        if isinstance(node, AttrGet):
            chain = self._chain(node)
            if chain:
                root, attrs = chain
                b = amap.get(root)
                if b:
                    repl = self._resolve_attr(b, attrs, node.lineno)
                    if repl is not None:
                        return repl
            node.obj_expr = self.rewrite(node.obj_expr, owner, shadow)
            return node
        if isinstance(node, NewInstance):
            b = amap.get(node.cls_name)
            if b and b[0] == "symbol":
                self._external(b[1], b[2], node.lineno)
                self.modules[b[1]].used.add(b[2])
                node.cls_name = self.modules[b[1]].cnames[b[2]]
            elif rec and node.cls_name in rec.cnames:
                node.cls_name = rec.cnames[node.cls_name]
            node.args = [self.rewrite(a, owner, shadow) for a in node.args]
            return node
        if isinstance(node, Def):
            if not in_class and rec and node.name in rec.cnames:
                node.name = rec.cnames[node.name]
            loc = set(node.params) | self._collect_locals(node.body)
            node.body = [self.rewrite(s, owner, loc) for s in node.body]
            return node
        if isinstance(node, ClassDef):
            if not hasattr(node, "orig_name"):
                node.orig_name = node.cls_name
            if rec and node.cls_name in rec.cnames:
                node.cls_name = rec.cnames[node.cls_name]
            if rec and node.base_name in rec.cnames:
                node.base_name = rec.cnames[node.base_name]
            body = []
            for st in node.body:
                if isinstance(st, Def):
                    loc = set(st.params) | self._collect_locals(st.body)
                    st.body = [self.rewrite(x, owner, loc) for x in st.body]
                    body.append(st)
                elif isinstance(st, Assign):
                    st.expr = self.rewrite(st.expr, owner, shadow)
                    body.append(st)
                else:
                    body.append(self.rewrite(st, owner, shadow))
            node.body = body
            return node
        if isinstance(node, (Assign, CompoundAssign)):
            if shadow is None or node.name not in shadow:
                if rec and node.name in rec.cnames:
                    node.name = rec.cnames[node.name]
            node.expr = self.rewrite(node.expr, owner, shadow)
            return node
        if isinstance(node, For):
            node.iter_expr = self.rewrite(node.iter_expr, owner, shadow)
            loc = set(shadow or ()) | {node.var}
            node.body = [self.rewrite(s, owner, loc) for s in node.body]
            return node
        if isinstance(node, TryCatch):
            node.body = [self.rewrite(s, owner, shadow) for s in node.body]
            loc = set(shadow or ()) | ({node.err_var} if node.err_var else set())
            node.catch_body = [self.rewrite(s, owner, loc) for s in node.catch_body]
            if node.finally_body:
                node.finally_body = [self.rewrite(s, owner, shadow) for s in node.finally_body]
            return node
        if isinstance(node, MatchNode):
            node.subject = self.rewrite(node.subject, owner, shadow)
            cases = []
            for pat, body in node.cases:
                loc = set(shadow or ())
                if isinstance(pat, BindingPattern) and pat.name:
                    loc.add(pat.name)
                cases.append((pat, [self.rewrite(s, owner, loc) for s in body]))
            node.cases = cases
            if node.elseb:
                node.elseb = [self.rewrite(s, owner, shadow) for s in node.elseb]
            return node
        for k, v in list(vars(node).items()):
            if k in ("lineno", "col", "file"):
                continue
            if isinstance(v, AST):
                setattr(node, k, self.rewrite(v, owner, shadow))
            elif isinstance(v, list):
                out = []
                for x in v:
                    if isinstance(x, AST):
                        out.append(self.rewrite(x, owner, shadow))
                    elif isinstance(x, tuple):
                        out.append(tuple(self.rewrite(y, owner, shadow) if isinstance(y, AST) else y
                                          for y in x))
                    else:
                        out.append(x)
                setattr(node, k, out)
        return node

    def _collect_locals(self, stmts):
        """函数内会被写入的名字（参数另算），用于避免把局部变量误当成模块符号。"""
        out = set()

        def walk(s):
            if isinstance(s, Assign):
                if s.scope == "local":
                    out.add(s.name)
            elif isinstance(s, CompoundAssign):
                out.add(s.name)
            elif isinstance(s, For):
                out.add(s.var)
                for x in s.body:
                    walk(x)
            elif isinstance(s, If):
                for x in s.body:
                    walk(x)
                for _c, b in s.elseifs:
                    for x in b:
                        walk(x)
                for x in s.elseb or []:
                    walk(x)
            elif isinstance(s, While):
                for x in s.body:
                    walk(x)
            elif isinstance(s, TryCatch):
                if s.err_var:
                    out.add(s.err_var)
                for x in s.body:
                    walk(x)
                for x in s.catch_body:
                    walk(x)
                for x in s.finally_body or []:
                    walk(x)
            elif isinstance(s, MatchNode):
                for pat, body in s.cases:
                    if isinstance(pat, BindingPattern) and pat.name:
                        out.add(pat.name)
                    for x in body:
                        walk(x)
                for x in s.elseb or []:
                    walk(x)

        for s in stmts:
            walk(s)
        return out

    def _build_cnames(self):
        for rec in self.order:
            safe = re.sub(r"[^0-9A-Za-z_]", "_", rec.name)
            rec.cnames = {name: f"__m_{safe}_{name}" for name in rec.symbols}
            rec.cname_to_name = {v: k for k, v in rec.cnames.items()}

    def _chain(self, node):
        attrs = []
        cur = node
        while isinstance(cur, AttrGet):
            attrs.insert(0, cur.attr_name)
            cur = cur.obj_expr
        if isinstance(cur, Var):
            return cur.name, attrs
        return None

    def _module_value(self, rec, lineno):
        """把模块对象编译成运行期字典值：导出名 -> 函数/顶层变量。"""
        names = sorted(rec.exports or set(rec.symbols))
        kv = []
        for name in names:
            node = rec.symbols.get(name)
            if not isinstance(node, (Def, Assign)):
                continue
            rec.used.add(name)
            kv.append((Str(name, lineno, 0), Var(rec.cnames[name], lineno, 0)))
        return Call(Var("__module__", lineno, 0),
                    [Str(rec.name, lineno, 0), DictLit(kv, lineno, 0)],
                    lineno, 0)

    def _resolve_attr(self, b, attrs, lineno):
        if b[0] == "symbol":
            # 模块里的类/对象属性：m.CONST.attr；只替换符号部分
            self._external(b[1], b[2], lineno)
            self.modules[b[1]].used.add(b[2])
            expr = Var(self.modules[b[1]].cnames[b[2]], lineno, 0)
            for a in attrs:
                expr = AttrGet(expr, a, lineno, 0)
            return expr
        if b[0] == "module":
            if not attrs:
                return self._module_value(self.modules[b[1]], lineno)
            sym = self._external(b[1], attrs[0], lineno)
            self.modules[b[1]].used.add(attrs[0])
            expr = Var(self.modules[b[1]].cnames[attrs[0]], lineno, 0)
            for a in attrs[1:]:
                expr = AttrGet(expr, a, lineno, 0)
            return expr
        # package：找最长已加载模块前缀，再取下一个属性为符号
        pkg, rest = b[1], attrs[:]
        prefix = None
        for i in range(len(rest), -1, -1):
            cand = pkg + ("." + ".".join(rest[:i]) if i else "")
            if cand in self.modules:
                prefix = cand
                break
        if prefix is None:
            raise LinkError(f"模块 {pkg} 未加载（第 {lineno} 行）")
        consumed = prefix[len(pkg):].strip(".").split(".") if prefix != pkg else []
        consumed = [c for c in consumed if c]
        remain = rest[len(consumed):]
        if not remain:
            return self._module_value(self.modules[prefix], lineno)
        sym = remain[0]
        self._external(prefix, sym, lineno)
        self.modules[prefix].used.add(sym)
        expr = Var(self.modules[prefix].cnames[sym], lineno, 0)
        for a in remain[1:]:
            expr = AttrGet(expr, a, lineno, 0)
        return expr

    # ---------- 裁剪 ----------
    @staticmethod
    def _mark_vars(node, rec):
        for n in _iter_nodes(node):
            if isinstance(n, Var) and n.name in rec.cname_to_name:
                rec.used.add(rec.cname_to_name[n.name])

    def _expand_used(self, rec, defs_only=True):
        changed = True
        while changed:
            changed = False
            for name in list(rec.used):
                node = rec.symbols.get(name)
                if isinstance(node, (Def, ClassDef)):
                    before = len(rec.used)
                    for st in node.body:
                        self._mark_vars(st, rec)
                    if len(rec.used) != before:
                        changed = True
            if not changed:
                break

    # ---------- 主流程 ----------
    def link(self, main_ast, stats=None):
        self.load_all_main(main_ast)
        if stats is not None:
            stats["modules"] = [rec.name for rec in self.order]
        # 主程序别名
        self.build_aliases(None, main_ast, None)
        for name, ast in self.sites:
            self.build_aliases(name, ast, self.pkg_ctx(self.modules[name]))
        self._build_cnames()
        # 先把所有模块内引用改写成扁平符号（保留裁剪候选）
        for rec in self.order:
            for st in rec.ast.stmts:
                if not isinstance(st, (ImportNode, FromImportNode, ExportNode)):
                    self.rewrite(st, rec.name)
        # 主程序改写
        for st in main_ast.stmts:
            if not isinstance(st, (ImportNode, FromImportNode, ExportNode)):
                self.rewrite(st, None)
        # 模块初始化语句里引用到的自身符号
        for rec in self.order:
            for st in rec.ast.stmts:
                if not isinstance(st, (Def, ClassDef, ImportNode, FromImportNode, ExportNode)):
                    self._mark_vars(st, rec)
            self._expand_used(rec)
        # 拼平：依赖在前
        flat = []
        for rec in self.order:
            for st in rec.ast.stmts:
                if isinstance(st, (ImportNode, FromImportNode, ExportNode)):
                    continue
                if isinstance(st, (Def, ClassDef, ExceptionDef)):
                    nm = st.name if hasattr(st, "name") else getattr(st, "cls_name", None) or getattr(st, "exc_name", None)
                    orig = rec.cname_to_name.get(nm, nm)
                    if orig not in rec.used:
                        continue
                    flat.append(st)
                else:
                    flat.append(st)
        # 主程序去掉 import
        linked = _strip_all_imports(main_ast)
        linked.stmts = flat + linked.stmts
        return linked

    def load_all_main(self, main_ast):
        for n in _walk_imports(main_ast):
            if isinstance(n, ImportNode):
                if n.is_python:
                    raise LinkError(f"C 后端不支持 Python 模块导入：{n.modname}")
                self.load(n.modname)
            else:
                target = self.resolve(n.modname, n.rel_level, None, n.lineno)
                self.load(target)


def _strip_all_imports(ast):
    """递归移除所有 import/export 语句，保留其它节点。"""
    for node in _iter_nodes(ast):
        for k, v in list(vars(node).items()):
            if isinstance(v, list) and v and isinstance(v[0], AST):
                v[:] = [x for x in v if not isinstance(x, (ImportNode, FromImportNode, ExportNode))]
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    if isinstance(x, tuple):
                        v[i] = tuple(y for y in x if not isinstance(y, (ImportNode, FromImportNode, ExportNode)))
    # Block / If 等 body 列表已覆盖；顶层再兜底
    ast.stmts = [s for s in ast.stmts if not isinstance(s, (ImportNode, FromImportNode, ExportNode))]
    return ast


def link_modules(ast, source_name=None, search_paths=None, stats=None):
    return Linker(source_name, search_paths).link(ast, stats=stats)
