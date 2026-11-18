#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Python C 后端从当前种子重建 imporc.exe（v3.6.17 自举解锁路径）。

背景：2026-10-31 的旧 imporc.exe 无法在 300 秒内编译 86KB 的新种子，
形成“旧二进制编译不了新种子、新二进制只能靠旧二进制产出”的死锁。
本脚本绕过旧二进制，直接用仓库的 Python 前端 + tcc 生成新的 A，
再由 A 走 selfhost_check.py 的 A→B→C→D 闭环。

用法：
    python bootstrap/build_with_python.py            # 只生成 bootstrap/imporc_py.exe
    python bootstrap/build_with_python.py --install  # 生成并替换 impor/imporc.exe
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "bootstrap", "imporc.impor")
IMPOR_PY = os.path.join(ROOT, "Impor.py")
OUT = os.path.join(ROOT, "bootstrap", "imporc_py.exe")
INSTALL = os.path.join(ROOT, "impor", "imporc.exe")


def escape_line(line):
    """把字符串字面量里的 {} 转义为 \\{\\}，避免 Python 前端当成插值。"""
    out = []
    i = 0
    n = len(line)
    quote = None
    while i < n:
        c = line[i]
        if quote is None:
            if c == "#":
                out.append(line[i:])
                break
            if c == "/" and i + 1 < n and line[i + 1] == "/":
                out.append(line[i:])
                break
            if c == "/" and i + 1 < n and line[i + 1] == "*":
                end = line.find("*/", i + 2)
                if end == -1:
                    out.append(line[i:])
                    break
                out.append(line[i:end + 2])
                i = end + 2
                continue
            if c in ('"', "'"):
                quote = c
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(c)
            out.append(line[i + 1])
            i += 2
            continue
        if c == quote:
            quote = None
            out.append(c)
            i += 1
            continue
        if c in "{}":
            out.append("\\" + c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def make_build_seed(dst):
    with io.open(SEED, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    new_lines = []
    count = 0
    for line in lines:
        new = escape_line(line)
        count += new.count("\\{") - line.count("\\{") + new.count("\\}") - line.count("\\}")
        new_lines.append(new)
    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(new_lines) + "\n")
    print("build seed:", dst, "escaped braces:", count)
    return dst


def patch_stack(src, dst, reserve_bytes=8 * 1024 * 1024):
    shutil.copy(src, dst)
    with open(dst, "rb+") as f:
        data = f.read(0x200)
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        opt = e_lfanew + 4 + 20
        if opt > len(data):
            f.seek(0)
            data = f.read(opt + 0x100)
        magic = struct.unpack_from("<H", data, opt)[0]
        if magic == 0x10B:      # PE32
            off = opt + 0x48
            fmt = "<I"
        elif magic == 0x20B:    # PE32+
            off = opt + 0x30
            fmt = "<Q"
        else:
            raise SystemExit("unknown PE magic 0x%04x" % magic)
        old = struct.unpack_from(fmt, data, off)[0]
        f.seek(off)
        f.write(struct.pack(fmt, reserve_bytes))
        print("stack: 0x%04x %d -> %d" % (magic, old, reserve_bytes))


def main():
    work = tempfile.mkdtemp(prefix="impor_pybuild_")
    seed = os.path.join(work, "seed_bootstrap.impor")
    raw = os.path.join(work, "imporc_py_raw.exe")
    try:
        make_build_seed(seed)
        cmd = [sys.executable, IMPOR_PY, "build", seed, "-o", raw]
        print("run:", " ".join(cmd))
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0 or not os.path.exists(raw):
            raise SystemExit("Python 后端构建失败，rc=%d" % r.returncode)
        patch_stack(raw, OUT)
        print("A:", OUT, os.path.getsize(OUT), "bytes")
        if "--install" in sys.argv:
            shutil.copy2(OUT, INSTALL)
            print("installed:", INSTALL)
            print("next: python bootstrap/selfhost_check.py")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
