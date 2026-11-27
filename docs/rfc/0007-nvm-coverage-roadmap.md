# RFC 0007：NVM 后端能力补齐路线图（import / 异常 / 类 / 并发）

- 状态：Accepted（路线图，分阶段落地；各阶段独立可发布）
- 关联：`docs/CAPABILITIES.md`、`docs/路线图-补四块短板.md`、`docs/整数语义.md`、`CHANGELOG.md`、`impor/nvm.py`
- 适用边界：本 RFC 只规划 **NVM 自有字节码后端的实现层补齐**，不新增任何关键字或语法糖（自 v3.5 冻结约束），语言表面与解释器完全一致。

## 1. 为什么要改

NVM 是 5.3.0 引入的第一个完全自有后端：栈式字节码 + 自带虚拟机，不依赖 gcc/tcc、不碰机器码。
当前对未覆盖特性在**编译期明确拒绝**（`BackendError`，提示改用 `impor run`），而不是静默糊弄，这是正确的工程边界。

但同一份能力矩阵里，NVM 与解释器仍有明确差距：

| 能力 | 解释器 | NVM |
|---|---|---|
| 模块导入 import / from | 支持 | 拒绝（明确） |
| 异常 try/catch/finally、raise | 支持 | 拒绝（明确） |
| 类 class、new、have 类名 | 支持 | 拒绝（明确） |
| 并发原语 thread/mutex/cond/atomic | 支持 | 拒绝（明确） |

长期不补，NVM 只能跑"单文件、无异常、无类、无并发"的子集，会一直是"二等后端"。
本 RFC 给出**有依赖顺序、每阶段可对拍验收、未完成项继续明确拒绝**的分阶段路线，避免一次性大重构带来的风险叠加。

## 2. 总体原则（所有阶段共同遵守）

1. **不碰语法面**：不新增关键字、不新增同义写法/语法糖；所有工作在编译器、CodeObject、VM 执行层。
2. **语义逐字节对齐解释器**：每个新特性必须与 `impor run`（含 `--big` 口径）对拍，输出一致才算支持。
3. **未完成继续明确拒绝**：某阶段未落地的特性保持 `BackendError`，不允许"部分能跑、部分崩"的中间态。
4. **小步可回退**：每阶段独立成版、独立测试，出现负收益或语义偏差可单独回退，不影响已交付阶段。
5. **优先复用运行时**：NVM 运行在 Python 之上，解释器已有的 `ImporClass`、`Module`、`rt_concurrency` 等运行时对象优先复用，不另造一套不兼容的对象模型。
6. **int64 语义不变**：新路径上的 `+ - *` 与一元负号仍保留 `E1012`，不静默回绕；`--big` 走任意精度。

## 3. 阶段总览与依赖顺序

| 阶段 | 能力 | 工作量档 | 主要前置 | 为什么排这个位置 |
|---|---|---|---|---|
| P0（已完成） | 核心表达式/语句、函数、闭包、泛型、int64、`.ibc` 往返 | — | — | 5.3.0 基线，12 用例 |
| P1 | import / from 模块系统 | M | P0 | 模块是大型程序基础；`.ibc` 已具备字节码序列化，天然支持模块编译缓存 |
| P2 | try/catch/finally、raise 异常 | M | P0（与 P1 可并行） | 纯控制流，用异常表元数据即可表达，不依赖外部资源 |
| P3 | class、new、have（含 have 类名） | L | P2、稳定调用约定 | 方法分派/继承/self 绑定最复杂，需先有稳定 `_do_call` 与异常展开 |
| P4 | 并发 thread/mutex/cond/atomic、channel | L | P1–P3 | 线程安全是横切关注点，需前面全部稳定后再开放共享状态 |

依赖图：`P0 → P1 → P4`；`P0 → P2 → P3 → P4`。P1 与 P2 之间无强依赖，可按迭代资源互换或并行。

---

## 4. P1：模块系统（import / from）

### 现状与方案

`ImportNode` / `FromImportNode` 当前在 `compile_stmt` 直接 `_unsupported("import")`。
NVM 已能把代码编译为 CodeObject 并做 `.ibc` 往返，模块即"独立 globals 命名空间 + 一份 CodeObject"。

### 变更内容

