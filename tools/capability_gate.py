#!/usr/bin/env python
"""语义能力矩阵门禁（CAPABILITIES gate）。

三个后端跑同一组探针，把每个能力的真实状态钉成一份矩阵：

  ok           跑通，且输出与 EXPECT 完全一致
  unsupported  后端明确拒绝（编译期报错 / WasmUnsupported），不是静默降级
  fail         跑起来了但结果不对，或崩溃、超时 —— 最危险的一类，必须为 0

用法：
  python tools/capability_gate.py --record     重新采集，写 docs/CAPABILITIES.json + docs/CAPABILITIES.md
  python tools/capability_gate.py --check      与已入库矩阵比对，任何一格变化即 exit 1
  python tools/capability_gate.py --show       只打印当前矩阵，不写文件

为什么要有它：4.7.x 反复出现「文档说支持、代码悄悄拒绝、或者接受了但算错」
三件事，本脚本把三者区分开，任何一格回退都会在 CI 里挂掉。
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

ROOT = Path(__file__).resolve().parents[1]
CAPS_DIR = ROOT / "bench" / "caps"
DOCS_DIR = ROOT / "docs"
MATRIX_JSON = DOCS_DIR / "CAPABILITIES.json"
MATRIX_MD = DOCS_DIR / "CAPABILITIES.md"

PY = sys.executable

# 能力名（md 里给人看的那一列），键为探针文件名
CASE_INFO = {
    "01_int.impor": ("整数 int64 全宽", "9223372036854775807 级别数值不截断，% 符号跟随除数"),
    "02_float.impor": ("浮点", "double 加法与乘法"),
    "03_str.impor": ("字符串", "拼接 / len / 重复"),
    "04_list.impor": ("列表", "字面量 / 长度 / 下标读取"),
    "05_dict.impor": ("字典", "字面量 / 键查 / 条目数"),
    "06_class.impor": ("类", "构造 / 字段写入 / 方法调用"),
    "07_closure.impor": ("闭包", "捕获外层变量 + nonlocal 写回"),
    "08_generic.impor": ("泛型", "类型参数 + 多实例单态化"),
    "09_exception.impor": ("异常", "自定义异常 / raise / catch"),
    "10_builtin.impor": ("内建转换", "int() / str()"),
    "11_have.impor": ("have 类型判定", "x have int / float / str / list / dict / 类名"),
}

REFUSE_HINTS = ("不支持", "拒绝", "unsupported", "无法编译", "not supported", "超出",
                "backenderror")

NODE_CANDIDATES = [
    os.environ.get("IMPOR_NODE", ""),
    r"C:\Users\41482\AppData\Local\Doubao\User Data\sandbox_runtime\bases\fa694f517e5995233c80a9a8db5212c2\node\node.exe",
]


def _node() -> str:
    for c in NODE_CANDIDATES:
        if c and Path(c).is_file():
            return c
    raise SystemExit("找不到 node：设置 IMPOR_NODE 指向 node.exe")


def _expect(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("# EXPECT:"):
            out.append(s[len("# EXPECT:"):].strip())
    return out


def _is_refusal(text: str) -> bool:
    low = text.lower()
    return any(h.lower() in low for h in REFUSE_HINTS)


def _judge(out: str, expect: list[str]) -> tuple[str, str]:
    got = [l.rstrip() for l in out.replace("\r\n", "\n").split("\n")]
    while got and got[-1] == "":
        got.pop()
    if got == expect:
        return "ok", ""
    return "fail", "got=%r expect=%r" % (got, expect)


def probe_interp(path: Path, expect: list[str]) -> tuple[str, str]:
    p = subprocess.run(
        [PY, "-m", "impor", "run", "--no-native", str(path)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    if p.returncode != 0:
        return "fail", "rc=%d %s" % (p.returncode, (p.stderr or p.stdout)[-400:])
    return _judge(p.stdout, expect)


def probe_native(path: Path, expect: list[str], work: Path) -> tuple[str, str]:
    exe = work / (path.stem + ".exe")
    b = subprocess.run(
        [PY, "-m", "impor", "build", str(path), "-o", str(exe), "--quiet"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )
    if b.returncode != 0:
        msg = (b.stdout + b.stderr).strip()
        return ("unsupported" if _is_refusal(msg) else "fail"), "build rc=%d %s" % (b.returncode, msg[-400:])
    if not exe.is_file():
        return "fail", "build rc=0 但没有产物"
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return "fail", "运行超时"
    if r.returncode != 0:
        return "fail", "exe rc=%d %s" % (r.returncode, (r.stderr or r.stdout)[-300:])
    return _judge(r.stdout, expect)


def probe_wasm(path: Path, expect: list[str], work: Path) -> tuple[str, str]:
    sys.path.insert(0, str(ROOT))
    from impor.interpreter import parse_source, strip_header
    from impor.wasm import WasmUnsupported, wasm_transpile
    src = path.read_text(encoding="utf-8")
    try:
        data, meta = wasm_transpile(parse_source(strip_header(src), path.name))
    except WasmUnsupported as e:
        return "unsupported", str(e)
    except Exception as e:  # 解析失败也算后端拒绝，但要标清楚
        msg = "%s: %s" % (type(e).__name__, e)
        return ("unsupported" if _is_refusal(msg) else "fail"), msg
    w = work / (path.stem + ".wasm")
    w.write_bytes(data)
    r = subprocess.run(
        [_node(), str(ROOT / "tools" / "wasm_run.js"), str(w), "\n".join(expect)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    if r.returncode != 0:
        return "fail", "node rc=%d out=%s" % (r.returncode, (r.stdout + r.stderr).strip()[-400:])
    return "ok", "%d 字节" % len(data)


def probe_vm(path: Path, expect: list[str]) -> tuple[str, str]:
    """自有字节码后端（NVM）：python -m impor vm run。"""
    p = subprocess.run(
        [PY, "-m", "impor", "vm", "run", str(path)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        return ("unsupported" if _is_refusal(msg) else "fail"), "rc=%d %s" % (p.returncode, msg[-400:])
    return _judge(p.stdout, expect)


def probe_opt(path: Path, expect: list[str]) -> tuple[str, str]:
    """优化编译器后端（OPTC）：python -m impor opt run。"""
    p = subprocess.run(
        [PY, "-m", "impor", "opt", "run", str(path)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        return ("unsupported" if _is_refusal(msg) else "fail"), "rc=%d %s" % (p.returncode, msg[-400:])
    return _judge(p.stdout, expect)


def probe_native64(path: Path, expect: list[str], work: Path) -> tuple[str, str]:
    """纯自足原生 x64 AOT（Native64）：impor native build，自写机器码+PE，零外部工具链。"""
    exe = work / (path.stem + "_n64.exe")
    b = subprocess.run(
        [PY, "-m", "impor", "native", "build", str(path), "-o", str(exe)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )
    if b.returncode != 0:
        msg = (b.stdout + b.stderr).strip()
        return ("unsupported" if _is_refusal(msg) else "fail"), "build rc=%d %s" % (b.returncode, msg[-400:])
    if not exe.is_file():
        return "fail", "build rc=0 但没有产物"
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        return "fail", "运行超时"
    if r.returncode != 0:
        return "fail", "exe rc=%d %s" % (r.returncode, (r.stderr or r.stdout)[-300:])
    return _judge(r.stdout, expect)


def collect() -> dict:
    cases = {}
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for f in sorted(CAPS_DIR.glob("*.impor")):
            expect = _expect(f)
            if not expect:
                raise SystemExit("%s 缺少 # EXPECT: 注释" % f.name)
            t0 = time.time()
            interp = probe_interp(f, expect)
            native = probe_native(f, expect, work)
            wasm = probe_wasm(f, expect, work)
            vm = probe_vm(f, expect)
            opt = probe_opt(f, expect)
            native64 = probe_native64(f, expect, work)
            cases[f.name] = {
                "interp": interp[0], "native": native[0], "wasm": wasm[0],
                "vm": vm[0], "opt": opt[0], "native64": native64[0],
                "expect": expect,
                "note": {k: v[1] for k, v in
                         (("interp", interp), ("native", native), ("wasm", wasm),
                          ("vm", vm), ("opt", opt), ("native64", native64)) if v[1]},
                "seconds": round(time.time() - t0, 2),
            }
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "cases": cases,
    }


ICON = {"ok": "支持", "unsupported": "拒绝（明确）", "fail": "**错误**", "skip": "跳过"}


def render_md(m: dict) -> str:
    lines = [
        "# Impor 语义能力矩阵",
        "",
        "由 `tools/capability_gate.py --record` 自动生成，**不要手改**。",
        "每个格子都是真跑出来的：`支持` = 跑通且输出与期望一致；`拒绝（明确）` = 后端在编译期就说不行；`错误` = 跑起来了但结果不对（必须为 0 才允许发版）。",
        "",
        "采集时间：%s · Python %s · 探针：`bench/caps/*.impor`" % (m["generated"], m["python"]),
        "",
        "| # | 能力 | 说明 | 解释器 | C 后端（原生 exe） | WASM 后端 | NVM 自有字节码 | OPTC 优化编译器 | Native64 自足机器码 |",
        "|---|------|------|--------|-------------------|-----------|----------------|----------------|--------------------|",
    ]
    for i, (name, info) in enumerate(sorted(CASE_INFO.items()), 1):
        c = m["cases"].get(name)
        if not c:
            continue
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, info[0], info[1], ICON[c["interp"]], ICON[c["native"]], ICON[c["wasm"]],
            ICON[c.get("vm", "skip")], ICON[c.get("opt", "skip")],
            ICON[c.get("native64", "skip")]))
    fails = [n for n, c in m["cases"].items()
             if "fail" in (c["interp"], c["native"], c["wasm"],
                           c.get("vm", "skip"), c.get("opt", "skip"),
                           c.get("native64", "skip"))]
    lines += ["", "## 门禁结论", ""]
    if fails:
        lines.append("**有 %d 个用例出现「错误」格**，发版前必须清零：%s" % (len(fails), ", ".join(fails)))
    else:
        lines.append("全部用例无「错误」格：六后端的边界和文档一致。")
    lines += [
        "",
        "## 复现",
        "",
        "```",
        "python tools/capability_gate.py --show      # 打印当前实测矩阵（不落盘）",
        "python tools/capability_gate.py --check     # CI：任何一格与 CAPABILITIES.json 不一致即失败",
        "python tools/capability_gate.py --record    # 能力真的变了：重采一份并提交 diff",
        "```",
        "",
        "单个探针也可以手工跑：",
        "",
        "```",
        "python -m impor run --no-native bench/caps/06_class.impor   # 解释器",
        "python -m impor build bench/caps/06_class.impor -o t.exe    # C 后端（成功/拒绝都能看到原因）",
        "python -m impor wasm  bench/caps/06_class.impor -o t.wasm   # WASM 后端",
        "```",
    ]
    return "\n".join(lines) + "\n"


def diff(old: dict, new: dict) -> list[str]:
    out = []
    old_c, new_c = old.get("cases", {}), new.get("cases", {})
    for name in sorted(set(old_c) | set(new_c)):
        if name not in old_c:
            out.append("+ 新增用例 %s" % name)
            continue
        if name not in new_c:
            out.append("- 用例消失 %s" % name)
            continue
        for be in ("interp", "native", "wasm", "vm", "opt", "native64"):
            a, b = old_c[name].get(be), new_c[name].get(be)
            if a != b:
                out.append("~ %s [%s] %s → %s" % (name, be, a, b))
        if old_c[name].get("expect") != new_c[name].get("expect"):
            out.append("~ %s [expect] 期望输出被改动" % name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true", help="重新采集并写入 docs/")
    g.add_argument("--check", action="store_true", help="与已入库矩阵比对")
    g.add_argument("--show", action="store_true", help="只打印，不落盘")
    args = ap.parse_args()

    m = collect()

    for name, c in sorted(m["cases"].items()):
        print("%-18s 解释器=%-11s C=%-11s WASM=%-11s NVM=%-11s OPTC=%-11s N64=%-11s %5.1fs"
              % (name, c["interp"], c["native"], c["wasm"], c.get("vm", "skip"),
                 c.get("opt", "skip"), c.get("native64", "skip"), c["seconds"]))
        for be, note in c["note"].items():
            if c.get(be) != "ok":
                print("    %-6s %s" % (be, note.replace("\n", " ")[:180]))

    if args.show:
        return 0

    if args.record:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        MATRIX_JSON.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MATRIX_MD.write_text(render_md(m), encoding="utf-8")
        print("\n已写 %s 与 %s" % (MATRIX_JSON.relative_to(ROOT), MATRIX_MD.relative_to(ROOT)))
        fails = [n for n, c in m["cases"].items()
                 if "fail" in (c["interp"], c["native"], c["wasm"], c.get("vm", "skip"),
                               c.get("opt", "skip"), c.get("native64", "skip"))]
        if fails:
            print("警告：仍有「错误」格：%s（矩阵已记录实况，修复后重跑 --record）" % ", ".join(fails))
        return 0

    if not MATRIX_JSON.is_file():
        print("缺少 %s：先跑 --record" % MATRIX_JSON)
        return 2
    old = json.loads(MATRIX_JSON.read_text(encoding="utf-8"))
    d = diff(old, m)
    if d:
        print("\n能力矩阵发生 %d 处变化（拒绝静默回退）：" % len(d))
        for line in d:
            print("  " + line)
        print("\n确认变化是预期内的，再跑 --record 更新矩阵并提交 diff。")
        return 1
    print("\n能力矩阵与入库版本完全一致（%d 个用例 × 6 个后端）。" % len(m["cases"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
