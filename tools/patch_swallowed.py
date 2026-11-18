# -*- coding: utf-8 -*-
"""给最危险的 20 处 except: pass 加诊断记录（不改变控制流）。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    ("builtins.py", 567, "sandbox allow list"),
    ("cli.py", 577, "build cache write"),
    ("cli.py", 613, "tcc stat"),
    ("cli.py", 631, "module hash"),
    ("cli.py", 643, "package hash"),
    ("cli.py", 667, "build cache read"),
    ("pkgmgr.py", 234, "lock hash"),
    ("lsp.py", 141, "lsp index update"),
    ("lsp.py", 202, "lsp project index"),
    ("editor.py", 437, "audit/harden"),
    ("editor.py", 472, "refresh index"),
    ("editor_runtime.py", 446, "problem mark"),
    ("editor_sync.py", 124, "remote snapshot merge"),
    ("editor_search.py", 227, "search scan"),
    ("editor_search.py", 280, "search scan"),
    ("editor_virtual.py", 46, "virtual file read"),
    ("editor_virtual.py", 60, "virtual file read"),
    ("plugin_host.py", 608, "plugin host"),
    ("plugin_host.py", 646, "plugin host"),
    ("plugin_host.py", 658, "plugin host"),
]


def patch(fname, lineno, area):
    path = os.path.join(ROOT, "impor", fname)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    idx = lineno - 1
    for probe in range(idx, min(idx + 4, len(lines))):
        if lines[probe].strip() == "pass":
            idx = probe
            break
    if lines[idx].strip() != "pass":
        raise RuntimeError("%s:%s not pass: %r" % (fname, lineno, lines[idx]))
    indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
    lines[idx] = indent + 'note_swallowed("%s")' % area
    if "from .observability import note_swallowed" not in lines:
        insert_at = 0
        for i, line in enumerate(lines[:80]):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
        lines.insert(insert_at, "from .observability import note_swallowed")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("patched", fname, lineno, area)


for fname in sorted({t[0] for t in TARGETS}):
    for t in sorted([x for x in TARGETS if x[0] == fname], key=lambda x: x[1], reverse=True):
        patch(*t)