- **`.impor` 用户模块/包**：复用解释器的模块查找路径（`_find_user_module_path`），把目标模块用 NVM 编译为 CodeObject，在独立 globals 上运行，结果包成模块对象；带模块缓存，重复 import 不重复执行，语义对齐解释器 `module_cache`。
- **包与相对导入**：支持 `import pkg.sub`（先加载各级父包再加载目标）、`from . / .. import`、`from mod import name / *`；`__init__` 作为包入口。
- **宿主 Python 模块回退**：导入非 `.impor` 的宿主模块时，直接把宿主模块对象放入 NVM globals（NVM 本就运行在 Python 上，与解释器同口径）。
- **private 访问校验**：跨文件导入模块的 private 符号，按解释器同一句 `AccessError` 拒绝。

### 验收（P1 Definition of Done）

- [ ] 多模块程序在 NVM 下运行，输出与 `impor run` 逐字节一致
- [ ] 模块编译产物可 `.ibc` 往返，再次加载结果一致
- [ ] 相对导入、`import *`、包 `__init__`、模块缓存命中均有对拍用例
- [ ] private 跨文件导入被明确拒绝，错误码/文案与解释器一致
- [ ] 找不到模块报 `ModuleNotFound`，不产生 crash.log

---

## 5. P2：异常处理（try/catch/finally、raise）

### 现状与方案

`TryCatch`、`Raise`、`ExceptionDef` 当前被拒。NVM 的错误已统一由 Python 的 `ImporException` 承载，
因此**不需要把异常做成普通跳转指令**，采用 CPython 3.11+ 同款**异常表（exception table）元数据**方案。

### 变更内容

- **异常表**：编译器为每个 try 记录 `[start, end, handler, err_type]`，标记受保护指令区间与处理器入口；finally 记录在展开表（unwind table）。
- **运行时匹配**：`ImporException` 抛出后，沿 frame 栈自底向上查表，按 `err_type` 过滤（`err_type=None` 为捕获全部），匹配则把异常对象绑定到 `err_var` 并跳到 handler；不匹配则继续向上抛。
- **finally 保证执行**：正常落地、return、异常展开三条路径都执行 finally；用展开表在退出受保护区间时统一触发，避免漏执行。
- **宿主异常包装**：非 Impor 的 Python 异常沿用 `wrap_py_error` 包装后再匹配，口径与解释器一致。
- **raise / 自定义异常**：支持 `ExceptionDef` 注册用户异常类型、`raise` 抛出（含 message 与 cause）。

### 验收（P2 Definition of Done）

- [ ] try/catch/finally、raise、自定义异常与解释器对拍一致
- [ ] 异常类型过滤（只捕获指定类型）、嵌套 try、异常跨函数传播均有用例
- [ ] finally 在正常返回与异常路径都执行且只执行一次
- [ ] 未被捕获的异常继续向上抛，错误码与栈帧信息正确
- [ ] 整数溢出 `E1012` 等运行时异常能被 catch 捕获，语义不回退

---

## 6. P3：面向对象（class、new、have）

### 现状与方案

`ClassDef`、`NewInstance(new)`、`have` 当前被拒。
复用解释器的 `ImporClass` 运行时对象，方法用 NVM 已有的 `PyFunction` 承载，避免另造对象模型。

### 变更内容

- **类编译**：class 体中的方法编译为 `PyFunction`，字段按类属性求值，组装成 `ImporClass`（含方法表、类字段、可见性集合）。
- **继承**：支持基类解析与方法/字段沿继承链查找；基类不是类时按解释器报 `TypeError`。
- **new 实例化与 self 绑定**：`new` 产生实例（实例字段 + 类指针）；`LOAD_ATTR` 命中方法时绑定 self 再调用，构造与字段写入语义对齐解释器。
- **可见性**：private / protected 访问按解释器规则校验；`implements` 真校验保留（缺方法报 `ContractError`）。
- **have 类型判定**：基础类型（int/float/str/list/dict）与类名判定一并支持，`have 类名` 沿继承链判断。

### 验收（P3 Definition of Done）

- [ ] 构造、字段读写、方法调用、继承、方法重写与解释器对拍一致
- [ ] `new` 与 self 绑定正确；`have`（含类名、继承情形）结果一致
- [ ] private/protected 越界访问、`implements` 缺方法被明确拒绝，文案对齐
- [ ] 类方法内的异常能被 P2 的 try/catch 正常捕获（验证两阶段协同）
- [ ] 能力矩阵 `#6 类`、`#11 have` 由"拒绝"变"支持"，无"错误"格

