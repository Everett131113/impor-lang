# Impor 4.9.0 发布说明

> 基线：4.8.0（性能门禁 / 两级编译 / WASM 第二后端 / 能力矩阵门禁）
> 本轮主题：把 4.8.0 报告里"未做"的项推进到有实测证据

## 1. 四条更新

### 1.1 WASM：f64 与字符串运算

4.8.0 的 WASM 只会整数。4.9.0 确立了值域约定（**所有值都放 i64，解释方式由编译期推断决定**）：

| 类型 | 槽内容 | 解码 |
| --- | --- | --- |
| int | 原样 i64 | — |
| float | f64 位模式 | `f64.reinterpret_i64` |
| str | `(ptr << 32) \| len` | 高 32 位指针 / 低 32 位长度 |

落地内容：

- 浮点：`f64.add/sub/mul/div/neg`、六种比较、`int → float` 隐式提升；新增 `env.print_f64`，打印口径与解释器一致（`3.0` 保留 `.0`、布尔打 `True`/`False`）。
- 字符串：`+`（`memory.copy` + bump 分配）、`*`（重复）、`==`/`!=`（逐字节）、`len()`（掩低 32 位）、`+=`；堆指针固定在全局索引 0（初值 131072），内存 4 页。

### 1.2 WASM：break / continue / 短路

- `ctrl` 块嵌套栈，`br` 立即数按"相对当前位置数几层"现算，不再人肉数缩进；`for` 的 `continue` 落点放在 `i += step` 之前。
- `and`/`or` 在**表达式层与条件层**都短路。条件层的短路是必需的：`if n != 0 and 10 / n > 1` 若右边被无条件求值，`n == 0` 就会除零 trap。
- 断言用例：`flow2.impor` 里 `1 == 2 and (10 / 0) == 0` 必须不 trap —— 实测通过。

### 1.3 浏览器 GUI 自动化验证

`impor wasm bench/suite/wasm_f64.impor -o demo.wasm --html demo.html` 产出的页面，用真实 Chrome 打开后渲染输出：

```
3.75
3.0
True
2.5
0.3333333333333333
-1.5

wasm 951 字节 · 函数 9 个 · 全局 5 个
```

与 Node 跑同一份 `.wasm` 逐行一致。为此补了 `--html` 模板里缺失的 `print_f64` 导入（4.8.0 的页面遇到浮点会直接 `Import #5 print_f64 requires a callable`）。

### 1.4 安装目录改名

`Impor 4.0.0` → `Impor 4.9.0`（目录名此前与版本号脱节），同步更新桌面 `Impor 编辑器.lnk` 的目标/参数/工作目录；`impor.bat` 用 `%~dp0` 相对定位，无需改。

## 2. 实测数字

### WASM 用例（12/12 MATCH，与解释器逐行比对）

| 用例 | 覆盖 | 产物 |
| --- | --- | --- |
| fib25 / sum2m / mod1m / nested1m | 递归、累加、`%`、双层循环 | 800 / 857 / 860 / 932 字节 |
| wasm_str | 字符串 print + 函数调用 + 全局 | 875 字节 |
| flow1 / flow3 | `for` + `continue`/`break`、`while` + `continue` | 876 / 821 字节 |
| flow2 | `and`/`or` 短路（含除零不 trap） | 875 字节 |
| wasm_f64 | 浮点四则、比较、一元负、打印口径 | 951 字节 |
| wasm_str2 | 拼接 / 重复 / 相等 / `+=` | 1051 字节 |
| caps 02_float / 03_str | 能力探针 | 817 / 861 字节 |

### 能力矩阵（10 探针 × 3 后端，零「错误」格）

| 后端 | 支持 | 明确拒绝 |
| --- | --- | --- |
| 解释器 | 10/10 | 0 |
| C 后端 | 10/10 | 0 |
| WASM | 3/10（`01_int` / `02_float` / `03_str`） | 7（容器/类/闭包/泛型/异常/`int()` 等） |

4.8.0 时 WASM 是 1/10；本轮 +2，且仍是"拒绝"而不是"算错"。

## 3. 未做（如实列出）

| 项 | 状态 | 原因 / 已给的东西 |
| --- | --- | --- |
| 8 字节 NaN-boxing 本体 | **未做** | Val 仍是 16 字节 tagged union。设计文档 `docs/nanboxing-design.md` 记录了三条路（48 位内联 + 溢出装箱 / 全装箱 / 低位 tag）与 895 个访问点的规模；本轮只做了整树备份（`D:\ImporWork\backup_490_pre_nanbox`，883 文件）与设计复核，**没有改表示**。真正的障碍是语义决策：int64 全范围与 64 位载荷里的 tag 位直接冲突，切换前要先定"整数是否允许 boxed 化"。 |
| Linux / macOS 运行证据 | **未做（本机物理不可得）** | 实测环境：`wsl` 存在但无发行版（`wsl -l -q` 返回 1）、`docker`/`qemu-*`/`wine`/`zig`/`podman`/`bash` 全部缺失。可执行证据链已就位：`.github/workflows/ci.yml` 里有 `ubuntu-latest` 编译 + 运行 ELF、`macos-latest` 用 clang 编译 + 运行、`tools/build_posix.sh --static`（musl）三步，推到 GitHub 即产出真实 Run 日志。本机只能给配置，不能给运行结果。 |

## 4. 复现命令

```powershell
python tools\wasm_batch.py                    # 12 个 WASM 用例：解释器产期望 → wasm 比对
python tools\capability_gate.py --check        # 能力矩阵与 docs/CAPABILITIES.json 比对
python tools\bench_gate.py --check             # 性能门禁（需 PATH 带 x86_64 gcc）
python D:\ImporWork\regress490.py              # 十项回归（约 900 s，日志 logs\progress490.txt）
python tools\pack_release.py                   # 打包
python tools\install490.py                     # 安装到 Impor 4.9.0 并核对清单
```

## 5. 本轮修掉的真 bug（都有实证）

1. `CompoundAssign` 的 `op` 是 `"+="` 带等号，而生成侧按 `"+"` 判断 → 所有复合赋值都走不到正确分支（4.8.0 未测 `+=` 所以没暴露）。
2. 全局索引重编号：插入 `__heap` 后沿用了 scan 里的旧索引，`a` 与堆指针撞到同一个槽 → `global.set[0] expected i32, found i64`。
3. `--html` 模板缺 `print_f64` 导入 → 浏览器里浮点用例直接 link error。
4. 条件层与表达式层的 `i32/i64` 收口混淆（`i64.extend_i32_u` 加错了层），在 `flow1-3` 与 `str2` 上各暴露一次。
5. `print(a > b)` 在 WASM 输出 `1`、解释器输出 `True` —— 口径差异，已在 `_print` 里按布尔表达式生成 `True`/`False` 字面量。
6. 安装目录改名漏改 `IconLocation`：桌面 `.lnk` 的图标还指着已删除的 `Impor 4.0.0\ImporPortable.exe,0`，图标显示成白纸。已改指 `Impor 4.9.0\ImporPortable.exe,0`，并把"目标/参数/工作目录/图标/描述"五处的对齐写进 `tools\install490.py` 的 `fix_shortcut()`，以后重装不会再漏。
