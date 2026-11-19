# 多语言对照用例

同一份 `case.json`（1000 个整数求和，期望输出 `sum = 500500`），每个语言一个最小实现：

| 文件 | 语言 |
|---|---|
| `case.impor` | Impor（`fileread` + `json_decode`，C 后端编译为独立 exe） |
| `case.c` | TCC-C（题设固定 JSON 结构，手写数组扫描） |
| `case.lua` | Lua 5.4（模式匹配取整数） |
| `case.py` | Python 3.11（`json`） |
| `case.go` | Go 1.23（`encoding/json`） |
| `case.js` | Node 22（`JSON.parse`） |

## 跑法

```powershell
# 默认只写本地文件，不碰发布表
python tools/compare.py --runs 30

# 正式发布：同机同一次运行，写 compare.json + docs/compare.md
python tools/compare.py --runs 30 --publish
```

- 固定输出 6 行；缺运行时填「未测量」，体积/耗时/内存写 `—`。
- 默认写 `compare.local.json` 与 `docs/compare.local.md`；`--publish` 才覆盖正式文件。
- `--python-mode auto|onefile|onedir|system`：`auto` 现场用 PyInstaller 构建 onefile 到临时目录，失败 exit 2，不降级。
- `--strict`：任何一行未测量或输出错误即 exit 1，CI 用。
- `--keep`：保留临时构建目录，便于排查。

## 加一门语言

1. 在 `benchmarks/compare/` 放 `case.<ext>`，正常路径打印 `sum = 500500`，并在第一行加：

   ```text
   if env IMPOR_COMPARE_FLOOR: exit 0
   ```

   纯启动测量会带 `IMPOR_COMPARE_FLOOR=1` 跑同一个产物；没有这行就只填 `—`，
   不要拿总耗时冒充启动。
2. 在 `tools/compare.py` 的 `ORDER` 加语言名，照现有 `fill()` 调用补一段：
   体积、产物描述、计数口径（`可执行产物` / `含运行时`）、测法
   （`compiled` / `onefile` / `onedir` / `script+runtime`）、依赖。
3. 跑 `python tools/compare.py --runs 30`，确认 6+N 行里新行是 `OK`；
   提交时附 `compare.local.json` 的机器信息（CPU / OS build / 版本号）。

## 提交 Lua / Go 补测

没有 Lua / Go 的机器不需要伪造数字：直接跑默认命令，表里这两行会是「未测量」。
有运行时的机器用参数指过去：

```powershell
python tools/compare.py --runs 30 --publish `
  --lua "C:\path\to\lua.exe" `
  --go "C:\path\to\go.exe" `
  --node "C:\path\to\node.exe" `
  --tcc "impor\tcc\win32\tcc.exe"
```

PR 里只需带更新后的 `docs/compare.md` 与 `compare.json`，表头会自动写
`runs=30` 和 `commit=<sha>`。
