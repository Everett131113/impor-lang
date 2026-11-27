# -*- coding: utf-8 -*-
"""Impor 性能门禁：基准集固定、基线落盘、退化自动拦截。

用法：
    python tools/bench_gate.py --record          # 采集基线（默认 3 次取最小）
    python tools/bench_gate.py --check           # 对比基线，退化超阈值则 exit 1
    python tools/bench_gate.py --check --runs 1  # 快速自查
阈值：默认 30%（本机是笔记本级机器，噪声大；CI 上可收紧到 15%）。

设计要点：
- 用例只有「已验证可跑、结果确定」的 4 个；字符串/容器/GC/并发因语言层语法
  未在本套件里验证，明确不纳入，避免制造假失败（见 CAPABILITIES.md）。
- 两个口径：interp（run --no-native，树遍历解释器）与 native（impor build 的 C 产物）。
- 基线文件 bench/baseline.json 进版本库，任何优化 pass 都要先过这一关。
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.path.join(ROOT, "bench", "suite")
BIN = os.path.join(SUITE, "bin")
BASELINE = os.path.join(ROOT, "bench", "baseline.json")
PY = sys.executable
CLI = os.path.join(ROOT, "Impor.py")

CASES = ["fib25", "sum2m", "mod1m", "nested1m"]
EXPECT = {
    "fib25": "75025",
    "sum2m": "1999999000000",
    "mod1m": "2999997",
    "nested1m": "1000000",
}


def run_cmd(cmd, timeout=300, cwd=None):
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd or ROOT)
    dt = time.perf_counter() - t0
    out = (p.stdout or b"").decode("utf-8", "ignore").strip()
    return dt, p.returncode, out, (p.stderr or b"").decode("utf-8", "ignore").strip()


def measure(cmd, runs):
    best = None
    last = None
    for _ in range(runs):
        dt, rc, out, err = run_cmd(cmd)
        if rc != 0:
            return None, rc, out, err
        last = (rc, out, err)
        best = dt if best is None else min(best, dt)
    return best, last[0], last[1], last[2]


def native_exe(name):
    os.makedirs(BIN, exist_ok=True)
    path = os.path.join(BIN, name + ".exe")
    src = os.path.join(SUITE, name + ".impor")
    if os.path.exists(path) and os.path.getmtime(path) > os.path.getmtime(src):
        return path
    dt, rc, out, err = run_cmd([PY, CLI, "build", src, "-o", path, "--quiet"])
    if rc != 0:
        raise SystemExit("构建 %s 失败：%s" % (name, err.strip() or out.strip()))
    return path


def collect(runs):
    res = {}
    for name in CASES:
        dt, rc, out, err = measure([PY, CLI, "run", "--no-native",
                                     os.path.join(SUITE, name + ".impor")], runs)
        if dt is None:
            raise SystemExit("%s 解释器运行失败：%s" % (name, (err or out).strip()[:300]))
        if out.splitlines()[-1].strip() != EXPECT[name]:
            raise SystemExit("%s 输出不符：期望 %s 实际 %r" % (name, EXPECT[name], out[-80:]))
        res["interp:" + name] = round(dt, 4)

        exe = native_exe(name)
        dt, rc, out, err = measure([exe], max(runs, 5))
        if dt is None:
            raise SystemExit("%s 原生产物运行失败：%s" % (name, (err or out).strip()[:300]))
        if out.splitlines()[-1].strip() != EXPECT[name]:
            raise SystemExit("%s 原生输出不符：期望 %s 实际 %r" % (name, EXPECT[name], out[-80:]))
        res["native:" + name] = round(dt, 4)
    return res


def cc_id():
    """native 口径的编译器身份。没装 x86_64 gcc 时 impor build 会退回 32 位 tcc，
    产物慢一大截（实测 fib25 0.0224 s → 0.0354 s），那不是代码退化，是环境不同。
    所以基线把编译器写进去，check 时身份不符就把 native 口径判为 SKIP 而不是 REGRESS。"""
    gcc = shutil.which("gcc")
    if not gcc:
        return "tcc"
    try:
        out = subprocess.run([gcc, "--version"], capture_output=True, text=True, timeout=15)
        first = (out.stdout or "").splitlines()[0].strip()
    except Exception:
        first = gcc
    return "gcc:" + first[:60]


def main():
    runs = 3
    if "--runs" in sys.argv:
        runs = int(sys.argv[sys.argv.index("--runs") + 1])
    limit = 0.30
    if "--limit" in sys.argv:
        limit = float(sys.argv[sys.argv.index("--limit") + 1])

    cur = collect(runs)
    cc = cc_id()

    if "--record" in sys.argv:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        payload = {"python": sys.version.split()[0], "cc": cc,
                   "cases": cur, "limit": limit}
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        for k, v in cur.items():
            print("%-18s %8.4f s" % (k, v))
        print("基线已写入 %s（编译器 %s）" % (BASELINE, cc))
        return 0

    if not os.path.exists(BASELINE):
        print("缺少基线：先跑 --record")
        return 2
    full = json.load(open(BASELINE, "r", encoding="utf-8"))
    base = full["cases"]
    base_cc = full.get("cc")
    cc_same = base_cc is None or base_cc == cc
    if not cc_same:
        print("注意：基线编译器 = %s，当前 = %s" % (base_cc, cc))
        print("      编译器不同，native 口径不可比，本轮判 SKIP（不是通过，也不是退化）。\n")

    bad = []
    skipped = []
    print("%-18s %9s %9s %8s %s" % ("metric", "baseline", "now", "ratio", "verdict"))
    for k in sorted(cur):
        b = base.get(k)
        if b is None:
            print("%-18s %9s %9.4f %8s %s" % (k, "-", cur[k], "-", "无基线"))
            continue
        ratio = cur[k] / b if b else 0.0
        verdict = "OK"
        if not cc_same and k.startswith("native:"):
            verdict = "SKIP(cc)"
            skipped.append(k)
        elif ratio > 1 + limit:
            verdict = "REGRESS"
            bad.append((k, b, cur[k], ratio))
        print("%-18s %9.4f %9.4f %8.2f %s" % (k, b, cur[k], ratio, verdict))

    if bad:
        print("\n性能退化（阈值 +%.0f%%）：" % (limit * 100))
        for k, b, c, r in bad:
            print("  %-18s %.4f -> %.4f (%.2fx)" % (k, b, c, r))
        return 1
    if skipped:
        print("\ninterp 口径全部在阈值内；native 的 %d 个口径因编译器不同已跳过。" % len(skipped))
        return 0
    print("\n全部在阈值内（+%.0f%%）" % (limit * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
