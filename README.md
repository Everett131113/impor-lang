# Impor (I++)

> CI 运行结论与截图见下方「CI 证据」一节。仓库当前为 private，shields / GitHub 徽章对外不可用，因此不在首屏放会 404 的徽章。

**Impor**（读作 "I++"）是一个自制的、完整的编程语言：拥有词法分析、语法分析、AST、树遍历解释器、编译到 Python 的转译器、交互式 REPL、命令行工具和带语法高亮的图形化编辑器。

版本：**3.11.0**（C ABI 矩阵 + `bench-spec`：push 门禁覆盖 win32 / win64 / linux64 / linux-arm64，每格校验机器类型与四个导出符号；macOS arm64 / x64 两格在 `c-abi-macos.yml` 手动触发（当前 macOS runner 额度不可用，不挂 push 门禁）。`impor bench-spec` 输出体积 / 冷启动 / 常驻内存 / 构建耗时。）

> **C ABI 最小验收**：`impor build --lib examples/c_abi/lib.impor -o lib.dll` 产出 `lib.dll + lib.h + lib.def`；
> 用 C demo 链接调用，输出 `add(2, 3) = 5`。字符串返回值用 `impor_free_str()` 释放。完整可复现步骤见 `examples/c_abi/README.md`。
> 需要 64 位 DLL 时改用 `impor build --lib --c-source ... -o out/`，再用 `build_msvc.bat` 或 `build_gcc.bat` 编译（内置 tcc 只产 32 位）。
> `lib.c` 是纯库源码，不含 `main`；`impor run`（64 位 Python）加载 32 位库会抛 `FFIArchError`，不再伪装成签名不匹配。

## CI 证据（仓库当前为 private）

本仓库目前是 **private**，因此匿名 `api.github.com` 与 shields 徽章会返回 404，外部无法直接点开验证。下面记录的是仓库主账号在 Actions 中看到的运行结果，截图已入档：

