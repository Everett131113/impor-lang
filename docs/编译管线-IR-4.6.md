# 编译管线与 IR 分层（4.6.0 工作区）

> 上一版：`docs/编译管线-IR-4.4.md`。4.6 只做一件事：**把 4.5.0 对外承认的三条短板补上**
> ——IR 不再拒类/异常/模块导入，C 后端有泛型专用实例，整数语义只剩 int64 一档。

## 一、为什么这一版动 IR

4.4 的 IR 是个"纯函数子集"：只描述单文件顶层 `def` 的指令流，遇到 `class`、`try/raise`、
> `import` 一律 `UnsupportedNode`。当时的理由站得住（类要对象布局、异常要 landing pad、
> 模块解析不属于 IR），但代价是：**用标准库的程序、写类的程序、写异常处理的程序，全都没法走
> `impor ir --build`**。也就是说 IR 后端只能编译"玩具级"示例，无法作为可交付后端。

4.6 的取舍是"**不新造模型，先复用已经能跑的那套**"：

| 缺口 | 4.6 的做法 | 为什么这样做 |
| --- | --- | --- |
| 模块导入 | 复用 C 后端的 `impor/clink.py::link_modules`，在 **AST 层面拼平**后再降 IR | C 后端本来就是这么干的；再在 IR 里造一套模块系统是重复建设 |
| 类 | IR 加 `IRClass` + 5 条对象指令，C 侧复用运行时既有的 `rt_new/rt_getattr/rt_objset/rt_method/rt_supercall` | 运行时对象模型已经定稿，IR 只需要"能表达" |
| 异常 | IR 加 8 条异常指令，C 侧用与 `ctrans` 同构的 `setjmp/longjmp` 模板 | 求值器与 C 产物必须同语义，模板共用最省事 |

## 二、五阶段现状（4.6）

| 阶段 | 实现 | 状态 |
| --- | --- | --- |
| Lex | `impor/lexer.py`（55 关键字、软关键字归一） | 已有 |
| Parse | `impor/parser.py`（缩进/花括号两种块） | 已有 |
| Semantic | `impor/analysis.py`、`impor/typecheck.py`、`impor/lint` | 已有（不在 IR 路径上强制） |
| **IR** | `impor/ir.py`（指令/序列化/参考求值器）+ `impor/ir_lower.py`（AST→IR）+ `impor/cir.py`（IR→C） | **4.4 建，4.6 补齐类/异常/模块导入** |
| Lowering | 解释器 / pytrans / ctrans / cfast / imporc | 仍各自 walk AST（IR 是并行后端，不是唯一路径） |

## 三、指令集（35 条 = 4.4 的 22 条 + 4.6 新增 13 条）

三地址码风格：`目的操作数 ← 源操作数`，每条带 `行号`，文本里以 `; L<n>` 结尾。
`IR_VERSION` 已随 4.6.0 提升，旧 IR 文本不保证可读（IR 文本是调试产物，没有跨版本兼容承诺）。

**4.4 已有的 22 条**（语义未变）：

`CONST` `LOAD` `STORE` `MOVE` `BINOP` `UNOP` `CALL` `PRINT` `LIST` `DICT` `INDEX` `SETINDEX`
`SLICE` `TOSTR` `CONCAT` `ITER` `ITER_NEXT` `LABEL` `JUMP` `JUMP_IF_FALSE` `ASSERT` `RETURN`

**4.6 新增：异常（8 条）**

| 指令 | 语义 |
| --- | --- |
| `TRY_BEGIN tag, catch, fin, rh` | 进入保护区间；`catch/fin` 是标签，`rh` 是"本层是否已捕获"的槽 |
| `TRY_END` | 保护区间正常结束 |
| `CATCH_BEGIN errvar` | 捕获入口；异常对象绑定到 `errvar` |
| `ERR_MATCH dst, errtype` | 判断当前异常类型是否匹配（`dst` 为 1/0） |
| `JUMP_IF_FALSE cond, label` | 不匹配则跳去 `finally`（复用已有指令） |
| `CATCH_END` | 捕获体结束 |
| `TRY_CLOSE` | 离开 try 结构；若 `rh` 置位则向上重抛 |
| `RAISE errtype, msg` | 抛异常（`raise ... from` 明确拒绝） |
| `RETHROW` | 把当前异常原样抛给外层（`finally` 里重抛用） |

