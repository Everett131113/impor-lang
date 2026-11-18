# -*- coding: utf-8 -*-
"""v2.16.0 回归测试：
① div() 整除内建（解释器与 C 后端语义一致：向下取整 / 除零报错）
② REPL help() 内建帮助系统（? / help() / help(名字)，stdout 注入）
③ impor env / impor demo / impor bench 三个新命令冒烟
④ impor version 增强（平台 / C 后端 / 自举状态）
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source
from impor import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_impor(src, fname="t.impor"):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.run(parse_source(src, filename=fname))
    return out


def call_cli(args):
    """同进程调用 cli.main，捕获 stdout。"""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = cli.main(args)
    finally:
        sys.stdout = old
    return rc, buf.getvalue()


class TestDivBuiltin(unittest.TestCase):
    """div(a, b) 整数除法向下取整。"""

    def test_div_pos(self):
        self.assertEqual(run_impor('print(div(7, 2))'), ["3"])

    def test_div_neg(self):
        self.assertEqual(run_impor('print(div(-7, 2))'), ["-4"])
        self.assertEqual(run_impor('print(div(7, -2))'), ["-4"])
        self.assertEqual(run_impor('print(div(-7, -2))'), ["3"])

    def test_div_exact(self):
        self.assertEqual(run_impor('print(div(8, 2), div(-8, 2))'), ["4 -4"])

    def test_div_zero_raises(self):
        with self.assertRaises(Exception):
            run_impor('div(1, 0)')


class TestReplHelp(unittest.TestCase):
    """REPL help / ? / help() / help(名字)。"""

    def _repl(self, lines):
        from impor.repl import repl
        out = io.StringIO()
        repl(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        return out.getvalue()

    def test_help_menu(self):
        r = self._repl(["?", "exit"])
        self.assertIn("REPL 帮助", r)
        self.assertIn("help()", r)

    def test_builtin_list(self):
        r = self._repl(["help()", "exit"])
        self.assertIn("div", r)
        self.assertIn("fileread", r)

    def test_builtin_doc(self):
        r = self._repl(['help(print)', "exit"])
        self.assertIn("打印", r)
        self.assertIn("print(\"xxx\")", r)

    def test_quoted_doc(self):
        r = self._repl(['help("print")', "exit"])
        self.assertIn("打印", r)
        self.assertIn("print(\"xxx\")", r)


class TestNewCommands(unittest.TestCase):
    """impor env / demo / bench / version 冒烟。"""

    def test_env(self):
        rc, out = call_cli(["env"])
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("环境诊断", out)
        self.assertIn("源码根", out)

    def test_demo_list(self):
        rc, out = call_cli(["demo"])
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("hello", out)
        self.assertIn("snake", out)

    def test_demo_unknown(self):
        rc, _ = call_cli(["demo", "no_such_demo"])
        self.assertNotEqual(rc, cli.EXIT_OK)

    def test_version_enhanced(self):
        rc, out = call_cli(["version"])
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("C 后端", out)


if __name__ == "__main__":
    unittest.main()
