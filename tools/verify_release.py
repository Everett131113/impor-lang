# -*- coding: utf-8 -*-
"""承诺的单一验收入口：每条 README / docs 承诺都映射到一个可执行 check。

用法：
    python tools/verify_release.py              # 跑全部
    python tools/verify_release.py --json       # 机器可读
    python tools/verify_release.py --check exe  # 只跑一条（给 claims.yaml 用）

退出码：全部通过 0；有失败 1。
"""
import argparse
import contextlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import textwrap
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CLI = os.path.join(ROOT, "Impor.py")

CHECKS = []


def check(name, title):
    def deco(fn):
        CHECKS.append((name, title, fn))
        return fn
    return deco


def run(args, cwd=ROOT, timeout=240):
    p = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


def impor_run(path, cwd=ROOT, timeout=180):
    return run([PY, CLI, "run", path], cwd=cwd, timeout=timeout)


def impor_build(path, out, cwd=ROOT, extra=(), timeout=240):
    args = [PY, CLI, "build", path, "-o", out]
    args.extend(extra)
    return run(args, cwd=cwd, timeout=timeout)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(textwrap.dedent(text).lstrip("\n"))


@contextlib.contextmanager
def local_server(root):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=root, **k)
        def log_message(self, *a):
            pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d" % port
    finally:
        srv.shutdown()
        srv.server_close()


@check("version", "版本号与 VERSION 文件一致")
def check_version():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        ver = f.read().strip()
    rc, out, err = run([PY, CLI, "version"])
    ok = rc == 0 and ver in (out + err)
    return ok, "VERSION=%s / CLI=%s" % (ver, (out + err).strip()[:80])


@check("quickstart_hello", "双击示例 1：hello 能跑")
def check_quickstart_hello():
    rc, out, err = impor_run("hello.impor", cwd=os.path.join(ROOT, "examples", "quickstart"))
    return rc == 0 and "你好" in out, "rc=%s / %s" % (rc, out.strip()[:80])


@check("quickstart_read_file", "双击示例 2：读文件能跑")
def check_quickstart_read_file():
    cwd = os.path.join(ROOT, "examples", "quickstart")
    rc, out, err = impor_run("read_file.impor", cwd=cwd)
    return rc == 0 and "sample.txt" in out, "rc=%s / %s" % (rc, out.strip()[:100])


@check("quickstart_dll", "双击示例 3：Windows 调 DLL 能跑")
def check_quickstart_dll():
    if os.name != "nt":
        return False, "仅 Windows 可验证"
    cwd = os.path.join(ROOT, "examples", "quickstart")
    rc, out, err = impor_run("call_dll.impor", cwd=cwd)
    ok = rc == 0 and any(ch.isdigit() for ch in out)
    return ok, "rc=%s / %s" % (rc, out.strip()[:100])


@check("exe", "单文件 exe 脱离 Python 可运行且 <= 320 KB")
def check_exe():
    tmp = tempfile.mkdtemp(prefix="impor_verify_exe_")
    try:
        target = os.path.join(tmp, "hello.exe")
        rc, out, err = impor_build(os.path.join(ROOT, "examples", "hello.impor"), target)
        if rc != 0 or not os.path.exists(target):
            return False, "构建失败 rc=%s %s" % (rc, (out + err)[-160:])
        size = os.path.getsize(target)
        r2, out2, err2 = run([target], cwd=tmp)
        ok = r2 == 0 and size <= 320 * 1024 and "Impor" in out2
        return ok, "size=%d rc=%s out=%s" % (size, r2, out2.strip()[:60])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("c_abi", "C ABI 产出 .dll/.h/.def")
def check_c_abi():
    src = os.path.join(ROOT, "examples", "c_abi", "lib.impor")
    if not os.path.exists(src):
        return False, "缺少 examples/c_abi/lib.impor"
    tmp = tempfile.mkdtemp(prefix="impor_verify_cabi_")
    try:
        dll = os.path.join(tmp, "lib.dll")
        rc, out, err = impor_build(src, dll, extra=["--lib"])
        tree = sorted(os.listdir(tmp))
        ok = rc == 0 and os.path.exists(dll) and "lib.h" in tree and "lib.def" in tree
        return ok, "rc=%s files=%s" % (rc, tree)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