求值器侧对应 `Evaluator._run_range` 的 `try_stack`（tag/catch/fin/entered）与 `_catch_pc`；
`finally` 保证执行，`catch` 体内再抛交给外层。

**4.6 新增：类（5 条）**

| 指令 | 语义 |
| --- | --- |
| `NEW dst, cls, args...` | `new Cls(...)`：走 `rt_new`（字段初始化 → 沿继承链找 `__init__`） |
| `GETATTR dst, obj, name` | `obj.x` / 方法取值（字段优先，回落属性） |
| `SETATTR obj, name, v` | `set obj.x = v`（`cir` 生成 `rt_objset((x).o, ...)`） |
| `CALLMETHOD dst, obj, name, args...` | `obj.m(...)`：`rt_method` 取方法后带 `self` 调用 |
| `SUPERCALL dst, name, args...` | `super().m(...)`：按当前类的 `base` 链找方法，`self` 取当前帧 |

类的**结构**不进指令流，而是 IR 模块级的 `IRClass`（名字 / 父类 / 方法 / 字段初始化体），
`cir` 据此生成：方法实现 `f_m<ci>_<mi>`、`self` 包装 `mw_<ci>_<mi>`、方法表 `mt_<ci>[]`、
字段初始化 `fi_<ci>`、`ClassInfo ci_<ci>`，并在 `main` 开头按声明顺序 `reg_class`。

## 四、覆盖矩阵（4.6 实测）

**支持**（4.4 的全部能力，加上下面四块）：

- 类：单继承、方法（`self` 由 lowering 隐式插入）、字段 `set self.x = v`、`new`、
  `obj.m()`、`super().m()`、多层继承的方法查找。
- 异常：`exception` 声明、`raise Type("msg")`、`try/catch/finally`、`catch e`（无类型）、
  嵌套 try（内层捕获后再抛 → 外层接住）、`finally` 一定执行。
- 模块：`use std.x` / `import std.x` / 包内相对导入（经 `clink` 拼平；依赖在前、按需裁剪）。
- 泛型：**函数级**泛型经 C 后端单态化后生成专用符号（见 `docs/单态化.md`）；
  IR 本身仍是擦除视图（IR 里泛型函数就是普通函数），单态化发生在 `ctrans`，不影响 IR 语义。

**明确拒绝**（`UnsupportedNode`，带 `文件:行:列`，不静默降级）：

| 语法 | 原因 |
| --- | --- |
| 嵌套 `def`、`lambda` | 闭包对象没有 IR 表示（`clink` 拼平后仍可能留下嵌套 def） |
| 并发内建 `thread_spawn`/`join`/`alive`/`id`、`mutex_*`、`cond_*` | 参考求值器是单线程、无调度语义；并发请走解释器或 ctrans 后端 |
| `match` / `defer` / `await` / `\|>` / `exit` / `cls()` | 语义尚未定表示方式 |
| `raise ... from` | 异常链没有 IR 槽位 |
| 类体里的非字段语句（如类级 `print`） | 类体只收 `set self.x = ...` 与方法 |

**4.4 中曾被拒、4.6 已放行的**：`class`/继承、`try/catch/raise/exception`、
`NewInstance`/`super()`、`AttrGet`/`AttrSet`（含方法调用）、`import`/`from ... import`。
`demo_23_stack` / `demo_24_queue` / `demo_25_bank_account` / `demo_28_inheritance` / `oop`
以及 `std_demo` / `std_v39_demo` / `demo_19_split_join` / `demo_21_word_frequency`
现在都能走 IR→C。

## 五、验收（本机实测，2026-11-23）

