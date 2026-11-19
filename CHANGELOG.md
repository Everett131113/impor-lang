# Changelog

Impor (I++) 的版本变更记录。每条只列**用户可感知的变化**；详细说明见 `README.md` 的「更新日志」。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本（主版本.次版本.修订号）。

> **版本口径（2026-11-16 起）**：对外发布只有当前 `VERSION` 一条线（现为 `3.11.0`），`VERSION` 与 `Impor.py` 的 `__version__` 为真源。`v6.0` / `v6.2` 是历史内部路线与性能标签，`7.3.0` 是已归档的历史实验包，都不作为安装包版本。详见 `docs/版本对齐与语法冻结-3.6.6.md`。

## [3.11.0] - 2026-11-18

### 新增：C ABI 矩阵（4 格 push 门禁 + macOS 2 格手动）

- `.github/workflows/c-abi-x64.yml` 每次 push 跑四格：win32（内置 tcc）、win64（MSVC）、
  linux64（gcc -m64）、linux-arm64（aarch64 交叉）。
- macOS arm64 / x64 两格在 `.github/workflows/c-abi-macos.yml`，`workflow_dispatch` 手动触发。
  当前账号的 macOS runner 分钟数不可用，push 触发会排队后被取消，因此不挂在 push 门禁上。
- 每格校验产物机器类型（PE `0x14c` / `0x8664`、ELF x86-64 / AArch64、
  Mach-O x86_64 / arm64）与四个导出符号 `impor_add / impor_sub / impor_mul / impor_greet`。
- 新增 `tools/check_artifact.py`，本地与 CI 共用同一套 PE / ELF / Mach-O 解析。

### 新增：`tools/compare.py` 多语言真跑对照

- 同一份 `benchmarks/compare/case.json`（1000 整数求和）分别用 Impor / TCC-C / Lua 5.4 /
  Python(PyInstaller onefile) / Go / Node 跑；固定 6 行，缺运行时填「未测量」。
- 表分「总耗时」与「纯启动」两列：总耗时 = 启动 + 读 JSON + 求和 + 退出；纯启动 = 同产物走空路径。
- 本机结果：Impor 120.5 KB / 21.44 ms / 22.41 ms / 6.0 MB / 无依赖；TCC-C 2.5 KB；
  Lua 477.5 KB（含运行时）；Python onefile 7.8 MB / 1.26 s；Go 1.9 MB；Node 76.8 MB。
  README 只宣称“体积 + 零依赖 + 可被宿主加载”三项组合，不宣称启动快于 TCC-C / Lua / Go。
- `--python-mode {auto,onefile,onedir,system}`：auto 现场用 PyInstaller 构建到临时目录，
  失败 exit 2，不静默降级成 system。
- 默认只写 `compare.local.json` / `docs/compare.local.md`；`--publish` 才覆盖正式文件。
- `--strict` 任一行未测量即 exit 1；`--runs < 10` 表头标「样本不足」，strict 直接拒绝。
- JSON 补 CPU / 内存 / OS build / commit / case sha256 / 各运行时版本；构建产物全部进临时目录。

### 修复：5 分钟陌生人测试在默认 PowerShell 下不可用

- `tools/five_minute_check.ps1` 改为 **UTF-8 with BOM**，Windows PowerShell 5.1 与 PowerShell 7
  都能直接按 `powershell -File` / `pwsh -File` 运行；此前无 BOM 会被 5.1 按 ANSI 读成乱码并立刻 exit 1。
- `conformance/run_conformance.py` 新增 `--sample N`（排序后等距抽样，去重补足），
  `five_minute_check.ps1` 默认抽 30 个用例，`-Full` 跑全量。本机 5.1 实测 31.01 s，pwsh 29.58 s。

### 文档：私有仓库的 CI 口径

- README 移除会 404 的 shields 徽章，新增「CI 证据」一节，如实写明仓库当前为 private、
  匿名 API / 徽章 / Actions 运行页对外不可核验。
- `docs/ci-evidence.md` 与两张 Actions 截图入档；只有仓库转 public 后，才把徽章作为对外证据。

### 新增：`impor bench-spec`

- `impor bench-spec FILE.impor [--runs N] [--json]` 输出四项：
  exe 体积、冷启动均值、常驻内存、构建耗时。
- README 首页规格表只列体积 / 启动 / 依赖 / 可被 C 调用四列，数字可用命令复现。

## [3.9.0] - 2026-11-18

### 新增：`impor build --lib --c-source`（64 位入口）

- 新增一级产物：`impor build --lib --c-source lib.impor -o out/`，产出
  `lib.c` / `lib.h` / `lib.def` / `build_msvc.bat` / `build_gcc.bat`。
- 内置 tcc 只产 **32 位（i386）**；需要 x64 DLL 时由用户机器上的 `cl` 或
  `gcc -m64` 编译源码包，不内嵌 w64devkit。
- `build --lib` 结束打印产物位宽；`impor sys-check` 新增“产物位宽”项。
- `lib.h` 不随位宽变化，`demo.c` 不改即可链接 x64 `lib.lib` / MinGW。
- `--c-source` 生成的 `lib.c` 不再包含 `main`，纯库源码；普通 exe / `--lib` 仍生成 `main`。
- 导出包装改用 `IMPOR_API` 宏：Windows 为 `__declspec(dllexport)`，POSIX 为 `visibility("default")`，同一份源码可交给 MSVC 或 gcc。
- 本机无 `cl/gcc`，x64 编译未实测；已用 tcc 对 `lib.c` 做过 32 位自检。
- 新增 `.github/workflows/c-abi-x64.yml`：`windows-latest` 跑 MSVC `cl /LD`，`ubuntu-latest` 跑 `gcc -shared -m64`，校验 PE/ELF 为 x64 且四个导出符号齐全。

### 新增：C 后端 FFI（`ffi_open` / `ffi_call`）

- 不新增语法，解释器已有的 `ffi_open` / `ffi_call` 在 C 后端可用。
- Windows `LoadLibraryA` + `GetProcAddress`，POSIX `dlopen` + `dlsym`。
- 参数编组 `int → long long`、`bool → int`、`float → double`、`cstr → const char *`，
  最多 8 参；返回值支持 `int/float/bool/cstr/void`。
- 允许清单 `.impor/ffi_allow.json` + `IMPOR_FFI_ALLOW`（逗号或分号），
  C 后端编译期烘焙，运行时不可扩。
- 未登记库、超 8 参、动态 `argtypes` 编译期 exit 3；C 后端明确拒绝 `ptr`、结构体、回调。
- 修复自举编译器内嵌运行时的 `g_ffi_allow_n` 未声明：新增
  `IMPOR_FFI_ALLOW_BAKED` 宏与空清单回退。

### 修复：两个实测体验伤

- 允许清单分隔符同时接受 `;` 与 `,`；`FFIAccessError` 回显当前生效清单。
- `FFIAccessError` / `FFIError` 的诊断解释按异常类分派，不再一律套“private 符号可见性”。
- **位宽诊断**：`impor run` 的宿主是 64 位 Python，`impor build` 默认产物是 32 位。加载前读 PE `Machine` 字段，位宽不匹配抛 `FFIArchError`（不再套成 `FFIError` 签名不匹配）；解释器与 C 后端都做了这项检查。`impor sys-check` 新增“解释器位宽”，与“产物位宽”并列。
- **自举编译器同步**：用 3.8.4 自举重建 `bootstrap/imporc.impor`，补 8MB 栈后写入 `impor/imporc.exe`，`imporc version` 现在自报 3.9.0，不再与 `VERSION` 脱节。
- **关键字计数断言**：`yield` / `with` 在 3.8.4 已进入保留字表，`KEYWORDS` 实际 58；两条旧断言从 56 更新为 58，避免“全量测试假红”。

### 测试

- 新增 `tests/test_v39x_ffi.py`：strlen、跨语言回环、未登记库、9 参、分号清单（64 位 `System32\msvcrt.dll` 正例）、C 源码包（断言无 `main`）、位宽输出、位宽不匹配 `FFIArchError`，共 8 条。

## [3.8.4] - 2026-11-18

### 修复：不再静默丢掉语义（A 组）

- **`yield` / `with` 编译期拒绝**：两者已进入关键字表；解析到语句位置时直接报
  `yield/with 尚未实现（v3.9 计划）`，exit 2；不再被当成普通标识符解析后输出 `None`。
- **默认参数 / 变参 / 可选参数**：`func f(a, b = 1)`、`func f(a...)`、`func f(a?)`
  在参数解析阶段直接报 `尚未实现（v3.9 计划）`，exit 2；不再以硬语法错误或静默忽略处理。
- **列表 / 字典推导式**：`[x for x in ...]`、`{x: x for x in ...}` 在字面量解析阶段
  直接报 `尚未实现（v3.9 计划）`，exit 2。
- **测试锁定**：`tests/test_v384.py` 对 `yield`、`with`、默认参数、列表推导式、字典
  推导式逐个断言“必须 exit 2 且提示 v3.9 计划”。

### 新增：C 后端 `try/catch/finally`

- `ctrans._gen_try()` 生成 `g_jmp_n++` + `setjmp(g_jmp_stack[...])` 跳转栈，catch 变量
  绑定为 `rt_err_obj()`；成功/异常路径都会把 `g_jmp_n` 恢复到进入 try 前的值。
- 运行时除法（`rt_div` / `rt_idiv`）从 `rt_abort` 改为 `rt_throw("ZeroDivisionError", ...)`，
  使错误可被 try/catch 捕获，而不是直接 abort。
- `_collect_writes` / `_iter_nested_defs` / `_collect_globals` 递归进入 try/catch/finally，
  try 内声明的局部变量与闭包正常分配。
- 验收：`impor build` 含 try/catch 的源码，运行后 catch 打印
  `CAUGHT ZeroDivisionError division by zero`，并以 `exit(7)` 返回 7。

### 本版未交付（如实列出）

- **B 组未实现**：默认参数 / 变参仍只“明确拒绝”，真生成器（yield）、推导式、`with`
  资源协议均未实现；路线为 v3.9。
- **C 组未实现**：变量类型稳定化、容器元素类型、null 安全规则未实现。
- **D 组未实现**：`std/net`、`std/sql`（SQLite）、公共包索引未实现。
- **E 组未实现**：5 项真实基准（JSON 1MB / 字典 100 万 / 字符串 10 万 / fib(30) /
  map-filter-reduce）未写入 README 门禁。
- **imporc 中文绝对输出路径**仍失败（`filemv E1099`）；`Impor.py build` 正常。


## [3.8.3] - 2026-11-17

### 修复：门禁不再接受虚假成功

- **UTF-8 BOM**：`fileread` 统一剥离文件开头的 UTF-8 BOM，imporc 不再把 BOM 报成 `无法识别的语句 [ \ufeff ]`；带 BOM 的 `.impor` 可正常编译。
- **单行/多行 `{}` 块**：imporc 的 `gen_block()` 接受大括号块，`func f() { return 1 }` 与跨行 `{ ... }` 都能通过，不再报 `无法识别的语句 [ { ]`。
- **失败不再返回 0**：语法编译失败、tcc 链接/编译失败都会 `exit(1)`，并且不生成产物；conformance 门禁可以真正捕获失败。
- **PyQt6 缺失时跳过**：`TestKernel3Preview` 改为按 `import PyQt6` 是否成功决定 skip，不再用 `os.name == "nt"` 误判为可运行。

### 新增：C ABI v2

- **字符串所有权**：新增 `impor_free_str(const char *)`；DLL 返回的字符串由 DLL 新分配，调用方必须用该函数释放，传入 `NULL` 安全。
- **类型映射扩展**：`size_t` 映射为 C `size_t`；`handle` / `ptr` 映射为 `void *`，用于不透明句柄和结构体按指针传递。
- `.h` 头文件加入 `<stddef.h>`，`.def` 导出表包含 `impor_free_str`。
- tcc 实测：`size_t` 传参/返回、`void *` 句柄往返、两次字符串返回后分别 `impor_free_str` 均正常。

### 本版未交付（如实列出）

- **MSVC `.lib` 未实测**：本机没有 `cl.exe` / `lib.exe` / `link.exe`，也没有 LLVM/MinGW 工具链；`.def` 已生成，MSVC 可用 `lib /def:lib.def /out:lib.lib` 自行生成，但本版不能声称“MSVC 链接已通过”。
- **调用方缓冲区字符串 API** 未实现；本版用 `impor_free_str` 解决所有权问题。
- 命名结构体的强类型指针未实现；本版用 `handle` / `ptr`（`void *`）表达不透明句柄与结构体指针。


## [3.8.2] - 2026-11-17

### 新增：`impor build --lib` 产出 C ABI 共享库

- 新语法 `export func`：解析后把函数标记为导出；`export 名字` / `export {名字}` 的旧写法保持可用。
- `impor build --lib x.impor -o x.dll` 同时产出 `x.dll`、`x.h`、`x.def`：
  - `x.h` 带 `extern "C"`，符号统一 `impor_` 前缀（如 `impor_add`）；
  - 类型映射 `int→long long`、`float→double`、`bool→int`、`str→const char *`；
  - 字符串返回值由 DLL 持有，调用方不要 `free`。
- 实测链路：`impor build --lib lib.impor -o lib.dll` → `tcc demo.c lib.dll -o demo.exe` → 运行输出 `add(2,3)=5 / greet=Hello, Impor / half=3.5`。示例在 `examples/c_abi/`。
- 本版边界（未满足直接报错，不猜测）：仅顶层 `export func`；参数/返回必须显式标注 `int/float/bool/str/void`；不支持默认参数、变参、泛型、类方法导出、`async` 导出。

### 修复：`impor fmt` 不再拆开返回类型箭头

- `_BIN_OPS` 补入 `->`，`def add(a : int) -> int` 不会再被格式化成 `- >`，格式化后的 C ABI 源码仍可直接 `build --lib`。
- `examples/c_abi/lib.impor` 改为 fmt-clean，`impor fmt --check` 通过。

### 变更：C 后端成为唯一发布路径

- `impor build` 是唯一发布出口（exe 或 DLL）；`impor run` / `repl` / `run-dbg` 只做开发期运行与调试。
- `impor compile`（Impor → Python）保留为开发兼容，不再承担发布职责；帮助文案已明确标注“不是发布路径”。
- `imporc` 只做内部一致性探针，不再是对外编译入口。
- C 后端遇到不支持特性时，不再引导用户“改用 Python 后端发布”，只提示开发期用 `impor run` 验证。

### 本版未交付（如实列出）

- MinGW `gcc demo.c -L. -lx` 未实测：本机没有 gcc/dlltool，无法生成/验证 `.dll.a` import library；已提供 `.def` 与 `dlltool` 命令说明，但不把未验证路径写成“已通过”。
- 默认参数、变参、泛型、类方法导出、`async` 导出、字符串所有权/释放 API 均未实现。

## [3.8.1] - 2026-11-16

### 修复：编译产物不再依赖“当前目录恰好是仓库”

- **路径自举**：转译产物头部改为按 `IMPOR_HOME` → 编译时安装根 → 产物目录/父目录 → 当前目录的顺序查找含 `impor` 的安装根，找到后注入 `sys.path`。产物复制到临时目录、干净副本或其他工程目录后仍能运行。
- **失败可诊断**：所有候选路径都找不到运行时，输出“找不到运行时 impor.runtime + 已尝试路径 + `IMPOR_HOME` 修复提示”，并以退出码 3 结束；不再裸抛 `ModuleNotFoundError`。
- **回归背景**：干净副本 `run_tests.py` 的 12 条失败全部落在 `test_*_transpile`，根因就是产物固定 `from impor.runtime import *`。本次修的是根因，不是测试特判。

### 新增：类型注解在编译入口真正检查

- `run` / `run-dbg` / `compile` / `build` 解析后调用 `impor/typecheck.py`，对确定不匹配的字面量赋值和函数返回类型报 `[FAIL] 类型 文件:行:列 …`，退出码 2。
- 边界：只做轻量静态检查，不引入泛型、不做控制流收窄；解释器运行时仍按动态值执行。注解不再“只解析不检查”，但也不是完整类型系统。

