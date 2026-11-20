# Impor Action（零依赖 GitHub Action）

`impor_action.exe` 由 Impor 自己编译，运行时不依赖 Python / Node。
它扫描指定目录里的 `.impor` 文件，统计行数，并在以下情况失败：

- 目录里没有任何 `.impor` 文件
- 存在空文件
- 存在 `async def` / `await ` 这类本版本明确拒绝的写法

## 在 workflow 里用

```yaml
- uses: Everett131113/impor-lang/apps/impor_action@main
  with:
    path: examples
```

## 本地跑

```bash
impor_action.exe examples
```

重建 exe：

```bash
python Impor.py build apps/impor_action/main.impor -o apps/impor_action/impor_action.exe
```

## 边界

- 只支持 Windows runner（exe 是 Windows 产物）。
- 递归深度上限 8 层，避免异常目录结构把 action 拖死。
- 不做完整语法检查；完整检查用 `python Impor.py check <file>`。
