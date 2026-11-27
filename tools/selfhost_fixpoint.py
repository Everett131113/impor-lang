# -*- coding: utf-8 -*-
"""自举 fixpoint 判定（4.7.1）。

和 `bootstrap/selfhost_check.py` 的区别：那个脚本证明「三代 C 产物一致」，
本脚本把这件事做成**可进 CI 的判据**，并且把「二进制一致」也纳入：

    A = bootstrap/imporc_py.exe      由 Python 前端（impor build + tcc）造出的引导器
    B = gcc 编译「A 产出的 out2.c」得到的编译器
    C.c = B 编译种子 bootstrap/imporc.impor 得到的 C 源码
    C.exe = gcc 编译 C.c
    D.c = C.exe 编译种子得到的 C 源码
    D.exe = gcc 编译 D.c

判据 1（源码 fixpoint）：C.c 与 D.c 逐字节一致。
判据 2（二进制 fixpoint）：C.exe 与 D.exe 逐字节一致（需要 ld 支持
                          --no-insert-timestamp 去掉 PE 时间戳；否则 SKIP 并说明原因）。

注意：判据 1 比判据 2 更可靠——同一个 C 源码配不同 ld 版本/时间戳可以得到不同
二进制，但「编译器的稳定点」本质是它对自身源码产出的中间表示稳定。所以本脚本
**主判据是 C.c == D.c**，二进制一致是加分项。

用法：
    py -3.11 tools/selfhost_fixpoint.py            # 全流程
    py -3.11 tools/selfhost_fixpoint.py --json     # 机器可读结果
    py -3.11 tools/selfhost_fixpoint.py --quick    # 跳过 D（只判 B→C 可复现）
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "bootstrap", "imporc.impor")
A_EXE = os.path.join(ROOT, "bootstrap", "imporc_py.exe")
STACK = 64 * 1024 * 1024


def patch_stack_exe(path, size=STACK):
    """把 PE 的 SizeOfStackReserve 调到 64MB；编译器编大文件会栈溢出。"""
    try:
        with open(path, "rb+") as f:
            head = f.read(0x200)
            e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
            opt = e_lfanew + 4 + 20
            magic = struct.unpack_from("<H", head, opt)[0]
            if magic == 0x10B:
                off, fmt = opt + 0x48, "<I"
            elif magic == 0x20B:
                off, fmt = opt + 0x48, "<Q"
            else:
                return False
            f.seek(off)
            f.write(struct.pack(fmt, size))
            return True
    except Exception:
        return False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _pe_offsets(path):
    """返回 (TimeDateStamp 偏移, CheckSum 偏移, 段表)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(0x200)
            f.seek(0)
            data = f.read()
    except OSError:
        return None
    if head[:2] != b"MZ":
        return None
    lfa = struct.unpack_from("<I", head, 0x3C)[0]
    if data[lfa:lfa + 4] != b"PE\0\0":
        return None
    optsz = struct.unpack_from("<H", data, lfa + 20)[0]
    opt = lfa + 24
    nsec = struct.unpack_from("<H", data, lfa + 6)[0]
    secs = []
    for i in range(nsec):
        off = opt + optsz + i * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vaddr, vsize, rawptr, rawsize = struct.unpack_from("<IIII", data, off + 8 + 4)
        secs.append((name, rawptr, rawsize))
    return lfa + 8, opt + 64, secs


