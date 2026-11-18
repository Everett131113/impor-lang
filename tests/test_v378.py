# -*- coding: utf-8 -*-
"""v3.7.8 回归：defer 延迟清理 / C 后端管道降级 / C 后端明确拒绝 defer。"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from impor.interpreter import Interpreter, parse_source
from impor.ctrans import c_transpile, UnsupportedFeature

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TCC = None
for _c in (os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe"),
           shutil.which("tcc")):
    if _c and os.path.isfile(_c):
        TCC = _c
        break


def run_impor(src, fname="t378.impor"):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.run(parse_source(src, filename=fname))
    return out


def run_cli(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                          cwd=cwd, capture_output=True, text=True, env=env, timeout=90)


class TestDeferInterpreter(unittest.TestCase):
    def test_lifo_and_return_value(self):
        src = ('func f()\n'
               '    defer { print("d1") }\n'
               '    defer { print("d2") }\n'
               '    print("body")\n'
               '    return 7\n'
               'print(f())\n')
        self.assertEqual(run_impor(src), ["body", "d2", "d1", "7"])

    def test_runs_on_exception(self):
        src = ('exception EvalError\n'
               'func f()\n'
               '    defer { print("clean") }\n'
               '    raise EvalError("boom")\n'
               'try {\n'
               '    f()\n'
               '} catch EvalError e {\n'
               '    print("caught", e.msg)\n'
               '}\n')
        self.assertEqual(run_impor(src), ["clean", "caught boom"])

    def test_module_scope_runs_at_end(self):
        src = ('defer { print("module-end") }\n'
               'print("top")\n')
        self.assertEqual(run_impor(src), ["top", "module-end"])

    def test_identifier_named_defer_still_works(self):
        src = ('var defer = 3\n'
               'print(defer)\n')
        self.assertEqual(run_impor(src), ["3"])

    def test_nested_function_and_class_method(self):
        src = ('class Box\n'
               '    def close()\n'
               '        defer { print("method-clean") }\n'
               '        print("method-body")\n'
               'func outer()\n'
               '    func inner()\n'
               '        defer { print("inner-clean") }\n'
               '        print("inner-body")\n'
               '    inner()\n'
               'var b = new Box()\n'
               'b.close()\n'
               'outer()\n')
        self.assertEqual(run_impor(src), ["method-body", "method-clean",
                                          "inner-body", "inner-clean"])

    def test_control_flow_inside_defer_is_rejected(self):
        with self.assertRaises(Exception):
            parse_source('func f()\n    defer { return 1 }\n')

    def test_fast_mode_executes_defer(self):
        src = ('func f()\n'
               '    defer { print("d1") }\n'
               '    defer { print("d2") }\n'
               '    print("body")\n'
               'f()\n')
        out = []
        interp = Interpreter(log_cb=out.append, fast_mode=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            interp.run(parse_source(src, filename="fast.impor"))
        self.assertEqual([x for x in buf.getvalue().splitlines() if x],
                         ["body", "d2", "d1"])


class TestDeferTranspile(unittest.TestCase):
    def test_compiled_python_matches_interpreter(self):
        src = ('func f()\n'
               '    defer { print("d1") }\n'
               '    defer { print("d2") }\n'
               '    print("body")\n'
               '    return 7\n'
               'print(f())\n')
        expected = run_impor(src)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "main.impor")
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            r = run_cli(["compile", path], d)
            self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
            py = os.path.join(d, "main.py")
            self.assertTrue(os.path.isfile(py))
            r2 = subprocess.run([sys.executable, "-B", py], cwd=d,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
            self.assertEqual(r2.returncode, 0, (r2.stdout or "") + (r2.stderr or ""))
            got = [x for x in (r2.stdout or "").splitlines() if x]
            self.assertEqual(got, expected)


class TestCBackendV378(unittest.TestCase):
    def test_pipe_is_lowered_to_call(self):
        src = ('func twice(x)\n'
               '    return x * 2\n'
               'func add(a, b)\n'
               '    return a + b\n'
               'print(3 |> twice)\n'
               'print(1 |> add(10))\n')
        c = c_transpile(parse_source(src, filename="pipe.impor"))
        self.assertIn('rt_trace_push("twice"', c)
        self.assertIn('rt_trace_push("add"', c)

    @unittest.skipUnless(TCC, "未找到 tcc")
    def test_pipe_builds_and_runs(self):
        src = ('func twice(x)\n'
               '    return x * 2\n'
               'func add(a, b)\n'
               '    return a + b\n'
               'print(3 |> twice)\n'
               'print(1 |> add(10))\n')
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pipe.impor")
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
            r = run_cli(["build", path], d)
            self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
            exe = os.path.join(d, "pipe.exe")
            self.assertTrue(os.path.isfile(exe))
            clean = {k: v for k, v in os.environ.items()
                     if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
            r2 = subprocess.run([exe], cwd=d, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=clean,
                                timeout=30)
            self.assertEqual(r2.returncode, 0, (r2.stdout or "") + (r2.stderr or ""))
            self.assertEqual((r2.stdout or "").split(), ["6", "11"])

    def test_c_backend_rejects_defer_explicitly(self):
        src = ('func f()\n'
               '    defer { print("clean") }\n'
               '    return 0\n'
               'f()\n')
        with self.assertRaises(UnsupportedFeature) as cm:
            c_transpile(parse_source(src, filename="defer.impor"))
        self.assertIn("defer", str(cm.exception))
