# -*- coding: utf-8 -*-
"""v4.3 回归：std.channel / std.pool（解释器 + Python 转译 + C 后端 64 位/32 位 四端一致）。

口径（impor/std/channel.impor 顶部注释）：
- cap <= 0 无界；cap > 0 有界，满了发送方自旋；
- recv 在「已关闭且已排空」时返回 ch.empty()，用 ch.is_empty(v) 判断；
- 通道是 mutex 保护的队列 + 原子计数 + sleep 轮询，不是 Go 那种调度器级阻塞；
- 线程池 p 是整数句柄，任务按「谁先取到谁执行」分派，不保证公平。

本文件同时守着两条 C 运行时的真实缺陷（修前必崩/必错）：
1. rt_dict_set / rt_arr_set 原来「先 free 再 dup」——值本身就在槽里时读到已释放内存
   （表现为 d["k"] = arrappend(d["k"], x) 里 len 变成 0，偶发 0xC0000005）。
2. C 后端把闭包捕获的容器按值复制——把通道 dict 直接传进线程会看到创建时的快照，
   所以 std/pool.impor 统一走模块级注册表 + 整数句柄。
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
from impor.interpreter import Interpreter, strip_header, parse_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 生产/消费 30 条 + 4 worker 分 20 个任务；worker 之间如何分不保证，只比总数
SRC = (
    'import std.channel as ch\n'
    'import std.pool as pool\n'
    'var c = ch.make(2)\n'
    'def produce() {\n'
    '    var i = 0\n'
    '    while i < 30 {\n'
    '        ch.send(c, i)\n'
    '        i += 1\n'
    '    }\n'
    '    ch.close(c)\n'
    '    return 30\n'
    '}\n'
    'def consume() {\n'
    '    var n = 0\n'
    '    var v = ch.recv(c)\n'
    '    while not ch.is_empty(v) {\n'
    '        set n = n + 1\n'
    '        set v = ch.recv(c)\n'
    '    }\n'
    '    return n\n'
    '}\n'
    'var hp = thread_spawn(produce)\n'
    'var hc = thread_spawn(consume)\n'
    'print("sent:", thread_join(hp))\n'
    'print("recv:", thread_join(hc))\n'
    'print("stats:", ch.stats(c))\n'
    'var p = pool.new_pool(4)\n'
    'var hits = atomic_new(0)\n'
    'def job() {\n'
    '    atomic_add(hits, 1)\n'
    '    return 1\n'
    '}\n'
    'var k = 0\n'
    'while k < 20 {\n'
    '    pool.submit(p, job)\n'
    '    k += 1\n'
    '}\n'
    'var counts = pool.shutdown(p)\n'
    'var sum = 0\n'
    'for n in counts {\n'
    '    set sum = sum + n\n'
    '}\n'
    'print("workers:", len(counts), "sum:", sum, "hits:", atomic_load(hits))\n'
    'print("after:", pool.submit(p, job), pool.pending(p))\n'
)
EXPECTED = ["sent: 30", "recv: 30", "stats: [30, 30, 0]",
            "workers: 4 sum: 20 hits: 20", "after: False 0"]


def run_interp(src, name="v43chan"):
    out = []
    Interpreter(log_cb=out.append, filename=name).run(
        parse_source(strip_header(src), filename=name))
    return out


def _run_built(src, prefix, extra_args):
    d = tempfile.mkdtemp(prefix=prefix, dir=os.path.join(ROOT, "tests"))
    sp = os.path.join(d, "case.impor")
    exe = os.path.join(d, "case.exe")
    try:
        with io.open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        rc = cli.cmd_build([sp, "-o", exe] + extra_args)
        if rc != cli.EXIT_OK:
            return rc, ""
        r = subprocess.run([exe], capture_output=True, text=True, timeout=90)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_pytrans(src):
    d = tempfile.mkdtemp(prefix="impor_v43chan_py_", dir=os.path.join(ROOT, "tests"))
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
    return _run_built(src, "impor_v43chan_c_", [])


def run_tcc(src):
    return _run_built(src, "impor_v43chan_tcc_", ["--cc", "tcc"])


class TestChannelPoolFourBackends(unittest.TestCase):
    def test_interpreter(self):
        self.assertEqual(run_interp(SRC), EXPECTED)

    def test_python_transpile(self):
        rc, out = run_pytrans(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_c_backend(self):
        rc, out = run_c(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)

    def test_tcc_32bit(self):
        rc, out = run_tcc(SRC)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(), EXPECTED)


class TestChannelEdgeCases(unittest.TestCase):
    def test_closed_send_returns_false(self):
        out = run_interp(
            'import std.channel as ch\n'
            'var c = ch.make(1)\n'
            'print(ch.send(c, 1))\n'
            'ch.close(c)\n'
            'print(ch.send(c, 2))\n'
            'print(ch.is_closed(c))\n')
        self.assertEqual(out, ["True", "False", "True"])

    def test_empty_sentinel_is_not_nil(self):
        # recv 关闭且排空 → empty()，且 empty() 是普通字符串，可作比较
        out = run_interp(
            'import std.channel as ch\n'
            'var c = ch.make(0)\n'
            'ch.close(c)\n'
            'var v = ch.recv(c)\n'
            'print(ch.is_empty(v), v == ch.empty())\n')
        self.assertEqual(out, ["True True"])

    def test_try_recv_pair_and_drain(self):
        out = run_interp(
            'import std.channel as ch\n'
            'var c = ch.make(0)\n'
            'ch.send(c, 7)\n'
            'var r = ch.try_recv(c)\n'
            'print(r[0], r[1])\n'
            'ch.send(c, 8)\n'
            'ch.close(c)\n'
            'print(ch.drain(c))\n'
            'print(ch.stats(c))\n')
        # stats: sends 2 / recvs 2（try_recv 一次 + drain 一次）/ size 0
        self.assertEqual(out, ["True 7", "[8]", "[2, 2, 0]"])

    def test_bounded_cap_blocks_sender(self):
        # cap=1：消费者慢，发送方必须自旋等待，不会丢消息
        out = run_interp(
            'import std.channel as ch\n'
            'var c = ch.make(1)\n'
            'def producer() {\n'
            '    var i = 0\n'
            '    while i < 5 {\n'
            '        ch.send(c, i)\n'
            '        i += 1\n'
            '    }\n'
            '    ch.close(c)\n'
            '    return 5\n'
            '}\n'
            'def consumer() {\n'
            '    var n = 0\n'
            '    var v = ch.recv(c)\n'
            '    while not ch.is_empty(v) {\n'
            '        sleep(0.01)\n'
            '        set n = n + 1\n'
            '        set v = ch.recv(c)\n'
            '    }\n'
            '    return n\n'
            '}\n'
            'var hp = thread_spawn(producer)\n'
            'var hc = thread_spawn(consumer)\n'
            'print(thread_join(hp), thread_join(hc), ch.size(c))\n')
        self.assertEqual(out, ["5 5 0"])

    def test_pool_bad_handle_is_safe(self):
        out = run_interp(
            'import std.pool as pool\n'
            'print(pool.submit(99, 1), pool.pending(0))\n'
            'print(pool.shutdown(99))\n')
        self.assertEqual(out, ["False 0", "[]"])


if __name__ == "__main__":
    unittest.main()
