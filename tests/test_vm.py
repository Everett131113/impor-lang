# -*- coding: utf-8 -*-
"""自有字节码后端（NVM）回归测试。

锁定四件事：
1. 过程式核心（变量/控制流/函数/闭包/list/dict）输出与解释器一致；
2. .ibc 字节码 compile → exec 往返，输出不变；
3. 不支持的特性（class/import/try）明确抛 BackendError，不静默改语义；
4. 进程级对拍：`impor vm run` 与 `impor run` 的 stdout 逐字节一致。
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")

sys.path.insert(0, ROOT)
from impor import nvm  # noqa: E402


def capture(src, gl=None):
    """直接调用 VM，用 log_cb 收集输出，返回行列表。"""
    lines = []
    nvm.run_source(src, "<test>", log_cb=lambda t: lines.append(t), globals_dict=gl)
    return lines


# 一段覆盖过程式核心的程序
CORE = '''
var x = 10
x += 5
print("x =", x)
var i = 0
var total = 0
while i < 5
    if i == 2
        set i = i + 1
        continue
    total += i
    i += 1
print("total =", total)
for n in [1, 2, 3]
    print("n =", n * 2)
def fib(k)
    if k < 2
        return k
    return fib(k - 1) + fib(k - 2)
print("fib =", fib(10))
'''

CLOSURE = '''
def counter(start)
    var v = start
    def inc()
        nonlocal set v = v + 1
        return v
    return inc
var c = counter(0)
print(c(), c(), c())
def adder(k)
    def add(x)
        return x + k
    return add
print(adder(5)(10))
'''

DICT_SRC = '''
var d = {"a": 1, "b": 2}
print(d.has("a"), d.has("z"))
print(d.get("z", 9))
print(d.keys(), d.values())
d["c"] = 3
print(d["c"])
'''


class TestCore(unittest.TestCase):
    def test_arithmetic_and_loops(self):
        out = capture(CORE)
        self.assertIn("x = 15", out)
        self.assertIn("total = 8", out)   # 0+1+3+4（跳过 2）
        self.assertIn("n = 2", out)
        self.assertIn("n = 6", out)
        self.assertIn("fib = 55", out)

    def test_closure_and_nonlocal(self):
        out = capture(CLOSURE)
        self.assertIn("1 2 3", out)
        self.assertIn("15", out)

    def test_dict_methods(self):
        out = capture(DICT_SRC)
        self.assertIn("True False", out)
        self.assertIn("9", out)
        self.assertIn("3", out)

    def test_default_print_goes_to_stdout(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nvm.run_source('print("ok", 1)')
        self.assertEqual(buf.getvalue(), "ok 1\n")

    def test_lambda_ternary_and_fstring(self):
        src = '''
var f = lambda (a, b): a + b
print(f(2, 3))
var name = "Impor"
print("hi {name}")
print(1 < 2 ? 1 : 0)
'''
        out = capture(src)
        self.assertEqual(out[:3], ["5", "hi Impor", "1"])


class TestIbcRoundtrip(unittest.TestCase):
    def test_compile_exec_roundtrip(self):
        d = tempfile.mkdtemp(prefix="ibc_")
        try:
            src_p = os.path.join(d, "t.impor")
            ibc_p = os.path.join(d, "t.ibc")
            with open(src_p, "w", encoding="utf-8") as f:
                f.write(CORE)
            program = nvm.compile_source(CORE, "t.impor")
            nvm.dump_ibc(program, ibc_p)
            self.assertTrue(os.path.isfile(ibc_p))
            # 重新加载并执行，输出与直接 run 一致
            lines = []
            nvm.run_ibc(ibc_p, log_cb=lambda t: lines.append(t))
            self.assertEqual(lines, capture(CORE))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_load_rejects_bad_magic(self):
        d = tempfile.mkdtemp(prefix="ibc_")
        try:
            bad = os.path.join(d, "x.ibc")
            with open(bad, "w", encoding="utf-8") as f:
                f.write('{"magic": "NOPE"}')
            with self.assertRaises(nvm.ImporException):
                nvm.load_ibc(bad)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestUnsupported(unittest.TestCase):
    def test_class_rejected(self):
        with self.assertRaises(nvm.ImporException) as cm:
            nvm.run_source('class A\n    def m(self)\n        return 1')
        self.assertIn("class", str(cm.exception))

    def test_import_rejected(self):
        with self.assertRaises(nvm.ImporException):
            nvm.run_source('import math')

    def test_recursion_guard(self):
        gl = nvm._build_builtin_globals()
        vm = nvm.VM(gl, max_depth=100)
        prog = nvm.compile_source('def recurse(n)\n    return recurse(n + 1)\nrecurse(0)')
        with self.assertRaises(nvm.ImporException) as cm:
            vm.run_module(prog)
        self.assertIn("RecursionError", str(cm.exception))

    def test_concurrency_builtin_rejected(self):
        # 并发原语在编译期就拒绝，而非运行到内部类型错误写 crash.log
        with self.assertRaises(nvm.ImporException) as cm:
            nvm.run_source('var m = mutex_new()')
        self.assertIn("mutex_new", str(cm.exception))
        with self.assertRaises(nvm.ImporException):
            nvm.run_source('def job()\n    return 1\nthread_spawn(job)')


class TestSubprocessParity(unittest.TestCase):
    """进程级对拍：vm run 与 run 的 stdout 必须一致。"""

    def _write(self, text):
        d = tempfile.mkdtemp(prefix="vmpar_")
        p = os.path.join(d, "t.impor")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return d, p

    def _run(self, cmd, p):
        r = subprocess.run([PY, IMPOR] + cmd + [p], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=120, cwd=ROOT)
        return r

    def test_vm_matches_interpreter(self):
        d, p = self._write(CORE + "\n" + CLOSURE)
        try:
            a = self._run(["run", "--no-native"], p)
            b = self._run(["vm", "run"], p)
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertEqual(
                (a.stdout or "").replace("\r\n", "\n"),
                (b.stdout or "").replace("\r\n", "\n"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
