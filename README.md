# Impor (I++)

> 本仓库已公开：CI 结论与运行截图见下方「CI 证据」一节，徽章可直接访问，不再有 404。

**Impor**（读作 "I++"）是一个自制的、完整的编程语言：拥有词法分析、语法分析、AST、树遍历解释器、编译到 Python 的转译器、C 后端、WASM 后端、**自有字节码虚拟机（NVM）**、**教科书优化编译器（OPTC，寄存器式）**、**纯自足原生 x64 AOT（Native64，自写机器码+PE32+）**、交互式 REPL、命令行工具和带语法高亮的图形化编辑器。

版本：**5.6.0**。定位：**可交付的小型教学语言**——能解压即用、能编译成 exe 交付、能自举，但它不是系统语言，也不是生产脚本语言。

**明确不竞争的维度**（写在这里，免得被拿来和 Rust / Go / Java 比时产生误会）：

| 维度 | 现状 | 为什么不去争 |
| --- | --- | --- |
| 生态 | 自带 37 个 std 模块 + 4 个自写原生绑定（http / image / sqlite3 / zlib） | 包管理器只做本地与 git 源，不接公共生态；一个人的时间和一个社区的体量不是一个量级 |
| 类型系统严格度 | 渐进类型：有标注才检查，默认 WARN，`--strict-types` 才 ERROR | 刻意保留「不标注也能跑」的宽松感；做全域推导/子类型系统是另一个项目 |
| 后端深度 | C 后端（gcc/tcc）+ WASM + 自有字节码 NVM + **寄存器式优化编译器 OPTC** + **纯自足原生 x64 AOT Native64**（自写机器码字节+PE32+，零外部工具链） | 已能直接发射原生机器码并产出可双击 exe；浮点 SSE/dtoa 与栈传参解 6 参数上限作为下一步独立评估 |
| 性能极限 | fib(28) 与手写 C 同级（20.4 / 19.9 ms）；1e8 长循环 1.55×（保留 E1012 逐运算检查） | 逐运算溢出判定本身约 +25 ms/1e8 次，物理上换不来 1.2×；要 C 同级得显式 `--no-int-ck` 放弃检查 |

**竞争的维度**：构建与启动速度、错误诊断质量（位置 + 为什么 + 修法 + 文档链接）、便携交付（解压即用、产物是单个 exe）、三端一致（解释器 / C 后端 / IR→C 逐字节对拍）、可自举（A→B→C→D 逐字节一致）。
已有条目都写在上面；“哪个后端支持什么”改成实测生成：[`docs/能力矩阵.md`](docs/能力矩阵.md)（`python tools/support_matrix.py`，`--check` 守住“已知缺口之外不得新增不一致”）。已知缺口仍挂在那里：C 后端编译期拒绝 `import`；`imporc` 是过程式子集，管道 `|>` 与部分较新内建不在它的映射表里（遇到明确报错，不静默改语义）；花括号归一化只认「行尾 `{` + 行首 `}`」，同一行里写多个语句仍不支持。

4.0 的核心变更：**唯一写法 + 验收自证**。12 类历史兼容写法已从 lexer/parser 物理删除（`func` / `loop` / `let/local` / `print` 省括号 / `:=` / `++/--` / `&& || !` / `<>` / `then` / `unless` / `for each|of` / `1..n` / C 风格 `for` / `do..until`），再写会得到 `E2002`；`check-fmt` 改为文件系统扫描，不依赖 Git；README 首屏的版本、保留字、内建、错误码与格式门禁数字由 `verify_claims.py` 从发布包内文件计算，不手写；源码区 `func` 已清零，`KEYWORDS` 58 → 55。

**v4.1 修订（工作区，未发版）**：两件事已实测闭合，不是改口径——
① `exception` / `raise` / 类型化 `catch T e` / 越界与除零，C 后端现在与解释器逐字节一致；
② `impor build` 默认走数值快路径，fib(28) 本机 **解释器 292 ms → 完整 Val 后端 456 ms → 默认构建 42 ms**，快路径带 64 位溢出检查（越界报错退出，不再静默回绕），要任意精度加 `--no-fast`。
③ 计数循环也进快路径（patch4）：`for i in range(1000000)` 此前被静默回退到完整 `Val` 后端，现在由 `cfast` 直接生成 C 的 `for`，`range(N)` 的界只求值一次。实测（本机、含进程启动、7 次取最优）**fib(25) 24.8 ms / 100 万循环 34.0 ms**，同一脚本的解释器是 287 ms / 2139 ms。详见 [`docs/性能报告-v6.3.md`](docs/性能报告-v6.3.md)。
④ 默认编译器换成 gcc -O2（patch5）：`impor build --cc auto|tcc|gcc`，`auto` 有 x86_64 gcc 就用 gcc。实测 `fib(25)` **21.5 ms**、百万循环 **20.7 ms**、浮点百万循环 **22.9 ms**，手写 C 基准 19.6 / 19.2 ms；`--no-fast` 完整 `Val` 后端 `fib(28)` 从 tcc 的 473–712 ms 降到 **82.9 ms**（解释器 301 ms）。`range(A,B,STEP)`、多参数 `print`、浮点 `double` 一并进快路径。详见 [`docs/性能报告-v6.4.md`](docs/性能报告-v6.4.md)。
⑤ `impor run` 也有 C 级路径（patch6）：`impor run --native x.impor` 先编成原生产物再执行（按源码指纹缓存，子集外自动回落解释器）；`impor.bat` 现有原生启动器 `impor-fast.exe`，命中缓存时直接执行产物。实测 `impor <脚本>` 百万循环 **39.7 ms**、`fib(25)` **38.1 ms**（C 16.7 / 16.5 ms，构建产物 16.3 / 16.9 ms）。纯 Python 的解释器本体会停在 176.8 / 2061 ms——那是 CPython 启动与 AST 遍历的地板，不是可优化项。详见 [`docs/性能报告-v6.5.md`](docs/性能报告-v6.5.md)。
⑥ `run` 默认就走原生、启动器改用 DLL（patch7）：`impor run` 不再需要 `--native`，默认即编译+缓存产物（`--no-native` 或 `IMPOR_RUN_NATIVE=0` 才用解释器），百万循环 **2132 → 207 ms**；`impor build --dll` 产出启动器专用 DLL，`impor-fast.exe` 在本进程内 `LoadLibrary` 调 `impor_entry`，不再起第二个进程，`impor <脚本>` **38.1/39.7 → 25.1/25.6 ms**（进程创建地板 18.6 ms）。顺手修掉两个真缺陷：热跑每次多付一次 `gcc --version`（147 ms，P0 收益被吃光），以及默认原生后 `const` 改写被快路径静默放行。详见 [`docs/性能报告-v6.6.md`](docs/性能报告-v6.6.md)。
⑦ **稳定模式 `--stable`（脚本级执行预算）**：`impor run --stable x.impor` 给一次执行加五道闸门——语句条数 500 万 / 墙钟 30 s / RSS 768 MB / 输出 8192 KB / 单次分配 800 万元素，越界以 `E1020`–`E1024` 退出并给出行号与修法；`--stable=strict` 是 200 万 / 10 s / 256 MB / 1024 KB / 100 万元素的批改档。分配在**真正发生之前**预检（`arrfill` / `list(n)` / `"x" * n` / `range` 等），所以不会等到系统 OOM；不开这一档时行为与之前逐字节一致。
    横向实测（同一台机器、外部 10 s watchdog、五引擎跑同五段敌意脚本）：Impor `--stable` **5/5 结构化熔断、0 挂起、0 原生崩溃**（最慢 0.97 s）；Python 1 例、Node 3 例、C 2 例只能被外部强杀，C 另在递归栈溢出与除零上原生崩溃。代价是同一段 20 万次循环负载慢约 1.2–1.4 倍（两次实测）——用一点速度换确定性，不白拿。`impor stability` 是本机自检（8/8 PASS）。边界写清楚：只保证失败可控，不保证永不失败；内存是 RSS 采样不是硬配额。详见 [`docs/稳定-4.0.md`](docs/稳定-4.0.md)、[`docs/稳定性对照-4.0.md`](docs/稳定性对照-4.0.md)。

