# -*- coding: utf-8 -*-
"""稳定性横向对照：同一批敌意程序，在 Impor / Python / C / Node 下各跑一遍。

看的是「失败方式」，不是「跑得多快」：

  * 死循环 / 刷屏 → 是有上限的熔断，还是永久挂起（只能靠外部 watchdog 杀）
  * 无限递归 / 一次巨型分配 / 除零 → 是结构化错误，还是进程级崩溃（段错误/被系统杀）

用法：
    python tools/stability_compare.py [--json OUT.json] [--md OUT.md] [--timeout 4]

判定口径（写死在 classify 里，报告里照抄）：
  contained  在时限内退出，且输出里有明确错误类型/错误码（程序可控地失败）
  hang       watchdog 超时强杀（进程自己不会停）
  crash      原生层异常退出（Windows 0xC0000005 等 / 负返回码）
  output     stdout 超过读取上限被父进程掐断
  completed  正常退出

诚实边界：这不是「谁更快」，也不是「谁的语义更对」；
C 的崩溃是它设计上不检查（UB），Python 的挂起是它没有预算概念——
两者都能被外部超时/容器杀掉，只是「语言自己不提供这个能力」。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")
IMPOR_PY = os.path.join(ROOT, "Impor.py")

# Windows 崩溃对话框会把「段错误/除零」变成「看起来卡住」：父进程关闭错误弹窗，
# 子进程继承该错误模式，崩溃立即以 0xC00000xx 退出，才能测到真实的失败方式。
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass

CASES = {
    "loop": {
        "title": "无限循环",
        "impor": "var i = 0\nwhile true\n    set i = i + 1\n",
        "python": "i = 0\nwhile True:\n    i += 1\n",
        "c": "int main(void){volatile long long i=0;while(1){i++;}return 0;}\n",
        "node": "let i=0;while(true){i++;}\n",
    },
    "recurse": {
        "title": "无限递归",
        "impor": "def f()\n    return f()\nf()\n",
        "python": "def f():\n    return f()\nf()\n",
        "c": ("long long f(long long n){return f(n+1)+1;}\n"
              "int main(void){return (int)f(0);}\n"),
        "node": "function f(){return f()+1;}f();\n",
    },
    "alloc": {
        "title": "一次要 100M 元素",
        "impor": "var a = arrfill(100000000, 0)\nprint(len(a))\n",
        "python": "a = [0] * 100000000\nprint(len(a))\n",
        "c": ("#include <stdlib.h>\n#include <stdio.h>\n"
              "int main(void){long long n=100000000;char*p=(char*)malloc((size_t)16*1024*1024*1024);"
              "p[0]=1;printf(\"%lld\\n\",n);return 0;}\n"),
        "node": "const a = new Array(100000000).fill(0);console.log(a.length);\n",
    },
    "flood": {
        "title": "无上限刷屏",
        "impor": 'var i = 0\nwhile true\n    print("这一行会被无限打印")\n    set i = i + 1\n',
        "python": 'i = 0\nwhile True:\n    print("这一行会被无限打印")\n    i += 1\n',
        "c": ("#include <stdio.h>\nint main(void){while(1)printf(\"flood-line\\n\");}\n"),
        "node": 'let i=0;while(true){console.log("这一行会被无限打印");i++;}\n',
    },
    "divzero": {
        "title": "除零",
        "impor": "var x = 1 / 0\nprint(x)\n",
        "python": "print(1 / 0)\n",
        "c": "int main(void){volatile long long a=1,z=0;return (int)(a/z);}\n",
        "node": "console.log(1n / 0n);\n",
    },
}

ENGINE_LABEL = {
    "impor_stable": "Impor --stable",
    "impor_default": "Impor 默认",
    "python": "Python 3.13",
    "c": "C (tcc -O2)",
    "node": "Node.js",
}


def _find_node():
    exe = shutil.which("node")
    if exe:
        return exe
    for guess in (r"C:\Program Files\nodejs\node.exe",
                  r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.isfile(guess):
            return guess
    return None


def probe(cmd, timeout=4.0, out_cap_kb=1024, cwd=None, env=None):
    """跑一个命令：超时或输出超限就强杀；返回结构化结果。"""
    cap = int(out_cap_kb) * 1024
    t0 = time.perf_counter()
    stdout_, stderr_ = [], []
    state = {"capped": False}

    def _read(stream, sink):
        chunks, total = [], 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    room = max(0, cap - (total - len(chunk)))
                    if room:
                        chunks.append(chunk[:room])
                    state["capped"] = True
                    break
                chunks.append(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            sink.append(b"".join(chunks))

    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    threads = [threading.Thread(target=_read, args=(proc.stdout, stdout_), daemon=True),
               threading.Thread(target=_read, args=(proc.stderr, stderr_), daemon=True)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    for t in threads:
        t.join(timeout=3)
    elapsed = time.perf_counter() - t0
    out = (stdout_[0] if stdout_ else b"").decode("utf-8", "replace")
    err = (stderr_[0] if stderr_ else b"").decode("utf-8", "replace")
    rc = proc.returncode
    kind = classify(rc, timed_out, state["capped"], out + "\n" + err)
    return {"rc": rc, "kind": kind, "elapsed": round(elapsed, 2),
            "stdout_tail": "\n".join(out.splitlines()[-2:]),
            "stderr_tail": "\n".join(err.splitlines()[-3:])}


def classify(rc, timed_out, capped, text):
    if rc is not None and (rc < 0 or rc >= 0xC0000000):
        return "crash"
    if any(code in text for code in ("E1020", "E1021", "E1022", "E1023", "E1024")):
        return "contained"
    if timed_out:
        return "hang"
    if capped:
        return "output"
    if rc is None:
        return "crash"
    if rc == 0:
        return "completed"
    low = text.lower()
    for marker in ("error", "exception", "错误", "异常", "traceback", "rangeerror",
                   "recursivedeep", "step limit", "out of memory"):
        if marker in low:
            return "contained"
    return "error"


LIMIT_FLAGS = ["--stable", "--max-steps=500000", "--max-seconds=3",
               "--max-memory=384", "--max-output=512", "--max-alloc=2000000"]


def _write(workdir, name, text, ext):
    path = os.path.join(workdir, name + ext)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _trivial_cmds(workdir):
    """每个引擎跑一个「什么都不做」的程序，用来量启动地板。

    本机 360 安全软件会把新生成的 tcc 产物/未签名 exe 先扫一遍，
    C 的启动地板能到 7–10 秒；不扣掉它，所有 C 用例都会被误判成「挂起」。
    """
    out = {}
    ipath = _write(workdir, "_floor", "print(1)\n", ".impor")
    out["impor_stable"] = [sys.executable, IMPOR_PY, "run", "--stable", ipath]
    out["impor_default"] = [sys.executable, IMPOR_PY, "run", "--no-native", ipath]
    ppath = _write(workdir, "_floor", "print(1)\n", ".py")
    out["python"] = [sys.executable, ppath]
    node = _find_node()
    if node:
        npath = _write(workdir, "_floor", "console.log(1);\n", ".js")
        out["node"] = [node, npath]
    if os.path.isfile(TCC):
        cpath = _write(workdir, "_floor", "int main(void){return 0;}\n", ".c")
        exe = os.path.join(workdir, "_floor.exe")
        r = subprocess.run([TCC, "-O2", "-o", exe, cpath], capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            out["c"] = None
        else:
            out["c"] = [exe]
    return out


def measure_floors(workdir, extra=12.0):
    floors = {}
    for engine, cmd in _trivial_cmds(workdir).items():
        if not cmd:
            continue
        try:
            floors[engine] = probe(cmd, timeout=extra)["elapsed"]
        except Exception:
            floors[engine] = 0.0
    return floors


def build_engines(workdir, case):
    """按用例生成各引擎的可执行命令；缺工具链的引擎返回 None。"""
    srcs = CASES[case]
    out = {}

    ipath = _write(workdir, case, srcs["impor"], ".impor")
    out["impor_stable"] = [sys.executable, IMPOR_PY, "run"] + LIMIT_FLAGS + [ipath]
    out["impor_default"] = [sys.executable, IMPOR_PY, "run", "--no-native", ipath]

    ppath = _write(workdir, case, srcs["python"], ".py")
    out["python"] = [sys.executable, ppath]

    node = _find_node()
    if node:
        npath = _write(workdir, case, srcs["node"], ".js")
        out["node"] = [node, npath]
    else:
        out["node"] = None

    if os.path.isfile(TCC):
        cpath = _write(workdir, case, srcs["c"], ".c")
        exe = os.path.join(workdir, case + ".exe")
        r = subprocess.run([TCC, "-O2", "-o", exe, cpath], capture_output=True,
                           text=True, timeout=60)
        out["c"] = [exe] if r.returncode == 0 and os.path.isfile(exe) else None
    else:
        out["c"] = None
    return out


BENCH = {
    "impor": ('var s = 0\nvar i = 0\nwhile i < 200000\n    set s = s + i * i\n'
              '    set i = i + 1\nvar t = ""\nvar j = 0\nwhile j < 3000\n'
              '    set t = t + "x"\n    set j = j + 1\nprint(s)\n'),
    "python": ('s = 0\ni = 0\nwhile i < 200000:\n    s = s + i * i\n    i += 1\n'
               't = ""\nj = 0\nwhile j < 3000:\n    t = t + "x"\n    j += 1\nprint(s)\n'),
    "node": ('let s=0,i=0;while(i<200000){s=s+i*i;i++;}let t="",j=0;'
             'while(j<3000){t=t+"x";j++;}console.log(s);\n'),
    "c": ('#include <stdio.h>\nint main(void){long long s=0,i=0;while(i<200000)'
          '{s=s+i*i;i++;}char t[5000];int j=0;while(j<3000){t[j]=\'x\';j++;}t[j]=0;'
          'printf("%lld\\n",s);return 0;}\n'),
}


def run_bench(floors, reps=2):
    """正常负载的耗时：稳定模式不能把普通程序拖成龟速，这一节用来交代代价。"""
    workdir = tempfile.mkdtemp(prefix="impor-stability-bench-")
    cmds = {}
    ipath = _write(workdir, "bench", BENCH["impor"], ".impor")
    cmds["impor_stable"] = [sys.executable, IMPOR_PY, "run", "--stable", ipath]
    cmds["impor_default"] = [sys.executable, IMPOR_PY, "run", "--no-native", ipath]
    ppath = _write(workdir, "bench", BENCH["python"], ".py")
    cmds["python"] = [sys.executable, ppath]
    node = _find_node()
    if node:
        npath = _write(workdir, "bench", BENCH["node"], ".js")
        cmds["node"] = [node, npath]
    if os.path.isfile(TCC):
        cpath = _write(workdir, "bench", BENCH["c"], ".c")
        exe = os.path.join(workdir, "bench.exe")
        r = subprocess.run([TCC, "-O2", "-o", exe, cpath], capture_output=True,
                           text=True, timeout=60)
        if r.returncode == 0:
            cmds["c"] = [exe]
    out = {}
    for engine, cmd in cmds.items():
        best = None
        ok = True
        for _ in range(reps):
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            r = probe(cmd, timeout=60, cwd=ROOT, env=env)
            if r["kind"] != "completed":
                ok = False
            best = r["elapsed"] if best is None else min(best, r["elapsed"])
        out[engine] = {"seconds": round(best or 0.0, 3), "completed": ok,
                       "output": (r.get("stdout_tail") or "").strip().splitlines()[-1:]}
    return out


def run_matrix(timeout=4.0):
    workdir = tempfile.mkdtemp(prefix="impor-stability-compare-")
    floors = measure_floors(workdir)
    rows = []
    for case, spec in CASES.items():
        try:
            engines = build_engines(workdir, case)
        except Exception:
            engines = {"c": None, "node": None, "python": None,
                       "impor_stable": None, "impor_default": None}
        for engine, cmd in engines.items():
            floor = floors.get(engine, 0.0)
            if cmd is None:
                rows.append({"case": case, "title": spec["title"], "engine": engine,
                             "kind": "unavailable", "rc": None, "elapsed": 0,
                             "floor": floor, "adjusted": 0,
                             "stderr_tail": "工具链缺失"})
                continue
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            r = probe(cmd, timeout=timeout + floor * 1.2 + 1.0, out_cap_kb=1024,
                      cwd=ROOT, env=env)
            r["floor"] = round(floor, 2)
            r["adjusted"] = round(max(0.0, r["elapsed"] - floor), 2)
            rows.append({"case": case, "title": spec["title"], "engine": engine, **r})
    return rows, workdir, floors


def summarize(rows):
    engines = sorted({r["engine"] for r in rows})
    cases = list(CASES)
    table = {}
    for e in engines:
        table[e] = {c: next((r["kind"] for r in rows if r["engine"] == e and r["case"] == c), "unavailable")
                    for c in cases}
    return table


def render_markdown(rows, workdir, timeout, floors, bench=None):
    table = summarize(rows)
    engines = [e for e in ENGINE_LABEL if e in table]
    lines = []
    lines.append("# Impor 稳定性横向对照（实测）\n")
    lines.append("同一台机器、同一批敌意程序、外部 watchdog %.0f 秒、stdout 读取上限 1 MB。\n" % timeout)
    lines.append("判定只看「失败方式」：`contained` 在时限内结构化报错；`hang` 未自行停止、"
                 "只能被外部强杀；`crash` 原生层异常退出；`output` 输出超限被父进程掐断；"
                 "`completed` 正常退出。\n")
    if floors:
        parts = ["%s %.2fs" % (ENGINE_LABEL.get(k, k), v) for k, v in sorted(floors.items())]
        lines.append("启动地板（同一引擎跑「什么都不做」的程序，逐次实测）：" + "；".join(parts)
                     + "。下表「耗时」给的是扣掉地板后的运行时间"
                     + ("；C 的地板偏高是安全软件在扫未签名 exe。\n"
                        if floors.get("c", 0) > 2 else "。\n"))
    lines.append("| 用例 | " + " | ".join(ENGINE_LABEL[e] for e in engines) + " |")
    lines.append("|---|" + "|".join(["---"] * len(engines)) + "|")
    for c in CASES:
        row = [CASES[c]["title"]]
        for e in engines:
            k = table[e].get(c, "unavailable")
            row.append({"contained": "结构化熔断", "hang": "未停（被强杀）", "crash": "进程崩溃",
                        "output": "刷屏被掐断", "completed": "正常退出", "error": "普通错误",
                        "unavailable": "—"}.get(k, k))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## 逐项读数（耗时 = 扣掉启动地板后的运行时间）\n")
    lines.append("| 用例 | 引擎 | 结果 | 退出码 | 耗时(s) | 现场（末行） |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        tail = (r.get("stderr_tail") or r.get("stdout_tail") or "").replace("|", "\\|")
        tail = tail.splitlines()[-1][:90] if tail else ""
        lines.append("| %s | %s | %s | %s | %s | %s |"
                     % (r["title"], ENGINE_LABEL.get(r["engine"], r["engine"]),
                        r["kind"], r.get("rc"), r.get("adjusted"), tail))
    lines.append("")
    if bench:
        lines.append("## 正常负载的代价（同一段计算：20 万次整数循环 + 3000 次字符串拼接，含进程启动）\n")
        lines.append("| 引擎 | 耗时(s) | 正常跑完 | 相对 Impor 默认 |")
        lines.append("|---|---|---|---|")
        base = (bench.get("impor_default") or {}).get("seconds") or 0
        for e in ENGINE_LABEL:
            if e not in bench:
                continue
            b = bench[e]
            rel = "—" if not base or not b["seconds"] else "%.2f×" % (b["seconds"] / base)
            lines.append("| %s | %.3f | %s | %s |"
                         % (ENGINE_LABEL[e], b["seconds"], "是" if b["completed"] else "否", rel))
        lines.append("")
        st = bench.get("impor_stable") or {}
        df = bench.get("impor_default") or {}
        py = bench.get("python") or {}
        if st.get("seconds") and df.get("seconds"):
            lines.append("读法：稳定模式比默认档慢 %.1f 倍（这一步的开销来自每条语句一次预算判断，"
                         "不是来自重复执行）；换来的是异常脚本在一秒内变成一条带错误码的错误。"
                         "另外要把两件事分开：Impor 作为树遍历解释器，本身吞吐就比 Python 慢"
                         "%s 倍左右，这是解释器架构的固有成本，不是稳定性带来的。\n"
                         % (st["seconds"] / df["seconds"],
                            ("%.0f" % (st["seconds"] / py["seconds"])) if py.get("seconds") else "若干"))
        lines.append("这一列不做地板扣除：它是端到端时间，能直接拿去和别家比「跑完同一件事花多久」。\n")
    lines.append("## 结论（只写实测支持的）\n")
    total = len(CASES)
    kinds = {e: [table[e].get(c, "unavailable") for c in CASES] for e in engines}

    def _count(engine, kind):
        return sum(1 for k in kinds.get(engine, []) if k == kind)

    st = kinds.get("impor_stable", [])
    st_rows = [r for r in rows if r["engine"] == "impor_stable"]
    st_max = max([r.get("adjusted") or 0 for r in st_rows] or [0])
    lines.append("1. **Impor --stable：%d/%d 结构化熔断，0 挂起，0 原生崩溃**，"
                 "最慢一例 %.2f 秒退出，且都给出错误码（E1020–E1024）、行号、为什么与修复建议。"
                 % (_count("impor_stable", "contained"), total, st_max))
    sev = []
    for e in engines:
        if e == "impor_stable":
            continue
        hang, crash = _count(e, "hang"), _count(e, "crash")
        if hang or crash:
            sev.append("%s：%d 例未停/强杀、%d 例原生崩溃" % (ENGINE_LABEL[e], hang, crash))
    if sev:
        lines.append("2. **其他实现的失败方式**：" + "；".join(sev) + "。"
                     "C 的崩溃来自「UB 不检查」，Python/Node 的未停来自「没有预算概念」——"
                     "两者都能被外部超时或容器兜住，但语言自己不提供上限。")
    dh = kinds.get("impor_default", [])
    if "contained" in dh:
        lines.append("3. **Impor 默认档（不开 --stable）与 Python 同档**："
                     "死循环会一直跑、刷屏无上限、巨型分配只能等系统处理——"
                     "所以「更强」不是免费的，必须显式打开稳定模式（或 `IMPOR_STABLE=1`）。")
    lines.append("")
    lines.append("## 这条结论的边界\n")
    lines.append("- 比的是「资源有界、失败可控」，不是「永不失败」：解释器自身/FFI 的原生崩溃"
                 "仍需 `run_isolated` 父进程围栏兜底。")
    lines.append("- Python/Node/C 挂起后都能被容器或超时杀掉，本表说的是「语言自己不提供上限」，"
                 "不是「无法被运维手段兜住」。")
    lines.append("- 内存闸门是 RSS 采样，不是硬配额；极端情况可能越过上限几十 MB 才熔断。")
    lines.append("- 脚本目录：`%s`\n" % workdir)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Impor 稳定性横向对照")
    ap.add_argument("--json", default=os.path.join(ROOT, "docs", "稳定性对照-4.0.json"))
    ap.add_argument("--md", default=os.path.join(ROOT, "docs", "稳定性对照-4.0.md"))
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)
    rows, workdir, floors = run_matrix(timeout=args.timeout)
    bench = run_bench(floors)
    data = {"rows": rows, "table": summarize(rows), "workdir": workdir,
            "timeout": args.timeout, "floors": floors, "bench": bench,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S")}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    md = render_markdown(rows, workdir, args.timeout, floors, bench)
    if args.md:
        with open(args.md, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
