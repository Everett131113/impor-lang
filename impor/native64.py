# -*- coding: utf-8 -*-
"""Impor 原生 x64 后端（真 AOT，零外部工具链）。

在三地址 IR（TAC）之上，自己做：
  1. 标量类型推断（让机器码知道 print 操作数是 int/bool/str/none）；
  2. 复用 optc 的 CFG / 活跃分析 / 线性扫描寄存器分配内核；
  3. 把 TAC 直接发射成 x86-64 机器码（含整数转字符串等运行时例程）；
  4. 由 pe64 链接成可在 Windows 直接运行的 PE（.exe）。

不调用任何汇编器/链接器/编译器。能力边界与 optc 一致（标量过程式核心），
本版 print 支持 int/bool/str/none；print 浮点（需 dtoa）编译期干净拒绝。
"""
import os

from . import optc
from .x64asm import Asm, REG
from .pe64 import link_exe
from .interpreter import ImporException, parse_source, strip_header
from .ir import IRError

# optc 的 R0..R5（线性扫描编号）-> 真实 x64 保留寄存器编号
# 全部选非易失寄存器，函数 prologue 统一保存，跨调用天然安全。
REAL_REGS = [REG["rbx"], REG["r12"], REG["r13"],
             REG["r14"], REG["r15"], REG["rdi"]]
# 临时/scratch 寄存器（非保留，调用约定易失）
T_RAX, T_RCX, T_RDX, T_R8, T_R9, T_R10, T_R11 = (
    REG["rax"], REG["rcx"], REG["rdx"], REG["r8"],
    REG["r9"], REG["r10"], REG["r11"])

# 6 个保留寄存器在 prologue 的 push 顺序（与 REAL_REGS 对应）
PUSH_REGS = list(REAL_REGS)
PUSH_BYTES = 8 * len(PUSH_REGS)          # 48

# 类型字符串
T_INT, T_FLOAT, T_BOOL, T_STR, T_NONE, T_UNKNOWN = (
    "int", "float", "bool", "str", "none", "unknown")

_CMP_OPS = frozenset({"==", "!=", "<", ">", "<=", ">="})


def _lit_type(v):
    if isinstance(v, bool):
        return T_BOOL
    if isinstance(v, int):
        return T_INT
    if isinstance(v, float):
        return T_FLOAT
    if isinstance(v, str):
        return T_STR
    if v is None:
        return T_NONE
    return T_UNKNOWN


def _merge(a, b):
    if a == b:
        return a
    if a == T_UNKNOWN:
        return b
    if b == T_UNKNOWN:
        return a
    if {a, b} == {T_INT, T_FLOAT}:
        return T_FLOAT
    return T_UNKNOWN


def _bin_type(op, tx, ty):
    if op in _CMP_OPS:
        return T_BOOL
    if op == "+" and tx == T_STR and ty == T_STR:
        return T_STR
    if op == "/":
        return T_FLOAT
    if op in ("//", "%"):
        return T_INT if (tx == T_INT and ty == T_INT) else T_FLOAT
    if op in ("+", "-", "*"):
        if tx == T_STR or ty == T_STR:
            return T_UNKNOWN
        return T_INT if (tx == T_INT and ty == T_INT) else T_FLOAT
    if op in ("&", "|", "^", "<<", ">>"):
        return T_INT
    return T_UNKNOWN


