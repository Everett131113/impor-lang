# patch8b 验收：四条局限 → 逐条实测

- 日期：2026-11-22
- 版本：Impor (I++) 4.0.0（`VERSION` 为唯一真源）
- 机器：Windows x64 / Python 3.11.5 / gcc 4.8.1（TDM64，`-O2 -std=gnu99`）

四条都是「拿去用」撞出来的，不是设计里的假设。每条下面写的是**怎么复现、改了什么、实测结果**。

---

## ① `imporc` 不支持 v4 标准的花括号块

**复现**：`if (x > 1) { … }`、`def f(n) { … }`、`while (…) { … }` 一律在闭合 `}` 上报
`imporc.impor : 第 4 行: 无法识别的语句 [ } ] type= 4 pos= …`；而 `imporc.impor:869` 的注释自称 v3.8.3 已支持。
诊断文件名还永远打 `imporc.impor`，与实际脚本无关。

**改法**（`bootstrap/imporc.impor`）：

- 新增 `mi_lead` / `mi_rtrim` / `mi_has_brace_style` / `mi_brace_to_indent`，在词法之前把「花括号写法」整段归一化成缩进写法。
  缩进 = **原行缩进 + 已开花括号层数 × 4**，因此文件自己的缩进风格被保留；`{` 换成块头、`}` 删行退层。
  纯缩进文件由 `mi_has_brace_style` 判否后**原样返回，一行不改**。
- 40 处 `print("imporc.impor", ": 第" …)` 改成 `print(gname, …)`，`gname` 在读到 `gscript` 后赋值为真实脚本路径。

**实测**（9 例，全部 `impor/imporc.exe run`）：

| 用例 | 内容 | 输出 |
|---|---|---|
| b1 | 顶层 `if (x>1) { print("A") }` | `A` / `done` |
| b2 | `def f(n) { return n }` + `print(f(7))` | `7` |
| b3 | `def fib(n)` 体内嵌花括号 `if` + `print(fib(10))` | `55` |
| b4 | 全花括号 `def fib` + `print(fib(10))` | `55` |
| braces | 花括号版 `fib(25)` | `75025` |
| f_indent | 纯缩进版 `fib(25)`（回归） | `75025`（与改前一致，证明没动缩进路径） |
| c1 | `def` + 嵌套 `if`（花括号） | `55` |
| c2 | `while` + `if / else` 链 | `0 1 two 3 4 end` |
| c3 | 块内注释 | `1` |

诊断示例：`b2.impor : 已生成  out2.exe` —— 报的是真实文件名，不再是 `imporc.impor`。

**自举闭环**（`python bootstrap/selfhost_check.py`）：A→B→C→D 全部通过，B/C/D 三代产物**逐字节一致**
（`277298` / `267872` 字节，120 个函数）。

---

## ② README 写 `impor serve run`，CLI 不认

**复现**：`impor serve run <脚本>` 只打印用法。

**根因**：`impor/cli.py` 的 `cmd_serve` 在参数循环里把所有位置参数当「未知参数」跳过，循环结束时位置参数已被清空，
`sub == "run"` 分支拿不到脚本。

**改法**：循环里收集 `pos`；`run` 用 `pos[0]` 当脚本、`pos[1:]` 当参数；只对以 `-` 开头且非 `--port/--bg/-h` 的开关报错。

**实测**：`impor serve run <百万循环脚本>` 输出 `499999500000`；`impor serve run`（不带文件）正常打印用法，不再吞错。

---

## ③ `impor syntax` 与 README 两套数字，且查不到「管道」

**复现**：`impor syntax` 打出 122 条（79 关键字 + 43 内建），其中 12 个是 4.0 已物理删除的历史写法
（`func` / `loop` / `let` / `local` / `until` / `do` …）；README 首屏是 55 保留字 / 101 内建。
`syntax 管道`、`syntax |>` 都回「没有找到语法条目」，而管道是宣传的差异化特性。

**改法**（`impor/syntax_ref.py`）：

- 以真源 `tools/api_baseline.json`（README 首屏数字也由它计算）对齐：删除过期项、补齐缺失项，
  自动收录条目的文案如实写「自动收录，说明待补」，不编造说明。