| 项 | 结果 |
| --- | --- |
| 类 / 异常 / 模块 / 泛型专项对拍 | 见下表，全部"解释器 / IR 求值器 / IR→C 产物"三路一致 |
| `examples` 全量 IR 对拍（66 个示例） | **52 逐字节一致 / 6 显式拒绝 / 2 构建失败 / 0 不一致**（4.5.0：36 / 21 / 3 / 0） |
| 稳定性复跑 | `examples/grade.impor` 与 `errors.impor` 产物各 40 轮、`impor run` 20 轮，均 0 失败（证据 `%TEMP%\irverify\fixver.txt`） |
| 自举闭环 | B/C/D 三代产物逐字节一致（64 位工具链，`bootstrap/selfhost_check.py`） |

6 个显式拒绝：`channels`（并发内建）/ `closures` / `demo_30_closures`（闭包）/
`demo_26_struct_point` / `struct`（结构体）/ `stdlib`（`cls()` 等）。
2 个构建失败：`cn_demo` / `err_demo`，两者都是**故意演示语法错误**的示例（`E2002` / `E2003`）。

专项对拍用例（脚本在 `%TEMP%\irverify\`，不进仓库，因为依赖本机 gcc）：

| 专项 | 用例 | 结果 |
| --- | --- | --- |
| 类 | `demo_23_stack` / `demo_24_queue` / `demo_25_bank_account` / `demo_28_inheritance` / `oop` | 5/5 三路一致 |
| 异常 | 未捕获 → rc=1；捕获 → rc=0、与解释器同码同文案 | 2/2 |
| 模块 | `std_demo` / `demo_19_split_join` / `demo_21_word_frequency` / `std_v39_demo` | 4/4 三路一致 |
| 泛型 | `examples/generics.impor` 生成 `gp_ident_i64` / `gp_max2_i64` / `gp_max2_f64` | 输出逐行一致，-O2 下 22% 提速 |

## 六、怎么用

```powershell
impor ir 程序.impor                     # 打印 IR 文本
impor ir 程序.impor --run               # 参考求值器执行（注意：会先打印 IR dump）
impor ir 程序.impor --c > 程序.c         # 只打印 C 源码到 stdout（4.6 起 `--c` 不带路径）
impor ir 程序.impor --build 程序.exe     # 走 IR→C→gcc
```

`--build` 需要可用的 C 编译器（`--cc gcc` 或内置 tcc）；IR 后端与 `build --fast` /
`build --no-fast` 是三条平行路径，语义应对齐。

## 七、已知边界（没做的，明确记录）

1. **并发不进 IR**：线程/锁/条件变量仍是"解释器与 ctrans 专属"。IR 的求值器模型是单线程的，
   硬塞进来会得到"能编译但语义不对"的产物，所以选择明确拒绝。
2. **闭包不进 IR**：嵌套 `def` 与 `lambda` 需要 `Closure` 对象表示，属结构性工作。
3. **IR 不做单态化**：泛型专用实例在 `ctrans` 生成；`cir` 目前按擦除视图生成。
   也就是说 `impor ir --build` 的泛型性能与 4.5.0 相同，提速只在默认 C 后端（`build --fast`/默认）生效。
4. **IR 文本不稳**：`IR_VERSION` 随版本走，不承诺跨版本可读。

## 八、复跑与证据

```powershell
cd <仓库根>
py -3.11 -m unittest tests.test_ir
py -3.11 -m unittest tests.test_v4_backend_parity
py -3.11 tools\verify_release.py
py -3.11 tools\check_fmt_all.py
py -3.11 tools\api_baseline.py

# IR 覆盖（示例级对拍，需要 gcc 在 PATH）
py -3.11 %TEMP%\irverify\verify_irpar.py
```

本机日志位置（不进仓库）：`%TEMP%\irverify\irpar46d.txt`（52/6/2/0）、`fixver.txt`
（setjmp 修复 40 轮）、`exc1.log`、`cls1.log`、`mod1.log`、`mono1.log`、`selfhost46.txt`、
`regress46.txt`。
