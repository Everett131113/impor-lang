# -*- coding: utf-8 -*-
"""语义诚实 P0：未实现关键字必须报错，const/readonly/static 必须只读。

这些测试走真实 CLI 子进程，断言用户看到的退出码与文案，而不是只测 parser 内部。
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")


def _run_source(source: str):
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "probe.impor")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source)
        proc = subprocess.run(
            [sys.executable, IMPOR_PY, "run", path],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        return proc.returncode, proc.stdout


class TestUnimplementedKeywordsRejected(unittest.TestCase):
    """async / await / defer 及无语义保留字：语句位置必须 exit 2。"""

    def assert_syntax_reject(self, source, keyword):
        rc, out = _run_source(source)
        self.assertEqual(rc, 2, f"{keyword} 应 exit 2，实际 {rc}\n{out}")
        self.assertIn(f"{keyword} 尚未实现", out)
        self.assertIn("v3.x 计划", out)

    def test_async_def(self):
        self.assert_syntax_reject(
            'async def f() {\n    print("a")\n}\nf()\n', "async")

    def test_defer_statement(self):
        self.assert_syntax_reject(
            'defer print("d")\nprint("main")\n', "defer")

    def test_await_expression(self):
        self.assert_syntax_reject(
            'def g() {\n    return 7\n}\nprint(await g())\n', "await")

    def test_interface_placeholder(self):
        self.assert_syntax_reject('interface Foo {\n}\n', "interface")

    def test_abstract_placeholder(self):
        self.assert_syntax_reject('abstract class A {\n}\n', "abstract")


class TestConstIsActuallyReadonly(unittest.TestCase):
    """const / static / readonly 的只读约束不能被快速转译路径绕过。"""

    def assert_const_error(self, source):
        rc, out = _run_source(source)
        self.assertEqual(rc, 1, f"改常量应运行错误 exit 1，实际 {rc}\n{out}")
        self.assertIn("ConstError", out)
        self.assertIn("不可修改", out)

    def test_const_reassign(self):
        self.assert_const_error("const x = 5\nx = 6\nprint(x)\n")

    def test_static_is_const_alias(self):
        self.assert_const_error("static s = 5\ns = 6\nprint(s)\n")

    def test_readonly_is_const_alias(self):
        self.assert_const_error("readonly r = 5\nr = 6\nprint(r)\n")

    def test_const_read_is_fine(self):
        rc, out = _run_source("const x = 5\nprint(x + 1)\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("6", out)

    def test_this_alias_works(self):
        rc, out = _run_source(
            "class A {\n"
            "    var x = 5\n"
            "    def get() {\n"
            "        return this.x\n"
            "    }\n"
            "}\n"
            "var a = new A()\n"
            "print(a.get())\n"
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("5", out)


if __name__ == "__main__":
    unittest.main()
