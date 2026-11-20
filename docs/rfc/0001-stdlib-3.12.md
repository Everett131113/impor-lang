# RFC 0001：3.12 标准库扩展（json / fs / path / regex / time / http）

## 1. 为什么要改

3.11 的内建函数能读写文件、编解码 JSON、做正则，但没有稳定的模块级入口。
用户必须记住 `json_decode` / `regex_match` 这类函数名，且没有统一的 `std.*`
命名空间。日常任务（抓网页 → 解析 JSON → 写文件）缺少“一个 import 就能用”的路径。

## 2. 变更内容

新增公开标准库模块（全部位于 `impor/std/`，随解释器与 C 后端分发）：

| 模块 | 能力 |
|---|---|
| `std.json` | `parse` / `stringify` / `try_parse` |
| `std.fs` | `read` / `write` / `exists` / `list` / `mkdir` / `move` / `size` / `read_json` / `write_json` |
| `std.path` | `norm` / `is_abs` / `join` / `basename` / `dirname` / `ext` / `stem` |
| `std.regex` | `is_match` / `find_all` / `replace_all` / `contains` |
| `std.time` | `readable` / `seconds` / `sleep_ms` / `elapsed` |
| `std.http` | `get` / `get_json`（使用系统自带 curl，不引入第三方包） |

同时把这些模块加入 `std` 包默认导出。`api_baseline.json` 新增 `std_modules` 面。

为让 `apps/impor_sqlite_report` 在 C 后端高效处理 10 万行 CSV，追加三个纯字符串内建
（解释器与 C 后端同名同义）：

| 内建 | 签名 | 说明 |
|---|---|---|
| `strfind` | `strfind(s, sub, start=0)` | 返回子串首次出现的**字符下标**，找不到 -1 |
| `strlines` | `strlines(s)` | 按 `\n` / `\r\n` 切行，返回数组，不保留行尾换行 |
| `strjoin` | `strjoin(arr, sep)` | 用分隔符连接数组元素 |
| `strreplace` | `strreplace(s, old, new)` | 一次扫描替换全部子串（不逐字符拼接） |
| `filelines` | `filelines(path)` | 读文件并按行返回数组，读完不保留整块文本 |

**没有新增关键字，没有删除任何已有 API，没有改错误码含义。**

## 3. 影响谁

- 已有脚本：不受影响；新模块是追加。
- C 后端：`std.json` / `std.path` / `std.regex` / `std.fs` / `std.time` / `std.http` 已用同一套基础内建实现，`impor build` 可编译并在无 Python 机器运行。
- 编辑器 / LSP：模块名进入补全与 `list-modules`；不需要语法改动。

## 4. 兼容与迁移

无破坏性变更，无需迁移。旧写法 `json_decode(...)` / `regex_match(...)`
继续有效；新代码推荐 `import std.json as json`。

## 5. 验收

- [x] `python tools/api_baseline.py --update --rfc docs/rfc/0001-stdlib-3.12.md`
- [x] 解释器：`tests/test_stdlib.py` 六个模块全部通过
- [x] C 后端：`impor build` 后用 `std.json` / `std.path` / `std.regex` / `std.http` 实测
- [x] `python tools/compat_check.py` 20/20
- [x] `strfind` / `strlines` / `strjoin` 解释器与 C 后端逐条对照（`apps/impor_sqlite_report` 10 万行实测 1.75 s / 峰值 7.9 MB）
- [x] `docs/errors.md` 无新增错误码，不需要改
- [x] CHANGELOG 记录

## 6. 决策记录

- 日期：2026-11-19
- 结论：采用“纯 Impor 模块 + 基础内建”的实现，不引入 Python 运行时依赖；
  `std.http` 用系统 `curl`，Windows 10/11 自带。
- 风险：`std.http` 依赖 curl 存在；缺失时明确失败，不静默返回空串。
