# -*- coding: utf-8 -*-
"""3.2.3 编辑器升级回归：语义高亮、异步诊断、覆盖率、三方合并、插件管理器。"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.editor import App, semantic_symbol_map, split_conflict_markers
from impor.studio_ext import PluginManager


class DummyPane:
    _collect_lint = App._collect_lint
    _coverage_exec_lines = App._coverage_exec_lines


class TestSemanticSymbols(unittest.TestCase):
    def test_kinds_and_precedence(self):
        src = """import math
from os import path as p
func add(a, b):
    var total = a + b
    return total
class Box:
    var value = 1
exception Bad
struct Point
for item in [1, 2]:
    print(item)
"""
        table = semantic_symbol_map(src)
        self.assertEqual(table["add"][0], "func")
        self.assertEqual(table["a"][0], "param")
        self.assertEqual(table["b"][0], "param")
        self.assertEqual(table["total"][0], "var")
        self.assertEqual(table["Box"][0], "class")
        self.assertEqual(table["Bad"][0], "class")
        self.assertEqual(table["Point"][0], "struct")
        self.assertEqual(table["math"][0], "module")
        self.assertEqual(table["p"][0], "module")
        self.assertEqual(table["item"][0], "var")

    def test_unknown_member_not_declared(self):
        table = semantic_symbol_map("func f(obj):\n    return obj.field\n")
        self.assertNotIn("field", table)
        self.assertEqual(table["obj"][0], "param")


class TestAsyncDiagnostics(unittest.TestCase):
    def test_collect_reports_syntax_error(self):
        issues, error = DummyPane()._collect_lint("func add(a, b) {", "bad.impor")
        self.assertTrue(error)
        self.assertEqual(issues, [])

    def test_collect_clean_source(self):
        issues, error = DummyPane()._collect_lint("var a = 1\nprint(a)\n", "ok.impor")
        self.assertIsNone(error)
        self.assertIsInstance(issues, list)

    def test_coverage_exec_lines(self):
        lines = DummyPane()._coverage_exec_lines("var a = 1\nif a > 0:\n    print(a)\n")
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(x, int) and x > 0 for x in lines))


class TestConflictParser(unittest.TestCase):
    SRC = """head
<<<<<<< ours
OURS-A
||||||| base
BASE-A
=======
THEIRS-A
>>>>>>> theirs
middle
<<<<<<< ours
OURS-B
=======
THEIRS-B
>>>>>>> theirs
tail
"""

    def test_segments_and_hunks(self):
        segments, hunks = split_conflict_markers(self.SRC)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0]["ours"], ["OURS-A\n"])
        self.assertEqual(hunks[0]["base"], ["BASE-A\n"])
        self.assertEqual(hunks[0]["theirs"], ["THEIRS-A\n"])
        self.assertEqual(hunks[1]["base"], [])
        self.assertEqual(len([s for s in segments if isinstance(s, str)]), 3)

    def test_resolve_build(self):
        segments, hunks = split_conflict_markers(self.SRC)
        out = []
        for seg in segments:
            if isinstance(seg, dict):
                out.append("".join(seg["theirs"] if seg["index"] == 1 else seg["ours"]))
            else:
                out.append(seg)
        text = "".join(out)
        self.assertNotIn("<<<<<<<", text)
        self.assertIn("OURS-A", text)
        self.assertIn("THEIRS-B", text)
        self.assertNotIn("OURS-B", text)


class FakeApp:
    def __init__(self, workspace):
        self.workspace = workspace
        self.project_settings = {}
        self.plugin_isolation = False


class TestPluginManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "plugins")
        self.app = FakeApp(self.tmp.name)
        self.mgr = PluginManager(self.app)
        self.mgr.home_root = lambda: self.root

    def tearDown(self):
        self.tmp.cleanup()

    def _make_plugin(self, folder, name="demo.py", text=None):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or (
                "# impor-name: Demo\n"
                "# impor-version: 1.2.3\n"
                "# impor-description: test plugin\n"
                "# impor-permissions: read, write\n"
                "def register(api):\n"
                "    pass\n"))
        return path

    def test_install_meta_enable_and_uninstall(self):
        src = self._make_plugin(os.path.join(self.tmp.name, "incoming"))
        installed = self.mgr.install_folder(src)
        self.assertEqual(len(installed), 1)
        self.assertTrue(os.path.isfile(installed[0]))
        meta = self.mgr.meta(installed[0])
        self.assertEqual(meta["name"], "Demo")
        self.assertEqual(meta["version"], "1.2.3")
        self.assertTrue(meta["permissions"]["write"])
        self.assertTrue(meta["enabled"])
        self.mgr.set_enabled(installed[0], False)
        self.assertFalse(self.mgr.meta(installed[0])["enabled"])
        self.mgr.uninstall(installed[0])
        self.assertFalse(os.path.exists(installed[0]))

    def test_install_zip(self):
        src = self._make_plugin(os.path.join(self.tmp.name, "zipsrc", "pkg"), name="plugin.py")
        zpath = os.path.join(self.tmp.name, "plugin.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(src, "pkg/plugin.py")
        installed = self.mgr.install_zip(zpath)
        self.assertTrue(installed)
        self.assertTrue(os.path.isfile(installed[0]))
        meta = self.mgr.meta(installed[0])
        self.assertEqual(meta["version"], "1.2.3")

    def test_zip_traversal_rejected(self):
        zpath = os.path.join(self.tmp.name, "bad.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../evil.py", "def register(api):\n    pass\n")
        with self.assertRaises(ValueError):
            self.mgr.install_zip(zpath)


if __name__ == "__main__":
    unittest.main()
