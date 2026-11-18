# -*- coding: utf-8 -*-
"""v3.7.3 原生数值快路径基准：CPython 3.11 vs `impor build --fast`。

只测纯整数、计算密集的循环负载；不含解析、构建和进程启动之外的噪声。
递归函数调用不在快路径的主收益区，单列为诊断项，不冒充 20x。
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

WORKLOADS = {
    "arith_50m": (
        50000000,
        """var i = 0
var s = 0
while i < {N} {{
    s = s + i * 2 - 1
    i = i + 1
}}
print(s)
""",
        """i = 0
s = 0
while i < {N}:
    s = s + i * 2 - 1
    i = i + 1
print(s)
""",
    ),
    "nested_10k": (
        10000,
        """var i = 0
var s = 0
while i < {N} {{
    var j = 0
    while j < {N} {{
        s = s + 1
        j = j + 1
    }}
    i = i + 1
}}
print(s)
""",
        """i = 0
s = 0
while i < {N}:
    j = 0
    while j < {N}:
        s = s + 1
        j = j + 1
    i = i + 1
print(s)
""",
    ),
    "builtin_20m": (
        20000000,
        """var i = 0
var s = 0
while i < {N} {{
    s = s + min(i, 1000000) + max(0, i - 5000000) + abs(i - 7000000)
    i = i + 1
}}
print(s)
""",
        """i = 0
s = 0
while i < {N}:
    s = s + min(i, 1000000) + max(0, i - 5000000) + abs(i - 7000000)
    i = i + 1
print(s)
""",
    ),
    "mod_20m_diag": (
        20000000,
        """var i = 0
var s = 0
while i < {N} {{
    s = s + i % 7
    i = i + 1
}}
print(s)
""",
        """i = 0
s = 0
while i < {N}:
    s = s + i % 7
    i = i + 1
print(s)
""",
    ),
    "fib_32_diag": (
        32,
        """def fib(n) {{
    if n < 2 {{ return n }}
    return fib(n - 1) + fib(n - 2)
}}
print(fib({N}))
""",
        """def fib(n):
    if n < 2: return n
    return fib(n-1)+fib(n-2)
print(fib({N}))
""",
    ),
}

GATE = {"arith_50m", "nested_10k", "builtin_20m"}


def run_once(cmd):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180)
    ms = (time.perf_counter() - t0) * 1000.0
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[:300])
    return ms, (p.stdout or "").strip()


def timed(cmd, tries):
    vals, outs = [], set()
    for _ in range(tries):
        ms, out = run_once(cmd)
        vals.append(ms)
        outs.add(out)
    if len(outs) != 1:
        raise RuntimeError("输出不一致：%s" % (outs,))
    return statistics.median(vals), outs.pop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="缩小规模，只做功能验证")
    ap.add_argument("--json", metavar="FILE")
    ap.add_argument("--md", metavar="FILE")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="impor_native20_")
    rows = []
    print("CPython=%s" % sys.version.split()[0])
    print("%-14s %12s %12s %8s" % ("workload", "CPython(ms)", "Impor-fast(ms)", "speedup"))
    for name, (n, impor_tmpl, py_tmpl) in WORKLOADS.items():
        if args.quick:
            n = 1000 if name != "fib_32_diag" else 20
        isrc = impor_tmpl.format(N=n)
        psrc = py_tmpl.format(N=n)
        ip = os.path.join(tmp, name + ".impor")
        pp = os.path.join(tmp, name + ".py")
        exe = os.path.join(tmp, name + ".exe")
        with open(ip, "w", encoding="utf-8") as f:
            f.write(isrc)
        with open(pp, "w", encoding="utf-8") as f:
            f.write(psrc)
        b = subprocess.run([PY, "-B", os.path.join(ROOT, "Impor.py"), "build", ip,
                            "-o", exe, "--fast", "--no-cache"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        if b.returncode != 0:
            raise RuntimeError("构建失败：%s\n%s" % (name, (b.stderr or b.stdout)[:500]))
        py_ms, py_out = timed([PY, "-B", pp], 2 if not args.quick else 1)
        im_ms, im_out = timed([exe], 3 if not args.quick else 1)
        if py_out != im_out:
            raise RuntimeError("%s 输出不一致：CPython=%r Impor=%r" % (name, py_out, im_out))
        ratio = py_ms / im_ms if im_ms else 0.0
        rows.append({"name": name, "n": n, "cpython_ms": py_ms,
                     "impor_fast_ms": im_ms, "speedup": ratio})
        print("%-14s %12.1f %12.1f %7.2fx" % (name, py_ms, im_ms, ratio))

    gate = all(r["speedup"] >= 20.0 for r in rows if r["name"] in GATE)
    print("\n20x 门禁（%s）：%s" % (", ".join(sorted(GATE)), "PASS" if gate else "FAIL"))
    print("说明：门禁只覆盖纯整数循环负载；mod/fib 是诊断项，不参与门禁。")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"python": sys.version, "rows": rows, "gate": gate},
                      f, ensure_ascii=False, indent=2)
    if args.md:
        lines = ["# Impor 3.7.3 原生数值快路径基准", "",
                 "环境：Windows / CPython %s / 随包 tcc，`impor build --fast`。"
                 % sys.version.split()[0], "",
                 "方法：同一份源码，CPython 直接运行，Impor 先构建为独立 exe；"
                 "构建不计时，取多次墙钟中位数。", "",
                 "| 用例 | CPython | Impor 快路径 | 倍率 |", "|---|---:|---:|---:|"]
        for r in rows:
            lines.append("| %s (N=%s) | %.1f ms | %.1f ms | %.2fx |"
                         % (r["name"], r["n"], r["cpython_ms"],
                            r["impor_fast_ms"], r["speedup"]))
        lines += ["", "20x 门禁：**%s**。" % ("PASS" if gate else "FAIL"),
                  "", "注：`mod_20m_diag` 与 `fib_32_diag` 是诊断项，不属于 20x 门禁；"
                  "取模与递归调用的倍率明显低于循环算术，不能据此宣称“所有脚本 20x”。"]
        with open(args.md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if not args.quick and not gate:
        sys.exit(1)


if __name__ == "__main__":
    main()
