# -*- coding: utf-8 -*-
"""patch8 回归：P2（`impor serve` 常驻）与 P3（完整 C 后端接管 run）。

三条底线：
1. `impor run` 对“快路径不认”的脚本（字符串/列表/函数）现在走完整 C 后端，
   输出必须与解释器逐字节一致，且 stderr 不得出现 [native] 提示；
2. `--no-native` 仍是解释器语义，作为参照；
3. `impor serve` 在同一个连接上连续请求，输出一致、退出码一致。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

GENERAL_SCRIPT = """var name = "Impor"
var xs = [1, 2, 3]
def add(a, b)
    return a + b
var total = 0
for i in range(2000)
    set total = add(total, i)
print("hi", name, len(xs), total)
"""


def _run_cli(args, timeout=300):
    return subprocess.run([sys.executable, "Impor.py"] + args, cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


class TestFullBackendRun(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(ROOT, "_v43_general.impor")
        with open(self.tmp, "w", encoding="utf-8") as f:
            f.write(GENERAL_SCRIPT)

    def tearDown(self):
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def test_general_script_same_output_and_silent(self):
        a = _run_cli(["run", self.tmp])
        b = _run_cli(["run", "--no-native", self.tmp])
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(a.stdout, b.stdout)
        self.assertNotIn("[native]", a.stderr)

    def test_exits_with_same_code_on_runtime_error(self):
        bad = os.path.join(ROOT, "_v43_bad.impor")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('print("a" + 1)\n')
        try:
            a = _run_cli(["run", bad])
            b = _run_cli(["run", "--no-native", bad])
            self.assertEqual(a.returncode, b.returncode)
            self.assertNotEqual(a.returncode, 0)
        finally:
            try:
                os.unlink(bad)
            except OSError:
                pass


class TestServe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from impor import serve as s
        cls.serve_mod = s
        cls.port = 0
        cls.ready = threading.Event()

        def _cb(p):
            cls.port = p
            cls.ready.set()

        cls.thr = threading.Thread(target=s.serve, kwargs={"port": 0, "ready_cb": _cb},
                                   daemon=True)
        cls.thr.start()
        cls.ready.wait(10)

    def _request(self, script, args=()):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=120)
        try:
            s.sendall(json.dumps({"script": script, "args": list(args)},
                                 ensure_ascii=False).encode("utf-8") + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
            return json.loads(data.decode("utf-8"))
        finally:
            s.close()

    def test_twice_same_output(self):
        tmp = os.path.join(ROOT, "_v43_serve.impor")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("var s = 0\nfor i in range(200000)\n    set s = s + i\nprint(s)\n")
        try:
            r1 = self._request(tmp)
            r2 = self._request(tmp)
            self.assertEqual(r1["code"], 0, r1)
            self.assertEqual(r1["out"].strip(), "19999900000")
            self.assertEqual(r2["out"], r1["out"])
            self.assertEqual(r2["tier"], r1["tier"])
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def test_general_script_tier_and_output(self):
        tmp = os.path.join(ROOT, "_v43_serve_gen.impor")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(GENERAL_SCRIPT)
        try:
            r = self._request(tmp)
            self.assertEqual(r["code"], 0, r)
            self.assertIn("hi Impor 3", r["out"])
            self.assertIn(r["tier"], ("dll", "full", "interp"))
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
