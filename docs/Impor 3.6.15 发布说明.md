# Impor 3.6.15 发布说明

**发布日期：2026-11-15**

3.6.15 不加语言语法，也不加编辑器功能。它只做一件事：**让 Impor 写出第一个作者自己每天真会双击使用的小工具，并把它打成不依赖 Python 的 Windows 单文件 exe。**

这一版的判断标准不是“又补了哪个关键字”，而是：

- 双击 exe 能弹出窗口，不弹控制台；
- 按空格能开始计时，倒计时真的在走；
- 关掉窗口后，今天的记录还在；
- 写的过程中卡住的地方，被如实记下来，作为下一版要修的东西。

## 为什么是“专注计时器”

没有选文件整理器或批量重命名，是因为它们需要目录遍历、文件移动、批量确认这些能力；当前 C 后端明确不支持目录遍历，Tk GUI 也只能走 Python 后端。硬做会把这一版变成“补后端能力”，又回到堆功能的路上。

专注计时器直接命中两个最缺的东西：

1. **每天真会用**：写作业、看书时开 25 分钟，休息 5 分钟；
2. **真 GUI**：窗口、按钮、输入框、列表、快捷键、置顶、关闭保存，都是 Windows 小工具的基本期待。

它不需要目录遍历，也不需要托盘，能在现有能力边界内真正跑通。

## 工具做了什么

源码：`apps/impor_focus/focus.impor`（约 16 KB，纯 Impor 编写）

- 25 / 5 分钟专注与休息，可改成 1–120 分钟；
- 开始、暂停、继续、重置、跳过；
- 给这一轮起任务名，默认“作业”；
- 今日专注总分钟数、完成段数、最近 8 条记录；
- 窗口置顶；
- 空格开始/暂停，Esc 重置；
- 关闭窗口自动保存；
- 数据存 `%USERPROFILE%\.impor_focus\history.json`。

界面是深色卡片 + 琥珀色大号倒计时，窗口 `580x600+60+40`，在 125% 缩放的 768p 屏幕上完整可见。

## 打包时真正卡住的地方

### 1. exe 能打出来，但双击报 `fileexists` 未定义

第一次 PyInstaller 构建成功，产物 11,447,301 B（补上运行时函数后最终为 11,448,197 B），但双击后弹出：

```text
Unhandled exception in script
name 'fileexists' is not defined
  File "_focus3615.py", line 60, in data_dir
    if not fileexists(dir)
```

根因不是 `focus.impor` 写错，而是生成代码的运行时边界：

- `impor compile` 生成的 Python 头部只有 `from impor.runtime import *`；
- `fileexists`、`filemkdir`、`fileread`、`filewrite`、`json_encode`、`json_decode` 这些名字，原先只由解释器在运行时注入 `builtins` 注册表；
- 直接在仓库里 `impor run` 能跑，是因为解释器在；PyInstaller 只收集 `impor.runtime`，不会收集解释器的动态内建注册。

修复：把这六个函数以同名同义补进 `impor/runtime.py`，并加入 `__all__`。生成代码在脱离解释器后也能拿到它们。这不是新语法，是打包必需的运行时导出修复。

修复后重新 `compile`、重新 PyInstaller，exe 双击通过。

### 2. `--specpath` 会让相对 icon 路径失效

第一次构建还遇到：

```text
FileNotFoundError: Icon input file ...\_build_focus3615\impor/assets/icons/impor.ico not found
```

`--specpath _build_focus3615` 之后，PyInstaller 以 spec 目录为基准解析相对路径。构建命令里必须用绝对 icon 路径：

```powershell
$icon = (Resolve-Path impor\assets\icons\impor.ico).Path
python -m PyInstaller --onefile --noconsole --noconfirm --clean `
  --name "Impor专注计时器" --icon $icon `
  --distpath apps\impor_focus --workpath _build_focus3615 `
  --specpath _build_focus3615 _focus3615.py
