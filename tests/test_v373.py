# -*- coding: utf-8 -*-
"""v3.7.3 原生数值快路径：功能一致性与拒绝边界。"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _run(args, **kw):
    return subprocess.run([PY, "-B"] + args, cwd=ROOT, capture_output=True,
                          text=True, **kw)


class TestFastNative(unittest.TestCase):
    def _build_and_run(self, src, extra=None):
        td = tempfile.mkdtemp(prefix="impor_v373_")
        ip = os.path.join(td, "case.impor")
        exe = os.path.join(td, "case.exe")
        with open(ip, "w", encoding="utf-8") as f:
            f.write(src)
        b = _run([os.path.join(ROOT, "Impor.py"), "build", ip, "-o", exe,
                  "--fast", "--no-cache"] + (extra or []))
        self.assertEqual(b.returncode, 0, b.stderr + b.stdout)
        r = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_fast_matches_interpreter(self):
        src = """var i = 0
var s = 0
while i < 300000 {
    if i % 2 == 0 { s = s + i } else { s = s - 1 }
    i = i + 1
}
print(s)

def f(n) {
    if n < 2 { return n }
    return f(n - 1) + f(n - 2)
}
print(f(12))
"""
        got = self._build_and_run(src)
        py = _run([os.path.join(ROOT, "Impor.py"), "run",
                   self._write_tmp(src)])
        self.assertEqual(got, py.stdout.strip())

    def _write_tmp(self, src):
        fd, p = tempfile.mkstemp(suffix=".impor")
        os.close(fd)
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        return p

    def test_fast_integer_helpers(self):
        got = self._build_and_run("print(min(3, 7) + max(2, 9) + abs(0 - 5))\n")
        self.assertEqual(got, "17")

    def test_fast_rejects_float(self):
        p = self._write_tmp("var x = 1.5\nprint(x)\n")
        b = _run([os.path.join(ROOT, "Impor.py"), "build", p,
                  "-o", os.path.join(tempfile.mkdtemp(), "x.exe"), "--fast", "--no-cache"])
        self.assertEqual(b.returncode, 3, b.stderr + b.stdout)
        self.assertIn("不支持", b.stderr)

    def test_fast_rejects_string_concat(self):
        p = self._write_tmp('var s = "a" + "b"\nprint(s)\n')
        b = _run([os.path.join(ROOT, "Impor.py"), "build", p, "-o",
                  os.path.join(tempfile.mkdtemp(), "x.exe"), "--fast", "--no-cache"])
        self.assertEqual(b.returncode, 3, b.stderr + b.stdout)
        self.assertIn("不支持", b.stderr)

    def test_fast_condition_logic(self):
        src = """var i = 0
var s = 0
while i < 20 {
    if i % 2 == 0 and i > 4 { s = s + i }
    i = i + 1
}
print(s)
"""
        got = self._build_and_run(src)
        py = _run([os.path.join(ROOT, "Impor.py"), "run", self._write_tmp(src)])
        self.assertEqual(got, py.stdout.strip())

    def test_fast_rejects_bool_storage(self):
        p = self._write_tmp("var b = 1 < 2\nprint(b)\n")
        b = _run([os.path.join(ROOT, "Impor.py"), "build", p,
                  "-o", os.path.join(tempfile.mkdtemp(), "x.exe"), "--fast", "--no-cache"])
        self.assertEqual(b.returncode, 3, b.stderr + b.stdout)
        self.assertIn("比较只能用于条件", b.stderr)

    def test_fast_negative_modulo(self):
        got = self._build_and_run("print((0 - 8) % 7)\nprint((0 - 1) % 7)\n")
        self.assertEqual(got.splitlines(), ["6", "6"])

    def test_fast_rejects_bool_print(self):
        p = self._write_tmp("print(1 < 2)\n")
        b = _run([os.path.join(ROOT, "Impor.py"), "build", p,
                  "-o", os.path.join(tempfile.mkdtemp(), "x.exe"), "--fast", "--no-cache"])
        self.assertEqual(b.returncode, 3, b.stderr + b.stdout)
        self.assertIn("布尔值", b.stderr)

    def test_generated_c_is_unboxed(self):
        from impor.interpreter import parse_source
        from impor.cfast import c_fast_transpile
        c = c_fast_transpile(parse_source("var i = 0\nwhile i < 10 { i = i + 1 }\nprint(i)\n"))
        self.assertIn("long long", c)
        self.assertNotIn("Val*", c)
        self.assertNotIn("rt_", c)


if __name__ == "__main__":
    unittest.main()
