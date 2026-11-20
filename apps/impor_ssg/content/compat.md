# 不变卦：3.12–3.14 的冻结承诺

用户流失的头号原因不是功能少，而是昨天能跑的今天不能跑。

- `api_baseline.json` 冻结关键字、内建、CLI 命令、std 模块和错误码。
- 任何删除或改义都会让 `python tools/api_baseline.py` 失败。
- 要改必须同时提交 `docs/rfc/NNNN-*.md`，并在 baseline 里登记。
- 3.11 的 20 个公开示例在 3.12 必须全过：`python tools/compat_check.py`。

这条承诺不是口号，是 CI 里会变红的一条 job。