def pe_equal_no_ts(p1, p2):
    """忽略 PE 时间戳与校验和后比较两个 PE。

    本机 ld 是 binutils 2.24（TDM64 4.8.1），不认 `--no-insert-timestamp`，
    所以 PE 的 TimeDateStamp 一定不同，CheckSum 又是链接器按时间戳算出来的
    （也跟着变）。这两处与「编译器对自身源码是否稳定」无关，置零后比其余全部字节。
    """
    o1 = _pe_offsets(p1)
    o2 = _pe_offsets(p2)
    if not o1 or not o2:
        return None, "不是 PE"
    ts1, ck1, s1 = o1
    ts2, ck2, s2 = o2
    with open(p1, "rb") as f:
        d1 = bytearray(f.read())
    with open(p2, "rb") as f:
        d2 = bytearray(f.read())
    if len(d1) != len(d2):
        return False, "长度不同（%d vs %d）" % (len(d1), len(d2))
    for d, ts, ck in ((d1, ts1, ck1), (d2, ts2, ck2)):
        d[ts:ts + 4] = b"\0\0\0\0"
        d[ck:ck + 4] = b"\0\0\0\0"
    if bytes(d1) == bytes(d2):
        return True, "只差 PE 时间戳与校验和"
    diffs = [i for i in range(len(d1)) if d1[i] != d2[i]]
    loc = "、".join(sorted({_sec_of(s1, i) for i in diffs[:200]}))
    return False, "零化时间戳/校验和后仍有 %d 字节不同（%s）" % (len(diffs), loc)


def _sec_of(secs, off):
    for name, rawptr, rawsize in secs:
        if rawptr and rawptr <= off < rawptr + rawsize:
            return "%s+0x%x" % (name, off - rawptr)
    return "header@0x%x" % off


def gcc_path():
    """找 64 位 gcc。

    顺序：IMPOR_GCC → PATH 里的 gcc → 本机已知的 TDM 路径。
    最后一条很关键：只写 gcc 绝对路径还不够——gcc 调 cc1.exe 时，cc1 自己
    要去 PATH 里找 libgcc_s_seh-1.dll / libwinpthread-1.dll。PATH 没有 bin 目录时
    cc1 直接 0xC0000135（DLL not found）退出，gcc 只会返回 rc=1 且**一个字都不打印**，
    看起来像「编译器莫名其妙失败」。三次实跑卡在这里。
    """
    cand = os.environ.get("IMPOR_GCC")
    if cand and os.path.isfile(cand):
        return cand
    cand = shutil.which("gcc")
    if cand:
        return cand
    for p in (r"C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\gcc.exe",
              r"C:\Program Files\Dev-Cpp\MinGW64\bin\gcc.exe"):
        if os.path.isfile(p):
            return p
    return None


def run(cmd, cwd, timeout=600, tag="step", logdir=None):
    """执行一步，输出直接落盘。

    这里**不用** `capture_output=True`：种子一次编译会往 stderr 刷几百行警告，
    Windows 的管道缓冲写满后子进程会卡在写、父进程卡在读，看起来就是「整机假死」
    （第三次实跑就是这样把 shell 拖了一个小时）。改成重定向到文件 + stdin=DEVNULL，
    进度始终可查。
    """
    p = os.path.join(logdir or cwd, "_step_%s.log" % tag)
    try:
        with io.open(p, "wb") as f:
            r = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT %ss" % timeout
    tail = ""
    try:
        with io.open(p, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-900:]
    except OSError:
        pass
    return r.returncode, tail


STUB_SRC = os.path.join(ROOT, "bootstrap", "cc64stub.c")
STUB_EXE = os.path.join(tempfile.gettempdir(), "irverify", "cc64stub.exe")


def ensure_stub(gcc):
    """把 cc64stub.c 编成 PE（名字仍叫 cc64.bat，但内容是可执行文件）。

    种子用 system() 调 `.\bootstrap\cc64.bat`，而 Impor 运行时的 rt_system 走
    CreateProcessW —— 它只能启动 PE，`.bat` 会直接返回 FALSE（调用方看到 rc=-1）。
    所以这里给出的「bat」其实是 exe：CreateProcess 不检查扩展名。
    """
    os.makedirs(os.path.dirname(STUB_EXE), exist_ok=True)
    if (os.path.isfile(STUB_EXE) and os.path.isfile(STUB_SRC)
            and os.path.getmtime(STUB_EXE) >= os.path.getmtime(STUB_SRC)):
        return STUB_EXE
    rc, tail = run([gcc, "-std=gnu99", "-O2", "-o", STUB_EXE, STUB_SRC],
                   os.path.dirname(STUB_EXE), timeout=300, tag="stub-build")
    if rc != 0 or not os.path.isfile(STUB_EXE):
        raise RuntimeError("cc64 桩编译失败：%s" % tail)
    return STUB_EXE


