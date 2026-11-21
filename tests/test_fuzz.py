# -*- coding: utf-8 -*-
"""
Impor (I++) 模糊测试（v2.6）。

对 lexer / parser / transpiler 做随机输入测试，捕获崩溃、无限循环与
未包装的内部异常；对随机生成的"有界小程序"做超时执行测试，捕获挂起。

运行（项目根目录）：
    python -m unittest tests.test_fuzz -v
    IMPOR_FUZZ_ITERS=60 python -m unittest tests.test_fuzz -v   # CI 快速模式

可接受异常：SyntaxError（语法错误，Impor 正常行为）。
其余异常（IndexError / AttributeError / RecursionError / 未捕获崩溃）= 失败。
"""

import os
import random
import string
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from impor.lexer import tokenize
from impor.interpreter import parse_source
from impor.transpiler import transpile

IMPOR_PY = os.path.join(PROJECT_ROOT, "Impor.py")

# 迭代次数：环境变量 IMPOR_FUZZ_ITERS 覆盖 chaos 轮数（其余按比例缩减），CI 用小值
_ITERS = int(os.environ.get("IMPOR_FUZZ_ITERS", "250"))
CHAOS_N = _ITERS
STRUCT_N = max(20, _ITERS * 4 // 5)
TRANSPILE_N = max(20, _ITERS * 3 // 5)
PROG_N = max(6, _ITERS // 20)

# 混沌输入字符池：可打印 ASCII + 常见语法符号 + 中文，模拟任意乱码输入
_CHAOS_CHARS = (
    string.printable
    + "中文测试///*/*/\"\"''\\\n\t\r  ，。！？"
    + "importfromasexportfuncdefclasswhileforifelsevar setglobalnonlocalreturn"
)

# 结构化随机行片段池：模仿 Impor 但大概率非法
_STRUCT_LINES = [
    "var x = 1", "var s = \"hi\"", "var lst = [1, 2, 3]", "var d = {\"a\": 1}",
    "set x = x + 1", "def f(a, b)", "    return a + b", "if x > 0", "else",
    "while i < 10", "for i in range(0, 5)", "print(x)", "return x",
    "class A", "    def m(self)", "        return 1", "import mymod",
    "from mymod import hello", "from . import tool", "try", "catch err", "loop",
    "break", "continue", "match x", "case 1:", "case _:", "default:",
    "raise Err(\"msg\")", "export calc", "new A()", "x.y = 1", "a[0] = 2",
    "x += 1", "r\"raw\\n\"", "/* 注释 */", "// 注释", "1 + 2 * 3", "\"a\" + \"b\"",
    "not x", "-x", "x ? 1 : 2", "lambda", "async", "def", "def",
]
_STRUCT_JUNK = ["+", "-", "*", "/", "(", ")", ",", "==", "!=", "<", ">", "=", "..", ".", "["]

# 有界小程序块模板：每个块自含正确缩进，循环必然推进，不会挂起
_PROG_BLOCKS = [
    "while i < 12\n    set i = i + 1\n    set s = s + i\n",
    "for j in range(0, 5)\n    set s = s + j\n",
    "def add(a, b)\n    return a + b\n",
    "if i % 2 == 0\n    print(s)\nelse\n    set s = s + 1\n",
]
_PROG_LINES = [
    "print(s)", "print(len(lst))", "print(to_str(s))", "lst.push(i)",
    "d[i] = i", "print(d[i])", "set s = add(s, i)", "set s = s + 1",
    "print(\"ok\")",
]


def chaos_source(rng):
    """纯随机字符输入（可能空、可能全是符号、可能含未闭合字符串）。"""
    n = rng.randint(0, 1500)
    return "".join(rng.choice(_CHAOS_CHARS) for _ in range(n))


def structured_source(rng):
    """从片段池随机拼行（随机缩进 + 随机垃圾 token 追加）。"""
    n = rng.randint(1, 25)
    lines = []
    for _ in range(n):
        line = rng.choice(_STRUCT_LINES)
        if rng.random() < 0.5:
            line += rng.choice(_STRUCT_JUNK) + rng.choice(_STRUCT_JUNK)
        indent = "    " * rng.randint(0, 4)
        lines.append(indent + line)
    return "\n".join(lines) + "\n"


def random_program(rng):
    """结构感知生成有界可运行小程序（循环块自含推进语句，不会死循环）。"""
    lines = ["var s = 0", "var i = 0", "var j = 0", "var lst = []", "var d = {}"]
    for _ in range(rng.randint(1, 3)):
        lines.append(rng.choice(_PROG_BLOCKS).rstrip("\n"))
    for _ in range(rng.randint(0, 4)):
        lines.append(rng.choice(_PROG_LINES))
    return "\n".join(lines) + "\n"


class TestFuzz(unittest.TestCase):
    def test_tokenize_chaos_never_crashes(self):
        """任意字符输入：tokenize 只允许 SyntaxError，不允许其他异常。"""
        rng = random.Random(20241101)
        for k in range(CHAOS_N):
            src = chaos_source(rng)
            try:
                tokenize(src)
            except SyntaxError:
                pass  # Impor 正常行为
            except Exception as e:
                self.fail(f"chaos #{k} tokenize 崩溃: {type(e).__name__}: {e}\n源码: {src[:120]!r}")

    def test_parse_structured_never_crashes(self):
        """结构化随机源码：parse_source 只允许 SyntaxError。"""
        rng = random.Random(20241102)
        for k in range(STRUCT_N):
            src = structured_source(rng)
            try:
                parse_source(src)
            except SyntaxError:
                pass
            except Exception as e:
                self.fail(f"structured #{k} 解析崩溃: {type(e).__name__}: {e}\n源码: {src[:200]!r}")

    def test_transpile_never_crashes(self):
        """解析成功的随机 AST 必须能转译：AST 合法则 transpile 不允许任何异常。"""
        rng = random.Random(20241103)
        done = 0
        k = 0
        while done < TRANSPILE_N and k < TRANSPILE_N * 10:
            k += 1
            src = structured_source(rng)
            try:
                ast = parse_source(src)
            except SyntaxError:
                continue
            try:
                transpile(ast)
            except Exception as e:
                self.fail(f"transpile #{k} 崩溃: {type(e).__name__}: {e}\n源码: {src[:200]!r}")
            done += 1

    def test_random_programs_execute_with_timeout(self):
        """随机有界小程序：运行必须结束（超时=死循环挂起=bug）；
        退出码 0（正常）或 1（Impor 运行时友好报错）均可。"""
        rng = random.Random(20241104)
        for k in range(PROG_N):
            src = random_program(rng)
            with tempfile.TemporaryDirectory() as td:
                impor_file = os.path.join(td, "prog.impor")
                with open(impor_file, "w", encoding="utf-8") as f:
                    f.write(src)
                try:
                    r = subprocess.run(
                        [sys.executable, IMPOR_PY, "run", impor_file],
                        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    self.fail(f"program #{k} 超时挂起（死循环）:\n{src}")
                self.assertIn(
                    r.returncode, (0, 1),
                    f"program #{k} 非预期退出码 {r.returncode}（内部崩溃）:\n{src}\nstderr: {r.stderr[:300]}",
                )
                if r.returncode == 1:
                    self.assertIn("第", r.stderr, f"运行失败应输出友好行号信息:\n{src}\n{r.stderr[:300]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