| 工作流 | 运行 | 结果 |
|---|---|---|
| `ci.yml` | [#14](https://github.com/Everett131113/impor-lang/actions/runs/44343129880) | 4 个 job 成功（Windows py3.11 / py3.12 full，Ubuntu py3.11 / py3.12 smoke） |
| `c-abi-x64.yml` | [#14](https://github.com/Everett131113/impor-lang/actions/runs/44343130120) | MSVC x64 / GCC x64 / TCC win32 / GCC linux-arm64 四格成功 |

- CI #14：`docs/ci-2026-11-19-ci14.png`
- C ABI #14：`docs/ci-2026-11-19-cabi14.png`

> macOS arm64 / x64 两格在 `c-abi-macos.yml` 中手动触发，当前账号 macOS runner 额度不可用，未计入上述 push 门禁。仓库转为 public 后，徽章和运行页才会成为外部可核验证据。

## 规格（实测，`impor bench-spec`）

| 指标 | Impor 3.11（Windows x64 宿主 / 32 位 tcc 产物） | 说明 |
|---|---|---|
| 单文件体积 | **121.5 KB** | `examples/hello.impor` 的独立 exe；不含 Python |
| 冷启动 | **约 28 ms**（30 次均值） | 进程启动到输出 |
| 运行时依赖 | **无** | 不依赖 Python、VC 运行库或第三方 DLL |
| 可被 C 调用 | **是** | `--lib` 产出 `.dll` + `.h` + `.def`；x64 用 `--c-source` 交给 `cl` / `gcc -m64` |

复现：`python Impor.py bench-spec examples/hello.impor --runs 30 --json`

> **新手教程**：docs/新手教程.md（30 分钟从零入门，示例全部实测通过）

> **头部标记已可选**：`important<<DAY<<english("English"):` 首行自 v2.3.1 起不再强制，
> 缺失时正常执行、仅给一行提示（模板仍默认生成）。

> **游戏示例**：`python Impor.py run examples\snake.impor` 运行用 Impor 写的贪吃蛇
> （方向键转向、R 重开、`--smoke` 无头自测模式）。

> **第一个真实用户工具（3.6.15，3.6.16 继续 dogfood）**：`python Impor.py run apps\impor_focus\focus.impor`
> 打开专注计时器；源码与打包说明见 `apps/impor_focus/README.md`。它不是 demo，是作者每天
> 真会用的 25/5 分钟计时器，记录存在 `%USERPROFILE%\.impor_focus\history.json`。

```
important<<DAY<<english("English"):
var name = "世界"
print("你好,", name)          // 你好, 世界
```

## 快速开始

### 方式一：命令行（无需安装）

```powershell
cd ImporLang
python Impor.py run examples\hello.impor    # 运行脚本（开发期）
python Impor.py repl                        # 进入交互式解释器
python Impor.py build examples\hello.impor -o hello.exe   # 发布：独立 exe（C 后端）
python Impor.py build --lib examples\c_abi\lib.impor -o lib.dll  # 发布：C ABI 库 + .h
python Impor.py compile examples\fib.impor -o fib.py   # 开发兼容：Impor → Python（不是发布路径）
python fib.py                                # 运行开发期转译产物
python Impor.py check examples\oop.impor    # 只做语法检查
python Impor.py run-dbg 程序.impor           # 命令行交互式调试（断点/单步/栈/变量）
python Impor.py fmt 程序.impor               # 格式化代码（缩进/空格/换行）
python Impor.py check-fmt 程序.impor         # 检查是否已格式化（CI 用）
python Impor.py lint 程序.impor              # 静态检查（未定义/未使用/死代码）
python Impor.py lsp                          # 启动 LSP 语言服务器（供 VS Code 连接）
python Impor.py list-modules                # 列出可发现模块
python Impor.py check-imports 程序.impor     # 静态检查导入（缺失/循环）
python Impor.py new 我的程序                 # 生成新脚本骨架
python Impor.py version                      # 版本号
python Impor.py                              # 无参数：打开图形化编辑器
```

### 方式二：安装为系统命令（推荐）

```powershell
cd ImporLang
.\install.bat        # 等价于 pip install -e .
impor run hello.impor
impor repl
```

### 方式三：Windows 双击

1. 双击 `Impor.py` 打开图形化编辑器；
2. 从「示例」菜单载入示例，点「运行」；
3. 支持语法高亮、行号、变量监视、沙盒、断点调试、编译为 Python。

### 方式四：Linux / macOS

```bash
# 前置：Python ≥ 3.10（macOS/Linux 自带或经包管理器安装）
cd ImporLang
python3 -m pip install --user -e .        # 安装为 impor 命令（免 root）
impor run examples/hello.impor
# 图形化编辑器依赖 tkinter；无 GUI 环境时用纯 CLI 即可
# 若缺 tkinter：Debian/Ubuntu → sudo apt install python3-tk；
# macOS 官方 Python → 自带；Homebrew python → brew install python-tk
python3 Impor.py repl
```

`impor pkg` 缓存目录在 Linux/macOS 遵循 XDG（`$XDG_CACHE_HOME/impor/pkg`，默认 `~/.cache/impor/pkg`）。

## 语言特性

| 类别 | 能力 |
|---|---|
| 基础 | 变量 `var` / `set`、常量 `true/false`、数字、字符串、`//`、`#`、`/* */` 注释 |
| 运算符 | 算术、比较、逻辑 `and/or/not`、三元 `条件 ? 真 : 假`、复合赋值 `+= -= *= /= %=` |
| 流程 | `if / elseif / else`、`while`、`loop`（无限循环）、`for in`（列表/range/字符串）、`break/continue` |
| 函数 | `def` / `func` 声明、参数、返回值、闭包、高阶函数、`nonlocal` / `global` 作用域 |
| 面向对象 | `class`、继承、`new`、`super()` 链式调用、实例属性、`set self.x = v` |
| 异常 | `exception` 自定义异常、`raise`、`try / catch / finally`、`catch err`、`catch Type err`、`catch Type, e`、异常链 `raise Err("m", from old)` / `raise Err("m") from old`、异常携带调用栈 `err.stack` |
| 断言（v2.8） | `assert 表达式`：条件为假抛 `AssertionError`（可 try/catch 捕获），配合 `impor test` 单元测试使用 |
| 诊断（v2.8） | 语法/运行时错误输出行列号 + 源码行 + 波浪线 `^` 指位；LSP 诊断同步列号 |
| 调试 | 断点、单步（步入/步过/步出）、调用栈、局部变量查看、CLI `impor run-dbg 程序.impor` 交互调试、GUI 调试面板（调用栈 + 局部变量窗口）、`breakpoint()` 内建 |
| 语法扩展 | 原始字符串 `r"..."`（关闭转义）、`match-case` 模式匹配（字面量/绑定/`_` 通配）、复合赋值扩展至对象属性与下标 |
| 工具链 | LSP 语言服务器 `impor lsp`（语法诊断/跳转定义/悬停/文档符号，VS Code 扩展随附）、`impor fmt` / `check-fmt` 格式化、`impor lint` 静态检查（未定义/未使用/死代码，支持 `# impor-lint-ignore` 抑制）、`impor pkg` 包管理器（本地/git 源、lock 锁文件、upgrade/remove、XDG 缓存目录） |
| 体验优化 | 头部标记可选（缺失仅警告）、编译产物异常显示源码行号（不泄露 Python 内部栈）、`IMPOR_DEBUG=1` 查看内部完整栈 |
| 容器 | 列表、字典、字符串，支持索引与切片 `a[1:4]`、负索引 |
| 模块 | `import mymod` / `import mymod as mm`、`from mymod import x as y`、`from .pkg import *` 相对导入、`export` 私有导出、目录包 `__pkg__.impor`、点分隔 `import pkg.sub`、循环导入安全缓存、`reload()`、模块元信息 `__name__/__file__/__is_package__`、`impor.toml` 项目配置、`import math` 等 Python 模块 |
| 内建 | `print`、`input`、`len`、`type`、`to_num`、`to_str`、`randi`、字符串/数组工具 |
| 游戏工具 | `vec2`、`rgb`、`clamp`、`lerp`、`remap`、`sign`、`distance`、`angle`、`shuffle`、`frame`、`fps` |
| 系统 | `args()` 命令行参数、`exit`、`cls`、`wait/sleep`、文件读写、`watch`/`snapshot` 监视、沙盒模式 |
| 编辑器 | Tk 图形编辑器（Code Studio）：语法高亮、行号、运行/清空、变量监视、断点、示例与导入导出；3.1 新增全项目搜索/事务化批量替换、大文件虚拟滚动、依赖图与离线审计、UIA 语义桥接、设置同步与插件进程隔离；3.8 新增多根工作区、workspace trust、跨根搜索、布局/会话恢复，另提供独立 PyQt6 内核 3.0 预览 |

头部标记**可选**：`important<<DAY<<english("English"):` 只是兼容旧脚本的声明头，缺失时解释器只给一条提示并继续执行；不写这一行不会改变任何语义，新脚本可以完全省略。

## 完整语法

见 [docs/语言规范.md](docs/语言规范.md)（当前 3.11.0 语法规范，与实现一致）。

## 目录结构

```
ImporLang/
├── Impor.py                 # 图形化编辑器 / CLI 启动器（兼容旧版入口）
├── impor/
│   ├── lexer.py             # 词法分析器
│   ├── ast_nodes.py         # AST 节点定义
│   ├── parser.py            # 递归下降语法分析器
│   ├── interpreter.py       # 树遍历解释器（模块缓存/包/export/循环导入）
│   ├── builtins.py          # 内建函数库（含 reload）
│   ├── stdlib.py            # 标准库白名单
│   ├── config.py            # impor.toml 项目配置
│   ├── transpiler.py        # Impor → Python 编译器
│   ├── runtime.py           # 编译产物的 Python 运行时
│   ├── repl.py              # 交互式解释器
│   ├── analysis.py          # 静态分析（符号表 + lint 检查）
│   ├── fmt.py               # 代码格式化（impor fmt / check-fmt）
│   ├── lsp.py               # LSP 语言服务器（impor lsp）
│   ├── pkgmgr.py            # 包管理器（impor pkg）
│   ├── cli.py               # 命令行入口（含 run-dbg/fmt/lint/lsp/pkg）
│   ├── editor.py            # Tk 图形化编辑器
│   ├── __main__.py          # python -m impor 入口
│   └── __init__.py          # 公开 API
├── vscode-impor/            # VS Code 扩展（语法高亮 + LSP 客户端）
├── examples/                # 60+ 个可运行示例（demo_01…demo_40 + hello/oop/game/stdlib 等）
├── tests/                   # 自动化测试
├── docs/语言规范.md          # 语法规范文档
├── install.bat              # 一键安装脚本
├── impor.bat                # 命令行启动器（免安装）
├── run_tests.py             # 一键运行测试
└── pyproject.toml           # 打包配置（pip install -e .）
```

## 测试

```powershell
cd ImporLang
python run_tests.py          # 或 python -m unittest discover -s tests
```

**703 项自动化测试**覆盖：词法/语法/解释器/转译器/REPL/CLI/示例回归，并校验**解释器与编译产物的输出完全一致**；模块系统测试覆盖 from-import/export/目录包/相对导入/循环导入/reload/元信息/异常；P0 测试覆盖原始字符串/match-case/异常链/复合赋值扩展/属性边界/断点与单步/CLI run-dbg；P1 测试覆盖格式化/静态检查/LSP 诊断与跳转/包管理器（安装/卸载/锁文件/版本约束）；v2.8 新增测试覆盖 assert 双后端一致性、Token 列号、波浪线诊断、`impor test` 子命令、pkg 依赖冲突。

**5 分钟陌生人测试**（脚本为 UTF-8 with BOM，Windows PowerShell 5.1 与 PowerShell 7 都可直接跑）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\five_minute_check.ps1
pwsh -File tools/five_minute_check.ps1
# 慢机默认 conformance 等距抽样 30 个用例；要跑全量：
powershell -ExecutionPolicy Bypass -File tools\five_minute_check.ps1 -Full
```

默认五步：版本 / 解释器 run / 编 exe 并脱离 Python 运行 / C ABI 源码包五件套 / conformance 抽样。本机 Windows PowerShell 5.1 实测 31.01 s，pwsh 29.58 s，上限 300 s。

**Impor 语言层单元测试（当前版本）**：写 `tests/` 目录下文件名含 `test` 的 `.impor` 文件（用 `assert` 断言），运行 `impor test` 即可执行并统计通过/失败。

## 编译为 Python（转译）

`impor compile` 把 Impor 源码转译为等价的 Python 源码（函数、类、继承、super、异常、闭包、导入全部转换），可直接运行或打包：

```powershell
impor compile examples\fib.impor -o fib.py
python fib.py

# 打包成独立 exe（需安装 PyInstaller）
impor compile examples\hello.impor -o hello.py --exe
```

## C 后端：脱离 Python 宿主（v2.13）

impor build 走「Impor → C 源码 → 随包分发的 tcc 原生编译」链路，产出**真正的独立可执行文件**：
运行不需要 Python、不需要 Impor 本体。

`powershell
impor build examples\fib.impor          # 生成 fib.exe
impor build demo.impor -o game.exe       # 指定输出名
./game.exe                               # 直接运行
`

- 编译器：impor/tcc/win32/tcc.exe（TinyCC 0.9.27，随包分发，无需预装）；PATH 里有 tcc 时自动回退。
- **原生数值快路径（3.7.3，显式 opt-in）**：`impor build loop.impor --fast` 把纯整数脚本编成不装箱的 `long long` C 代码（tcc `-O2`），适合 `while`/`if`/整数算术/`min/max/abs` 等计算循环。基准中三个纯整数循环为 CPython 的 **59–90 倍**；取模循环与递归调用低于 20 倍，浮点/字符串/容器/类会明确拒绝并提示去掉 `--fast`。默认 `impor build` 仍走完整 C 后端。
- **产物自带 I++ 图标**：tcc 没有 windres，编译成功后由 `impor/peicon.py` 调用 Windows `UpdateResource` 把 `impor.ico` 的 7 档尺寸写入 PE 的 `RT_ICON` / `RT_GROUP_ICON`；资源管理器、任务栏和文件属性里看到的就是 I++ 图标。`imporc.exe` 同样带图标。
- **双后端一致**：C 后端与 AST 解释器对同一脚本输出逐字符一致（单测强校验）。
- **第一版子集**（过程式）：变量、算术/比较/逻辑、字符串拼接与插值、if/elif/else、while、for-range、顶层函数、print/input/inputstr、常用内建（to_num/to_str/len/type/trim/upper/lower/strpad/strcut/abs/sign/min/max/clamp/randi/seed/randstr/sleep/now/clock）。
- 类/闭包/异常/模块/数组/字典等特性在 C 后端会得到**友好报错**并提示改用 Python 后端（impor run / impor compile --exe）。
- AST 树遍历解释器完整保留，用于教学与全特性运行；--backend 双后端架构不删旧能力。

## 许可

MIT

## 工具链（v2.3 P1）

### LSP 语言服务器

`impor lsp` 提供标准 LSP 服务（stdio JSON-RPC），随附 VS Code 扩展 `vscode-impor/`：

- **语法诊断**：编辑 .impor 时实时报告语法错误与未定义变量
- **跳转定义 / 悬停提示 / 文档符号**：F12 跳转、悬停看签名、大纲视图
- **模块搜索路径**：支持 impor.toml（诊断基础设施预留）

```powershell
# 在 VS Code 中安装扩展（开发模式）
cd vscode-impor
npm install && code . && F5
# 或打包安装
npm install -g @vscode/vsce && vsce package
code --install-extension vscode-impor-2.3.0.vsix
```

### 格式化与静态检查

```powershell
impor fmt 程序.impor          # 缩进统一 4 空格、行尾空格清理、空行压缩、运算符空格规范
impor fmt 程序.impor --max-line 88   # 超长调用/数组按顶层逗号在括号内换行（v3.9）
impor check-fmt 程序.impor    # CI 检查是否已格式化（退出码 0/1）
impor lint 程序.impor         # 未定义变量/未使用变量/未使用导入/不可达代码
impor lint --fix 程序.impor   # 自动删除未使用导入、给未使用变量加 _ 前缀（v3.9）
# lint 抑制：行内 `# impor-lint-ignore[: 规则1,规则2]`、文件头 `# impor-lint-ignore-file`、
#           或 impor.toml `[lint] disable = ["规则"]`
```

### 包管理器

```powershell
impor pkg install ./path/to/pkg       # 本地源码包（含 impor.toml）
impor pkg install git+https://...     # git 仓库源
impor pkg install mylib@0.2.0         # 私有源指定版本
impor pkg install mylib= >=0.2        # 私有源按约束取最高版本
impor pkg install --frozen            # 按 impor.lock 还原并逐项校验哈希（CI）
impor pkg list                        # 项目依赖 + 已安装包
impor pkg lock                        # 生成/刷新 impor.lock（版本/来源/哈希/传递依赖）
impor pkg lock --check                # 校验锁文件与缓存是否一致
impor pkg check                       # 同 lock --check
impor pkg upgrade [name]              # 私有源/git 源重新解析升级
impor pkg registry                    # 显示私有源地址与令牌状态（不打印令牌）
impor pkg pack ./mylib -o dist        # 确定性打包（同内容必同 sha256）
impor pkg publish ./mylib             # 打包并上传归档 + 自动更新 index.json
impor pkg publish dist/mylib-0.2.0.zip --force   # 覆盖同一版本
impor pkg outdated                    # 按锁文件来源检查可升级的依赖
impor pkg prune                       # 清理缓存中不可达的版本
impor pkg remove mylib                # 卸载
```

安装的包进入平台标准缓存目录（Windows `~/.impor/pkg`；Linux/macOS 遵循 XDG），运行时自动加入模块搜索路径（`import mylib` 直接可用）。

**锁文件**：`impor.lock` v1.1 记录直接与传递依赖的版本、来源、12 位目录哈希、完整 `dir_sha256`、归档 `sha256` 和 `requires`。提交锁文件后，CI 用 `impor pkg install --frozen` 即可复现同一份依赖；任何版本或内容漂移都会返回 1。

**私有源**：在 `impor.toml` 写

```toml
[registry]
url = "https://registry.example.com/impor"
token_env = "IMPOR_TOKEN"   # 也可用 IMPOR_REGISTRY 覆盖 url
```

索引为 `<url>/index.json`：

```json
{
  "packages": {
    "mylib": {
      "0.1.0": {"url": "mylib-0.1.0.zip", "sha256": "…64 位…"},
      "0.2.0": {"url": "mylib-0.2.0.zip", "sha256": "…64 位…"}
    }
  }
}
```

归档为 zip（`impor.toml` 位于 zip 根或唯一顶层目录），必须提供 `sha256`；缺失即拒绝安装。HTTP 源会带 `Authorization: Bearer <IMPOR_TOKEN>`。`file://` 与本地路径同样可用，便于离线私有源。

**发布到私有源**：`impor pkg pack` 固定 zip 时间戳、权限位与条目顺序，同内容必得同一 `sha256`；`impor pkg publish` 支持目录（自动 pack）或 `.zip`（自动读取 `impor.toml` 元信息），上传归档后读取/创建 `index.json` 并写入 `url` + `sha256`；同一版本已存在时必须加 `--force`。HTTP 源通过 `PUT` 上传并自动带 Bearer 令牌，`file://` 与本地路径直接写文件。`impor pkg outdated` 逐项对比锁文件来源中约束内可用的最高版本，`impor pkg prune` 清掉缓存里不再被任何锁文件或依赖引用的版本。

**VS Code 扩展**：`vscode-impor/vscode-impor-4.2.0.vsix` 已实际打包，可 `code --install-extension` 直接安装；市场发布步骤见 `vscode-impor/PUBLISH.md`，需要 publisher `impor-lang` 与 `VSCE_PAT`。

---

## 更新日志

### v3.9.0（64 位入口 + C 后端 FFI ✅ / x64 未实测）
- **`impor build --lib --c-source`**：产出 `lib.c / lib.h / lib.def / build_msvc.bat / build_gcc.bat`，由用户机器上的 `cl` 或 `gcc -m64` 编译 64 位 DLL。内置 tcc 只产 32 位（i386），`build --lib` 与 `sys-check` 都明确打印位宽。
- **C 后端 FFI**：`ffi_open` / `ffi_call` 不新增语法，Windows `LoadLibraryA` + `GetProcAddress`，POSIX `dlopen` + `dlsym`；参数/返回值编组、允许清单编译期烘焙、未登记库/超 8 参/动态 argtypes 编译期 exit 3。
- **位宽诊断**：`impor run` 是 64 位 Python，`impor build` 默认产物是 32 位；加载前读 PE `Machine` 字段，位宽不匹配时抛 `FFIArchError` 并给中文原因，不再套成 `FFIError` 签名不匹配。`sys-check` 同时报告解释器位宽与产物位宽。
- **`--c-source` 纯库源码**：生成的 `lib.c` 不再包含 `main`，只保留四个导出包装；普通 exe / `--lib` 仍生成 `main`。
- **两个体验伤**：允许清单同时接受 `,` 与 `;`；`FFIAccessError` / `FFIArchError` / `FFIError` 解释按异常类分派，不再一律套 private 符号。
- **CI 门禁**：`.github/workflows/c-abi-x64.yml` 在 `windows-latest` 用 MSVC `cl /LD`、在 `ubuntu-latest` 用 `gcc -shared -m64` 各编一次 `examples/c_abi/ci_lib.impor`，校验 PE/ELF 为 x64 且四个导出符号齐全。
- **测试**：`tests/test_v39x_ffi.py` 8 条；主回归 189 条 OK；conformance 100/100。
- **未实测（如实列出）**：本机无 `cl/gcc`，x64 `--c-source` 编译与 `cl demo.c lib.lib` 只能由 GitHub Actions 验证；复杂导出函数生成的 GNU 语句表达式 `({ ... })` 是否被 MSVC 接受未验证。

### v3.8.4（语义诚实 + C 后端 try/catch ✅ / B–E 未交付）
- **静默丢语义修复**：`yield`、`with` 进入关键字表并在语句位置直接拒绝；默认参数、变参、可选参数在参数解析阶段拒绝；列表/字典推导式在字面量阶段拒绝。全部提示“尚未实现（v3.9 计划）”，exit 2，不再把未实现特性当 null 静默执行。
- **C 后端 `try/catch/finally`**：`setjmp` 跳转栈 + `g_jmp_n` push/pop，catch 变量绑定 `rt_err_obj()`；`rt_div` / `rt_idiv` 的除零改为 `rt_throw`，错误可被捕获。实测 catch 打印 `CAUGHT ZeroDivisionError division by zero`，`exit(7)` 返回 7。
- **测试**：新增 `tests/test_v384.py`，逐个锁定五类未实现特性的 exit 2 与提示文案，并真实 build/run 一个含 try/catch 的 exe。
- **未交付（如实列出）**：B 组（默认参数/变参、真生成器、推导式、with）、C 组（类型稳定化/容器元素类型/null 安全）、D 组（std/net、std/sql、公共索引）、E 组（5 项真实基准）均未实现；本版只完成“语义诚实 + C 异常”这一组，路线为 v3.9。

### v3.8.3（门禁可信度 + C ABI v2 ✅ / MSVC 链接未实测）
- **imporc 门禁修复**：`fileread` 统一剥离 UTF-8 BOM；`gen_block()` 接受单行/多行 `{}` 块；语法失败与 tcc 失败改为 `exit(1)` 且不生成产物。三条都有回归测试，不再“报错但 exit 0”。
- **PyQt6 跳过条件**：`TestKernel3Preview` 改为按 `import PyQt6` 成功与否决定 skip，PyQt6 缺失时不再执行并失败。
- **C ABI v2**：新增 `impor_free_str()`；字符串返回值由 DLL 新分配，调用方必须释放。类型映射新增 `size_t → size_t`、`handle/ptr → void *`，用于不透明句柄和结构体按指针传递；头文件补 `<stddef.h>`，`.def` 导出表包含 `impor_free_str`。
- **实测**：tcc 链接 demo 调用 `size_t` 双向、`void *` 句柄往返、连续两次字符串返回并分别释放，输出 `add=5 / greet1=Hello, A / greet2=Hello, B / twice=42 / handle=00001234`。
- **未交付（如实列出）**：本机无 `cl.exe` / `lib.exe` / `link.exe`，MSVC `.lib` 链接未实测；调用方缓冲区字符串 API、命名结构体强类型指针未实现。

### v3.8.2（C ABI 第一版 + 发布路径收口 ✅）
- **`export func` → `.dll + .h`**：`impor build --lib lib.impor -o lib.dll` 同时产出 `lib.dll`、`lib.h`、`lib.def`。头文件带 `extern "C"`，符号统一 `impor_` 前缀；类型映射 `int→long long`、`float→double`、`bool→int`、`str→const char *`。
- **实测 C 调用**：随包 tcc 编译 `demo.c` 直接链接 DLL，运行输出 `add(2, 3) = 5` / `greet = Hello, Impor` / `half = 3.5`。示例与复现命令在 `examples/c_abi/`。
- **C 后端成为唯一发布路径**：`impor build` 出 exe 或 DLL；`impor run / repl / run-dbg` 只做开发期运行与调试；`impor compile`（Impor → Python）降为开发兼容，不再承担发布；`imporc` 只做内部一致性探针。
- **明确边界**：仅顶层 `export func`；参数/返回必须显式标注 `int/float/bool/str/void`；默认参数、变参、泛型、类方法导出、`async` 导出均报错。MinGW `gcc -L. -lx` 需 `.dll.a`，本机无 gcc/dlltool，未实测。
- **格式化修复**：`impor fmt` 不再把返回类型箭头 `->` 拆成 `- >`；`examples/c_abi/lib.impor` 已 fmt-clean。
- **测试**：新增 `tests/test_v382.py` 覆盖 DLL/头文件生成、C demo 真链接调用、未标注类型与无导出报错。

### v3.8.1（一致性补丁：产物可搬家、类型检查、imporc 参数 ✅）
- **编译产物脱离仓库**：转译头不再固定 `from impor.runtime import *`。产物启动时依次尝试 `IMPOR_HOME`、编译时安装根、产物目录/父目录、当前目录，找到含 `impor` 的安装根后注入 `sys.path`；全部找不到才以退出码 3 报“找不到运行时”并给修复命令，不再裸 `ModuleNotFoundError`。这修复了干净副本 `test_*_transpile` 换目录即失败的根因。
- **类型注解不再只解析**：`run` / `run-dbg` / `compile` / `build` 在解析后调用 `typecheck.check_source`，对可静态判定的字面量赋值与函数返回类型报 `[FAIL] 类型` 并以退出码 2 中止。仍是轻量检查：无泛型、无流分析，解释器运行时保持动态语义。
- **`imporc` 真正读 argv**：C 运行时 main 注入 `argc/argv`，`args()` 在 C 后端可用；自举主流程优先读进程参数，无参数才回退 `imporc_args.txt`。`imporc <脚本> -o <输出>` 真正生成指定文件；`-o` 缺值、未知参数、缺脚本都明确报错并返回 2，不再“忽略参数、自编译、exit 0”。`imporc` 的 `c_runtime.c` 与 tcc 改为按可执行文件所在目录定位，在任意当前目录调用都能找到随包运行时。
- **文档与 fmt 对齐**：`docs/写法规范.md` 以 `impor fmt` 实际输出重写（`def`、`while true`、`var`，`set` 与裸赋值都保留）；`docs/语言规范.md` 收敛版本线、中文关键字统一为“v5.2 起移除、仅保留中文标识符”；两处不再互相矛盾。
- **本版未交付（明确边界）**：`yield`、推导式、默认参数/变参、`with`；C 后端 `try/catch`、线程/原子、`.dll` 导出。它们是路线图项，3.8.1 不冒充完成。
- **测试**：新增 `tests/test_v381.py` 覆盖产物搬家、类型门禁、`imporc` 参数与错误分支；全量结果见发布记录。

### v3.8.0（工作区 2.0 + 内核 3.0 预览 ✅ / 内核主线未替换）
- **多根工作区**：`.imporproj` 的 `roots` 可增删；资源管理器头部 `＋/－` 与右键菜单均可操作，新增/移除根后自动重建项目索引并刷新项目树。主根受保护，避免切换工作区时丢配置。
- **workspace trust**：新增全局 `trusted_workspaces`、受限模式横幅与「信任此工作区」。未信任时只拦截终端/任务、运行、构建和项目插件加载；打开、浏览、搜索、编辑仍可用。信任状态写入 `~/.impor_studio.json`。
- **跨根搜索**：全项目搜索支持多根，按根校验路径包含并去重重叠根；搜索 UI 直接使用 `workspace_roots`。
- **布局与会话恢复**：保存/恢复窗口 geometry、sash、main/split 两组标签、活动标签、每个文件的光标行列与滚动位置；旧 `tabs`/`folds` 配置仍兼容。
- **内核 3.0 预览**：新增 `impor/kernel3.py`，PyQt6 `QPlainTextEdit` 独立窗口，含行号、当前行高亮、Impor 语法高亮、minimap、`Ctrl+Alt+↑/↓` 多光标、同列多光标、多光标输入/删除、平滑滚动与 Qt 原生 IME；10.4MB / 10 万行无头打开约 2.3–2.8 秒。
- **启动默认空白**：新建标签不再注入 `// 新脚本` / `print("你好，Impor!")` 模板。
- **边界**：内核 3.0 是第一阶段 PoC，不替换现有 Tk 面板；未接入 LSP、调试、Git、AI、插件、项目树和主题跟随，也不含 Monaco/WebView2 或 QScintilla。语言侧本版未改语法。
- **测试**：`tests/test_v380.py` 7 项通过；工作区、会话恢复与 10MB 级内核大文件烟测通过；全量 `run_tests.py` **667 项全部通过**（888.298 秒）。

### v3.7.8（defer 延迟清理、C 后端管道、能力差距路线图 ✅ / 其余方向仅起步）
- **defer**：支持 `defer { ... }`、`defer:` 缩进块与 `defer` 换行缩进块；函数/模块退出时按 LIFO 执行，正常返回、异常退出、`exit` 都触发。`defer` 仍是软关键字，`var defer = 1` 不受影响。
- **defer 边界**：解释器、快速执行路径与 Python 转译都已支持；`defer` 体内禁止 `return/break/continue`，C 后端明确报“defer 延迟块（C 后端暂不支持）”，不静默跳过；`imporc` 仍是 v3.3 语法子集。
- **C 后端管道**：`x |> f(a)` 降级为 `f(x, a)`，`x |> f` 降级为 `f(x)`，链式管道递归降级；独立 exe 实测输出一致。
- **路线图**：新增 `docs/语言能力差距与路线图-3.7.8.md`，把相对 C / Python 的缺口、四端不一致与分批计划写清。
- **规范修正**：不再声称 C 后端支持 `try/catch/finally`；补充 `defer` 语义与边界。
- **本版未交付**：`with`、`yield`、默认参数/变参、解构遍历、半开区间、`guard`；C ABI/库导出、线程/原子、LLVM；编辑器工作区 2.0、AI 逐 hunk 接受/拒绝、LSP call hierarchy / code action。它们是后续目标，不在 3.7.8 中冒充完成。
- **测试**：`tests/test_v378.py` 11 项通过；全量结果见发布记录。

### v3.7.7（安全跨文件替换、递减 for、AI 输出预算 ✅ / 其余编辑器与语言项未交付）
- **未保存缓冲区可选纳入替换**：全项目搜索新增「包含未保存缓冲区（会一并保存）」选项。勾选后，dirty 文件用编辑器里的缓冲区全文参与匹配和替换，避免“磁盘旧内容覆盖未保存修改”；不勾选时不读取缓冲区，dirty 文件仍不可选。
- **二次确认与统一保存**：替换前先预览、写事务备份；若选中了 dirty 文件，额外确认“会连同未保存修改一起保存，并重新加载编辑器”。替换后按实际写入路径重载普通标签和分块标签（虚拟标签走 `reload_from_disk()`），清 dirty。
- **递减 for**：`for i = 5; i > 0; i--` 与 `for j = 3; j >= 1; j--` 现在可用，分别产生 `5 4 3 2 1` / `3 2 1`；`i--` 配 `< / <=`、`i++` 配 `> / >=` 会在解析期报错，避免死循环。
- **AI token 预算**：AI 设置新增「输出上限」（64–8192，默认 1200），请求体写入 `max_tokens`；`normalize_config()` 做范围收敛，异常值回退 1200。
- **本版未交付**：AI hunk 逐块接受/拒绝、调试 watch / 变量修改 / attach / 后台图形面板、LSP call hierarchy / code action / 增量语义 token、`defer` / `with` / `yield`、workspace trust。它们仍是后续版本目标，不在 3.7.7 中冒充完成。
- **测试**：`tests/test_v377.py` 5 项通过；全量结果见发布记录。

### v3.7.6（大文件设置、跨块替换、AI 流式 ✅）
- **大文件设置 UI**：编辑器设置新增阈值（256–65536 KB，默认 2000）与打开模式（自动分块 / 始终完整打开 / 打开时询问）；`large_file_kb` / `large_file_mode` 持久化并参与设置同步，恢复默认回到 2000 / auto。
- **跨块查找**：分块编辑的 `find_next()` / `find_all()` 改为搜索完整行列表；命中块外内容自动跳块并选中，找不到时从头回绕。`find_all()` 只给当前窗口命中打 tag，避免给 26 万行全量标色。
- **跨块替换**：查找条新增「跨块替换」，对整文件做字面量、不区分大小写的全部替换；替换前先强制 flush 当前窗口，替换后刷新窗口并标记未保存。普通全文标签提示使用 `Ctrl+Shift+F` 全项目替换。
- **AI 流式**：新增 SSE 流式请求，`data:` 行拼接 `delta.content`，`[DONE]` / EOF 结束；非 SSE 响应自动回退整段 JSON。请求期间有带「取消」的流式窗口，取消抛 `AICancelled` 并提示「已取消」；fix / tests 仍等完整结果后进 diff 预览。
- **AI 设置**：新增「流式输出」开关，默认开启；服务端不支持时可关闭。
- **测试**：`tests/test_v376.py` 13 项通过；全量结果见发布记录。

### v3.7.5（大文件编辑与可选 AI 内联助手 ✅）
- **大文件默认分块**：超过 `large_file_kb`（默认 2 MB）自动进入 `VirtualLargeFilePane`，Tk Text 只保存当前窗口（默认最多 3000 行、按平均行长自适应，单窗口约 22 万字符上限），行号与保存仍是整文件绝对行号；不再每次询问。
- **可视区高亮 + 后台解析**：只对可视区 ±80 行套 tag，滚动做 90–110ms 防抖；符号解析在 daemon 线程按窗口缓存，回主线程应用，同一窗口不重复解析。
- **10 MB 实测**：11,685,459 B / 262,146 行，本机读取 0.037s、首窗 0.146s、换块 0.004s、可视区高亮 0.006s、取全文 0.033s；Tk Text 内仅 3000 行。这是单机冷启动数据，不等于所有机器都达到同一数字。
- **可选 AI 助手**：命令面板新增「AI 解释选中代码」「AI 修复选中代码（diff 预览）」「AI 生成测试（diff 预览）」「AI 助手设置（默认关闭）」；默认 `enabled=false` + `privacy_ack=false`，必须同时确认并配置 key 才发送。
- **只发选区**：请求只带当前选区（默认上限 12,000 字符，超出截断）、文件名 basename、语言名；不发送整文件、工作区路径或终端输出。API key 只用于 `Authorization` 头，不进入发给模型的内容。
- **兼容 OpenAI Chat Completions**：endpoint / model / key 可配置，`IMPOR_AI_API_KEY` 优先；只允许 https 或本机 http。修复与测试结果进入既有统一 diff 预览，点「应用修改」才写入选区或新标签。
- **测试**：`tests/test_v375.py` 18 项通过；全量 631 项 OK（542.250 秒）。

### v3.7.4（编辑器调试与 LSP 能力 ✅）
- **DAP 断点完整化**：`setBreakpoints` 支持 `condition`、`hitCondition`（`5` / `%5` / `>=3` / `>3` / `<=3` / `<3` / `==3` / `!=3`）和 `logMessage`；日志点输出 `{}` 插值，不再暂停；VS Code 与 Studio 的条件/命中次数语义一致。
- **异常断点**：`exceptionBreakpointFilters` 提供“未捕获异常 / 所有异常”；解释器在抛出点回调，DAP 发送 `stopped(reason=exception)`；Studio 原有“异常暂停”开关保留。
- **LSP 六项**：`semanticTokens/full`（与 Studio 共用 `analysis` 符号表）、`foldingRange`（缩进块 / import 区 / 块注释）、`inlayHint`（未标注字面量类型）、`codeLens`（引用计数，点击调用 `impor.showReferences`）、`documentHighlight`、`prepareRename`。VS Code 扩展新增引用命令。
- **统一分析**：`semantic_symbol_map` 优先使用 `impor/analysis.py` 的真实作用域符号表，解析中或语法未闭合时回退轻量正则；`test_v323` 的 `struct`/`exception`/模块口径保持不变。
- **命令面板**：按文件/编辑/导航/运行调试/测试/Git/工具分组，最近使用与当前上下文（有选区、调试中、已打开文件）优先，快捷键重复会标出冲突命令；原命令仍可按名称模糊搜索。
- **测试**：`tests/test_v374.py` 16 项通过；未包含大文件内核重写与在线 AI 助手。

### v3.7.3（原生数值快路径 ✅）
- **`impor build --fast`**：纯整数脚本生成不装箱 C 代码（`long long`），随包 tcc `-O2` 编译为独立 exe；显式 opt-in，默认构建不变。`--fast-native` 为别名，`--no-fast` 显式关闭。
- **覆盖与拒绝**：整数变量/赋值/复合赋值、`while`、`if/elif/else`、顶层函数、`return`、`print`、`min/max/abs`、`+ - * %`；比较与 `and/or/not` 只用于条件，不能存成布尔值或打印（避免 True/False 与 C 的 1/0 差异）。浮点、字符串运算、容器、类、异常、嵌套函数、`for` 等明确报错（返回码 3），不静默降级。变量是 int64，超出会回绕；`%` 按 Impor 同号取模，`% 0` 报错退出。
- **基准**：`benchmarks/bench_native20.py` 同源码对比 CPython 3.11.5，三个纯整数循环 73.33x / 58.71x / 89.87x，门禁 PASS；取模 8.90x、递归 fib 7.87x 作为诊断项，证明 20x 有明确适用边界。
- **测试**：`tests/test_v373.py` 9 项通过；构建缓存区分 fast/full，快路径编译加 `-O2`。

### v3.7.2（关键字收敛 ✅）
- 空保留字降级；`static` / `readonly` / `async` / `await` / `this` / `defer` 改软关键字，可作普通标识符。
- 同义写法收敛为 `def` / `elif` / `match` / `while` / `var`；别名仍解析但标 deprecated，`impor fmt` 与 LSP 补全只推主写法。

### v3.2.3（编辑器五项升级 ✅）
- **符号级语义高亮**：`semantic_symbol_map` 建立参数 / 局部变量 / 函数 / 类结构异常 / 模块映射，`highlight()` 按类别套 `sem_var`、`sem_param`、`sem_module`、`func`、`cls`，`obj.field` 推断 `sem_field`；三套主题同步新增配色。
- **异步防抖诊断**：编辑后 300ms 防抖，后台 daemon 线程只做 `_collect_lint` 纯计算，结果经 `queue.Queue` 回主线程；代次丢弃过期结果；每个 pane 保存自己的问题标记，切换标签不串文件；保存/显式检查仍同步执行。
- **覆盖率行号栏内联**：行号栏画绿色命中 / 红色未命中竖条，错误红下划线、警告黄下划线；覆盖率 payload 带 header offset，Treeview 与行号栏对齐；新增“跳下一个未覆盖”。
- **Git 三方合并编辑器**：`split_conflict_markers` 支持 merge / diff3；`git show :1/:2/:3` + `git merge-file --diff3` 生成 BASE / OURS / THEIRS 三栏，逐块采用或保留双方；保存前语法校验、原子写回，可选 `git add`。
- **插件管理器**：本地插件发现、文件夹 / ZIP 安装、启停状态持久化、meta 版本与权限、卸载与 reload；管理器窗口列出状态 / 名称 / 版本 / 权限 / 路径，双击切换启停。
- 测试：`tests/test_v323.py` 10 项单测通过；`_gui323.json` 真实 Tk 验证五项；`_git323.json` 临时仓库验证真实 Git 冲突。

### v4.2.0（P0：字典 / 切片 / 闭包 / 模块值 / 表驱动测试 / 统一诊断 ✅）
- **字典三端打通**：`{k: v}` 字面量、`d[k]` 读写、`d.keys()/values()/items()/get()/has()`、`for k in d` 迭代键、字典与数组深比较 `==`；解释器、C 后端、imporc 自举编译器同一份样例 12 行输出逐行一致。
- **切片三端打通**：数组与字符串 `a[1:5]`、`a[-2:]`、`a[::2]`、`s[1:4]`，支持负索引、越界裁剪与负步长；字典切片按显式 `TypeError` 拒绝。
- **嵌套函数 / 闭包打通**：函数体内 `func` 可捕获外层变量，`nonlocal set` 改值、`global set` 改全局，函数可作为参数/返回值/变量；C 后端用「独立 C 函数 + `Val*[]` 环境 + 外层变量装箱」实现，imporc 同步为全部局部装箱，重复调用保留状态。`examples/closures.impor` 在解释器、Python 转译、C 后端、imporc 四路径输出逐字一致。
- **`expect()` 表驱动断言**：`expect(actual, expected, label)` 落地解释器、Python 转译、C 后端与 imporc，失败消息统一为「标签：期望 X，实际 Y」；`assert` 与 `expect` 都计入断言条数。
- **`impor test` 隔离**：每个测试文件在独立临时目录中执行（`IMPOR_TEST_TMP` 可取路径），新增 `--no-isolate` / `--keep` / `--filter`，汇总输出断言条数。
- **统一错误码**：运行错误首行附加 `[E1001]`…`[E1099]`，解释器 / C 后端 / imporc 同一套编号。
- **调用栈**：未捕获错误在四段式后输出「调用栈（自顶向下）」；`try/catch` 捕获后不打印、进程正常继续；C 后端除零/下标消息改成与解释器一致的 `division by zero` / `list index out of range`。
- **模块对象作为值（C 后端）**：`import m as mod` 后模块可以赋值、传参、打印、比较；C 后端用运行期 `V_MODULE`（`Mod{name, attrs}`）承载，属性表是「导出名 -> 函数/顶层变量」，`mod.f(..)` 动态分派、`mod.v` 读变量、`type_of(mod) == "module"`、打印为 `<Impor 模块 名>`，与解释器一致。`tests/test_v42_module.py`（4 项）覆盖解释器、C 后端与 imporc（imporc 单层 `import` 编译期内联）。
- **新增回归**：`tests/test_v42_dict.py`（8 项）、`tests/test_v42_diag.py`（5 项）、`tests/test_v42_closure.py`（4 项，四路径）、`tests/test_v42_module.py`（4 项）；`tests/test_impor.py` 183 项通过。
- **已知边界**：imporc 已支持单层 `import mod [as m]` 与模块对象作为值（编译期内联 + 运行期 `V_MODULE`），但尚不支持 `from ... import`、包路径与嵌套 import；两层以上嵌套函数与自递归闭包会明确报 `UnsupportedFeature`；imporc 的调用栈只记录函数名、未记录行号。

### v4.1.0（语言核心补齐 + 私有源可写 ✅）
- **C 后端支持模块导入**：`import m`、`import m as mm`、`from m import a`、`from m import a as b`、`from m import *`；编译期由 `impor/clink.py` 把模块代码拼接进主程序，按实际引用裁剪未使用的函数/类/变量与不可达死代码；模块内符号统一改名为 `__m_<模块>_<符号>`，`std.algo` 与 `std.text` 同名函数可同时导入；相对导入、`__pkg__.impor`、`std.*` 包路径均支持。C 后端产物仍是独立 exe，不依赖 Python。
- **`type_of` 内建**：解释器、Python 转译、C 后端、imporc 四路径统一；`type_of([1,2]) == "list"`、对象返回真实类名（如 `"Point"`）。C 运行时 `rt_type` 增加类显示名，导入类的类名不会被内部改名污染。
- **`std.collections.flatten`**：递归展开嵌套数组，`flatten([1,[2,3],[4,[5]]]) → [1,2,3,4,5]`，解释器与 C 后端一致。
- **imporc 嵌套 `switch`**：引入 switch 缩进栈，修复内层 switch 消费外层缩进基准导致的分支错乱与结尾 `DED` 泄漏；嵌套样例输出 `aa / b / z`。
- **私有源可写**：`impor pkg pack` 确定性打包、`impor pkg publish`（目录/zip、自动更新 `index.json`、同版本需 `--force`）、`impor pkg outdated`、`impor pkg prune`；HTTP 源走 `PUT` + Bearer。
- **版本与扩展**：`4.1.0` 同步到 Python 包、`pyproject.toml`、`imporc` 版本打印与 VS Code 扩展；重建 `impor/imporc.exe`（8MB 栈 + 图标）。
- **回归**：新增 `tests/test_v52.py`（5 项）与 `tests/test_v53.py`（8 项）；全量 `run_tests.py` **536 项全部通过**（490s），conformance 23/23 逐字节一致；自举 B/C/D 三代逐字节一致。

### v3.10.0（生态闭环：依赖锁定 / 私有源 / 市场发布 ✅）
- **依赖锁定**：`impor.lock` 升到 v1.1，记录直接+传递依赖、来源、12 位目录哈希、完整 `dir_sha256`、归档 `sha256`、约束与 `requires`；`impor pkg install --frozen` 严格还原并复核，`pkg lock --check` / `pkg check` 校验漂移。
- **私有源**：`[registry] url` + `token_env`（默认 `IMPOR_TOKEN`），`IMPOR_REGISTRY` 可覆盖；`index.json` + zip + 强制 sha256；支持 `name` / `name@版本` / `name=约束`，Bearer 令牌鉴权，`pkg registry` 不回显令牌。
- **完整性**：下载先校验 sha256 再解压，拒绝路径穿越；包名/版本与索引不符即拒绝。
- **市场发布**：扩展补 `LICENSE`、128×128 图标、`CHANGELOG.md`、`.vscodeignore`、`PUBLISH.md` 与 GitHub Actions 发布工作流；`vscode-languageclient` 进入运行时依赖；已打出 `vscode-impor-3.10.0.vsix`（322 文件，约 471 KB）。

### v3.9.0（生态与工具链：标准库 / fmt / LSP / lint ✅）
- **`std.algo` 新库**：`sorted`、`sorted_desc`、`index_of`、`bsearch`、`min_index`、`max_index`；`use std` 一次注入 text / mathx / collections / algo 四库。
- **标准库扩展**：`std.text` 增加 `pad_left` / `pad_right` / `index_of` / `count_sub` / `split_lines` / `is_blank`；`std.collections` 增加 `find` / `find_index` / `take` / `drop`。新增函数只用三端共有内建，解释器与 Python 转译输出逐行一致。
- **`impor fmt --max-line N` 真换行**：超长调用/数组按顶层逗号在括号内换行，参数缩进一级；换行后可幂等重格式化，`var arr = [` 等空格保持正确。
- **LSP 格式化**：新增 `textDocument/formatting` / `rangeFormatting`，VS Code 保存时格式化直接复用 `impor fmt`；`Impor: 格式化当前脚本` 改为走 LSP。
- **`impor lint --fix`**：自动删除未使用导入、给未使用变量加 `_` 前缀；`[lint]` 可配置 `max_function_lines` / `max_function_statements` / `magic_number_repeats` / `short_name_whitelist` / `enum_exhaustive`；`--style` 新增 enum 穷尽性提示。
- **imporc switch**：支持绑定模式 `case v` 与 `Color.MEMBER` 常量；conformance 新增 `23_switch_match.impor`。已知：`imporc` 暂不支持嵌套 switch。

### v3.8.0（语言可用性第二批：enum / lint / fmt ✅）
- **`enum` 枚举**：`enum Color { RED, GREEN, BLUE }` 或缩进块；成员自动递增，支持显式整数（`MEDIUM = 5`），`Color.RED` 可直接比较/参与 `switch`。解析期脱糖为整型常量，关键字总数仍为 120。
- **三端 + 自举**：解释器、Python 转译、C 后端、`imporc` 同一份 enum 样例输出一致；conformance 新增 `22_enum.impor`，22/22 全绿。
- **lint 可读性提示**：`impor lint --style`（或 `--readability` / `impor.toml [lint] style = true`）新增命名过短、重复魔法数字、函数过长三条提示；全部为提示级、默认不启用，避免误报。
- **fmt 大括号块缩进**：`func f() { ... }` 的块内语句与闭合括号正确对齐；同时修掉左括号后的多余空格、字符串末尾与 `+` 的空格丢失。注释、多行字符串与原始语义保持不变。
- **新增回归**：`tests/test_v49.py`（21 项）覆盖 enum 四路径、`switch` 中的 `Color.X` 模式、enum 语法错误、lint 三条规则、fmt 缩进/注释/语义/幂等/字典键冒号。

### v3.7.0（系统级核心第一批：struct ✅）
- **`struct` 字段记录类型**：`struct Point` + 缩进块内 `var x = 0` / `var y = 0`；`new Point(3, 4)` 按字段声明顺序传参，字段可读写。省掉「只为装几个字段写一个类」的样板代码。
- **解析器脱糖**：`_parse_struct` 把 struct 转成 `ClassDef` + 自动 `__init__`，复用既有类语义；软关键字识别（`var struct = 1` 不受影响），关键字总数仍为 120。空的 / 重复字段 / 非字段语句会报明确的语法错误。
- **三端一致**：解释器、Python 转译、C 后端（`impor build`）、自举编译器（`imporc`）同一份样例输出一致；imporc 新增 struct 分支（缩进块 / 大括号块）与 `m_<名>___init__` 生成。
- **C 后端多实参修复**：tcc 对元素由函数调用初始化的 `(Val[]){...}` 复合字面量报 `index too large`（2 个及以上必现），这正是此前双参 `__init__` / `new Point(3, 4)` 在 C 后端崩溃的根因。改为语句表达式临时数组传递，`ctrans.py` 与 `imporc.impor` 的 `new` / 方法调用 / `super()` 三处同步。
- **新增回归**：`tests/test_v46.py`（6 项）——四路径输出一致 + struct 体内非法语句报错。
- **conformance 扩展**：新增 `conformance/cases/21_struct.impor`，三后端逐字节比对 21/21 全绿。
- **同步更新**：版本四处真源 → 3.7.0；`imporc` 重建；`docs/语言规范.md` 新增 6.1 节。

### v3.6.0（语言宪法 P2：人类友好的诊断系统 ✅）
- **目标**：报错从「位置 + 一行提示」升级为统一四段式——**哪里 + 是什么 + 为什么 + 修复**。
- **唯一渲染器**：`impor/diagnostics.py` 的 `render_diagnostic()` 成为诊断中枢，CLI（`run` / `run-dbg` / `check` / `lint` / `test` / `compile`）、REPL、LSP、编辑器输出面板共用同一套段落构造。
- **语法错误**：保留原首行 `语法错误: 第N行 列M:`（对既有脚本与测试零破坏），其后输出「为什么 / 修复」，并带出错行前 1 行 / 后 1 行上下文与精确列位波浪线（中文按双宽对齐）。
- **运行错误**：`运行错误: 第N行 列M: NameError: ...`；按异常名匹配知识库（未定义名字、参数个数、不可调用、除以 0、下标越界、类型不匹配、const 只读、断言失败、递归过深、导入失败、属性缺失、作用域、运算符、左值、C 后端限制等 20+ 条规则），未命中时给通用兜底而不是省略段落。
- **彩色**：自动按 `sys.stderr.isatty()` 判断，Windows 控制台自动开启 VT；`IMPOR_COLOR=always|never` 强制开关、`NO_COLOR` 关闭；管道 / 重定向捕获时是干净纯文本，不给脚本消费带副作用。
- **LSP**：语法诊断 `message` 追加「为什么 / 修复」；未使用变量 / 导入等给出针对性建议；诊断位置与既有补全、跳转、改名不变。
- **`impor lint`**：警告 / 错误项后追加缩进的「修复：」一行，行首 `文件:行:列 [级别]` 的机器可解析格式保持不变。
- **新增回归** `tests/test_v44.py`（22 项）：知识库命中、四段结构、上下文片段、彩色开关、CLI / REPL / LSP 实跑。
- **同步更新**：版本四处真源 → 3.6.0；`imporc` 按新种子重建；`docs/语言规范.md` 新增第 22 节；`docs/新手教程.md` 新增 15.4 节。
- **启动过场（v3.6 追加）**：新增 `impor/splash.py`，图形编辑器打开前先显示自绘的「IMPOR IS COMING」启动画面——进度条、三段状态文案、`v3.6.0` 标识，约 1.15 秒后淡出进入主界面；单击 / Esc / Space 可跳过，`IMPOR_NO_SPLASH=1` 可完全关闭（自动化截图用）。
- **已知边界**：C 后端仍以 `/*@L<行号>*/` 标记回溯 Impor 行号，宏展开 / 跨行表达式可能定位到最近的上一处标记。

### v3.5.0（语言宪法 P0+P1：写法收敛 + 文档注释 ✅）
- **目标**：把五条设计原则落成可执行的标准——同一件事只有一种推荐写法；文档与代码绑定。
- **P0 语法收敛**：
  - 新增 **`docs/写法规范.md`**：唯一「推荐写法表」，覆盖语句/输出/变量/条件/循环/函数/类/异常/运算符/中文写法/注释/模块 12 类意图。
  - **冻结新增语法糖**：v3.5 起不再新增同义写法；v3.4 的 12 类同义写法统一降级为「兼容层」（能跑、不扩、不推荐）。
  - 新增 **`impor lint --style`**（或 `impor.toml` 的 `[lint] style = true`）：逐条报告兼容写法并给出唯一推荐写法，全部为 `[提示]` 级，**不影响退出码**，可用 `# impor-lint-ignore` / `[lint] disable` 抑制。
  - 不删任何功能：34 项 v3.4 用例与既有示例保持通过；真正淘汰留到 v4 评估。
- **P1 文档注释**：
  - 词法：整行 `##` 生成 `DOC` token（普通 `#` 行为不变；行尾 `##` 与括号续行内的 `##` 仍按普通注释）。
  - 语法：连续多行 `##` 合并，绑定紧随其后的第一条语句（`func`/`def`/`class`/`exception`/`var`/`const` 及类方法），写入 AST 节点 `doc` 属性；三端按 `type(node)` 分发、不读 `doc`，执行语义零变化。
  - 符号表：`Symbol` 新增 `doc` 字段，`impor lint` / 跳转 / 改名行为不变。
  - LSP：`textDocument/hover` 在签名与类型说明后追加文档注释（引用块），文档与代码在同一处可见。
  - 新命令 **`impor doc FILE [-o API.md]`**：从 `##` 注释生成 Markdown API 文档（按模块→函数/类→方法组织，UTF-8 + LF）。
  - 编辑器：`##` 文档注释独立配色，与普通注释一眼区分；`impor fmt` 原样保留文档注释行。
- **新增回归** `tests/test_v43.py`（19 项）：词法/解析/解释器/C 后端 exe/LSP hover/`impor doc`/`lint --style`/fmt 保真/关键字仍 120。
- **同步更新**：`docs/语言规范.md` 新增第 20 节（文档注释）与第 21 节（写法收敛）；版本四处真源 → 3.5.0；`imporc` 按新种子重建。
- **已知边界**：`imporc` 仍按 v3.3 语法子集工作，不认 `##` 文档注释（带文档注释的脚本走 `impor run` / `impor build`）；文档注释是纯文本，不解析 Markdown 结构。

### v3.4.0（语法宽容层：写法更多样、更少报错 ✅）
- **目标**：同一意图允许多种写法，常见笔误不再让整份文件解析失败。所有新写法在词法/文法层**降级成既有 AST 节点**，解释器 / Python 转译 / C 后端（`impor build` 独立 exe）行为一致；关键字总量仍是 120，语义零变化。
- **语句**：`print "hi"`（省略括号）、`;` 语句分隔（多余分号忽略）、单行块体 `if x: a(); b()`、`var x: int = 5` / `def f(a: int) -> int` 类型注解（3.8.1 起在 `run/compile/build` 入口做轻量静态检查，不再是解析后直接丢弃）、`var x` 省略初始化、`x := 1`、`x++`/`--x`（含 `arr[i]++`、`obj.n--`）、`&&` / `||` / `<>`。
- **块与条件**：`if x then { }` / `if x 则 { }`、`unless x { }`（`除非` 同义）。
- **循环**：`for i = 0; i < 3; i++`（C 风格计数循环，`<=` 含上界）、`for i in 1..5`（闭区间）、`for each x in 表`、`for x of 表`、`do { } until cond` / `do { } while cond`（降级为标志变量 + while，块体只出现一次，`continue` 仍会重新判定条件）。
- **函数/类/调用**：`func f a, b { }` 参数省略括号、`class 乙 extends 甲`、`new 盒` 省略空括号、`super.m()` 等价 `super().m()`。
- **异常与续行**：`catch { }` 不写异常变量、`catch (e) { }`；圆括号/方括号未闭合期间换行不再结束语句（`add(1,\n 2)`、多行数组字面量）。
- **中文软别名**：`打印` / `输出` → `print`，`然后` / `则` → 块引导，`除非` → `unless`，`每个` → `each`，`继承` → `extends`（在词法层改写成普通标识符，故不能同时用作变量名）。
- **C 后端**：补齐 `Block` 语句生成；全局/局部符号收集递归进入块与循环体（修复 `do` 块新变量 `undeclared`）。数字扫描不再把 `1..5` 拆成 `1.` 与 `.5`。
- **新增回归** `tests/test_v42.py`（32 项）：解释器 + C 后端 exe 实跑双路验证。
- **已知边界**：`imporc`（自举编译器）仍按 v3.3 语法子集工作，宽容写法请走 `impor run` / `impor build` / `impor compile`；类型注解不做检查；参数默认值、变参尚未支持。

### v3.3.0（一致性收敛 + 跨后端 conformance ✅）
- **本版不加新功能**：只修"三端不一致"和"会崩的地方"。10 项 P0/P1 清单全部落地并逐条验证。
- **① `input()/inputstr()` 缺省参数**：探测确认 v3.2 已具备（`builtin_input(prompt="")` / `builtin_inputstr(prompt="")`），本版无需改动。
- **② `args()` 放开**：探测确认 `main` 已接 argv（`python Impor.py run 脚本 aa bb` → `ARGS= ['aa','bb']`），无需改动。
- **③ Unicode `len`/下标按字符**：`impor/c_runtime.c` 新增 `rt_u8len`（按 UTF-8 编码单元计数）、`rt_u8_at`、`rt_index`（支持负下标与越界报错）；`rt_len` / `rt_strcut` 改为按字符偏移；`ctrans.py` 的 `IndexGet` 改发 `rt_index`；`parser.py` 允许字符串字面量直接下标（`"你好"[1]` 此前被误解析为 `print` 第二个参数）。修复前后：`len("你好")` 6→2、`"你好"[1]` 乱码→`好`、`strcut` 乱码→`好世`。
- **④ 数值统一（int64）**：C 运行时 `rt_ck_add/sub/mul` 做 LLONG 上下界溢出检测，溢出报 `整数溢出：超出 64 位有符号整数范围`；`rt_neg` 补 LLONG_MIN 检查；`rt_isnum/rt_num/rt_int` 纳入 `V_BOOL`。`9223372036854775807 + 1` 从静默回绕 `-9223372036854775808` 变为明确报错。
- **⑤ 三处语义对齐（以解释器为准）**：`1 == true` → `true`（布尔参与数值比较）；`1 + "a"` → `TypeError: 不能将字符串与数字相加（请用 str() 显式转换）`（不再隐式拼接成 `1a`）；`"ab" * 3` → `"ababab"`（`rt_str_repeat`，次数 ≤ 0 得空串，>200MB 报内存不足）。
- **⑥ `gc()`/`mem()` 口径可解释**：`MemoryManager` 新增 `last_freed` / `freed_total`，`stats()` 增 `freed`/`freed_total`/`live_scope`；`report()` 与 `gc()` 文案明确说明"list/dict/str 由 CPython 引用计数即时释放，不进入存活采样，因此 `set x = 0` 后存活数不变是正常的；gc() 处理的是循环引用与解释器缓存"。
- **⑦ `conformance/` 三后端逐字节比对**：`conformance/cases/` 100 个脚本 + `conformance/run_conformance.py`（解释器 / C 后端 / 自举编译器三路径，stdout 逐字节比对，`--ci` 失败即非零退出）+ `conformance/README.md`；接入 `tests/test_conformance.py`（随 `run_tests.py` 与 GitHub Actions 执行，非 Windows 自动跳过）与 `.github/workflows/ci.yml` 的 Windows job，并作为发版门禁。首次运行即抓出 4 个真实不一致：imporc 缺 `div`/`type` 内建、`set arr[i] = v` 下标赋值不支持、字符串下标走旧 `rt_arr_get`。修复 imporc 后 23/23 全绿，3.6.17 扩展到 100/100。
- **⑧ C 编译失败转 Impor 诊断**：`ctrans._gen_block` 为每条语句前置 `/*@L<行号>*/` 标记；`cli._render_c_error` 解析 tcc 的 `文件:行:列` 报错，向上回溯最近标记映射回 Impor 行，输出 `C 编译错误: 第N行 列M` + 源码行 + 指示符 + 提示（无法映射时保留原始消息）。
- **⑨ 递归上限 ≥3000**：`Interpreter.__init__` 设 `sys.setrecursionlimit(200000)`；`_call_closure` 在 3200 层显式抛 `RecursionDeep`，避免树遍历解释器把 C 栈撞成整进程段错误。快速后端与树遍历统一为同一上限 3200 层，脚本里有没有 class 不再改变递归上限。`wrap_py_error` 把原生 `RecursionError` 也映射为 `RecursionDeep`（"递归过深：超出解释器栈上限（约 3200 层）"）。
- **⑩ 仓库卫生**：新增 `LICENSE`（MIT）、`CHANGELOG.md`、`.gitignore`；清理根目录遗留产物（`imporc_args.txt`、`imporc_self_raw.exe`、`out2.c` 与本轮探针脚本），保留 `_setstack.py` / `_diag2.py` 两个历史工具。
- **imporc 同步**：自举编译器跟随 `rt_index` / `div` / `type` / `set 数组[下标]` 四类缺口修复，重建为 184832 字节（8MB 栈），A→B→C→D 自举闭环逐字节一致。
- **路径含空格修复**：`imporc_args.txt` 原按空格切分参数，导致放在 `D:\...\Impor(I++) 3.3.0\` 这类含空格目录时脚本路径被截断、报 `fileread: 无法打开文件`（三端比对因此全 BUILD_FAIL）。imporc 现支持双引号包裹参数，runner 也按 `build "路径" -o "路径"` 写入，桌面镜像目录内复跑 20/20 全绿。
- **新增回归** `tests/test_v41.py`（13 项）：Unicode 字符语义、语义对齐三例、1000 层递归与超限诊断、内存口径文案、tcc 错误反查（真实 tcc 编译非法 C 验证 `第3行` 映射）、C 后端溢出报错与 Unicode 对齐。

### v3.2.0（自动内存管理 + 高级编辑器 ✅）
- **解释器自动内存管理**：新增 `impor/memory.py` 的 `MemoryManager`——分配计数、累计字节估算、峰值、存活对象按类型分类、阈值（默认 20000 个对象）自动回收、手动 `collect()`、模块加载失败对象随回收清理；`impor/interpreter.py` 全程接线（数组/字典/实例/闭包/模块等分配点）。
- **四个内存内建（三端可用）**：`mem()` 打印并返回当前内存摘要；`memstats()` 返回结构化字典（alloc/peak/live/collections/threshold/auto）；`gc()` 立即回收并返回回收次数；`autogc(N)` 设置自动回收阈值（0 关闭）。解释器、C 后端（`ctrans`）、自举编译器（`imporc`）均可调用。
- **C 运行时内存管理**：`impor/c_runtime.c` 新增全局分配计数/累计字节/峰值、托管数组注册表（`rt_arr_reg` / `rt_arr_unreg`，释放时自动注销）、字符串字面量驻留池（`rt_intern` + `v_str_lit`，同一字面量只分配一次）；`mem()` / `gc()` 在独立 exe 中同样输出「分配 N 次 / 累计 B 字节 / 峰值 / 托管数组 / 驻留常量」。
- **新增 `impor mem` 子命令**：无参显示进程内存（RSS + Python gc 分代计数）、存活分类、默认阈值、内建清单与用法；`impor mem 脚本.impor` 先运行脚本再输出「运行前存活 → 运行后存活 / 本次分配 / 峰值 / 回收次数」与完整内存报告。
- **编辑器全面重构为 Code Studio（`impor/editor.py` 整文件重写）**：深色主题（19 色 token 表，主色蓝 `#7aa2f7` + 橙 `#ff9e64` + 绿 `#9ece6a`，不使用大面积紫/靛）；Windows 11 DWM 深色标题栏 + 圆角 + 边框/文字色定制，高 DPI 感知，Cascadia Code / JetBrains Mono / Fira Code 字体优选。
- **高级界面**：自绘圆角按钮（ghost/primary/success/danger 四态）、左侧资源管理器树（examples/docs/bootstrap/std/tests 一键展开）、多标签 Notebook（脏标记、关闭确认）、输出/问题/变量/内存四联底部面板、状态栏（版本、文件名、Ln/Col、编码、内存与分配实时刷新）、命令面板（`Ctrl+Shift+P`，26 条命令含 10 个示例）、查找条（`Ctrl+F` 高亮全部命中）。
- **编辑体验**：token 级多色高亮（关键字/流程词/内建/字符串（含三引号）/数字/注释（斜体）/函数名/类名/声明符/运算符）、当前行高亮、括号配对、行号栏、自动缩进（`:` 或块语句开头 +4 空格）、`Tab` 块缩进 / `Shift+Tab` 反缩进、`Ctrl+D` 复制行、`Ctrl+/` 切注释、`Ctrl+G` 跳行、字号缩放。
- **运行与构建**：默认后台线程运行（UI 不冻结，输出经队列 80ms 批量回显），调试模式走主线程同步 + 步进面板（调用栈/局部变量/步入步过步出/继续）；`构建` 按钮后台调用 `impor build` 产出独立 exe；`编译为 Python` 一键转译。
- 新增回归测试 `tests/test_v40.py`（13 项）：MemoryManager 计数/阈值/清理器/开关、解释器四内建、模块清理钩子、C 后端 `mem/gc` 独立 exe、`impor mem` 无参与带脚本、编辑器静态特征与 Tk 可用时的控件冒烟。
- 编辑器 UI 自测（独立脚本 14 项）全部通过：多标签、行号、多色高亮、括号配对、查找高亮、自动缩进、内存面板、命令面板 26 条与过滤、后台运行输出、内存内建、无紫色主题。

### v3.1.0（三端合一 ✅）
- **三端语法能力对齐**：主解释器 / C 后端（`impor/ctrans.py`）/ 自举编译器（`bootstrap/imporc.impor` → `impor/imporc.exe`）在 class、数组迭代、流程控制上语法能力对齐，同一负载三端输出逐行一致。
- **imporc 补齐语法**（`bootstrap/imporc.impor` 1302 行）：新增 `match/switch`（含 `case/default` 与 `else` 分支）、`try/catch/finally`（支持 `catch 类型` 与 `catch 类型, 变量` 两种写法、异常类型分派、`finally` 必执行、未捕获时 `rt_throw_again` 重抛）、`repeat N`、数组 `for-in`、数组字面量 `[..]`（→ `rt_arr_of`）、`range(N)` 表达式（→ `rt_range`）；`gen_block2` 统一消费 `{}` 与缩进两套块语法。
- **C 后端 class 支持**：CGen 新增类方法生成（`static Val fnN(Obj *self, Val *args)`）、方法表 / 字段初始化器 / 类注册 `reg_class`；`new X(...)` → `rt_new`、`obj.方法(...)` → `obj_call`、`super().方法(...)` → `rt_supercall`、`obj.属性` 与 `obj.属性 = v` → `rt_objget` / `rt_objset`、方法内 `self` → `lself`。
- **C 后端数组 for-in**：`for x in 数组` 从"仅支持 range(...)"放开为通用数组迭代（`rt_arr_get` 逐元素），与解释器 / 自举编译器输出一致。
- **8MB 栈补丁（关键修复）**：`rc=3221225725`（0xC00000FD STACK_OVERFLOW）根因是 tcc 生成的 PE 默认 `SizeOfStackReserve = 1MB`，大文件编译越界崩溃且崩点随代码增量漂移。新增 PE 头栈扩容（`_setstack.py` 独立工具 + `bootstrap/selfhost_check.py` 内联 `patch_stack_exe`），改为 8MB 后编译稳定、无副作用。
- **三端一致性审计**：同一负载分别经主解释器 / `impor build`（C 后端）/ `imporc` 编译并运行，逐行比对输出——class 继承 super 与数组 for-in 两项**三端完全一致**；`match/try/repeat` 负载主解释器与 imporc 一致（C 后端暂不支持 match/try/exception，留待 v3.2）。
- **自举闭环复验**：A→B→C→D 逐字节一致（B 产物 76006 字节、52 个函数；比对正文 74660 字节）；8MB 栈补丁已进入闭环流程，`impor/imporc.exe` 升级为 8MB 栈自举版。
- 新增回归测试 `tests/test_v39.py`（9 项）：解释器 class/数组 for-in/match/try、C 后端 class/数组 for-in、imporc match/try/repeat/each、三端同负载逐行一致。

### v2.19.0（完整自举独立编译器 ✅）
- **完整自举独立编译器**：`imporc`（用 Impor 自身写成的编译器，`bootstrap/imporc.impor`）升级为完整语言编译器——支持 `class` 类定义、继承、`new` 构造、`super()` 父类调用、`self/this`、属性链读写（`.name` / `.方法()`）、属性赋值、`//` 行注释、`important` 头部行自动剥离；`imporc.exe` 为原生 C 编译的**独立可执行文件**，编译与运行全程不依赖 Python。
- **class 面向对象优先**：对象系统运行时（`impor/c_runtime.c`，与 C 后端共享）支持继承链方法查找、`__init__` 构造器、字段初始化器、`super()` 沿继承链调用、对象属性读写；`examples/oop.impor`（动物/狗/猫 继承示例）经 `imporc` 编译后输出精准匹配。
- **imporc CLI**：`imporc.cmd run|build 脚本.impor [-o 输出.exe]` 与 `imporc version`；`imporc run oop.impor` 一条命令完成编译+运行，`imporc build` 产出独立 exe。
- **自举闭环复验**：A→B→C→D 三代逐字节一致（产物 65776 字节、50 个函数），编译器已用自身语言实现；旧 v2.14 引导器备份于 `bootstrap/imporc_seed_v214.exe`。
- 回归：**352 项中 349 项通过**，仅 3 项 tkinter 沙箱环境预期失败（与 v2.18 基线一致）。

### v2.18.0（看得见的新功能：2 项）
- **大括号代码块全面可用（可省略缩进）**：`{` 与语句同行、`{` 单独一行、顶层 `{...}` 程序块均可解析；`if x > 0 { print(x) }` 单行块、嵌套块、C 后端编译全部支持——缩进与花括号两套写法自由混用。
- **历史关键字规模（v2.18 记录）**：当时保留字为英文 58 + 中文 62；中文关键字已在 v5.2 全部移除，3.8.1 只保留纯英文保留字。英文新增 `fn`（函数定义别名，软关键字）、`let / local`（变量声明）、`elif`（elseif 别名）、`repeat`（次数循环，软关键字）、`each`（for 别名）、`with / until / async / await / do / of`（预留）、`case / default`（match 分支）、`nil / null / none`（空值字面量）、`static / readonly`（const 别名）、`yield`（预留）、`this`（当前对象别名）；中文新增项仅作为历史记录，不再参与关键字识别。
- 空值字面量全链路：`none / nil / null` 与中文 `空 / 无` 在解释器与 C 后端均产出 NULL 值（`v_null()`），`print` 输出 None。
- `this` 可在类方法内替代 `self`：`this.x`、`this.方法()` 与 class 大括号写法组合可用。
- 冲突修复：新增关键字与 std 标准库 / 示例标识符的 3 处冲突全部解决——`fn`、`repeat` 改为**软关键字**（仅语句位置特殊解析，可继续作函数名、参数名、内建调用），中文"生成"改映射 `产出`，避免撞 2048 示例函数名。
- 全量回归：**345 项中 342 项通过**，仅 3 项 tkinter 沙箱环境预期失败（与 v2.17 基线一致）；自举闭环 B/C/D 三代逐字节一致。

### v2.17.0（看得见的新功能：8 项）
- **C 后端数组支持（大型游戏独立 exe 的根基）**：新增列表字面量 `[1, 2, 3]`、下标读写 `a[1]` / `a[0] = 9`、`len()` 支持数组；12 个数组内建全端同步——`arrfill(大小, 值)`（大小在前）、`arrappend`（原地返回同数组）、`arrpop`（空返 None）、`arrfind`（返回下标或 -1）、`arrdup` / `arrrev`（返回新数组）、`shuffle`（原地）、`max_arr` / `min_arr` / `sum_arr` 聚合、`list()` 构造（0 参空数组 / 数字参定长零数组）；解释器与 C 后端 exe 输出逐行一致。
- **游戏示例 examples/game_2048.impor**：纯计算 2048 自动演示（16 格一维数组、随机 24 步策略、棋盘渲染、得分累计），`python Impor.py build examples\game_2048.impor` 产出**独立 exe**（原生 C 编译，运行不依赖 Python）。
- **修复 C 后端 2 个作用域 BUG**：顶层 while/if 体内的 `var` 声明漏收集（g4 undeclared）；函数内 `set 名字 = ...` 就近更新被误当局部（set 得分不生效）。现已与解释器 v2.10 作用域语义一致。
- 新关键字 **`const`**（只读常量）：`const 速度 = 100` 后赋值/复合赋值报"不可修改"；重复声明报错。
- 新关键字 **`switch`**（`match` 的别名）：`switch x { case 1: ... default: ... }`，中文关键字 `开关`。
- 放宽语法：**数字下划线** `1_000_000` / `10_000_000_000` / `3.141_592`（词法器与 C 编译 ccomp 双端支持）。
- 性能：**bench 复测加速比 x4.1**（v2.16 基线 x3.2）——rt_add 字符串拼接改单次 malloc+memcpy，数组支持新增大批原生 C 内建。
- 新增回归测试 tests/test_v36.py（13 项）：数组解释器/C 后端一致性、const 只读/重复声明、switch 别名、数字下划线、2048 冒烟（解释器 + build exe）。

### v2.16.0（看得见的新功能：6 项）
- 新增 `div(a, b)` 整除内建（向下取整，对齐 Python `//` 语义；除零报错）：解释器 builtins、C 后端 ctrans、共享运行时 rt_idiv、纯 C 编译器 ccomp 四端同步，解释器与独立 exe 输出一致（`div(7,2)=3, div(-7,2)=-4, div(7,-2)=-4, div(-7,-2)=3`）。
- REPL 新增内建帮助系统：`?` / `help` 显示帮助菜单；`help()` 列出全部 59+ 内建函数中文说明（builtins.py 新增 BUILTIN_DOCS 速查表）；`help(print)` / `help("print")` 查看单个说明；帮助类命令不记入历史。
- 新增 `impor env` 环境诊断命令：输出平台/Python 版本/源码根/C 编译器 imporc.exe/tcc/自举种子与产物 B/REPL 历史/包缓存路径。
- 新增 `impor demo` 一键示例命令：无参列出内置示例目录（hello/cn/grade/fib/guess/snake），`impor demo 名字` 直接运行。
- 新增 `impor bench` 性能基准命令：同一算术循环脚本分别用解释器与 C 后端原生 exe 运行，输出耗时与加速比（实测 100 万次循环约 x3）。
- `impor version` 增强：显示平台 / Python / C 后端可用性 / 自举状态（imporc.exe、imporc_self.exe）。
- 新增回归测试 tests/test_v35.py（12 项）：div 语义（含负数/除零）、REPL 帮助注入式、env/demo/bench/version 命令冒烟。

### v2.15.0（自举闭环达成 ✅）
- 自举种子 bootstrap/imporc.impor 扩展完成：缩进 token（IND/DED + 多级缩进挂起补发）、func/if/elif/else/while/return/表达式语句完整转译、函数预登记与前向声明、函数段/全局段/主流程分段输出、rt_truth/rt_cmp 包装、l_/g_ 变量分流。
- 修复 3 个自举内核 BUG：top_ind() 尾切片空值导致缩进栈失控；表达式内无参函数调用 NULL 缓冲；esc_str 转义序列（\n/\\/\"）错误输出导致字符串换行语义损坏。
- 自举闭环验证通过：imporc.exe 编译种子 → B；B 编译种子 → C；C 编译种子 → D；B/C/D 三代产物逐字节一致（39868 字符 / 40450 字节，46 个 C 函数）。
- 新增 bootstrap/selfhost_check.py 一键闭环验证脚本（A→B→C→D 四步对比）。

### v2.14.0（自举种子 + 内核修复）
- 新增自举种子 bootstrap/imporc.impor：用 Impor 语言编写的小型编译器（词法 scan + 递归下降表达式 + 顶层 var/set/print 语句转译，产物调用 tcc 生成独立 exe）；解释器运行种子（Step0）与 imporc.exe 编译种子（Step1）均通过。
- 新增自举工具内建 fileread/filewrite/system/exit，解释器、C 后端、C 运行时三端同步注册。
- 修复 lexer 两处内核 BUG：字符串转义 `\"`/`\'` 提前截断；字符串插值 `{`…`}` 跨字符串字面量配对。
- 修复 C 编译器 imporc 两处内存崩溃：无参数函数调用在临时缓冲为 NULL 时的 memcpy/写入越界（表达式内调用即崩溃，如 `"a" + f()`）。
- 补齐 C 运行时 rt_exit；共享运行时抽取为 impor/c_runtime.c（ctrans.py 优先读取，内嵌副本兜底）。
- 新增 impor/ccomp.c（纯 C 编译器）与 impor/imporc.exe：支持顶层函数/控制流/27 个内建的 Impor 子集转译，IMPORC_TRACE 调试钩子。

### v2.13.0（C 后端：脱离 Python 宿主）
- 新增 C 后端 impor/ctrans.py：Impor AST → C 源码，内嵌 Val 值模型运行库（数值/字符串/布尔/空值，类型保持、惰性 and/or、取正模、浮点 .0 格式）。
- 新增 impor build FILE.impor [-o OUT.exe] 命令：随包分发 tcc，一键产出独立可执行文件；运行不依赖 Python。
- 修复 C 后端 3 处 BUG：FStr 插值表达式段用 
.exprs 对齐（此前误用源码文本）；cmd_build 缺 io/subprocess 导入；布尔输出对齐 True/False。
- 解释器新增 abs/min/max 内建，与 C 后端语义一致（clamp 对齐为全 float）。
- 新增回归测试 tests/test_v34.py（9 项）：C 后端 exe 与解释器输出逐字符一致、不支持特性友好报错、build 命令注册。

### v2.12.1（BUG 修复版）
- 修复：impor run xxx.impor --smoke 不再挂起（input/inputstr 在 smoke 模式自动返回，交互示例可秒退）。
- 修复：转译器 import pkg.sub 与解释器一致，绑定顶层包名（std_demo 编译产物可运行）。
- 修复：impor test 默认同时扫描 tests/ 与 examples/，找不到测试时给出提示。
- 修复：REPL 支持中文块头关键字（函数/如果/当/类/尝试…）多行输入；支持 exit()/quit() 退出。
- 修复：转译器 rom . import x 纯相对导入按子模块加载，与解释器行为一致。
- 新增回归测试 tests/test_v33.py（10 项，全量 300 项测试通过）。
