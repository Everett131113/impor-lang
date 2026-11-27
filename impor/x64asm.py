# -*- coding: utf-8 -*-
"""Impor 自有的 x86-64 指令编码器（汇编器）。

不依赖任何外部汇编器：把本后端需要的指令直接编码成机器码字节，支持符号标签与
跳转/调用偏移的回填。配合 pe64.py 可产出能在 Windows 直接运行的原生可执行文件。

设计目标只覆盖 Impor 标量后端实际用到的指令，不追求成为通用汇编器。

寄存器以整数 0..15 编号（与 Intel 手册 ModR/M 编号一致）：
  0 RAX 1 RCX 2 RDX 3 RBX 4 RSP 5 RBP 6 RSI 7 RDI
  8 R8 9 R9 10 R10 11 R11 12 R12 13 R13 14 R14 15 R15
"""

# 64 位寄存器名 -> 编号
REG = {
    "rax": 0, "rcx": 1, "rdx": 2, "rbx": 3, "rsp": 4, "rbp": 5,
    "rsi": 6, "rdi": 7, "r8": 8, "r9": 9, "r10": 10, "r11": 11,
    "r12": 12, "r13": 13, "r14": 14, "r15": 15,
}

# ALU 操作在 /digit 里的编号
_ALU_DIGIT = {"add": 0, "or": 1, "adc": 2, "sbb": 3, "and": 4,
              "sub": 5, "xor": 6, "cmp": 7}
# 短跳转/条件跳转的条件码（Jcc）
CC = {"jo": 0, "jno": 1, "jb": 2, "jae": 3, "je": 4, "jne": 5,
      "jbe": 6, "ja": 7, "js": 8, "jns": 9, "jp": 10, "jnp": 11,
      "jl": 12, "jge": 13, "jle": 14, "jg": 15}


def _is_int(v, bits):
    # 立即数按**有符号**范围判断：imm8 为 -128..127（128..255 会被硬件解释成负数）
    if bits == 8:
        return -128 <= v <= 127
    if bits == 32:
        return -(1 << 31) <= v <= (1 << 32) - 1
    return -(1 << 63) <= v <= (1 << 64) - 1


def _i8(v):
    return v & 0xFF


def _i32(v):
    return v & 0xFFFFFFFF


