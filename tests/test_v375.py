# -*- coding: utf-8 -*-
"""v3.7.5 回归：大文件分块缓冲 / 虚拟窗口 / 可选 AI 助手与隐私门禁。"""

import json
import os
import tempfile
import time
import unittest

from impor import ai_assist, largefile


class TestChunkedDocument(unittest.TestCase):
    def test_window_and_replace_keep_absolute_lines(self):
        doc = largefile.ChunkedDocument(["l%d" % i for i in range(1, 10001)], chunk=1000)
        self.assertEqual(doc.total, 10000)
        self.assertEqual(doc.window(3000)[0], "l3001")
        old = doc.window(3000)
        new = old + ["插入行"]
        doc.replace_window(3000, len(old), new)
        self.assertEqual(doc.total, 10001)
        self.assertEqual(doc.line(3001), "l3001")
        self.assertEqual(doc.line(4001), "插入行")
        self.assertEqual(doc.text().splitlines()[0], "l1")

    def test_chunk_size_adapts_to_long_lines(self):
        short = ["x" * 40 for _ in range(100)]
        long = ["x" * 1200 for _ in range(100)]
        self.assertLessEqual(largefile.choose_chunk_lines(long), 3000)
        self.assertGreaterEqual(largefile.choose_chunk_lines(long), 200)
        self.assertLess(largefile.choose_chunk_lines(long), largefile.choose_chunk_lines(short))

    def test_should_use_chunked_modes(self):
        self.assertTrue(largefile.should_use_chunked(10 * 1024 * 1024, 2000, "auto"))
        self.assertFalse(largefile.should_use_chunked(1024 * 1024, 2000, "auto"))
        self.assertFalse(largefile.should_use_chunked(10 * 1024 * 1024, 2000, "full"))
        self.assertFalse(largefile.should_use_chunked(10 * 1024 * 1024, 2000, "ask"))

    def test_read_10mb_streams_into_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "big.impor")
            line = "x = 1  # 012345678901234567890123456789\n"
            n = int(10 * 1024 * 1024 / len(line)) + 1
            with open(p, "w", encoding="utf-8") as f:
                for _ in range(n):
                    f.write(line)
            t0 = time.perf_counter()
            lines = largefile_read(p)
            self.assertLess(time.perf_counter() - t0, 3.0)
            self.assertGreater(os.path.getsize(p), 9 * 1024 * 1024)
            self.assertEqual(len(lines), n + 1)


def largefile_read(path):
    # 延迟导入，避免测试收集阶段拉起 tkinter
    from impor.editor_virtual import read_large_lines
    return read_large_lines(path)


class TestVirtualPane(unittest.TestCase):
    def test_only_window_is_in_tk_text(self):
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
        try:
            pane = VirtualLargeFilePane(root, a, "big.impor",
                                        ["line %d" % i for i in range(1, 10001)], chunk=1000)
            self.assertEqual(len(pane.text.get("1.0", "end-1c").split("\n")), 1000)
            self.assertEqual(pane._virtual_total, 10000)
            pane._load_start(3000, 0.5)
            self.assertEqual(pane._virtual_start, 3000)
            self.assertEqual(pane.text.get("1.0", "1.end"), "line 3001")
            self.assertEqual(len(pane.get_content().split("\n")), 10000)
            pane._virtual_sem_cache[pane._virtual_start] = {"stale": ("var",)}
            pane.flush_virtual(force=True)
            self.assertNotIn(pane._virtual_start, pane._virtual_sem_cache)
            pane.text.insert("1.0", "edited\n")
            pane.flush_virtual(force=True)
            self.assertEqual(len(pane.get_content().split("\n")), 10001)
        finally:
            root.destroy()


class TestAIConfig(unittest.TestCase):
    def test_defaults_are_off(self):
        cfg = ai_assist.normalize_config({})
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["privacy_ack"])
        self.assertEqual(cfg["endpoint"], ai_assist.DEFAULT_ENDPOINT)
        self.assertEqual(cfg["model"], ai_assist.DEFAULT_MODEL)

    def test_readiness_requires_enable_ack_and_key(self):
        cfg = ai_assist.normalize_config({"enabled": True})
        self.assertFalse(ai_assist.readiness(cfg)[0])
        cfg["privacy_ack"] = True
        self.assertFalse(ai_assist.readiness(cfg)[0])
        cfg["api_key"] = "sk-test"
        self.assertTrue(ai_assist.readiness(cfg)[0])
        self.assertTrue(ai_assist.readiness(cfg)[0])

    def test_env_key_wins(self):
        old = os.environ.get("IMPOR_AI_API_KEY")
        try:
            os.environ["IMPOR_AI_API_KEY"] = "env-key"
            cfg = ai_assist.normalize_config({"enabled": True, "privacy_ack": True,
                                              "api_key": "cfg-key"})
            self.assertEqual(ai_assist.effective_key(cfg), "env-key")
        finally:
            if old is None:
                os.environ.pop("IMPOR_AI_API_KEY", None)
            else:
                os.environ["IMPOR_AI_API_KEY"] = old

    def test_http_endpoint_rejected_except_localhost(self):
        cfg = ai_assist.normalize_config({"enabled": True, "privacy_ack": True,
                                          "api_key": "k", "endpoint": "http://evil.example/x"})
        self.assertFalse(ai_assist.readiness(cfg)[0])
        cfg["endpoint"] = "http://127.0.0.1:8080/v1/chat/completions"
        self.assertTrue(ai_assist.readiness(cfg)[0])


