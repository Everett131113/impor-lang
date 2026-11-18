# -*- coding: utf-8 -*-
"""v3.9.x：FFI 上 C 后端 + C 源码包 + 两个实测体验伤。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMPOR_PY = os.path.join(ROOT, "Impor.py")
TCC = os.path.join(ROOT, "impor", "tcc", "win32", "tcc.exe")

LIB_SRC = """export func add(a: int, b: int) -> int {
    return a + b
}
"""


def _run(cmd, cwd, env=None, timeout=90):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=e)


class TestFfiCBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="impor_v39_ffi_")
        os.makedirs(os.path.join(self.tmp, ".impor"), exist_ok=True)
        with open(os.path.join(self.tmp, ".impor", "ffi_allow.json"),
                  "w", encoding="utf-8") as f:
            f.write('{"allow":["msvcrt.dll","lib.dll"]}')
        self.lib = os.path.join(self.tmp, "lib.impor")
        with open(self.lib, "w", encoding="utf-8") as f:
            f.write(LIB_SRC)
        self.dll = os.path.join(self.tmp, "lib.dll")
        p = _run([sys.executable, IMPOR_PY, "build", "--lib", self.lib,
                  "-o", self.dll], ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_exe(self, name, src):
        path = os.path.join(self.tmp, name + ".impor")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        exe = os.path.join(self.tmp, name + ".exe")
        p = _run([sys.executable, IMPOR_PY, "build", path, "-o", exe,
                  "--no-cache"], ROOT)
        return p, exe

    def _run_exe(self, exe):
        return _run([exe], self.tmp)

    def test_strlen_c_backend(self):
        p, exe = self._build_exe(
            "strlen",
            'var lib = ffi_open("msvcrt.dll")\n'
            'print(ffi_call(lib, "strlen", ["abc"], "int", ["cstr"]))\n')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self._run_exe(exe)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "3")

    def test_cross_language_add(self):
        p, exe = self._build_exe(
            "add",
            'var lib = ffi_open("lib.dll")\n'
            'print(ffi_call(lib, "impor_add", [2, 3], "int", ["int", "int"]))\n')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self._run_exe(exe)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "5")

    def test_unregistered_library_is_build_error(self):
        p, _ = self._build_exe(
            "bad",
            'var lib = ffi_open("nope.dll")\n'
            'print(ffi_call(lib, "foo", [], "int", []))\n')
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("未登记", p.stdout + p.stderr)

    def test_more_than_eight_args_is_build_error(self):
        p, _ = self._build_exe(
            "nine",
            'var lib = ffi_open("msvcrt.dll")\n'
            'print(ffi_call(lib, "foo", [1,2,3,4,5,6,7,8,9], "int", '
            '["int","int","int","int","int","int","int","int","int"]))\n')
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("最多支持 8 个参数", p.stdout + p.stderr)

    def test_semicolon_allowlist_is_accepted(self):
        sysroot = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                               "System32", "msvcrt.dll")
        if not os.path.isfile(sysroot):
            self.skipTest("找不到 System32\\msvcrt.dll")
        p, _ = self._build_exe(
            "msvcrt64",
            'var lib = ffi_open("%s")\n'
            'print(ffi_call(lib, "strlen", ["abc"], "int", ["cstr"]))\n'
            % sysroot.replace("\\", "\\\\"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = _run([sys.executable, IMPOR_PY, "run",
                  os.path.join(self.tmp, "msvcrt64.impor")],
                 ROOT, env={"IMPOR_FFI_ALLOW": "msvcrt.dll;lib.dll"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "3")

    def test_width_mismatch_is_explicit(self):
        if sys.maxsize <= 2 ** 32:
            self.skipTest("32 位 Python 可直接加载 32 位 lib.dll")
        with open(os.path.join(self.tmp, "width.impor"), "w",
                  encoding="utf-8") as f:
            f.write('var lib = ffi_open("lib.dll")\n'
                    'print(ffi_call(lib, "impor_add", [2, 3], "int", '
                    '["int", "int"]))\n')
        p = _run([sys.executable, IMPOR_PY, "run",
                  os.path.join(self.tmp, "width.impor")],
                 ROOT, env={"IMPOR_FFI_ALLOW": "msvcrt.dll;lib.dll"})
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        text = p.stdout + p.stderr
        self.assertIn("FFIArchError", text)
        self.assertIn("位宽不匹配", text)
        self.assertIn("32 位", text)

    def test_c_source_package(self):
        out = os.path.join(self.tmp, "csrc")
        p = _run([sys.executable, IMPOR_PY, "build", "--lib", "--c-source",
                  self.lib, "-o", out], ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for name in ("lib.c", "lib.h", "lib.def",
                     "build_msvc.bat", "build_gcc.bat"):
            self.assertTrue(os.path.isfile(os.path.join(out, name)), name)
        with open(os.path.join(out, "lib.h"), encoding="utf-8") as f:
            h = f.read()
        self.assertIn("32 位", h)
        self.assertIn("gcc -m64", h)
        with open(os.path.join(out, "lib.c"), encoding="utf-8") as f:
            c = f.read()
        self.assertNotIn("int main", c)
        # 用 tcc 自检 lib.c 至少能编译（32 位），x64 需要外部 cl/gcc。
        p = _run([TCC, "-shared", os.path.join(out, "lib.c"),
                  "-o", os.path.join(out, "lib_tcc.dll")], ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_build_lib_prints_width(self):
        p = _run([sys.executable, IMPOR_PY, "build", "--lib", self.lib,
                  "-o", self.dll, "--no-cache"], ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("产物位宽：32 位(i386)", p.stdout)


if __name__ == "__main__":
    unittest.main()
