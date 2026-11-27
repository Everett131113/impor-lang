# 编译管线与 IR 分层（4.4.0 工作区）

> 更新时间：2026-11-22 · 源码目录 `Impor 4.0.0`（目录名沿用旧名，`VERSION` = 4.3.1）
> 口径：本文所有数字都是本机实测、可用命令复跑；没接入的后端如实写成“未接入”，不冒充已完成。

## 一、为什么要加这一层

对照 `xlang`（`桌面\XLang-master`）的做法，它的编译器有明确五段：

```
Lex → Parse → Semantic → IR(TAC) → Lowing(JVM / x64)
```

每段一个目录、一套单测，**所有后端从同一份 IR 下降**，所以“JVM 能跑、native 跑不了”这类问题
会在 IR 测试里先暴露，而不是等两个后端各自解释 AST 时才发现。

Impor 原本是“一份 AST、四条执行/生成路径各自 walk 一遍”：

| 路径 | 文件 | 怎么工作 |
| --- | --- | --- |
| 树遍历解释器 | `impor/interpreter.py` | 直接 visit AST |
| Python 转译 | `impor/transpiler.py` | AST → Python 源码 |
| C 完整后端 | `impor/ctrans.py` | AST → C（完整 `Val` 值模型） |
| C 数值快路径 | `impor/cfast.py` | AST → C（int64 快路径） |
| 自举子集编译器 | `bootstrap/imporc.impor` | 自己解析自己，过程式子集 |

语义一致性此前只靠 `conformance/cases` 的输出对拍兜着。4.4 补的正是 xlang 那条**单一中间表示**：

```
AST ──lower──▶ IR（三地址码） ──┬── impor ir        （dump 成文本，可 diff）
                               ├── impor ir --run  （IR 参考求值器，独立于解释器）
                               └── （下一步）各后端从 IR 下降
```

本版**没有**替换任何后端——替换是破坏性变更，按 `tools/api_baseline.json` 的规矩要先写 RFC。
这一版交付的是“地基 + 可执行的验收”，不是“已经换完内核”。

## 二、五阶段现状（哪个已接、哪个没接）

| 阶段 | 实现 | 状态 |
| --- | --- | --- |
| Lex | `impor/lexer.py`（55 关键字、软关键字归一） | 已有 |
| Parse | `impor/parser.py`（缩进/花括号两种块） | 已有 |
| Semantic | `impor/analysis.py`、`impor/typecheck.py`、`impor/lint` | 已有（不在 IR 路径上强制） |
| **IR** | `impor/ir.py`（指令/序列化/参考求值器）+ `impor/ir_lower.py`（AST→IR） | **4.4 新增** |
| Lowing | 解释器 / pytrans / ctrans / cfast / imporc | 仍各自 walk AST（未改成从 IR 下降） |

## 三、指令集（22 条）

三地址码风格：`目的操作数 ← 源操作数`，每条带 `行号`，文本里以 `; L<n>` 结尾。

| 指令 | 语义 |
| --- | --- |
| `CONST dst, v` | 字面量进临时量 |
| `LOAD dst, name` | 读变量（当前帧 → 外层帧 → 全局 → 函数 → 内建） |
| `STORE name, mode, v` | 写变量；`mode` ∈ `local / update / global / loop` |
| `MOVE dst, v` | 赋值（用于 and/or、三元的分支合流） |
| `BINOP dst, op, l, r` | `+ - * / % ** < <= > >= == != and or` |
| `UNOP dst, op, v` | `- + not` |
| `CALL dst, callee, args...` | 具名函数 / 函数值 / 内建 |
| `PRINT v...` | 按空格连接后输出一行（对齐 `visit_Print`） |
| `LIST dst, items...` / `DICT dst, k, v, ...` | 容器字面量 |
| `INDEX dst, c, i` / `SETINDEX c, i, v` / `SLICE dst, c, a, b, step` | 下标/切片 |
| `TOSTR dst, v` / `CONCAT dst, parts...` | 字符串插值与拼接 |
| `ITER dst, it` / `ITER_NEXT item, it, end` | 迭代器协议（`for` 用） |
| `LABEL` / `JUMP` / `JUMP_IF_FALSE` | 控制流（`if` / `while` / `for` / 短路） |
| `ASSERT v` | 断言失败 → `IRError` |
| `RETURN v` | 返回（函数末行的裸表达式按隐式返回处理，与解释器一致） |

## 四、覆盖矩阵（4.4 当前实测）

**支持**：变量声明/更新（`var`/`set`/`+=`）、`print`、`if/elif/else`、`while`（含 `break`/`continue`）、
`for`（`range(...)`、列表、字典）、顶层 `def`（递归、多参数、无参）、`return` 与隐式返回、
算术/比较/短路逻辑、三元、字符串拼接与 `{expr}` 插值、列表/字典字面量与下标读写、切片、
`assert`、函数值传递（把具名函数当参数传来再调用）、以及 `builtins.py` 里的纯函数内建
（`abs`/`div`/`len`/`strcut`/`strpad`/`strjoin`/`sum_arr`/`to_str` 等 22 个）。

