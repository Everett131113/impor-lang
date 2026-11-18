# -*- coding: utf-8 -*-
"""v4.1 语言核心：type_of 内建 / C 后端模块导入 / imporc 嵌套 switch / flatten。"""
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
from impor.interpreter import Interpreter, parse_source, strip_header


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run_py(src, files=None):
    d = tempfile.mkdtemp(prefix="impor_v53py_", dir=os.path.join(ROOT, "tests"))
    old = os.getcwd()
    try:
        for name, text in (files or {}).items():
            _write(os.path.join(d, name), text)
        _write(os.path.join(d, "main.impor"), src)
        os.chdir(d)
        out = []
        Interpreter(log_cb=out.append, filename="v53").run(
            parse_source(strip_header(src), filename="v53"))
        return out
    finally:
        os.chdir(old)
        shutil.rmtree(d, ignore_errors=True)


def run_c(src, files=None):
    d = tempfile.mkdtemp(prefix="impor_v53c_", dir=os.path.join(ROOT, "tests"))
    try:
        for name, text in (files or {}).items():
            _write(os.path.join(d, name), text)
        sp = os.path.join(d, "main.impor")
        _write(sp, src)
        exe = os.path.join(d, "main.exe")
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60, cwd=d)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_imporc(src):
    old = ""
    try:
        if os.path.exists(ARGFILE):
            with io.open(ARGFILE, "r", encoding="utf-8") as f:
                old = f.read()
        d = tempfile.mkdtemp(prefix="impor_v53s_")
        sp = os.path.join(d, "nested.impor")
        _write(sp, src)
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write("run " + sp)
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT,
                           timeout=120)
        return r.returncode, r.stdout
    finally:
        # 恢复 imporc_args.txt，避免影响其它测试
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write(old or "build bootstrap\\imporc.impor -o imporc_self_raw.exe")


MOD_SRC = (
    'func add(a, b) {\n'
    '    return a + b\n'
    '}\n'
    'func unused_bad() {\n'
    '    var s = "x"\n'
    '    return s.upper()\n'
    '}\n'
    'var G = 10\n'
    'export add, G\n'
)

MAIN_SRC = (
    'import m\n'
    'from m import add as plus\n'
    'print(m.add(2, 3))\n'
    'print(plus(4, 5))\n'
    'print(m.G)\n'
)

EXPECTED = ["5", "9", "10"]


class V53LanguageCore(unittest.TestCase):
    def test_type_of_interpreter(self):
        src = (
            'class Point { var x = 1 }\n'
            'print(type_of([1, 2]))\n'
            'print(type_of("a"))\n'
            'print(type_of(1))\n'
            'print(type_of(true))\n'
            'print(type_of(new Point()))\n'
        )
        self.assertEqual(run_py(src), ["list", "str", "int", "bool", "Point"])

    def test_type_of_c_backend(self):
        src = (
            'class Point { var x = 1 }\n'
            'print(type_of([1, 2]))\n'
            'print(type_of("a"))\n'
            'print(type_of(1))\n'
            'print(type_of(true))\n'
            'print(type_of(new Point()))\n'
        )
        rc, out = run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), ["list", "str", "int", "bool", "Point"])

    def test_c_import_module_and_from(self):
        rc, out = run_c(MAIN_SRC, {"m.impor": MOD_SRC})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_import_prunes_unreferenced_broken_code(self):
        # unused_bad 里的 s.upper() C 后端不支持；未被引用应被裁掉
        rc, out = run_c(MAIN_SRC, {"m.impor": MOD_SRC})
        self.assertEqual(rc, 0, out)

    def test_c_import_class_display_name(self):
        src = (
            'from pt import P\n'
            'var p = new P()\n'
            'print(type_of(p))\n'
            'print(p.x)\n'
        )
        rc, out = run_c(src, {"pt.impor": 'class P {\n    var x = 1\n}\nexport P\n'})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), ["P", "1"])

    def test_c_import_std_name_collision(self):
        src = (
            'import std.algo\n'
            'import std.text\n'
            'print(std.algo.index_of([7, 8, 9], 8))\n'
            'print(std.text.index_of("hello", "ll"))\n'
        )
        rc, out = run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), ["1", "2"])

    def test_flatten_std_collections(self):
        src = (
            'import std.collections\n'
            'print(std.collections.flatten([1, [2, 3], [4, [5]]]))\n'
        )
        rc, out = run_c(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "[1, 2, 3, 4, 5]")

    def test_imporc_nested_switch(self):
        src = (
            'func f(x)\n'
            '    switch x\n'
            '        case 1\n'
            '            switch x\n'
            '                case 1\n'
            '                    return "aa"\n'
            '                default\n'
            '                    return "ab"\n'
            '        case 2\n'
            '            return "b"\n'
            '        default\n'
            '            return "z"\n'
            '    return "end"\n'
            '\n'
            'print(f(1))\n'
            'print(f(2))\n'
            'print(f(3))\n'
        )
        rc, out = run_imporc(src)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines()[-3:], ["aa", "b", "z"])


if __name__ == "__main__":
    unittest.main()
