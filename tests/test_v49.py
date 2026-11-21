# -*- coding: utf-8 -*-
"""v3.8.0 回归：enum 枚举 + lint 可读性规则 + fmt 大括号缩进。

① enum：解释器 / Python 转译 / C 后端 / 自举 imporc 四路径同负载输出一致
② enum 语法边界：重复成员、空枚举、非整数值、未知成员
③ lint：命名过短 / 重复魔法数字 / 函数过长，且默认 lint 不误报
④ fmt：大括号块缩进、保留注释、保留原始语义、幂等
"""
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
from impor.analysis import analyze, readability_lint
from impor.errors import ImporSyntaxError
from impor.fmt import check_fmt, fmt_source
from impor.interpreter import Interpreter, parse_source, strip_header

ENUM_SRC = (
    'enum Color { RED, GREEN, BLUE }\n'
    'enum Size\n'
    '    SMALL\n'
    '    MEDIUM = 5\n'
    '    LARGE\n'
    'print(Color.RED, Color.GREEN, Color.BLUE)\n'
    'print(Size.SMALL, Size.MEDIUM, Size.LARGE)\n'
    'def name_of(c)\n'
    '    if c == Color.RED\n'
    '        return "红"\n'
    '    if c == Color.GREEN\n'
    '        return "绿"\n'
    '    return "蓝"\n'
    'print(name_of(Color.GREEN))\n'
)

EXPECTED = ["0 1 2", "0 5 6", "绿"]


def run_impor(src):
    out = []
    Interpreter(log_cb=out.append, filename="v49").run(
        parse_source(strip_header(src), filename="v49"))
    return out


def run_ctrans(src):
    d = tempfile.mkdtemp(prefix="impor_v49c_", dir=os.path.join(ROOT, "tests"))
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


def run_pytrans(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v49p_")
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


def run_imporc(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v49c_")
    os.close(fd)
    old = None
    try:
        if os.path.exists(ARGFILE):
            with io.open(ARGFILE, "r", encoding="utf-8") as f:
                old = f.read()
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
            f.write("run " + sp + "\n")
        r = subprocess.run([IMPORC], capture_output=True, text=True, cwd=ROOT,
                           timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        try:
            os.unlink(sp)
        except OSError:
            pass
        if old is not None:
            with io.open(ARGFILE, "w", encoding="utf-8", newline="\n") as f:
                f.write(old)


class TestEnumInterpreter(unittest.TestCase):
    def test_members_and_explicit_values(self):
        self.assertEqual(run_impor(ENUM_SRC), EXPECTED)

    def test_braces_with_colon(self):
        src = 'enum Flag: { A = 2, B }\nprint(Flag.A, Flag.B)\n'
        self.assertEqual(run_impor(src), ["2 3"])


class TestEnumSyntax(unittest.TestCase):
    def _bad(self, src):
        with self.assertRaises(ImporSyntaxError):
            parse_source(strip_header(src), filename="bad.impor")

    def test_duplicate_member(self):
        self._bad('enum C { A, A }\n')

    def test_empty_enum(self):
        self._bad('enum C { }\n')

    def test_non_integer_value(self):
        self._bad('enum C { A = "x" }\n')

    def test_unknown_member(self):
        self._bad('enum C { A }\nprint(C.NOPE)\n')


class TestEnumPythonTranspile(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_pytrans(ENUM_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), EXPECTED)


SWITCH_SRC = (
    'enum Color { RED, GREEN, BLUE }\n'
    'def name(c)\n'
    '    switch c\n'
    '        case Color.RED\n'
    '            return "红"\n'
    '        case Color.GREEN\n'
    '            return "绿"\n'
    '        default\n'
    '            return "蓝"\n'
    'print(name(Color.BLUE))\n'
)


class TestEnumInSwitch(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_impor(SWITCH_SRC), ["蓝"])

    def test_python_transpile(self):
        rc, out = run_pytrans(SWITCH_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), ["蓝"])


class TestEnumCBackend(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_ctrans(ENUM_SRC)
        self.assertEqual(rc, cli.EXIT_OK, out)
        self.assertEqual(out.splitlines(), EXPECTED)


@unittest.skipUnless(os.path.isfile(IMPORC), "缺少 impor/imporc.exe")
class TestEnumImporc(unittest.TestCase):
    def test_same_output(self):
        rc, out = run_imporc(ENUM_SRC)
        self.assertEqual(rc, 0, out)
        for line in EXPECTED:
            self.assertIn(line, out)


class TestReadabilityLint(unittest.TestCase):
    def _codes(self, body):
        ast = parse_source(body)
        return [i.code for i in readability_lint(ast)]

    def test_short_name(self):
        body = 'def f(a)\n    var b = a\n    return b\nprint(f(1))\n'
        self.assertIn("style-naming", self._codes(body))

    def test_magic_number_repeated(self):
        body = ('def f(x)\n'
                '    if x > 999\n        return 999\n'
                '    if x < 999\n        return 1001\n'
                '    return 0\nprint(f(1))\n')
        self.assertIn("style-magic-number", self._codes(body))

    def test_long_function(self):
        body = 'def long_one()\n' + ('    print(1)\n' * 35) + 'long_one()\n'
        self.assertIn("style-long-function", self._codes(body))

    def test_default_lint_does_not_add_style_noise(self):
        body = 'def f(a)\n    var b = a\n    return b\nprint(f(1))\n'
        codes = [i.code for i in analyze(parse_source(body), body.split("\n")).issues]
        self.assertNotIn("style-naming", codes)
        self.assertNotIn("style-magic-number", codes)
        self.assertNotIn("style-long-function", codes)


FMT_SRC = (
    '// 顶部注释：格式化后必须保留\n'
    'def calc(a,b){\n'
    'var x=1+2   // 行尾注释\n'
    'if x>2{\n'
    'print( "值:"+to_str( x ) )\n'
    '}\n'
    'return x\n'
    '}\n'
    'print(calc(1,2))\n'
)


class TestFmt(unittest.TestCase):
    def test_brace_block_indent_and_comments(self):
        out = fmt_source(FMT_SRC)
        self.assertIn("// 顶部注释：格式化后必须保留", out)
        self.assertIn("// 行尾注释", out)
        self.assertIn("def calc(a, b) {", out)
        self.assertIn("    var x = 1 + 2", out)
        self.assertIn("    if x > 2 {", out)
        self.assertIn("        print(\"值:\" + to_str(x))", out)

    def test_semantics_preserved(self):
        out = fmt_source(FMT_SRC)
        self.assertEqual(run_impor(out), ["值:3", "3"])

    def test_idempotent(self):
        once = fmt_source(FMT_SRC)
        twice = fmt_source(once)
        self.assertEqual(once, twice)

    def test_check_fmt_detects_unformatted(self):
        ok, _ = check_fmt(FMT_SRC)
        self.assertFalse(ok)
        ok2, _ = check_fmt(fmt_source(FMT_SRC))
        self.assertTrue(ok2)

    def test_multiline_string_preserved(self):
        src = 'var s = """第一行\n  第二行\n"""\nprint(s)\n'
        out = fmt_source(src)
        self.assertIn('"""第一行\n  第二行\n"""', out)
        self.assertEqual(run_impor(out), ["第一行\n  第二行\n"])

    def test_dict_key_colon_tight(self):
        src = 'var d = {"a" : 1, "b": 2}\nprint(d["a"], d["b"])\n'
        out = fmt_source(src)
        self.assertIn('{ "a": 1, "b": 2 }', out)
        self.assertEqual(run_impor(out), ["1 2"])


if __name__ == "__main__":
    unittest.main()
