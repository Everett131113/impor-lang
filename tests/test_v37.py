# -*- coding: utf-8 -*-
"""v2.18.0 回归测试：
① 大括号代码块全面可用：{ 单独一行 / 嵌套 / 顶层块 / 单行大括号（省略缩进）
② 关键字扩至 120 个（英文 58 + 中文 62）：fn/let/local/elif/repeat(次数)/each/static/readonly
   none/nil/null 空值字面量、this→self、中文别名（若/且/遍历…）、预留关键字友好报错
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source
from impor import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_impor(src, fname="t.impor"):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.run(parse_source(src, filename=fname))
    return out


def build_exe(src):
    """C 后端编译 → exe，返回 (rc, exe_path, run_out)。"""
    fd, fpath = tempfile.mkstemp(suffix=".impor", prefix="impor_t37_")
    os.close(fd)
    exe = os.path.splitext(fpath)[0] + ".exe"
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(src)
        rc = cli.cmd_build([fpath, "-o", exe])
        if rc != cli.EXIT_OK or not os.path.isfile(exe):
            return rc, None, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return rc, exe, r.stdout
    finally:
        for p in (fpath, exe):
            try:
                os.unlink(p)
            except OSError:
                pass


class TestBraceBlocks(unittest.TestCase):
    """大括号代码块：省略缩进，块内缩进任意。"""

    SRC = (
        'if 1 > 0\n'
        '{\n'
        '    print("brace-own-line")\n'
        '    if 2 > 1\n'
        '    {\n'
        '        print("nested")\n'
        '    }\n'
        '}\n'
        '{\n'
        '    var a = 10\n'
        '    print("top-block", a)\n'
        '}\n'
        'if 3 > 2 { print("inline-brace") }\n'
    )

    def test_brace_own_line(self):
        out = run_impor(self.SRC)
        self.assertIn("brace-own-line", out)
        self.assertIn("nested", out)
        self.assertIn("top-block 10", out)
        self.assertIn("inline-brace", out)

    def test_brace_c_backend(self):
        rc, exe, rout = build_exe(
            'if 1 > 0 { print("c-brace") }\n'
            'if 2 > 1\n'
            '{\n'
            '    print("c-own-line")\n'
            '}\n'
        )
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("c-brace", rout)
        self.assertIn("c-own-line", rout)


class TestEnglishKeywords(unittest.TestCase):
    """v5.2：中文关键字已全部移除，保留字为纯英文集合。"""

    def test_count(self):
        from impor.lexer import KEYWORDS
        self.assertEqual(len(KEYWORDS), 55)  # v4.0.0 删除 func / loop / local
        self.assertFalse(any(any("\u4e00" <= ch <= "\u9fff" for ch in w) for w in KEYWORDS))

    def test_aliases(self):
        out = run_impor(
            'def 加(a, b) { return a + b }\n'
            'var 速度 = 100\n'
            'var 名字 = "impor"\n'
            'if 1 > 0 { print("big") } elseif 2 > 1 { print("elif-ok") } else { print("no") }\n'
            'static PI = 3.14\n'
            'readonly 上限 = 999\n'
            'print(加(1, 2), 速度, 名字, PI, 上限)\n')
        self.assertEqual(out[-1], "3 100 impor 3.14 999")
        self.assertIn("big", out)

    @unittest.skip("v4.0.0 已删除 for each/of；等价写法是 for x in")
    def test_repeat_each(self):
        out = run_impor(
            'repeat 3 { print("r", __r) }\n'
            'var 表 = [10, 20]\n'
            'each 项 in 表 { print("e", 项) }\n'
            'repeat 项2 in 表 { print("r2", 项2) }\n')
        self.assertIn("r 0", out)
        self.assertIn("r 2", out)
        self.assertIn("e 10", out)
        self.assertIn("e 20", out)
        self.assertIn("r2 10", out)

    def test_null_literal(self):
        out = run_impor(
            'print(none, nil, null)\n'
            'var 空值 = none\n'
            'if 空值 == null { print("null-eq") }\n'
            'print(type(none))\n')
        self.assertIn("None None None", out)
        self.assertIn("null-eq", out)
        self.assertEqual(out[-1], "NoneType")

    def test_null_c_backend(self):
        rc, exe, rout = build_exe(
            'print(none)\n'
            'var x = null\n'
            'if x == none { print("c-null-eq") }\n'
            'print(x)\n')
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("None", rout)
        self.assertIn("c-null-eq", rout)

    def test_chinese_keywords_rejected(self):
        from impor.interpreter import ImporException
        with self.assertRaises(ImporException):
            run_impor('若 1 > 0 { print("c-若") }\n')

    def test_this_alias(self):
        out = run_impor(
            'class 点 {\n'
            '  var x = 0\n'
            '  def 设(x) { this.x = x }\n'
            '  def 取() { return this.x }\n'
            '}\n'
            'var p = new 点()\n'
            'p.设(42)\n'
            'print(p.取())\n')
        self.assertEqual(out[-1], "42")

    def test_reserved_keywords(self):
        from impor.parser import Parser
        from impor.lexer import tokenize, KEYWORDS
        from impor.errors import ImporSyntaxError
        # 每个硬关键字都不能当变量名；新增关键字自动进入守护。
        for kw in KEYWORDS:
            with self.assertRaises(ImporSyntaxError):
                Parser(tokenize("var %s = 1\n" % kw)).program()
        # v3.7.1 解封的 11 个 + v3.7.2 降级的 6 个软关键字。
        for kw in ("record", "template", "final", "of", "do",
                   "each", "lambda", "macro",
                   "static", "readonly", "async", "await", "this", "defer"):
            Parser(tokenize("var %s = 1\nprint(%s)\n" % (kw, kw))).program()

    def test_aliases_c_backend(self):
        rc, exe, rout = build_exe(
            'def 加(a, b) { return a + b }\n'
            'var 速度 = 100\n'
            'if 1 > 0 { print("c-big") } elif 2 > 1 { print("c-elif") } else { print("no") }\n'
            'repeat 2 { print("c-rep", __r) }\n'
            'static PI = 3.14\n'
            'print(加(1, 2), 速度, PI)\n')
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("c-big", rout)
        self.assertIn("c-rep 1", rout)
        self.assertIn("3 100 3.14", rout)


if __name__ == "__main__":
    unittest.main()
