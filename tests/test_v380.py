# -*- coding: utf-8 -*-
"""v3.8.0 回归：多根工作区、跨根搜索、workspace trust、Qt 内核预览自检。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from impor.editor import App
from impor.editor_search import SearchOptions, search_project
from impor.project import load_project, save_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestWorkspaceRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v380_")
        self.base = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.base, "src"))
        os.makedirs(os.path.join(self.base, "extra"))
        with open(os.path.join(self.base, "src", "a.impor"), "w", encoding="utf-8") as f:
            f.write("// needle one\nprint(1)\n")
        with open(os.path.join(self.base, "extra", "b.impor"), "w", encoding="utf-8") as f:
            f.write("// needle two\nprint(2)\n")
        save_project(self.base, roots=["src", "extra"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_multi_roots(self):
        project = load_project(self.base, self.base)
        self.assertEqual(len(project["roots"]), 2)
        self.assertEqual({os.path.basename(p) for p in project["roots"]}, {"src", "extra"})

    def test_search_across_roots(self):
        project = load_project(self.base, self.base)
        results = search_project(project["roots"], SearchOptions(query="needle"))
        self.assertEqual(len(results), 2)
        self.assertEqual({os.path.basename(r.path) for r in results}, {"a.impor", "b.impor"})

    def test_search_dedupes_overlapping_roots(self):
        project = load_project(self.base, self.base)
        results = search_project([self.base, os.path.join(self.base, "src")],
                                 SearchOptions(query="needle"))
        self.assertEqual(len(results), 2)

    def test_search_stays_inside_roots(self):
        outside = os.path.join(self.tmp, "outside.impor")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("// needle outside\n")
        project = load_project(self.base, self.base)
        results = search_project(project["roots"], SearchOptions(query="needle"),
                                 paths=[outside])
        self.assertEqual(results, [])

    @unittest.skipUnless(os.name == "nt", "Windows 路径大小写规范化")
    def test_trust_key_normalizes_case(self):
        a = App._trust_key(self.base)
        b = App._trust_key(self.base.upper())
        self.assertEqual(a, b)


def _has_pyqt6():
    try:
        import PyQt6  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_has_pyqt6(), "PyQt6 未安装，跳过 Qt 内核预览烟测")
class TestKernel3Preview(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run([sys.executable, "-m", "impor.kernel3"] + list(args),
                              cwd=ROOT, capture_output=True, text=True, env=env, timeout=90)

    def test_smoke(self):
        proc = self._run("--smoke")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SMOKE_OK", proc.stdout)

    def test_window_renders(self):
        proc = self._run("--window-smoke")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("WINDOW_SMOKE_OK 900x600", proc.stdout)


if __name__ == "__main__":
    unittest.main()
