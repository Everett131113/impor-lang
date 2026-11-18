# -*- coding: utf-8 -*-
"""v3.6.15：第一个真实用户工具 Impor 专注计时器的无窗口回归。

这里只测纯逻辑与脚本可执行性，不打开 Tk 窗口：
- 时间格式化 25:00 / 00:59 / 负数归零
- 今日分钟数 / 段数统计
- JSON 编解码往返
- `--selftest` 在真实 Impor 解释器里退出码为 0
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "apps", "impor_focus", "focus.impor")


class TestImporFocusApp(unittest.TestCase):
    def test_app_source_exists(self):
        self.assertTrue(os.path.isfile(APP), APP)
        with open(APP, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Impor 专注计时器", src)
        self.assertIn("--selftest", src)
        self.assertIn("--smoke-gui", src)

    def test_selftest(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "Impor.py"), "run", APP, "--selftest"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SELFTEST OK", r.stdout)

    def test_compiled_python_selftest(self):
        """生成代码脱离解释器内建表后，文件/JSON 内建仍必须可用。

        这是 3.6.15 打包 exe 时真实踩到的坑：PyInstaller 只打包
        `impor.runtime`，不会把解释器在运行时注入的 `fileexists` 等内建带进去。
        """
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "focus_generated.py")
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            c = subprocess.run(
                [sys.executable, os.path.join(ROOT, "Impor.py"), "compile", APP, "-o", out],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=env,
            )
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.assertTrue(os.path.isfile(out))
            r = subprocess.run(
                [sys.executable, out, "--selftest"], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120, env=env,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("SELFTEST OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
