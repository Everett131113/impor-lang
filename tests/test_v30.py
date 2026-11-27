# -*- coding: utf-8 -*-
"""v2.10 回归测试：大括号块语法、AST 位置记录、自定义异常、运行时异常包装、作用域规范。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.lexer import tokenize
from impor.parser import Parser
from impor.errors import ImporSyntaxError
from impor.interpreter import Interpreter, ImporException, ImporRuntimeError, parse_source


def run_impor(src):
    out = []
    Interpreter(log_cb=out.append, filename="test.impor").run(parse_source(src, filename="test.impor"))
    return out


class TestBraceBlocks(unittest.TestCase):
    """大括号块语法（新标准）+ quick 单行 + 旧缩进兼容。"""

    def test_brace_if_while(self):
        self.assertEqual(run_impor("""if 1 > 0 { print("yes") }
while false { print("no") }
"""), ["yes"])

    def test_quick_single_line(self):
        self.assertEqual(run_impor("""if 1 > 0 print("quick")
for i in [1, 2] print(i)
"""), ["quick", "1", "2"])

    def test_legacy_indent_still_works(self):
        self.assertEqual(run_impor("""if 1 > 0
    print("indent")
"""), ["indent"])

    def test_brace_function_implicit_return(self):
        self.assertEqual(run_impor("""def add(a, b) { a + b }
print(add(2, 3))
"""), ["5"])

    def test_brace_elseif_else(self):
        self.assertEqual(run_impor("""var x = 2
if x > 5 { print("big") } elseif x > 1 { print("mid") } else { print("small") }
"""), ["mid"])

    def test_brace_while_multiple_stmts(self):
        self.assertEqual(run_impor("""var i = 0
while i < 3 { print(i) set i = i + 1 }
"""), ["0", "1", "2"])

    def test_brace_match_with_else(self):
        src = """def score(x) {
    match x {
        case 1 { return "一" }
        case 2 { return "二" }
        else { return "其他" }
    }
}
print(score(1))
print(score(9))
"""
        self.assertEqual(run_impor(src), ["一", "其他"])

    def test_brace_try_catch_finally(self):
        self.assertEqual(run_impor("""exception Oops
try { raise Oops("boom") } catch Oops e { print(e.msg) } finally { print("fin") }
"""), ["boom", "fin"])

    def test_brace_class_methods(self):
        self.assertEqual(run_impor("""class Dog { var name = "旺财" def speak() { print(self.name) } }
var d = new Dog()
d.speak()
"""), ["旺财"])

    def test_brace_nested_indent_mix(self):
        self.assertEqual(run_impor("""if true {
    if 1 > 0
        print("nested")
}
"""), ["nested"])

    def test_unclosed_brace_reports_position(self):
        with self.assertRaises(ImporSyntaxError):
            parse_source("""if true {
    print(1)
""")


class TestSourcePositions(unittest.TestCase):
    """AST 节点携带行号/列号；语法错误可定位。"""

    def test_parser_error_has_line_col_hint(self):
        with self.assertRaises(ImporSyntaxError) as cm:
            Parser(tokenize("var x = \n")).program()
        e = cm.exception
        self.assertEqual(e.lineno, 1)
        self.assertGreaterEqual(e.col, 8)
        self.assertIn("表达式缺失", e.msg)
        self.assertIsNotNone(e.hint)

    def test_syntax_error_is_syntaxerror(self):
        with self.assertRaises(SyntaxError):
            Parser(tokenize("if true {")).program()

    def test_ast_nodes_carry_col(self):
        ast = parse_source("def f() { 1 + 2 }")
        stmt = ast.stmts[0]
        self.assertTrue(hasattr(stmt, "col"))
        self.assertTrue(hasattr(stmt, "file"))  # v2.10 AST 基类位置字段 file

    def test_newline_token_has_col(self):
        toks = tokenize("print(1)\n")
        nl = [t for t in toks if t.type == "NEWLINE"]
        self.assertTrue(nl)
        self.assertGreaterEqual(nl[0].col, 1)


class TestRuntimeWrapping(unittest.TestCase):
    """运行时异常包装：Python 底层异常 → ImporRuntimeError，隐藏内部堆栈。"""

    def test_div_by_zero_wrapped(self):
        try:
            run_impor("print(1 / 0)")
            self.fail("should raise")
        except ImporRuntimeError as e:
            self.assertEqual(e.name, "ZeroDivisionError")
            self.assertEqual(e.lineno, 1)
            self.assertGreaterEqual(e.col, 8)
            self.assertIsNotNone(e.filename)

    def test_runtime_error_is_impor_exception(self):
        with self.assertRaises(ImporException):
            run_impor("print(1 / 0)")

    def test_caught_in_script_continues(self):
        self.assertEqual(run_impor("""try { print(1 / 0) } catch e { print("caught") }
print("alive")
"""), ["caught", "alive"])

    def test_name_error_position(self):
        try:
            run_impor("print(missing_var)")
            self.fail("should raise")
        except ImporRuntimeError as e:
            self.assertEqual(e.name, "NameError")
            self.assertEqual(e.lineno, 1)
        except ImporException as e:
            # 既有 ImporException 也接受（带位置字段）
            self.assertEqual(e.lineno, 1)
            self.assertGreaterEqual(e.col, 0)


class TestScopeSemantics(unittest.TestCase):
    """v2.10 作用域规范：var 局部 / set 就近更新 / nonlocal / 闭包。"""

    def test_var_local_does_not_leak(self):
        self.assertEqual(run_impor("""var g = 1
def f() { var g = 99 set g = g + 1 print(g) }
f()
print(g)
"""), ["100", "1"])

    def test_set_updates_outer_in_closure(self):
        self.assertEqual(run_impor("""def make_counter() {
    var n = 0
    def inc() { set n = n + 1 return n }
    return inc
}
var c = make_counter()
print(c())
print(c())
print(c())
"""), ["1", "2", "3"])

    def test_nonlocal_set(self):
        self.assertEqual(run_impor("""def outer() {
    var x = 1
    def inner() { nonlocal set x = x + 5 }
    inner()
    print(x)
}
outer()
"""), ["6"])

    def test_instance_fields_isolated(self):
        self.assertEqual(run_impor("""class Counter { var n = 0 def bump() { set self.n = self.n + 1 return self.n } }
var a = new Counter()
var b = new Counter()
a.bump()
print(a.bump())
print(b.bump())
"""), ["2", "1"])


if __name__ == "__main__":
    unittest.main()
