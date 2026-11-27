# Changelog

Impor (I++) 的版本变更记录。每条只列**用户可感知的变化**；详细说明见 `README.md` 的「更新日志」。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本（主版本.次版本.修订号）。

> **版本口径（2026-11-16 起）**：对外发布只有当前 `VERSION` 一条线（现为 `4.0.1`），`VERSION` 与 `Impor.py` 的 `__version__` 为真源。`v6.0` / `v6.2` 是历史内部路线与性能标签，`7.3.0` 是已归档的历史实验包，都不作为安装包版本。详见 `docs/版本对齐与语法冻结-3.6.6.md`。

## [未发布]

### 5.6.0：纯自足原生 x64 AOT（自写机器码 + PE32+，零外部工具链）

新增第五个执行后端，也是第一个**直接产出可双击运行原生 exe** 的真 AOT 后端：在三地址 IR
之上自己把指令编码成 x86-64 机器码字节、自己生成 Windows PE32+ 可执行文件，全程不调用
nasm / gcc / ld 等任何外部工具链。对标项目 XLang 的 x64 后端只生成汇编文本再调外部
nasm/ld，自身不产生机器码字节与 PE；Impor 做到纯自足机器码 + PE 是真正的超越。

**流水线（用户可感知）**
- 指令编码器 `x64asm.py`：REX/ModR/M/SIB/立即数/位移编码与标签回填，覆盖算术、比较、
  乘除、字节存取、LEA、栈与 RIP 相对调用。
- PE32+ 链接器 `pe64.py`：自构 DOS/PE/COFF/可选头、节区、导入表与 IAT，回填 RIP 引用。
- 原生后端 `native64.py`：标量类型推断，复用 OPTC 的 CFG/活跃/线性扫描分配，机器码发射
  与整数转字符串等运行时例程；`impor native build FILE [-o OUT.exe]` 产出 exe，
  `native run` 编译并立即执行。

**能力边界（明确拒绝，不静默改语义、不写 crash.log）**
- 支持标量过程式核心：int/bool/str/none、变量、算术/比较/逻辑、if/else、while、函数与
  递归（含 6 参数与深 spill）、print；整数边界含 INT64_MIN/INT64_MAX 均正确。
- 编译期 BackendError 干净拒绝：浮点（SSE/dtoa 列后续）、list/dict、索引/属性、class、
  try/catch、import、闭包/内建调用、超过 6 个参数。

**验证**
- `tests/test_native64.py` 41 用例全绿：编码逐字节、PE 结构、类型推断、exe 真实运行、
  与解释器逐字节对拍、拒绝路径。
- 能力矩阵新增 Native64 列，六后端 11 探针无任何「错误」格。
- 语言基线不变，无新增关键字、无语法糖。

### 5.5.0：教科书优化编译器后端 OPTC（寄存器式，无语法糖）

新增第四个执行后端，也是第一个**寄存器式、含完整中端分析**的自有后端：在三地址 IR 之上实现
教科书（龙书 / Engineering a Compiler）标准后端流水线，全程不依赖外部编译器/汇编器。

**流水线（用户可感知）**
- 基本块划分 + 控制流图（CFG）：识别 leader、构建块间后继（跳转/分支/顺序落入）。
- 活跃变量数据流分析：后向 gen/kill 不动点，求每个块的 live-in/live-out。
- 线性扫描寄存器分配：6 个物理寄存器 R0..R5，参数从 R0 起传递；寄存器不足时溢出（spill）
  到栈槽，栈槽编号唯一，使用前重载。
- 寄存器式目标码 + 寄存器机执行：直接操作寄存器数组，而非栈式虚拟机。
- `impor opt run FILE` 立即执行；`opt dump` 反汇编（含块/区间/溢出统计），`-o` 落盘。

**能力边界（明确拒绝，不静默改语义）**
- 支持标量过程式核心：int/float/bool/str/none、变量、算术/比较/逻辑、if/else、while、
  函数与递归、print。
- 编译期 BackendError 拒绝：list/dict、索引/切片、属性、class、try/catch、import、
  闭包/lambda、异常、for-in、global 作用域、内建调用（len/int/str 等）、超过 6 个参数。
- 整数仍为 int64，加减乘与一元负号越界报 `OverflowError [E1012]`，除零报运行时错误。

**验证**
- `tests/test_optc.py` 21 用例全绿（CFG/数据流方程/寄存器分配/溢出/对拍/拒绝）。
- 进程级对拍：`impor opt run` 与 `impor run` 输出逐字节一致；`tests/test_vm.py` 12 用例仍全绿。
- 能力矩阵新增 OPTC 列，11 探针无任何「错误」格；不支持项均为干净 BackendError（不写 crash.log）。
- 语言基线不变，无新增关键字、无语法糖。

### 5.4.0：NVM 执行提速 + 能力补齐路线图（无语法糖，不碰语言表面）

本版只做内核与工程化，不新增关键字、不新增语法糖，语言表面与 5.3.0 完全一致。

**NVM 性能优化（实测驱动，fib(28) 同一机器、Python 3.13）**
- 热点分支重排：`_run` 分派链按实测 opcode 频率（LOAD_FAST/LOAD_CONST/BINARY 最高）重排，降低平均比较次数。
- 内联 int64 溢出检查：消除热路径上高频的 `_i64` 方法调用，常见不溢出路径只做一次范围比较，`E1012` 语义不变。
- 调用快速路径：单参数调用（递归最常见）免去切片与删除，无 cell 的函数走紧凑参数绑定。
- 实测结果：NVM `fib(28)` 进程内从约 4.90s 降到约 3.58s（耗时降低约 27%），CLI `vm run` 从约 5.04s 降到约 3.73s；输出始终为 317811，`tests/test_vm.py` 12 用例全绿。

**一次诚实的负收益记录**
- 曾实现 peephole 超级指令（5 个融合 opcode，含跳转重映射），但实测在纯 Python VM 上重 handler 的 tuple 解包与重复检查成本超过省掉的循环开销（fib 反而变慢），经微基准确认方法分派同样为负收益后整体回退，保持轻量内联分派。

**能力补齐路线图**
- 新增 `docs/rfc/0007-nvm-coverage-roadmap.md`：按依赖与风险分阶段规划 import → 异常 → 类 → 并发，每阶段独立可发布、与解释器对拍验收，未完成项继续明确拒绝。

**工程卫生**
- 修复主开发工作树与发布物脱节：把 5.3.0（含 `nvm.py`、`test_vm.py`）回灌主开发工作树，覆盖前先备份；回灌后该工作树 12 用例全绿、`vm run` 正常。

### 5.3.0：自有字节码后端 NVM（不依赖外部编译器，无语法糖）

新增第三个执行后端，也是第一个**完全自有**的后端：C 后端依赖 gcc/tcc、WASM 依赖宿主，NVM 把 AST 编成栈式字节码（30 个操作码），由自带虚拟机直接执行，全程不调用外部编译器、不生成机器码、不做寄存器分配。

**新增（用户可感知）**
- `impor vm run FILE`：编译并立即执行；`compile`：产出 `.ibc` 字节码文件；`exec`：加载 `.ibc` 执行；`dump`：反汇编。
- `.ibc` 为 JSON 字节码容器（magic `IMPOR-IBC`），可跨机器拷贝后用 `exec` 加载，输出与 `run` 一致。
- 闭包用 Cell 盒子 + 两阶段作用域分析，支持 nonlocal 写回；`and`/`or` 真短路。

**能力边界（明确拒绝，不静默改语义）**
- 支持：变量、if/elif/else、while、for、break/continue、函数与递归、闭包、lambda、三元、字符串插值、list/dict 及常用内建。
- 拒绝并提示改用 `impor run`：class、import、try/catch、raise/exception、match、defer、yield、await、have、new，以及全部并发内建（`thread_spawn`/`mutex_*`/`atomic_*` 等，编译期拒绝，不写 crash.log）。

**整数语义对齐**
- 默认与 `impor run` 同一 int64 口径：整数加减乘与一元负号越界即抛 `OverflowError [E1012]`，不静默回绕；`--big` 切换任意精度。

**验证**
- `tests/test_vm.py` 12 个用例全部通过（含与解释器的进程级逐字节对拍、`.ibc` 往返、递归深度保护、并发内建编译期拒绝）。
- 能力矩阵新增 NVM 列：11 探针 8 支持 / 3 明确拒绝，零「错误」格；35 个过程式示例 31 通过，其余 4 个为 class/import 边界。
- 语言基线不变：关键字 55、标准库模块 37、错误码 30，无新增语法糖。

### 4.7.3：编辑器本身加固（崩溃兜底 / 进程树 / 编码保真 / 可验证 / 可观测）

4.7.2 收口的是语言内核；这一版只碰**编辑器**，目标是「自身够硬」——
出问题时能自己兜住、能自证、能留现场，而不是靠用户描述。

#### ① 崩溃兜底：最多丢 3 秒，重开接回原路径

以前只有 30 秒一次的自动保存，进程被强杀（任务管理器结束进程 / 断电）时，
最后 30 秒的输入没有任何副本。现在：

- 新增 `_recovery_tick()`：每 3 秒把所有 `_dirty` 标签写成 `recover_<id>.impor` +
  `recover_<id>.json`（path / title / bom / ends_nl / newline / reason / ts），
  **只写恢复目录，绝不碰用户原文件**；
- 恢复目录固定在 `~/.impor/recovery`（不再跟着 workspace 走——workspace 可能是只读目录或网络盘，
  写不进去就等于兜底静默失效）；
- `restore_recovery()` 恢复时把**原文件路径、BOM、换行、末尾换行**一起接回，恢复后 Ctrl+S 直接存回原文件；
- 关闭窗口时先落盘恢复副本，再问「仍要退出吗」，误点「是」也能还原。

实测（`tests/test_editor_headless.py::TestCrashDrill`）：写脏标签 → 等副本落盘 →
`psutil.Process(pid).kill()` 硬杀（不走 on_close）→ 重开带 `IMPOR_EDITOR_EXPECT_RESTORE=<标记>`
必须找回正文并接回路径。**通过**。

#### ② 进程树：Ctrl+C 真的停得掉整棵进程

旧版 `_term_interrupt` 只 `proc.kill()` 父进程，脚本里拉起的子进程（`subprocess`、
自举编译器、外部 gcc）会变成孤儿继续跑。现在走 `stability.kill_tree(pid)`：
Windows 用 `taskkill /F /T /PID`，POSIX 用 `killpg`，失败才回退单杀，并把结果写进输出。

实测（`TestKillTree`）：`python -c` 拉起一个 `sleep(300)` 子进程 → `kill_tree(父)` →
父子在 1 秒内都不存活。**通过**。

#### ③ 定时器链自愈：一次 tick 抛异常不再让整条链断掉

Tk 的 `after` 回调抛异常只交给 `report_callback_exception`，**链本身不会重排**——
自动保存、恢复副本、文件监视会就此永久失效且无提示。新增 `_after_safe(ms, fn)`：
异常写 `note_swallowed` + `crash.log`，然后**保证重排**；所有 `after` 链（autosave / recovery）
都改走它。

实测（冒烟 `after-chain` 步骤）：注册一个第一次必抛异常的 tick，1.5 秒内必须被触发 ≥2 次。**通过**。

#### ④ 编码保真：BOM + CRLF 文件保存后字节一致

两个真缺陷：

1. `editor_groups.open_new_tab(path=...)` 带路径打开时只当空标签建，不读盘也不识别编码 →
   从该入口打开 CRLF+BOM 文件，保存一次就被静默改写成 LF + 无 BOM；
2. Tk Text 的 `end` 天然带一个换行，`get_content()` 原样写盘 → **末尾没有换行的文件每存一次多一个空行**
   （CRLF 下 +2 字节）；正文里若残留 `\r`，`newline="\r\n"` 会写成 `\r\r\n`（每行多 1 字节）。

修法：打开时记录真实字节特征（bom / newline / `ends_nl`）；保存前 `_align_eol()` 归一
`\r\n`→`\n`、`\r`→`\n`，再按 `ends_nl` 决定末尾是否补换行，最后由 `open(newline=...)` 统一生成换行。

实测（冒烟 `encoding-roundtrip` 步骤）：写 `b"\xef\xbb\xbf" + "var a = 1\r\nprint(a)\r\n"` →
打开 → `save_file()` → 保存前后字节必须完全相同（26 B → 26 B）。**通过**。

#### ⑤ 跨盘路径：`relpath` 不再抛 ValueError

`os.path.relpath` 在 C: 路径对 D: 基目录时会抛 `ValueError`。标签栏每次刷新都调用它，
于是「保存后卡顿/异常」的真因是刷新标签时抛异常（`crash.log` 里能看到
`path to mount 'C:', start on mount 'D:'`）。新增 `impor/pathsafe.py` 的 `rel_quiet(path, base)`：
跨盘退化为 abspath 而不抛；8 个编辑器模块共 **35 处** `os.path.relpath` 全部替换。

实测（`TestRelQuiet`）：`rel_quiet(r"C:\Users\x\a.impor", r"D:\ImporWork\impor471")` 返回绝对路径且不抛。**通过**。

#### ⑥ 可验证 / 可观测

- 新增无交互冒烟 `IMPOR_EDITOR_SMOKE=1`：不显示窗口，跑完即退出，返回码即结论。
  六步：`new-tab → save-readback → recovery → encoding-roundtrip → after-chain → diagnostics`；
- 新增回归文件 `tests/test_editor_headless.py`（10 项：换行对齐、跨盘路径、进程树、冒烟六步、强杀恢复、崩溃日志）；
- 新增崩漏现场 `install_crash_hooks()`（`sys.excepthook` + `threading.excepthook` → `~/.impor/crash.log`，幂等）；
- 新增诊断面板：命令面板「内部诊断（崩溃 / 被吞异常）」→ 被吞异常按 area 的次数排行 + 崩溃现场 + 最近 120 条记录 + 一键打开日志目录；
- 关键路径补 `note_swallowed`：`editor.save` / `editor.save.audit` / `editor.save.tmp_cleanup` / `editor.autosave` / `editor.recovery.write` / `editor.recovery.restore` / `editor.after:*` / `editor.term.interrupt`。

复跑命令：

```powershell
$env:IMPOR_EDITOR_SMOKE="1"; & "D:\编程软件\python3.11\python.exe" "Impor.py"   # 期望 SMOKE PASS + rc=0
& "D:\编程软件\python3.11\python.exe" "tests\test_editor_headless.py" -v        # 期望 Ran 10 tests OK
```

### 4.7.2：Val 收紧到 16 字节 tagged union + 语言层内联 + 区间证明收益实测

这一版处理 4.7.1 里明确标注「未做」的两项里能真正闭环的部分：**值表示**与**内联**，
并把上一版没敢引用的性能数字用干净对照重测了一遍。

#### ① Val 从 56 字节收紧到 16 字节（tagged union）

```c
/* 4.7.1 及以前 */  typedef struct { VType t; long long i; double f; char *s; Arr *a; Obj *o; Fn *fn; } Val;  /* 56 B */
/* 4.7.2 */        typedef struct { VType t; union { long long i; double f; char *s; Arr *a; Obj *o; Fn *fn; }; } Val;  /* 16 B */
```

依据：一个 `Val` 在任一时刻只有一个类型，6 个指针槽天然互斥。改成匿名 union 后，
`v.t` / `v.i` / `v.s` 这些写法**一字未改**，895 个字段访问点全部保持原样。

**踩到并修掉的真缺陷**（写在这里防止重犯）：旧代码惯用「先把所有槽清零，再赋目标槽」：

```c
v.t = V_STR; v.i = 0; v.f = 0; v.s = strdup(s); v.a = NULL; v.o = NULL; v.fn = NULL;
```

在 7 槽结构下这是保险，在 union 下 `v.a = NULL` 会把刚写的 `v.s` **重新清零**（同一地址）。
症状是字符串全部打印 `(null)`，而 `tag` 仍然正确 —— 极易误判成编码问题。共 23 处构造点，
统一改成「只写 tag + 目标 payload」。

**硬门禁**（防止以后又被悄悄改回去）：

- 编译期：`typedef char val_size_must_be_16[(sizeof(Val) == 16) ? 1 : -1];`，两处定义各一条，谁加字段谁编译失败；
- 源码期：`tools/check_val_layout.py` 检查两处 typedef 必须是 union、且同一条构造语句里 payload 槽最多 1 个。

**实测（同机、同脚本、同 gcc -O3、只差 Val 布局）**：

| 指标 | 4.7.1（56 字节） | 4.7.2（16 字节） | 变化 |
| --- | --- | --- | --- |
| `sum1e8` 运行 8 轮最小值 | 0.0457 s | 0.0452 s | -1.1%（在噪声内） |
| 100 万元素数组峰值 RSS（psutil 采样） | **52.54 MB** | **18.44 MB** | **-64.9%（2.85×）** |
| `sizeof(Val)` | 56 | 16 | -71.4% |

结论口径要精确：**收益在内存与缓存，不在数值内循环的时间**。`sum1e8` 走的是标注单态化后的
原生 `long long` 循环，Val 根本不进内循环，所以时间不变才是正确的；数组元素、栈帧、参数数组
这类「Val 真正落内存」的地方，收益是 2.85×。

#### ② 语言层一般内联（新 pass `impor/inline.py`）

AST → AST 的 pass，接在 `link_modules` 与 `_reject_const_mutation` 之后、`CGen.gen_program` 之前，
完整后端与 IR 后端共用。

判据是白名单式的（详见 `docs/inline-rules.md`）：候选必须是「单 `return` 表达式 + 参数≤4 + 体积≤40 节点
+ 表达式中无 Call/副作用/控制流 + 不自递归」；调用点还要求「形参用 0 次则实参须纯、用 1 次可展开、
用多次则实参须纯」。

**报错位置判据（单独定死）**：内联进来的整棵子树，`lineno`/`col` 一律改写为**调用点**的。

实测对照（`tests/inline/inl2.impor`，第 8 行 `var r: int = mul2(big)` 触发 E1012，函数体在第 4 行）：

| 配置 | 报错行 | 调用栈 |
| --- | --- | --- |
| 内联开启 | **第 8 行**（`var r: int = mul2(big)`） | `<main>` |
| `IMPOR_NO_INLINE=1` | 第 4 行（`return x * 2`） | `mul2（第8行）` |

两者指向同一次调用：内联版落在用户真写的那一行，非内联版落在函数体并补一层调用点。**行号没有丢，只是少了一层栈。**

其它实测：`inl1.impor`（`add1(twice(sq(i)))` 三层嵌套）实际展开 **2 处**（`IMPOR_INLINE_DEPTH=2` 限制了最外层），
输出 `sum=19` 与手算一致；`inl3.impor`（`dbl(len(xs))`，形参用两次而实参是 Call）正确跳过、输出 `6`。

开关：`IMPOR_NO_INLINE` / `IMPOR_INLINE_MAX_NODES` / `IMPOR_INLINE_DEPTH` / `IMPOR_INLINE_TRACE`。
构建摘要新增一行 `内联：N 处单表达式小函数已在调用点展开（报错位置仍指向调用点）`。

#### ③ 区间证明收益：补测（4.7.1 时因机器被占用未敢引用）

同版本、同 Val 布局、同 gcc -O3，只切 `IMPOR_NO_RANGEPROOF`：

| 配置 | `sum1e8` 8 轮最小值 |
| --- | --- |
| `IMPOR_NO_RANGEPROOF=1`（每步都查溢出） | 0.0648 s |
| 默认（区间已证，免检） | **0.0410 s** |
| 加速 | **1.58×（-36.7%）** |

参考：纯 C `-O3` 同脚本 0.0446 s —— 即 Impor 在已证循环上已经略快于手写 C。

#### ④ 修掉跨盘构建崩溃（真 bug，不是环境问题）

源文件放在 `C:` 盘临时目录、而 `D:` 盘上有安装目录的 std 模块时，`impor build` 会直接抛
`ValueError: path is on mount 'D:', start on mount 'C:'` —— 根因是模块指纹计算里的
`os.path.relpath(cand, base)` 跨盘无意义。

```python
try:
    rel = os.path.relpath(cand, base)
except ValueError:
    rel = os.path.abspath(cand)
h.update(b"\0" + rel.encode("utf-8", "replace"))
```

它只在 Windows 多盘机器上复现，之前工作树一直在 `C:` 所以没暴露；这次工作树搬到 `D:` 立刻命中，
`tools/verify_release.py` 的 `stdlib_c`（六个 std 模块在 C 后端可用）因此从 FAIL 转 OK。

#### ⑤ 8 字节 NaN-boxing：仍未做，但已把障碍量化

`docs/nanboxing-design.md` 记录了完整设计与实测规模（`tools/count_val_access.py` 可复现）：
访问点 **895 个**（820 读 / 75 写，分布 t270/i74/f27/s175/a304/o30/fn15），以及真正的硬障碍 ——
Impor 的整数是 **int64 全范围**（越界报 E1012），64 位载荷里没有空闲位放 tag，要么把整数限到 48/60 位、
要么全 boxed、要么放弃 double 同构，三种都要付语义代价。这不是"加个位运算"能解决的，需要先在语言层做决策。

### 4.7.1：自举 fixpoint 真正成立 + 语言层 TCO / 常量免检 + 包索引求解

这一版的重心是「把 4.7.0 里"报告写着、实际没跑通"的那几项真正跑通，并把证据留在仓库里」。
判据一律可复算：`tools/selfhost_fixpoint.py`、`impor/versionsolve.py`、`tools/pkg_index.py`、
`tools/check_posix_port.py` 都能一条命令复现。

#### ① 自举 fixpoint 成立（`tools/selfhost_fixpoint.py`）

链路 A → B → C → D，主判据是**源码 fixpoint**（C.c 与 D.c 逐字节一致），二进制一致是加分项：

| 环节 | 结果 |
| --- | --- |
| A 编译种子 → a.c | 340441 字节 |
| B = gcc(a.c) | 1155131 字节 |
| C.c = B 编种子 | 340441 字节 |
| D.c = C 编种子 | 340441 字节 |
| 源码 fixpoint（C.c == D.c） | ✅ 两侧 SHA256 相同；a.c 也是同一份 |
| 二进制 fixpoint（同参编 C.c / D.c） | ✅ 只差 PE 时间戳与校验和（逐字节比对，无实质差异） |

跑通之前卡住的四件事，全部是工具层面的真缺陷，已逐条修掉：

1. **引导器必须用当前前端重建**。仓库里的 `bootstrap/imporc_py.exe` 是历史产物，内嵌的
   `rt_system` 与当前 `impor/c_runtime.c` 不一致：同一份命令串下 `CreateProcessW` 直接返回
   FALSE，连 `C:\Windows\System32\where.exe` 都启动不起来（种子只报一句 `rc= -1`）。
   现在 `--seed` 默认走 `impor build bootstrap/imporc.impor`（Python 前端 + 当前运行时），
   `--use-vendored` 才用旧二进制。
2. **种子编自身会爆栈**：默认 1MB 栈 → `0xC00000FD`。重建后统一把 `SizeOfStackReserve`
   补到 64MB（`patch_stack_exe`）。
3. **`.bat` 不能用 `CreateProcessW` 启动**：种子调 `bootstrap\cc64.bat`，而 CreateProcess 只认 PE。
   新增 `bootstrap/cc64stub.c`：编译成 PE、命名为 `cc64.bat`（CreateProcess 不检查扩展名），
   gcc 路径经 `IMPOR_CC64_GCC` 传入、并把 gcc 的 bin 目录前置进子进程 PATH
   （否则 `cc1.exe` 0xC0000135 静默失败）。
4. **二进制比较必须同参且同源文件名**：第一版拿「种子自产的 C.exe」和「工具重链的 D.exe」比，
   编译参数与源文件名都不同，白报 757272 字节差异；修正后只差时间戳/校验和。

#### ② `system()` 在 Windows 上统一走 `cmd.exe /c`

`rt_system` 原来是直调 `CreateProcessW`，只能启动 PE：`.bat` / `.cmd` / `echo` 这类 cmd 内建命令
一律返回 `-1`。改为 `cmd.exe /c "<cmd>"` 后与 POSIX `system()` 语义对齐。实测探针（三条）：

```
echo hi-ok > sys_echo.txt   rc=0   文件已生成
".\runme.bat" 7             rc=7   退出码透传，bat 内容生效
runme.cmd 9                 rc=9   同上
```

#### ③ 语言层自尾递归 → while（`impor/monomorph.py`）

`def f(...) { ... return f(...) }` 形式的自尾递归在单态化实例里直接生成
「先算全部实参到 `__tail<i>`，再写回形参，然后 `continue`」的循环体，外层用 `while (1)` 包住。
`IMPOR_NO_TAIL=1` 可关闭用于对照。**硬证据（都按 `-O0` 编，排除 gcc 自己的 sibling call）**：

| 产物 | 结果 |
| --- | --- |
| TCO 版（`mono_tail=1`） | rc=0，0.24s，输出 `500000500000` / `465` |
| 无 TCO 版（`mono_tail=0`） | rc=3221225725（`0xC00000FD` 栈溢出），8.31s 后崩溃 |

顺带修掉两个自己引入的坑：常量特化 + 自递归会把形参内联成字面量 → 写回后不再被读 → `while(1)` 死循环，
所以自递归函数不做常量特化；`IMPOR_NO_TAIL=1` 时 `stmt` 仍生成裸 `continue` 会编译失败，已按
「外层是否真的建了 `while(1)`」判定。

#### ④ `-O3` + `-flto`，失败自动回退

`impor build` 在 gcc、非 `--lib`、`opt>0` 时追加 `-flto`；链接失败则去掉 `-flto` 重试一次并把
`stats["lto"]="fallback"` 记进摘要 —— 不静默降级、也不因工具链不支持而整体失败。

#### ⑤ 区间证明免检 + 常量特化（`impor/rangeproof.py`）

整数区间抽象解释：算术点在 int64 范围内就不生成 `rt_ck_add/sub/mul`，只保留必要检查。
`for i in range(...)` / `while i < 常量` + `i = i + 常量` 两种循环形状能给循环变量定区间。
调用点全是字面量时按常量特化生成专用实例（`gp_sum_to_i64_k100000000`），体内
`for (v_i = 0; v_i < 100000000LL; v_i += 1) { v_total = (v_total + v_i); }` 完全没有 `rt_ck_add`。
通用实例仍保留检查作为对照，输出 `4999999950000000` 正确。
**性能数字尚未在空闲机器重测**（当时的 76.2ms / 80.7ms / 145.2ms 受后台编译污染），本版不引用。

#### ⑥ 包索引与版本约束求解（`impor/versionsolve.py` + `tools/pkg_index.py`）

- 约束语法：`*` / 裸版本 / `>= <= == != > <` / `^`（major>0 → `<major+1.0.0`，major==0 → `<0.(minor+1).0`）
  / `~` / 逗号或空格分隔的合取。`^`、`~` 后面没数字直接报错，不静默当「任意版本」。
- 求解：DFS + 回溯、MRV（候选最少的先定）、每步记录 `pick`/`fail`，超 `MAX_NODES=200` 明确报错而不是假装能解。
- `impor/pkgmgr.py` 的 `ver_satisfies` 委托新求解器；`_check_dep_conflicts` 从「字符串不同就冲突」
  改成真区间交集（`>=1.0` 与 `>=1.0,<2.0` 不再误报）。
- `tools/pkg_index.py index|verify|resolve|selftest`：索引 `*.zip` 内的 `impor.toml`，校验
  sha256/size/身份/未索引孤儿文件。`SELFTEST PASS`：3 包 / 7 版本 → 求解 3 步 → 篡改包报哈希+大小
  不符 → 新增未索引包被报出。质量线写在工具头：**索引不含签名，信任来自发布渠道**。
- 遗留：`pkg install` / `lock` 主流程仍走旧路径，只切了冲突预判与约束语义。

#### ⑦ 绑定生成器（`tools/bindgen.py`）

从 C 头文件生成 Impor 绑定：`gcc -E -P` 预处理、去 `__attribute__/__declspec/_CRTIMP/__cdecl/WINAPI/inline`、
深度扫描删函数体、按 `;` 分条匹配原型、类型映射 int/float/cstr/void/bool，跳过
struct/union/enum、指针参数、函数指针、可变参、>8 参并记账。
`impor/tcc/win32/include/stdio.h` → 36 个包装 / 跳过 110（指针 21、FILE 51、可变参 22、va_list 12、void 4），
产物 `bindings/auto/cstdio.impor` `check-types` PASS；端到端 `puts("hello-from-bindgen")` 返回 0。

#### ⑧ POSIX 可移植性审计（`tools/check_posix_port.py` + `tools/build_posix.sh`）

去注释后按 `#if` 栈判断 `_WIN32` 块是否有 `#else`，并检测不在保护内的 Windows API 直调。
`impor/c_runtime.c` 3551 行：50 个预处理块、39 个含 `_WIN32`（31 个有 `#else`）、
**0 处无保护致命调用**，报告落 `docs/posix-audit.json`。另加 `--static` 与
`tools/build_posix.sh`（Linux 优先 `musl-gcc`，macOS 用 clang 并明确降级提示）。
**边界**：本机是 Windows，Linux/macOS 产物只能交给 CI 实编，本机无实跑证据。

#### ⑨ 版本单一真源（`tools/version_sync.py`）

`VERSION` 是唯一真源，派生点（`IR_VERSION`、`vscode-impor/package.json`、两份 baseline、
`pack_release.py`、`bootstrap/imporc.impor` 注释与横幅、README 两处、`docs/claims.md`）
由 `--set` 统一改写、`--check` 统一校验。顺手修掉 `tools/pack_release.py` 里写死的 `VER = "4.0.1"`。

#### 明确未做（不写成"已完成"）

- **NaN-boxing / tagged 值表示**：要重写 `Val` 表示、全部 C 生成与运行时，本版未动。
- **语言层一般内联**：只做到 `-flto`（编译器层）+ 自尾递归转循环 + 常量特化。
- **跨平台实编译**：静态审计通过、脚本与 CI 就绪，但本机无法实编 Linux/macOS 产物。
- **区间证明性能复测**：数据受污染，未引用。
- **`pkg install/lock` 主流程**：未切到新求解器。

### 4.7.0：把「精致化」五项一起做完

这一版不是加功能，而是把「粗糙但能交付」推向「精致且可信」：类型检查分层、单态化按标注定型、
IR 上补三项优化、长循环性能、以及把验收脚本收进仓库。原则不变：**能实测的给数字，没做到的当场写"未做"**。

#### ① 渐进类型检查（新增 `impor/semantic.py` + `impor check-types`）

- `impor/typecheck.py` 从 3.8.1 起就在 run/build/compile 入口硬拦类型错误；本版在其上补三类
  它没覆盖、且代价最低的检查，并**默认降为 WARN**，`--strict-types` 才升 ERROR：
  - `arith-type`：算术两侧类型不可能成立（`"a" - 1`、`[1] * 2`、`str % int`）；
  - `int-range`：整数字面量超出 int64（`2**63`），把 E1012 从运行期提前到解析后；
  - `missing-return`：标注了返回类型、函数体却完全没有 `return`（且不是以 `raise` 结尾）。
- 新增命令 `impor check-types FILE... [--json] [--strict-types]`（退出码 0 / 1 / 3），
  与 `run/build/compile` 的入口门禁共用同一套代码；`--strict-types` 是全局开关（`--` 之后的脚本参数不受影响）。
- 零破坏验证：全仓 **267 个 .impor 扫一遍，warn 0 / error 0**（`examples` / `tests` / `std` /
  `benchmarks` / `conformance` / `bootstrap` / `tools`）。唯一条被拦下的是
  `examples/c_abi/lib.impor` 的 `size_t * int`，属 C ABI 手柄类型的常规写法，已列入白名单。
- **顺手修掉一个自己造出来的假类型**（这才是这一版最该修的东西）：第一版 `arith-type` 直接复用了
  `typecheck._type_of` 的类型推断，而父类为了给出更友好的提示，会对**无返回标注的函数猜一个类型**
  （按函数体 push 的顺序默认 `int`）。于是自举编译器 `bootstrap/imporc.impor` 里
  `gen_stmt` 的 `ev`（`var ev = parse_expr()`，实际是字符串）被猜成 `int`，
  `opfn + "(" + curp + ", " + ev + ")"` 一次报 **8 条 `arith-type` 假警告**。
  修法：`semantic.py` 新增 `_strict_type`，只信「字面量 / 本函数带标注的变量 / 带返回标注的调用」，
  推不出就返回 `?` 并跳过 —— 与模块头写的「只有确定成立才报」对齐。
  修完：`bootstrap/imporc.impor` 8 条 → **0 条**，`%TEMP%` 的坏样本 3 条诊断（`arith-type` / `int-range` /
  `missing-return`）原样保留。

