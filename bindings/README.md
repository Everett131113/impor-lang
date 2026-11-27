# Impor 第三方库绑定（v4.3.1）

这里放「官方维护的绑定」：一个薄 C 层负责把第三方库的指针/结构体句柄压成整数、把结果编成 JSON，
Impor 侧只看到 `int` / `cstr` / 列表 / 字典。这样绕开 `ffi_call` 的三条硬限制（不接受 `ptr` 入参与出参、
不支持结构体、最多 8 个 `int/cstr` 参数），又不需要给 Impor 加新的 ABI。

## 目录

| 绑定 | 状态 | 原生依赖 | 产物 |
|---|---|---|---|
| `sqlite3/` | **可用（已实测）** | 任意 `sqlite3.dll`（运行时 `LoadLibrary`，不需要 import lib） | `impor_bind_sqlite3.dll` |
| `zlib/` | **可用（已实测）** | `zlib1.dll`（运行时 `LoadLibrary`） | `impor_bind_zlib.dll` |
| `image/` | **未完成** | 需要 `stb_image.h`（本机没有；单头文件需从上游取） | — |

## 怎么用（以 sqlite3 为例）

```powershell
cd bindings\sqlite3
gcc -shared -O2 -o impor_bind_sqlite3.dll impor_bind_sqlite3.c
py -3 ..\..\Impor.py run demo.impor
```

`demo.impor` 会打开内存库、建表、插 3 行、查询并打印 JSON。绑定要求两个条件：

1. **位宽一致**：`impor run` 用本机 64 位 Python，DLL 必须 64 位（`gcc` 默认就是）；`impor build` 默认 tcc 是 32 位，
   要让它调绑定得 `--cc gcc` 生成 64 位产物，或另编 32 位 DLL。
2. **允许清单**：`.impor/ffi_allow.json` 的 `allow` 数组要登记 DLL 文件名（本目录已带一份），
   否则 `ffi_open` 会报 `FFIAccessError`——这是 v3.9 起的安全默认，不是缺陷。

## 为什么不做「包管理器」

`impor pkg` 只做本地 vendor（`lib/<name>/`），没有公共仓库。第三方库这条路的真实门槛不是"下载"，
而是"装得上、位宽对、API 能被 C 层吃掉"。所以这里的做法是：**官方绑定 + 明确的构建命令**，
每个绑定自带 `build.ps1` 与 demo，失败信息是中文且指向具体原因（位宽 / 允许清单 / 符号缺失）。
