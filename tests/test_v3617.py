# -*- coding: utf-8 -*-
"""v3.6.17：专注计时器真实使用修正的无窗口回归。

这些用例不打开 Tk 窗口，只固定 3.6.17 从一次真实 25 分钟里得出的四条行为：
- 结束时刻可见；
- 完成时把窗口带回前面；
- 关窗保留当前未完成一轮；
- 第二天打开能看到昨天；
并确认没有偷偷加入托盘、系统通知或长休息。
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "apps", "impor_focus", "focus.impor")
VERSION = os.path.join(ROOT, "VERSION")


class TestFocus317(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(APP, "r", encoding="utf-8") as f:
            cls.src = f.read()
        with open(VERSION, "r", encoding="utf-8") as f:
            cls.version = f.read().strip()

    def test_version_is_3617(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")
        self.assertIn("Impor 专注计时器 · 3.6.17", self.src)
        self.assertIn("IMPOR FOCUS / 3.6.17", self.src)

    def test_end_time_is_visible_while_running(self):
        self.assertIn("var end_label", self.src)
        self.assertIn('"结束 " + time.strftime("%H:%M"', self.src)
        self.assertIn('"结束 --:--"', self.src)
        self.assertGreaterEqual(self.src.count("update_end_label()"), 8)

    def test_close_keeps_unfinished_round(self):
        self.assertIn("func state_path()", self.src)
        self.assertIn('return os.path.join(data_dir(), "state.json")', self.src)
        self.assertIn("func save_state()", self.src)
        self.assertIn("func clear_state()", self.src)
        close = self.src.split("func on_close()", 1)[1].split("func ", 1)[0]
        self.assertIn("save_state()", close)
        self.assertIn("save_history()", close)

    def test_second_day_shows_yesterday(self):
        self.assertIn("func yesterday_str()", self.src)
        self.assertIn("func last_session_for(items, day)", self.src)
        self.assertIn("昨天 " , self.src)
        self.assertIn("yesterday_label.configure", self.src)
        self.assertIn('"昨天 "', self.src)
        self.assertIn('"今天 "', self.src)

    def test_completion_brings_window_back(self):
        self.assertIn("func attention()", self.src)
        attention = self.src.split("func attention()", 1)[1].split("func ", 1)[0]
        for call in ("deiconify()", "lift()", "focus_force()", '"-topmost", true'):
            self.assertIn(call, attention)
        self.assertIn("root.after(5000, release_attention)", attention)
        finish = self.src.split("func finish_round()", 1)[1].split("func ", 1)[0]
        self.assertEqual(finish.count("attention()"), 2)

    def test_task_name_is_locked_and_cleared(self):
        self.assertIn("var session_task", self.src)
        add = self.src.split("func add_session(mins)", 1)[1].split("func ", 1)[0]
        self.assertIn("var name = session_task", add)
        self.assertLess(add.index("var name = session_task"), add.index("task_entry.get()"))
        finish = self.src.split("func finish_round()", 1)[1].split("func ", 1)[0]
        self.assertIn('global set session_task = ""', finish)
        self.assertIn("completed_task", finish)

    def test_no_tray_notification_or_long_break(self):
        for forbidden in ("pystray", "plyer", "win10toast", "Notification.notify", "long_break"):
            self.assertNotIn(forbidden, self.src)
        self.assertNotIn("托盘", self.src)
        self.assertNotIn("系统通知", self.src)
        self.assertNotIn("长休息", self.src)

    def test_selftest_still_passes(self):
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "Impor.py"), "run", APP, "--selftest"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SELFTEST OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
