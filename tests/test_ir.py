# -*- coding: utf-8 -*-
"""4.4.0 IR 层回归。

三块验收，对齐 xlang 的做法（阶段可单测、IR 可 dump、IR 可独立执行）：
  1. lowering 结构：指令形状、标签、隐式返回、序列化稳定；
  2. 语义对拍：IR 参考求值器 vs 树遍历解释器，逐行输出必须相同；
  3. 拒绝路径：不支持的语法带行号明确报错，不静默降级；CLI 行为固定。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source, strip_header
from impor.ir import IRError, UnsupportedNode, evaluate
from impor.ir_lower import lower_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")

# 覆盖各类语句/表达式的内联用例：每条都必须与解释器逐行一致
CASES = {
    "arith": 'var a = 17\nvar b = 5\nprint("sum", a + b, a / b, a % b)\nprint(div(a, b), abs(-9))\n',
    "if_elif_else": 'var a = 3\nif a > 5\n    print("big")\nelif a > 1\n    print("mid")\nelse\n    print("small")\n',
    "for_range": 'var total = 0\nfor i in range(1, 5)\n    total = total + i\nprint("total", total)\n',
    "for_range_step": 'var s = 0\nfor i in range(10, 0, -2)\n    s += i\nprint(s)\n',
    "while_break_continue": ('var i = 0\nvar s = 0\nwhile true\n    i += 1\n'
                             '    if i > 6\n        break\n    if i % 2 == 0\n'
                             '        continue\n    s += i\nprint(i, s)\n'),
    "recursive_def": ('def fib(n)\n    if n <= 1\n        return n\n'
                      '    return fib(n-1) + fib(n-2)\nprint(fib(10))\n'),
    "implicit_return": 'def add(a, b)\n    a + b\nprint(add(2, 3))\n',
    "string_fstr": 'var name = "x"\nprint("hi,{name}!", len(name), name + "-" + name)\n',
    "list_slice_set": 'var a = [3, 1, 2]\nprint(a[0], len(a), a[1:3])\na[1] = 9\nprint(a)\n',
    "dict_index": 'var d = {"k": 1}\nd["n"] = 2\nprint(d["k"] + d["n"], d["n"])\n',
    "logic": 'print(1 && 2, 0 || 3, !0)\n',
    "ternary": 'var x = 3\nprint(x > 2 ? "yes" : "no")\n',
    "higher_order": ('def apply(f, x)\n    return f(x)\ndef twice(n)\n    return n * 2\n'
                     'print(apply(twice, 21))\n'),
    "assert_ok": 'var a = 2\nassert a + 1 == 3\nprint("ok")\n',
}


def run_interp(src, name="t.impor"):
    out = []
    Interpreter(log_cb=out.append, filename=name).run(
        parse_source(strip_header(src), name))
    return out


def run_ir(src, name="t.impor"):
    module = lower_source(strip_header(src), name)
    out, ev = evaluate(module)
    return out, module, ev


class TestLoweringStructure(unittest.TestCase):
    def test_render_has_stages_and_ops(self):
        module = lower_source(CASES["for_range"], "t.impor")
        text = module.render()
        self.assertTrue(text.startswith("; Impor IR"), text[:80])
        self.assertIn("func __main__", text)
        for op in ("STORE", "CALL", "ITER", "ITER_NEXT", "JUMP", "PRINT", "RETURN"):
            self.assertIn(op, text, "IR 文本缺少指令 %s" % op)
        for label in ("L_for_cond", "L_for_end"):
            self.assertIn(label, text, "IR 文本缺少标签 %s" % label)

    def test_stats_count_functions_and_labels(self):
        st = lower_source(CASES["recursive_def"], "t.impor").stats()
        self.assertEqual(st["functions"], 2)   # __main__ + fib
        self.assertGreater(st["instructions"], 0)
        loop_st = lower_source(CASES["for_range"], "t.impor").stats()
        self.assertEqual(loop_st["labels"], 2)  # for_cond / for_end

    def test_function_has_implicit_return(self):
        module = lower_source(CASES["implicit_return"], "t.impor")
        fn = [f for f in module.funcs if f.name == "add"][0]
        self.assertEqual(fn.params, ["a", "b"])
        ops = [i.op for i in fn.body]
        self.assertIn("RETURN", ops)
        # 隐式返回发生在收尾 RETURN nil 之前
        self.assertNotEqual(ops[-2], "CONST")

    def test_render_is_stable(self):
        module = lower_source(CASES["while_break_continue"], "t.impor")
        self.assertEqual(module.render(), module.render())


class TestDifferential(unittest.TestCase):
    """IR 求值器与解释器必须给出同样的输出行。"""

    def test_inline_cases_match_interpreter(self):
        for name, src in CASES.items():
            with self.subTest(case=name):
                expected = run_interp(src)
                actual, module, ev = run_ir(src)
                self.assertEqual(expected, actual,
                                 "case=%s module=%r" % (name, module))
                self.assertGreater(ev.executed, 0)

    def test_short_circuit_does_not_divide_by_zero(self):
        src = 'var x = 0\nprint(x != 0 && 1 / x > 1)\n'
        self.assertEqual(run_interp(src), ["False"])
        self.assertEqual(run_ir(src)[0], ["False"])

    def test_demo_examples_match_interpreter(self):
        covered, mismatched, rejected = [], [], []
        if not os.path.isdir(EXAMPLES):
            self.skipTest("缺少 examples 目录")
        for fn in sorted(os.listdir(EXAMPLES)):
            if not (fn.startswith("demo_") and fn.endswith(".impor")):
                continue
            with open(os.path.join(EXAMPLES, fn), encoding="utf-8") as fh:
                src = fh.read()
            try:
                expected = run_interp(src, fn)
            except Exception:
                continue    # 解释器自己都不支持的示例（GUI / 交互）不参与对拍
            try:
                actual, _module, _ev = run_ir(src, fn)
            except IRError as e:
                rejected.append((fn, str(e)))
                continue
            if expected == actual:
                covered.append(fn)
            else:
                mismatched.append((fn, expected[:5], actual[:5]))
        detail = "覆盖 %d 个 / 拒绝 %d 个 / 不一致 %d 个" % (
            len(covered), len(rejected), len(mismatched))
        self.assertEqual(mismatched, [], detail)
        self.assertGreaterEqual(len(covered), 25,
                                "示例覆盖面缩水了：%s\n%s" % (detail, covered))
        # 拒绝必须是显式 IR 错误（带位置），不能是崩溃或静默跳过
        for fn, msg in rejected:
            self.assertTrue(":" in msg, "%s 的拒绝信息缺位置：%s" % (fn, msg))


class TestRejections(unittest.TestCase):
    def test_class_rejected_with_position(self):
        src = 'var a = 1\nclass Box\n    var v = 1\n'
        with self.assertRaises(UnsupportedNode) as ctx:
            run_ir(src, "box.impor")
        err = ctx.exception
        self.assertIn("ClassDef", str(err))
        self.assertEqual(err.lineno, 2)
        self.assertEqual(err.filename, "box.impor")

    def test_nested_def_rejected(self):
        src = 'def outer()\n    def inner()\n        return 1\n    return inner()\nprint(outer())\n'
        with self.assertRaises(UnsupportedNode) as ctx:
            run_ir(src)
        self.assertIn("嵌套 def", str(ctx.exception))

    def test_import_rejected(self):
        with self.assertRaises(UnsupportedNode):
            run_ir('import math\nprint(1)\n')


class TestCLI(unittest.TestCase):
    def _run_cli(self, args):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, cwd=ROOT, timeout=120)

    def test_dump_run_and_stats(self):
        src_path = os.path.join(EXAMPLES, "demo_02_arithmetic.impor")
        if not os.path.isfile(src_path):
            self.skipTest("缺少 demo_02_arithmetic.impor")
        tmp = tempfile.mkdtemp()
        ir_path = os.path.join(tmp, "demo02.ir")
        r = self._run_cli(["ir", src_path, "--run", "--stats", "-o", ir_path])
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(ir_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("func __main__", text)
        self.assertIn("== IR 统计 ==", r.stdout)
        code_out = [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("IR 已写入")]
        code_out = [ln for ln in code_out if not ln.startswith("函数 ")
                    and not ln.startswith("== IR")]
        with open(src_path, encoding="utf-8") as fh:
            expected = run_interp(fh.read(), os.path.basename(src_path))
        self.assertEqual(expected, code_out)

    def test_cli_rejects_unsupported_with_exit_code(self):
        src_path = os.path.join(EXAMPLES, "demo_23_stack.impor")
        if not os.path.isfile(src_path):
            self.skipTest("缺少 demo_23_stack.impor")
        r = self._run_cli(["ir", src_path])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("IR 暂不支持", r.stderr)


if __name__ == "__main__":
    unittest.main()
