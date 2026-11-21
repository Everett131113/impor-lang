# -*- coding: utf-8 -*-
"""v4.2：统一错误码 + 调用栈 + 跨后端诊断文案的一致性回归。"""

import io
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
IMPOR = os.path.join(ROOT, "Impor.py")


def run_impor(*args, cwd=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [PY, "-X", "utf8", IMPOR, *args],
        cwd=cwd or ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


class TestErrorCodes(unittest.TestCase):
    def test_table(self):
        from impor.diagnostics import ERROR_CODES, error_code
        codes = list(ERROR_CODES.values())
        self.assertEqual(len(codes), len(set(codes)), "错误码不能重复")
        for c in codes:
            self.assertTrue(c.startswith("E1"), c)
        self.assertEqual(error_code("ZeroDivisionError"), "E1001")
        self.assertEqual(error_code("NoSuchThing"), "E1099")

    def test_render_keeps_old_text_and_adds_code(self):
        from impor.diagnostics import render_diagnostic
        out = render_diagnostic("运行错误", "NameError: x 未定义", 0, 0, None,
                                subject="NameError", color=False)
        self.assertIn("运行错误: NameError: x 未定义 [E1008]", out)
        # 语法错误不加运行错误码
        out2 = render_diagnostic("语法错误", "缺少 )", 0, 0, None,
                                 subject="语法错误", color=False)
        self.assertNotIn("[E10", out2)


class TestDiagnosticConsistency(unittest.TestCase):
    SRC = (
        "def b(x)\n"
        "    return 1 / x\n"
        "def a()\n"
        "    return b(0)\n"
        "a()\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_diag_", dir=os.path.join(ROOT, "tests"))
        self.src = os.path.join(self.tmp, "e.impor")
        with io.open(self.src, "w", encoding="utf-8") as f:
            f.write(self.SRC)

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.unlink(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def test_interpreter_code_and_stack(self):
        r = run_impor("run", self.src)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ZeroDivisionError: division by zero [E1001]", r.stderr)
        self.assertIn("调用栈（自顶向下）:", r.stderr)
        self.assertIn("-> b（第", r.stderr)
        self.assertIn("a（第", r.stderr)

    def test_c_backend_code_and_stack(self):
        exe = os.path.join(self.tmp, "e.exe")
        b = run_impor("build", self.src, "-o", exe)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        last = None
        for _ in range(8):
            try:
                last = subprocess.run([exe], capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=20)
                break
            except (PermissionError, OSError) as e:  # 安全软件延迟扫描
                last = e
                time.sleep(1.5)
        self.assertNotIsInstance(last, Exception)
        self.assertEqual(last.returncode, 1, last.stdout)
        self.assertIn("ZeroDivisionError: division by zero [E1001]", last.stdout)
        self.assertIn("调用栈（自顶向下）:", last.stdout)
        self.assertIn("-> b（第", last.stdout)
        self.assertIn("a（第", last.stdout)

    def test_interpreter_catch_keeps_exit_zero(self):
        src = os.path.join(self.tmp, "c.impor")
        with io.open(src, "w", encoding="utf-8") as f:
            f.write("def f()\n"
                    "    try\n"
                    "        var x = 1 / 0\n"
                    "    catch ZeroDivisionError e\n"
                    "        print(\"caught:\", e.name)\n"
                    "f()\n")
        r = run_impor("run", src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("caught: ZeroDivisionError", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
