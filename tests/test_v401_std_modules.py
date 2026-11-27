"""v4.0.1：标准库 16 个模块必须都能 import 且核心函数可用。

用解释器路径跑（--no-native），保证只验证语言层与 std 源码，不牵扯后端差异。
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

MODULES = [
    "text", "mathx", "collections", "algo", "json", "fs", "path",
    "regex", "time", "http", "thread", "atomic", "sort", "random", "stats", "csv",
]

SCRIPT = "\n".join(f"import std.{m} as {m}0" for m in MODULES) + """
print(len([1, 2, 3]))
"""


class TestStdModules(unittest.TestCase):
    def test_all_sixteen_importable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "all_std.impor")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(SCRIPT)
            proc = subprocess.run(
                [PY, os.path.join(ROOT, "Impor.py"), "run", path, "--no-native"],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertIn("3", proc.stdout)

    def test_new_modules_callable(self):
        body = """
import std.sort as so
import std.stats as st
import std.csv as cv
import std.random as rd
print(so.sort([3, 1, 2]))
print(st.median([1, 2, 3, 4]))
print(cv.stringify(cv.parse("a,b")))
print(rd.between(1, 3) >= 1)
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "call.impor")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            proc = subprocess.run(
                [PY, os.path.join(ROOT, "Impor.py"), "run", path, "--no-native"],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        lines = [ln.strip() for ln in proc.stdout.strip().splitlines()]
        self.assertEqual(lines[0], "[1, 2, 3]")
        self.assertEqual(lines[1], "2.5")
        self.assertEqual(lines[2], "a,b")
        self.assertEqual(lines[3], "True")


if __name__ == "__main__":
    unittest.main()
