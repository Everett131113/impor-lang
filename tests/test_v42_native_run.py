# -*- coding: utf-8 -*-
"""v4.2 回归：`impor run --native` 透明原生执行 + `impor build --quiet`。

`--native` 的契约：命令、门禁、错误信息都不变，只是把“可静态编译”的脚本
编译成原生产物再执行；编译产物按源码/工具链指纹缓存，命中后不再付编译时间。
对拍口径与 `cmp_fast_vs_interp.py` 一致：同一份源码，解释器与原生路径 stdout 逐行一致。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
IMPOR = os.path.join(ROOT, "Impor.py")

from impor import cli


def _write(d, name, src):
    p = os.path.join(d, name)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    return p


def _run(script_args, timeout=120):
    p = subprocess.run([PY, IMPOR] + script_args, capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", cwd=ROOT)
    return p.returncode, p.stdout, p.stderr


NUM_SRC = (
    'var s = 0\n'
    'for i in range(0, 1000, 3)\n'
    '    set s = s + i\n'
    'print("合计", s, "结束")\n'
    'print(s * 2)\n'
)

STR_SRC = (
    'var name = "Impor"\n'
    'print("你好,", name)\n'
    'var xs = [1, 2, 3]\n'
    'print(len(xs))\n'
)


class TestQuietBuild(unittest.TestCase):
    def test_quiet_build_prints_nothing(self):
        d = tempfile.mkdtemp(prefix="impor_v42q_", dir=os.path.join(ROOT, "tests"))
        try:
            src = _write(d, "q.impor", 'print(1 + 2)\n')
            exe = os.path.join(d, "q.exe")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_build([src, "-o", exe, "--quiet", "--no-cache"])
            self.assertEqual(rc, cli.EXIT_OK)
            self.assertEqual(buf.getvalue().strip(), "", "quiet 模式不能打印构建摘要")
            self.assertTrue(os.path.isfile(exe))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestNativeRun(unittest.TestCase):
    def test_native_matches_interpreter(self):
        d = tempfile.mkdtemp(prefix="impor_v42n_", dir=os.path.join(ROOT, "tests"))
        try:
            sp = _write(d, "n.impor", NUM_SRC)
            rc_i, out_i, _ = _run(["run", sp])
            rc_n, out_n, err_n = _run(["run", "--native", sp])
            self.assertEqual(rc_i, 0)
            self.assertEqual(rc_n, 0, err_n)
            self.assertEqual(out_n, out_i, "原生执行必须与解释器逐行一致")
            self.assertIn("合计", out_i)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_native_falls_back_for_non_numeric(self):
        d = tempfile.mkdtemp(prefix="impor_v42f_", dir=os.path.join(ROOT, "tests"))
        try:
            sp = _write(d, "s.impor", STR_SRC)
            rc_i, out_i, _ = _run(["run", sp])
            rc_n, out_n, err_n = _run(["run", "--native", sp])
            self.assertEqual(rc_n, 0, err_n)
            self.assertEqual(out_n, out_i, "子集外脚本应回落解释器且输出一致")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_native_cache_second_run(self):
        d = tempfile.mkdtemp(prefix="impor_v42c_", dir=os.path.join(ROOT, "tests"))
        try:
            sp = _write(d, "c.impor", 'print(6 * 7)\n')
            rc1, out1, err1 = _run(["run", "--native", sp])
            rc2, out2, err2 = _run(["run", "--native", sp])
            self.assertEqual(rc1, 0, err1)
            self.assertEqual(rc2, 0, err2)
            self.assertEqual(out1, "42\n")
            self.assertEqual(out2, "42\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestDefaultNativeAndDll(unittest.TestCase):
    """patch7：run 默认走原生；build --dll 产出可被启动器 LoadLibrary 的入口。"""

    def test_run_default_is_native_and_silent(self):
        d = tempfile.mkdtemp(prefix="impor_v42d_", dir=os.path.join(ROOT, "tests"))
        try:
            sp = _write(d, "d.impor", NUM_SRC)
            rc_i, out_i, _ = _run(["run", "--no-native", sp])
            rc_d, out_d, err_d = _run(["run", sp])
            self.assertEqual(rc_i, 0)
            self.assertEqual(rc_d, 0, err_d)
            self.assertEqual(out_d, out_i, "默认走原生必须与解释器逐行一致")
            self.assertNotIn("[native]", err_d, "默认路径不该每次刷 [native] 提示")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_run_default_falls_back_silently(self):
        d = tempfile.mkdtemp(prefix="impor_v42e_", dir=os.path.join(ROOT, "tests"))
        try:
            sp = _write(d, "e.impor", STR_SRC)
            rc_i, out_i, _ = _run(["run", "--no-native", sp])
            rc_d, out_d, err_d = _run(["run", sp])
            self.assertEqual(rc_d, 0, err_d)
            self.assertEqual(out_d, out_i, "子集外脚本默认也应静默回落解释器")
            self.assertNotIn("[native]", err_d)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_build_dll_has_entry_export(self):
        d = tempfile.mkdtemp(prefix="impor_v42x_", dir=os.path.join(ROOT, "tests"))
        try:
            src = _write(d, "x.impor", 'print(6 * 7)\n')
            dll = os.path.join(d, "x.dll")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_build([src, "-o", dll, "--dll", "--quiet", "--no-cache"])
            self.assertEqual(rc, cli.EXIT_OK)
            self.assertTrue(os.path.isfile(dll))
            with open(dll, "rb") as f:
                data = f.read()
            # PE 导出表以 ASCII 保存导出名；有名字就说明 impor_entry 真的可被 GetProcAddress 找到
            self.assertIn(b"impor_entry", data, "--dll 产物必须导出 impor_entry")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
