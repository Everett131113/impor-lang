# -*- coding: utf-8 -*-
"""启动器（Impor.py）分派路径测试：防止子命令白名单与 CLI 派发表漂移。"""
import importlib
import io
import os
import subprocess
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(PROJECT_ROOT, "Impor.py")
SNAKE = os.path.join(PROJECT_ROOT, "examples", "snake.impor")


class TestLauncher(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, IMPOR_PY, *args],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=120,
        )

    def test_version(self):
        r = self._run("version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Impor (I++)", r.stdout)

    def test_check_snake(self):
        r = self._run("check", SNAKE)
        self.assertEqual(r.returncode, 0)

    def test_lint_snake(self):
        r = self._run("lint", SNAKE)
        self.assertEqual(r.returncode, 0)

    def test_run_smoke(self):
        r = self._run("run", SNAKE, "--smoke")
        self.assertEqual(r.returncode, 0)
        self.assertIn("SMOKE OK", r.stdout)

    def test_unknown_command_reports_usage(self):
        # 未知命令 → CLI 报错（不再静默打开 GUI 编辑器）
        r = self._run("no-such-command-xyz")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("未知命令", r.stdout + r.stderr)

    def test_direct_script_file_runs(self):
        # 直接给 .impor 文件 → 相当于 run（旧版会误开编辑器）
        r = self._run(SNAKE, "--smoke")
        self.assertEqual(r.returncode, 0)
        self.assertIn("SMOKE OK", r.stdout)

    def test_launcher_whitelist_matches_cli(self):
        # 启动器不再自带命令元组：白名单必须来自 impor.cli 且覆盖全部显式子命令
        sys.path.insert(0, PROJECT_ROOT)
        try:
            from impor.cli import SUPPORTED_COMMANDS
        finally:
            sys.path.remove(PROJECT_ROOT)
        self.assertEqual(
            SUPPORTED_COMMANDS,
            frozenset({
                "run", "run-dbg", "repl", "compile", "build", "check", "new",
                "list-modules", "reload", "check-imports", "fmt", "check-fmt",
                "lint", "doc", "lsp", "pkg", "test", "demo", "env", "bench", "bench-spec", "mem",
                "dap", "explain", "doctor", "teach", "conformance", "migrate-cn",
                "release-check", "sys-check", "syntax",
            }),
        )

    # ---------- v2.5.1：版本常量 / 编辑器导入友好降级 ----------
    def test_version_constant_and_docstring_sync(self):
        # 唯一版本常量存在，且 docstring 第一行自动注入版本号
        sys.path.insert(0, PROJECT_ROOT)
        try:
            import Impor as launcher
            importlib.reload(launcher)
            import impor
            self.assertEqual(launcher.__version__, impor.__version__)
            self.assertIn("v" + launcher.__version__, launcher.__doc__)
        finally:
            sys.path.remove(PROJECT_ROOT)

    def test_editor_import_error_friendly(self):
        # 模拟 impor.editor 导入失败（缺少 tkinter）→ 友好提示而非原始堆栈
        sys.path.insert(0, PROJECT_ROOT)
        try:
            import Impor as launcher
            importlib.reload(launcher)
            saved = sys.modules.get("impor.editor")
            sys.modules["impor.editor"] = None  # 导入 None → ImportError
            stderr = io.StringIO()
            old_err = sys.stderr
            sys.stderr = stderr
            try:
                rc = launcher._open_editor()
            finally:
                sys.stderr = old_err
                if saved is None:
                    sys.modules.pop("impor.editor", None)
                else:
                    sys.modules["impor.editor"] = saved
            self.assertEqual(rc, 1)
            msg = stderr.getvalue()
            self.assertIn("无法打开图形化编辑器", msg)
            self.assertIn("impor repl", msg)
            self.assertNotIn("Traceback", msg)
        finally:
            sys.path.remove(PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
