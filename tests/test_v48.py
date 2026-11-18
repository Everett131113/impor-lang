# -*- coding: utf-8 -*-
"""v3.7.0 回归：C 后端的 input / inputstr 提示参数与零参数调用。"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TestCBackendInput(unittest.TestCase):
    def _build_run(self, body, stdin_text):
        tmp = tempfile.mkdtemp(prefix="impor_input_")
        src = os.path.join(tmp, "input_demo.impor")
        exe = os.path.join(tmp, "input_demo.exe")
        with open(src, "w", encoding="utf-8") as f:
            f.write('important<<DAY<<english("English"):\n' + body + "\n")
        try:
            from impor.cli import main
            self.assertEqual(main(["build", src, "-o", exe]), 0)
            # 刚写入图标的 exe 可能被安全软件短暂扫描锁定
            for attempt in range(3):
                try:
                    return subprocess.run([exe], input=stdin_text, capture_output=True,
                                          text=True, timeout=30)
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_inputstr_prompt(self):
        r = self._build_run('print(inputstr("请输入："))', "abc\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("请输入：abc", r.stdout)

    def test_inputstr_without_args(self):
        r = self._build_run("print(inputstr())", "hello\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello", r.stdout)

    def test_input_prompt_and_number(self):
        r = self._build_run('print(to_num(inputstr("数字：")) + 1)', "41\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("数字：42", r.stdout)


if __name__ == "__main__":
    unittest.main()