# ============================================================
# 类型推断：每个函数建立 变量名 -> 类型，迭代到不动点（含跨函数调用）
# ============================================================
def infer_types(module):
    order = [module.main] + list(module.funcs)
    envs = {fn.name: {p: T_UNKNOWN for p in fn.params} for fn in order}
    ret = {fn.name: T_UNKNOWN for fn in order}
    names = {fn.name for fn in order}
    fn_by_name = {fn.name: fn for fn in order}
    # lower 会在每个函数末尾无条件补一条合成的 RETURN None（不可达兜底），
    # 记录其下标，推断返回类型时跳过，避免污染真实返回类型。
    synth_ret = set()
    for fn in order:
        body = fn.body
        if body:
            last = body[-1]
            if last.op == "RETURN" and not isinstance(last.args[0], str) \
                    and last.args[0].value is None:
                synth_ret.add((fn.name, len(body) - 1))

    def operand_type(x, env):
        if isinstance(x, str):
            if x in optc.KNOWN_GLOBALS:
                return _lit_type(optc.KNOWN_GLOBALS[x])
            return env.get(x, T_UNKNOWN)
        return _lit_type(x.value)

    for _ in range(20):
        changed = False
        for fn in order:
            env = envs[fn.name]
            for idx, ins in enumerate(fn.body):
                op, a = ins.op, ins.args
                new = None
                if op == "CONST":
                    new = (a[0], _lit_type(a[1].value))
                elif op in ("LOAD", "MOVE"):
                    new = (a[0], operand_type(a[1], env))
                elif op == "STORE":
                    new = (a[0], operand_type(a[2], env))
                elif op == "BINOP":
                    new = (a[0], _bin_type(
                        a[1], operand_type(a[2], env),
                        operand_type(a[3], env)))
                elif op == "UNOP":
                    t = operand_type(a[2], env)
                    if a[1] == "not":
                        t = T_BOOL
                    new = (a[0], t)
                elif op == "CALL":
                    new = (a[0], ret.get(a[1], T_UNKNOWN))
                    # 调用点实参类型 -> 被调用函数参数（前向传播，迭代到不动点）
                    callee = a[1]
                    if callee in envs:
                        cparams = fn_by_name[callee].params
                        cenv = envs[callee]
                        for j, argx in enumerate(a[2:]):
                            if j >= len(cparams):
                                break
                            argty = operand_type(argx, env)
                            oldp = cenv.get(cparams[j], T_UNKNOWN)
                            mtp = _merge(oldp, argty)
                            if mtp != oldp:
                                cenv[cparams[j]] = mtp
                                changed = True
                if new is not None:
                    nm, ty = new
                    old = env.get(nm, T_UNKNOWN)
                    mt = _merge(old, ty)
                    if mt != old:
                        env[nm] = mt
                        changed = True
                if op == "RETURN" and (fn.name, idx) not in synth_ret:
                    ty = operand_type(a[0], env)
                    mt = _merge(ret[fn.name], ty)
                    if mt != ret[fn.name]:
                        ret[fn.name] = mt
                        changed = True
        if not changed:
            break

    return envs, ret


# ============================================================
# 字符串池：去重收集 rdata 字符串（统一加 \0，供 strlen 与 WriteFile 使用）
# ============================================================
class StringPool:
    def __init__(self):
        self._bytes = {}
        self.data = {}
        self.n = 0

    def get(self, s):
        b = s.encode("utf-8") + b"\0"
        if b in self._bytes:
            return self._bytes[b]
        sym = "__str%d" % self.n
        self.n += 1
        self._bytes[b] = sym
        self.data[sym] = b
        return sym


# 比较运算符 -> setcc 条件助记符
_CMP_CC = {"==": "je", "!=": "jne", "<": "jl",
           ">": "jg", "<=": "jle", ">=": "jge"}


def _align16(v):
    return (v + 15) & ~15


def _spill_disp(k):
    # rbp 之下先有 6 个保留寄存器（48 字节），再排 spill 槽
    return -(PUSH_BYTES + 8 * (k + 1))


