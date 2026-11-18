# Impor C ABI 示例（v3.9.0）

这条链是本版的验收证据：

```bat
impor build --lib lib.impor -o lib.dll
tcc demo.c lib.dll -o demo.exe
demo.exe
```

也可以双击 `build.bat` 一次跑完。

输出：

```
add(2, 3) = 5
greet("Impor") = Hello, Impor
twice((size_t)21) = 42
```

## 64 位：`--c-source`

内置 tcc 只产 32 位 DLL。需要 x64 时生成源码包，用本机工具链编译：

```bat
impor build --lib --c-source lib.impor -o out\
out\build_msvc.bat
out\build_gcc.bat
```

`lib.h` 不变，`demo.c` 不改即可链接 x64 `lib.lib` / MinGW import library。
`--c-source` 生成的 `lib.c` 是纯库源码，不包含 `main`；普通 `impor build` / `build --lib` 仍生成 `main`。

四导出 x64 门禁见仓库根 `.github/workflows/c-abi-x64.yml`：`windows-latest` 用 MSVC、`ubuntu-latest` 用 gcc，各自编译
`examples/c_abi/ci_lib.impor` 并校验 PE/ELF 为 x64、`impor_add` / `impor_sub` / `impor_mul` / `impor_greet` 四个符号齐全。

## 产物

- `lib.dll`：C ABI 共享库，导出符号 `impor_add` / `impor_greet` / `impor_twice` / `impor_free_str`（统一加 `impor_` 前缀）。
- `lib.h`：调用方头文件，含 `extern "C"` 与 `<stddef.h>`，可直接被 C / C++ 包含。
- `lib.def`：导出名清单，供 MSVC `lib /def:` 或 MinGW `dlltool` 生成 import library。

## 当前边界（未实现就报错，不猜测）

只支持顶层 `export func`；参数和返回值必须显式标注
`int / float / bool / str / void / size_t / handle / ptr`。

不支持：默认参数、变参、泛型、类方法导出、async 导出、命名结构体的强类型指针。

类型映射：

| Impor | C ABI |
|---|---|
| int | long long |
| float | double |
| bool | int（0/1） |
| str | const char *（返回值由 DLL 新分配，调用方用 `impor_free_str()` 释放） |
| void | void |
| size_t | size_t |
| handle / ptr | void *（不透明句柄 / 结构体按指针传递） |

字符串所有权示例：

```c
const char *s = impor_greet("Impor");
printf("%s\n", s);
impor_free_str(s);   /* 必须释放；传入 NULL 安全 */
```

## 关于 `gcc demo.c -L. -lx`

本机没有 gcc / dlltool，无法实测 MinGW 的 `-lx` 路径，因此本版不把它写成“已验证”。
现有的可复现实测是随包 tcc 直接链接 DLL：

```bat
..\..\impor\tcc\win32\tcc.exe demo.c lib.dll -o demo.exe
```

需要 `-lx` 的 MinGW 用户，可先用 `dlltool -d lib.def -l liblib.dll.a` 生成 import library，再用
`gcc demo.c -L. -llib`。这条路径未在本机验证。

MSVC 路径同样未实测：本机没有 `cl.exe` / `lib.exe` / `link.exe`。有 MSVC 的机器可以：

```bat
lib /def:lib.def /out:lib.lib /machine:x64
cl demo.c lib.lib
```

这条命令是使用说明，不是本版的实测结果。
