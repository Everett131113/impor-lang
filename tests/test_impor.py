# -*- coding: utf-8 -*-
"""
Impor (I++) 测试套件。

运行方式（项目根目录）：
    python -m unittest discover -s tests -v
    或
    python run_tests.py

覆盖：词法/语法/语义/内建/异常/类/模块/编译器输出比对。
"""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from impor.lexer import tokenize
from impor.parser import Parser
from impor.interpreter import Interpreter, ImporException, strip_header, parse_source
from impor.transpiler import transpile

HEADER = 'important<<DAY<<english("English"):\n'


def run_impor(src, argv=None):
    """运行 Impor 源码（src 即头部之后的代码体），返回输出行列表。"""
    body = strip_header(src) if src.startswith(HEADER) else src
    ast = parse_source(body)
    out = []
    interp = Interpreter(log_cb=out.append, argv=argv)
    interp.run(ast)
    return out


def run_impor_err(src):
    """运行并返回抛出的 ImporException。"""
    body = strip_header(src) if src.startswith(HEADER) else src
    ast = parse_source(body)
    interp = Interpreter(log_cb=lambda t: None)
    try:
        interp.run(ast)
    except ImporException as e:
        return e
    return None


def compile_and_run(src):
    """编译为 Python 并在子进程中运行，返回输出行列表。"""
    body = strip_header(src) if src.startswith(HEADER) else src
    ast = parse_source(body)
    py_code = transpile(ast)
    with tempfile.TemporaryDirectory() as td:
        pyf = os.path.join(td, "prog.py")
        with open(pyf, "w", encoding="utf-8") as f:
            f.write(py_code)
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        r = subprocess.run([sys.executable, pyf], capture_output=True, text=True,
                           env=env, cwd=PROJECT_ROOT, timeout=60)
        if r.returncode != 0:
            raise AssertionError(f"编译产物运行失败: {r.stderr}")
        return r.stdout.splitlines()


def assert_same_output(test, src):
    a = run_impor(src)
    b = compile_and_run(src)
    # 归一化：解释器按日志条目记录（可能含换行），编译产物按物理行输出
    flat_a = [seg for entry in a for seg in entry.split("\n")]
    test.assertEqual(flat_a, b, f"解释器与编译产物输出不一致\n源码:\n{src}\n解释器: {a}\n编译后: {b}")


# ================= 基础 =================

class TestHeaderAndComments(unittest.TestCase):
    def test_missing_header_strict(self):
        with self.assertRaises(ImporException):
            strip_header("print(1)", strict=True)  # 旧行为：无头部标记报错

    def test_header_optional(self):
        # v2.3：头部可选，缺失不报错，返回原内容
        self.assertEqual(strip_header("print(1)"), "print(1)")
        self.assertEqual(strip_header(HEADER + "print(1)"), "\nprint(1)")

    def test_with_header(self):
        self.assertEqual(run_impor(HEADER + "print(1)"), ["1"])

    def test_no_header_runs(self):
        # 无头部脚本正常解析执行（警告不阻断）
        self.assertEqual(run_impor("print(1)"), ["1"])

    def test_line_comments(self):
        self.assertEqual(run_impor("// 注释\n# 注释\nvar a = 1 // 行尾\nprint(a)"), ["1"])

    def test_block_comments(self):
        src = "/* 多行\n注释 */\nvar a = 1\nprint(a)"
        self.assertEqual(run_impor(src), ["1"])

    def test_unclosed_block_comment(self):
        with self.assertRaises(SyntaxError):
            run_impor("/* 未闭合\nvar a = 1")


class TestVariables(unittest.TestCase):
    def test_var_and_set(self):
        self.assertEqual(run_impor("var a = 100\nvar b = 200\nprint(a + b)"), ["300"])

    def test_reassign(self):
        self.assertEqual(run_impor("var a = 1\na = a + 5\nprint(a)"), ["6"])

    def test_compound_assign(self):
        self.assertEqual(
            run_impor("var a = 10\na += 5\na -= 3\na *= 2\nprint(a)\nvar b = 7\nb %= 4\nprint(b)"),
            ["24", "3"])

    def test_negative_literal(self):
        self.assertEqual(run_impor("var a = -5\nvar b = -a\nprint(a, b)"), ["-5 5"])

    def test_int_float(self):
        self.assertEqual(run_impor("var a = 10\nvar b = 3.5\nprint(a, b, 10 / 4)"), ["10 3.5 2.5"])


class TestStrings(unittest.TestCase):
    def test_quotes(self):
        self.assertEqual(run_impor('var s1 = "hi"\nvar s2 = \'yo\'\nprint(s1, s2)'), ["hi yo"])

    def test_escape(self):
        self.assertEqual(run_impor(r'print("a\tb\nc")'), ["a\tb\nc"])

    def test_triple_string(self):
        self.assertEqual(run_impor('var s = """多行\n文本"""\nprint(s)'), ["多行\n文本"])

    def test_slice(self):
        src = ("var s = \"abcdef\"\n"
               "print(s[1:4])\n"
               "print(s[:3])\n"
               "print(s[3:])\n"
               "print(s[::2])\n"
               "var a = [0, 1, 2, 3, 4, 5]\n"
               "print(a[1:5])\n"
               "print(a[-2:])")
        self.assertEqual(run_impor(src), ["bcd", "abc", "def", "ace", "[1, 2, 3, 4]", "[4, 5]"])
        assert_same_output(self, src)


class TestContainers(unittest.TestCase):
    def test_list(self):
        self.assertEqual(run_impor("var a = [10, 20, 30]\nprint(a[1])\na[0] = 99\nprint(a)"),
                         ["20", "[99, 20, 30]"])

    def test_dict(self):
        self.assertEqual(run_impor('var d = {"name": "小明", "age": 16}\nprint(d["name"], d["age"])'),
                         ["小明 16"])

    def test_negative_index(self):
        self.assertEqual(run_impor("var a = [1,2,3]\nprint(a[-1])"), ["3"])

    def test_nested(self):
        self.assertEqual(run_impor("var a = [[1,2],[3,4]]\nprint(a[1][0])"), ["3"])


