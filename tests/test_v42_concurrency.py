# -*- coding: utf-8 -*-
"""v4.2 回归：线程 / 互斥锁 / 原子量（解释器 + Python 转译 + C 后端三端一致）。

口径（impor/rt_concurrency.py 顶部注释）：
- 句柄是整数；atomic_add/sub/exchange 返回旧值；thread_join 返回线程函数结果；
- 原子量只保证单个 64 位整数不撕裂，容器共享必须自己用 mutex 包住。

关键回归点：
1. 解释器不是重入的（cur_env / call_stack 是实例属性）→ thread_spawn 必须给
   每个线程一份执行副本，否则表现为「变量 x 未定义」（本文件 test_thread_local_scope）。
2. C 后端 try/raise 的跳转栈原来是全局的 → 已改成线程局部，否则两线程各自 try 会乱跳。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor import cli
from impor.interpreter import (Interpreter, ImporRuntimeError, strip_header,
                               parse_source)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 4 线程各 500 次原子自增 + mutex 保护的普通自增（非原子写），要求精确等于 2000
SRC = (
    'var lock = mutex_new()\n'
    'var hits = atomic_new(0)\n'
    'var plain = 0\n'
    'def work(n) {\n'
    '    var i = 0\n'
    '    while i < n {\n'
    '        atomic_add(hits, 1)\n'
    '        mutex_lock(lock)\n'
    '        set plain = plain + 1\n'
    '        mutex_unlock(lock)\n'
    '        i += 1\n'
    '    }\n'
    '    return n\n'
    '}\n'
    'def make_job(n) {\n'
    '    def job() { return work(n) }\n'
    '    return job\n'
    '}\n'
    'var hs = []\n'
    'var k = 0\n'
    'while k < 4 {\n'
    '    arrappend(hs, thread_spawn(make_job(500)))\n'
    '    k += 1\n'
    '}\n'
    'var total = 0\n'
    'for h in hs { total += thread_join(h) }\n'
    'print("joined:", total)\n'
    'print("atomic:", atomic_load(hits))\n'
    'print("plain:", plain)\n'
    'print("cas:", atomic_cas(hits, 2000, 2001), atomic_load(hits))\n'
)
EXPECTED = ["joined: 2000", "atomic: 2000", "plain: 2000", "cas: True 2001"]

# 线程里读闭包捕获的参数：解释器副本必须拿对环境
SCOPE_SRC = (
    'def make_job(n) {\n'
    '    def job() { return n }\n'
    '    return job\n'
    '}\n'
    'var h = thread_spawn(make_job(7))\n'
    'print("inner:", thread_join(h))\n'
    'def plain() { return 42 }\n'
    'print("plain:", thread_join(thread_spawn(plain)))\n'
)
SCOPE_EXPECTED = ["inner: 7", "plain: 42"]


def run_interp(src, name="v42conc"):
    out = []
    Interpreter(log_cb=out.append, filename=name).run(
        parse_source(strip_header(src), filename=name))
    return out


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v42conc_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    py = os.path.join(d, "case.py")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_compile([sp, "-o", py])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([sys.executable, py], capture_output=True, text=True, timeout=90)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_c(src):
    d = tempfile.mkdtemp(prefix="impor_v42conc_c_", dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe])
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=90)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestThreadMatchesName(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(SRC), EXPECTED)

    def test_thread_local_scope(self):
        self.assertEqual(run_interp(SCOPE_SRC), SCOPE_EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)


class TestConcurrencyEdgeCases(unittest.TestCase):
    def test_atomic_returns_old_value(self):
        # 口径：add/sub/exchange 均返回**旧值**（对齐 C 的 fetch-and-add）
        out = run_interp(
            'var a = atomic_new(10)\n'
            'print(atomic_add(a, 5))\n'      # 旧值 10，现值 15
            'print(atomic_load(a))\n'        # 15
            'print(atomic_sub(a, 20))\n'     # 旧值 15，现值 -5
            'print(atomic_exchange(a, 3))\n' # 旧值 -5，现值 3
            'print(atomic_load(a))\n')       # 3
        self.assertEqual(out, ["10", "15", "15", "-5", "3"])

    def test_unknown_handle_reports(self):
        # 句柄校验失败由运行库抛 KeyError，解释器统一包装成 ImporRuntimeError
        with self.assertRaises(ImporRuntimeError) as cm:
            run_interp('thread_join(999)\n')
        self.assertIn("999", str(cm.exception))

    def test_cpu_count_positive(self):
        out = run_interp('print(cpu_count() > 0)\n')
        self.assertEqual(out, ["True"])


class TestNewStdModules(unittest.TestCase):
    def test_sort_and_stats(self):
        out = run_interp(
            'use std.sort\n'
            'use std.stats\n'
            'var a = sort([5, 2, 9, 1])\n'
            'print(a)\n'
            'print(search(a, 9), search(a, 7))\n'
            'print(mean([2, 4, 6]), median([1, 3, 5, 7]))\n'
            'print(percentile([1, 2, 3, 4], 50))\n')
        self.assertEqual(out, ["[1, 2, 5, 9]", "3 -1", "4.0 4.0", "2.5"])

    def test_thread_module_wrappers(self):
        out = run_interp(
            'use std.thread\n'
            'use std.atomic\n'
            'var c = counter(0)\n'
            'def bump(n) {\n'
            '    var i = 0\n'
            '    while i < n {\n'
            '        add(c, 1)\n'
            '        i += 1\n'
            '    }\n'
            '    return n\n'
            '}\n'
            'def make(n) { def job() { return bump(n) }\n return job }\n'
            'print(join(spawn(make(100))), load(c))\n')
        self.assertEqual(out, ["100 100"])


if __name__ == "__main__":
    unittest.main()
