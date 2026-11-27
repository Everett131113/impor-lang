# -*- coding: utf-8 -*-
"""4.1 回归：稳定性内核（五道资源闸门 + 子进程围栏 + 崩溃留证）。

锁住四件事：
  1. 默认关闭：不传 --stable 时行为与 4.0.0 一致（没有闸门、没有额外错误码）；
  2. 打开后：死循环/卡死/刷屏/巨型分配/内存累积/无限递归/除零全部结构化熔断；
  3. 熔断是一次「错误」不是「报废」：同一个解释器熔断后还能继续跑；
  4. 额度按顶层执行重置，REPL/编辑器不会被上一条命令拖累。
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from impor import cli
from impor.interpreter import Interpreter, ImporException, parse_source, strip_header
from impor import stability as st


def _interp(stable=None, out=None):
    interp = Interpreter(log_cb=(out.append if out is not None else None), filename="stab")
    if stable is not None:
        interp.enable_stability(stable)
    return interp


def _run(interp, src):
    interp.run(parse_source(strip_header(src), filename="stab"))


def _cli(args, timeout=40):
    return subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py")] + args,
                          cwd=ROOT, capture_output=True, text=True, timeout=timeout)


class TestDefaultOff(unittest.TestCase):
    def test_interpreter_has_no_governor_by_default(self):
        interp = Interpreter()
        self.assertIsNone(interp.stab)

    def test_long_loop_still_runs_without_stable(self):
        out = []
        interp = _interp(None, out)
        _run(interp, "var i = 0\nwhile i < 200000\n    set i = i + 1\nprint(i)\n")
        self.assertEqual(out, ["200000"])

    def test_cli_without_stable_keeps_exit_code(self):
        r = _cli(["run", "--no-native", os.path.join(ROOT, "examples", "quickstart", "hello.impor")])
        self.assertEqual(r.returncode, 0)
        self.assertIn("你好", r.stdout)


class TestGatesTrip(unittest.TestCase):
    def test_step_limit(self):
        interp = _interp(st.Limits(steps=200))
        with self.assertRaises(ImporException) as ctx:
            _run(interp, "var i = 0\nwhile true\n    set i = i + 1\n")
        self.assertEqual(ctx.exception.name, "StepLimit")
        self.assertIn("E1020", interp_filename_code(ctx.exception))

    def test_time_limit(self):
        interp = _interp(st.Limits(seconds=0.4, steps=10 ** 12))
        with self.assertRaises(ImporException) as ctx:
            _run(interp, "var i = 0\nwhile true\n    sleep(0.05)\n    set i = i + 1\n")
        self.assertEqual(ctx.exception.name, "TimeLimit")

    def test_output_limit(self):
        out = []
        interp = _interp(st.Limits(output_kb=1), out)
        with self.assertRaises(ImporException) as ctx:
            _run(interp, 'var i = 0\nwhile i < 5000\n    print("0123456789")\n    set i = i + 1\n')
        self.assertEqual(ctx.exception.name, "OutputLimit")
        self.assertLess(len(out), 5000, "熔断后不该继续刷屏")

    def test_alloc_limit_checked_before_allocating(self):
        interp = _interp(st.Limits(alloc=1000000))
        with self.assertRaises(ImporException) as ctx:
            _run(interp, "var a = arrfill(50000000, 0)\n")
        self.assertEqual(ctx.exception.name, "AllocLimit")
        # 分配前拒绝：进程 RSS 不该因此跳到 400 MB
        self.assertLess(st.rss_bytes() / st.MB, 400)

    def test_string_repeat_prechecked(self):
        interp = _interp(st.Limits(alloc=1000000))
        with self.assertRaises(ImporException) as ctx:
            _run(interp, 'var s = "x" * 50000000\n')
        self.assertEqual(ctx.exception.name, "AllocLimit")

    def test_recursion_has_e1011(self):
        interp = _interp(st.Limits(steps=10 ** 9))
        with self.assertRaises(ImporException) as ctx:
            _run(interp, "def f()\n    return f()\nf()\n")
        from impor.diagnostics import error_code
        self.assertEqual(ctx.exception.name, "RecursionDeep")
        self.assertEqual(error_code(ctx.exception.name), "E1011")


class TestRecovery(unittest.TestCase):
    def test_same_interpreter_survives_a_trip(self):
        out = []
        interp = _interp(st.Limits(steps=300), out)
        with self.assertRaises(ImporException):
            _run(interp, "var i = 0\nwhile true\n    set i = i + 1\n")
        # 额度是「每次执行」的，熔断次数是累计的（编辑器状态栏/评测报告要用）
        self.assertEqual(interp.stab.trip_counts.get("StepLimit"), 1)
        # 同一个解释器继续执行：额度按每次顶层执行重置
        _run(interp, "print(1 + 1)")
        self.assertEqual(out[-1], "2")

    def test_limits_reset_between_runs(self):
        out = []
        interp = _interp(st.Limits(steps=500), out)
        for _ in range(3):
            _run(interp, "var i = 0\nwhile i < 400\n    set i = i + 1\n")
        # 400 次循环 + 声明/while/收尾语句，允许几条固定开销
        self.assertGreaterEqual(interp.stab.steps, 400)
        self.assertLessEqual(interp.stab.steps, 410)
        self.assertEqual(interp.stab.sessions >= 3, True)

    def test_repeated_trips_do_not_leak(self):
        interp = _interp(st.Limits(steps=200))
        for _ in range(6):
            with self.assertRaises(ImporException):
                _run(interp, "var i = 0\nwhile true\n    set i = i + 1\n")
        self.assertLess(st.rss_bytes() / st.MB, 600)


class TestEnvAndIsolation(unittest.TestCase):
    def test_limits_from_env(self):
        lim = st.Limits.from_env(environ={"IMPOR_STABLE": "1", "IMPOR_LIMIT_STEPS": "12345",
                                          "IMPOR_LIMIT_SECONDS": "7.5", "IMPOR_LIMIT_MEMORY_MB": "321",
                                          "IMPOR_LIMIT_OUTPUT_KB": "64", "IMPOR_LIMIT_ALLOC": "100"})
        self.assertEqual((lim.steps, lim.seconds, lim.memory_mb, lim.output_kb, lim.alloc),
                         (12345, 7.5, 321, 64, 100))

    def test_cli_env_limit_applies(self):
        fd, path = tempfile.mkstemp(suffix=".impor", prefix="impor_stab_env_")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("var i = 0\nwhile true\n    set i = i + 1\n")
            env = dict(os.environ)
            env.update({"IMPOR_STABLE": "1", "IMPOR_LIMIT_STEPS": "1000",
                        "PYTHONIOENCODING": "utf-8"})
            r = subprocess.run([sys.executable, os.path.join(ROOT, "Impor.py"),
                                "run", path], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=40)
            self.assertEqual(r.returncode, 1)
            self.assertIn("E1020", r.stderr + r.stdout)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_run_isolated_classifies_timeout(self):
        fd, path = tempfile.mkstemp(suffix=".impor", prefix="impor_stab_iso_")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("var i = 0\nwhile true\n    sleep(0.2)\n    set i = i + 1\n")
            r = st.run_isolated(["run", "--stable", path], limits=st.Limits(seconds=100),
                                timeout=2)
            self.assertEqual(r["kind"], st.KIND_TIMEOUT)
            self.assertEqual(r["exit_code"], 5)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_selftest_all_pass(self):
        r = st.selftest(verbose=False)
        self.assertEqual(r["passed"], r["total"], [c for c in r["cases"] if not c["ok"]])
        self.assertGreaterEqual(r["total"], 8)


class TestCrashLog(unittest.TestCase):
    def test_crash_log_written(self):
        try:
            raise ValueError("stab-test-marker")
        except ValueError as e:
            path = st.write_crash_log(e, where="unit-test")
        self.assertTrue(path and os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("stab-test-marker", body)
        self.assertIn("where: unit-test", body)


def interp_filename_code(exc):
    from impor.diagnostics import error_code
    return error_code(getattr(exc, "name", ""))


if __name__ == "__main__":
    unittest.main()
