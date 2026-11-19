# 多语言对照（本机实测）

同一份 `benchmarks/compare/case.json`（1000 个整数求和），各语言独立进程运行，
冷启动取 30 次中位数；峰值内存来自 psutil 采样。命令都在 `benchmarks/compare/` 下执行。

| 语言 | 产物体积 | 冷启动中位数 | 峰值内存 | 目标机依赖 | 结果 |
|---|---:|---:|---:|---|---|
| Impor 3.11 | 120.5 KB | 23.61 ms | 6176 KB | 无（不依赖 Python / VC 运行库） | OK |
| TCC-C | 2.0 KB | 24.29 ms | 6920 KB | 无 | OK |
| Lua 5.4 | 477.5 KB | 21.16 ms | 5408 KB | 需要 lua.exe / lua54.dll | OK |
| Python 3.11 | 7.8 MB | 1262.91 ms | 9732 KB | 无（打包运行时） | OK |
| Go 1.23 | 1.9 MB | 24.61 ms | 6868 KB | 无 | OK |
| Node 22 | 76.8 MB | 119.56 ms | 29548 KB | 需要 node.exe（约 80 MB） | OK |

## 复现命令

```text
Impor 3.11 case_impor.exe
TCC-C      case_tcc.exe
Lua 5.4    lua.exe case.lua
Python 3.11 case_python.exe
Go 1.23    case_go.exe
Node 22    node.exe case.js
```

完整 JSON：`compare.json`。

产物清单：case_impor.exe（内置 tcc，32 位）；case_tcc.exe（tcc -O2）；lua.exe + lua54.dll（若同目录存在）；PyInstaller onefile；case_go.exe（-ldflags=-s -w）；node.exe（不含脚本）
