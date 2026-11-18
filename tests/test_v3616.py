# -*- coding: utf-8 -*-
"""v3.6.16：Tk 花括号字面量与内建单一注册源的无窗口回归。

不打开 Tk 窗口，只验证：
- `"{Segoe UI} 9"` 被当作字面文本，不再把 Segoe 当变量；
- `"\\{Segoe UI\\} 9"` 旧转义写法仍然可用；
- 同一字符串里混用字面花括号与 `{expr}` 插值时，转译产物仍是合法 Python；
- 内建名单只在 `impor.builtin_manifest` 登记，解释器与 runtime 都能对账。
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "apps", "impor_focus", "focus.impor")

SOURCE = r'''var size = 9
var family = "Segoe UI"
var font = "{Segoe UI} {size}"
var escaped = "\{Segoe UI\} {size}"
var greet = "你好 {family}"
print(font)
print(escaped)
print(greet)
'''

EXPECTED = ["{Segoe UI} 9", "{Segoe UI} 9", "你好 Segoe UI"]


class TestBuiltinManifest(unittest.TestCase):
    def test_one_registry_covers_interpreter(self):
        from impor.builtins import make_builtins
        from impor.builtin_manifest import (
            ALL_BUILTINS, RUNTIME_BUILTINS, HOST_BUILTINS, INTERPRETER_ONLY_BUILTINS,
        )

        self.assertEqual(
            len(ALL_BUILTINS),
            len(RUNTIME_BUILTINS) + len(HOST_BUILTINS) + len(INTERPRETER_ONLY_BUILTINS),
        )
        self.assertFalse(RUNTIME_BUILTINS & HOST_BUILTINS)
        self.assertFalse(RUNTIME_BUILTINS & INTERPRETER_ONLY_BUILTINS)
        self.assertFalse(HOST_BUILTINS & INTERPRETER_ONLY_BUILTINS)

        class Stub:
            gui_root = None
            sandbox_mode = False

        self.assertEqual(set(make_builtins(Stub())), ALL_BUILTINS)

    def test_runtime_exports_every_registered_builtin(self):
        import impor.runtime as rt
        from impor.builtin_manifest import RUNTIME_BUILTINS

        for name in RUNTIME_BUILTINS:
            self.assertIn(name, rt.__all__, name)
            self.assertTrue(callable(getattr(rt, name)), name)


class TestTkFontBraces(unittest.TestCase):
    def _env(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _write_source(self, td):
        src = os.path.join(td, "tk_font.impor")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write(SOURCE)
        return src

    def test_interpreter_literal_and_interpolated_braces(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._write_source(td)
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "Impor.py"), "run", src],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60, env=self._env(),
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip().splitlines(), EXPECTED)

    def test_compiled_python_literal_and_interpolated_braces(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._write_source(td)
            out = os.path.join(td, "tk_font_generated.py")
            c = subprocess.run(
                [sys.executable, os.path.join(ROOT, "Impor.py"), "compile", src, "-o", out],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=self._env(),
            )
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            with open(out, "r", encoding="utf-8") as f:
                generated = f.read()
            self.assertIn("{{Segoe UI}}", generated)

            r = subprocess.run(
                [sys.executable, out], cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120, env=self._env(),
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(r.stdout.strip().splitlines(), EXPECTED)

    def test_focus_app_dogfoods_space_font_family(self):
        with open(APP, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("{Segoe UI}", src)
        self.assertNotIn('"font": "Arial', src)


if __name__ == "__main__":
    unittest.main()
