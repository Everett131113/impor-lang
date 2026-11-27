# RFC 0009：纯自足原生 x64 AOT 后端（自写机器码 + PE32+）

## 1. 为什么要改

RFC 0008 的 OPTC 已具备完整的中端分析与寄存器分配内核，但目标码仍由 Python 寄存器机
解释执行，没有真正落到硬件。本 RFC 补上「直接到硬件」的最后一块：在三地址 IR 之上，
**自己把指令编码成 x86-64 机器码字节、自己生成 Windows PE32+ 可执行文件**，全程不调用
nasm / gcc / ld 等任何外部工具链，产出可双击运行、结果与解释器逐字节一致的原生 exe。

关键战略前提：对标项目 XLang 的 x64 后端（`CompileX64.hs` 的 `assembleOne` /
`linkIfNeeded`）只生成汇编文本，再调用外部 nasm / as / ld 完成机器码化与链接，其自身
并不产生机器码字节、也不构造 PE。因此 Impor 做到「纯自足机器码 + PE」是真正的超越，
而非仅追平。

## 2. 变更内容

- 新增公开项：
  - `impor/x64asm.py`：x86-64 指令编码器。整数 0..15 编号寄存器，REX/ModR/M/SIB/
    立即数/位移编码与符号标签回填；覆盖本后端所需的算术、比较、乘除、字节存取、LEA、
    栈与 RIP 相对调用等指令。
  - `impor/pe64.py`：PE32+ 链接器 `link_exe`。自构 DOS/PE/COFF/240 字节可选头、节区、
    导入表与 IAT，回填 RIP 相对引用；`.text` + `.rdata` 两节，无重定位表。
  - `impor/native64.py`：原生后端。标量类型推断、复用 OPTC 的 CFG/活跃/线性扫描分配、
    机器码发射与整数转字符串等运行时例程；顶层入口
    `build_exe_from_source` / `build_exe_from_module`。
  - CLI 子命令 `impor native [run|build] FILE [-o OUT.exe]`。
- 删除/改名的公开项：无。语言表面零变化，不新增关键字、不新增语法糖（遵循 v3.5 冻结）。
- 受影响的错误码：统一复用 `BackendError`，不新增错误码。

## 3. 影响谁

- 已有脚本/项目：纯新增后端，默认 `run` 行为不变；只有显式调用 `impor native` 才走该
  后端。
- 其他后端：不改动其代码路径；与解释器逐字节对拍保证语义一致。
- 打包：便携版 spec 的 hiddenimports 增加 `x64asm` / `pe64` / `native64`。

## 4. 设计与边界

- 寄存器映射：OPTC 的 R0..R5 映射到非易失真实寄存器（RBX/R12/R13/R14/R15/RDI），
  函数 prologue 统一保存，跨 CALL 天然安全；RAX/RCX/RDX/R8..R11 作为易失 scratch。
- 栈帧：`push rbp` + 6 个保留寄存器（48 字节）+ 16 对齐的 work 区（spill 槽、
  shadow/home、print 用 intbuf）。第 5/6 个参数经 rbp 相对偏移读取。
- 运行时例程（全局一份，只用易失寄存器）：`__rt_write`（GetStdHandle + WriteFile）、
  `__rt_strlen`、`__rt_int_to_str`（负数与 INT64_MIN 正确，无符号 div + 栈反转）。
- 明确边界（编译期 BackendError，不静默降级、不写 crash.log）：
  - 浮点整体拒绝（SSE 运算与 dtoa 列后续）；
  - list/dict、索引/属性、class、try/catch、import、嵌套函数/闭包、内建调用等不支持
    操作同样干净拒绝；
  - 函数参数超过 6 个先拒绝。

## 5. 验收

- [x] `tests/test_native64.py` 41 用例全绿：编码逐字节、PE 结构、类型推断、exe 真实
  运行、解释器对拍、拒绝路径。
- [x] 生成 exe 真实运行，退出码 0；fib/递归/6 参数/控制流/深 spill 输出与解释器逐字节
  一致。
- [x] 边界含 INT64_MIN / INT64_MAX / 0 / 大整数，均正确。
- [x] 浮点/容器/内建等编译期 BackendError，crash.log 无变化。
- [x] 能力矩阵新增 Native64 列，六后端无「错误」格。

## 6. 决策记录

- 日期：2026-11-26
- 结论：自写机器码与 PE，而非调用外部工具链——这是与 XLang 的关键差异点，且使产物
  完全自足、可离线复现。
- 端到端调试中抓出并修复的真实缺陷（均有回归测试锁定）：
  - 整数转字符串长度计算误用寄存器；大小比较误用 `test`；`cmp` 后 `xor` 破坏标志；
    第 5/6 栈参数 rbp 相对接收；实参类型前向传播；合成兜底 RETURN None 污染返回类型。
  - **imm8 有符号范围 bug**：立即数判断曾允许 128..255 进入 imm8，但 ALU imm8 是有符号
    的（-128..127），`sub rsp,128` 被编码成 `sub rsp,-128`，栈帧方向反转导致退出访问
    违例。修正为有符号范围，128 起走 imm32。
- 后续：浮点 SSE 运算与 dtoa、栈传参解除 6 参数上限、图着色/SSA，分别独立评估。
