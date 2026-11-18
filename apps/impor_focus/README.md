# Impor 专注计时器

这是 Impor 3.6.17 的“第一个真实用户工具”，不是演示，也不是语法样例。

目标只有一个：作者每天写作业/看书时真的会双击打开它，用一个 25 分钟专注、5 分钟休息的计时器，并且第二天还能看到昨天的记录。

## 3.6.17：一次真实 25 分钟跑出来的四个摩擦

15:58:18 用真实 `clock()` 开始 25 分钟，任务名“3.6.17 真实专注”，到 16:23:18 走完整个完成路径并写入 1 条记录。这次跑的是 3.6.16 代码副本、隔离在 `_dogfood25b_home`，所以拿到的是“真用旧版会碰到什么”；3.6.17 自身的完成路径另用 1 分钟段验证。这次不是看一眼代码，是让窗口真的跑满一轮。四个实际摩擦：

1. **专注时只看到 `24:59`，不知道几点结束**：25 分钟要自己在脑子里加，切回来时还得重新算。现在计时卡片右侧固定显示“结束 HH:MM”；暂停后显示“还剩 mm:ss”。
2. **完成时窗口不回来，只有一声 `bell()`**：如果人正在别的窗口里，声音很容易被忽略，回头才发现已经过了几分钟。现在完成时会把窗口从最小化恢复、提升到前面并临时置顶 5 秒；5 秒后恢复用户原来的置顶设置。没有加托盘、系统 Toast 或后台常驻。
3. **关掉窗口会丢掉当前这一轮**：实测运行 8 秒后关闭，`history.json` 仍是空的，只有完成的轮次才算记录。现在关窗会把任务名、剩余秒数、专注/休息设置写进 `state.json`；重新打开显示“上次未完成 · 任务 · 还剩 24:52”，按钮变成“继续”。
4. **第二天打开只看到“今天还没有记录”**：昨天的分钟数、段数、最后一轮任务名全部消失。现在统计卡固定显示“昨天 X 分钟 · Y 段 · 最后一轮 任务 N 分钟”；今天没有记录时，历史列表直接列出昨天最多 3 条，前缀“昨天”。

## 它做什么

- 25 / 5 分钟专注与休息，可改成 1–120 分钟
- 开始、暂停、继续、重置、跳过；专注中显示结束时刻
- 给这一轮起一个任务名（默认“作业”），开始后锁定到这一轮，完成后写入记录
- 今日专注总分钟数、完成段数、最近 6 条今日记录
- 昨天总分钟/段数/最后一轮任务；今天无记录时直接列出昨天最近 3 条
- 完成时把窗口带回前面，临时置顶 5 秒
- 置顶窗口
- 空格开始/暂停，Esc 重置
- 关闭窗口保存已完成记录，并保留当前未完成一轮，下次可继续
- 记录存放在 `%USERPROFILE%\.impor_focus\history.json`，未完成状态在 `state.json`

## 运行

在仓库根目录：

```powershell
D:\编程软件\python3.11\python.exe Impor.py run apps\impor_focus\focus.impor
```

无窗口自检：

```powershell
D:\编程软件\python3.11\python.exe Impor.py run apps\impor_focus\focus.impor --selftest
```

输出 `SELFTEST OK` 表示格式化、统计、JSON 往返都正常。

隐藏窗口的 GUI 冒烟测试（会实际走一遍“开始 → 暂停”）：

```powershell
D:\编程软件\python3.11\python.exe Impor.py run apps\impor_focus\focus.impor --smoke-gui
```

输出 `GUI SMOKE OK`。

## 构建 Windows 双击程序

```powershell
D:\编程软件\python3.11\python.exe Impor.py compile apps\impor_focus\focus.impor -o _focus3617.py
$icon = (Resolve-Path impor\assets\icons\impor.ico).Path
D:\编程软件\python3.11\python.exe -m PyInstaller --onefile --noconsole --noconfirm --clean --name "Impor专注计时器" --icon $icon --distpath apps\impor_focus --workpath _build_focus3617 --specpath _build_focus3617 _focus3617.py
```

`--icon` 必须传绝对路径：`--specpath` 会把相对路径按 spec 目录解析，容易报 `Icon input file ...\_build_focus3616\impor/assets/icons/impor.ico not found`。

产物是 `apps\impor_focus\Impor专注计时器.exe`，双击即可打开窗口，不弹控制台。已实测双击启动、按空格开始、倒计时从 `25:00` 走到 `24:57`、Alt+F4 关闭后生成 `%USERPROFILE%\.impor_focus\history.json`。

Tk 单文件 exe 大约 11–12 MB。它换来的是一台没装 Python 的 Windows 也能直接双击运行；这不是 C 后端 108 KB 的量级，但小工具的体积优先级低于“真能打开一个窗口”。

