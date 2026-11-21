# -*- coding: utf-8 -*-
"""v2.11 回归测试：异常统一包装全覆盖（解释器/转译产物/CLI）、
所有自定义异常携带 filename/line/col、IMPOR_DEBUG 控制完整堆栈。"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.lexer import tokenize
from impor.parser import Parser
from impor.errors import ImporSyntaxError
from impor.interpreter import Interpreter, ImporException, ImporRuntimeError, parse_source
from impor.transpiler import transpile
import impor.runtime as rt


def run_impor(src, fname="test.impor"):
    out = []
    Interpreter(log_cb=out.append, filename=fname).run(parse_source(src, filename=fname))
    return out


class TestCompiledErrorStyle(unittest.TestCase):
    """编译产物（.py）运行时错误输出 Impor 风格：源码片段+波浪线，屏蔽 Python 栈。"""

    def _compile_and_run(self, src, env_extra=None, script="main.impor"):
        d = tempfile.mkdtemp()
        src_path = os.path.join(d, script)
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(src)
        body = src
        ast = parse_source(body, filename=script)
        py = transpile(ast, source_name=script, src_lines=src.splitlines())
        py_path = os.path.join(d, "out.py")
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(py)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        if env_extra:
            env.update(env_extra)
        r = subprocess.run([sys.executable, py_path], capture_output=True,
                           text=True, timeout=60, env=env, cwd=root)
        return r

    def test_compiled_div_zero_impor_style(self):
        r = self._compile_and_run("""def div(a, b) {
    return a / b
}
print(div(10, 0))
""")
        self.assertEqual(r.returncode, 1)
        self.assertIn("第2行 ZeroDivisionError", r.stderr)
        self.assertIn("return a / b", r.stderr)
        self.assertIn("^", r.stderr)                      # 波浪线
        self.assertNotIn("Traceback", r.stderr)           # 屏蔽 Python 栈
        self.assertNotIn("impor/runtime.py", r.stderr)

    def test_compiled_debug_env_shows_traceback(self):
        r = self._compile_and_run(
            "def div(a, b) {\n    return a / b\n}\nprint(div(10, 0))\n",
            env_extra={"IMPOR_DEBUG": "1"})
        self.assertIn("第2行 ZeroDivisionError", r.stderr)
        self.assertIn("Traceback", r.stderr)              # IMPOR_DEBUG=1 显示完整栈

    def test_compiled_impor_exception_style(self):
        r = self._compile_and_run("""exception Oops
raise Oops("boom")
""")
        self.assertIn("Oops", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestRuntimeCol(unittest.TestCase):
    """runtime.ImporException 携带 col；install_tb_map 支持 src_lines。"""

    def test_runtime_impor_exception_has_col(self):
        e = rt.ImporException("NameError", "x 未定义", 3, col=7)
        self.assertEqual(e.lineno, 3)
        self.assertEqual(e.col, 7)
        self.assertEqual(str(e), "NameError: x 未定义")


class TestInterpreterPosFill(unittest.TestCase):
    """解释器变量读取类 NameError 自动补齐位置。"""

    def test_name_error_has_position(self):
        try:
            run_impor("print(missing_var)\n")
            self.fail("should raise")
        except ImporException as e:
            self.assertEqual(e.name, "NameError")
            self.assertEqual(e.lineno, 1)
            self.assertGreaterEqual(e.col, 7)
            self.assertEqual(e.filename, "test.impor")

    def test_compound_assign_name_error_position(self):
        try:
            run_impor("var x = 1\nx += missing_var\n")
            self.fail("should raise")
        except ImporException as e:
            self.assertEqual(e.name, "NameError")
            self.assertEqual(e.lineno, 2)

    def test_class_def_base_error_position(self):
        try:
            run_impor("class A (missing_base) {}\n")
            self.fail("should raise")
        except ImporException as e:
            self.assertEqual(e.name, "NameError")
            self.assertEqual(e.lineno, 1)


class TestCliTopLevel(unittest.TestCase):
    """CLI 顶层统一捕获：未知内部异常不泄露 Python 栈。"""

    def test_cli_unknown_command_friendly(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); from impor.cli import main; sys.exit(main(['run', '不存在的文件_xyz.impor']))"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # run 文件不存在应输出 Impor 风格错误而非 Python Traceback
        self.assertNotIn("Traceback", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
