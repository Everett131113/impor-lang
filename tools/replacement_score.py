#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""替代记分牌：用可复跑的数字回答“Impor 能不能替代 Python 做脚本分发”。

对每个用例同时跑四条路径，记录：是否成功、产物体积、构建耗时、启动+运行耗时、输出是否与解释器一致。

    python tools/replacement_score.py            # 生成 docs/替代记分牌.md
    python tools/replacement_score.py --json     # 只打印 JSON（给 CI 用）
    python tools/replacement_score.py --all      # 扫描 examples/ 全量

设计原则：数字必须能一条命令复跑，不许手抄。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMPOR = ROOT / "Impor.py"

# 代表“真实脚本”而不是“语言特性”的用例；顺序按读者关心的能力排
CASES = [
    ("hello.impor", "最小脚本"),
    ("demo_03_strings.impor", "字符串处理"),
    ("demo_05_loops.impor", "循环 + 打印"),
    ("demo_12_bubble_sort.impor", "数组 / 排序"),
    ("demo_22_dict_stats.impor", "字典统计"),
    ("demo_27_enum.impor", "枚举"),
    ("demo_28_inheritance.impor", "类 / 继承"),
    ("demo_29_exceptions.impor", "自定义异常"),
    ("demo_39_json.impor", "JSON 序列化"),
    ("fib.impor", "递归 / 性能"),
    ("stdlib.impor", "标准库 import（已知缺口）"),
]


def _run(args, cwd=None, timeout=120):
    """跑一条命令，返回 (ok, stdout, stderr, 秒)。"""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            args, cwd=str(cwd or ROOT), capture_output=True, timeout=timeout,
            text=True, encoding="utf-8", errors="replace",
        )
        dt = time.perf_counter() - t0
        return p.returncode == 0, p.stdout, p.stderr, dt
    except subprocess.TimeoutExpired:
        return False, "", f"超时 {timeout}s", time.perf_counter() - t0


def measure(case, workdir):
    src = ROOT / "examples" / case
    out = {"case": case, "exists": src.exists()}
    if not out["exists"]:
        out["reason"] = "用例文件不存在"
        return out

    # 1) 解释器 = 语义基准
    ok, stdout, err, dt = _run([sys.executable, str(IMPOR), "run", str(src)])
    out["interp_ok"], out["interp_stdout"], out["interp_ms"] = ok, stdout, round(dt * 1000, 1)
    out["source_bytes"] = src.stat().st_size
    if not ok:
        out["reason"] = "解释器跑不通，跳过"
        out["interp_err"] = err.strip().splitlines()[-1:] and err.strip().splitlines()[-1] or ""

    # 2) C 后端（默认 auto）
    exe = workdir / f"{src.stem}.exe"
    ok_b, _, err_b, dt_b = _run([sys.executable, str(IMPOR), "build", str(src), "-o", str(exe)])
    out["build_ok"], out["build_ms"] = ok_b, round(dt_b * 1000, 1)
    if ok_b and exe.exists():
        out["exe_bytes"] = exe.stat().st_size
        # 第一次跑会被杀软扫描新产物，冷启动噪声大；跑两次取最快，口径里写明
        _run([str(exe)], timeout=120)
        ok_r, stdout_r, err_r, dt_r = _run([str(exe)], timeout=120)
        out["exe_run_ok"], out["exe_run_ms"] = ok_r, round(dt_r * 1000, 1)
        out["exe_stdout"] = stdout_r
        out["exe_err"] = (err_r.strip().splitlines() or [""])[-1]
    else:
        out["exe_bytes"] = None
        out["build_err"] = (err_b.strip().splitlines() or [""])[-1]

    # 3) Python 转译
    py = workdir / f"{src.stem}.py"
    ok_c, _, err_c, dt_c = _run([sys.executable, str(IMPOR), "compile", str(src), "-o", str(py)])
    out["trans_ok"], out["trans_ms"] = ok_c, round(dt_c * 1000, 1)
    if ok_c and py.exists():
        _run([sys.executable, str(py)])  # 预热：消掉磁盘/杀软的首读抖动
        ok_p, stdout_p, err_p, dt_p = _run([sys.executable, str(py)])
        out["py_run_ok"], out["py_run_ms"] = ok_p, round(dt_p * 1000, 1)
        out["py_stdout"] = stdout_p
    else:
        out["trans_err"] = (err_c.strip().splitlines() or [""])[-1]

    # 4) 契约：C 后端一旦构建成功，stdout 必须与解释器逐字节相同
    if ok_b and out.get("exe_run_ok"):
        out["exe_matches"] = out["exe_stdout"] == out["interp_stdout"]
    else:
        out["exe_matches"] = None
    if ok_c and out.get("py_run_ok"):
        out["py_matches"] = out["py_stdout"] == out["interp_stdout"]
    else:
        out["py_matches"] = None
    return out