#### ② 单态化从「字面量定型」扩到「标注定型」

- 4.6 只处理泛型 `def f<T>(a:T)`，且只有调用点写字面量才能定型，普通函数（`def sum_to(n:int)->int`）完全没机会。
- 本版把**全部参数与返回是同一个标量标注**的函数也纳入候选：`def sum_to(n:int)->int` → `gp_sum_to_i64(long long)`。
  判据近乎保守：标注重合、函数体整段落在原生子集内（否则直接跳过，回退擦除版），擦除版始终保留。
- `_NativeGen` 新增 `for i in range(...)` 支持（含 `range(a,b)` / `range(a,b,step)`、负步长），
  循环变量与累加器都声明在函数开头的原生类型里，循环体内不再出现 `Val`。
- **语义守住了 E1012**：原生实例的 `+ - *` 改走 `rt_ck_add/sub/mul`（纯 `long long` 入参，非 `Val`），
  溢出照常报错，与擦除版文案一致。`--no-int-ck` 可关闭逐运算检查换成裸 C 算术（默认**不关**）。
  实测：`add1(INT64_MAX)` 在 mono 路径上报 `E1012` 并退出 1，不静默回绕。
- 开关：`--no-mono` 整条关闭单态化（排查或对比用）；`--no-int-ck` 关闭原生实例的溢出检查。

#### ③ IR 三项优化 pass（新增 `impor/irop.py`）

- 在 IR 上（不是 AST）做，理由写在模块头：AST 的求值顺序里藏着副作用，IR 是显式三地址码，读写集合清楚。
- 三项都是「可证明安全」的最小形态：
  - **常量折叠**：`BINOP dst, op, Lit, Lit` → `CONST`；结果越 int64 或除零时不折叠，留给运行时 E1012；
    `JUMP_IF_FALSE Lit, L` 按真值简化成删除或无条件跳转。
  - **死代码消除**：只删「目的量全函数从未被读」的 `CONST` 与 `MOVE`（无副作用、不抛错）；
    算术即使结果没人用也**不删**（可能抛溢出/除零，删了会改变错误行为）；顺带删「JUMP 到下一条 LABEL」。
  - **循环不变量外提**：识别 `LABEL L ... JUMP L` 回边区间，把区间内「读循环内从未被写的名字」的 `LOAD`
    与常量 `CONST` 提到循环前；只处理互不重叠的区间，避免嵌套后索引错位。
- 求值器（`ir.evaluate`）与 IR→C（`cir.to_c`）走同一套优化，所以两边等价由三路对拍兜住。
  `impor ir --stats` 现在多打一行：`折叠 N / 删死代码 N / 外提不变量 N / 简化跳转 N（指令 a → b）`。
- 实测（`%TEMP%` 里两个专门用例）：`1 + 2 * 3` 折成 `CONST 7`（23→22 条）；
  `for` 里读循环外 `base` 的 `LOAD` 外提 1 次，输出与解释器一致（50）。
- 全量对拍：**一致 50 / 拒绝 6 / 构建失败 2 / 非确定跳过 2 / 不一致 0**（与 4.6.0 的 52/6/2 相比，
  两个非确定用例——掷骰子与图标宽度——单列成 `nondet`，不再混进「不一致」，也不静默丢弃）。

#### ④ 确定性门禁入库（新增 `tools/jitter.py` + `tools/verify_ir_coverage.py`）

- 4.6 收尾时的四个真缺陷全靠 `%TEMP%` 临时脚本抓到，CI 一条都没覆盖。本版把两个脚本正式收进仓库：
  - `tools/jitter.py FILE --runs 30`：同一个产物重复跑 N 次，比 `rc / stdout / stderr`，出现两种组合即失败；
    支持 `--via build|ir`、`--cc auto|gcc|tcc`（优先 gcc，其次内置 tcc），缺编译器明确 SKIP。
    实测 `examples/grade.impor` 20 次：**稳定（1 种组合）**。
  - `tools/verify_ir_coverage.py`：`examples` 全量逐个「解释器（`--no-native`） vs `IR→C 产物」对拍，
    不一致一律失败；拒绝/构建失败**只能减少不能新增**（与 `tools/ir_coverage_baseline.json` 比对）；
    缺 gcc 时 SKIP 并打印原因，不静默算绿。
- 顺手把「非确定用例」建成显式分类：`demo_38_dice`（时间随机种子）、`icon_demo`（终端宽度），
  报告里单列 `nondet` 计数，不再污染不一致统计。

#### ⑤ 定位口径（README 首屏）

- 定位从「教学语言 + 小型工具 + 可发布玩具」改为「**可交付的小型教学语言**」（4.7.0 之前的口径已过时）。
- 新增「**明确不竞争的维度**」表：生态 / 类型系统严格度 / 后端深度 / 性能极限，逐条写现状与不争的理由；
  对应地写「竞争的维度」：构建与启动速度、诊断质量、便携交付、三端一致、可自举。
- 原先那句「它没有真线程 / async、泛型、yield…」已不成立（4.5/4.6 已有泛型、atomic/channel、exception），本版删除。

#### 未做到（明确记录）

- **1e8 长循环压到 1.2× 这个目标，在保留 E1012 逐运算检查的前提下没有达成。**
  实测（同一台机器、8 轮交替取最小值，`for` + `sum += i`，1e8）：
  | 路径 | 时间 | 相对手写 C |
  | --- | --- | --- |
  | 手写 C（`gcc -O2`） | 43.7 ms | 1.00× |
  | 4.6 擦除版（`--no-fast --no-mono`） | 296.7 ms | 6.8× |
  | 4.7 原生实例 + E1012 检查（默认） | 71.8 ms | 1.64× |
  | 4.7 原生实例 + `--no-int-ck` | 43.1 ms | **0.99×** |
  结论：从 **6.8× 压到 1.64×**（默认档，仍保留溢出报错）；要 C 同级必须显式 `--no-int-ck` 放弃逐运算检查。
  1e8 次溢出判定本身约 +25 ms，1.2× 在这个约束下物理上做不到 —— 这一点不掩饰。
- 单态化的原生子集仍不含字符串拼接、内建调用（`print` 等）、`while` 里的非 `range` 迭代器；
  命中不了就回退擦除版，行为不变但也拿不到收益。
- IR 的三个 pass 都只做最小形态：不做跨分支的全局活跃分析、不做循环展开/向量化、外提只处理互不重叠的循环。

### 4.6.0：把上一轮点名的三条短板补完 + 自举 E1013

上一轮（4.5.0）对外承认了三条短板，这一版逐条兑现；顺手修掉自举链里既有的 `E1013`。
原则还是那条：**能实测的给数字，没做到的当场写"未做"**。

#### ① 泛型单态化（新增 `impor/monomorph.py`）

- 之前的泛型是"声明期约束 + 运行期擦除"：`def max2 < T >(a : T, b : T) -> T` 生成的 C 里
  只有 `static Val f_N(Val, Val)`，每次调用都走 `Val` 分派。
- 现在按**调用点静态类型**生成专用实例：`max2(9, 2)` → `gp_max2_i64(long long, long long)`，
  `max2(2.5, 3.5)` → `gp_max2_f64(double, double)`，`ident(7)` → `gp_ident_i64(long long)`。
  实例函数体内部的算术/比较/逻辑**全部是原生 C 运算**，只在 `return` 处装箱一次。
- **擦除版一律保留**作回退：实参不是字面量（静态类型不可知）、函数体含字符串拼接/内建调用/
  打印/`for` 迭代器、或类型参数不是单个 `T` 时，仍调用原来的擦除版，行为与 4.5.0 完全一致。
- 覆盖边界（未做，明确记录）：只认 `i64` / `f64` 两种实例；`str` 与自定义类型不单态化；
  不做局部变量/返回值的类型推导（只按字面量定型）。
- 实测（`-O2`，200 万次 `max2` 调用，同一台机器）：单态化命中 **0.35 s**，回退擦除版 **0.45 s**。

#### ② 整数语义统一（新增 `impor check-int` + `docs/整数语义.md`）

- 交付层只有一档语义：**int64**。`--big`（任意精度）降级为**仅解释器可用的调试档**：
  `impor run FILE --big` 会先打一行 stderr 提示"这条路径不可移植"，`impor build FILE --big`
  直接以 `EXIT_USAGE(3)` 拒绝，不再静默产出与解释器语义不同的产物。
- 新增 `impor check-int FILE [--json]`：静态扫描字面量，判定"能否按 int64 交付"。
  逐文件输出 PASS/FAIL，退出码 0/1/3；`--json` 给出 `version/files/literals/min/max/violations`。
  一元负号按组合判定：`-9223372036854775808` 合法，`-9223372036854775809` 非法。
- 四端 E1012 文案对齐：`c_runtime.c` 的 `rt_ck_add/sub/mul` 与 `rt_neg` 补上算子名
  （"（加法）/（减法）/（乘法）/（取负）"），`rt_diag_why` / `rt_diag_fix` 与解释器逐字同文；
  `cir._lit_c` 的超界常量也改成同一措辞。四端差异只剩外层头部与退出码，见 `docs/整数语义.md` §4。
- 边界：`check-int` 只查**字面量**，运行时溢出仍由运行时报 E1012（例：`a * b` 的乘积越界）。

#### ③ IR 补齐类 / 异常 / 模块导入（`impor ir` / `impor ir --build`）

- **异常控制流**：IR 新增 `TRY_BEGIN / TRY_END / CATCH_BEGIN / CATCH_END / TRY_CLOSE / RAISE /
  ERR_MATCH / RETHROW`；参考求值器加了 `try_stack`（`catch` 体内再抛交给外层，`finally` 保证执行）；
  `cir` 用与 `ctrans` 同构的 `setjmp/longjmp` 模板生成 C。
- **类模型**：`IRClass`（名字/父类/方法/字段初始化）+ `NEW / GETATTR / SETATTR / CALLMETHOD /
  SUPERCALL` 五条指令；求值器有 `_Instance`（字段回落到属性），`cir` 生成方法 impl + `mw_*` 包装 +
  `mt_*` 方法表 + `fi_*` 字段初始化 + `ci_*` 的 `ClassInfo`，并在 `main` 开头按声明顺序 `reg_class`。
  `super().m()` 依据当前类的 `base` 链解析。
- **模块导入**：IR 不再自己造模块系统，**复用 C 后端的 `clink.link_modules`** —— `use std.x` /
  `import std.x` 在 AST 层面就拼平（依赖在前、按需裁剪），IR 只描述拼平后的单文件程序。
  因此 `std_demo` / `std_v39_demo` / `demo_19_split_join` / `demo_21_word_frequency` 这类
  "用标准库"的程序现在也能走 `impor ir --build`。
- **并发内建明确拒绝**：`thread_spawn` / `mutex_*` / `cond_*` 在 IR 下降阶段直接报
  `UnsupportedNode`（参考求值器是单线程、没有调度语义），不再生成会在 gcc 阶段炸掉的坏 C。
- 仍未支持（明确拒绝，不静默降级）：嵌套 `def` / `lambda`（闭包对象没有 IR 表示）、
  `match` / `defer` / `await` / `|>` / `exit` / `cls()`。
- **验收**（`%TEMP%\irverify\verify_irpar.py`，66 个示例逐个 `--build` 后与解释器逐字节比对）：
  **52 逐字节一致 / 6 显式拒绝 / 2 构建失败 / 0 不一致**（4.5.0 是 36 / 21 / 3 / 0）。
  6 个拒绝是 `channels` / `closures` / `demo_30_closures` / `demo_26_struct_point` / `stdlib` / `struct`；
  2 个构建失败是 `cn_demo` / `err_demo` 这种**故意演示错误**的示例（`E2002` / `E2003`）。

#### ④ 自举 B→C 的 E1013（`MemoryError: 内存不足`，第 0 行）

- 复现：`bootstrap/selfhost_check.py` 的 A→B 成功，B→C 报 `[E1013]`，调用栈只有 `<main>`。
- **根因是内存天花板，不是坏指针**：给 B 加内存采样后确认它从 60MB 一路涨到 **2018MB**
  后失败 —— B 是 tcc 产出的 **32 位** PE，用户地址空间只有 2GB。
  增长来自 Impor 版编译器把输出 C 用 `gfuncs = gfuncs + 段` 累积，累计复制 O(n²)，
  编译 104KB 的种子自身时峰值约 **2.01GB**（64 位实测值）。
- **修法**：自举链全程改走 64 位 —— A 产出的 `out2.c` 用 64 位 gcc 编成 B；
  种子内编译产物改走新增的 `bootstrap/cc64.bat`（优先 gcc，找不到才回退内置 tcc）。
  同时把自举 exe 的 `SizeOfStackReserve` 提到 64MB（64 位产物默认 2MB，深递归会栈溢出）。
- 附带说明：`impor/imporc.exe`（纯 C 引导器）本身不受影响，它的输出是流式的；
  Python 版 `impor build` 也不受影响。**受影响的只有"Impor 版编译器编译自身"这条链**。
- 未做（记录在案）：`gfuncs` 的 O(n²) 累积本身没有改成流式输出/rope，所以峰值仍在 2GB 量级；
  32 位工具链下这条链依然会失败（现在会明确提示用 64 位 gcc，而不是崩一个 E1013）。

#### ⑤ 顺带修掉：`try/catch` 在 gcc -O2 下约 46% 概率崩溃（既有缺陷）

- **怎么掉出来的**：IR 对拍时同一脚本的解释器端偶发 `rc 1/0`（5 次里 1 次、20 次里 8 次）。
  `--no-native` 跑 12 次全过 → 问题不在解释器，在 `impor run` 默认的原生产物。
- **缩小**：`impor build examples/grade.impor` 的 exe 跑 20 次 **6 次 `0xC0000005`**，
  stdout 停在 `try` 之前；同一份 C 用 `-O0` 编 20/20 通过、`-O2` 编 6/20 崩 → 与优化相关。
- **定位**：注入日志后看到 `[ljmp] idx=0` 之后立即崩；`jmp_buf` 256 字节在保存时与跳转前
  **逐字节相同**（没被踩坏）；gdb 停在 `ntdll!RtlVirtualUnwind2`。
- **根因**：mingw-w64 在 x64 上把 `setjmp` 定义成
  `_setjmp((BUF), __builtin_frame_address(0))` —— 第二个参数是 **SEH 链锚点**，
  会被存进 `jmp_buf`；`-O2` 没有帧指针，这个地址在部分栈布局下指向会被后续调用覆盖的区域，
  `longjmp` 之后由它恢复栈链就死在 ntdll。加 `-fno-omit-frame-pointer` 无效（仍 16/40 崩）。
- **修法**：`c_runtime.c` 新增 `IMPOR_SETJMP(BUF)` 宏 —— mingw + x64 下展开成
  `_setjmp((BUF), NULL)`（不安装 SEH 帧；纯 C 跳转不需要 SEH，与 mingw 32 位分支的
  `_setjmp3(BUF, NULL)` 同源），tcc / MSVC / Linux 保持 `setjmp(BUF)`。
  `ctrans._gen_try` 与 `cir` 的 `TRY_BEGIN` 都改走这个宏；`_tj` / `_rh` 同时补 `volatile`
  （C99 §7.13.2.1：只有 volatile 自动变量保证 longjmp 后值不变）。
- **验收**：ctrans 产物 `-O0` / `-O2` / `-O3` 各 **40/40**、`impor build` **40/40**、
  `impor run` **20/20**、`impor ir --build` **20/20**，且每档 stdout 只有 1 种。
- 说明：这是 4.1 异常功能落地起就存在的缺陷，4.5.0 同样存在；`backend_parity` 23 例里没有
  「`-O2` + 顶层 `try` + 函数内 `raise`」这个组合，所以一直没被门禁抓住。

### 4.5.0：三阶段一起推进 —— IR 下降后端 / 深度五层 / 宽度 20→37

这一版把「深度」与「宽度」同批推进：先让 IR 能被独立后端下降（阶段1），
再补三端一致性的深水区（阶段2），最后把标准库从 20 个模块扩到 37 个（阶段3）。
原则不变：**能用实测数字说话的地方一律给数字，做不到的地方明确写"未做"**。

#### 阶段1：IR → C 下降后端（`impor/cir.py`）

- **新增 `impor/ir.py` 的独立消费者 `impor/cir.py`**：只接受 IR 的 22 条指令，机械映射到
  `c_runtime.c` 的 `rt_*` API，**不遍历 AST**。因此它同时是一个 IR 校验器：任何
  "AST 能编、IR 编不了"的语义空洞，都会在这里以 `CIRUnsupported` 暴露。
- **新增 CLI**：`impor ir FILE --c` 打印下降后的 C 源码；`impor ir FILE --build OUT` 直接编成 exe；
  失败退出码 `EXIT_BUILD = 4`。
- **为支撑下降补的运行时助手**：`rt_pow_impor` / `rt_pos_impor` / `rt_setindex`（dict 与数组分派）/
  `rt_iter_n` / `rt_iter_keys`（dict 按键迭代）/ `rt_iter_at`（字符串按码点、其余按下标）/ `rt_range3`（range 1~3 参数）。
- **修掉的真实缺陷**（均从对拍里掉出来，不是预设）：
  - 临时量重复声明（`redefinition of 't1'`）：`_declare_temps` 既登记又单独声明，改为 `_is_tmp()` 判定 + `setdefault`；
  - `f_%d` 格式串被传成 str → TypeError；
  - `range(a, b)` 报 `too many arguments`（`rt_range` 只收 1 参）；
  - `v_arr(&v, a)` 签名不符；
  - 一等函数包装器原型缺失（`fw_f_3 undeclared`）；
  - 跨函数可见性：解释器里顶层 `var` 是模块级变量，下降时只有 `loop` 保持局部，顶层 `STORE` 全部提升为全局。
- **验收（`%TEMP%\irverify\verify_irpar.py`，66 个示例逐个 build 后与解释器逐字节比对）**：
  **36 个逐字节一致 / 21 个 IR 显式拒绝 / 3 个构建失败 / 0 个"编出来但结果不同"**。
  21 个拒绝全部来自 `ir_lower.py` 的拒绝清单（类、异常、try、闭包、lambda、模块 import、AttrGet、SuperCall 等）；
  3 个构建失败里 2 个是 `cn_demo` / `err_demo` 这种**故意演示错误**的示例。

#### 阶段2：深度五层

1. **Unicode 大小写（完成，diff=0）**：新增生成物 `impor/unicode_case.py`（`UPPER_MAP` / `LOWER_MAP`）与
   生成器 `tools/gen_unicode_case.py`——**一份数据同时写 Python 表与 C 表**，C 侧二分查找 + UTF-8 解码/编码。
   口径：ASCII / 拉丁-1 / 拉丁扩展 A-B / 希腊 / 西里尔 / 全角，只收 1:1 映射（不做 ß→SS、ﬁ→FI、土耳其 i）。
   `builtin_upper` / `builtin_lower` 与 `rt_upper` / `rt_lower` 共用这套表。
   验收：`upper("abc-äöü-ΑΒΓ-абв")` / `lower("ABC-ÄÖÜ-ΑΒΓ-АБВ")` / `upper("ß")` / `upper("Straße")` 三端逐字节一致。
2. **码点原语 `ord` / `chr`（新增，见 RFC 0004）**：三端同语义（按 Unicode 码点，不按字节），
   C 侧复用 `rt_uc_decode` / `rt_uc_encode`。
3. **`strpad` 按码点补齐（修一致性缺陷）**：C 侧此前用 `strlen`（字节数）判断宽度，中文列宽会错，
   导致 `std.table` 渲染出的表格两端不同。改为 `rt_u8len`（字符数）+ 支持多字节填充符。
   验收：`std.table.render` 的中文表头两端输出**逐字节相同**。
4. **内存泄漏检测（新增）**：`c_runtime.c` 增加 `g_rt_leak_live` 与 `rt_leak_report()`，
   由 `rt_shutdown()` 调用；设 `IMPOR_LEAK_CHECK=1` 时向 stderr 打印
   `[leaks] 分配 X 次 / 释放 Y 次 / 未释放 Z 块`。验收：同一 exe 开/关环境变量，只在开启时出现该行。
5. **并发 `select`（补齐最后一格）**：`std.channel` 此前已是条件变量版（`mutex_new` / `cond_new` /
   `cond_wait_ms`，`recv_timeout` 已在），4.5.0 补 `select(chans, ms)` 与 `select_now(chans)`：
   返回 `[索引, 值]`，超时或全部关闭且排空返回 `[-1, empty()]`；实现用"每通道 `recv_timeout(1 ms)` 轮转"，
   靠条件变量让出 CPU，不做自杀式忙等。
6. **泛型单态化（未做，明确记录）**：C 后端仍按 `Val` 统一值模型，不为每个实例化点生成专用符号。
   原因：单态化要先把 types 信息从 `semantic` 阶段带进 `cfast`/`ctrans`，属结构性改动，按 RFC 单独排期。

#### 阶段3：宽度 20 → 37 个标准库模块

- **新增 17 个模块**：`log` `config` `encoding`（base64/hex/URL）`template` `cli` `test` `bench` `metrics`
  `uuid` `semver` `validate` `table` `units` `cache` `color` `process` `diff`；
  并把此前的三个"壳模块"补实：`json`（+ `try_parse` / `is_valid` / `get` / `keys_of` / `has` / `merge`）、
  `regex`（+ `is_match` / `find_first` / `find_all` / `count` / `replace_all`）、
  `time`（+ `human` / `pad2` / `since`）。
- **写法上踩到并绕开的三个真实限制**（都写进了模块注释）：Impor 的 `//` 是注释而非整除（索引用 `int(a / b)`）；
  裸 `catch {` 不可用，必须 `catch e {`；C 后端不支持 `throw`，标准库里不抛异常，改用空串/退出码等哨兵返回值。
- **验收**：23 个模块的解释器冒烟（每个 import + 调代表函数）**0 失败**；
  代表性模块（`encoding` / `table` / `validate`）**build 后与解释器逐字节一致 3/3**；
  base64 往返 `"a"…"abcdefg"` 6 个长度全部 `True`。
- **新增文档**：`docs/能力矩阵.md`（特性 × 后端 × 平台，带 Tier 分级）、`docs/宽度预算.md`（宽度扩张的预算与排序）。

#### 治理与口径

- **`docs/生产化/README.md` 的过时状态已改**：原文写着"多线程、锁、原子、channel 未完成"与"现有 build 走 TinyCC"，
  但 4.2/4.3 已交付并发全套、4.3.1 起默认优先 gcc64 —— **旧文档在贬低自己的实现**，这一版按实际状态重写。
- API 基线刷新：55 关键字 / **124 内建**（+`ord` `chr`）/ **37 标准库模块** / 30 错误码，变更依据 `docs/rfc/0004-ord-chr.md`。

### 4.4.0（工作区，未发版）：IR 分层 —— 借 xlang 的阶段化管线

这一版不加语言特性，补的是**编译管线本身**：把「AST → 各后端各走一遍」换成
「AST → 单一 IR → (dump / 参考求值 / 将来各后端下降)」，对齐 xlang 的 Lex → Parse → Semantic → IR → Lowing。

- **新增 `impor/ir.py`**：22 条三地址码指令（`CONST/LOAD/STORE/MOVE/BINOP/UNOP/CALL/PRINT/LIST/DICT/INDEX/SLICE/SETINDEX/TOSTR/CONCAT/ITER/ITER_NEXT/LABEL/JUMP/JUMP_IF_FALSE/ASSERT/RETURN`）、`IRModule.render()` 文本序列化，以及**独立于解释器的参考求值器**（`Evaluator`：只共享 `builtins.py` 的纯函数内建，不共享 AST 遍历路径）。
- **新增 `impor/ir_lower.py`**：AST → IR。支持 `var/set/+=`、`print`、`if/elif/else`、`while`（`break`/`continue`）、`for`（`range`/列表/字典）、顶层 `def`（递归/多参数/隐式返回）、算术与比较、短路 `and|or`、三元、字符串拼接与 `{expr}` 插值、列表/字典与下标读写、切片、`assert`、函数值传递。不支持的语法一律抛 `UnsupportedNode`（带 `文件:行:列` 与原因），**不静默降级**。
- **新增 `impor ir` 子命令**：`impor ir FILE` 打印 IR；`-o` 落盘便于 diff（对应 xlang 的 `Ir.txt`）；`--run` 用参考求值器执行；`--stats` 输出函数/指令/标签与实际执行指令数。不支持的脚本退出码 2。
- **对拍抓到并修掉一个真缺陷**：parser 已把 `&&`/`||` 归一成 `and`/`or`，但 `interpreter.visit_BinOp` 没有对应分支，`print(1 && 2)` 一直报 `OpError: 未知运算符 and`。现补上短路求值（`x != 0 && 1 / x > 1` 不会先炸除零），树遍历路径与 IR 求值器同语义。
- **验收（本机实测）**：`tests.test_ir` **12/12**；`examples/demo_01…demo_40` 对拍 **27 个逐行一致 / 13 个显式拒绝 / 0 个不一致**；既有回归 `tests.test_impor + test_v431_sync + test_v42_generics` **200/200**；`tools/api_baseline.py` 一致（公开面未动：55 关键字 / 122 内建 / 20 模块 / 30 错误码）。
- **明确没做**：四个后端（解释器 / pytrans / ctrans / cfast）**仍然各自 walk AST**，没有改成从 IR 下降；`imporc` 未接入；类/异常/导入/闭包/`AttrGet` 方法调用仍在拒绝清单里。替换内核属破坏性变更，需先写 RFC。详见 `docs/编译管线-IR-4.4.md`。

### 4.3.2 patch8：三端一致性收口（std.text 崩 exe · 缺失内建 · 谎报口径 · 诊断与打包）

这一版不新加语言特性，只修「同一份代码在解释器能跑、编成 exe 就崩」这一类问题，
以及对外口径里与实际实现不符的几处说法。全部修复都有三路（解释器 / `compile` Python /
`build` C 产物）实测对照。

- **P0：`use std.text` 的程序编 exe 必崩（E1006）**。根因是 `impor/std/text.impor` 的
  `split` 直接 `return s.split(sep)`——那是 Python 宿主方法，C 后端 `rt_method` 没有
  字符串 `split` 分派，一进模块就抛 `AttributeError: 字符串无此方法`。`starts_with` /
  `ends_with` 同理（写的是 Python 名 `.startswith` / `.endswith`，C 侧只认
  `.starts_with` / `.ends_with`）。三处改成纯内建实现（`len` + 切片/`strcut` + 比较 +
  `arrappend`），`examples/std_demo.impor` 的 C 产物由「第 65 行崩」变为完整跑完。
- **P0：C 运行库补字符串 `split`**。除 std 库外，用户直接写 `s.split(",")` 在 exe 里
  同样会崩。`c_runtime.c` 的 `rt_method` 现支持：带分隔符按分隔符切、不带参数按连续空白
  切并丢空段、第二参数 `maxsplit`（与解释器/Python 语义一致），并新增
  `.startswith` / `.endswith` 别名。实测中文串 `"你好，世界，Impor".split("，")` 三路输出
  都是 `['你好', '世界', 'Impor']`。
- **P1：`impor build` 横幅谎报「任意精度整数」**。横幅与快路径说明改为
  `整数 int64 语义，溢出报 E1012` / `数值快路径（64 位整数，溢出报 E1012 并中止）`。
  文案只在 `cli.py::_print_build_summary` 一处，`bench-spec` 复用同函数，一并生效。
- **P1：溢出提示指向不存在的开关**。数值快路径的 `iovf()` 此前提示 `--no-fast` /
  `--no-native`（前者只对 `build` 生效、后者并不会切到任意精度），按提示做仍然报错。
  改为唯一真实可用的入口：`impor run 脚本.impor --big`。实测 `--no-native` 与 `--int64`
  同样报 `E1012`，`--big` 输出 `9223372036854775808`。
- **P1：`div` 在 `impor compile` 产物里缺失**。`div` 原本被登记在
  `INTERPRETER_ONLY_BUILTINS`（注释理由是"依赖解释器状态"），但它是纯算术内建，
  Python 转译产物 `from impor.runtime import *` 拿不到它。已移入 `RUNTIME_BUILTINS`
  并在 `runtime.py` 补实现（除零抛 `DivisionByZero`、非数值抛 `TypeError`、结果按
  floor：`div(-7, 2) = -4`）。转译产物与 C 产物现在同值。
- **P1：`build_gcc.bat` 写裸 `gcc`**。本机 gcc（Dev-Cpp MinGW64）不在 PATH，生成的 bat
  直接 `gcc ...` 会静默失败（rc=1、无输出）。`cli.py` 生成 bat 时改用 `find_gcc64()`
  定位，脚本首行前置 `set "PATH=<gcc bin>;%PATH%"`，编译命令走带引号的绝对路径；
  找不到 gcc 时仍回落裸 `gcc`，行为不比之前差。实测重新生成并 `cmd /c` 跑通（0x8664 DLL）。
- **P2：`E1012` 没有专门的诊断条目**。`diagnostics.py` 的规则表只有泛化兜底，
  `impor run` 溢出时给的是"检查写法"这类空话。新增规则（`OverflowError|整数溢出|E1012`）：
  说明 int64 范围、说明"报错而非静默回绕"，修法指向 `--big` 并注明 C 产物不支持大整数。
- **P2：conformance 抖动**。并发压测时出现过 99/100，单独重跑 100/100。两个根因：
  ① `TIMEOUT=120` 在慢机/并发下会误判超时，提到 240（判定口径不变，失败仍计失败）；
  ② runner 每个用例都读写**仓库根目录的全局** `imporc_args.txt`，两个进程同时跑会互相
  覆盖——imporc 早已按 cwd 读参数，那套"备份/恢复"已是死代码，删除后每个用例只碰自己的
  `tempfile.mkdtemp` 目录。
