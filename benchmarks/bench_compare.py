# -*- coding: utf-8 -*-
"""v5.6 基准：Impor 解释器 vs Python 3.11 的墙钟时间（含启动）。"""
import os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

CASES = {
    "loop_1e6": (
        "var i = 0\nvar s = 0\nwhile i < 1000000 {\n    s = s + i\n    i = i + 1\n}\nprint(s)\n",
        "i=0\ns=0\nwhile i<1000000:\n    s+=i\n    i+=1\nprint(s)\n",
    ),
    "str_2000": (
        'var s = ""\nvar i = 0\nwhile i < 2000 {\n    s = s + "x"\n    i = i + 1\n}\nprint(len(s))\n',
        's=""\ni=0\nwhile i<2000:\n    s+="x"\n    i+=1\nprint(len(s))\n',
    ),
    "fib_22": (
        "func fib(n) {\n    if n < 2 {\n        return n\n    }\n    return fib(n - 1) + fib(n - 2)\n}\nprint(fib(22))\n",
        "def fib(n):\n    if n<2:\n        return n\n    return fib(n-1)+fib(n-2)\nprint(fib(22))\n",
    ),
}


def run(cmd, cwd):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
    return (time.perf_counter() - t0) * 1000, p.returncode, (p.stdout or "").strip().splitlines()[-1:] or [""]


def main():
    tmp = tempfile.mkdtemp(prefix="impor_bench_")
    rows = []
    for name, (impor_src, py_src) in CASES.items():
        ip = os.path.join(tmp, name + ".impor")
        pp = os.path.join(tmp, name + ".py")
        open(ip, "w", encoding="utf-8").write(impor_src)
        open(pp, "w", encoding="utf-8").write(py_src)
        best_i = min(run([PY, "-X", "utf8", os.path.join(ROOT, "Impor.py"), "run", ip], ROOT)[0] for _ in range(2))
        best_p = min(run([PY, "-X", "utf8", pp], ROOT)[0] for _ in range(2))
        rows.append((name, best_i, best_p, best_p / best_i if best_i else 0))
        print("%-12s Impor=%7.1f ms  Python=%7.1f ms  比值=%.2fx" % (name, best_i, best_p, best_p / best_i))
    print("\n说明：包含解释器/进程启动，适合看小工具体感，不等价于纯计算吞吐。")


if __name__ == "__main__":
    main()
