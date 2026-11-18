# -*- coding: utf-8 -*-
"""v4.2 回归：字典 / 切片 / 表驱动 expect 在解释器、Python 转译、C 后端、自举 imporc 四路径一致。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")
ARGFILE = os.path.join(ROOT, "imporc_args.txt")

from impor import cli
from impor.interpreter import Interpreter, strip_header, parse_source

DICT_SLICE_SRC = (
    'var d = {"name": "小明", "age": 16}\n'
    'print(d["name"], d["age"])\n'
    'd["age"] = 17\n'
    'print(d)\n'
    'print(len(d))\n'
    'var a = [0,1,2,3,4,5]\n'
    'print(a[1:5])\n'
    'print(a[-2:])\n'
    'print(a[::2])\n'
    'var s = "abcdef"\n'
    'print(s[1:4])\n'
    'print(s[::2])\n'
    'print(d.keys())\n'
    'print(d.get("none", "缺"))\n'
    'for k in d\n'
    '    print(k)\n'
)
EXPECTED = [
    "小明 16", "{'name': '小明', 'age': 17}", "2", "[1, 2, 3, 4]",
    "[4, 5]", "[0, 2, 4]", "bcd", "ace", "['name', 'age']", "缺", "name", "age",
]


def run_interp(src):
    out = []
    Interpreter(log_cb=out.append, filename="v42").run(
        parse_source(strip_header(src), filename="v42"))
    return out


def run_pytrans(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v42p_")
    os.close(fd)
    py = sp + ".py"
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True,
                           timeout=60)
        return r.returncode, r.stdout
    finally:
        for p in (sp, py):
            try:
                os.unlink(p)
            except OSError:
                pass


def run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v42c_", dir=os.path.join(ROOT, "tests"))
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


def run_imporc(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v42i_")
    os.close(fd)
    exe = sp[:-6] + ".exe"
    old = None
    try:
        if os.path.exists(ARGFILE):
            with io.open(ARGFILE, "r", encoding="utf-8") as f:
                old = f.read()
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write('build "' + sp + '" -o "' + exe + '"\n')
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT,
                           timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        if os.path.exists(exe):
            rr = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            out += rr.stdout
            os.unlink(exe)
        return r.returncode, out
    finally:
        try:
            os.unlink(sp)
        except OSError:
            pass
        try:
            if old is not None:
                with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
                    f.write(old)
        except OSError:
            pass


class TestDictSliceCrossBackend(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(DICT_SLICE_SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(DICT_SLICE_SRC)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(DICT_SLICE_SRC)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_imporc_backend(self):
        rc, out = run_imporc(DICT_SLICE_SRC)
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if not l.startswith("imporc.impor:")]
        self.assertEqual(lines, EXPECTED)


class TestExpectBuiltin(unittest.TestCase):
    def test_table_driven(self):
        src = (
            'var cases = [[1,1,2],[2,3,5],[0,0,0]]\n'
            'for c in cases\n'
            '    expect(c[0] + c[1], c[2], "add")\n'
        )
        out = []
        it = Interpreter(log_cb=out.append)
        it.run(parse_source(src))
        self.assertEqual(it.assert_count, 3)

    def test_failure_message(self):
        src = 'expect(1 + 1, 3, "加法")\n'
        with self.assertRaises(Exception) as cm:
            Interpreter(log_cb=lambda t: None).run(parse_source(src))
        self.assertIn("加法", str(cm.exception))

    def test_python_transpile(self):
        src = 'expect("a", "a", "eq")\nprint("ok")\n'
        rc, out = run_pytrans(src)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "ok")


class TestImporTestIsolation(unittest.TestCase):
    def test_file_isolation(self):
        d = tempfile.mkdtemp(prefix="impor_v42t_")
        sp = os.path.join(d, "iso_test.impor")
        try:
            with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write('filewrite("side.txt", "x")\nexpect(1 + 1, 2, "add")\n')
            rc = cli.cmd_test([sp])
            self.assertEqual(rc, cli.EXIT_OK)
            self.assertFalse(os.path.exists(os.path.join(d, "side.txt")),
                             "测试文件不应污染源码目录")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