- 新增别名 `管道 / 管道运算符 / pipe → |>`，并手写 `|>` 运算符条目（含语义边界说明）。
- `exit` 既是关键字又是内建，字典键只能占一个身份，导致「内建 100 永远差基线 1」；
  现在给条目记 `extra_kinds`，显式双身份，`impor syntax` 计数改按分类求和。

**实测**：

```
Impor 语法查询：157 个关键字/内建条目
关键字（55）
内建函数（101）
运算符（1）   |>
```

`syntax 管道` → 正确返回 `|>` 条目；`syntax exit` → 关键字条目，并注明「它同时是内建函数」。
157 = 55 + 101 + 1，与 README 首屏同一口径，不再打架。

---

## ④ 公开面：官网仍挂 3.2.3

**事实**：`site/Impor官网.html` 是 3.2.3 的技术专题存档（页面自己写着「本页是 3.2.3 存档，不是当前版本说明」），
公网 tiiny.site 上挂的就是它，而当前版本是 4.0.0。

**改法**：新增 `site/Impor官网-4.0.0.html` —— 只放本版事实：跨语言同口径实测表、四条修复记录、能力边界、
拿包路径。单文件自包含（无外部字体/JS/CSS 依赖），可直接静态托管。

**未做**：tiiny.site 的替换需要该站账号，本机没有凭据，因此公网页面**没有**被替换；
这一步只能由持有账号的人上传（包里的 HTML 即待传文件）。

---

## 顺手修掉的两个发布链静默 bug

这两个不修，上面 ① 的产物会直接跑不起来：

1. **PE32+ 栈补丁偏移写错**：`bootstrap/build_with_python.py` 与 `bootstrap/selfhost_check.py` 对 PE32+ 用 `opt + 0x30`
   作 `SizeOfStackReserve` —— 那是 `SectionAlignment`。写坏头部后新 exe 报
   「不是有效的应用程序」/ `Access is denied.`，看起来像权限或占用问题，实际是 exe 被改坏。
   PE32 与 PE32+ 的 `SizeOfStackReserve` 都是 `opt + 0x48`（宽度 4 / 8 字节），已改回。重建日志：
   `stack: 0x020b 2097152 -> 8388608`。
2. **归一化把块头自己也缩进一级**：第一版 `mi_brace_to_indent` 先加深度再算缩进，顶层 `if` 因此报
   「意外的缩进开始」。改为先按当前深度定本行缩进、再提升后续行深度。

---

## 门禁（全部在本轮修复后跑的）

| 门禁 | 命令 | 结果 |
|---|---|---|
| unittest 五套 | `python -m unittest tests.test_v42_native_run tests.test_v42_fast_path tests.test_v41_cfast_for tests.test_v41_nonlocal tests.test_v43_serve_and_full` | **35 tests OK**（25.2 s） |
| 三路一致性 | `python conformance/run_conformance.py` | **100 / 100**，失败 0 |
| 数值快路径对拍 | `cmp_fast_vs_interp.py cases gcc` | **24 / 24** |
| 完整 C 后端对拍 | `cmp_full_vs_interp.py cases gcc --no-fast` | **24 / 24** |
| 格式门禁 | `python tools/check_fmt_all.py` | **207 / 207**（跳过 21） |
| 承诺兑现 | `python tools/verify_claims.py` | **22 / 22** |
| 错误诊断审计 | `python tools/error_audit.py` | **20 / 20** |
| 自举闭环 | `python bootstrap/selfhost_check.py` | A→B→C→D 三代逐字节一致 |

一致性这一项值得单独说：改前 README 挂的是「`imporc` 只过 16 例中的 7 例」，现在是**全量 100 例三路径逐字节相同**。
原因不是补了几个内建，而是花括号归一化让 imporc 能吃下同一份用例源码。

## 边界（照实写）

- `imporc` 仍是过程式子集：管道 `|>`、部分较新内建不在映射表里，遇到明确报错，不静默改语义。
- 花括号归一化只处理「行尾 `{` + 行首 `}`」：同一行里写多个语句（`{ print(1); print(2) }`）仍不支持。
- 归一化是文本层处理，不理解字符串/注释里的 `{` `}`；纯缩进文件不受影响，混合写法的极端边界未覆盖。
- 公网官网页未替换（无 tiiny.site 凭据）。