### 修复：`imporc` 读取 argv、`-o` 真正生效

- **C 运行时**：`c_runtime.c` 新增 `rt_args_init(argc, argv)`，生成 exe 的 `main` 注入 argc/argv；`ctrans.py` 的 `BUILTINS` 暴露 `args()`。
- **自举 CLI**：`bootstrap/imporc.impor` 优先读 `args()`，无参数才回退 `imporc_args.txt`；支持 `imporc <脚本> [-o 输出]` 与 `imporc run|build <脚本> [-o 输出]`。
- **不再静默**：`-o` 缺输出名、未知参数、`run/build` 缺脚本都打印明确错误并返回 2；成功时真正写到 `-o` 指定的路径，不再忽略参数自编译成 `imporc_self_raw.exe`。
- **可在外目录调用**：新增 C 运行时 `exedir()`，`imporc` 按自身可执行文件目录定位 `impor/c_runtime.c` 与 tcc；在临时目录调用 `imporc hi.impor -o custom.exe` 也能生成并运行产物，不再隐含要求当前目录是仓库。
- **中文目录可用**：Windows 下 `exedir()` 先取 `GetModuleFileNameW` 的宽字符模块路径，再转 UTF-8；`桌面\总Impor文件\Impor(I++) 3.8.1` 这类路径不会再被 ANSI 字节截断或误读，`imporc` 在干净副本里能正常调用 tcc。TCC 自带精简头缺少 `WideCharToMultiByte` 声明，已在运行库内补齐，编译不再出现隐式声明告警。
- **自举位置兼容**：`imporc` 位于 `<安装根>\impor\` 时向上取安装根；位于仓库根或其他目录（自举验证中的 B/C 代）时按当前目录解析运行时，A/B/C 逐字节一致闭环保持通过。
- **语言层**：`exit(code)` 可作为带退出码的内建调用（裸 `exit` 仍是结束脚本）；解释器与 C 后端都按给定码退出。

### 文档：写法规范 / 语言规范与 fmt 对齐

- `docs/写法规范.md` 重写：以 `impor fmt` 实际输出为准，`def`、`while true`、`var` 为推荐；`func`、`loop` 降为兼容；`set` 与裸赋值都保留但推荐 `set`；类型注解改为“编译入口检查”；中文关键字统一写“v5.2 起移除，仅保留中文标识符”。
- `docs/语言规范.md` 收敛开头版本线，删除“3.0.0 规范 + v4.3/v5.6/v6.2/7.3.0 堆叠”的历史叙事；第 17 节从“支持中文关键字”改为“已移除”；第 19.7 节更新类型检查与未实现能力边界。
- `README.md` 更新当前版本与 3.8.1 变更段；历史关键字条目改为“v2.18 历史记录”，不再让人误读为当前仍提供中文关键字。

### 本版未交付（如实列出）

- `yield` / 生成器、列表/字典推导式、默认参数/变参、`with`。
- C 后端 `try/catch`、线程/原子、`.dll` 导出。
- `impor lint --style` 的具体规则实现位置仍未系统整理；本版没有新增 lint 规则。

### 测试

- 新增 `tests/test_v381.py`：产物跨目录运行、类型门禁阻断、`imporc` 的 `version/help`、`-o` 成功路径、`-o` 缺值与未知参数退出码。
- 全量结果见发布记录。

## [3.8.0] - 2026-11-16

### 工作区 2.0：多根、项目树、信任与搜索范围

- **多根工作区**：`.imporproj` 的 `roots` 成为可编辑的真实根列表。资源管理器头部新增 `＋/－`，右键菜单新增「添加工作区根目录…」「移除该根目录」；主根受保护，避免切换工作区时丢配置。根列表写回 `.imporproj`，保留 `ignore`、`search_paths` 与 `format`。
- **项目树**：资源管理器按 `workspace_roots` 逐个挂根节点，常用目录懒加载；新增/移除根后自动重建 `ProjectIndex` 并刷新符号索引。
- **workspace trust**：新增全局 `trusted_workspaces` 列表、受限模式横幅与「信任此工作区」按钮。首次打开未信任工作区只限制执行类操作：终端/任务、运行、构建、项目插件加载被拦截；打开、浏览、搜索、编辑不受影响。信任状态写入 `~/.impor_studio.json`。
- **跨根搜索**：全项目搜索从单根扩展为多根，`search_project()` 接受根列表，按根做路径包含校验、重叠根去重；搜索 UI 直接传入 `workspace_roots`。
- **布局与会话恢复**：配置新增 `layout`，保存并恢复窗口 geometry、左右/上下 sash 位置、main/split 两组标签、活动标签、每个文件的光标行列与滚动位置；兼容旧的 `tabs`/`folds` 配置。

### 编辑内核 3.0：独立 Qt 预览

- **新增 `impor/kernel3.py`**：PyQt6 `QPlainTextEdit` 内核预览，独立窗口运行，不替换现有 Tk 面板。工具栏右侧与命令面板新增「内核3.0 预览」入口；当前文件未保存时打开空白预览，不伪造写回。
- **手感能力**：行号区、当前行高亮、Impor 关键字/字符串/注释/数字高亮、右侧 minimap、`Ctrl+Alt+↑/↓` 多光标、`Alt+Shift+↑/↓` 同列多光标、多光标输入/退格/删除/回车/Tab、`Esc` 清除多光标、滚轮平滑滚动、Qt 原生 IME。
- **大文件**：10.4MB / 10 万行文本在无头烟测中 约 2.3–2.8 秒打开并可编辑；超过 2,000,000 字符自动关闭 undo 以降低内存与卡顿。
- **边界**：这是第一阶段 PoC，不是 Monaco/WebView2 或 QScintilla 的完整迁移；没有 LSP、调试、Git、AI、插件、项目树、主题跟随等 Tk 侧能力。当前机器没有 QtWebEngine、QScintilla、pywebview，Monaco/WebView2 仍需额外依赖或更深 COM 集成。

### 启动默认内容

- **空白标签**：`open_new_tab()` 不再注入 `// 新脚本` / `print("你好，Impor!")` 示例模板；新建标签默认完全空白，显式传入内容时行为不变。

### 测试与未交付

- `tests/test_v380.py` 7 项通过：多根 `.imporproj`、跨根搜索、重叠根去重、越界路径过滤、Windows 信任键规范化、Qt 内核无头烟测、Qt 内核窗口渲染烟测。
- 工作区烟测 `_check_workspace2.py`：2 根加载、受限模式、跨根搜索 2 命中、信任写入、3 根持久化。
- 会话烟测 `_check_session2.py`：main/split 双组标签与活动标签恢复通过。
- 内核大文件烟测 `_check_kernel3_large.py`：10.4MB / 100002 行，最近一次 2.27 秒打开。
- 全量 `python run_tests.py`：**667 项全部通过**（888.298 秒，`EXIT=0`）；首次全量暴露的 `vscode-impor/package.json` UTF-8 BOM 已清除并复跑通过。
- **本版未交付**：内核 3.0 未替换 Tk 主线；Monaco/WebView2、QScintilla、列选择鼠标拖拽、滚动同步、LSP/调试/插件接入均未完成。语言侧 `with`、`yield`、默认参数/变参、解构遍历、半开区间、`guard` 仍未实现。

## [3.7.8] - 2026-11-16

### 语言：defer 延迟清理

- **语法落地**：`defer { ... }`、`defer:` 缩进块、`defer` 换行缩进块三种写法都可解析；`defer` 保持软关键字，`var defer = 1` / `defer()` 不受影响。
- **执行语义**：函数/模块进入时压入延迟帧，退出时按 LIFO 执行；正常返回、异常退出、`exit` 都执行清理。模块顶层 `defer` 在脚本结束时执行。
- **四端边界**：解释器与 Python 转译已支持；C 后端明确报“defer 延迟块（C 后端暂不支持）”，不静默跳过。`imporc` 仍按 v3.3 语法子集工作，不识别 `defer`。
- **静态检查**：`lint` 会进入 `defer` 体检查未定义/未使用变量，不新建作用域。

### 四端一致性：C 后端支持管道

- **`|>` 降级为调用**：C 后端不再拒绝管道，`x |> f(a)` 生成 `f(x, a)`，`x |> f` 生成 `f(x)`；链式管道递归降级。独立 exe 实测输出与解释器一致。

### 文档

- **新增路线图**：`docs/语言能力差距与路线图-3.7.8.md` 列出相对 C / Python 的硬缺口、四端不一致项与分批计划。
- **规范修正**：`docs/语言规范.md` 不再声称 C 后端支持 `try/catch/finally`；新增 `defer` 语义与边界说明。

### 测试

- `tests/test_v378.py` 11 项通过：defer 的 LIFO、异常清理、模块退出、软关键字、嵌套函数/类方法、快速执行路径、体内控制流拒绝、Python 转译一致性；C 后端管道转译/构建运行、defer 明确拒绝。

### 未交付（不计入本版完成项）

- `with`、`yield`、默认参数/变参、解构遍历、半开区间、`guard` 未实现。
- C 侧 `try/catch`、自定义异常、C ABI/库导出、线程/原子/channel、LLVM 与调试信息未实现。
- 编辑器工作区 2.0、AI 逐 hunk 接受/拒绝、LSP call hierarchy/code action 未实现。


## [3.7.7] - 2026-11-15

### 安全跨文件替换

- **未保存缓冲区可选纳入**：全项目搜索新增「包含未保存缓冲区（会一并保存）」复选框。勾选后，dirty 文件以编辑器缓冲区全文参与匹配与替换，不再从磁盘读取旧内容；未勾选时保持原有行为，dirty 文件不可选。
- **明确确认**：选中 dirty 文件时，替换前额外提示“会连同未保存修改一起保存，并重新加载编辑器”；仍先做预览和事务备份。
- **替换后重载**：按实际写入的路径重载普通标签与分块标签，分块标签调用 `reload_from_disk()` 重建行列表并清 dirty，避免窗口内旧文本残留。
- **路径归一化**：缓冲区键、dirty 集合与搜索结果统一使用绝对路径，避免大小写/相对路径造成匹配错位。

### 语言

- **递增/递减方向校验**：`i--` 配 `< / <=` 或 `i++` 配 `> / >=` 在解析期报错，提示会死循环。
- **三端验证**：Python 解释器与 C 后端均实测 `5 4 3 2 1` / `3 2 1`；`imporc.exe` 只更新版本标记，未改其独立解析子集。

### AI

- **输出 token 预算**：AI 设置新增「输出上限」（64–8192，默认 1200），非流式与流式请求体均写入 `max_tokens`；非法值回退 1200。

### 未交付（不计入本版完成项）

- AI 结果 hunk 逐块接受/拒绝、调试 watch/变量修改/attach/后台图形面板、LSP call hierarchy/code action/增量语义 token、`defer`/`with`/`yield`、workspace trust 未实现，留待后续版本。
- C 后端仍不支持 defer/异常清理结构；`defer` 未进入解析器，仍是保留字。

## [3.7.6] - 2026-11-15

### 大文件设置 UI

- **设置面板暴露阈值与模式**：编辑器设置新增「大文件阈值」（256–65536 KB，默认 2000）和「大文件打开」三选一（自动分块 / 始终完整打开 / 打开时询问）。改完立即作用于下一次打开，不需要手改配置文件。
- **持久化与同步**：`large_file_kb` / `large_file_mode` 继续写入 `~/.impor_studio.json`；设置同步拉取时一并刷新面板控件。
- **恢复默认**：恢复默认按钮把阈值还原为 2000 KB、模式还原为自动分块。

### 分块编辑：跨块查找 / 替换

- **全文查找**：`VirtualLargeFilePane.find_next()` / `find_all()` 改为搜索完整行列表，不再只搜 Tk Text 当前 3000 行；命中块外内容会自动跳块并选中，找不到时从头回绕。
- **跨块替换**：查找条新增「跨块替换」，对完整行列表做字面量、不区分大小写的全部替换，替换后刷新当前窗口并标记未保存；普通全文标签仍提示使用 `Ctrl+Shift+F` 全项目替换。
- **不丢未保存编辑**：替换前先 `flush_virtual(force=True)`，把当前窗口最后一次编辑写回完整行列表；含换行的替换文本会重建行列表并更新总行数。
- **当前窗口只标命中**：`find_all()` 统计全文命中数，但只给当前窗口内的命中套 `search` tag，避免给 26 万行一次性打 tag。

### AI 内联助手：流式与取消

- **SSE 流式输出**：新增 `request_suggestion_stream()`，请求体带 `"stream": true`；读取 `data:` 行、拼 `choices[0].delta.content`、遇到 `[DONE]` 或 EOF 结束；非 `text/event-stream` 响应自动回退整段 JSON。
- **可取消**：请求期间显示带「取消」按钮的流式结果窗；取消设置 `threading.Event`，worker 在读到下一段时抛 `AICancelled`，UI 提示「已取消」而不是失败。服务端长时间静默时，取消最迟在请求 timeout 生效。
- **fix / tests 仍先预览**：流式窗口只展示接收内容；修复与生成测试必须等完整结果再进 diff 预览，避免半截代码被应用。
- **设置开关**：AI 设置新增「流式输出」开关，服务端不支持 SSE 时可关闭，回到整段返回。默认开启。
- **错误加固**：同步与流式路径都能处理错误对象为字符串、`choices` 缺失、空 delta、非 JSON 的 SSE 行，不再因 `.get()` 链式调用崩成 AttributeError。

- **测试**：新增 `tests/test_v376.py`，覆盖跨块替换、跨块 find_next 回绕、SSE 拼接、非 SSE 回退、取消与字符串错误对象。全量 `run_tests.py` 结果见发布记录。

### 未验证 / 边界

- 没有真实 API key，未对真实供应商、代理/企业网关、真实超时做联网验收；SSE 行为只用本地假响应验证。
- 跨块替换按字面量处理，不支持正则、全词匹配或跨行 needle（替换文本可以含换行）。
- 10 MB 数据仍是单机冷启动，不代表所有机器的交互帧率。


## [3.7.5] - 2026-11-15

### 大文件编辑性能（P2）

- **默认分块编辑**：超过 `large_file_kb`（默认 2000 KB）的文件不再弹三选一对话框，直接打开 `VirtualLargeFilePane`；Tk Text 只装当前窗口（默认最多 3000 行，按平均行长自适应，单窗口字符量上限约 22 万），完整行列表留在 Python 内存，行号始终是整文件绝对行号。
- **可视区高亮**：虚拟窗口只对当前可视区 ±80 行套 tag；滚动只排一次 90–110ms 防抖重绘，不再每次滚动重跑 3000 行高亮。
- **后台增量解析**：符号解析放到 daemon 线程，按窗口起始行缓存；解析完成后回主线程套语义 tag，同一窗口只解析一次，滚动返回已解析窗口直接命中缓存。
- **保存与编辑不变**：窗口编辑仍按 450ms 防抖回写完整行列表，`Ctrl+S` 先写临时文件再原子替换整文件；保存时强制 flush 最后一次未派发的编辑。
- **切回完整编辑**：面包屑或命令面板「切换大文件为完整编辑」仍可主动转成全文 `EditorPane`，默认不再询问。
- **10 MB 实测**：本机 11,685,459 B / 262,146 行，读取 0.037s、首窗打开 0.146s、换块 0.004s、可视区高亮 0.006s、取全文 0.033s；Tk Text 内仅 3000 行（Python 3.11.5 + Tk，冷启动，单机数据，不作为所有机器的保证）。

### AI 内联助手（P2，可选）

