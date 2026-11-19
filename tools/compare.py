#!/usr/bin/env python3
"""tools/compare.py — 同一份 JSON 任务的多语言真跑对照。

任务：读 case.json（1000 个整数），求和，打印 `sum = 500500`。
固定输出 6 行；缺运行时填「未测量」，不再让表随机器变短。

默认只写本地文件（docs/compare.local.md、compare.local.json），
只有显式 --publish 才覆盖发布表 docs/compare.md、compare.json。
"""

import argparse
import hashlib
import json
import os
import platform
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
FLOOR_ENV = "IMPOR_COMPARE_FLOOR"
ORDER = ["Impor 3.11", "TCC-C", "Lua 5.4", "Python 3.11", "Go 1.23", "Node 22"]

try:
    import psutil
except ImportError:
    print("缺少 psutil：本脚本用它采样峰值内存。请先运行 `pip install psutil`。", file=sys.stderr)
    raise SystemExit(2)


def resolve_bin(explicit, name, *candidates):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    return find_bin(name, *candidates)


def find_bin(name, *candidates):
    p = shutil.which(name)
    if p:
        return p
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def run(cmd, cwd=CASE_DIR, timeout=30, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=env)


def measure(cmd, runs, cwd=CASE_DIR, floor=False):
    """返回 (total_ms_median, peak_rss_kb, stdout, rc)。

    floor=True 时设置 IMPOR_COMPARE_FLOOR，被测程序走空路径，仅测进程地板；
    此时 stdout 不做内容校验。
    """
    env = dict(os.environ)
    if floor:
        env[FLOOR_ENV] = "1"

    for _ in range(3):
        run(cmd, cwd=cwd, env=env)

    times = []
    peaks = []
    out = ""
    rc = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
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
        rc = p.returncode
        times.append((time.perf_counter() - t0) * 1000.0)
        if peak:
            peaks.append(peak / 1024.0)
    return (round(statistics.median(times), 2),
            round(max(peaks), 0) if peaks else None, out.strip(), rc)


def build_impor(out_dir):
    exe = os.path.join(out_dir, "case_impor.exe")
    r = run([sys.executable, IMPOR_PY, "build", "case.impor", "-o", exe], cwd=CASE_DIR)
    if r.returncode != 0:
        raise RuntimeError("Impor build 失败：\n" + r.stdout + r.stderr)
    return exe


def build_empty_impor(out_dir):
    src = os.path.join(out_dir, "empty.impor")
    with open(src, "w", encoding="utf-8") as f:
        f.write('print("")\n')
    exe = os.path.join(out_dir, "empty_impor.exe")
    r = run([sys.executable, IMPOR_PY, "build", src, "-o", exe], cwd=out_dir)
    if r.returncode != 0:
        raise RuntimeError("Impor 空程序编译失败：\n" + r.stdout + r.stderr)
    return exe


