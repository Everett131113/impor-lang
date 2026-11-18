# 贡献指南

## 开始之前

1. 读 `docs/生产化/README.md`，确认改动属于哪个阶段。
2. 破坏性语法变更必须先写 RFC，不允许直接改实现。
3. 新功能必须同时提交测试；只加文档不算完成。

## 本地验证

```powershell
python Impor.py doctor
python Impor.py conformance --ci
python run_tests.py
python Impor.py release-check --full
```

四项中任何一项失败，PR 不应合并。

## 代码约定

- Python 代码使用 4 空格缩进，中文诊断文案。
- 新语法必须同时覆盖解释器、C 后端、自举编译器；不能只改一端。
- 错误信息必须包含文件、行、列与可操作建议。
- 不引入新的第三方运行时依赖，除非在 RFC 中说明并给出回滚方案。

## 版本与发布

- 版本只在 `Impor.py` 的 `__version__` 改一处，其余文件由升版脚本同步。
- 发布前必须生成 ZIP、VSIX、校验和与发布说明。
- 发布说明必须列出“已完成 / 未完成 / 已知限制”。

## 提交信息

```
feat(scope): 一句话
fix(scope): 一句话
docs(scope): 一句话
test(scope): 一句话
```
