# -*- coding: utf-8 -*-
"""v3.1.0 回归测试：三端合一（主解释器 / C 后端 ctrans / 自举编译器 imporc）
覆盖：class 继承 super、数组 for-in、match、try/raise、repeat、数组字面量、range
并做三端同负载输出一致性校验。
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

from impor import cli
from impor.interpreter import Interpreter, parse_source, strip_header


def run_impor(src):
    """主解释器跑源码，返回 stdout 列表。"""
    out = []
    Interpreter(log_cb=out.append, filename="v39").run(parse_source(strip_header(src), filename="v39"))
    return out


def run_ctrans(src):
    """C 后端编译 → 独立 exe → 运行，返回 (rc, stdout)。"""
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v39_")
    os.close(fd)
    exe = sp + ".exe"
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        for p in (sp, exe):
            try:
                os.unlink(p)
            except OSError:
                pass


def run_imporc(mode, script, out=None):
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


OOP_SRC = ("class Animal\n"
           "    def __init__(name)\n"
           "        set self.name = name\n"
           "    def speak()\n"
           "        print(self.name, \"发出声音\")\n"
           "class Dog(Animal)\n"
           "    def speak()\n"
           "        super().speak()\n"
           "        print(self.name, \"汪汪叫！\")\n"
           "var dog = new Dog(\"旺财\")\n"
           "dog.speak()\n")

EACH_SRC = ("var a = [10, 20, 30]\n"
            "for x in a\n"
            "    print(x)\n"
            "def add(p, q)\n"
            "    return p + q\n"
            "print(add(2, 3))\n")

MATCH_SRC = ("match 5\n"
             "    case 1:\n"
             "        print(\"one\")\n"
             "    case 5:\n"
             "        print(\"five\")\n"
             "    default:\n"
             "        print(\"other\")\n")

TRY_SRC = ("exception MyError\n"
           "try\n"
           "    raise MyError(\"boom\")\n"
           "catch MyError e\n"
           "    print(e.msg)\n")

FLOW_SRC = ("exception MyError\n"
            "match 5\n"
            "    case 5:\n"
            "        print(\"five\")\n"
            "    default:\n"
            "        print(\"other\")\n"
            "try\n"
            "    raise MyError(\"boom\")\n"
            "catch MyError e\n"
            "    print(e.msg)\n"
            "repeat 2\n"
            "    print(\"r\")\n"
            "for x in [1, 2]\n"
            "    print(x)\n")


class TestInterpreterV31(unittest.TestCase):
    """主解释器：v3.1 新语法。"""

    def test_class_inherit_super(self):
        out = run_impor(OOP_SRC)
        self.assertEqual(out, ["旺财 发出声音", "旺财 汪汪叫！"])

    def test_array_for_each(self):
        out = run_impor(EACH_SRC)
        self.assertEqual(out, ["10", "20", "30", "5"])

    def test_match(self):
        out = run_impor(MATCH_SRC)
        self.assertEqual(out, ["five"])

    def test_try_raise_catch(self):
        out = run_impor(TRY_SRC)
        self.assertEqual(out, ["boom"])


class TestCtransV31(unittest.TestCase):
    """C 后端（ctrans）：class 与数组 for-in。"""

    def test_class_build(self):
        rc, out = run_ctrans(OOP_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip().replace("\r", ""), "旺财 发出声音\n旺财 汪汪叫！")

    def test_array_for_each_build(self):
        rc, out = run_ctrans(EACH_SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip().replace("\r", ""), "10\n20\n30\n5")


class TestImporcV31(unittest.TestCase):
    """自举编译器（imporc）：match / try / repeat / 数组 for-in。"""

    def _build_run(self, src, tag):
        fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v39_" + tag)
        os.close(fd)
        exe = sp + ".exe"
        try:
            with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
            rc, out = run_imporc("build", sp, exe)
            self.assertEqual(rc, 0, out)
            self.assertTrue(os.path.isfile(exe))
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.replace("\r", "").strip()
        finally:
            for p in (sp, exe):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_flow(self):
        self.assertEqual(self._build_run(FLOW_SRC, "flow"), "five\nboom\nr\nr\n1\n2")

    def test_class(self):
        self.assertEqual(self._build_run(OOP_SRC, "oop"), "旺财 发出声音\n旺财 汪汪叫！")


class TestThreeBackendConsistency(unittest.TestCase):
    """三端同负载输出逐行一致。"""

    def test_oop_three_backends(self):
        interp = "\n".join(run_impor(OOP_SRC)).strip()
        c_rc, c_out = run_ctrans(OOP_SRC)
        self.assertEqual(c_rc, 0)
        self.assertEqual(interp, c_out.replace("\r", "").strip())
        fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v39_cons_")
        os.close(fd)
        exe = sp + ".exe"
        try:
            with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(OOP_SRC)
            self.assertEqual(run_imporc("build", sp, exe)[0], 0)
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(interp, r.stdout.replace("\r", "").strip())
        finally:
            for p in (sp, exe):
                try:
                    os.unlink(p)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
