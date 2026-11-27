# -*- coding: utf-8 -*-
"""v2.5.0 新增能力测试：fmt 配置 / lint 参数数量 / 条件断点表达式 / new 模板 / CLI help。"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.parser import Parser
from impor.lexer import tokenize
from impor.analysis import analyze
from impor.fmt import fmt_source, fmt_options_from_dir
from impor.interpreter import Interpreter, ImporException
import impor.cli as cli


def _analyze(src):
    body = src.split("\n", 1)[1]
    ast = Parser(tokenize(body)).program()
    return analyze(ast, body.split("\n"))


class TestFmtConfig(unittest.TestCase):
    def test_indent_width_applies(self):
        src = 'important<<DAY<<english("English"):\nvar a = 1\ndef f()\n    return a\n'
        self.assertIn("\n  return a\n", fmt_source(src, indent_width=2))
        self.assertIn("\n    return a\n", fmt_source(src, indent_width=4))

    def test_toml_fmt_section(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "impor.toml"), "w", encoding="utf-8") as f:
            f.write("[fmt]\nindent_width = 2\nmax_line_length = 40\n")
        opts = fmt_options_from_dir(d)
        self.assertEqual(opts["indent_width"], 2)
        self.assertEqual(opts["max_line_length"], 40)

    def test_check_fmt_with_long_lines(self):
        long = "var " + "x" * 50 + " = 1\n"
        src = 'important<<DAY<<english("English"):\n' + long
        from impor.fmt import check_fmt
        ok, formatted = check_fmt(src, max_line_length=30)
        self.assertIsInstance(formatted, str)
        self.assertTrue(ok)          # 格式正确（超长仅提示，不截断）
        self.assertIn("x" * 50, formatted)


class TestLintArgCount(unittest.TestCase):
    SRC = ('important<<DAY<<english("English"):\n'
           "def add(a, b)\n"
           "    return a + b\n"
           "var y = add(1, 2, 3)\n"   # 3 args, 期望 2
           "var z = len(1, 2)\n"      # 内建 len 期望 1
           "var w = add(1, 2)\n"      # OK
           "print(y)\n")

    def test_user_func_arity(self):
        an = _analyze(self.SRC)
        codes = [(i.code, i.msg) for i in an.issues if i.code == "arg-count"]
        self.assertTrue(any("add 需要 2 个参数，实际传入 3 个" in m for _, m in codes))
        self.assertTrue(any("len 需要 1 个参数，实际传入 2 个" in m for _, m in codes))
        # add(1,2) 与 print 不应误报
        self.assertEqual(len(codes), 2)


class TestEvalExprText(unittest.TestCase):
    def test_conditional_breakpoint_expr(self):
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        body = "var x = 10\nvar y = x + 5\nprint(y)"
        interp.run(Parser(tokenize(body)).program())
        interp.cur_env = interp.global_env
        self.assertTrue(interp.eval_expr_text("x > 5"))
        self.assertFalse(interp.eval_expr_text("x > 100"))
        self.assertEqual(interp.eval_expr_text("x + y"), 25)


class TestNewTemplate(unittest.TestCase):
    def test_new_templates(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                for tmpl, marker in (("script", "你好"), ("lib", "def greet"),
                                     ("game", "canvas_create")):
                    fname = f"t_{tmpl}.impor"
                    rc = cli.cmd_new([fname, "--template", tmpl])
                    self.assertEqual(rc, 0)
                    content = open(fname, encoding="utf-8").read()
                    self.assertIn(marker, content)
                    self.assertFalse(content.startswith("important<<"),
                                     "4.0.1 起新模板不再写头部标记")
                rc = cli.cmd_new(["bad", "--template", "nope"])
                self.assertNotEqual(rc, 0)
            finally:
                os.chdir(old)


class TestCliHelp(unittest.TestCase):
    def test_help_subcommand(self):
        buf = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = buf, buf
        try:
            rc = cli.cmd_help(["run"])
            rc2 = cli.cmd_help(["no-such-cmd"])
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(rc, 0)
        self.assertNotEqual(rc2, 0)
        self.assertIn("impor run FILE", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