class Asm:
    """累积机器码，管理符号标签与重定位回填。"""

    def __init__(self):
        self.buf = bytearray()
        self.labels = {}          # 内部符号 -> 绝对字节偏移
        # (fixup 字节偏移, 符号, 长度, 指令起点, 是否外部导入)
        self.fixups = []
        self.extern = set()       # 外部导入符号（IAT），由 pe64 解析

    # ---------- 基础 ----------
    def here(self):
        return len(self.buf)

    def raw(self, bs):
        self.buf += bytes(bs)

    def label(self, name):
        if name in self.labels:
            raise ValueError("标签重复: %s" % name)
        self.labels[name] = self.here()

    def _rex(self, w, r, x, b):
        v = 0x40
        if w:
            v |= 0x08
        if r:
            v |= 0x04
        if x:
            v |= 0x02
        if b:
            v |= 0x01
        return v

    def _emit_rex(self, w, r, x, b):
        # 仅当需要时才发 REX，保持编码紧凑
        need = w or (r & 8) or (x & 8) or (b & 8)
        if need:
            self.buf.append(self._rex(w, r & 8, x & 8, b & 8))

    # ---------- 寄存器-寄存器 ----------
    def _modrm_rr(self, reg, rm):
        return 0xC0 | ((reg & 7) << 3) | (rm & 7)

    def alu_rr(self, op, dst, src):
        """dst = dst OP src（64 位，寄存器-寄存器）。"""
        digit = _ALU_DIGIT[op]
        self._emit_rex(True, src, 0, dst)
        # r/m64, r64 形式：每个操作占 8 个 opcode，64 位形态基础为 0x01
        self.buf.append(0x01 + digit * 8)
        self.buf.append(self._modrm_rr(src, dst))

    def mov_rr(self, dst, src):
        """mov dst, src（64 位）。"""
        self._emit_rex(True, src, 0, dst)
        self.buf.append(0x89)               # MOV r/m64, r64
        self.buf.append(self._modrm_rr(src, dst))

    def xor_rr(self, dst, src):
        """xor（常用于 32 位清零，调用方传 32 位语义这里按 64 位也可）。"""
        self.alu_rr("xor", dst, src)

    # ---------- 寄存器-内存（base + disp） ----------
    def _mem_modrm(self, reg, base, disp):
        """发射 ModR/M + 可选 SIB + disp，返回是否已处理。"""
        b = base & 7
        use_sib = (b == 4)                  # RSP/R12 必须走 SIB
        # RBP/R13 不能用 mod=0
        if disp == 0 and b != 5:
            mod = 0
        elif _is_int(disp, 8):
            mod = 1
        else:
            mod = 2
        if use_sib:
            self.buf.append((mod << 6) | ((reg & 7) << 3) | 4)
            # SIB: scale=0, index=4(无), base=base 低3位
            self.buf.append(0x24 | (b))
        else:
            self.buf.append((mod << 6) | ((reg & 7) << 3) | b)
        if mod == 1:
            self.buf.append(_i8(disp))
        elif mod == 2:
            self.buf += _i32(disp).to_bytes(4, "little")

    def load(self, dst, base, disp):
        """mov dst, [base+disp]（64 位）。"""
        self._emit_rex(True, dst, 0, base)
        self.buf.append(0x8B)               # MOV r64, r/m64
        self._mem_modrm(dst, base, disp)

    def store(self, src, base, disp):
        """mov [base+disp], src（64 位）。"""
        self._emit_rex(True, src, 0, base)
        self.buf.append(0x89)               # MOV r/m64, r64
        self._mem_modrm(src, base, disp)

    def alu_mem(self, op, src, base, disp):
        """[base+disp] OP= src（64 位，内存目标）。"""
        digit = _ALU_DIGIT[op]
        self._emit_rex(True, src, 0, base)
        self.buf.append(0x01 + digit * 8)
        self._mem_modrm(src, base, disp)

    # ---------- 立即数 ----------
    def mov_imm64(self, dst, value):
        """mov r64, imm64（10 字节，用于无法塞进 imm32 的值）。"""
        self._emit_rex(True, 0, 0, dst)
        self.buf.append(0xB8 + (dst & 7))
        self.buf += (value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")

    def mov_imm32(self, dst, value):
        """mov r32, imm32（零扩展到 64 位，5 字节）。"""
        # 32 位不带 REX.W；高编号仍需 REX
        self._emit_rex(False, 0, 0, dst)
        self.buf.append(0xB8 + (dst & 7))
        self.buf += _i32(value).to_bytes(4, "little")

    def alu_ri(self, op, dst, value):
        """dst OP= imm（64 位），优先 imm8 紧凑编码。"""
        digit = _ALU_DIGIT[op]
        if _is_int(value, 8):
            self._emit_rex(True, 0, 0, dst)
            self.buf.append(0x83)
            self.buf.append(0xC0 | (digit << 3) | (dst & 7))
            self.buf.append(_i8(value))
        else:
            self._emit_rex(True, 0, 0, dst)
            self.buf.append(0x81)
            self.buf.append(0xC0 | (digit << 3) | (dst & 7))
            self.buf += _i32(value).to_bytes(4, "little")

    # ---------- 一元 / 乘除 ----------
    def _one_rm(self, digit, dst):
        self._emit_rex(True, 0, 0, dst)
        self.buf.append(0xF7)
        self.buf.append(0xC0 | (digit << 3) | (dst & 7))

    def neg(self, dst):
        self._one_rm(3, dst)

    def imul_rr(self, dst, src):
        """imul dst, src（两操作数，64 位有符号）。"""
        self._emit_rex(True, src, 0, dst)
        self.buf.append(0x0F)
        self.buf.append(0xAF)
        self.buf.append(self._modrm_rr(dst, src))

    def bsr_rr(self, dst, src):
        """bsr dst, src：最高置位的索引（src 为 0 时标志未定义，调用方需保证非零）。"""
        self._emit_rex(True, dst, 0, src)
        self.buf.append(0x0F)
        self.buf.append(0xBD)
        self.buf.append(self._modrm_rr(dst, src))

    def cqo(self):
        """cqo：rdx:rax 符号扩展，为 idiv 做准备。"""
        self._emit_rex(True, 0, 0, 0)
        self.buf.append(0x99)

    def idiv(self, src):
        """idiv src：rax = rdx:rax / src，rdx 存余数。"""
        self._one_rm(7, src)

    def udiv(self, src):
        """div src：无符号 rdx:rax / src。"""
        self._one_rm(6, src)

    def mul_rr(self, src):
        """mul src：无符号 rdx:rax = rax * src（F7 /4）。"""
        self._emit_rex(True, 0, 0, src)
        self.buf.append(0xF7)
        self.buf.append(0xC0 | (4 << 3) | (src & 7))

    _SHIFT_DIGIT = {"shl": 4, "shr": 5, "sar": 7}

    def shift_cl(self, op, dst):
        """shl/shr/sar dst, cl（64 位，计数在 cl，D3 /digit）。"""
        digit = self._SHIFT_DIGIT[op]
        self._emit_rex(True, 0, 0, dst)
        self.buf.append(0xD3)
        self.buf.append(0xC0 | (digit << 3) | (dst & 7))

    def shift_imm(self, op, dst, imm):
        """shl/shr/sar dst, imm8（64 位，C1 /digit ib）。"""
        digit = self._SHIFT_DIGIT[op]
        self._emit_rex(True, 0, 0, dst)
        self.buf.append(0xC1)
        self.buf.append(0xC0 | (digit << 3) | (dst & 7))
        self.buf.append(imm & 0xFF)

    def test_rr(self, dst, src):
        """test dst, src（64 位）。"""
        self._emit_rex(True, src, 0, dst)
        self.buf.append(0x85)
        self.buf.append(self._modrm_rr(src, dst))

    def setcc(self, cond, dst):
        """setcc r/m8：把条件结果写入低字节（调用前应先 xor 清零目标）。"""
        # 高编号目标需要 REX（在 setcc 前发，不带.W）
        self._emit_rex(False, 0, 0, dst)
        self.buf.append(0x0F)
        self.buf.append(0x90 + CC[cond])
        self.buf.append(0xC0 | (dst & 7))

    def movzx8(self, dst, src):
        """movzx r64, r/m8：字节零扩展到 64 位（寄存器操作数）。"""
        self._emit_rex(True, dst, 0, src)
        self.buf.append(0x0F)
        self.buf.append(0xB6)
        self.buf.append(self._modrm_rr(dst, src))

    def load_byte(self, dst, base, disp):
        """movzx r64, byte [base+disp]：从内存加载字节并零扩展。"""
        self._emit_rex(True, dst, 0, base)
        self.buf.append(0x0F)
        self.buf.append(0xB6)
        self._mem_modrm(dst, base, disp)

    def store_byte(self, src, base, disp):
        """mov byte [base+disp], r8：把低字节存入内存。"""
        self._emit_rex(False, src, 0, base)
        self.buf.append(0x88)
        self._mem_modrm(src, base, disp)

    def lea_mem(self, dst, base, disp):
        """lea dst, [base+disp]：计算内存地址。"""
        self._emit_rex(True, dst, 0, base)
        self.buf.append(0x8D)
        self._mem_modrm(dst, base, disp)

    # ---------- 栈 / 控制 ----------
    def push(self, r):
        self._emit_rex(False, 0, 0, r)
        self.buf.append(0x50 + (r & 7))

    def pop(self, r):
        self._emit_rex(False, 0, 0, r)
        self.buf.append(0x58 + (r & 7))

    def sub_rsp(self, n):
        self.alu_ri("sub", 4, n)

    def add_rsp(self, n):
        self.alu_ri("add", 4, n)

    def rep_stosq(self):
        """rep stosq：以 rax 填充 [rdi] 共 rcx 个 u64，rdi 自动前进。"""
        self.buf += b"\xF3\x48\xAB"

    def rep_movsq(self):
        """rep movsq：[rdi] = [rsi] 共 rcx 个 u64，两指针自动前进。"""
        self.buf += b"\xF3\x48\xA5"

    def loop_rel8(self, target):
        """loop：rcx -= 1，非零则短跳（rel8）；不修改标志寄存器。"""
        start = self.here()
        self.buf.append(0xE2)
        # 内部 rel8 回填，复用长度 1 的 fixup
        self.fixups.append((self.here(), target, 1, start, False))
        self.buf.append(0)

    def rep_movsb(self):
        """rep movsb：[rdi] = byte [rsi] 共 rcx 字节，两指针自动前进。"""
        self.buf += b"\xF3\xA4"

    def nop(self):
        self.buf.append(0x90)

    def ret(self):
        self.buf.append(0xC3)

    def call_rip(self, target):
        """call [rip+disp32]：间接调用 IAT 项，符号在链接时由 pe64 解析。"""
        start = self.here()
        self.buf.append(0xFF)
        self.buf.append(0x15)              # /2, rip 相对
        self.extern.add(target)
        self.fixups.append((self.here(), target, 4, start, True))
        self.buf += b"\x00\x00\x00\x00"

    def jmp_rip(self, target):
        """jmp [rip+disp32]。"""
        start = self.here()
        self.buf.append(0xFF)
        self.buf.append(0x25)              # /4, rip 相对
        self.extern.add(target)
        self.fixups.append((self.here(), target, 4, start, True))
        self.buf += b"\x00\x00\x00\x00"

    def lea_rip(self, dst, target):
        """lea dst, [rip+disp32]：加载数据/字符串符号地址（外部，链接器解析）。"""
        start = self.here()
        self._emit_rex(True, dst, 0, 5)
        self.buf.append(0x8D)              # LEA
        self.buf.append((dst & 7) << 3 | 5)   # mod=00, r/m=5 (RIP)
        self.extern.add(target)
        self.fixups.append((self.here(), target, 4, start, True))
        self.buf += b"\x00\x00\x00\x00"

    def rel32(self, opcode, target):
        """E8/E9 或 0F 8x 这类相对当前指令的 rel32 跳转/调用。"""
        start = self.here()
        if isinstance(opcode, (list, tuple)):
            self.buf += bytes(opcode)
        else:
            self.buf.append(opcode)
        self.fixups.append((self.here(), target, 4, start, False))
        self.buf += b"\x00\x00\x00\x00"

    def call(self, target):
        self.rel32(0xE8, target)

    def jmp(self, target):
        self.rel32(0xE9, target)

    def jcc(self, cond, target):
        self.rel32([0x0F, 0x80 + CC[cond]], target)

    # ---------- SSE2 标量双精度（scalar double） ----------
    # 编码顺序：[强制前缀 F2/66] [可选 REX(.W)] 0F opcode ModRM[/SIB/disp]
    def _sse_rr(self, prefix, opcode, dst, src, w=False):
        self.buf.append(prefix)
        self._emit_rex(w, dst, 0, src)
        self.buf.append(0x0F)
        self.buf.append(opcode)
        self.buf.append(self._modrm_rr(dst, src))

    def _sse_mem(self, prefix, opcode, reg, base, disp, w=False):
        self.buf.append(prefix)
        self._emit_rex(w, reg, 0, base)
        self.buf.append(0x0F)
        self.buf.append(opcode)
        self._mem_modrm(reg, base, disp)

    def movsd_rr(self, dst, src):
        self._sse_rr(0xF2, 0x10, dst, src)

    def movsd_load(self, dst, base, disp):
        self._sse_mem(0xF2, 0x10, dst, base, disp)

    def movsd_store(self, src, base, disp):
        self._sse_mem(0xF2, 0x11, src, base, disp)

    def addsd(self, dst, src):
        self._sse_rr(0xF2, 0x58, dst, src)

    def subsd(self, dst, src):
        self._sse_rr(0xF2, 0x5C, dst, src)

    def mulsd(self, dst, src):
        self._sse_rr(0xF2, 0x59, dst, src)

    def divsd(self, dst, src):
        self._sse_rr(0xF2, 0x5E, dst, src)

    def sqrtsd(self, dst, src):
        self._sse_rr(0xF2, 0x51, dst, src)

    def xorpd(self, dst, src):
        self._sse_rr(0x66, 0x57, dst, src)

    def comisd(self, dst, src):
        # 66 0F 2F：比较并设置 EFLAGS（无序时 ZF=PF=CF=1）
        self._sse_rr(0x66, 0x2F, dst, src)

    def cvtsi2sd(self, xmm, gpr):
        # 有符号 64 位整数 -> double：F2 REX.W 0F 2A
        self._sse_rr(0xF2, 0x2A, xmm, gpr, w=True)

    def cvttsd2si(self, gpr, xmm):
        # double -> 有符号 64 位整数（截断）：F2 REX.W 0F 2C
        self._sse_rr(0xF2, 0x2C, gpr, xmm, w=True)

    def movsd_load_rip(self, xmm, target):
        # rip 相对加载 8 字节 double 常量：F2 [REX] 0F 10 /r（r/m=5）
        start = self.here()
        self.buf.append(0xF2)
        self._emit_rex(False, xmm, 0, 5)
        self.buf.append(0x0F)
        self.buf.append(0x10)
        self.buf.append(((xmm & 7) << 3) | 5)
        self.extern.add(target)
        self.fixups.append((self.here(), target, 4, start, True))
        self.buf += b"\x00\x00\x00\x00"

    def movq_xmm_rr(self, xmm, gpr):
        # movq xmm, r64：66 REX.W 0F 6E /r（位搬运，不解释为浮点）
        self._sse_rr(0x66, 0x6E, xmm, gpr, w=True)

    def movq_rr_xmm(self, gpr, xmm):
        # movq r64, xmm：66 REX.W 0F 7E /r
        self._sse_rr(0x66, 0x7E, gpr, xmm, w=True)

    # ---------- 回填 ----------
    def extern_fixups(self):
        """外部导入（IAT）fixup 列表：(字段偏移, 符号, 长度, 指令起点)。"""
        return [(at, t, ln, st) for (at, t, ln, st, ex) in self.fixups if ex]

    def finish(self):
        """解析内部 fixup，返回机器码字节；外部 fixup 留待 pe64 回填。"""
        for at, target, length, instr_start, ex in self.fixups:
            if ex:
                continue
            if target not in self.labels:
                raise ValueError("未定义符号: %s" % target)
            # 内部重定位以 fixup 字段末尾(at+length)为基准计算相对位移
            disp = self.labels[target] - (at + length)
            if length == 1:                  # rel8（loop）：直接按带符号字节
                if not (-128 <= disp <= 127):
                    raise ValueError("短跳转越界: %s (%d)" % (target, disp))
                self.buf[at] = disp & 0xFF
            else:
                self.buf[at:at + length] = _i32(disp).to_bytes(length, "little")
        return bytes(self.buf)
