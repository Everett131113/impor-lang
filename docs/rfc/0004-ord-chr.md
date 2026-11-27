# RFC 0004：新增内建 `ord` / `chr`（4.5.0）

- 状态：已接受（4.5.0 落地）
- 影响面：公开 API（内建函数 +2）、C 运行时、标准库
- 兼容性：纯新增，不改变既有语义

## 背景

4.5.0 给标准库补若干模块（`std.encoding`、`std.color`、`std.validate` 等），它们要做
「字节/码点级」处理：base64 编解码、十六进制转换、ANSI 转义、字符类别判定。
这些功能都需要「字符 ↔ 码点」这一对原语，而此前：

- `impor/ir.py` 的参考求值器里有 `ord` / `chr`；
- 解释器内建表与 C 运行时**都没有**；
- 结果同名字符在 IR 路径能跑、在 `impor run` 与 `impor build` 上不可用。

这属于「同一语义在不同路径上可用性不一致」，按 4.3.2 的收口口径必须消除。

## 决定

新增两个内建，三端同语义：

| 内建 | 语义 | 边界 |
|---|---|---|
| `ord(s)` | 返回 `s` 首字符的 **Unicode 码点** | `s` 为空串抛 `ValueError`；不做「取第一个字节」的降级 |
| `chr(n)` | 返回码点 `n` 对应的单字符 | `n` 必须在 `0..0x10FFFF`，否则抛 `ValueError` |

实现位置：

- 解释器：`impor/builtins.py` 的 `builtin_ord` / `builtin_chr`；
- 登记表：`impor/builtin_manifest.py` 的 `RUNTIME_BUILTINS`（缺登记会启动即报错）；
- C 运行时：`impor/c_runtime.c` 的 `rt_ord` / `rt_chr`，复用 4.5 的 `rt_uc_decode` /
  `rt_uc_encode`（UTF-8 编解码），与 `rt_upper` / `rt_lower` 共用同一套码点基础设施；
- 转译映射：`impor/ctrans.py` 的 `BUILTINS` 表。

## 为什么按码点而不是按字节

C 侧字符串是 UTF-8，若 `ord` 返回首字节，则 `ord("豆")` 会得到 232（`0xE8`），
与解释器的 35910 不一致——这正是 4.3.2 里被判定为「同一份代码两端不同结果」的缺陷类型。
因此 `ord` 一律走 UTF-8 解码，与 `len()`（按字符计数，v3.3 起）、`s[i]`（按字符下标）保持同一口径。

## 验收

| 项 | 口径 | 结果 |
|---|---|---|
| 解释器 vs C 产物 | `print(ord("A")) / print(ord("豆")) / print(chr(66)) / print(chr(20320))` 逐字节对拍 | 一致（`65 / 35910 / B / 你`） |
| API 基线 | `tools/api_baseline.py --update --rfc docs/rfc/0004-ord-chr.md` | 内建 122 → 124，其余不变 |
| 回归 | `tests/test_v4_backend_parity.py`、`tools/verify_claims.py` | 全通过 |

## 未采纳方案

- **让 `ord` 返回字节**：与解释器冲突，且会把 UTF-8 细节漏给用户。
- **只做 `str.ord` 方法**：Impor 的方法分派是宿主相关的（4.3.2 已因 `.split` 踩过坑），
  内建函数比方法分派更容易在三端对齐。