- **P2：`std.http` 在「cwd ≠ 脚本目录」时不是回退 curl，而是崩 E1099**。`_maybe()` 用
  `fileexists(path)`（按进程 cwd 解析）判断，再把这个相对路径交给 `ffi_open`（按脚本目录
  解析）；两边口径不同就会出现「存在检查通过、加载却报原生库不存在」，本该走 curl 兜底的
  调用直接抛异常。改成加载失败一律 `catch` 后返回 false（继续下一个候选 / 兜底）。
  实测：脚本在 `%TEMP%\x\` 而 cwd 在仓库根时，修复前 `E1099`、修复后 `HTTP-OK`。
  `tools/verify_release.py --check stdlib_http` 由 FAIL 转为 OK。
- **P3：文档口径回到实现**。`docs/语言规范.md` 与 `conformance/README.md` 原写
  "默认 `impor run` / `--no-native` 是 Python 任意精度"，与实现（解释器早已按 int64 拦截）
  相反。改成四条路径统一 int64、任意精度只有 `impor run --big` 一个入口，并给出各路径
  的退出码对照表。
- **版本与自举**：`VERSION` / `impor/version.py` / `Impor.py` / VS Code 清单同步到
  `4.3.2`（`tools/sync_version.py`）；用 `bootstrap/build_with_python.py --install` 重建
  `impor/imporc.exe`，`imporc version` 现在自报 `v4.3.2`（此前停在 4.3.1，会让
  `test_v38.test_version_mode` 红）。
- **API 基线走 RFC 流程**：`div` 换面属公开面变更，按仓库规则先写
  `docs/rfc/0003-div-runtime.md`（为什么不改不行、影响谁、验收、回滚），再
  `tools/api_baseline.py --update --rfc …` 刷新。基线现在一致：55 关键字 / 122 内建 /
  20 模块 / 30 错误码；内建总数未变（117 是旧基线的分组计数，div 只是换了分组）。
- **验收（本机实测，Python 3.11.5）**：15 个示例三路对比 **15/15 逐字节一致**（4.3.1
  时为 14/15，差的正是 `std_demo`）；`conformance/run_conformance.py` **100/100**；
  `tests.test_v4_backend_parity` **23/23**；`tools/check_fmt_all.py` **220/220**；
  `tools/verify_claims.py` **自动承诺 22/22**（`stdlib_http` 与 `api_frozen` 两项由红转绿）；
  `build_gcc.bat` 重新生成后 `cmd /c` 实跑通过。
- **明确没做**：C 后端的 `upper` / `lower` 只处理 ASCII（解释器按 Unicode 处理，中文与
  带重音的拉丁字母会不同）；`--big` 只在解释器路径存在，C 产物没有大整数；`imporc`
  自举子集本版未动（只重建、只对齐版本串）。分 TU 仍未做。
- **一条不修的记录**：C 产物在 PowerShell 里 `2>&1` 合流时 stdout/stderr 顺序错乱，
  经 `cmd /c "x.exe > f 2>&1"` 复测顺序正确，是 PowerShell 的合流缓冲伪影，不是产品缺陷。

### 4.3.1 patch7：图像 / HTTP 绑定 · imporc 语法子集补齐 · 分 TU 仍未做

这一版把 4.3.1 公开列出的「未做」往前推了四项，只留下分 TU 一项明确不做完不吹。

- **图像绑定 + `std.image`（第 20 个 std 模块）**：本机没有 `stb_image.h`，改用 Windows 自带 **WIC**（`wincodec.h` / `ole32` / `windowscodecs`），不引入第三方源码。`bindings/image/impor_bind_image.c` 导出 `load/width/height/red/green/blue/alpha/save_png/close`，句柄压成 1..8 整数；`std.image` 提供 `configure/ready/load/size/pixel/save_png/close/error`，自动在 `bindings/image/`、`impor/bindings/image/`、`exedir()/bindings/image/` 找 DLL，**找不到时 `load()` 返回 0，不崩**。实测：1024×1024 PNG 读入、中心像素 `13,18,30,255`、PNG 另存再读回尺寸一致、`roundtrip: True`；解释器与 `impor build --no-cache` 的 C 产物输出一致。
- **HTTP TLS 硬化**：`bindings/http/impor_bind_http.c` 走 WinHTTP；`WinHttpCrackUrl` 的 host/path 是原串切片，必须先复制再传（否则 12005 Invalid URL）；TLS 从 1.2 起、证书错误不忽略、`MAX_REDIRECTS=5`、响应上限 16 MB、只接受 `http(s)`。MinGW 4.8.1 的 `winhttp.h` 缺三个常量，已用 `#ifndef` 兜底（`TLS1_2` 是 `0x800`，写成 `0x8` 会 12175）。`std.http` 重写为「绑定优先 + `curl -sS -L --proto =https|=http --tlsv1.2 --max-redirs 5` 兜底」，新增 `get_status/request/configure/ready`；`get()` 保持原语义。实测 `https://example.com`：status 200 / body 577 字节 / 含 HTML；`ftp://` 被拒。
- **跨后端修一个真缺陷**：C 运行时 `rt_strfind` 是三参，但 `ctrans.py` 对两参 `strfind` 生成两参调用，C 产物报 `too few arguments`；已在 `_gen_call` 补 `v_int(0)`。这次是 `std.http` 的 C 构建暴露出来的。
- **imporc 的 4.3.1 语法子集（patch7）**：原子集不认多行字面量、`!`/`&&`/`||`、`nil`、`+=` 系列、泛型、嵌套 import、channel/pool。已补：
  - 多行 dict/list/实参：`mi_skip_layout()` 吞排版记号；`mi_brace_to_indent` 用「块头白名单 + 字面量计数 `lits`」区分代码块 `{` 与字典 `{`，行首 `}` 只在 `depth>0` 时当块结束，避免把 `return { ... }` 的收尾 `}` 吃掉；dict/list 允许尾逗号。
  - `!` 一元、`&&`/`||` 词法归一为 `and`/`or`、`nil`→`v_null()`、`+=/-=/*=//=/%` 复合赋值（变量/属性/下标三类左值）。
  - 泛型声明运行期擦除：跳过 `<T>`、参数 `: T`、返回 `-> T`（词法补 `->`）。
  - 并发/原子/数组/时间内建统一走 `bi_expr`（mutex/cond/atomic/thread/arrappend/arrfind/clock 等），不再发成 `g_xxx`。
  - 被内联模块自己的 `import m as mm` 生成 `mm = imporc_module(...)` 绑定（`std.pool` 内的 `chan` 就是这样修好的）。
  - **任意 cwd 可用**：`mi_read_module` 追加 `exe 安装目录/impor/<模块>` 与 `exe 安装目录/<模块>` 两个**绝对**搜索根。此前脚本与 cwd 都在临时目录时，`impor/` 相对路径不存在，报「找不到模块 std.channel / std.pool」；修后在 `%TEMP%\mx_test` 直接调用已安装的 `imporc.exe`，`build rc=0`、产物 `run rc=0`，输出与解释器一致。
  - 验收：`examples/{channels,generics,closures,condvar,threads}.impor` 用 `impor/imporc.exe` 编译出的 exe 与解释器**逐行同输出**：channels `sent 20 / sum 210 / stats [20,20,0] / workers 4 / done 16`；generics 4 行；closures 4 行；condvar 6 行；threads 4 行。`bootstrap/imporc_py.exe` 920,576 → 969,728 字节（多次迭代后当前值），已安装到 `impor/imporc.exe`。
  - `tools/support_matrix.py` 的 imporc 通过数由 **9/20 提升到 11/20**（channel / 条件变量两条由旧版「临时 cwd 找不到 std」转绿），总违例仍为已知基线 1 条。
- **75 模块全量回归（一次性跑完）**：`py -3 -m unittest discover -s tests -p 'test_*.py' -v` 共 **871 项，26 failures + 14 errors + 23 skipped，1595 s**。失败集中在存量模块（v37/v42/v46/v49/v51/v53 等），本轮改动相关的 `test_v43_channel` / `test_v431_sync` / `test_v42_concurrency` / `test_v42_generics` / `test_v38` 未新增红；`test_v42_closure::test_imporc_backend` 仍红（imporc 闭包跨后端断言，属存量 imporc 子集差异）。**未逐条复核 40 条失败是否全部为存量**，这里只如实报总数与相关模块状态。
- **未做（如实）**：**分 TU 编译 + `impor_rt.h`**。当前 `ctrans.gen_program` 把整份 `RUNTIME_C`（全部 `static` 函数/类型）拼进单一程序 TU，程序直接调用 static 符号；拆 TU 必须先导出运行时符号并生成完整头文件，属结构性改动。本轮没有动 `impor/cli.py:cmd_build` 的单文件 `cpath → gcc → 链接` 链路，仍是「整包缓存 + 单 TU」，不宣称模块级 `.o` 增量。

### 4.3.1：语法坑收口 · 条件变量挂起 · 错误输出分道

这一版是 4.3.0 的收口版：把「写起来会踩坑」的三处语法补平，把 channel 从轮询改成条件变量挂起，并把运行错误从 stdout 挪到 stderr（不再污染程序输出）。

- **跨行字面量可用**：`{` 字典 / `[` 列表 / 调用实参三处都可以换行书写。根因是词法层 `paren_depth` 只统计 `(` 和 `[`，`{` 内的换行会留下 `NEWLINE/INDENT/DEDENT` 三种排版记号，旧解析器只跳前两种，于是报「E1099 表达式缺失」且错误位置显示成引用方的文件名。现在三处统一调 `_skip_layout()`，`examples/condvar.impor` 的多行 `{"a": 1, "b": [...]}` 四端都能跑。
- **`!` / `&&` / `||` 三个符号别名**：解析期分别归一成 `not` / `and` / `or`，转译与 C 后端无需额外分支。同时修掉 `impor fmt` 的同源缺陷——旧实现只把 `||`、`&&` 当单字符运算符，格式化后会把 `||` 拆成 `| |`、`&&` 拆成 `& &`、`!false` 拆成 `! false`（能过 fmt 门禁但语义被改坏）；现在 `||`/`&&` 进二元运算符集合、`!` 按一元前缀紧贴操作数。
- **转译端支持 `nil`**：`impor/transpiler.py` 的表达式分派补 `Null` 节点（输出 `None`），此前 `nil` 在解释器与 C 后端都能打印 `None`，只有 pytrans 报「无法编译的表达式节点 Null」。
- **错误输出分道**：`c_runtime.c` 的 `rt_print_snippet` / `rt_trace_print` / `rt_report_error` / `rt_report_abort` 全部改走 `fprintf(stderr, ...)`，程序正常输出与运行错误不再混在同一个流里（`tests/test_fuzz` 由红转绿）。
- **条件变量（5 个新内建，四端一致）**：`cond_new` / `cond_wait` / `cond_wait_ms` / `cond_notify` / `cond_notify_all`。语义按 C 口径：`cond_wait(cond, mutex)` 必须由已持锁线程调用，内部「释放锁 → 挂起 → 唤醒后重新持锁」；`cond_wait_ms` 返回 bool（true=被唤醒 / false=超时）；`notify` 无等待者时通知丢失（条件变量不是信号量）；句柄上限 64、跨种类不通用。Windows 64 位用 `CONDITION_VARIABLE` + `SleepConditionVariableCS`，POSIX 用 `pthread_cond_t`；**内置 tcc 32 位的旧 win32 头文件没有 `CONDITION_VARIABLE`，因此走 `__TINYC__` 分支退化为「代际计数 + `Sleep(1)` 轮询」**——语义不丢通知，唤醒粒度 1 ms，不宣称真阻塞。
- **`std.sync`（第 19 个标准库模块）**：`make_lock/lock/unlock/try_lock` + `make_cond/wait/wait_ms/notify/notify_all` 的薄封装，接入 `impor/std/__pkg__.impor`。
- **`std.channel` 改挂起版**：`make(cap)` 的通道 dict 增加 `cond`；`send` 在有界队列满时 `cond_wait`（关闭则返 false），`recv` 在空且未关闭时 `cond_wait`，取走后 `notify` 等空间的一方；`close` 做 `notify_all`；新增 `recv_timeout(c, ms)`（超时返回 `[false, empty()]`）。`try_recv` / `drain` / `stats` 保留。
- **新增用例与门禁**：`examples/condvar.impor`（条件变量唤醒 + cap=1 生产消费 + `recv_timeout` + 多行字面量 + `!`/`&&`/`||` + `nil`，四端同输出 `wake: 1` / `sum: 10` / `timeout: False` / `dict: 4` / `nil: True None` / `logic: True`）；`tests/test_v431_sync.py` 7 项；`tools/support_matrix.py` 增「条件变量 / 阻塞通道（v4.3.1）」一行。实测：`check_fmt_all` **220/220 通过（跳 21）**、`api_baseline` 一致（**55 关键字 / 122 内建 / 19 模块 / 30 错误码**）、`test_v431_sync` **7/7**、`test_v43_channel + test_v42_concurrency + test_v401_std_modules + test_v34 + test_v42_generics` 合跑 **46/46**。
- **`imporc` 版本串对齐**：用 `bootstrap/build_with_python.py --install` 重建 `impor/imporc.exe`，`imporc version` 现在输出 `imporc (Impor 自举编译器) v4.3.1 · 子集模式`（此前停在 v4.0.0，导致两个版本断言测试红）。
- **第三方库绑定（2/3）**：新建 `bindings/` 目录约定（薄 C 层 + Impor 包装 + demo + `.impor/ffi_allow.json`）。SQLite3 与 zlib 两个绑定实测通过：sqlite3 运行时 `LoadLibraryW` 任意 `sqlite3.dll`（不必 import lib），句柄压成整数、结果集编成 JSON，`demo.impor` 建表/插 3 行/回读 `[[1, '甲', 91.5], [2, '乙', 78], [3, '丙', None]]`；zlib `crc32("abc")=891568578`、40 字节文本压缩到 63 字节且往返一致。图像绑定未做：本机没有 `stb_image.h`，也没有可用的 PNG 解码库。过程中踩到并修掉一个真问题：路径含中文时必须走 `LoadLibraryW`，`LoadLibraryA` 按 ANSI 解释会返回 126。
- **构建缓存升级（增量编译的一半）**：缓存落到项目内 `.impor-cache/build-cache.json`，缓存键改为**递归依赖指纹**（入口文件 + 直接/间接 import 的模块 + 包目录 + 安装目录 `impor/std/*`，旧版只看第一层）。实测 `examples/condvar.impor`：首次 2.06 s、命中 **0.19 s**；改 `impor/std/sync.impor` 一行注释后缓存正确失效（重建 1.99 s）。
- **`imporc` 模块搜索根**：自举编译器原来只在「脚本目录 / 当前目录」找模块，`examples/` 下 `import std.x` 直接 `fileread` 失败；现补「安装目录 `impor/` → 脚本目录 `../impor/`」两个搜索根，并用 `fileexists` 先判存在（`fileread` 失败会抛异常，旧写法根本走不到兜底分支）。为此在 `bootstrap/imporc.impor` 的表达式发射器里补了 `fileexists → rt_fileexists`：子集编译器原先把它当未知内建发成 `g_fileexists`，自编译 `imporc.impor` 时直接链接失败。同处还修回 `gen_block2()` 被错改的缩进（`if/else` 落到第 0 列），`tests.test_v38` 7 项（含 imporc A→B 自编译闭环）现已通过。**仍未支持**：`imporc` 的解析器不认 4.3.1 的多行字典字面量，所以 `std.channel` / `std.pool` / `std.sync` 在 imporc 下仍是编译期拒绝；泛型同样未同步。A→B→C→D 全链未跑。
- **未做（如实列出）**：`bindings/image/`（缺 `stb_image.h`）、`std.image`、HTTP 客户端硬化（TLS）；分 TU 编译与 `impor_rt.h`；`imporc` 的多行字面量 / 泛型 / channel / pool 支持。四条短板的进度与验收见 `docs/路线图-补四块短板.md`。

### 4.3.0：通道 / 线程池 · 标准库到 18 个模块 · 修两个 C 运行时缺陷

这一版补的是 4.2 明确留下的那条短板——「有线程、锁、原子量，但没有 channel 和线程池」。做法是**不新增内建**，用纯 Impor 库实现 CSP 风格队列，再用它搭常驻 worker 池；顺带修掉两个只有并发路径才会踩到的 C 运行时缺陷。

- **`std.channel`（通道）**：`make(cap)` / `send` / `recv` / `try_recv` / `drain` / `size` / `is_closed` / `close` / `stats`，另有 `empty()` / `is_empty(v)` 哨兵。`cap>0` 有界、满了发送方自旋；`cap<=0` 无界；空且未关闭时 `recv` 每 1 ms 轮询等待；关闭且排空返回 `empty()`。`send` 到已关闭通道返回 `false`，不抛异常。
- **`std.pool`（线程池）**：`new_pool(n)` 起 n 个常驻 worker，`submit` / `pending` / `shutdown`（关闭队列 + join，返回每个 worker 处理数，幂等）。池句柄是**整数**而不是 dict：C 后端会把闭包捕获的容器按值复制，把通道 dict 直接传进线程只会看到创建时的快照（本轮踩到并改掉）。
- **修掉两个 C 运行时真缺陷（不修则 channel 在 C 端必错/必崩）**：① `rt_dict_set` / `rt_arr_set` 原来是「先 `rt_val_free` 再 `rt_val_dup`」，当值本身就在槽里时（`d["k"] = arrappend(d["k"], v)`、`a[0] = a[0]`）会读已释放内存——表现为 `len` 变 0，偶发 `0xC0000005`；现改为**先 dup 再 free**。② `rt_concurrency.thread_spawn` 在编译产物路径上（`interp is None`）不克隆解释器执行上下文，而 std 模块函数是解释器 `Closure`，于是两个线程共用一个 `cur_env` 互相覆盖局部变量（表现为「变量 v 未定义」）；现在无参调用会自行向运行时取解释器并克隆。
- **顺带修掉 `impor build` 的一处内部错误**：`impor/cfast.py` 的赋值扫描对「字符串插值」这类节点里存的裸 `str` 调用了 `vars()`，合法源码（`print("平方 = {n * n}")`）会直接抛 `TypeError: vars() argument must have __dict__ attribute`，且 4.2.0 包内同样复现（存量缺陷，非本版引入）。现只下钻 AST 节点与容器，`tests/test_v34.py::test_build_rich_semantics` 由红转绿。
- **标准库 16 → 18 个模块**：`channel` / `pool` 两个都接入 `impor/std/__pkg__.impor`，`tools/api_baseline.json` 的 `std_modules` 同步到 18、`version` 升到 `4.3.0`；`VERSION` / `impor/version.py` / `vscode-impor` 等由 `tools/sync_version.py` 统一到 **4.3.0**。
- **新增用例与门禁**：`examples/channels.impor`（生产 20 条求和 210 + 4 worker 分 16 个任务）；`tests/test_v43_channel.py` 9 项（解释器 / pytrans / gcc64 / tcc32 四端 + 关闭后 send、哨兵、try_recv+drain、有界阻塞、坏句柄）；`tools/support_matrix.py` 增「通道 / 线程池（v4.3）」一行。实测：`check_fmt_all` **218/218 通过（跳 21）**、`verify_claims` 自动验证 **22/22（100%）**、`support_matrix` **19 个用例 / 契约违例 1 条（仍是已基线的 int64 溢出 × 转译器，新增 0）**。
- **口径（写进 §24.3，别夸大）**：这是**库级 CSP 队列 + 轮询等待**，不是 Go 运行时那种调度器级阻塞；任务分派不保证公平（实测出现过 `[40, 0, 0, 0]` 与 `[5, 5, 5, 25]`）；没有 `select`、没有通道超时参数、没有关闭广播；`yield` 有语法位但没有协程调度。
- **未做（如实列出）**：不新增加载器/包管理器（第三方库生态仍未动）、不做 C 单态化与模板级抽象、不做增量编译/多模块并行编译。`imporc` 自举编译器仍未同步泛型与并发内建（能力矩阵里对 imporc 是「编译期拒绝」）。四条短板的可执行路线见 `docs/路线图-补四块短板.md`。

### 4.2.0：泛型 · 线程/原子 · 标准库到 16 个模块

这一版加的是语言本体的三样能力：**泛型**、**线程/互斥锁/原子量**、**标准库扩充**。三端（解释器 / `impor compile` 的 Python 转译 / `impor build` 的 C 后端）同一份源码实测输出逐字节相同；没做完的写在最后。

- **泛型（声明期约束 + 运行期擦除）**：`def max2<T>(a: T, b: T) -> T` 语法可用，`export def` 与嵌套 `def` 同样支持。类型参数在静态检查里等价于「任意类型」，不做 C 的单态化、运行时零开销；唯一的检查收益在调用点——实参字面量类型绑到类型参数上，同一次调用同一个 `T` 绑成两种类型即报 `generic-mismatch`（`impor run max2(1,"a")` rc=2，提示「混型请先显式转换」）。`<T: int>` 约束、重复类型参数、未闭合 `>` 解析期报 E2002；不支持显式实例化 `f<int>(1)`（声明位之外 `<` 仍是比较运算符）。实测 `max2(9,2)` / `ident("hi")` / `ident(2.5)` → `9 / hi / 2.5`，解释器与 gcc 64 位产物（252,928 B / 1.54 s）一致。规范见 `docs/语言规范.md` §5.1。
- **线程 / 互斥锁 / 原子量（三端都支持）**：新增 16 个运行时内建（`thread_spawn/join/alive/id`、`cpu_count`、`mutex_new/lock/unlock/trylock`、`atomic_new/load/store/add/sub/exchange/cas`），口径对齐 C：句柄是整数 id、跨种类不通用，`atomic_add/sub/exchange` 返回**旧值**，`atomic_cas` 返回 bool，`thread_join` 幂等返回线程函数结果。解释器侧每个线程一份独立执行上下文（`cur_env`/`call_stack` 线程局部，`Closure.__call__` 优先线程副本），C 侧 try/raise 的跳转栈、错误类型/信息、调用栈、当前行号全部线程局部（Windows TLS / POSIX `pthread_key`），`rt_note_alloc` 等共享登记表进自旋锁，`rt_shutdown` 先等未 join 线程收尾。实测 4 线程 × 2000 次原子自增 → `join: 2000`×4 / `8000` / `cas: True 8001`；8 线程 × 5000 次「mutex 保护自增 + 同量原子自增」→ `40000 / 40000`（gcc64 259,584 B、tcc32 139,776 B 与解释器一致）。**原子量在 C 侧用运行时锁实现，不是无锁**（32 位 tcc 下 64 位变量不保证 8 字节对齐，`Interlocked*64` 会踩对齐红线）；列表/字典/对象跨线程共享仍需自己用 mutex 包住。规范见 `docs/语言规范.md` §24。
- **标准库 15 → 16 个模块并补齐 `std.csv` 的接线**：本轮新增 `std.sort`（`sort/sort_by/search/is_sorted`）、`std.random`（`between/choice/shuffled/pick/token`）、`std.stats`（`mean/median/variance/stddev/mode/percentile/sqrt`）、`std.thread`（`spawn/join/alive/current/cpus/map_parallel/range_parallel`）、`std.atomic`（`counter/load/store/add/sub/exchange/cas` + `lock/enter/leave/try_enter/with_lock`），全部纯 Impor 实现并接入 `impor/std/__pkg__.impor`。同时修掉一处真不一致：`std/csv.impor` 早在 4.0.1 写好并有测试，但 `__pkg__.impor` 一直没 import/export 它，`use std` 拿不到 `std.csv`——现已接线，`tools/api_baseline.json` 的 `std_modules` 从 15 补到 16。实测 `std1.impor`：`sort: [1, 2, 5, 5, 9] sorted? True`、`search 9 -> 4  search 7 -> -1`、`mean: 4.0 median: 4.0`、`stddev: 2.0`、`p50: 2.5`、`threaded count: 500 cpus: 8`，gcc64 产物 298,496 B / 2.76 s 一致。
- **顺带修掉两个跨端一致性缺陷（不修就会让上面的"三端一致"是空话）**：① Python 转译完全没实现 `set x = ...` 的「就近更新」语义——`_scope_decls` 只处理 `global` / `nonlocal` / `+=`，漏了 `Assign(scope='update')`，导致 `set g = g + 1` 在转译产物里变成局部声明、报 `UnboundLocalError: cannot access local variable 'g'`；现按「外层函数局部 → `nonlocal`，模块全局 → `global`」补齐（`tests/test_v42_concurrency.py` 锁定）。② `impor fmt` 未覆盖新建的 `std/stats.impor`，`check_fmt_all` 曾报 211/212；格式化后 213/213。
- **门禁与基线**：`tools/api_baseline.json` 升到 `version 4.2.0`、`builtins.runtime` 84 条（含 16 个并发内建）、`std_modules` 16 个、`keywords` 仍 55（本轮不新增保留字）；`VERSION` 与 `impor/version.py` 同步升到 `4.2.0`。`python tools/check_fmt_all.py` = **213/213 通过，跳过 21 个基线/生成文件**；`python tools/verify_claims.py` 自动验证 **22/22（100%）**（`api_frozen` / `fmt_clean` / `stdlib_*` / `c_abi` 全绿）。
- **未做（如实列出）**：泛型不做约束求解（`<T: 可比>` 这类写不了）、不做单态化优化；无协程/channel/线程池、无线程取消；C 侧原子量非无锁实现，只有 8 线程 × 5000 次量级压测、没有长时间 GUP 压测；Linux 侧 `--target linux64` 只补了 `-pthread`，ubuntu 上 pthread 链接未在本机验证（本机无 WSL/docker/clang）；`imporc` 自举编译器未同步泛型与并发内建（能力矩阵里这两行对 imporc 是「编译期拒绝」）。另有一条**存量缺口**（非本版引入、本版复现）：Python 转译路径没有实装 int64 溢出拦截——`var a = 4611686018427387904` / `a * 3` 在解释器与 C 后端都报 E1012，转译产物直接打印 13835058055282163712。已在 `docs/能力矩阵基线.json` 登记为已知缺口，不冒充已修。

### 4.0.1：整数语义统一、`pkg upgrade` 修复、标准库扩到 16 个模块

这一版只收录「有实测数据支撑」的改动；没做完的写在最后，不冒充已完成。

- **整数只有一种语义**：交付层默认 `int64`（`impor run`、`impor build`、REPL 一致），越界统一 `E1012`。此前默认走数值快路径（实为 int64）而 `--no-native` 是任意精度，同一份源码两个答案。新增 `--big`（等价 `--bignum`）恢复任意精度（只能走解释器树遍历，故同时关闭快路径），新增 `impor.toml` 的 `[project] int_mode = "int64" | "big"`（兼容 `[runtime]`）。实测 `print(9223372036854775807 + 1)`：默认 / `--no-native` / `--int64` 均 `rc=1 + E1012`，`--big` 输出 `9223372036854775808`。
- **`pkg upgrade` 从必崩到可用**：`pkgmgr.upgrade` 调用了从未定义过的 `_installed_source()`，`impor pkg upgrade` 一直抛 `NameError`。补上该函数（从 `impor.lock` 读来源，`local/cache/project` 视为无远程来源），并在升级成功后刷新 `<proj>/lib/<name>/` 的 vendor 副本，CLI 打印「已同步刷新 N 份项目内 vendor 副本」。新增 `tests/test_v401_upgrade_vendor.py` 2 项，实测「已升级 upgprobe → 2.0.0 / 已刷新 vendor 副本」。
- **`pkg search`（新）**：扫全局缓存目录与项目根 / `dist/` 下的 `*.zip`，逐行打印 `名字 版本 [cache|zip] 路径`，不联网。实测 3 个命中（`pkgy 1.0.0 [cache]`、`mylib 1.0.0 [zip]`、`mylib 1.1.0 [zip]`）。
- **标准库 10 → 16 个模块**：新增 `std.random`、`std.sort`、`std.stats`、`std.csv`、`std.atomic`、`std.thread`，全部接入 `impor/std/__pkg__.impor` 的 import/export。`std` 现有 16 个模块 / 114 个导出函数。新增 `tests/test_v401_std_modules.py` 2 项（16 个模块全部可 `import`、新模块函数可调用），实测 `sort([3,1,2])→[1,2,3]`、`median([1,2,3,4])→2.5`、CSV 往返、`thread.cpus()` 正常。
- **文档口径修正**：`docs/库-atlas-3.0.md` 与 `docs/库-atlas-3.1.md` 里「`std.atlas` / 700 个微库」是虚账，发布包内没有 `impor/std/atlas/`、也没有 `tests/atlas_700_smoke.impor`，源码里 atlas 字样命中 0。两篇已改为「撤回声明 + 真实清单」，`site/Impor官网.html` 的 6 处 700 宣称同步改写为 16 模块 / 114 函数。
- **整数语义与整除的书面口径**：`docs/语言规范.md` 运算符节补 `div` / `%` 完整小节（向下取整、恒等式 `a == div(a,b)*b + a%b`、一元负号优先级警告：`-7 % 3` 解析成 `-(7%3) = -1`，不是 `2`）；单行 `if` 的 `else` 边界补说明（跨行 `else` 明确 `E2002`，不是静默错绑）。
- **未做（如实列出）**：`yield` 仍是 `E2001` 硬拒绝（生成器需可挂起执行帧，本版未实现）；`runtime.py` 的静态内建清单尚未登记 `atomic_*` / `thread_*` / `mutex_*`，这些模块目前只在解释器路径可用；`test_conformance` 未跑出完整耗时数字；7 个存量测试模块（`test_fuzz` / `test_v34` / `test_v3615` / `test_v3616` / `test_v37` / `test_v373` / `test_v25` 的 usage 文本断言）仍红。

### 自举编译器支持花括号块 · 命令行/语法表/官网页对齐（patch8b）

四条都是从「拿去用」撞出来的，不是设计里的假设；改完全部实测复验，并重打 `patch8b` 包。

- **`imporc` 认花括号块（新）**：此前 `if (x) { … }`、`def f(n) { … }`、`while (…) { … }` 一律在第 4 行闭合 `}` 上失败（`无法识别的语句 [ } ]`），而 4.0 的标准写法就是花括号——`imporc.impor:869` 的注释自称 v3.8.3 已支持，实际没有。修法：在词法之前把「花括号写法」整段归一化成缩进写法（`mi_brace_to_indent`），缩进 = 原行缩进 + 已开花括号层数×4，所以**保留文件自己的缩进风格**，纯缩进文件经 `mi_has_brace_style` 判否后原样返回、一行不改。`{` 换成块头、`}` 删行退层，`} else {` / `} else if (…) {` 正常衔接。
- **诊断打真实脚本名**：40 处硬编码 `print("imporc.impor", ": 第" …)` 改为 `print(gname, …)`，`gname` 在读到 `gscript` 后赋值。现在报错写 `b2.impor : 第 4 行: …`，不再永远指 `imporc.impor`。
- **`impor serve run <脚本>` 真的能跑**：`cmd_serve` 的参数循环把所有位置参数当「未知参数」吃掉，走完循环 `rest` 已空，`sub=="run"` 拿不到脚本，只能打印用法。修法：循环里收集位置参数，`run` 用第一个当脚本、其余当参数，只对以 `-` 开头且非 `--port/--bg/-h` 的开关报错。
- **`impor syntax` 表对齐真源**：旧表 79 关键字 + 43 内建 = 122 条，其中 12 个是 4.0 已物理删除的历史写法（`func` / `loop` / `let/local` / `until` / `do` …），内建缺一半，连 `管道` 都回「没有找到语法条目」，与 README 首屏的 55 / 101 两套数字打架。修法：以 `tools/api_baseline.json` 为真源删除过期项、补齐缺失项（自动收录项文案如实写「说明待补」），新增别名 `管道/管道运算符/pipe → |>` 与手写 `|>` 条目；`exit` 既是关键字又是内建，字典键只能占一个身份、导致「内建 100 永远差 1」，现在给条目记 `extra_kinds` 显式双身份。实测 `impor syntax` = **55 关键字 + 101 内建 + 1 运算符 = 157 条**，与 README 首屏同一口径；`syntax 管道` 正常返回。
- **官网页重写为 4.0.0**：公网托管页仍是 3.2.3 技术专题存档（`site/Impor官网.html` 里自己也写着「本页是 3.2.3 存档」），与本版事实对不上。新增 `site/Impor官网-4.0.0.html`：只放本版实测（跨语言同口径表）、四条修复记录与边界，单文件无外部依赖，可直接静态托管。**公网 tiiny.site 的替换需要该站账号，本机没有凭据，这一步留给持有账号的人上传。**
- **顺手修掉两个静默 bug**（都在发布链上，不修就白改）：
  - `bootstrap/build_with_python.py` 与 `bootstrap/selfhost_check.py` 的 8 MB 栈补丁对 **PE32+** 用了 `opt+0x30`——那是 `SectionAlignment`，不是 `SizeOfStackReserve`（PE32 与 PE32+ 都是 `opt+0x48`，只是宽度 4/8 字节）。写坏头部后产物报「不是有效的应用程序」/ `Access is denied.`，看起来像权限问题，实际是 exe 被改坏。已改回 `opt+0x48`，重建后 `stack: 0x020b 2097152 -> 8388608` 正常。
  - `mi_brace_to_indent` 第一版把块头自己也缩进一级（先加深度再算缩进），顶层的 `if` 因此报「意外的缩进开始」；改为先按当前深度定本行缩进、再提升后续行深度。
- **实测**：9 个用例全过——`b1`（`A` / `done`）、`b2`（`7`）、`b3` 与 `b4`（`55`）、`braces`（`75025`）、纯缩进 `fib(25)` 回归（`75025` 不变）、`c1`（def+嵌套 if，`55`）、`c2`（`while` + `if/else` 链，逐行 `0 1 two 3 4 end`）、`c3`（块内注释，`1`）。自举闭环 `bootstrap/selfhost_check.py`：A→B→C→D **三代产物逐字节一致**（`277298` / `267872` 字节，120 个函数）。三路一致性 `conformance/run_conformance.py`：**100/100**（解释器 / C 后端 / imporc 三路径 stdout 逐字节相同）。
- **边界**：`imporc` 仍是过程式子集，管道 `|>` 与部分较新内建不在它的映射表里，遇到明确报错、不静默改语义；花括号归一化只处理「行尾 `{` + 行首 `}`」，同一行内 `{ … }` 写多个语句仍不支持。

### 编辑器键入卡死修复：`fib()` 不再「未响应」（4.1）

- **症状**：在编辑器里键入 `fib(` 或 `fib()`，窗口长时间「未响应」，同机必现。
- **根因**：① 键入 `(` 触发的参数提示在 **UI 线程**调用 `index.definition()`，而后台工作区索引正在全量重建，两者争同一把 RLock，界面只能干等；② 旧版 `_drop_file` 删一个文件就遍历整张引用表（O(文件×名字)），把一次重建拖成数十秒。
- **修复**：`ProjectIndex.refresh()` 改为单飞闸门——已有重建在跑时后来者**立即返回**，不再排队等锁；新增 `ProjectIndex.probe()` 供 UI 线程使用（不等锁、不触发重建、索引忙时返回空），`_show_param_hint` 改为「先查当前文件的正则，再用 probe 查项目索引」；`_drop_file` 只清理该文件自己引用过的名字（按文件维护 `_refs_by_file`），并顺手把 `by_name` 的空条目删掉。
- **实测**：无头 Tk 模拟键入 `fib()`，单键处理 ≤2 ms；键入后空闲期 **46.7 s → 4.7 s**（探针自身含 4 s 固定等待，故实际约 0.7 s）；本工程全量索引 **0.69 s**（228 个 `.impor` / 782 个名字 / 948 条引用），增量刷新 0.07 s。
- **回归**：新增 `tests/test_v41_editor_freeze.py` 6 项（并发 refresh 单飞、probe 跨线程不等锁、gate 忙时立即返回、删文件只清本文件引用）；索引 / LSP / 稳定性相关 **49 项 unittest 全过**。

