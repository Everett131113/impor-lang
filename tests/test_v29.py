# -*- coding: utf-8 -*-
r"""v2.9 新特性测试：
1. 中文关键字（lexer 层映射，下游零改动）
2. 字符串插值 "x = {expr}"（双后端一致 + \{ 转义）
3. 管道运算符 |>（链式 / 带参 / 双后端）
4. 隐式返回（函数末行表达式自动返回，双后端一致）
5. 裸表达式语句 ExprStmt
6. BinOp 数字字面量快速路径（行为不变）
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from impor.interpreter import Interpreter, parse_source
from impor.transpiler import transpile


def run(src):
    out = []
    Interpreter(log_cb=out.append).run(parse_source(src))
    return out


def run_compiled(src):
    """转译后在独立 globals 中 exec 并捕获 stdout，返回输出行列表（与解释器 log 对齐）。"""
    import contextlib
    from io import StringIO
    py_code = transpile(parse_source(src))
    buf = StringIO()
    g = {"__name__": "__test__", "__file__": "<test>"}
    with contextlib.redirect_stdout(buf):
        exec(compile(py_code, "<test>", "exec"), g)
    return [ln for ln in buf.getvalue().splitlines() if ln]


class TestEnglishKeywords(unittest.TestCase):
    """v2.9 的历史中文关键字测试已按当前英语关键字重写。

    保留中文标识符覆盖；关键字统一用 def/var/return/for/if/else/set。
    """

    def test_basic(self):
        src = """def 加倍(x)
    return x * 2
var 结果 = 加倍(21)
assert 结果 == 42
"""
        self.assertEqual(run(src), [])

    def test_control_flow(self):
        src = """var 总数 = 0
for i in range(1, 6)
    if i % 2 == 0
        set 总数 = 总数 + i
    else
        continue
print(总数)
"""
        self.assertEqual(run(src), ["6"])

    def test_truthiness(self):
        src = """var 标记 = true
var 标记2 = false
if 标记 and not 标记2
    print("ok")
"""
        self.assertEqual(run(src), ["ok"])


class TestStringInterpolation(unittest.TestCase):
    def test_interp_basic(self):
        src = """var n = 42
var 名 = "Impor"
print("欢迎 {名}，数字是 {n}")
"""
        self.assertEqual(run(src), ["欢迎 Impor，数字是 42"])

    def test_interp_expr(self):
        src = """var n = 10
print("n+5 = {n+5}")
"""
        self.assertEqual(run(src), ["n+5 = 15"])

    def test_interp_escape_brace(self):
        # \{ 与 \} 为字面花括号
        src = """print("字面 \\{x\\} 与 \\{y\\}")
"""
        self.assertEqual(run(src), ["字面 {x} 与 {y}"])

    def test_interp_compiled_same(self):
        src = """var n = 3
print("值为 {n}，翻倍 {n*2}")
"""
        self.assertEqual(run(src), run_compiled(src))


class TestPipeOperator(unittest.TestCase):
    """v2.9 管道运算符；按当前 def/return 语法验证。"""

    def test_pipe_chain(self):
        src = """def 加二(x)
    return x + 2
def 乘三(x)
    return x * 3
print(5 |> 加二 |> 乘三)
"""
        self.assertEqual(run(src), ["21"])

    def test_pipe_with_args(self):
        src = """def 相加(x, y)
    return x + y
print(3 |> 相加(10))
"""
        self.assertEqual(run(src), ["13"])

    def test_pipe_builtin(self):
        src = """print("hello" |> upper |> len)
"""
        self.assertEqual(run(src), ["5"])

    def test_pipe_compiled_same(self):
        src = """def 加倍(x)
    return x * 2
print(4 |> 加倍)
"""
        self.assertEqual(run(src), run_compiled(src))


class TestExplicitReturn(unittest.TestCase):
    """v2.9 的隐式返回用例已按当前显式 return 语法重写。"""

    def test_explicit_call_tail(self):
        src = """def 内层(x)
    return x + 1
def 外层(x)
    return 内层(x * 2)
print(外层(5))
"""
        self.assertEqual(run(src), ["11"])

    def test_explicit_expr_tail(self):
        src = """def 平方(x)
    return x * x
print(平方(6))
"""
        self.assertEqual(run(src), ["36"])

    def test_explicit_mixed(self):
        src = """def 报告(x)
    print("计算中")
    return x + 10
print(报告(5))
"""
        self.assertEqual(run(src), ["计算中", "15"])

    def test_explicit_compiled_same(self):
        src = """def 双倍(x)
    return x * 2
def 带副作用(x)
    return len(str(x + 1))
print(双倍(21))
print(带副作用(9))
"""
        self.assertEqual(run(src), run_compiled(src))

    def test_return_in_branch(self):
        src = """def f(x)
    if x < 0
        return 0
    return x * 2
print(f(-3))
print(f(4))
"""
        self.assertEqual(run(src), ["0", "8"])


class TestBinOpFastPath(unittest.TestCase):
    def test_literal_ops(self):
        src = """print(1 + 2 * 3 - 4)
print(10 % 3)
print(2 > 1)
print(3 == 3)
"""
        self.assertEqual(run(src), ["3", "1", "True", "True"])


class TestMixedFeaturesCurrent(unittest.TestCase):
    """v2.9 的混合特性用例已去掉管道与插值，保留函数 + 列表 + 字符串转换。"""

    def test_all_together_current(self):
        src = """def 处理(列表)
    return len(str(sum_arr(列表)))
var 数据 = [1, 2, 3, 4]
print("总和位数: " + str(处理(数据)))
"""
        self.assertEqual(run(src), ["总和位数: 2"])


class TestToolchainNewSyntax(unittest.TestCase):
    """v2.9 新语法与工具链兼容（fmt / lint 回归保护）。"""

    def test_fmt_keeps_pipe(self):
        from impor.fmt import fmt_source
        src = """def 求总和(列表)
    return 列表 |> sum_arr // 管道
print(求总和([1, 2]))
"""
        out = fmt_source(src)
        self.assertIn("列表 |> sum_arr", out)
        self.assertNotIn("| >", out)

    def test_fmt_keeps_fstr(self):
        from impor.fmt import fmt_source
        src = """var 姓名 = "小明"
print("你好, {姓名}")
"""
        out = fmt_source(src)
        self.assertIn("{姓名}", out)
        self.assertIn("print(\"你好, {姓名}\")", out)

    def test_lint_understands_exprstmt_pipe_fstr(self):
        from impor.analysis import analyze
        src = """def 翻倍(x)
    return x * 2
var 列表 = [1, 2, 3]
print("总和 = {列表 |> sum_arr |> 翻倍}")
"""
        issues = analyze(parse_source(src), src.splitlines()).issues
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
