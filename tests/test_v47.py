# -*- coding: utf-8 -*-
"""v3.7 回归：独立 exe 的图标资源。"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from impor.peicon import _load_ico, apply_icon


def _icon_count(exe):
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    large = ctypes.c_void_p()
    small = ctypes.c_void_p()
    n = shell32.ExtractIconExW(os.path.abspath(exe), 0,
                              ctypes.byref(large), ctypes.byref(small), 1)
    for h in (large, small):
        if h.value:
            ctypes.WinDLL("user32").DestroyIcon(h)
    return n


@unittest.skipUnless(sys.platform == "win32", "仅 Windows PE")
class TestExeIcon(unittest.TestCase):
    def test_ico_has_all_sizes(self):
        ico = os.path.join(ROOT, "impor", "assets", "icons", "impor.ico")
        images = _load_ico(ico)
        self.assertEqual(len(images), 7)
        self.assertEqual(sorted((im["w"] or 256) for im in images),
                         [16, 24, 32, 48, 64, 128, 256])

    def test_build_embeds_icon(self):
        tmp = tempfile.mkdtemp(prefix="impor_icon_", dir=os.path.join(ROOT, "tests"))
        src = os.path.join(tmp, "hello.impor")
        exe = os.path.join(tmp, "hello.exe")
        with open(src, "w", encoding="utf-8") as f:
            f.write('important<<DAY<<english("English"):\nprint("icon ok")\n')
        try:
            from impor.cli import main
            rc = main(["build", src, "-o", exe])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(exe))
            out = subprocess.check_output([exe], text=True)
            self.assertIn("icon ok", out)
            self.assertGreater(_icon_count(exe), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_icon_replaces_on_copy(self):
        src = os.path.join(ROOT, "impor", "imporc.exe")
        self.assertTrue(os.path.isfile(src))
        tmp = tempfile.mkdtemp(prefix="impor_icopy_")
        dst = os.path.join(tmp, "imporc_copy.exe")
        try:
            shutil.copyfile(src, dst)
            apply_icon(dst)
            self.assertGreater(_icon_count(dst), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
