# -*- coding: utf-8 -*-
"""v3.3 回归：跨后端一致性修复（Unicode 字符语义 / int64 溢出 / 语义对齐 /
递归深度 / 内存口径 / C 编译错误反查）。"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python"
IMPOR = os.path.join(ROOT, "Impor.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")


def _write(text):
    d = tempfile.mkdtemp(prefix="v33_")
    p = os.path.join(d, "t.impor")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return d, p


def run(src, build=False):
    d, p = _write(src)
    try:
        cmd = [PY, IMPOR, "build" if build else "run", p]
        if build:
            cmd += ["-o", os.path.join(d, "t.exe")]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=ROOT)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_built(src):
    """build 成独立 exe 后运行，返回 (rc, stdout, stderr)。编译失败返回 BUILD_FAIL。"""
    d, p = _write(src)
    try:
        exe = os.path.join(d, "t.exe")
        b = subprocess.run([PY, IMPOR, "build", p, "-o", exe],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=ROOT)
        if not os.path.isfile(exe):
            return "BUILD_FAIL", "", (b.stdout or "") + (b.stderr or "")
        r = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, cwd=ROOT)
        return (r.returncode,
                (r.stdout or "").replace("\r\n", "\n"),
                (r.stderr or "").replace("\r\n", "\n"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestUnicodeSemantics(unittest.TestCase):
    def test_len_counts_characters(self):
        rc, out, err = run('var s = "你好"\nprint(len(s))\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "2")

    def test_index_returns_character(self):
        rc, out, err = run('var s = "你好"\nprint(s[1])\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "好")

    def test_strcut_by_char(self):
        rc, out, err = run('print(strcut("你好世界", 1, 3))\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "好世")

    def test_literal_index(self):
        rc, out, err = run('print("你好"[1])\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "好")


class TestSemanticAlignment(unittest.TestCase):
    def test_int_equals_true(self):
        rc, out, err = run("print(1 == true)\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "True")

    def test_str_repeat(self):
        rc, out, err = run('print("ab" * 3)\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "ababab")

    def test_str_plus_number_is_error(self):
        rc, out, err = run('print(1 + "a")\n')
        self.assertNotEqual(rc, 0, '1 + "a" 应报类型错误，实际通过')
        self.assertIn("TypeError", out + err)


class TestRecursionDepth(unittest.TestCase):
    def test_1000_levels(self):
        src = ('def f(n)\n    if n <= 0\n        return 0\n    return f(n - 1) + 1\n'
               'print(f(1000))\n')
        rc, out, err = run(src)
        self.assertEqual(rc, 0, "1000 层递归失败：" + (out + err)[-800:])
        self.assertEqual(out.strip(), "1000")

    def test_too_deep_gives_diagnostic(self):
        src = 'def f(n)\n    return f(n + 1)\nprint(f(0))\n'
        rc, out, err = run(src)
        self.assertNotEqual(rc, 0)
        self.assertIn("递归过深", out + err)


class TestMemoryReport(unittest.TestCase):
    def test_mem_explains_containers(self):
        rc, out, err = run('var a = [1, 2, 3]\nprint(mem())\ngc()\n')
        self.assertEqual(rc, 0, err)
        self.assertIn("引用计数", out)
        self.assertIn("循环引用", out)


@unittest.skipUnless(os.path.isfile(TCC), "缺少 tcc")
class TestCDiagnostic(unittest.TestCase):
    def test_render_c_error_maps_to_impor_line(self):
        from impor.cli import _render_c_error
        c_code = ("/*@L1*/\nint main(void) {\n"
                  "/*@L3*/\n  int x = ;\n"
                  "  return 0;\n}\n")
        d = tempfile.mkdtemp(prefix="tcc_")
        try:
            cpath = os.path.join(d, "a.c")
            with io.open(cpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(c_code)
            r = subprocess.run([TCC, cpath, "-o", os.path.join(d, "a.exe")],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, "构造的非法 C 应编译失败")
            msg = _render_c_error(r.stderr or r.stdout, c_code,
                                  ["var a = 1", "", "undefined(1)"], 0)
            self.assertIn("第3行", msg)
            self.assertIn("^", msg)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_overflow_reported_c_backend(self):
        rc, out, err = run_built("print(9223372036854775807 + 1)\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("整数溢出", out + err)


class TestCBackendParity(unittest.TestCase):
    def test_unicode_c_backend(self):
        rc, out, err = run_built('var s = "你好"\nprint(len(s))\nprint(s[1])\n')
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip().splitlines(), ["2", "好"])


if __name__ == "__main__":
    unittest.main()
