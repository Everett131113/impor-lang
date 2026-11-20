# Impor SQLite 报表工具

读 CSV → 写入 SQLite（Windows 自带 `winsqlite3.dll`）→ 按区域聚合 → 生成 HTML 报表。
整个工具由 Impor 编写，C 后端编译成单个 exe，不依赖 Python / Node / `sqlite3.exe`。

## 这个项目证明什么

- 真实文件的读写与 10 万行级数据处理
- 通过 C ABI 调用系统 DLL（不是玩具 FFI）
- 在陌生机器上零依赖运行（复制 exe + 数据即可）

## 跑起来

在仓库根目录：

```powershell
python Impor.py run apps/impor_sqlite_report/report.impor
```

或直接用已编译的 exe（不需要 Python）：

```powershell
apps\impor_sqlite_report\report.exe
```

换一个小数据集快速看结果：

```powershell
apps\impor_sqlite_report\report.exe apps\impor_sqlite_report\data\sales_1000.csv 1000
```

重建 exe：

```powershell
python Impor.py build apps/impor_sqlite_report/report.impor -o apps/impor_sqlite_report/report.exe --no-cache
```

## 数据

- `data/sales_100k.csv`：100,000 行，2,145,496 字节，UTF-8 中文；列 `region,category,amount`
- `data/sales_1000.csv`：前 1,000 行，用于快速抽检
- `gen_csv.py`：用固定 seed 20261119 重新生成数据

## 实测（本机 Windows 11，单线程）

- 100,000 行 → `0.8 s` 左右（多次运行 0.77–0.86 s）
- 峰值工作集：`47 MB` 左右（`< 50 MB` 目标内）
- 输出：`report.html`，5 个区域、总金额 `100401555.2`
- 报表行数：100000 / 区域数：5

复测峰值（PowerShell 采样）：

```powershell
$p = Start-Process .\apps\impor_sqlite_report\report.exe -NoNewWindow -PassThru
$max = 0
while (-not $p.HasExited) { $max = [math]::Max($max, $p.WorkingSet64); Start-Sleep -Milliseconds 50 }
"peak MB = " + [math]::Round($max / 1MB, 1)
```

更精确的峰值由 `python tools/verify_release.py --check app_sqlite` 用 psapi 采样并断言 `< 50 MB`。

## 为什么内存能压在 50 MB 内

没有 GC 的运行时里，最容易失控的是“每行都造一堆临时字符串”。这里做了三件事：

1. `filelines` 直接读文件切行，不长期保留整块 CSV 文本；
2. 每 100 行用 `strjoin` 拼成一块，再用 C 内建 `strreplace` 一次扫描生成 SQL 值列，避免每行 5 次字符串拼接；
3. SQLite 用磁盘数据库而不是 `:memory:`，并关闭 journal / mmap，减少页缓存。

## 边界

- CSV 解析假设字段内没有逗号；带引号逗号的 CSV 需要先用 `std` 工具预处理。
- 只使用 Windows 自带的 `winsqlite3.dll`；其他平台需要换成对应 SQLite 动态库。
- 报表按 `region` 聚合，`category` 保留在库里但不参与当前报表。
