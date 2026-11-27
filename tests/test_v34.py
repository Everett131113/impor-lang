# -*- coding: utf-8 -*-
"""v2.13.0 回归测试：C 后端（impor build）——转译产物可被 tcc 原生编译，
独立 exe 在干净环境（无 PYTHONPATH）运行，输出与解释器完全一致；
不支持的语法（类/列表）给出友好 UnsupportedFeature。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source
from impor import cli
from impor.ctrans import c_transpile, UnsupportedFeature

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TCC = None
for _c in (os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe"),
           shutil.which("tcc")):
    if _c and os.path.isfile(_c):
        TCC = _c
        break


def run_impor(src, fname="t.impor"):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.run(parse_source(src, filename=fname))
    return out


def build_exe(src, name="demo"):
    """编译成独立 exe，返回 (exe 路径, 编译输出文本)。"""
    d = tempfile.mkdtemp()
    src_path = os.path.join(d, name + ".impor")
    with open(src_path, "w", encoding="utf-8") as f:
        f.write(src)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    exe = os.path.join(d, name + ".exe")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                        "build", src_path, "-o", exe],
                       capture_output=True, text=True, env=env, timeout=90)
    return d, exe, r


def run_exe_clean(exe, cwd):
    """去掉 PYTHONPATH/PYTHONHOME 后运行 exe，模拟无 Python 宿主环境。"""
    clean = {k: v for k, v in os.environ.items()
             if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    return subprocess.run([exe], capture_output=True, text=True,
                          env=clean, cwd=cwd, timeout=30)


@unittest.skipUnless(TCC, "未找到 tcc（impor/tcc/win32/tcc.exe 或 PATH）")
class TestCBuild(unittest.TestCase):
    """impor build 端到端：解释器输出 === 独立 exe 输出。"""

    PROC_SRC = (
        'def fib(n)\n'
        '    if n <= 1\n'
        '        return n\n'
        '    return fib(n-1) + fib(n-2)\n'
        'var total = 0\n'
        'for i in range(10)\n'
        '    total = total + fib(i)\n'
        'print("fib sum:", total)\n'
    )

    def test_build_exe_matches_interpreter(self):
        interp_out = "\n".join(run_impor(self.PROC_SRC))
        d, exe, r = build_exe(self.PROC_SRC)
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        self.assertTrue(os.path.exists(exe))
        rr = run_exe_clean(exe, d)
        self.assertEqual(rr.returncode, 0, rr.stderr)
        self.assertEqual(rr.stdout.strip(), interp_out.strip())

    def test_build_rich_semantics(self):
        src = (
            'var s = "abc" + "def"\n'
            'print(len(s), s)\n'
            'print(2.5 * 4, 7 / 2, 7 % 3)\n'
            'print(0 or "fallback", "" and "x")\n'
            'var n = 5\n'
            'print("平方 = {n * n}")\n'
            'var flag = 1 > 2\n'
            'print(flag, 1 == 2, 3 >= 3)\n'
            'var x = 3\n'
            'x += 4\n'
            'print(x, 10 - x, x * 2, x / 4)\n'
            'print(to_str(3), to_num("12") + 1, abs(-7), sign(-3))\n'
            'print(min(4, 9), max(4, 9), clamp(5, 0, 3))\n'
        )
        interp_out = "\n".join(run_impor(src))
        d, exe, r = build_exe(src)
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        rr = run_exe_clean(exe, d)
        self.assertEqual(rr.returncode, 0, rr.stderr)
        self.assertEqual(rr.stdout.strip(), interp_out.strip())

    def test_build_sleep_and_loop(self):
        src = (
            'var i = 0\n'
            'while i < 3\n'
            '    print(i)\n'
            '    i += 1\n'
            'for j in range(2, 5)\n'
            '    print("j", j)\n'
            'for k in range(0, 10, 3)\n'
            '    print("k", k)\n'
            'sleep(0.01)\n'
        )
        interp_out = "\n".join(run_impor(src))
        d, exe, r = build_exe(src)
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        rr = run_exe_clean(exe, d)
        self.assertEqual(rr.returncode, 0, rr.stderr)
        self.assertEqual(rr.stdout.strip(), interp_out.strip())


class TestCTranspileErrors(unittest.TestCase):
    """C 后端子集边界：不支持特性必须报 UnsupportedFeature，不静默降级。"""

    def _raises(self, src):
        with self.assertRaises(UnsupportedFeature):
            c_transpile(parse_source(src), "x.impor")

    def test_class_supported_v31(self):
        # v3.1 起 C 后端支持 class / 继承 / super / 属性读写，不再报 UnsupportedFeature
        c = c_transpile(parse_source(
            'class Dog\n    var name = "旺财"\nvar d = new Dog()\nprint(d.name)\n'), "x.impor")
        self.assertIn("reg_class", c)

    def test_list_supported_v217(self):
        # v2.17 起 C 后端支持数组字面量 / 下标 / 内建，不再报 UnsupportedFeature
        c = c_transpile(parse_source('var a = [1, 2, 3]\na[0] = 9\nprint(len(a))\n'), "x.impor")
        self.assertIn("rt_arr_of", c)
        self.assertIn("rt_arr_set", c)

    def test_try_catch_supported_in_c_backend(self):
        # v3.8.4：C 后端补上 try/catch/finally，不再抛 UnsupportedFeature
        c = c_transpile(parse_source(
            'try\n    print(1)\ncatch e\n    print(e.msg)\n'), "x.impor")
        self.assertIn("setjmp", c)
        self.assertIn("g_jmp_n", c)
        self.assertIn("rt_err_obj", c)

    def test_import_unsupported(self):
        self._raises('import mymod\n')

    def test_cli_build_missing_file(self):
        # 不存在的文件：语法错误路径，不应抛内部异常
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                            "build", os.path.join(ROOT, "_no_such_file_.impor")],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_cli_build_help_registered(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                            "help", "build"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("C 后端", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
