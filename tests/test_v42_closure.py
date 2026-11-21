# -*- coding: utf-8 -*-
"""v4.2 回归：嵌套函数 / 闭包（捕获 + nonlocal 改值）四路径一致。"""
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

CLOSURE_SRC = (
    'def make_counter(start)\n'
    '    var count = start\n'
    '    def inc()\n'
    '        nonlocal set count = count + 1\n'
    '        return count\n'
    '    return inc\n'
    'var c1 = make_counter(0)\n'
    'print("c1:", c1(), c1(), c1())\n'
    'var c2 = make_counter(100)\n'
    'print("c2:", c2())\n'
    'def plus_one(x)\n'
    '    return x + 1\n'
    'def apply_twice(f, x)\n'
    '    return f(f(x))\n'
    'print("apply_twice(plus_one, 3) =", apply_twice(plus_one, 3))\n'
)
EXPECTED = ["c1: 1 2 3", "c2: 101", "apply_twice(plus_one, 3) = 5"]


def run_interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="v42clo").run(
        parse_source(strip_header(src), filename="v42clo"))
    return out


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v42cp_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    py = os.path.join(d, "case.py")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True,
                           timeout=60)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v42cc_", dir=os.path.join(ROOT, "tests"))
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
    d = tempfile.mkdtemp(prefix="impor_v42ci_", dir=os.path.join(ROOT, "tests"))
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
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT,
                           timeout=300)
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


class TestClosureCrossBackend(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(CLOSURE_SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(CLOSURE_SRC)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(CLOSURE_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_imporc_backend(self):
        rc, out = run_imporc(CLOSURE_SRC)
        self.assertEqual(rc, 0, out)
        lines = [l for l in out.splitlines() if not l.startswith("imporc.impor:")]
        self.assertEqual(lines, EXPECTED)


if __name__ == "__main__":
    unittest.main()
