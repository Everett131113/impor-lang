# -*- coding: utf-8 -*-
"""v3.12 文本/文件内建：解释器 runtime 与 builtin_manifest 必须同名同义。"""
import os
import tempfile
import unittest

from impor import runtime
from impor import builtins as impor_builtins


class TextBuiltinsTest(unittest.TestCase):
    def test_find_lines_replace(self):
        self.assertEqual(runtime.strfind("a,b,c", ","), 1)
        self.assertEqual(runtime.strfind("a,b,c", ",", 2), 3)
        self.assertEqual(runtime.strfind("a,b,c", "z"), -1)
        self.assertEqual(runtime.strlines("a\nb\r\nc"), ["a", "b", "c"])
        self.assertEqual(runtime.strreplace("a,b,c", ",", "','"), "a','b','c")

    def test_filelines(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("one\n二\nthree\n")
            self.assertEqual(runtime.filelines(path), ["one", "二", "three"])
        finally:
            os.remove(path)

    def test_runtime_and_builtin_registry_agree(self):
        class _Interp:
            gui_root = None
            sandbox_mode = False

        fns = impor_builtins.make_builtins(_Interp())
        for name in ("strfind", "strlines", "strreplace", "filelines"):
            self.assertIn(name, fns)
            self.assertTrue(callable(fns[name]))
            self.assertTrue(callable(getattr(runtime, name)))


if __name__ == "__main__":
    unittest.main()
