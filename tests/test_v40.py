# -*- coding: utf-8 -*-
"""v3.2.0 回归测试：自动内存管理（解释器 + C 后端 + CLI）与高级编辑器静态检查。"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from impor import cli
from impor.memory import MemoryManager, process_memory_report
from impor.interpreter import Interpreter, parse_source, strip_header


def run_impor(src):
    out = []
    interp = Interpreter(log_cb=out.append, filename="v40")
    interp.run(parse_source(strip_header(src), filename="v40"))
    return out, interp


def run_ctrans(src):
    fd, sp = tempfile.mkstemp(suffix=".impor", prefix="impor_v40_")
    os.close(fd)
    exe = sp + ".exe"
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    finally:
        for p in (sp, exe):
            try:
                os.unlink(p)
            except OSError:
                pass


MEM_SRC = (
    "var a = [1, 2, 3]\n"
    'var d = {"k": 1}\n'
    "class P\n"
    "    def __init__(x)\n"
    "        set self.x = x\n"
    "var p = new P(9)\n"
    'print("alloc=", memstats()["alloc"])\n'
    'print("threshold=", autogc())\n'
    "print(mem())\n"
    "print(gc())\n"
)

MEM_C_SRC = (
    "var a = [1, 2, \"abc\", 3.5]\n"
    'print("数组:", a)\n'
    "print(mem())\n"
    "print(gc())\n"
)


class MemoryManagerTest(unittest.TestCase):
    def test_alloc_and_stats(self):
        m = MemoryManager(threshold=1000)
        m.alloc("list", 32)
        m.alloc("instance", 64)
        st = m.stats()
        self.assertEqual(st["alloc"], 2)
        self.assertEqual(st["live"], 0)
        self.assertEqual(st["threshold"], 1000)
        self.assertTrue(st["auto"])
        self.assertGreater(getattr(m, "bytes_estimate", 0), 0)

    def test_threshold_triggers_collect(self):
        m = MemoryManager(threshold=3)
        for _ in range(4):
            m.alloc("list", 8)
        st = m.stats()
        self.assertEqual(st["alloc"], 4)
        self.assertEqual(st["peak"], 4)
        self.assertGreaterEqual(st["collections"], 1)

    def test_cleaner_and_toggle(self):
        m = MemoryManager()
        hits = []
        m.add_cleaner(lambda: hits.append(1))
        m.collect(reason="manual")
        self.assertEqual(len(hits), 1)
        self.assertEqual(m.collections, 1)
        m.set_enabled(False)
        for _ in range(30):
            m.alloc("list", 8)
        self.assertEqual(m.collections, 1)
        after = m.get_threshold() if hasattr(m, "get_threshold") else m.threshold
        self.assertEqual(after, 20000)

    def test_report_text(self):
        m = MemoryManager()
        text = m.report()
        self.assertIn("内存", text)
        # v3.3：报告改为「累计分配 / 存活 / 回收」口径，并说明容器不计入存活
        self.assertIn("累计分配", text)
        self.assertIn("循环引用", text)

    def test_process_memory_report(self):
        text = process_memory_report()
        self.assertIn("RSS", text)


class InterpreterMemoryTest(unittest.TestCase):
    def test_builtins_work(self):
        out, interp = run_impor(MEM_SRC)
        text = "\n".join(out)
        self.assertIn("内存: 累计分配", text)
        self.assertIn("自动内存回收", text)
        self.assertGreaterEqual(interp.memory.alloc_count, 3)
        self.assertEqual(interp.memory.collections, 1)

    def test_autogc_sets_threshold(self):
        interp = Interpreter(log_cb=lambda t: None, filename="v40")
        self.assertEqual(interp.memory.threshold, 20000)
        interp.memory.set_threshold(5)
        self.assertEqual(interp.memory.threshold, 5)
        for _ in range(6):
            interp.memory.alloc("list", 8)
        self.assertGreaterEqual(interp.memory.collections, 1)

    def test_mem_cleanup_hook(self):
        interp = Interpreter(log_cb=lambda t: None, filename="v40")
        interp.module_cache["broken"] = type("M", (), {"state": "failed"})()
        interp.memory.collect()
        self.assertNotIn("broken", interp.module_cache)


class CBackendMemoryTest(unittest.TestCase):
    def test_mem_and_gc(self):
        rc, out = run_ctrans(MEM_C_SRC)
        self.assertEqual(rc, 0, out)
        self.assertIn("C 运行时内存", out)
        self.assertIn("分配", out)
        self.assertIn("0", out)


class CliMemTest(unittest.TestCase):
    def test_overview(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_mem([])
        self.assertEqual(rc, cli.EXIT_OK)
        text = buf.getvalue()
        self.assertIn("自动内存管理", text)
        self.assertIn("RSS", text)
        self.assertIn("20000", text)

    def test_run_script(self):
        fd, sp = tempfile.mkstemp(suffix=".impor", prefix="v40_cli_")
        os.close(fd)
        try:
            with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(MEM_SRC)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_mem([sp])
            self.assertEqual(rc, cli.EXIT_OK)
            self.assertIn("运行前存活", buf.getvalue())
            self.assertIn("本次分配", buf.getvalue())
        finally:
            try:
                os.unlink(sp)
            except OSError:
                pass


class EditorTest(unittest.TestCase):
    def test_source_has_premium_features(self):
        src = ""
        for fn in ("editor.py", "editor_pane.py", "editor_dialogs.py",
                   "editor_groups.py", "editor_panels.py", "editor_runtime.py"):
            p = os.path.join(ROOT, "impor", fn)
            if os.path.isfile(p):
                with io.open(p, encoding="utf-8") as f:
                    src += f.read()
        for key in ("Code Studio", "CommandPalette", "curline", "bracket",
                    "FlatButton", "EditorPane", "资源管理器", "自动"):
            self.assertIn(key, src)
        self.assertNotIn("#9932cc", src.lower())

    def test_widgets_when_tk_available(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as e:
            self.skipTest("当前环境无可用 tkinter/Tcl：%s" % e)
        root.withdraw()
        try:
            from impor.editor import App
            app = App(root)
            root.update_idletasks()
            # 7.2 起渐进式启动：首帧先建外壳，打开文件后才出现编辑标签
            app.open_path(os.path.join(ROOT, "examples", "demo_01_variables.impor"))
            root.update_idletasks()
            self.assertEqual(len(app.panes), 1)
            pal = app.open_palette()
            self.assertGreater(pal.listbox.size(), 10)
            pal.destroy()
            app.show_bottom("mem")
            self.assertIn("内存", app.mem_text.get("1.0", "end"))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