def build_go(go, out_dir):
    exe = os.path.join(out_dir, "case_go.exe")
    env = dict(os.environ, GO111MODULE="off", CGO_ENABLED="0")
    r = subprocess.run([go, "build", "-ldflags=-s -w", "-o", exe, "case.go"],
                       cwd=CASE_DIR, capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("go build 失败：\n" + r.stdout + r.stderr)
    return exe


def build_c(tcc, out_dir):
    exe = os.path.join(out_dir, "case_tcc.exe")
    r = run([tcc, "-O2", "-o", exe, "case.c"])
    if r.returncode != 0:
        raise RuntimeError("tcc 编译失败：\n" + r.stdout + r.stderr)
    return exe


def pyinstaller_module(py):
    r = run([py, "-m", "PyInstaller", "--version"], cwd=ROOT)
    if r.returncode != 0:
        print("缺少 PyInstaller：`pip install pyinstaller` 后重跑，或显式 --python-mode system。",
              file=sys.stderr)
        raise SystemExit(2)
    return r.stdout.strip()


def build_python_onefile(py, out_dir):
    dist = os.path.join(out_dir, "py_dist")
    work = os.path.join(out_dir, "py_build")
    spec = os.path.join(out_dir, "py_spec")
    r = run([py, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
             "--distpath", dist, "--workpath", work, "--specpath", spec,
             os.path.join(CASE_DIR, "case.py")], cwd=CASE_DIR, timeout=300)
    if r.returncode != 0:
        print("PyInstaller onefile 构建失败，不降级：\n" + r.stdout + r.stderr, file=sys.stderr)
        raise SystemExit(2)
    return os.path.join(dist, "case.exe"), dist


def build_python_onedir(py, out_dir):
    dist = os.path.join(out_dir, "py_onedir")
    work = os.path.join(out_dir, "py_build_dir")
    spec = os.path.join(out_dir, "py_spec_dir")
    r = run([py, "-m", "PyInstaller", "--onedir", "--clean", "--noconfirm",
             "--distpath", dist, "--workpath", work, "--specpath", spec,
             os.path.join(CASE_DIR, "case.py")], cwd=CASE_DIR, timeout=300)
    if r.returncode != 0:
        print("PyInstaller onedir 构建失败，不降级：\n" + r.stdout + r.stderr, file=sys.stderr)
        raise SystemExit(2)
    return os.path.join(dist, "case", "case.exe"), os.path.join(dist, "case")


def dir_size(path):
    return sum(os.path.getsize(os.path.join(dp, f))
               for dp, _dn, fn in os.walk(path) for f in fn)


def human_size(n):
    if n is None:
        return "—"
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1024.0 / 1024.0)


def display_command(cmd):
    """仓库内二进制写相对路径；外部运行时写 basename；脚本参数原样保留。"""
    shown = []
    for x in cmd:
        ax = os.path.abspath(x)
        if ax.startswith(ROOT + os.sep):
            shown.append(os.path.relpath(ax, ROOT).replace(os.sep, "/"))
        elif os.path.isabs(x):
            shown.append(os.path.basename(x))
        else:
            shown.append(x)
    return " ".join(shown)


def make_row(language):
    return {
        "language": language,
        "measured": False,
        "artifact_bytes": None,
        "artifact": "—",
        "artifact_kind": "—",
        "mode": "—",
        "total_ms_median": None,
        "startup_ms_median": None,
        "rss_kb_peak": None,
        "deps": "—",
        "command": "—",
        "output": "",
        "output_ok": False,
        "note": "",
    }


def fill(row, artifact_bytes, artifact, kind, mode, cmd, total, floor, rss, deps, out, note=""):
    row.update({
        "measured": True,
        "artifact_bytes": artifact_bytes,
        "artifact": artifact,
        "artifact_kind": kind,
        "mode": mode,
        "total_ms_median": total,
        "startup_ms_median": floor,
        "rss_kb_peak": rss,
        "deps": deps,
        "command": display_command(cmd),
        "output": out,
        "output_ok": out == EXPECTED,
        "note": note,
    })
    print("%-10s %10s B  total %8s ms  floor %8s ms  %8s KB  %s" % (
        row["language"], artifact_bytes, total, floor, rss,
        "OK" if row["output_ok"] else "FAIL: " + out))


def missing_rows_msg(rows):
    return "、".join(r["language"] for r in rows if not r["measured"]) or "无"


