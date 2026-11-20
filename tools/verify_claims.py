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
PY = sys.executable

try:
    import yaml
except ImportError:
    print("需要 PyYAML：python -m pip install pyyaml")
    raise SystemExit(2)


def git_commit():
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        )
        return p.stdout.strip() if p.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
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
    commit = git_commit()
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
        "- 提交：`%s`" % commit,
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
    with open(MD_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    return 0 if auto_pass == auto_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