### 稳定模式 `--stable`：脚本级执行预算（4.0.0 加固）

- **`impor run --stable`（新）**：给一次执行加五道闸门——语句条数、墙钟时间、内存 RSS、输出字节、单次分配元素数；越界以 `E1020`–`E1024` 退出，并带行号、`为什么`、`修复`。不开这一档时行为与之前完全一致（不计量、不改输出）。严格档 `--stable=strict` 供作业批改/在线运行台使用。
- **单项放宽**：`--max-steps` / `--max-seconds` / `--max-memory` / `--max-output` / `--max-alloc`，或环境变量 `IMPOR_STABLE=1` + `IMPOR_LIMIT_*`。命令行覆盖环境变量，环境变量覆盖档位默认（三者都生效——只认 flag 会让额度静默失效，这条踩过坑并写进回归）。
- **分配前预检**：`arrfill` / `list(n)` / `randstr` / `strpad` / `filelines` / `arrdup` / `arrrev` / `range` / 字符串与数组的 `*` 都在**真正分配之前**判定 `E1024`，不依赖 `MemoryError` 事后恢复。
- **输出按 UTF-8 字节计**：先前按字符数统计，中文场景下 512 KB 额度实际写出 1.39 MB；改为字节后额度等于真实压力。
- **父进程围栏 `run_isolated`**：子进程 + 采集线程 + 超时强杀，输出超限判定 `output-cap`，退出码 `0 ok / 1 script-error / 2 syntax / 3 circuit-breaker / 4 crash / 5 timeout / 6 output-cap`，编辑器与批量任务走这条。
- **熔断后可继续**：额度按次重置（同一解释器可连跑第二个脚本），跨执行熔断计数单独累计；6 次反复熔断后 RSS 仍 < 600 MB。
- **`impor stability`（新子命令）**：8 个敌意用例自检（死循环/卡死/刷屏/巨型分配/内存累积/无限递归/除零/大整数不误报），全过返回 0，`--json` 给机器读，`--limits` 打印两档额度。本机实测 **8/8 PASS**。
- **崩溃留证**：CLI 未捕获异常写 `~/.impor/crash.log`（512 KB 轮转）；编辑器接管 Tk 回调异常与主循环兜底。
- **顺手修掉既有缺陷**：`RecursionDeep` 此前没有错误码映射，递归深度报错落到 `E1099`；现为 `E1011`。
- **顺手修掉既有缺陷（诊断一致性）**：`run` 默认走原生后端后，原生产物的运行错误只打「位置/为什么/修复/片段/调用栈」，缺「文档：`docs/errors.md#E1xxx`」一条，且片段排在修复之后，与解释器五段式不一致——`error_audit` 因此从 20/20 掉到 13/20（7 个运行错误用例全部判「文档锚点缺失」）。已在 `impor/c_runtime.c` 的 `rt_report_error` / `rt_report_abort` 补锚点行并把片段移到「为什么」之前，审计回到 **20/20**；回归 26 项仍 OK。
- **实测对照**（`tools/stability_compare.py`，同一台机器、外部 10 s watchdog、五引擎同批敌意脚本）：Impor `--stable` **5/5 结构化熔断、0 挂起、0 原生崩溃**（最慢 0.97 s）；Python 1 例、Node 3 例、C 2 例只能被外部强杀，C 另在递归栈溢出与除零上原生崩溃（`0xC00000FD` / `0xC0000094`）。代价：同一段 20 万次循环负载，稳定档比默认档慢约 1.2–1.4 倍（两次实测）。详见 [`docs/稳定性对照-4.0.md`](docs/稳定性对照-4.0.md)。
- **边界**：只保证失败可控，不保证永不失败；内存是 RSS 采样而非硬配额；解释器自身/FFI 的原生崩溃仍靠围栏与容器兜。设计与边界见 [`docs/稳定-4.0.md`](docs/稳定-4.0.md)。
- **回归**：新增 `tests/test_v40_stability.py`（17 项）全过；`test_launcher` 白名单补 `stability`。

### `impor serve` 常驻 + 完整 C 后端接管（patch8）

- **`impor serve`（新子命令）**：`start [--bg] [--port N]` / `stop` / `status` / `run <脚本>`。常驻进程与客户端只用一行 JSON 往返（`{"script":…,"args":[…],"opt":2}` → `{"code":…,"out":…,"err":…,"tier":…,"ms":…}`），state 落在 `~/.impor/serve.json`。执行三档回退，`tier` 明确告诉调用方走了哪一档：`dll`（数值快路径产物以 DLL 形式在本进程内 `LoadLibrary` 调用）→ `full`（完整 C 后端产物 exe）→ `interp`（进程内解释器）。实测百万循环 **4.79 ms/请求**（19 次均值、同一条连接连发），对比启动器每次新起进程的 25.6 ms。
- **`impor-serve-client.exe`（新）**：连 `127.0.0.1` 发一行 JSON，`-n N` 同连接连发并报均值——编辑器与批量任务用它，比每次起启动器省掉一次进程创建。
- **完整 C 后端接管 `run` 与启动器的回落档（P3）**：`run` 的原生链从「快路径 → 解释器」变为「快路径 → 完整 `ctrans` 后端 → 解释器」。字符串、列表、`len`、函数、递归、异常脚本因此不再回落解释器：同一份 `g1.impor`（输出 `hi Impor 3 499999500000`）**8879 → 230.3 ms**（≈38×），启动器口径 **31.6 ms**。完整后端 24 例对拍 **24/24**。全新手写 C 字节码 VM 的方案被实测否掉（百万循环 17.2 ms，比编译产物 0.3 ms 慢 60 倍），所以复用已有后端。
- **浮点 `%` 与 `repr` 与 Python 对齐**：新增 `rt_fmod_py`（Python 的浮点 `%` 结果符号跟随除数，C `fmod` 跟随被除数）；`rt_fmt_double` 改为「`%.20e` 取 21 位 + 十进制串第 p 位四舍五入 + `strtod` 往返校验 + Python 记法规则」的最短往返表示，`0.1+0.2` 不再打成 `0.3`、`1e16` 不再打成 `1e+016`。**代价**：`strtod` 被引用后静态链接多约 100 KB，`hello.impor` 产物 153,088 → 255,488 B，因此 vitals 的 exe 体积承诺由 **≤160 KB 改为 ≤320 KB**（`docs/claims.yaml` 与 `tools/verify_release.py` 同步）。
- **修掉三个真缺陷**：① `range` 步长为 0 在完整后端是死循环（现在报错退出）；② 循环变量在 for 头部声明会遮蔽同名变量、循环后 `print(i)` 打 `None`（八个用例受影响，改为三界预求值 + 循环体首赋值）；③ **serve 的完整后端档每请求 726 ms**——根因不是缓存失效（`run-cache` 前后文件数差 0），而是无控制台的父进程 spawn 一个控制台子系统程序时 Windows 要为它新建 ConHost（实测 643–763 ms），加 `CREATE_NO_WINDOW` 后回到 **75.7 ms/请求**。
- **挡住一个回归**：完整后端直接接管带 `use std.*` 的脚本会破坏语义（`demo_19_split_join` 报 `E1006`，`compat_20` 掉到 20/22）。`_native_run` 增加 `_has_module_import(ast)` 门，命中 import 就跳过完整后端、回落解释器。
- **门禁**：unittest 五套 **35 tests OK**；快路径对拍 24/24；完整后端对拍 24/24；`check_fmt_all` 207/207；`verify_claims` 22/22；`error_audit` 20/20。新增 `tests/test_v43_serve_and_full.py`（4 项）。
- **边界**：serve 的 `dll` 档只覆盖数值快路径能编的脚本，字符串脚本走 `full` 档（75.7 ms，含一次产物进程创建）；随机浮点 fuzz 324 例仍有约 5 例末位不同（两串 round-trip 到同一 double，属显示层）；完整后端仍不覆盖 `import`。详见 [`docs/性能报告-v6.7.md`](docs/性能报告-v6.7.md)。

### `run` 默认原生 + DLL 启动器（patch7）

- **`impor run` 默认即原生**：不再需要记 `--native`。能静态编译的脚本先编成产物再执行（按源码指纹缓存在 `~/.impor/run-cache`），子集外脚本静默回落解释器；`--no-native`（或 `IMPOR_RUN_NATIVE=0`）才强制解释器。默认路径不打印任何 `[native]` 提示，输出与解释器逐字节一致。实测百万循环 **2132.4 → 207.4 ms**（10.3×）；`fib(25)` 187.1 → 192.4 ms、`print(1+2)` 174 → 177 ms——小脚本上产物进程创建（≈18 ms）与省下的 AST 遍历几乎抵平，收益只在长循环上。
- **`impor build --dll`（新）**：产出启动器专用 DLL（入口 `impor_entry`，`__declspec(dllexport)`，结尾 `fflush(stdout)`）；`--dll` 与 `--lib` / `--c-source` 互斥，内置 tcc 明确报错（32 位 DLL 无法被 64 位启动器加载）。
- **`impor-fast.exe` 改为本进程内调 DLL**：命中缓存即 `LoadLibrary` + `GetProcAddress("impor_entry")` 调用，不再 `CreateProcess`，也不调 `FreeLibrary`（进程即将退出，省一次卸载）；未命中先试编 DLL，失败落 `.nodll` 标记，再退老 `exe` + `_P_OVERLAY`，最后回落解释器。实测 `impor <脚本>` **38.1 / 39.7 → 25.1 / 25.6 ms**（`noop.exe` 进程创建地板 18.6 ms，DLL 加载+调用 2.4–3.7 ms），启动器 23,040 B。
- **静默探测**：冷跑试编 DLL / exe 失败的诊断不再漏到用户眼前（探测子进程 stderr 重定向到 `NUL`）。
- **修掉两个真缺陷**：① `_native_run` 每次热跑多付一次 `gcc --version`（实测 **147.5 ms**），P0 收益被吃光——改为 `_native_cache_key`（键只含源码/工具链文件指纹/版本/优化级，不含编译器身份，产物存在即执行）；② 默认原生之后 `const A = 1; A = 2` 被快路径静默放行，`error_audit` 从 20/20 掉到 19/20——在 `_native_run` 开头跑 `_reject_const_mutation`，命中即交解释器出完整诊断。
- **口径**：25 ms 不等于 C 的 16.5 ms；差距全在进程创建地板。追平 C 的只有 `impor build` 产物（16.3 / 16.9 ms）。`impor run` 的下界约 200 ms（启动 Python 查缓存本身 ≈174 ms），再快只能走常驻服务或重写 C 内核。详见 `docs/性能报告-v6.6.md`。
- **回归**：`tests/test_v42_native_run.py` +3 项（默认/回落输出一致且无提示、子集外静默回落、DLL 含 `impor_entry` 导出名）；unittest 四套 **31 OK**、对拍 **24/24**、`check_fmt_all` **207/207**、`verify_claims` **22/22**、`error_audit` **20/20**。
- **边界**：`run` 缓存不覆盖编译器身份（换 gcc 请删 `~/.impor/run-cache`）；冷跑首次 50–60 ms（Defender 扫描 + 首次编译），不是语言开销。

### `impor run` 的 C 级路径：透明编译 + 原生启动器（patch6）

- **`impor run --native`**：命令、门禁、错误信息不变；能静态编译的脚本先编成原生产物再执行，产物按「源码 + 工具链指纹」缓存在 `~/.impor/run-cache`，子集外脚本自动回落解释器并提示。实测百万循环 **2061 ms → 305 ms**。
- **`impor-fast.exe`（新增原生启动器，21 KB）**：`impor.bat` 早前预留的入口现在有了实现——命中缓存直接把进程换成编译好的 exe（`_P_OVERLAY`，不再起第二个 Python），未命中则 `build --quiet` 静默编译后执行，编译失败回落 `run`。实测 `impor <脚本>` 百万循环 **39.7 ms**、`fib(25)` **38.1 ms**（C 16.7 / 16.5 ms；构建产物 16.3 / 16.9 ms）。
- **`impor build --quiet`**：只产出文件，不打印构建摘要（供启动器调用，避免污染程序输出）。
- **`impor.bat`**：传 `IMPOR_CALLER_CWD`，保证相对脚本路径按调用者目录解析。
- **口径**：纯 Python 的 AST 解释器有物理地板（`python -S -I` 空启动 39 ms、导入 `impor.cli` 197 ms），解释器内部优化不可能把 2061 ms 做到 16 ms；能到 C 量级的是产物与「run 走原生启动器」这条命令。详见 `docs/性能报告-v6.5.md`。
- **回归**：新增 `tests/test_v42_native_run.py`（4 项）；`check_fmt_all` 207/207、`verify_claims` 22/22。

### 产物逼平 C：默认换 gcc -O2 + 快路径扩容（patch5）

- **`impor build` 默认不再用内置 tcc**：新增 `--cc auto|tcc|gcc|clang|<路径>` 与 `-O0..-O3`（`--opt N`），`auto` 在找得到 x86_64 gcc 时用 **gcc -O2**，否则退回内置 tcc；`IMPOR_BUILD_CC` 可全局改默认。构建摘要新增「编译器：gcc.exe -O2（版本行）」，缓存键带上编译器与优化级，换编译器不会命中旧产物。
- **同一份生成的 C，换编译器实测**（本机、含进程启动、取最优）：数值快路径 `fib(25)` 从 tcc 的 16.6–19.8 ms 变成 gcc -O2 的 **16.6 ms**；**完整 `Val` 后端百万循环从 tcc 的 210.6 ms 变成 17.7 ms**。结论修正：此前说的“装箱 8 倍惩罚”其实是 tcc 不会内联/优化，不是值模型本身的问题。
- **交付形态（默认 `impor build`）**：`fib(25)` **21.5 ms**、百万循环 **20.7 ms**、浮点百万循环 **22.9 ms**（手写 C 基准 19.6 / 19.2 ms）；`--no-fast` 完整后端百万循环 **20.0 ms**（与快路径同量级）。详见 `docs/性能报告-v6.4.md`。
- **快路径扩容**：`range(A, B)` / `range(A, B, STEP)` 进数值快路径（此前一律回退）；循环结束后变量停在**最后一个值**（patch4 停在越界值，已修）；`print` 支持多参数混排（`print("合计", s, "结束")`）；出现浮点字面量则该脚本数值域切成 `double`，输出按 Python 最短往返 repr（`1.0` / `-0.0` / `1e+16` / `1e-05` / `0.30000000000000004` 全部一致）。
- **明确回退**：同一变量整数/浮点混用、浮点脚本含 `def`、`range` 边界为浮点 → 自动落完整后端（不静默改语义）。
- **回归**：新增 `tests/test_v42_fast_path.py`（13 项）；`tests/test_v41_cfast_for.py` 按新语义更新（`range(A,B)` 由“应回退”改为“应支持”）。对拍 24 用例 **24/24**；`check_fmt_all` 207/207、`verify_claims` 22/22。

### 计数循环进入数值快路径（patch4）

- **`for 变量 in range(N)` 不再回退**：此前 `cfast` 完全不认识 `For`，最常见的计数循环被静默回退到完整 `Val` 后端。现在由 `cfast` 直接生成 C 的 `for`，`range(N)` 的界在进循环时求值一次（循环体改 `N` 不影响迭代次数），循环体里的 `break` / `continue` 语义与解释器一致。
- **实测（本机、含进程启动、7 次取最优）**：`fib(25)` **24.8 ms**、100 万次累加 **34.0 ms**（同一脚本解释器为 287 ms / 2139 ms；`For` 未进快路径前是 214.4 ms）。详见 `docs/性能报告-v6.3.md`。
- **边界仍明确**：`range(a, b)` / `range(a, b, step)` 与浮点界继续回退完整后端（不静默改语义）；快路径仍是 `long long`，溢出报中文错误并以退出码 3 结束，任意精度请 `--no-fast`。
- **新增回归** `tests/test_v41_cfast_for.py`（6 项）：能编、输出与解释器逐行一致、界只求值一次、越界写法明确回退、`break` 一致。

### C ABI 交付层四关口（v4.1 修订）

- **`FreeLibrary` 崩溃修好**：根因是“调用过会分配内存的导出函数 + 卸载 DLL”时，CRT/atexit 钩子在进程退出阶段踩到已卸载模块。修法三件套：DLL 模式跳过 `atexit(rt_shutdown)`（`IMPOR_BUILD_DLL`），Windows DLL 改用进程堆分配器（`rtd_alloc/rtd_free/...` 覆盖 `malloc/free`），并在每个导出函数首行调 `impor_pin_once()`（运行时 `GetModuleHandleExW` 带 `PIN` 标志钉住自身）。32 位宿主 mode0–6 与 `host_free` 全部 rc=0。
- **64 位产物可用**：`impor/cli.py` 新增 `find_gcc64()`（`IMPOR_CC` → PATH → 常见 MinGW 安装位，`gcc -dumpmachine` 校验 x86_64）；`--target win64` 在缺 `cl` 时自动回落 x86_64 gcc，编译后校验 PE `Machine=0x8664`，不符即拒绝交付、不静默退回 32 位 tcc。调用时把 gcc 自身 `bin` 前置进子进程 `PATH`（否则找不到 `cc1/as/ld` 会静默失败），命令带 `-std=gnu99`（TDM gcc 4.8 默认 gnu89）。
- **Linux 产物跑通**：`--target linux64` 补 `-std=gnu99`，并校验产物确为 ELF64 / `Machine=62`；`.github/workflows/c-abi-x64.yml` 新增 `linux64-runtime` job：ubuntu 上生成 `liblogic.so`、校验导出符号、再实跑 C 静态宿主、C `dlopen/dlclose` 宿主与 Python `ctypes` 宿主，三者退出码均为 0。CI 运行 `C ABI matrix #44890796019` 五格全绿，日志留档 `docs/ci-evidence.md` + `docs/ci-2026-11-21-linux64-job.log`。
- **落地示例**：新增 `examples/cabi_demo/`（`logic.impor` + `host_c.c` / `host_c_dyn.c` / `host_python.py` / `host_rust.rs` / README），一份逻辑三种宿主直接调用；`host_c_dyn.c` 同一份源码在 Windows 走 `LoadLibrary/FreeLibrary`、在 Linux 走 `dlopen/dlclose`。

### 语法：`nonlocal` 后不再强制 `set`

- `nonlocal count = count + 1` 与 `nonlocal set count = count + 1` 等价（`global` 同理），旧写法继续支持，`impor fmt` 两种写法都原样保留。自举编译器 `bootstrap/imporc.impor` 一直允许省略，此前属于四端不一致，v4.1 让参考解析器对齐。新增 `tests/test_v41_nonlocal.py` 覆盖 解释器 / 转译 / C 后端 / imporc 四条路径。

### 头部标记门禁

- `tools/check_fmt_all.py` 增加“首行头部形态”校验：头部若存在必须与规范逐字一致（`important<<DAY<<english("English"):`），写花（多空格 / 缺引号 / 粘贴串行）判红；头部仍可省略（v2.3.1 起的既有规则）。该串是语言头部指令，不是乱码或生成残留。
- `tools/verify_claims.py` 的构建数字补上“跳过多少个基线/生成文件”，README 首屏口径改为 `207/207 通过（另有 21 个基线/生成文件按规则跳过）`，与树内 228 个 `.impor` 对得上账。

### 四端一致性收口 + 默认构建提速（v4.1 修订）

- **异常在 C 后端补齐**：`exception` 声明、`raise T("消息")`、`catch T e` 的类型过滤，以及类型不匹配时“先跑 finally 再抛给外一层”的语义（`rt_err_matches` / `rt_rethrow`）。数组越界与除零从 `rt_abort`（直接退出）改成 `rt_throw`（可被 `try/catch` 捕获）。`examples/demo_29_exceptions.impor`、`examples/errors.impor` 与“嵌套 try + 类型不匹配”用例，解释器与独立 exe 的 stdout 逐字节一致。
- **`impor build` 默认走数值快路径**：`--fast` 从显式 opt-in 变为默认行为，新增 `--no-fast` 退回完整 Val 后端；`--lib` / `--c-source` 固定走完整后端。快路径的 `+ - * %` 与取负换成带 64 位溢出检查的 `iadd/isub/imul/ineg`，溢出时打印中文错误并以退出码 3 结束、提示改用 `--no-fast`，不再静默回绕。构建摘要新增「后端：」一行。
- **JSON 浮点**：C 运行时改用最短可回读的浮点写法（`6.6` 而不是 `6.5999999999999996`），整数值浮点保证带 `.0`。
- **能力矩阵落地**：新增 `tools/support_matrix.py`，16 个用例同时跑 解释器 / 转译器 / C 后端(完整) / C 后端(auto) / imporc，直接生成 `docs/能力矩阵.md`；`--check` 只在已知基线之外出现新不一致时判红，基线在 `docs/能力矩阵基线.json`。
- 实测（本机）：fib(28) 解释器 292 ms、完整 Val 后端 456 ms、默认构建 42 ms；矩阵 16 例 C 后端 14/16 与解释器一致，imporc 7/16（`import` 与较新内建未进 imporc 映射表，仍为已知缺口）。

### 包管理器：装完即可 import（vendor 化）

- `impor pkg install` 默认把包装到 `<项目>/lib/<名字>/`，并把 `./lib`（必要时补 `./lib/<名字>`）写进 `impor.toml` 的 `[module_search_paths]`，装完直接 `import` 可用；`--no-vendor` 保留旧的"只进缓存"行为。
- `impor pkg install` / `impor pkg remove` 不再在缺少 `impor.toml` 的目录里凭空生成配置：此时只写全局缓存并打印提示（此前会往当前目录写入 `impor.toml` + `impor.lock`）。
- `impor/config.py`：全局缓存下**每个已安装包目录**也加入模块搜索路径，修复"安装成功但 `import` 报 `E1015`"（`<包>/<名字>.impor` 这种没有 `__pkg__.impor` 的布局）。
- `impor.toml` 的 `[dependencies]` 与 `[module_search_paths]` 改为按行编辑，保留注释与原有顺序（此前 `_write_toml` 整篇重写会删掉注释）。

验收：`tests.test_impor` / `tests.test_v51` 中 pkg 相关 14 项通过；自建 25 条 vendor 断言全过；`tools/verify_release.py` 22/22。

### 后端语义对齐：`impor run --int64`

- 新增 `--int64`（别名 `--strict-int`）：把解释器切到 C 后端的 int64 语义，加 / 减 / 乘 / 取负 / 字面量越界一律报 `整数溢出：超出 64 位有符号整数范围`（`E1012`）。默认解释器仍是 Python 任意精度、行为不变；这条开关把"两个引擎说同样的话、答案不一样"从只能靠人记变成可检出的错误。
- 数值快路径（`--fast`）、`c_runtime.c`、解释器 `--int64` 三处溢出文案逐字统一为 `整数溢出：超出 64 位有符号整数范围 [E1012]`；旧文案"数值快路径的 64 位整数溢出"退役，`impor/cfast.py` 的提示改为指向 `--no-fast` / `--no-native` 与 `--int64` 自检。
- 修掉一处真实的闸门绕过：`visit_BinOp` 里"双数字字面量快速路径"在溢出检查之前直接返回，`print(9223372036854775807 + 1)` 因此能穿过 `--int64`；现已与变量热路径共用同一闸门。

### 递归上限可配

- 写死的 3200 层改为 `--max-depth N` 或 `IMPOR_MAX_DEPTH`；树遍历与快速后端同步生效，Python 帧上限按深度等比放宽。

### 头部标记退役

- `impor new` 的 script / lib / game / pkg 四种模板不再在首行写 `important<<DAY<<english("English"):`；版本信息归 `impor.toml` 的 `[project] version`。
- 缺头部默认不再打印提示（旧行为可用 `IMPOR_HEADER_HINT=1` 打开）；已有头部的文件继续解析，`tools/check_fmt_all.py` 仍校验逐字一致。

### 规范与文档单一化

- `docs/语言规范.md` §0 重写：删除"按机器整数回绕"（它与同文件"C 后端溢出报错"自相矛盾），新增四行后端语义矩阵与 `--int64` 自检命令；"唯一写法"补上准确边界（删除的 12 类 vs 长期兼容的 `elseif` / `switch` / 缩进块 / 三种注释 / 头部）。
- `conformance/README.md` 的"语义差异"改为"可用 `--int64` 比对"，并指向新单测 `tests/test_v4_backend_parity.py`（12 项）。

验收：`tests.test_v4_backend_parity` 12/12（含 C 产物同句比对）；`tools/verify_release.py` 22/22；pkg 相关 14 项不变。

## [4.0.0] - 2026-11-21

### 唯一写法：12 类兼容写法物理删除

- `impor/lexer.py` 从 `KEYWORDS` 删除 `func` / `loop` / `local`，`DEPRECATED_ALIASES` 只保留 `elseif` / `switch`。
- `impor/parser.py` 删除 12 类分支：`func`、`loop`、`let/local`、裸 `print`、`:=`、`++/--`、`&& || !`、`<>`、`then`、`unless`、`for each/of`、`1..n`、C 风格 `for`、`do..until`。`imporc.impor` 同步删除对 `func` 的识别；旧写法统一报 `E2002`，不静默降级。裸 `print` 在语句位置按内建标识符拦截（`print "hi"` / `print 1 + 2` / `print x` 均 `E2002`），`print(...)` 正常；`impor lint` 对这类文件返回 1 并带错误码，不再输出“未发现问题”。
- 源码迁移：`bootstrap/imporc.impor` 55 处 `func→def`，并删除其 4 处 `func` 解析分支；`tests/` 33 个文件 191 处；`conformance/compat_3_11/` 9 个 fixture 改为 `def` / `while true`，`manifest.json` 重算哈希并标注 `syntax_version: 4.0.0`。
- API 基线关键字 58 → 55；新增 `tests/test_v4_alias_removed.py` 锁定删除项。

### 验收自证

- `tools/check_fmt_all.py` 改为文件系统扫描（`os.walk`），不再调用 `git ls-files`；发布包内可直接跑。
- `tools/verify_claims.py` 去掉 `git rev-parse`；README 首屏的版本、保留字、内建、标准库、错误码、格式门禁数字全部从发布包内文件计算并写入标记块，`--check` 会抓漂移。
- README 删除硬编码的 47 MB / 0.8 s / 121.5 KB / 16.8 MB / 28 ms；改为工具现场打印或指向复现命令。

### 明确边界

- `elseif` / `switch`、分号、冒号缩进块、`extends`、`new A` 省括号、`super.m()`、`catch {}`、裸 `exit`、隐式返回、`var x`、`static/readonly` 仍兼容，不在本次 12 类清单内。
- README 没有“可 catch 熔断”的承诺；本版未新增步数/时间预算 API，避免把未实现的能力写成文案。

## [3.14.0] - 2026-11-21

### 验收机制变成可执行事实

- `tools/api_baseline.json` 进仓库 `tools/` 并随发布包分发；`api_frozen` 继续按基线校验公开面。
- README 兑现率改由 `python tools/verify_claims.py` 直出，手写数字被标记块替换；新增 `--check` 模式，README 或 `docs/claims.md` 与脚本结果不一致时直接失败。当前自动验证 22/22，人工验收 2 项。
- 新增 `tests/test_v314.py`：缺文件干净失败、仅 WARN 时 rc=0、冒烟不创建 Tk 三项锁死。

### 全仓格式化锁绿

- `tools/check_fmt_all.py` 进入 CI 与发布门禁：受版本控制的 237 个 `.impor` 中，216 个纳入门禁并全部通过；跳过的 21 个是 3.11 冻结 fixture（20）与自举生成物 `bootstrap/imporc.impor`（1）。
- 同步修复 `impor/fmt.py` 三个真实缺陷：大括号块内缩进子块被拍平、`return [` 前空格丢失、一元负号后多空格；修复后对 108 个改动文件做 AST 对比，语义未变。

### 后端口径定稿

- 解释器是唯一语义定义；C 后端明确为“语法子集，不支持即编译期拒绝”，不再自称唯一发布通道。
- 删除代码与当前文档中的旧口径残留；历史 CHANGELOG 标注“该口径已废止”，不篡改历史事实。

### 退出码与测试入口

- `impor check`：只有 error 级问题返回非零，格式未达标只给 WARN；文件不存在输出干净失败信息，不再抛 `UnboundLocalError` 或内部栈。
- `examples/snake.impor --smoke` 走纯逻辑 60 帧，不创建 `Tk()`，无显示环境也能跑。

### 未做（明确边界到 v4）

- 12 类同义写法仍保留；物理删别名、edition 开关与 parser 分支删除留给 v4，v3.14 只完成格式化锁绿与口径收口。

## [3.13.0] - 2026-11-20

### 已修：声明语义变真

- `set q = 5` / `q = 5` / `q := 5` 在 `q` 未声明时，解释器、`impor run`、`impor lint`、`impor build`、`impor compile` 全部拒绝；不再静默创建变量。
- `Env.update_nearest` 找不到绑定改为抛 `NameError`；裸赋值不再被解析成 `local` 声明；快速后端对 `update/global/nonlocal` 强制回退树遍历。
- `impor lint` 新增 `undeclared-assign`；`build` / `compile` 新增 `_decl_gate` 静态门禁。
- enum 合成变量单独豁免，全仓 206 个 `.impor` 重扫零误报。
- 回归：`tests/test_strict_declarations.py`（四端 + 声明后更新 + 函数内更新）。

### 已改口径：不再自称“好语言”

- 定位写死为 **教学语言 + 小型工具 + 可发布玩具**。
- 删除“C 后端是唯一发布通道”的旧口径，改为“解释器/便携版是默认路径，C 后端是可选的脱离 Python 分发路径”。
- README 新增 `fib(28)` 算力实测：解释器 301–331 ms，C exe 473–479 ms，**C 后端更慢**；删除“原生更快”的误读空间。
- 类型注解降级为“仅字面量静态提示”，新增 `docs/类型注解边界.md`。
- 修复 `docs/语言规范.md` 两个 `## 14`（C 后端改为 §23）与 19.2 中文关键字矛盾。

### 未做（明确边界）

- 12 类同义写法未物理删除；`impor fmt` 与 `impor lint --style` 负责收敛，兼容层标为 deprecated。
- C 后端大整数 / 自定义异常未补齐，边界见 `docs/3.13-后端能力边界.md`。
- CHANGELOG 历史未拆分，本版只停止继续膨胀。

## [3.12.0] - 2026-11-19

### 第 0 层：陌生人 3 分钟

- 新增 `START_HERE.txt`（5 行中文）与 `常见问题.txt`（窗口一闪而过 / Defender / 找不到 Python / 权限 / 中文方块 / 如何 build）。
- 新增 `examples/quickstart/`：`hello.impor`、`read_file.impor`、`call_dll.impor` 与三个双击 `.bat`。
- 新增 `ImporPortable.exe`（16.8 MB，自带 Python 运行时与内置 tcc），在没装 Python 的 Windows 上 `run` / `build` 都可直接双击。
- 新增 `portable_entry.py` / `ImporPortable.spec`；spec 显式打包 `impor/c_runtime.c` 与 `impor/tcc`，修复便携版 `build` 找不到运行时源码的问题。

### 第 1 层：错误说人话

- 新增语法错误码 `E2001–E2007 / E2099`；运行错误继续使用 `E1001–E1014 / E1099`。
- 每条错误输出位置、人话原因、怎么修、稳定错误码、`docs/errors.md#E****` 文档锚点。
- 新增 `docs/errors.md`（24 个错误码的可跳转锚点与正确写法）。
- `defer expr` 文案改为「defer 需要块写法 `defer { ... }`；示例：defer { close(f) }」。
- 新增 `tools/error_audit.py`：20 条真实 CLI 探针，20/20 合规，已接入 CI。

### 第 2 层：不变卦

- 新增 `api_baseline.json` + `tools/api_baseline.py`：冻结关键字、内建、模块、错误码；新增项必须带 RFC 才能刷新。
- 新增 `conformance/compat_3_11/`：20 个 3.11 示例 + SHA256 manifest；`tools/compat_check.py` 在最新版逐条运行，20/20。
- 新增 `docs/compatibility.md`：3.12–3.14 不删除任何已发布 API；破坏性变更必须走 RFC。
- 新增 `docs/rfc/0000-template.md`。

### 第 3 层：承诺可验证

- 新增 `tools/verify_release.py`（21 个 check）与 `tools/verify_claims.py`。
- 新增 `docs/claims.yaml`（21 条自动 + 2 条人工）与自动生成的 `docs/claims.md`。
- 当前自动兑现率 **21/21（100%）**；人工项（陌生人 3 分钟、前 10 个真实用户）不冒充已验证。
- CI 增跑 `api_baseline.py` / `compat_check.py` / `error_audit.py` / `verify_claims.py`。