class TestOperators(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(run_impor("print(1 + 2 * 3)"), ["7"])
        self.assertEqual(run_impor("print(10 % 3)"), ["1"])

    def test_compare(self):
        self.assertEqual(run_impor("print(3 > 2, 3 <= 2, 3 == 3, 3 != 4)"), ["True False True True"])

    def test_logic(self):
        self.assertEqual(run_impor("print(true and false, true or false, not true, not false)"),
                         ["False True False True"])

    def test_ternary(self):
        self.assertEqual(run_impor('var r = 10 > 5 ? "大于" : "小于"\nprint(r)'), ["大于"])


class TestControlFlow(unittest.TestCase):
    def test_if_elseif_else(self):
        src = ("var a = 7\n"
               "if a > 10\n    print(\"大\")\n"
               "elseif a > 5\n    print(\"中\")\n"
               "else\n    print(\"小\")")
        self.assertEqual(run_impor(src), ["中"])

    def test_if_with_colon(self):
        src = ("var a = 1\n"
               "if a > 0:\n    print(\"正\")\n"
               "else:\n    print(\"负\")")
        self.assertEqual(run_impor(src), ["正"])

    def test_while(self):
        src = "var i = 0\nwhile i < 3\n    print(i)\n    var i = i + 1"
        self.assertEqual(run_impor(src), ["0", "1", "2"])

    def test_loop_break(self):
        src = "var i = 0\nwhile true\n    print(i)\n    var i = i + 1\n    if i >= 3\n        break"
        self.assertEqual(run_impor(src), ["0", "1", "2"])

    def test_continue(self):
        src = "for i in range(0, 5)\n    if i == 2\n        continue\n    print(i)"
        self.assertEqual(run_impor(src), ["0", "1", "3", "4"])

    def test_for_list(self):
        self.assertEqual(run_impor("for x in [10, 20, 30]\n    print(x)"), ["10", "20", "30"])

    def test_for_range(self):
        self.assertEqual(run_impor("for i in range(0, 3)\n    print(i)"), ["0", "1", "2"])

    def test_range_step(self):
        self.assertEqual(run_impor("for i in range(0, 6, 2)\n    print(i)"), ["0", "2", "4"])


# ================= 函数 =================

class TestFunctions(unittest.TestCase):
    def test_def(self):
        src = "def add(x, y)\n    return x + y\nprint(add(3, 5))"
        self.assertEqual(run_impor(src), ["8"])

    def test_def(self):
        src = "def sub(a, b)\n    return a - b\nprint(sub(9, 4))"
        self.assertEqual(run_impor(src), ["5"])

    def test_recursion(self):
        src = ("def fib(n)\n    if n < 2\n        return n\n    return fib(n-1) + fib(n-2)\n"
               "print(fib(10))")
        self.assertEqual(run_impor(src), ["55"])

    def test_bare_return(self):
        src = "def f(x)\n    if x > 0\n        return\n    return 0\nprint(f(5))"
        self.assertEqual(run_impor(src), ["None"])

    def test_closure_nonlocal(self):
        src = ("def make_counter(start)\n"
               "    def inc()\n"
               "        nonlocal set start = start + 1\n"
               "        return start\n"
               "    return inc\n"
               "var c = make_counter(0)\n"
               "print(c())\n"
               "print(c())")
        self.assertEqual(run_impor(src), ["1", "2"])

    def test_global(self):
        src = ("var count = 0\n"
               "def bump()\n"
               "    global set count = count + 1\n"
               "bump()\n"
               "bump()\n"
               "print(count)")
        self.assertEqual(run_impor(src), ["2"])

    def test_local_shadow(self):
        src = ("var x = 10\n"
               "def f()\n    var x = 99\n    return x\n"
               "print(f(), x)")
        self.assertEqual(run_impor(src), ["99 10"])

    def test_arg_count_error(self):
        e = run_impor_err("def f(a)\n    return a\nprint(f(1, 2))")
        self.assertIsNotNone(e)
        self.assertEqual(e.name, "CallError")


# ================= 类 =================

class TestClasses(unittest.TestCase):
    def test_class_basic(self):
        src = ("class Person\n"
               "    def __init__(name)\n        set self.name = name\n"
               "    def say()\n        print(self.name)\n"
               "var p = new Person(\"张三\")\n"
               "p.say()")
        self.assertEqual(run_impor(src), ["张三"])

    def test_inheritance_super(self):
        src = ("class Person\n"
               "    def say()\n        print(\"人\")\n"
               "class Student(Person)\n"
               "    def say()\n        super().say()\n        print(\"学生\")\n"
               "var s = new Student()\n"
               "s.say()")
        self.assertEqual(run_impor(src), ["人", "学生"])

    def test_instance_fields(self):
        src = ("class C\n"
               "    def setx(v)\n        set self.v = v\n"
               "    def getx()\n        return self.v\n"
               "var c = new C()\n"
               "c.setx(42)\n"
               "print(c.getx(), c.v)")
        self.assertEqual(run_impor(src), ["42 42"])

    def test_method_chain(self):
        src = ("class C\n"
               "    def a()\n        print(\"a\")\n        self.b()\n"
               "    def b()\n        print(\"b\")\n"
               "var c = new C()\n"
               "c.a()")
        self.assertEqual(run_impor(src), ["a", "b"])


# ================= 异常 =================

class TestExceptions(unittest.TestCase):
    def test_catch_any(self):
        src = "try\n    print(1 / 0)\ncatch err\n    print(\"捕获\", err.msg)"
        self.assertEqual(run_impor(src), ["捕获 division by zero"])

    def test_catch_typed(self):
        src = ("try\n    var a = [1,2]\n    print(a[99])\n"
               "catch IndexError err\n    print(\"索引\", err.msg)")
        self.assertEqual(run_impor(src), ["索引 list index out of range"])

    def test_catch_typed_comma(self):
        src = ("exception MyError\n"
               "try\n    raise MyError(\"x\")\n"
               "catch MyError, e\n    print(\"抓到\", e.msg)")
        self.assertEqual(run_impor(src), ["抓到 x"])

    def test_custom_exception(self):
        src = ("exception MyError\n"
               "try\n    raise MyError(\"出错啦\")\n"
               "catch MyError e\n    print(\"捕获:\", e.msg)")
        self.assertEqual(run_impor(src), ["捕获: 出错啦"])

    def test_unmatched_type_reraises(self):
        e = run_impor_err("exception A\nexception B\n"
                          "try\n    raise A(\"x\")\ncatch B e\n    print(\"不会到这\")")
        self.assertIsNotNone(e)
        self.assertEqual(e.name, "A")

    def test_finally(self):
        src = ("try\n    print(\"try\")\n"
               "catch e\n    print(\"catch\")\n"
               "finally\n    print(\"finally\")")
        self.assertEqual(run_impor(src), ["try", "finally"])

    def test_finally_on_error(self):
        src = ("try\n    print(1 / 0)\n"
               "catch e\n    print(\"catch\")\n"
               "finally\n    print(\"finally\")")
        self.assertEqual(run_impor(src), ["catch", "finally"])

    def test_name_error_line(self):
        e = run_impor_err("var a = 1\nprint(zzz)")
        self.assertIsNotNone(e)
        self.assertEqual(e.name, "NameError")
        self.assertEqual(e.lineno, 2)


# ================= 内建与标准库 =================

class TestBuiltins(unittest.TestCase):
    def test_len_type(self):
        self.assertEqual(run_impor('var a = [1,2,3]\nprint(len(a))\nprint(type(a))\nprint(type("s"))'),
                         ["3", "list", "str"])

    def test_to_num_to_str(self):
        self.assertEqual(run_impor('print(to_num("3.14"))\nprint(to_num("3") + 1)\nprint(to_str(42) + "!")'),
                         ["3.14", "4", "42!"])

    def test_string_tools(self):
        self.assertEqual(run_impor('print(trim("  hi  "))\nprint(upper("ab"))\nprint(lower("AB"))\n'
                                   'print(strcut("abcdef", 1, 4))\nprint(strjoin([1,2,3], "-"))'),
                         ["hi", "AB", "ab", "bcd", "1-2-3"])

    def test_array_tools(self):
        self.assertEqual(run_impor("var a = [3, 1, 2]\nprint(max_arr(a), min_arr(a), sum_arr(a))\n"
                                   "print(arrfind(a, 2))\nprint(arrrev(a))\n"
                                   "arrappend(a, 9)\nprint(a)\nprint(arrpop(a))"),
                         ["3 1 6", "2", "[2, 1, 3]", "[3, 1, 2, 9]", "9"])

    def test_randi_range(self):
        out = run_impor("for i in range(0, 50)\n    var r = randi(1, 3)\n    if r < 1 or r > 3\n        print(\"越界\")")
        self.assertEqual(out, [])

    def test_shuffle(self):
        out = run_impor("var a = [1,2,3,4,5]\nshuffle(a)\nprint(sum_arr(a), len(a))")
        self.assertEqual(out, ["15 5"])

    def test_game_tools(self):
        out = run_impor("var p = vec2(10, 20)\n"
                        "p.x = 50\n"
                        "p.y = 60\n"
                        "print(p)\n"
                        "print(distance(0, 0, p.x, p.y))\n"
                        "print(clamp(150, 0, 100), lerp(0, 10, 0.5), sign(-3))\n"
                        "print(remap(50, 0, 100, 0, 1))")
        self.assertEqual(out, ["(50, 60)", "78.10249675906654", "100 5.0 -1", "0.5"])

    def test_color(self):
        out = run_impor("var c = rgb(255, 100, 50)\nprint(c, c.hex, c.r)")
        self.assertEqual(out, ["#FF6432 #FF6432 255"])

    def test_print_multi(self):
        self.assertEqual(run_impor('print("a", 1, true)'), ["a 1 True"])

    def test_exit(self):
        self.assertEqual(run_impor("print(1)\nexit\nprint(2)"), ["1"])

    def test_hash(self):
        out = run_impor('print(len(hash("abc")))')
        self.assertEqual(out, ["32"])


class TestImport(unittest.TestCase):
    def test_import_math(self):
        out = run_impor("import math\nprint(math.floor(3.7))")
        self.assertEqual(out, ["3"])

    def test_import_legacy(self):
        out = run_impor("import important.math\nprint(math.ceil(3.2))")
        self.assertEqual(out, ["4"])

    def test_import_alias(self):
        out = run_impor("import random as rnd\nvar v = rnd.randint(1, 5)\nprint(1 <= v <= 5)")
        self.assertEqual(out, ["True"])

    def test_import_user_module(self):
        mod_dir = tempfile.mkdtemp()
        mod_path = os.path.join(mod_dir, "mymod.impor")
        with open(mod_path, "w", encoding="utf-8") as f:
            f.write(HEADER + "def hello()\n    return \"来自模块\"\nvar version = \"1.0\"\n")
        body = strip_header(HEADER + "import mymod\nprint(mymod.hello(), mymod.version)")
        ast = parse_source(body)
        out = []
        interp = Interpreter(log_cb=out.append)
        interp.module_search_paths.insert(0, mod_dir)
        interp.run(ast)
        self.assertEqual(out, ["来自模块 1.0"])


# ================= 模块系统 v2.1（阶段一） =================

def _run_with_mods(mods, src, interp=None):
    """在临时目录放置模块文件后运行程序体 src。

    mods: {相对路径: 模块体（自动补头部）}
    返回输出行列表；抛 ImporException 则原样上抛。
    """
    mod_dir = tempfile.mkdtemp()
    for fn, content in mods.items():
        full = os.path.join(mod_dir, fn)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if not content.startswith(HEADER):
            content = HEADER + content
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    ast = parse_source(strip_header(HEADER + src) if not src.startswith(HEADER) else strip_header(src))
    out = []
    if interp is None:
        interp = Interpreter(log_cb=out.append)
    else:
        interp.log_cb = out.append
    interp.module_search_paths.insert(0, mod_dir)
    interp.run(ast)
    return out


class TestModuleSystem(unittest.TestCase):
    def test_from_import_selective(self):
        mods = {"mymod.impor": "def hello()\n    return \"hi\"\ndef calc(a, b)\n    return a + b\n"}
        out = _run_with_mods(mods, "from mymod import hello, calc\nprint(hello(), calc(2, 3))")
        self.assertEqual(out, ["hi 5"])

    def test_from_import_alias(self):
        mods = {"mymod.impor": "def hello()\n    return \"hi\"\n"}
        out = _run_with_mods(mods, "from mymod import hello as hi\nprint(hi())")
        self.assertEqual(out, ["hi"])

    def test_import_user_alias(self):
        mods = {"mymod.impor": "def hello()\n    return \"hi\"\n"}
        out = _run_with_mods(mods, "import mymod as mm\nprint(mm.hello())")
        self.assertEqual(out, ["hi"])

    def test_star_import(self):
        mods = {"mymod.impor": "def a1()\n    return 1\nvar v = 5\n"}
        out = _run_with_mods(mods, "from mymod import *\nprint(a1(), v)")
        self.assertEqual(out, ["1 5"])

    def test_no_export_full_expose(self):
        # 兼容旧行为：无 export 的模块全部顶层符号对外可见
        mods = {"mymod.impor": "def f()\n    return 7\nvar v = 9\n"}
        out = _run_with_mods(mods, "import mymod\nprint(mymod.f(), mymod.v)")
        self.assertEqual(out, ["7 9"])

    def test_export_strict_private(self):
        mods = {"mymod.impor":
                "def pub()\n    return \"公开\"\n"
                "def secret()\n    return \"私有\"\n"
                "export pub\n"}
        out = _run_with_mods(
            mods,
            "import mymod\nprint(mymod.pub())\n"
            "try\n    print(mymod.secret())\n"
            "catch err\n    print(\"私有不可见\")")
        self.assertEqual(out, ["公开", "私有不可见"])

    def test_export_batch(self):
        mods = {"mymod.impor":
                "def a()\n    return 1\n"
                "def b()\n    return 2\n"
                "export { a, b }\n"}
        out = _run_with_mods(mods, "from mymod import a, b\nprint(a() + b())")
        self.assertEqual(out, ["3"])

    def test_export_missing_name(self):
        mods = {"mymod.impor": "def ok()\n    return 1\nexport nope\n"}
        with self.assertRaises(ImporException) as cm:
            _run_with_mods(mods, "import mymod")
        self.assertEqual(cm.exception.name, "ExportError")

    def test_module_not_found(self):
        with self.assertRaises(ImporException) as cm:
            _run_with_mods({}, "import no_such_module_xyz")
        self.assertEqual(cm.exception.name, "ModuleNotFound")

    def test_package_dotted_import(self):
        mods = {
            "mygame/__pkg__.impor": "var version = \"1.0\"\nexport version\n",
            "mygame/graphics.impor": "def draw()\n    return \"画图\"\n",
            "mygame/physics.impor": "def step()\n    return \"物理\"\n",
        }
        out = _run_with_mods(
            mods,
            "import mygame.graphics\n"
            "print(mygame.graphics.draw())\n"
            "from mygame.physics import step\n"
            "print(step())")
        self.assertEqual(out, ["画图", "物理"])

    def test_package_pkg_aggregate(self):
        # __pkg__.impor 聚合子模块符号并导出
        mods = {
            "mygame/__pkg__.impor": "from .graphics import draw\nexport { draw }\n",
            "mygame/graphics.impor": "def draw()\n    return \"画图\"\n",
        }
        out = _run_with_mods(mods, "from mygame import draw\nprint(draw())")
        self.assertEqual(out, ["画图"])

    def test_relative_import_parent(self):
        # 子模块内用 .. 访问父包模块
        mods = {
            "pkg/__pkg__.impor": "var name = \"pkg\"\n",
            "pkg/a.impor": "def aval()\n    return \"A\"\n",
            "pkg/sub/__pkg__.impor": "var name = \"sub\"\n",
            "pkg/sub/b.impor": "from ..a import aval\ndef bval()\n    return aval() + \"B\"\n",
        }
        out = _run_with_mods(mods, "from pkg.sub.b import bval\nprint(bval())")
        self.assertEqual(out, ["AB"])

    def test_relative_import_forbidden_at_top(self):
        with self.assertRaises(ImporException) as cm:
            _run_with_mods({}, "from .x import y")
        self.assertEqual(cm.exception.name, "ImportError")
        self.assertIn("包内", cm.exception.msg)

    def test_circular_import_safe(self):
        mods = {
            "a.impor": "import b\ndef aval()\n    return \"a\"\n",
            "b.impor": "import a\ndef bval()\n    return \"b\"\n",
        }
        out = _run_with_mods(mods, "import a\nprint(a.aval(), a.b.bval())")
        self.assertEqual(out, ["a b"])

    def test_module_meta(self):
        mods = {"mymod.impor": "var version = \"1.0\"\ndef hello()\n    return \"hi\"\n"}
        out = _run_with_mods(
            mods,
            "import mymod\nprint(mymod.__name__)\nprint(mymod.__is_package__)\nprint(mymod.hello())")
        self.assertEqual(out, ["mymod", "False", "hi"])

    def test_package_meta(self):
        mods = {"mygame/__pkg__.impor": "var version = \"1.0\"\nexport version\n"}
        out = _run_with_mods(mods, "import mygame\nprint(mygame.__is_package__)\nprint(mygame.version)")
        self.assertEqual(out, ["True", "1.0"])

    def test_reload_updates_in_place(self):
        mod_dir = tempfile.mkdtemp()
        mod_path = os.path.join(mod_dir, "mymod.impor")
        with open(mod_path, "w", encoding="utf-8") as f:
            f.write(HEADER + "def f()\n    return 1\n")
        interp = Interpreter(log_cb=lambda t: None)
        interp.module_search_paths.insert(0, mod_dir)
        ast1 = parse_source("import mymod\nprint(mymod.f())")
        out1 = []
        interp.log_cb = out1.append
        interp.run(ast1)
        self.assertEqual(out1, ["1"])
        # 修改模块文件后 reload，同一引用看到新函数
        with open(mod_path, "w", encoding="utf-8") as f:
            f.write(HEADER + "def f()\n    return 2\n")
        out2 = []
        interp.log_cb = out2.append
        ast2 = parse_source("reload(\"mymod\")\nprint(mymod.f())")
        interp.run(ast2)
        self.assertEqual(out2, ["2"])

    def test_reload_unloaded_module(self):
        with self.assertRaises(ImporException) as cm:
            _run_with_mods({}, "reload(\"not_loaded\")")
        self.assertEqual(cm.exception.name, "ModuleNotFound")

    def test_compile_from_import_parity(self):
        # 解释器 vs 编译产物：from-import 别名 + 包聚合 + export 严格模式
        mod_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(mod_dir, "pkg"), exist_ok=True)
        H = HEADER
        with open(os.path.join(mod_dir, "mymod.impor"), "w", encoding="utf-8") as f:
            f.write(H + "def hello()\n    return \"hi\"\nexport { hello }\n")
        with open(os.path.join(mod_dir, "pkg", "__pkg__.impor"), "w", encoding="utf-8") as f:
            f.write(H + "from .util import tool\nexport tool\n")
        with open(os.path.join(mod_dir, "pkg", "util.impor"), "w", encoding="utf-8") as f:
            f.write(H + "def tool()\n    return \"TOOL\"\n")
        src = ('import_path("{}")\n'
               "from mymod import hello as hi\n"
               "print(hi())\n"
               "from pkg import tool\n"
               "print(tool())\n").format(mod_dir.replace(os.sep, "/"))
        assert_same_output(self, src)


