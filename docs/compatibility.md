# Impor 兼容性承诺（3.12–3.14）

## 一句话

**3.12、3.13、3.14 三个发布线内，不删除、不改义任何已发布 API。** 需要变更时，先写 RFC，再更新 `api_baseline.json`，并在 CHANGELOG 标注破坏性变更与迁移方式。

## 被冻结的公开面

| 面 | 文件 / 入口 | 检查命令 |
|---|---|---|
| 关键字 | `impor/lexer.py` | `python tools/api_baseline.py` |
| 内建函数（运行时 / 宿主 / 仅解释器） | `impor/builtin_manifest.py` | 同上 |
| CLI 子命令 | `impor/cli.py` | 同上 |
| 运行错误码 | `impor/diagnostics.py` | 同上 |
| 语法错误码 | `impor/diagnostics.py` | 同上 |
| 3.11 示例行为 | `conformance/compat_3_11/` | `python tools/compat_check.py` |

`api_baseline.json` 是机器可读的唯一基线。它由脚本生成，不靠手写；提交前必须跑一次严格比对。

## 什么算破坏性变更

- 删除或改名任何关键字、内建、CLI 子命令；
- 改变错误码的含义（编号不能复用）；
- 新增关键字（会把用户已有标识符变成保留字）；
- 让 3.11 的 20 个示例从“通过”变成“报错”。

新增内建、新增 CLI 子命令、新增错误码不属于破坏性变更，但仍要跑基线确认没有误删。

## 变更流程

1. 复制 `docs/rfc/0000-template.md` 为 `docs/rfc/NNNN-标题.md`。
2. 在 RFC 里写清原因、影响、迁移方式、验收。
3. 实现变更与迁移测试。
4. `python tools/api_baseline.py --update --rfc docs/rfc/NNNN-标题.md`
5. `python tools/compat_check.py` 必须 20/20；若确实要让旧示例改变，必须在 RFC 里逐条解释。
6. CHANGELOG 加“破坏性变更”段落。

## 不承诺什么

- 不承诺“永不变化”：安全问题、法律合规、不可修复的设计缺陷可以走 RFC 变更。
- 不承诺第三方 Python 包 API、未公开的内部函数、编辑器 UI 细节。
- 不承诺跨大版本（3.x → 4.x）的自动兼容；大版本会另写迁移指南。
