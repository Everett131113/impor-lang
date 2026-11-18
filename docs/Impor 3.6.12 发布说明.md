# Impor (I++) 3.6.12 发布说明

日期：2026-11-15

## 一句话

3.6.12 不动语言语法，只把测试基线再收敛一刀：REPL / `use` / 样式 lint 共 8 条旧用例切到当前语法与当前文案，`LEGACY_IDS` 从 14 条降到 6 条。默认 suite 实测 **566 tests / OK**。

## 本版改了什么

- `tests/test_v32.py`：`用 std.text` 改为当前 `use std.text`，验证星号注入。
- `tests/test_v33.py`：REPL 多行块从旧 `函数` / `变量` / `如果` 改为 `def` / `var` / `if`。
- `tests/test_v35.py`：`help(print)` 断言改为当前说明「打印」与 `print("xxx")`。
- `tests/test_v43.py`：样式 lint 用例改用当前可解析源码，验证 `style-semicolon` / `style-extends` / `style-params` / `style-catch`；`--style` CLI 用例改用 `print("x"); print("y")`。
- `run_tests.py`：`LEGACY_IDS` 14 → 6；日志标签 `legacy_3612`。
- 版本元数据统一到 3.6.12，`imporc.exe` 按 3.6.12 源码重建。

## 验证

```text
legacy_3612: 6 条旧语法用例已分类跳过（--all 可跑全量）
Ran 566 tests in 140.483s
OK
```

```text
imporc (Impor 自举编译器) v3.6.12 · 子集模式
```

## 未做

剩余 6 条不属本版：`try/catch` C 后端、保留字口径、`print` 软别名、`then`、诊断一致性，以及必须单独排期的自举闭包。