class NativeEmitter:
    def __init__(self, module):
        self.module = module
        self.asm = Asm()
        self.pool = StringPool()
        self.envs, self.ret = infer_types(module)
        self.order = [module.main] + list(module.funcs)
        self.fnset = {fn.name for fn in self.order}
        # 每个函数编译期分配结果
        self.info = {}
        self._bool_n = 0
        self.rbp = REG["rbp"]

    # ---------- 对外 ----------
    def build(self):
        self._precheck()
        self._alloc_all()
        self._emit_runtime()
        for fn in self.order:
            self._emit_function(fn)
        self._emit_start()
        imports = {"kernel32.dll": [
            "GetStdHandle", "WriteFile", "ExitProcess"]}
        return link_exe(self.asm, self.pool.data, imports, "__start")

    # ---------- 边界检查 ----------
    def _precheck(self):
        for fn in self.order:
            if len(fn.params) > optc.NUM_REGS:
                raise ImporException(
                    "BackendError",
                    "原生后端当前最多支持 %d 个参数，函数 %s 有 %d 个；请改用 `impor run`"
                    % (optc.NUM_REGS, fn.name, len(fn.params)), fn.lineno)
            for ins in fn.body:
                if ins.op not in optc.SUPPORTED_OPS:
                    raise ImporException(
                        "BackendError",
                        "原生后端暂不支持指令 %s（%s，第 %s 行）；请改用 `impor run`"
                        % (ins.op, fn.name, ins.lineno), ins.lineno)
                if ins.op == "CALL" and ins.args[1] not in self.fnset:
                    raise ImporException(
                        "BackendError",
                        "原生后端暂不支持内建调用 %s（第 %s 行）；请改用 `impor run`"
                        % (ins.args[1], ins.lineno), ins.lineno)
        # 浮点本版整体拒绝（SSE 运算 + dtoa 列后续）
        for fn in self.order:
            env = self.envs[fn.name]
            for nm, ty in env.items():
                if ty == T_FLOAT:
                    raise ImporException(
                        "BackendError",
                        "原生后端本版暂不支持浮点（变量 %s）；请改用 `impor run`" % nm,
                        fn.lineno)
            for ins in fn.body:
                if ins.op == "PRINT":
                    for x in ins.args:
                        ty = self._operand_type(x, fn.name)
                        if ty not in (T_INT, T_BOOL, T_STR, T_NONE):
                            raise ImporException(
                                "BackendError",
                                "原生后端本版 print 仅支持 int/bool/str/none（第 %s 行）；"
                                "请改用 `impor run`" % ins.lineno, ins.lineno)

    def _operand_type(self, x, fname):
        if isinstance(x, str):
            if x in optc.KNOWN_GLOBALS:
                return _lit_type(optc.KNOWN_GLOBALS[x])
            return self.envs[fname].get(x, T_UNKNOWN)
        return _lit_type(x.value)

    # ---------- 寄存器分配（复用 optc 内核） ----------
    def _alloc_all(self):
        for fn in self.order:
            body = fn.body
            _, bounds, succ, _ = optc.build_cfg(body)
            _, live_out = optc.liveness(body, bounds, succ)
            intervals = optc.live_intervals(body, bounds, live_out)
            alloc, slot_count = optc.linear_scan(intervals, fn.params)
            has_print = any(i.op == "PRINT" for i in body)
            self.info[fn.name] = (alloc, slot_count, has_print)

    def _work_size(self, fn):
        _, slot_count, has_print = self.info[fn.name]
        size = 48 + 8 * slot_count          # shadow32 + home16 + spill
        if has_print:
            size += 32                      # intbuf
        return _align16(size)

    def _intbuf_disp(self, fn):
        _, slot_count, has_print = self.info[fn.name]
        return -(PUSH_BYTES + 8 * slot_count + 32)

    # ---------- 操作数加载 ----------
    def _load_const(self, dst, v):
        if isinstance(v, bool):
            self.asm.mov_imm32(dst, 1 if v else 0)
        elif isinstance(v, int):
            if 0 <= v <= 0xFFFFFFFF:
                self.asm.mov_imm32(dst, v)
            else:
                self.asm.mov_imm64(dst, v)
        elif isinstance(v, str):
            self.asm.lea_rip(dst, self.pool.get(v))
        elif v is None:
            self.asm.mov_imm32(dst, 0)

    def _load_operand(self, x, dst, fname):
        if isinstance(x, str):
            if x in optc.KNOWN_GLOBALS:
                self._load_const(dst, optc.KNOWN_GLOBALS[x])
                return
            alloc, _, _ = self.info[fname]
            loc = alloc[x]
            if loc[0] == "r":
                src = REAL_REGS[loc[1]]
                if src != dst:
                    self.asm.mov_rr(dst, src)
            else:
                self.asm.load(dst, self.rbp, _spill_disp(loc[1]))
        else:
            self._load_const(dst, x.value)

    def _store_to(self, name, src, fname):
        alloc, _, _ = self.info[fname]
        loc = alloc[name]
        if loc[0] == "r":
            dst = REAL_REGS[loc[1]]
            if dst != src:
                self.asm.mov_rr(dst, src)
        else:
            self.asm.store(src, self.rbp, _spill_disp(loc[1]))

    # ---------- 运行时例程 ----------
    def _emit_runtime(self):
        self._rt_write()
        self._rt_strlen()
        self._rt_int_to_str()

    def _rt_write(self):
        # RCX=buf RDX=len
        a = self.asm
        a.label("__rt_write")
        a.push(self.rbp)
        a.mov_rr(self.rbp, REG["rsp"])
        a.sub_rsp(48)
        a.store(REG["rcx"], self.rbp, -8)
        a.store(REG["rdx"], self.rbp, -16)
        a.mov_imm64(REG["rcx"], -11)
        a.call_rip("GetStdHandle")
        a.mov_rr(REG["rcx"], REG["rax"])
        a.load(REG["rdx"], self.rbp, -8)
        a.load(REG["r8"], self.rbp, -16)
        a.lea_mem(REG["r9"], self.rbp, -24)
        a.xor_rr(REG["r10"], REG["r10"])
        a.store(REG["r10"], REG["rsp"], 32)
        a.call_rip("WriteFile")
        a.raw(b"\xC9")                      # leave
        a.ret()

    def _rt_strlen(self):
        a = self.asm
        a.label("__rt_strlen")
        a.push(self.rbp)
        a.mov_rr(self.rbp, REG["rsp"])
        a.mov_rr(REG["r10"], REG["rcx"])
        a.xor_rr(REG["r11"], REG["r11"])
        a.label("__rt_strlen_l")
        a.load_byte(REG["r8"], REG["r10"], 0)
        a.test_rr(REG["r8"], REG["r8"])
        a.jcc("je", "__rt_strlen_d")
        a.alu_ri("add", REG["r10"], 1)
        a.alu_ri("add", REG["r11"], 1)
        a.jmp("__rt_strlen_l")
        a.label("__rt_strlen_d")
        a.mov_rr(REG["rax"], REG["r11"])
        a.raw(b"\xC9")
        a.ret()

    def _rt_int_to_str(self):
        a = self.asm
        a.label("__rt_int_to_str")
        a.push(self.rbp)
        a.mov_rr(self.rbp, REG["rsp"])
        a.sub_rsp(48)
        a.store(REG["rdx"], self.rbp, -8)       # 原始 buf
        a.mov_rr(REG["r8"], REG["rdx"])         # 写指针
        a.mov_rr(REG["rax"], REG["rcx"])        # 值
        a.xor_rr(REG["r11"], REG["r11"])        # 数字计数
        a.test_rr(REG["rax"], REG["rax"])
        a.jcc("jns", "__rt_ints_pos")
        a.mov_imm32(REG["r10"], 45)             # '-'
        a.store_byte(REG["r10"], REG["r8"], 0)
        a.alu_ri("add", REG["r8"], 1)
        a.neg(REG["rax"])
        a.label("__rt_ints_pos")
        a.mov_imm32(REG["r10"], 10)
        a.label("__rt_ints_l")
        a.xor_rr(REG["rdx"], REG["rdx"])
        a.udiv(REG["r10"])
        a.alu_ri("add", REG["rdx"], 48)
        a.push(REG["rdx"])
        a.alu_ri("add", REG["r11"], 1)
        a.test_rr(REG["rax"], REG["rax"])
        a.jcc("jne", "__rt_ints_l")
        a.label("__rt_ints_p")
        a.pop(REG["rcx"])
        a.store_byte(REG["rcx"], REG["r8"], 0)
        a.alu_ri("add", REG["r8"], 1)
        a.alu_ri("sub", REG["r11"], 1)
        a.jcc("jne", "__rt_ints_p")
        a.load(REG["r10"], self.rbp, -8)
        a.mov_rr(REG["rax"], REG["r8"])
        a.alu_rr("sub", REG["rax"], REG["r10"])   # 长度 = 写指针 - 原始buf
        a.raw(b"\xC9")
        a.ret()

    # ---------- 函数 ----------
    def _fn_label(self, name):
        return "__fn_" + name

    def _emit_function(self, fn):
        a = self.asm
        work = self._work_size(fn)
        a.label(self._fn_label(fn.name))
        a.push(self.rbp)
        a.mov_rr(self.rbp, REG["rsp"])
        for r in PUSH_REGS:
            a.push(r)
        a.sub_rsp(work)

        # 参数进入保留寄存器（前 N 个）
        arg_regs = [REG["rcx"], REG["rdx"], REG["r8"], REG["r9"]]
        for i, p in enumerate(fn.params):
            if i < 4:
                self._store_to(p, arg_regs[i], fn.name)
            else:
                # 第5/6 参数在调用方 home 区。prologue 后 rbp 指向保存的旧 rbp，
                # 返回地址在 rbp+8，故第5参数 = rbp+48、第6 = rbp+56。
                disp = 48 + 8 * (i - 4)
                a.load(REG["r10"], self.rbp, disp)
                self._store_to(p, REG["r10"], fn.name)

        for ins in fn.body:
            self._emit_instr(ins, fn)

        a.label("__ep_" + fn.name)
        a.add_rsp(work)
        for r in reversed(PUSH_REGS):
            a.pop(r)
        a.pop(self.rbp)
        a.ret()

    def _emit_instr(self, ins, fn):
        a = self.asm
        op, aa = ins.op, ins.args
        fname = fn.name

        if op == "LABEL":
            a.label("__lb_%s_%s" % (fname, aa[0]))
        elif op == "CONST":
            self._load_const(REG["r10"], aa[1].value)
            self._store_to(aa[0], REG["r10"], fname)
        elif op in ("LOAD", "MOVE"):
            self._load_operand(aa[1], REG["r10"], fname)
            self._store_to(aa[0], REG["r10"], fname)
        elif op == "STORE":
            self._load_operand(aa[2], REG["r10"], fname)
            self._store_to(aa[0], REG["r10"], fname)
        elif op == "BINOP":
            self._emit_binop(ins, fn)
        elif op == "UNOP":
            self._load_operand(aa[2], REG["r10"], fname)
            if aa[1] == "not":
                a.xor_rr(REG["rax"], REG["rax"])
                a.test_rr(REG["r10"], REG["r10"])
                a.setcc("je", REG["rax"])
                a.mov_rr(REG["r10"], REG["rax"])
            elif aa[1] == "-":
                a.neg(REG["r10"])
            self._store_to(aa[0], REG["r10"], fname)
        elif op == "JUMP":
            a.jmp("__lb_%s_%s" % (fname, aa[0]))
        elif op == "JUMP_IF_FALSE":
            self._load_operand(aa[0], REG["r10"], fname)
            a.test_rr(REG["r10"], REG["r10"])
            a.jcc("je", "__lb_%s_%s" % (fname, aa[1]))
        elif op == "CALL":
            self._emit_call(ins, fn)
        elif op == "RETURN":
            self._load_operand(aa[0], REG["rax"], fname)
            a.jmp("__ep_" + fname)
        elif op == "PRINT":
            self._emit_print(ins, fn)

    def _emit_binop(self, ins, fn):
        a = self.asm
        aa = ins.args
        dst, op = aa[0], aa[1]
        self._load_operand(aa[2], REG["r10"], fn.name)
        self._load_operand(aa[3], REG["r11"], fn.name)
        if op in ("+", "-"):
            a.alu_rr("add" if op == "+" else "sub", REG["r10"], REG["r11"])
        elif op == "*":
            a.imul_rr(REG["r10"], REG["r11"])
        elif op in ("//", "%"):
            a.mov_rr(REG["rax"], REG["r10"])
            a.cqo()
            a.idiv(REG["r11"])
            a.mov_rr(REG["r10"], REG["rax"] if op == "//" else REG["rdx"])
        elif op in _CMP_CC:
            a.xor_rr(REG["rax"], REG["rax"])      # 先清零，避免破坏 cmp 标志
            a.alu_rr("cmp", REG["r10"], REG["r11"])
            a.setcc(_CMP_CC[op], REG["rax"])
            a.mov_rr(REG["r10"], REG["rax"])
        self._store_to(dst, REG["r10"], fn.name)

    def _emit_call(self, ins, fn):
        a = self.asm
        aa = ins.args
        dst, callee = aa[0], aa[1]
        argvals = aa[2:]
        arg_regs = [REG["rcx"], REG["rdx"], REG["r8"], REG["r9"]]
        for i, x in enumerate(argvals):
            self._load_operand(x, REG["r10"], fn.name)
            if i < 4:
                a.mov_rr(arg_regs[i], REG["r10"])
            else:
                a.store(REG["r10"], REG["rsp"], 32 + 8 * (i - 4))
        a.call(self._fn_label(callee))
        self._store_to(dst, REG["rax"], fn.name)

    def _emit_print(self, ins, fn):
        a = self.asm
        args = ins.args
        last = len(args) - 1
        for i, x in enumerate(args):
            ty = self._operand_type(x, fn.name)
            if ty == T_INT:
                self._load_operand(x, REG["rcx"], fn.name)
                a.lea_mem(REG["rdx"], self.rbp, self._intbuf_disp(fn))
                a.call("__rt_int_to_str")
                a.lea_mem(REG["rcx"], self.rbp, self._intbuf_disp(fn))
                a.mov_rr(REG["rdx"], REG["rax"])
                a.call("__rt_write")
            elif ty == T_BOOL:
                self._emit_print_bool(x, fn)
            elif ty == T_NONE:
                a.lea_rip(REG["rcx"], self.pool.get("None"))
                a.mov_imm32(REG["rdx"], 4)
                a.call("__rt_write")
            elif ty == T_STR:
                self._load_operand(x, REG["rcx"], fn.name)
                a.call("__rt_strlen")
                a.mov_rr(REG["rdx"], REG["rax"])
                self._load_operand(x, REG["rcx"], fn.name)
                a.call("__rt_write")
            if i != last:
                a.lea_rip(REG["rcx"], self.pool.get(" "))
                a.mov_imm32(REG["rdx"], 1)
                a.call("__rt_write")
        a.lea_rip(REG["rcx"], self.pool.get("\n"))
        a.mov_imm32(REG["rdx"], 1)
        a.call("__rt_write")

    def _emit_print_bool(self, x, fn):
        a = self.asm
        self._load_operand(x, REG["r10"], fn.name)
        a.test_rr(REG["r10"], REG["r10"])
        n = self._bool_n
        self._bool_n += 1
        a.jcc("je", "__pb_f_%d" % n)
        a.lea_rip(REG["rcx"], self.pool.get("True"))
        a.mov_imm32(REG["rdx"], 4)
        a.jmp("__pb_m_%d" % n)
        a.label("__pb_f_%d" % n)
        a.lea_rip(REG["rcx"], self.pool.get("False"))
        a.mov_imm32(REG["rdx"], 5)
        a.label("__pb_m_%d" % n)
        a.call("__rt_write")

    # ---------- 入口 ----------
    def _emit_start(self):
        a = self.asm
        a.label("__start")
        a.push(self.rbp)
        a.mov_rr(self.rbp, REG["rsp"])
        a.sub_rsp(48)
        a.call(self._fn_label(self.module.main.name))
        a.xor_rr(REG["rcx"], REG["rcx"])
        a.call_rip("ExitProcess")


# ============================================================
# 顶层入口
# ============================================================
def build_exe_from_module(module):
    return NativeEmitter(module).build()


def build_exe_from_source(source, filename=None):
    from .ir_lower import lower_ast
    from .irop import optimize_module

    ast = parse_source(strip_header(source), filename)
    try:
        module = lower_ast(ast, filename)
    except IRError as e:
        raise ImporException(
            "BackendError",
            "原生后端暂不支持该语法（%s）；请改用 `impor run`" % e,
            getattr(e, "lineno", 0))
    optimize_module(module)
    return build_exe_from_module(module)
