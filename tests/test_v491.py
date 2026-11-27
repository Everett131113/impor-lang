# -*- coding: utf-8 -*-
"""4.9.1 回归：`have` 关键字 + WASM 后端的 list / dict / class。

三处断言：
  1. 解释器：have 的语义（bool 独立、类名、object）
  2. C 后端：同一份源码编译出的 exe 与解释器逐行一致
  3. WASM 后端：wasm_transpile 不再拒绝 04/05/06，且产物字节数 > 0
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HAVE_SRC = """\
var a = 3
if a have int:
    print(a)
print(a have float)
print("s" have str)
var c = [1, 2]
print(c have list)
var d = {"k": 1}
print(d have dict)
class P
    def __init__(x)
        set self.x = x
    def get()
        return self.x
var p = new P(7)
print(p have P, p.get(), p have object)
"""

EXPECT = ["3", "False", "True", "True", "True", "True 7 True"]


def _write(dirpath, name, text):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestHaveInterpreter(unittest.TestCase):
    def test_have_semantics(self):
        from impor.interpreter import Interpreter, parse_source
        out = []
        i = Interpreter()
        i.log_cb = out.append
        i.run(parse_source(HAVE_SRC))
        self.assertEqual(out, EXPECT)

    def test_have_is_soft_keyword(self):
        """`have` 不进 KEYWORDS：它必须能当普通标识符用。"""
        from impor.interpreter import Interpreter, parse_source
        out = []
        i = Interpreter()
        i.log_cb = out.append
        i.run(parse_source("var have = 1\nprint(have)\n"))
        self.assertEqual(out, ["1"])


class TestHaveCBackend(unittest.TestCase):
    def test_c_backend_matches(self):
        tmp = tempfile.mkdtemp(prefix="impor_have_c_")
        try:
            src = _write(tmp, "have.impor", HAVE_SRC)
            exe = os.path.join(tmp, "have.exe")
            from impor.cli import main
            self.assertEqual(main(["build", src, "-o", exe, "--quiet"]), 0)
            r = subprocess.run([exe], capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = [l.rstrip() for l in r.stdout.splitlines() if l.strip()]
            self.assertEqual(got, EXPECT)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestWasmContainers(unittest.TestCase):
    def _transpile(self, name):
        from impor.interpreter import parse_source, strip_header
        from impor.wasm import wasm_transpile
        path = os.path.join(ROOT, "bench", "caps", name)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        data, meta = wasm_transpile(parse_source(strip_header(src), name))
        self.assertGreater(len(data), 512)
        return meta

    def test_list(self):
        meta = self._transpile("04_list.impor")
        self.assertGreaterEqual(meta["funcs"], 12)

    def test_dict(self):
        self._transpile("05_dict.impor")

    def test_class(self):
        meta = self._transpile("06_class.impor")
        self.assertGreaterEqual(meta["funcs"], 14)   # 11 helper + 2 方法 + main

    def test_have(self):
        self._transpile("11_have.impor")

    def test_inheritance_still_refused(self):
        from impor.interpreter import parse_source, strip_header
        from impor.wasm import WasmUnsupported, wasm_transpile
        src = "class A\n    def f()\n        return 1\nclass B(A)\n    def f()\n        return 2\n"
        with self.assertRaises(WasmUnsupported):
            wasm_transpile(parse_source(strip_header(src), "t.impor"))


class TestHaveHighlight(unittest.TestCase):
    """4.9.2：`have` 必须有着色（Tk 编辑器 / Qt / LSP / VS Code 语法）。"""

    def test_highlight_keywords_set(self):
        from impor.lexer import KEYWORDS, SOFT_KEYWORDS, HIGHLIGHT_KEYWORDS
        self.assertIn("have", SOFT_KEYWORDS)
        self.assertNotIn("have", KEYWORDS)      # 软关键字，不封锁标识符
        self.assertIn("have", HIGHLIGHT_KEYWORDS)

    def test_pane_tags_have_as_keyword(self):
        try:
            import tkinter as tk
            import impor.editor  # noqa: F401  真实运行路径：注入分模块命名空间
            from impor.editor_pane import EditorPane
        except Exception as exc:
            self.skipTest("tkinter/editor unavailable: %s" % exc)
        try:
            root = tk.Tk()
            root.withdraw()
        except Exception as exc:
            self.skipTest("display unavailable: %s" % exc)

        class _Index:
            def symbols(self, *a, **k):
                return {}

            def semantic_positions(self, *a, **k):
                return {}

        class _App:
            pass

        a = _App()
        a.root = root
        a.mono = "Consolas"
        a.font_size = 11
        a.semantic_highlight = False
        a.inlay_hints = False
        a.show_minimap = False
        a.word_wrap = False
        a.theme = "dark"
        a.index = _Index()
        a.mark_dirty = lambda p: None
        a.convert_virtual_to_full = lambda p: None
        try:
            pane = EditorPane(root, a, path=None, content=HAVE_SRC)
            pane.highlight()
            text = pane.text
            idx = text.search("have", "1.0", stopindex="end")
            self.assertTrue(idx, "源码里应该有 have")
            self.assertIn("kw", text.tag_names(idx))
        finally:
            root.destroy()

    def test_vscode_grammar_matches_have(self):
        import json
        path = os.path.join(ROOT, "vscode-impor", "syntaxes", "impor.tmLanguage.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pats = data["repository"]["keywords"]["patterns"]
        self.assertTrue(any("have" in p.get("match", "") for p in pats))

    def test_lsp_semantic_uses_highlight_set(self):
        from impor import lsp
        self.assertIn("have", lsp.HIGHLIGHT_KEYWORDS)


if __name__ == "__main__":
    unittest.main()
