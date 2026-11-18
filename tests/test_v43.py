# -*- coding: utf-8 -*-
"""v3.5 回归：语言宪法落地（文档注释 `##` + 推荐写法检查）。

覆盖：
  - 词法：`##` 生成 DOC token；普通 `#` 仍是注释
  - 解析/运行：文档注释不影响解释器执行，可挂在函数/类/方法上
  - 符号表：Symbol.doc 取到文档；LSP hover 带出文档
  - impor doc：生成 Markdown API 文档（含无文档时的提示）
  - impor lint --style：识别 v3.4 兼容写法并给唯一推荐写法；推荐写法零提示
  - C 后端：带文档注释仍能 build 成原生 exe 并正确运行
  - fmt：格式化不丢文档注释
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")

from impor.lexer import tokenize, TOK_DOC  # noqa: E402
from impor.parser import Parser  # noqa: E402
from impor.analysis import analyze, style_lint  # noqa: E402
from impor.lsp import _hover_md  # noqa: E402


def _write(text):
    d = tempfile.mkdtemp(prefix="v35_")
    p = os.path.join(d, "t.impor")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return d, p


def run(src, build=False, argv=None):
    d, p = _write(src)
    try:
        cmd = [PY, IMPOR] + (["build", p] if build else ["run", p])
        if build:
            cmd += ["-o", os.path.join(d, "t.exe")]
        elif argv:
            cmd = [PY, IMPOR] + list(argv[:1]) + [p] + list(argv[1:])
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=ROOT)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


DOC_SRC = '''## 两数相加
## 返回 a + b
func add(a, b) {
    return a + b
}

## 盒子
class Box {
    ## 构造函数
    func __init__(v) {
        self.v = v
    }

    ## 读取内部值
    func get() {
        return self.v
    }
}

print(add(2, 3))
var b = new Box(9)
print(b.get())
'''


class TestDocLexer(unittest.TestCase):
    def test_doc_token_emitted(self):
        toks = tokenize("## 第一行\n## 第二行\nprint(1)\n")
        docs = [t.value for t in toks if t.type == TOK_DOC]
        self.assertEqual(docs, ["第一行", "第二行"])

    def test_plain_comment_not_doc(self):
        toks = tokenize("# 普通注释\nprint(1)\n")
        self.assertEqual([t.value for t in toks if t.type == TOK_DOC], [])

    def test_doc_keeps_code_parsable(self):
        ast = Parser(tokenize(DOC_SRC)).program()
        self.assertEqual(ast.stmts[0].name, "add")
        self.assertEqual(ast.stmts[0].doc, "两数相加\n返回 a + b")


class TestDocRuntime(unittest.TestCase):
    def test_run_with_docs(self):
        rc, out, err = run(DOC_SRC)
        self.assertEqual(rc, 0, f"rc={rc}\n{err}\n{out}")
        self.assertIn("5", out)
        self.assertIn("9", out)

    def test_doc_inside_brace_block(self):
        rc, out, err = run('## 说明\nif 1 > 0 {\n    ## 块内说明\n    print("ok")\n}\n')
        self.assertEqual(rc, 0, f"rc={rc}\n{err}")
        self.assertIn("ok", out)

    def test_c_backend_with_docs(self):
        d, p = _write(DOC_SRC)
        try:
            exe = os.path.join(d, "t.exe")
            r = subprocess.run([PY, IMPOR, "build", p, "-o", exe],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, cwd=ROOT)
            self.assertEqual(r.returncode, 0, f"build rc={r.returncode}\n{r.stderr}\n{r.stdout}")
            rr = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120, cwd=ROOT)
            out = (rr.stdout or "").replace("\r\n", "\n")
            self.assertEqual(rr.returncode, 0, f"exe rc={rr.returncode}\n{rr.stderr}\n{out}")
            self.assertIn("5", out)
            self.assertIn("9", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestDocSymbolsAndHover(unittest.TestCase):
    def test_symbol_doc(self):
        ast = Parser(tokenize(DOC_SRC)).program()
        an = analyze(ast, DOC_SRC.split("\n"))
        self.assertEqual(an.symbols["add"][0].doc, "两数相加\n返回 a + b")
        self.assertEqual(an.symbols["Box"][0].doc, "盒子")
        self.assertEqual(an.symbols["get"][0].doc, "读取内部值")

    def test_hover_contains_doc(self):
        ast = Parser(tokenize(DOC_SRC)).program()
        an = analyze(ast, DOC_SRC.split("\n"))
        md = _hover_md(an.symbols["add"][0], DOC_SRC.split("\n"))
        self.assertIn("两数相加", md)
        self.assertIn("> ", md)

    def test_no_doc_symbol_untouched(self):
        src = 'func f(a) {\n    return a\n}\n'
        ast = Parser(tokenize(src)).program()
        an = analyze(ast, src.split("\n"))
        self.assertIsNone(an.symbols["f"][0].doc)


class TestDocCommand(unittest.TestCase):
    def test_doc_markdown(self):
        d, p = _write(DOC_SRC)
        try:
            r = subprocess.run([PY, IMPOR, "doc", p],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = (r.stdout or "").replace("\r\n", "\n")
            self.assertIn("## `add(a, b)`", out)
            self.assertIn("两数相加", out)
            self.assertIn("### `get()`", out)
            self.assertIn("读取内部值", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_doc_without_comments(self):
        rc, out, err = run('print(1)\n', argv=["doc"])
        self.assertEqual(rc, 0, err)
        self.assertIn("没有", out)


class TestStyleLint(unittest.TestCase):
    def _codes(self, src):
        return [i.code for i in style_lint(src)]

    def test_canonical_code_is_clean(self):
        src = ('func add(a, b) {\n'
               '    return a + b\n'
               '}\n'
               'var x = 1\n'
               'if x > 0 { print(x) }\n'
               'for i in range(0, 3) { print(i) }\n'
               'print(add(1, 2))\n')
        self.assertEqual(self._codes(src), [])

    def test_detects_main_sugars(self):
        src = ('var x = 0; x := 1\n'
               'if x > 0 then { print "p" }\n'
               'unless x < 0 { print("n") }\n'
               'for each it of [1, 2] { print(it) }\n'
               'for i in 1..3 { print(i) }\n'
               'var y = 1\n'
               'y++\n'
               'var z = y && x || y <> 2\n'
               'do { x = x + 1 } until x > 5\n'
               'var b = new B\n')
        codes = self._codes(src)
        for code in ("style-semicolon", "style-walrus", "style-then", "style-print",
                     "style-unless", "style-foreach", "style-forof", "style-range",
                     "style-incdec", "style-operator", "style-dountil", "style-new"):
            self.assertIn(code, codes, f"{code} 未报告；实际 {codes}")

    def test_detects_semicolon_extends_params_catch(self):
        src = ('print("hi"); print("x")\n'
               'class B extends A { }\n'
               'func f a, b {\n    return a\n}\n'
               'try { print(1) } catch { print("e") }\n')
        codes = self._codes(src)
        for code in ("style-semicolon", "style-extends", "style-params", "style-catch"):
            self.assertIn(code, codes, f"{code} 未报告；实际 {codes}")

    def test_c_for_reported_once_without_semicolon_noise(self):
        src = 'for i = 0; i < 3; i++ { print(i) }\n'
        codes = self._codes(src)
        self.assertEqual(codes.count("style-cfor"), 1)
        self.assertNotIn("style-semicolon", codes)
        self.assertNotIn("style-incdec", codes)

    def test_cli_style_only_does_not_fail_build(self):
        d, p = _write('print("x"); print("y")\n')
        try:
            r = subprocess.run([PY, IMPOR, "lint", "--style", p],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("不推荐写法", r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cli_without_style_is_unchanged(self):
        d, p = _write('print("x"); print("y")\n')
        try:
            r = subprocess.run([PY, IMPOR, "lint", p],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("不推荐写法", r.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestFmtKeepsDocs(unittest.TestCase):
    def test_fmt_preserves_doc_lines(self):
        src = '## 说明一\n## 说明二\nfunc f(a)\n    return a\n'
        d, p = _write(src)
        try:
            r = subprocess.run([PY, IMPOR, "fmt", p],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            with io.open(p, encoding="utf-8") as f:
                out = f.read()
            self.assertIn("## 说明一", out)
            self.assertIn("## 说明二", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestKeywordCountUnchanged(unittest.TestCase):
    def test_keyword_total_v52(self):
        from impor.lexer import KEYWORDS
        self.assertEqual(len(KEYWORDS), 58)  # v3.8.4 起含 yield / with
        self.assertFalse(any(any("\u4e00" <= ch <= "\u9fff" for ch in w) for w in KEYWORDS))


if __name__ == "__main__":
    unittest.main()
