# -*- coding: utf-8 -*-
"""Impor 三后端一致性测试（conformance）。

同一份脚本分别走三条执行路径：
  1) 解释器      python Impor.py run  case.impor
  2) C 后端      python Impor.py build case.impor -o out.exe
  3) 自举编译器  impor/imporc.exe build case.impor -o out.exe（参数走 imporc_args.txt）

要求三条路径的 stdout 逐字节一致。任何不一致即报告差异并以非零码退出。

用法：
  python conformance/run_conformance.py            # 全部用例
  python conformance/run_conformance.py --ci       # CI 模式（失败即 exit 1）
  python conformance/run_conformance.py --only 05  # 只跑编号含 05 的用例
  python conformance/run_conformance.py --json out.json
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_DIR = os.path.join(ROOT, "conformance", "cases")
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")
PY = sys.executable or "python"
# v4.3.2：120s 在慢机 / 并发压测下会误判 TIMEOUT（实测过一次 99/100，
# 单独重跑 100/100）。单次调用给到 240s，失败仍然计入失败，不放宽判定口径。
TIMEOUT = 240


def _norm(text):
    """统一换行后比较；仅做 CRLF→LF，不做编码以外的归一。"""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def run_interpreter(case):
    r = subprocess.run([PY, os.path.join(ROOT, "Impor.py"), "run", case],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=TIMEOUT, cwd=ROOT)
    return r.returncode, _norm(r.stdout), _norm(r.stderr)


def _run_exe(exe):
    # 新生成的 exe 可能被 Windows 安全软件短暂扫描锁定；等待后重试
    last = None
    for _ in range(3):
        try:
            r = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=TIMEOUT, cwd=ROOT)
            return r.returncode, _norm(r.stdout), _norm(r.stderr)
        except PermissionError as exc:
            last = exc
            time.sleep(2)
    raise last


def run_ctrans(case, workdir):
    exe = os.path.join(workdir, "ctrans_out.exe")
    b = subprocess.run([PY, os.path.join(ROOT, "Impor.py"), "build", case, "-o", exe],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=TIMEOUT, cwd=ROOT)
    if not os.path.isfile(exe):
        return "BUILD_FAIL", "", _norm(b.stdout) + _norm(b.stderr)
    return _run_exe(exe)


def run_imporc(case, workdir):
    exe = os.path.join(workdir, "imporc_out.exe")
    # 自举编译器的参数文件不支持含空格的路径（例如桌面目录
    # “Impor(I++) 3.0.0”）；先把用例复制到无空格临时目录再编译。
    local_case = os.path.join(workdir, "case.impor")
    try:
        shutil.copyfile(case, local_case)
    except OSError as exc:
        return "BUILD_FAIL", "", "copy case failed: %s" % exc
    # imporc 从“当前目录”读 imporc_args.txt。放到全局仓库 cwd 会和并行任务
    # 撞车（例如测试套件和 tools/support_matrix.py 同时跑），因此每个用例
    # 用自己的临时目录当 cwd，参数文件天然隔离。
    argfile = os.path.join(workdir, "imporc_args.txt")
    b = None
    try:
        # 新拷贝的文件偶尔被 Windows 安全软件锁住，imporc 的 fileread 会失败；
        # 这是环境抖动，不是语义差异，重试几次即可。
        for attempt in range(3):
            with io.open(argfile, "w", encoding="utf-8", newline="\n") as f:
                # imporc_args.txt 按空格切分参数；路径用双引号包裹，避免含空格的目录被截断
                f.write('build "%s" -o "%s"' % (local_case, exe))
            b = subprocess.run([IMPORC], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=TIMEOUT, cwd=workdir)
            if os.path.isfile(exe):
                break
            err = (b.stdout or "") + (b.stderr or "")
            if "fileread" not in err and "无法打开文件" not in err:
                break
            time.sleep(1.5)
    finally:
        try:
            os.remove(argfile)
        except OSError:
            pass
        # v4.3.2：不再读写仓库根目录的 imporc_args.txt。
        # imporc 已按 cwd 读参数（这里 cwd=workdir），旧代码仍然
        # "读出原文 → 写 → 恢复" 全局文件，两个 conformance 同时跑时
        # 会互相覆盖，造成假失败。每个用例只碰自己的临时目录。
    if not os.path.isfile(exe):
        return "BUILD_FAIL", "", _norm(b.stdout) + _norm(b.stderr)
    return _run_exe(exe)


def first_diff(a, b):
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<缺失行>"
        y = lb[i] if i < len(lb) else "<缺失行>"
        if x != y:
            return "第%d行: %r != %r" % (i + 1, x, y)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ci", action="store_true", help="CI 模式：任何不一致返回 1")
    ap.add_argument("--only", default="", help="只跑文件名包含该子串的用例")
    ap.add_argument("--sample", type=int, default=0,
                    help="按排序等距抽取 N 个用例（0 = 全量；用于慢机/陌生人测试）")
    ap.add_argument("--json", default="", help="把结果写入 JSON 文件")
    args = ap.parse_args()

    if not os.path.isdir(CASES_DIR):
        print("找不到 conformance/cases 目录")
        return 2
    cases = sorted(f for f in os.listdir(CASES_DIR) if f.endswith(".impor"))
    if args.only:
        cases = [f for f in cases if args.only in f]
    if args.sample > 0 and args.sample < len(cases):
        picked = [cases[i * len(cases) // args.sample] for i in range(args.sample)]
        # 等距取整可能重复，去重后再补足到目标数量
        cases = sorted(set(picked))
        if len(cases) < args.sample:
            for name in sorted(f for f in os.listdir(CASES_DIR) if f.endswith(".impor")):
                if name not in cases and (not args.only or args.only in name):
                    cases.append(name)
                if len(cases) >= args.sample:
                    break
            cases.sort()
    if not cases:
        print("没有匹配的用例")
        return 2

    results = []
    fails = 0
    print("Impor conformance —— 三后端逐字节比对（%d 个用例）" % len(cases))
    print("-" * 72)
    for name in cases:
        case = os.path.join(CASES_DIR, name)
        workdir = tempfile.mkdtemp(prefix="conf_")
        try:
            rc_i, out_i, err_i = run_interpreter(case)
            rc_c, out_c, err_c = run_ctrans(case, workdir)
            rc_m, out_m, err_m = run_imporc(case, workdir)
        except subprocess.TimeoutExpired:
            results.append({"case": name, "status": "TIMEOUT"})
            print("%-28s TIMEOUT" % name)
            fails += 1
            continue
        finally:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)

        same = out_i == out_c == out_m
        ok_rc = (rc_i == 0 and rc_c == 0 and rc_m == 0)
        status = "OK" if (same and ok_rc) else "DIFF"
        if status != "OK":
            fails += 1
        results.append({
            "case": name, "status": status,
            "rc": [rc_i, rc_c, rc_m],
            "out": [out_i, out_c, out_m],
            "err": [err_i, err_c, err_m],
        })
        print("%-28s %-6s rc=%s" % (name[:-6], status, (rc_i, rc_c, rc_m)))
        if status != "OK":
            print("   解释器: %r" % out_i[:160])
            print("   ctrans : %r" % out_c[:160])
            print("   imporc : %r" % out_m[:160])
            if out_i != out_c:
                print("   interp vs ctrans " + first_diff(out_i, out_c))
            if out_i != out_m:
                print("   interp vs imporc " + first_diff(out_i, out_m))
            if err_i.strip():
                print("   interp stderr: %s" % err_i.strip()[:160])
            if err_c.strip():
                print("   ctrans  stderr: %s" % err_c.strip()[:160])
            if err_m.strip():
                print("   imporc  stderr: %s" % err_m.strip()[:160])

    print("-" * 72)
    print("共 %d 个用例，通过 %d，失败 %d" % (len(cases), len(cases) - fails, fails))
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"total": len(cases), "failed": fails, "results": results},
                      f, ensure_ascii=False, indent=2)
        print("报告已写入 %s" % args.json)
    if fails and args.ci:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