## 这一版暴露/确认的 Impor 使用问题

写这个工具时真实卡住或绕开的地方，按出现顺序：

1. **Tk 字体族带空格（3.6.16 已修在转译层）**：`"Segoe UI 9"` 仍会被 Tcl 解释成字体族 `Segoe`、字号 `UI`，这是 Tcl 语法，Impor 不该替它改；要写 `"{Segoe UI} 9"`。3.6.15 时 `{Segoe UI}` 会被字符串插值层当成变量 `Segoe`，报 `变量 Segoe 未定义`。3.6.16 起规则改为：`{…}` 里能解析成完整 Impor 表达式才算插值，解析不了就整段当字面文本。因此 `"{Segoe UI} 9"` 现在直接可用，`"\{Segoe UI\} 9"` 旧写法也保留；`"{size}"` 仍是插值。本工具的 UI 已全部改用 `{Segoe UI}`，不再退回 `Arial 9`。
2. **`global set` 必须逐条写**：函数里改多个全局（`running`、`remaining`、`deadline`、`mode`）时，每条赋值都要 `global set`，漏一条就静默变成局部变量或报未定义；写 GUI 状态机时很容易漏。
3. **打包后内建缺失（3.6.16 已改为单一注册源）**：PyInstaller 只收集 `impor.runtime`，而 `fileexists`、`filemkdir`、`fileread`、`filewrite`、`json_encode`、`json_decode` 原先只由解释器在运行时注入，不在该模块命名空间。第一次打出的 exe 双击就报 `NameError: name 'fileexists' is not defined`。3.6.15 是把六个函数补进 `impor.runtime` 止血；3.6.16 的根因修法是新增 `impor/builtin_manifest.py` 作为唯一内建注册表：`impor.runtime` 导入时自检所有 `RUNTIME_BUILTINS`，缺一个直接 `ImportError`；`make_builtins()` 返回前与 `ALL_BUILTINS` 对账，解释器启动就暴露。以后新增内建忘登记或忘实现，会在测试/打包阶段失败，不会拖到用户双击 exe。
4. **Tk 依赖只能在 Python 后端（定位认知，不是本版要修的 bug）**：C 后端做不了 GUI，也不能打包 Tk；因此这个工具只能走 Python 后端 + PyInstaller，`imporc.exe` 无法参与交付。3.6.16 明确接受“Windows 小工具”分裂成两类，不要求现在给 C 后端加 Tk 绑定：

   | 类型 | 后端 | 体积 | 适合 |
   | --- | --- | --- | --- |
   | 控制台工具 | C 后端 `imporc` | 约 108 KB | 批量处理、脚本、CI、管道 |
   | GUI 工具 | Python + PyInstaller | 约 11 MB | 日常双击应用、Tk 界面 |

   两者差约 100 倍。GUI 用户通常不介意 11 MB；作者必须知道这是两条交付路径，不能拿 108 KB 去要求 GUI 工具。
5. **GUI 打包不是 `--exe` 自动完成的**：`impor compile --exe` 只会调 PyInstaller 的 `--onefile`，不会加 `--noconsole`；要交付“双击弹窗、无控制台”的程序，必须手动再跑一次 PyInstaller。这一点已经写进上面的构建命令。
6. **窗口高度要考虑 DPI 缩放**：`580x720` 在 125% 缩放的 768p 屏幕上会把底部记录区推到屏幕外；改成 `580x600` 并在左上角定位后才完整可见。

这些不是“再加三个语言特性”能解决的，是第一个真实用户才会遇到的摩擦。3.6.16 修了前两条真 bug（Tk 花括号、内建双注册源）；3.6.17 的四个修改全部来自上面那轮真实 25 分钟，不是代码审计，也没有新增语言特性。`global set`、`--noconsole`、DPI 三条保持文档说明。

## 已知边界

- 没有最小化到托盘；关闭就是退出，但会保留当前未完成一轮。
- 没有系统通知/声音，只有 Tk 的 `bell()`；完成时靠把窗口带回前面提醒。
- 记录只存本地 JSON，没有云同步、没有多设备。
- 没有番茄钟长休息（每 4 轮 15 分钟）——这一版仍然刻意不做，等真实使用证明需要。
- 单文件 exe 约 11–12 MB，启动比 C 后端工具慢；这是 GUI 工具路径的已知代价。

## 这一周怎么用

3.6.17 的输入不是“再看一遍源码”，是真实跑满了一轮 25 分钟：知道结束时刻缺什么、完成后窗口不回来、关窗会丢当前一轮、第二天看不到昨天。接下来仍然每天用它，记录新的摩擦；托盘、系统通知、长休息、云同步继续不做，直到真实使用证明需要。