def prepare_tree(work, gcc=None):
    """种子按 ghome 规则读 ./impor/c_runtime.c 与 ./bootstrap/cc64.bat，
    所以在临时目录里搭一个最小同名结构，避免往仓库根写中间文件。

    仓库里的 cc64.bat 是批处理；种子用 system() 调它，而 Windows 侧 rt_system
    用 CreateProcessW（只能启动 PE），批处理永远返回 -1。这里改用编译好的桩，
    并把它当作 cc64.bat 放进去（详见 ensure_stub）。
    """
    os.makedirs(os.path.join(work, "impor"), exist_ok=True)
    os.makedirs(os.path.join(work, "bootstrap"), exist_ok=True)
    rt = os.path.join(ROOT, "impor", "c_runtime.c")
    if os.path.isfile(rt):
        shutil.copyfile(rt, os.path.join(work, "impor", "c_runtime.c"))
    g = gcc or gcc_path()
    os.environ["IMPOR_CC64_GCC"] = g
    stub = ensure_stub(g)
    shutil.copyfile(stub, os.path.join(work, "bootstrap", "cc64.bat"))


def build_seed(work, gcc, log):
    """用当前 Python 前端 + 当前运行时重建引导器 A。

    为什么不能直接用 bootstrap/imporc_py.exe：那是个**历史产物**，它内嵌的
    rt_system 与当前 impor/c_runtime.c 不一定一致（实测发布的 1,037,824 字节版本
    在同一份命令串下 CreateProcessW 直接失败，返回 -1；连 C:\Windows\System32\where.exe
    都启动不起来）。自举链的语义应该是「当前工具链 → 种子 → C」，A 必须与当前树同步。
    """
    out = os.path.join(work, "A.exe")
    print("  [A] 用当前前端重建引导器…", flush=True)
    rc, tail = run([sys.executable, os.path.join(ROOT, "Impor.py"), "build", SEED,
                    "-o", out, "--no-fast"],
                   ROOT, timeout=1800, tag="seed-build", logdir=work)
    log.append("build-seed rc=%d tail=%r" % (rc, tail[-400:]))
    if rc != 0 or not os.path.exists(out):
        return None
    # 种子编译大文件递归很深，1MB 默认栈会 0xC00000FD
    patch_stack_exe(out, STACK)
    return out