class TestSandbox(unittest.TestCase):
    def test_sandbox_blocks_open(self):
        body = strip_header(HEADER + 'open("x.txt")')
        ast = parse_source(body)
        interp = Interpreter(log_cb=lambda t: None, sandbox_mode=True)
        with self.assertRaises(ImporException) as ctx:
            interp.run(ast)
        self.assertEqual(ctx.exception.name, "SecurityError")


class TestArgs(unittest.TestCase):
    def test_args(self):
        out = run_impor("print(args())", argv=["a", "b"])
        self.assertEqual(out, ["['a', 'b']"])


# ================= 编译器（解释器 vs 编译产物） =================

class TestTranspiler(unittest.TestCase):
    def test_hello(self):
        assert_same_output(self, 'var name = "世界"\nprint("你好,", name)')

    def test_control_flow(self):
        src = ("var total = 0\n"
               "for i in range(1, 6)\n    total += i\n"
               "print(total)\n"
               "var i = 0\n"
               "while i < 3\n    print(i)\n    var i = i + 1\n"
               "if total > 10\n    print(\"大\")\n"
               "elseif total > 5\n    print(\"中\")\n"
               "else\n    print(\"小\")")
        assert_same_output(self, src)

    def test_functions_closures(self):
        src = ("def make_counter(start)\n"
               "    def inc()\n"
               "        nonlocal set start = start + 1\n"
               "        return start\n"
               "    return inc\n"
               "var c = make_counter(0)\n"
               "print(c(), c())\n"
               "def add(x, y)\n    return x + y\n"
               "print(add(3, 4))")
        assert_same_output(self, src)

    def test_global_and_compound(self):
        src = ("var count = 0\n"
               "def bump()\n"
               "    global set count = count + 1\n"
               "bump()\n"
               "bump()\n"
               "print(count)\n"
               "var a = 5\n"
               "a *= 3\n"
               "print(a)")
        assert_same_output(self, src)

    def test_classes(self):
        src = ("class Person\n"
               "    def __init__(name)\n        set self.name = name\n"
               "    def say()\n        print(\"我是\", self.name)\n"
               "class Student(Person)\n"
               "    def say()\n        super().say()\n        print(\"学生\")\n"
               "var p = new Person(\"张三\")\n"
               "p.say()\n"
               "var s = new Student(\"李四\")\n"
               "s.say()")
        assert_same_output(self, src)

    def test_exceptions(self):
        src = ("exception MyError\n"
               "try\n    raise MyError(\"出错\")\n"
               "catch MyError e\n    print(\"捕获:\", e.msg)\n"
               "finally\n    print(\"清理\")\n"
               "try\n    var a = [1]\n    print(a[9])\n"
               "catch IndexError err\n    print(\"索引:\", err.msg)")
        assert_same_output(self, src)

    def test_builtins(self):
        src = ('var p = vec2(3, 4)\n'
               'print(p)\n'
               'print(clamp(200, 0, 100), lerp(0, 10, 0.25), sign(-9))\n'
               'print(len("hello"), upper("abc"), to_num("7") + 1)\n'
               'var a = [3, 1, 2]\n'
               'print(max_arr(a), min_arr(a), sum_arr(a))\n'
               'print(strjoin([1, 2], "-"), strcut("abcdef", 1, 3))')
        assert_same_output(self, src)

    def test_ternary_and_logic(self):
        src = ('var x = 10\n'
               'var r = x > 5 ? "大" : "小"\n'
               'print(r)\n'
               'print(true and false, not true, x == 10 and x > 3)')
        assert_same_output(self, src)

    def test_import_math_compiled(self):
        src = "import math\nprint(math.floor(3.7))\nimport random\nprint(1 <= random.randint(1, 3) <= 3)"
        assert_same_output(self, src)

    def test_strings_containers(self):
        src = ('var d = {"name": "小明", "age": 16}\n'
               'print(d["name"])\n'
               'd["age"] = 17\n'
               'print(d["age"])\n'
               'var s = """多行\n文本"""\n'
               'print(s)')
        assert_same_output(self, src)

    def test_loop_break_continue(self):
        src = ("var i = 0\n"
               "while true\n    var i = i + 1\n    if i >= 4\n        break\n"
               "    if i == 2\n        continue\n"
               "    print(i)")
        assert_same_output(self, src)

    def test_recursion(self):
        src = ("def fib(n)\n    if n < 2\n        return n\n    return fib(n-1) + fib(n-2)\n"
               "for i in range(0, 8)\n    print(fib(i))")
        assert_same_output(self, src)


