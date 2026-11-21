# -*- coding: utf-8 -*-
"""v2.12.1 回归测试：CLI --smoke 不挂起、转译 import pkg.sub 顶层绑定、
impor test 双目录扫描、REPL 中文块关键字与 exit()、转译 from . import x。"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source
from impor.repl import repl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_impor(src, fname="t.impor", smoke=False):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.smoke_mode = smoke
    interp.run(parse_source(src, filename=fname))
    return out


def cli(args, cwd=None, stdin=None, timeout=90):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=cwd or ROOT, input=stdin)


class TestSmokeMode(unittest.TestCase):
    """CLI --smoke：input/inputstr 自动返回，交互脚本不挂起。"""

    def test_interpreter_smoke_input(self):
        out = run_impor("""var n = to_num(inputstr())
print(n * 2)
""", smoke=True)
        # inputstr 返回 "0\n" → to_num → 0 → 0*2=0
        self.assertIn("0", str(out))

    def test_cli_smoke_guess_not_hang(self):
        r = cli(["run", os.path.join(ROOT, "examples", "guess.impor"), "--smoke"],
                timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_cli_smoke_snake(self):
        r = cli(["run", os.path.join(ROOT, "examples", "snake.impor"), "--smoke"],
                timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestTranspileImportSemantics(unittest.TestCase):
    """转译器 import 语义与解释器一致（v2.12.1 修复）。"""

    def _compile_and_run(self, src, files=None, script="main.impor"):
        d = tempfile.mkdtemp()
        sp = os.path.join(d, script)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(src)
        for rel, content in (files or {}).items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        out_py = os.path.join(d, "out.py")
        rc = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                             "compile", sp, "-o", out_py],
                            capture_output=True, text=True, timeout=60,
                            env=env, cwd=d)
        if rc.returncode != 0:
            return None, rc.stderr
        r = subprocess.run([sys.executable, out_py], capture_output=True,
                           text=True, timeout=30, env=env, cwd=d)
        return r.returncode, r.stdout

    def test_compile_import_pkg_sub_binds_top(self):
        files = {
            "mylib/__pkg__.impor": "from .greet import greet\nexport greet\n",
            "mylib/greet.impor": "def greet(n)\n    return 'hi-' + n\nexport greet\n",
        }
        rc, out = self._compile_and_run(
            "import mylib\nprint(mylib.greet('X'))\n", files=files)
        self.assertEqual(rc, 0, out)
        self.assertIn("hi-X", out)

    def test_compile_std_demo(self):
        r = cli(["compile", os.path.join(ROOT, "examples", "std_demo.impor"),
                 "-o", os.path.join(tempfile.mkdtemp(), "o.py")], timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_compile_from_dot_import(self):
        # from . import x（纯相对）：绑定子模块，解释器/转译器行为一致
        files = {
            "mylib/__pkg__.impor": "from . import greet\nexport greet\n",
            "mylib/greet.impor": "def greet(n)\n    return 'hi-' + n\nexport greet\n",
        }
        interp_rc, interp_out = None, None
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "main.impor"), "w", encoding="utf-8") as f:
            f.write("import mylib\nprint(mylib.greet)\n")  # 打印类型，不调用
        for rel, content in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        ri = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                             "run", "main.impor"], capture_output=True, text=True,
                            timeout=30, env=env, cwd=d)
        out_py = os.path.join(d, "out.py")
        rc = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                             "compile", "main.impor", "-o", out_py],
                            capture_output=True, text=True, timeout=60,
                            env=env, cwd=d)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        rc2 = subprocess.run([sys.executable, out_py], capture_output=True,
                             text=True, timeout=30, env=env, cwd=d)
        # 两后端都视为模块对象（绑定子模块），逻辑名一致
        self.assertIn("mylib.greet", ri.stdout + ri.stderr)
        self.assertIn("mylib.greet", rc2.stdout + rc2.stderr)


class TestTestCommand(unittest.TestCase):
    """impor test 默认扫描 tests/ 与 examples/。"""

    def test_test_scans_examples(self):
        r = cli(["test"], timeout=90)
        joined = r.stdout + r.stderr
        self.assertIn("test_math.impor", joined)
        self.assertIn("测试结果", joined)


class TestReplBlocks(unittest.TestCase):
    """REPL：多行块输入、exit() 退出（当前 def / var / if 语法）。"""

    def _repl(self, src):
        buf_in = io.StringIO(src)
        buf_out = io.StringIO()
        repl(stdin=buf_in, stdout=buf_out)
        return buf_out.getvalue()

    def test_repl_func_block(self):
        out = self._repl("def f(a)\n    return a + 1\nprint(f(1))\nvar x = 5\nprint(x)\nexit()\n")
        self.assertIn("2", out)
        self.assertIn("5", out)
        self.assertNotIn("期望INDENT", out)

    def test_repl_if_block(self):
        # 空行结束多行块后 exit()
        out = self._repl("if 1 < 2\n    print(\"ok\")\n\nexit()\n")
        self.assertIn("ok", out)

    def test_repl_exit_paren(self):
        out = self._repl("print(1)\nexit()\n")
        self.assertIn("1", out)


if __name__ == "__main__":
    unittest.main()
