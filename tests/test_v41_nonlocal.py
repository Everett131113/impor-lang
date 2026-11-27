# -*- coding: utf-8 -*-
"""v4.1 回归：`nonlocal` 后不再强制 `set`（四条路径一致）。

背景：自举编译器 `bootstrap/imporc.impor` 一直允许 `nonlocal x = ...`，
参考解析器过去要求 `nonlocal set x = ...`，属于四端不一致。v4.1 起参考解析器
接受两种写法，旧写法保留兼容。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")

from impor import cli
from impor.interpreter import Interpreter, strip_header, parse_source

SRC = (
    'def make_counter(start)\n'
    '    var count = start\n'
    '    def inc()\n'
    '        nonlocal count = count + 1\n'
    '        return count\n'
    '    return inc\n'
    'def make_counter_legacy(start)\n'
    '    var count = start\n'
    '    def inc2()\n'
    '        nonlocal set count = count + 1\n'
    '        return count\n'
    '    return inc2\n'
    'var c1 = make_counter(0)\n'
    'print("bare:", c1(), c1(), c1())\n'
    'var c2 = make_counter_legacy(100)\n'
    'print("legacy:", c2(), c2())\n'
)
EXPECTED = ["bare: 1 2 3", "legacy: 101 102"]


def run_interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="v41nl").run(
        parse_source(strip_header(src), filename="v41nl"))
    return out


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v41nl_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    py = os.path.join(d, "case.py")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v41nc_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_imporc(src):
    d = tempfile.mkdtemp(prefix="impor_v41ni_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    old = None
    try:
        if os.path.exists(ARGFILE):
            with io.open(ARGFILE, "r", encoding="utf-8") as f:
                old = f.read()
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write('build "' + sp + '" -o "' + exe + '"\n')
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT, timeout=300)
        out = (r.stdout or "") + (r.stderr or "")
        if os.path.exists(exe):
            rr = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            out += rr.stdout
        return r.returncode, out
    finally:
        shutil.rmtree(d, ignore_errors=True)
        try:
            if old is not None:
                with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
                    f.write(old)
        except OSError:
            pass


class TestNonlocalOptionalSet(unittest.TestCase):
    def test_parser_accepts_both(self):
        for s in ('nonlocal x = x + 1', 'nonlocal set x = x + 1'):
            src = 'def outer()\n    var x = 1\n    def inner()\n        ' + s + '\n        return x\n    return inner\n'
            parse_source(strip_header(src), filename="v41nl_parse")

    def test_interpreter(self):
        self.assertEqual(run_interp(SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(SRC)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_imporc_backend(self):
        rc, out = run_imporc(SRC)
        self.assertEqual(rc, 0, out)
        # imporc 的诊断前缀从硬编码 "imporc.impor:" 换成了真实脚本路径（patch8b），
        # 这里按「路径 + ' : '」的通用形状过滤，不再依赖某个固定文件名。
        lines = [l for l in out.splitlines()
                 if not l.startswith("imporc.impor:") and " : " not in l]
        self.assertEqual(lines, EXPECTED)


if __name__ == "__main__":
    unittest.main()
