# -*- coding: utf-8 -*-
"""项目依赖图与审计的纯逻辑层（编辑器 3.1）。

- 文件图：.impor 源码之间的 import/from 关系，解析同目录/包入口/唯一模块名。
- 包图：复用 pkgmgr 的 impor.lock / 依赖解析。
- 审计：循环、未解析导入、自导入、孤立文件、重复模块名、包锁问题。
"""

from __future__ import annotations

import json
import os
import re
from .pathsafe import rel_quiet
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)", re.M)
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".impor_recovery", "node_modules",
             ".venv", "venv", "dist", "build", "_backup"}


@dataclass
class FileNode:
    path: str
    imports: List[Tuple[str, int]] = field(default_factory=list)
    resolved: List[str] = field(default_factory=list)
    unresolved: List[Tuple[str, int]] = field(default_factory=list)


@dataclass
class GraphReport:
    root: str
    nodes: Dict[str, FileNode] = field(default_factory=dict)
    reverse: Dict[str, List[str]] = field(default_factory=dict)
    cycles: List[List[str]] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)
    package_roots: List[str] = field(default_factory=list)
    packages: Dict[str, dict] = field(default_factory=dict)
    package_issues: List[Tuple[str, str]] = field(default_factory=list)

    def audit_issues(self) -> List[Tuple[str, str, str]]:
        issues: List[Tuple[str, str, str]] = []
        for path, node in self.nodes.items():
            for mod, line in node.unresolved:
                issues.append(("错误", "未解析导入", "%s:%d  %s" % (rel(self.root, path), line, mod)))
        for cyc in self.cycles:
            issues.append(("错误", "循环依赖", " → ".join(rel(self.root, p) for p in cyc)))
        for path, node in self.nodes.items():
            for mod, line in node.imports:
                if _module_guess(self.root, path) == mod.replace("/", "."):
                    issues.append(("警告", "自导入", "%s:%d" % (rel(self.root, path), line)))
        for path in self.orphans:
            issues.append(("提示", "孤立文件", rel(self.root, path)))
        for mod, paths in self.duplicates.items():
            if len(paths) > 1:
                issues.append(("警告", "重复模块名", "%s: %s" % (mod, "、".join(rel(self.root, p) for p in paths))))
        for level, text in self.package_issues:
            issues.append((level, "包审计", text))
        return issues

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "nodes": [{"path": rel(self.root, p), "imports": n.imports,
                       "resolved": [rel(self.root, x) for x in n.resolved],
                       "unresolved": n.unresolved} for p, n in sorted(self.nodes.items())],
            "cycles": [[rel(self.root, p) for p in c] for c in self.cycles],
            "orphans": [rel(self.root, p) for p in self.orphans],
            "duplicates": {k: [rel(self.root, p) for p in v] for k, v in self.duplicates.items()},
            "packages": self.packages,
            "package_roots": self.package_roots,
            "package_issues": self.package_issues,
            "issues": self.audit_issues(),
        }


def rel(root, path):
    try:
        return rel_quiet(path, root).replace("\\", "/")
    except Exception:
        return path


def _walk(root):
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".impor"):
                yield os.path.join(dirpath, name)


def _module_guess(root, path):
    relpath = rel_quiet(path, root).replace("\\", "/")
    if relpath.endswith(".impor"):
        relpath = relpath[:-6]
    if relpath.endswith("/__pkg__"):
        relpath = relpath[:-8] or "__pkg__"
    return relpath.replace("/", ".")


def _resolve_module(root, module, files):
    root = os.path.abspath(root)
    dotted = module.replace(".", os.sep)
    candidates = [
        os.path.join(root, dotted + ".impor"),
        os.path.join(root, dotted, "__pkg__.impor"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    # 唯一 basename 回退：允许 import 标准库模块名
    base = module.split(".")[-1]
    matches = [p for p in files if os.path.basename(p)[:-6] == base]
    if len(matches) == 1:
        return os.path.abspath(matches[0])
    return None


def _find_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    cycles = []
    seen_sets = set()
    visiting, done = set(), set()

    def dfs(node, stack):
        if node in visiting:
            idx = stack.index(node)
            cyc = stack[idx:] + [node]
            key = frozenset(cyc)
            if key not in seen_sets:
                seen_sets.add(key)
                cycles.append(cyc)
            return
        if node in done:
            return
        visiting.add(node)
        stack.append(node)
        for nxt in graph.get(node, []):
            dfs(nxt, stack)
        stack.pop()
        visiting.discard(node)
        done.add(node)

    for n in sorted(graph):
        dfs(n, [])
    return cycles


def build_file_graph(root: str, files: Optional[Iterable[str]] = None,
                     package_dir: Optional[str] = None) -> GraphReport:
    root = os.path.abspath(root)
    files = [os.path.abspath(p) for p in (files if files is not None else _walk(root))]
    report = GraphReport(root=root)
    duplicates: Dict[str, List[str]] = {}
    for p in files:
        name = os.path.basename(p)[:-6]
        duplicates.setdefault(name, []).append(p)
    report.duplicates = {k: v for k, v in duplicates.items() if len(v) > 1}
    for path in files:
        node = FileNode(path=path)
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except Exception:
            text = ""
        for m in IMPORT_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            node.imports.append((m.group(1), line))
        report.nodes[path] = node
    for path, node in report.nodes.items():
        for mod, line in node.imports:
            target = _resolve_module(root, mod, files)
            if not target:
                node.unresolved.append((mod, line))
            elif os.path.abspath(target) != os.path.abspath(path) and target not in node.resolved:
                node.resolved.append(target)
                report.reverse.setdefault(target, []).append(path)
        if node.unresolved:
            pass
    edges = {p: [t for t in n.resolved if t in report.nodes] for p, n in report.nodes.items()}
    report.cycles = _find_cycles(edges)
    for p, n in report.nodes.items():
        if not n.imports and not report.reverse.get(p):
            report.orphans.append(p)
    # 包依赖与审计：复用 pkgmgr；没有 impor.lock 时给提示而不是报错
    proj = package_dir or root
    try:
        from .pkgmgr import dependency_graph, audit
        roots, packages = dependency_graph(proj)
        report.package_roots = list(roots)
        report.packages = packages
        report.package_issues = list(audit(proj))
    except Exception as exc:
        report.package_issues = [("提示", "读取 impor.lock 失败：%s" % str(exc)[:100])]
    return report


def package_tree(report: GraphReport) -> List[Tuple[str, str, str, int]]:
    """返回 (name, version, installed, depth) 的 DFS 展平，UI 可直接建树。"""
    rows = []
    entries = report.packages
    roots = report.package_roots or sorted(entries)
    visiting = set()

    def walk(name, depth, last=True):
        meta = entries.get(name) or {}
        version = str(meta.get("version") or "?")
        installed = "已安装" if meta.get("installed", True) else "未安装"
        rows.append((name, version, installed, depth))
        if name in visiting:
            rows.append(("↺ " + name, "循环", "", depth + 1))
            return
        visiting.add(name)
        for req in meta.get("requires") or []:
            walk(str(req), depth + 1, False)
        visiting.discard(name)

    for r in roots:
        walk(str(r), 0)
    return rows
