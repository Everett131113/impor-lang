# examples/cabi_demo —— 一份逻辑，C / Rust / Python 三种宿主

演示 Impor 作为“交付层”的用法：逻辑写在 `logic.impor`，编译成一个 DLL，
被三种语言的宿主直接调用；全程“加载 → 调用 → 卸载 → 退出”退出码 0。

## 目录

| 文件 | 说明 |
|---|---|
| `logic.impor` | 逻辑层，导出 `add` / `fib` / `greet` |
| `host_c.c` | C 宿主（链接导入库 `logic.lib`） |
| `host_python.py` | Python 宿主（`ctypes`，需 64 位解释器） |
| `host_rust.rs` | Rust 宿主（`extern "C"`，本机未装 rustc，未验证） |

## 复跑（Windows x64）

```powershell
# 0) 进工程根目录
cd "Impor 4.0.0"

# 1) 产出 64 位 DLL（需要 x86_64 gcc：Dev-Cpp MinGW64 / MinGW-w64 / MSVC cl，或用 IMPOR_CC 指定）
py -3 Impor.py build examples/cabi_demo/logic.impor --lib --c-source -o examples/cabi_demo/out --target win64

# 2) C 宿主（MinGW，编译时其 bin 目录需在 PATH 中）
$env:PATH="C:\Program Files (x86)\Dev-Cpp\MinGW64\bin;"+$env:PATH
cd examples/cabi_demo/out
gcc ..\host_c.c logic.lib -o host_c.exe
.\host_c.exe; "C rc=$LASTEXITCODE"

# 3) Python 宿主
py -3 ..\host_python.py logic.dll; "Python rc=$LASTEXITCODE"

# 4) Rust 宿主（装了 rustc 才有这一步）
# rustc ..\host_rust.rs -L . -l logic
# .\host_rust.exe; "Rust rc=$LASTEXITCODE"
```

## 注意

- 头文件 `logic.h` 里 `impor_add` 的返回类型是 `long long`（64 位），按头文件签名声明，不要写成 `int`（会栈损坏）。
- `impor_greet` 返回的字符串必须用 `impor_free_str` 释放（Impor 侧堆）。
- DLL 内置“首次调用时钉住模块”的逻辑，`FreeLibrary` 后再退出不会再崩；代价是映射保留到进程结束（与 CPython 扩展一致）。
