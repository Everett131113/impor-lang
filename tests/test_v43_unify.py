# -*- coding: utf-8 -*-
"""v4.3 验收：impor explain / build 统计 / 统一错误 / imporc from+包+嵌套导入。"""
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"


def run_cli(args, timeout=180):
    r = subprocess.run([PY, "-X", "utf8", os.path.join(ROOT, "Impor.py")] + args,
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class TestExplain(unittest.TestCase):
    def test_single_code(self):
        rc, out = run_cli(["explain", "E1001"])
        self.assertEqual(rc, 0)
        for part in ("E1001", "ZeroDivisionError", "为什么", "修复", "示例"):
            self.assertIn(part, out)

    def test_exception_name(self):
        rc, out = run_cli(["explain", "ZeroDivisionError"])
        self.assertEqual(rc, 0)
        self.assertIn("E1001", out)

    def test_list_and_unknown(self):
        rc, out = run_cli(["explain"])
        self.assertEqual(rc, 0)
        self.assertIn("E1001", out)
        self.assertIn("E1099", out)
        rc2, out2 = run_cli(["explain", "E9999"])
        self.assertEqual(rc2, 3)
        self.assertIn("未知错误码", out2)


class TestBuildAndError(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(ROOT, "tests", "_v43_tmp")
        os.makedirs(self.tmp, exist_ok=True)
        with open(os.path.join(self.tmp, "mod.impor"), "w", encoding="utf-8", newline="\n") as f:
            f.write("export add, base\n\ndef add(a, b)\n    return a + b\n\nvar base = 10\n")
        with open(os.path.join(self.tmp, "main.impor"), "w", encoding="utf-8", newline="\n") as f:
            f.write("from mod import add, base\nprint(add(1, base))\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_reports_and_runs(self):
        exe = os.path.join(self.tmp, "main.exe")
        rc, out = run_cli(["build", "tests/_v43_tmp/main.impor", "-o", exe])
        self.assertEqual(rc, 0, out)
        for part in ("构建成功", "大小：", "耗时：", "模块：1 个（mod）"):
            self.assertIn(part, out)
        r = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertEqual(r.stdout.strip(), "11")

    def test_c_error_has_code_snippet_why(self):
        src = os.path.join(self.tmp, "err.impor")
        exe = os.path.join(self.tmp, "err.exe")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("def f(x)\n    return 10 / x\nprint(f(0))\n")
        rc, out = run_cli(["build", "tests/_v43_tmp/err.impor", "-o", exe])
        self.assertEqual(rc, 0, out)
        r = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        text = (r.stdout or "") + (r.stderr or "")
        for part in ("[E1001]", "为什么", "修复", "return 10 / x", "^", "调用栈"):
            self.assertIn(part, text)


class TestImporcImports(unittest.TestCase):
    def test_from_package_nested(self):
        if not os.path.exists(os.path.join(ROOT, "imporc.cmd")):
            self.skipTest("imporc.cmd 不存在")
        tmp = os.path.join(ROOT, "tests", "_v43_tmp")
        try:
            os.makedirs(os.path.join(tmp, "pkg"), exist_ok=True)
            with open(os.path.join(tmp, "mod.impor"), "w", encoding="utf-8", newline="\n") as f:
                f.write("export add, base\n\ndef add(a, b)\n    return a + b\n\nvar base = 10\n")
            with open(os.path.join(tmp, "pkg", "util.impor"), "w", encoding="utf-8", newline="\n") as f:
                f.write("export twice\n\ndef twice(x)\n    return x * 2\n")
            main = os.path.join(tmp, "imp_main.impor")
            with open(main, "w", encoding="utf-8", newline="\n") as f:
                f.write("from mod import add, base\nimport pkg.util as u\n"
                        "def calc(x)\n    import mod as m2\n"
                        "    return add(x, base) + u.twice(x) + m2.base\n"
                        "print(calc(1))\n")
            exe = os.path.join(tmp, "imp_main.exe")
            cmdline = 'cmd /c ""{}" build "{}" -o "{}""'.format(
                os.path.join(ROOT, "imporc.cmd"),
                os.path.join("tests", "_v43_tmp", "imp_main.impor"), exe)
            r = subprocess.run(cmdline, cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            text = (r.stdout or "") + (r.stderr or "")
            self.assertEqual(r.returncode, 0, text)
            self.assertTrue(os.path.exists(exe), text)
            rr = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
            self.assertEqual(rr.stdout.strip(), "23")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            with open(os.path.join(ROOT, "imporc_args.txt"), "w", encoding="utf-8",
                      newline="\n") as f:
                f.write("build bootstrap\\imporc.impor -o imporc_self_raw.exe")


if __name__ == "__main__":
    unittest.main()
