# -*- coding: utf-8 -*-
"""v3.8.4 回归：语义诚实（未实现关键字编译期拒绝）+ C 后端 try/catch。

用户实测暴露的问题：yield 被语法接受、运行不报错、生成器值全丢。
本测试锁定“未实现特性必须报错”和“C 后端异常可捕获且按指定码退出”。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")

YIELD_SRC = """def counter(n) {
    for i in range(n) {
        yield i
    }
}
for v in counter(3) {
    print(v)
}
"""

WITH_SRC = """def f() {
    with x {
        print(1)
    }
}
"""

DEFAULT_PARAM_SRC = """def g(a, b = 1) {
    return a
}
print(g(1))
"""

LIST_COMP_SRC = """var xs = [x for x in range(3)]
print(xs)
"""

DICT_COMP_SRC = """var m = {x: x for x in range(3)}
print(m)
"""

TRY_CATCH_SRC = """def main() {
    try {
        var x = 1 / 0
        print("NO_ERROR", x)
    } catch (e) {
        print("CAUGHT", e.name, e.msg)
    }
    exit(7)
}
main()
"""


def _run(cmd, cwd=ROOT, timeout=90):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


class TestUnimplementedRejected(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v384_reject_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _assert_rejected(self, name, text, needle, plan="v3.x 计划"):
        path = self._write(name, text)
        proc = _run([sys.executable, IMPOR_PY, "run", path])
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        out = proc.stdout + proc.stderr
        self.assertIn(needle, out)
        self.assertIn(plan, out)

    def test_yield_is_compile_error(self):
        self._assert_rejected("yield.impor", YIELD_SRC, "yield 尚未实现")

    def test_with_is_compile_error(self):
        self._assert_rejected("with.impor", WITH_SRC, "with 尚未实现")

    def test_default_param_is_compile_error(self):
        self._assert_rejected("default.impor", DEFAULT_PARAM_SRC,
                              "默认参数尚未实现")

    def test_variadic_param_is_compile_error(self):
        self._assert_rejected("variadic.impor", "def h(*args)\n    return 1\n",
                              "变参尚未实现")

    def test_list_comprehension_is_compile_error(self):
        self._assert_rejected("listcomp.impor", LIST_COMP_SRC,
                              "列表推导式尚未实现", plan="v3.9 计划")

    def test_dict_comprehension_is_compile_error(self):
        self._assert_rejected("dictcomp.impor", DICT_COMP_SRC,
                              "字典推导式尚未实现", plan="v3.9 计划")


class TestCBackendTryCatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v384_try_")
        self.src = os.path.join(self.tmp, "try.impor")
        self.exe = os.path.join(self.tmp, "try.exe")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(TRY_CATCH_SRC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_try_catch_builds_and_exits_with_requested_code(self):
        proc = _run([sys.executable, IMPOR_PY, "build", self.src,
                     "-o", self.exe, "--no-cache"], timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(self.exe))
        run = _run([self.exe], cwd=self.tmp)
        self.assertEqual(run.returncode, 7, run.stdout + run.stderr)
        self.assertIn("CAUGHT", run.stdout)
        self.assertIn("ZeroDivisionError", run.stdout)
        self.assertNotIn("NO_ERROR", run.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
