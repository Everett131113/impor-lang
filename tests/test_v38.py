# -*- coding: utf-8 -*-
"""v2.19.0 回归测试：完整自举独立编译器
① imporc（Impor 自举编译器，原生 C 独立 exe）编译 class 面向对象程序：
   类 / 继承 / super / new / self / 属性读写 / 头部行剥离 / // 注释 / 赋值语句
② imporc CLI（imporc_args.txt 参数文件）build / run / version 三种模式
③ 生成的 exe 为独立可执行文件，脱离 Python 运行
④ 自举闭环（B/C/D 三代逐字节一致）烟雾验证
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")


def run_imporc(mode, script, out=None):
    """写 imporc_args.txt 并运行 imporc.exe。返回 (rc, stdout)。"""
    args = mode + " " + script
    if out:
        args += " -o " + out
    old = None
    if os.path.exists(ARGFILE):
        with io.open(ARGFILE, "r", encoding="utf-8") as f:
            old = f.read()
    try:
        with io.open(ARGFILE, "w", encoding="utf-8") as f:
            f.write(args + "\n")
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT, timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        if old is not None:
            with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
                f.write(old)


class TestImporcClass(unittest.TestCase):
    """imporc 编译 class 面向对象程序并运行。"""

    def test_oop_example_run(self):
        rc, out = run_imporc("run", "examples/oop.impor")
        self.assertEqual(rc, 0, out)
        self.assertIn("旺财 发出声音", out)
        self.assertIn("旺财 汪汪叫！", out)
        self.assertIn("咪咪 喵喵叫！", out)
        self.assertIn("旺财 今年 3 岁", out)

    def test_oop_example_build_independent_exe(self):
        fd, exe = tempfile.mkstemp(suffix=".exe", prefix="impor_t38_")
        os.close(fd)
        try:
            rc, out = run_imporc("build", "examples/oop.impor", exe)
            self.assertEqual(rc, 0, out)
            self.assertTrue(os.path.isfile(exe))
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0)
            self.assertIn("旺财 汪汪叫！", r.stdout)
        finally:
            for p in (exe,):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_inherit_super_attr(self):
        src = (
            'important<<DAY<<english("English"):\n'
            '// 继承 + super + 属性读写 + 赋值语句\n'
            'class Animal\n'
            '    def __init__(name)\n'
            '        set self.name = name\n'
            '    def greet()\n'
            '        print("hi", self.name)\n'
            'class Bird(Animal)\n'
            '    def greet()\n'
            '        super().greet()\n'
            '        print("fly")\n'
            'var b = new Bird("小翠")\n'
            'b.greet()\n'
            'b.age = 2\n'
            'print(b.name, "age", b.age)\n')
        fd, exe = tempfile.mkstemp(suffix=".exe", prefix="impor_t38b_")
        os.close(fd)
        fd2, fpath = tempfile.mkstemp(suffix=".impor", prefix="impor_t38b_")
        os.close(fd2)
        try:
            with io.open(fpath, "w", encoding="utf-8") as f:
                f.write(src)
            rc, out = run_imporc("run", fpath, exe)
            self.assertEqual(rc, 0, out)
            self.assertIn("hi 小翠", out)
            self.assertIn("fly", out)
            self.assertIn("小翠 age 2", out)
        finally:
            for p in (exe, fpath):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_class_field_defaults_and_methods(self):
        src = (
            'class Counter\n'
            '    var count = 0\n'
            '    def add(n)\n'
            '        set self.count = self.count + n\n'
            '    def get()\n'
            '        return self.count\n'
            'var c = new Counter()\n'
            'c.add(5)\n'
            'c.add(3)\n'
            'print(c.get())\n'
            'print(c.count)\n')
        fd, exe = tempfile.mkstemp(suffix=".exe", prefix="impor_t38c_")
        os.close(fd)
        fd2, fpath = tempfile.mkstemp(suffix=".impor", prefix="impor_t38c_")
        os.close(fd2)
        try:
            with io.open(fpath, "w", encoding="utf-8") as f:
                f.write(src)
            rc, out = run_imporc("run", fpath, exe)
            self.assertEqual(rc, 0, out)
            self.assertIn("8", out)
            self.assertIn("8", out)
        finally:
            for p in (exe, fpath):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_version_mode(self):
        rc, out = run_imporc("version", "")
        self.assertEqual(rc, 0, out)
        from impor import __version__ as ver
        self.assertIn("v" + ver, out)


class TestImporcSelfHost(unittest.TestCase):
    """自举闭环：A/B/C/D 三代逐字节一致（完整验证见 bootstrap/selfhost_check.py）。"""

    def test_seed_and_compiler_present(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "bootstrap", "imporc.impor")))
        self.assertTrue(os.path.isfile(IMPORC))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "imporc.cmd")))

    def test_selfhost_closure(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bootstrap", "selfhost_check.py")],
            capture_output=True, text=True, cwd=ROOT, timeout=1800)
        self.assertEqual(r.returncode, 0, (r.stdout or "")[-500:])


if __name__ == "__main__":
    unittest.main()
