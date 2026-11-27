# -*- coding: utf-8 -*-
"""4.0.1：impor pkg upgrade 要同步刷新项目内 vendor 副本（<proj>/lib/<name>）。

改前实测：upgrade 只重写全局缓存，项目里的 lib/<name> 仍是旧版，跑起来用的还是老代码。
本文件在临时目录内直接调 pkgmgr（显式传 cache_dir），不碰真实 ~/.impor/pkg。
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from impor import pkgmgr  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _make_src(dirpath, name, version, greeting):
    _write(os.path.join(dirpath, "impor.toml"),
           '[project]\nname = "%s"\nversion = "%s"\n' % (name, version))
    _write(os.path.join(dirpath, name + ".impor"),
           'def hello() {\n    return "%s"\n}\n\nexport hello\n' % greeting)


class UpgradeVendorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upg_")
        self.cache = os.path.join(self.tmp, "cache")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        _write(os.path.join(self.proj, "impor.toml"),
               '[project]\nname = "proj"\nversion = "0.1.0"\n')
        self.src = os.path.join(self.tmp, "src")
        _make_src(self.src, "upgprobe", "1.0.0", "旧版")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lib_hello(self):
        p = os.path.join(self.proj, "lib", "upgprobe", "upgprobe.impor")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_upgrade_refreshes_vendored_copy(self):
        pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache,
                       vendor=True, report={})
        self.assertIn("旧版", self._lib_hello())

        _make_src(self.src, "upgprobe", "2.0.0", "新版")
        report = {}
        pkgmgr.upgrade(project_dir=self.proj, cache_dir=self.cache, report=report)

        self.assertIn("新版", self._lib_hello(),
                      "upgrade 后 lib/ 副本没刷新")
        with open(os.path.join(self.proj, "lib", "upgprobe", "impor.toml"),
                  encoding="utf-8") as f:
            meta = f.read()
        self.assertIn('version = "2.0.0"', meta)
        self.assertEqual(len(report.get("revendored") or []), 1)

    def test_upgrade_does_not_create_vendor_when_absent(self):
        pkgmgr.install(self.src, project_dir=self.proj, cache_dir=self.cache,
                       vendor=False, report={})
        self.assertFalse(os.path.isdir(os.path.join(self.proj, "lib", "upgprobe")))

        _make_src(self.src, "upgprobe", "2.0.0", "新版")
        report = {}
        pkgmgr.upgrade(project_dir=self.proj, cache_dir=self.cache, report=report)
        self.assertFalse(os.path.isdir(os.path.join(self.proj, "lib", "upgprobe")),
                         "--no-vendor 装过的包，upgrade 不该凭空生成 lib 副本")
        self.assertEqual(report.get("revendored"), [])


if __name__ == "__main__":
    unittest.main()
