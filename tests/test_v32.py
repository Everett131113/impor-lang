# -*- coding: utf-8 -*-
"""v2.12 回归测试：use 关键字 + 内置标准库、impor check 聚合、模块语义修复、
from . import 子模块、调试器断点列表/删除命令、脚手架 pkg 模板。"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_impor(src, fname="test.impor", paths=None):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    if paths:
        interp.module_search_paths[:0] = paths
    interp.run(parse_source(src, filename=fname))
    return out


def cli(args, cwd=None, stdin=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                          capture_output=True, text=True, timeout=90,
                          env=env, cwd=cwd or ROOT, input=stdin)


class TestUseStd(unittest.TestCase):
    """use 关键字 + 内置标准库（解释器路径）。"""

    def test_use_text(self):
        out = run_impor("""use std.text
print(split("a,b,c", ","))
print(join(["x", "y"], "-"))
print(upper("hi"))
print(starts_with("hello", "he"))
""")
        self.assertIn("['a', 'b', 'c']", str(out))
        self.assertIn("x-y", str(out))
        self.assertIn("HI", str(out))
        self.assertTrue(any("True" in str(x) for x in out))

    def test_use_mathx(self):
        out = run_impor("""use std.mathx
print(gcd(12, 18))
print(lcm(4, 6))
print(fact(5))
print(is_prime(7))
""")
        joined = str(out)
        self.assertIn("6", joined)      # gcd(12,18)
        self.assertIn("12", joined)     # lcm(4,6)
        self.assertIn("120", joined)    # 5!
        self.assertIn("True", joined)   # 7 是素数

    def test_use_collections_hof(self):
        out = run_impor("""use std.collections
def dbl(x)
    return x * 2
def add(a, b)
    return a + b
print(map([1, 2, 3], dbl))
print(reduce([1, 2, 3], add, 0))
print(first([7, 8]))
""")
        joined = str(out)
        self.assertIn("2", joined)
        self.assertIn("6", joined)     # sum via reduce
        self.assertIn("7", joined)     # first

    def test_use_injects_symbols(self):
        out = run_impor("""use std.text
print(upper("ok"))
""")
        self.assertIn("OK", str(out))

    def test_import_std_module_form(self):
        out = run_impor("""import std.text
print(std.text.split("1,2", ","))
""")
        self.assertIn("['1', '2']", str(out))


class TestCheckAggregate(unittest.TestCase):
    """impor check 聚合四段检查。"""

    def test_check_std_demo_passes(self):
        demo = os.path.join(ROOT, "examples", "std_demo.impor")
        r = cli(["check", demo])
        joined = r.stdout + r.stderr
        self.assertIn("[PASS] 语法", joined)
        self.assertIn("[PASS] 导入", joined)
        self.assertIn("[PASS] 格式", joined)
        # lint 最多一条提示，不允许 FAIL
        self.assertNotIn("[FAIL] lint", joined)

    def test_check_unknown_var_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.impor")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write("var a = 1\nprint(no_such_name)\n")
            r = cli(["check", p])
            joined = r.stdout + r.stderr
            # 未定义变量是 error 级（运行必然 NameError）→ 退出码非 0
            self.assertIn("未定义变量", joined)
            self.assertNotEqual(r.returncode, 0)


class TestModuleFixes(unittest.TestCase):
    """模块语义修复回归。"""

    def test_export_private_helper_visible_inside(self):
        # 严格 export 模式下，模块内私有辅助函数互相调用可见（mathx._abs 场景）
        with tempfile.TemporaryDirectory() as d:
            mod = os.path.join(d, "privmod.impor")
            with io.open(mod, "w", encoding="utf-8") as f:
                f.write("def helper(v)\n    if v < 0\n        return -v\n    return v\n\n"
                        "def do_abs(v)\n    return helper(v)\n\nexport do_abs\n")
            out = run_impor("import privmod\nprint(privmod.do_abs(-5))\n", paths=[d])
            self.assertIn("5", str(out))

    def test_from_dot_import_submodule(self):
        # 目录包：from . import 子模块 + 聚合导出函数（脚手架 pkg 模板语义）
        with tempfile.TemporaryDirectory() as d:
            pkg = os.path.join(d, "mylib")
            os.makedirs(pkg)
            with io.open(os.path.join(pkg, "__pkg__.impor"), "w", encoding="utf-8") as f:
                f.write("from .greet import greet\nexport greet\n")
            with io.open(os.path.join(pkg, "greet.impor"), "w", encoding="utf-8") as f:
                f.write("def greet(n)\n    return \"你好, \" + n\nexport greet\n")
            out = run_impor("import mylib\nprint(mylib.greet(\"Impor\"))\n", paths=[d])
            self.assertIn("你好, Impor", str(out))


class TestPkgScaffold(unittest.TestCase):
    """脚手架 impor new --template pkg 全链路（CLI）。"""

    def test_new_pkg_and_run(self):
        with tempfile.TemporaryDirectory() as d:
            name = "tpkg"
            r = cli(["new", name, "--template", "pkg"], cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isdir(os.path.join(d, name, "lib", name + "_lib")))
            self.assertTrue(os.path.isfile(os.path.join(d, name, "impor.toml")))
            r2 = cli(["run", "main.impor"], cwd=os.path.join(d, name))
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("你好, Impor", r2.stdout)


class TestDebuggerCmds(unittest.TestCase):
    """调试器：断点列表 b / 删除 del（管道输入）。"""

    def test_b_list_and_del(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dbg.impor")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write("var x = 1\nprint(x)\nprint(x * 2)\n")
            r = cli(["run-dbg", p], cwd=d, stdin="b 3\nb\nq\n")
            joined = r.stdout + r.stderr
            self.assertIn("断点已设在第 3 行", joined)
            self.assertIn("断点列表", joined)
            r2 = cli(["run-dbg", p], cwd=d, stdin="b 3\ndel 3\nb\nq\n")
            joined2 = r2.stdout + r2.stderr
            self.assertIn("已删除断点", joined2)
            self.assertIn("（无断点）", joined2)


if __name__ == "__main__":
    unittest.main()
