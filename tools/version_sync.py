# -*- coding: utf-8 -*-
"""版本单一真源工具（4.7.1）。

问题：同一套东西的版本号散落在五处以上——仓库根 `VERSION`、打包脚本里的
`VER` 常量、`impor/ir.py` 的 `IR_VERSION`、`vscode-impor/package.json`、
自举编译器源码的版本行与横幅、README/claims 的正文数字、以及**目录名/备份名/
便携包文件名**。改一处忘一处，就会出现「报告说 4.7，包名写 4.5，目录名写 4.0」。

本工具把 `VERSION` 定为唯一真源，其余全部由它派生或校验：

    py -3.11 tools/version_sync.py --check      # 只校验（CI 用），有漂移则退出 1
    py -3.11 tools/version_sync.py --apply      # 把 VERSION 写进所有派生物
    py -3.11 tools/version_sync.py --set 4.8.0  # 改 VERSION，再 apply

它**不**会去改已经存在的目录名（那会断开桌面快捷方式与 .bat 里的绝对路径）。
目录名由 `--print-names` 给出「应当叫什么」，交给安装脚本决定。
"""

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEMVER = r"(\d+(?:\.\d+){1,2})"

# ---- 派生物清单：路径 + 正则 + 替换模板（{v} 处填版本号）----
RULES = [
    ("impor/ir.py",
     r'IR_VERSION\s*=\s*"%s"' % SEMVER,
     'IR_VERSION = "{v}"'),
    ("vscode-impor/package.json",
     r'"version"\s*:\s*"%s"' % SEMVER,
     '"version": "{v}"'),
    ("tools/api_baseline.json",
     r'"version"\s*:\s*"%s"' % SEMVER,
     '"version": "{v}"'),
    ("tools/ir_coverage_baseline.json",
     r'"updated"\s*:\s*"%s"' % SEMVER,
     '"updated": "{v}"'),
    ("tools/verify_ir_coverage.py",
     r'"updated"\s*:\s*"%s"' % SEMVER,
     '"updated": "{v}"'),
    ("tools/pack_release.py",
     r'VER\s*=\s*"%s"' % SEMVER,
     'VER = "{v}"'),
    ("bootstrap/imporc.impor",
     r"# 版本：v%s" % SEMVER,
     "# 版本：v{v}"),
    ("bootstrap/imporc.impor",
     r"(print\(\"imporc \(Impor 自举编译器\) v)%s" % SEMVER,
     r"\g<1>{v}"),
    ("README.md",
     r"版本：\*\*%s\*\*" % SEMVER,
     "版本：**{v}**"),
    ("README.md",
     r"(由本脚本从发布包内文件计算）：版本 )%s" % SEMVER,
     r"\g<1>{v}"),
    ("docs/claims.md",
     r"当前构建：%s" % SEMVER,
     "当前构建：{v}"),
]

# 允许这些文件不存在（可选派生物）
OPTIONAL = {"vscode-impor/package.json", "docs/claims.md", "CHANGELOG.md"}


def read_version(root=ROOT):
    path = os.path.join(root, "VERSION")
    with io.open(path, "r", encoding="utf-8") as f:
        v = f.read().strip()
    if not re.match(r"^\d+(\.\d+){1,2}$", v):
        raise SystemExit("VERSION 内容不是 x.y.z：%r" % v)
    return v


def _read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def scan(root=ROOT, version=None):
    """返回 [(rel, regex, 命中数, 是否已一致)]。"""
    v = version or read_version(root)
    rows = []
    for rel, pattern, repl in RULES:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            rows.append((rel, pattern, 0, rel in OPTIONAL))
            continue
        text = _read(path)
        hits = re.findall(pattern, text)
        ok = bool(hits) and all((h[-1] if isinstance(h, tuple) else h) == v for h in hits)
        rows.append((rel, pattern, len(hits), ok))
    return rows


def apply(root=ROOT, version=None):
    v = version or read_version(root)
    changed = []
    for rel, pattern, tmpl in RULES:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        text = _read(path)
        new, n = re.subn(pattern, tmpl.replace("{v}", v), text)
        if n and new != text:
            _write(path, new)
            changed.append((rel, n))
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Impor 版本单一真源")
    ap.add_argument("--root", default=ROOT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只校验，漂移则退出码 1")
    g.add_argument("--apply", action="store_true", help="把 VERSION 写进所有派生物")
    g.add_argument("--set", metavar="X.Y.Z", help="改 VERSION 并 apply")
    ap.add_argument("--print-names", action="store_true",
                    help="打印安装目录/备份目录/发布包应有的名字")
    args = ap.parse_args(argv)

    if args.print_names:
        v = read_version(args.root)
        print("安装目录    : Impor %s" % v)
        print("备份目录    : Impor %s (备份-<YYYYMMDD>)" % v)
        print("发布包      : Impor-%s-Windows-x64.zip" % v)
        print("同步说明    : 同步说明-%s.md" % v)
        return 0

    if args.set:
        if not re.match(r"^\d+(\.\d+){1,2}$", args.set):
            print("--set 需要 x.y.z，收到 %r" % args.set)
            return 2
        _write(os.path.join(args.root, "VERSION"), args.set + "\n")
        print("VERSION → %s" % args.set)

    v = read_version(args.root)
    if args.apply or args.set:
        changed = apply(args.root, v)
        if changed:
            for rel, n in changed:
                print("  %-34s %d 处 → %s" % (rel, n, v))
        else:
            print("  无需改动，全部已是 %s" % v)
        return 0

    # 默认 / --check
    bad = 0
    rows = scan(args.root, v)
    for rel, pattern, hits, ok in rows:
        if hits == 0:
            print("  %-34s 未命中（%s）" % (rel, "可选" if rel in OPTIONAL else "缺失规则"))
            if rel not in OPTIONAL:
                bad += 1
            continue
        if not ok:
            path = os.path.join(args.root, rel.replace("/", os.sep))
            vals = []
            if os.path.isfile(path):
                vals = [("".join(h) if isinstance(h, tuple) else h)
                        for h in re.findall(pattern, _read(path))]
            print("  %-34s %d 处与 VERSION 不一致：%s" % (rel, hits, ", ".join(vals)))
            bad += 1
    if bad:
        print("版本漂移：%d 个文件。跑 --apply 修正。" % bad)
        return 1
    print("版本一致：%s（%d 个派生点）" % (v, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
