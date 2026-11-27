# -*- coding: utf-8 -*-
"""v4.1 回归：`for 变量 in range(N)` 进入原生数值快路径（cfast）。

背景：v4.1 起 `impor build` 默认先尝试 cfast（不装箱的 long long）。
cfast 原先完全不接受 `For` 语句，导致最常见的计数循环
（`for i in range(1000000)`）静默回退到完整 Val 后端，速度差 6 倍。
本文件锁住四件事：能编、结果一致、range(N) 只求值一次、越界写法明确回退。
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
    return parse_source(strip_header(src), filename="cfast_for")


def _interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="cfast_for").run(_parse(src))
    return out


def _run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v41for_", dir=os.path.join(ROOT, "tests"))
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


LOOP_SRC = (
    'var s = 0\n'
    'for i in range(10)\n'
    '    set s = s + i\n'
    'print(s)\n'
)

BOUND_ONCE_SRC = (
    'var n = 3\n'
    'var s = 0\n'
    'for i in range(n)\n'
    '    set n = n + 1\n'
    '    set s = s + i\n'
    'print(s)\n'
    'print(n)\n'
)


class TestFastPathForRange(unittest.TestCase):
    def test_cfast_accepts_for_range(self):
        c = cfast.c_fast_transpile(_parse(LOOP_SRC))
        self.assertIn("for (", c, "应生成 C 的 for 循环")
        self.assertIn("long long _impor_stop", c, "range 的界应只求值一次")

    def test_output_matches_interpreter(self):
        want = _interp(LOOP_SRC)
        rc, out = _run_c(LOOP_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)

    def test_bound_evaluated_once(self):
        want = _interp(BOUND_ONCE_SRC)
        rc, out = _run_c(BOUND_ONCE_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)
        self.assertEqual(want, ["3", "6"])

    def test_range_two_args_supported(self):
        # v4.2：range(A, B) 不再是回退理由，应当直接进快路径且结果一致。
        src = 'var s = 0\nfor i in range(1, 5)\n    set s = s + i\nprint(s)\n'
        want = _interp(src)
        rc, out = _run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)

    def test_range_float_falls_back(self):
        with self.assertRaises(FastUnsupported):
            cfast.c_fast_transpile(_parse('for i in range(2.5)\n    print(i)\n'
                                          'print(1.5)\n'))

    def test_break_inside_for(self):
        src = ('var s = 0\n'
               'for i in range(100)\n'
               '    if i >= 5\n'
               '        break\n'
               '    set s = s + i\n'
               'print(s)\n')
        want = _interp(src)
        rc, out = _run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), want)


if __name__ == "__main__":
    unittest.main()
