# -*- coding: utf-8 -*-
"""v3.14 验收机制与品牌口径回归。"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from impor.interpreter import Interpreter, parse_source, strip_header
from impor.cli import _read_source


def cli(args, timeout=60):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=ROOT)


class TestCheckExitCodes(unittest.TestCase):
    def test_missing_file_clean_failure(self):
        r = cli(["check", os.path.join(ROOT, "examples", "__not_here__.impor")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("文件不存在", r.stdout + r.stderr)
        self.assertNotIn("UnboundLocalError", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_warn_only_returns_zero(self):
        with tempfile.NamedTemporaryFile("w", suffix=".impor", encoding="utf-8",
                                         delete=False, newline="\n") as f:
            f.write("var x=1\n")
            path = f.name
        try:
            r = cli(["check", path])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("WARN", r.stdout + r.stderr)
        finally:
            os.unlink(path)


class TestSmokeWithoutGui(unittest.TestCase):
    def test_snake_smoke_never_calls_tk(self):
        import tkinter
        created = []
        original = tkinter.Tk

        def boom(*a, **k):
            created.append(1)
            raise RuntimeError("Tk 不应在冒烟模式创建")

        tkinter.Tk = boom
        try:
            path = os.path.join(ROOT, "examples", "snake.impor")
            out = []
            interp = Interpreter(log_cb=out.append, filename=path)
            interp.argv = ["snake.impor", "--smoke"]
            interp.smoke_mode = True
            interp.run(parse_source(strip_header(_read_source(path)), filename=path))
        finally:
            tkinter.Tk = original
        self.assertEqual(created, [])
        self.assertTrue(any("SMOKE" in str(x) for x in out), out)


if __name__ == "__main__":
    unittest.main()
