# -*- coding: utf-8 -*-
"""v3.6 启动过场（impor/splash.py）回归测试。

Tk 不可用（无 init.tcl 的沙箱、无显示器环境）时自动跳过 GUI 用例，
只保留环境变量开关的纯逻辑断言。
"""

import os
import time
import unittest

from impor.splash import SplashScreen, splash_enabled


class TestSplashEnabled(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("IMPOR_NO_SPLASH")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("IMPOR_NO_SPLASH", None)
        else:
            os.environ["IMPOR_NO_SPLASH"] = self._old

    def test_default(self):
        os.environ.pop("IMPOR_NO_SPLASH", None)
        self.assertTrue(splash_enabled())

    def test_disabled_values(self):
        for v in ("1", "true", "TRUE", "yes", "on", " 1 "):
            os.environ["IMPOR_NO_SPLASH"] = v
            self.assertFalse(splash_enabled(), v)

    def test_other_values(self):
        for v in ("0", "false", "no", "off", "anything"):
            os.environ["IMPOR_NO_SPLASH"] = v
            self.assertTrue(splash_enabled(), v)


class TestSplashScreen(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
        except Exception as e:          # 沙箱 3.13 没有 init.tcl
            self.skipTest("Tk 不可用：%s" % e)
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _pump(self, done, timeout=2.0):
        end = time.monotonic() + timeout
        while not done and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        return bool(done)

    def test_finish_callback(self):
        s = SplashScreen(self.root)
        s.show()
        done = []
        s.set_on_done(lambda: done.append(1))
        s.start_animation()
        self.assertEqual(s.top.winfo_width(), 620)
        s.finish()
        self.assertTrue(self._pump(done), "finish() 后未触发 on_done")

    def test_skip_before_animation(self):
        s = SplashScreen(self.root)
        s.show()
        done = []
        s.set_on_done(lambda: done.append(1))
        s.finish()                      # 动画尚未开始时请求跳过
        s.start_animation()
        self.assertTrue(self._pump(done), "预跳过后 start_animation 未结束过场")

    def test_destroy_idempotent(self):
        s = SplashScreen(self.root)
        s.show()
        s._destroy()
        s._destroy()
        self.assertTrue(s._done)


if __name__ == "__main__":
    unittest.main()
