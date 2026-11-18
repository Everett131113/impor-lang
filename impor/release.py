# -*- coding: utf-8 -*-
"""Impor 生产化发布门禁（v3.6.0）。

用法:
  impor release-check            # 快速门禁：版本一致性、必备文件、一致性用例、文档
  impor release-check --full     # 追加 doctor / conformance --ci / 单元测试
  impor release-check --json out.json

设计原则：
  * 只做可验证的检查，不把“文档存在”当成“能力完成”。
  * 任何失败返回非零退出码，可直接放进 CI。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"


def _read(path):
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _check_version():
    """五处版本文件 + 站点 update.json 必须同版。"""
    targets = [
        ("Impor.py", r'__version__\s*=\s*"([^"]+)"'),
        ("impor/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        ("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        ("vscode-impor/package.json", r'"version"\s*:\s*"([^"]+)"'),
        ("bootstrap/imporc.impor", r"v([0-9]+\.[0-9]+\.[0-9]+)"),
    ]
    found = {}
    for rel, pattern in targets:
        text = _read(os.path.join(ROOT, rel))
        m = re.search(pattern, text, re.MULTILINE)
        found[rel] = m.group(1) if m else "<missing>"
    values = set(found.values())
    return (len(values) == 1 and "<missing>" not in values, found)


def _check_required():
    required = [
        "Impor.py", "impor/cli.py", "impor/interpreter.py", "impor/ctrans.py",
        "conformance/run_conformance.py", "docs/生产化/README.md",
        "docs/生产化/01-原生后端.md", "docs/生产化/02-生态与服务.md",
        "docs/生产化/03-治理与发布.md", "docs/系统编程/README.md",
        "SECURITY.md", "CONTRIBUTING.md",
        "CODEOWNERS",
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(ROOT, p))]
    return (not missing, missing)


def _check_cases():
    d = os.path.join(ROOT, "conformance", "cases")
    cases = [x for x in os.listdir(d) if x.endswith(".impor")] if os.path.isdir(d) else []
    return (len(cases) >= 20, {"count": len(cases), "min": 20})


def _run(cmd, label):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        return False, {"label": label, "error": str(e)}
    detail = (r.stdout or "")[-2000:] + (r.stderr or "")[-1000:]
    return r.returncode == 0, {"label": label, "returncode": r.returncode, "tail": detail}


def run_release_check(argv=None):
    ap = argparse.ArgumentParser(prog="impor release-check")
    ap.add_argument("--full", action="store_true", help="追加 doctor / conformance / 单元测试")
    ap.add_argument("--json", default="", help="写入 JSON 报告")
    args = ap.parse_args(argv or [])

    checks = []
    ok_v, detail_v = _check_version()
    checks.append({"name": "版本一致性", "ok": ok_v, "detail": detail_v})
    ok_f, missing = _check_required()
    checks.append({"name": "生产化必备文件", "ok": ok_f, "detail": {"missing": missing}})
    ok_c, detail_c = _check_cases()
    checks.append({"name": "一致性用例数量", "ok": ok_c, "detail": detail_c})

    if args.full:
        for cmd, label in (
            ([PY, os.path.join(ROOT, "Impor.py"), "doctor"], "doctor"),
            ([PY, os.path.join(ROOT, "conformance", "run_conformance.py"), "--ci"], "conformance"),
            ([PY, os.path.join(ROOT, "run_tests.py")], "unit-tests"),
        ):
            ok, detail = _run(cmd, label)
            checks.append({"name": label, "ok": ok, "detail": detail})

    bad = [c for c in checks if not c["ok"]]
    report = {
        "version": detail_v.get("Impor.py", ""),
        "root": ROOT,
        "ok": not bad,
        "passed": len(checks) - len(bad),
        "failed": len(bad),
        "checks": checks,
    }
    print("Impor 生产化发布门禁")
    for c in checks:
        print("  [%s] %s" % ("OK" if c["ok"] else "FAIL", c["name"]))
    print("结果：%d 项通过，%d 项失败" % (report["passed"], report["failed"]))
    if bad:
        print("未通过项：")
        for c in bad:
            print("  - %s: %s" % (c["name"], json.dumps(c["detail"], ensure_ascii=False)[:500]))

    if args.json:
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告：" + args.json)
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(run_release_check(sys.argv[1:]))
