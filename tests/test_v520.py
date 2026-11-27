# -*- coding: utf-8 -*-
"""v5.2 回归：科学计数法 + 17 个新内建（字符串/数组/数值/函数式）。"""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from impor.lexer import tokenize
from impor.interpreter import Interpreter, ImporException, parse_source
from impor.builtin_manifest import ALL_BUILTINS


def run(src):
    out = []
    interp = Interpreter(log_cb=out.append)
    interp.run(parse_source(src))
    return out


def nums(src):
    return [t.value for t in tokenize(src) if t.type == "NUM"]


class TestSciNotation(unittest.TestCase):
    def test_values(self):
        self.assertEqual(nums("x = 1e3"), [1000.0])
        self.assertEqual(nums("x = 2.5E-4"), [0.00025])
        self.assertEqual(nums("x = 1_000e+2"), [100000.0])
        self.assertEqual(nums("x = 3.0e0"), [3.0])

    def test_bare_e_not_consumed(self):
        # 1e 后面没数字：e 不被当作指数，1 仍是整数
        self.assertEqual(nums("x = 1e"), [1])

    def test_range_double_dot_kept(self):
        self.assertEqual(nums("x = 1..5"), [1, 5])

    def test_runtime_output(self):
        self.assertEqual(run("print(1.5e2)\nprint(2e-3)"), ["150.0", "0.002"])


class TestNewStringBuiltins(unittest.TestCase):
    def test_strsplit(self):
        self.assertEqual(run('print(strsplit("a,b,c", ","))'), ["['a', 'b', 'c']"])
        self.assertEqual(run('print(strsplit("a  b c"))'), ["['a', 'b', 'c']"])
        self.assertEqual(run('print(strsplit("a,b,c", ",", 1))'), ["['a', 'b,c']"])

    def test_strcount(self):
        self.assertEqual(run('print(strcount("a,a,a", "a"))'), ["3"])

    def test_strstart_strend(self):
        self.assertEqual(run('print(strstart("hello", "he"))'), ["True"])
        self.assertEqual(run('print(strend("hello", "lo"))'), ["True"])
        self.assertEqual(run('print(strend("hello", "he"))'), ["False"])


class TestNewArrayBuiltins(unittest.TestCase):
    def test_arrinsert(self):
        self.assertEqual(run("print(arrinsert([1, 3], 1, 2))"), ["[1, 2, 3]"])

    def test_arrremove(self):
        self.assertEqual(run("print(arrremove([1, 2, 3], 2))"), ["[1, 3]"])
        with self.assertRaises(ImporException):
            run("print(arrremove([1, 2], 9))")

    def test_arrsort(self):
        self.assertEqual(run("print(arrsort([3, 1, 2]))"), ["[1, 2, 3]"])
        self.assertEqual(run("print(arrsort([3, 1, 2], true))"), ["[3, 2, 1]"])

    def test_arrcount(self):
        self.assertEqual(run("print(arrcount([1, 1, 2], 1))"), ["2"])

    def test_original_not_mutated(self):
        self.assertEqual(run("var a = [3, 1, 2]\nvar b = arrsort(a)\nprint(a)"), ["[3, 1, 2]"])


class TestNewNumericBuiltins(unittest.TestCase):
    def test_round(self):
        self.assertEqual(run("print(round(2.5))"), ["2"])  # 五成双
        self.assertEqual(run("print(round(3.14159, 2))"), ["3.14"])

    def test_floor_ceil(self):
        self.assertEqual(run("print(floor(2.9))\nprint(ceil(2.1))"), ["2", "3"])


class TestLogicAndFunctional(unittest.TestCase):
    def test_any_all(self):
        self.assertEqual(run("print(any([0, 1]))\nprint(all([1, true]))"), ["True", "True"])
        self.assertEqual(run("print(any([0, 0]))\nprint(all([1, 0]))"), ["False", "False"])

    def test_map_filter(self):
        src = ("def dbl(x)\n    return x * 2\n"
               "print(map(dbl, [1, 2, 3]))\n"
               "print(filter(dbl, [0, 1, 2]))")
        self.assertEqual(run(src), ["[2, 4, 6]", "[1, 2]"])

    def test_zip_enumerate(self):
        self.assertEqual(run('print(zip([1, 2], ["a", "b"]))'),
                         ["[[1, 'a'], [2, 'b']]"])
        self.assertEqual(run('print(enumerate(["x", "y"]))'),
                         ["[[0, 'x'], [1, 'y']]"])


class TestRegistryAndRuntime(unittest.TestCase):
    NEW = (
        "strsplit", "strcount", "strstart", "strend",
        "arrinsert", "arrremove", "arrsort", "arrcount",
        "round", "floor", "ceil",
        "any", "all", "map", "filter", "zip", "enumerate",
    )

    def test_manifest_contains_new(self):
        for name in self.NEW:
            self.assertIn(name, ALL_BUILTINS)

    def test_runtime_module_selfcheck(self):
        # 导入即触发 check_runtime_namespace，缺函数会 ImportError
        import impor.runtime as rt
        for name in self.NEW:
            self.assertTrue(callable(getattr(rt, name)), name)

    def test_interpreter_registers_new(self):
        interp = Interpreter(log_cb=lambda t: None)
        for name in self.NEW:
            self.assertIn(name, interp.global_env.store)


if __name__ == "__main__":
    unittest.main(verbosity=2)
