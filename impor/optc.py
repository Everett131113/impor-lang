# -*- coding: utf-8 -*-
"""
Impor (I++) 教科书优化编译器后端（OPTC = Optimizing Compiler）。

在已有三地址 IR（TAC，见 ir.py / ir_lower.py）之上，补齐教科书（龙书 / Engineering a
Compiler）意义上优化编译器的标准后端流水线，全程**不依赖任何外部编译器或汇编器**：

  三地址 IR
    -> 1. 基本块划分 + 控制流图（CFG）
    -> 2. 活跃变量数据流分析（后向 gen/kill 不动点，live-in / live-out）
    -> 3. 线性扫描寄存器分配（有限物理寄存器；不足时溢出 spill 到栈槽）
    -> 4. 寄存器式目标代码（操作数为物理寄存器 / 立即数 / 栈槽）
    -> 5. 寄存器机直接执行（操作寄存器数组，而非栈式虚拟机）

与 NVM（栈式字节码 VM）的区别：NVM 是「栈式」目标，本模块是「寄存器式」目标，且第一次
真正实现了 CFG、数据流分析与寄存器分配——这是优化编译器区别于普通解释器的核心环节。

当前覆盖（与解释器同语义，标量过程式内核）：
  int/float/bool/str/none、变量、算术/比较/逻辑、if/else、while、def/return/递归、print。
明确不支持（编译期 BackendError，不静默改语义）：
  list/dict、索引/切片、属性、class、try/catch、import、嵌套函数/闭包、lambda、异常、
  for-in 等；遇到这些会提示改用 `impor run`。
"""

from .interpreter import (
    parse_source, strip_header, ImporException,
    I64_MIN, I64_MAX, I64_MSG,
)
from .ir import IRError

# ============================================================
# 目标机参数：模拟一台真实的寄存器机器。
# 物理寄存器只有有限个，超出的虚拟寄存器必须溢出到栈槽——这是寄存器分配的根本矛盾。
# ============================================================
NUM_REGS = 6            # 可分配通用寄存器 R0..R5（参数也从 R0 起依次传入）
MAX_DEPTH = 3200        # 递归深度保护（与 NVM 一致）

# 本后端支持的 TAC 指令；其余 op 一律编译期拒绝。
SUPPORTED_OPS = frozenset({
    "LABEL", "CONST", "LOAD", "STORE", "MOVE", "BINOP", "UNOP",
    "JUMP", "JUMP_IF_FALSE", "CALL", "RETURN", "PRINT",
})

# 目标指令里操作数的三种寻址方式：
#   ("r", k)  物理寄存器 Rk
#   ("i", v)  立即数
#   ("s", k)  栈槽（spill slot）Sk
def R(k):
    return ("r", int(k))

def I(v):
    return ("i", v)

def S(k):
    return ("s", int(k))


class TargetFunction:
    """一个函数编译后的寄存器式目标代码。"""

    __slots__ = ("name", "params", "code", "labels", "blocks",
                 "intervals", "spilled", "used_regs")

    def __init__(self, name, params, code, labels, blocks,
                 intervals, spilled, used_regs):
        self.name = name
        self.params = list(params)
        self.code = code                  # [ (op, ...operands), ... ]
        self.labels = labels              # label -> 目标指令下标
        self.blocks = blocks              # CFG 基本块数
        self.intervals = intervals        # 活跃区间数
        self.spilled = spilled            # 溢出到栈槽的变量数
        self.used_regs = used_regs        # 实际用到的物理寄存器数


class TargetProgram:
    __slots__ = ("functions", "main_name", "total_spill")

    def __init__(self, functions, main_name, total_spill):
        self.functions = functions        # name -> TargetFunction
        self.main_name = main_name
        self.total_spill = total_spill


# ============================================================
# 指令分析：取出一条 TAC 的定义目标 dst 与使用到的变量（仅 str 操作数，Lit 不算变量）
# ============================================================
# TAC 里布尔/空值通过 LOAD/RETURN 以全局名引用，它们是已知常量，不需要占寄存器，
# 代码生成时直接转立即数。
KNOWN_GLOBALS = {"True": True, "False": False, "None": None, "nil": None}


