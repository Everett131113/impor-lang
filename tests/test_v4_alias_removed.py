# -*- coding: utf-8 -*-
"""v4.0.0：12 类历史写法必须被 parser 拒绝，不能静默降级。"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from impor.errors import ImporSyntaxError
from impor.diagnostics import syntax_code
from impor.interpreter import parse_source


class TestAliasRemoved(unittest.TestCase):
    CASES = [
        ("func old(a)\n    return a\n", "func"),
        ("loop\n    break\n", "loop"),
        ("let x = 1\n", "let"),
        ("local x = 1\n", "local"),
        ("print 1, 2\n", "print 无括号"),
        ("print \"hello\"\n", "print 裸字符串"),
        ("print 1 + 2\n", "print 裸表达式"),
        ("var x = 41\nprint x\n", "print 裸变量"),
        ("x := 1\n", ":="),
        ("var x = 1\nx++\n", "x++"),
        ("if true && false {\n}\n", "&&"),
        ("if true || false {\n}\n", "||"),
        ("if true <> false {\n}\n", "<>"),
        ("if true then {\n}\n", "then"),
        ("unless true {\n}\n", "unless"),
        ("for each x in [1] {\n}\n", "for each"),
        ("for x of [1] {\n}\n", "for of"),
        ("for i in 1..3 {\n}\n", "1..3"),
        ("for i = 0; i < 3; i++ {\n}\n", "C 风格 for"),
        ("do {\n    print(1)\n} until true\n", "do..until"),
    ]

    def test_alias_is_rejected(self):
        for src, label in self.CASES:
            with self.subTest(label=label):
                with self.assertRaises(ImporSyntaxError) as raised:
                    parse_source(src, "test_v4_alias_removed.impor")
                self.assertEqual(syntax_code(str(raised.exception)), "E2002")

    def test_bare_print_is_not_silent_in_run_and_lint(self):
        src = 'print "hello"\n'
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bare_print.impor")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
            run = subprocess.run([sys.executable, "-X", "utf8", "Impor.py", "run", path],
                                 cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("E2002", run.stdout + run.stderr)

            lint = subprocess.run([sys.executable, "-X", "utf8", "Impor.py", "lint", path],
                                  cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            self.assertEqual(lint.returncode, 1, lint.stdout + lint.stderr)
            self.assertIn("E2002", lint.stdout + lint.stderr)
            self.assertNotIn("未发现问题", lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