⑧ **常驻服务 `impor serve` + 完整 C 后端接管（patch8）**：`impor serve start --bg / status / stop / run` 起一个常驻进程，客户端发一行 JSON 就执行，三档回退（DLL → 完整后端 exe → 进程内解释器）。百万循环 **25.6 ms（启动器）→ 4.79 ms/请求（serve）**，因为固定成本被摊成了 0；`impor-serve-client.exe -n N` 在同一连接上连发 N 次报均值，测的是真实连续调用。P3 不是重写 C 内核（实测手写 C 字节码 VM 比编译产物慢 60 倍），而是把已有的 `ctrans` 完整 Val 后端接进 `run` 与启动器的回落链路——字符串/列表/函数/异常脚本不再回落解释器，`g1` **8879 → 230 ms**（≈38×），启动器 31.6 ms，完整后端 24 例对拍 24/24（含浮点 `%` 与 `repr`）。修掉的三个真缺陷：`range` 步长 0 的完整后端死循环、循环变量遮蔽导致循环后 `print(i)` 打 `None`、以及 serve 的完整后端档每请求 726 ms（根因是**无控制台父进程 spawn 控制台子进程要新建 ConHost**，实测 643–763 ms，加 `CREATE_NO_WINDOW` 后 75.7 ms）。代价如实写：浮点最短 repr 引入 `strtod`，静态链接让产物从 153,088 B 涨到 255,488 B，exe 体积承诺因此由 160 KB 改为 320 KB。详见 [`docs/性能报告-v6.7.md`](docs/性能报告-v6.7.md)。

⑨ **自举编译器认花括号块 + 四处可用性缺陷（patch8b）**：`imporc` 此前对 `if (x) { … }` / `def f(n) { … }` 一律在闭合 `}` 上报「无法识别的语句」，而 4.0 的标准写法就是花括号。修法是在词法之前把花括号写法归一化成缩进写法（缩进 = 原行缩进 + 已开花括号层数×4，**保留文件自己的缩进风格**；纯缩进文件原样返回、一行不改），并把 40 处硬编码的诊断文件名改成真实脚本名。同时修掉：README 写、CLI 不认的 `impor serve run <脚本>`（参数循环把位置参数吃掉）、`impor syntax` 表与 README 两套数字（79+43=122 → **55 关键字 + 101 内建 + 1 运算符 = 157**，对齐 `tools/api_baseline.json`，`管道` 加别名，`exit` 关键字/内建双身份）、官网页停在 3.2.3（新增 `site/Impor官网-4.0.0.html`）。顺手修掉两个发布链上的静默 bug：`build_with_python.py` / `selfhost_check.py` 的 8 MB 栈补丁对 PE32+ 用了 `opt+0x30`（那是 `SectionAlignment`，写坏 EXE 头部，表现为「不是有效的应用程序」/ `Access is denied.`），已改回 `opt+0x48`。实测：9 个花括号/缩进用例全过（含 `} else {` 与块内注释）、纯缩进 `fib(25)` 回归不变、自举闭环 A→B→C→D 三代产物逐字节一致、三路一致性 **100/100**。
已有条目都写在上面；“哪个后端支持什么”改成实测生成：[`docs/能力矩阵.md`](docs/能力矩阵.md)（`python tools/support_matrix.py`，`--check` 守住“已知缺口之外不得新增不一致”）。已知缺口仍挂在那里：C 后端编译期拒绝 `import`；`imporc` 是过程式子集，管道 `|>` 与部分较新内建不在它的映射表里（遇到明确报错，不静默改语义）；花括号归一化只认「行尾 `{` + 行首 `}`」，同一行里写多个语句仍不支持。一致性覆盖面本身已从「16 例过 7 例」推进到 **`conformance/cases` 100/100**（解释器 / C 后端 / imporc 三路径 stdout 逐字节相同）。

## 3 分钟上手（陌生人路径）

1. 解压 ZIP，双击 `开始这里.bat`，看到「你好，Impor」即可。
2. 双击 `examples\quickstart\1-你好.bat`，运行第一个脚本。
3. 双击 `2-读文件.bat` / `3-调DLL.bat`，看读文件与调用系统 DLL。
4. 不装 Python、编译器或第三方包；卡住先看 `常见问题.txt`。
5. 想写自己的脚本：复制 `hello.impor`，改内容，双击同名 `.bat`。

从仓库直接跑（需要 Python 3.9+）：

```bash
python Impor.py run examples/quickstart/hello.impor
```

<!-- claims:start -->
**承诺兑现率：自动验证 22/22（100%），另有 2 项人工验收待测。** 每条承诺都能执行复现，明细见 [`docs/claims.md`](docs/claims.md)，源清单见 [`docs/claims.yaml`](docs/claims.yaml)。CI 会跑 `python tools/verify_claims.py --check`，任一条失败或数字漂移即红。

**当前构建（由本脚本从发布包内文件计算）：版本 5.6.0 · 保留字 55 · 内建 124 · 标准库模块 37 · 错误码 30 · 全仓 `.impor` 237/237 通过（另有 21 个基线/生成文件按规则跳过）。**
<!-- claims:end -->

### 三个真项目（陌生人可独立跑通）

| 项目 | 证明什么 | 入口 |
|---|---|---|
| [`apps/impor_ssg`](apps/impor_ssg/README.md) | 读文件 → Markdown → 模板 → 写 HTML 的完整闭环；解释器与 C 后端同一份源码 | `python Impor.py run apps/impor_ssg/ssg.impor` |
| [`apps/impor_action`](apps/impor_action/README.md) | 用 Impor 写的零依赖 GitHub Action；在别人机器上不需要 Python | `apps\impor_action\impor_action.exe examples` |
| [`apps/impor_sqlite_report`](apps/impor_sqlite_report/README.md) | 10 万行 CSV → SQLite → HTML 报表；峰值内存与耗时由 `python tools/verify_release.py --check app_sqlite` 现场测量并断言 | `apps\impor_sqlite_report\report.exe` |

三个项目都带自己的 README 与复现命令；`python tools/verify_release.py --check app_ssg / app_action / app_sqlite`
会分别重新构建并断言结果，这三条也已写进 `docs/claims.yaml`。

> 一体积口径：**语言产物**是 `impor bench-spec` 现场构建并打印的单文件 exe 体积；**便携构建包** `ImporPortable.exe` 自带 Python 运行时和内置 tcc，用于在没装 Python / 编译器的 Windows 上 `run` 与 `build`。两者不是同一个东西，README 不写死体积数字，构建后以工具输出为准。

> **C ABI 最小验收**：`impor build --lib examples/c_abi/lib.impor -o lib.dll` 产出 `lib.dll + lib.h + lib.def`；
> 用 C demo 链接调用，输出 `add(2, 3) = 5`。字符串返回值用 `impor_free_str()` 释放。完整可复现步骤见 `examples/c_abi/README.md`。
> 需要 64 位 DLL 时改用 `impor build --lib --c-source ... -o out/`，再用 `build_msvc.bat` 或 `build_gcc.bat` 编译（内置 tcc 只产 32 位）。
> `lib.c` 是纯库源码，不含 `main`；`impor run`（64 位 Python）加载 32 位库会抛 `FFIArchError`，不再伪装成签名不匹配。

## CI 证据（仓库当前为 private）

本仓库目前是 **private**，因此匿名 `api.github.com` 与 shields 徽章会返回 404，外部无法直接点开验证。下面记录的是仓库主账号在 Actions 中看到的运行结果，截图已入档：

