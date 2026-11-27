# -*- coding: utf-8 -*-
"""全仓 .impor 格式门禁（v4.0：不依赖 Git）。

只扫描随发布包分发的源码目录，因此解压后也能独立复现：
  apps / benchmarks / bootstrap / conformance / demos / examples /
  impor / launcher / site / tests / tools / vscode-impor

排除两类不能/不应改写的文件：
  - conformance/compat_3_11/*：3.11 行为基线，保留缩进块写法，SHA256 在 manifest；
  - bootstrap/imporc.impor：自举编译器生成物，格式化器对其大括号嵌套不稳定。
其余所有 .impor 必须 `check_fmt` 通过。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from impor.fmt import check_fmt

SOURCE_DIRS = (
    "apps", "benchmarks", "bootstrap", "conformance", "demos", "examples",
    "impor", "launcher", "site", "tests", "tools", "vscode-impor",
)
SKIP_DIRS = {"__pycache__", "build", "dist", "dist-gui", "node_modules", ".git",
             ".impor", ".impor_recovery", ".pytest_cache"}
EXCLUDE_PREFIX = ("conformance/compat_3_11/",)
EXCLUDE_EXACT = {"bootstrap/imporc.impor"}

# 首行头部标记（docs/语言规范.md §1）：存在时必须与规范逐字一致
HEADER_CANONICAL = 'important<<DAY<<english("English"):'


def header_problem(src):
    """首行若带语言头部，必须与规范完全一致。

    头部自 v2.3.1 起可选（缺失只警告），但“存在时写花”很容易被误认为生成残留：
    多一个空格、少一个引号、复制粘贴串行。这里只校验形态，不强制要求存在。
    """
    lines = src.splitlines()
    first = lines[0].strip() if lines else ""
    if not first or first.startswith("#") or first.startswith("//"):
        return None
    if "important" in first and first != HEADER_CANONICAL:
        return "首行头部不合法：期望 %r，实际 %r" % (HEADER_CANONICAL, first)
    return None


def iter_files():
    for base in SOURCE_DIRS:
        top = os.path.join(ROOT, base)
        if not os.path.isdir(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(".impor"):
                    continue
                p = os.path.join(dirpath, fn)
                yield os.path.relpath(p, ROOT).replace(os.sep, "/")


def main():
    files = sorted(iter_files())
    bad = []
    skipped = 0
    checked = 0
    for rel in files:
        if rel.startswith(EXCLUDE_PREFIX) or rel in EXCLUDE_EXACT:
            skipped += 1
            continue
        p = os.path.join(ROOT, rel)
        try:
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            hp = header_problem(src)
            if hp:
                checked += 1
                bad.append((rel, hp))
                continue
            ok, _ = check_fmt(src)
        except Exception as e:
            bad.append((rel, str(e)))
            continue
        checked += 1
        if not ok:
            bad.append((rel, "未格式化"))
    print("check-fmt 全仓：%d/%d 通过，跳过 %d 个基线/生成文件（不依赖 Git）" %
          (checked - len(bad), checked, skipped))
    for rel, why in bad:
        print("  - %s: %s" % (rel, why))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
