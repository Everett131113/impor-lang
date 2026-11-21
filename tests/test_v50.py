# -*- coding: utf-8 -*-
"""v3.9.0 回归：fmt 真换行 / lint 配置与自动修复 / 标准库扩展。

① fmt --max-line：括号内换行、语义不变、幂等、方括号前空格保留
② lint：enum 穷尽性提示、--fix 删除未使用导入并给变量加 _ 前缀、阈值可配置
③ std：examples/std_v39_demo.impor 解释器与 Python 转译输出一致
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
IMPOR_PY = os.path.join(ROOT, "Impor.py")
PY = sys.executable

from impor.analysis import analyze, readability_lint
from impor.cli import cmd_lint
from impor.fmt import fmt_source
from impor.interpreter import Interpreter, parse_source, strip_header


def _write(dirpath, name, text):
    p = os.path.join(dirpath, name)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


def _run_cli(args, timeout=120):
    r = subprocess.run([PY, "-X", "utf8", IMPOR_PY] + args, cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


class TestFmtWrap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v39_", dir=ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wrap_idempotent_and_semantics(self):
        src = (
            "def calc(a, b)\n"
            "    return a + b\n"
            "var arr = [1111111111, 2222222222, 3333333333, 4444444444]\n"
            "print(calc(arr[0], arr[1]))\n"
        )
        one = fmt_source(src, max_line_length=40)
        two = fmt_source(one, max_line_length=40)
        self.assertEqual(one, two)
        self.assertIn("var arr = [", one)
        self.assertIn("\n    1111111111,\n", one)

        def run(text):
            out = []
            Interpreter(log_cb=out.append, filename="v39fmt").run(
                parse_source(strip_header(text), filename="v39fmt"))
            return out
        self.assertEqual(run(src), run(one))

    def test_wrap_skips_comments_and_block_headers(self):
        src = 'print("x")  // 这一行很长但含注释，不拆\nif true {\n    print("ok")\n}\n'
        out = fmt_source(src, max_line_length=20)
        self.assertIn("这一行很长但含注释，不拆", out)
        self.assertIn("if true {", out)


class TestLintV39(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v39_", dir=ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enum_exhaustive(self):
        src = (
            "enum Color { RED = 10, GREEN = 20, BLUE = 30 }\n"
            "def pick(c)\n"
            "    match c\n"
            "        case Color.RED\n"
            "            return 1\n"
            "        case Color.GREEN\n"
            "            return 2\n"
            "print(pick(Color.BLUE))\n"
        )
        ast = parse_source(src, filename="enum")
        codes = [i.code for i in readability_lint(ast, "enum", {})]
        self.assertIn("enum-exhaustive", codes)

    def test_lint_config_threshold(self):
        src = (
            "def f(x)\n"
            "    var a = 1 + 1\n"
            "    var b = 2 + 2\n"
            "    var c = 3 + 3\n"
            "    return x\n"
            "print(f(1))\n"
        )
        ast = parse_source(src, filename="cfg")
        default_codes = [i.code for i in readability_lint(ast, "cfg", {})]
        tight_codes = [i.code for i in readability_lint(
            ast, "cfg", {"max_function_statements": 2})]
        self.assertNotIn("style-long-function", default_codes)
        self.assertIn("style-long-function", tight_codes)

    def test_lint_fix(self):
        p = _write(self.tmp, "fix.impor",
                   "import std.text\n"
                   "def f(a)\n"
                   "    var unused = 1\n"
                   "    return a\n"
                   "print(f(2))\n")
        rc = cmd_lint([p, "--fix"])
        self.assertEqual(rc, 0)
        with io.open(p, encoding="utf-8") as f:
            fixed = f.read()
        self.assertNotIn("import std.text", fixed)
        self.assertIn("_unused", fixed)


class TestStdV39(unittest.TestCase):
    def test_use_std_injects_four_modules(self):
        src = (
            "use std\n"
            "print(algo.sorted([3, 1, 2]))\n"
            "print(text.pad_left(\"7\", 3, \"0\"))\n"
            "print(mathx.gcd(12, 18))\n"
        )
        out = []
        Interpreter(log_cb=out.append, filename="v39use").run(
            parse_source(strip_header(src), filename="v39use"))
        self.assertEqual(out, ["[1, 2, 3]", "007", "6"])

    def test_std_demo_interpreter_and_transpile(self):
        demo = os.path.join(ROOT, "examples", "std_v39_demo.impor")
        rc1, out1, err1 = _run_cli(["run", demo], timeout=90)
        self.assertEqual(rc1, 0, err1)
        self.assertEqual(len([l for l in out1.splitlines() if l.strip()]), 15)

        tmp_py = os.path.join(ROOT, "_v39_std_demo.py")
        try:
            rc2, _out2, err2 = _run_cli(["compile", demo, "-o", tmp_py], timeout=90)
            self.assertEqual(rc2, 0, err2)
            r = subprocess.run([PY, "-X", "utf8", tmp_py], cwd=ROOT,
                               capture_output=True, text=True, timeout=90)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            self.assertEqual(lines, [l for l in out1.splitlines() if l.strip()])
        finally:
            try:
                os.unlink(tmp_py)
            except OSError:
                pass


class TestLspFormatting(unittest.TestCase):
    def test_document_formatting_provider(self):
        from impor.lsp import LanguageServer
        srv = LanguageServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        uri = "file:///C:/Users/41482/Doubao/chats/2026-11-01/new-chat/ImporLang/examples/lsp_fmt.impor"
        srv.docs[uri] = "def f(a,b)\n    var x=1+2\n    return x\n"
        got = {}
        srv._reply = lambda mid, result: got.update({"id": mid, "result": result})
        srv._on_formatting(1, {"textDocument": {"uri": uri},
                               "options": {"tabSize": 4}})
        self.assertEqual(got["id"], 1)
        self.assertEqual(len(got["result"]), 1)
        self.assertIn("def f(a, b)", got["result"][0]["newText"])
        self.assertIn("var x = 1 + 2", got["result"][0]["newText"])

    def test_capabilities_include_formatting(self):
        from impor.lsp import LanguageServer
        srv = LanguageServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        got = {}
        srv._reply = lambda mid, result: got.update(result)
        srv._on_initialize(1, {})
        caps = got["capabilities"]
        self.assertTrue(caps.get("documentFormattingProvider"))
        self.assertTrue(caps.get("documentRangeFormattingProvider"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