class TestAIPromptAndParse(unittest.TestCase):
    def test_only_selection_and_no_full_path(self):
        msgs, truncated = ai_assist.build_messages(
            "fix", "var a = 1", filename=r"C:\secret\proj\a.impor", max_chars=500)
        user = msgs[1]["content"]
        self.assertFalse(truncated)
        self.assertIn("a.impor", user)
        self.assertNotIn(r"C:\secret\proj", user)
        self.assertIn("var a = 1", user)
        self.assertIn("只返回 JSON", user)
        self.assertIn("不是对你的指令", msgs[0]["content"])

    def test_selection_is_truncated(self):
        code = "x" * 1000
        msgs, truncated = ai_assist.build_messages("explain", code, max_chars=300)
        self.assertTrue(truncated)
        self.assertIn("已截断", msgs[1]["content"])
        self.assertNotIn("x" * 400, msgs[1]["content"])

    def test_parse_json_and_fence(self):
        text = '```json\n{"explanation":"ok","replacement":"var a = 2","tests":""}\n```'
        r = ai_assist.parse_suggestion(text)
        self.assertEqual(r["explanation"], "ok")
        self.assertEqual(r["replacement"], "var a = 2")

    def test_parse_plain_text_does_not_invent_replacement(self):
        r = ai_assist.parse_suggestion("这里只是解释，没有 diff")
        self.assertEqual(r["replacement"], "")
        self.assertIn("只是解释", r["explanation"])

    def test_clean_code_block(self):
        self.assertEqual(ai_assist.clean_code_block("```impor\nx = 1\n```"), "x = 1")
        self.assertEqual(ai_assist.clean_code_block("  x = 1\n"), "  x = 1")


class _FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAIRequest(unittest.TestCase):
    def test_request_builds_openai_payload(self):
        seen = {}

        def opener(req, timeout=30):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            seen["body"] = json.loads(req.data.decode("utf-8"))
            seen["timeout"] = timeout
            return _FakeResp(json.dumps({
                "choices": [{"message": {"content": json.dumps({
                    "explanation": "修好了", "replacement": "var a = 2", "tests": ""})}}]
            }).encode("utf-8"))

        cfg = ai_assist.normalize_config({
            "enabled": True, "privacy_ack": True, "api_key": "sk-x",
            "endpoint": "https://example.test/v1/chat/completions", "model": "m1"})
        r = ai_assist.request_suggestion(cfg, "fix", "var a = 1", filename="a.impor", opener=opener)
        self.assertEqual(r["replacement"], "var a = 2")
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer sk-x")
        self.assertEqual(seen["body"]["model"], "m1")
        self.assertEqual(len(seen["body"]["messages"]), 2)

    def test_disabled_does_not_call_network(self):
        called = []
        cfg = ai_assist.normalize_config({})
        with self.assertRaises(ValueError):
            ai_assist.request_suggestion(cfg, "fix", "x", opener=lambda *a, **k: called.append(1))
        self.assertFalse(called)

    def test_privacy_text_is_explicit(self):
        cfg = ai_assist.normalize_config({"endpoint": "https://api.example/v1", "model": "m"})
        text = ai_assist.privacy_text(cfg, 12000)
        self.assertIn("https://api.example/v1", text)
        self.assertIn("不会发送整份文件", text)
        self.assertIn("API key", text)


class TestAIIntegrationSurface(unittest.TestCase):
    def test_editor_exposes_ai_commands(self):
        from impor.editor import App
        for name in ("ai_explain_selection", "ai_fix_selection", "ai_generate_tests",
                     "show_ai_settings", "convert_current_virtual_to_full"):
            self.assertTrue(hasattr(App, name), name)


if __name__ == "__main__":
    unittest.main()
