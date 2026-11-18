# -*- coding: utf-8 -*-
"""v3.8.2 回归：export func → .dll + .h → C demo 链接调用。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")

LIB_SRC = """export func add(a: int, b: int) -> int {
    return a + b
}

export func greet(name: str) -> str {
    return "Hello, " + name
}

export func half(x: float) -> float {
    return x / 2
}
"""

DEMO_C = """#include <stdio.h>
#include "lib.h"

int main(void) {
    printf("%lld|%s|%.1f\\n", impor_add(2, 3), impor_greet("Impor"), impor_half(7.0));
    return 0;
}
"""


def _run(cmd, cwd, timeout=90):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


class TestCAbi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v382_cabi_")
        self.src = os.path.join(self.tmp, "lib.impor")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(LIB_SRC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_lib_generates_dll_and_header(self):
        dll = os.path.join(self.tmp, "lib.dll")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", self.src,
                     "-o", dll], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(dll), proc.stdout + proc.stderr)
        header = os.path.join(self.tmp, "lib.h")
        self.assertTrue(os.path.isfile(header))
        with open(header, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("impor_add", text)
        self.assertIn("long long", text)
        self.assertIn('extern "C"', text)

    def test_c_demo_links_and_calls_dll(self):
        dll = os.path.join(self.tmp, "lib.dll")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", self.src,
                     "-o", dll], ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        demo_c = os.path.join(self.tmp, "demo.c")
        with open(demo_c, "w", encoding="utf-8") as f:
            f.write(DEMO_C)
        demo = os.path.join(self.tmp, "demo.exe")
        build = _run([TCC, "demo.c", "lib.dll", "-o", "demo.exe"], self.tmp)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run = _run([demo], self.tmp, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.strip(), "5|Hello, Impor|3.5")

    def test_untyped_export_is_rejected(self):
        bad = os.path.join(self.tmp, "bad.impor")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("export func f(a) -> int {\n    return 1\n}\n")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", bad], ROOT)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("显式标注类型", proc.stdout + proc.stderr)

    def test_no_export_is_rejected(self):
        bad = os.path.join(self.tmp, "none.impor")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("func f(a: int) -> int {\n    return a\n}\n")
        proc = _run([sys.executable, IMPOR_PY, "build", "--lib", bad], ROOT)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("没有可导出的函数", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
