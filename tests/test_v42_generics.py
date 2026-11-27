# -*- coding: utf-8 -*-
"""v4.2 回归：泛型函数声明（声明期约束 + 运行期擦除）。

口径（docs/语言规范.md §2.6）：
- 语法 `def name<T, U>(...)`，只接受无约束类型参数；
- 运行期是**擦除**的（不做 C 单态化），所以三端行为必须一致；
- `impor check` 入口的轻量类型检查会把 `T` 放宽成「任意类型」，
  并在调用点做一致性约束：同一次调用里同一类型参数不能既绑 int 又绑 str。
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
from impor.parser import ImporSyntaxError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = (
    'def max2<T>(a: T, b: T) -> T {\n'
    '    if a > b { return a }\n'
    '    return b\n'
    '}\n'
    'def ident<T>(x: T) -> T {\n'
    '    return x\n'
    '}\n'
    'def pair<K, V>(k: K, v: V) -> list {\n'
    '    return [k, v]\n'
    '}\n'
    'print(max2(3, 9))\n'
    'print(ident("hi"))\n'
    'print(max2(1.5, 2.5))\n'
    'print(pair("k", 7))\n'
)
EXPECTED = ["9", "hi", "2.5", "['k', 7]"]


def run_interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="v42gen").run(
        parse_source(strip_header(src), filename="v42gen"))
    return out


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v42gen_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    py = os.path.join(d, "case.py")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v42genc_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestGenericSyntax(unittest.TestCase):
    def test_single_and_multi_param(self):
        for decl in ('def f<T>(a: T) -> T { return a }',
                     'def f<T, U>(a: T, b: U) { return a }'):
            parse_source(strip_header(decl), filename="v42gen_parse")

    def test_export_generic(self):
        src = 'export def pick<T>(a: T) -> T { return a }\n'
        parse_source(strip_header(src), filename="v42gen_parse")

    def test_constraint_rejected(self):
        with self.assertRaises(ImporSyntaxError):
            parse_source(strip_header('def f<T: int>(a: T) { return a }'),
                         filename="v42gen_parse")

    def test_unclosed_rejected(self):
        with self.assertRaises(ImporSyntaxError):
            parse_source(strip_header('def f<T(a) { return a }'),
                         filename="v42gen_parse")

    def test_duplicate_rejected(self):
        with self.assertRaises(ImporSyntaxError):
            parse_source(strip_header('def f<T, T>(a: T) { return a }'),
                         filename="v42gen_parse")


class TestGenericRuntime(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)


class TestGenericTypeGate(unittest.TestCase):
    def test_mixed_types_rejected(self):
        from impor.typecheck import check_source

        src = ('def max2<T>(a: T, b: T) -> T {\n'
               '    if a > b { return a }\n'
               '    return b\n'
               '}\n'
               'print(max2(1, "a"))\n')
        issues = check_source(src)
        self.assertTrue(issues)
        self.assertEqual(issues[0].code, "generic-mismatch")

    def test_same_types_pass(self):
        from impor.typecheck import check_source

        src = ('def max2<T>(a: T, b: T) -> T {\n'
               '    if a > b { return a }\n'
               '    return b\n'
               '}\n'
               'print(max2(1, 2))\n')
        self.assertEqual(check_source(src), [])


if __name__ == "__main__":
    unittest.main()
