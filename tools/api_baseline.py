# -*- coding: utf-8 -*-
"""API 基线：把已承诺的公开面冻结成 api_baseline.json。

用法：
  python tools/api_baseline.py --init     # 首次生成基线（需提交）
  python tools/api_baseline.py            # 严格比对；任何删除/改义 exit 1
  python tools/api_baseline.py --update --rfc docs/rfc/0001-xxx.md
                                          # 破坏性变更：必须带 RFC 才允许刷新

冻结面：
  - 词法关键字（新增关键字会让用户标识符变保留字，属破坏性）
  - 内建函数（运行时 / 宿主 / 仅解释器三组）
  - CLI 子命令
  - 运行错误码映射、语法错误码集合
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BASELINE = os.path.join(ROOT, "api_baseline.json")


def snapshot():
    from impor.lexer import KEYWORDS
    from impor.builtin_manifest import (
        RUNTIME_BUILTINS, HOST_BUILTINS, INTERPRETER_ONLY_BUILTINS,
    )
    from impor.diagnostics import ERROR_CODES, SYNTAX_CODES
    from impor.cli import SUPPORTED_COMMANDS

    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        version = f.read().strip()
    std_dir = os.path.join(ROOT, "impor", "std")
    std_modules = sorted(
        n[:-6] for n in os.listdir(std_dir)
        if n.endswith(".impor") and n != "__pkg__.impor"
    ) if os.path.isdir(std_dir) else []
    return {
        "schema": 1,
        "version": version,
        "obligation": "冻结公开面；半年内不删除、不改义。变更必须修改本文件并提交 docs/rfc/NNNN-*.md。",
        "keywords": sorted(KEYWORDS),
        "builtins": {
            "runtime": sorted(RUNTIME_BUILTINS),
            "host": sorted(HOST_BUILTINS),
            "interpreter_only": sorted(INTERPRETER_ONLY_BUILTINS),
        },
        "cli_commands": sorted(SUPPORTED_COMMANDS),
        "std_modules": std_modules,
        "error_codes": dict(sorted(ERROR_CODES.items())),
        "syntax_codes": dict(sorted(SYNTAX_CODES.items())),
    }


def diff(old, new):
    problems = []
    for key in ("keywords", "cli_commands"):
        removed = sorted(set(old.get(key, [])) - set(new.get(key, [])))
        added = sorted(set(new.get(key, [])) - set(old.get(key, [])))
        if removed:
            problems.append("%s 被删除：%s" % (key, ", ".join(removed)))
        if added and key == "keywords":
            problems.append("keywords 新增（会把用户标识符变成保留字）：%s" % ", ".join(added))
    for group in ("runtime", "host", "interpreter_only"):
        o = set(old.get("builtins", {}).get(group, []))
        n = set(new.get("builtins", {}).get(group, []))
        removed = sorted(o - n)
        if removed:
            problems.append("builtins.%s 被删除：%s" % (group, ", ".join(removed)))
    removed = sorted(set(old.get("std_modules", [])) - set(new.get("std_modules", [])))
    if removed:
        problems.append("std_modules 被删除：%s" % ", ".join(removed))
    for key in ("error_codes", "syntax_codes"):
        o = old.get(key, {})
        n = new.get(key, {})
        for name, code in o.items():
            if name not in n:
                problems.append("%s 删除 %s（%s）" % (key, name, code))
            elif n[name] != code:
                problems.append("%s 改义 %s：%s -> %s" % (key, name, code, n[name]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="首次写基线")
    ap.add_argument("--update", action="store_true", help="刷新基线（必须带 --rfc）")
    ap.add_argument("--rfc", default="", help="破坏性变更对应的 RFC 文件")
    args = ap.parse_args()
    cur = snapshot()
    if args.init:
        if os.path.exists(BASELINE):
            print("基线已存在；如确需刷新请用 --update --rfc <文件>")
            return 1
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("已生成 api_baseline.json：%d 关键字 / %d 内建 / %d 错误码" % (
            len(cur["keywords"]),
            sum(len(v) for v in cur["builtins"].values()),
            len(cur["error_codes"]) + len(cur["syntax_codes"]),
        ))
        return 0
    if not os.path.exists(BASELINE):
        print("缺少 api_baseline.json；先运行 --init")
        return 1
    with open(BASELINE, encoding="utf-8") as f:
        old = json.load(f)
    problems = diff(old, cur)
    if not problems:
        if args.update:
            if not args.rfc:
                print("--update 必须同时提供 --rfc docs/rfc/NNNN-*.md")
                return 1
            rfc_path = args.rfc if os.path.isabs(args.rfc) else os.path.join(ROOT, args.rfc)
            if not os.path.exists(rfc_path):
                print("RFC 不存在：" + rfc_path)
                return 1
            cur["rfc"] = os.path.relpath(rfc_path, ROOT).replace("\\", "/")
            with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print("基线已刷新（无破坏性差异，新增项已登记）：" + cur["rfc"])
            return 0
        print("API 基线一致：%d 关键字 / %d 内建 / %d 模块 / %d 错误码" % (
            len(cur["keywords"]),
            sum(len(v) for v in cur["builtins"].values()),
            len(cur.get("std_modules", [])),
            len(cur["error_codes"]) + len(cur["syntax_codes"]),
        ))
        return 0
    print("检测到破坏性 API 变更：")
    for p in problems:
        print("  - " + p)
    if not args.update:
        print("拒绝。若要变更：写 docs/rfc/NNNN-*.md，再运行 --update --rfc <文件>。")
        return 1
    if not args.rfc:
        print("--update 必须同时提供 --rfc docs/rfc/NNNN-*.md")
        return 1
    rfc_path = args.rfc if os.path.isabs(args.rfc) else os.path.join(ROOT, args.rfc)
    if not os.path.exists(rfc_path):
        print("RFC 不存在：" + rfc_path)
        return 1
    cur["rfc"] = os.path.relpath(rfc_path, ROOT).replace("\\", "/")
    with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("基线已按 RFC 更新：" + cur["rfc"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
