# -*- coding: utf-8 -*-
"""v4.3.1 回归：条件变量 / 阻塞通道 / 跨行字面量 / ! 与 || / nil 转译。

覆盖四端（解释器 + Python 转译 + C 后端 64 位 + 内置 tcc 32 位）：
- std.sync 的 wait / notify 真的能唤醒（不是 sleep 轮询）；
- std.channel 在有界满时挂起、收空时挂起，close 后 recv 立刻返回哨兵；
- recv_timeout 超时返回 [false, empty()]；
- 多行 dict / list 字面量、`!`、`||`、`&&`、`nil` 都能解析并在三端一致；
- 运行期错误写 stderr（v4.3.1 之前 C 运行时打在 stdout）。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor import cli
from impor.interpreter import Interpreter, strip_header, parse_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = (
    'import std.sync as sy\n'
    'import std.channel as ch\n'
    'var m = sy.make_lock()\n'
    'var cv = sy.make_cond()\n'
    'var st = atomic_new(0)\n'
    'def worker() {\n'
    '    sy.lock(m)\n'
    '    while atomic_load(st) == 0 {\n'
    '        sy.wait(cv, m)\n'
    '    }\n'
    '    sy.unlock(m)\n'
    '    return 1\n'
    '}\n'
    'var t = thread_spawn(worker)\n'
    'sleep(0.02)\n'
    'sy.lock(m)\n'
    'atomic_store(st, 1)\n'
    'sy.notify(cv)\n'
    'sy.unlock(m)\n'
    'print("wake:", thread_join(t))\n'
    'var c = ch.make(1)\n'
    'def consumer() {\n'
    '    var n = 0\n'
    '    var v = ch.recv(c)\n'
    '    while not ch.is_empty(v) {\n'
    '        set n = n + 1\n'
    '        set v = ch.recv(c)\n'
    '    }\n'
    '    return n\n'
    '}\n'
    'var h = thread_spawn(consumer)\n'
    'var i = 0\n'
    'while i < 10 {\n'
    '    ch.send(c, i)\n'
    '    i += 1\n'
    '}\n'
    'ch.close(c)\n'
    'print("sum:", thread_join(h))\n'
    'var tr = ch.recv_timeout(ch.make(0), 50)\n'
    'print("timeout:", tr[0])\n'
    'var d = {\n'
    '    "a": 1,\n'
    '    "b": [1,\n'
    '        2, 3],\n'
    '}\n'
    'print("dict:", d["a"] + d["b"][2])\n'
    'var nv = nil\n'
    'print("nil:", nv == nil, to_str(nv))\n'
    'if !false || false {\n'
    '    print("logic:", 1 == 1 && true)\n'
    '}\n'
)
EXPECTED = ["wake: 1", "sum: 10", "timeout: False",
            "dict: 4", "nil: True None", "logic: True"]


def run_interp(src, name="v431"):
    out = []
    Interpreter(log_cb=out.append, filename=name).run(
        parse_source(strip_header(src), filename=name))
    return out


def _run_built(src, prefix, extra_args):
    d = tempfile.mkdtemp(prefix=prefix, dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe] + extra_args)
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=90)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v431_py_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    py = os.path.join(d, "case.py")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True, timeout=90)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestV431FourBackends(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = _run_built(SRC, "impor_v431_c_", [])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_tcc_32bit(self):
        rc, out = _run_built(SRC, "impor_v431_tcc_", ["--cc", "tcc"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)


class TestV431Syntax(unittest.TestCase):
    def test_multiline_literal_and_aliases(self):
        out = run_interp(
            'var d = {\n'
            '    "x": [\n'
            '        1,\n'
            '        2,\n'
            '    ],\n'
            '    "y": 3,\n'
            '}\n'
            'print(d["x"][1], d["y"], !false && true || false)\n'
            'def f(a: int,\n'
            '      b: int) -> int {\n'
            '    return a + b\n'
            '}\n'
            'print(f(1,\n'
            '        2))\n')
        self.assertEqual(out, ["2 3 True", "3"])

    def test_nil_compile_does_not_crash(self):
        rc, out = run_pytrans('var x = nil\nprint(x == nil, to_str(x))\n')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), ["True None"])


class TestV431ErrorStream(unittest.TestCase):
    def test_runtime_error_goes_to_stderr(self):
        d = tempfile.mkdtemp(prefix="impor_v431_err_", dir=os.path.join(ROOT, "tests"))
        sp = os.path.join(d, "case.impor")
        exe = os.path.join(d, "case.exe")
        try:
            with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write('var d = {}\nprint(d["missing"])\n')
            rc = cli.cmd_build([sp, "-o", exe])
            self.assertEqual(rc, cli.EXIT_OK)
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 1)
            self.assertIn("第", r.stderr)
            self.assertEqual(r.stdout, "")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
