# -*- coding: utf-8 -*-
"""原生 x64 后端（真 AOT，零外部工具链）回归测试。

锁定六件事：
1. x64asm 指令编码器：关键指令逐字节正确，REX/ModR/M/立即数与标签回填无误；
2. pe64 链接器：产出结构合法的 PE32+（MZ/PE 签名、节区、对齐、kernel32 导入）；
3. 标量类型推断：int/bool/str 判定、比较为 bool、跨函数实参前向传播；
4. 端到端：生成的 exe 真实运行，退出码 0 且输出正确（含递归、6 参数、深 spill）；
5. 与树遍历解释器逐字节对拍；
6. 能力边界（浮点/容器/内建/参数过多）编译期干净 BackendError，不写 crash.log。

整条链路不调用任何外部汇编器/链接器/编译器。
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from impor.x64asm import Asm, REG  # noqa: E402
from impor import pe64  # noqa: E402
from impor.native64 import (  # noqa: E402
    build_exe_from_source, infer_types, T_INT, T_BOOL, T_STR)
from impor.interpreter import (  # noqa: E402
    Interpreter, ImporException, strip_header, parse_source)
from impor.ir_lower import lower_source  # noqa: E402
from impor.irop import optimize_module  # noqa: E402


# ============================================================
# 辅助
# ============================================================
def asm_bytes(fn):
    a = Asm()
    fn(a)
    return a.finish()


def build_module(source):
    module = lower_source(source, "<test>")
    optimize_module(module)
    return module


def interp_bytes(source):
    outs = []
    Interpreter(log_cb=lambda t: outs.append(t)).run(
        parse_source(strip_header(source), "t.impor"))
    return "".join(t + "\n" for t in outs).encode()


# ============================================================
# 1. 指令编码器逐字节对照
# ============================================================
class TestEncoderBytePatterns(unittest.TestCase):
    def test_mov_rr_basic(self):
        # mov rax, rbx -> 48 89 d8
        self.assertEqual(
            asm_bytes(lambda a: a.mov_rr(REG["rax"], REG["rbx"])),
            b"\x48\x89\xd8")

    def test_mov_rr_high_number_rex(self):
        # mov r12, rax（高编号目标需 REX.B）-> 49 89 c4
        self.assertEqual(
            asm_bytes(lambda a: a.mov_rr(REG["r12"], REG["rax"])),
            b"\x49\x89\xc4")

    def test_push_pop(self):
        # push rbx -> 53；pop rdi -> 5f
        self.assertEqual(asm_bytes(lambda a: a.push(REG["rbx"])), b"\x53")
        self.assertEqual(asm_bytes(lambda a: a.pop(REG["rdi"])), b"\x5f")

    def test_alu_ri_imm8(self):
        # add rax, 5 -> 48 83 c0 05
        self.assertEqual(
            asm_bytes(lambda a: a.alu_ri("add", REG["rax"], 5)),
            b"\x48\x83\xc0\x05")

    def test_alu_ri_negative_imm8(self):
        # add rax, -128（合法有符号 imm8）-> 48 83 c0 80
        self.assertEqual(
            asm_bytes(lambda a: a.alu_ri("add", REG["rax"], -128)),
            b"\x48\x83\xc0\x80")

    def test_alu_ri_128_forces_imm32(self):
        # 回归：sub rsp,128 必须走 imm32。旧 bug 把 128 塞进 imm8，
        # 字节 0x80 被硬件解释成 -128，栈帧方向反掉导致退出访问违例。
        # 正确：48 81 ec 80 00 00 00（7 字节），而非 48 83 ec 80。
        self.assertEqual(
            asm_bytes(lambda a: a.alu_ri("sub", REG["rsp"], 128)),
            b"\x48\x81\xec\x80\x00\x00\x00")

    def test_imm8_imm32_boundary(self):
        # 127 仍走 imm8；128 起走 imm32
        self.assertEqual(
            asm_bytes(lambda a: a.alu_ri("add", REG["rax"], 127)),
            b"\x48\x83\xc0\x7f")
        self.assertEqual(
            asm_bytes(lambda a: a.alu_ri("add", REG["rax"], 128)),
            b"\x48\x81\xc0\x80\x00\x00\x00")

    def test_nop_ret(self):
        self.assertEqual(asm_bytes(lambda a: a.nop()), b"\x90")
        self.assertEqual(asm_bytes(lambda a: a.ret()), b"\xc3")

    def test_setcc(self):
        # sete al -> 0f 94 c0
        self.assertEqual(
            asm_bytes(lambda a: a.setcc("je", REG["rax"])),
            b"\x0f\x94\xc0")

    def test_udiv(self):
        # div r10（高编号需 REX.B）-> 49 f7 f2
        self.assertEqual(
            asm_bytes(lambda a: a.udiv(REG["r10"])),
            b"\x49\xf7\xf2")

    def test_test_rr(self):
        # test rax,rax -> 48 85 c0
        self.assertEqual(
            asm_bytes(lambda a: a.test_rr(REG["rax"], REG["rax"])),
            b"\x48\x85\xc0")

    def test_internal_label_fixup(self):
        def build(a):
            a.jmp("L")
            a.label("L")
            a.nop()
        # e9 <rel32=0> 90
        self.assertEqual(asm_bytes(build), b"\xe9\x00\x00\x00\x00\x90")

    def test_undefined_symbol_raises(self):
        def build(a):
            a.jmp("missing")
        with self.assertRaises(ValueError):
            asm_bytes(build)


# ============================================================
# 2. PE32+ 链接器结构
# ============================================================
class TestLinkerStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = build_exe_from_source("print(1)", "t.impor")

    def test_mz_signature(self):
        self.assertEqual(self.exe[:2], b"MZ")

    def test_pe_signature_and_machine(self):
        pe_off = struct.unpack_from("<I", self.exe, 0x3C)[0]
        self.assertEqual(self.exe[pe_off:pe_off + 4], b"PE\0\0")
        # Machine = AMD64 (0x8664)，紧跟 PE 签名
        self.assertEqual(
            struct.unpack_from("<H", self.exe, pe_off + 4)[0], 0x8664)

    def test_optional_header_magic(self):
        pe_off = struct.unpack_from("<I", self.exe, 0x3C)[0]
        # COFF 头 20 字节后是可选头 Magic
        self.assertEqual(
            struct.unpack_from("<H", self.exe, pe_off + 24)[0], 0x20B)

    def test_section_names(self):
        self.assertIn(b".text", self.exe)
        self.assertIn(b".rdata", self.exe)

    def test_file_alignment(self):
        self.assertEqual(len(self.exe) % pe64.FILE_ALIGN, 0)

    def test_kernel32_imports_present(self):
        self.assertIn(b"kernel32.dll", self.exe)
        self.assertIn(b"ExitProcess", self.exe)
        self.assertIn(b"WriteFile", self.exe)
        self.assertIn(b"GetStdHandle", self.exe)


# ============================================================
# 3. 类型推断
# ============================================================
class TestTypeInference(unittest.TestCase):
    def test_fib_param_and_return_int(self):
        src = (
            "def fib(n){ if n<2 { return n } "
            "return fib(n-1)+fib(n-2) }\nprint(fib(5))")
        envs, ret = infer_types(build_module(src))
        self.assertEqual(envs["fib"]["n"], T_INT)
        self.assertEqual(ret["fib"], T_INT)

    def test_comparison_is_bool(self):
        envs, _ = infer_types(build_module("var x = 3\nvar y = x > 1"))
        self.assertEqual(envs["__main__"]["y"], T_BOOL)

    def test_string_var(self):
        envs, _ = infer_types(build_module('var s = "hi"'))
        self.assertEqual(envs["__main__"]["s"], T_STR)

    def test_int_var(self):
        envs, _ = infer_types(build_module("var x = 42"))
        self.assertEqual(envs["__main__"]["x"], T_INT)


# ============================================================
# 4 & 5. 端到端真实运行 + 与解释器对拍
# ============================================================
FIB_SRC = (
    "def fib(n){ if n<2 { return n } "
    "return fib(n-1)+fib(n-2) }\n")

DEEP_SRC = "".join(
    "var v%d = %d\n" % (i, i) for i in range(1, 13)) + \
    "print(" + "+".join("v%d" % i for i in range(1, 13)) + ")\n"


class TestEndToEndRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="native64_")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def run_exe(self, source):
        exe = os.path.join(self.dir, "t.exe")
        with open(exe, "wb") as f:
            f.write(build_exe_from_source(source, "t.impor"))
        return subprocess.run([exe], capture_output=True, timeout=60)

    def _check(self, source, expected):
        r = self.run_exe(source)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, expected)

    def test_arithmetic(self):
        self._check("print(1 + 2 * 3)", b"7\n")

    def test_control_flow(self):
        self._check(
            "var i=0\nvar s=0\nwhile i<10 { s=s+i\n i=i+1 }\nprint(s)",
            b"45\n")

    def test_if_else(self):
        self._check(
            "var x=7\nif x>5 { print(100) } else { print(200) }",
            b"100\n")

    def test_fib_recursion(self):
        self._check(FIB_SRC + "print(fib(20))", b"6765\n")

    def test_six_stack_arguments(self):
        self._check(
            "def f(a,b,c,d,e,f){ return a+b+c+d+e+f }"
            "\nprint(f(1,2,3,4,5,6))",
            b"21\n")

    def test_deep_spill(self):
        # 关键回归：work_size 达到/超过 128，退出不得访问违例
        r = self.run_exe(DEEP_SRC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, b"78\n")

    def test_negative_and_int64_min(self):
        self._check("print(-42)", b"-42\n")
        self._check(
            "print(-9223372036854775808)", b"-9223372036854775808\n")

    def test_boolean_and_string(self):
        self._check('print(true)\nprint(false)', b"True\nFalse\n")
        self._check('print("hello world")', b"hello world\n")


class TestByteParityWithInterpreter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="native64par_")
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def _parity(self, source):
        exe = os.path.join(self.dir, "t.exe")
        with open(exe, "wb") as f:
            f.write(build_exe_from_source(source, "t.impor"))
        r = subprocess.run([exe], capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, interp_bytes(source))

    def test_mixed_scalar_program(self):
        self._parity(
            "print(17 % 5)\nprint(2 == 2)\nprint(3 > 4)\n"
            "print(1 < 2 and 5 >= 5)\nprint(not false)")

    def test_fib_and_loop(self):
        self._parity(FIB_SRC + "print(fib(15))\n"
                     "var i=0\nvar s=0\nwhile i<50 { s=s+i\n i=i+1 }\n"
                     "print(s)")

    def test_deep_spill_parity(self):
        self._parity(DEEP_SRC)


# ============================================================
# 6. 能力边界：干净拒绝，不写 crash.log
# ============================================================
class TestRejections(unittest.TestCase):
    def _assert_backend_error(self, source):
        with self.assertRaises(ImporException) as cm:
            build_exe_from_source(source, "t.impor")
        self.assertEqual(cm.exception.name, "BackendError")

    def test_float_rejected(self):
        self._assert_backend_error("print(1.5)")

    def test_float_division_rejected(self):
        self._assert_backend_error("print(3 / 2)")

    def test_list_rejected(self):
        self._assert_backend_error("var a=[1,2,3]\nprint(a)")

    def test_index_rejected(self):
        self._assert_backend_error('var s="abc"\nprint(s[0])')

    def test_builtin_rejected(self):
        self._assert_backend_error('print(len("abc"))')

    def test_too_many_params_rejected(self):
        params = ", ".join("p%d" % i for i in range(7))
        args = ", ".join("0" for _ in range(7))
        self._assert_backend_error(
            "def f(%s){ return p0 }\nprint(f(%s))" % (params, args))

    def test_no_crash_log_written(self):
        from impor.stability import crash_log_path
        cp = crash_log_path()
        before = os.path.getmtime(cp) if os.path.exists(cp) else None
        for src in ("print(1.5)", "var a=[1]\nprint(a)",
                    'print(len("x"))'):
            try:
                build_exe_from_source(src, "t.impor")
            except ImporException:
                pass
        after = os.path.getmtime(cp) if os.path.exists(cp) else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
