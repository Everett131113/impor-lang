# -*- coding: utf-8 -*-
"""教科书优化编译器后端（OPTC）回归测试。

锁定五件事：
1. CFG 基本块划分与后继正确，所有块从入口可达；
2. 活跃变量数据流分析满足数据流方程，入口 live-in 含参数；
3. 线性扫描寄存器分配：寄存器不超量、同寄存器区间不重叠，压力下正确溢出；
4. 标量过程式核心（算术/控制流/递归/print）输出与解释器一致，溢出程序结果正确；
5. 不支持的特性与 int64 溢出/除零明确报错，不静默改语义。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")

sys.path.insert(0, ROOT)
from impor import optc  # noqa: E402
from impor.ir_lower import lower_source  # noqa: E402
from impor.irop import optimize_module  # noqa: E402


def build_ir(source):
    """lower + irop 优化，返回 IRModule（与 CLI 同一前置管线）。"""
    module = lower_source(source, "<test>")
    optimize_module(module)
    return module


def fn_ir(source, name):
    module = build_ir(source)
    for fn in [module.main] + module.funcs:
        if fn.name == name:
            return fn
    raise KeyError(name)


FIB = '''
def fib(n) {
  if n < 2 { return n }
  return fib(n - 1) + fib(n - 2)
}
print(fib(10))
'''

CORE = '''
var sum = 0
var i = 1
while i <= 100 {
  sum += i
  i += 1
}
print(sum)
def fact(n) {
  if n <= 1 { return 1 }
  return n * fact(n - 1)
}
print(fact(5))
var a = 3
var b = 7
print(a > b ? a : b)
print(20 / 3, 20 % 3)
'''

SPILL = '''
def spill_test(a) {
  var b = a + 1
  var c = b + 1
  var d = c + 1
  var e = d + 1
  var f = e + 1
  var g = f + 1
  var h = g + 1
  return a + b + c + d + e + f + g + h
}
print(spill_test(0))
'''


# ============================================================
# 阶段 1：CFG
# ============================================================
class TestCFG(unittest.TestCase):
    def test_fib_block_count(self):
        fn = fn_ir(FIB, "fib")
        leaders, bounds, succ, _ = optc.build_cfg(fn.body)
        self.assertEqual(len(bounds), 4)

    def test_all_reachable_from_entry(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        seen = set()
        stack = [0]
        while stack:
            b = stack.pop()
            if b in seen:
                continue
            seen.add(b)
            stack.extend(succ.get(b, []))
        self.assertEqual(seen, set(range(len(bounds))))

    def test_return_block_has_no_successor(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        # 至少存在一个以 RETURN 结尾、后继为空的块
        no_succ = [b for b in range(len(bounds)) if not succ.get(b)]
        self.assertTrue(no_succ)


# ============================================================
# 阶段 2：活跃变量数据流分析
# ============================================================
class TestLiveness(unittest.TestCase):
    def test_entry_livein_contains_param(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        live_in, live_out = optc.liveness(fn.body, bounds, succ)
        self.assertIn("n", live_in[0])

    def test_satisfies_dataflow_equation(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        live_in, live_out = optc.liveness(fn.body, bounds, succ)
        for bi, (start, end) in enumerate(bounds):
            use, defined = optc._block_use_def(fn.body, start, end)
            expect_in = set(use) | (live_out[bi] - defined)
            self.assertEqual(live_in[bi], expect_in)
            expect_out = set()
            for s in succ.get(bi, []):
                expect_out |= live_in[s]
            self.assertEqual(live_out[bi], expect_out)

    def test_intervals_cover_usage(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        _, live_out = optc.liveness(fn.body, bounds, succ)
        intervals = optc.live_intervals(fn.body, bounds, live_out)
        # 参数 n 与每个被定义的临时变量都有区间，且起点不晚于终点
        self.assertIn("n", intervals)
        for name, (st, en) in intervals.items():
            self.assertLessEqual(st, en)


# ============================================================
# 阶段 3：寄存器分配
# ============================================================
class TestAllocation(unittest.TestCase):
    def test_registers_within_limit(self):
        fn = fn_ir(FIB, "fib")
        tf = optc.compile_function(fn)
        for ins in tf.code:
            for operand in ins[1:]:
                self._check_operand(operand)

    def _check_operand(self, x):
        if isinstance(x, tuple):
            if x[0] == "r":
                self.assertLess(x[1], optc.NUM_REGS)
                self.assertGreaterEqual(x[1], 0)
        elif isinstance(x, list):
            for sub in x:
                self._check_operand(sub)

    def test_pressure_forces_spill(self):
        fn = fn_ir(SPILL, "spill_test")
        tf = optc.compile_function(fn)
        self.assertGreaterEqual(tf.spilled, 1)
        # 栈槽编号唯一
        slots = [self._slots(tf)]
        flat = slots[0]
        self.assertEqual(len(flat), len(set(flat)))

    def _slots(self, tf):
        out = []
        for ins in tf.code:
            self._collect_slots(ins[1:], out)
        return out

    def _collect_slots(self, x, out):
        if isinstance(x, tuple):
            if x[0] == "s":
                out.append(x[1])
        elif isinstance(x, list):
            for sub in x:
                self._collect_slots(sub, out)

    def test_no_overlapping_intervals_share_reg(self):
        fn = fn_ir(FIB, "fib")
        _, bounds, succ, _ = optc.build_cfg(fn.body)
        _, live_out = optc.liveness(fn.body, bounds, succ)
        intervals = optc.live_intervals(fn.body, bounds, live_out)
        alloc, _ = optc.linear_scan(intervals, fn.params)
        by_reg = {}
        for name, loc in alloc.items():
            if loc[0] == "r":
                by_reg.setdefault(loc[1], []).append(name)
        for reg, names in by_reg.items():
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = intervals[names[i]], intervals[names[j]]
                    self.assertTrue(a[1] < b[0] or b[1] < a[0],
                                    "寄存器 R%d 上区间重叠" % reg)


# ============================================================
# 阶段 4/5：端到端执行
# ============================================================
class TestEndToEnd(unittest.TestCase):
    def test_core_outputs(self):
        lines = []
        optc.run_source(CORE, "<core>", log_cb=lambda t: lines.append(t))
        self.assertEqual(lines[0], "5050")
        self.assertEqual(lines[1], "120")
        self.assertEqual(lines[2], "7")
        self.assertEqual(lines[3].split(),
                         ["6.666666666666667", "2"])

    def test_fib_value(self):
        lines = []
        optc.run_source(FIB, "<fib>", log_cb=lambda t: lines.append(t))
        self.assertEqual(lines, ["55"])

    def test_spill_program_correct(self):
        lines = []
        optc.run_source(SPILL, "<spill>", log_cb=lambda t: lines.append(t))
        # 0+1+2+3+4+5+6+7 = 28
        self.assertEqual(lines, ["28"])

    def test_unary_and_boolean(self):
        src = '''
var x = 5
print(-x)
print(not False)
print(x == 5 and x > 2)
print(x != 5 or True)
'''
        lines = []
        optc.run_source(src, "<b>", log_cb=lambda t: lines.append(t))
        self.assertEqual(lines, ["-5", "True", "True", "True"])


# ============================================================
# 错误：int64 溢出 / 除零 / 不支持特性
# ============================================================
class TestErrors(unittest.TestCase):
    def test_int64_overflow(self):
        src = '''
def big(n) { return n * n * n }
print(big(100000000000))
'''
        with self.assertRaises(optc.ImporException) as cm:
            optc.run_source(src, "<ov>")
        self.assertIn("OverflowError", str(cm.exception))

    def test_division_by_zero(self):
        with self.assertRaises(optc.ImporException) as cm:
            optc.run_source('print(1 / 0)', "<z>")
        self.assertIn("除数为 0", str(cm.exception))

    def test_list_rejected(self):
        src = 'var x = [1, 2, 3]\nprint(x)'
        with self.assertRaises(optc.ImporException) as cm:
            optc.run_source(src, "<l>")
        self.assertIn("BackendError", str(cm.exception))

    def test_try_rejected(self):
        src = '''
try { print(1) } catch e { print(2) }
'''
        with self.assertRaises(optc.ImporException):
            optc.run_source(src, "<t>")

    def test_global_scope_rejected(self):
        src = '''
def f() { global set g = 1 }
f()
'''
        with self.assertRaises(optc.ImporException) as cm:
            optc.run_source(src, "<g>")
        self.assertIn("global", str(cm.exception))

    def test_too_many_params_rejected(self):
        params = ", ".join("p%d" % i for i in range(optc.NUM_REGS + 1))
        src = "def many(%s) { return p0 }\nmany(%s)" % (
            params, ", ".join("0" for _ in range(optc.NUM_REGS + 1)))
        with self.assertRaises(optc.ImporException) as cm:
            optc.run_source(src, "<m>")
        self.assertIn("参数", str(cm.exception))


# ============================================================
# 进程级对拍：opt run 与 run 的 stdout 一致
# ============================================================
class TestSubprocessParity(unittest.TestCase):
    def test_opt_matches_interpreter(self):
        d = tempfile.mkdtemp(prefix="optpar_")
        try:
            p = os.path.join(d, "t.impor")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(CORE + "\n" + FIB)
            a = subprocess.run([PY, IMPOR, "run", "--no-native", p],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=120, cwd=ROOT)
            b = subprocess.run([PY, IMPOR, "opt", "run", p],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=120, cwd=ROOT)
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertEqual(
                (a.stdout or "").replace("\r\n", "\n"),
                (b.stdout or "").replace("\r\n", "\n"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_dump_to_file(self):
        d = tempfile.mkdtemp(prefix="optdump_")
        try:
            p = os.path.join(d, "t.impor")
            out = os.path.join(d, "t.asm")
            with open(p, "w", encoding="utf-8") as f:
                f.write(FIB)
            r = subprocess.run([PY, IMPOR, "opt", "dump", p, "-o", out],
                               capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(out))
            with open(out, encoding="utf-8") as fh:
                self.assertIn("R0", fh.read())
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