### 第 4 层：三个真项目

- `apps/impor_ssg`：Markdown + 模板 → 4 页静态站点；解释器与 C 后端 exe 都能跑，exe 约 156 KB。
- `apps/impor_action`：用 Impor 写的零依赖 GitHub Action；扫描 `.impor` 并拒绝 `async`，exe 约 144 KB。
- `apps/impor_sqlite_report`：10 万行 CSV → SQLite → HTML 报表；`0.8 s`、峰值 `47 MB`、总金额 `100401555.2`。

### 第 5 层：社区入口

- 重写 `CONTRIBUTING.md`：3 分钟跑通、bug 必填项、本地验证、新增 API 四步、good first issue 范围。
- 新增 `.github/ISSUE_TEMPLATE/`（bug / feature / config）。
- 新增 `docs/community.md` 与 `docs/meeting/2026-11.md`：明确 10 个真实用户前不建群 / 论坛。

### 新内建与运行时

- 新增 `strfind` / `strlines` / `strjoin` / `strreplace` / `filelines`；解释器、C 运行时、`builtin_manifest`、`runtime.py` 四端同名同义。
- C 后端补 `fileexists / filesize / filemkdir / filelist`。
- 修复 FFI 每次调用不释放参数数组导致的 10 万行级内存堆积。
- 新增 `tests/test_text_builtins.py` 覆盖文本/文件内建与注册表一致性。

### 3.12.0 收尾修复（2026-11-20）

- 便携版 `build` 不再出现「exe 图标写入失败」：冻结版改为在进程内对副本写图标并原子替换，
  并对刚产出的 exe 被扫描器短暂占用做重试；同时消除图标子进程的 GBK 解码异常。
- 修正 `imporc` 版本线：`tools/sync_version.py` 同步 `bootstrap/imporc.impor` 版本串；
  重建改用 `bootstrap/build_with_python.py --install`，并让 `rebuild_imporc.py` 在种子失败时直接报错，
  不再把旧 exe 误拷成新版本。
- 修复用户自定义函数被内建劫持：`ctrans` 先查用户函数表再查内建，`imporc` 的 `strfind(hay, needle)` 恢复为 2 参数语义。
- 修复整除/取余除零时调用栈为空：现在抛带行号的 `ZeroDivisionError`，栈帧完整。
- 语法层接受 `then` / `do` / `则` 作为块引导；`打印` / `输出` 仅作普通标识符，不再静默当作关键字。
- `ModuleNotFound` 独立为 `E1015`（此前与 `ImportError` 共用 `E1010`），错误码表恢复唯一；
  新增 `docs/rfc/0002-error-codes.md` 并刷新 `api_baseline.json`。
