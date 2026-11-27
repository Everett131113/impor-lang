# -*- coding: utf-8 -*-
"""v4.2 回归：编译器可选（--cc）+ 快路径覆盖面（range 三形态 / 多参数 print / 浮点）。

本文件锁住三件事：
1. `impor build --cc` 能在 tcc 与 x86_64 gcc 之间切换，且两条路径产物都可运行；
2. `range(N) / range(A, B) / range(A, B, STEP)` 与解释器逐行一致，循环后变量保留最后一个值；
3. 浮点脚本走 double 数值域，输出按 Python repr 的最短往返规则，整数值带 `.0`。
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

from impor import cfast, cli
from impor.cfast import FastUnsupported
from impor.interpreter import Interpreter, strip_header, parse_source


def _parse(src):
    return parse_source(strip_header(src), filename="v42")


def _interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="v42").run(_parse(src))
    return out


def _run_c(src, extra=None):
    d = tempfile.mkdtemp(prefix="impor_v42_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe, "--no-cache"] + (extra or []))
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60, encoding="utf-8")
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


RANGE_3_SRC = (
    'var s = 0\n'
    'for i in range(0, 10, 3)\n'
    '    set s = s + i\n'
    'print(s)\n'
    'print(i)\n'
)

RANGE_NEG_SRC = (
    'var s = 0\n'
    'for i in range(5, 0, -1)\n'
    '    set s = s + i\n'
    'print(s)\n'
    'print(i)\n'
)

RANGE_EMPTY_SRC = (
    'var i = -7\n'
    'for i in range(5, 5)\n'
    '    print(999)\n'
    'print(i)\n'
)

PRINT_MULTI_SRC = (
    'var s = 0\n'
    'for i in range(1, 5)\n'
    '    set s = s + i\n'
    'print("合计", s, "结束")\n'
    'print(s, "是结果")\n'
)

FLOAT_SRC = (
    'var a = 1.5\n'
    'print(a)\n'
    'print(0.1)\n'
    'print(0.1 + 0.2)\n'
    'print(1.0)\n'
    'print(-0.0)\n'
    'print(a * 2)\n'
)


class TestRangeForms(unittest.TestCase):
    def test_three_arg_range(self):
        want = _interp(RANGE_3_SRC)
        rc, out = _run_c(RANGE_3_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want, ["18", "9"])

    def test_negative_step(self):
        want = _interp(RANGE_NEG_SRC)
        rc, out = _run_c(RANGE_NEG_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want, ["15", "1"])

    def test_empty_range_keeps_variable(self):
        want = _interp(RANGE_EMPTY_SRC)
        rc, out = _run_c(RANGE_EMPTY_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want, ["-7"])

    def test_loop_variable_after_loop(self):
        # 解释器与 C 都必须让 i 停在最后一个值（4），而不是越界值（5）。
        src = 'for i in range(5)\n    print(i)\nprint(i)\n'
        want = _interp(src)
        rc, out = _run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want[-1], "4")

    def test_step_zero_is_runtime_error(self):
        rc, out = _run_c('for i in range(0, 5, 0)\n    print(i)\n')
        self.assertNotEqual(rc, 0, "步长 0 必须是运行期错误，不能死循环")


class TestPrintAndFloat(unittest.TestCase):
    def test_multi_arg_print(self):
        want = _interp(PRINT_MULTI_SRC)
        rc, out = _run_c(PRINT_MULTI_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)

    def test_float_repr_matches_python(self):
        want = _interp(FLOAT_SRC)
        rc, out = _run_c(FLOAT_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want[0], "1.5")
        self.assertEqual(want[2], "0.30000000000000004")

    def test_float_var_declared_double(self):
        c = cfast.c_fast_transpile(_parse('var a = 1.5\nprint(a)\n'))
        self.assertIn("static double g_a", c)

    def test_mixed_int_float_var_falls_back(self):
        # x 先被赋整数 1、后被赋浮点：动态类型允许，静态 C 表示不了，必须回退。
        with self.assertRaises(FastUnsupported):
            cfast.c_fast_transpile(_parse('var x = 1\nprint(x)\nset x = x + 0.5\nprint(x)\n'))


class TestCompilerChoice(unittest.TestCase):
    def test_auto_prefers_x64_gcc(self):
        tcc = cli._find_tcc()
        path, kind, _opt = cli._resolve_build_cc(None, tcc, 2)
        gcc = cli.find_gcc64()
        if gcc:
            self.assertEqual(kind, "gcc")
            self.assertEqual(os.path.basename(path), os.path.basename(gcc))
        else:
            self.assertEqual(kind, "tcc")

    def test_explicit_tcc(self):
        tcc = cli._find_tcc()
        path, kind, _opt = cli._resolve_build_cc("tcc", tcc, 2)
        self.assertEqual(kind, "tcc")
        self.assertEqual(path, tcc)

    def test_opt_level_flag(self):
        tcc = cli._find_tcc()
        gcc = cli.find_gcc64()
        if not gcc:
            self.skipTest("没有 x86_64 gcc")
        path, kind, opt = cli._resolve_build_cc("gcc", tcc, 3)
        cmd = cli._cc_build_cmd(path, kind, "x.c", "x.exe", opt)
        self.assertIn("-O3", cmd)
        self.assertIn("-std=gnu99", cmd)

    def test_build_with_tcc_runs(self):
        rc, out = _run_c('var s = 0\nfor i in range(10)\n    set s = s + i\nprint(s)\n',
                         ["--cc", "tcc"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "45")


if __name__ == "__main__":
    unittest.main()