STD_SCRIPT = """
import std.json as json
import std.fs as fs
import std.path as path
import std.regex as re
import std.time as tm
print(json.stringify(json.parse("[1,2]")))
print(path.join("docs", "a.md"))
print(path.basename("C:/tmp/x.txt"))
print(path.ext("a.md"))
print(re.is_match("abc", "a.c"))
print(re.replace_all("a1b2", "[0-9]", "#"))
fs.write("std_probe.txt", "ok")
print(fs.read("std_probe.txt"))
print(tm.seconds() > 0)
"""


@check("stdlib_interp", "std.json/fs/path/regex/time 解释器全部可用")
def check_stdlib_interp():
    tmp = tempfile.mkdtemp(prefix="impor_verify_std_")
    try:
        p = os.path.join(tmp, "std.impor")
        write(p, STD_SCRIPT)
        rc, out, err = impor_run(p, cwd=tmp)
        want = ["[1,2]", "docs/a.md", "x.txt", ".md", "True", "a#b#", "ok", "True"]
        lines = [l.strip() for l in out.replace("\r", "").splitlines()]
        ok = rc == 0 and all(w in lines for w in want)
        return ok, "rc=%s out=%s" % (rc, "|".join(lines[-8:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


HTTP_SCRIPT = """
import std.http as http
print(http.get("%s/hello.txt"))
"""


@check("stdlib_http", "std.http 在解释器下能 GET 本地页面")
def check_stdlib_http():
    tmp = tempfile.mkdtemp(prefix="impor_verify_http_")
    try:
        write(os.path.join(tmp, "hello.txt"), "HTTP-OK\n")
        with local_server(tmp) as base:
            p = os.path.join(tmp, "http.impor")
            write(p, HTTP_SCRIPT % base)
            rc, out, err = impor_run(p, cwd=tmp)
            ok = rc == 0 and "HTTP-OK" in out
            return ok, "rc=%s out=%s" % (rc, out.strip()[:80])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


C_SCRIPT = """
import std.json as json
import std.fs as fs
import std.path as path
import std.regex as re
import std.time as tm
import std.http as http
print(json.stringify(json.parse("[1,2]")))
print(path.join("docs", "a.md"))
print(re.is_match("abc", "a.c"))
fs.write("std_c.txt", "ok")
print(fs.read("std_c.txt"))
print(tm.seconds() > 0)
print(http.get("%s/hello.txt"))
"""


@check("stdlib_c", "六个 std 模块在 C 后端 exe 中同样可用")
def check_stdlib_c():
    tmp = tempfile.mkdtemp(prefix="impor_verify_stdc_")
    try:
        write(os.path.join(tmp, "hello.txt"), "HTTP-OK\n")
        with local_server(tmp) as base:
            p = os.path.join(tmp, "stdc.impor")
            write(p, C_SCRIPT % base)
            exe = os.path.join(tmp, "stdc.exe")
            rc, out, err = impor_build(p, exe, cwd=tmp)
            if rc != 0 or not os.path.exists(exe):
                return False, "构建失败 rc=%s %s" % (rc, (out + err)[-200:])
            r2, out2, err2 = run([exe], cwd=tmp)
            want = ["[1,2]", "docs/a.md", "True", "ok", "HTTP-OK"]
            lines = [l.strip() for l in out2.replace("\r", "").splitlines()]
            ok = r2 == 0 and all(w in lines for w in want)
            return ok, "rc=%s out=%s" % (r2, "|".join(lines[-6:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("errors_actionable", "20 条真实报错全部含 5 项可行动信息")
def check_errors():
    rc, out, err = run([PY, os.path.join(ROOT, "tools", "error_audit.py")])
    ok = rc == 0 and "20/20" in (out + err)
    return ok, (out + err).strip().splitlines()[-1] if (out + err).strip() else "no output"


@check("error_docs", "docs/errors.md 含 E1009 / E2002 锚点")
def check_error_docs():
    p = os.path.join(ROOT, "docs", "errors.md")
    if not os.path.exists(p):
        return False, "缺少 docs/errors.md"
    with open(p, encoding="utf-8") as f:
        text = f.read()
    return ('id="E1009"' in text and 'id="E2002"' in text), "docs/errors.md"


@check("api_frozen", "api_baseline.json 无未授权漂移")
def check_api():
    rc, out, err = run([PY, os.path.join(ROOT, "tools", "api_baseline.py")])
    return rc == 0, (out + err).strip().splitlines()[-1]


@check("compat_20", "3.11 行为基线的 20 个示例在 4.0 全部通过")
def check_compat():
    rc, out, err = run([PY, os.path.join(ROOT, "tools", "compat_check.py")])
    return rc == 0 and "20/20" in (out + err), (out + err).strip().splitlines()[-1]


@check("fmt_clean", "全仓 .impor 已格式化（文件系统扫描，不依赖 Git）")
def check_fmt_clean():
    rc, out, err = run([PY, os.path.join(ROOT, "tools", "check_fmt_all.py")])
    txt = (out + err).strip().splitlines()
    return rc == 0, txt[-1] if txt else "rc=%s" % rc


@check("keywords", "保留字静默接受 = 0")
def check_keywords():
    rc, out, err = run([PY, os.path.join(ROOT, "tools", "keyword_audit.py")])
    ok = rc == 0 and "静默接受（0）" in out
    return ok, "rc=%s" % rc


@check("portable", "便携版 ImporPortable.exe 存在且 > 10 MB")
def check_portable():
    p = os.path.join(ROOT, "ImporPortable.exe")
    if not os.path.exists(p):
        return False, "缺少 ImporPortable.exe"
    size = os.path.getsize(p)
    return size > 10 * 1024 * 1024, "size=%d" % size


@check("entry_files", "START_HERE / 常见问题 / 三个 bat 都在")
def check_entry():
    need = ["START_HERE.txt", "常见问题.txt", "开始这里.bat"]
    missing = [n for n in need if not os.path.exists(os.path.join(ROOT, n))]
    qs = os.path.join(ROOT, "examples", "quickstart")
    for n in ["1-你好.bat", "2-读文件.bat", "3-调DLL.bat"]:
        if not os.path.exists(os.path.join(qs, n)):
            missing.append("examples/quickstart/" + n)
    return not missing, "missing=%s" % missing


@check("defer", "defer 块写法可用，非块写法明确拒绝")
def check_defer():
    tmp = tempfile.mkdtemp(prefix="impor_verify_defer_")
    try:
        p = os.path.join(tmp, "d.impor")
        write(p, """
        def f() {
            defer { print("defer-ok") }
            print("body")
        }
        f()
        """)
        rc, out, err = impor_run(p, cwd=tmp)
        if rc != 0 or "defer-ok" not in out:
            return False, "块写法失败 rc=%s %s" % (rc, (out + err)[-160:])
        p2 = os.path.join(tmp, "bad.impor")
        write(p2, 'defer print("x")\n')
        rc2, out2, err2 = impor_run(p2, cwd=tmp)
        ok = rc2 == 2 and "defer 需要块写法" in (out2 + err2)
        return ok, "block=%s bad=%s" % (out.strip()[:40], rc2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("reject_async", "async / await 明确拒绝（不是静默接受）")
def check_reject_async():
    tmp = tempfile.mkdtemp(prefix="impor_verify_async_")
    try:
        p = os.path.join(tmp, "a.impor")
        write(p, "async def f() {\n}\n")
        rc, out, err = impor_run(p, cwd=tmp)
        ok = rc == 2 and "尚未实现" in (out + err)
        return ok, "rc=%s" % rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_peak(exe, args=(), cwd=ROOT):
    """运行 exe，返回 (rc, stdout, stderr, peak_working_set_bytes)。

    Windows 用 psapi 采样；其他平台 peak=0（不参与断言）。
    """
    p = subprocess.Popen(
        [exe] + list(args), cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    peak = 0
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, p.pid)
            if handle:
                while p.poll() is None:
                    pmc = PMC()
                    pmc.cb = ctypes.sizeof(PMC)
                    if ctypes.windll.psapi.GetProcessMemoryInfo(
                            handle, ctypes.byref(pmc), pmc.cb):
                        peak = max(peak, int(pmc.PeakWorkingSetSize))
                    time.sleep(0.02)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            peak = 0
    out, err = p.communicate()
    return p.returncode, out, err, peak


@check("app_ssg", "真项目 1：Impor 静态站点生成器可构建并生成 4 页")
def check_app_ssg():
    tmp = tempfile.mkdtemp(prefix="impor_verify_ssg_")
    try:
        exe = os.path.join(tmp, "ssg.exe")
        rc, out, err = impor_build(os.path.join(ROOT, "apps", "impor_ssg", "ssg.impor"), exe)
        if rc != 0 or not os.path.exists(exe):
            return False, "构建失败 rc=%s %s" % (rc, (out + err)[-200:])
        r2, out2, err2 = run([exe], cwd=ROOT)
        index = os.path.join(ROOT, "apps", "impor_ssg", "_site", "index.html")
        ok = r2 == 0 and "4 页" in out2 and os.path.exists(index)
        return ok, "rc=%s %s" % (r2, out2.strip().splitlines()[-1] if out2.strip() else "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("app_action", "真项目 2：GitHub Action 用 Impor exe 扫描 quickstart 并通关")
def check_app_action():
    tmp = tempfile.mkdtemp(prefix="impor_verify_action_")
    try:
        exe = os.path.join(tmp, "action.exe")
        rc, out, err = impor_build(os.path.join(ROOT, "apps", "impor_action", "main.impor"), exe)
        if rc != 0 or not os.path.exists(exe):
            return False, "构建失败 rc=%s %s" % (rc, (out + err)[-200:])
        r2, out2, err2 = run([exe, os.path.join("examples", "quickstart")], cwd=ROOT)
        ok = r2 == 0 and "文件：3" in out2 and "通过" in out2
        return ok, "rc=%s out=%s" % (r2, out2.strip().replace("\n", " / ")[:140])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("app_sqlite", "真项目 3：SQLite 报表处理 10 万行，峰值 < 50 MB")
def check_app_sqlite():
    tmp = tempfile.mkdtemp(prefix="impor_verify_sqlite_")
    try:
        exe = os.path.join(tmp, "report.exe")
        src = os.path.join(ROOT, "apps", "impor_sqlite_report", "report.impor")
        rc, out, err = impor_build(src, exe)
        if rc != 0 or not os.path.exists(exe):
            return False, "构建失败 rc=%s %s" % (rc, (out + err)[-200:])
        csv = os.path.join(ROOT, "apps", "impor_sqlite_report", "data", "sales_100k.csv")
        r2, out2, err2, peak = run_peak(exe, [csv, "100000"], cwd=ROOT)
        peak_mb = peak / (1024 * 1024)
        report = os.path.join(ROOT, "apps", "impor_sqlite_report", "report.html")
        ok = r2 == 0 and "100000" in out2 and os.path.exists(report)
        if peak:
            ok = ok and peak_mb < 50
        detail = "rc=%s rows=100000 peak=%s MB" % (r2, ("%.1f" % peak_mb) if peak else "未采样")
        return ok, detail
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check")
    args = ap.parse_args()

    selected = [(n, t, f) for n, t, f in CHECKS if not args.check or n == args.check]
    if not selected:
        print("未知 check：" + str(args.check))
        return 2

    results = []
    for name, title, fn in selected:
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, "%s: %s" % (type(e).__name__, e)
        results.append({
            "id": name, "title": title, "ok": bool(ok),
            "detail": str(detail), "seconds": round(time.time() - t0, 2),
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    else:
        for r in results:
            print(("OK  " if r["ok"] else "FAIL") + "  " + r["id"].ljust(20) + r["title"])
            if not r["ok"]:
                print("      " + r["detail"])
        ok = sum(1 for r in results if r["ok"])
        print("\n通过 %d/%d" % (ok, len(results)))

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
