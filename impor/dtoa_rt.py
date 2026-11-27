# -*- coding: utf-8 -*-
"""Impor 原生 dtoa 运行时（最短往返 double -> 十进制字符串）。

把已验证的多精度参考算法（tools/dtoa_ref.py 的 Burger-Dybvig 最短往返，
机器码友好版见临时 dtoa_mp.py：固定 N 个 u64 limb，仅用可移植原语）
逐行发射成 x86-64 机器码，作为 native64 可调用的运行时子程序。

调用约定（Win64）：
  入口 RCX = double 的 64 位表示（bit pattern），RDX = 输出缓冲区指针；
  出口 RAX = 字符串长度（不含终止 \\0），字节写入缓冲区并补一个 \\0。

多精度原语子程序（__mp_*）只破坏易失寄存器 rax/rcx/rdx/r8..r11，
不碰非易失寄存器与调用方栈帧，便于主逻辑把长期状态放在非易失寄存器。
"""
from .x64asm import REG

# 多精度参数：N 个 u64 limb，limb[0] 为最低位，内存连续
N = 20
MP_BYTES = N * 8              # 160

# 运行时子程序标签
LBL_DTOA = "__rt_dtoa"
LBL_ZERO = "__mp_zero"
LBL_SET = "__mp_set"
LBL_COPY = "__mp_copy"
LBL_MUL10 = "__mp_mul10"
LBL_ADD = "__mp_add"
LBL_SUB = "__mp_sub"
LBL_CMP = "__mp_cmp"
LBL_ISZERO = "__mp_iszero"
LBL_SHL = "__mp_shl"
LBL_POW10 = "__mp_pow10"
LBL_FINDP = "__mp_findp"

# rdata 固定字符串符号（含 \0）
S_NAN = "__d_nan"
S_INF = "__d_inf"
S_NINF = "__d_ninf"
S_ZERO = "__d_zero"
S_NZERO = "__d_nzero"

RDATA_STRINGS = {
    S_NAN: b"nan\0",
    S_INF: b"inf\0",
    S_NINF: b"-inf\0",
    S_ZERO: b"0.0\0",
    S_NZERO: b"-0.0\0",
}

# 易失寄存器编号
RAX, RCX, RDX, RSI, RDI = (REG["rax"], REG["rcx"], REG["rdx"],
                           REG["rsi"], REG["rdi"])
R8, R9, R10, R11 = REG["r8"], REG["r9"], REG["r10"], REG["r11"]
# 非易失寄存器编号
RBX, R12, R13, R14, R15 = (REG["rbx"], REG["r12"], REG["r13"],
                            REG["r14"], REG["r15"])


def _align16(v):
    return (v + 15) & ~15


class Layout:
    """计算 dtoa 帧内各对象相对 rbp 的（负）偏移。"""

    def __init__(self):
        self.off = 0
        self.syms = {}

    def take(self, name, size):
        start = -(self.off + size)
        self.off += size
        self.syms[name] = start
        return start

    @property
    def frame_size(self):
        return _align16(self.off)