class TestRepl(unittest.TestCase):
    def _repl_out(self, lines):
        import io
        from impor.repl import repl
        out = io.StringIO()
        repl(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
        return out.getvalue()

    def test_repl_basic(self):
        text = self._repl_out(["print(1 + 2)", "var x = 5", "print(x * 2)", "exit"])
        self.assertIn("Impor (I++)", text)
        self.assertIn("3", text)
        self.assertIn("10", text)

    def test_repl_multiline_block(self):
        text = self._repl_out(["if 1 > 0", '    print("yes")', "",
                               "else", '    print("no")', "", "exit"])
        self.assertIn("yes", text)
        self.assertNotIn("no", text)

    def test_repl_errors(self):
        text = self._repl_out(["print(zzz)", "exit"])
        self.assertIn("运行错误", text)


# ================= Impor-Pro P0（v2.1） =================

class _QuitDbg(BaseException):
    pass


class TestRawString(unittest.TestCase):
    def test_raw_double_quote(self):
        self.assertEqual(run_impor(r'print(r"a\nb\tc")'), [r"a\nb\tc"])

    def test_raw_single_quote(self):
        self.assertEqual(run_impor(r"print(r'a\qb')"), [r"a\qb"])

    def test_raw_uppercase(self):
        self.assertEqual(run_impor(r'print(R"x\y")'), [r"x\y"])

    def test_normal_escape_still_works(self):
        self.assertEqual(run_impor(r'print("a\tb")'), ["a\tb"])

    def test_raw_with_quotes_inside(self):
        self.assertEqual(run_impor(r'print(r"say \"hi\"")'), [r'say \"hi\"'])


class TestMatchCase(unittest.TestCase):
    def test_literal_wildcard(self):
        src = ("var x = 3\n"
               "match x\n"
               "    case 1:\n        print(\"一\")\n"
               "    case 2:\n        print(\"二\")\n"
               "    case _:\n        print(\"其他\")")
        self.assertEqual(run_impor(src), ["其他"])

    def test_string_bool_literal(self):
        src = ("def pick(k)\n"
               "    match k\n"
               "        case \"red\":\n            return 1\n"
               "        case true:\n            return 2\n"
               "        case _:\n            return 0\n"
               "print(pick(\"red\"))\nprint(pick(true))")
        self.assertEqual(run_impor(src), ["1", "2"])

    def test_binding_pattern(self):
        src = ("def f(n)\n"
               "    match n\n"
               "        case 0:\n            return \"零\"\n"
               "        case v:\n            return \"值:\" + to_str(v)\n"
               "print(f(0))\nprint(f(9))")
        self.assertEqual(run_impor(src), ["零", "值:9"])

    def test_no_match_falls_through(self):
        src = ("var r = \"未匹配\"\n"
               "match 99\n"
               "    case 1:\n        set r = \"一\"\n"
               "print(r)")
        self.assertEqual(run_impor(src), ["未匹配"])

    def test_match_is_soft_keyword(self):
        src = "var match = 5\nprint(match)\nvar casex = match + 1\nprint(casex)"
        self.assertEqual(run_impor(src), ["5", "6"])

    def test_negative_literal_pattern(self):
        src = ("var x = -3\n"
               "match x\n"
               "    case -1:\n        print(\"负一\")\n"
               "    case -3:\n        print(\"负三\")\n"
               "    case _:\n        print(\"其他\")")
        self.assertEqual(run_impor(src), ["负三"])

    def test_transpile_parity(self):
        src = ("def score(x)\n"
               "    match x\n"
               "        case 1:\n            return \"一\"\n"
               "        case n:\n            return \"其他:\" + to_str(n)\n"
               "print(score(1))\nprint(score(7))")
        assert_same_output(self, src)


class TestExceptionChain(unittest.TestCase):
    def test_raise_from_in_paren(self):
        src = ("exception Inner\nexception Outer\n"
               "try\n"
               "    try\n        raise Inner(\"boom\")\n"
               "    catch e\n        raise Outer(\"wrap\", from e)\n"
               "catch err\n"
               "    print(err.msg)\n    print(err.cause.msg)")
        self.assertEqual(run_impor(src), ["wrap", "boom"])

    def test_raise_from_after_paren(self):
        src = ("exception Inner\nexception Outer\n"
               "try\n    try\n        raise Inner(\"a\")\n"
               "    catch e\n        raise Outer(\"b\") from e\n"
               "catch err\n    print(err.msg, err.cause.msg)")
        self.assertEqual(run_impor(src), ["b a"])

    def test_no_cause(self):
        src = ("exception E\n"
               "try\n    raise E(\"x\")\n"
               "catch err\n    print(err.msg)\n    print(err.cause)")
        self.assertEqual(run_impor(src), ["x", "None"])

    def test_exception_stack_frames(self):
        src = ("exception E\n"
               "def inner()\n    raise E(\"深\")\n"
               "def outer()\n    inner()\n"
               "try\n    outer()\n"
               "catch err\n    print(len(err.stack))")
        self.assertEqual(run_impor(src), ["3"])  # main + outer + inner

    def test_transpile_parity(self):
        src = ("exception Inner\nexception Outer\n"
               "try\n"
               "    try\n        raise Inner(\"a\")\n"
               "    catch e\n        raise Outer(\"b\", from e)\n"
               "catch err\n    print(err.msg, err.cause.msg)")
        assert_same_output(self, src)


class TestCompoundAssignExt(unittest.TestCase):
    def test_attr_compound(self):
        src = ("class Box\n"
               "    def __init__()\n        set self.val = 1\n"
               "var b = new Box()\n"
               "b.val += 10\n"
               "b.val *= 3\n"
               "print(b.val)")
        self.assertEqual(run_impor(src), ["33"])

    def test_index_compound(self):
        src = ("var a = [10, 20, 30]\n"
               "a[0] += 5\n"
               "a[1] *= 2\n"
               "a[2] %= 7\n"
               "print(a)")
        self.assertEqual(run_impor(src), ["[15, 40, 2]"])

    def test_dict_compound(self):
        src = ('var d = {"n": 1}\n'
               'd["n"] += 9\n'
               'print(d["n"])')
        self.assertEqual(run_impor(src), ["10"])

    def test_transpile_parity(self):
        src = ("class Box\n"
               "    def __init__()\n        set self.val = 1\n"
               "var b = new Box()\n"
               "b.val += 10\n"
               "var a = [1, 2]\n"
               "a[1] += 5\n"
               "print(b.val, a[1])")
        assert_same_output(self, src)


class TestAttrBoundary(unittest.TestCase):
    def test_int_attr_set_error(self):
        e = run_impor_err("var x = 5\nx.len = 2")
        self.assertIsNotNone(e)
        self.assertEqual(e.name, "AttributeError")

    def test_list_attr_set_error(self):
        e = run_impor_err("var a = [1,2]\na.size = 3")
        self.assertIsNotNone(e)
        self.assertEqual(e.name, "AttributeError")


class TestDebugSystem(unittest.TestCase):
    def test_breakpoint_pause_and_stack(self):
        src = ("def add(a, b)\n"
               "    var s = a + b\n"
               "    return s\n"
               "var t = add(1, 2)\n"
               "print(t)")
        pauses = []
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        interp.breakpoints.add(3)  # return s 行
        interp.debug_pause_callback = lambda ln, env: pauses.append(
            (ln, len(interp.call_stack) - 1, [f.name for f in interp.call_stack]))
        interp.run(parse_source(src))
        self.assertEqual(pauses, [(3, 1, ["<main>", "add"])])

    def test_step_into_start(self):
        # 启动即单步：只暂停第一条语句
        src = "var a = 1\nvar b = 2\nvar c = a + b\nprint(c)"
        pauses = []
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        interp.step_mode = "into"
        interp.debug_pause_callback = lambda ln, env: pauses.append(ln)
        interp.run(parse_source(src))
        self.assertEqual(pauses, [1])

    def test_step_over_skips_function(self):
        src = ("def f()\n    return 7\n"
               "var y = f()\n"
               "print(y)")
        pauses = []
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        interp.step_mode = "into"
        def on_pause(ln, env):
            pauses.append(ln)
            if ln == 1:
                interp.step_mode = "over"
                interp._dbg_step_target = interp._dbg_depth()  # 0
            elif ln == 3:
                interp.step_mode = None
        interp.debug_pause_callback = on_pause
        interp.run(parse_source(src))
        self.assertEqual(pauses, [1, 3])

    def test_step_into_function(self):
        src = ("def f()\n    return 7\n"
               "var y = f()\n"
               "print(y)")
        pauses = []
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        interp.step_mode = "into"
        def on_pause(ln, env):
            pauses.append(ln)
            if ln == 1:
                interp.step_mode = "over"
                interp._dbg_step_target = 0
            elif ln == 3:
                interp.step_mode = "into"  # 步入函数
            else:
                interp.step_mode = None
        interp.debug_pause_callback = on_pause
        interp.run(parse_source(src))
        self.assertEqual(pauses, [1, 3, 2])

    def test_step_out_to_caller(self):
        src = ("def f()\n    var s = 1\n    return s\n"
               "var y = f()\n"
               "print(y)")
        pauses = []
        interp = Interpreter(log_cb=lambda t: None, debug_mode=True)
        interp.breakpoints.add(2)  # 停到函数内
        def on_pause(ln, env):
            pauses.append(ln)
            if ln == 2:
                interp.step_mode = "out"
                interp._dbg_step_target = interp._dbg_depth() - 1  # 0
            else:
                interp.step_mode = None
        interp.debug_pause_callback = on_pause
        interp.run(parse_source(src))
        # finish 语义：函数返回后停在调用语句之后的下一行（GDB finish 同款）
        self.assertEqual(pauses, [2, 5])

    def test_call_stack_clean_after_run(self):
        src = "def a()\n    return 1\nvar x = a()\nprint(x)"
        interp = Interpreter(log_cb=lambda t: None)
        interp.run(parse_source(src))
        self.assertEqual(len(interp.call_stack), 1)
        self.assertEqual(interp.call_stack[0].name, "<main>")


class TestCliRunDbg(unittest.TestCase):
    def test_run_dbg_interactive(self):
        from unittest import mock
        from impor.cli import cmd_run_dbg
        script = ("important<<DAY<<english(\"English\"):\n"
                  "var total = 0\n"
                  "def add(a, b)\n"
                  "    var s = a + b\n"
                  "    return s\n"
                  "total = add(10, 20)\n"
                  "total += 5\n"
                  "print(total)\n")
        with tempfile.TemporaryDirectory() as td:
            fname = os.path.join(td, "dbg.impor")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(script)
            cmds = ["b 5", "c", "p s", "bt", "q"]
            buf = []
            with mock.patch("builtins.input", side_effect=cmds):
                rc = cmd_run_dbg([fname])
        self.assertEqual(rc, 0)
        # 输出已被 cmd_run_dbg 打到 stdout（捕获不到也不影响断言）

    def test_run_dbg_prints_traceback_on_error(self):
        from unittest import mock
        from impor.cli import cmd_run_dbg
        script = ("important<<DAY<<english(\"English\"):\n"
                  "def a()\n    raise\n"
                  "a()\n")
        # 用真实异常：未声明异常类型 raise 会报 RaiseError
        script = ("important<<DAY<<english(\"English\"):\n"
                  "def a()\n"
                  "    var z = 1 / 0\n"
                  "a()\n")
        with tempfile.TemporaryDirectory() as td:
            fname = os.path.join(td, "err.impor")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(script)
            with mock.patch("builtins.input", side_effect=["c", "q"]):
                rc = cmd_run_dbg([fname])
        self.assertEqual(rc, 1)


# ================= Impor-Pro P1（v2.3 工具链） =================

class TestFmt(unittest.TestCase):
    def test_indent_normalized(self):
        from impor.fmt import fmt_source
        src = HEADER + "def f()\n" \
                       "    var a = 1\n" \
                       "    return a\n" \
                       "print(f())\n"
        out = fmt_source(src)
        self.assertIn("\n    var a = 1\n", out)
        self.assertIn("\n    return a\n", out)
        self.assertTrue(out.startswith(HEADER))

    def test_trailing_whitespace_removed(self):
        from impor.fmt import fmt_source
        src = HEADER + "var a = 1   \nprint(a)  \n"
        self.assertNotIn("   \n", fmt_source(src))

    def test_comments_preserved(self):
        from impor.fmt import fmt_source
        src = HEADER + "// 注释\nvar a = 1  # 行尾注释\nprint(a) // 尾部\n"
        out = fmt_source(src)
        self.assertIn("// 注释", out)
        self.assertIn("# 行尾注释", out)
        self.assertIn("// 尾部", out)

    def test_string_content_unchanged(self):
        from impor.fmt import fmt_source
        src = HEADER + 'var s = "a  b  c"\nprint(s)\nvar r = r"x  y"\nprint(r)\n'
        out = fmt_source(src)
        self.assertIn('"a  b  c"', out)
        self.assertIn('r"x  y"', out)

    def test_blank_lines_collapsed(self):
        from impor.fmt import fmt_source
        src = HEADER + "var a = 1\n\n\n\n\nvar b = 2\n"
        out = fmt_source(src)
        self.assertEqual(out.count("\n\n"), 1)

    def test_check_fmt(self):
        from impor.fmt import check_fmt
        ok, _ = check_fmt(HEADER + "var a = 1\n")
        self.assertTrue(ok)
        ok, _ = check_fmt(HEADER + "var a = 1   \n")
        self.assertFalse(ok)

    def test_operator_spacing(self):
        from impor.fmt import fmt_source
        src = HEADER + "var a = 1+2*3\nvar b=a==1\nprint(a+b)\n"
        out = fmt_source(src)
        self.assertIn("var a = 1 + 2 * 3", out)
        self.assertIn("var b = a == 1", out)

    def test_fmt_roundtrip_runs(self):
        src = HEADER + ("def fib(n)\n"
                        "    if n < 2\n        return n\n"
                        "    return fib(n-1)+fib(n-2)\n"
                        "print(fib(6))\n")
        from impor.fmt import fmt_source
        out = fmt_source(src)
        self.assertEqual(run_impor(out), ["8"])


class TestLint(unittest.TestCase):
    def _lint(self, body):
        from impor.analysis import analyze
        ast = parse_source(body)
        return analyze(ast, body.split("\n")).issues

    def test_undefined_variable(self):
        issues = self._lint("print(x)\n")
        codes = [i.code for i in issues]
        self.assertIn("undefined-name", codes)

    def test_builtins_not_flagged(self):
        issues = self._lint("print(len([1,2]), to_str(3), range(0,3))\n")
        self.assertEqual([i.code for i in issues], [])

    def test_unused_param(self):
        issues = self._lint("def f(a, b)\n    return a\nprint(f(1, 2))\n")
        codes = [i.code for i in issues]
        self.assertIn("unused-variable", codes)
        self.assertTrue(any("b" in i.msg for i in issues))

    def test_unused_import(self):
        issues = self._lint("import math\nprint(1)\n")
        codes = [i.code for i in issues]
        self.assertIn("unused-import", codes)

    def test_used_import_not_flagged(self):
        issues = self._lint("import math\nprint(math.sqrt(4))\n")
        self.assertEqual([i.code for i in issues], [])

    def test_unreachable_after_return(self):
        issues = self._lint("def f()\n    return 1\n    print(2)\nprint(f())\n")
        codes = [i.code for i in issues]
        self.assertIn("unreachable", codes)

    def test_self_and_export_not_flagged(self):
        body = ("class C\n"
                "    def setx(self, v)\n        set self.x = v\n"
                "    def getx(self)\n        return self.x\n"
                "var c = new C()\n"
                "c.setx(5)\n"
                "export c\n")
        issues = self._lint(body)
        self.assertEqual([i.code for i in issues], [])

    def test_module_var_used_in_function(self):
        body = ("var g = 10\n"
                "def f()\n    return g\n"
                "print(f())\n")
        issues = self._lint(body)
        self.assertEqual([i.code for i in issues], [])

    def test_for_and_catch_vars(self):
        body = ("for i in range(0, 3)\n    print(i)\n"
                "try\n"
                "    var z = 1 / 0\n"
                "catch e\n    print(e.msg)\n")
        issues = self._lint(body)
        self.assertEqual([i.code for i in issues], [])


class TestLsp(unittest.TestCase):
    def _server(self):
        import io
        from impor.lsp import LanguageServer
        return LanguageServer(stdin=io.BytesIO(), stdout=io.BytesIO())

    def test_initialize_reply(self):
        s = self._server()
        s._on_initialize(1, {"rootUri": "file:///tmp"})
        s.stdout.seek(0)
        payload = s.stdout.read()
        self.assertIn(b'"capabilities"', payload)
        self.assertIn(b'"definitionProvider"', payload)
        self.assertIn(b'"hoverProvider"', payload)

    def test_diagnostics_syntax_error(self):
        s = self._server()
        uri = "file:///t.impor"
        s._update_doc(uri, "var a = \n")
        s.stdout.seek(0)
        payload = s.stdout.read()
        self.assertIn(b"textDocument/publishDiagnostics", payload)
        self.assertIn(b"diagnostics", payload)

    def test_definition(self):
        s = self._server()
        uri = "file:///t.impor"
        s._update_doc(uri, "def add(a, b)\n    return a + b\nvar x = add(1, 2)\n")
        s._on_definition(2, {"textDocument": {"uri": uri},
                             "position": {"line": 2, "character": 10}})
        s.stdout.seek(0)
        payload = s.stdout.read().decode("utf-8")
        self.assertIn('"line": 0', payload)  # 定义在第 1 行

    def test_hover(self):
        s = self._server()
        uri = "file:///t.impor"
        s._update_doc(uri, "var name = \"x\"\nprint(name)\n")
        s._on_hover(3, {"textDocument": {"uri": uri},
                        "position": {"line": 1, "character": 7}})
        s.stdout.seek(0)
        payload = s.stdout.read().decode("utf-8")
        self.assertIn("变量", payload)
        self.assertIn("name", payload)

    def test_document_symbol(self):
        s = self._server()
        uri = "file:///t.impor"
        s._update_doc(uri, "def f()\n    return 1\nclass C\n    def m(self)\n        return 2\n")
        s._on_document_symbol(4, {"textDocument": {"uri": uri}})
        s.stdout.seek(0)
        payload = s.stdout.read().decode("utf-8")
        self.assertIn('"name": "f"', payload)
        self.assertIn('"name": "C"', payload)


class TestPkg(unittest.TestCase):
    def _make_pkg(self, root, name, ver):
        pkg = os.path.join(root, name)
        os.makedirs(pkg)
        with open(os.path.join(pkg, "impor.toml"), "w", encoding="utf-8") as f:
            f.write(f'[project]\nname = "{name}"\nversion = "{ver}"\n')
        with open(os.path.join(pkg, "lib.impor"), "w", encoding="utf-8") as f:
            f.write(HEADER + f"def hello()\n    return \"{name} {ver}\"\nexport hello\n")
        return pkg

    def _make_project(self, root):
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "impor.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "proj"\nversion = "0.1.0"\n')
        return root

    def test_version_parse_and_compare(self):
        from impor.pkgmgr import _norm_ver, parse_constraint, ver_satisfies
        self.assertEqual(_norm_ver("1.2.3"), (1, 2, 3))
        self.assertEqual(_norm_ver("v0.5"), (0, 5, 0))
        self.assertEqual(parse_constraint(">=0.2"), (">=", (0, 2, 0)))
        self.assertTrue(ver_satisfies("0.3.0", ">=0.2"))
        self.assertFalse(ver_satisfies("0.1.0", ">=0.2"))
        self.assertTrue(ver_satisfies("1.0.0", "==1.0"))

    def test_install_remove_list_lock(self):
        from impor import pkgmgr
        with tempfile.TemporaryDirectory() as td:
            src = self._make_pkg(td, "mylib", "0.2.0")
            proj = self._make_project(os.path.join(td, "proj"))
            cache = os.path.join(td, "cache")
            pkgmgr.CACHE_DIR = cache

            name, ver = pkgmgr.install(src, project_dir=proj, cache_dir=cache)
            self.assertEqual((name, ver), ("mylib", "0.2.0"))
            self.assertTrue(os.path.isdir(os.path.join(cache, "mylib")))
            self.assertTrue(os.path.isfile(os.path.join(proj, "impor.lock")))

            deps, installed = pkgmgr.list_pkgs(project_dir=proj, cache_dir=cache)
            self.assertIn("mylib", deps)
            self.assertEqual(installed.get("mylib"), "0.2.0")

            entries = pkgmgr.lock(project_dir=proj, cache_dir=cache)
            self.assertEqual(entries["mylib"]["version"], "0.2.0")
            self.assertEqual(len(entries["mylib"]["hash"]), 12)

            removed = pkgmgr.remove("mylib", project_dir=proj, cache_dir=cache)
            self.assertTrue(removed)
            deps, _ = pkgmgr.list_pkgs(project_dir=proj, cache_dir=cache)
            self.assertNotIn("mylib", deps)

    def test_install_version_constraint_violation(self):
        from impor import pkgmgr
        with tempfile.TemporaryDirectory() as td:
            src = self._make_pkg(td, "oldlib", "0.1.0")
            proj = self._make_project(os.path.join(td, "proj"))
            with open(os.path.join(proj, "impor.toml"), "a", encoding="utf-8") as f:
                f.write('\n[dependencies]\noldlib = ">=0.5"\n')
            cache = os.path.join(td, "cache")
            with self.assertRaises(ValueError):
                pkgmgr.install(src, project_dir=proj, cache_dir=cache)


# ================= Impor-Pro P0（v2.3.1 优化） =================

class TestHeaderOptional(unittest.TestCase):
    def test_hint_only_when_env_set(self):
        import io
        import os as _os
        from contextlib import redirect_stderr
        from impor.interpreter import strip_header
        old = _os.environ.get("IMPOR_HEADER_HINT")
        _os.environ["IMPOR_HEADER_HINT"] = "1"
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                body = strip_header("print(1)", warn=True)
        finally:
            if old is None:
                _os.environ.pop("IMPOR_HEADER_HINT", None)
            else:
                _os.environ["IMPOR_HEADER_HINT"] = old
        self.assertEqual(body, "print(1)")
        self.assertIn("Impor 提示", buf.getvalue())

    def test_warn_is_silent_without_env(self):
        import io
        import os as _os
        from contextlib import redirect_stderr
        from impor.interpreter import strip_header
        old = _os.environ.pop("IMPOR_HEADER_HINT", None)
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                strip_header("print(1)", warn=True)
        finally:
            if old is not None:
                _os.environ["IMPOR_HEADER_HINT"] = old
        self.assertEqual(buf.getvalue(), "")

    def test_no_warn_silent(self):
        import io
        from contextlib import redirect_stderr
        from impor.interpreter import strip_header
        buf = io.StringIO()
        with redirect_stderr(buf):
            body = strip_header("print(1)")
        self.assertEqual(body, "print(1)")
        self.assertEqual(buf.getvalue(), "")


class TestLineMap(unittest.TestCase):
    def test_transpile_embeds_line_map(self):
        from impor.transpiler import transpile
        src = ("def f(x)\n"
               "    return x + missing_name\n"
               "print(f(1))\n")
        ast = parse_source(src)
        code = transpile(ast, source_name="t.impor")
        self.assertIn("install_tb_map", code)
        self.assertIn("# L2", code)   # return 语句行号注释
        self.assertIn("# L3", code)

    def test_runtime_error_shows_source_line(self):
        import tempfile
        from impor.transpiler import transpile
        src = ("var a = 1\n"
               "var b = a + 2\n"
               "print(nonexistent)\n")
        ast = parse_source(src)
        code = transpile(ast, source_name="t.impor")
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        with tempfile.TemporaryDirectory() as td:
            pyfile = os.path.join(td, "out.py")
            with open(pyfile, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            r = subprocess.run([sys.executable, pyfile], capture_output=True,
                               text=True, cwd=PROJECT_ROOT, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("t.impor: 第3行", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_transpile_no_error_parity(self):
        # 成功路径：行号映射不影响正常输出
        src = "def fib(n)\n    if n < 2\n        return n\n    return fib(n-1)+fib(n-2)\nprint(fib(6))\n"
        ast = parse_source(src)
        from impor.transpiler import transpile
        code = transpile(ast, source_name="t.impor")
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        with tempfile.TemporaryDirectory() as td:
            pyfile = os.path.join(td, "out.py")
            with open(pyfile, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            r = subprocess.run([sys.executable, pyfile], capture_output=True,
                               text=True, cwd=PROJECT_ROOT, env=env)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "8")


class TestXdgCacheDir(unittest.TestCase):
    def test_windows_default(self):
        from impor import pkgmgr
        self.assertTrue(pkgmgr._default_cache_dir().endswith(
            os.path.join(".impor", "pkg")))

    def test_posix_xdg(self):
        from impor import pkgmgr
        # Windows 上 os.path 按 ntpath 判定 POSIX 路径，mock isabs 验证 XDG 分支
        with unittest.mock.patch("impor.pkgmgr.os.path.isabs", return_value=True):
            d = pkgmgr._posix_cache_dir("/tmp/xdg")
        self.assertTrue(d.replace("\\", "/").startswith("/tmp/xdg/impor/pkg"))
        d2 = pkgmgr._posix_cache_dir(None)
        parts = d2.split(os.sep)
        self.assertIn("impor", parts)
        self.assertIn("pkg", parts)


class TestLintIgnore(unittest.TestCase):
    def _lint(self, body):
        from impor.analysis import analyze
        ast = parse_source(body)
        return analyze(ast, body.split("\n")).issues

    def test_line_ignore_all(self):
        body = ("var a = 1  # impor-lint-ignore\n"
                "var b = 2\n"
                "print(a, b)\n")
        codes = [i.code for i in self._lint(body)]
        self.assertEqual(codes, [])

    def test_line_ignore_specific_rule(self):
        body = ("import math  # impor-lint-ignore: unused-import\n"
                "print(1)\n")
        codes = [i.code for i in self._lint(body)]
        self.assertNotIn("unused-import", codes)

    def test_file_ignore(self):
        body = ("# impor-lint-ignore-file\n"
                "var x = 1\n"
                "print(nope)\n")
        self.assertEqual([i.code for i in self._lint(body)], [])

    def test_disabled_rules(self):
        body = ("import math\n"
                "print(nope)\n")
        ast = parse_source(body)
        from impor.analysis import analyze
        an = analyze(ast, body.split("\n"),
                     disabled_rules={"undefined-name", "unused-import"})
        self.assertEqual([i.code for i in an.issues], [])


# ================= v2.4 语法宽松与稳定性增强 =================

class TestUnaryRhs(unittest.TestCase):
    """一元正负号允许出现在二元/比较运算符右侧（v2.4 语法宽松）。"""

    def test_cmp_negative_literal(self):
        assert_same_output(self, "var a = 1\nif a != -1\n    print(\"ok\")\nelse\n    print(\"bad\")\n")

    def test_add_unary(self):
        assert_same_output(self, "var a = 5\nprint(a + -2)\nprint(a - -3)\n")

    def test_mul_unary(self):
        assert_same_output(self, "var a = 2\nprint(a * -4)\nprint(a / -2)\n")

    def test_chained_unary(self):
        assert_same_output(self, "var a = 3\nprint(- -a)\nprint(a + - -1)\n")

    def test_range_compare(self):
        assert_same_output(self, "var a = 0\nif a >= -2 and a <= 2\n    print(\"in\")\n")


class TestMatchElse(unittest.TestCase):
    """match-case 支持 else: / default: 兜底分支（v2.4）。"""

    def test_else_fallback(self):
        src = ("var x = 99\n"
               "match x\n"
               "    case 1:\n"
               "        print(\"one\")\n"
               "    else:\n"
               "        print(\"other\")\n")
        assert_same_output(self, src)

    def test_default_alias(self):
        src = ("var x = \"z\"\n"
               "match x\n"
               "    case \"a\":\n"
               "        print(\"A\")\n"
               "    default:\n"
               "        print(\"Z\")\n")
        assert_same_output(self, src)

    def test_else_not_run_when_matched(self):
        src = ("var x = 2\n"
               "match x\n"
               "    case 2:\n"
               "        print(\"two\")\n"
               "    else:\n"
               "        print(\"else\")\n")
        assert_same_output(self, src)

    def test_else_with_binding(self):
        src = ("var x = 7\n"
               "match x\n"
               "    case v:\n"
               "        print(v)\n"
               "    else:\n"
               "        print(\"never\")\n")
        assert_same_output(self, src)

    def test_only_else(self):
        src = ("var x = 1\n"
               "match x\n"
               "    else:\n"
               "        print(\"always\")\n")
        assert_same_output(self, src)


class TestLintForwardRef(unittest.TestCase):
    """lint：模块级前向引用（后声明函数/变量）不误报未定义（v2.4）。"""

    def _codes(self, body):
        from impor.analysis import analyze
        ast = parse_source(body)
        return [i.code for i in analyze(ast, body.split("\n")).issues]

    def test_forward_func_call(self):
        body = ("def main()\n"
                "    helper()\n"
                "def helper()\n"
                "    print(1)\n"
                "main()\n")
        self.assertNotIn("undefined-name", self._codes(body))

    def test_forward_global_var(self):
        body = ("def f()\n"
                "    print(g)\n"
                "var g = 10\n"
                "f()\n")
        self.assertNotIn("undefined-name", self._codes(body))

    def test_true_undefined_still_reported(self):
        body = "print(nope)\n"
        self.assertIn("undefined-name", self._codes(body))


if __name__ == "__main__":
    unittest.main(verbosity=2)