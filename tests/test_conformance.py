# -*- coding: utf-8 -*-
"""v3.3：三后端一致性测试接入 unittest（CI 与 run_tests.py 共用）。

Windows 上跑 conformance/run_conformance.py 全量用例；
非 Windows 或无 tcc/imporc 时跳过（C 后端需要随仓库分发的 tcc.exe）。
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "conformance", "run_conformance.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")
IMPORC = os.path.join(ROOT, "impor", "imporc.exe")


@unittest.skipUnless(sys.platform == "win32", "conformance 需要 Windows + tcc")
@unittest.skipUnless(os.path.isfile(TCC), "缺少 impor/tcc/win32/tcc.exe")
@unittest.skipUnless(os.path.isfile(IMPORC), "缺少 impor/imporc.exe")
class TestConformance(unittest.TestCase):
    def test_three_backends_byte_identical(self):
        r = subprocess.run([sys.executable, RUNNER, "--ci"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800, cwd=ROOT)
        self.assertEqual(r.returncode, 0, "conformance 存在差异：\n" + (r.stdout or "")[-3000:])

    def test_cases_count(self):
        d = os.path.join(ROOT, "conformance", "cases")
        n = len([f for f in os.listdir(d) if f.endswith(".impor")])
        self.assertGreaterEqual(n, 100, "conformance 用例少于 100 个（发版门禁要求 100+）")


if __name__ == "__main__":
    unittest.main()