def kb(n):
    if not n:
        return "—"
    return f"{n / 1024:.1f} KB"


def render(rows, mode):
    n = len(rows)
    ok_build = sum(1 for r in rows if r.get("build_ok"))
    ok_trans = sum(1 for r in rows if r.get("trans_ok"))
    match = [r for r in rows if r.get("exe_matches") is True]
    bad = [r for r in rows if r.get("exe_matches") is False]
    sizes = [r["exe_bytes"] for r in rows if r.get("exe_bytes")]
    interp_t = [r["interp_ms"] for r in rows if r.get("interp_ok")]
    exe_t = [r["exe_run_ms"] for r in rows if r.get("exe_run_ms")]

    lines = []
    lines.append("# Impor 替代记分牌（自动生成，请勿手改）")
    lines.append("")
    lines.append("由 `python tools/replacement_score.py` 实测生成：每个用例同时跑解释器、Python 转译、C 后端三条路径，")
    lines.append("记体积、耗时、输出一致性。**替代不是口号，这张表就是证据**——数字可一条命令复跑。")
    lines.append("")
    lines.append("## 口径")
    lines.append("")
    lines.append("- **解释器是语义基准**；C 后端只要构建成功，stdout 必须与解释器逐字节相同，不同即为缺陷。")
    lines.append("- 耗时是“进程启动 + 运行”的墙钟时间：产物与转译产物都跑两遍取最快（第一遍会被杀软扫描新文件干扰），只用于看数量级，不做微基准。")
    lines.append("- 体积是 C 后端产出的独立 exe 大小（默认 auto：数值快路径，失败回落完整值模型）。")
    lines.append("")
    lines.append("## 记分表")
    lines.append("")
    lines.append("| 用例 | 能力 | 源码 | 解释器 | 转译 py | C 后端 exe | exe 体积 | exe 启动+运行 | 输出一致 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        case = r["case"]
        if not r.get("exists"):
            lines.append(f"| `{case}` | — | — | — | — | — | — | — | 缺失 |")
            continue
        interp = f"{r['interp_ms']} ms" if r.get("interp_ok") else "失败"
        trans = f"{r['trans_ms']} ms" if r.get("trans_ok") else "拒绝"
        build = f"{r['build_ms']} ms" if r.get("build_ok") else "拒绝"
        size = kb(r.get("exe_bytes"))
        exe_run = f"{r['exe_run_ms']} ms" if r.get("exe_run_ms") else "—"
        m = r.get("exe_matches")
        flag = "✅" if m else ("❌ DIFF" if m is False else "—")
        lines.append(
            f"| `{case}` | {r.get('label', '')} | {r.get('source_bytes', 0)} B | {interp} | {trans} | {build} | {size} | {exe_run} | {flag} |"
        )
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 用例数：{n}")
    lines.append(f"- C 后端构建成功：{ok_build}/{n}")
    lines.append(f"- Python 转译成功：{ok_trans}/{n}")
    lines.append(f"- 输出与解释器逐字节一致：{len(match)} 条；不一致：{len(bad)} 条")
    if sizes:
        lines.append(f"- exe 体积：中位 {kb(sorted(sizes)[len(sizes) // 2])}，最大 {kb(max(sizes))}，最小 {kb(min(sizes))}")
    if interp_t and exe_t:
        lines.append(f"- 启动+运行：解释器中位 {sorted(interp_t)[len(interp_t) // 2]} ms，C 后端产物中位 {sorted(exe_t)[len(exe_t) // 2]} ms")
    lines.append("")
    lines.append("## 被拒绝的用例与原因")
    lines.append("")
    refused = [r for r in rows if not r.get("build_ok")]
    if not refused:
        lines.append("无。")
    for r in refused:
        lines.append(f"- `{r['case']}`：{r.get('build_err') or r.get('reason') or '未知'}")
    lines.append("")
    lines.append(f"生成模式：{mode}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="生成替代记分牌")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    ap.add_argument("--all", action="store_true", help="扫描 examples/ 全量")
    ap.add_argument("--out", default=str(ROOT / "docs" / "替代记分牌.md"))
    a = ap.parse_args()

    if a.all:
        cases = [(p.name, "") for p in sorted((ROOT / "examples").glob("*.impor"))]
    else:
        cases = CASES

    rows = []
    with tempfile.TemporaryDirectory(prefix="impor_score_") as td:
        wd = Path(td)
        for case, label in cases:
            r = measure(case, wd)
            r["label"] = label
            rows.append(r)

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    text = render(rows, "全量" if a.all else f"精选 {len(CASES)} 例")
    Path(a.out).write_text(text, encoding="utf-8")
    print(f"已写入 {a.out}")
    ok = sum(1 for r in rows if r.get("build_ok"))
    diff = sum(1 for r in rows if r.get("exe_matches") is False)
    print(f"用例 {len(rows)}，C 后端构建 {ok}，输出不一致 {diff}")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
