# -*- coding: utf-8 -*-
"""v3.8.3 回归：imporc 门禁可信度 + C ABI v2（字符串释放 / size_t / 句柄）。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")

LIB_SRC = """export def add(a: int, b: int) -> int {
    return a + b
}

export def greet(name: str) -> str {
    return "Hello, " + name
}

export def twice(n: size_t) -> size_t {
    return n * 2
}

export def same_handle(h: handle) -> handle {
    return h
}
"""

DEMO_C = """#include <stdio.h>
#include "lib.h"

int main(void) {
    const char *a = impor_greet("A");
    const char *b = impor_greet("B");
    printf("add=%lld greet1=%s greet2=%s\\n", impor_add(2, 3), a, b);
    impor_free_str(a);
    impor_free_str(b);
    printf("twice=%zu handle=%p\\n", impor_twice((size_t)21), impor_same_handle((void *)0x1234));
    return 0;
}
"""


def _run(cmd, cwd, timeout=90):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


class TestCAbiV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v383_cabi_")
        self.src = os.path.join(self.tmp, "lib.impor")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(LIB_SRC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_header_declares_free_and_v2_types(self):
        dll = os.path.join(self.tmp, "lib.dll")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", self.src,
                     "-o", dll], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        header = os.path.join(self.tmp, "lib.h")
        with open(header, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("impor_free_str", text)
        self.assertIn("size_t impor_twice(size_t a0)", text)
        self.assertIn("void * impor_same_handle(void * a0)", text)
        self.assertIn("#include <stddef.h>", text)
        def_file = os.path.join(self.tmp, "lib.def")
        with open(def_file, "r", encoding="utf-8") as f:
            self.assertIn("impor_free_str", f.read())

    def test_c_demo_frees_strings_and_roundtrips_handle(self):
        dll = os.path.join(self.tmp, "lib.dll")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", self.src,
                     "-o", dll], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        demo_c = os.path.join(self.tmp, "demo.c")
        with open(demo_c, "w", encoding="utf-8") as f:
            f.write(DEMO_C)
        build = _run([TCC, demo_c, dll, "-o",
                      os.path.join(self.tmp, "demo.exe")], self.tmp)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = _run([os.path.join(self.tmp, "demo.exe")], self.tmp, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("add=5 greet1=Hello, A greet2=Hello, B", run.stdout)
        self.assertIn("twice=42 handle=", run.stdout)


class TestImporcGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v383_gate_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, name, text, encoding="utf-8"):
        src = os.path.join(self.tmp, name + ".impor")
        mode = "wb" if encoding is None else "w"
        if mode == "wb":
            with open(src, "wb") as f:
                f.write(text)
        else:
            with open(src, "w", encoding=encoding, newline="\n") as f:
                f.write(text)
        out = os.path.join(self.tmp, name + ".exe")
        proc = _run([IMPORC, "build", src, "-o", out], self.tmp, timeout=60)
        return proc, out

    def test_utf8_bom_source_builds(self):
        data = b"\xef\xbb\xbfprint(\"BOM_OK\")\n"
        proc, out = self._build("bom", data, encoding=None)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(out), proc.stdout + proc.stderr)
        run = _run([out], self.tmp, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("BOM_OK", run.stdout)

    def test_single_line_brace_block_builds(self):
        proc, out = self._build("brace", "def one() { return 1 }\nprint(one())\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(out), proc.stdout + proc.stderr)
        run = _run([out], self.tmp, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("1", run.stdout)

    def test_syntax_error_exits_nonzero_without_output(self):
        proc, out = self._build("bad", "var x =\n")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(os.path.isfile(out), proc.stdout + proc.stderr)
        self.assertIn("编译失败", proc.stdout + proc.stderr)


class TestPyQtSkipCondition(unittest.TestCase):
    def test_helper_matches_importability(self):
        from tests.test_v380 import _has_pyqt6
        try:
            import PyQt6  # noqa: F401
            available = True
        except Exception:
            available = False
        self.assertEqual(_has_pyqt6(), available)


if __name__ == "__main__":
    unittest.main()
