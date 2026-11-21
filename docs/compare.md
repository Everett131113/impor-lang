# 多语言对照（本机实测）

同机同一次运行；runs=30；commit=8373c5539b3c。

| 语言 | 产物 | 计数口径 | 测法 | 总耗时中位数 | 纯启动中位数 | 峰值内存 | 依赖 | 结果 |
|---|---|---|---:|---:|---:|---:|---|---|
| Impor 3.11 | 120.5 KB · case_impor.exe，内置 tcc 32 位 | 可执行产物 | compiled | 21.44 ms | 22.41 ms | 6176 KB | 无（不依赖 Python / VC 运行库） | OK |
| TCC-C | 2.5 KB · case_tcc.exe（tcc -O2） | 可执行产物 | compiled | 21.76 ms | 23.17 ms | 6996 KB | 无 | OK |
| Lua 5.4 | 477.5 KB · lua.exe + lua54.dll（若同目录存在） | 含运行时 | script+runtime | 21.27 ms | 21.06 ms | 5412 KB | 需要 lua.exe / lua54.dll | OK |
| Python 3.11 | 7.8 MB · PyInstaller onefile | 含运行时（打包） | onefile | 1207.48 ms | 1232.31 ms | 9744 KB | 无（打包运行时） | OK |
| Go 1.23 | 1.9 MB · case_go.exe（-ldflags=-s -w） | 可执行产物 | compiled | 24.57 ms | 23.20 ms | 6860 KB | 无 | OK |
| Node 22 | 76.8 MB · node.exe（不含脚本） | 含运行时 | script+runtime | 120.02 ms | 114.22 ms | 29448 KB | 需要 node.exe（约 80 MB） | OK |

口径说明：

- **计数口径**「可执行产物」= 只算被加载的 exe；「含运行时」= exe/DLL/解释器一起算。Node 与 Python-system 的格子含整个运行时，Impor / TCC-C / Go 只含产物本身。
- **测法** `compiled` = 独立 exe；`onefile` / `onedir` = PyInstaller 打包；`script+runtime` = 脚本由外部运行时执行。
- **总耗时** = 启动 + 读 JSON + 求和 + 退出；**纯启动** = 空程序 / 空脚本退出，只测进程地板（Python onefile 仍包含解包）。
- TCC-C 的 2.5 KB 来自 tcc 自带微型 CRT；MSVC 编译同源程序通常 100 KB+。
- README 规格表测的是 `hello.impor`，本表测的是 `case.impor`；程序与测法不同，两组数字不应逐位对比。

## 复现命令

```text
Impor 3.11 case_impor.exe
TCC-C      case_tcc.exe
Lua 5.4    lua.exe case.lua
Python 3.11 case.exe
Go 1.23    case_go.exe
Node 22    node.exe case.js
```

未测量：无

脚注：

- **TCC-C**：2.5 KB 来自 tcc 自带微型 CRT；MSVC 编译同源约 100 KB+
- **Lua 5.4**：脚本不打包；体积含运行时
- **Node 22**：脚本不打包；体积含运行时