def _var(x):
    """str 操作数且不是已知全局常量，才是需要分配寄存器的虚拟变量。"""
    return isinstance(x, str) and x not in KNOWN_GLOBALS


def _analyze(ins):
    op = ins.op
    a = ins.args
    if op == "CONST":
        dst, uses = a[0], []
    elif op == "LOAD":
        dst, uses = a[0], [a[1]] if _var(a[1]) else []
    elif op == "MOVE":
        dst, uses = a[0], [a[1]] if _var(a[1]) else []
    elif op == "STORE":
        # STORE name, mode, value：对命名变量的局部赋值/更新；mode 合法性在代码生成时检查
        dst, uses = a[0], [a[2]] if _var(a[2]) else []
    elif op == "BINOP":
        dst, uses = a[0], [x for x in (a[2], a[3]) if _var(x)]
    elif op == "UNOP":
        dst, uses = a[0], [a[2]] if _var(a[2]) else []
    elif op == "JUMP":
        dst, uses = None, []
    elif op == "JUMP_IF_FALSE":
        dst, uses = None, [a[0]] if _var(a[0]) else []
    elif op == "CALL":
        dst, uses = a[0], [x for x in a[2:] if _var(x)]
    elif op == "RETURN":
        dst, uses = None, [a[0]] if _var(a[0]) else []
    elif op == "PRINT":
        dst, uses = None, [x for x in a if _var(x)]
    else:
        dst, uses = None, []
    return dst, uses


# ============================================================
# 阶段 1：基本块划分 + 控制流图（CFG）
# ============================================================
def _label_map(body):
    """标签名 -> 它所在的指令索引（含 LABEL 指令本身）。"""
    out = {}
    for idx, ins in enumerate(body):
        if ins.op == "LABEL":
            out[ins.args[0]] = idx
    return out


def build_cfg(body):
    """返回 (leaders, succ)。

    leaders：基本块入口指令索引集合（已排序）。
    succ：块入口索引 -> 后继块入口索引列表。
    """
    n = len(body)
    labels = _label_map(body)

    leaders = {0}
    for idx, ins in enumerate(body):
        if ins.op == "JUMP":
            leaders.add(labels[ins.args[0]])
            if idx + 1 < n:
                leaders.add(idx + 1)
        elif ins.op == "JUMP_IF_FALSE":
            leaders.add(labels[ins.args[1]])
            if idx + 1 < n:
                leaders.add(idx + 1)

    leader_list = sorted(leaders)

    # 每个块的范围 [start, end)
    bounds = []
    for i, start in enumerate(leader_list):
        end = leader_list[i + 1] if i + 1 < len(leader_list) else n
        bounds.append((start, end))

    # 标签可能连续多个，块内出现的所有标签名都指向该块。
    label_block = {}
    for bi, (start, end) in enumerate(bounds):
        for idx in range(start, end):
            if body[idx].op == "LABEL":
                label_block[body[idx].args[0]] = bi

    def block_of_index(idx):
        # 找到 idx 所属的块
        for bi, (start, end) in enumerate(bounds):
            if start <= idx < end:
                return bi
        return None

    succ = {}
    for bi, (start, end) in enumerate(bounds):
        # 块内最后一条有效（非 LABEL）指令
        term = None
        for idx in range(end - 1, start - 1, -1):
            if body[idx].op != "LABEL":
                term = body[idx]
                break
        if term is None:
            target = bi + 1 if bi + 1 < len(bounds) else None
            succ[bi] = [target] if target is not None else []
        elif term.op == "JUMP":
            succ[bi] = [label_block[term.args[0]]]
        elif term.op == "JUMP_IF_FALSE":
            order = [label_block[term.args[1]]]
            if bi + 1 < len(bounds):
                order.append(bi + 1)       # 条件为真时顺序落入下一块
            succ[bi] = order
        elif term.op == "RETURN":
            succ[bi] = []
        else:
            succ[bi] = [bi + 1] if bi + 1 < len(bounds) else []

    return leader_list, bounds, succ, label_block