---

## 7. P4：并发（thread/mutex/cond/atomic、channel）

### 现状与方案

并发内建（`CONCURRENCY_BUILTINS`）当前在 `compile_expr` 经 `UNSUPPORTED_BUILTINS` 拒绝。
`rt_concurrency` 已是纯 Python 实现，在 GIL 下可直接工作；难点不在原语本身，而在 **NVM 共享状态的线程安全**。

### 变更内容

- **移除编译期拒绝**：把并发内建从 `UNSUPPORTED_BUILTINS` 放开，直接复用 `rt_concurrency` 同一组实现（thread/mutex/cond/atomic）与 `std.channel`、`std.pool`。
- **线程独立 VM 根**：每个新线程建立独立的 frame 栈与调用深度计数；共享 globals 的写操作由 GIL 保证字节码级安全，逻辑竞态由用户 mutex 负责（与解释器同口径，不承诺更强）。
- **异常跨线程**：线程内未捕获异常按运行时约定回传/记录，不污染其他线程的 frame 栈。
- **闭包/Cell 共享**：多线程共享的 Cell 与容器，安全边界与解释器一致。

### 验收（P4 Definition of Done）

- [ ] thread_spawn/join、mutex、cond、atomic 与解释器对拍一致
- [ ] 多线程共享计数（加锁/原子量）结果正确，无丢失更新
- [ ] channel 收发、超时、关闭语义与解释器一致
- [ ] 线程内异常不导致整个进程崩溃或其他线程状态错乱
- [ ] 能力矩阵并发相关项由"拒绝"变"支持"，且 `support_matrix` 无新增违例

---

## 8. 能力矩阵演进（预期）

| 能力 | 5.3.0（P0） | P1 后 | P2 后 | P3 后 | P4 后 |
|---|---|---|---|---|---|
| import / from | 拒绝 | 支持 | 支持 | 支持 | 支持 |
| try/catch/finally、raise | 拒绝 | 拒绝 | 支持 | 支持 | 支持 |
| class / new / have | 拒绝 | 拒绝 | 拒绝 | 支持 | 支持 |
| 并发原语 / channel | 拒绝 | 拒绝 | 拒绝 | 拒绝 | 支持 |

每阶段落地后用 `tools/capability_gate.py --record` 重采，`CAPABILITIES.md` / `CAPABILITIES.json` 同步更新，全程不允许出现"错误"格。

## 9. 明确不做

- **不新增关键字或语法糖**：不为任何阶段引入新的表面写法（冻结约束）。
- **不做 M:N 抢占式调度器 / work-stealing**：与 `docs/路线图-补四块短板.md` 一致，线程安全与栈切换红线上不提前投入。
- **不做寄存器式 VM 重写**：本路线在现有栈式形态内补齐能力；寄存器式分派属于后续独立评估，不与能力补齐捆绑。
- **不静默放宽 int64**：新路径不引入静默回绕，溢出仍报 `E1012`。

## 10. 风险与回退

- 每阶段独立成版、独立对拍；若某阶段出现语义偏差或实测负收益，单独回退该阶段，已交付阶段与未完成项的明确拒绝均不受影响。
- P3/P4 触及共享对象与多线程，风险最高，必须在 P1/P2 稳定且回归全绿后才启动，不提前开放。
- 模块与类的 CodeObject 结构若扩展，需同步 `.ibc` 序列化与版本兼容，旧 `.ibc` 仍可加载或给出明确版本提示。

## 11. 决策记录

- 日期：2026-11-26
- 结论：接受分阶段路线，顺序为 import → 异常 → 类 → 并发；每阶段独立可发布、对拍验收、未完成继续明确拒绝。
- 反对意见与处理：
  - "四个一起做，尽快补齐"——被否：四特性中类与并发属高风险横切改动，一次性叠加难以定位回归；改为小步交付。
  - "用超级指令/分派重写先换性能"——实测在纯 Python VM 上重 handler 与方法分派为负收益，已回退；本路线聚焦能力补齐，性能优化另走轻量内联路径。
