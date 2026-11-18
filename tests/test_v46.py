# -*- coding: utf-8 -*-
"""v3.7.0 回归测试：struct（显式字段记录）三端一致 + C 后端多参构造修复。

① 解释器：字段默认值、构造参数、字段读写
② Python 转译：同一负载输出一致
③ C 后端：同一负载输出一致（并覆盖 2 个构造实参的临时数组生成路径）
④ 自举编译器 imporc：同一负载输出一致
⑤ 语法边界：struct 体内非字段语句报语法错误
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")

from impor import cli
from impor.interpreter import Interpreter, parse_source, strip_header
from impor.parser import Parser
from impor.lexer import tokenize
from impor.errors import ImporSyntaxError

STRUCT_SRC = (
    'struct Point\n'
    '    var x = 0\n'
    '    var y = 0\n'
    'var p = new Point(3, 4)\n'
    'print("x=", p.x, "y=", p.y)\n'
    'set p.x = 10\n'
    'print("sum=", p.x + p.y)\n'
    'var q = new Point(0, 0)\n'
    'print("default=", q.x)\n'
)

EXPECTED = ["x= 3 y= 4", "sum= 14", "default= 0"]


def run_impor(src):
    out = []
    Interpreter(log_cb=out.append, filename="v46").run(
        parse_source(strip_header(src), filename="v46"))
    return out


def run_ctrans(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v46_")
    os.close(fd)
    exe = sp + ".exe"
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        for p in (sp, exe):
            try:
                os.unlink(p)
            except OSError:
                pass


def run_pytrans(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v46p_")
    os.close(fd)
    py = sp + ".py"
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
        for p in (sp, py):
            try:
                os.unlink(p)
            except OSError:
                pass


def run_imporc(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v46c_")
    os.close(fd)
    old = None
    if os.path.exists(ARGFILE):
        with io.open(ARGFILE, "r", encoding="utf-8") as f:
            old = f.read()
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write("run " + sp + "\n")
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT,
                           timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        try:
            os.unlink(sp)
        except OSError:
            pass
        if old is not None:
            with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
                f.write(old)


class TestStructInterpreter(unittest.TestCase):
    def test_fields_and_constructor(self):
        self.assertEqual(run_impor(STRUCT_SRC), EXPECTED)

    def test_body_must_be_field_only(self):
        bad = 'struct P\n    print("no")\n'
        with self.assertRaises(ImporSyntaxError):
            parse_source(strip_header(bad), filename="bad.impor")


class TestStructPythonTranspile(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_pytrans(STRUCT_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), EXPECTED)


class TestStructCBackend(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_ctrans(STRUCT_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), EXPECTED)


@unittest.skipUnless(os.path.isfile(IMPORC), "缺少 impor/imporc.exe")
class TestStructImporc(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_imporc(STRUCT_SRC)
        self.assertEqual(rc, 0, out)
        for line in EXPECTED:
            self.assertIn(line, out)


CLASS2_SRC = (
    'class Pair\n'
    '    def __init__(a, b)\n'
    '        set self.a = a\n'
    '        set self.b = b\n'
    'var q = new Pair(3, 4)\n'
    'print(q.a + q.b)\n'
)


class TestClassMultiArgCBackend(unittest.TestCase):
    def test_two_constructor_args(self):
        rc, out = run_ctrans(CLASS2_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), ["7"])


if __name__ == "__main__":
    unittest.main()