def version_of(cmd, pattern=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        text = (r.stdout or r.stderr).strip().splitlines()
        return text[0] if text else "unknown"
    except Exception:
        return "unknown"


def git_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description="多语言真跑对照（固定 6 行）")
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--python-mode", choices=["auto", "onefile", "onedir", "system"],
                    default="auto")
    ap.add_argument("--json", default=None)
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--node", default="")
    ap.add_argument("--lua", default="")
    ap.add_argument("--go", default="")
    ap.add_argument("--tcc", default="")
    ap.add_argument("--publish", action="store_true", help="写入正式 compare.json / docs/compare.md")
    ap.add_argument("--strict", action="store_true", help="任一行未测量或输出错误即 exit 1")
    ap.add_argument("--keep", action="store_true", help="保留临时构建目录")
    ap.add_argument("--skip-python-package", action="store_true", help="旧参数，等价 --python-mode system")
    args = ap.parse_args()

    if args.skip_python_package:
        args.python_mode = "system"
    if args.runs < 10:
        print("警告：--runs %d 样本不足，表头会标注。" % args.runs, file=sys.stderr)
        if args.strict:
            print("--strict 拒绝少于 10 次的样本。", file=sys.stderr)
            raise SystemExit(2)

    formal_json = os.path.join(ROOT, "compare.json")
    formal_md = os.path.join(ROOT, "docs", "compare.md")
    args.json = args.json or (formal_json if args.publish else os.path.join(ROOT, "compare.local.json"))
    args.markdown = args.markdown or (formal_md if args.publish else os.path.join(ROOT, "docs", "compare.local.md"))
    for target, formal in ((args.json, formal_json), (args.markdown, formal_md)):
        if not args.publish and os.path.abspath(target) == formal and os.path.exists(formal):
            print("拒绝覆盖发布文件 %s：请加 --publish，或换输出路径。" % target, file=sys.stderr)
            raise SystemExit(2)

    if not os.path.isdir(CASE_DIR):
        print("找不到 benchmarks/compare", file=sys.stderr)
        raise SystemExit(2)

    work = tempfile.mkdtemp(prefix="impor_compare_")
    rows = {name: make_row(name) for name in ORDER}
    py = sys.executable

    try:
        # 1) Impor
        try:
            exe = build_impor(work)
            empty = build_empty_impor(work)
            total, rss, out, rc = measure([exe], args.runs)
            floor, _rss2, _out2, _rc2 = measure([empty], args.runs)
            fill(rows["Impor 3.11"], os.path.getsize(exe), "case_impor.exe，内置 tcc 32 位",
                 "可执行产物", "compiled", [exe], total, floor, rss, "无（不依赖 Python / VC 运行库）", out)
        except Exception as e:
            rows["Impor 3.11"]["note"] = "构建失败：%s" % e
            print("Impor 行失败：%s" % e, file=sys.stderr)

        # 2) TCC-C
        tcc = resolve_bin(args.tcc, "tcc", TCC)
        if tcc:
            try:
                c_exe = build_c(tcc, work)
                total, rss, out, rc = measure([c_exe], args.runs)
                floor, _r2, _o2, _rc2 = measure([c_exe], args.runs, floor=True)
                fill(rows["TCC-C"], os.path.getsize(c_exe), "case_tcc.exe（tcc -O2）",
                     "可执行产物", "compiled", [c_exe], total, floor, rss, "无", out,
                     "2.5 KB 来自 tcc 自带微型 CRT；MSVC 编译同源约 100 KB+")
            except Exception as e:
                rows["TCC-C"]["note"] = "构建失败：%s" % e
                print("TCC-C 行失败：%s" % e, file=sys.stderr)
        else:
            rows["TCC-C"]["note"] = "未测量（缺 tcc）"

        # 3) Lua
        lua = resolve_bin(args.lua, "lua", r"C:\Program Files\Lua\5.4\lua.exe",
                          r"C:\Program Files (x86)\Lua\5.4\lua.exe")
        if lua:
            try:
                size = os.path.getsize(lua)
                dll = os.path.join(os.path.dirname(lua), "lua54.dll")
                if os.path.exists(dll):
                    size += os.path.getsize(dll)
                cmd = [lua, "case.lua"]
                total, rss, out, rc = measure(cmd, args.runs)
                floor, _r2, _o2, _rc2 = measure(cmd, args.runs, floor=True)
                fill(rows["Lua 5.4"], size, "lua.exe + lua54.dll（若同目录存在）",
                     "含运行时", "script+runtime", cmd, total, floor, rss,
                     "需要 lua.exe / lua54.dll", out, "脚本不打包；体积含运行时")
            except Exception as e:
                rows["Lua 5.4"]["note"] = "测量失败：%s" % e
                print("Lua 行失败：%s" % e, file=sys.stderr)
        else:
            rows["Lua 5.4"]["note"] = "未测量（缺 lua）"

        # 4) Python
        try:
            if args.python_mode == "system":
                size = os.path.getsize(py)
                for d in ("python311.dll", "vcruntime140.dll"):
                    p = os.path.join(os.path.dirname(py), d)
                    if os.path.exists(p):
                        size += os.path.getsize(p)
                cmd = [py, "case.py"]
                mode, kind, art = "system", "含运行时", "python.exe + python311.dll + vcruntime140.dll"
            elif args.python_mode == "onedir":
                exe, folder = build_python_onedir(py, work)
                size, cmd = dir_size(folder), [exe]
                mode, kind, art = "onedir", "含运行时（打包）", "onedir 目录（case.exe + 运行时）"
            else:
                pyinstaller_module(py)
                exe, dist = build_python_onefile(py, work)
                size, cmd = os.path.getsize(exe), [exe]
                mode, kind, art = "onefile", "含运行时（打包）", "PyInstaller onefile"
            total, rss, out, rc = measure(cmd, args.runs)
            floor, _r2, _o2, _rc2 = measure(cmd, args.runs, floor=True)
            fill(rows["Python 3.11"], size, art, kind, mode, cmd, total, floor, rss,
                 "无（打包运行时）" if mode != "system" else "需要解释器与 Python 运行时", out)
        except SystemExit:
            raise
        except Exception as e:
            rows["Python 3.11"]["note"] = "构建失败：%s" % e
            print("Python 行失败：%s" % e, file=sys.stderr)

        # 5) Go
        go = resolve_bin(args.go, "go", r"C:\Program Files\Go\bin\go.exe", r"C:\Go\bin\go.exe")
        if go:
            try:
                go_exe = build_go(go, work)
                total, rss, out, rc = measure([go_exe], args.runs)
                floor, _r2, _o2, _rc2 = measure([go_exe], args.runs, floor=True)
                fill(rows["Go 1.23"], os.path.getsize(go_exe), "case_go.exe（-ldflags=-s -w）",
                     "可执行产物", "compiled", [go_exe], total, floor, rss, "无", out)
            except Exception as e:
                rows["Go 1.23"]["note"] = "构建失败：%s" % e
                print("Go 行失败：%s" % e, file=sys.stderr)
        else:
            rows["Go 1.23"]["note"] = "未测量（缺 go）"

        # 6) Node
        node = resolve_bin(args.node, "node", r"C:\Program Files\nodejs\node.exe",
                           r"C:\Program Files (x86)\nodejs\node.exe")
        if node:
            try:
                cmd = [node, "case.js"]
                total, rss, out, rc = measure(cmd, args.runs)
                floor, _r2, _o2, _rc2 = measure(cmd, args.runs, floor=True)
                fill(rows["Node 22"], os.path.getsize(node), "node.exe（不含脚本）",
                     "含运行时", "script+runtime", cmd, total, floor, rss,
                     "需要 node.exe（约 80 MB）", out, "脚本不打包；体积含运行时")
            except Exception as e:
                rows["Node 22"]["note"] = "测量失败：%s" % e
                print("Node 行失败：%s" % e, file=sys.stderr)
        else:
            rows["Node 22"]["note"] = "未测量（缺 node）"
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("保留临时构建目录：%s" % work)

    missing = [r["language"] for r in rows.values() if not r["measured"]]
    run_rows = [rows[n] for n in ORDER]
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": sys.platform,
        "os_build": platform.version(),
        "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
        "ram_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
        "commit_sha": git_commit(),
        "case_sha256": hashlib.sha256(open(os.path.join(CASE_DIR, "case.json"), "rb").read()).hexdigest(),
        "runs_per_language": args.runs,
        "sample_ok": args.runs >= 10,
        "python_mode": args.python_mode,
        "case": "benchmarks/compare/case.json",
        "expected": EXPECTED,
        "versions": {
            "python": sys.version.split()[0],
            "node": version_of([node, "--version"]) if node else "not found",
            "go": version_of([go, "version"]) if go else "not found",
            "lua": version_of([lua, "-v"]) if lua else "not found",
        },
        "rows": run_rows,
        "missing": missing,
    }

    sample = "runs=%d%s" % (args.runs, "" if args.runs >= 10 else "（样本不足）")
    lines = [
        "# 多语言对照（本机实测）",
        "",
        "同机同一次运行；%s；commit=%s。" % (sample, result["commit_sha"][:12]),
        "",
        "| 语言 | 产物 | 计数口径 | 测法 | 总耗时中位数 | 纯启动中位数 | 峰值内存 | 依赖 | 结果 |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for r in run_rows:
        if not r["measured"]:
            lines.append("| %s | — | — | — | — | — | — | — | 未测量 |" % r["language"])
            continue
        lines.append("| %s | %s · %s | %s | %s | %.2f ms | %.2f ms | %s | %s | %s |" % (
            r["language"], human_size(r["artifact_bytes"]), r["artifact"],
            r["artifact_kind"], r["mode"], r["total_ms_median"], r["startup_ms_median"],
            ("%.0f KB" % r["rss_kb_peak"]) if r["rss_kb_peak"] else "—",
            r["deps"], "OK" if r["output_ok"] else "FAIL"))
    lines += [
        "",
        "口径说明：",
        "",
        "- **计数口径**「可执行产物」= 只算被加载的 exe；「含运行时」= exe/DLL/解释器一起算。"
        "Node 与 Python-system 的格子含整个运行时，Impor / TCC-C / Go 只含产物本身。",
        "- **测法** `compiled` = 独立 exe；`onefile` / `onedir` = PyInstaller 打包；"
        "`script+runtime` = 脚本由外部运行时执行。",
        "- **总耗时** = 启动 + 读 JSON + 求和 + 退出；**纯启动** = 空程序 / 空脚本退出，"
        "只测进程地板（Python onefile 仍包含解包）。",
        "- TCC-C 的 2.5 KB 来自 tcc 自带微型 CRT；MSVC 编译同源程序通常 100 KB+。",
        "- README 规格表测的是 `hello.impor`，本表测的是 `case.impor`；程序与测法不同，"
        "两组数字不应逐位对比。",
        "",
        "## 复现命令",
        "",
        "```text",
    ]
    for r in run_rows:
        lines.append("%-10s %s" % (r["language"], r["command"]))
    lines += ["```", "", "未测量：" + (", ".join(missing) if missing else "无")]
    notes = [r for r in run_rows if r["measured"] and r["note"]]
    if notes:
        lines += ["", "脚注：", ""]
        for r in notes:
            lines.append("- **%s**：%s" % (r["language"], r["note"]))
    if missing:
        lines.append("")
        lines.append("未测量行不参与结论；`--strict` 下会直接失败。")

    json_text = json.dumps(result, ensure_ascii=False, indent=2)
    md_text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(args.markdown), exist_ok=True)
    tmp_json = args.json + ".tmp"
    tmp_md = args.markdown + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        f.write(json_text)
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(md_text)
    os.replace(tmp_json, args.json)
    os.replace(tmp_md, args.markdown)

    print("\n写入 %s 与 %s" % (args.json, args.markdown))
    if args.strict and (missing or any(not r["output_ok"] for r in run_rows if r["measured"])):
        raise SystemExit(1)
    if any(r["measured"] and not r["output_ok"] for r in run_rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
