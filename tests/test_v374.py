# -*- coding: utf-8 -*-
"""v3.7.4 回归：DAP 条件/命中/日志/异常断点、LSP 六项能力、统一符号表、命令面板。"""

import io
import json
import os
import unittest

from impor.dap import DAPServer
from impor.interpreter import ImporException, Interpreter
from impor.lexer import tokenize
from impor.parser import Parser
from impor.lsp import LanguageServer
from impor.editor import semantic_symbol_map
from impor.editor_dialogs import CommandPalette


def _frames(buf):
    raw = buf.getvalue()
    out = []
    while b"\r\n\r\n" in raw:
        head, _, rest = raw.partition(b"\r\n\r\n")
        try:
            length = int(head.decode("ascii").split(":", 1)[1].strip())
        except Exception:
            break
        body, raw = rest[:length], rest[length:]
        out.append(json.loads(body.decode("utf-8")))
    return out


def _run(src, setup=None):
    it = Interpreter(debug_mode=True, filename="test_v374.impor")
    if setup:
        setup(it)
    it.run(Parser(tokenize(src)).program())
    return it


class TestDAPBreakpoints(unittest.TestCase):
    def test_initialize_advertises_conditional_and_exception(self):
        dap = DAPServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        dap._cmd_initialize({"seq": 1, "command": "initialize"})
        reply = _frames(dap.stdout)[0]
        caps = reply["body"]
        self.assertTrue(caps["supportsConditionalBreakpoints"])
        self.assertTrue(caps["supportsHitConditionalBreakpoints"])
        self.assertTrue(caps["supportsLogPoints"])
        self.assertEqual([f["filter"] for f in caps["exceptionBreakpointFilters"]],
                         ["uncaught", "all"])

    def test_set_breakpoints_keeps_condition_hit_and_log(self):
        dap = DAPServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        dap.program = "C:/tmp/a.impor"
        dap.head_skip = 1
        dap._cmd_setBreakpoints({
            "seq": 2, "command": "setBreakpoints",
            "arguments": {"source": {"path": dap.program}, "breakpoints": [
                {"line": 4, "condition": "i > 2", "hitCondition": ">=3",
                 "logMessage": "i={i}"}]},
        })
        specs = dap.bp_specs[os.path.abspath(dap.program)]
        self.assertEqual(specs[0]["line"], 3)
        self.assertEqual(specs[0]["condition"], "i > 2")
        self.assertEqual(specs[0]["hitCondition"], ">=3")
        self.assertEqual(specs[0]["logMessage"], "i={i}")
        self.assertIn("日志点", _frames(dap.stdout)[0]["body"]["breakpoints"][0]["message"])

    def test_set_exception_breakpoints(self):
        dap = DAPServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        dap._cmd_setExceptionBreakpoints({
            "seq": 3, "command": "setExceptionBreakpoints",
            "arguments": {"filters": ["all"]}})
        self.assertEqual(dap.exception_mode, "all")


class TestInterpreterBreakpoints(unittest.TestCase):
    SRC = "var i = 0\nwhile i < 8:\n    i = i + 1\n"

    def test_condition_and_hit_condition(self):
        seen = []

        def setup(it):
            it.breakpoints = {3}
            it.breakpoint_conditions = {3: "i >= 2"}
            it.breakpoint_hit_conditions = {3: "%2"}
            it.debug_pause_callback = lambda ln, env: seen.append((ln, env.get("i")))

        _run(self.SRC, setup)
        self.assertEqual(seen, [(3, 3), (3, 5), (3, 7)])

    def test_bare_hit_number_means_every_nth(self):
        seen = []

        def setup(it):
            it.breakpoints = {3}
            it.breakpoint_hit_conditions = {3: "3"}
            it.debug_pause_callback = lambda ln, env: seen.append(env.get("i"))

        _run(self.SRC, setup)
        self.assertEqual(seen, [2, 5])

    def test_exception_all_hook_fires_once_at_raise(self):
        seen = []

        def setup(it):
            it.exception_break_mode = "all"
            it.exception_pause_callback = lambda exc, ln: seen.append(
                (getattr(exc, "name", "?"), ln))

        with self.assertRaises(ImporException):
            _run('exception Bad\nraise Bad("x")\n', setup)
        self.assertEqual(seen, [("Bad", 2)])


