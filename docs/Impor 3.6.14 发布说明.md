# Impor 3.6.14 发布说明

**发布日期：2026-11-15**

3.6.14 只做一件事：**打破自举死锁，让 `test_selfhost_closure` 真正通过并从 `LEGACY_IDS` 移出。** 本版不改变语言语法，不新增/删除关键字，不调整诊断文案。

## 问题是什么

3.6.13 的 `test_selfhost_closure` 不是闭包语义不对，而是 Step 1 就超时：

```
subprocess.TimeoutExpired: ...\impor\imporc.exe timed out after 300 seconds
```

`bootstrap/selfhost_check.py` 验证的是自举闭环：现有 `imporc.exe` 编译 `bootstrap/imporc.impor` 得到 B，B 再编译种子得到 C，C 再编译种子得到 D，要求 B/C/D 对同一份种子产出的 C 代码逐字节一致。

2026-10-31 生成的旧 `impor/imporc.exe`（385,536 B）面对已增长到 86KB 的新种子，超过 900 秒仍不返回。旧二进制编译不了新种子，新二进制又只能靠旧二进制产出，形成死锁。

## 怎么解的

### 1. 用 Python C 后端重建 A

新增 `bootstrap/build_with_python.py`，直接用仓库的 Python 前端 + tcc 从当前种子生成新的 A：

```powershell
python bootstrap/build_with_python.py
```

脚本处理两处兼容点：

- 种子字符串字面量里的 `{` `}` 是 C 代码文本，Python 前端会当成插值；生成临时构建副本时转义为 `\{` / `\}`，词法分析后再还原。
- 种子源码第 2051 行变量名 `local` 与 Python 前端保留字冲突，已改名为 `localname`。

生成结果：

- `bootstrap/imporc_py.exe`：588,288 B；
- 构建耗时 0.52s（转译 0.23s / tcc 0.08s）；
- 自动把 PE `SizeOfStackReserve` 从 1MB 补到 8MB，避免扫描器递归导致 `0xC00000FD` 栈溢出。

### 2. 修 C 运行时的 O(n²)

A 最初能启动，但 86KB 种子在 300 秒内跑不完。定位到 `impor/c_runtime.c`：

- `rt_strcut` 每次调用都执行 `rt_u8len(s)` 从字符串头扫到尾部，再用 `rt_u8_at` 从头扫到目标字符下标；
- 种子编译器 `scan()` / `gen_block()` 大量执行 `strcut(gsrc, gpos, gpos+1)`，`gsrc` 是 86KB 全文，累计 O(n²)。

修复：

- 新增同一字符串“字符下标 → 字节偏移”顺序缓存，顺序切片退化为 O(1)；
- `rt_add` 的双字符串拼接不再对两个操作数各做一次 `rt_tostr()` 全量复制，只分配并复制一次结果。

效果：同一份完整种子，A 的编译时间从 **600 秒以上** 降到 **52.56 秒**。

### 3. 重新生成并验证闭环

用 A 生成新的 `impor/imporc.exe`（v3.6.14，417,280 B），执行：

```powershell
python bootstrap/selfhost_check.py
```

结果：

```text
✓ A（imporc.exe）编译种子 → B（imporc_self.exe）
✓ B 编译种子 → C（out2.exe）
  B 产物 out2.c：240975 字节，112 个函数
✓ C 编译种子产物与 B 逐字节一致（自举成立）  (234686 字节)
✓ D 编译种子产物与 C 逐字节一致（跨代稳定）

自举闭环： ✅ 全部通过 —— B/C/D 三代产物逐字节一致，编译器已用自身语言实现
```

## 测试

- `run_tests.py` 的 `LEGACY_IDS` 从 6 条降为 5 条，`test_v38.TestImporcSelfHost.test_selfhost_closure` 已移出，默认 suite 实际执行。
- 默认 suite：**567 tests / OK / 408.230s**（含自举闭环，仓库路径实测）；
- 桌面中文/空格/括号路径：**567 tests / OK / 364.178s**（最终包实测）。
- 版本一致性：`VERSION`、`Impor.py`、`impor/__init__.py`、`vscode-impor/package.json`、`bootstrap/imporc.impor` 全部为 3.6.14。

## 交付物

- `impor/imporc.exe`：v3.6.14，417,280 B；
- `bootstrap/imporc_py.exe`：Python 后端重建的 A，588,288 B；
- `bootstrap/build_with_python.py`：可复现的重建入口；
- 桌面目录：`C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.14`；
- ZIP：`Impor-3.6.14-Windows-x64.zip`；
- VSIX：`vscode-impor-3.6.14.vsix`，482,362 B，
  SHA256 `C3198F000EA6D65BA3E6993BF084A5E0E2133BA08A645C038A68D5575C25B4B5`。

## 边界

- 3.6.14 不改语言语义，不混入其他收敛项。
- `rt_strcut` 缓存按“同一字符串指针 + 首尾字节”识别；编译器对 `gsrc` 的连续切片是主要收益路径。若后续引入并发或字符串缓冲区复用，应改为显式长度元数据，而不是继续依赖指针缓存。
- `impor/ccomp.c` 仍是 v2.14 手写 C 编译器，本版未将其作为引导编译器。