# ============================================================
# 阶段 2：活跃变量数据流分析（后向不动点）
# ============================================================
def _block_use_def(body, start, end):
    """块级 use（定义前被引用）与 def（块内定义），按指令顺序计算。"""
    use, defined, seen = [], set(), set()
    for idx in range(start, end):
        ins = body[idx]
        if ins.op == "LABEL":
            continue
        dst, uses = _analyze(ins)
        for u in uses:
            if u not in seen and u not in use:
                use.append(u)
        if dst is not None:
            defined.add(dst)
            seen.add(dst)
    return use, defined


def liveness(body, bounds, succ):
    """块级活跃变量不动点分析。返回 (live_in, live_out)，元素为集合的列表。"""
    nb = len(bounds)
    block_use, block_def = [], []
    for (start, end) in bounds:
        use, defined = _block_use_def(body, start, end)
        block_use.append(set(use))
        block_def.append(defined)

    live_in = [set() for _ in range(nb)]
    live_out = [set() for _ in range(nb)]
    changed = True
    while changed:
        changed = False
        for bi in range(nb - 1, -1, -1):
            new_out = set()
            for s in succ.get(bi, []):
                new_out |= live_in[s]
            new_in = block_use[bi] | (new_out - block_def[bi])
            if new_out != live_out[bi] or new_in != live_in[bi]:
                live_out[bi] = new_out
                live_in[bi] = new_in
                changed = True
    return live_in, live_out


def live_intervals(body, bounds, live_out):
    """逐指令后向传播，求每个虚拟寄存器的活跃区间 [start_idx, end_idx]。

    返回 {name: [start, end]}，索引为含 LABEL 的整条 body 指令下标。
    """
    intervals = {}

    def touch(name, idx):
        if name in intervals:
            iv = intervals[name]
            if idx < iv[0]:
                iv[0] = idx
            if idx > iv[1]:
                iv[1] = idx
        else:
            intervals[name] = [idx, idx]

    for bi, (start, end) in enumerate(bounds):
        cur = set(live_out[bi])
        for idx in range(end - 1, start - 1, -1):
            ins = body[idx]
            if ins.op == "LABEL":
                continue
            dst, uses = _analyze(ins)
            for v in cur:
                touch(v, idx)
            for u in uses:
                touch(u, idx)
            if dst is not None:
                touch(dst, idx)
            # 向后传播：本指令之前 = (之后 - 定义) | 使用
            cur = (cur - {dst}) if dst is not None else set(cur)
            cur |= set(uses)
    return intervals


# ============================================================
# 阶段 3：线性扫描寄存器分配
# ============================================================
def linear_scan(intervals, params):
    """线性扫描。返回 (alloc, spill_count)。

    alloc: name -> ("r", k) 或 ("s", slot)
    """
    # 同一起点的稳定顺序：参数优先（按参数位置），保证参数依次拿到 R0,R1,...
    order = {}
    for i, p in enumerate(params):
        order[p] = i
    next_order = len(params)
    for name in intervals:
        if name not in order:
            order[name] = next_order
            next_order += 1

    items = sorted(intervals.keys(),
                   key=lambda nm: (intervals[nm][0], order[nm]))

    free_regs = list(range(NUM_REGS))
    active = []                    # [(end, name, reg)]，按 end 维护
    alloc = {}
    next_slot = 0

    for name in items:
        st, en = intervals[name]

        # 过期回收：end < 当前区间起点的寄存器释放
        remain = []
        for (ae, an, ar) in active:
            if ae < st and alloc.get(an) == ("r", ar):
                free_regs.append(ar)
            else:
                remain.append((ae, an, ar))
        active = remain
        free_regs.sort()

        if free_regs:
            reg = free_regs.pop(0)
            alloc[name] = R(reg)
            active.append((en, name, reg))
        else:
            # 无空闲寄存器：在活跃区间结束最晚者与当前区间之间，溢出结束更晚的
            victim = max(active, key=lambda x: x[0])
            if victim[0] > en:
                alloc[victim[1]] = S(next_slot)
                reg = victim[2]
                active = [x for x in active if x[1] != victim[1]]
                alloc[name] = R(reg)
                active.append((en, name, reg))
            else:
                alloc[name] = S(next_slot)
            next_slot += 1
        active.sort(key=lambda x: x[0])

    return alloc, next_slot


