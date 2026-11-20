# RFC 0002：把 ModuleNotFound 独立为 E1015

- 状态：已接受（3.12.0 发布前修正）
- 日期：2026-11-20

## 1. 为什么要改

3.12.0 的 `ERROR_CODES` 曾同时把 `ImportError` 与 `ModuleNotFound` 映射到
`E1010`。这违反两条既有约束：

1. `tests/test_v42_diag.py::TestErrorCodes::test_table` 要求所有错误码唯一；
2. `docs/errors.md` 的“编号不复用、含义不改变”契约要求一个编号只对应一个
   稳定含义。

重复错误码会让用户按 `E1010` 搜索文档时得到两个不同异常名，也会让
`impor explain` 的机器可读输出不稳定。

## 2. 变更内容

- 删除 / 改名的公开项：无。
- 新增的公开项：运行错误码 `E1015`，异常名 `ModuleNotFound`，标题
  「模块文件不存在」。
- 受影响的错误码：
  - `ImportError` 继续使用 `E1010`；
  - `ModuleNotFound` 从 `E1010` 改为 `E1015`。

## 3. 影响谁

- 已有脚本 / 项目：脚本行为不变，只是模块文件缺失时的诊断末尾由
  `[E1010]` 变为 `[E1015]`。
- C 后端 / 解释器：`impor/diagnostics.py::ERROR_CODES`、`ERROR_TITLES`
  同步；解释器与 C 后端共用同一张表。
- 编辑器 / LSP / 文档：`docs/errors.md` 增加 `E1015` 条目与锚点；
  `api_baseline.json` 的 `error_codes` 追加一项。

## 4. 兼容与迁移

- 是否保留旧写法：不保留双映射；`E1010` 仍表示 `ImportError`。
- 自动迁移工具：无需迁移脚本；按错误码做字符串匹配的外部工具只需把
  `ModuleNotFound` 的期望码改为 `E1015`。
- 迁移示例：
  - 旧：`ModuleNotFound -> E1010`
  - 新：`ModuleNotFound -> E1015`

## 5. 验收

- [x] `python tools/api_baseline.py --update --rfc docs/rfc/0002-error-codes.md`
- [x] `tests/test_v42_diag.py::TestErrorCodes::test_table` 通过
- [x] `docs/errors.md` 与 `docs/claims.md` 同步
- [x] CHANGELOG 记录

## 6. 决策记录

- 日期：2026-11-20
- 结论：这是 3.12.0 发布前的编号去重，不改变任何已发布编号的含义；
  `E1015` 按“只追加”规则新增。
- 反对意见与处理：若直接把 `ModuleNotFound` 从表中删除并回退到 `E1099`，
  会丢失“模块文件不存在”的可行动信息，因此不采用。