```

## 写工具时暴露的 Impor 使用摩擦

完整记录在 `apps/impor_focus/README.md`，这里只列结论：

1. **Tk 字体族不能带空格直接写**：`"Segoe UI 9"` 被 Tcl 拆成族 `Segoe`、字号 `UI`；`"{Segoe UI} 9"` 的花括号又被 Impor 字符串/插值层吃掉。最后只能用 `"Arial 9"`、`"Consolas 46 bold"` 这类无空格族名。
2. **`global set` 必须逐条写**：函数里改多个全局时，每条都要 `global set`，漏一条会静默变局部或报未定义。
3. **打包后解释器内建不在 `impor.runtime` 里**：即上面 `fileexists` 的坑，已修。
4. **Tk 依赖只能在 Python 后端**：C 后端不支持 Tk，`imporc.exe` 不能参与 GUI 交付。
5. **`impor compile --exe` 不会自动加 `--noconsole`**：要“双击弹窗、无控制台”，必须手动再跑 PyInstaller。
6. **窗口高度要考虑 DPI 缩放**：`580x720` 在 125% 缩放的 768p 屏幕上底部会出屏，改成 `580x600` 并固定左上角后才完整。

这些是第一个真实用户才会遇到的摩擦，不是“再加三个语法”能解决的。

## 验证

### 自检与 GUI 冒烟

```text
SELFTEST OK
GUI SMOKE OK
```

覆盖 `fmt_time(1500)=25:00`、`00:59`、负数归零、`59:59`、今日分钟数/段数、JSON 往返，以及实际的“开始 → 暂停”窗口流程。

### 打包 exe 实测

- 双击 `apps\impor_focus\Impor专注计时器.exe`，窗口正常出现，标题 `Impor 专注计时器 · 3.6.15`；
- 按空格开始，按钮从“开始”变“暂停”，状态变“计时中”，倒计时从 `25:00` 走到 `24:57`；
- Alt+F4 关闭后生成 `%USERPROFILE%\.impor_focus\history.json`；
- 单文件 exe **11,448,197 B**，无控制台，带 `impor.ico`；SHA256 `256CD196A0E1C6728B3607820E3916390F44C525C37DFD30C374A5ECB06B83C7`。

### 自举闭环

版本号升到 3.6.15 后，重新用 `bootstrap/build_with_python.py --install` 生成 A 并安装为 `impor/imporc.exe`，再跑 `bootstrap/selfhost_check.py`：

```text
✓ A（imporc.exe）编译种子 → B（imporc_self.exe）
✓ B 编译种子 → C（out2.exe）
  B 产物 out2.c：240975 字节，112 个函数
✓ C 编译种子产物与 B 逐字节一致（自举成立）  (234686 字节)
✓ D 编译种子产物与 C 逐字节一致（跨代稳定）

自举闭环： ✅ 全部通过 —— B/C/D 三代产物逐字节一致，编译器已用自身语言实现
```

### 回归测试

新增 `tests/test_v3615.py`，三条无窗口用例：

- 工具源码存在且声明了 `--selftest` / `--smoke-gui`；
- 解释器运行 `--selftest` 退出码 0；
- 转译后的 Python 也能跑 `--selftest`，专门防止 PyInstaller 环境下运行时内建缺失回归。

全量 suite 在 3.6.15 代码上跑完：**570 tests / OK / 547.144s**（比 3.6.14 的 567 条多出本版 3 条；自举闭环仍在 suite 内，因此耗时较长）。

## 交付物

- 桌面目录：`C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.15`
- ZIP：`C:\Users\41482\Desktop\总Impor文件\Impor-3.6.15-Windows-x64.zip`
- VSIX：`C:\Users\41482\Desktop\总Impor文件\vscode-impor-3.6.15.vsix`
- 桌面快捷方式：`C:\Users\41482\Desktop\Impor (I++) 3.6.15.lnk`
- 工具源码与说明：`apps/impor_focus/focus.impor`、`apps/impor_focus/README.md`
- 工具 exe：`apps/impor_focus/Impor专注计时器.exe`（同时进入桌面目录与 ZIP）

## 边界

- 单文件 Tk exe 约 11.4 MB，不是 C 后端 108 KB 的量级。它换来的是“没装 Python 的 Windows 也能双击运行”，这一版不再为体积牺牲 GUI。
- 没有最小化到托盘；关闭就是退出并保存。
- 没有系统通知，只有 Tk `bell()`。
- 记录只存本地 JSON，没有云同步、多设备。
- 没有长休息（每 4 轮 15 分钟），先用一周再决定。
- `imporc.exe` 仍是 C 后端编译器，能参与自举，但不能编译这个 Tk GUI。

## 这一版刻意没做

- 没有新增任何语言语法、关键字、编辑器开关或性能优化；
- 没有做包生态、GitHub 依赖、新手从零走查、托盘；
- 没有为了让 exe 变小而退回控制台工具。

下一版该做什么，以这周真实使用中再次卡住的地方为准，而不是继续凭想象补功能。
