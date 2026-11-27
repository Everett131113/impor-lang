# -*- coding: utf-8 -*-
"""产物抖动门禁：同一个产物重复跑 N 次，比 rc 与 stdout。

为什么需要它：4.6.0 收尾时发现 C 后端的 try/catch 在 gcc -O2 下有约 46% 概率
直接 0xC0000005（根因是 mingw 把 setjmp 展开成 _setjmp(BUF, __builtin_frame_address(0))）。
单次对拍永远是绿的，只有同一产物反复跑才能暴露这类「偶发崩」。

用法：
    python tools/jitter.py examples/grade.impor --runs 30
    python tools/jitter.py path/to/产物.exe --runs 50 --json jitter.json
    python tools/jitter.py examples/grade.impor --runs 30 --via ir   # 用 IR→C 后端

退出码：0 稳定；1 抖动或构建失败；2 用法错误。
"""
import argparse
import collections
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR = os.path.join(ROOT, "Impor.py")


def find_cc(prefer):
    """返回 (cc 路径或 None, 说明)。优先 gcc（x64，能触发真实的 SEH 路径），其次内置 tcc。"""
    if prefer not in ("auto", "gcc", "tcc"):
        return None, "未知 --cc %r" % prefer
    env_cc = os.environ.get("IMPOR_CC")
    if prefer in ("auto", "gcc"):
        cand = env_cc if env_cc and os.path.isfile(env_cc) else shutil.which("gcc")
        if cand:
            return cand, "gcc（64 位优先）"
        if prefer == "gcc":
            return None, "指定了 gcc 但 PATH 里找不到"
    tcc = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")
    if prefer in ("auto", "tcc") and os.path.isfile(tcc):
        return tcc, "内置 tcc（32 位）"
    return None, "没有可用的 C 编译器（gcc / 内置 tcc 都缺）"


def build_exe(src, cc, via, workdir):
    exe = os.path.join(workdir, "jitter_probe.exe")
    if via == "ir":
        argv = [sys.executable, IMPOR, "ir", src, "--build", exe, "--cc", cc]
    else:
        argv = [sys.executable, IMPOR, "build", src, "-o", exe, "--cc", cc]
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (exe if p.returncode == 0 and os.path.isfile(exe) else None,
            p.returncode, (p.stderr or p.stdout or "").strip())


def digest(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def run_once(exe, workdir, timeout):
    try:
        p = subprocess.run([exe], cwd=workdir, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -9, "", "<timeout>"
    except OSError as exc:
        return -8, "", "<无法启动: %s>" % exc


def main(argv=None):
    ap = argparse.ArgumentParser(description="产物抖动门禁（重复跑，比 rc / stdout）")
    ap.add_argument("target", help=".impor 源码，或已编好的 .exe")
    ap.add_argument("--runs", type=int, default=30, help="重复次数（默认 30）")
    ap.add_argument("--cc", default="auto", choices=["auto", "gcc", "tcc"])
    ap.add_argument("--via", default="build", choices=["build", "ir"],
                    help="build=默认 C 后端；ir=IR→C 后端")
    ap.add_argument("--timeout", type=int, default=120, help="单次超时秒数")
    ap.add_argument("--json", default=None, help="把结果写到 JSON")
    args = ap.parse_args(argv)

    target = os.path.abspath(args.target)
    if not os.path.isfile(target):
        sys.stderr.write("找不到目标：%s\n" % args.target)
        return 2
    if args.runs < 2:
        sys.stderr.write("--runs 至少 2（要比对次数）\n")
        return 2

    workdir = tempfile.mkdtemp(prefix="jitter_")
    print("目标：%s" % target)
    print("重复：%d 次    后端：%s" % (args.runs, args.via))

    if target.lower().endswith(".exe"):
        exe, note = target, "直接跑给定产物"
        print("产物：%s（%s）" % (exe, note))
        cc_note = "-"
    else:
        cc, cc_note = find_cc(args.cc)
        if cc is None:
            print("SKIP: %s" % cc_note)
            return 0
        print("编译器：%s（%s）" % (cc, cc_note))
        exe, rc, detail = build_exe(target, cc, args.via, workdir)
        if exe is None:
            print("构建失败 rc=%d：%s" % (rc, detail.splitlines()[0] if detail else ""))
            if args.json:
                io.open(args.json, "w", encoding="utf-8", newline="\n").write(
                    json.dumps({"target": target, "ok": False, "phase": "build",
                                "rc": rc, "detail": detail[-4000:]},
                               ensure_ascii=False, indent=2))
            return 1

    combos = collections.Counter()
    samples = {}
    for i in range(args.runs):
        rc, out, err = run_once(exe, os.path.dirname(target) or workdir, args.timeout)
        key = (rc, digest(out), digest(err))
        combos[key] += 1
        samples.setdefault(key, (out, err))
        if (i + 1) % 10 == 0:
            print("  ...%d/%d" % (i + 1, args.runs))

    ok = len(combos) == 1
    print("\n结果：%s（%d 种 (rc, stdout, stderr) 组合）"
          % ("稳定" if ok else "**抖动**", len(combos)))
    for key, n in combos.most_common():
        rc, oh, eh = key
        print("  rc=%-12d 次数=%-4d stdout=%s stderr=%s" % (rc, n, oh, eh))
        if not ok:
            out, err = samples[key]
            tail = out[-300:].replace("\n", "\\n")
            print("    stdout 末尾: %r" % tail)
            if err.strip():
                print("    stderr 末尾: %r" % err.strip()[-300:])

    if args.json:
        io.open(args.json, "w", encoding="utf-8", newline="\n").write(json.dumps({
            "target": target, "ok": ok, "runs": args.runs, "via": args.via,
            "cc": (cc_note if not target.lower().endswith(".exe") else "-"),
            "combinations": [{"rc": k[0], "count": v, "stdout": k[1], "stderr": k[2]}
                             for k, v in combos.most_common()],
        }, ensure_ascii=False, indent=2))

    shutil.rmtree(workdir, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
