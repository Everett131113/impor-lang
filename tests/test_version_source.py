# -*- coding: utf-8 -*-
"""版本号单一真源门禁：VERSION 必须与启动器/包/VS Code 清单一致。"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestVersionSource(unittest.TestCase):
    def test_version_is_single_source(self):
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            ver = f.read().strip()
        self.assertTrue(ver)

        from impor.version import __version__ as pkg_ver
        self.assertEqual(pkg_ver, ver)

        with open(os.path.join(ROOT, "vscode-impor", "package.json"), encoding="utf-8") as f:
            ext = json.load(f)
        self.assertEqual(ext["version"], ver)

        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            py = f.read()
        self.assertIn('version = { file = ["VERSION"] }', py)
        self.assertNotIn('version = "3.', py)

        # 发布脚本只在仓库开发时存在，不随分发包分发；门禁不能依赖它。
        release = os.path.join(ROOT, "_release3617.py")
        if os.path.exists(release):
            with open(release, encoding="utf-8") as f:
                rel = f.read()
            self.assertIn('open(os.path.join(ROOT, "VERSION")', rel)
            self.assertNotIn('VER = "3.', rel)


if __name__ == "__main__":
    unittest.main()