- 回归：全量 `719` 条测试通过；`verify_release.py` 21/21；`verify_claims.py` 自动项 21/21。
- 增量构建缓存键纳入 `c_runtime.c` / `ctrans.py` / `cfast.py` 指纹，改运行时或转译器不再命中旧 exe。
- `fileread` 在复制成字符串后释放临时缓冲，反复读取大文件不再堆积。
- 桌面打包不再复制 `apps/impor_sqlite_report/report.db`、`report.html` 与 `apps/impor_ssg/_site/` 运行产物。
- 修复安装到含括号目录（如 `Impor(I++) 3.12.0`）时双击入口报「未知命令: (」：`.bat` 的 `if (...)` 块会被路径里的 `)` 截断，
  入口与三个快速开始脚本改为 `goto` 结构，`install.bat` 的 `cd /d` 补引号；桌面发布目录同步改为无括号的 `Impor 3.12.0`。
- 入口中文提示不再由 `echo` 输出，改由 `type examples/quickstart/_welcome.txt` 输出，避免 cmd 在部分双击环境下把全角冒号误当命令分隔符。
- CLI 首次运行不再打印旧版的“未找到可选的头部标记”提示；头部标记自 v2.3 起就是可选项，新手入口保持干净输出。

## [3.11.0] - 2026-11-18

### 修复：未实现关键字不再静默执行

- `async def` / `await expr` 在语句或表达式位置出现时语法错误 exit 2；
  `defer expr` 这类非代码块写法同样拒绝，`defer { ... }` 的 LIFO 延迟语义保持不变。
  文案统一为「xxx 尚未实现（v3.x 计划）；不再静默接受或执行」。
- `interface / namespace / abstract / override / implements / sealed / final` 等无运行时
  语义的保留字同样明确拒绝，避免“写了像支持”。
- `static / readonly` 仅在变量声明位置保留为 `const` 别名；方法/类修饰位置明确拒绝。
- 修复快速转译路径绕过 `const` 检查的问题：含 `const` 的脚本回退树遍历，
  `const x = 5; x = 6` 现在抛 `ConstError`（rc=1）；C 后端在构建期拒绝同一脚本。
- 新增 `tools/keyword_audit.py`：遍历全部关键字，输出
  「已实现 / 明确拒绝 / 静默接受」三张表；静默接受非空即 exit 1，已接入 CI。
- 新增 `tests/test_semantic_honesty.py`，用真实 CLI 断言上述退出码与文案。

### 新增：C ABI 矩阵（4 格 push 门禁 + macOS 2 格手动）

- `.github/workflows/c-abi-x64.yml` 每次 push 跑四格：win32（内置 tcc）、win64（MSVC）、
  linux64（gcc -m64）、linux-arm64（aarch64 交叉）。
- macOS arm64 / x64 两格在 `.github/workflows/c-abi-macos.yml`，`workflow_dispatch` 手动触发。
  当前账号的 macOS runner 分钟数不可用，push 触发会排队后被取消，因此不挂在 push 门禁上。
- 每格校验产物机器类型（PE `0x14c` / `0x8664`、ELF x86-64 / AArch64、
  Mach-O x86_64 / arm64）与四个导出符号 `impor_add / impor_sub / impor_mul / impor_greet`。
- 新增 `tools/check_artifact.py`，本地与 CI 共用同一套 PE / ELF / Mach-O 解析。

### 新增：`tools/compare.py` 多语言真跑对照

- 同一份 `benchmarks/compare/case.json`（1000 整数求和）分别用 Impor / TCC-C / Lua 5.4 /
  Python(PyInstaller onefile) / Go / Node 跑；固定 6 行，缺运行时填「未测量」。
- 表分「总耗时」与「纯启动」两列：总耗时 = 启动 + 读 JSON + 求和 + 退出；纯启动 = 同产物走空路径。
- 本机结果：Impor 120.5 KB / 21.44 ms / 22.41 ms / 6.0 MB / 无依赖；TCC-C 2.5 KB；
  Lua 477.5 KB（含运行时）；Python onefile 7.8 MB / 1.26 s；Go 1.9 MB；Node 76.8 MB。
  README 只宣称“体积 + 零依赖 + 可被宿主加载”三项组合，不宣称启动快于 TCC-C / Lua / Go。
- `--python-mode {auto,onefile,onedir,system}`：auto 现场用 PyInstaller 构建到临时目录，
  失败 exit 2，不静默降级成 system。
- 默认只写 `compare.local.json` / `docs/compare.local.md`；`--publish` 才覆盖正式文件。
- `--strict` 任一行未测量即 exit 1；`--runs < 10` 表头标「样本不足」，strict 直接拒绝。
- JSON 补 CPU / 内存 / OS build / commit / case sha256 / 各运行时版本；构建产物全部进临时目录。

### 修复：5 分钟陌生人测试在默认 PowerShell 下不可用

- `tools/five_minute_check.ps1` 改为 **UTF-8 with BOM**，Windows PowerShell 5.1 与 PowerShell 7
  都能直接按 `powershell -File` / `pwsh -File` 运行；此前无 BOM 会被 5.1 按 ANSI 读成乱码并立刻 exit 1。
- `conformance/run_conformance.py` 新增 `--sample N`（排序后等距抽样，去重补足），
  `five_minute_check.ps1` 默认抽 30 个用例，`-Full` 跑全量。本机 5.1 实测 31.01 s，pwsh 29.58 s。

### 文档：私有仓库的 CI 口径

- README 移除会 404 的 shields 徽章，新增「CI 证据」一节，如实写明仓库当前为 private、
  匿名 API / 徽章 / Actions 运行页对外不可核验。
- `docs/ci-evidence.md` 与两张 Actions 截图入档；只有仓库转 public 后，才把徽章作为对外证据。

### 新增：`impor bench-spec`

- `impor bench-spec FILE.impor [--runs N] [--json]` 输出四项：
  exe 体积、冷启动均值、常驻内存、构建耗时。
- README 首页规格表只列体积 / 启动 / 依赖 / 可被 C 调用四列，数字可用命令复现。

## [3.9.0] - 2026-11-18

### 新增：`impor build --lib --c-source`（64 位入口）

- 新增一级产物：`impor build --lib --c-source lib.impor -o out/`，产出
  `lib.c` / `lib.h` / `lib.def` / `build_msvc.bat` / `build_gcc.bat`。
- 内置 tcc 只产 **32 位（i386）**；需要 x64 DLL 时由用户机器上的 `cl` 或
  `gcc -m64` 编译源码包，不内嵌 w64devkit。
- `build --lib` 结束打印产物位宽；`impor sys-check` 新增“产物位宽”项。
- `lib.h` 不随位宽变化，`demo.c` 不改即可链接 x64 `lib.lib` / MinGW。
- `--c-source` 生成的 `lib.c` 不再包含 `main`，纯库源码；普通 exe / `--lib` 仍生成 `main`。
- 导出包装改用 `IMPOR_API` 宏：Windows 为 `__declspec(dllexport)`，POSIX 为 `visibility("default")`，同一份源码可交给 MSVC 或 gcc。
- 本机无 `cl/gcc`，x64 编译未实测；已用 tcc 对 `lib.c` 做过 32 位自检。
- 新增 `.github/workflows/c-abi-x64.yml`：`windows-latest` 跑 MSVC `cl /LD`，`ubuntu-latest` 跑 `gcc -shared -m64`，校验 PE/ELF 为 x64 且四个导出符号齐全。

### 新增：C 后端 FFI（`ffi_open` / `ffi_call`）

- 不新增语法，解释器已有的 `ffi_open` / `ffi_call` 在 C 后端可用。
- Windows `LoadLibraryA` + `GetProcAddress`，POSIX `dlopen` + `dlsym`。
- 参数编组 `int → long long`、`bool → int`、`float → double`、`cstr → const char *`，
  最多 8 参；返回值支持 `int/float/bool/cstr/void`。
- 允许清单 `.impor/ffi_allow.json` + `IMPOR_FFI_ALLOW`（逗号或分号），
  C 后端编译期烘焙，运行时不可扩。
- 未登记库、超 8 参、动态 `argtypes` 编译期 exit 3；C 后端明确拒绝 `ptr`、结构体、回调。
- 修复自举编译器内嵌运行时的 `g_ffi_allow_n` 未声明：新增
  `IMPOR_FFI_ALLOW_BAKED` 宏与空清单回退。

### 修复：两个实测体验伤

- 允许清单分隔符同时接受 `;` 与 `,`；`FFIAccessError` 回显当前生效清单。
- `FFIAccessError` / `FFIError` 的诊断解释按异常类分派，不再一律套“private 符号可见性”。
- **位宽诊断**：`impor run` 的宿主是 64 位 Python，`impor build` 默认产物是 32 位。加载前读 PE `Machine` 字段，位宽不匹配抛 `FFIArchError`（不再套成 `FFIError` 签名不匹配）；解释器与 C 后端都做了这项检查。`impor sys-check` 新增“解释器位宽”，与“产物位宽”并列。
- **自举编译器同步**：用 3.8.4 自举重建 `bootstrap/imporc.impor`，补 8MB 栈后写入 `impor/imporc.exe`，`imporc version` 现在自报 3.9.0，不再与 `VERSION` 脱节。
- **关键字计数断言**：`yield` / `with` 在 3.8.4 已进入保留字表，`KEYWORDS` 实际 58；两条旧断言从 56 更新为 58，避免“全量测试假红”。

### 测试

- 新增 `tests/test_v39x_ffi.py`：strlen、跨语言回环、未登记库、9 参、分号清单（64 位 `System32\msvcrt.dll` 正例）、C 源码包（断言无 `main`）、位宽输出、位宽不匹配 `FFIArchError`，共 8 条。

## [3.8.4] - 2026-11-18

### 修复：不再静默丢掉语义（A 组）

- **`yield` / `with` 编译期拒绝**：两者已进入关键字表；解析到语句位置时直接报
  `yield/with 尚未实现（v3.9 计划）`，exit 2；不再被当成普通标识符解析后输出 `None`。
- **默认参数 / 变参 / 可选参数**：`func f(a, b = 1)`、`func f(a...)`、`func f(a?)`
  在参数解析阶段直接报 `尚未实现（v3.9 计划）`，exit 2；不再以硬语法错误或静默忽略处理。
- **列表 / 字典推导式**：`[x for x in ...]`、`{x: x for x in ...}` 在字面量解析阶段
  直接报 `尚未实现（v3.9 计划）`，exit 2。
- **测试锁定**：`tests/test_v384.py` 对 `yield`、`with`、默认参数、列表推导式、字典
  推导式逐个断言“必须 exit 2 且提示 v3.9 计划”。

### 新增：C 后端 `try/catch/finally`

- `ctrans._gen_try()` 生成 `g_jmp_n++` + `setjmp(g_jmp_stack[...])` 跳转栈，catch 变量
  绑定为 `rt_err_obj()`；成功/异常路径都会把 `g_jmp_n` 恢复到进入 try 前的值。
- 运行时除法（`rt_div` / `rt_idiv`）从 `rt_abort` 改为 `rt_throw("ZeroDivisionError", ...)`，
  使错误可被 try/catch 捕获，而不是直接 abort。
- `_collect_writes` / `_iter_nested_defs` / `_collect_globals` 递归进入 try/catch/finally，
  try 内声明的局部变量与闭包正常分配。
- 验收：`impor build` 含 try/catch 的源码，运行后 catch 打印
  `CAUGHT ZeroDivisionError division by zero`，并以 `exit(7)` 返回 7。

### 本版未交付（如实列出）

- **B 组未实现**：默认参数 / 变参仍只“明确拒绝”，真生成器（yield）、推导式、`with`
  资源协议均未实现；路线为 v3.9。
- **C 组未实现**：变量类型稳定化、容器元素类型、null 安全规则未实现。
- **D 组未实现**：`std/net`、`std/sql`（SQLite）、公共包索引未实现。
- **E 组未实现**：5 项真实基准（JSON 1MB / 字典 100 万 / 字符串 10 万 / fib(30) /
  map-filter-reduce）未写入 README 门禁。
- **imporc 中文绝对输出路径**仍失败（`filemv E1099`）；`Impor.py build` 正常。


## [3.8.3] - 2026-11-17

### 修复：门禁不再接受虚假成功

- **UTF-8 BOM**：`fileread` 统一剥离文件开头的 UTF-8 BOM，imporc 不再把 BOM 报成 `无法识别的语句 [ \ufeff ]`；带 BOM 的 `.impor` 可正常编译。
- **单行/多行 `{}` 块**：imporc 的 `gen_block()` 接受大括号块，`func f() { return 1 }` 与跨行 `{ ... }` 都能通过，不再报 `无法识别的语句 [ { ]`。
- **失败不再返回 0**：语法编译失败、tcc 链接/编译失败都会 `exit(1)`，并且不生成产物；conformance 门禁可以真正捕获失败。
- **PyQt6 缺失时跳过**：`TestKernel3Preview` 改为按 `import PyQt6` 是否成功决定 skip，不再用 `os.name == "nt"` 误判为可运行。

### 新增：C ABI v2

- **字符串所有权**：新增 `impor_free_str(const char *)`；DLL 返回的字符串由 DLL 新分配，调用方必须用该函数释放，传入 `NULL` 安全。
- **类型映射扩展**：`size_t` 映射为 C `size_t`；`handle` / `ptr` 映射为 `void *`，用于不透明句柄和结构体按指针传递。
- `.h` 头文件加入 `<stddef.h>`，`.def` 导出表包含 `impor_free_str`。
- tcc 实测：`size_t` 传参/返回、`void *` 句柄往返、两次字符串返回后分别 `impor_free_str` 均正常。

### 本版未交付（如实列出）

- **MSVC `.lib` 未实测**：本机没有 `cl.exe` / `lib.exe` / `link.exe`，也没有 LLVM/MinGW 工具链；`.def` 已生成，MSVC 可用 `lib /def:lib.def /out:lib.lib` 自行生成，但本版不能声称“MSVC 链接已通过”。
- **调用方缓冲区字符串 API** 未实现；本版用 `impor_free_str` 解决所有权问题。
- 命名结构体的强类型指针未实现；本版用 `handle` / `ptr`（`void *`）表达不透明句柄与结构体指针。


## [3.8.2] - 2026-11-17

### 新增：`impor build --lib` 产出 C ABI 共享库

- 新语法 `export func`：解析后把函数标记为导出；`export 名字` / `export {名字}` 的旧写法保持可用。
- `impor build --lib x.impor -o x.dll` 同时产出 `x.dll`、`x.h`、`x.def`：
  - `x.h` 带 `extern "C"`，符号统一 `impor_` 前缀（如 `impor_add`）；
  - 类型映射 `int→long long`、`float→double`、`bool→int`、`str→const char *`；
  - 字符串返回值由 DLL 持有，调用方不要 `free`。
- 实测链路：`impor build --lib lib.impor -o lib.dll` → `tcc demo.c lib.dll -o demo.exe` → 运行输出 `add(2,3)=5 / greet=Hello, Impor / half=3.5`。示例在 `examples/c_abi/`。
- 本版边界（未满足直接报错，不猜测）：仅顶层 `export func`；参数/返回必须显式标注 `int/float/bool/str/void`；不支持默认参数、变参、泛型、类方法导出、`async` 导出。

### 修复：`impor fmt` 不再拆开返回类型箭头

- `_BIN_OPS` 补入 `->`，`def add(a : int) -> int` 不会再被格式化成 `- >`，格式化后的 C ABI 源码仍可直接 `build --lib`。
- `examples/c_abi/lib.impor` 改为 fmt-clean，`impor fmt --check` 通过。

### 变更：C 后端当时被定为唯一发布通道（该口径已在 3.13 废止）

- `impor build` 是当时的唯一发布出口（exe 或 DLL）；`impor run` / `repl` / `run-dbg` 只做开发期运行与调试。
- `impor compile`（Impor → Python）保留为开发兼容，不再承担发布职责；帮助文案已明确标注“不是发布路径”。
- `imporc` 只做内部一致性探针，不再是对外编译入口。
- C 后端遇到不支持特性时，不再引导用户“改用 Python 后端发布”，只提示开发期用 `impor run` 验证。

### 本版未交付（如实列出）

- MinGW `gcc demo.c -L. -lx` 未实测：本机没有 gcc/dlltool，无法生成/验证 `.dll.a` import library；已提供 `.def` 与 `dlltool` 命令说明，但不把未验证路径写成“已通过”。
- 默认参数、变参、泛型、类方法导出、`async` 导出、字符串所有权/释放 API 均未实现。

## [3.8.1] - 2026-11-16

### 修复：编译产物不再依赖“当前目录恰好是仓库”

- **路径自举**：转译产物头部改为按 `IMPOR_HOME` → 编译时安装根 → 产物目录/父目录 → 当前目录的顺序查找含 `impor` 的安装根，找到后注入 `sys.path`。产物复制到临时目录、干净副本或其他工程目录后仍能运行。
- **失败可诊断**：所有候选路径都找不到运行时，输出“找不到运行时 impor.runtime + 已尝试路径 + `IMPOR_HOME` 修复提示”，并以退出码 3 结束；不再裸抛 `ModuleNotFoundError`。
- **回归背景**：干净副本 `run_tests.py` 的 12 条失败全部落在 `test_*_transpile`，根因就是产物固定 `from impor.runtime import *`。本次修的是根因，不是测试特判。

### 新增：类型注解在编译入口真正检查

- `run` / `run-dbg` / `compile` / `build` 解析后调用 `impor/typecheck.py`，对确定不匹配的字面量赋值和函数返回类型报 `[FAIL] 类型 文件:行:列 …`，退出码 2。
- 边界：只做轻量静态检查，不引入泛型、不做控制流收窄；解释器运行时仍按动态值执行。注解不再“只解析不检查”，但也不是完整类型系统。

### 修复：`imporc` 读取 argv、`-o` 真正生效

- **C 运行时**：`c_runtime.c` 新增 `rt_args_init(argc, argv)`，生成 exe 的 `main` 注入 argc/argv；`ctrans.py` 的 `BUILTINS` 暴露 `args()`。
- **自举 CLI**：`bootstrap/imporc.impor` 优先读 `args()`，无参数才回退 `imporc_args.txt`；支持 `imporc <脚本> [-o 输出]` 与 `imporc run|build <脚本> [-o 输出]`。
- **不再静默**：`-o` 缺输出名、未知参数、`run/build` 缺脚本都打印明确错误并返回 2；成功时真正写到 `-o` 指定的路径，不再忽略参数自编译成 `imporc_self_raw.exe`。
- **可在外目录调用**：新增 C 运行时 `exedir()`，`imporc` 按自身可执行文件目录定位 `impor/c_runtime.c` 与 tcc；在临时目录调用 `imporc hi.impor -o custom.exe` 也能生成并运行产物，不再隐含要求当前目录是仓库。
- **中文目录可用**：Windows 下 `exedir()` 先取 `GetModuleFileNameW` 的宽字符模块路径，再转 UTF-8；`桌面\总Impor文件\Impor(I++) 3.8.1` 这类路径不会再被 ANSI 字节截断或误读，`imporc` 在干净副本里能正常调用 tcc。TCC 自带精简头缺少 `WideCharToMultiByte` 声明，已在运行库内补齐，编译不再出现隐式声明告警。
- **自举位置兼容**：`imporc` 位于 `<安装根>\impor\` 时向上取安装根；位于仓库根或其他目录（自举验证中的 B/C 代）时按当前目录解析运行时，A/B/C 逐字节一致闭环保持通过。
- **语言层**：`exit(code)` 可作为带退出码的内建调用（裸 `exit` 仍是结束脚本）；解释器与 C 后端都按给定码退出。

### 文档：写法规范 / 语言规范与 fmt 对齐

- `docs/写法规范.md` 重写：以 `impor fmt` 实际输出为准，`def`、`while true`、`var` 为推荐；`func`、`loop` 降为兼容；`set` 与裸赋值都保留但推荐 `set`；类型注解改为“编译入口检查”；中文关键字统一写“v5.2 起移除，仅保留中文标识符”。
- `docs/语言规范.md` 收敛开头版本线，删除“3.0.0 规范 + v4.3/v5.6/v6.2/7.3.0 堆叠”的历史叙事；第 17 节从“支持中文关键字”改为“已移除”；第 19.7 节更新类型检查与未实现能力边界。
- `README.md` 更新当前版本与 3.8.1 变更段；历史关键字条目改为“v2.18 历史记录”，不再让人误读为当前仍提供中文关键字。

### 本版未交付（如实列出）

- `yield` / 生成器、列表/字典推导式、默认参数/变参、`with`。
- C 后端 `try/catch`、线程/原子、`.dll` 导出。
- `impor lint --style` 的具体规则实现位置仍未系统整理；本版没有新增 lint 规则。

### 测试

- 新增 `tests/test_v381.py`：产物跨目录运行、类型门禁阻断、`imporc` 的 `version/help`、`-o` 成功路径、`-o` 缺值与未知参数退出码。
- 全量结果见发布记录。

## [3.8.0] - 2026-11-16

### 工作区 2.0：多根、项目树、信任与搜索范围

- **多根工作区**：`.imporproj` 的 `roots` 成为可编辑的真实根列表。资源管理器头部新增 `＋/－`，右键菜单新增「添加工作区根目录…」「移除该根目录」；主根受保护，避免切换工作区时丢配置。根列表写回 `.imporproj`，保留 `ignore`、`search_paths` 与 `format`。
- **项目树**：资源管理器按 `workspace_roots` 逐个挂根节点，常用目录懒加载；新增/移除根后自动重建 `ProjectIndex` 并刷新符号索引。
- **workspace trust**：新增全局 `trusted_workspaces` 列表、受限模式横幅与「信任此工作区」按钮。首次打开未信任工作区只限制执行类操作：终端/任务、运行、构建、项目插件加载被拦截；打开、浏览、搜索、编辑不受影响。信任状态写入 `~/.impor_studio.json`。
- **跨根搜索**：全项目搜索从单根扩展为多根，`search_project()` 接受根列表，按根做路径包含校验、重叠根去重；搜索 UI 直接传入 `workspace_roots`。
- **布局与会话恢复**：配置新增 `layout`，保存并恢复窗口 geometry、左右/上下 sash 位置、main/split 两组标签、活动标签、每个文件的光标行列与滚动位置；兼容旧的 `tabs`/`folds` 配置。

### 编辑内核 3.0：独立 Qt 预览

- **新增 `impor/kernel3.py`**：PyQt6 `QPlainTextEdit` 内核预览，独立窗口运行，不替换现有 Tk 面板。工具栏右侧与命令面板新增「内核3.0 预览」入口；当前文件未保存时打开空白预览，不伪造写回。
- **手感能力**：行号区、当前行高亮、Impor 关键字/字符串/注释/数字高亮、右侧 minimap、`Ctrl+Alt+↑/↓` 多光标、`Alt+Shift+↑/↓` 同列多光标、多光标输入/退格/删除/回车/Tab、`Esc` 清除多光标、滚轮平滑滚动、Qt 原生 IME。
- **大文件**：10.4MB / 10 万行文本在无头烟测中 约 2.3–2.8 秒打开并可编辑；超过 2,000,000 字符自动关闭 undo 以降低内存与卡顿。
- **边界**：这是第一阶段 PoC，不是 Monaco/WebView2 或 QScintilla 的完整迁移；没有 LSP、调试、Git、AI、插件、项目树、主题跟随等 Tk 侧能力。当前机器没有 QtWebEngine、QScintilla、pywebview，Monaco/WebView2 仍需额外依赖或更深 COM 集成。

### 启动默认内容

- **空白标签**：`open_new_tab()` 不再注入 `// 新脚本` / `print("你好，Impor!")` 示例模板；新建标签默认完全空白，显式传入内容时行为不变。

### 测试与未交付

- `tests/test_v380.py` 7 项通过：多根 `.imporproj`、跨根搜索、重叠根去重、越界路径过滤、Windows 信任键规范化、Qt 内核无头烟测、Qt 内核窗口渲染烟测。
- 工作区烟测 `_check_workspace2.py`：2 根加载、受限模式、跨根搜索 2 命中、信任写入、3 根持久化。
- 会话烟测 `_check_session2.py`：main/split 双组标签与活动标签恢复通过。
- 内核大文件烟测 `_check_kernel3_large.py`：10.4MB / 100002 行，最近一次 2.27 秒打开。
- 全量 `python run_tests.py`：**667 项全部通过**（888.298 秒，`EXIT=0`）；首次全量暴露的 `vscode-impor/package.json` UTF-8 BOM 已清除并复跑通过。
- **本版未交付**：内核 3.0 未替换 Tk 主线；Monaco/WebView2、QScintilla、列选择鼠标拖拽、滚动同步、LSP/调试/插件接入均未完成。语言侧 `with`、`yield`、默认参数/变参、解构遍历、半开区间、`guard` 仍未实现。

## [3.7.8] - 2026-11-16

### 语言：defer 延迟清理

- **语法落地**：`defer { ... }`、`defer:` 缩进块、`defer` 换行缩进块三种写法都可解析；`defer` 保持软关键字，`var defer = 1` / `defer()` 不受影响。
- **执行语义**：函数/模块进入时压入延迟帧，退出时按 LIFO 执行；正常返回、异常退出、`exit` 都执行清理。模块顶层 `defer` 在脚本结束时执行。
- **四端边界**：解释器与 Python 转译已支持；C 后端明确报“defer 延迟块（C 后端暂不支持）”，不静默跳过。`imporc` 仍按 v3.3 语法子集工作，不识别 `defer`。
- **静态检查**：`lint` 会进入 `defer` 体检查未定义/未使用变量，不新建作用域。

### 四端一致性：C 后端支持管道

- **`|>` 降级为调用**：C 后端不再拒绝管道，`x |> f(a)` 生成 `f(x, a)`，`x |> f` 生成 `f(x)`；链式管道递归降级。独立 exe 实测输出与解释器一致。

### 文档

- **新增路线图**：`docs/语言能力差距与路线图-3.7.8.md` 列出相对 C / Python 的硬缺口、四端不一致项与分批计划。
- **规范修正**：`docs/语言规范.md` 不再声称 C 后端支持 `try/catch/finally`；新增 `defer` 语义与边界说明。

### 测试

- `tests/test_v378.py` 11 项通过：defer 的 LIFO、异常清理、模块退出、软关键字、嵌套函数/类方法、快速执行路径、体内控制流拒绝、Python 转译一致性；C 后端管道转译/构建运行、defer 明确拒绝。

### 未交付（不计入本版完成项）

- `with`、`yield`、默认参数/变参、解构遍历、半开区间、`guard` 未实现。
- C 侧 `try/catch`、自定义异常、C ABI/库导出、线程/原子/channel、LLVM 与调试信息未实现。
- 编辑器工作区 2.0、AI 逐 hunk 接受/拒绝、LSP call hierarchy/code action 未实现。


## [3.7.7] - 2026-11-15

### 安全跨文件替换

- **未保存缓冲区可选纳入**：全项目搜索新增「包含未保存缓冲区（会一并保存）」复选框。勾选后，dirty 文件以编辑器缓冲区全文参与匹配与替换，不再从磁盘读取旧内容；未勾选时保持原有行为，dirty 文件不可选。
- **明确确认**：选中 dirty 文件时，替换前额外提示“会连同未保存修改一起保存，并重新加载编辑器”；仍先做预览和事务备份。
- **替换后重载**：按实际写入的路径重载普通标签与分块标签，分块标签调用 `reload_from_disk()` 重建行列表并清 dirty，避免窗口内旧文本残留。
- **路径归一化**：缓冲区键、dirty 集合与搜索结果统一使用绝对路径，避免大小写/相对路径造成匹配错位。

### 语言

- **递增/递减方向校验**：`i--` 配 `< / <=` 或 `i++` 配 `> / >=` 在解析期报错，提示会死循环。
- **三端验证**：Python 解释器与 C 后端均实测 `5 4 3 2 1` / `3 2 1`；`imporc.exe` 只更新版本标记，未改其独立解析子集。

### AI

- **输出 token 预算**：AI 设置新增「输出上限」（64–8192，默认 1200），非流式与流式请求体均写入 `max_tokens`；非法值回退 1200。

### 未交付（不计入本版完成项）

- AI 结果 hunk 逐块接受/拒绝、调试 watch/变量修改/attach/后台图形面板、LSP call hierarchy/code action/增量语义 token、`defer`/`with`/`yield`、workspace trust 未实现，留待后续版本。
- C 后端仍不支持 defer/异常清理结构；`defer` 未进入解析器，仍是保留字。

## [3.7.6] - 2026-11-15

### 大文件设置 UI

- **设置面板暴露阈值与模式**：编辑器设置新增「大文件阈值」（256–65536 KB，默认 2000）和「大文件打开」三选一（自动分块 / 始终完整打开 / 打开时询问）。改完立即作用于下一次打开，不需要手改配置文件。
- **持久化与同步**：`large_file_kb` / `large_file_mode` 继续写入 `~/.impor_studio.json`；设置同步拉取时一并刷新面板控件。
- **恢复默认**：恢复默认按钮把阈值还原为 2000 KB、模式还原为自动分块。

### 分块编辑：跨块查找 / 替换

- **全文查找**：`VirtualLargeFilePane.find_next()` / `find_all()` 改为搜索完整行列表，不再只搜 Tk Text 当前 3000 行；命中块外内容会自动跳块并选中，找不到时从头回绕。
- **跨块替换**：查找条新增「跨块替换」，对完整行列表做字面量、不区分大小写的全部替换，替换后刷新当前窗口并标记未保存；普通全文标签仍提示使用 `Ctrl+Shift+F` 全项目替换。
- **不丢未保存编辑**：替换前先 `flush_virtual(force=True)`，把当前窗口最后一次编辑写回完整行列表；含换行的替换文本会重建行列表并更新总行数。
- **当前窗口只标命中**：`find_all()` 统计全文命中数，但只给当前窗口内的命中套 `search` tag，避免给 26 万行一次性打 tag。

### AI 内联助手：流式与取消

- **SSE 流式输出**：新增 `request_suggestion_stream()`，请求体带 `"stream": true`；读取 `data:` 行、拼 `choices[0].delta.content`、遇到 `[DONE]` 或 EOF 结束；非 `text/event-stream` 响应自动回退整段 JSON。
- **可取消**：请求期间显示带「取消」按钮的流式结果窗；取消设置 `threading.Event`，worker 在读到下一段时抛 `AICancelled`，UI 提示「已取消」而不是失败。服务端长时间静默时，取消最迟在请求 timeout 生效。
- **fix / tests 仍先预览**：流式窗口只展示接收内容；修复与生成测试必须等完整结果再进 diff 预览，避免半截代码被应用。
- **设置开关**：AI 设置新增「流式输出」开关，服务端不支持 SSE 时可关闭，回到整段返回。默认开启。
- **错误加固**：同步与流式路径都能处理错误对象为字符串、`choices` 缺失、空 delta、非 JSON 的 SSE 行，不再因 `.get()` 链式调用崩成 AttributeError。

- **测试**：新增 `tests/test_v376.py`，覆盖跨块替换、跨块 find_next 回绕、SSE 拼接、非 SSE 回退、取消与字符串错误对象。全量 `run_tests.py` 结果见发布记录。

### 未验证 / 边界

- 没有真实 API key，未对真实供应商、代理/企业网关、真实超时做联网验收；SSE 行为只用本地假响应验证。
- 跨块替换按字面量处理，不支持正则、全词匹配或跨行 needle（替换文本可以含换行）。
- 10 MB 数据仍是单机冷启动，不代表所有机器的交互帧率。


## [3.7.5] - 2026-11-15

### 大文件编辑性能（P2）

- **默认分块编辑**：超过 `large_file_kb`（默认 2000 KB）的文件不再弹三选一对话框，直接打开 `VirtualLargeFilePane`；Tk Text 只装当前窗口（默认最多 3000 行，按平均行长自适应，单窗口字符量上限约 22 万），完整行列表留在 Python 内存，行号始终是整文件绝对行号。
- **可视区高亮**：虚拟窗口只对当前可视区 ±80 行套 tag；滚动只排一次 90–110ms 防抖重绘，不再每次滚动重跑 3000 行高亮。
- **后台增量解析**：符号解析放到 daemon 线程，按窗口起始行缓存；解析完成后回主线程套语义 tag，同一窗口只解析一次，滚动返回已解析窗口直接命中缓存。
- **保存与编辑不变**：窗口编辑仍按 450ms 防抖回写完整行列表，`Ctrl+S` 先写临时文件再原子替换整文件；保存时强制 flush 最后一次未派发的编辑。
- **切回完整编辑**：面包屑或命令面板「切换大文件为完整编辑」仍可主动转成全文 `EditorPane`，默认不再询问。
- **10 MB 实测**：本机 11,685,459 B / 262,146 行，读取 0.037s、首窗打开 0.146s、换块 0.004s、可视区高亮 0.006s、取全文 0.033s；Tk Text 内仅 3000 行（Python 3.11.5 + Tk，冷启动，单机数据，不作为所有机器的保证）。

### AI 内联助手（P2，可选）

- **默认关闭**：`ai.enabled` 与 `ai.privacy_ack` 默认均为 `false`；必须同时开启、确认隐私提示并配置 key，才会发出请求。
- **三种动作**：选中代码 → 解释 / 修复 / 生成测试；解释显示文本结果，修复与生成测试进入统一 diff 预览，点「应用修改」后才写入选区或新标签。
- **只发送选区**：请求体只包含当前选区（默认最多 12,000 字符，可截断并注明）、文件名 basename、语言名；不发送整文件、工作区路径或终端输出。API key 只用于 `Authorization` 头，不进入发给模型的内容。
- **兼容 OpenAI Chat Completions**：endpoint / model / key 可配置，key 也可用环境变量 `IMPOR_AI_API_KEY`（优先）；仅允许 `https://` 或本机 `http://127.0.0.1` / `localhost`。
- **隐私入口**：设置面板与新命令「AI 助手设置（默认关闭）」列出 endpoint、model、选区上限、key 存放位置与风险提示；首次发送前再次弹窗确认。
- **未就绪不发送**：关闭、未确认、缺 key、endpoint 非 https 时都在本地拦截，提示原因，不发起网络请求。
- **密钥不进同步**：设置同步（`config_sync.py`）推送的配置会清空 `ai.api_key`，密钥只留在本机 `~/.impor_studio.json`；仍建议用环境变量。

- **测试**：`tests/test_v375.py` 18 项通过；全量 `run_tests.py` 631 项 OK（542.250 秒，含本版新增 18 项）。
- **失败兜底**：AI 返回结构异常或内容为空时给出错误提示而不是崩溃；请求期间选区/大文件窗口变化后，旧的修复 diff 不会误写到新位置。


## [3.7.4] - 2026-11-15

### 编辑器：调试与语言能力

- **DAP 条件断点**：`impor/dap.py` 的 `setBreakpoints` 不再只取行号；支持 `condition`、`hitCondition`、`logMessage`，并把条件、命中阈值、日志点传给解释器。VS Code 与 Impor Studio 现在使用同一套断点语义。
- **异常断点**：DAP 新增 `exceptionBreakpointFilters`（未捕获异常 / 所有异常），抛出和未捕获时发送 `stopped(reason=exception)`；Impor Studio 原有的“异常暂停”开关继续保留，并与 DAP 使用同一套异常语义。
- **LSP 能力补齐**：新增 `semanticTokens`、`foldingRange`、`inlayHint`、`codeLens`、`documentHighlight`、`prepareRename` 六项；VS Code 扩展注册对应客户端能力，语义高亮与 Studio 同源。
- **统一分析引擎**：Studio 的轻量语义符号表改为优先复用 `impor/analysis.py` 的作用域符号表，解析失败时回退正则；编辑器、LSP、lint 的符号口径收敛到一处。
- **命令面板减负**：命令按“文件/编辑/导航/运行/调试/Git/项目/帮助”分组，支持最近使用、当前上下文推荐、模糊匹配和快捷键冲突提示；旧命令文本仍可搜到。
- **测试**：新增 `tests/test_v374.py`，覆盖 DAP 条件/命中/日志/异常断点、LSP 六项能力、分析引擎一致性和命令面板排序。

> 说明：本版不包含大文件编辑器内核重写与在线 AI 助手；`editor_pane.highlight` 已有可视区重绘与 2 MB 上限，继续观察真实大文件反馈后再决定是否值得换渲染层。

## [3.7.3] - 2026-11-15

### 性能：原生数值快路径

- 新增 `impor build --fast`（别名 `--fast-native`，`--no-fast` 显式关闭）：对纯整数数值脚本生成不装箱的 C 代码（变量/参数为 `long long`），由随包 tcc 加 `-O2` 编译成独立 exe；显式 opt-in，默认 `impor build` 仍是原完整 C 后端，避免 64 位整数与 Impor 任意精度整数语义冲突。
- 快路径覆盖整数变量/赋值/复合赋值、`while`、`if/elif/else`、顶层函数、`return`、`print`、`abs/min/max`、`+ - *`（`%` 用 Python 同号取模）、三元表达式；比较 `==/!=/</<=/>/>=`、`and/or/not` 只允许出现在条件中，不能存成整数或打印布尔值。浮点、字符串运算、容器、类、异常、嵌套函数、`for`、裸表达式等会抛 `FastUnsupported`，CLI 返回码 3 并提示去掉 `--fast`，不静默降级。
- 边界：变量与参数是 64 位有符号，超出 int64 按机器整数回绕，不做解释器的任意精度/溢出报错；`% 0` 运行时输出 `division by zero` 并以 1 退出。
- 构建缓存键区分 `fast` / `full`，避免快路径产物与完整 C 后端产物互相覆盖。
- 新增 `tests/test_v373.py` 9 项：解释器一致性、`min/max/abs`、条件逻辑、负数取模、拒绝浮点、拒绝字符串拼接、拒绝布尔打印、拒绝布尔存储、生成代码不含 `Val*`/`rt_`。
- 新增 `benchmarks/bench_native20.py`：同一份源码，CPython 直接运行，Impor 先 `build --fast` 再跑独立 exe；构建不计时，取多次墙钟中位数。门禁三项实测：
  - `arith_50m`：11359.4 ms → 154.9 ms，**73.33x**
  - `nested_10k`：14255.8 ms → 242.8 ms，**58.71x**
  - `builtin_20m`：11928.6 ms → 132.7 ms，**89.87x**
  - 门禁 **PASS**，完整数据见 `benchmarks/native20.md`。
- **适用边界（不夸大）**：20x 只在上述“纯整数计算循环”成立。取模循环 `mod_20m_diag` 为 8.90x，递归 `fib_32_diag` 为 7.87x，均作为诊断项保留且明确不计入门禁；不能据此宣称所有 Impor 脚本都比 CPython 快 20 倍。

## [3.7.2] - 2026-11-15

### 关键字收敛

- `defer` 等无语法产生式的空保留字降级；新增自动审计测试：`KEYWORDS` 中每个词都必须在 parser 源码中有实际出现。
- `static`、`readonly`、`async`、`await`、`this`、`defer` 改为软关键字：保留原有语法语义，同时可作普通标识符。
- 同义写法收敛：`func`→`def`、`elseif`→`elif`、`switch`→`match`、`loop`→`while`、`let`→`var`；`impor fmt` 只输出主写法，LSP 补全只推主写法。
- 新增 `tests/test_v372.py`，覆盖空保留字审计、17 个可作标识符的词、deprecated 不进补全、fmt 幂等归一。

## [3.7.1] - 2026-11-15

### 版本真源与门禁修复

- `tests/test_version_source.py` 不再依赖未随包分发的 `_release3617.py`，干净目录也能通过。
- README 版本行与语法规范行、`CHANGELOG` 版本口径、`benchmarks/latest.md`、`bootstrap/imporc.impor` 自报版本统一到 3.7.1。
- `imporc` 接受 `version` 与 `--version`；干净目录不再在 `fileread` 处崩掉，并补充 `help/--help` 用法提示。

### 保留字解封

- `record/template/final/of/do/yield/let/each/with/lambda/macro` 不再作为硬保留字封锁；`var` 后可直接用作标识符。`do {}`、`lambda`、`record` 等已有语法保留兼容。

### 转译路径静默失败修复

- `dict.has(k)` / `dict.contains(k)` 改走 `_rt.dict_has`，不再运行期 `AttributeError`。
- `filelist`、`regex_match` 补进运行时导出，编译产物不再“编译通过、运行期找不到名字”。

### 递归上限统一

- 快速后端与树遍历统一为 3000 层；`sys.setrecursionlimit` 提到 32000。脚本里有没有 `class` 不再改变递归上限。

## [3.6.17] - 2026-11-15

### 真实使用后的四个小修

- **专注中不知道几点结束**：计时卡片右侧新增“结束 HH:MM”，开始/继续时显示真实结束时刻；暂停后改为“还剩 mm:ss”。25 分钟不再需要在脑子里做加法。
- **完成时窗口不回来**：专注或休息结束时，窗口会从最小化恢复、提升到前面并临时置顶 5 秒，同时保留 `bell()`。没有加托盘、系统通知或云同步；只在完成这一下抢一次注意力，之后恢复用户原来的置顶设置。
- **关窗会丢掉当前一轮**：运行中或暂停中关窗，会把任务名、剩余秒数、专注/休息设置写入 `%USERPROFILE%\.impor_focus\state.json`。下次打开显示“上次未完成 · 任务 · 还剩 24:52”，按钮变成“继续”，按开始就从剩余时间接着跑。完成、重置、跳过会清掉这份状态。
- **第二天打开只看得到今天**：新增“昨天 X 分钟 · Y 段 · 最后一轮 任务 N 分钟”。如果今天还没有记录，历史列表直接列出昨天最多 3 条，前缀“昨天”；今天的记录前缀“今天”，不再把两天的记录混在一起。
- 任务名在开始那一刻锁定到 `session_task`，完成记录用开始时的任务名，不用于结束时输入框里的新文字。
- 仍然**没有**加托盘、系统通知、长休息、云同步；也**没有**给 C 后端加 Tk 绑定。

### 测试

- 新增 `tests/test_v3617.py`：8 tests / OK / 0.479s。
- 实际 25 分钟 dogfood：15:58:18 启动，16:23:18 完成；真实 `clock()` 计时，完成后写入 1 条 25 分钟记录。
- 关窗测试：运行 8 秒后关闭，`state.json` 保存 `remaining=1492`，`history` 为空；重新打开显示“上次未完成 · 关窗测试 · 还剩 24:52”，按钮为“继续”。
- 第二天测试：只放昨天两条记录，打开后显示“昨天 55 分钟 · 2 段 · 最后一轮 英语 30 分钟”，历史列表两条“昨天”。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：`GUI SMOKE OK`。
- 全量 suite：583 tests / OK / 666.647s。

### 打包

- `apps/impor_focus/Impor专注计时器.exe`：11,456,903 B，SHA256 `67A0135F22E2ACC7B2F6F436D9158A8A4DD2382B5894D9D120B4B2A7A8055F2A`。
- 重新自举 `impor/imporc.exe` / `bootstrap/imporc_py.exe`：588,288 B，SHA256 `752F6BA233F09644C1CEAEAB1CFDD40E8F83B1EBAB439B0389AA1AF889C20B24`；`imporc version` 输出 `v3.6.17`。
- 新增 `docs/Impor 3.6.17 发布说明.md`；README、CHANGELOG、VERSION、CLI/VSIX manifest 同步到 3.6.17。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.17`。

## [3.6.16] - 2026-11-15

### 两个真 bug 的架构修复

- **Tk 字体族带空格不再被插值吃掉**：`"{Segoe UI} 9"` 以前会把 `Segoe` 当变量、把 `UI` 留在原地，报 `变量 Segoe 未定义`。现在 `{…}` 必须能解析成完整 Impor 表达式才算插值；解析失败就整段按字面文本处理，花括号原样交给 Tcl。`\{Segoe UI\} 9` 旧写法继续可用，`"{size}"` 仍是插值。转译器同时把文本段花括号翻倍，保证生成的 Python f-string 合法。
- **内建注册只有一处**：新增 `impor/builtin_manifest.py`，把 94 个内建分为 `RUNTIME_BUILTINS`（58，编译产物必须提供）、`HOST_BUILTINS`（9，Python 宿主提供）、`INTERPRETER_ONLY_BUILTINS`（27，依赖解释器状态/FFI）。`impor.runtime` 导入时自检缺一即 `ImportError`；`make_builtins()` 返回前与 `ALL_BUILTINS` 对账；编辑器的 `BUILTIN_NAMES` 也从该表派生。以后新增内建忘记登记或忘记运行时实现，会在测试/打包阶段失败，不会拖到用户双击 exe。
- `apps/impor_focus` 的 UI 字体从 `Arial` 全部改为 `{Segoe UI}`，真实 dogfood 新语法；版本字串升到 3.6.16，重新编译并重打 exe。
- 本工具定位明确分裂为两类：控制台 C 工具约 108 KB，Tk GUI 走 Python + PyInstaller 约 11 MB。本版只记录这个 100 倍差距，**不给 C 后端加 Tk 绑定**。
- `global set` 逐条写、`impor compile --exe` 不自动加 `--noconsole`、DPI 缩放仍是使用摩擦，写进 `apps/impor_focus/README.md`，不改语言。

### 测试

- 新增 `tests/test_v3616.py`：5 条无窗口用例，覆盖字面花括号 + 混用插值（解释器与转译产物）、旧 `\{...\}` 转义、内建清单与解释器/runtime 对账。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：`GUI SMOKE OK`；打包 exe 的 `--smoke-gui` 退出码 0，实际启动窗口标题 `Impor 专注计时器 · 3.6.16`。
- 全量 suite：**575 tests / OK / 618.237s**。

### 打包

- `apps/impor_focus/Impor专注计时器.exe`：11,451,460 B，SHA256 `3953F7A2F30221645452A8E7779E035001D9AB88AE6F82CC8B0B67563DA770F6`。
- 重建 `impor/imporc.exe` / `bootstrap/imporc_py.exe`：588,288 B，SHA256 `4C73FA7C66C04E62887366AF9EB84A7ED279D8AAAC2779F8E839CC9C391FDFD4`；`imporc version` 输出 `v3.6.16`，全量测试里旧二进制版本不一致的 1 条失败已消除。
- 新增 `docs/Impor 3.6.16 发布说明.md`；README、CHANGELOG、VERSION、CLI/VSIX manifest 同步到 3.6.16。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.16`。

## [3.6.15] - 2026-11-15

### 第一个真实用户工具

- 新增 `apps/impor_focus/focus.impor`：一个纯 Impor 写的桌面专注计时器，25/5 分钟专注与休息、开始/暂停/重置/跳过、任务名、置顶、今日统计、历史记录、空格/Esc 快捷键、关闭自动保存。
- 记录持久化到 `%USERPROFILE%\.impor_focus\history.json`，JSON 编解码与文件读写都走 Impor 运行时。
- 新增 `apps/impor_focus/README.md`，记录第一条真实使用路径，以及写这个工具时实际卡住的 6 个地方。
- 用 PyInstaller 打成单文件、无控制台的 `Impor专注计时器.exe`；Tk 单文件约 11–12 MB，双击即可打开窗口。
- 修复打包后 `NameError: name 'fileexists' is not defined`：把 `fileexists` / `filemkdir` / `fileread` / `filewrite` / `json_encode` / `json_decode` 以同名同义补进 `impor.runtime`，让生成代码脱离解释器内建表后仍可用。这是打包必需修复，不新增语法。
- 本版**不新增任何语言语法、关键字或编辑器功能**；3.6.15 只交付工具本身、必要版本同步和发布包。

### 测试

- 新增 `tests/test_v3615.py`：无窗口验证时间格式化、今日统计、JSON 往返与 `--selftest` 退出码；并新增“转译后的 Python 也能跑 `--selftest`”，防止 PyInstaller 环境下运行时内建缺失回归。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：实际走一遍“开始 → 暂停”后 `GUI SMOKE OK`。
- 打包 exe 实测：双击打开窗口，空格开始后倒计时从 `25:00` 走到 `24:57`，Alt+F4 关闭后生成 `%USERPROFILE%\.impor_focus\history.json`。
- 全量 suite：**570 tests / OK / 547.144s**（3.6.14 为 567 条，本版新增 3 条）。

### 文档与打包

- 新增 `docs/Impor 3.6.15 发布说明.md`；README 更新到 3.6.15。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.15`。

## [3.6.14] - 2026-11-15

### 自举解锁

- 旧 `impor/imporc.exe`（2026-10-31 生成，385,536 B）面对 86KB 的 `bootstrap/imporc.impor` 超过 900 秒不返回，`test_selfhost_closure` 在 Step 1 以 300 秒超时失败。根因不是断言，而是旧二进制与新种子的解析/性能不兼容。
- 新增 `bootstrap/build_with_python.py`：用 Python C 后端从当前种子生成新的 A（`bootstrap/imporc_py.exe`，588,288 B，0.52s），并自动把 PE 栈补到 8MB。该脚本把种子字符串字面量内的 `{}` 临时转义为 `\{`/`\}`，避开 Python 前端插值；种子源码中 `local` 变量改名为 `localname`，避开 Python 保留字。
- 修复 `rt_strcut` 的 O(n²)：新增同一字符串“字符下标 → 字节偏移”顺序缓存，`strcut(gsrc, i, i+1)` 不再每次从头扫描全文。A 编译完整 86KB 种子从 600 秒以上降到 **52.56 秒**。
- `rt_add` 的字符串拼接不再对两个操作数各做一次 `rt_tostr()` 全量复制，只分配并复制一次结果。
- 用 A 重新生成 `impor/imporc.exe`（v3.6.14，417,280 B），跑通 `bootstrap/selfhost_check.py`：
  - A 编译种子 → B；
  - B 编译种子 → C；
  - C 编译种子 → D；
  - B/C/D 输出的 C 代码逐字节一致（234,686 B，112 个函数）。
- 自举闭环不再依赖 `bootstrap/imporc_seed_v214.exe` 旧链，新种子可由新二进制稳定编译。

### 测试

- `test_v38.TestImporcSelfHost.test_selfhost_closure` 从 `run_tests.py` 的 `LEGACY_IDS` 移出，默认 suite 实际执行该用例；`LEGACY_IDS` 6 → 5。
- 默认 suite：**567 tests / OK / 408.230s**（含自举闭环）；
- 桌面中文/空格/括号路径：**567 tests / OK / 364.178s**。
- 版本一致性：`VERSION`、`Impor.py`、`impor/__init__.py`、`vscode-impor/package.json`、`imporc.impor` 全部为 3.6.14。

### 文档与打包

- 新增 `docs/Impor 3.6.14 发布说明.md`；`docs/测试基线分类-3.6.7.md` 补记自举项移出 LEGACY。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.14`。

## [3.6.13] - 2026-11-15

### 路径健壮性

- 修复 `imporc` 调 tcc 时输出路径未加引号：`Impor(I++) 3.6.13` 这类含空格/括号/中文的目录不再被拆成 `3.6.13\tests\...`。
- `imporc` 的 `run` 模式执行产物路径同样加引号。
- 测试写 `imporc_args.txt` 时统一用双引号包裹脚本/输出路径；`cmd /c` 调用 `imporc.cmd` 改为整条命令双引号包裹，避免空格与括号截断。
- C 运行时 Windows 文件 API 改为 UTF-8 → UTF-16 后走 `_wfopen`；`system` 改用 `CreateProcessW`。中文目录下 `fileread` / `filewrite` / tcc 子进程不再被 ANSI 代码页截断。
- 桌面路径 `C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.13\` 下默认 suite 实测 `Ran 566 tests in 123.649s / OK`；仓库路径 `140.922s / OK`。

### 文档

- `docs/三端能力矩阵-3.6.6.md` 补记 3.6.11 决策：管道 `|>` 与字符串插值保留；并标注三端实际支持边界与 3.6.13 路径验收。
- 新增 `docs/Impor 3.6.13 发布说明.md`；`docs/测试基线分类-3.6.7.md` 补记 3 条默认红项的根因与修复。

## [3.6.12] - 2026-11-15

### 测试

- REPL / use / style lint 共 8 条旧用例切到当前语法与当前文案：`use std.text`、`def` / `var` / `if` 多行块、`help(print)` 当前说明、`style-semicolon` 等当前规则。
- `LEGACY_IDS` 从 14 条降到 6 条；默认 suite 实际执行新增的 8 条。
- 剩余 6 条：`try/catch`、保留字、自举闭包、`print` 软别名、`then`、诊断一致性。

## [3.6.11] - 2026-11-15

### 测试

- 修 LSP 补全空路径崩溃，rename 对当前文档保留原 URI。
- 启动器白名单补 `release-check` / `sys-check` / `syntax` 三项。
- **决策：管道 `|>` 与字符串插值保留。** 两者在解释器与转译器已跑通；7 条红项只是测试仍用旧中文关键字，已切到 `def` / `var` / 显式 `return`。
- `LEGACY_IDS` 从 24 条降到 14 条；默认 suite 实际执行新增的 10 条。
- 自举闭包 `test_selfhost_closure` 单独排期，不在本版混入。

## [3.6.10] - 2026-11-15

### 测试

- 处理 `test_v29` 最老的 10 条旧语法红项：9 条按当前 `def` / `var` / `return` 语法重写，1 条裸表达式用例删除。
- `LEGACY_IDS` 从 34 条降到 24 条；默认 `run_tests.py` 实际执行这 10 条，不再跳过。
- 剩余 24 条：`test_v29` 7 条、LSP 2 条、启动器 1 条、REPL/保留字/自举/诊断/样式 lint 等 14 条。

## [3.6.9] - 2026-11-15

### 编辑器延迟

- 按键热路径不再逐键重画标签栏；未保存圆点只在首次变脏时刷新。
- 高亮改为 180ms 防抖、400 行以上只重绘可视区 ±60 行；滚动重新高亮。
- 状态栏 50ms、目录树 500ms 防抖；实测 `_on_key + _on_modified` 平均 **0.399ms/键**。

## [3.6.8] - 2026-11-15

### 编辑器手感

- 停用输入触发的自动补全：打 `p` 不再自动弹 `print`；需要补全时按 `Ctrl+Space` 手动调出。
- 保留签名提示与括号配对，不影响现有补全数据。

## [3.6.7] - 2026-11-15

### 结构收敛

- 真 Git tag：仓库已 `git init`，首个 tag `v3.6.6`；本版提交打 `v3.6.7`。
- 编辑器再拆：`EditorPane`、对话框、编辑器组/标签栏、底部面板/文件树、运行/调试/测试 UI 分别拆到 `editor_pane.py`、`editor_dialogs.py`、`editor_groups.py`、`editor_panels.py`、`editor_runtime.py`；`editor.py` 从 9,622 行降到 5,618 行（低于 6000 行目标）。
- 测试分类：34 条 3.6.6 遗留红项按“旧语法兼容 / LSP / 自举闭包 / 启动器”分类，默认 `run_tests.py` 跳过并输出分类数字，`--all` 仍可跑全量；详见 `docs/测试基线分类-3.6.7.md`。
- 版本同步：`VERSION`、`Impor.py`、`impor/__init__.py`、`pyproject.toml`、VSIX 全部对齐到 `3.6.7`。

## [3.6.6] - 2026-11-15

### 结构收敛

- 版本对齐：新增 `VERSION`，`Impor.py version` 直接打印发布线说明；`docs/路线图-v6.0.md`、性能报告标注为历史内部文档。
- 三端契约：新增 `docs/三端能力矩阵-3.6.6.md`；JSON / 正则 / lambda 已实测 C 后端可用，文件 API / FFI 在 C 后端构建前明确拒绝，自举端明确拒绝子集外语法。
- 编辑器拆分：`editor.py` 的 Tk 自绘控件层与主题色板拆到 `impor/editor_widgets.py`、`impor/editor_theme.py`，主文件从 9,590 行降到 9,058 行。
- 测试基线：全量 `run_tests.py` 重新执行并留档 `_test_baseline_366.log`；已知失败/错误按真实数量记录，不再用“关键字计数不变”冒充全绿。

### 新增

- 多编辑器组：`Ctrl+\` 创建独立第二组，标签栏按组过滤，资源管理器可“在右侧编辑器组打开”，组内可独立开关标签。
- 行内诊断：错误/警告直接标在文本行（整行浅色背景 + 波浪下划线），不再只看底部问题面板。
- F8 / Shift+F8 问题导航：在当前文件的问题之间循环跳转，命令面板同步提供入口。
- Git 变更审查台：左侧变更文件树、右侧逐文件 diff、按文件暂存/取消暂存、暂存全部、提交框、打开文件。
- 可配置键位：`.impor/keybindings.json`（`[{"key":"ctrl+alt+g","command":"global_search"}]`），保存后即时重载，非法键位在面板提示。
- 测试树新增耗时与覆盖列；双击用例按测试输出里的 `file.impor:行` 精确定位。
- 命令面板新增“下一个问题 / 上一个问题 / 键位设置 / 当前文件在右侧编辑器组打开”。

### 说明

- 第二编辑器组是独立的组，不再克隆当前文件；在资源管理器打开新文件会进入当前活动组。
- 覆盖率列读取最近一次运行的行覆盖快照；需要先在“覆盖”入口开启行覆盖率运行，未运行覆盖时该列为空。

## [3.6.5] - 2026-11-14

### 新增

- 多根工作区资源管理器：项目配置里的 `roots` 会显示为多个顶层节点，不再固定成单个 `ImporLang` 根。
- 工作区符号索引热更新：后台按 mtime 轮询 `.impor`，外部修改或 `git checkout` 后自动增量刷新。
- 状态栏新增索引状态：`索引中…` / `索引 N 符号` / `索引已更新` / `索引失败`。
- 项目质量检查问题双击跳转改为 `(文件路径, 行, 列)`，先打开对应文件再定位。

### 修复

- 项目检查里解析失败的文件未登记跳转标记，导致后续问题整体错位一行。

## [3.6.4] - 2026-11-14

### 新增

- 自动配对 `()`、`[]`、`{}`、`""`、`''`；选中文本时直接包裹，键入右符号自动跳过。
- 智能退格：空配对上一次删除两边，不跨行误删。
- 多行注释 `Ctrl+/`：选中多行一次注释/取消，保留缩进。
- 整块缩进 / 反缩进：多行选区按设置宽度整体调整。
- `Alt+↑` / `Alt+↓` 上下移动当前行或选区；`Ctrl+Shift+K` 删除当前行或选区。
- `Ctrl+Backspace` 删除光标前一个词，中文按连续字处理。
- 智能 `Home`：在行首与第一个非空格之间切换。
- `elif`、`except`、`case`、`default` 等行尾输入回车时自动加深缩进。
- 设置页新增“自动配对括号与引号”开关，配置随项目保存。

## [3.6.3] - 2026-11-14

### 新增

- 语法查询：`impor syntax`、`impor syntax print`、`--search`、`--json`。
- 覆盖 73 个关键字与常用内建函数，每条含意思 / 格式 / 例子 / 例子说明。
- 编辑器 F1 与命令面板“语法查询”面板。
- REPL `help(名字)` 优先显示完整语法条目。

## [3.6.2] - 2026-11-14

> 系统编程门禁 + 受控指针 FFI：只增加可验收的审计、CI 退出码和解释器侧受控缓冲区，不声称系统编程能力已完成。

### Added
- `impor sys-check`：逐项报告 C 后端、自举编译器、FFI 基础、指针/结构体、C ABI、自动内存、线程/原子、syscall、freestanding、LLVM 后端状态。
- `impor sys-check --strict`：`systems_ready` 为 `NO` 时返回退出码 2，可直接接入 CI。
- `impor sys-check --json`：输出 `systems_ready` 与逐项状态报告。
- `docs/系统编程/README.md`：系统编程实施顺序与验收条件。
- 受控原生缓冲区 FFI：`ffi_alloc`、`ffi_alloc_str`、`ffi_buffer_size`、`ffi_read_i64`、`ffi_write_i64`、`ffi_read_f64`、`ffi_write_f64`、`ffi_read_str`、`ffi_write_str`、`ffi_buffers`。
- `ffi_call` 增加 `argtypes` 与 `ptr/cstr/void` 返回类型；`ptr` 只接受运行时分配的缓冲区 token，不接受任意整数地址。

### Changed
- 版本统一升至 3.6.2。

### Known limits
- 受控指针 FFI 只在解释器侧实现；C 后端与自举 `imporc` 尚未同步。
- 结构体布局、回调、稳定 C ABI、线程/原子、syscall、freestanding、LLVM 后端仍未实现。
- `sys-check --strict` 当前会失败，这是预期行为。

## [3.6.1] - 2026-11-14

> 系统编程内存策略定稿：采用自动管理，不暴露手动 `free`。本版仍不包含线程/原子、稳定 C ABI、LLVM/Cranelift 原生后端。

### Changed
- 阶段一内存模型由“默认 GC + 可选手动/区域分配”改为“运行时自动管理”：语言层不提供 `malloc/free`，用户不需要手写释放；内部 arena 仅限库实现，不作为公开 API。
- `docs/生产化/01-原生后端.md` 与 `README.md` 同步更新。
- 版本统一升至 3.6.1。

### Known limits
- 自动管理不等于系统编程完成：仍缺线程、原子、锁、稳定 C ABI、freestanding 运行时与原生后端。
- 自举闭环测试 `test_selfhost_closure` 仍未通过。

## [3.6.0] - 2026-11-14

> 生产化基线：把“能跑”变成“可验收”。本版不包含 LLVM/Cranelift 原生后端、中央包注册表与多线程运行时；这些明确列为未完成项。

### Added
- `impor release-check`：发布门禁。快速模式校验版本一致性、生产化必备文件与 conformance 用例数量；`--full` 追加 `doctor`、`conformance --ci` 与单元测试；支持 `--json` 报告。
- `docs/生产化/`：三阶段路线、验收标准与当前状态，明确区分“基线完成”和“能力完成”。
- `SECURITY.md`：报告渠道、响应目标、安全边界与已知限制。
- `CONTRIBUTING.md`：本地验证、三端一致性要求、版本与发布约定。
- `CODEOWNERS`：核心、原生后端、工具链、编辑器、VS Code、质量与安全的责任划分。

### Changed
- 版本统一升至 3.6.0：`Impor.py`、`impor/__init__.py`、`pyproject.toml`、`vscode-impor/package.json`、`bootstrap/imporc.impor`、`site/update.json`。

### Known limits
- 原生后端仍走 TinyCC，无优化等级、稳定 C ABI、调试信息与交叉编译。
- 内存模型未冻结；热路径分配规则未定义。
- 缺 HTTP/TLS/SQL/连接池/指标/健康检查标准库。
- 包管理器仍以本地/git 源为主，无中央注册表、签名、审计与撤回。
- 无 LTS、SBOM、可复现构建与正式 CVE 流程。

## [3.2.3] - 2026-11-14

> 编辑器五项升级：符号级语义高亮、异步防抖诊断、覆盖率行号栏内联、Git 三方合并编辑器、插件管理器。语言与安全策略沿用 3.2.2。

### Added
- 符号级语义高亮：`semantic_symbol_map` 先建符号表，再按参数 / 局部变量 / 函数 / 类结构异常 / 模块着色；新增 `sem_var`、`sem_param`、`sem_field`、`sem_module` 标签与三套主题色，`obj.field` 推断字段。
- 异步防抖诊断：300ms 防抖 + 后台线程解析 + 代次丢弃过期结果；`queue.Queue` 回主线程，`_apply_lint` 按 pane 保存标记，切换标签不串文件；保存/显式检查仍走同步 `_lint_pane`。
- 覆盖率行号栏内联：行号栏新增绿色命中 / 红色未命中竖条，错误红下划线、警告黄下划线；payload 带 header offset，Treeview 与行号栏对齐；新增“跳下一个未覆盖”。
- Git 三方合并编辑器：`split_conflict_markers` 支持 merge / diff3；`git show :1/:2/:3` + `git merge-file --diff3` 生成三栏；逐块采用 OURS / THEIRS / BASE / 双方；保存前语法校验、原子写回、可选 `git add`；命令面板新增入口。
- 插件管理器：本地插件发现、文件夹/ZIP 安装、启停状态持久化、meta 版本/权限、卸载与 reload；980×620 管理器窗口，Treeview 列表 + 详情 + 双击切换；安装/卸载后自动加载。
- `tests/test_v323.py`：10 项单元测试覆盖语义映射、诊断纯计算、覆盖率行、冲突解析、插件安装/启停/卸载/zip 防穿越。

### Fixed
- 覆盖率 header 偏移：可执行行与命中行统一加 offset，修复首行 header 导致的行号错位。
- 诊断面板串文件：每个 pane 保存自己的 `_problem_marks` / `_lint_payload`，只有当前 pane 刷新面板。
- 插件包卸载只删 `plugin.py` 留下空目录；现在删除插件包父目录。
- 三方合并保存后未重置 `edit_modified`，重新编辑可能误标脏；保存后已重置。

## [3.2.2] - 2026-11-13

> 编辑器五项闭环：运行时行覆盖率、插件面板跨进程 builder、索引与搜索后台化、UIA 逐控件 provider、诊断 Quick Fix 扩面。语言与安全策略沿用 3.2.1。

### Added
- 运行时行覆盖率：解释器 `enable_coverage` / `coverage_snapshot` 按 `lineno` 累加；编辑器 `run_code(coverage=True)` 贯通异步与同步执行；覆盖率视图以 AST 语句行为分母，列出命中次数、已执行/未执行与源码，双击跳转，支持重跑与清空。
- 插件面板跨进程渲染：子进程 `_UIBuilder` 把 Frame / LabelFrame / Label / Button / Entry / Text / Checkbutton / Separator / Treeview 与 pack/grid/place 序列化成 UI 描述；父进程 `_isolated_panel` 渲染并保留进程状态、权限与重启入口；按钮经 `panel_action` 回调子进程；`tkinter` shim 兼容旧插件写法。
- 索引后台化：`refresh_index_async` 用代次合并请求，启动与切工作区不再阻塞主线程；`ProjectIndex` 增加可重入锁，刷新与查询并发安全。
- 搜索后台化与分页/取消：`search_project(..., should_cancel=...)` 逐文件检查取消；搜索窗口新增“取消”“加载更多”，已扫结果保留，结果按页续接。
- UIA 逐控件 provider：`ControlProvider` 暴露 id / name / role / group / accelerator / state / bounds / invoke；`build_control_providers` 覆盖 16 个主入口，控件树按组展示角色、状态与快捷键，`Control+Tab` 循环焦点，Enter 执行；`refresh()` 导出 `control_providers.json`。
- 诊断 Quick Fix 扩面：新增缺少 return、match 不穷尽、重复定义、类型不匹配、await/async、ADT 载荷数量、FFI 签名等规则；Quick Fix 覆盖关键字拼写、未声明赋值补 `var`、条件 `=` 改 `==`、括号/引号/大括号补齐、未使用变量删除；诊断解释窗口改为“原文 / 为什么 / 怎么修 / 最小示例”。

### Changed
- `quick_fix_smart` / `quick_fix_all` 前后加 `edit_separator()`，并把修复提示改为可按 `Ctrl+Z` 逐个撤销。
- 插件父进程请求改用负数 id，避免与子进程代理请求的正数 id 冲突；`register_panel` 兼容一参/两参 builder。

### Fixed
- 取消搜索后 token 自增导致 `_search_done` 被丢弃、`_busy` 永久为真；现在由 worker 以同一 token 收尾。
- `_UIBuilder` 后代节点原先全部平铺到根节点，现按 `_parent` 维护层级。
- `register_panel` 只传 `root` 导致两参 builder 报错。
- Windows Tk 不识别 `<Control-ISO_Left_Tab>` 导致 UIA 初始化抛出 `TclError`。



> “全部”升级：语言六项 + 编辑器六项 + 应用层最高安全策略。核心原则是“解释器能跑、编译产物也能跑，跑不了的地方明确报边界”。

### Added
- 带载荷 ADT：`enum Result { Ok(value), Err(message) }`，构造 `Result.Ok(41)`，`match` 按 `case Result.Ok(v)` 绑定字段；解释器与 C 后端输出一致，穷尽性检查按 tag 覆盖。
- `async func` / `await` / `run_tasks` / `task_status`：单线程任务模型；解释器与 C 后端（同步语义）状态与结果一致。
- 三端补齐：C 后端与解释器同时支持 JSON（`json_encode` / `json_decode`，含转义、`\uXXXX`、整数/小数/指数）、基础正则（`regex_match` / `regex_find` / `regex_replace`）、`lambda(x): ...`；`_t_parity.impor` 9 行逐行一致。
- 跨文件访问控制：模块顶层 `visibility private/protected` 收集到模块符号表，跨文件 `AttrGet` / `from ... import` 拒绝并抛出 `AccessError [E1099]`。
- FFI：`ffi_open` / `ffi_call`，默认拒绝一切原生库，白名单来自 `IMPOR_FFI_ALLOW` 或 `.impor/ffi_allow.json`；仅 int/float/str/bool、最多 8 参，不做指针/结构体。已用 `kernel32.GetTickCount` 实测。
- 诊断升级：`AccessError / private 符号` 规则；问题面板右键「解释诊断」给出“为什么 / 怎么修”，可直接跳到 Quick Fix。
- 编辑器：安全重构统一 diff 预览 + 一键撤销（重命名 / 提取变量 / 提取函数 / 移动文件含引用改写）；测试失败内联到编辑器与行号栏、只重跑失败、函数覆盖率视图、断点持久化；大文件分块可编辑并原子回写；插件崩溃自动重启（最多 3 次）、权限清单、隔离进程面板宿主；UIA 可聚焦控件树 + 高对比主题按钮；搜索结果 200 条增量虚拟列表。
- 安全中心：「威胁模型 / 审计日志」面板；启动进程加固（清 `PYTHONINSPECT` / `PYTHONSTARTUP` / `PYTHONPATH`）；保存、插件写入写 JSONL 审计。

### Changed
- C 后端 `run_tasks` / `task_status` 接入运行时；`task_status` 恒 `done`（C 端 async 为同步求值）。
- 大文件保存后跳过全量 AST/lint，避免 2 MB+ 文件保存卡死 UI。
- `bootstrap/imporc.impor` 版本号改为 3.2.1，并明确标注“子集模式”（尚不支持 ADT/async/lambda/JSON/正则）。
- 自举编译器仍是 C 后端子集，是三端一致性唯一未闭环项，其余差异已收敛到 JSON/正则/lambda。

### Security
- 新增 `impor/security.py`：路径围栏（realpath + commonpath）、原子写、SHA256 清单校验、JSONL 审计、进程加固、威胁模型与残余风险。
- 插件子进程接入 Python `audit hook`：未声明 `write` 直接写文件、未声明 `net` 的 socket、未声明 `process` 的 `subprocess` / `os.system` 均抛 `PermissionError`；读范围限工作区、插件目录与 Python 运行时，越界 `open` / `listdir` 被阻断。
- 插件默认只能读取工作区与运行时文件；写文件走 `safe_write` + 审计，并强制落在工作区；FFI 未登记库默认拒绝。
- 诚实边界：这是应用层安全模型，不是 OS 沙箱；未启用 AppContainer/Job Object，更新通道未做签名验证；audit hook 只覆盖 Python 级事件，ctypes/原生扩展可绕过；同用户权限下的恶意代码仍可绕过。

## [3.1.0] - 2026-11-13

> 版本号压缩：7.3.0 重编号为 3.1.0；本日完成编辑器五项升级，功能与发布物同步到 3.1.0。

### Added
- 可选类型标注：变量、参数、返回类型；`int/float` 数值互通、`T?` 可空、`any/?` 放行；`impor check` 新增「类型」阶段。
- 枚举穷尽性：无 `else`/`_`/绑定通配的 `match` 必须覆盖全部成员，报 `match-exhaustive`。
- `private` / `protected`：解释器运行时 `AccessError` + 同文件静态 `access-private` / `access-protected`。
- `impor build` 增量缓存与 `--no-cache`：缓存源码、版本、路径、TCC 与依赖；实测 0.66 s → 0.05 s。
- `impor conformance` 子命令（`--ci` / `--only` / `--json`）；runner 保存并恢复 `imporc_args.txt`，修正 `first_diff` 误报。
- `impor pkg graph` 依赖树（循环/未安装标记）与 `impor pkg audit` 离线审计（lock、版本、哈希、HTTP 源、requires）。
- 12 课教学系统 `impor teach`：讲义、练习、答案、`--write` 与 `impor teach grade` 自动判题；全部 12 课答案实测通过。
- 编辑器「项目质量检查（类型+lint）」聚合 300 条诊断到问题面板；「教学大纲（12 课）」入口。
- 插件写 API `write_file` / `write_current`，默认拒绝，按 `plugin_write_allow` 或 `IMPOR_PLUGIN_PERMISSIONS` 授权，路径限制在工作区。
- 编辑器五项升级：全项目搜索/批量替换（正则、区分大小写、全词、diff 预览、按文件勾选、事务备份与撤销）、大文件虚拟滚动只读窗口、项目依赖图 + 离线审计三标签窗口、Windows 屏幕阅读器语义桥接（原生 EDIT + JSON 快照）、设置快照同步（newer/local/remote 三策略）。
- 插件独立进程隔离：默认子进程宿主 + JSON-RPC 代理，命令/格式化器/主题/状态项可用，面板 builder 降级为占位；写权限仍由父进程按 `plugin_write_allow` / `IMPOR_PLUGIN_PERMISSIONS` 校验。
- 文档：`docs/语言-3.1.md`、`docs/编辑器-3.1.md`、`docs/测试矩阵-3.1.md`、`docs/库-atlas-3.1.md`。
- 标准库 `std.atlas`：700 个 Impor 独有微库（10 族 × 7 套 API × 10 变体），每库一个独立 `.impor` 模块与可运行函数；目录见 `impor/std/atlas/CATALOG.md`，全量导入冒烟见 `tests/atlas_700_smoke.impor`。

### Changed
- 版本号 7.3.0 → 3.1.0（重编号 + 编辑器五项升级，功能有增量）。
- `typecheck` 全仓 `.impor` 扫描为 0 误报（修复 `vars()` 非 AST 节点崩溃与 `int`/`float` 误判）。
- `conformance` 兼容含空格与括号的安装路径：自举后端先把用例复制到无空格临时目录，桌面镜像 `Impor(I++) 3.1.0` 实测 23/23。
- 修复 `impor.bat` 在 `if` 块里展开 `%errorlevel%` 导致退出码恒为 0：`impor check` 的类型失败现在能正确返回 1，可直接接入 CI。
- 本版边界：带载荷 ADT、`async/await` 与最小并发、C 后端/自举的 JSON/正则/lambda、跨文件访问控制仍未完成；UIA 为语义桥接而非逐控件 provider，插件隔离为进程隔离而非 OS 沙箱。

### Fixed
- 全项目搜索/替换面板：搜索→替换→撤销已在真实 Tk 主循环中跑通；批量替换/撤销不再对虚拟滚动 pane 做整文件重载，避免把当前 3,000 行窗口写成全文。
- 大文件虚拟滚动：breadcrumb 徽章在编辑器延迟初始化后重新写入，稳定显示「虚拟滚动 起-止 / 总行数 · 只读」。
- 依赖图/审计窗口：只扫描当前工作区，不再误扫安装根；刷新加入代次校验，快速连点刷新不会重复插入旧结果。

## [7.2.0] - 2026-11-12

### Added
- 渐进式启动：工具栏、资源管理器、outline、标签和编辑区先构建并进入事件循环；底部面板、会话恢复、插件、文件监听在首帧后补齐；splash 停留 280ms、淡出 120ms，可点击立即进入。
- 自动更新：启动 7 秒后静默读取清单，状态栏提示新版本；下载进度窗、SHA256 校验、安装后询问重载、失败回滚旧 VSIX。
- 项目级配置 `.impor/settings.json`：主题、字号、缩进、换行、缩略图、inlay、语义高亮、大文件阈值、保存格式化、自动保存、任务模板、shell profile、插件目录；项目覆盖全局。
- 只读插件 API：`current_text/current_path/read_file/list_files/toast`；注册命令、侧栏面板、状态栏、主题、格式化器。
- Git：行级 blame、按 hunk 暂存、分支查看/切换、冲突文件入口。
- 多终端标签：cmd/powershell/pwsh profile，每标签独立持久 shell；任务模板支持 `{file}/{workspace}/{python}/{kind}`；problem matcher 抓 `file:line[:col]`。
- 编辑器智能：inlay hints 字面量类型、常量语义高亮、四层括号着色、作用域吸顶条、大文件可视行着色。
- 可访问性：高对比主题、Ctrl+1 编辑器 / Ctrl+2 资源管理器 / Ctrl+3 底部命令条 / Ctrl+4 问题 / Ctrl+5 命令面板、字号缩放。

### Changed
- 7.1.0 → 7.2.0。
- 自举编译器 `imporc.exe` / `imporc_self.exe` / `imporc_self_raw.exe` 的内置版本号同步为 7.2.0。

## [7.1.0] - 2026-11-12

### Added
- 原生控制台启动壳 `impor-fast.exe`：console 子系统，控制台窗口随进程创建即出现，实测入口 35–84 ms；Python 解析顺序为 `IMPOR_PYTHON` → 同目录 `impor_python.txt` → PATH → 本机兜底。
- `version` / `--version` / `-v` 快路径：不导入 lexer/parser/interpreter，直跑 `-S -I` 中位约 93 ms（最低 74 ms）。

### Changed
- 无参数启动先打开编辑器，不再先加载 CLI 语言后端。
- `impor.bat` 优先调用 `impor-fast.exe`，回退 `python -S -I -X utf8 Impor.py`。
- Python 解释器固定到 `impor_python.txt` 指定的 3.11.5，避免命中 Windows 应用别名。
- 7.0.0 → 7.1.0。

## [7.0.0] - 2026-11-11

### Added
- 调用图：从函数体抽取 caller→callee 边，左侧列出函数与出度，右侧分“调用者/被调用”两段展示，双击跳转。
- 条件断点与命中次数：条件按源码行号下发；断点可设“第 N 次命中才暂停”，断点列表显示条件、阈值与累计命中。
- 日志点集中管理：断点列表统一编辑条件、命中次数、日志表达式。
- 运行配置：`.imporrun` 保存入口文件、参数、工作目录、环境变量；Ctrl+Shift+F5 直接运行。
- 项目级快速修复：全工作区格式化预览，逐文件 unified diff，确认后批量写回。
- 右键菜单补齐：文件树可复制相对路径、在资源管理器打开；标签可复制路径、打开目录、关闭右侧标签。

### Changed
- 6.9.3 → 7.0.0。

## [6.9.3] - 2026-11-11

### Added
- 全项目重命名改为预览面板，逐条列出文件、行、代码后再应用。
- 项目诊断总览：扫描工作区，按错误/警告列出文件、行、说明，双击跳转。
- 导航历史：Alt+Left / Alt+Right 后退与前进，保留最近 50 个位置。
- 批量编码规范化：预览后把目录内 `.impor` 统一为 UTF-8 无 BOM + LF。
- 更新失败回滚：VSIX 安装返回非 0 时自动尝试重装上一版；清单支持 `min_version`。

### Changed
- 6.9.2 → 6.9.3。

## [6.9.2] - 2026-11-11

6.9.1 收尾补丁。

### Added
- 项目引用总览：跨工作区按引用次数列出符号，回车跳到定义。
- `site/update.json` 增加 `sha256` 字段，下载后校验安装包。

### Changed
- 6.9.1 → 6.9.2。

## [6.9.1] - 2026-11-11

6.9.0 收尾补丁。

### Added
- 差异块支持“撤销上一块”，每个标签保留最近 30 次块操作。
- 命令面板最近使用写入配置，重启后仍置顶。
- 状态栏编码/换行可点击：UTF-8、UTF-8 BOM、LF、CRLF，可转换并保存。

### Changed
- 6.9.0 → 6.9.1。

## [6.9.0] - 2026-11-10

编辑器五项升级（逐块合并 / 全项目索引 / 命令面板 / 编码换行 / 更新校验）。

### Added
- 差异视图支持按块处理：上一块/下一块、采用磁盘版当前块、保留编辑器版、全部载入磁盘。
- 项目符号索引：跨整个工作区列出函数、类、结构体与变量，回车跳到对应文件行。
- 命令面板模糊匹配：子序列匹配按紧凑度排序，最近使用的命令自动靠前。
- 状态栏显示 UTF-8 / UTF-8 BOM / CRLF / LF，打开时检测，保存时按原编码和换行写回。
- 更新下载改用 `.part` 临时文件，支持 SHA256 校验，校验失败自动丢弃。

### Changed
- 6.8.0 → 6.9.0。

## [6.8.0] - 2026-11-09

编辑器五项升级（图形设置 / 可操作差异 / 保存冲突 / 更新清单 / 搜索与大纲沿用增强）。

### Added
- 图形设置面板补齐工作区选择、恢复默认、打开配置文件。
- 差异视图可操作：载入磁盘版、保存编辑器版、跳转下一处差异。
- 保存冲突三选：先看差异、覆盖保存、放弃保存。
- 更新清单 `site/update.json`，支持 `IMPOR_UPDATE_URL` 远程清单、下载与一键安装。

### Changed
- 版本 6.7.0 → 6.8.0。

## [6.5.0] - 2026-11-09

桌面编辑器六项升级（签名 / 测试 / 调试 / 重构 / 终端 / 设置）。

### Added
- 签名帮助：输入 `(` 或 `,` 时浮出多行签名，当前参数用 `▶` 高亮；LSP 新增 `textDocument/signatureHelp` 与 `signatureHelpProvider`。
- 测试面板：不运行也能先“发现用例”；只重跑失败文件；监听测试文件改动并自动重跑；显示通过 / 失败 / 断言计数。
- 调试增强：手工监视表达式随配置持久化；断点列表提供“条件 / 日志”可视化编辑按钮；异常类型过滤输入框，只暂停匹配的异常。
- 项目级重构：F2 重命名前预览涉及文件与处数；一次重命名可整批撤销；新增项目依赖图与“可能未使用的符号”列表。
- 真终端：底部命令条支持 `cd`、`pwd`、`clear`，上下键翻阅历史，`Ctrl+C` 终止当前命令，基础 ANSI 颜色映射。
- 设置面板：新增字体族、缩略图开关，补全自动换行 / 缩进 / 自动保存 / 保存格式化；配置持久化。

### Changed
- 版本 6.4.0 → 6.5.0。

## [6.4.0] - 2026-11-09

桌面编辑器六项升级一次落地（导航 / 调试 / 编辑 / 测试 / 任务条 / 大文件）。

### Added
- 导航与重构：面包屑按「文件 › class › def」分段可点击跳转；`Shift+F12` 查找引用、`Alt+F12` 调用层级，结果可双击跳转；`F2` 触发重命名符号。
- 调试闭环：调试页新增表达式求值框，可在当前帧或选中栈帧求值；变量树双击可直接改写值；断点列表显示行号与条件/日志点，双击跳转、Delete 删除；异常时自动暂停并展示异常调用栈（可在调试页关闭该开关）。
- 编辑效率：`Ctrl+D` 选中下一个相同词（`Ctrl+Shift+D` 仍复制当前行）；`Alt+Shift` 拖拽做矩形列选择；代码片段、保存时自动格式化沿用现有能力；折叠状态按文件写入配置，下次打开自动恢复。
- 测试面板：`F6` 只运行当前文件测试；失败用例双击跳堆栈；通过 / 失败用绿 / 红着色，并按用例集统计通过、失败数。
- 任务运行条：底部常驻 `$` 命令输入，回车执行任意命令，或点「运行 / 构建 / 测试」直接跑当前文件；输出进入现有输出面板。
- 大文件保护：打开超过约 2 MB 的文件先提示；大文件只高亮可视区，minimap 自动降采样。

### Changed
- 版本 6.3.0 → 6.4.0。

## [6.3.0] - 2026-11-09

桌面编辑器三项升级。

### Added
- 调试器从模态弹窗改为底部「调试」停靠页：调用栈可点选切帧，变量树可展开 dict / list，F10 / F11 / Shift+F11 驱动，继续后释放解释器。
- 代码折叠：按缩进识别 class / def / struct / if / elif / else / for / while / match / try / catch，行号左侧箭头点击折叠，命令面板可展开全部。
- 面包屑：编辑器顶部显示「文件 › class › def」。
- 外部文件变更自动重载：干净标签自动刷新并保留光标与滚动位置；有未保存改动时提示冲突，不覆盖用户内容。

### Changed
- 版本 6.2.0 → 6.3.0。

## [6.2.0] - 2026-11-09

第二轮性能专项：函数调用与启动导入开销。

### Changed
- 闭包创建时预计算“末行是否为隐式返回”和正文长度，调用时不再每帧判断与切片。
- 参数绑定直接写局部字典；成功调用不再调用 `sys.exc_info()`，只在异常路径收集调用栈。
- `config.py` 不再为包缓存目录导入整个 `pkgmgr`（连带 `urllib` / `http.client` / `ssl` / `zipfile`），启动少加载约 95 ms。
- `pkgmgr.CACHE_DIR` 与 `config` 共用同一个缓存目录函数，保持路径语义不变。

### Performance
- `impor bench --gate`：0.536 s → **0.520–0.563 s**（多次实测，门槛仍为 3400 ms）。
- `bench_compare.py`：`loop_1e6` 799.8 → 731.9 ms，`str_2000` 283.4 → 249.1 ms，`fib_22` 694.6 → 648.4 ms。
- `fib(22)` cProfile 总量 1.916 s → 1.758 s；`_call_closure` 自身耗时 0.347 s → 0.290 s。

### Notes
- 本轮没有改执行模型；字节码 VM 仍是后续选项。递归场景仍与 CPython 有约 5 倍差距。

## [6.1.0] - 2026-11-08

性能专项：解释器 100 万次循环从 7.031 秒降到 0.581 秒。

### Changed
- 帧采样改为每 1024 轮取一次时钟，不再每轮调用 `time.time()`。
- 作用域未出现 `const` 时跳过每次赋值的可变性检查。
- 二元运算增加整数热路径，跳过通用类型分派。
- 变量读取先查当前作用域字典，未命中再沿作用域链查找。
- `impor bench --gate` 门槛由 9000 ms 收紧到 **3400 ms**。

### Performance
- `loop_1e6`：8042.1 ms → 799.8 ms（含进程启动，约 10.1 倍）。
- `str_2000`：525.6 ms → 283.4 ms。
- `fib_22`：1293.4 ms → 694.6 ms。
- 纯解释执行 `impor bench`：7.031 s → 0.581 s。

## [6.0.0] - 2026-11-08

v6.0 只做“把支持做实”，不新增关键字。

### Added
- `implements` 真契约校验：类缺少接口方法时直接报 `ContractError`（E1014）；基类继承来的方法也算实现。
- 文件/目录内建：`filelist` / `filemkdir` / `fileexists` / `filesize`（解释器路径）。
- `impor teach [课号]`：直接查看 12 课教学大纲，可只显示某一课。
- `impor bench --gate`：读取 `benchmarks/gate.json`，解释器 100 万次循环超过 9000 ms 即失败退出。
- `docs/路线图-v6.0.md`：四条线逐项标“已开工 / 下一步 / 边界”。

### Changed
- 诊断库新增 E1014 与中文“为什么 / 修复”文案。
- 版本号 5.6.0 → 6.0.0；VSIX 重打为 `vscode-impor-6.0.0.vsix`。

### Notes
- 修饰符仍是声明性标记，没有运行时访问控制；JSON/正则/C 后端一致性仍待 v6.1。
- `bench --gate` 是防退化门槛，不代表已达到 Python 级性能。

## [5.6.0] - 2026-11-08

定位收敛为「中文母语入门 + Windows 小工具独立 exe」，先补可信度与工程化。

### Added
- `impor doctor`：逐模块检查运行时、C 后端、自举产物，缺一项即失败。
- JSON 内建：`json_encode` / `json_decode`；正则内建：`regex_match` / `regex_find` / `regex_replace`（解释器路径）。
- `benchmarks/bench_compare.py`：循环、字符串、递归三类任务对比 Python 3.11。
- `docs/路线图-v5.6.md`：功能、性能、生态、人群四张表，标明已完成/进行中/计划。
- `docs/教学大纲.md`：12 课教学路径与评估方式。
- CI 增加 `impor doctor` 步骤。

### Changed
- 版本号 5.3.0 → 5.6.0；VSIX 重打为 `vscode-impor-5.6.0.vsix`。
- 桌面镜像改为完整运行时同步，补回 23 个缺失模块并跑 version/run/build 冒烟。

### Notes
- JSON/正则目前只在解释器可用，C 后端与自举编译器待同步。
- 修饰符仍无访问控制语义，`implements` 仍不做契约校验；路线图中列为进行中。

## [5.3.0] - 2026-11-08

中文兼容、迁移工具与新关键字语法。

### Added
- 中文关键字兼容开关：`IMPOR_LEGACY_CN=1` 或命令行 `--legacy-cn` 恢复旧中文关键字映射，默认仍为纯英文。
- `impor migrate-cn [--dry-run] [路径...]`：把 `.impor` 文件中的中文关键字迁移为英文，自动生成 `.cn.bak` 备份，跳过字符串与注释。
- `interface` 作为类契约、`record` 作为字段记录、`namespace` 作为命名空间容器；`public` / `private` / `protected` / `abstract` / `final` / `sealed` / `override` 作为修饰符，`implements` 支持接口列表。
- `lambda (a, b): 表达式` 匿名函数表达式，解释器支持闭包捕获。

### Changed
- VS Code 语法高亮同步英文新词，中文关键字不再高亮，仅在兼容模式下由词法器识别。

### Notes
- `defer` / `macro` / `template` 仍为预留保留字；`lambda` 与 `interface` 等新语法目前只在解释器路径完整支持，C 后端/自举编译器暂未同步。
- 旧的 `.impor` 中文关键字脚本仍建议用 `impor migrate-cn` 迁移后再提交。

## [5.2.0] - 2026-11-08

全英文关键字。

### Changed
- 删除全部中文关键字与中文软别名：`如果` / `变量` / `函数` / `打印` / `除非` 等不再翻译为英文关键字，只按普通标识符处理。
- 保留字集合改为纯英文，新增 15 个英文保留字：`interface` / `namespace` / `public` / `private` / `protected` / `abstract` / `final` / `override` / `implements` / `defer` / `lambda` / `macro` / `template` / `sealed` / `record`。
- 编辑器代码片段、REPL 多行块判断、lint 中文别名提示同步移除中文关键字路径。

### Notes
- 既有中文关键字脚本需迁移为英文写法；中文仍可作为标识符、注释与字符串内容。
- 新增英文保留字目前为预留占位，尚无对应语法。

## [5.1.0] - 2026-11-07

类型系统、DAP 调试、AST 索引、测试树与多根工作区。

### Added
- 跨文件语义类型检查：变量赋值、函数参数标注与调用实参做类型传播，诊断附可执行的 Quick Fix。
- 标准 DAP 调试适配器 `impor dap`：VS Code 断点、单步、调用栈、局部变量与表达式求值复用同一解释器。
- 符号索引改为 AST 优先建索引（语法树作用域链），解析失败自动回退行扫描，修复同名不同作用域冲突。
- 测试面板升级为用例集 / 用例两级树，通过用例带覆盖率标记。
- 多根工作区与 `.imporproj`：源码根、忽略目录、搜索路径、缩进与行宽写入项目配置。

### Notes
- 类型检查是轻量静态传播，表达式无法推断时保留 `?`，不阻断运行。
- DAP 复用解释器线程，单步与断点语义与编辑器内置调试面板一致。

## [5.0.0] - 2026-11-07

语言服务与调试能力升级：项目级符号索引、跨文件 LSP、真实诊断修复和更完整的调试器。

### Added
- 项目级语义符号索引：扫描工作区 `.impor`，记录声明、参数、作用域、类型推断、跨文件引用。
- LSP 升级：跨文件跳定义、引用查找、工作区符号、项目补全、跨文件重命名、类型化悬停。
- `textDocument/references` 与 `workspace/symbol` 能力，VS Code 扩展继续复用同一 LSP。
- Quick Fix 接真实诊断：未定义名称自动补 `var` 或从项目导入；支持整文件批量修复。
- 调试器：运行到光标（Ctrl+F5）、日志点（Ctrl+Shift+B）、步过/步入/步出与调用栈面板。
- 编辑器跨文件跳定义和重命名改走符号索引，不再每次线性扫描全项目。
- VSIX 现在携带 LSP 客户端依赖，安装后语言服务可直接启动。

### Notes
- 符号索引按行扫描 + 缩进作用域，类型推断覆盖字面量；复杂表达式的精确类型仍可能为 `?`。
- 跨文件重命名目前按项目引用表执行，同名不同作用域仍可能一起改。
- 日志点在解释器线程里求值，只记录不暂停。

## [4.9.0] - 2026-11-07

编辑器工作区与日常使用闭环：打开文件夹、会话恢复、多光标、标签拖拽、精准修复、测试面板、Git 差异与正则替换。

### Added
- 打开任意文件夹作为工作区，文件树、搜索、运行和 Git 以当前工作区为根。
- 会话恢复与配置持久化：主题、字号、缩进、自动保存、工作区、打开的标签在重启后恢复。
- 多光标：`Alt+Click` 追加光标，输入与退格同步到所有光标。
- 标签拖拽排序：鼠标拖动标签调整顺序。
- 精准快速修复：补 `var`、补右括号、删除只出现一次的未使用变量。
- 测试面板：`impor test` 输出结构化到「测试」页，双击失败行跳到源码。
- Git 面板新增查看差异、当前分支、切换分支。
- 正则批量替换，支持捕获组。

### Notes
- 超长文件仍采用可视区增量高亮 + 关闭缩略图的降级方案，未实现行级虚拟滚动。
- 多光标暂不支持跨行 Enter 同步和列选择拖拽。

## [4.8.0] - 2026-11-07

编辑器代码动作与大文件性能：提取变量/函数、生成 expect 测试、补全 import；可视区增量高亮、文件树懒加载、超长文件降级。

### Added
- 代码动作：提取变量、提取函数、生成 `expect` 测试、按符号自动补全 `import`。
- 文件树懒加载：展开目录时才扫描子项，避免启动时遍历整棵树。
- 大文件模式：超过 1500 行只重绘可视窗口内的高亮，滚动时按需补绘。
- 超过 6000 行时跳过缩略图重绘，优先保证输入响应。

### Notes
- 超长文件暂未实现真正的虚拟滚动，采用“可视区增量高亮 + 关闭缩略图”的降级方案。

## [4.7.0] - 2026-11-07

编辑器全功能补齐：补全/跳转/重命名、拆分独立文件、项目替换、Git 与测试面板、片段与多行编辑。

### Added
- 模糊补全、方法参数提示、悬停文档；`Ctrl+Click` 支持跨文件跳定义。
- 符号重命名（`Ctrl+Shift+R`）与项目批量替换（带文件级预览）。
- 拆分视图可左右各开不同文件，支持双向同步。
- 资源管理器右键新建文件/文件夹、重命名、删除。
- 标签左移/右移（`Ctrl+Shift+PageUp/PageDown`）、插入代码片段、多行行尾插入。
- 快速整理（`Shift+Alt+F`）、解释选中代码、运行测试（`Ctrl+Shift+T`）、Git 面板。

### Fixed
- 主题切换保留光标位置与滚动位置（撤销栈仍会重置）。

## [4.4.0] - 2026-11-07

编辑器工程化：左右拆分视图、浅色/深色主题切换、设置面板；全项目搜索、断点行号映射与可视化调试补齐。

### Added
- 左右拆分视图（`Ctrl+\`）：当前文件克隆到右侧独立编辑，可一键同步。
- 浅色/深色主题切换：设置面板或命令面板切换，重建界面并保留打开文件。
- 编辑器设置面板（`Ctrl+,`）：字号、缩进、自动格式化、自动保存、自动换行。
- 集成终端（`Ctrl+反引号`）与包管理面板。
- 运行选中代码（`Ctrl+Shift+Return`）。

### Fixed
- 断点行号按真实文件映射，调试执行行显示箭头与整行高亮。
- 活动栏「搜索」改为全项目搜索入口。

## [4.3.0] - 2026-11-07

报错体验三端统一与 imporc 模块导入补齐：错误统一给出源码片段、波浪线定位、错误码与带行号调用栈；新增 `impor explain`；C 后端 / imporc 的错误文案与解释器同源；imporc 支持 `from ... import`、包路径与嵌套 import；`impor build` 输出耗时、产物大小与依赖模块。

### Added
- `impor explain [E1001|ZeroDivisionError]`：直接解释错误原因、修复方式与最小示例；不带参数列出全部错误码。
- 统一错误显示：运行错误首行 `运行错误: 第N行: 异常名: 消息 [E100x]`，随后「为什么 / 修复 / 源码片段 + `^` / 调用栈（自顶向下，带行号）」；解释器、C 后端、imporc 三者同格式。
- C 后端独立 exe 运行时错误：源码行编译期嵌入产物，脱离 Python 后仍能打印出错行与上下文。
- imporc 模块导入：支持 `from mod import a`、`from mod import a as b`、`from mod import *`、`import pkg.mod`、`import pkg.mod as m`，以及函数体内嵌套 `import`（编译期内联 + 运行期模块对象）。
- `impor build` 成功输出：构建路径、产物大小（人类可读 + 字节数）、总耗时（转译 / tcc 分项）、依赖模块列表、是否依赖 Python。
- 转换内建：新增 `str(x)` 与 `int(x)`（浮点截断、无法解析返回 0），解释器 / C 后端同语义；`to_str` 保留为兼容别名。
- 编辑器启动改为空白文档，不再自动载入示例程序。

### Fixed
- imporc 调用栈行号不再恒为 0：直接函数调用记录真实源码行。
- imporc 编译错误提示带源码行号，不再只给一句无位置的消息。
- imporc 产物与 C 后端共用运行时：无源码嵌入时使用运行时内置空表，不再出现 `undefined symbol 'g_src_lines'`。

### Tests
- 新增 `tests/test_v43_unify.py`：`impor explain` 四种路径、`impor build` 输出字段、C 后端运行时片段 / 错误码 / 调用栈、imporc `from` + 包路径 + 嵌套 import 产物执行。

## [4.2.0] - 2026-11-07

P0 第一批：面向真实项目的容器能力与测试基础设施。字典、切片、嵌套函数/闭包与模块对象在解释器、C 后端、imporc 自举编译器三端打通（imporc 支持单层 `import mod [as m]`），`expect()` 提供表驱动断言，`impor test` 每个测试文件独立临时目录隔离。

### Added
- 字典：`{k: v}` 字面量、`d[k]` 读取与赋值、`d.keys()` / `d.values()` / `d.items()` / `d.get(k, 默认值)` / `d.has(k)`、`for k in d` 迭代键；字典与数组参与 `==` 时按内容深比较。
- 切片：数组与字符串 `c[a:b]`、`c[a:b:step]`、`c[-2:]`、`c[::2]`；负索引、越界裁剪、负步长与 Python 语义一致；`step == 0` 报 `ValueError`；字典切片报 `TypeError`。
- 嵌套函数 / 闭包：函数体内 `func` 捕获外层自由变量，`nonlocal set` 改捕获变量、`global set` 改全局；函数可作为参数、返回值、变量并在之后调用。C 后端生成独立 C 函数 + `Val*[]` 环境，imporc 将全部局部装箱为 `Val*`，重复调用保留状态。
- `expect(actual, expected, label?)` 内建断言：失败抛 `AssertionError`，消息统一为「标签：期望 X，实际 Y」；解释器、Python 转译、C 后端与 imporc 支持。
- 模块对象作为值（C 后端）：`import m as mod` 后可赋值、传参、打印、比较；运行期 `V_MODULE` 承载模块名与「导出名 -> 函数/顶层变量」属性表，`mod.f(..)` 动态调用、`mod.v` 读取、`type_of(mod) == "module"`、打印为 `<Impor 模块 名>`，与解释器一致。
- `impor test` 临时目录隔离：每个测试文件在独立临时目录执行，`IMPOR_TEST_TMP` 提供路径；新增 `--no-isolate` / `--keep` / `--filter 关键字`；汇总显示断言条数。
- 统一错误码：运行错误首行附加 `[E1001]`…`[E1099]`，解释器 / C 后端 / imporc 同一套编号（`ZeroDivisionError`=`E1001`、`NameError`=`E1008`、`AssertionError`=`E1007` 等）。
- 调用栈：未捕获运行错误在四段式后输出「调用栈（自顶向下）」，C 后端与 imporc 产物同样输出；`try/catch` 捕获后不打印、进程正常继续。
- 诊断文案对齐：C 后端除零 / 下标越界的消息改为与解释器一致的 `division by zero` / `list index out of range`。

### Fixed
- `imporc` 字典下标赋值、字典 `for` 迭代、`rt_method` 方法分派与切片后缀生成。
- Python 转译字典 `keys()` / `values()` 不再外泄 `dict_keys` / `dict_values` 内部类型。
- C 运行时 `rt_repr` 让 `print(d)` 的嵌套字符串带引号，与解释器输出一致。

### Tests
- 新增 `tests/test_v42_dict.py`（8 项）：解释器 / Python 转译 / C 后端 / imporc 四路径字典切片输出一致，`expect` 成功计数与失败消息，`impor test` 文件隔离。
- 新增 `tests/test_v42_closure.py`（4 项）：闭包捕获 `var` 局部、`nonlocal` 改值、函数作参数，四路径输出一致。
- 新增 `tests/test_v42_module.py`（3 项）：模块赋值/传参/动态调用/打印/比较，解释器与 C 后端一致；缺属性报错。
- 新增 `tests/test_v42_diag.py`（5 项）：错误码表唯一性、解释器与 C 后端首行 / 调用栈一致、`catch` 后退出码仍为 0。
- `tests/test_impor.py` 183 项通过。

## [4.1.0] - 2026-11-07

语言核心补齐与私有源可写运营：C 后端可直接导入模块并保持独立 exe，标准库补 `type_of` / `flatten`，自举编译器修复嵌套 `switch`；包管理器从只读消费升级为可打包、可发布、可检查过期、可清理缓存。

### Added
- C 后端模块导入：`import m` / `import m as mm` / `from m import a` / `from m import a as b` / `from m import *`；编译期拼接模块代码，按引用裁剪未使用定义与死代码，`std.*` 与自定义模块同名符号内部改名隔离；支持相对导入、目录包 `__pkg__.impor`。
- `type_of` 内建：解释器 / Python 转译 / C 后端 / imporc 四路径统一，返回 `null` / `int` / `float` / `str` / `bool` / `list` / 类名；C 运行时 `ClassInfo` 增加显示名。
- `std.collections.flatten`：递归展开嵌套数组。
- `impor pkg pack`：确定性 zip（固定时间戳、权限、条目顺序），同内容必同 `sha256`。
- `impor pkg publish`：目录自动打包或直接发布 `.zip`，上传后自动创建/更新 `index.json`；同版本需 `--force`。
- `impor pkg outdated`：按锁文件来源比对约束内可用最高版本。
- `impor pkg prune`：清理缓存中不可达的版本。
- 私有源 HTTP `PUT` 上传与 Bearer 鉴权，`file://` / 本地路径可直接写并自动建父目录。

### Changed
- 版本统一为 `4.1.0`：`Impor.py`、`impor/__init__.py`、`pyproject.toml`、`bootstrap/imporc.impor`、VS Code `package.json` / `package-lock.json`。
- 重建 `impor/imporc.exe`（8MB 栈、图标注入、版本打印 4.1.0）。
- 清理 `ctrans.py` 过时的「不支持节点」文案：属性访问、类、异常处理、`match` 等已支持项不再误报。

### Fixed
- imporc 嵌套 `switch`：引入 switch 缩进栈，修复内层 switch 消耗外层缩进基准导致的分支错乱；补齐收尾 `DED` 回退，避免函数体被提前关闭、主程序被残留语句打断。
- `install()` 写依赖约束时保留原始约束（原来写死版本号，导致 `outdated` 永远判已最新）。

### Verification
- 新增 `tests/test_v52.py`（5 项）：确定性 pack、`file://` publish + install、重复版本需 `--force`、HTTP PUT 假源 publish + outdated、prune 清缓存。
- 新增 `tests/test_v53.py`（8 项）：`type_of` 解释器与 C（含对象类名）、C 模块导入（`import` / `from` / 别名 / 类）、死代码裁剪、`std` 同名符号、`flatten`、imporc 嵌套 `switch`。
- 自举闭环 A→B→C→D 全部通过，B/C/D 产物逐字节一致；`imporc` 版本输出 `v4.1.0`。
- 全量 `python run_tests.py`：**536 项全部通过**（490.038s）；conformance 单独运行 23/23 逐字节一致。

### Known limitations
- C 后端模块导入为编译期拼接：模块顶层代码会随主程序执行；不支持嵌套函数/嵌套类、把模块对象当值传递、Python 模块导入。
- `pkg publish` 的 HTTP 契约要求源端支持 `PUT`；只支持手工上传的源需自行上传归档与 `index.json`。
- VS Code 市场上架仍需要 publisher `impor-lang` 的账号权限；没有 PAT 时可用 `vsce publish --azure-credential` 走 Microsoft Entra ID 登录，本环境只完成 VSIX 产物与发布流程。
- `vscode-impor/package.json` 的 `repository` 仍为空：项目不是 git 仓库，环境中没有可确认的公开仓库 URL，未编造填充（vsce 仅警告，不影响打包）。

## [3.10.0] - 2026-11-07

生态闭环：可复现的依赖锁定、私有源，以及 VS Code 扩展的市场发布链路。

### Added
- 锁文件升级到 `impor.lock` v1.1：记录每个直接/传递依赖的 `version`、`source`、12 位目录哈希、完整目录 `dir_sha256`、归档 `sha256`、`requested` 约束与 `requires` 列表；`[lock]` 段记录 `generated_by` / `generated_at` / 私有源地址。
- `impor pkg install --frozen`：严格按锁文件还原缺失或被改动的包，逐项复核版本与 `dir_sha256`；还原后再校验一次，任何漂移立即报错退出（适合 CI 与复现构建）。
- `impor pkg lock --check` 与 `impor pkg check`：校验锁文件、缓存版本与完整内容哈希，不一致逐条打印并返回 `1`。
- 私有源：`impor.toml` 新增 `[registry] url` + `token_env`（默认 `IMPOR_TOKEN`，也兼容直接写 `token`）；`IMPOR_REGISTRY` 环境变量可覆盖 URL。索引约定 `<url>/index.json`，包归档为 zip 且必须提供 `sha256`；支持 `name`、`name@版本`、`name=约束` 三种安装写法。
- 私有源鉴权：HTTP 请求带 `Authorization: Bearer <token>`；`impor pkg registry` 只显示 URL 与令牌是否已配置，绝不回显令牌。
- 完整性：归档下载后先校验 sha256 再解压，解压时拒绝路径穿越；归档内包名/版本与索引不一致直接拒绝。
- `impor pkg upgrade` 支持私有源（按约束取最高版本并复核约束），git/本地源重新拉取并刷新锁文件。
- VS Code 扩展发布元数据：`LICENSE`、128×128 `icon.png`、`CHANGELOG.md`、`.vscodeignore`、`keywords` / `galleryBanner`；`vscode-languageclient` 移入运行时 `dependencies`，VSIX 自带依赖；新增 `PUBLISH.md` 与手动触发的 GitHub Actions 发布工作流。
- 实际打出 `vscode-impor-3.10.0.vsix`（322 个文件，约 471 KB），可直接 `code --install-extension` 分发。

### Verification
- 新增 `tests/test_v51.py`（11 项）：锁文件来源/哈希字段、缓存篡改检测与 `--frozen` 还原、传递依赖 `requires`、CLI `lock --check` / `install --frozen` / `check`、私有源安装与来源串、`>=0.1` 取最高版本、`file://` 索引、HTTP Bearer 鉴权成功/缺令牌失败、sha256 篡改拒绝、私有源传递依赖自动安装。
- `npx @vscode/vsce package` 成功，VSIX 内含 `LICENSE`、`changelog.md`、`icon.png`、`node_modules/vscode-languageclient`、语法文件与客户端。
- 全量 `run_tests.py` **522 项 OK / 458.435s**；conformance **23/23 三后端逐字节一致**；旧 `tests/test_impor.py::TestPkg` 3 项保持通过；自举闭环 B/C/D 三代产物逐字节一致。

### Known limitations
- 私有源只支持 zip（不支持 tar.gz）；`index.json` 必须显式提供 `sha256`，缺失即拒绝安装。
- `impor pkg install --frozen` 的本地/git 来源仍要求原路径或远端可用；归档来源以 `registry:<url>#<name>@<ver>` 写入锁文件。
- 市场发布需要 publisher `impor-lang` 与 `VSCE_PAT`，本环境没有令牌，只完成到 `vsix` 产物与发布流程；实际上架需在 `PUBLISH.md` 步骤中提供 PAT。

## [3.9.0] - 2026-11-07

生态与工具链：标准库扩充、`impor fmt` 真换行、LSP 格式化、`lint --fix`、imporc switch 绑定模式。

### Added
- 标准库新增 `std.algo`：`sorted` / `sorted_desc` / `index_of` / `bsearch` / `min_index` / `max_index`，全部用三端共有内建实现；`use std` 现已覆盖 text / mathx / collections / algo 四库。
- `std.text` 新增 `pad_left` / `pad_right` / `index_of` / `count_sub` / `split_lines` / `is_blank`；`std.collections` 新增 `find` / `find_index` / `take` / `drop`。
- `impor fmt --max-line N` 真正生效：对超长调用/数组按顶层逗号在括号内换行，参数缩进一级，语义不变；续行可幂等重格式化。
- LSP 新增 `documentFormattingProvider` / `documentRangeFormattingProvider`，VS Code 保存时格式化直接复用 `impor fmt`；`impor.formatFile` 命令改为走 LSP，不再起子进程。
- `impor lint --fix`：自动删除未使用导入、给未使用变量加 `_` 前缀（其余规则仍只报告）。
- `impor lint --style` 新增 `enum-exhaustive`：对 enum 变量做 match 且缺成员、又无 `default` / `case _` 时给提示。
- `impor.toml [lint]` 新增可配置阈值：`max_function_lines` / `max_function_statements` / `magic_number_repeats` / `short_name_whitelist` / `enum_exhaustive`。
- `bootstrap/imporc.impor` 的 `switch` 支持绑定模式（`case v`）与 `Color.MEMBER` 常量分支；新增 conformance `23_switch_match.impor`（三后端逐字节一致）。
- VS Code 扩版本升到 `3.9.0`，补齐格式化能力声明；LSP 客户端消费 `stderr` 输出到 Output Channel，避免管道写满阻塞。

### Verification
- `tests/test_v50.py` 新增 9 项：fmt 换行幂等与语义不变、注释/块头不拆、enum 穷尽性、lint 阈值、`--fix`、LSP 格式化与能力声明、`use std` 四模块注入、标准库 demo 解释器/转译一致。
- conformance `23_switch_match` 三后端逐字节一致，全量 **23/23**；自举闭环 B/C/D 三代产物逐字节一致；全量 `run_tests.py` 通过。

### Known limitations
- `imporc`（自举编译器）对**嵌套 switch**（`case` 体里再写 `switch`）仍会报「意外的缩进开始」；C 后端与解释器不受影响，嵌套场景请先用 `impor build` / 解释器。
- `std.text` 的旧函数（`split` / `trim` / `upper` / `lower` / `replace` / `contains` / `starts_with` / `ends_with` / `char_at` / `substring`）仍依赖 Python 宿主方法，C 后端暂不支持；v3.9 新增函数已按三端共有内建实现。

## [3.8.0] - 2026-11-06

语言可用性第二批：`enum` 枚举 + lint 可读性规则 + fmt 大括号块缩进。

### Added
- `enum` 软关键字：支持 `enum Color { RED, GREEN, BLUE }` 与缩进块两种写法，成员可用逗号/分号/换行分隔；值自动从 0 递增，支持显式整数值（`MEDIUM = 5`，后续成员继续递增）；`Color.RED` 在解析期改写为脱糖整型常量，可直接与 `if` / `switch` 配合。关键字总数不变。
- 三端同步：解释器 / Python 转译 / C 后端（`impor build`）/ 自举编译器（`imporc`）均支持 enum；`imporc` 新增 enum 分支与 `Color.MEMBER` 改写。
- conformance 新增 `22_enum.impor`，三后端输出逐字节一致（22/22）。
- `impor lint --style`（或 `--readability`、`impor.toml [lint] style = true`）新增三条可读性提示：命名过短、重复魔法数字（同一非小整数出现 3 次以上）、函数过长（超过 50 行或 30 条语句）。全部为提示级，不影响退出码；默认 lint 不启用，避免误报。
- `tests/test_v49.py`（21 项）：enum 四路径一致性 + `switch` 中 `Color.X` 模式 + 语法边界、lint 三条规则与默认不误报、fmt 大括号缩进/注释保留/语义保留/幂等/字典键冒号紧贴。

### Fixed
- `impor fmt` 大括号块缩进：原实现只累计词法 INDENT/DEDENT，`func f() { ... }` 的块内语句会被顶到行首；改为同时累计 `{`/`}` 配对深度，闭合括号与块头对齐，并忽略块内原始空白造成的假缩进。
- `impor fmt` 行内空格：左括号/左方括号后不再保留多余空格，字符串结束后与 `+` / 逗号 / 关键字之间恢复空格，`{` 前补空格（`func f(a, b) {`、`print("值:" + x)`）；字典键为字符串时冒号前保持紧贴（`{ "width": W }`），不再写成 `"width" : W`。
- 测试稳定性（v3.8 全量回归）：安全软件偶发把 `%TEMP%` 下新生成的 exe 隔离/删除，`test_v36` 的 2048 冒烟改用项目内临时目录构建，`run_tests.py` 对所有 `.exe` 启动额外重试 `FileNotFoundError`；`examples/snake.impor` 已按新 fmt 规则就地格式化，`impor check` 恢复通过。

### Docs
- `docs/语言规范.md` 新增 enum 枚举小节；`conformance/README.md` 用例清单更新为 22 个。

## [3.7.0] - 2026-11-06

系统级核心第一批：`struct` 字段记录类型。

### Added
- `struct` 软关键字：`struct Point` + 缩进块内 `var x = 0` 字段声明，解析器脱糖为 `ClassDef` + 自动 `__init__`；构造按字段声明顺序传参（`new Point(3, 4)`），字段可读写；空结构体、重复字段、非字段语句均报语法错误。关键字总数不变。
- 三端同步：解释器 / Python 转译 / C 后端（`impor build`）/ 自举编译器（`imporc`）均支持缩进块与 `{ ... }` 大括号块；`imporc` 新增 struct 分支（两种块写法）与自动 `__init__` 生成。
- `tests/test_v46.py`（6 项）：解释器、Python 转译、C 后端、imporc 四路径同负载输出一致 + struct 体内非法语句报错。
- 应用图标：新增 `I++` 品牌图标（深色圆角底 + 白色衬线 I + 双蓝加号），提供 16/32/48/64/128/256/512/1024 PNG 与多尺寸 `impor.ico`；编辑器主窗口、启动过场、关于窗口、调试器、`input` 与断点对话框统一设置窗口图标，并独立 `AppUserModelID` 让任务栏不再显示 Python 文档图标。图标由 `tools/make_icon.py` 确定性生成，可重复构建。
- 独立 exe 图标：`impor build` 在 tcc 编译成功后，用 Windows `UpdateResource` 把 7 档图标写入 PE 的 `RT_ICON` / `RT_GROUP_ICON`（无需 windres），产物在资源管理器、任务栏与文件属性中显示 I++ 图标；`imporc.exe` 一并带图标，`bootstrap/rebuild_imporc.py` 重建时自动补齐。`tests/test_v47.py`（3 项）验证 ICO 帧完整、构建产物含图标且仍可运行。

### Fixed
- C 后端多实参构造/方法调用：tcc 对元素由函数调用初始化的复合字面量 `(Val[]){...}` 报 `index too large`（2 个及以上必现），导致 `new Point(3, 4)` 与双参 `__init__` 长期不可用。改为语句表达式内临时数组 `({ Val _iaN[k] = {...}; rt_new(...); })`；`ctrans.py` 与 `bootstrap/imporc.impor` 的 `new` / `obj_call` / `rt_supercall` 三处同步修复。
- 测试稳定性：`run_tests.py` 对测试内部所有 `.exe` 启动统一重试 `PermissionError`（新写入图标资源的 exe 会被 Windows 安全软件短暂扫描锁定），避免把环境扫描误报成功能失败；被测逻辑与断言不变。
- C 后端 `input()` / `inputstr("提示")`：运行时函数误把字符串实参声明为 `const char *`，实际传入的是 `Val`，tcc 报 `cannot cast 'struct <anonymous>' to 'const char *'`；零参数调用还会报 `too few arguments`。改为接收 `Val` 并用 `rt_str_of()` 取提示文本，生成器为零参数补 `v_str("")`；`c_runtime.c` 与 `ctrans.py` 同步修复，`tests/test_v48.py`（3 项）覆盖带提示、零参数与数字转换。

### Docs
- `docs/语言规范.md` 新增 6.1 节（struct 语义、字段规则、软关键字、三端一致性）。

## [3.6.0] - 2026-11-06

语言宪法 P2：人类友好的诊断系统。

### Added
- 启动过场动画：图形编辑器打开前显示自绘的 `IMPOR IS COMING` 画面（进度条、三段状态文案、版本号），约 1.15 秒后淡出进入主窗口；单击 / Esc / Space 可跳过，`IMPOR_NO_SPLASH=1` 可关闭。
- 四段式诊断：`哪里 + 是什么 + 为什么 + 修复`。`impor/diagnostics.py` 新增 `render_diagnostic()`，CLI（`run` / `run-dbg` / `check` / `lint` / `test` / `compile`）、REPL、LSP、编辑器输出面板共用。
- 诊断知识库 `explain(subject, message)`：覆盖未定义名字、参数个数、不可调用、除以 0、下标越界、类型不匹配、const 只读、断言失败、递归过深、导入失败、属性缺失、作用域、运算符、左值、C 后端限制等 20+ 条规则；未命中时给通用兜底。
- 上下文代码片段：出错行前 1 行 / 后 1 行 + 行号槽 + 按显示宽度对齐的波浪线（中文按双宽）。
- 彩色输出：自动按 `sys.stderr.isatty()` 判断，Windows 控制台自动开启 VT；`IMPOR_COLOR=always|never` 强制、`NO_COLOR` 关闭。
- `tests/test_v44.py`（22 项）覆盖知识库、四段渲染、片段对齐、彩色开关、CLI / REPL / LSP 实跑。

### Changed
- 语法错误首行保持 `语法错误: 第N行 列M:`（既有脚本与断言零破坏），其后追加「为什么 / 修复」。
- 运行错误统一为 `运行错误: 第N行 列M: <异常名>: <信息>`，并附原因与修复。
- `impor lint`：警告 / 错误项后追加缩进「修复：」，`文件:行:列 [级别]` 行首格式不变。
- `impor test` 失败改用同一渲染器，保留 `AssertionError` 等异常名。
- LSP `publishDiagnostics` 的 `message` 追加「为什么 / 修复」（语法错误与未使用变量 / 导入）。
- 编辑器输出面板在语法错误 / 运行异常后追加「修复：」一行。
- 版本四处真源同步 3.6.0，`impor/imporc.exe` 按新种子重建。

### Docs
- `docs/语言规范.md` 新增第 22 节（诊断信息结构）。
- `docs/新手教程.md` 新增 15.4 节（看懂报错）。
- `README.md` 版本描述与更新日志新增 v3.6.0。

### Known limitations
- C 后端仍按 `/*@L<行号>*/` 标记回溯 Impor 行号；宏展开、跨行表达式可能定位到最近的上一处标记。
- 彩色只在终端生效；被管道或文件捕获时输出纯文本（有意为之）。
- 知识库按异常名与消息模式匹配，自定义异常名未收录时只给通用兜底建议。

## [3.5.0] - 2026-11-05

语言宪法 P0+P1：写法收敛 + 文档注释。

### Added
- `docs/写法规范.md`：唯一「推荐写法表」，12 类意图各给出唯一推荐写法与对应兼容写法。
- `impor lint --style`（及 `impor.toml` 的 `[lint] style = true`）：报告 v3.4 兼容写法并给出唯一推荐写法；全部 `[提示]` 级，不影响退出码。
- `##` 文档注释：整行 `##` 绑定紧随其后的 `func`/`def`/`class`/`exception`/`var`/`const` 及类方法，写入 AST `doc` 与符号表 `Symbol.doc`。
- `textDocument/hover` 追加文档注释（引用块显示）。
- `impor doc FILE [-o API.md]`：从文档注释生成 Markdown API 文档。
- `tests/test_v43.py`（19 项）覆盖词法 / 解析 / 解释器 / C 后端 exe / hover / doc 命令 / style 检查 / fmt 保真。

### Changed
- 编辑器：`##` 文档注释使用独立配色（`#7d94b8`），与普通注释区分。
- 语法糖冻结：v3.5 起不再新增同义写法；v3.4 的 12 类写法正式降级为「兼容层」。
- 版本四处真源同步 3.5.0（`Impor.py` / `impor/__init__.py` / `pyproject.toml` / `bootstrap/imporc.impor`），`impor/imporc.exe` 按新种子重建。
- 启动器命令白名单新增 `doc`。

### Docs
- `docs/语言规范.md` 新增第 20 节（文档注释）与第 21 节（写法收敛）。
- `README.md` 版本描述与更新日志新增 v3.5.0。

### Known limitations
- `imporc`（自举编译器）按 v3.3 语法子集工作，不认 `##` 文档注释；带文档注释的脚本请走 `impor run` / `impor build`。
- 文档注释是纯文本，不解析 Markdown 结构、不做交叉引用检查；悬停不自动补参数说明与类型。
- 兼容写法不会自动迁移，`impor lint --style` 只提示不改写。

## [3.4.0] - 2026-11-05

语法宽容层：同一意图多种写法，常见笔误不再直接解析失败。所有新写法在词法/文法层降级为既有 AST 节点，解释器 / Python 转译 / C 后端（`impor build` 独立 exe）行为一致；关键字总量仍为 120，语义不变。

### Added
- `print` 可省略括号（`print "hi"`、`print a, b`）；中文软别名 `打印` / `输出`。
- `;` 作语句分隔符（多余分号忽略），单行块体支持多条语句。
- 类型注解 `var x: int = 5`、`func f(a: int) -> int`（解析后丢弃，仅作提示）；`var x` 可省略初始化（= none）。
- 多行调用 / 数组：圆括号、方括号未闭合期间换行不再结束语句。
- `do { } until cond` / `do { } while cond`（降级为标志变量 + while，`continue` 语义正确）。
- `unless` / `除非` 取反条件；`then` / `则` 作块引导符（可代替冒号）。
- `for i in 1..5` 闭区间；`for each x in ...`；`for x of ...`。
- `for i = 0; i < 3; i++` / `for j = 1; j <= 3; j++`：C 风格计数循环，降级为 `for i in range(...)`。
- `++` / `--`（前缀/后缀，含 `arr[i]++`、`obj.n--`）；`&&` / `||` / `<>`；`:=` 赋值。
- `func f a, b { }` 参数省略括号；`class A extends B`；`new A` 省略空括号；`super.m()` 等价 `super().m()`。
- `catch { }` 不写异常变量、`catch (e)` 括号形式。
- `tests/test_v42.py`：32 项回归（解释器 + C 后端 exe 实跑）。

### Fixed
- C 后端补齐 `Block` 语句生成；全局/局部符号收集递归进入块与循环体（此前 `do` 块内新变量报 `undeclared`）。
- 数字扫描不再把 `1..5` 拆成 `1.` 与 `.5`。

### Changed
- 版本 3.4.0（`Impor.py` / `impor/__init__.py` / `pyproject.toml` / `bootstrap/imporc.impor`）；`impor/imporc.exe` 已按新种子重建（184832 字节）。
- `docs/语言规范.md` 新增「19. 宽容语法层（v3.4）」。

### Known limitations
- `imporc` 自举编译器仍按 v3.3 语法子集工作；宽容写法请走 `impor run` / `impor build` / `impor compile`。
- 类型注解只解析不检查；参数默认值、变参未支持。

## [3.3.0] - 2026-11-05

一致性收敛版本：**不新增语言特性**，只消除三端（解释器 / C 后端 / 自举编译器）的不一致与崩溃点。

### Fixed
- Unicode 语义：`len` / 下标 / `strcut` 一律按字符计数（C 后端按 UTF-8 编码单元），负数下标与越界报错；字符串字面量可直接下标（`"你好"[1]` 此前被误解析为 `print` 的第二个参数）。
- 数值：C 后端明确为 64 位有符号整数，加/减/乘溢出报 `整数溢出：超出 64 位有符号整数范围`（此前静默回绕）。
- 语义对齐（以解释器为准）：`1 == true` 为 `true`；`1 + "a"` 报 `TypeError`（不再隐式拼接）；`"ab" * 3` 得 `"ababab"`。
- 递归：解释器上限提升到约 3000 层，超限报 `RecursionDeep`（此前 1000 层即 `RecursionError`）。
- C 编译失败：tcc 的行列错误反查回 Impor 源码行，输出 `C 编译错误: 第N行 列M` + 源码 + 指示符。
- 自举编译器 imporc：补齐 `div` / `type` 内建、`set 数组[下标] = 值`、字符串下标走 `rt_index`；`imporc_args.txt` 支持双引号包裹含空格的路径（此前路径被空格截断，报 `fileread: 无法打开文件`）。

### Added
- `conformance/`：20 个脚本 × 三后端逐字节比对（`run_conformance.py`，支持 `--ci` / `--only` / `--json`），接入 `tests/test_conformance.py` 与 GitHub Actions。
- `tests/test_v41.py`：13 项 v3.3 回归。
- `LICENSE`（MIT）、`CHANGELOG.md`、`.gitignore`。

### Changed
- `mem()` / `gc()` 输出补齐 `freed` / `freed_total` / `live_scope`，并说明"容器由引用计数即时释放、不进入存活采样"，使数字口径不再令人困惑。

## [3.2.0] - 2026-11-04

- 自动内存管理：`MemoryManager`（阈值回收 / 分配统计 / 存活分类）、`mem()/memstats()/gc()/autogc()` 三端内建、`impor mem` 子命令、C 运行时托管数组与字符串驻留池。
- 图形编辑器重构为深色 Code Studio：多标签、命令面板、输出/问题/变量/内存四联面板、token 化高亮、后台线程运行与调试步进。

## [3.1.0] - 2026-11-02

- 三端语法能力对齐：imporc 补齐 `match/switch`、`try/catch/finally`、`repeat`、数组 `for-in`/字面量、`range`；C 后端新增 `class`/继承/`super` 与数组 `for-in`。
- 8MB 栈补丁修复大文件编译栈溢出（`_setstack.py`）。

## [2.19.0] - 2026-10-31

- 完整自举独立编译器：`bootstrap/imporc.impor` 用 Impor 自身实现，原生 C 独立 exe，脱离 Python。

## [2.18.0] - 2026-10-29

- 大括号代码块 + 120 关键字（58 英文 + 62 中文）。

## [2.17.0] - 2026-10-27

- C 后端数组支持、`const` / `switch` 关键字。

## [2.16.0] - 2026-10-25

- `div()` 内建、REPL 帮助系统、`impor env` / `demo` / `bench` 子命令。

## [2.13.0] - 2026-10-18

- C 后端（`build` 命令）：Impor AST → C → 随包 tcc 原生编译，产出不依赖 Python 的独立可执行文件。

更早版本（v2.1–v2.12）的能力变更见 `README.md` 更新日志历史条目。
