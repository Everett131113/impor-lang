# Impor 3.6.16 发布说明

**发布日期：2026-11-15**

3.6.16 不是功能版，也不加新语法。它只修 3.6.15 第一个真实用户工具暴露的两处**架构 bug**，并把“哪些问题只写文档、不现在改”明确记录下来。

## 1. Tk 字体花括号：从“退回 Arial 9”到转译层原样传递

3.6.15 写 `apps/impor_focus/focus.impor` 时，`"{Segoe UI} 9"` 会报：

```text
NameError: 变量 Segoe 未定义
```

根因有两层：

1. 词法层把任意 `{…}` 都当成插值；
2. 解析层对插值段只调一次 `.expr()`，`"{Segoe UI}"` 解析出 `Segoe` 后把 `UI` 留在原地，旧代码不检查残留，于是花括号被悄悄吞掉，生成 `f'{Segoe} {size}'`。

3.6.16 的规则：

- `{…}` 必须能解析成**完整** Impor 表达式，且除换行/EOF 外不能有残留，才算插值；
- 解析失败就整段按字面文本处理，`{Segoe UI}` 原样保留；
- 转译器再把文本段的每个 `{` / `}` 翻倍为 `{{` / `}}`，保证生成的 Python f-string 合法；
- `"\{Segoe UI\} 9"` 旧转义写法继续可用；
- `"{size}"`、`"你好 {name}"` 仍是正常插值。

因此 Tk 的字体、颜色、布局、bind 参数里的 `{...}` 可以原样写，不需要退回 `Arial 9`。本工具的 UI 已全部改成 `{Segoe UI}`，真实 dogfood。

## 2. 内建注册：从“两份名单”到唯一注册表

3.6.15 打包后的 exe 报 `NameError: name 'fileexists' is not defined`。当时把六个函数补进 `impor.runtime` 只是止血；根因是：

- 解释器用 `impor.builtins.make_builtins()` 注入内建；
- 转译产物用 `impor.runtime.__all__`；
- 两份名单手工同步，新增内建必然再漏。

3.6.16 新增 `impor/builtin_manifest.py` 作为唯一注册表：

| 分类 | 数量 | 含义 |
| --- | --- | --- |
| `RUNTIME_BUILTINS` | 58 | 编译产物通过 `from impor.runtime import *` 必须能拿到 |
| `HOST_BUILTINS` | 9 | Python 宿主直接提供（print/len/int 等） |
| `INTERPRETER_ONLY_BUILTINS` | 27 | 依赖解释器状态、FFI、沙盒等，编译产物不承诺提供 |

强制校验：

- `impor.runtime` 导入时逐个检查 `RUNTIME_BUILTINS` 是否存在且可调用，缺一个直接 `ImportError`；
- `make_builtins()` 返回前与 `ALL_BUILTINS` 对账，缺/多都 `RuntimeError`；
- 编辑器高亮用的 `BUILTIN_NAMES` 也从 `ALL_BUILTINS` 派生，不再手工维护第二份；
- `tests/test_v3616.py` 固定这三条约束。

以后新增内建忘记登记或忘记运行时实现，会在解释器启动/测试/打包阶段失败，而不是等用户双击 exe。

## 3. 明确不做的三件事

### GUI 体积分裂：108 KB vs 11 MB

- 控制台工具：C 后端 `imporc`，约 **108 KB**；
- GUI 工具：Python + Tk + PyInstaller，约 **11 MB**。

差约 100 倍。本版**不给 C 后端加 Tk 绑定**；这是两条交付路径，不是同一个“Windows 小工具”定位。GUI 用户通常不介意 11 MB，作者必须知道差异。

### 使用摩擦只写文档

- `global set` 必须逐条写；
- `impor compile --exe` 不会自动加 `--noconsole`，GUI 交付要手动 PyInstaller；
- 窗口高度要考虑 DPI 缩放。

这些不改语言，写进 `apps/impor_focus/README.md`。

### 番茄钟功能仍然刻意不做

没有托盘、没有系统通知、没有长休息、没有云同步。3.6.16 不靠“想象需求”加功能；这一周继续每天真用，哪里不爽就是下一版的唯一输入。

## 4. 验证记录

| 项目 | 结果 |
| --- | --- |
| `tests/test_v3616.py` | 5 tests / OK |
| 全量 suite | 575 tests / OK / 618.237s |
| focus `--selftest` | `SELFTEST OK` |
| focus `--smoke-gui` | `GUI SMOKE OK` |
| 打包 exe `--smoke-gui` | 退出码 0 |
| 双击窗口标题 | `Impor 专注计时器 · 3.6.16` |
| exe 大小 / SHA256 | 11,451,460 B / `3953F7A2F30221645452A8E7779E035001D9AB88AE6F82CC8B0B67563DA770F6` |
| `imporc version` | `imporc (Impor 自举编译器) v3.6.16 · 子集模式` |
| `imporc.exe` 大小 / SHA256 | 588,288 B / `4C73FA7C66C04E62887366AF9EB84A7ED279D8AAAC2779F8E839CC9C391FDFD4` |

## 5. 交付物

- `apps/impor_focus/Impor专注计时器.exe`（3.6.16，重新打包）
- `impor/imporc.exe` / `bootstrap/imporc_py.exe`（3.6.16，重新自举构建）
- `apps/impor_focus/README.md`（6 个卡点更新：2 个真 bug 已修，3 个摩擦保留，1 个定位说明）
- `impor/builtin_manifest.py`（唯一内建注册表）
- `tests/test_v3616.py`
- ZIP / VSIX 与桌面目录 `Impor(I++) 3.6.16`