class TestLSPCapabilities(unittest.TestCase):
    def _server(self, text):
        srv = LanguageServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        srv._update_doc("file:///C:/tmp/v374.impor", text)
        return srv

    def _call(self, srv, name, params):
        srv.stdout.seek(0)
        srv.stdout.truncate(0)
        getattr(srv, name)(1, {"textDocument": {"uri": "file:///C:/tmp/v374.impor"},
                               **params})
        return _frames(srv.stdout)[-1]["result"]

    def test_initialize_has_six_new_capabilities(self):
        srv = LanguageServer(stdin=io.BytesIO(), stdout=io.BytesIO())
        srv._on_initialize(1, {})
        caps = _frames(srv.stdout)[0]["result"]["capabilities"]
        for key in ("semanticTokensProvider", "foldingRangeProvider", "inlayHintProvider",
                    "codeLensProvider", "documentHighlightProvider"):
            self.assertIn(key, caps)
        self.assertTrue(caps["renameProvider"]["prepareProvider"])

    def test_semantic_tokens_are_delta_encoded(self):
        srv = self._server("def add(a, b):\n    var total = a + b\n    return total\n")
        data = self._call(srv, "_on_semantic_tokens", {})["data"]
        self.assertEqual(len(data) % 5, 0)
        lengths = data[2::5]
        self.assertTrue(all(n > 0 for n in lengths))
        # 至少一个声明修饰符
        self.assertTrue(any(mod for mod in data[4::5]))

    def test_folding_range_groups_block(self):
        srv = self._server("def f():\n    var a = 1\n    var b = 2\nvar c = 3\n")
        folds = self._call(srv, "_on_folding_range", {})
        self.assertTrue(any(f["startLine"] == 0 and f["endLine"] == 2 for f in folds))

    def test_inlay_hint_infers_literal_type(self):
        srv = self._server('var n = 1\nvar s = "x"\n')
        hints = self._call(srv, "_on_inlay_hint", {})
        self.assertEqual([h["label"] for h in hints], [": int", ": string"])

    def test_code_lens_reports_reference_count(self):
        srv = self._server("def add(a, b):\n    return a + b\nadd(1, 2)\n")
        lenses = self._call(srv, "_on_code_lens", {})
        self.assertTrue(lenses)
        self.assertEqual(lenses[0]["command"]["command"], "impor.showReferences")
        self.assertIn("处引用", lenses[0]["command"]["title"])

    def test_document_highlight_returns_occurrences(self):
        srv = self._server("var total = 1\nprint(total)\n")
        hits = self._call(srv, "_on_document_highlight",
                          {"position": {"line": 0, "character": 5}})
        self.assertEqual(len(hits), 2)

    def test_prepare_rename_returns_placeholder(self):
        srv = self._server("var total = 1\nprint(total)\n")
        prep = self._call(srv, "_on_prepare_rename",
                          {"position": {"line": 0, "character": 5}})
        self.assertEqual(prep["placeholder"], "total")
        self.assertEqual(prep["range"]["start"]["character"], 4)


class TestUnifiedSemanticMap(unittest.TestCase):
    def test_analyzer_keeps_v323_expectations(self):
        src = ('import math as m\ndef add(a, b):\n    var total = a + b\n'
               '    return total\nclass Box:\n    var value = 1\nexception Bad\n'
               'struct Point\nfor item in [1, 2]:\n    print(item)\n')
        table = semantic_symbol_map(src)
        self.assertEqual(table["add"][0], "func")
        self.assertEqual(table["a"][0], "param")
        self.assertEqual(table["total"][0], "var")
        self.assertEqual(table["Box"][0], "class")
        self.assertEqual(table["Bad"][0], "class")
        self.assertEqual(table["Point"][0], "struct")
        self.assertEqual(table["m"][0], "module")
        self.assertEqual(table["item"][0], "var")


class TestCommandPalette(unittest.TestCase):
    def _palette(self, commands):
        p = CommandPalette.__new__(CommandPalette)
        p.commands = commands
        p._conflicts = {}
        p._recent = []
        p._context = {"保存"}
        for c in commands:
            if c[1]:
                p._conflicts.setdefault(c[1], []).append(c[0])
        return p

    def test_category_grouping(self):
        p = self._palette([])
        self.assertEqual(p._category("Git 面板"), "Git")
        self.assertEqual(p._category("运行当前脚本"), "运行/调试")
        self.assertEqual(p._category("查找引用"), "导航/符号")
        self.assertEqual(p._category("保存"), "文件/项目")

    def test_conflict_note(self):
        p = self._palette([("打开终端", "Ctrl+`", lambda: None),
                           ("多终端标签", "Ctrl+`", lambda: None)])
        note = p._conflict_note("打开终端", "Ctrl+`")
        self.assertIn("冲突", note)
        self.assertIn("多终端标签", note)


if __name__ == "__main__":
    unittest.main()
