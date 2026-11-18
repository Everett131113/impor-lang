# -*- coding: utf-8 -*-
"""v2.17.0 回归测试：
① C 后端数组支持：ListLit / 下标读写 / 12 个数组内建（解释器语义一致）
② 新关键字：const 只读常量（赋值报错）、switch（match 别名，含 default 兜底）
③ 放宽语法：数字下划线 1_000_000
④ 游戏示例 examples/game_2048.impor 冒烟（解释器 + C 后端 exe）
"""
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from impor.interpreter import Interpreter, parse_source
from impor import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_impor(src, fname="t.impor"):
    out = []
    interp = Interpreter(log_cb=out.append, filename=fname)
    interp.run(parse_source(src, filename=fname))
    return out


def build_exe(src):
    """C 后端编译 → exe，返回 (rc, exe_path, run_out)。"""
    # v3.8：某些安全软件会把 %TEMP% 下新生成的 exe 误判并隔离/删除；
    # 项目内临时目录可稳定运行，测试结束再删除。
    tmpdir = tempfile.mkdtemp(prefix="impor_t36_", dir=ROOT)
    fpath = os.path.join(tmpdir, "game.impor")
    exe = os.path.join(tmpdir, "game.exe")
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(src)
        rc = cli.cmd_build([fpath, "-o", exe])
        if rc != cli.EXIT_OK or not os.path.isfile(exe):
            return rc, None, ""
        # 刚嵌入图标的 exe 可能被 Windows Defender 短暂扫描锁定（甚至短暂消失），重试
        last = None
        for attempt in range(5):
            try:
                if not os.path.isfile(exe):
                    raise FileNotFoundError(exe)
                r = subprocess.run([exe], capture_output=True, text=True, timeout=60)
                return rc, exe, r.stdout
            except (PermissionError, FileNotFoundError) as exc:
                last = exc
                time.sleep(2)
        raise last
    finally:
        try:
            os.unlink(fpath)
        except OSError:
            pass
        if os.path.isdir(tmpdir):
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except OSError:
                pass


class TestCBackendArrays(unittest.TestCase):
    """C 后端数组：解释器与 exe 输出一致。"""

    SRC = (
        'var a = [1, 2, 3]\n'
        'print("a[1] =", a[1])\n'
        'a[0] = 9\n'
        'print("set:", a)\n'
        'print("len:", len(a))\n'
        'var b = arrfill(4, 7)\n'
        'print("fill:", b)\n'
        'var c = arrappend(b, 8)\n'
        'print("append:", c)\n'
        'var p = arrpop(c)\n'
        'print("pop:", p, "rest:", c)\n'
        'print("find:", arrfind(c, 7))\n'
        'print("rev:", arrrev(c))\n'
        'print("dup:", arrdup(c))\n'
        'print("max/min/sum:", max_arr(c), min_arr(c), sum_arr(c))\n'
        'print("type:", type(a))\n'
    )

    def test_interp_arrays(self):
        out = run_impor(self.SRC)
        self.assertIn("a[1] = 2", out)
        self.assertIn("set: [9, 2, 3]", out)
        self.assertIn("fill: [7, 7, 7, 7]", out)
        self.assertIn("max/min/sum: 7 7 28", out)
        self.assertIn("type: list", out)

    def test_build_arrays(self):
        rc, exe, rout = build_exe(self.SRC)
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIsNotNone(exe)
        self.assertIn("a[1] = 2", rout)
        self.assertIn("set: [9, 2, 3]", rout)
        self.assertIn("max/min/sum: 7 7 28", rout)
        self.assertIn("type: list", rout)

    def test_list_builtin(self):
        out = run_impor('print(list(), list(3))')
        self.assertEqual(out, ["[] [0, 0, 0]"])
        rc, exe, rout = build_exe('print(list(), list(3))')
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("[] [0, 0, 0]", rout)


class TestConstKeyword(unittest.TestCase):
    """const 只读常量：正常声明 + 赋值报错。"""

    def test_const_ok(self):
        out = run_impor(
            'const 速度 = 100\n'
            'const MAX = 999\n'
            'print(速度, MAX)\n')
        self.assertEqual(out, ["100 999"])

    def test_const_readonly(self):
        with self.assertRaises(Exception) as ctx:
            run_impor('const x = 5\nx = 9\n')
        self.assertIn("不可修改", str(ctx.exception))

    def test_const_dup(self):
        with self.assertRaises(Exception) as ctx:
            run_impor('const x = 1\nconst x = 2\n')
        self.assertIn("重复声明", str(ctx.exception))

    def test_const_c_backend(self):
        rc, exe, rout = build_exe('const A = 10\nprint(A + 1)\n')
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("11", rout)


class TestSwitchKeyword(unittest.TestCase):
    """switch = match 别名；支持 case 与 default 兜底。"""

    SRC = (
        'var level = 2\n'
        'switch level {\n'
        '  case 1:\n'
        '    print("one")\n'
        '  case 2:\n'
        '    print("two")\n'
        '  default:\n'
        '    print("other")\n'
        '}\n'
    )

    def test_switch_interp(self):
        self.assertEqual(run_impor(self.SRC), ["two"])

    def test_switch_default(self):
        src = 'var x = 9\nswitch x {\n  case 1:\n    print("one")\n  default:\n    print("other")\n}\n'
        self.assertEqual(run_impor(src), ["other"])


class TestNumericUnderscore(unittest.TestCase):
    """数字下划线：1_000_000 → 1000000。"""

    def test_interp(self):
        out = run_impor('print(1_000_000, 10_000_000_000, 3.141_592)\n')
        self.assertEqual(out, ["1000000 10000000000 3.141592"])

    def test_c_backend(self):
        rc, exe, rout = build_exe('print(1_000_000 + 5)\n')
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIn("1000005", rout)


class TestGame2048Smoke(unittest.TestCase):
    """2048 游戏示例：解释器可跑、C 后端可编译成 exe 独立运行。"""

    GAME = os.path.join(ROOT, "examples", "game_2048.impor")

    def test_interp_smoke(self):
        from impor.cli import strip_header
        with open(self.GAME, encoding="utf-8") as f:
            src = f.read()
        out = run_impor(strip_header(src, warn=False))
        self.assertTrue(any("最高方块" in s for s in out))
        self.assertTrue(any("2048" in s for s in out))

    def test_build_smoke(self):
        rc, exe, rout = build_exe(open(self.GAME, encoding="utf-8").read())
        self.assertEqual(rc, cli.EXIT_OK)
        self.assertIsNotNone(exe)
        self.assertIn("最高方块", rout)
        self.assertIn("2048", rout)


if __name__ == "__main__":
    unittest.main()
