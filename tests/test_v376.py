# -*- coding: utf-8 -*-
"""v3.7.6 回归：大文件设置口径 / 跨块查找替换 / AI 流式与取消。"""

import json
import os
import threading
import unittest

from impor import ai_assist, largefile


class TestChunkedReplace(unittest.TestCase):
    def test_replace_all_across_lines_is_case_insensitive(self):
        doc = largefile.ChunkedDocument(
            ["alpha Needle", "beta", "NEEDLE gamma", "needle needle"], chunk=2)
        count = doc.replace_all("needle", "X")
        self.assertEqual(count, 4)
        self.assertEqual(doc.text(), "alpha X\nbeta\nX gamma\nX X")
        self.assertEqual(doc.total, 4)

    def test_replace_all_multiline_rebuilds_lines(self):
        doc = largefile.ChunkedDocument(["a\nb", "c"], chunk=1)
        count = doc.replace_all("a\nb", "x\ny\nz")
        self.assertEqual(count, 1)
        self.assertEqual(doc.text(), "x\ny\nz\nc")
        self.assertEqual(doc.total, 4)

    def test_replace_all_no_match_and_empty_needle(self):
        doc = largefile.ChunkedDocument(["a", "b"], chunk=1)
        self.assertEqual(doc.replace_all("zzz", "q"), 0)
        self.assertEqual(doc.replace_all("", "q"), 0)
        self.assertEqual(doc.text(), "a\nb")


class TestVirtualCrossChunk(unittest.TestCase):
    def _pane(self):
        try:
            import tkinter as tk
            from impor.editor_virtual import VirtualLargeFilePane
        except Exception as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        try:
            root = tk.Tk()
            root.withdraw()
        except Exception as exc:
            self.skipTest("display unavailable: %s" % exc)

        class I:
            def semantic_positions(self, *a):
                return {}

        class A:
            pass

        a = A()
        a.root = root
        a.mono = "Consolas"
        a.font_size = 11
        a.semantic_highlight = True
        a.inlay_hints = False
        a.show_minimap = False
        a.word_wrap = False
        a.index = I()
        a.mark_dirty = lambda p: None
        a.convert_virtual_to_full = lambda p: None
        lines = ["line %d" % i for i in range(1, 10001)]
        lines[0] = "head marker"
        lines[4999] = "target here"
        pane = VirtualLargeFilePane(root, a, "big.impor", lines, chunk=1000)
        self.addCleanup(root.destroy)
        return root, pane

    def test_find_next_jumps_outside_window(self):
        root, pane = self._pane()
        pane.text.mark_set("insert", "1.0")
        pane.find_next("target")
        self.assertEqual(pane._virtual_start, 4666)
        self.assertEqual(pane.text.index("insert").split(".")[0], "334")
        self.assertEqual(pane.text.get("sel.first", "sel.last"), "target")

    def test_find_next_wraps_to_file_head(self):
        root, pane = self._pane()
        pane._load_start(5000, 0.3)
        pane.text.mark_set("insert", "end-1c")
        pane.find_next("head marker")
        self.assertEqual(pane._virtual_start, 0)
        self.assertEqual(pane.text.get("sel.first", "sel.last"), "head marker")

    def test_replace_all_updates_full_document(self):
        root, pane = self._pane()
        count = pane.replace_all("line", "row", nocase=True)
        self.assertEqual(count, 9998)
        self.assertEqual(pane._virtual_total, 10000)
        self.assertEqual(pane._virtual_full_lines[4999], "target here")
        self.assertEqual(pane._virtual_full_lines[0], "head marker")
        self.assertIn("row 7", pane.text.get("1.0", "end-1c"))


class _FakeSSEResp:
    def __init__(self, lines, ctype="text/event-stream"):
        self.lines = lines
        self.headers = {"Content-Type": ctype}

    def __iter__(self):
        return iter(self.lines)

    def read(self):
        return b"".join(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


class TestAIStream(unittest.TestCase):
    def _cfg(self):
        return ai_assist.normalize_config({
            "enabled": True, "privacy_ack": True, "api_key": "sk-x",
            "endpoint": "https://example.test/v1/chat/completions", "model": "m1",
            "stream": True})

    def test_stream_assembles_sse_and_reports_deltas(self):
        final = json.dumps({"explanation": "流式好了", "replacement": "var a = 2", "tests": ""})
        lines = [
            _sse({"choices": [{"delta": {"content": final[:12]}}]}),
            _sse({"choices": [{"delta": {"content": final[12:]}}]}),
            b"data: [DONE]\n",
        ]
        seen = {}
        deltas = []

        def opener(req, timeout=30):
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResp(lines)

        r = ai_assist.request_suggestion_stream(
            self._cfg(), "fix", "var a = 1", opener=opener,
            on_delta=lambda t: deltas.append(t))
        self.assertTrue(seen["body"]["stream"])
        self.assertEqual("".join(deltas), final)
        self.assertEqual(r["explanation"], "流式好了")
        self.assertEqual(r["replacement"], "var a = 2")

    def test_non_sse_response_falls_back_to_json(self):
        payload = json.dumps({"choices": [{"message": {"content": json.dumps({
            "explanation": "整段", "replacement": "", "tests": ""})}}]
        }).encode("utf-8")

        def opener(req, timeout=30):
            return _FakeSSEResp([payload], ctype="application/json")

        deltas = []
        r = ai_assist.request_suggestion_stream(
            self._cfg(), "explain", "x", opener=opener,
            on_delta=lambda t: deltas.append(t))
        self.assertEqual(r["explanation"], "整段")
        self.assertEqual(len(deltas), 1)

    def test_cancel_before_request_does_not_call_network(self):
        called = []
        event = threading.Event()
        event.set()
        with self.assertRaises(ai_assist.AICancelled):
            ai_assist.request_suggestion_stream(
                self._cfg(), "explain", "x", cancel_event=event,
                opener=lambda *a, **k: called.append(1))
        self.assertFalse(called)

    def test_cancel_mid_stream_raises(self):
        event = threading.Event()
        lines = [
            _sse({"choices": [{"delta": {"content": "a"}}]}),
            _sse({"choices": [{"delta": {"content": "b"}}]}),
        ]

        def opener(req, timeout=30):
            return _FakeSSEResp(lines)

        def on_delta(text):
            event.set()

        with self.assertRaises(ai_assist.AICancelled):
            ai_assist.request_suggestion_stream(
                self._cfg(), "explain", "x", on_delta=on_delta, cancel_event=event,
                opener=opener)

    def test_string_error_does_not_crash(self):
        payload = json.dumps({"error": "boom", "choices": []}).encode("utf-8")

        def opener(req, timeout=30):
            return _FakeSSEResp([payload], ctype="application/json")

        with self.assertRaises(ValueError) as ctx:
            ai_assist.request_suggestion_stream(self._cfg(), "explain", "x", opener=opener)
        self.assertIn("boom", str(ctx.exception))


class TestEditorSurface(unittest.TestCase):
    def test_v376_methods_exist(self):
        from impor.editor import App
        for name in ("_find_replace", "_ai_stream_open", "_ai_stream_append",
                     "_ai_stream_cancel_done", "_ai_stream_finish"):
            self.assertTrue(hasattr(App, name), name)

    def test_ai_stream_default_is_on(self):
        self.assertTrue(ai_assist.normalize_config({})["stream"])


if __name__ == "__main__":
    unittest.main()