# ============================================================
# 阶段 4：寄存器式目标代码生成
# ============================================================
def _operand_loc(x, alloc):
    if isinstance(x, str):
        if x in KNOWN_GLOBALS:
            return I(KNOWN_GLOBALS[x])
        return alloc[x]
    return I(x.value)           # Lit


def generate_code(body, alloc):
    """把 TAC 逐条翻译成寄存器式目标指令。"""
    code = []
    for ins in body:
        op = ins.op
        a = ins.args
        if op == "LABEL":
            code.append(("LABEL", a[0]))
        elif op == "CONST":
            code.append(("SET", alloc[a[0]], I(a[1].value)))
        elif op in ("LOAD", "MOVE"):
            code.append(("SET", alloc[a[0]], _operand_loc(a[1], alloc)))
        elif op == "STORE":
            if a[1] == "global":
                raise ImporException(
                    "BackendError",
                    "优化编译器后端暂不支持 global 作用域赋值（第 %s 行）；请改用 `impor run`"
                    % ins.lineno, ins.lineno)
            code.append(("SET", alloc[a[0]], _operand_loc(a[2], alloc)))
        elif op == "BINOP":
            code.append(("BIN", alloc[a[0]], a[1],
                         _operand_loc(a[2], alloc), _operand_loc(a[3], alloc)))
        elif op == "UNOP":
            code.append(("UN", alloc[a[0]], a[1], _operand_loc(a[2], alloc)))
        elif op == "JUMP":
            code.append(("JMP", a[0]))
        elif op == "JUMP_IF_FALSE":
            code.append(("JFALSE", _operand_loc(a[0], alloc), a[1]))
        elif op == "CALL":
            arg_locs = [_operand_loc(x, alloc) for x in a[2:]]
            code.append(("CALL", alloc[a[0]], a[1], arg_locs))
        elif op == "RETURN":
            code.append(("RET", _operand_loc(a[0], alloc)))
        elif op == "PRINT":
            code.append(("PRT", [_operand_loc(x, alloc) for x in a]))
    return code


# ============================================================
# 单函数编译：CFG -> 活跃分析 -> 寄存器分配 -> 代码生成
# ============================================================
def compile_function(fn):
    body = fn.body

    # 边界：参数走寄存器传递，超过物理寄存器数量先明确拒绝（栈传参列入后续）
    if len(fn.params) > NUM_REGS:
        raise ImporException(
            "BackendError",
            "优化编译器后端当前最多支持 %d 个参数，函数 %s 有 %d 个；请改用 `impor run`"
            % (NUM_REGS, fn.name, len(fn.params)), fn.lineno)

    # 边界：不支持的 TAC 指令一律编译期拒绝
    for ins in body:
        if ins.op not in SUPPORTED_OPS:
            raise ImporException(
                "BackendError",
                "优化编译器后端暂不支持指令 %s（%s，第 %s 行）；请改用 `impor run` 执行"
                % (ins.op, fn.name, ins.lineno),
                ins.lineno)

    leader_list, bounds, succ, _ = build_cfg(body)
    live_in, live_out = liveness(body, bounds, succ)
    intervals = live_intervals(body, bounds, live_out)
    alloc, slot_count = linear_scan(intervals, fn.params)
    code = generate_code(body, alloc)

    # 目标指令的标签映射
    target_labels = {}
    for i, ins in enumerate(code):
        if ins[0] == "LABEL":
            target_labels[ins[1]] = i

    used_regs = len({loc[1] for loc in alloc.values() if loc[0] == "r"})
    spilled = sum(1 for loc in alloc.values() if loc[0] == "s")

    return TargetFunction(fn.name, fn.params, code, target_labels,
                          blocks=len(bounds), intervals=len(intervals),
                          spilled=spilled, used_regs=used_regs)