def compile_seed(exe, work, tag, gcc, log):
    """让 exe 编译种子，返回 (out2.c 路径, out2.exe 路径, ok)。"""
    with io.open(os.path.join(work, "imporc_args.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("build arg_script.impor\n")
    shutil.copyfile(SEED, os.path.join(work, "arg_script.impor"))
    print("  [%s] 编译种子中…" % tag, flush=True)
    rc, tail = run([exe], work, timeout=600, tag="seed-%s" % tag)
    log.append("%s rc=%d tail=%r" % (tag, rc, tail[-400:]))
    c_path = os.path.join(work, "out2.c")
    exe_path = os.path.join(work, "out2.exe")
    if rc != 0 or not os.path.exists(c_path):
        return c_path, exe_path, False
    if os.path.exists(exe_path):
        patch_stack_exe(exe_path)
    return c_path, exe_path, True


def link_c(c_path, out_exe, gcc, work, log, no_ts=True):
    """编译 C 到 exe；no_ts 时尝试去掉 PE 时间戳以支持二进制比较。

    参数必须与种子内部那条路一致（`-std=gnu99 -O0 -w`），否则两个 exe 的
    编译条件不同，比较出来的差异与被测编译器无关 —— 第一次全量跑就是这样
    把 757,272 字节的差异误算成「二进制 fixpoint 不成立」。
    """
    base = [gcc, "-std=gnu99", "-O0", "-w", "-o", out_exe, c_path]
    tries = []
    if no_ts:
        tries.append(base + ["-Wl,--no-insert-timestamp"])
    tries.append(base)
    for i, cmd in enumerate(tries):
        tag = "gcc-%s%s" % (os.path.basename(out_exe), "-nots" if i == 0 and no_ts else "")
        rc, tail = run(cmd, work, timeout=600, tag=tag)
        log.append("gcc %s rc=%d tail=%r" % (tag, rc, tail[-400:]))
        if rc == 0 and os.path.exists(out_exe):
            patch_stack_exe(out_exe)
            return (i == 0 and no_ts)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Impor 自举 fixpoint")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quick", action="store_true", help="跳过 D 代")
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    ap.add_argument("--seed", default=None,
                    help="引导器 exe；默认用当前前端重建，指定则用给定文件")
    ap.add_argument("--use-vendored", action="store_true",
                    help="用仓库里的 bootstrap/imporc_py.exe（旧运行时，通常跑不通）")
    args = ap.parse_args(argv)

    res = {"ok": False, "stage": None, "src_fixpoint": None, "bin_fixpoint": None,
           "bin_skip": None, "sizes": {}, "hashes": {}, "log": []}
    log = res["log"]

    gcc = gcc_path()
    if not gcc:
        res["stage"] = "no-gcc"
        res["bin_skip"] = "缺少 64 位 gcc（32 位 tcc 会撞 2GB 地址空间上限 E1013）"
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else "SKIP：找不到 64 位 gcc，自举链无法验证")
        return 2
    os.environ["PATH"] = os.path.dirname(os.path.abspath(gcc)) + os.pathsep + os.environ.get("PATH", "")

    if not os.path.isfile(SEED):
        res["stage"] = "missing-seed"
        print("SKIP：缺 %s" % SEED)
        return 2

    work = tempfile.mkdtemp(prefix="impor_fixpoint_")
    try:
        prepare_tree(work, gcc)
        # A 必须复制到 work 根目录再跑：种子的 ghome 规则是「exe 所在目录名 == impor
        # 才取上级，否则为 .」，而 A 原本在 bootstrap\ 下 → ghome="."，拼出来的
        # `.\bootstrap\cc64.bat` 在 「bootstrap\imporc_py.exe」这种相对路径下会变成
        # `\bootstrap\cc64.bat`（Windows 解释为当前盘根目录），文件不存在 → system()
        # 返回 -1，种子只报一句「tcc 失败 rc= -1」。复制到 work 根后路径才是干净的
        # `.\bootstrap\cc64.bat`。
        a_exe = os.path.join(work, "A.exe")
        if args.seed:
            a_src = args.seed
        elif args.use_vendored:
            a_src = A_EXE
        else:
            a_src = build_seed(work, gcc, log)
            if not a_src:
                res["stage"] = "seed-build-fail"
                print("\n".join(log[-3:]))
                return 1
        if not os.path.isfile(a_src):
            res["stage"] = "missing-seed"
            print("SKIP：缺 %s" % a_src)
            return 2
        if os.path.abspath(a_src) != os.path.abspath(a_exe):
            shutil.copyfile(a_src, a_exe)
        patch_stack_exe(a_exe, STACK)
        a_c, _, ok = compile_seed(a_exe, work, "A", gcc, log)
        if not ok:
            res["stage"] = "A-fail"
            print("\n".join(log[-3:]))
            return 1
        res["sizes"]["a.c"] = os.path.getsize(a_c)
        shutil.copyfile(a_c, os.path.join(work, "a.c"))

        b_exe = os.path.join(work, "B.exe")
        no_ts = link_c(a_c, b_exe, gcc, work, log)
        if no_ts is None:
            res["stage"] = "B-link-fail"
            print("\n".join(log[-3:]))
            return 1
        res["sizes"]["B.exe"] = os.path.getsize(b_exe)

        # B 编种子 → C.c
        c_c, c_exe, ok = compile_seed(b_exe, work, "B", gcc, log)
        if not ok:
            res["stage"] = "B-fail"
            print("\n".join(log[-3:]))
            return 1
        shutil.copyfile(c_c, os.path.join(work, "C.c"))
        res["sizes"]["C.c"] = os.path.getsize(c_c)

        if args.quick:
            res["src_fixpoint"] = "SKIP(quick)"
            res["ok"] = True
            res["stage"] = "quick-ok"
            return 0

        # C.exe 编种子 → D.c
        if not os.path.exists(c_exe):
            # 种子自举时产出的 out2.exe 可能被上一步覆盖成 C.c 的产物；重新链一遍
            c_exe = os.path.join(work, "C.exe")
            if link_c(c_c, c_exe, gcc, work, log) is None:
                res["stage"] = "C-link-fail"
                return 1
        else:
            shutil.copyfile(c_exe, os.path.join(work, "C.exe"))
            c_exe = os.path.join(work, "C.exe")

        d_c, d_exe, ok = compile_seed(c_exe, work, "C", gcc, log)
        if not ok:
            res["stage"] = "C-fail"
            print("\n".join(log[-3:]))
            return 1
        # 种子固定把 C 写到 out2.c；复制成 D.c 再链接，保证与 C.c 那次
        # 的「编译单元文件名」一致 —— 否则调试段里源文件名不同，会伪装成
        # 二进制不一致（第一次全量就踩了这个：C.c vs out2.c）。
        d_c_file = os.path.join(work, "D.c")
        shutil.copyfile(d_c, d_c_file)
        res["sizes"]["D.c"] = os.path.getsize(d_c_file)

        same_src = io.open(os.path.join(work, "C.c"), encoding="utf-8", errors="replace").read() == \
            io.open(d_c_file, encoding="utf-8", errors="replace").read()
        res["src_fixpoint"] = same_src
        res["hashes"]["C.c"] = sha256(os.path.join(work, "C.c"))
        res["hashes"]["D.c"] = sha256(d_c_file)

        d_exe = os.path.join(work, "D.exe")
        c2_exe = os.path.join(work, "C2.exe")
        ok_d = link_c(d_c_file, d_exe, gcc, work, log) is not None
        # 两次编译必须用**同一个源文件名**：gcc 会把编译单元名写进调试段，
        # 名字不同（C.c vs out2.c）就会伪装成二进制不一致 —— 第一次全量就这样
        # 白报 8 字节差异。C.c 与 D.c 内容已判定相同，直接覆盖同一路径再编。
        if same_src:
            shutil.copyfile(os.path.join(work, "C.c"), d_c_file)
            ok_c = link_c(d_c_file, c2_exe, gcc, work, log) is not None
        else:
            ok_c = False
        if not (ok_c and ok_d):
            res["bin_fixpoint"] = "SKIP(link-fail)"
            res["bin_skip"] = "同参重链失败"
        else:
            equal, why = pe_equal_no_ts(c2_exe, d_exe)
            res["bin_fixpoint"] = bool(equal) if equal is not None else "SKIP(not-pe)"
            res["bin_note"] = why
            if equal is None:
                res["bin_skip"] = why
            res["hashes"]["C2.exe"] = sha256(c2_exe)
            res["hashes"]["D.exe"] = sha256(d_exe)

        res["ok"] = bool(same_src) and res["bin_fixpoint"] in (True, "SKIP(link-fail)")
        res["stage"] = "done"
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("A 编译种子 → a.c  %d 字节" % res["sizes"].get("a.c", 0))
            print("B = gcc(a.c)      %d 字节" % res["sizes"].get("B.exe", 0))
            print("C.c = B 编种子    %d 字节" % res["sizes"].get("C.c", 0))
            print("D.c = C 编种子    %d 字节" % res["sizes"].get("D.c", 0))
            print("源码 fixpoint（C.c == D.c）：%s" % ("✅" if same_src else "❌"))
            if res["bin_fixpoint"] is True:
                bin_txt = "✅（%s）" % (res.get("bin_note") or "")
            elif res["bin_fixpoint"] is False:
                bin_txt = "❌（%s）" % (res.get("bin_note") or "")
            else:
                bin_txt = "SKIP（%s）" % (res.get("bin_skip") or "无法链接比较")
            print("二进制 fixpoint（C.exe == D.exe）：%s" % bin_txt)
            print("\n结论：%s" % ("自举成立（编译器对自身源码已到稳定点）"
                                if res["ok"] else "自举不成立"))
        return 0 if res["ok"] else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print("工作目录：%s" % work)


if __name__ == "__main__":
    sys.exit(main())
