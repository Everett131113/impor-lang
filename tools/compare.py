#!/usr/bin/env python3
"""tools/compare.py — 同一份 JSON 任务的多语言真跑对照。

任务：读 case.json（1000 个整数），求和，打印 `sum = 500500`。
对每个语言测：产物体积、冷启动中位数（30 次）、峰值常驻内存、目标机依赖。
所有数字都来自本机实测；缺失的运行时会在表里标 `未安装`，不抄博客数字。

用法（在仓库根）：
    python tools/compare.py
    python tools/compare.py --runs 30 --json compare.json --markdown docs/compare.md
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE_DIR = os.path.join(ROOT, "benchmarks", "compare")
IMPOR_PY = os.path.join(ROOT, "Impor.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")
EXPECTED = "sum = 500500"


def find_bin(name, *candidates):
    p = shutil.which(name)
    if p:
        return p
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def run(cmd, cwd=CASE_DIR, timeout=30):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def measure(cmd, runs, cwd=CASE_DIR):
    """返回 (median_ms, peak_rss_kb, stdout)。冷启动取 30 次中位数。"""
    import psutil

    # 预热，避免首次读盘/杀软扫描污染样本
    for _ in range(3):
        run(cmd, cwd=cwd)

    times = []
    peaks = []
    out = ""
    for _ in range(runs):
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        peak = 0
        try:
            proc = psutil.Process(p.pid)
            while p.poll() is None:
                try:
                    peak = max(peak, proc.memory_info().rss)
                except psutil.Error:
                    break
                time.sleep(0.002)
        except psutil.Error:
            pass
        out, _err = p.communicate(timeout=30)
        times.append((time.perf_counter() - t0) * 1000.0)
        if peak:
            peaks.append(peak / 1024.0)
    return round(statistics.median(times), 2), round(max(peaks), 0) if peaks else None, out.strip()


def build_impor():
    exe = os.path.join(CASE_DIR, "case_impor.exe")
    r = run([sys.executable, IMPOR_PY, "build", "case.impor", "-o", exe], cwd=CASE_DIR)
    if r.returncode != 0:
        raise RuntimeError("Impor build 失败：\n" + r.stdout + r.stderr)
    return exe


def build_go(go):
    exe = os.path.join(CASE_DIR, "case_go.exe")
    env = dict(os.environ, GO111MODULE="off", CGO_ENABLED="0")
    r = subprocess.run([go, "build", "-ldflags=-s -w", "-o", exe, "case.go"],
                       cwd=CASE_DIR, capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("go build 失败：\n" + r.stdout + r.stderr)
    return exe


def build_c(tcc):
    exe = os.path.join(CASE_DIR, "case_tcc.exe")
    r = run([tcc, "-O2", "-o", exe, "case.c"])
    if r.returncode != 0:
        raise RuntimeError("tcc 编译失败：\n" + r.stdout + r.stderr)
    return exe


def add_row(rows, lang, artifact_size, artifact_desc, cmd, runs, deps, note=""):
    median, rss, out = measure(cmd, runs)
    ok = out == EXPECTED
    rows.append({
        "language": lang,
        "artifact_bytes": artifact_size,
        "artifact": artifact_desc,
        "startup_ms_median": median,
        "rss_kb_peak": rss,
        "deps": deps,
        "command": " ".join(cmd),
        "_cmd": cmd,
        "output": out,
        "output_ok": ok,
        "note": note,
    })
    print("%-10s %10s B  %8s ms  %8s KB  %s" % (
        lang, artifact_size if artifact_size else "n/a", median,
        rss if rss is not None else "n/a", "OK" if ok else "FAIL: " + out))


def human_size(n):
    if n is None:
        return "n/a"
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1024.0 / 1024.0)


def display_command(cmd):
    """Markdown 里不暴露本机绝对路径，只保留可识别的可执行文件名。"""
    return " ".join(os.path.basename(x) if os.path.isabs(x) else x for x in cmd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--json", default=os.path.join(ROOT, "compare.json"))
    ap.add_argument("--markdown", default=os.path.join(ROOT, "docs", "compare.md"))
    ap.add_argument("--node", default="")
    ap.add_argument("--lua", default="")
    ap.add_argument("--go", default="")
    ap.add_argument("--tcc", default="")
    ap.add_argument("--skip-python-package", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(CASE_DIR):
        raise SystemExit("找不到 benchmarks/compare")
    rows = []
    missing = []

    # 1) Impor（C 后端独立 exe）
    impor_exe = build_impor()
    add_row(rows, "Impor 3.11", os.path.getsize(impor_exe), "case_impor.exe（内置 tcc，32 位）",
            [impor_exe], args.runs, "无（不依赖 Python / VC 运行库）")

    # 2) TCC-C
    tcc = args.tcc or find_bin("tcc", TCC)
    if tcc:
        c_exe = build_c(tcc)
        add_row(rows, "TCC-C", os.path.getsize(c_exe), "case_tcc.exe（tcc -O2）",
                [c_exe], args.runs, "无")
    else:
        missing.append("tcc")

    # 3) Lua
    lua = args.lua or find_bin("lua", r"C:\Program Files\Lua\5.4\lua.exe",
                               r"C:\Program Files (x86)\Lua\5.4\lua.exe")
    if lua:
        size = os.path.getsize(lua)
        dll = os.path.join(os.path.dirname(lua), "lua54.dll")
        if os.path.exists(dll):
            size += os.path.getsize(dll)
        add_row(rows, "Lua 5.4", size, "lua.exe + lua54.dll（若同目录存在）",
                [lua, "case.lua"], args.runs, "需要 lua.exe / lua54.dll", "脚本不打包")
    else:
        missing.append("lua")

    # 4) Python（解释器；若已打包 onefile 则用打包产物）
    py_pack = os.path.join(CASE_DIR, "dist", "case_python.exe")
    if os.path.exists(py_pack) and not args.skip_python_package:
        add_row(rows, "Python 3.11", os.path.getsize(py_pack), "PyInstaller onefile",
                [py_pack], args.runs, "无（打包运行时）", "onefile")
    else:
        py = sys.executable
        size = os.path.getsize(py)
        for d in ("python311.dll", "vcruntime140.dll"):
            p = os.path.join(os.path.dirname(py), d)
            if os.path.exists(p):
                size += os.path.getsize(p)
        add_row(rows, "Python 3.11", size, "python.exe + python311.dll + vcruntime140.dll",
                [py, "case.py"], args.runs, "需要解释器与 Python 运行时", "未打包")

    # 5) Go
    go = args.go or find_bin("go", r"C:\Program Files\Go\bin\go.exe", r"C:\Go\bin\go.exe")
    if go:
        go_exe = build_go(go)
        add_row(rows, "Go 1.23", os.path.getsize(go_exe), "case_go.exe（-ldflags=-s -w）",
                [go_exe], args.runs, "无")
    else:
        missing.append("go")

    # 6) Node
    node = args.node or find_bin("node", r"C:\Program Files\nodejs\node.exe",
                                 r"C:\Program Files (x86)\nodejs\node.exe")
    if node:
        add_row(rows, "Node 22", os.path.getsize(node), "node.exe（不含脚本）",
                [node, "case.js"], args.runs, "需要 node.exe（约 80 MB）", "脚本不打包")
    else:
        missing.append("node")

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": sys.platform,
        "case": "benchmarks/compare/case.json",
        "expected": EXPECTED,
        "runs_per_language": args.runs,
        "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        "missing": missing,
    }
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines = [
        "# 多语言对照（本机实测）",
        "",
        "同一份 `benchmarks/compare/case.json`（1000 个整数求和），各语言独立进程运行，",
        "冷启动取 %d 次中位数；峰值内存来自 psutil 采样。命令都在 `benchmarks/compare/` 下执行。" % args.runs,
        "",
        "| 语言 | 产物体积 | 冷启动中位数 | 峰值内存 | 目标机依赖 | 结果 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for r in rows:
        lines.append("| %s | %s | %.2f ms | %s | %s | %s |" % (
            r["language"], human_size(r["artifact_bytes"]), r["startup_ms_median"],
            ("%.0f KB" % r["rss_kb_peak"]) if r["rss_kb_peak"] else "n/a",
            r["deps"], "OK" if r["output_ok"] else "FAIL"))
    lines += ["", "## 复现命令", "", "```text"]
    for r in rows:
        lines.append("%-10s %s" % (r["language"], display_command(r["_cmd"])))
    lines += ["```", "", "完整 JSON：`compare.json`。"]
    if missing:
        lines += ["", "未安装、未测量的运行时：`" + "`, `".join(missing) + "`。"]
    lines += ["", "产物清单：" + "；".join(r["artifact"] for r in rows)]

    os.makedirs(os.path.dirname(args.markdown), exist_ok=True)
    with open(args.markdown, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n写入 %s 与 %s" % (args.json, args.markdown))
    if any(not r["output_ok"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