# ============================================================
# 整个 IR 模块编译
# ============================================================
def compile_ir_module(module):
    order = [module.main] + list(module.funcs)
    user_fns = {fn.name for fn in order}

    # 边界：CALL 的被调用方必须是用户自定义函数；内建（len/int/str 等）本后端不支持，
    # 编译期干净拒绝，而不是运行时在函数表里找不到而内部错误。
    for fn in order:
        for ins in fn.body:
            if ins.op == "CALL" and ins.args[1] not in user_fns:
                raise ImporException(
                    "BackendError",
                    "优化编译器后端暂不支持内建调用 %s（第 %s 行）；请改用 `impor run`"
                    % (ins.args[1], ins.lineno), ins.lineno)

    functions = {}
    total_spill = 0
    for fn in order:
        tf = compile_function(fn)
        functions[tf.name] = tf
        total_spill += tf.spilled
    return TargetProgram(functions, module.main.name, total_spill)


# ============================================================
# 阶段 5：寄存器机执行器
# ============================================================
def _apply_bin(op, x, y, lineno=0):
    if op == "+":
        r = x + y
    elif op == "-":
        r = x - y
    elif op == "*":
        r = x * y
    elif op == "/":
        if y == 0:
            raise ImporException("ZeroDivisionError", "除数为 0", lineno)
        r = x / y
    elif op == "//":
        if y == 0:
            raise ImporException("ZeroDivisionError", "除数为 0", lineno)
        r = x // y
    elif op == "%":
        if y == 0:
            raise ImporException("ZeroDivisionError", "除数为 0", lineno)
        r = x % y
    elif op == "**":
        r = x ** y
    elif op == "==":
        return x == y
    elif op == "!=":
        return x != y
    elif op == "<":
        return x < y
    elif op == ">":
        return x > y
    elif op == "<=":
        return x <= y
    elif op == ">=":
        return x >= y
    else:
        raise ImporException("BackendError", "未知运算符 %s" % op, lineno)

    # int64 溢出检查：仅 + - * 且两个操作数都是 int，语义与 NVM / impor run 一致
    if op in ("+", "-", "*") and type(x) is int and type(y) is int \
            and (r < I64_MIN or r > I64_MAX):
        raise ImporException("OverflowError", I64_MSG, lineno)
    return r


class RegisterMachine:
    def __init__(self, program, max_depth=MAX_DEPTH):
        self.program = program
        self.output = []
        self.max_depth = max_depth
        self.depth = 0

    def run(self):
        self._invoke(self.program.main_name, [])
        return self.output

    def _val(self, loc, regs, slots):
        kind = loc[0]
        if kind == "r":
            return regs[loc[1]]
        if kind == "i":
            return loc[1]
        return slots.get(loc[1], 0)

    def _put(self, loc, value, regs, slots):
        if loc[0] == "r":
            regs[loc[1]] = value
        else:
            slots[loc[1]] = value

    def _invoke(self, name, arg_values):
        if self.depth >= self.max_depth:
            raise ImporException("RecursionError",
                                 "递归深度超过 %d" % self.max_depth, 0)
        fn = self.program.functions.get(name)
        if fn is None:
            raise ImporException(
                "BackendError", "优化编译器后端找不到函数 %s" % name, 0)
        self.depth += 1
        try:
            regs = [0] * NUM_REGS
            slots = {}
            for i, val in enumerate(arg_values):
                regs[i] = val
            code = fn.code
            labels = fn.labels
            pc = 0
            nc = len(code)
            while pc < nc:
                ins = code[pc]
                pc += 1
                op = ins[0]
                if op == "LABEL":
                    continue
                if op == "SET":
                    self._put(ins[1], self._val(ins[2], regs, slots), regs, slots)
                elif op == "BIN":
                    x = self._val(ins[3], regs, slots)
                    y = self._val(ins[4], regs, slots)
                    self._put(ins[1], _apply_bin(ins[2], x, y), regs, slots)
                elif op == "UN":
                    v = self._val(ins[3], regs, slots)
                    uop = ins[2]
                    if uop == "not":
                        res = not bool(v)
                    elif uop == "-":
                        res = -v
                        if type(v) is int and (res < I64_MIN or res > I64_MAX):
                            raise ImporException("OverflowError", I64_MSG, 0)
                    else:
                        res = +v
                    self._put(ins[1], res, regs, slots)
                elif op == "JMP":
                    pc = labels[ins[1]]
                elif op == "JFALSE":
                    if not bool(self._val(ins[1], regs, slots)):
                        pc = labels[ins[2]]
                elif op == "CALL":
                    callee = ins[2]
                    argv = [self._val(loc, regs, slots) for loc in ins[3]]
                    ret = self._invoke(callee, argv)
                    self._put(ins[1], ret, regs, slots)
                elif op == "RET":
                    return self._val(ins[1], regs, slots)
                elif op == "PRT":
                    line = " ".join(str(self._val(loc, regs, slots))
                                    for loc in ins[1])
                    self.output.append(line)
            return None
        finally:
            self.depth -= 1


