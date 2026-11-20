# -*- coding: utf-8 -*-
"""错误可行动率审计：20 条真实 CLI 探针，逐条检查 5 项。

验收：每条都必须同时有
  1) 位置（第 N 行）  2) 为什么：  3) 修复：  4) 稳定码 E1001/E2001...  5) docs/errors.md#E....
任何一条缺项，脚本 exit 1，可直接进 CI。
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")

PROBES = [
    ("async-def", "async def f() {\n    print(1)\n}\n"),
    ("await", "def g() {\n    return 7\n}\nprint(await g())\n"),
    ("defer-expr", "defer print(\"d\")\n"),
    ("bad-token", "var x = )\n"),
    ("expr-missing", "var x =\n"),
    ("unclosed", "if true {\n    print(1)\n"),
    ("bad-signature", "func f( {\n}\n"),
    ("lvalue", "1 = x\n"),
    ("default-param", "func f(x = 1) {\n    return x\n}\n"),
    ("zero-div", "print(10 / 0)\n"),
    ("type", "print(1 + \"a\")\n"),
    ("index", "var a = [1, 2]\nprint(a[5])\n"),
    ("key", "var d = {\"a\": 1}\nprint(d[\"b\"])\n"),
    ("value", "print(json_decode(\"{bad\"))\n"),
    ("attribute", "var x = 1\nprint(x.nope)\n"),
    ("assert", "expect(1, 2, \"one\")\n"),
    ("name", "print(nope)\n"),
    ("const", "const A = 1\nA = 2\n"),
    ("import", "import no_such_mod\n"),
    ("recursion", "func f() {\n    return f()\n}\nf()\n"),
]

CODE_RE = re.compile(r"(E1\d{3}|E2\d{3})")
ANCHOR_RE = re.compile(r"docs/errors\.md#(E1\d{3}|E2\d{3})")


def run_probe(name, source):
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, name + ".impor")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source)
        proc = subprocess.run(
            [sys.executable, IMPOR_PY, "run", path],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", timeout=30,
        )
        return proc.returncode, proc.stdout


def check(name, source):
    rc, out = run_probe(name, source)
    missing = []
    if not re.search(r"第\d+行", out):
        missing.append("位置")
    if "为什么：" not in out:
        missing.append("为什么")
    if "修复：" not in out:
        missing.append("修复")
    code = CODE_RE.search(out)
    if not code:
        missing.append("错误码")
    anchor = ANCHOR_RE.search(out)
    if not anchor:
        missing.append("文档锚点")
    if code and anchor and code.group(1) != anchor.group(1):
        missing.append("码与锚点不一致")
    return rc, code.group(1) if code else "-", missing, out


def main():
    rows = []
    failures = 0
    for name, src in PROBES:
        rc, code, missing, out = check(name, src)
        ok = not missing
        rows.append((name, code, "OK" if ok else "FAIL", ",".join(missing)))
        if not ok:
            failures += 1
            print("---- %s ----" % name)
            print(out)
    for name, code, status, missing in rows:
        print("%-16s %-6s %-5s %s" % (name, code, status, missing))
    print("\n合规 %d/%d（目标 20/20）" % (len(PROBES) - failures, len(PROBES)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
