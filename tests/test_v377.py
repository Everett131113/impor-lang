# -*- coding: utf-8 -*-
"""v3.7.7 回归：安全缓冲区替换 / 递减 for / AI token 预算。"""

import os
import subprocess
import sys
import tempfile
import unittest

from impor import ai_assist, editor_search


class TestDirtyBufferSearch(unittest.TestCase):
    def test_buffer_text_is_used_instead_of_disk(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.impor")
            with open(path, "w", encoding="utf-8") as f:
                f.write("old needle\n")
            opts = editor_search.SearchOptions("needle", suffixes=(".impor",))
            results = editor_search.search_project(
                root, opts, paths=[path], dirty_paths=[path],
                buffer_texts={path: "new needle\nunsaved\n"})
            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertTrue(r.dirty)
            self.assertEqual(r.before, "new needle\nunsaved\n")
            self.assertEqual(r.count, 1)

            editor_search.prepare_replacements(results, "X", opts)
            txn = editor_search.apply_results(root, results, os.path.join(root, ".backup"))
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "new X\nunsaved\n")
            self.assertTrue(os.path.isdir(txn))

    def test_disk_content_when_buffer_not_supplied(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "a.impor")
            with open(path, "w", encoding="utf-8") as f:
                f.write("disk needle\n")
            opts = editor_search.SearchOptions("needle", suffixes=(".impor",))
            results = editor_search.search_project(
                root, opts, paths=[path], dirty_paths=[path])
            self.assertEqual(results[0].before.splitlines(), ["disk needle"])


class TestDescendingFor(unittest.TestCase):
    def _run(self, source):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "main.impor")
            with open(path, "w", encoding="utf-8") as f:
                f.write(source)
            r = subprocess.run(
                [sys.executable, "-B", os.path.join(os.path.dirname(os.path.dirname(__file__)), "Impor.py"),
                 "run", path],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60)
            return r.returncode, (r.stdout or "") + (r.stderr or "")

    def test_gt_and_gte(self):
        code = ("for i = 5; i > 0; i-- {\n    print(i)\n}\n"
                "for j = 3; j >= 1; j-- {\n    print(j)\n}\n")
        rc, out = self._run(code)
        self.assertEqual(rc, 0, out)
        digits = [x for x in out.split() if x.isdigit()]
        self.assertEqual(digits, ["5", "4", "3", "2", "1", "3", "2", "1"])

    def test_mismatched_direction_is_rejected(self):
        rc, out = self._run("for i = 0; i < 3; i-- {\n print(i)\n}\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("递减步进", out)


class TestAITokenBudget(unittest.TestCase):
    def test_clamped_to_safe_range(self):
        cfg = ai_assist.normalize_config({"ai": {"max_tokens": 1}})
        self.assertEqual(cfg["max_tokens"], 64)
        cfg = ai_assist.normalize_config({"ai": {"max_tokens": 999999}})
        self.assertEqual(cfg["max_tokens"], 8192)
        cfg = ai_assist.normalize_config({"ai": {"max_tokens": "bad"}})
        self.assertEqual(cfg["max_tokens"], 1200)


if __name__ == "__main__":
    unittest.main()
