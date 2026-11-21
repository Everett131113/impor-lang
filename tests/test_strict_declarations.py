# -*- coding: utf-8 -*-
"""v3.13 回归：声明语义必须四端一致（解释器 / lint / build / compile）。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _run(args, cwd):
    p = subprocess.run([PY, "-X", "utf8", os.path.join(ROOT, "Impor.py")] + args,
                       cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class StrictDeclarationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="impor_v313_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _assert_rejected(self, src, expected=("未声明",)):
        for args in (["run", src], ["lint", src],
                     ["build", src, "--no-cache"],
                     ["compile", src, "-o", os.path.join(self.dir, "out.py")]):
            rc, out = _run(args, self.dir)
            self.assertNotEqual(rc, 0, (args, rc, out))
            self.assertTrue(any(x in out for x in expected), (args, out))

    def test_set_undeclared_rejected_everywhere(self):
        self._assert_rejected(self._write("bad_set.impor", "set q = 5\nprint(q)\n"))

    def test_bare_undeclared_rejected_everywhere(self):
        self._assert_rejected(self._write("bad_bare.impor", "w = 7\nprint(w)\n"))

    def test_walrus_undeclared_rejected_everywhere(self):
        # v4.0：`:=` 不再是赋值别名，直接按语法错误拒绝，不再进入声明检查。
        self._assert_rejected(self._write("bad_walrus.impor", "z := 5\nprint(z)\n"),
                              expected=("语法错误", ":="))

    def test_declared_update_allowed(self):
        src = self._write("ok.impor", "var w = 1\nw = 2\nprint(w)\n")
        for args in (["run", src], ["lint", src],
                     ["build", src, "--no-cache"],
                     ["compile", src, "-o", os.path.join(self.dir, "ok.py")]):
            rc, out = _run(args, self.dir)
            self.assertEqual(rc, 0, (args, rc, out))

    def test_function_scope_update_allowed(self):
        src = self._write("ok_fn.impor",
                          "def f()\n    var w = 1\n    w = 2\n    return w\nprint(f())\n")
        rc, out = _run(["run", src], self.dir)
        self.assertEqual(rc, 0, (rc, out))
        self.assertIn("2", out)


if __name__ == "__main__":
    unittest.main()
