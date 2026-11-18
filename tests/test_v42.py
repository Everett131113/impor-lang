# -*- coding: utf-8 -*-
"""v3.4 回归：语法宽容层（同一意图多种写法 / 常见笔误不再直接崩）。

覆盖：print 省略括号、分号分隔、类型注解、多行调用、do-until、
unless、for each/of/区间、then 引导、中文软别名、catch 省略变量、
++/--、&&/||/<>、无括号函数定义、class extends、:= 赋值、new 省略括号。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")


def _write(text):
    d = tempfile.mkdtemp(prefix="v34_")
    p = os.path.join(d, "t.impor")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return d, p


def run(src, build=False):
    d, p = _write(src)
    try:
        cmd = [PY, IMPOR, "build" if build else "run", p]
        if build:
            cmd += ["-o", os.path.join(d, "t.exe")]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=ROOT)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def ok_run(testcase, src, expect=()):
    rc, out, err = run(src)
    testcase.assertEqual(rc, 0, f"rc={rc}\n{err}\n{out}")
    for e in expect:
        testcase.assertIn(e, out)
    return out


def ok_build(expected_ok, src, expect=()):
    """build 成独立 exe 并运行，输出里必须包含 expect 各片段。"""
    testcase = expected_ok
    d, p = _write(src)
    try:
        exe = os.path.join(d, "t.exe")
        r = subprocess.run([PY, IMPOR, "build", p, "-o", exe],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=ROOT)
        testcase.assertEqual(r.returncode, 0,
                             f"build rc={r.returncode}\n{r.stderr}\n{r.stdout}")
        rr = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120, cwd=ROOT)
        out = (rr.stdout or "").replace("\r\n", "\n")
        testcase.assertEqual(rr.returncode, 0, f"exe rc={rr.returncode}\n{rr.stderr}\n{out}")
        for e in expect:
            testcase.assertIn(e, out)
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestPrintTolerance(unittest.TestCase):
    def test_print_without_parens(self):
        out = ok_run(self, 'print "你好"\nprint 1, 2, "a"\n', ["你好", "1 2 a"])

    def test_print_variants_same_output(self):
        a = ok_run(self, 'print("x")\n')
        b = ok_run(self, 'print "x"\n')
        self.assertEqual(a.strip(), b.strip())


class TestStatementSeparators(unittest.TestCase):
    def test_semicolon_separates(self):
        ok_run(self, 'var a = 1; var b = 2; print(a + b)\n', ["3"])

    def test_semicolon_inside_brace_block(self):
        ok_run(self, 'if 1 > 0 { print("a"); print("b") }\n', ["a", "b"])

    def test_semicolon_in_quick_block(self):
        ok_run(self, 'if 1 > 0: print("q1"); print("q2")\n', ["q1", "q2"])

    def test_stray_semicolons_ignored(self):
        ok_run(self, ';\nprint("s")\n;\n', ["s"])


class TestTypeAnnotations(unittest.TestCase):
    def test_var_and_param_and_return(self):
        ok_run(self, '''
var x: int = 5
var y: str = "s"
func add(a: int, b: int) -> int {
  return a + b
}
print(add(x, 2), y)
''', ["7 s"])

    def test_var_without_initializer(self):
        out = ok_run(self, 'var q\nprint(type(q))\n', ["NoneType"])

    def test_const_without_initializer(self):
        ok_run(self, 'const P: int\nprint(type(P))\n', ["NoneType"])


class TestMultilineDelimiters(unittest.TestCase):
    def test_call_split_over_lines(self):
        ok_run(self, '''
func add(a, b) {
  return a + b
}
var r = add(1,
            2)
print(r)
''', ["3"])

    def test_array_split_over_lines(self):
        ok_run(self, '''
var arr = [1,
           2,
           3]
print(len(arr))
''', ["3"])

    def test_multiline_call_inside_indented_block(self):
        ok_run(self, '''
if 1 > 0 {
    print(
        "deep"
    )
    print("after")
}
''', ["deep", "after"])


class TestLoops(unittest.TestCase):
    def test_do_until_runs_once_then_condition(self):
        ok_run(self, '''
var i = 0
do {
  print("i", i)
  i += 1
} until i >= 3
''', ["i 0", "i 1", "i 2"])

    def test_do_while_form(self):
        ok_run(self, '''
var i = 0
do { print("w", i); i += 1 } while i < 2
''', ["w 0", "w 1"])

    def test_for_range_inclusive(self):
        ok_run(self, 'for i in 1..3 { print("r", i) }\n', ["r 1", "r 2", "r 3"])

    def test_for_each_and_of(self):
        ok_run(self, '''
for each x in [10, 20] { print("e", x) }
for y of [7, 8] { print("o", y) }
''', ["e 10", "e 20", "o 7", "o 8"])

    def test_c_style_for(self):
        ok_run(self, '''
for i = 0; i < 3; i++ { print("cf", i) }
for j = 1; j <= 2; j++ { print("cf2", j) }
''', ["cf 0", "cf 1", "cf 2", "cf2 1", "cf2 2"])


class TestConditionTolerance(unittest.TestCase):
    def test_unless(self):
        ok_run(self, 'unless 0 > 1 { print("unless-ok") }\n', ["unless-ok"])

    def test_then_keywords(self):
        ok_run(self, '''
if 1 > 0 then { print("then-ok") }
if 1 > 0 则 { print("ze-ok") }
while 0 > 1 then { print("never") }
print("done")
''', ["then-ok", "ze-ok", "done"])


class TestChineseSoftAliases(unittest.TestCase):
    def test_print_alias(self):
        ok_run(self, '打印 "hi"\n输出 1, 2\n', ["hi", "1 2"])


class TestCatchTolerance(unittest.TestCase):
    def test_catch_without_variable(self):
        ok_run(self, '''
try {
  raise ValueError("boom")
} catch {
  print("caught")
}
''', ["caught"])

    def test_catch_parenthesized_var(self):
        ok_run(self, '''
try {
  var x = 1 / 0
} catch (e) {
  print("e", e.name)
}
''', ["e"])


class TestOperators(unittest.TestCase):
    def test_incdec(self):
        ok_run(self, '''
var i = 0
i++
i++
print(i)
var a = [0]
a[0]++
print(a[0])
''', ["2", "1"])

    def test_prefix_incdec(self):
        ok_run(self, '''
var i = 0
++i
++i
--i
print(i)
''', ["1"])

    def test_c_style_boolean_and_not_equal(self):
        ok_run(self, 'print(true && true, false || true, 1 <> 2)\n',
               ["True True True"])

    def test_colon_eq(self):
        ok_run(self, 'var x := 1\nx := 2\nprint(x)\n', ["2"])


class TestDeclarations(unittest.TestCase):
    def test_no_paren_def(self):
        ok_run(self, '''
func 加 a, b {
  return a + b
}
print(加(2, 3))
''', ["5"])

    def test_new_without_parens(self):
        ok_run(self, '''
class 盒 {
  func __init__() { self.v = 1 }
  func get() { return self.v }
}
var o = new 盒
print(o.get())
''', ["1"])

    def test_super_without_parens(self):
        ok_run(self, '''
class 甲 {
  func hi() { return "hi" }
}
class 乙 extends 甲 {
  func hi() { return super.hi() + "!" }
}
var o = new 乙()
print(o.hi())
''', ["hi!"])


class TestCBackendTolerance(unittest.TestCase):
    """同一批宽容写法在 C 后端（Python 转译 + C 编译）也要成立。"""

    def test_c_print_and_semis(self):
        ok_build(self, 'print "c1"; print(1 + 2)\n', ["c1", "3"])

    def test_c_do_until(self):
        ok_build(self, '''
var i = 0
do { print("c-do", i); i += 1 } until i >= 2
''', ["c-do 0", "c-do 1"])

    def test_c_range_for(self):
        ok_build(self, 'for i in 1..3 { print("c-r", i) }\n',
                 ["c-r 1", "c-r 2", "c-r 3"])

    def test_c_style_for(self):
        ok_build(self, 'for i = 0; i < 2; i++ { print("c-cf", i) }\n',
                 ["c-cf 0", "c-cf 1"])

    def test_c_operators(self):
        ok_build(self, '''
var i = 0
i++
print(i, true && false, 1 <> 2)
''', ["1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