class DtoaRt:
    def __init__(self, asm):
        self.a = asm
        self.layout = Layout()
        self._define_layout()

    # ---------- 栈布局（主逻辑使用） ----------
    def _define_layout(self):
        L = self.layout
        # 6 个多精度数组：主算法 r/s/ml/mr + 两个临时
        for nm in ("R", "S", "ML", "MR", "TA", "TB"):
            L.take(nm, MP_BYTES)
        # 标量槽（每个 8 字节）
        for nm in ("F", "E", "P", "Q", "K", "I", "EVEN", "POW2",
                   "SIGN", "D", "NDIG", "BUF", "GECNT", "GESAVE"):
            L.take(nm, 8)
        # 数字字节缓冲（最多 17 位十进制数字）
        L.take("DIG", 24)

    def d(self, name):
        return self.layout.syms[name]

    def emit(self):
        self._prim_zero()
        self._prim_set()
        self._prim_copy()
        self._prim_mul10()
        self._prim_add()
        self._prim_sub()
        self._prim_cmp()
        self._prim_iszero()
        self._prim_shl()
        self._prim_pow10()
        self._emit_dtoa()

    # ============================================================
    # 多精度原语
    # ============================================================
    def _prim_zero(self):
        # rdi = arr；清零 N 个 u64
        a = self.a
        a.label(LBL_ZERO)
        a.xor_rr(RAX, RAX)
        a.mov_imm32(RCX, N)
        a.rep_stosq()
        a.ret()

    def _prim_set(self):
        # rdi = arr, rsi = u64；最低 limb = u64，其余清零
        a = self.a
        a.label(LBL_SET)
        a.store(RSI, RDI, 0)
        a.xor_rr(RAX, RAX)
        a.lea_mem(RDI, RDI, 8)
        a.mov_imm32(RCX, N - 1)
        a.rep_stosq()
        a.ret()

    def _prim_copy(self):
        # rdi = dst, rsi = src
        a = self.a
        a.label(LBL_COPY)
        a.mov_imm32(RCX, N)
        a.rep_movsq()
        a.ret()

    def _prim_mul10(self):
        # rdi = arr；从低到高，limb*10 + 进位
        a = self.a
        a.label(LBL_MUL10)
        a.xor_rr(R8, R8)                       # carry = 0
        a.mov_imm32(R9, N)
        a.label("__mp_m10_l")
        a.load(RAX, RDI, 0)
        a.mov_imm32(RCX, 10)
        a.mul_rr(RCX)                          # rdx:rax = limb*10
        a.alu_rr("add", RAX, R8)               # + 进位
        a.alu_ri("adc", RDX, 0)
        a.store(RAX, RDI, 0)
        a.mov_rr(R8, RDX)                      # 新进位
        a.alu_ri("add", RDI, 8)
        a.alu_ri("sub", R9, 1)
        a.jcc("jne", "__mp_m10_l")
        a.ret()

    def _prim_add(self):
        # rdi = a, rsi = b；a += b。指针用 lea、计数用 loop，均不破坏 CF。
        a = self.a
        a.label(LBL_ADD)
        a.raw(b"\xF8")                         # clc
        a.mov_imm32(RCX, N)
        a.label("__mp_add_l")
        a.load(RAX, RSI, 0)
        a.alu_mem("adc", RAX, RDI, 0)
        a.lea_mem(RDI, RDI, 8)
        a.lea_mem(RSI, RSI, 8)
        a.loop_rel8("__mp_add_l")
        a.ret()

    def _prim_sub(self):
        # rdi = a, rsi = b；a -= b（调用方保证 a >= b）。
        a = self.a
        a.label(LBL_SUB)
        a.raw(b"\xF8")                         # clc
        a.mov_imm32(RCX, N)
        a.label("__mp_sub_l")
        a.load(RAX, RSI, 0)
        a.alu_mem("sbb", RAX, RDI, 0)
        a.lea_mem(RDI, RDI, 8)
        a.lea_mem(RSI, RSI, 8)
        a.loop_rel8("__mp_sub_l")
        a.ret()

    def _prim_cmp(self):
        # rdi = a, rsi = b；从高到低，返回 rax = 1 / 0 / -1
        a = self.a
        a.label(LBL_CMP)
        a.lea_mem(RDI, RDI, (N - 1) * 8)
        a.lea_mem(RSI, RSI, (N - 1) * 8)
        a.mov_imm32(R9, N)
        a.label("__mp_cmp_l")
        a.load(RAX, RDI, 0)
        a.load(RCX, RSI, 0)
        a.alu_rr("cmp", RAX, RCX)
        a.jcc("ja", "__mp_cmp_gt")
        a.jcc("jb", "__mp_cmp_lt")
        a.alu_ri("sub", RDI, 8)
        a.alu_ri("sub", RSI, 8)
        a.alu_ri("sub", R9, 1)
        a.jcc("jne", "__mp_cmp_l")
        a.xor_rr(RAX, RAX)
        a.ret()
        a.label("__mp_cmp_gt")
        a.mov_imm32(RAX, 1)
        a.ret()
        a.label("__mp_cmp_lt")
        a.mov_imm64(RAX, -1)
        a.ret()

    def _prim_iszero(self):
        # rdi = arr；全 0 返回 1，否则 0
        a = self.a
        a.label(LBL_ISZERO)
        a.mov_imm32(R9, N)
        a.xor_rr(R8, R8)
        a.label("__mp_isz_l")
        a.load(RAX, RDI, 0)
        a.alu_rr("or", R8, RAX)
        a.alu_ri("add", RDI, 8)
        a.alu_ri("sub", R9, 1)
        a.jcc("jne", "__mp_isz_l")
        a.xor_rr(RAX, RAX)
        a.test_rr(R8, R8)
        a.setcc("je", RAX)
        a.ret()

    def _prim_shl(self):
        # rdi = arr, rsi = k；整体左移 k 位（任意 k）
        a = self.a
        a.label(LBL_SHL)
        a.test_rr(RSI, RSI)
        a.jcc("je", "__mp_shl_done")
        a.mov_rr(R8, RSI)
        a.mov_rr(R9, RSI)
        a.shift_imm("shr", R8, 6)              # r8 = ls = k >> 6
        a.mov_imm32(RCX, 63)
        a.alu_rr("and", R9, RCX)               # r9 = bs = k & 63
        # 第一步：整体向高移动 ls 个 limb
        a.mov_imm32(R10, N)
        a.alu_rr("sub", R10, R8)               # count = N - ls
        a.lea_mem(R11, RDI, (N - 1) * 8)       # dstp = arr + 152
        a.mov_rr(RAX, R11)
        a.mov_rr(RCX, R8)
        a.shift_imm("shl", RCX, 3)             # ls*8
        a.alu_rr("sub", RAX, RCX)              # srcp = dstp - ls*8
        a.mov_rr(RCX, RAX)
        a.label("__mp_shl_ml")
        a.load(RAX, RCX, 0)
        a.store(RAX, R11, 0)
        a.alu_ri("sub", R11, 8)
        a.alu_ri("sub", RCX, 8)
        a.alu_ri("sub", R10, 1)
        a.jcc("jne", "__mp_shl_ml")
        # 低 ls 个 limb 清零
        a.test_rr(R8, R8)
        a.jcc("je", "__mp_shl_bp")
        a.mov_rr(R11, RDI)
        a.mov_rr(R10, R8)
        a.label("__mp_shl_z")
        a.xor_rr(RAX, RAX)
        a.store(RAX, R11, 0)
        a.alu_ri("add", R11, 8)
        a.alu_ri("sub", R10, 1)
        a.jcc("jne", "__mp_shl_z")
        a.label("__mp_shl_bp")
        a.test_rr(R9, R9)
        a.jcc("je", "__mp_shl_done")
        # 第二步：每个目标 limb 独立从相邻低 limb 计算（原地，从高到低时
        # 低 limb 尚未被修改，读到的是第一步的值）。
        # r9 = bs，r10 = 64-bs，r8 = ls，r11 = 目标指针，rdx = 计数
        a.mov_imm32(R10, 64)
        a.alu_rr("sub", R10, R9)               # 64 - bs
        a.lea_mem(R11, RDI, (N - 1) * 8)
        a.mov_imm32(RDX, N)
        a.alu_rr("sub", RDX, R8)               # count = N - ls
        a.label("__mp_shl_bl")
        a.mov_rr(RCX, R9)
        a.load(RAX, R11, 0)                    # v = a[i]
        a.shift_cl("shl", RAX)                 # v << bs
        a.mov_rr(RCX, R10)
        # 最低 limb（R11==arr）不越界读，low 视为 0；否则读 a[i-1]
        a.mov_rr(RSI, R11); a.alu_rr("cmp", RSI, RDI)
        a.jcc("je", "__mp_shl_zlow")
        a.load(RSI, R11, -8)                   # low = a[i-1]
        a.shift_cl("shr", RSI)                 # low >> (64-bs)
        a.jmp("__mp_shl_comb")
        a.label("__mp_shl_zlow")
        a.xor_rr(RSI, RSI)
        a.label("__mp_shl_comb")
        a.alu_rr("or", RAX, RSI)
        a.store(RAX, R11, 0)
        a.alu_ri("sub", R11, 8)
        a.alu_ri("sub", RDX, 1)
        a.jcc("jne", "__mp_shl_bl")
        a.label("__mp_shl_done")
        a.ret()

    def _prim_pow10(self):
        # rdi = arr, rsi = k；arr = 10^k。入口 rsp ≡ 8。
        a = self.a
        rbp = REG["rbp"]
        a.label(LBL_POW10)
        a.push(rbp)                          # ≡0
        a.mov_rr(rbp, REG["rsp"])
        a.push(RDI)                          # rbp-8 = arr   ≡8
        a.push(RSI)                          # rbp-16 = k     ≡0
        a.mov_imm32(RSI, 1)
        a.call(LBL_SET)                      # 入口 ≡8
        a.load(R8, rbp, -16)                 # r8 = k
        a.test_rr(R8, R8)
        a.jcc("je", "__mp_pow10_done")
        a.label("__mp_pow10_l")              # rsp ≡0
        a.load(RDI, rbp, -8)                 # rdi = arr（每轮重置）
        a.push(R8)                           # ≡8
        a.sub_rsp(8)                         # ≡0
        a.call(LBL_MUL10)                    # 入口 ≡8
        a.add_rsp(8)                         # ≡8
        a.pop(R8)                            # ≡0
        a.alu_ri("sub", R8, 1)
        a.jcc("jne", "__mp_pow10_l")
        a.label("__mp_pow10_done")
        a.raw(b"\xC9")                       # leave
        a.ret()

    # ============================================================
    # 发射 helper（约定：原语调用破坏 rax/rcx/rdx/rsi/rdi/r8..r11；
    # rbx=输出写指针，r12=bits，r13=f，r14=e，r15=digit 写指针）
    # ============================================================
    def _mp1(self, fn, arr):
        a = self.a
        a.lea_mem(RDI, REG["rbp"], self.d(arr)); a.call(fn)

    def _mp1_imm(self, fn, arr, val):
        a = self.a
        a.lea_mem(RDI, REG["rbp"], self.d(arr))
        a.mov_imm32(RSI, val); a.call(fn)

    def _mp1_reg(self, fn, arr, reg):
        a = self.a
        a.lea_mem(RDI, REG["rbp"], self.d(arr))
        a.mov_rr(RSI, reg); a.call(fn)

    def _mp2(self, fn, a_arr, b_arr):
        a = self.a
        a.lea_mem(RDI, REG["rbp"], self.d(a_arr))
        a.lea_mem(RSI, REG["rbp"], self.d(b_arr)); a.call(fn)

    def _copy_str(self, sym, n):
        a = self.a
        a.lea_rip(RSI, sym); a.mov_rr(RDI, RBX)
        a.mov_imm32(RCX, n); a.rep_movsb()
        a.mov_rr(RBX, RDI)

    def _putc_imm(self, c):
        a = self.a
        a.mov_imm32(RAX, c); a.store_byte(RAX, RBX, 0)
        a.alu_ri("add", RBX, 1)

    def _putc_reg(self, reg):
        a = self.a
        a.store_byte(reg, RBX, 0); a.alu_ri("add", RBX, 1)

    def _putdigit_reg(self, reg):
        a = self.a
        a.alu_ri("add", reg, 48); a.store_byte(reg, RBX, 0)
        a.alu_ri("add", RBX, 1)

    # ============================================================
    # dtoa 主逻辑
    # ============================================================
    def _emit_dtoa(self):
        a = self.a
        rbp = REG["rbp"]
        rsp = REG["rsp"]

        # ---- prologue ----
        a.label(LBL_DTOA)
        a.push(rbp)
        a.mov_rr(rbp, rsp)
        for r in (RBX, R12, R13, R14, R15, RDI):
            a.push(r)
        a.sub_rsp(self.layout.frame_size)
        a.store(RDX, rbp, self.d("BUF"))
        a.mov_rr(RBX, RDX)                 # 输出写指针
        a.mov_rr(R12, RCX)                 # bits

        # ---- 特殊值检测 ----
        a.mov_imm64(RAX, 0x7FFFFFFFFFFFFFFF)
        a.alu_rr("and", RAX, R12)
        a.mov_rr(RDX, RAX)                 # absbits
        a.test_rr(RAX, RAX)
        a.jcc("je", "__d_zero")
        a.mov_rr(RAX, R12)
        a.shift_imm("shr", RAX, 52)
        a.mov_imm32(RCX, 0x7FF)
        a.alu_rr("and", RAX, RCX)
        a.mov_rr(RCX, RAX)
        a.mov_imm32(RAX, 0x7FF)
        a.alu_rr("cmp", RCX, RAX)
        a.jcc("je", "__d_naninf")
        a.jmp("__d_normal")

        # ---- 零 ----
        a.label("__d_zero")
        a.mov_rr(RAX, R12)
        a.shift_imm("shr", RAX, 63)
        a.test_rr(RAX, RAX)
        a.jcc("jne", "__d_zneg")
        self._copy_str(S_ZERO, 3)
        a.jmp("__d_done")
        a.label("__d_zneg")
        self._copy_str(S_NZERO, 4)
        a.jmp("__d_done")

        # ---- NaN / inf ----
        a.label("__d_naninf")
        a.mov_rr(RAX, R12)
        a.mov_imm64(RCX, 0xFFFFFFFFFFFFF)
        a.alu_rr("and", RAX, RCX)
        a.test_rr(RAX, RAX)
        a.jcc("jne", "__d_nan")
        a.mov_rr(RAX, R12)
        a.shift_imm("shr", RAX, 63)
        a.test_rr(RAX, RAX)
        a.jcc("jne", "__d_ninf")
        self._copy_str(S_INF, 3)
        a.jmp("__d_done")
        a.label("__d_ninf")
        self._copy_str(S_NINF, 4)
        a.jmp("__d_done")
        a.label("__d_nan")
        self._copy_str(S_NAN, 3)
        a.jmp("__d_done")

        # ---- 正常数：分解出 f/e ----
        a.label("__d_normal")
        a.mov_rr(RAX, R12)
        a.shift_imm("shr", RAX, 52)
        a.mov_imm32(RCX, 0x7FF)
        a.alu_rr("and", RAX, RCX)
        a.mov_rr(R15, RAX)                 # R15 暂存 ef
        a.mov_rr(RAX, R12)
        a.mov_imm64(RCX, 0xFFFFFFFFFFFFF)
        a.alu_rr("and", RAX, RCX)           # RAX = mant
        a.test_rr(R15, R15)
        a.jcc("je", "__d_subn")
        a.mov_imm64(RCX, 1 << 52)
        a.alu_rr("or", RAX, RCX)
        a.mov_rr(R13, RAX)                 # f
        a.mov_rr(R14, R15)
        a.alu_ri("sub", R14, 1075)         # e = ef - 1075
        a.jmp("__d_havefe")
        a.label("__d_subn")
        a.mov_rr(R13, RAX)                 # f = mant
        a.mov_imm64(R14, -1074)
        a.label("__d_havefe")
        # 【诊断】decompose 后 R13 → F
        a.store(R13, rbp, self.d("F"))
        # 符号
        a.mov_rr(RAX, R12)
        a.shift_imm("shr", RAX, 63)
        a.store(RAX, rbp, self.d("SIGN"))
        # is_pow2：spec 且 f == 2^52
        a.xor_rr(RAX, RAX)
        a.test_rr(R15, R15)
        a.jcc("je", "__d_notpow")
        a.mov_imm64(RCX, 1 << 52)
        a.mov_rr(RAX, R13)
        a.alu_rr("cmp", RAX, RCX)
        a.jcc("jne", "__d_notpow")
        a.mov_imm32(RAX, 1)
        a.label("__d_notpow")
        a.store(RAX, rbp, self.d("POW2"))

        # ---- find_p（内联） ----
        # TA = vn = f
        self._mp1_reg(LBL_SET, "TA", R13)
        # TB = vd = 1
        self._mp1_imm(LBL_SET, "TB", 1)
        a.mov_rr(RAX, R14)
        a.test_rr(RAX, RAX)
        a.jcc("js", "__fp_neg")
        self._mp1_reg(LBL_SHL, "TA", R14)
        a.jmp("__fp_corr")
        a.label("__fp_neg")
        a.mov_rr(RSI, R14)
        a.neg(RSI)
        self._mp1_reg(LBL_SHL, "TB", RSI)
        a.label("__fp_corr")
        # p 只依赖 R13/R14，放在 shl 之后计算，避免 shl 破坏 R10
        a.mov_rr(RAX, R13)
        a.bsr_rr(RCX, RAX)                 # 最高位索引
        a.alu_ri("add", RCX, 1)
        a.mov_rr(RAX, R14)
        a.alu_rr("add", RAX, RCX)
        a.alu_ri("sub", RAX, 1)            # E
        a.mov_rr(R8, RAX)
        a.mov_imm32(R9, 64656993)
        a.imul_rr(R8, R9)
        a.shift_imm("sar", R8, 31)
        a.mov_rr(R10, R8)                 # R10 = p
        # while not ge(p): p -= 1
        a.label("__fp_lo")
        self._fp_ge(rbp, rsp, "A", R10)
        a.test_rr(R11, R11)
        a.jcc("jne", "__fp_lo_keep")
        a.alu_ri("sub", R10, 1)
        a.label("__fp_lo_keep")
        # while ge(p+1): p += 1
        a.mov_rr(R8, R10)
        a.alu_ri("add", R8, 1)
        self._fp_ge(rbp, rsp, "B", R8)
        a.test_rr(R11, R11)
        a.jcc("je", "__fp_done")
        a.mov_rr(R10, R8)
        a.jmp("__fp_lo_keep")
        a.label("__fp_done")
        a.store(R10, rbp, self.d("P"))

        # ---- 初始化 r/s/ml/mr（四分支） ----
        a.mov_rr(RAX, R14)
        a.test_rr(RAX, RAX)
        a.jcc("js", "__ini_en")
        a.load(RAX, rbp, self.d("POW2"))
        a.test_rr(RAX, RAX)
        a.jcc("je", "__ini_ep0")
        # e >= 0, 2 的幂
        self._mp1_reg(LBL_SET, "R", R13)
        a.mov_rr(RSI, R14); a.alu_ri("add", RSI, 2)
        self._mp1_reg(LBL_SHL, "R", RSI)
        self._mp1_imm(LBL_SET, "S", 4)
        self._mp1_imm(LBL_SET, "ML", 1)
        self._mp1_reg(LBL_SHL, "ML", R14)
        self._mp1_imm(LBL_SET, "MR", 1)
        a.mov_rr(RSI, R14); a.alu_ri("add", RSI, 1)
        self._mp1_reg(LBL_SHL, "MR", RSI)
        a.jmp("__ini_done")
        a.label("__ini_ep0")
        # e >= 0, 普通
        self._mp1_reg(LBL_SET, "R", R13)
        a.mov_rr(RSI, R14); a.alu_ri("add", RSI, 1)
        self._mp1_reg(LBL_SHL, "R", RSI)
        self._mp1_imm(LBL_SET, "S", 2)
        self._mp1_imm(LBL_SET, "ML", 1)
        self._mp1_reg(LBL_SHL, "ML", R14)
        self._mp2(LBL_COPY, "MR", "ML")
        a.jmp("__ini_done")
        a.label("__ini_en")
        a.load(RAX, rbp, self.d("POW2"))
        a.test_rr(RAX, RAX)
        a.jcc("je", "__ini_en0")
        # e < 0, 2 的幂
        self._mp1_reg(LBL_SET, "R", R13)
        a.mov_imm64(RSI, 2)
        self._mp1_reg(LBL_SHL, "R", RSI)
        self._mp1_imm(LBL_SET, "S", 1)
        a.mov_rr(RSI, R14); a.neg(RSI); a.alu_ri("add", RSI, 2)
        self._mp1_reg(LBL_SHL, "S", RSI)
        self._mp1_imm(LBL_SET, "ML", 1)
        self._mp1_imm(LBL_SET, "MR", 2)
        a.jmp("__ini_done")
        a.label("__ini_en0")
        # e < 0, 普通
        self._mp1_reg(LBL_SET, "R", R13)
        # 【诊断】set 后 R[0] → E
        a.load(RAX, rbp, self.d("R")); a.store(RAX, rbp, self.d("E"))
        a.mov_imm64(RSI, 1)
        self._mp1_reg(LBL_SHL, "R", RSI)
        # 【诊断】shl 后 R[0] → Q
        a.load(RAX, rbp, self.d("R")); a.store(RAX, rbp, self.d("Q"))
        self._mp1_imm(LBL_SET, "S", 1)
        a.mov_rr(RSI, R14); a.neg(RSI); a.alu_ri("add", RSI, 1)
        self._mp1_reg(LBL_SHL, "S", RSI)
        self._mp1_imm(LBL_SET, "ML", 1)
        self._mp1_imm(LBL_SET, "MR", 1)
        a.label("__ini_done")
        a.load(RAX, rbp, self.d("S")); a.store(RAX, rbp, self.d("EVEN"))  # EVEN: ini_done S[0]

        # ---- q = p+1 缩放（计数存 GECNT，mul10 会破坏 r9） ----
        a.load(R10, rbp, self.d("P"))
        a.mov_rr(R8, R10); a.alu_ri("add", R8, 1)   # q = p+1
        a.store(R8, rbp, self.d("GECNT"))
        a.load(RAX, rbp, self.d("GECNT"))
        a.test_rr(RAX, RAX)
        a.jcc("js", "__q_neg")
        # q >= 0: s *= 10^q
        a.label("__q_pos_l")
        a.load(RAX, rbp, self.d("GECNT"))
        a.test_rr(RAX, RAX); a.jcc("je", "__q_done")
        self._mp1(LBL_MUL10, "S")
        a.load(RAX, rbp, self.d("GECNT"))
        a.alu_ri("sub", RAX, 1); a.store(RAX, rbp, self.d("GECNT"))
        a.jmp("__q_pos_l")
        a.label("__q_neg")
        # 工作计数 = -q
        a.load(RAX, rbp, self.d("GECNT")); a.neg(RAX)
        a.store(RAX, rbp, self.d("GECNT"))
        a.label("__q_neg_l")
        a.load(RAX, rbp, self.d("GECNT"))
        a.test_rr(RAX, RAX); a.jcc("je", "__q_done")
        self._mp1(LBL_MUL10, "R")
        self._mp1(LBL_MUL10, "ML")
        self._mp1(LBL_MUL10, "MR")
        a.load(RAX, rbp, self.d("GECNT"))
        a.alu_ri("sub", RAX, 1); a.store(RAX, rbp, self.d("GECNT"))
        a.jmp("__q_neg_l")
        a.label("__q_done")

        # ---- digit 写指针 ----
        a.lea_mem(R15, rbp, self.d("DIG"))
        a.mov_imm32(RAX, 0); a.store(RAX, rbp, self.d("K"))   # 一次性标志

        # ---- 主循环 ----
        a.label("__ml_top")
        self._mp1(LBL_MUL10, "R")
        self._mp1(LBL_MUL10, "ML")
        self._mp1(LBL_MUL10, "MR")
        a.mov_imm32(RAX, 0)
        a.store(RAX, rbp, self.d("D"))
        # 【诊断】mul10 后、试商前 R[0] → Q
        a.load(RAX, rbp, self.d("R")); a.store(RAX, rbp, self.d("Q"))
        # 试商 d = r//s
        a.label("__ml_div")
        self._mp2(LBL_CMP, "R", "S")
        a.test_rr(RAX, RAX)
        a.jcc("jl", "__ml_divdone")
        self._mp2(LBL_SUB, "R", "S")
        a.load(RAX, rbp, self.d("D"))
        a.alu_ri("add", RAX, 1)
        a.store(RAX, rbp, self.d("D"))
        a.jmp("__ml_div")
        a.label("__ml_divdone")
        # even = (f & 1) == 0
        a.mov_rr(RAX, R13); a.mov_imm32(RCX, 1)
        a.alu_rr("and", RAX, RCX)
        a.xor_rr(RCX, RCX)
        a.test_rr(RAX, RAX); a.setcc("je", RCX)
        a.store(RCX, rbp, self.d("EVEN"))
        # low
        self._mp2(LBL_CMP, "R", "ML")
        a.mov_rr(RCX, RAX)
        a.load(RDX, rbp, self.d("EVEN"))
        a.test_rr(RDX, RDX); a.jcc("je", "__low_odd")
        a.xor_rr(R10, R10); a.test_rr(RCX, RCX)
        a.setcc("jle", R10)
        a.jmp("__low_done")
        a.label("__low_odd")
        a.xor_rr(R10, R10); a.test_rr(RCX, RCX)
        a.setcc("jl", R10)
        a.label("__low_done")
        # high：TB = s - r
        self._mp2(LBL_COPY, "TB", "S")
        self._mp2(LBL_SUB, "TB", "R")
        self._mp2(LBL_CMP, "TB", "MR")
        a.mov_rr(RCX, RAX)
        a.load(RDX, rbp, self.d("EVEN"))
        a.test_rr(RDX, RDX); a.jcc("je", "__high_odd")
        a.xor_rr(R11, R11); a.test_rr(RCX, RCX)
        a.setcc("jle", R11)
        a.jmp("__high_done")
        a.label("__high_odd")
        a.xor_rr(R11, R11); a.test_rr(RCX, RCX)
        a.setcc("jl", R11)
        a.label("__high_done")
        # 【诊断】仅第一轮：试商后 R[0]→I，low→NDIG，high→GESAVE
        a.load(RAX, rbp, self.d("K")); a.test_rr(RAX, RAX)
        a.jcc("jne", "__diag_skip")
        a.load(RAX, rbp, self.d("R")); a.store(RAX, rbp, self.d("I"))
        a.store(R10, rbp, self.d("NDIG"))
        a.store(R11, rbp, self.d("GESAVE"))
        a.mov_imm32(RAX, 1); a.store(RAX, rbp, self.d("K"))
        a.label("__diag_skip")
        # 都不满足：append d，继续
        a.mov_rr(RAX, R10); a.alu_rr("or", RAX, R11)
        a.test_rr(RAX, RAX); a.jcc("jne", "__ml_term")
        a.load(RAX, rbp, self.d("D"))
        a.alu_ri("add", RAX, 48)
        a.store_byte(RAX, R15, 0)
        a.alu_ri("add", R15, 1)
        a.jmp("__ml_top")

        # ---- 终止：选 final_d ----
        a.label("__ml_term")
        a.mov_rr(RAX, R10); a.test_rr(RAX, RAX)
        a.jcc("je", "__ml_notboth")
        a.mov_rr(RAX, R11); a.test_rr(RAX, RAX)
        a.jcc("je", "__ml_notboth")
        # both：TA = 2r，cmp TA, S
        self._mp2(LBL_COPY, "TA", "R")
        self._mp2(LBL_ADD, "TA", "R")
        self._mp2(LBL_CMP, "TA", "S")
        a.mov_rr(RCX, RAX)
        a.test_rr(RCX, RCX); a.jcc("jg", "__fd_up")
        a.test_rr(RCX, RCX); a.jcc("jne", "__fd_keep")
        a.load(RAX, rbp, self.d("D")); a.mov_imm32(RDX, 1)
        a.alu_rr("and", RAX, RDX)
        a.test_rr(RAX, RAX); a.jcc("jne", "__fd_up")
        a.jmp("__fd_keep")
        a.label("__ml_notboth")
        a.mov_rr(RAX, R11); a.test_rr(RAX, RAX)
        a.jcc("jne", "__fd_up")
        a.jmp("__fd_keep")
        a.label("__fd_up")
        a.load(RCX, rbp, self.d("D"))
        a.alu_ri("add", RCX, 1)
        a.store(RCX, rbp, self.d("D"))
        a.label("__fd_keep")
        a.load(RAX, rbp, self.d("D"))
        a.alu_ri("cmp", RAX, 10)
        a.jcc("jl", "__fd_append")
        a.jmp("__carry")
        a.label("__fd_append")
        a.load(RAX, rbp, self.d("D"))
        a.alu_ri("add", RAX, 48)
        a.store_byte(RAX, R15, 0)
        a.alu_ri("add", R15, 1)
        a.jmp("__render")

        # ---- 进位 ----
        a.label("__carry")
        a.mov_imm32(RAX, 57)                 # 先写 '9'
        a.store_byte(RAX, R15, 0)
        a.alu_ri("add", R15, 1)
        a.mov_rr(R10, R15); a.alu_ri("sub", R10, 1)
        a.lea_mem(R11, rbp, self.d("DIG"))
        a.label("__carry_l")
        a.load_byte(RAX, R10, 0)
        a.mov_imm32(RCX, 57)
        a.alu_rr("cmp", RAX, RCX)
        a.jcc("jne", "__carry_inc")
        a.mov_imm32(RAX, 48)
        a.store_byte(RAX, R10, 0)
        a.alu_ri("sub", R10, 1)
        a.mov_rr(RAX, R10); a.alu_rr("cmp", RAX, R11)
        a.jcc("jge", "__carry_l")
        # 溢出：整体后移，头插 '1'
        a.mov_rr(RSI, R15); a.alu_ri("sub", RSI, 1)
        a.label("__ov_l")
        a.load_byte(RAX, RSI, 0)
        a.mov_rr(RDX, RSI); a.alu_ri("add", RDX, 1)
        a.store_byte(RAX, RDX, 0)
        a.mov_rr(RAX, RSI); a.alu_rr("cmp", RAX, R11)
        a.jcc("je", "__ov_done")
        a.alu_ri("sub", RSI, 1)
        a.jmp("__ov_l")
        a.label("__ov_done")
        a.mov_imm32(RAX, 49)
        a.store_byte(RAX, R11, 0)
        a.alu_ri("add", R15, 1)
        a.load(RAX, rbp, self.d("P"))
        a.alu_ri("add", RAX, 1)
        a.store(RAX, rbp, self.d("P"))
        a.jmp("__carry_tail")
        a.label("__carry_inc")
        a.load_byte(RAX, R10, 0)
        a.alu_ri("add", RAX, 1)
        a.store_byte(RAX, R10, 0)
        a.label("__carry_tail")
        a.label("__ct_l")
        a.mov_rr(RAX, R15); a.alu_rr("sub", RAX, R11)
        a.alu_ri("cmp", RAX, 1)
        a.jcc("jle", "__ct_done")
        a.mov_rr(RSI, R15); a.alu_ri("sub", RSI, 1)
        a.load_byte(RAX, RSI, 0)
        a.mov_imm32(RCX, 48)
        a.alu_rr("cmp", RAX, RCX)
        a.jcc("jne", "__ct_done")
        a.alu_ri("sub", R15, 1)
        a.jmp("__ct_l")
        a.label("__ct_done")

        # ---- 渲染 ----
        a.label("__render")
        a.lea_mem(R11, rbp, self.d("DIG"))     # R11 = DIG 读指针
        a.load(RAX, rbp, self.d("SIGN"))
        a.test_rr(RAX, RAX); a.jcc("je", "__r_h")
        self._putc_imm(45)
        a.label("__r_h")
        a.load(R10, rbp, self.d("P"))
        a.mov_imm64(RCX, -4)
        a.mov_rr(RAX, R10); a.alu_rr("cmp", RAX, RCX)
        a.jcc("jl", "__r_sci")
        a.mov_imm32(RCX, 15)
        a.mov_rr(RAX, R10); a.alu_rr("cmp", RAX, RCX)
        a.jcc("jg", "__r_sci")
        a.jmp("__r_fixed")

        # 定点
        a.label("__r_fixed")
        a.mov_rr(RAX, R10); a.alu_ri("add", RAX, 1)
        a.mov_rr(R10, RAX)                 # k = p+1
        a.mov_rr(R8, R15); a.alu_rr("sub", R8, R11)   # ndig
        a.mov_rr(RAX, R10); a.test_rr(RAX, RAX)
        a.jcc("jle", "__f_path1")
        a.mov_rr(RAX, R10); a.alu_rr("cmp", RAX, R8)
        a.jcc("jge", "__f_path2")
        # path3：前 k 个，'.'，剩余
        a.mov_rr(R9, R10)
        a.label("__f3_a")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1)
        a.alu_ri("sub", R9, 1)
        a.jcc("jne", "__f3_a")
        self._putc_imm(46)
        a.label("__f3_b")
        a.mov_rr(RAX, R11); a.alu_rr("cmp", RAX, R15)
        a.jcc("jge", "__r_done")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1)
        a.jmp("__f3_b")
        # path1：0. + 前导0 + 全部
        a.label("__f_path1")
        self._putc_imm(48); self._putc_imm(46)
        a.mov_rr(R9, R10); a.neg(R9)
        a.label("__f1_z")
        a.test_rr(R9, R9); a.jcc("je", "__f1_d")
        self._putc_imm(48)
        a.alu_ri("sub", R9, 1); a.jmp("__f1_z")
        a.label("__f1_d")
        a.label("__f1_l")
        a.mov_rr(RAX, R11); a.alu_rr("cmp", RAX, R15)
        a.jcc("jge", "__r_done")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1); a.jmp("__f1_l")
        # path2：全部 + 补0 + .0
        a.label("__f_path2")
        a.label("__f2_l")
        a.mov_rr(RAX, R11); a.alu_rr("cmp", RAX, R15)
        a.jcc("jge", "__f2_zstart")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1); a.jmp("__f2_l")
        a.label("__f2_zstart")
        a.mov_rr(R9, R10); a.alu_rr("sub", R9, R8)
        a.label("__f2_z")
        a.test_rr(R9, R9); a.jcc("je", "__f2_dot")
        self._putc_imm(48)
        a.alu_ri("sub", R9, 1); a.jmp("__f2_z")
        a.label("__f2_dot")
        self._putc_imm(46); self._putc_imm(48)
        a.jmp("__r_done")

        # 科学
        a.label("__r_sci")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1)
        a.mov_rr(RAX, R11); a.alu_rr("cmp", RAX, R15)
        a.jcc("jge", "__s_exp")
        self._putc_imm(46)
        a.label("__s_l")
        a.mov_rr(RAX, R11); a.alu_rr("cmp", RAX, R15)
        a.jcc("jge", "__s_exp")
        a.load_byte(RAX, R11, 0)
        self._putc_reg(RAX)
        a.alu_ri("add", R11, 1); a.jmp("__s_l")
        a.label("__s_exp")
        self._putc_imm(101)
        a.load(R10, rbp, self.d("P"))
        a.mov_rr(RAX, R10); a.test_rr(RAX, RAX)
        a.jcc("jns", "__s_pos")
        self._putc_imm(45)
        a.jmp("__s_abs")
        a.label("__s_pos")
        self._putc_imm(43)
        a.label("__s_abs")
        a.mov_rr(RAX, R10); a.test_rr(RAX, RAX)
        a.jcc("jns", "__s_have")
        a.neg(RAX)
        a.label("__s_have")
        a.xor_rr(R9, R9)
        a.label("__s_dl")
        a.xor_rr(RDX, RDX)
        a.mov_imm32(R8, 10)
        a.udiv(R8)
        a.alu_ri("add", RDX, 48)
        a.push(RDX)
        a.alu_ri("add", R9, 1)
        a.test_rr(RAX, RAX); a.jcc("jne", "__s_dl")
        a.mov_rr(RAX, R9); a.alu_ri("cmp", RAX, 1)
        a.jcc("jne", "__s_emit")
        self._putc_imm(48)
        a.label("__s_emit")
        a.label("__s_emit_l")
        a.test_rr(R9, R9); a.jcc("je", "__r_done")
        a.pop(RAX)
        self._putc_reg(RAX)
        a.alu_ri("sub", R9, 1); a.jmp("__s_emit_l")

        # ---- epilogue ----
        a.label("__r_done")
        # 【诊断】追加：NDIG(初始化后) GESAVE(mul10后) Q(试商后) I(ML) F(low) E(high)
        self._putc_imm(35)
        # 顺序：F(set后) E(shl后) Q(mul10后) I(试商后) NDIG(low) GESAVE(high)
        for idx, slot_nm in enumerate(("F", "E", "Q", "I", "NDIG", "GESAVE")):
            a.load(RAX, rbp, self.d(slot_nm))
            a.test_rr(RAX, RAX); a.jcc("jne", "__dn%s_l" % idx)
            self._putc_imm(48); a.jmp("__dn%s_end" % idx)
            a.label("__dn%s_l" % idx)
            a.xor_rr(R9, R9); a.mov_imm32(R8, 10)
            a.label("__dn%s_dl" % idx)
            a.xor_rr(RDX, RDX); a.udiv(R8)
            a.alu_ri("add", RDX, 48); a.push(RDX); a.alu_ri("add", R9, 1)
            a.test_rr(RAX, RAX); a.jcc("jne", "__dn%s_dl" % idx)
            a.label("__dn%s_pe" % idx)
            a.test_rr(R9, R9); a.jcc("je", "__dn%s_end" % idx)
            a.pop(RAX); self._putc_reg(RAX)
            a.alu_ri("sub", R9, 1); a.jmp("__dn%s_pe" % idx)
            a.label("__dn%s_end" % idx)
            if idx < 5:
                self._putc_imm(44)
        a.label("__d_done")
        a.xor_rr(RAX, RAX)
        a.store_byte(RAX, RBX, 0)
        a.mov_rr(RAX, RBX)
        a.load(RCX, rbp, self.d("BUF"))
        a.alu_rr("sub", RAX, RCX)
        a.add_rsp(self.layout.frame_size)
        for r in (RDI, R15, R14, R13, R12, RBX):
            a.pop(r)
        a.pop(rbp)
        a.ret()

    def _fp_ge(self, rbp, rsp, tag, kreg):
        # 计算 ge(k)，结果写入 R11（0/1）。用 R 做临时数组。
        # mul10 会破坏 r8/r9，故外层计数存栈槽 GECNT；原始 k 存 GESAVE，结束恢复。
        a = self.a
        cnt = self.d("GECNT")
        save = self.d("GESAVE")
        a.store(kreg, rbp, save)              # 保存原始 k
        a.mov_rr(RAX, kreg)
        a.test_rr(RAX, RAX)
        a.jcc("js", "__ge%s_kneg" % tag)
        self._mp2(LBL_COPY, "R", "TB")
        a.store(kreg, rbp, cnt)
        a.label("__ge%s_pk" % tag)
        a.load(RAX, rbp, cnt); a.test_rr(RAX, RAX)
        a.jcc("je", "__ge%s_cmpp" % tag)
        self._mp1(LBL_MUL10, "R")
        a.load(RAX, rbp, cnt); a.alu_ri("sub", RAX, 1)
        a.store(RAX, rbp, cnt); a.jmp("__ge%s_pk" % tag)
        a.label("__ge%s_cmpp" % tag)
        self._mp2(LBL_CMP, "TA", "R")        # 正 k：TA >= TB*10^k
        a.jmp("__ge%s_set" % tag)
        a.label("__ge%s_kneg" % tag)
        self._mp2(LBL_COPY, "R", "TA")
        a.mov_rr(RAX, kreg); a.neg(RAX); a.store(RAX, rbp, cnt)
        a.label("__ge%s_nk" % tag)
        a.load(RAX, rbp, cnt); a.test_rr(RAX, RAX)
        a.jcc("je", "__ge%s_cmpn" % tag)
        self._mp1(LBL_MUL10, "R")
        a.load(RAX, rbp, cnt); a.alu_ri("sub", RAX, 1)
        a.store(RAX, rbp, cnt); a.jmp("__ge%s_nk" % tag)
        a.label("__ge%s_cmpn" % tag)
        self._mp2(LBL_CMP, "R", "TB")        # 负 k：TA*10^-k >= TB
        a.label("__ge%s_set" % tag)
        a.xor_rr(R11, R11)
        a.test_rr(RAX, RAX)
        a.setcc("jge", R11)
        a.load(kreg, rbp, save)               # 恢复原始 k
