# -*- coding: utf-8 -*-
"""4.1：编辑器键入卡死修复的回归。

现场：键入 `fib(` 时 `_show_param_hint` 在 UI 线程调用 `index.definition()`，
而工作区索引正在后台全量重建，两者抢同一把 RLock —— 界面「未响应」数十秒。
本文件锁住三条契约：
  1) 重建不可重入排队（refresh 单飞，后来者立即返回）；
  2) UI 线程查询 probe() 绝不等锁、绝不触发重建；
  3) 删文件只清理它自己引用过的名字（旧版遍历全表是 O(文件×名字)）。
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from impor.symbol_index import ProjectIndex  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class TestRefreshSingleFlight(unittest.TestCase):
    def test_concurrent_refresh_does_not_queue(self):
        idx = ProjectIndex(ROOT, roots=[ROOT])
        calls = []
        slow = {"n": 0}
        real = idx._refresh

        def slow_refresh(force=False):
            slow["n"] += 1
            time.sleep(0.30)
            return real(force)

        idx._refresh = slow_refresh
        threads = [threading.Thread(target=lambda: calls.append(idx.refresh()))
                   for _ in range(4)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0
        # 只有一次真正重建，其余三次立即返回 False；总耗时 ≈ 0.3s 而非 1.2s
        self.assertEqual(slow["n"], 1)
        self.assertEqual(calls.count(False), 3)
        self.assertLess(elapsed, 0.8, "并发 refresh 仍在排队：%.2fs" % elapsed)


class TestProbeNeverBlocks(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        write(os.path.join(self.td, "a.impor"), "func fib(n)\n    return n\nprint(fib(1))\n")
        self.idx = ProjectIndex(self.td, roots=[self.td])
        self.idx.refresh()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_probe_returns_definition(self):
        syms = self.idx.probe("fib")
        self.assertTrue(syms, "索引空闲时 probe 应能拿到定义")
        self.assertEqual(syms[0].kind, "func")

    def test_probe_does_not_wait_for_lock(self):
        # 锁由另一个线程持有（RLock 同线程可重入，必须跨线程才模拟出真实争用）
        held = threading.Event()
        release = threading.Event()

        def holder():
            with self.idx._lock:
                held.set()
                release.wait(2.0)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(1.0)
        try:
            t0 = time.time()
            syms = self.idx.probe("fib")
            dt = time.time() - t0
        finally:
            release.set()
            t.join()
        self.assertEqual(syms, [])
        self.assertLess(dt, 0.05, "probe 等了锁：%.3fs" % dt)

    def test_probe_does_not_wait_for_refresh(self):
        self.idx._gate.acquire()  # 模拟后台正在全量重建
        try:
            t0 = time.time()
            syms = self.idx.probe("fib")
            dt = time.time() - t0
        finally:
            self.idx._gate.release()
        self.assertEqual(syms, [])
        self.assertLess(dt, 0.05, "probe 等了重建：%.3fs" % dt)

    def test_definition_still_blocking_after_fix(self):
        self.assertTrue(self.idx.definition("fib"), "definition 仍需给后台线程完整结果")


class TestDropFileIsLocal(unittest.TestCase):
    def test_drop_only_clears_own_refs(self):
        td = tempfile.mkdtemp()
        try:
            a = os.path.join(td, "a.impor")
            b = os.path.join(td, "b.impor")
            write(a, "func fa()\n    print(shared)\n")
            write(b, "func fb()\n    print(shared)\n")
            idx = ProjectIndex(td, roots=[td])
            idx.refresh()
            self.assertEqual(len(idx.refs.get("shared", [])), 2)
            os.remove(a)
            idx.refresh()
            # 引用表里只剩 b 的那条，名字没有被整条删掉
            refs = idx.refs.get("shared", [])
            self.assertEqual(len(refs), 1)
            self.assertEqual(os.path.abspath(refs[0][0]), os.path.abspath(b))
            self.assertNotIn(os.path.abspath(a), idx._refs_by_file)
            self.assertTrue(idx.probe("fb"))
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
