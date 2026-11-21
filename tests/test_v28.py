# -*- coding: utf-8 -*-
"""v2.8.0 新增能力测试：
assert 关键字（解释器 + 转译器一致）、Token 列号、语法诊断波浪线、
impor test 子命令、pkgmgr 依赖冲突检测、LSP 诊断列号。
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.parser import Parser
from impor.lexer import tokenize
from impor.interpreter import Interpreter, ImporException, parse_source
from impor.transpiler import transpile
from impor.diagnostics import parse_position, render_error
from impor.analysis import analyze
import impor.cli as cli


def _run(src, argv=None):
    out = []
    Interpreter(log_cb=out.append, argv=argv or []).run(parse_source(src))
    return out


def _transpile_run(src):
    import subprocess
    py = transpile(parse_source(src))
    td = tempfile.mkdtemp()
    pyf = os.path.join(td, "p.py")
    with open(pyf, "w", encoding="utf-8") as f:
        f.write(py)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, pyf], capture_output=True, text=True, env=env)
    return r.stdout, r.returncode


class TestAssert(unittest.TestCase):
    def test_assert_pass(self):
        out = _run("assert 1 + 1 == 2\nprint('ok')\n")
        self.assertEqual(out, ["ok"])

    def test_assert_fail_raises(self):
        with self.assertRaises(ImporException) as ctx:
            _run("assert 1 == 2\n")
        self.assertEqual(ctx.exception.name, "AssertionError")
        self.assertEqual(ctx.exception.lineno, 1)

    def test_assert_catchable(self):
        src = "try\n    assert 10 > 100\ncatch AssertionError e\n    print('c', e.msg)\n"
        self.assertEqual(_run(src), ["c 断言失败"])

    def test_assert_transpile_consistency(self):
        src = "assert 2 * 3 == 6\nprint('t')\n"
        out, rc = _transpile_run(src)
        self.assertEqual(rc, 0)
        self.assertIn("t", out)

    def test_assert_transpile_fail(self):
        src = "assert 1 == 2\n"
        _, rc = _transpile_run(src)
        self.assertNotEqual(rc, 0)


class TestTokenCol(unittest.TestCase):
    def test_col_numbers(self):
        ts = tokenize("var x = 10\nprint(x)\n")
        want = [("KEY", "var", 1, 1), ("ID", "x", 1, 5), ("NUM", 10, 1, 9),
                ("ID", "print", 2, 1), ("ID", "x", 2, 7)]
        got = [(t.type, t.value, t.line, t.col) for t in ts
               if t.type in ("ID", "NUM", "KEY")]
        self.assertEqual(got, want)

    def test_str_and_op_col(self):
        ts = tokenize("var s = \"hi\"\nvar r = r\"a\\\\b\"\n")
        cols = [(t.type, t.value, t.col) for t in ts if t.type == "STR"]
        self.assertEqual(cols[0][1], "hi")
        self.assertEqual(cols[0][2], 9)
        self.assertEqual(cols[1][1], "a\\\\b")


class TestDiagnostics(unittest.TestCase):
    def test_parse_position(self):
        line, col, msg = parse_position("第3行 列7: 期望 ID，得到 '='")
        self.assertEqual((line, col, msg), (3, 7, "期望 ID，得到 '='"))

    def test_parse_position_no_col(self):
        line, col, msg = parse_position("第3行: 表达式缺失")
        self.assertEqual((line, col, msg), (3, 0, "表达式缺失"))

    def test_parse_position_plain(self):
        line, col, msg = parse_position("随便一条消息")
        self.assertIsNone(line)
        self.assertEqual(msg, "随便一条消息")

    def test_render_error_caret(self):
        out = render_error(["var x = ", "print(x)"], 1, 9, "语法错误", "表达式缺失")
        self.assertIn("语法错误: 第1行 列9", out)
        self.assertIn("var x = ", out)
        self.assertIn("^", out)

    def test_parser_error_message_has_col(self):
        try:
            Parser(tokenize("var x = \n")).program()
            self.fail("should raise")
        except SyntaxError as e:
            line, col, msg = parse_position(str(e))
            self.assertEqual(line, 1)
            self.assertEqual(col, 9)  # v2.10：NEWLINE 带列号，定位到行尾 'var x = ' 之后
            self.assertIn("表达式缺失", msg)


class TestCliTestCmd(unittest.TestCase):
    def _write(self, d, name, text):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_test_cmd_pass_and_fail(self):
        d = tempfile.mkdtemp()
        self._write(d, "test_ok.impor", "assert 1 == 1\nprint('ok')\n")
        self._write(d, "test_bad.impor", "assert 1 == 2\n")
        self._write(d, "helper.impor", "// 不是测试\n")
        rc_pass = cli.cmd_test([os.path.join(d, "test_ok.impor")])
        self.assertEqual(rc_pass, cli.EXIT_OK)
        rc_dir = cli.cmd_test([d])
        self.assertEqual(rc_dir, cli.EXIT_RUNTIME)

    def test_test_cmd_no_files(self):
        d = tempfile.mkdtemp()
        rc = cli.cmd_test([d])
        self.assertEqual(rc, cli.EXIT_USAGE)


class TestPkgConflict(unittest.TestCase):
    def test_conflict_detected(self):
        from impor.pkgmgr import _write_toml, _check_dep_conflicts
        td = tempfile.mkdtemp()
        proj = os.path.join(td, "proj"); os.makedirs(proj)
        pkg = os.path.join(td, "pkg"); os.makedirs(pkg)
        _write_toml(os.path.join(proj, "impor.toml"),
                    {"project": {"name": "proj"}, "dependencies": {"x": ">=1.0"}})
        _write_toml(os.path.join(pkg, "impor.toml"),
                    {"project": {"name": "pkg"}, "dependencies": {"x": "==0.5"}})
        confs = _check_dep_conflicts(pkg, proj)
        self.assertEqual(confs, [("x", ">=1.0", "==0.5")])
        _write_toml(os.path.join(pkg, "impor.toml"),
                    {"project": {"name": "pkg"}, "dependencies": {"x": ">=1.0"}})
        self.assertEqual(_check_dep_conflicts(pkg, proj), [])


class TestLspDiagCol(unittest.TestCase):
    def test_parse_body_col(self):
        from impor.lsp import _parse_body
        ast, err = _parse_body("var x = \n")
        self.assertIsNotNone(err)
        self.assertEqual(err[0], 1)  # line
        self.assertEqual(err[2].startswith("表达式缺失"), True)


class TestLintWithAssert(unittest.TestCase):
    def test_assert_no_false_positive(self):
        src = "var x = 1\nassert x == 1\nprint(x)\n"
        an = analyze(parse_source(src), src.split("\n"))
        self.assertEqual([i.code for i in an.issues], [])


if __name__ == "__main__":
    unittest.main()