| 工作流 | 运行 | 结果 |
|---|---|---|
| `ci.yml` | [#15](https://github.com/Everett131113/impor-lang/actions/runs/44418086033) | 4 个 job 成功（Windows py3.11 / py3.12 full，Ubuntu py3.11 / py3.12 smoke） |
| `c-abi-x64.yml` | [#15](https://github.com/Everett131113/impor-lang/actions/runs/44418085903) | MSVC x64 / GCC x64 / TCC win32 / GCC linux-arm64 四格成功 |
| 上一次基线 | [#14](https://github.com/Everett131113/impor-lang/actions/runs/44343129880) / [C ABI #14](https://github.com/Everett131113/impor-lang/actions/runs/44343130120) | 均成功（提交 `aa78f10`） |

- CI #15：`docs/ci-2026-11-19-ci15.png`
- CI #14：`docs/ci-2026-11-19-ci14.png`
- C ABI 四格：`docs/ci-2026-11-19-cabi14.png`

> macOS arm64 / x64 两格在 `c-abi-macos.yml` 中手动触发，当前账号 macOS runner 额度不可用，未计入上述 push 门禁。仓库转为 public 后，徽章和运行页才会成为外部可核验证据。

## 规格（由工具现场打印，不写死）

| 指标 | 复现方式 | 说明 |
|---|---|---|
| 单文件体积 | `python Impor.py bench-spec examples/hello.impor --runs 30 --json` | 输出中包含当前构建的体积 |
| 冷启动 | 同上 | 输出中包含 30 次均值 |
| 运行时依赖 | 同上 | 不依赖 Python、VC 运行库或第三方 DLL |
| 可被 C 调用 | `impor build --lib examples/c_abi/lib.impor -o lib.dll` | 产出 `.dll + .h + .def`；x64 用 `--c-source` 交给 `cl` / `gcc -m64` |

README 不抄这些数字：它们随构建变化，抄进来就会过期。

## 多语言对照（历史基线，3.11 实测）

下表保留 3.11 的一次性对照数据，作为历史记录，不作为 4.0.0 的性能承诺。要得到当前机器与当前构建的数字，运行 `python tools/compare.py --runs 30`，脚本会写 `compare.local.json` / `docs/compare.local.md`。

| 语言 | 产物 | 计数口径 | 测法 | 总耗时 | 纯启动 | 峰值内存 | 依赖 | 结果 |
|---|---|---|---:|---:|---:|---|---|
| **Impor 3.11** | **120.5 KB** · case_impor.exe | **可执行产物** | compiled | **21.44 ms** | **22.41 ms** | **6.0 MB** | **无** | OK |
| TCC-C | 2.5 KB · case_tcc.exe | 可执行产物 | compiled | 21.76 ms | 23.17 ms | 6.9 MB | 无 | OK |
| Lua 5.4 | 477.5 KB · lua.exe + lua54.dll | 含运行时 | script+runtime | 21.27 ms | 21.06 ms | 5.3 MB | lua.exe / lua54.dll | OK |
| Python 3.11 | 7.8 MB · PyInstaller onefile | 含运行时（打包） | onefile | 1207.48 ms | 1232.31 ms | 9.7 MB | 无（打包运行时） | OK |
| Go 1.23 | 1.9 MB · case_go.exe | 可执行产物 | compiled | 24.57 ms | 23.20 ms | 6.9 MB | 无 | OK |
| Node 22 | 76.8 MB · node.exe | 含运行时 | script+runtime | 120.02 ms | 114.22 ms | 29.4 MB | node.exe | OK |

**结论只写数据能证明的那一句**：在脚本／胶水语言这一组（Lua / Python / Node）里，Impor 是唯一同时满足「百 KB 级体积 + 零运行时依赖 + 可被 C 宿主直接加载」的；TCC-C 体积更小，但它是 C 本身，不是胶水层；Go 零依赖但 1.9 MB，且默认不导出 C ABI。单看启动，TCC-C / Lua / Go 与 Impor 同处约 21–25 ms 的进程地板，Impor 不宣称更快。TCC-C 的 2.5 KB 来自 tcc 自带微型 CRT，MSVC 编译同源通常 100 KB+。

**体积随编译器口径变，别把 120.5 KB 当成当前数字**：上表那行是 3.11 + 内置 tcc 的历史记录。4.3.2 实测（同一份源码、`--no-cache`）：`--cc tcc` 约 **126.5 KB**，默认 `--cc auto`（gcc -O2 静态链接 Val 运行库）约 **248–306 KB**（无模块的 hello 级 248 KB，带 3 个 std 模块的 `std_demo` 305.5 KB）。`docs/claims.yaml` 的体积承诺是 **≤ 320 KB**（patch8 起按 gcc + 静态 `strtod` 的实际成本重新定的），两条都在这条线内；要极小产物就显式 `--cc tcc`。

表里「总耗时」= 启动 + 读 JSON + 求和 + 退出；「纯启动」= 同一产物走空路径，只测进程地板。上表的 case 示例与 `bench-spec` 的 hello 示例不是同一个程序，两组数字不应逐位对比；4.0.0 的当前数字请现场重跑 `tools/compare.py`。

### 算力口径：C 后端不比解释器快（3.13 实测）

上表只测「进程地板」，不能用来推断计算性能。补一组递归 `fib(28)` 的实测，同一台 Windows x64 机器、各跑 5 次，去掉首次冷启动后取稳定值：

| 路径 | 5 次耗时（ms） | 说明 |
|---|---:|---|
| 解释器 `impor run` | 301 / 301 / 330 / 330 / 331 | 含 Python 进程启动；树遍历 + Python 大整数 |
| C 后端 `fib.exe`（内置 tcc） | 712（冷） / 479 / 477 / 475 / 473 | 原生 exe，值模型是 `Val*` 装箱 + GC |
| C 后端 `fib.exe`（gcc -O2，patch5 起的默认） | **82.9**（5 次取最优） | 同一份生成的 C，换编译器后装箱后端不再拖后腿 |
| 数值快路径 `fib.exe`（gcc -O2） | **21.5**（`fib(25)`，含进程启动） | `long long` 不装箱，溢出报错退出 |

**结论（patch5 修订）**：早前“C 后端比解释器慢 40%–55%”是在**内置 tcc** 下的结果——tcc 不会内联、不做循环优化，装箱代码被它拖成了 8 倍慢。`impor build` 默认换成 **gcc -O2** 后，完整 `Val` 后端 `fib(28)` 从 473–712 ms 降到 **82.9 ms**（比解释器快 3.6×），百万循环 100 万次累加 **20.0 ms**（`--no-fast`，与快路径 20.7 ms 同量级）；默认快路径 `fib(25)` **21.5 ms**、百万循环 **20.7 ms**，手写 C 基准是 19.6 / 19.2 ms。代价：gcc 产物 127 KB 起（静态链接运行时），要极小产物用 `--cc tcc`。详见 `docs/性能报告-v6.4.md`。

复现：`python Impor.py run _p0/fib.impor` 与 `python Impor.py build _p0/fib.impor -o _p0/fib.exe --no-cache`（脚本为 `fib(28)` 单行打印）。

被 C 加载所需代码约 10 行：`LoadLibrary` + `GetProcAddress("impor_add")` + 调用 + `FreeLibrary`，见 `examples/c_abi/README.md`。完整表与逐条命令在 `docs/compare.md`，原始数据在 `compare.json`。

复现（3 分钟内，缺哪个运行时就把哪个参数指过去；首次运行先 `pip install psutil`）：

```powershell
python tools/compare.py --runs 30            # 只写 compare.local.json / docs/compare.local.md
python tools/compare.py --runs 30 --publish  # 正式更新 compare.json / docs/compare.md
```

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
| 流程 | `if / elif / else`、`while`、`for in`（列表/range/字符串）、`break/continue` |
| 函数 | `def` 声明、参数、返回值、闭包、高阶函数、`nonlocal` / `global` 作用域 |
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

见 [docs/语言规范.md](docs/语言规范.md)（当前 4.0.0 语法规范，与实现一致）。

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

impor build 走「Impor → C 源码 → 本机 C 编译器原生编译」链路，产出**真正的独立可执行文件**：
运行不需要 Python、不需要 Impor 本体。

`powershell
impor build examples\fib.impor          # 生成 fib.exe
impor build demo.impor -o game.exe       # 指定输出名
impor build demo.impor --cc tcc          # 强制内置 tcc（产物 6.6 KB 级）
impor build demo.impor -O3               # 指定优化级别
./game.exe                               # 直接运行
`

- 编译器（patch5 起）：默认 `--cc auto` —— 找得到 **x86_64 gcc** 就用 `gcc -O2`（本机 fib(28) 完整后端 82.9 ms、百万循环 20.0 ms），找不到退回随包 `impor/tcc/win32/tcc.exe`；`--cc tcc|gcc|clang|<路径>` 可强制指定，`IMPOR_BUILD_CC` 可全局改默认。gcc 用 `-std=gnu99`（TDM 4.8.1 默认 gnu89）并静态链接运行时，因此产物 127 KB 起。
- **原生数值快路径（默认开启）**：纯整数 / 整数循环 / 浮点脚本编成不装箱的 C 代码（`long long` / `double`），`for 变量 in range(N|A,B|A,B,STEP)` 全部覆盖，`print` 支持多参数混排。`fib(25)` **21.5 ms**、百万循环 **20.7 ms**、浮点百万循环 **22.9 ms**，与手写 C 基准（19.6 / 19.2 ms）同量级。要任意精度 `--no-fast`（同样走 gcc，`fib(28)` 82.9 ms）。
- **`impor run --native` / `impor.bat` 原生启动器（patch6）**：开发期命令也能吃到产物速度——`impor run --native x.impor` 先编再跑（`~/.impor/run-cache` 缓存，改源码自动重编，子集外回落解释器）；`impor.bat` 发现同目录 `impor-fast.exe` 时走它，命中缓存即直接执行产物。实测 `impor <脚本>` 38–40 ms（解释器原状 176.8 / 2061.0 ms）。详见 [`docs/性能报告-v6.5.md`](docs/性能报告-v6.5.md)。
- **产物自带 I++ 图标**：tcc 没有 windres，编译成功后由 `impor/peicon.py` 调用 Windows `UpdateResource` 把 `impor.ico` 的 7 档尺寸写入 PE 的 `RT_ICON` / `RT_GROUP_ICON`；资源管理器、任务栏和文件属性里看到的就是 I++ 图标。`imporc.exe` 同样带图标。
- **双后端一致**：C 后端与 AST 解释器对同一脚本输出逐字符一致（单测强校验）。
- **第一版子集**（过程式）：变量、算术/比较/逻辑、字符串拼接与插值、if/elif/else、while、for-range、顶层函数、print/input/inputstr、常用内建（to_num/to_str/len/type/trim/upper/lower/strpad/strcut/abs/sign/min/max/clamp/randi/seed/randstr/sleep/now/clock）。
- 类/闭包/异常/模块/数组/字典等特性在 C 后端会得到**友好报错**并提示改用 Python 后端（impor run / impor compile --exe）。
- AST 树遍历解释器完整保留，用于教学与全特性运行；--backend 双后端架构不删旧能力。

## WASM 后端：不装工具链也能出产物（v4.9.0）

第二后端，和 C 后端同一个语义模型、两份独立实现：`impor wasm` 直接把 `.impor` 编成**标准 WASM 二进制**（手写字节码，不依赖 LLVM / wat2wasm / emscripten / C 工具链），产物丢进浏览器或 Node 就能跑。

```powershell
impor wasm examples\fib.impor -o fib.wasm          # 产出 fib.wasm
impor wasm examples\fib.impor --html fib.html       # 顺便出一个内嵌 wasm 的演示页
node tools\wasm_run.js fib.wasm 75025               # 带期望输出比对，不符 exit 2
python tools\wasm_batch.py                          # 12 个用例一次跑完（解释器产期望 → wasm 比对）
```

- 覆盖：int64 算术/比较（溢出检测与 E1012 同口径）、**f64 四则与比较**、**字符串拼接/重复/相等/len/`+=`**、`if`/`while`/`for`、**`break`/`continue`（可嵌套）**、**`and`/`or` 真短路（两层都短）**、函数与递归、print。
- 值域约定：所有值都放 i64，解释方式由编译期推断决定（int 原样 / float 位模式 / str `(ptr<<32)|len`）；同名变量必须类型稳定、函数在所有调用点必须单态，否则**编译期明确拒绝**而不是猜错。
- 仍明确拒绝（带行列号，退出码 3）：列表/字典、类、闭包、泛型、异常、浮点 `%`、字符串 `<`/`>`、`int()`/`str()` 内建。
- 实测：12 个用例（4 个数值基准 + 字符串 + break/continue ×2 + 短路 + f64 + str2 + 能力探针 02/03）全部与解释器逐行一致，产物 800–1051 字节；`and`/`or` 用例里 `1 == 2 and (10 / 0) == 0` 不 trap，证明右边真的没被求值。
- 浏览器侧：`impor wasm ... --html demo.html` 产出的页面在真实 Chrome 里渲染出的输出与 Node 完全一致（4.9.0 起页面内补了 `print_f64` 导入，浮点/布尔打印口径与解释器一致：`3.0`、`True`）。
- 能力边界与设计见 [`docs/第二个后端-WASM-4.9.0.md`](docs/第二个后端-WASM-4.9.0.md)。

## NVM 后端：自有字节码 + 自带虚拟机（v5.3.0）

第三个执行后端，也是**第一个完全自有、不借外部编译器的后端**：C 后端要 gcc/tcc、WASM 要宿主，NVM 把 AST 编成**栈式字节码**，再由随包自带的虚拟机（NVM，Native bytecode Virtual Machine）直接执行，全程不需要 gcc/tcc，也不需要生成机器码和做寄存器分配。这是 CPython、Lua、JVM 等脚本语言自有运行时的主流形态，可移植、可调试、字节码可序列化落盘。

```powershell
impor vm run examples\fib.impor            # 编译并立即执行
impor vm compile examples\fib.impor -o fib.ibc   # 只编译，产出字节码文件
impor vm exec fib.ibc                      # 加载 .ibc 直接执行
impor vm dump examples\fib.impor           # 反汇编，打印字节码助记符
impor vm run big.impor --big               # 任意精度（默认 int64，溢出报 E1012）
```

- **字节码文件 `.ibc`**：JSON 容器（magic `IMPOR-IBC`），主代码与函数 CodeObject 内联，常量池、名字池一起序列化；`compile` 产出的 `.ibc` 可拷贝到任意带 Impor 的机器用 `exec` 加载，输出与 `run` 完全一致（单测强校验往返）。
- **指令集（30 个操作码）**：局部变量走槽位（FAST）、模块变量走 globals、闭包变量走 cell（DEREF）；覆盖算术/比较/一元、短路逻辑（`and`/`or` 真跳转，不提前求值右操作数）、跳转与迭代、调用与返回、函数/闭包、list/dict/切片、属性与下标。
- **整数语义对齐**：默认与 `impor run` 同一 int64 口径，整数 `+`/`-`/`*`（含一元负号）结果越界即抛 `OverflowError [E1012]`，不静默回绕；`--big` 切换为任意精度（Python 整数），与解释器 `--big` 一致。
- **闭包**：Cell 盒子 + 两阶段作用域分析——先收集各作用域局部名与候选自由变量，再后处理把自由变量解析到外层函数局部并建 cell；`nonlocal`/`global` 显式声明的名字不进入本作用域局部，与解释器闭包语义一致。
- **支持**：变量与全部过程式控制流（if/elif/else、while、for、break/continue）、函数与递归、闭包与 nonlocal、lambda、三元、字符串插值、list/dict 及其方法、常用内建。
- **明确拒绝（带行号，不静默改语义）**：`class`、`import`、`try/catch`、`raise/exception`、`match`、`defer`、`yield`、`await`、`have` 类型判定、`new` 实例化，以及全部并发内建（`thread_spawn`/`mutex_*`/`atomic_*` 等，编译期拒绝，不写 crash.log）；报错统一提示改用 `impor run` 走全特性解释器。
- **验证**：`tests/test_vm.py` 12 个用例（核心语义、`.ibc` 往返、不支持特性报错、递归深度保护、并发内建编译期拒绝、与解释器的进程级逐字节对拍）全部通过；能力矩阵 11 个探针中 NVM 8 项支持、3 项明确拒绝，**无任何「算错」格**。35 个过程式示例 31 个直接通过，其余 4 个均为 class/import 边界并给出友好报错。

- **v5.4.0 执行提速**：热点 opcode 分支前置、内联 int64 溢出检查、单参数调用快速路径，`fib(28)` 进程内约 4.90s → 约 3.58s（耗时降低约 27%），语义与 `.ibc` 格式不变；能力补齐顺序见 `docs/rfc/0007-nvm-coverage-roadmap.md`。

## OPTC 后端：教科书优化编译器（v5.5.0，寄存器式）

第四个执行后端，在三地址 IR 之上补齐教科书（龙书 / Engineering a Compiler）优化编译器的标准
后端流水线，全程不依赖外部编译器或汇编器，输出寄存器式目标码并由寄存器机直接执行。

```powershell
impor opt run examples\fib.impor           # 编译并立即执行
impor opt dump examples\fib.impor          # 反汇编寄存器式目标码（含块/区间/溢出统计）
impor opt dump examples\fib.impor -o t.asm # 反汇编落盘
```

- **流水线五阶段**：① 基本块划分 + 控制流图（CFG，识别 leader 与块间后继）；② 活跃变量
  数据流分析（后向 gen/kill 不动点，求 live-in/live-out）；③ 线性扫描寄存器分配（6 个物理
  寄存器 R0..R5，参数从 R0 起依次传入）；④ 寄存器式目标码（操作数为物理寄存器/立即数/栈槽）；
  ⑤ 寄存器机执行，直接操作寄存器数组。
- **溢出（spill）**：寄存器不足时，把活跃区间结束最晚的变量溢出到栈槽，栈槽编号唯一，定义
  后写栈、使用前重载；压力程序会真实触发并验证结果正确。
- **支持**：标量过程式核心——int/float/bool/str/none、变量、算术/比较/逻辑、if/else、while、
  函数与递归、print。
- **明确拒绝（BackendError，不写 crash.log）**：list/dict、索引/切片、属性、class、try/catch、
  import、闭包/lambda、异常、for-in、global 作用域、内建调用（len/int/str 等）、超过 6 个参数。
- **整数语义**：仍为 int64，`+`/`-`/`*`（含一元负号）越界报 `OverflowError [E1012]`，除零报
  运行时错误，与解释器/NVM 完全一致。
- **验证**：`tests/test_optc.py` 21 个用例（CFG 可达性、数据流方程、寄存器不超量与区间不重叠、
  溢出正确性、int64/除零、不支持特性、进程级对拍）全部通过；`impor opt run` 与 `impor run`
  stdout 逐字节一致，`tests/test_vm.py` 12 用例仍全绿；能力矩阵新增 OPTC 列且无「错误」格。
  设计与边界见 `docs/rfc/0008-optimizing-compiler-backend.md`。

## Native64 后端：纯自足原生 x64 AOT（v5.6.0，自写机器码 + PE32+）

第五个执行后端，也是第一个**直接产出可双击运行原生 exe** 的真 AOT 后端：在三地址 IR 之上
自己把指令编码成 x86-64 机器码字节、自己生成 Windows PE32+ 可执行文件，全程不调用
nasm / gcc / ld 等任何外部工具链。对标项目 XLang 的 x64 后端只生成汇编文本再调外部
nasm/ld，自身不产生机器码字节与 PE；Impor 做到纯自足机器码 + PE 是真正的超越。

```powershell
impor native build examples\fib.impor -o fib.exe  # 编译为原生 exe（默认同名 .exe）
impor native run examples\fib.impor               # 编译到临时 exe 并立即执行
```

- **三个自足模块**：① `x64asm.py` 指令编码器（REX/ModR/M/SIB/立即数/位移编码与标签回填）；
  ② `pe64.py` PE32+ 链接器（自构 DOS/PE/COFF/可选头、节区、导入表与 IAT，回填 RIP 引用）；
  ③ `native64.py` 标量类型推断、复用 OPTC 的 CFG/活跃/线性扫描分配、机器码发射与运行时例程。
- **寄存器与栈帧**：R0..R5 映射到非易失真实寄存器（RBX/R12/R13/R14/R15/RDI），prologue
  统一保存、跨 CALL 安全；运行时例程 `__rt_write` / `__rt_strlen` / `__rt_int_to_str`
  全局一份、只用易失寄存器；整数边界含 INT64_MIN/INT64_MAX 均正确。
- **支持**：标量过程式核心——int/bool/str/none、变量、算术/比较/逻辑、if/else、while、
  函数与递归（含 6 参数与深 spill）、print。
- **明确拒绝（BackendError，不写 crash.log）**：浮点（SSE/dtoa 列后续）、list/dict、
  索引/属性、class、try/catch、import、闭包/内建调用、超过 6 个参数。
- **验证**：`tests/test_native64.py` 41 个用例（编码逐字节、PE 结构、类型推断、exe 真实
  运行、与解释器逐字节对拍、拒绝路径）全部通过；能力矩阵新增 Native64 列，六后端无「错误」格。
  设计与边界见 `docs/rfc/0009-native-x64-aot-backend.md`。

## 两道门禁：性能与语义（v4.8.0 起）

以前「快」「支持」靠文档说，现在靠脚本验：

```powershell
python tools\bench_gate.py --check        # 性能：四个基准 × 两口径（解释器/原生），超基线 30% 或输出不符即 exit 1
python tools\capability_gate.py --check   # 语义：十个探针 × 三后端，任一格与 docs/CAPABILITIES.json 不一致即 exit 1
```

- `bench_gate` 的基线在 `bench/baseline.json`（interp fib25 2.3905 s / native fib25 0.0164 s 等），`--record` 重采、`--limit` 调阈值；基线里同时记编译器身份（`cc`），**没装 x86_64 gcc 的机器会退回 32 位 tcc，native 口径自动判 `SKIP(cc)` 而不是误报退化**。
- `capability_gate` 把「支持 / 明确拒绝 / **算错了**」区分开——第三种必须为 0 格才允许发版，矩阵自动生成在 `docs/CAPABILITIES.md`。
- 首次编译的体感断崖也一并拆掉：`impor run` 首次从 1.46 s 降到 1.056 s（先出 `-O1` 快速档，后台 `native_upgrade.py` 再编满档 LTO，第二次 0.511 s）。

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
impor ir 程序.impor           # 4.4：打印三地址 IR（AST 降下来的单一份中间表示）
impor ir 程序.impor --run     # 用 IR 参考求值器执行（与解释器对拍用）；--stats 看指令计数
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

### v5.6.0（纯自足原生 x64 AOT，自写机器码 + PE32+，零外部工具链）
- **新增第五执行后端 Native64**：三地址 IR → 自写 x86-64 机器码字节 → 自写 PE32+ 可执行文件，产出可双击 exe，不调 nasm/gcc/ld；XLang 同类后端只生成汇编文本再调外部 nasm/ld，本后端为真正超越。
- **三个自足模块**：`x64asm.py`（指令编码器）、`pe64.py`（PE32+ 链接器）、`native64.py`（类型推断+寄存器分配复用+机器码发射+运行时例程）；CLI 新增 `impor native run|build`。
- **能力与边界**：支持标量过程式核心（int/bool/str/none、控制流、递归、6 参数、深 spill，含 INT64_MIN）；浮点/容器/内建等编译期 BackendError 干净拒绝、不写 crash.log。
- **验证**：`tests/test_native64.py` 41 用例全绿，与解释器逐字节对拍；能力矩阵新增 Native64 列、六后端零错误格。
- 语言表面零变化：无新增关键字、无语法糖、无同义写法。

### v5.4.0（NVM 执行提速 + 能力补齐路线图，无语法糖）
- **NVM 性能优化**：热点分支重排、内联 int64 溢出检查、单参数调用快速路径；`fib(28)` 进程内从约 4.90s 降到约 3.58s（耗时降低约 27%），CLI `vm run` 从约 5.04s 降到约 3.73s，`E1012` 等整数语义不变。
- **能力路线图**：新增 `docs/rfc/0007`，按 import → 异常 → 类 → 并发分阶段补齐，每阶段对拍验收、未完成继续明确拒绝。
- **工程卫生**：修复主开发工作树与发布物脱节，回灌后该工作树测试全绿。
- 语言表面零变化：关键字仍 55、标准库模块仍 37、错误码仍 30，**无新增语法糖、无同义写法、无新关键字**。

### v5.3.0（自有字节码后端 NVM，无语法糖）
- **新增自有后端**：`impor vm` 把 AST 编成栈式字节码，由自带虚拟机直接执行，**不依赖 gcc/tcc、不生成机器码**；与 C 后端、WASM 并列，是第一个完全自有的执行后端。
- **四个子命令**：`run`（编译即执行）、`compile`（产出 `.ibc` 字节码文件）、`exec`（加载 `.ibc`）、`dump`（反汇编）。
- **能力边界**：支持过程式核心、函数递归、闭包与 nonlocal、lambda、三元、字符串插值、list/dict；`class`/`import`/`try`/`raise`/`match`/`defer`/`yield`/`await`/`have` 等明确报错并提示改用 `impor run`。
- 语言基线不变：关键字仍 55，标准库模块仍 37，错误码仍 30，**无新增语法糖、无同义写法**；能力矩阵已纳入 NVM 列（11 探针 8 支持 / 3 明确拒绝，零错误格）。

### v5.2.0（科学计数法 + 17 个新内建，无语法糖）
- **科学计数法**：数字支持 `e/E` 指数与正负号，如 `1e3`、`2.5E-4`、`1_000e+2`；`1e` 不构成指数时保持旧行为，`1..5` 区间写法不变。
- **字符串 4 个**：`strsplit` `strcount` `strstart` `strend`
- **数组 4 个**：`arrinsert` `arrremove` `arrsort` `arrcount`（返回新数组，不改原数组）
- **数值 3 个**：`round`（五成双）`floor` `ceil`
- **逻辑/函数式 6 个**：`any` `all` `map` `filter` `zip` `enumerate`
- 内建总数 124 → 141；关键字仍 55，标准库模块仍 37，错误码仍 30。
- 新内建解释器与转译 Python 产物可用；C 后端暂不支持，会给出友好报错。

### v5.1.1（独立输出窗口改为运行时自动弹出）
- 每次点「运行」都会自动弹出独立输出窗口，无需再手动点「独立窗口」；窗口已存在时只提到最前，不重复创建。
- 新增自动弹出回归校验（关窗后运行 → 窗口自动出现且收到输出），回归合集 40/40 OK。
- 语言基线不变：55 关键字 / 124 内建 / 37 模块 / 30 错误码。

### v5.1.0（编辑器：独立输出窗口 + 系统控制台运行，像 Python 一样单独界面）
- **独立窗口**：输出面板头部新增「独立窗口」，把运行输出弹到单独的 Toplevel 窗口，历史内容与颜色完整复制，之后实时同步；「清空」两边联动。
- **控制台运行**：新增「控制台」按钮，在一个全新的系统控制台窗口里运行当前代码（独立黑窗口，结束后按任意键关闭），与 Python 双击/终端运行的体验一致。
- 语言本体零改动：55 关键字 / 124 内建 / 37 模块 / 30 错误码不变，无新增语法糖。
- 门禁：回归合集 40/40 OK（含编辑器无头套件），格式 241/241。

### v5.0.1（修复：两个张量相减时反向传播报错）
- **bug**：`_autograd.sub` 的反向闭包误写 `g.data`（梯度本身就是 NDArray，没有 `.data`），凡是计算图里出现两个张量相减（如均方误差 `sub(pred, y)`），`backward()` 就抛 `AttributeError: 'NDArray' object has no attribute 'data'`。
- **修复**：改为 `g.mul(-1.0)`；新增回归测试 `test_sub_gradient`（解析梯度校验），`test_imporai` 现 14/14 OK。
- 语言基线不变：55 关键字 / 124 内建 / 37 模块 / 30 错误码。

### v5.0.0（AI 数值栈：C 内核 ndarray + 自动求导 + 训练闭环，语法零改动）
- **C 数值内核 `impor/native/imporai.dll`**（gcc 编译，源码 `impor/native/imporai.c`）：连续 float64 多维数组、shape/strides、逐元素运算、reshape/转置、**矩阵乘**、偏置广播、行求和、随机数（含正态 randn）。
- **`imporai` 包**（`import imporai`）：NDArray 绑定、Tensor 自动求导（matmul/线性层/tanh/sigmoid/relu/log 的梯度，解析梯度与数值梯度一致）、SGD/Adam 优化器、模型二进制存取（`save_model`/`load_model`）。
- **端到端示例 `examples/ai_train.impor`**：两层 MLP 二维二分类，30 轮训练损失 0.184 → 0.0012，准确率 **40/40**，模型可存盘读回。
- **门禁**：新增 `tests/test_imporai.py` 13 项全 OK；conformance 2/2 OK（1162s）；`KEYWORDS` 仍 55、API 基线 55-124-37-30 不变——**没有新增语法或语法糖**，全部是标准库与运行时。
- **边界（如实列出）**：仅 CPU、float64；自动求导覆盖训练常用算子，不是完整算子库；词法暂不支持科学计数法字面量（示例里用小数写法）。

### v4.9.2（`have` 补上语法高亮：编辑器 / Qt / LSP / VS Code 四处）
- **问题**：`have` 是软关键字（不进 `KEYWORDS`），而所有着色器历史上只认 `KEYWORDS`，于是 `if a have int:` 里的 `have` 渲染成普通标识符色，看不出是语法。
- **修法**：`impor/lexer.py` 新增 `HIGHLIGHT_KEYWORDS = KEYWORDS | IMPLEMENTED_SOFT_KEYWORDS`（59 词，含 `have` / `static` / `readonly` / `this`）。四条着色路径统一改用它：Tk 编辑器（`editor_pane.highlight`，`kw` 色）、Qt 高亮（`kernel3`）、LSP 语义着色（`lsp.semanticTokens`）、补全列表。`KEYWORDS` 仍是 55，`var have = 1` 照旧可用。
- **VS Code**：`vscode-impor/syntaxes/impor.tmLanguage.json` 新增一条 `keyword.operator.type.impor`（匹配 `\b(have)\b`），单独配色，不与 `if`/`var` 同色。
- **回归**：`tests/test_v491.py` 从 8 项加到 **12 项**（新增「Tk 真实打 tag」「语法文件含 have」「LSP 用 HIGHLIGHT_KEYWORDS」），实测 12/12 OK；`test_v372` 4/4 OK；`check-fmt 240/240`；API 基线仍 **55-124-37-30**（没碰 `KEYWORDS`）。

### v4.9.1（WASM 补到 6/10：列表 / 字典 / 类 + 新语法 `have`）
- **WASM 补容器**：`list` 与 `dict` 都有字面量、`len()`、下标读取（`a[1]` / `d["k"]`）。值域沿用「所有值放 i64」的约定：容器句柄 `(ptr<<32)|元素个数`，`list` 每格 8 字节、`dict` 每条 16 字节（key 是已打包的字符串值 + value）。新增 helper `alloc`（bump 分配）/`list_get`/`dict_get`，索引 14/15/16；`len()` 仍不发调用，掩低 32 位即可。越界与负下标一律 trap（解释器那时抛 IndexError，两边都不会给错值）。
- **WASM 补类**：`class` / `def __init__` / `set self.x` / 方法调用 / `new P(...)`。对象是裸内存块，字段顺序按 `__init__` 里 `set self.x` 的书写顺序固定，每个字段 8 字节；方法在索引空间里排「顶层函数之后、main 之前」，`self` 是方法局部 0。继承、接口、类属性一律明确拒绝（WASM 里没有运行期字段表，猜布局就会算错）。
- **新语法 `have`**：`x have int` —— 类型判定，右侧接受 `int / float / str / bool / list / dict / 任意类名 / object`。语法挂在比较层（`a have int == True` 读作 `(a have int) == True`），所以能进 `if`/`while` 条件，也能链式 `a have int and b have str`。**软关键字**：不进 `KEYWORDS`，标识符 `have` 照旧可用。
  - 解释器：运行期真判，口径与 `typename()` 一致（`bool` 独立，`1 have float` 为假、`True have int` 为假）。
  - C 后端：新增运行库 `rt_have(Val, const char *)`，内部复用 `rt_type()`——**真运行期判定**，与解释器逐行一致。
  - WASM：槽里不带运行期 tag，只能按编译期类型折叠（`var a = 3` 后 `a have int` 恒真）。类型是精确推断的（int 不冒充 float、str 不冒充 int），但对「同一变量先后是不同类型」的写法，WASM 会先报类型不稳定 —— 这条差异写在 `docs/第二个后端-WASM-4.9.1.md`。
- **能力矩阵**：WASM 3/10 → **6/10**（`04_list` / `05_dict` / `06_class` 转「支持」），新增第 11 个探针 `11_have`；回归批跑 **16/16 MATCH**。仍**零「错误」格**。
- **未做（如实列出）**：WASM 的闭包 / 泛型 / 异常 / `int()` `str()` 转换（仍是明确拒绝）、WASM 容器回收（bump 只增不减，长循环里反复建容器会在 1 MiB 堆上耗尽）、v4.9.0 未做的两项（NaN-boxing 本体、Linux/macOS 运行证据）保持不变。

### v4.9.0（WASM 补 f64 / 字符串 / break-continue / 真短路 + 浏览器演示页 + 安装目录改名）
- **WASM 补 f64**：`f64.add/sub/mul/div/neg`（0xA0–0xA3 / 0x9A）、六种比较（0x61–0x66）、`int → float` 隐式提升；值域约定确立为「所有值都放 i64，解释方式由编译期推断决定」（int 原样 / float 位模式 / str `(ptr<<32)|len`）。新增 `env.print_f64` 导入，打印口径与解释器对齐（`3.0` 保留 `.0`、`True`/`False` 不再是 `1`/`0`）。
- **WASM 补字符串运算**：`+`（`str_concat`，`memory.copy` + bump 分配）、`*`（`str_repeat`）、`==`/`!=`（`str_eq` 逐字节）、`len()`（掩低 32 位，不发调用）、`+=`。全局 `__heap` 固定占索引 0（初值 131072），内存 4 页；三个 helper 占索引 11/12/13。
- **WASM 补 break / continue / 真短路**：`ctrl` 块嵌套栈按「相对深度」算 `br` 立即数；`while` = `block(brk)+loop(cont)`，`for` = `block(brk)+loop(blk)+block(cont)`（continue 落在 `i += step` 之前）。`and`/`or` 在**表达式层与条件层**都短路，`flow2` 用 `1 == 2 and (10 / 0) == 0` 断言右边真的没被求值（不 trap）。
- **浏览器演示页修好**：`--html` 模板补 `print_f64` 导入、副标题版本号改为从 `__version__` 取；在真实 Chrome 里打开 `wasm_f64` 演示页，输出与 Node 逐行一致。
- **能力矩阵重录**：WASM 从 1/10 升到 **3/10**（`01_int` / `02_float` / `03_str` 支持，其余七项仍为明确拒绝），仍**零「错误」格**；`docs/CAPABILITIES.md` / `.json` 已按实测刷新。
- **安装目录改名**：`Impor 4.0.0` → `Impor 4.9.0`，同步桌面 `Impor 编辑器.lnk` 的目标/参数/工作目录（`impor.bat` 用 `%~dp0` 相对定位，无需改）。
- **未做（如实列出）**：8 字节 NaN-boxing 本体（本轮只做了整树备份（`D:\ImporWork\backup_490_pre_nanbox`）与设计复核，Val 仍是 16 字节 tagged union）、Linux/macOS 运行证据（本机无 WSL 发行版、无 docker/qemu/wine/zig，物理不可得；`.github/workflows/ci.yml` 里的 ubuntu/macos C 后端步骤是可执行证据链，但没有在本机跑出运行日志）。

### v4.8.0（性能进 CI + 首次编译两级 + 第二个后端 WASM + 能力矩阵门禁）
- **性能从「宣称」变成门禁**：`tools/bench_gate.py` 四个基准用例、两个口径（`interp:` 树遍历解释器 / `native:` C 后端 exe），基线入库 `bench/baseline.json`（interp fib25 2.3905 s、native fib25 0.0164 s、native sum2m 0.0166 s…），默认 30% 阈值、`--runs` 可调轮数。**输出不符 / 进程崩 / 用例缺失一律 exit 1**——这条比时间更硬。
- **首次编译断崖拆成两级**：`impor run` 首次 1.46 s → **1.056 s**（先产 `-O1` 快速档），`.c` 留档交 `impor/native_upgrade.py` 后台编 `-O2/-O3 + LTO` 满档，`os.replace` 原子替换、占用时 6 次重试、失败静默；第二次运行 **0.511 s**。`tcc` 与显式 `-O0/-O1` 仍单级，不假装有 LTO。
- **第二后端 WASM**：`impor wasm FILE -o OUT.wasm [--html PAGE.html]`，手写 WASM 二进制（LEB128/section/vec/name 自建，5 个带溢出检测的 i64 helper），**无 LLVM / 无 wat2wasm / 无 C 工具链**。四个数值基准 + 字符串用例全部 MATCH，产物 482–608 字节；子集外（浮点/字符串运算/容器/类/闭包/泛型/异常/短路）编译期拒绝并给行列号，退出码 3。
- **能力矩阵进门禁**：`tools/capability_gate.py` + `bench/caps/` 十探针 × 三后端，区分「支持 / 明确拒绝 / **算错了**」。当前结果：解释器 10/10、C 后端 10/10、WASM 1/10（int64 快路径），**零「错误」格**；矩阵自动生成 `docs/CAPABILITIES.md` + `.json`，`--check` 任一格变化即 exit 1。
- **本轮踩到并修掉的三个 WASM 真 bug**（都有实证，不是猜的）：`i64.rem_s` 误写成 `0x80`（`div_u`）导致 `mod1m` 输出 71428071429 而非 2999997——`sum(floor(i/7))` 恰好等于这个数，反推才定位到操作码；字符串偏移从 0 起算而非 data 段基址 1024，导致 `print("a =", a)` 打成「 3」；泛型函数未拒绝而被当单签名 i64 编译，`max2("a","b")` 触发 `call[0] expected i64, found i32` 的编译期类型错误——现在明确拒绝并指路 C 后端。
- **文档**：`docs/第二个后端-WASM-4.8.0.md`、`docs/Impor 4.8.0 发布说明.md`、`docs/CAPABILITIES.md`。
- **未做（如实列出）**：WASM 后端的 f64 与字符串运算（现为明确拒绝）、WASM 的 `break`/`continue`/短路、8 字节 NaN-boxing 本体、Linux/macOS 运行证据、GUI 鼠标键盘级自动化。

### v4.7.2（Val 16 字节 tagged union + 语言层内联 + 区间证明收益实测）
- **Val 从 56 字节收紧到 16 字节**：6 个指针槽合并成匿名 union（一个值只有一个类型），`v.t`/`v.i`/`v.s` 语法不变、895 个字段访问点零改动。代价是踩了一个坑：旧代码「先把所有槽清零再赋目标槽」的写法在 union 下会把刚写的指针清掉（字符串全打印 `(null)`），23 处构造点已统一改成「只写 tag + 目标 payload」。加了两道门禁：编译期 `val_size_must_be_16` 静态断言、源码期 `tools/check_val_layout.py`。
- **实测（同机同脚本同 gcc -O3，只差 Val 布局）**：100 万元素数组峰值 RSS **52.54 MB → 18.44 MB（-64.9%，2.85×）**；`sum1e8` 8 轮最小值 0.0457 → 0.0452 s（-1.1%，在噪声内 —— 那段循环走原生 `long long`，Val 不进内循环，所以时间不变才是对的）。
- **语言层一般内联**（`impor/inline.py`）：单 `return` 表达式小函数在调用点展开，判据白名单化（体积≤40 节点、参数≤4、无 Call/副作用/控制流、不自递归；形参用多次时实参必须是纯的）。**报错位置判据定死**：内联子树的行列一律改写为调用点 —— 实测 `mul2(big)` 溢出报第 8 行（用户写的调用行），关闭内联才是第 4 行（函数体）+ 栈里补 `mul2（第8行）`。开关 `IMPOR_NO_INLINE` / `_MAX_NODES`（40）/ `_DEPTH`（2）/ `_TRACE`，摘要新增「内联：N 处」。
- **区间证明收益补测**：`IMPOR_NO_RANGEPROOF` 对照 **0.0648 → 0.0410 s（1.58×，-36.7%）**，纯 C `-O3` 为 0.0446 s。
- **跨平台交叉编译（Zig 0.13.0）**：`tools/cross_build.py` 把 Impor 生成的 C 直接交给 `zig cc`，四个目标全部链接成功 —— Linux x86-64 静态 ELF（426 KB，无 `PT_INTERP`）、Linux aarch64 静态 ELF（1.2 MB）、macOS x64 / arm64 Mach-O。**编译链接已证明，运行未验证**（本机 Windows 内核，无 WSL/docker/qemu-user）。详见 `docs/交叉编译-4.7.2.md`。
- **修掉跨盘构建崩溃**：源文件在 `C:` 临时目录、std 模块在 `D:` 安装目录时，模块指纹里的 `relpath` 抛 `ValueError`，`impor build` 直接失败；改为捕获后回退绝对路径，`verify_release` 的 `stdlib_c` 由 FAIL 转 OK。
- **8 字节 NaN-boxing 仍未做**，但障碍已量化（`docs/nanboxing-design.md`）：895 个字段访问点（820 读 / 75 写，`tools/count_val_access.py` 可复现），且 int64 全范围与 64 位载荷里的 tag 位直接冲突，需要先做语言层语义决策。
- **未做（如实列出）**：8 字节 NaN-boxing 本体、Linux/macOS 上的**运行**证据（只有编译链接）、方法调用与多语句函数的内联、`pkg install/lock` 主流程切新求解器。

### v4.7.1（自举 fixpoint 成立 + 语言层 TCO / 常量免检 + 包索引求解）
- **自举 fixpoint 成立**：`tools/selfhost_fixpoint.py` 跑通 A → B → C → D，**C.c == D.c 逐字节一致**（340441 字节，两侧 SHA256 相同，`a.c` 也是同一份），二进制同参重链后只差 PE 时间戳与校验和。卡住的四个真缺陷已修：引导器必须用当前前端重建（仓库里的旧 `imporc_py.exe` 内嵌旧运行时，`CreateProcessW` 连 `where.exe` 都启不动）、种子编自身 1MB 栈爆 `0xC00000FD`（补到 64MB）、`.bat` 不是 PE 所以新增 `bootstrap/cc64stub.c` 做成同名 PE 桩、二进制比较必须同参同源文件名（第一版白报 757272 字节差异）。
- **`system()` 修成 POSIX 语义**：Windows 侧统一走 `cmd.exe /c`。实测 `.bat` 退出码透传（rc=7）、`.cmd`（rc=9）、`echo hi > f`（rc=0 且文件生成）；旧实现这三条全是 `-1`。
- **语言层自尾递归 → while**：`impor/monomorph.py` 对 `return f(...)` 生成「算实参到临时量 → 写回形参 → `continue`」，外层包 `while(1)`；`IMPOR_NO_TAIL=1` 可关。`-O0` 对照：TCO 版 0.24s 正常，无 TCO 版 `0xC00000FD`、8.31s 后崩溃。常量特化遇到自递归会死循环，已禁止两者叠加。
- **`-O3` + `-flto`**：非 `--lib` 且 gcc 时追加 `-flto`，链接失败自动去掉重试并把 `lto=fallback` 记进摘要。
- **区间证明免检 + 常量特化**：`impor/rangeproof.py` 判算术点是否稳在 int64 内，稳则免 `rt_ck_*`；全字面量调用点特化成 `gp_f_i64_k<常量>`。实测生成体 `for (v_i = 0; v_i < 100000000LL; v_i += 1) { v_total = (v_total + v_i); }` 无检查、输出正确。**性能数字未在空闲机器重测，本版不引用**。
- **包索引 + 版本约束求解**：新增 `impor/versionsolve.py`（`^ ~ >= <= != ==`、多段合取、DFS+回溯+MRV、超 200 节点明确报错）与 `tools/pkg_index.py`（index/verify/resolve/selftest）。`pkgmgr` 的冲突判定改成真区间交集，`>=1.0` 与 `>=1.0,<2.0` 不再误报。selftest 端到端 PASS。
- **绑定生成器**：`tools/bindgen.py` 从 C 头文件生成 Impor 绑定，stdio.h → 36 个包装 / 跳过 110，端到端 `puts` 返回 0。
- **POSIX 审计与构建脚本**：`tools/check_posix_port.py` 报 0 处无保护致命调用；`--static` + `tools/build_posix.sh` + CI 三平台。**本机无法实编 Linux/macOS 产物**。
- **版本单一真源**：`tools/version_sync.py --set/--check` 统一管住 `IR_VERSION`、`vscode-impor/package.json`、两份 baseline、打包脚本、自举种子横幅、README 与 `docs/claims.md` 等全部派生点，修掉 `pack_release.py` 写死的 `VER`。
- **未做（如实列出）**：NaN-boxing / tagged 值表示（要重写 `Val` 与全部 C 生成）、语言层一般内联、Linux/macOS 实编证据、区间证明性能复测、`pkg install/lock` 主流程切新求解器。

### v4.7.0（精致化五项：真类型 / 真优化器 / 真数字 / 真门禁）
- **渐进类型检查**：新增 `impor/semantic.py`，在原有硬门禁之上补 `arith-type`（算术两侧类型不可能成立）、`int-range`（字面量越 int64）、`missing-return`（标了返回却无 return）。默认 **WARN**，`--strict-types` 才 ERROR —— 老脚本一个都不坏。新增 `impor check-types FILE... [--json] [--strict-types]`（也接受目录）。全仓 **267 个 `.impor`：0 warn / 0 error**；第一版复用父类「猜类型」时在自举种子上报过 8 条假警告，已改成只信字面量与标注（`_strict_type`）。详见 [`docs/类型注解边界.md`](docs/类型注解边界.md)。
- **单态化从"字面量定型"扩到"按标注定型"**：`def sum_to(n: int) -> int` 这种非泛型函数也能生成 `gp_sum_to_i64(long long)`；原生实例支持 `for i in range(...)`；`+ - *` 走 `rt_ck_*`，**保留 E1012，不静默回绕**。`--no-mono` / `--no-int-ck` 两个开关。详见 [`docs/单态化.md`](docs/单态化.md)。
- **IR 三项优化 pass**：新增 `impor/irop.py` —— 常量折叠（不折溢出/除零）、死代码消除（只删无副作用的 `CONST`/`MOVE`）、循环不变量外提（只做互不重叠回边）。`impor ir --stats` 多打一行「折叠 N / 删死代码 N / 外提不变量 N」。详见 [`docs/编译管线-IR-4.7-优化.md`](docs/编译管线-IR-4.7-优化.md)。
- **长循环性能**：1e8 累加从 4.6 的 **296.7 ms（6.8×）** 压到 **71.8 ms（1.64×）**（默认，保留 E1012 逐运算检查）；`--no-int-ck` 为 **43.1 ms（0.99×）**。**1.2× 这个目标在保留检查下没做到**，如实记录。
- **确定性门禁入库**：`tools/jitter.py`（同一产物重复跑 N 次比 rc/stdout/stderr，抓 -O2 才现形的抖动）、`tools/verify_ir_coverage.py`（60 示例三路对拍 + 拒绝/构建失败基线 + 非确定分类 `nondet`）。后者当前 **一致 50 / 拒绝 6 / 构建失败 2 / 非确定 2 / 不一致 0**。`examples/grade.impor` 20 次稳定。
- **定位改口径**：从"教学语言 + 小型工具 + 可发布玩具"改为"**可交付的小型教学语言**"，并在首屏新增「明确不竞争的维度」表（生态 / 类型系统严格度 / 后端深度 / 性能极限）。
- **未做（如实列出）**：1e8 保留检查下的 1.2× 未达成；单态化不含字符串/内建/`while` 非 `range`/`try`/类方法；IR 无全局活跃分析与循环展开；混合标注（`int`+`float`）不支持。

### v4.6.0（三条短板补齐 + 自举 E1013）
- **泛型单态化**：`impor/monomorph.py` 按调用点字面量类型生成 `gp_<fn>_i64 / gp_<fn>_f64` 专用实例，函数体走原生 C 运算；擦除版保留作回退。实测 200 万次调用 -O2 下 0.35 s vs 0.45 s。详见 [`docs/单态化.md`](docs/单态化.md)。
- **整数只剩 int64 一档**：`--big` 降级为解释器调试档（`run` 打提示、`build` 直接 exit 3 拒绝）；新增 `impor check-int FILE [--json]` 静态判定"能否按 int64 交付"；四端 E1012 文案与 why/fix 对齐。详见 [`docs/整数语义.md`](docs/整数语义.md)。
- **IR 补齐类 / 异常 / 模块导入**：`try/catch/raise`、`class/继承/字段/方法/super()`、`use/import`（复用 `clink` 在 AST 层拼平）都能走 `impor ir --build`；并发内建明确拒绝。详见 [`docs/编译管线-IR-4.6.md`](docs/编译管线-IR-4.6.md)。
- **自举 E1013 根因确认**：Impor 版编译器编译自身峰值约 2.01GB，32 位 PE 的 2GB 地址空间撞顶。自举链改为全程 64 位（新增 `bootstrap/cc64.bat`，种子内编译走 gcc），B/C/D 三代产物逐字节一致。**O(n²) 拼接本身未改**，32 位工具链仍会失败（现在会提前提示）。
- **顺带修掉既有缺陷：`try/catch` 在 `gcc -O2` 下约 46% 概率崩溃**。根因是 mingw-w64 把 `setjmp` 展开成 `_setjmp(BUF, __builtin_frame_address(0))`，第二个参数当 SEH 链锚点存进 `jmp_buf`，-O2 无帧指针时该地址不稳定，`longjmp` 后死在 `ntdll!RtlVirtualUnwind2`。运行时改走 `IMPOR_SETJMP`（mingw+x64 → `_setjmp(BUF, NULL)`），`ctrans` 与 `cir` 同步。验收：`-O0/-O2/-O3` 各 40/40、`build` 40/40、`run` 20/20、`ir --build` 20/20。
- **未做（如实列出）**：单态化只覆盖 `i64/f64` 字面量调用点（不推导变量类型、不处理 str/自定义类型）；IR 仍拒绝嵌套 `def`/`lambda` 与并发内建；C 侧没有 bignum。

### v3.14.0（验收机制优先于语法特性）
- **API 基线进包**：`tools/api_baseline.json` 随发布包分发，`impor build` 的公开面继续按 `api_frozen` 校验。
- **兑现率不再手写**：README 标记块由 `python tools/verify_claims.py` 直出；`--check` 在 README 或 `docs/claims.md` 与脚本结果不一致时失败。
- **全仓格式化锁绿**：`tools/check_fmt_all.py` 进入 CI 与发布门禁；当前树内 228 个 `.impor` 中 207 个纳入门禁并全部通过，跳过的 21 个是 3.11 冻结 fixture（20）与自举生成物（1）；v4.1 起同一门禁还校验首行语言头部：存在时必须与规范逐字一致（`important<<DAY<<english("English"):`），写花即红。同时修复 `fmt.py` 三处真实缺陷，并用 AST 对比确认 108 个改动文件语义未变。物理删别名与 parser 分支删除已在 v4.0.0 完成。
- **后端口径定稿**：解释器是唯一语义定义；C 后端是语法子集，不支持即编译期拒绝；代码与当前文档中的旧发布口径清零。
- **退出码诚实**：`impor check` 的 WARN 不再返回非零；文件不存在输出干净失败信息，不抛内部异常。
- **冒烟去 GUI**：`examples/snake.impor --smoke` 只跑 60 帧逻辑，不创建 `Tk()`。
- **测试**：新增 `tests/test_v314.py` 锁定上述三项行为；`verify_claims` 自动验证 22/22（新增 `fmt_clean`），人工验收 2 项。

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
- **C 后端当时被称为唯一发布通道（该口径已废止）**：`impor build` 出 exe 或 DLL；`impor run / repl / run-dbg` 只做开发期运行与调试；`impor compile`（Impor → Python）降为开发兼容，不再承担发布；`imporc` 只做内部一致性探针。
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
