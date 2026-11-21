# -*- coding: utf-8 -*-
"""执行 docs/claims.yaml 里的全部 auto 承诺，并生成 docs/claims.md。

用法：
    python tools/verify_claims.py
    python tools/verify_claims.py --json

设计原则：
- 只有 status=auto 的承诺才计入「自动兑现率」；manual 项单独列，不冒充已验证。
- 任一 auto 失败：exit 1，CI 红。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YAML_PATH = os.path.join(ROOT, "docs", "claims.yaml")
MD_PATH = os.path.join(ROOT, "docs", "claims.md")
README_PATH = os.path.join(ROOT, "README.md")
PY = sys.executable

try:
    import yaml
except ImportError:
    print("需要 PyYAML：python -m pip install pyyaml")
    raise SystemExit(2)


def build_id():
    """发布包内没有 .git；用发布时写入的 IMPOR_BUILD_ID，避免 check 依赖 Git。"""
    return os.environ.get("IMPOR_BUILD_ID", "package")


def release_metrics():
    """首屏数字只从发布包内文件计算，保证另一台机器也能复算。"""
    version = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    with open(os.path.join(ROOT, "tools", "api_baseline.json"), encoding="utf-8") as f:
        base = json.load(f)
    keywords = len(base.get("keywords", []))
    builtins = sum(len(v) for v in base.get("builtins", {}).values())
    modules = len(base.get("std_modules", []))
    errors = len(base.get("error_codes", [])) + len(base.get("syntax_codes", []))
    fmt_ok = fmt_total = 0
    p = subprocess.run(
        [PY, os.path.join(ROOT, "tools", "check_fmt_all.py")],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    m = re.search(r"(\d+)/(\d+) 通过", p.stdout + p.stderr)
    if m:
        fmt_ok, fmt_total = int(m.group(1)), int(m.group(2))
    return {
        "version": version, "keywords": keywords, "builtins": builtins,
        "modules": modules, "errors": errors, "fmt_ok": fmt_ok,
        "fmt_total": fmt_total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="只校验 README / docs/claims.md 是否与当前结果同步，不写文件")
    args = ap.parse_args()

    with open(YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    claims = data.get("claims", [])

    results = []
    auto_total = 0
    auto_pass = 0
    for c in claims:
        cid = str(c.get("id", ""))
        status = c.get("status", "manual")
        row = {
            "id": cid,
            "claim": c.get("claim", ""),
            "status": status,
            "ok": None,
            "detail": "",
        }
        if status == "auto":
            auto_total += 1
            check = str(c.get("check", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]+", check):
                row["ok"] = False
                row["detail"] = "check id 非法：" + check
            else:
                p = subprocess.run(
                    [PY, os.path.join(ROOT, "tools", "verify_release.py"), "--check", check],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300,
                )
                row["ok"] = p.returncode == 0
                out = (p.stdout + p.stderr).strip().splitlines()
                row["detail"] = out[-1] if out else ""
                if row["ok"]:
                    auto_pass += 1
            row["check"] = check
        else:
            row["how_to_verify"] = c.get("how_to_verify", "")
            row["ok"] = None
        results.append(row)

    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = build_id()
    metrics = release_metrics()
    manual_total = sum(1 for r in results if r["status"] != "auto")
    rate = (100.0 * auto_pass / auto_total) if auto_total else 0.0

    if args.json:
        print(json.dumps({
            "date": today, "commit": commit, "auto_total": auto_total,
            "auto_pass": auto_pass, "manual_total": manual_total,
            "results": results,
        }, ensure_ascii=False))
    else:
        for r in results:
            if r["status"] == "auto":
                print(("OK  " if r["ok"] else "FAIL") + "  " + r["id"])
            else:
                print("MANUAL  " + r["id"])
        print("\n自动验证 %d/%d（%.0f%%）；人工验收 %d 项" % (
            auto_pass, auto_total, rate, manual_total))

    lines = [
        "# 承诺验收表",
        "",
        "> 本表由 `python tools/verify_claims.py` 生成，不要手改。",
        "> 每条承诺对应一个可执行 check；auto 项失败会让 CI 变红。",
        "",
        "- 生成时间：%s" % today,
        "- 构建标识：`%s`" % commit,
        "- **当前构建：%s · 保留字 %d · 内建 %d · 标准库 %d · 错误码 %d · 全仓 .impor %d/%d**" % (
            metrics["version"], metrics["keywords"], metrics["builtins"],
            metrics["modules"], metrics["errors"], metrics["fmt_ok"], metrics["fmt_total"]),
        "- **自动验证：%d/%d（%.0f%%）**" % (auto_pass, auto_total, rate),
        "- 人工验收：%d 项（未完成前不计入兑现率）" % manual_total,
        "",
        "| 结果 | 承诺 | 状态 | 验证方式 |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r["status"] == "auto":
            mark = "✅" if r["ok"] else "❌"
            how = "`verify_release.py --check %s`" % r.get("check", "")
        else:
            mark = "⏳"
            how = r.get("how_to_verify", "人工实测")
        lines.append("| %s | %s | %s | %s |" % (
            mark, r["claim"].replace("|", "\\|"), r["status"], how))
    lines += [
        "",
        "## 怎么复现",
        "",
        "```bash",
        "python tools/verify_claims.py",
        "python tools/verify_release.py",
        "```",
        "",
        "新增承诺时：先在 `tools/verify_release.py` 加 check，再在 `docs/claims.yaml` 加条目；",
        "只写文案不加验证的承诺不算数。",
        "",
    ]
    md_text = "\n".join(lines)
    block = readme_block(auto_pass, auto_total, manual_total, rate, metrics)
    if args.check:
        drift = []
        try:
            old_md = open(MD_PATH, encoding="utf-8").read()
        except FileNotFoundError:
            old_md = ""
        if _strip_volatile(old_md) != _strip_volatile(md_text):
            drift.append("docs/claims.md")
        try:
            readme = open(README_PATH, encoding="utf-8").read()
        except FileNotFoundError:
            readme = ""
        expected, n = re.subn(r"<!-- claims:start -->.*?<!-- claims:end -->",
                              block, readme, flags=re.S)
        if n != 1 or expected != readme:
            drift.append("README.md")
        if drift:
            print("claims 未同步：%s；请运行 python tools/verify_claims.py"
                  % "、".join(drift))
            return 1
        print("claims 已同步：README 与 docs/claims.md 均为脚本直出")
        return 0 if auto_pass == auto_total else 1

    with open(MD_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(md_text)

    sync_readme(auto_pass, auto_total, manual_total, rate)

    return 0 if auto_pass == auto_total else 1


def _strip_volatile(text):
    """忽略生成时间 / 提交行，避免 --check 因时间戳误报。"""
    return "\n".join(x for x in text.splitlines()
                     if not x.startswith("- 生成时间：") and not x.startswith("- 构建标识：`"))


def readme_block(auto_pass, auto_total, manual_total, rate, metrics):
    return (
        "<!-- claims:start -->\n"
        "**承诺兑现率：自动验证 %d/%d（%.0f%%），另有 %d 项人工验收待测。** "
        "每条承诺都能执行复现，明细见 [`docs/claims.md`](docs/claims.md)，"
        "源清单见 [`docs/claims.yaml`](docs/claims.yaml)。"
        "CI 会跑 `python tools/verify_claims.py --check`，任一条失败或数字漂移即红。\n\n"
        "**当前构建（由本脚本从发布包内文件计算）：版本 %s · 保留字 %d · 内建 %d · "
        "标准库模块 %d · 错误码 %d · 全仓 `.impor` %d/%d 通过。**\n"
        "<!-- claims:end -->" % (
            auto_pass, auto_total, rate, manual_total,
            metrics["version"], metrics["keywords"], metrics["builtins"],
            metrics["modules"], metrics["errors"], metrics["fmt_ok"],
            metrics["fmt_total"])
    )


def sync_readme(auto_pass, auto_total, manual_total, rate):
    """把 README 的兑现率与构建数字写进标记块；数字只由本脚本产生，禁止手写。"""
    block = readme_block(auto_pass, auto_total, manual_total, rate, release_metrics())
    try:
        text = open(README_PATH, encoding="utf-8").read()
    except FileNotFoundError:
        return
    new, n = re.subn(r"<!-- claims:start -->.*?<!-- claims:end -->", block, text,
                     flags=re.S)
    if n != 1:
        raise SystemExit("README 缺少唯一的 claims 标记块，拒绝写入手写兑现率")
    with open(README_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)


if __name__ == "__main__":
    raise SystemExit(main())
