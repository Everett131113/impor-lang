# CI 证据（2026-11-21 更新）

仓库当前为 **private**：匿名 `api.github.com`、shields 徽章、Actions 运行页对外均不可访问。
以下记录来自仓库主账号在 GitHub Actions 中看到的页面，日志同目录留档。

## v4.1 交付层：C ABI #44890796019（分支 `cabi-delivery-4.1`，提交 `d9aca01`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44890796019
- 结论: `success`，5 个 job 全绿
- Job:
  - **Linux x64 产物 + 三种宿主实跑 (ubuntu-latest) — success**（本轮新增）
  - MSVC x64 (windows-latest) — success
  - GCC x64 (ubuntu-latest) — success
  - TCC win32 (windows-latest) — success
  - GCC linux-arm64 (ubuntu-latest) — success
- 日志: `ci-2026-11-21-linux64-job.log`

Linux 那一格的实际输出（原文摘自日志，行号略）：

```
out/linux/liblogic.so: ELF 64-bit LSB shared object, x86-64, version 1 (SYSV), dynamically linked,
    BuildID[sha1]=2891dd1a402b2037dd3e6922fddb54e06529453d, not stripped
导出符号齐全：impor_add / impor_fib / impor_greet / impor_free_str / impor_runtime_shutdown
C 宿主: impor_add(2,3)=5  impor_fib(15)=610
C 宿主: impor_greet("rust")=hello, rust
C 宿主退出码：0
add=5 fib=55            ← dlopen 版宿主
greet=hello, rust
unloaded                ← dlclose 完成
C 动态宿主退出码：0
Python 宿主: impor_add(2,3) = 5
Python 宿主: impor_fib(15)  = 610
Python 宿主: impor_greet('rust') = hello, rust
ok
Python 宿主退出码：0
```

这一格回答了三件事：`--target linux64` 真的产出 x86-64 ELF（不是把 32 位产物改名）、
Linux 上 `dlopen → 调用 → dlclose → 退出` 同样干净（对应 Windows 的 `FreeLibrary` 回归）、
同一份 `examples/cabi_demo/` 示例在 Linux 的 C 与 Python 宿主下都可直接跑。

## 本机（Windows）同轮证据

| 关口 | 命令 | 结果 |
|---|---|---|
| 32 位（内置 tcc） | `impor build ... --lib --c-source` → tcc | `cabi_probe.dll` 97,504 B；宿主 mode0–6 全 rc=0 |
| `FreeLibrary` 回归 | `host_life.exe 3` / `host_free.exe` / `host_c.exe` | rc=0（钉住模块后卸载不崩） |
| 64 位产物 | `--target win64`（x86_64 gcc 4.8.1 TDM64） | `logic.dll` 126,586 B，PE Machine=0x8664，导出 5 个符号，1.69 s |
| 64 位 C 宿主（静态导入） | `gcc host_c.c logic.lib -I. -o host_c` | `add=5 fib(15)=610`，`greet=hello, rust`，rc=0 |
| 64 位 C 宿主（动态加载） | `gcc host_c_dyn.c` → `host_c_dyn.exe logic.dll` | `add=5 fib=55` / `greet` / `unloaded`，rc=0 |
| 64 位 Python 宿主 | `py -3 examples/cabi_demo/host_python.py logic.dll` | `5 / 610 / hello, rust`，rc=0 |
| Rust 宿主 | 需 `rustc`，本机未安装 | **未验证**，仅交付源码（`host_rust.rs`） |

工具链两条坑，已写进 `impor/cli.py` 的注释里，复跑时不必再踩：
x86_64 gcc 调用时必须把自身 `bin` 前置进子进程 `PATH`（否则找不到 `cc1/as/ld`，静默 rc=1、输出为空）；
TDM gcc 4.8 默认 gnu89，生成 C 含 for 内声明，必须加 `-std=gnu99`。

## 更早记录

### CI #15（提交 `2bed78c`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44418086033
- 标题: `fix: five-minute check runs on PowerShell 5.1 (BOM + sampled conformance); honest CI evidence for private repo`
- 结论: `success`，8m14s，4 个 job 成功
- 截图: `ci-2026-11-19-ci15.png`

## C ABI matrix #15（当前提交 `2bed78c`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44418085903
- 结论: `success`
- 四格定义与 #14 相同：MSVC x64 / GCC x64 / TCC win32 / GCC linux-arm64

## 基线 CI #14 / C ABI #14（提交 `aa78f10`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44343129880
- 标题: `ci: macOS C ABI optional workflow, push gate = 4 verified grids`
- 结论: `success`，4 个 job 成功
- Job:
  - Windows py3.11 full — success
  - Windows py3.12 full — success
  - Ubuntu py3.11 smoke — success
  - Ubuntu py3.12 smoke — success
- 截图: `ci-2026-11-19-ci14.png`

- C ABI Run: https://github.com/Everett131113/impor-lang/actions/runs/44343130120
- 结论: `success`
- Job:
  - MSVC x64 (windows-latest) — success
  - GCC x64 (ubuntu-latest) — success
  - TCC win32 (windows-latest) — success
  - GCC linux-arm64 (ubuntu-latest) — success
- 截图: `ci-2026-11-19-cabi14.png`

## macOS 两格

`c-abi-macos.yml` 为 `workflow_dispatch` 手动触发。当前账号 macOS runner 额度不可用，
`macos-13` 已退役、`macos-14` 解析失败、`macos-15-intel` 排队后被取消。因此不计入 push 门禁，
也不对外宣称已通过。

## 对外口径

仓库保持 private 时，只能写：

> CI 四格已定义，运行记录见仓库 Actions（私有），截图见 `docs/`。

仓库转为 public 后，才可以使用 shields / Actions 徽章作为外部可核验证据。