# ============================================================
# 对外便捷入口
# ============================================================
def run_ir_module(module):
    """编译并执行一个已优化的 IRModule，返回 (输出行, TargetProgram)。"""
    program = compile_ir_module(module)
    machine = RegisterMachine(program)
    output = machine.run()
    return output, program


def run_source(source, filename=None, log_cb=None):
    """源码 -> 解析 -> lower -> irop 优化 -> 编译 -> 寄存器机执行，返回 (输出, 程序)。"""
    from .ir_lower import lower_ast
    from .irop import optimize_module

    ast = parse_source(strip_header(source), filename)
    try:
        module = lower_ast(ast, filename)
    except IRError as e:
        # lower 阶段对不支持节点抛 UnsupportedNode，统一转成干净的 BackendError，
        # 不落到通用兜底写 crash.log。
        raise ImporException("BackendError",
                             "优化编译器后端暂不支持该语法（%s）；请改用 `impor run`" % e,
                             getattr(e, "lineno", 0))
    optimize_module(module)
    program = compile_ir_module(module)
    machine = RegisterMachine(program)
    output = machine.run()
    if log_cb is not None:
        for line in output:
            log_cb(line)
    return output, program


def disassemble(program):
    """把目标程序渲染成可读的寄存器式汇编文本。"""
    def loc_str(loc):
        if loc[0] == "r":
            return "R%d" % loc[1]
        if loc[0] == "i":
            v = loc[1]
            if isinstance(v, str):
                return repr(v)
            return str(v)
        return "S%d" % loc[1]

    lines = []
    for name in sorted(program.functions.keys()):
        fn = program.functions[name]
        lines.append("; ---- %s(%s)  块=%d 区间=%d 溢出=%d 寄存器=%d ----" % (
            fn.name, ", ".join(fn.params), fn.blocks, fn.intervals,
            fn.spilled, fn.used_regs))
        for ins in fn.code:
            op = ins[0]
            if op == "LABEL":
                lines.append("%s:" % ins[1])
            elif op == "SET":
                lines.append("    %-6s %s, %s" % ("MOV", loc_str(ins[1]), loc_str(ins[2])))
            elif op == "BIN":
                lines.append("    %-6s %s, %s, %s" % (ins[2], loc_str(ins[1]),
                                                       loc_str(ins[3]), loc_str(ins[4])))
            elif op == "UN":
                lines.append("    %-6s %s, %s" % (ins[2], loc_str(ins[1]), loc_str(ins[3])))
            elif op == "JMP":
                lines.append("    JMP    %s" % ins[1])
            elif op == "JFALSE":
                lines.append("    JFALSE %s, %s" % (loc_str(ins[1]), ins[2]))
            elif op == "CALL":
                args = ", ".join(loc_str(x) for x in ins[3])
                lines.append("    CALL   %s, %s(%s)" % (loc_str(ins[1]), ins[2], args))
            elif op == "RET":
                lines.append("    RET    %s" % loc_str(ins[1]))
            elif op == "PRT":
                lines.append("    PRINT  %s" % ", ".join(loc_str(x) for x in ins[1]))
        lines.append("")
    return "\n".join(lines)
