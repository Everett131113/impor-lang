# Impor (I++) 3.6.5 发布说明

## 定位

3.6.5 是 **IDE 工作区增强版**。本版不改变语言语法，重点补 VS Code 级 IDE 最影响日常使用的两块：

1. 工作区多根资源管理器
2. 外部文件变化后的符号索引热更新

> 边界：Impor 仍是 Tk 桌面编辑器，不是 VS Code 的完整替代品。本版没有新增 Electron 外壳、远程开发、扩展市场或完整 DAP。

## 新增

### 1. 多根工作区资源管理器

- `impor.toml` / 项目配置里的 `roots` 现在会真正显示在左侧资源管理器。
- 每个根目录独立成顶层节点，不再把整个工作区硬编码成单个 `ImporLang` 节点。
- 常用目录（`examples`、`docs`、`bootstrap`、`impor/std`、`tests`、`src`）仍按原逻辑懒加载。
- 根目录下的 `.impor` 文件直接列在对应根节点下。

### 2. 符号索引热更新

- 编辑器启动后会在后台按 2.5 秒间隔检查工作区 `.impor` 文件的 mtime。
- 外部修改、`git checkout`、其他编辑器保存后，`ProjectIndex` 会自动增量刷新，不需要手动重开项目。
- 状态栏新增索引状态：`索引中…` / `索引 N 符号` / `索引已更新` / `索引失败`。
- 索引刷新仍在后台线程执行，不阻塞编辑输入。

### 3. 项目问题跳转修复（延续上一轮）

- 项目质量检查的问题标记现在保存 `(文件路径, 行, 列)`。
- 双击问题会先打开对应文件，再定位行列，而不是只跳当前文件。
- 解析失败的文件也会写入 `(path, 1, 1)`，避免后续问题整体错位一行。

## 验证

- `py_compile`：`Impor.py`、`impor/editor.py`、`impor/__init__.py` 通过。
- 桌面启动冒烟：`APP_BOOT_365_OK`。
- 编辑器十项行为：`EDITOR_FEEL_OK`。
- 新增 `_test_ide_workspace.py`：`INDEX_HOT_REFRESH_OK`、`MULTI_ROOT_TREE_OK`、`IDE_WORKSPACE_OK`。
- `imporc.cmd version`：`imporc (Impor 自举编译器) v3.6.5 · 子集模式`。

## 已知边界

- “VS Code 级别完整 IDE”不是本版完成的承诺。多根工作区、索引热更新只是其中一层。
- 仍未实现：完整扩展市场、远程开发、完整 DAP 调试适配器、Web/Electron 渲染层。
- 后台索引按 mtime 轮询，不是操作系统级文件系统事件；超大仓库会有可测量的扫描开销。