**明确拒绝**（`UnsupportedNode`，带 `文件:行:列`）：

| 语法 | 原因（也是下一步的工作项） |
| --- | --- |
| `class` / `struct` / 继承 / `interface` | 类模型尚未在 IR 里建模 |
| `try`/`catch`/`raise`/`exception` | 异常边（landing pad）需要单独表示 |
| `import` / `from ... import` / `export` | 模块解析应在 IR 之前完成，IR 只描述单文件函数体 |
| 嵌套 `def`、`lambda` | 闭包对象没有 IR 表示 |
| `AttrGet` / `AttrSet`（含方法调用 `s.upper()`） | 依赖类/模块模型与接收者分发 |
| `match` / `defer` / `await` / `NewInstance` / `super()` / `\|>` / `exit` / `cls()` | 各自的语义还没定表示方式 |

`AttrGet` 是当前最大的缺口：`demo_20/22` 这类“字典方法 + 字符串方法”的示例因此被拒。

## 五、验收（本机实测）

```powershell
cd 'C:\Users\41482\Desktop\总Impor文件\关于Impor\Impor 4.0.0'
py -3 -m unittest tests.test_ir -v      # 12 项，1.1 s
```

| 项 | 结果 |
| --- | --- |
| `tests.test_ir` | **12/12 OK**（结构 4 项 + 对拍 3 项 + 拒绝 3 项 + CLI 2 项） |
| `examples/demo_01…demo_40` 对拍 | **27 个逐行一致 / 13 个显式拒绝 / 0 个不一致** |
| 内联语义用例 | 13 个（算术、elif、range 步长、while break/continue、递归、隐式返回、插值、切片、字典、短路、三元、高阶函数、assert）全一致 |
| `tools/api_baseline.py` | 一致：55 关键字 / 122 内建 / 20 模块 / 30 错误码（公开面未动） |
| `tools/check_fmt_all.py` | 220/221（`impor/std/http.impor` 未格式化，属并行改动，不在本次范围） |

**对拍当场抓到并修掉的一个真缺陷**：`and` / `or` 在 parser 里已把 `&&`/`||` 归一成 `and`/`or`，
但树遍历解释器 `visit_BinOp` 没有对应分支，`print(1 && 2)` 直接报 `OpError: 未知运算符 and`。
4.4 在 `visit_BinOp` 顶部补了短路求值（`x != 0 && 1 / x > 1` 不会先炸除零），
IR 求值器用同一语义，`tests.test_ir` 里锁了一条回归。

## 六、怎么用

```powershell
impor ir 程序.impor                     # 打印 IR 文本（含函数/指令/标签计数头部）
impor ir 程序.impor -o 程序.ir          # 落盘，方便和上一版 diff
impor ir 程序.impor --run               # 用 IR 参考求值器执行，输出应与解释器逐行相同
impor ir 程序.impor --run --stats       # 追加“函数/指令/标签/实际执行指令”统计
impor ir 不支持的.impor                 # 退出码 2，stderr 给出 文件:行:列 + 语法名 + 原因
```

示例（`demo_05_loops.impor` 的 `for` 循环片段）：

```
L_for_cond_1:
    ITER_NEXT  t3, t2, L_for_end_2                         ; L4
    STORE      i, loop, t3
    ...
    JUMP       L_for_cond_1                                ; L4
L_for_end_2:
```

## 七、下一步（按收益排序）

1. **`ctrans` 从 IR 下降**：先拿“纯数值 + 控制流 + print”这一段做 A/B 对拍（IR→C vs 现 ctrans→C），
   逐字节比 stdout。这一段是 C 后端最常被用的部分，收益最大、风险最小。
2. **补 `AttrGet` 的方法调用分发**：能立刻把 13 个被拒示例里的 4 个（20/22/19/21 之外）拉进来。
3. **异常边建模**：`try/catch` 需要 IR 的块参数与 landing pad，属于结构性工作，建议单独一版。
4. **`imporc` 对齐**：自举编译器是过程式子集，长期应改成“读 IR 文本”而不是自己再解析一遍源码。
5. **类模型**：`class/struct/inheritance` 需要对象布局与虚表，等 C 后端对象模型定稿后再进 IR。

## 八、复跑与证据

```powershell
py -3 -m unittest tests.test_ir
py -3 -m unittest tests.test_impor tests.test_v431_sync tests.test_v42_generics   # 200 项，确认改动无回归
py -3 tools\api_baseline.py
py -3 tools\check_fmt_all.py
py -3 Impor.py ir examples\demo_05_loops.impor
py -3 Impor.py ir examples\demo_02_arithmetic.impor --run --stats
```

代码入口：

- `impor/ir.py`：指令定义、`IRModule.render()`、`Evaluator`（参考求值器）
- `impor/ir_lower.py`：`lower_ast` / `lower_source` / `UNSUPPORTED_REASONS`
- `impor/cli.py`：`cmd_ir`、`_COMMANDS["ir"]`、`SUBCOMMAND_HELP["ir"]`
- `tests/test_ir.py`：结构、对拍、拒绝、CLI 四组