- **默认关闭**：`ai.enabled` 与 `ai.privacy_ack` 默认均为 `false`；必须同时开启、确认隐私提示并配置 key，才会发出请求。
- **三种动作**：选中代码 → 解释 / 修复 / 生成测试；解释显示文本结果，修复与生成测试进入统一 diff 预览，点「应用修改」后才写入选区或新标签。
- **只发送选区**：请求体只包含当前选区（默认最多 12,000 字符，可截断并注明）、文件名 basename、语言名；不发送整文件、工作区路径或终端输出。API key 只用于 `Authorization` 头，不进入发给模型的内容。
- **兼容 OpenAI Chat Completions**：endpoint / model / key 可配置，key 也可用环境变量 `IMPOR_AI_API_KEY`（优先）；仅允许 `https://` 或本机 `http://127.0.0.1` / `localhost`。
- **隐私入口**：设置面板与新命令「AI 助手设置（默认关闭）」列出 endpoint、model、选区上限、key 存放位置与风险提示；首次发送前再次弹窗确认。
- **未就绪不发送**：关闭、未确认、缺 key、endpoint 非 https 时都在本地拦截，提示原因，不发起网络请求。
- **密钥不进同步**：设置同步（`config_sync.py`）推送的配置会清空 `ai.api_key`，密钥只留在本机 `~/.impor_studio.json`；仍建议用环境变量。

- **测试**：`tests/test_v375.py` 18 项通过；全量 `run_tests.py` 631 项 OK（542.250 秒，含本版新增 18 项）。
- **失败兜底**：AI 返回结构异常或内容为空时给出错误提示而不是崩溃；请求期间选区/大文件窗口变化后，旧的修复 diff 不会误写到新位置。


## [3.7.4] - 2026-11-15

### 编辑器：调试与语言能力

- **DAP 条件断点**：`impor/dap.py` 的 `setBreakpoints` 不再只取行号；支持 `condition`、`hitCondition`、`logMessage`，并把条件、命中阈值、日志点传给解释器。VS Code 与 Impor Studio 现在使用同一套断点语义。
- **异常断点**：DAP 新增 `exceptionBreakpointFilters`（未捕获异常 / 所有异常），抛出和未捕获时发送 `stopped(reason=exception)`；Impor Studio 原有的“异常暂停”开关继续保留，并与 DAP 使用同一套异常语义。
- **LSP 能力补齐**：新增 `semanticTokens`、`foldingRange`、`inlayHint`、`codeLens`、`documentHighlight`、`prepareRename` 六项；VS Code 扩展注册对应客户端能力，语义高亮与 Studio 同源。
- **统一分析引擎**：Studio 的轻量语义符号表改为优先复用 `impor/analysis.py` 的作用域符号表，解析失败时回退正则；编辑器、LSP、lint 的符号口径收敛到一处。
- **命令面板减负**：命令按“文件/编辑/导航/运行/调试/Git/项目/帮助”分组，支持最近使用、当前上下文推荐、模糊匹配和快捷键冲突提示；旧命令文本仍可搜到。
- **测试**：新增 `tests/test_v374.py`，覆盖 DAP 条件/命中/日志/异常断点、LSP 六项能力、分析引擎一致性和命令面板排序。

> 说明：本版不包含大文件编辑器内核重写与在线 AI 助手；`editor_pane.highlight` 已有可视区重绘与 2 MB 上限，继续观察真实大文件反馈后再决定是否值得换渲染层。

## [3.7.3] - 2026-11-15

### 性能：原生数值快路径

- 新增 `impor build --fast`（别名 `--fast-native`，`--no-fast` 显式关闭）：对纯整数数值脚本生成不装箱的 C 代码（变量/参数为 `long long`），由随包 tcc 加 `-O2` 编译成独立 exe；显式 opt-in，默认 `impor build` 仍是原完整 C 后端，避免 64 位整数与 Impor 任意精度整数语义冲突。
- 快路径覆盖整数变量/赋值/复合赋值、`while`、`if/elif/else`、顶层函数、`return`、`print`、`abs/min/max`、`+ - *`（`%` 用 Python 同号取模）、三元表达式；比较 `==/!=/</<=/>/>=`、`and/or/not` 只允许出现在条件中，不能存成整数或打印布尔值。浮点、字符串运算、容器、类、异常、嵌套函数、`for`、裸表达式等会抛 `FastUnsupported`，CLI 返回码 3 并提示去掉 `--fast`，不静默降级。
- 边界：变量与参数是 64 位有符号，超出 int64 按机器整数回绕，不做解释器的任意精度/溢出报错；`% 0` 运行时输出 `division by zero` 并以 1 退出。
- 构建缓存键区分 `fast` / `full`，避免快路径产物与完整 C 后端产物互相覆盖。
- 新增 `tests/test_v373.py` 9 项：解释器一致性、`min/max/abs`、条件逻辑、负数取模、拒绝浮点、拒绝字符串拼接、拒绝布尔打印、拒绝布尔存储、生成代码不含 `Val*`/`rt_`。
- 新增 `benchmarks/bench_native20.py`：同一份源码，CPython 直接运行，Impor 先 `build --fast` 再跑独立 exe；构建不计时，取多次墙钟中位数。门禁三项实测：
  - `arith_50m`：11359.4 ms → 154.9 ms，**73.33x**
  - `nested_10k`：14255.8 ms → 242.8 ms，**58.71x**
  - `builtin_20m`：11928.6 ms → 132.7 ms，**89.87x**
  - 门禁 **PASS**，完整数据见 `benchmarks/native20.md`。
- **适用边界（不夸大）**：20x 只在上述“纯整数计算循环”成立。取模循环 `mod_20m_diag` 为 8.90x，递归 `fib_32_diag` 为 7.87x，均作为诊断项保留且明确不计入门禁；不能据此宣称所有 Impor 脚本都比 CPython 快 20 倍。

## [3.7.2] - 2026-11-15

### 关键字收敛

- `defer` 等无语法产生式的空保留字降级；新增自动审计测试：`KEYWORDS` 中每个词都必须在 parser 源码中有实际出现。
- `static`、`readonly`、`async`、`await`、`this`、`defer` 改为软关键字：保留原有语法语义，同时可作普通标识符。
- 同义写法收敛：`func`→`def`、`elseif`→`elif`、`switch`→`match`、`loop`→`while`、`let`→`var`；`impor fmt` 只输出主写法，LSP 补全只推主写法。
- 新增 `tests/test_v372.py`，覆盖空保留字审计、17 个可作标识符的词、deprecated 不进补全、fmt 幂等归一。

## [3.7.1] - 2026-11-15

### 版本真源与门禁修复

- `tests/test_version_source.py` 不再依赖未随包分发的 `_release3617.py`，干净目录也能通过。
- README 版本行与语法规范行、`CHANGELOG` 版本口径、`benchmarks/latest.md`、`bootstrap/imporc.impor` 自报版本统一到 3.7.1。
- `imporc` 接受 `version` 与 `--version`；干净目录不再在 `fileread` 处崩掉，并补充 `help/--help` 用法提示。

### 保留字解封

- `record/template/final/of/do/yield/let/each/with/lambda/macro` 不再作为硬保留字封锁；`var` 后可直接用作标识符。`do {}`、`lambda`、`record` 等已有语法保留兼容。

### 转译路径静默失败修复

- `dict.has(k)` / `dict.contains(k)` 改走 `_rt.dict_has`，不再运行期 `AttributeError`。
- `filelist`、`regex_match` 补进运行时导出，编译产物不再“编译通过、运行期找不到名字”。

### 递归上限统一

- 快速后端与树遍历统一为 3000 层；`sys.setrecursionlimit` 提到 32000。脚本里有没有 `class` 不再改变递归上限。

## [3.6.17] - 2026-11-15

### 真实使用后的四个小修

- **专注中不知道几点结束**：计时卡片右侧新增“结束 HH:MM”，开始/继续时显示真实结束时刻；暂停后改为“还剩 mm:ss”。25 分钟不再需要在脑子里做加法。
- **完成时窗口不回来**：专注或休息结束时，窗口会从最小化恢复、提升到前面并临时置顶 5 秒，同时保留 `bell()`。没有加托盘、系统通知或云同步；只在完成这一下抢一次注意力，之后恢复用户原来的置顶设置。
- **关窗会丢掉当前一轮**：运行中或暂停中关窗，会把任务名、剩余秒数、专注/休息设置写入 `%USERPROFILE%\.impor_focus\state.json`。下次打开显示“上次未完成 · 任务 · 还剩 24:52”，按钮变成“继续”，按开始就从剩余时间接着跑。完成、重置、跳过会清掉这份状态。
- **第二天打开只看得到今天**：新增“昨天 X 分钟 · Y 段 · 最后一轮 任务 N 分钟”。如果今天还没有记录，历史列表直接列出昨天最多 3 条，前缀“昨天”；今天的记录前缀“今天”，不再把两天的记录混在一起。
- 任务名在开始那一刻锁定到 `session_task`，完成记录用开始时的任务名，不用于结束时输入框里的新文字。
- 仍然**没有**加托盘、系统通知、长休息、云同步；也**没有**给 C 后端加 Tk 绑定。

### 测试

- 新增 `tests/test_v3617.py`：8 tests / OK / 0.479s。
- 实际 25 分钟 dogfood：15:58:18 启动，16:23:18 完成；真实 `clock()` 计时，完成后写入 1 条 25 分钟记录。
- 关窗测试：运行 8 秒后关闭，`state.json` 保存 `remaining=1492`，`history` 为空；重新打开显示“上次未完成 · 关窗测试 · 还剩 24:52”，按钮为“继续”。
- 第二天测试：只放昨天两条记录，打开后显示“昨天 55 分钟 · 2 段 · 最后一轮 英语 30 分钟”，历史列表两条“昨天”。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：`GUI SMOKE OK`。
- 全量 suite：583 tests / OK / 666.647s。

### 打包

- `apps/impor_focus/Impor专注计时器.exe`：11,456,903 B，SHA256 `67A0135F22E2ACC7B2F6F436D9158A8A4DD2382B5894D9D120B4B2A7A8055F2A`。
- 重新自举 `impor/imporc.exe` / `bootstrap/imporc_py.exe`：588,288 B，SHA256 `752F6BA233F09644C1CEAEAB1CFDD40E8F83B1EBAB439B0389AA1AF889C20B24`；`imporc version` 输出 `v3.6.17`。
- 新增 `docs/Impor 3.6.17 发布说明.md`；README、CHANGELOG、VERSION、CLI/VSIX manifest 同步到 3.6.17。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.17`。

## [3.6.16] - 2026-11-15

### 两个真 bug 的架构修复

- **Tk 字体族带空格不再被插值吃掉**：`"{Segoe UI} 9"` 以前会把 `Segoe` 当变量、把 `UI` 留在原地，报 `变量 Segoe 未定义`。现在 `{…}` 必须能解析成完整 Impor 表达式才算插值；解析失败就整段按字面文本处理，花括号原样交给 Tcl。`\{Segoe UI\} 9` 旧写法继续可用，`"{size}"` 仍是插值。转译器同时把文本段花括号翻倍，保证生成的 Python f-string 合法。
- **内建注册只有一处**：新增 `impor/builtin_manifest.py`，把 94 个内建分为 `RUNTIME_BUILTINS`（58，编译产物必须提供）、`HOST_BUILTINS`（9，Python 宿主提供）、`INTERPRETER_ONLY_BUILTINS`（27，依赖解释器状态/FFI）。`impor.runtime` 导入时自检缺一即 `ImportError`；`make_builtins()` 返回前与 `ALL_BUILTINS` 对账；编辑器的 `BUILTIN_NAMES` 也从该表派生。以后新增内建忘记登记或忘记运行时实现，会在测试/打包阶段失败，不会拖到用户双击 exe。
- `apps/impor_focus` 的 UI 字体从 `Arial` 全部改为 `{Segoe UI}`，真实 dogfood 新语法；版本字串升到 3.6.16，重新编译并重打 exe。
- 本工具定位明确分裂为两类：控制台 C 工具约 108 KB，Tk GUI 走 Python + PyInstaller 约 11 MB。本版只记录这个 100 倍差距，**不给 C 后端加 Tk 绑定**。
- `global set` 逐条写、`impor compile --exe` 不自动加 `--noconsole`、DPI 缩放仍是使用摩擦，写进 `apps/impor_focus/README.md`，不改语言。

### 测试

- 新增 `tests/test_v3616.py`：5 条无窗口用例，覆盖字面花括号 + 混用插值（解释器与转译产物）、旧 `\{...\}` 转义、内建清单与解释器/runtime 对账。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：`GUI SMOKE OK`；打包 exe 的 `--smoke-gui` 退出码 0，实际启动窗口标题 `Impor 专注计时器 · 3.6.16`。
- 全量 suite：**575 tests / OK / 618.237s**。

### 打包

- `apps/impor_focus/Impor专注计时器.exe`：11,451,460 B，SHA256 `3953F7A2F30221645452A8E7779E035001D9AB88AE6F82CC8B0B67563DA770F6`。
- 重建 `impor/imporc.exe` / `bootstrap/imporc_py.exe`：588,288 B，SHA256 `4C73FA7C66C04E62887366AF9EB84A7ED279D8AAAC2779F8E839CC9C391FDFD4`；`imporc version` 输出 `v3.6.16`，全量测试里旧二进制版本不一致的 1 条失败已消除。
- 新增 `docs/Impor 3.6.16 发布说明.md`；README、CHANGELOG、VERSION、CLI/VSIX manifest 同步到 3.6.16。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.16`。

## [3.6.15] - 2026-11-15

### 第一个真实用户工具

- 新增 `apps/impor_focus/focus.impor`：一个纯 Impor 写的桌面专注计时器，25/5 分钟专注与休息、开始/暂停/重置/跳过、任务名、置顶、今日统计、历史记录、空格/Esc 快捷键、关闭自动保存。
- 记录持久化到 `%USERPROFILE%\.impor_focus\history.json`，JSON 编解码与文件读写都走 Impor 运行时。
- 新增 `apps/impor_focus/README.md`，记录第一条真实使用路径，以及写这个工具时实际卡住的 6 个地方。
- 用 PyInstaller 打成单文件、无控制台的 `Impor专注计时器.exe`；Tk 单文件约 11–12 MB，双击即可打开窗口。
- 修复打包后 `NameError: name 'fileexists' is not defined`：把 `fileexists` / `filemkdir` / `fileread` / `filewrite` / `json_encode` / `json_decode` 以同名同义补进 `impor.runtime`，让生成代码脱离解释器内建表后仍可用。这是打包必需修复，不新增语法。
- 本版**不新增任何语言语法、关键字或编辑器功能**；3.6.15 只交付工具本身、必要版本同步和发布包。

### 测试

- 新增 `tests/test_v3615.py`：无窗口验证时间格式化、今日统计、JSON 往返与 `--selftest` 退出码；并新增“转译后的 Python 也能跑 `--selftest`”，防止 PyInstaller 环境下运行时内建缺失回归。
- `--selftest`：`SELFTEST OK`；`--smoke-gui`：实际走一遍“开始 → 暂停”后 `GUI SMOKE OK`。
- 打包 exe 实测：双击打开窗口，空格开始后倒计时从 `25:00` 走到 `24:57`，Alt+F4 关闭后生成 `%USERPROFILE%\.impor_focus\history.json`。
- 全量 suite：**570 tests / OK / 547.144s**（3.6.14 为 567 条，本版新增 3 条）。

### 文档与打包

