# 参与 Impor

Impor 现在最需要的不是更多语法，而是“陌生人能跑通、报错能照做、承诺不缩水”。
下面这些是最有效的参与方式。

## 第一次跑通（约 3 分钟）

1. 解压 Windows ZIP。
2. 双击 `开始这里.bat`，看到“你好，Impor”。
3. 再双击 `examples\quickstart\2-读文件.bat` 和 `3-调DLL.bat`。
4. 卡住时先看 `常见问题.txt`；仍然卡住就开 issue，写清版本、系统、最小复现。

## 报告问题

请用 bug issue 模板，必须包含：

- `Impor.py version` 或 `ImporPortable.exe version` 的输出
- 操作系统与版本（例如 Windows 11 24H2）
- 最小复现代码（能复制粘贴的 `.impor` 片段）
- 期望结果 / 实际结果

## 本地验证

```powershell
python Impor.py doctor
python run_tests.py
python tools/verify_claims.py
python tools/error_audit.py
```

提交前至少让后三条通过。新增公开 API 时：

1. 先加 `tools/verify_release.py` 的 check；
2. 再在 `docs/claims.yaml` 登记 claim；
3. 运行 `python tools/verify_claims.py` 重新生成 `docs/claims.md`；
4. 如果改了冻结面，补 `docs/rfc/` 并运行
   `python tools/api_baseline.py --update --rfc docs/rfc/xxxx.md`。

## 代码约定

- Python 代码使用 4 空格缩进，中文诊断文案。
- 新语法必须同时覆盖解释器、C 后端；不能只改一端。
- 错误信息必须包含文件、行、列、原因、修法与稳定错误码。
- 不引入新的第三方运行时依赖，除非在 RFC 中说明并给出回滚方案。

## 适合第一次贡献的 issue

- 把某条错误码的“怎么修”写得更具体
- 给 `docs/errors.md` 补一个真实触发例子
- 给 `apps/impor_ssg` 增加一种 Markdown 语法
- 把 `常见问题.txt` 里某一条改写得更像人话

带 `good first issue` 标签的 issue 都是这种范围。

## 版本与发布

- 版本只在 `VERSION` / `Impor.py` 的 `__version__` 改，其余由 `tools/sync_version.py` 同步。
- 发布前必须生成 ZIP、VSIX、校验和与发布说明。
- 发布说明必须列出“已完成 / 未完成 / 已知限制”。

## 提交信息

```
feat(scope): 一句话
fix(scope): 一句话
docs(scope): 一句话
test(scope): 一句话
```

## 不做的事

- 不为了“看起来像大语言”而加语法糖或中文关键字
- 不追“比 C 快”；当前只承诺单线程 C 量级的合理性能
- 不在 10 个真实用户之前做包管理器、IDE 全套或空社区
