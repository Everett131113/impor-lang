# 多语言对照用例

同一份 `case.json`（1000 个整数求和，期望输出 `sum = 500500`），每个语言一个最小实现：

| 文件 | 语言 |
|---|---|
| `case.impor` | Impor（`fileread` + `json_decode`，解释器与 C 后端均可） |
| `case.c` | TCC-C（题设固定 JSON 结构，手写数组扫描） |
| `case.lua` | Lua 5.4（模式匹配取整数） |
| `case.py` | Python 3.11（`json`） |
| `case.go` | Go 1.23（`encoding/json`） |
| `case.js` | Node 22（`JSON.parse`） |

在仓库根运行：

```powershell
python tools/compare.py --runs 30
```

脚本会现场编译/运行全部语言，写出 `compare.json` 与 `docs/compare.md`。
某个运行时没装时，脚本把它标为未测量，不会填博客数字。
