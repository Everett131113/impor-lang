# -*- coding: utf-8 -*-
"""4.7.3：编辑器加固的无头回归。

锁住四件事，任何一件退回旧行为都算失败：
  1) 编码保真：BOM + CRLF 文件保存后字节一致（旧版每存一次多 1 个空行）；
  2) 崩溃兜底：进程被强杀后 .impor_recovery 里有最后 3 秒的正文，重开能接回原路径；
  3) 进程树终止：kill_tree 杀掉父进程后，它拉起的子进程也必须死；
  4) 定时器链自愈：一次 tick 抛异常后，链必须被重排（否则自动保存永久失效）。

跑法：python tests/test_editor_headless.py -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PY = sys.executable

from impor.editor import _align_eol  # noqa: E402
from impor.pathsafe import rel_quiet  # noqa: E402
from impor.stability import kill_tree, crash_log_path  # noqa: E402


class FakePane:
    def __init__(self, ends_nl=True):
        self._ends_nl = ends_nl


class TestAlignEol(unittest.TestCase):
    """纯函数：换行对齐是「保存一次多一个空行」的根因所在。"""

    def test_trailing_newline_kept(self):
        self.assertEqual(_align_eol("a\r\nb\r\n", FakePane(True)), "a\nb\n")

    def test_no_trailing_newline_stripped(self):
        # Tk 的 end 天然带一个换行；原文件没有末尾换行时必须去掉它
        self.assertEqual(_align_eol("a\nb\n", FakePane(False)), "a\nb")
        self.assertEqual(_align_eol("a\nb", FakePane(False)), "a\nb")

    def test_bare_cr_normalized(self):
        # 正文里残留 \r 会被 newline="\r\n" 写成 \r\r\n（每行多 1 字节）
        self.assertEqual(_align_eol("a\rb\r", FakePane(False)), "a\nb")

    def test_empty(self):
        self.assertEqual(_align_eol("", FakePane(False)), "")


class TestRelQuiet(unittest.TestCase):
    """跨盘 relpath 必须退化为 abspath，不能抛 ValueError（旧版会让标签栏刷新崩掉）。"""

    def test_cross_drive_no_raise(self):
        got = rel_quiet(r"C:\Users\x\a.impor", r"D:\ImporWork\impor471")
        self.assertTrue(os.path.isabs(got))

    def test_same_drive_relative(self):
        got = rel_quiet(r"D:\ImporWork\impor471\impor\editor.py",
                        r"D:\ImporWork\impor471")
        self.assertIn("editor.py", got)
        self.assertFalse(os.path.isabs(got))


class TestKillTree(unittest.TestCase):
    """父进程拉一个长睡子进程，kill_tree 之后两个都必须消失。"""

    SPAWN = (
        "import subprocess,sys,time,json\n"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'])\n"
        "print(json.dumps({'child':p.pid}),flush=True)\n"
        "time.sleep(300)\n"
    )

    def _alive(self, pid):
        try:
            import psutil
            return psutil.Process(pid).is_running()
        except Exception:
            return False

    def test_tree_dies(self):
        import json
        proc = subprocess.Popen([PY, "-c", self.SPAWN],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline().strip()
            info = json.loads(line)
            child = int(info["child"])
            self.assertTrue(self._alive(proc.pid))
            self.assertTrue(self._alive(child))
            ok, detail = kill_tree(proc.pid, timeout=4.0)
            time.sleep(1.0)
            self.assertFalse(self._alive(proc.pid), "父进程仍存活: %s" % detail)
            self.assertFalse(self._alive(child), "子进程仍存活: %s" % detail)
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass


class TestEditorSmoke(unittest.TestCase):
    """无交互 GUI 冒烟：新标签 → 保存回读 → 恢复副本 → BOM/CRLF 往返 → 定时器链自愈。"""

    def _run(self, env_extra, timeout=120):
        env = dict(os.environ)
        env.pop("IMPOR_EDITOR_SMOKE", None)
        env.pop("IMPOR_EDITOR_EXPECT_RESTORE", None)
        env.pop("IMPOR_EDITOR_CRASH_DRILL", None)
        env.update(env_extra)
        return subprocess.run([PY, "Impor.py"], cwd=ROOT, env=env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_smoke_all_steps(self):
        r = self._run({"IMPOR_EDITOR_SMOKE": "1"})
        self.assertEqual(r.returncode, 0, r.stdout)
        for step in ("new-tab", "save-readback", "recovery",
                     "encoding-roundtrip", "after-chain", "diagnostics"):
            self.assertIn(step, r.stdout, "缺少步骤 %s\n%s" % (step, r.stdout))


class TestCrashDrill(unittest.TestCase):
    """强杀演练：脏标签 → 恢复副本落盘 → kill -9 → 重开必须找回正文与原路径。"""

    def test_recover_after_hard_kill(self):
        marker = "CRASHDRILL-%d" % int(time.time())
        env = dict(os.environ)
        env.pop("IMPOR_EDITOR_SMOKE", None)
        env["IMPOR_EDITOR_CRASH_DRILL"] = marker
        proc = subprocess.Popen([PY, "Impor.py"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        armed = False
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                if "DRILL ARMED" in line:
                    armed = True
                    break
            self.assertTrue(armed, "崩溃演练未进入 ARMED")
            rec_dir = os.path.join(os.path.expanduser("~"), ".impor", "recovery")
            if not os.path.isdir(rec_dir):
                rec_dir = os.path.join(ROOT, ".impor_recovery")
            seen = []
            hit = None
            for fn in (os.listdir(rec_dir) if os.path.isdir(rec_dir) else []):
                if not fn.endswith(".impor"):
                    continue
                try:
                    with open(os.path.join(rec_dir, fn), encoding="utf-8") as f:
                        body = f.read()
                    seen.append("%s=%r" % (fn, body[:60]))
                    if marker in body:
                        hit = fn
                        break
                except Exception as exc:
                    seen.append("%s!%r" % (fn, exc))
                    continue
            self.assertIsNotNone(hit, "被强杀前恢复副本里没有 marker；%s 下: %s"
                                 % (rec_dir, seen or "目录为空"))
            # 硬杀：模拟崩溃/断电（不走 on_close）
            import psutil
            psutil.Process(proc.pid).kill()
            proc.wait(timeout=10)
            # 重开：冒烟先跑 restore，必须找回正文并接回原路径
            r = subprocess.run([PY, "Impor.py"], cwd=ROOT,
                               env=dict(os.environ, IMPOR_EDITOR_SMOKE="1",
                                        IMPOR_EDITOR_EXPECT_RESTORE=marker),
                               timeout=120, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("restore", r.stdout)
        finally:
            if proc.poll() is None:
                try:
                    import psutil
                    psutil.Process(proc.pid).kill()
                except Exception:
                    pass


class TestCrashLog(unittest.TestCase):
    def test_crash_log_path_writable(self):
        p = crash_log_path()
        d = os.path.dirname(p)
        os.makedirs(d, exist_ok=True)
        self.assertTrue(os.path.isdir(d))


if __name__ == "__main__":
    unittest.main(verbosity=2)