- 新增 `docs/Impor 3.6.15 发布说明.md`；README 更新到 3.6.15。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.15`。

## [3.6.14] - 2026-11-15

### 自举解锁

- 旧 `impor/imporc.exe`（2026-10-31 生成，385,536 B）面对 86KB 的 `bootstrap/imporc.impor` 超过 900 秒不返回，`test_selfhost_closure` 在 Step 1 以 300 秒超时失败。根因不是断言，而是旧二进制与新种子的解析/性能不兼容。
- 新增 `bootstrap/build_with_python.py`：用 Python C 后端从当前种子生成新的 A（`bootstrap/imporc_py.exe`，588,288 B，0.52s），并自动把 PE 栈补到 8MB。该脚本把种子字符串字面量内的 `{}` 临时转义为 `\{`/`\}`，避开 Python 前端插值；种子源码中 `local` 变量改名为 `localname`，避开 Python 保留字。
- 修复 `rt_strcut` 的 O(n²)：新增同一字符串“字符下标 → 字节偏移”顺序缓存，`strcut(gsrc, i, i+1)` 不再每次从头扫描全文。A 编译完整 86KB 种子从 600 秒以上降到 **52.56 秒**。
- `rt_add` 的字符串拼接不再对两个操作数各做一次 `rt_tostr()` 全量复制，只分配并复制一次结果。
- 用 A 重新生成 `impor/imporc.exe`（v3.6.14，417,280 B），跑通 `bootstrap/selfhost_check.py`：
  - A 编译种子 → B；
  - B 编译种子 → C；
  - C 编译种子 → D；
  - B/C/D 输出的 C 代码逐字节一致（234,686 B，112 个函数）。
- 自举闭环不再依赖 `bootstrap/imporc_seed_v214.exe` 旧链，新种子可由新二进制稳定编译。

### 测试

- `test_v38.TestImporcSelfHost.test_selfhost_closure` 从 `run_tests.py` 的 `LEGACY_IDS` 移出，默认 suite 实际执行该用例；`LEGACY_IDS` 6 → 5。
- 默认 suite：**567 tests / OK / 408.230s**（含自举闭环）；
- 桌面中文/空格/括号路径：**567 tests / OK / 364.178s**。
- 版本一致性：`VERSION`、`Impor.py`、`impor/__init__.py`、`vscode-impor/package.json`、`imporc.impor` 全部为 3.6.14。

### 文档与打包

- 新增 `docs/Impor 3.6.14 发布说明.md`；`docs/测试基线分类-3.6.7.md` 补记自举项移出 LEGACY。
- 重新打 ZIP / VSIX，并同步桌面 `Impor(I++) 3.6.14`。

## [3.6.13] - 2026-11-15

### 路径健壮性

- 修复 `imporc` 调 tcc 时输出路径未加引号：`Impor(I++) 3.6.13` 这类含空格/括号/中文的目录不再被拆成 `3.6.13\tests\...`。
- `imporc` 的 `run` 模式执行产物路径同样加引号。
- 测试写 `imporc_args.txt` 时统一用双引号包裹脚本/输出路径；`cmd /c` 调用 `imporc.cmd` 改为整条命令双引号包裹，避免空格与括号截断。
- C 运行时 Windows 文件 API 改为 UTF-8 → UTF-16 后走 `_wfopen`；`system` 改用 `CreateProcessW`。中文目录下 `fileread` / `filewrite` / tcc 子进程不再被 ANSI 代码页截断。
- 桌面路径 `C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.13\` 下默认 suite 实测 `Ran 566 tests in 123.649s / OK`；仓库路径 `140.922s / OK`。

### 文档

- `docs/三端能力矩阵-3.6.6.md` 补记 3.6.11 决策：管道 `|>` 与字符串插值保留；并标注三端实际支持边界与 3.6.13 路径验收。
- 新增 `docs/Impor 3.6.13 发布说明.md`；`docs/测试基线分类-3.6.7.md` 补记 3 条默认红项的根因与修复。

## [3.6.12] - 2026-11-15

### 测试

- REPL / use / style lint 共 8 条旧用例切到当前语法与当前文案：`use std.text`、`def` / `var` / `if` 多行块、`help(print)` 当前说明、`style-semicolon` 等当前规则。
- `LEGACY_IDS` 从 14 条降到 6 条；默认 suite 实际执行新增的 8 条。
- 剩余 6 条：`try/catch`、保留字、自举闭包、`print` 软别名、`then`、诊断一致性。

## [3.6.11] - 2026-11-15

### 测试

- 修 LSP 补全空路径崩溃，rename 对当前文档保留原 URI。
- 启动器白名单补 `release-check` / `sys-check` / `syntax` 三项。
- **决策：管道 `|>` 与字符串插值保留。** 两者在解释器与转译器已跑通；7 条红项只是测试仍用旧中文关键字，已切到 `def` / `var` / 显式 `return`。
- `LEGACY_IDS` 从 24 条降到 14 条；默认 suite 实际执行新增的 10 条。
- 自举闭包 `test_selfhost_closure` 单独排期，不在本版混入。

## [3.6.10] - 2026-11-15

### 测试

- 处理 `test_v29` 最老的 10 条旧语法红项：9 条按当前 `def` / `var` / `return` 语法重写，1 条裸表达式用例删除。
- `LEGACY_IDS` 从 34 条降到 24 条；默认 `run_tests.py` 实际执行这 10 条，不再跳过。
- 剩余 24 条：`test_v29` 7 条、LSP 2 条、启动器 1 条、REPL/保留字/自举/诊断/样式 lint 等 14 条。

## [3.6.9] - 2026-11-15

### 编辑器延迟

- 按键热路径不再逐键重画标签栏；未保存圆点只在首次变脏时刷新。
- 高亮改为 180ms 防抖、400 行以上只重绘可视区 ±60 行；滚动重新高亮。
- 状态栏 50ms、目录树 500ms 防抖；实测 `_on_key + _on_modified` 平均 **0.399ms/键**。

## [3.6.8] - 2026-11-15

### 编辑器手感

- 停用输入触发的自动补全：打 `p` 不再自动弹 `print`；需要补全时按 `Ctrl+Space` 手动调出。
- 保留签名提示与括号配对，不影响现有补全数据。

## [3.6.7] - 2026-11-15

### 结构收敛

- 真 Git tag：仓库已 `git init`，首个 tag `v3.6.6`；本版提交打 `v3.6.7`。
- 编辑器再拆：`EditorPane`、对话框、编辑器组/标签栏、底部面板/文件树、运行/调试/测试 UI 分别拆到 `editor_pane.py`、`editor_dialogs.py`、`editor_groups.py`、`editor_panels.py`、`editor_runtime.py`；`editor.py` 从 9,622 行降到 5,618 行（低于 6000 行目标）。
- 测试分类：34 条 3.6.6 遗留红项按“旧语法兼容 / LSP / 自举闭包 / 启动器”分类，默认 `run_tests.py` 跳过并输出分类数字，`--all` 仍可跑全量；详见 `docs/测试基线分类-3.6.7.md`。
- 版本同步：`VERSION`、`Impor.py`、`impor/__init__.py`、`pyproject.toml`、VSIX 全部对齐到 `3.6.7`。

## [3.6.6] - 2026-11-15

### 结构收敛

- 版本对齐：新增 `VERSION`，`Impor.py version` 直接打印发布线说明；`docs/路线图-v6.0.md`、性能报告标注为历史内部文档。
- 三端契约：新增 `docs/三端能力矩阵-3.6.6.md`；JSON / 正则 / lambda 已实测 C 后端可用，文件 API / FFI 在 C 后端构建前明确拒绝，自举端明确拒绝子集外语法。
- 编辑器拆分：`editor.py` 的 Tk 自绘控件层与主题色板拆到 `impor/editor_widgets.py`、`impor/editor_theme.py`，主文件从 9,590 行降到 9,058 行。
- 测试基线：全量 `run_tests.py` 重新执行并留档 `_test_baseline_366.log`；已知失败/错误按真实数量记录，不再用“关键字计数不变”冒充全绿。

### 新增

- 多编辑器组：`Ctrl+\` 创建独立第二组，标签栏按组过滤，资源管理器可“在右侧编辑器组打开”，组内可独立开关标签。
- 行内诊断：错误/警告直接标在文本行（整行浅色背景 + 波浪下划线），不再只看底部问题面板。
- F8 / Shift+F8 问题导航：在当前文件的问题之间循环跳转，命令面板同步提供入口。
- Git 变更审查台：左侧变更文件树、右侧逐文件 diff、按文件暂存/取消暂存、暂存全部、提交框、打开文件。
- 可配置键位：`.impor/keybindings.json`（`[{"key":"ctrl+alt+g","command":"global_search"}]`），保存后即时重载，非法键位在面板提示。
- 测试树新增耗时与覆盖列；双击用例按测试输出里的 `file.impor:行` 精确定位。
- 命令面板新增“下一个问题 / 上一个问题 / 键位设置 / 当前文件在右侧编辑器组打开”。

### 说明

- 第二编辑器组是独立的组，不再克隆当前文件；在资源管理器打开新文件会进入当前活动组。
- 覆盖率列读取最近一次运行的行覆盖快照；需要先在“覆盖”入口开启行覆盖率运行，未运行覆盖时该列为空。

## [3.6.5] - 2026-11-14

### 新增

- 多根工作区资源管理器：项目配置里的 `roots` 会显示为多个顶层节点，不再固定成单个 `ImporLang` 根。
- 工作区符号索引热更新：后台按 mtime 轮询 `.impor`，外部修改或 `git checkout` 后自动增量刷新。
- 状态栏新增索引状态：`索引中…` / `索引 N 符号` / `索引已更新` / `索引失败`。
- 项目质量检查问题双击跳转改为 `(文件路径, 行, 列)`，先打开对应文件再定位。

### 修复

- 项目检查里解析失败的文件未登记跳转标记，导致后续问题整体错位一行。

## [3.6.4] - 2026-11-14

### 新增

- 自动配对 `()`、`[]`、`{}`、`""`、`''`；选中文本时直接包裹，键入右符号自动跳过。
- 智能退格：空配对上一次删除两边，不跨行误删。
- 多行注释 `Ctrl+/`：选中多行一次注释/取消，保留缩进。
- 整块缩进 / 反缩进：多行选区按设置宽度整体调整。
- `Alt+↑` / `Alt+↓` 上下移动当前行或选区；`Ctrl+Shift+K` 删除当前行或选区。
- `Ctrl+Backspace` 删除光标前一个词，中文按连续字处理。
- 智能 `Home`：在行首与第一个非空格之间切换。
- `elif`、`except`、`case`、`default` 等行尾输入回车时自动加深缩进。
- 设置页新增“自动配对括号与引号”开关，配置随项目保存。

## [3.6.3] - 2026-11-14

### 新增

- 语法查询：`impor syntax`、`impor syntax print`、`--search`、`--json`。
- 覆盖 73 个关键字与常用内建函数，每条含意思 / 格式 / 例子 / 例子说明。
- 编辑器 F1 与命令面板“语法查询”面板。
- REPL `help(名字)` 优先显示完整语法条目。

## [3.6.2] - 2026-11-14

> 系统编程门禁 + 受控指针 FFI：只增加可验收的审计、CI 退出码和解释器侧受控缓冲区，不声称系统编程能力已完成。

### Added
- `impor sys-check`：逐项报告 C 后端、自举编译器、FFI 基础、指针/结构体、C ABI、自动内存、线程/原子、syscall、freestanding、LLVM 后端状态。
- `impor sys-check --strict`：`systems_ready` 为 `NO` 时返回退出码 2，可直接接入 CI。
- `impor sys-check --json`：输出 `systems_ready` 与逐项状态报告。
- `docs/系统编程/README.md`：系统编程实施顺序与验收条件。
- 受控原生缓冲区 FFI：`ffi_alloc`、`ffi_alloc_str`、`ffi_buffer_size`、`ffi_read_i64`、`ffi_write_i64`、`ffi_read_f64`、`ffi_write_f64`、`ffi_read_str`、`ffi_write_str`、`ffi_buffers`。
- `ffi_call` 增加 `argtypes` 与 `ptr/cstr/void` 返回类型；`ptr` 只接受运行时分配的缓冲区 token，不接受任意整数地址。

### Changed
- 版本统一升至 3.6.2。

### Known limits
- 受控指针 FFI 只在解释器侧实现；C 后端与自举 `imporc` 尚未同步。
- 结构体布局、回调、稳定 C ABI、线程/原子、syscall、freestanding、LLVM 后端仍未实现。
- `sys-check --strict` 当前会失败，这是预期行为。

## [3.6.1] - 2026-11-14

> 系统编程内存策略定稿：采用自动管理，不暴露手动 `free`。本版仍不包含线程/原子、稳定 C ABI、LLVM/Cranelift 原生后端。

### Changed
- 阶段一内存模型由“默认 GC + 可选手动/区域分配”改为“运行时自动管理”：语言层不提供 `malloc/free`，用户不需要手写释放；内部 arena 仅限库实现，不作为公开 API。
- `docs/生产化/01-原生后端.md` 与 `README.md` 同步更新。
- 版本统一升至 3.6.1。

### Known limits
- 自动管理不等于系统编程完成：仍缺线程、原子、锁、稳定 C ABI、freestanding 运行时与原生后端。
- 自举闭环测试 `test_selfhost_closure` 仍未通过。

## [3.6.0] - 2026-11-14

> 生产化基线：把“能跑”变成“可验收”。本版不包含 LLVM/Cranelift 原生后端、中央包注册表与多线程运行时；这些明确列为未完成项。

### Added
- `impor release-check`：发布门禁。快速模式校验版本一致性、生产化必备文件与 conformance 用例数量；`--full` 追加 `doctor`、`conformance --ci` 与单元测试；支持 `--json` 报告。
- `docs/生产化/`：三阶段路线、验收标准与当前状态，明确区分“基线完成”和“能力完成”。
- `SECURITY.md`：报告渠道、响应目标、安全边界与已知限制。
- `CONTRIBUTING.md`：本地验证、三端一致性要求、版本与发布约定。
- `CODEOWNERS`：核心、原生后端、工具链、编辑器、VS Code、质量与安全的责任划分。

### Changed
- 版本统一升至 3.6.0：`Impor.py`、`impor/__init__.py`、`pyproject.toml`、`vscode-impor/package.json`、`bootstrap/imporc.impor`、`site/update.json`。

### Known limits
- 原生后端仍走 TinyCC，无优化等级、稳定 C ABI、调试信息与交叉编译。
- 内存模型未冻结；热路径分配规则未定义。
- 缺 HTTP/TLS/SQL/连接池/指标/健康检查标准库。
- 包管理器仍以本地/git 源为主，无中央注册表、签名、审计与撤回。
- 无 LTS、SBOM、可复现构建与正式 CVE 流程。

## [3.2.3] - 2026-11-14

> 编辑器五项升级：符号级语义高亮、异步防抖诊断、覆盖率行号栏内联、Git 三方合并编辑器、插件管理器。语言与安全策略沿用 3.2.2。

### Added
- 符号级语义高亮：`semantic_symbol_map` 先建符号表，再按参数 / 局部变量 / 函数 / 类结构异常 / 模块着色；新增 `sem_var`、`sem_param`、`sem_field`、`sem_module` 标签与三套主题色，`obj.field` 推断字段。
- 异步防抖诊断：300ms 防抖 + 后台线程解析 + 代次丢弃过期结果；`queue.Queue` 回主线程，`_apply_lint` 按 pane 保存标记，切换标签不串文件；保存/显式检查仍走同步 `_lint_pane`。
- 覆盖率行号栏内联：行号栏新增绿色命中 / 红色未命中竖条，错误红下划线、警告黄下划线；payload 带 header offset，Treeview 与行号栏对齐；新增“跳下一个未覆盖”。
- Git 三方合并编辑器：`split_conflict_markers` 支持 merge / diff3；`git show :1/:2/:3` + `git merge-file --diff3` 生成三栏；逐块采用 OURS / THEIRS / BASE / 双方；保存前语法校验、原子写回、可选 `git add`；命令面板新增入口。
- 插件管理器：本地插件发现、文件夹/ZIP 安装、启停状态持久化、meta 版本/权限、卸载与 reload；980×620 管理器窗口，Treeview 列表 + 详情 + 双击切换；安装/卸载后自动加载。
- `tests/test_v323.py`：10 项单元测试覆盖语义映射、诊断纯计算、覆盖率行、冲突解析、插件安装/启停/卸载/zip 防穿越。

### Fixed
- 覆盖率 header 偏移：可执行行与命中行统一加 offset，修复首行 header 导致的行号错位。
- 诊断面板串文件：每个 pane 保存自己的 `_problem_marks` / `_lint_payload`，只有当前 pane 刷新面板。
- 插件包卸载只删 `plugin.py` 留下空目录；现在删除插件包父目录。
- 三方合并保存后未重置 `edit_modified`，重新编辑可能误标脏；保存后已重置。

## [3.2.2] - 2026-11-13

> 编辑器五项闭环：运行时行覆盖率、插件面板跨进程 builder、索引与搜索后台化、UIA 逐控件 provider、诊断 Quick Fix 扩面。语言与安全策略沿用 3.2.1。

### Added
- 运行时行覆盖率：解释器 `enable_coverage` / `coverage_snapshot` 按 `lineno` 累加；编辑器 `run_code(coverage=True)` 贯通异步与同步执行；覆盖率视图以 AST 语句行为分母，列出命中次数、已执行/未执行与源码，双击跳转，支持重跑与清空。
- 插件面板跨进程渲染：子进程 `_UIBuilder` 把 Frame / LabelFrame / Label / Button / Entry / Text / Checkbutton / Separator / Treeview 与 pack/grid/place 序列化成 UI 描述；父进程 `_isolated_panel` 渲染并保留进程状态、权限与重启入口；按钮经 `panel_action` 回调子进程；`tkinter` shim 兼容旧插件写法。
- 索引后台化：`refresh_index_async` 用代次合并请求，启动与切工作区不再阻塞主线程；`ProjectIndex` 增加可重入锁，刷新与查询并发安全。
- 搜索后台化与分页/取消：`search_project(..., should_cancel=...)` 逐文件检查取消；搜索窗口新增“取消”“加载更多”，已扫结果保留，结果按页续接。
- UIA 逐控件 provider：`ControlProvider` 暴露 id / name / role / group / accelerator / state / bounds / invoke；`build_control_providers` 覆盖 16 个主入口，控件树按组展示角色、状态与快捷键，`Control+Tab` 循环焦点，Enter 执行；`refresh()` 导出 `control_providers.json`。
- 诊断 Quick Fix 扩面：新增缺少 return、match 不穷尽、重复定义、类型不匹配、await/async、ADT 载荷数量、FFI 签名等规则；Quick Fix 覆盖关键字拼写、未声明赋值补 `var`、条件 `=` 改 `==`、括号/引号/大括号补齐、未使用变量删除；诊断解释窗口改为“原文 / 为什么 / 怎么修 / 最小示例”。

### Changed
- `quick_fix_smart` / `quick_fix_all` 前后加 `edit_separator()`，并把修复提示改为可按 `Ctrl+Z` 逐个撤销。
- 插件父进程请求改用负数 id，避免与子进程代理请求的正数 id 冲突；`register_panel` 兼容一参/两参 builder。

### Fixed
- 取消搜索后 token 自增导致 `_search_done` 被丢弃、`_busy` 永久为真；现在由 worker 以同一 token 收尾。
- `_UIBuilder` 后代节点原先全部平铺到根节点，现按 `_parent` 维护层级。
- `register_panel` 只传 `root` 导致两参 builder 报错。
- Windows Tk 不识别 `<Control-ISO_Left_Tab>` 导致 UIA 初始化抛出 `TclError`。



> “全部”升级：语言六项 + 编辑器六项 + 应用层最高安全策略。核心原则是“解释器能跑、编译产物也能跑，跑不了的地方明确报边界”。

### Added
- 带载荷 ADT：`enum Result { Ok(value), Err(message) }`，构造 `Result.Ok(41)`，`match` 按 `case Result.Ok(v)` 绑定字段；解释器与 C 后端输出一致，穷尽性检查按 tag 覆盖。
- `async func` / `await` / `run_tasks` / `task_status`：单线程任务模型；解释器与 C 后端（同步语义）状态与结果一致。
- 三端补齐：C 后端与解释器同时支持 JSON（`json_encode` / `json_decode`，含转义、`\uXXXX`、整数/小数/指数）、基础正则（`regex_match` / `regex_find` / `regex_replace`）、`lambda(x): ...`；`_t_parity.impor` 9 行逐行一致。
- 跨文件访问控制：模块顶层 `visibility private/protected` 收集到模块符号表，跨文件 `AttrGet` / `from ... import` 拒绝并抛出 `AccessError [E1099]`。
- FFI：`ffi_open` / `ffi_call`，默认拒绝一切原生库，白名单来自 `IMPOR_FFI_ALLOW` 或 `.impor/ffi_allow.json`；仅 int/float/str/bool、最多 8 参，不做指针/结构体。已用 `kernel32.GetTickCount` 实测。
- 诊断升级：`AccessError / private 符号` 规则；问题面板右键「解释诊断」给出“为什么 / 怎么修”，可直接跳到 Quick Fix。
- 编辑器：安全重构统一 diff 预览 + 一键撤销（重命名 / 提取变量 / 提取函数 / 移动文件含引用改写）；测试失败内联到编辑器与行号栏、只重跑失败、函数覆盖率视图、断点持久化；大文件分块可编辑并原子回写；插件崩溃自动重启（最多 3 次）、权限清单、隔离进程面板宿主；UIA 可聚焦控件树 + 高对比主题按钮；搜索结果 200 条增量虚拟列表。
- 安全中心：「威胁模型 / 审计日志」面板；启动进程加固（清 `PYTHONINSPECT` / `PYTHONSTARTUP` / `PYTHONPATH`）；保存、插件写入写 JSONL 审计。

### Changed
- C 后端 `run_tasks` / `task_status` 接入运行时；`task_status` 恒 `done`（C 端 async 为同步求值）。
- 大文件保存后跳过全量 AST/lint，避免 2 MB+ 文件保存卡死 UI。
- `bootstrap/imporc.impor` 版本号改为 3.2.1，并明确标注“子集模式”（尚不支持 ADT/async/lambda/JSON/正则）。
- 自举编译器仍是 C 后端子集，是三端一致性唯一未闭环项，其余差异已收敛到 JSON/正则/lambda。

### Security
- 新增 `impor/security.py`：路径围栏（realpath + commonpath）、原子写、SHA256 清单校验、JSONL 审计、进程加固、威胁模型与残余风险。
- 插件子进程接入 Python `audit hook`：未声明 `write` 直接写文件、未声明 `net` 的 socket、未声明 `process` 的 `subprocess` / `os.system` 均抛 `PermissionError`；读范围限工作区、插件目录与 Python 运行时，越界 `open` / `listdir` 被阻断。
- 插件默认只能读取工作区与运行时文件；写文件走 `safe_write` + 审计，并强制落在工作区；FFI 未登记库默认拒绝。
- 诚实边界：这是应用层安全模型，不是 OS 沙箱；未启用 AppContainer/Job Object，更新通道未做签名验证；audit hook 只覆盖 Python 级事件，ctypes/原生扩展可绕过；同用户权限下的恶意代码仍可绕过。

## [3.1.0] - 2026-11-13

> 版本号压缩：7.3.0 重编号为 3.1.0；本日完成编辑器五项升级，功能与发布物同步到 3.1.0。

### Added
- 可选类型标注：变量、参数、返回类型；`int/float` 数值互通、`T?` 可空、`any/?` 放行；`impor check` 新增「类型」阶段。
- 枚举穷尽性：无 `else`/`_`/绑定通配的 `match` 必须覆盖全部成员，报 `match-exhaustive`。
- `private` / `protected`：解释器运行时 `AccessError` + 同文件静态 `access-private` / `access-protected`。
- `impor build` 增量缓存与 `--no-cache`：缓存源码、版本、路径、TCC 与依赖；实测 0.66 s → 0.05 s。
- `impor conformance` 子命令（`--ci` / `--only` / `--json`）；runner 保存并恢复 `imporc_args.txt`，修正 `first_diff` 误报。
- `impor pkg graph` 依赖树（循环/未安装标记）与 `impor pkg audit` 离线审计（lock、版本、哈希、HTTP 源、requires）。
- 12 课教学系统 `impor teach`：讲义、练习、答案、`--write` 与 `impor teach grade` 自动判题；全部 12 课答案实测通过。
- 编辑器「项目质量检查（类型+lint）」聚合 300 条诊断到问题面板；「教学大纲（12 课）」入口。
- 插件写 API `write_file` / `write_current`，默认拒绝，按 `plugin_write_allow` 或 `IMPOR_PLUGIN_PERMISSIONS` 授权，路径限制在工作区。
- 编辑器五项升级：全项目搜索/批量替换（正则、区分大小写、全词、diff 预览、按文件勾选、事务备份与撤销）、大文件虚拟滚动只读窗口、项目依赖图 + 离线审计三标签窗口、Windows 屏幕阅读器语义桥接（原生 EDIT + JSON 快照）、设置快照同步（newer/local/remote 三策略）。
- 插件独立进程隔离：默认子进程宿主 + JSON-RPC 代理，命令/格式化器/主题/状态项可用，面板 builder 降级为占位；写权限仍由父进程按 `plugin_write_allow` / `IMPOR_PLUGIN_PERMISSIONS` 校验。
- 文档：`docs/语言-3.1.md`、`docs/编辑器-3.1.md`、`docs/测试矩阵-3.1.md`、`docs/库-atlas-3.1.md`。
- 标准库 `std.atlas`：700 个 Impor 独有微库（10 族 × 7 套 API × 10 变体），每库一个独立 `.impor` 模块与可运行函数；目录见 `impor/std/atlas/CATALOG.md`，全量导入冒烟见 `tests/atlas_700_smoke.impor`。

### Changed
- 版本号 7.3.0 → 3.1.0（重编号 + 编辑器五项升级，功能有增量）。
- `typecheck` 全仓 `.impor` 扫描为 0 误报（修复 `vars()` 非 AST 节点崩溃与 `int`/`float` 误判）。
- `conformance` 兼容含空格与括号的安装路径：自举后端先把用例复制到无空格临时目录，桌面镜像 `Impor(I++) 3.1.0` 实测 23/23。
- 修复 `impor.bat` 在 `if` 块里展开 `%errorlevel%` 导致退出码恒为 0：`impor check` 的类型失败现在能正确返回 1，可直接接入 CI。
- 本版边界：带载荷 ADT、`async/await` 与最小并发、C 后端/自举的 JSON/正则/lambda、跨文件访问控制仍未完成；UIA 为语义桥接而非逐控件 provider，插件隔离为进程隔离而非 OS 沙箱。

### Fixed
- 全项目搜索/替换面板：搜索→替换→撤销已在真实 Tk 主循环中跑通；批量替换/撤销不再对虚拟滚动 pane 做整文件重载，避免把当前 3,000 行窗口写成全文。
- 大文件虚拟滚动：breadcrumb 徽章在编辑器延迟初始化后重新写入，稳定显示「虚拟滚动 起-止 / 总行数 · 只读」。
- 依赖图/审计窗口：只扫描当前工作区，不再误扫安装根；刷新加入代次校验，快速连点刷新不会重复插入旧结果。

## [7.2.0] - 2026-11-12

### Added
- 渐进式启动：工具栏、资源管理器、outline、标签和编辑区先构建并进入事件循环；底部面板、会话恢复、插件、文件监听在首帧后补齐；splash 停留 280ms、淡出 120ms，可点击立即进入。
- 自动更新：启动 7 秒后静默读取清单，状态栏提示新版本；下载进度窗、SHA256 校验、安装后询问重载、失败回滚旧 VSIX。
- 项目级配置 `.impor/settings.json`：主题、字号、缩进、换行、缩略图、inlay、语义高亮、大文件阈值、保存格式化、自动保存、任务模板、shell profile、插件目录；项目覆盖全局。
- 只读插件 API：`current_text/current_path/read_file/list_files/toast`；注册命令、侧栏面板、状态栏、主题、格式化器。
- Git：行级 blame、按 hunk 暂存、分支查看/切换、冲突文件入口。
- 多终端标签：cmd/powershell/pwsh profile，每标签独立持久 shell；任务模板支持 `{file}/{workspace}/{python}/{kind}`；problem matcher 抓 `file:line[:col]`。
- 编辑器智能：inlay hints 字面量类型、常量语义高亮、四层括号着色、作用域吸顶条、大文件可视行着色。
- 可访问性：高对比主题、Ctrl+1 编辑器 / Ctrl+2 资源管理器 / Ctrl+3 底部命令条 / Ctrl+4 问题 / Ctrl+5 命令面板、字号缩放。

### Changed
- 7.1.0 → 7.2.0。
- 自举编译器 `imporc.exe` / `imporc_self.exe` / `imporc_self_raw.exe` 的内置版本号同步为 7.2.0。

## [7.1.0] - 2026-11-12

### Added
- 原生控制台启动壳 `impor-fast.exe`：console 子系统，控制台窗口随进程创建即出现，实测入口 35–84 ms；Python 解析顺序为 `IMPOR_PYTHON` → 同目录 `impor_python.txt` → PATH → 本机兜底。
- `version` / `--version` / `-v` 快路径：不导入 lexer/parser/interpreter，直跑 `-S -I` 中位约 93 ms（最低 74 ms）。

### Changed
- 无参数启动先打开编辑器，不再先加载 CLI 语言后端。
- `impor.bat` 优先调用 `impor-fast.exe`，回退 `python -S -I -X utf8 Impor.py`。
- Python 解释器固定到 `impor_python.txt` 指定的 3.11.5，避免命中 Windows 应用别名。
- 7.0.0 → 7.1.0。

## [7.0.0] - 2026-11-11

### Added
- 调用图：从函数体抽取 caller→callee 边，左侧列出函数与出度，右侧分“调用者/被调用”两段展示，双击跳转。
- 条件断点与命中次数：条件按源码行号下发；断点可设“第 N 次命中才暂停”，断点列表显示条件、阈值与累计命中。
- 日志点集中管理：断点列表统一编辑条件、命中次数、日志表达式。
- 运行配置：`.imporrun` 保存入口文件、参数、工作目录、环境变量；Ctrl+Shift+F5 直接运行。
- 项目级快速修复：全工作区格式化预览，逐文件 unified diff，确认后批量写回。
- 右键菜单补齐：文件树可复制相对路径、在资源管理器打开；标签可复制路径、打开目录、关闭右侧标签。

### Changed
- 6.9.3 → 7.0.0。

## [6.9.3] - 2026-11-11

### Added
- 全项目重命名改为预览面板，逐条列出文件、行、代码后再应用。
- 项目诊断总览：扫描工作区，按错误/警告列出文件、行、说明，双击跳转。
- 导航历史：Alt+Left / Alt+Right 后退与前进，保留最近 50 个位置。
- 批量编码规范化：预览后把目录内 `.impor` 统一为 UTF-8 无 BOM + LF。
- 更新失败回滚：VSIX 安装返回非 0 时自动尝试重装上一版；清单支持 `min_version`。

### Changed
- 6.9.2 → 6.9.3。

## [6.9.2] - 2026-11-11

6.9.1 收尾补丁。

### Added
- 项目引用总览：跨工作区按引用次数列出符号，回车跳到定义。
- `site/update.json` 增加 `sha256` 字段，下载后校验安装包。

### Changed
- 6.9.1 → 6.9.2。

## [6.9.1] - 2026-11-11

6.9.0 收尾补丁。

### Added
- 差异块支持“撤销上一块”，每个标签保留最近 30 次块操作。
- 命令面板最近使用写入配置，重启后仍置顶。
- 状态栏编码/换行可点击：UTF-8、UTF-8 BOM、LF、CRLF，可转换并保存。

### Changed
- 6.9.0 → 6.9.1。

## [6.9.0] - 2026-11-10

编辑器五项升级（逐块合并 / 全项目索引 / 命令面板 / 编码换行 / 更新校验）。

### Added
- 差异视图支持按块处理：上一块/下一块、采用磁盘版当前块、保留编辑器版、全部载入磁盘。
- 项目符号索引：跨整个工作区列出函数、类、结构体与变量，回车跳到对应文件行。
- 命令面板模糊匹配：子序列匹配按紧凑度排序，最近使用的命令自动靠前。
- 状态栏显示 UTF-8 / UTF-8 BOM / CRLF / LF，打开时检测，保存时按原编码和换行写回。
- 更新下载改用 `.part` 临时文件，支持 SHA256 校验，校验失败自动丢弃。

### Changed
- 6.8.0 → 6.9.0。

## [6.8.0] - 2026-11-09

编辑器五项升级（图形设置 / 可操作差异 / 保存冲突 / 更新清单 / 搜索与大纲沿用增强）。

### Added
- 图形设置面板补齐工作区选择、恢复默认、打开配置文件。
- 差异视图可操作：载入磁盘版、保存编辑器版、跳转下一处差异。
- 保存冲突三选：先看差异、覆盖保存、放弃保存。
- 更新清单 `site/update.json`，支持 `IMPOR_UPDATE_URL` 远程清单、下载与一键安装。

### Changed
- 版本 6.7.0 → 6.8.0。

## [6.5.0] - 2026-11-09

桌面编辑器六项升级（签名 / 测试 / 调试 / 重构 / 终端 / 设置）。

### Added
- 签名帮助：输入 `(` 或 `,` 时浮出多行签名，当前参数用 `▶` 高亮；LSP 新增 `textDocument/signatureHelp` 与 `signatureHelpProvider`。
- 测试面板：不运行也能先“发现用例”；只重跑失败文件；监听测试文件改动并自动重跑；显示通过 / 失败 / 断言计数。
- 调试增强：手工监视表达式随配置持久化；断点列表提供“条件 / 日志”可视化编辑按钮；异常类型过滤输入框，只暂停匹配的异常。
- 项目级重构：F2 重命名前预览涉及文件与处数；一次重命名可整批撤销；新增项目依赖图与“可能未使用的符号”列表。
- 真终端：底部命令条支持 `cd`、`pwd`、`clear`，上下键翻阅历史，`Ctrl+C` 终止当前命令，基础 ANSI 颜色映射。
- 设置面板：新增字体族、缩略图开关，补全自动换行 / 缩进 / 自动保存 / 保存格式化；配置持久化。

### Changed
- 版本 6.4.0 → 6.5.0。

## [6.4.0] - 2026-11-09

桌面编辑器六项升级一次落地（导航 / 调试 / 编辑 / 测试 / 任务条 / 大文件）。

### Added
- 导航与重构：面包屑按「文件 › class › def」分段可点击跳转；`Shift+F12` 查找引用、`Alt+F12` 调用层级，结果可双击跳转；`F2` 触发重命名符号。
- 调试闭环：调试页新增表达式求值框，可在当前帧或选中栈帧求值；变量树双击可直接改写值；断点列表显示行号与条件/日志点，双击跳转、Delete 删除；异常时自动暂停并展示异常调用栈（可在调试页关闭该开关）。
- 编辑效率：`Ctrl+D` 选中下一个相同词（`Ctrl+Shift+D` 仍复制当前行）；`Alt+Shift` 拖拽做矩形列选择；代码片段、保存时自动格式化沿用现有能力；折叠状态按文件写入配置，下次打开自动恢复。
- 测试面板：`F6` 只运行当前文件测试；失败用例双击跳堆栈；通过 / 失败用绿 / 红着色，并按用例集统计通过、失败数。
- 任务运行条：底部常驻 `$` 命令输入，回车执行任意命令，或点「运行 / 构建 / 测试」直接跑当前文件；输出进入现有输出面板。
- 大文件保护：打开超过约 2 MB 的文件先提示；大文件只高亮可视区，minimap 自动降采样。

### Changed
- 版本 6.3.0 → 6.4.0。

## [6.3.0] - 2026-11-09

桌面编辑器三项升级。

### Added
- 调试器从模态弹窗改为底部「调试」停靠页：调用栈可点选切帧，变量树可展开 dict / list，F10 / F11 / Shift+F11 驱动，继续后释放解释器。
- 代码折叠：按缩进识别 class / def / struct / if / elif / else / for / while / match / try / catch，行号左侧箭头点击折叠，命令面板可展开全部。
- 面包屑：编辑器顶部显示「文件 › class › def」。
- 外部文件变更自动重载：干净标签自动刷新并保留光标与滚动位置；有未保存改动时提示冲突，不覆盖用户内容。

### Changed
- 版本 6.2.0 → 6.3.0。

## [6.2.0] - 2026-11-09

第二轮性能专项：函数调用与启动导入开销。

### Changed
- 闭包创建时预计算“末行是否为隐式返回”和正文长度，调用时不再每帧判断与切片。
- 参数绑定直接写局部字典；成功调用不再调用 `sys.exc_info()`，只在异常路径收集调用栈。
- `config.py` 不再为包缓存目录导入整个 `pkgmgr`（连带 `urllib` / `http.client` / `ssl` / `zipfile`），启动少加载约 95 ms。
- `pkgmgr.CACHE_DIR` 与 `config` 共用同一个缓存目录函数，保持路径语义不变。

### Performance
- `impor bench --gate`：0.536 s → **0.520–0.563 s**（多次实测，门槛仍为 3400 ms）。
- `bench_compare.py`：`loop_1e6` 799.8 → 731.9 ms，`str_2000` 283.4 → 249.1 ms，`fib_22` 694.6 → 648.4 ms。
- `fib(22)` cProfile 总量 1.916 s → 1.758 s；`_call_closure` 自身耗时 0.347 s → 0.290 s。

### Notes
- 本轮没有改执行模型；字节码 VM 仍是后续选项。递归场景仍与 CPython 有约 5 倍差距。

## [6.1.0] - 2026-11-08

性能专项：解释器 100 万次循环从 7.031 秒降到 0.581 秒。

### Changed
- 帧采样改为每 1024 轮取一次时钟，不再每轮调用 `time.time()`。
- 作用域未出现 `const` 时跳过每次赋值的可变性检查。
- 二元运算增加整数热路径，跳过通用类型分派。
- 变量读取先查当前作用域字典，未命中再沿作用域链查找。
- `impor bench --gate` 门槛由 9000 ms 收紧到 **3400 ms**。

### Performance
- `loop_1e6`：8042.1 ms → 799.8 ms（含进程启动，约 10.1 倍）。
- `str_2000`：525.6 ms → 283.4 ms。
- `fib_22`：1293.4 ms → 694.6 ms。
- 纯解释执行 `impor bench`：7.031 s → 0.581 s。

## [6.0.0] - 2026-11-08

v6.0 只做“把支持做实”，不新增关键字。

### Added
- `implements` 真契约校验：类缺少接口方法时直接报 `ContractError`（E1014）；基类继承来的方法也算实现。
- 文件/目录内建：`filelist` / `filemkdir` / `fileexists` / `filesize`（解释器路径）。
- `impor teach [课号]`：直接查看 12 课教学大纲，可只显示某一课。
- `impor bench --gate`：读取 `benchmarks/gate.json`，解释器 100 万次循环超过 9000 ms 即失败退出。
- `docs/路线图-v6.0.md`：四条线逐项标“已开工 / 下一步 / 边界”。

### Changed
- 诊断库新增 E1014 与中文“为什么 / 修复”文案。
- 版本号 5.6.0 → 6.0.0；VSIX 重打为 `vscode-impor-6.0.0.vsix`。

### Notes
- 修饰符仍是声明性标记，没有运行时访问控制；JSON/正则/C 后端一致性仍待 v6.1。
- `bench --gate` 是防退化门槛，不代表已达到 Python 级性能。

## [5.6.0] - 2026-11-08

定位收敛为「中文母语入门 + Windows 小工具独立 exe」，先补可信度与工程化。

### Added
- `impor doctor`：逐模块检查运行时、C 后端、自举产物，缺一项即失败。
- JSON 内建：`json_encode` / `json_decode`；正则内建：`regex_match` / `regex_find` / `regex_replace`（解释器路径）。
- `benchmarks/bench_compare.py`：循环、字符串、递归三类任务对比 Python 3.11。
- `docs/路线图-v5.6.md`：功能、性能、生态、人群四张表，标明已完成/进行中/计划。
- `docs/教学大纲.md`：12 课教学路径与评估方式。
- CI 增加 `impor doctor` 步骤。

### Changed
- 版本号 5.3.0 → 5.6.0；VSIX 重打为 `vscode-impor-5.6.0.vsix`。
- 桌面镜像改为完整运行时同步，补回 23 个缺失模块并跑 version/run/build 冒烟。

### Notes
- JSON/正则目前只在解释器可用，C 后端与自举编译器待同步。
- 修饰符仍无访问控制语义，`implements` 仍不做契约校验；路线图中列为进行中。

## [5.3.0] - 2026-11-08

中文兼容、迁移工具与新关键字语法。

### Added
- 中文关键字兼容开关：`IMPOR_LEGACY_CN=1` 或命令行 `--legacy-cn` 恢复旧中文关键字映射，默认仍为纯英文。
- `impor migrate-cn [--dry-run] [路径...]`：把 `.impor` 文件中的中文关键字迁移为英文，自动生成 `.cn.bak` 备份，跳过字符串与注释。
- `interface` 作为类契约、`record` 作为字段记录、`namespace` 作为命名空间容器；`public` / `private` / `protected` / `abstract` / `final` / `sealed` / `override` 作为修饰符，`implements` 支持接口列表。
- `lambda (a, b): 表达式` 匿名函数表达式，解释器支持闭包捕获。

### Changed
- VS Code 语法高亮同步英文新词，中文关键字不再高亮，仅在兼容模式下由词法器识别。

### Notes
- `defer` / `macro` / `template` 仍为预留保留字；`lambda` 与 `interface` 等新语法目前只在解释器路径完整支持，C 后端/自举编译器暂未同步。
- 旧的 `.impor` 中文关键字脚本仍建议用 `impor migrate-cn` 迁移后再提交。

## [5.2.0] - 2026-11-08

全英文关键字。

### Changed
- 删除全部中文关键字与中文软别名：`如果` / `变量` / `函数` / `打印` / `除非` 等不再翻译为英文关键字，只按普通标识符处理。
- 保留字集合改为纯英文，新增 15 个英文保留字：`interface` / `namespace` / `public` / `private` / `protected` / `abstract` / `final` / `override` / `implements` / `defer` / `lambda` / `macro` / `template` / `sealed` / `record`。
- 编辑器代码片段、REPL 多行块判断、lint 中文别名提示同步移除中文关键字路径。

### Notes
- 既有中文关键字脚本需迁移为英文写法；中文仍可作为标识符、注释与字符串内容。
- 新增英文保留字目前为预留占位，尚无对应语法。

## [5.1.0] - 2026-11-07

类型系统、DAP 调试、AST 索引、测试树与多根工作区。

### Added
- 跨文件语义类型检查：变量赋值、函数参数标注与调用实参做类型传播，诊断附可执行的 Quick Fix。
- 标准 DAP 调试适配器 `impor dap`：VS Code 断点、单步、调用栈、局部变量与表达式求值复用同一解释器。
- 符号索引改为 AST 优先建索引（语法树作用域链），解析失败自动回退行扫描，修复同名不同作用域冲突。
- 测试面板升级为用例集 / 用例两级树，通过用例带覆盖率标记。
- 多根工作区与 `.imporproj`：源码根、忽略目录、搜索路径、缩进与行宽写入项目配置。

### Notes
- 类型检查是轻量静态传播，表达式无法推断时保留 `?`，不阻断运行。
- DAP 复用解释器线程，单步与断点语义与编辑器内置调试面板一致。

## [5.0.0] - 2026-11-07

语言服务与调试能力升级：项目级符号索引、跨文件 LSP、真实诊断修复和更完整的调试器。

### Added
- 项目级语义符号索引：扫描工作区 `.impor`，记录声明、参数、作用域、类型推断、跨文件引用。
- LSP 升级：跨文件跳定义、引用查找、工作区符号、项目补全、跨文件重命名、类型化悬停。
- `textDocument/references` 与 `workspace/symbol` 能力，VS Code 扩展继续复用同一 LSP。
- Quick Fix 接真实诊断：未定义名称自动补 `var` 或从项目导入；支持整文件批量修复。
- 调试器：运行到光标（Ctrl+F5）、日志点（Ctrl+Shift+B）、步过/步入/步出与调用栈面板。
- 编辑器跨文件跳定义和重命名改走符号索引，不再每次线性扫描全项目。
- VSIX 现在携带 LSP 客户端依赖，安装后语言服务可直接启动。

### Notes
- 符号索引按行扫描 + 缩进作用域，类型推断覆盖字面量；复杂表达式的精确类型仍可能为 `?`。
- 跨文件重命名目前按项目引用表执行，同名不同作用域仍可能一起改。
- 日志点在解释器线程里求值，只记录不暂停。

## [4.9.0] - 2026-11-07

编辑器工作区与日常使用闭环：打开文件夹、会话恢复、多光标、标签拖拽、精准修复、测试面板、Git 差异与正则替换。

### Added
- 打开任意文件夹作为工作区，文件树、搜索、运行和 Git 以当前工作区为根。
- 会话恢复与配置持久化：主题、字号、缩进、自动保存、工作区、打开的标签在重启后恢复。
- 多光标：`Alt+Click` 追加光标，输入与退格同步到所有光标。
- 标签拖拽排序：鼠标拖动标签调整顺序。
- 精准快速修复：补 `var`、补右括号、删除只出现一次的未使用变量。
- 测试面板：`impor test` 输出结构化到「测试」页，双击失败行跳到源码。
- Git 面板新增查看差异、当前分支、切换分支。
- 正则批量替换，支持捕获组。

### Notes
- 超长文件仍采用可视区增量高亮 + 关闭缩略图的降级方案，未实现行级虚拟滚动。
- 多光标暂不支持跨行 Enter 同步和列选择拖拽。

## [4.8.0] - 2026-11-07

编辑器代码动作与大文件性能：提取变量/函数、生成 expect 测试、补全 import；可视区增量高亮、文件树懒加载、超长文件降级。

### Added
- 代码动作：提取变量、提取函数、生成 `expect` 测试、按符号自动补全 `import`。
- 文件树懒加载：展开目录时才扫描子项，避免启动时遍历整棵树。
- 大文件模式：超过 1500 行只重绘可视窗口内的高亮，滚动时按需补绘。
- 超过 6000 行时跳过缩略图重绘，优先保证输入响应。

### Notes
- 超长文件暂未实现真正的虚拟滚动，采用“可视区增量高亮 + 关闭缩略图”的降级方案。

## [4.7.0] - 2026-11-07

编辑器全功能补齐：补全/跳转/重命名、拆分独立文件、项目替换、Git 与测试面板、片段与多行编辑。

### Added
- 模糊补全、方法参数提示、悬停文档；`Ctrl+Click` 支持跨文件跳定义。
- 符号重命名（`Ctrl+Shift+R`）与项目批量替换（带文件级预览）。
- 拆分视图可左右各开不同文件，支持双向同步。
- 资源管理器右键新建文件/文件夹、重命名、删除。
- 标签左移/右移（`Ctrl+Shift+PageUp/PageDown`）、插入代码片段、多行行尾插入。
- 快速整理（`Shift+Alt+F`）、解释选中代码、运行测试（`Ctrl+Shift+T`）、Git 面板。

### Fixed
- 主题切换保留光标位置与滚动位置（撤销栈仍会重置）。

## [4.4.0] - 2026-11-07

编辑器工程化：左右拆分视图、浅色/深色主题切换、设置面板；全项目搜索、断点行号映射与可视化调试补齐。

### Added
- 左右拆分视图（`Ctrl+\`）：当前文件克隆到右侧独立编辑，可一键同步。
- 浅色/深色主题切换：设置面板或命令面板切换，重建界面并保留打开文件。
- 编辑器设置面板（`Ctrl+,`）：字号、缩进、自动格式化、自动保存、自动换行。
- 集成终端（`Ctrl+反引号`）与包管理面板。
- 运行选中代码（`Ctrl+Shift+Return`）。

### Fixed
- 断点行号按真实文件映射，调试执行行显示箭头与整行高亮。
- 活动栏「搜索」改为全项目搜索入口。

## [4.3.0] - 2026-11-07

报错体验三端统一与 imporc 模块导入补齐：错误统一给出源码片段、波浪线定位、错误码与带行号调用栈；新增 `impor explain`；C 后端 / imporc 的错误文案与解释器同源；imporc 支持 `from ... import`、包路径与嵌套 import；`impor build` 输出耗时、产物大小与依赖模块。

### Added
- `impor explain [E1001|ZeroDivisionError]`：直接解释错误原因、修复方式与最小示例；不带参数列出全部错误码。
- 统一错误显示：运行错误首行 `运行错误: 第N行: 异常名: 消息 [E100x]`，随后「为什么 / 修复 / 源码片段 + `^` / 调用栈（自顶向下，带行号）」；解释器、C 后端、imporc 三者同格式。
- C 后端独立 exe 运行时错误：源码行编译期嵌入产物，脱离 Python 后仍能打印出错行与上下文。
- imporc 模块导入：支持 `from mod import a`、`from mod import a as b`、`from mod import *`、`import pkg.mod`、`import pkg.mod as m`，以及函数体内嵌套 `import`（编译期内联 + 运行期模块对象）。
- `impor build` 成功输出：构建路径、产物大小（人类可读 + 字节数）、总耗时（转译 / tcc 分项）、依赖模块列表、是否依赖 Python。
- 转换内建：新增 `str(x)` 与 `int(x)`（浮点截断、无法解析返回 0），解释器 / C 后端同语义；`to_str` 保留为兼容别名。
- 编辑器启动改为空白文档，不再自动载入示例程序。

### Fixed
- imporc 调用栈行号不再恒为 0：直接函数调用记录真实源码行。
- imporc 编译错误提示带源码行号，不再只给一句无位置的消息。
- imporc 产物与 C 后端共用运行时：无源码嵌入时使用运行时内置空表，不再出现 `undefined symbol 'g_src_lines'`。

### Tests
- 新增 `tests/test_v43_unify.py`：`impor explain` 四种路径、`impor build` 输出字段、C 后端运行时片段 / 错误码 / 调用栈、imporc `from` + 包路径 + 嵌套 import 产物执行。

## [4.2.0] - 2026-11-07

P0 第一批：面向真实项目的容器能力与测试基础设施。字典、切片、嵌套函数/闭包与模块对象在解释器、C 后端、imporc 自举编译器三端打通（imporc 支持单层 `import mod [as m]`），`expect()` 提供表驱动断言，`impor test` 每个测试文件独立临时目录隔离。

### Added
- 字典：`{k: v}` 字面量、`d[k]` 读取与赋值、`d.keys()` / `d.values()` / `d.items()` / `d.get(k, 默认值)` / `d.has(k)`、`for k in d` 迭代键；字典与数组参与 `==` 时按内容深比较。
- 切片：数组与字符串 `c[a:b]`、`c[a:b:step]`、`c[-2:]`、`c[::2]`；负索引、越界裁剪、负步长与 Python 语义一致；`step == 0` 报 `ValueError`；字典切片报 `TypeError`。
- 嵌套函数 / 闭包：函数体内 `func` 捕获外层自由变量，`nonlocal set` 改捕获变量、`global set` 改全局；函数可作为参数、返回值、变量并在之后调用。C 后端生成独立 C 函数 + `Val*[]` 环境，imporc 将全部局部装箱为 `Val*`，重复调用保留状态。
- `expect(actual, expected, label?)` 内建断言：失败抛 `AssertionError`，消息统一为「标签：期望 X，实际 Y」；解释器、Python 转译、C 后端与 imporc 支持。
- 模块对象作为值（C 后端）：`import m as mod` 后可赋值、传参、打印、比较；运行期 `V_MODULE` 承载模块名与「导出名 -> 函数/顶层变量」属性表，`mod.f(..)` 动态调用、`mod.v` 读取、`type_of(mod) == "module"`、打印为 `<Impor 模块 名>`，与解释器一致。
- `impor test` 临时目录隔离：每个测试文件在独立临时目录执行，`IMPOR_TEST_TMP` 提供路径；新增 `--no-isolate` / `--keep` / `--filter 关键字`；汇总显示断言条数。
- 统一错误码：运行错误首行附加 `[E1001]`…`[E1099]`，解释器 / C 后端 / imporc 同一套编号（`ZeroDivisionError`=`E1001`、`NameError`=`E1008`、`AssertionError`=`E1007` 等）。
- 调用栈：未捕获运行错误在四段式后输出「调用栈（自顶向下）」，C 后端与 imporc 产物同样输出；`try/catch` 捕获后不打印、进程正常继续。
- 诊断文案对齐：C 后端除零 / 下标越界的消息改为与解释器一致的 `division by zero` / `list index out of range`。

### Fixed
- `imporc` 字典下标赋值、字典 `for` 迭代、`rt_method` 方法分派与切片后缀生成。
- Python 转译字典 `keys()` / `values()` 不再外泄 `dict_keys` / `dict_values` 内部类型。
- C 运行时 `rt_repr` 让 `print(d)` 的嵌套字符串带引号，与解释器输出一致。

### Tests
- 新增 `tests/test_v42_dict.py`（8 项）：解释器 / Python 转译 / C 后端 / imporc 四路径字典切片输出一致，`expect` 成功计数与失败消息，`impor test` 文件隔离。
- 新增 `tests/test_v42_closure.py`（4 项）：闭包捕获 `var` 局部、`nonlocal` 改值、函数作参数，四路径输出一致。
- 新增 `tests/test_v42_module.py`（3 项）：模块赋值/传参/动态调用/打印/比较，解释器与 C 后端一致；缺属性报错。
- 新增 `tests/test_v42_diag.py`（5 项）：错误码表唯一性、解释器与 C 后端首行 / 调用栈一致、`catch` 后退出码仍为 0。
- `tests/test_impor.py` 183 项通过。

## [4.1.0] - 2026-11-07

语言核心补齐与私有源可写运营：C 后端可直接导入模块并保持独立 exe，标准库补 `type_of` / `flatten`，自举编译器修复嵌套 `switch`；包管理器从只读消费升级为可打包、可发布、可检查过期、可清理缓存。

### Added
- C 后端模块导入：`import m` / `import m as mm` / `from m import a` / `from m import a as b` / `from m import *`；编译期拼接模块代码，按引用裁剪未使用定义与死代码，`std.*` 与自定义模块同名符号内部改名隔离；支持相对导入、目录包 `__pkg__.impor`。
- `type_of` 内建：解释器 / Python 转译 / C 后端 / imporc 四路径统一，返回 `null` / `int` / `float` / `str` / `bool` / `list` / 类名；C 运行时 `ClassInfo` 增加显示名。
- `std.collections.flatten`：递归展开嵌套数组。
- `impor pkg pack`：确定性 zip（固定时间戳、权限、条目顺序），同内容必同 `sha256`。
- `impor pkg publish`：目录自动打包或直接发布 `.zip`，上传后自动创建/更新 `index.json`；同版本需 `--force`。
- `impor pkg outdated`：按锁文件来源比对约束内可用最高版本。
- `impor pkg prune`：清理缓存中不可达的版本。
- 私有源 HTTP `PUT` 上传与 Bearer 鉴权，`file://` / 本地路径可直接写并自动建父目录。

### Changed
- 版本统一为 `4.1.0`：`Impor.py`、`impor/__init__.py`、`pyproject.toml`、`bootstrap/imporc.impor`、VS Code `package.json` / `package-lock.json`。
- 重建 `impor/imporc.exe`（8MB 栈、图标注入、版本打印 4.1.0）。
- 清理 `ctrans.py` 过时的「不支持节点」文案：属性访问、类、异常处理、`match` 等已支持项不再误报。

### Fixed
- imporc 嵌套 `switch`：引入 switch 缩进栈，修复内层 switch 消耗外层缩进基准导致的分支错乱；补齐收尾 `DED` 回退，避免函数体被提前关闭、主程序被残留语句打断。
- `install()` 写依赖约束时保留原始约束（原来写死版本号，导致 `outdated` 永远判已最新）。

### Verification
- 新增 `tests/test_v52.py`（5 项）：确定性 pack、`file://` publish + install、重复版本需 `--force`、HTTP PUT 假源 publish + outdated、prune 清缓存。
- 新增 `tests/test_v53.py`（8 项）：`type_of` 解释器与 C（含对象类名）、C 模块导入（`import` / `from` / 别名 / 类）、死代码裁剪、`std` 同名符号、`flatten`、imporc 嵌套 `switch`。
- 自举闭环 A→B→C→D 全部通过，B/C/D 产物逐字节一致；`imporc` 版本输出 `v4.1.0`。
- 全量 `python run_tests.py`：**536 项全部通过**（490.038s）；conformance 单独运行 23/23 逐字节一致。

### Known limitations
- C 后端模块导入为编译期拼接：模块顶层代码会随主程序执行；不支持嵌套函数/嵌套类、把模块对象当值传递、Python 模块导入。
- `pkg publish` 的 HTTP 契约要求源端支持 `PUT`；只支持手工上传的源需自行上传归档与 `index.json`。
- VS Code 市场上架仍需要 publisher `impor-lang` 的账号权限；没有 PAT 时可用 `vsce publish --azure-credential` 走 Microsoft Entra ID 登录，本环境只完成 VSIX 产物与发布流程。
- `vscode-impor/package.json` 的 `repository` 仍为空：项目不是 git 仓库，环境中没有可确认的公开仓库 URL，未编造填充（vsce 仅警告，不影响打包）。

## [3.10.0] - 2026-11-07

生态闭环：可复现的依赖锁定、私有源，以及 VS Code 扩展的市场发布链路。

### Added
- 锁文件升级到 `impor.lock` v1.1：记录每个直接/传递依赖的 `version`、`source`、12 位目录哈希、完整目录 `dir_sha256`、归档 `sha256`、`requested` 约束与 `requires` 列表；`[lock]` 段记录 `generated_by` / `generated_at` / 私有源地址。
- `impor pkg install --frozen`：严格按锁文件还原缺失或被改动的包，逐项复核版本与 `dir_sha256`；还原后再校验一次，任何漂移立即报错退出（适合 CI 与复现构建）。
- `impor pkg lock --check` 与 `impor pkg check`：校验锁文件、缓存版本与完整内容哈希，不一致逐条打印并返回 `1`。
- 私有源：`impor.toml` 新增 `[registry] url` + `token_env`（默认 `IMPOR_TOKEN`，也兼容直接写 `token`）；`IMPOR_REGISTRY` 环境变量可覆盖 URL。索引约定 `<url>/index.json`，包归档为 zip 且必须提供 `sha256`；支持 `name`、`name@版本`、`name=约束` 三种安装写法。
- 私有源鉴权：HTTP 请求带 `Authorization: Bearer <token>`；`impor pkg registry` 只显示 URL 与令牌是否已配置，绝不回显令牌。
- 完整性：归档下载后先校验 sha256 再解压，解压时拒绝路径穿越；归档内包名/版本与索引不一致直接拒绝。
- `impor pkg upgrade` 支持私有源（按约束取最高版本并复核约束），git/本地源重新拉取并刷新锁文件。
- VS Code 扩展发布元数据：`LICENSE`、128×128 `icon.png`、`CHANGELOG.md`、`.vscodeignore`、`keywords` / `galleryBanner`；`vscode-languageclient` 移入运行时 `dependencies`，VSIX 自带依赖；新增 `PUBLISH.md` 与手动触发的 GitHub Actions 发布工作流。
- 实际打出 `vscode-impor-3.10.0.vsix`（322 个文件，约 471 KB），可直接 `code --install-extension` 分发。

### Verification
- 新增 `tests/test_v51.py`（11 项）：锁文件来源/哈希字段、缓存篡改检测与 `--frozen` 还原、传递依赖 `requires`、CLI `lock --check` / `install --frozen` / `check`、私有源安装与来源串、`>=0.1` 取最高版本、`file://` 索引、HTTP Bearer 鉴权成功/缺令牌失败、sha256 篡改拒绝、私有源传递依赖自动安装。
- `npx @vscode/vsce package` 成功，VSIX 内含 `LICENSE`、`changelog.md`、`icon.png`、`node_modules/vscode-languageclient`、语法文件与客户端。
- 全量 `run_tests.py` **522 项 OK / 458.435s**；conformance **23/23 三后端逐字节一致**；旧 `tests/test_impor.py::TestPkg` 3 项保持通过；自举闭环 B/C/D 三代产物逐字节一致。

### Known limitations
- 私有源只支持 zip（不支持 tar.gz）；`index.json` 必须显式提供 `sha256`，缺失即拒绝安装。
- `impor pkg install --frozen` 的本地/git 来源仍要求原路径或远端可用；归档来源以 `registry:<url>#<name>@<ver>` 写入锁文件。
- 市场发布需要 publisher `impor-lang` 与 `VSCE_PAT`，本环境没有令牌，只完成到 `vsix` 产物与发布流程；实际上架需在 `PUBLISH.md` 步骤中提供 PAT。

## [3.9.0] - 2026-11-07

生态与工具链：标准库扩充、`impor fmt` 真换行、LSP 格式化、`lint --fix`、imporc switch 绑定模式。

### Added
- 标准库新增 `std.algo`：`sorted` / `sorted_desc` / `index_of` / `bsearch` / `min_index` / `max_index`，全部用三端共有内建实现；`use std` 现已覆盖 text / mathx / collections / algo 四库。
- `std.text` 新增 `pad_left` / `pad_right` / `index_of` / `count_sub` / `split_lines` / `is_blank`；`std.collections` 新增 `find` / `find_index` / `take` / `drop`。
- `impor fmt --max-line N` 真正生效：对超长调用/数组按顶层逗号在括号内换行，参数缩进一级，语义不变；续行可幂等重格式化。
- LSP 新增 `documentFormattingProvider` / `documentRangeFormattingProvider`，VS Code 保存时格式化直接复用 `impor fmt`；`impor.formatFile` 命令改为走 LSP，不再起子进程。
- `impor lint --fix`：自动删除未使用导入、给未使用变量加 `_` 前缀（其余规则仍只报告）。
- `impor lint --style` 新增 `enum-exhaustive`：对 enum 变量做 match 且缺成员、又无 `default` / `case _` 时给提示。
- `impor.toml [lint]` 新增可配置阈值：`max_function_lines` / `max_function_statements` / `magic_number_repeats` / `short_name_whitelist` / `enum_exhaustive`。
- `bootstrap/imporc.impor` 的 `switch` 支持绑定模式（`case v`）与 `Color.MEMBER` 常量分支；新增 conformance `23_switch_match.impor`（三后端逐字节一致）。
- VS Code 扩版本升到 `3.9.0`，补齐格式化能力声明；LSP 客户端消费 `stderr` 输出到 Output Channel，避免管道写满阻塞。

### Verification
- `tests/test_v50.py` 新增 9 项：fmt 换行幂等与语义不变、注释/块头不拆、enum 穷尽性、lint 阈值、`--fix`、LSP 格式化与能力声明、`use std` 四模块注入、标准库 demo 解释器/转译一致。
- conformance `23_switch_match` 三后端逐字节一致，全量 **23/23**；自举闭环 B/C/D 三代产物逐字节一致；全量 `run_tests.py` 通过。

### Known limitations
- `imporc`（自举编译器）对**嵌套 switch**（`case` 体里再写 `switch`）仍会报「意外的缩进开始」；C 后端与解释器不受影响，嵌套场景请先用 `impor build` / 解释器。
- `std.text` 的旧函数（`split` / `trim` / `upper` / `lower` / `replace` / `contains` / `starts_with` / `ends_with` / `char_at` / `substring`）仍依赖 Python 宿主方法，C 后端暂不支持；v3.9 新增函数已按三端共有内建实现。

## [3.8.0] - 2026-11-06

语言可用性第二批：`enum` 枚举 + lint 可读性规则 + fmt 大括号块缩进。

### Added
- `enum` 软关键字：支持 `enum Color { RED, GREEN, BLUE }` 与缩进块两种写法，成员可用逗号/分号/换行分隔；值自动从 0 递增，支持显式整数值（`MEDIUM = 5`，后续成员继续递增）；`Color.RED` 在解析期改写为脱糖整型常量，可直接与 `if` / `switch` 配合。关键字总数不变。
- 三端同步：解释器 / Python 转译 / C 后端（`impor build`）/ 自举编译器（`imporc`）均支持 enum；`imporc` 新增 enum 分支与 `Color.MEMBER` 改写。
- conformance 新增 `22_enum.impor`，三后端输出逐字节一致（22/22）。
- `impor lint --style`（或 `--readability`、`impor.toml [lint] style = true`）新增三条可读性提示：命名过短、重复魔法数字（同一非小整数出现 3 次以上）、函数过长（超过 50 行或 30 条语句）。全部为提示级，不影响退出码；默认 lint 不启用，避免误报。
- `tests/test_v49.py`（21 项）：enum 四路径一致性 + `switch` 中 `Color.X` 模式 + 语法边界、lint 三条规则与默认不误报、fmt 大括号缩进/注释保留/语义保留/幂等/字典键冒号紧贴。

### Fixed
- `impor fmt` 大括号块缩进：原实现只累计词法 INDENT/DEDENT，`func f() { ... }` 的块内语句会被顶到行首；改为同时累计 `{`/`}` 配对深度，闭合括号与块头对齐，并忽略块内原始空白造成的假缩进。
- `impor fmt` 行内空格：左括号/左方括号后不再保留多余空格，字符串结束后与 `+` / 逗号 / 关键字之间恢复空格，`{` 前补空格（`func f(a, b) {`、`print("值:" + x)`）；字典键为字符串时冒号前保持紧贴（`{ "width": W }`），不再写成 `"width" : W`。
- 测试稳定性（v3.8 全量回归）：安全软件偶发把 `%TEMP%` 下新生成的 exe 隔离/删除，`test_v36` 的 2048 冒烟改用项目内临时目录构建，`run_tests.py` 对所有 `.exe` 启动额外重试 `FileNotFoundError`；`examples/snake.impor` 已按新 fmt 规则就地格式化，`impor check` 恢复通过。

### Docs
- `docs/语言规范.md` 新增 enum 枚举小节；`conformance/README.md` 用例清单更新为 22 个。

## [3.7.0] - 2026-11-06

系统级核心第一批：`struct` 字段记录类型。

### Added
- `struct` 软关键字：`struct Point` + 缩进块内 `var x = 0` 字段声明，解析器脱糖为 `ClassDef` + 自动 `__init__`；构造按字段声明顺序传参（`new Point(3, 4)`），字段可读写；空结构体、重复字段、非字段语句均报语法错误。关键字总数不变。
- 三端同步：解释器 / Python 转译 / C 后端（`impor build`）/ 自举编译器（`imporc`）均支持缩进块与 `{ ... }` 大括号块；`imporc` 新增 struct 分支（两种块写法）与自动 `__init__` 生成。
- `tests/test_v46.py`（6 项）：解释器、Python 转译、C 后端、imporc 四路径同负载输出一致 + struct 体内非法语句报错。
- 应用图标：新增 `I++` 品牌图标（深色圆角底 + 白色衬线 I + 双蓝加号），提供 16/32/48/64/128/256/512/1024 PNG 与多尺寸 `impor.ico`；编辑器主窗口、启动过场、关于窗口、调试器、`input` 与断点对话框统一设置窗口图标，并独立 `AppUserModelID` 让任务栏不再显示 Python 文档图标。图标由 `tools/make_icon.py` 确定性生成，可重复构建。
- 独立 exe 图标：`impor build` 在 tcc 编译成功后，用 Windows `UpdateResource` 把 7 档图标写入 PE 的 `RT_ICON` / `RT_GROUP_ICON`（无需 windres），产物在资源管理器、任务栏与文件属性中显示 I++ 图标；`imporc.exe` 一并带图标，`bootstrap/rebuild_imporc.py` 重建时自动补齐。`tests/test_v47.py`（3 项）验证 ICO 帧完整、构建产物含图标且仍可运行。

### Fixed
- C 后端多实参构造/方法调用：tcc 对元素由函数调用初始化的复合字面量 `(Val[]){...}` 报 `index too large`（2 个及以上必现），导致 `new Point(3, 4)` 与双参 `__init__` 长期不可用。改为语句表达式内临时数组 `({ Val _iaN[k] = {...}; rt_new(...); })`；`ctrans.py` 与 `bootstrap/imporc.impor` 的 `new` / `obj_call` / `rt_supercall` 三处同步修复。
- 测试稳定性：`run_tests.py` 对测试内部所有 `.exe` 启动统一重试 `PermissionError`（新写入图标资源的 exe 会被 Windows 安全软件短暂扫描锁定），避免把环境扫描误报成功能失败；被测逻辑与断言不变。
- C 后端 `input()` / `inputstr("提示")`：运行时函数误把字符串实参声明为 `const char *`，实际传入的是 `Val`，tcc 报 `cannot cast 'struct <anonymous>' to 'const char *'`；零参数调用还会报 `too few arguments`。改为接收 `Val` 并用 `rt_str_of()` 取提示文本，生成器为零参数补 `v_str("")`；`c_runtime.c` 与 `ctrans.py` 同步修复，`tests/test_v48.py`（3 项）覆盖带提示、零参数与数字转换。

### Docs
- `docs/语言规范.md` 新增 6.1 节（struct 语义、字段规则、软关键字、三端一致性）。

## [3.6.0] - 2026-11-06

语言宪法 P2：人类友好的诊断系统。

### Added
- 启动过场动画：图形编辑器打开前显示自绘的 `IMPOR IS COMING` 画面（进度条、三段状态文案、版本号），约 1.15 秒后淡出进入主窗口；单击 / Esc / Space 可跳过，`IMPOR_NO_SPLASH=1` 可关闭。
- 四段式诊断：`哪里 + 是什么 + 为什么 + 修复`。`impor/diagnostics.py` 新增 `render_diagnostic()`，CLI（`run` / `run-dbg` / `check` / `lint` / `test` / `compile`）、REPL、LSP、编辑器输出面板共用。
- 诊断知识库 `explain(subject, message)`：覆盖未定义名字、参数个数、不可调用、除以 0、下标越界、类型不匹配、const 只读、断言失败、递归过深、导入失败、属性缺失、作用域、运算符、左值、C 后端限制等 20+ 条规则；未命中时给通用兜底。
- 上下文代码片段：出错行前 1 行 / 后 1 行 + 行号槽 + 按显示宽度对齐的波浪线（中文按双宽）。
- 彩色输出：自动按 `sys.stderr.isatty()` 判断，Windows 控制台自动开启 VT；`IMPOR_COLOR=always|never` 强制、`NO_COLOR` 关闭。
- `tests/test_v44.py`（22 项）覆盖知识库、四段渲染、片段对齐、彩色开关、CLI / REPL / LSP 实跑。

### Changed
- 语法错误首行保持 `语法错误: 第N行 列M:`（既有脚本与断言零破坏），其后追加「为什么 / 修复」。
- 运行错误统一为 `运行错误: 第N行 列M: <异常名>: <信息>`，并附原因与修复。
- `impor lint`：警告 / 错误项后追加缩进「修复：」，`文件:行:列 [级别]` 行首格式不变。
- `impor test` 失败改用同一渲染器，保留 `AssertionError` 等异常名。
- LSP `publishDiagnostics` 的 `message` 追加「为什么 / 修复」（语法错误与未使用变量 / 导入）。
- 编辑器输出面板在语法错误 / 运行异常后追加「修复：」一行。
- 版本四处真源同步 3.6.0，`impor/imporc.exe` 按新种子重建。

### Docs
- `docs/语言规范.md` 新增第 22 节（诊断信息结构）。
- `docs/新手教程.md` 新增 15.4 节（看懂报错）。
- `README.md` 版本描述与更新日志新增 v3.6.0。

### Known limitations
- C 后端仍按 `/*@L<行号>*/` 标记回溯 Impor 行号；宏展开、跨行表达式可能定位到最近的上一处标记。
- 彩色只在终端生效；被管道或文件捕获时输出纯文本（有意为之）。
- 知识库按异常名与消息模式匹配，自定义异常名未收录时只给通用兜底建议。

## [3.5.0] - 2026-11-05

语言宪法 P0+P1：写法收敛 + 文档注释。

### Added
- `docs/写法规范.md`：唯一「推荐写法表」，12 类意图各给出唯一推荐写法与对应兼容写法。
- `impor lint --style`（及 `impor.toml` 的 `[lint] style = true`）：报告 v3.4 兼容写法并给出唯一推荐写法；全部 `[提示]` 级，不影响退出码。
- `##` 文档注释：整行 `##` 绑定紧随其后的 `func`/`def`/`class`/`exception`/`var`/`const` 及类方法，写入 AST `doc` 与符号表 `Symbol.doc`。
- `textDocument/hover` 追加文档注释（引用块显示）。
- `impor doc FILE [-o API.md]`：从文档注释生成 Markdown API 文档。
- `tests/test_v43.py`（19 项）覆盖词法 / 解析 / 解释器 / C 后端 exe / hover / doc 命令 / style 检查 / fmt 保真。

### Changed
- 编辑器：`##` 文档注释使用独立配色（`#7d94b8`），与普通注释区分。
- 语法糖冻结：v3.5 起不再新增同义写法；v3.4 的 12 类写法正式降级为「兼容层」。
- 版本四处真源同步 3.5.0（`Impor.py` / `impor/__init__.py` / `pyproject.toml` / `bootstrap/imporc.impor`），`impor/imporc.exe` 按新种子重建。
- 启动器命令白名单新增 `doc`。

### Docs
- `docs/语言规范.md` 新增第 20 节（文档注释）与第 21 节（写法收敛）。
- `README.md` 版本描述与更新日志新增 v3.5.0。

### Known limitations
- `imporc`（自举编译器）按 v3.3 语法子集工作，不认 `##` 文档注释；带文档注释的脚本请走 `impor run` / `impor build`。
- 文档注释是纯文本，不解析 Markdown 结构、不做交叉引用检查；悬停不自动补参数说明与类型。
- 兼容写法不会自动迁移，`impor lint --style` 只提示不改写。

## [3.4.0] - 2026-11-05

语法宽容层：同一意图多种写法，常见笔误不再直接解析失败。所有新写法在词法/文法层降级为既有 AST 节点，解释器 / Python 转译 / C 后端（`impor build` 独立 exe）行为一致；关键字总量仍为 120，语义不变。

### Added
- `print` 可省略括号（`print "hi"`、`print a, b`）；中文软别名 `打印` / `输出`。
- `;` 作语句分隔符（多余分号忽略），单行块体支持多条语句。
- 类型注解 `var x: int = 5`、`func f(a: int) -> int`（解析后丢弃，仅作提示）；`var x` 可省略初始化（= none）。
- 多行调用 / 数组：圆括号、方括号未闭合期间换行不再结束语句。
- `do { } until cond` / `do { } while cond`（降级为标志变量 + while，`continue` 语义正确）。
- `unless` / `除非` 取反条件；`then` / `则` 作块引导符（可代替冒号）。
- `for i in 1..5` 闭区间；`for each x in ...`；`for x of ...`。
- `for i = 0; i < 3; i++` / `for j = 1; j <= 3; j++`：C 风格计数循环，降级为 `for i in range(...)`。
- `++` / `--`（前缀/后缀，含 `arr[i]++`、`obj.n--`）；`&&` / `||` / `<>`；`:=` 赋值。
- `func f a, b { }` 参数省略括号；`class A extends B`；`new A` 省略空括号；`super.m()` 等价 `super().m()`。
- `catch { }` 不写异常变量、`catch (e)` 括号形式。
- `tests/test_v42.py`：32 项回归（解释器 + C 后端 exe 实跑）。

### Fixed
- C 后端补齐 `Block` 语句生成；全局/局部符号收集递归进入块与循环体（此前 `do` 块内新变量报 `undeclared`）。
- 数字扫描不再把 `1..5` 拆成 `1.` 与 `.5`。

### Changed
- 版本 3.4.0（`Impor.py` / `impor/__init__.py` / `pyproject.toml` / `bootstrap/imporc.impor`）；`impor/imporc.exe` 已按新种子重建（184832 字节）。
- `docs/语言规范.md` 新增「19. 宽容语法层（v3.4）」。

### Known limitations
- `imporc` 自举编译器仍按 v3.3 语法子集工作；宽容写法请走 `impor run` / `impor build` / `impor compile`。
- 类型注解只解析不检查；参数默认值、变参未支持。

## [3.3.0] - 2026-11-05

一致性收敛版本：**不新增语言特性**，只消除三端（解释器 / C 后端 / 自举编译器）的不一致与崩溃点。

### Fixed
- Unicode 语义：`len` / 下标 / `strcut` 一律按字符计数（C 后端按 UTF-8 编码单元），负数下标与越界报错；字符串字面量可直接下标（`"你好"[1]` 此前被误解析为 `print` 的第二个参数）。
- 数值：C 后端明确为 64 位有符号整数，加/减/乘溢出报 `整数溢出：超出 64 位有符号整数范围`（此前静默回绕）。
- 语义对齐（以解释器为准）：`1 == true` 为 `true`；`1 + "a"` 报 `TypeError`（不再隐式拼接）；`"ab" * 3` 得 `"ababab"`。
- 递归：解释器上限提升到约 3000 层，超限报 `RecursionDeep`（此前 1000 层即 `RecursionError`）。
- C 编译失败：tcc 的行列错误反查回 Impor 源码行，输出 `C 编译错误: 第N行 列M` + 源码 + 指示符。
- 自举编译器 imporc：补齐 `div` / `type` 内建、`set 数组[下标] = 值`、字符串下标走 `rt_index`；`imporc_args.txt` 支持双引号包裹含空格的路径（此前路径被空格截断，报 `fileread: 无法打开文件`）。

### Added
- `conformance/`：20 个脚本 × 三后端逐字节比对（`run_conformance.py`，支持 `--ci` / `--only` / `--json`），接入 `tests/test_conformance.py` 与 GitHub Actions。
- `tests/test_v41.py`：13 项 v3.3 回归。
- `LICENSE`（MIT）、`CHANGELOG.md`、`.gitignore`。

### Changed
- `mem()` / `gc()` 输出补齐 `freed` / `freed_total` / `live_scope`，并说明"容器由引用计数即时释放、不进入存活采样"，使数字口径不再令人困惑。

## [3.2.0] - 2026-11-04

- 自动内存管理：`MemoryManager`（阈值回收 / 分配统计 / 存活分类）、`mem()/memstats()/gc()/autogc()` 三端内建、`impor mem` 子命令、C 运行时托管数组与字符串驻留池。
- 图形编辑器重构为深色 Code Studio：多标签、命令面板、输出/问题/变量/内存四联面板、token 化高亮、后台线程运行与调试步进。

## [3.1.0] - 2026-11-02

- 三端语法能力对齐：imporc 补齐 `match/switch`、`try/catch/finally`、`repeat`、数组 `for-in`/字面量、`range`；C 后端新增 `class`/继承/`super` 与数组 `for-in`。
- 8MB 栈补丁修复大文件编译栈溢出（`_setstack.py`）。

## [2.19.0] - 2026-10-31

- 完整自举独立编译器：`bootstrap/imporc.impor` 用 Impor 自身实现，原生 C 独立 exe，脱离 Python。

## [2.18.0] - 2026-10-29

- 大括号代码块 + 120 关键字（58 英文 + 62 中文）。

## [2.17.0] - 2026-10-27

- C 后端数组支持、`const` / `switch` 关键字。

## [2.16.0] - 2026-10-25

- `div()` 内建、REPL 帮助系统、`impor env` / `demo` / `bench` 子命令。

## [2.13.0] - 2026-10-18

- C 后端（`build` 命令）：Impor AST → C → 随包 tcc 原生编译，产出不依赖 Python 的独立可执行文件。

更早版本（v2.1–v2.12）的能力变更见 `README.md` 更新日志历史条目。
