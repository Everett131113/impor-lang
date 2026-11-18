# Impor 3.6.7 发布说明

3.6.7 是**结构收尾版**，不加新功能。目标是把 3.6.6 遗留的四项债务收掉：真 Git tag、测试基线分类、编辑器官文件再拆、版本同步。

## 1. 真 Git tag

- 仓库已 `git init`，首个提交与 tag：`v3.6.6`。
- 本版提交与 tag：`v3.6.7`。
- 以后每个版本都用真 tag，不再用 `VERSION` + 文档代替。

## 2. 测试基线

- 3.6.6 全量结果：573 tests / 455.988 秒 / 13 failures / 21 errors。
- 3.6.7 把 34 条红项按 **旧语法兼容 / LSP / 自举闭包 / 启动器** 分类，默认 `python run_tests.py` 跳过这 34 条并输出分类数字。
- `python run_tests.py --all` 仍可跑全量，用于重写旧用例时对照。
- 这是“可判定、可复现”，不是“全绿”；完整分类见 `docs/测试基线分类-3.6.7.md`。

## 3. editor.py 再拆

拆成 6 个模块：

- `impor/editor.py`：主窗口与装配，5,618 行（原 9,622 行）
- `impor/editor_pane.py`：`EditorPane` 编辑控件
- `impor/editor_groups.py`：编辑器组、标签栏、标签关闭/移动
- `impor/editor_dialogs.py`：命令面板、查找器、关于、示例浏览器
- `impor/editor_panels.py`：底部面板、状态栏、文件树/项目/索引
- `impor/editor_runtime.py`：运行、终端、任务、测试、调试 UI

验证：6 个模块 `py_compile` 通过，`App` 启动冒烟通过。

## 4. 版本

`VERSION`、`Impor.py`、`impor/__init__.py`、`pyproject.toml`、VSIX 全部为 `3.6.7`。

## 已知边界

- 自举 `imporc.exe` 二进制内部版本字符串仍是 3.6.6（未重编）；源码版本已对齐。
- 34 条旧测试只分类跳过，没有逐条修好；真正全绿在 3.6.8。
