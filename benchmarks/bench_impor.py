#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Impor (I++) 性能基准（v2.6）。

对比两种执行后端的性能：
  A. 树遍历解释器（Impor Interpreter，教学/默认后端）
  B. 编译产物（impor compile → Python，转译后端）

用法（项目根目录）：
    python benchmarks/bench_impor.py             # 全量
    python benchmarks/bench_impor.py --quick     # 快速（迭代数减半，CI 用）
    python benchmarks/bench_impor.py --json out.json
    python benchmarks/bench_impor.py --only arith,list_ops

输出：每项负载的解释器耗时、编译产物耗时与加速比。退出码恒为 0。
防性能退化：将本脚本纳入 CI（--quick）观察趋势；人工对比不同版本运行
本脚本的报告即可，无需固定阈值。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from impor.interpreter import Interpreter, parse_source
from impor.transpiler import transpile

# 负载定义：name -> (迭代规模 N, Impor 源码模板)。模板用 {N} 占位。
BENCHMARKS = {
    "arith": (150000, """var s = 0
var i = 0
while i < {N}
    set s = s + i * 2 - 1
    set i = i + 1
print(s)
"""),
    "func_calls": (40000, """func add(a, b)
    return a + b
var s = 0
var i = 0
while i < {N}
    set s = add(s, i)
    set i = i + 1
print(s)
"""),
    "list_ops": (40000, """var lst = []
var i = 0
while i < {N}
    arrappend(lst, i)
    set i = i + 1
print(len(lst))
"""),
    "str_ops": (20000, """var s = ""
var i = 0
while i < {N}
    set s = s + to_str(i)
    set i = i + 1
print(len(s))
"""),
    "dict_ops": (30000, """var d = {{}}
var i = 0
while i < {N}
    d[i] = i * 2
    set i = i + 1
print(d[1])
"""),
    "fib": (18, """func fib(n)
    if n < 2
        return n
    return fib(n - 1) + fib(n - 2)
print(fib({N}))
"""),
}


def _ms(sec):
    return sec * 1000.0


def bench_interpreter(src):
    """进程内跑树遍历解释器，返回毫秒。"""
    ast = parse_source(src)
    interp = Interpreter(log_cb=lambda t: None)
    t0 = time.perf_counter()
    interp.run(ast)
    return _ms(time.perf_counter() - t0)


def bench_compiled(src):
    """转译并在子进程运行（含 Python 启动开销），返回毫秒。"""
    ast = parse_source(src)
    py_code = transpile(ast)
    with tempfile.TemporaryDirectory() as td:
        pyf = os.path.join(td, "prog.py")
        with open(pyf, "w", encoding="utf-8") as f:
            f.write(py_code)
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        t0 = time.perf_counter()
        r = subprocess.run([sys.executable, pyf], capture_output=True, text=True,
                           env=env, cwd=PROJECT_ROOT, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"编译产物运行失败（{r.stderr[:200]}）")
        return _ms(time.perf_counter() - t0)


def bench_compiled_inproc(src):
    """转译并在本进程 exec（剔除 Python 启动开销，公平对比后端纯性能）。"""
    ast = parse_source(src)
    py_code = transpile(ast)
    g = {"__name__": "__impor_bench__", "__file__": "<impor-bench>"}
    t0 = time.perf_counter()
    exec(compile(py_code, "<impor-bench>", "exec"), g)
    return _ms(time.perf_counter() - t0)


def main():
    ap = argparse.ArgumentParser(description="Impor 性能基准")
    ap.add_argument("--quick", action="store_true", help="快速模式（迭代数减半）")
    ap.add_argument("--json", metavar="FILE", help="输出 JSON 报告")
    ap.add_argument("--only", metavar="NAMES", help="只跑指定负载，逗号分隔（如 arith,list_ops）")
    ap.add_argument("--inproc", action="store_true",
                    help="编译产物在本进程 exec 测量（剔除 Python 启动开销，公平对比后端纯性能）")
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(",")] if args.only else list(BENCHMARKS)
    report = {"quick": args.quick, "inproc": args.inproc, "benchmarks": []}
    rows = []

    print("Impor (I++) 性能基准" + ("（快速模式）" if args.quick else ""))
    print(f"{'负载':<12}{'解释器(ms)':>14}{'编译产物(ms)':>16}{'加速比':>10}")
    print("-" * 56)
    for name in names:
        if name not in BENCHMARKS:
            print(f"未知负载: {name}", file=sys.stderr)
            sys.exit(1)
        n, tmpl = BENCHMARKS[name]
        if args.quick:
            n = max(1, n // 2)
        src = tmpl.format(N=n)
        try:
            a = bench_interpreter(src)
            b = (bench_compiled_inproc(src) if args.inproc else bench_compiled(src))
        except Exception as e:
            print(f"{name:<12} 负载失败: {e}", file=sys.stderr)
            sys.exit(1)
        ratio = a / b if b > 0 else float("inf")
        rows.append((name, a, b, ratio))
        print(f"{name:<12}{a:>14.1f}{b:>16.1f}{ratio:>10.2f}x")

    total_a = sum(r[1] for r in rows)
    total_b = sum(r[2] for r in rows)
    print("-" * 56)
    print(f"{'合计':<12}{total_a:>14.1f}{total_b:>16.1f}{total_a / total_b:>10.2f}x")

    if args.json:
        for name, a, b, ratio in rows:
            report["benchmarks"].append({"name": name, "interpreter_ms": a,
                                         "compiled_ms": b, "speedup": ratio})
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告已写入: {args.json}")


if __name__ == "__main__":
    main()
