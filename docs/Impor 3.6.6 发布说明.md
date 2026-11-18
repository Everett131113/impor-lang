# Impor (I++) 3.6.6 发布说明

3.6.6 是 **编辑器协作与审查版**。本版不改变语言语法，集中补五件日常开发里真正会用到的事：多编辑器组、行内诊断与 F8 问题导航、Git 变更审查台、可配置键位、测试与覆盖率联动。

## 1. 多编辑器组

- `Ctrl+\` 创建独立的“编辑器组 2”，不再把当前文件复制一份。
- 两个组各自有标签栏，切换标签只影响自己的组。
- 资源管理器右键文件 →「在右侧编辑器组打开」。
- 组内标签可独立关闭；关闭最后一个标签时，第二组自动收起。
- 关闭第二组时若存在未保存文件，会先询问。

## 2. 行内诊断 + F8 问题导航

- 错误行整行浅红、警告行整行浅黄；具体位置加红/黄波浪下划线。
- 底部问题面板仍然保留，行内标记与面板同步。
- `F8` 跳到下一个问题，`Shift+F8` 跳到上一个，循环导航。
- 命令面板新增「下一个问题 / 上一个问题」。

## 3. Git 变更审查台

- 左侧是 `git status --porcelain` 的变更文件树，按暂存/修改/未跟踪着色。
- 右侧显示选中文件的逐文件 diff；未跟踪文件按全部新增显示。
- 支持「暂存选中 / 取消暂存 / 暂存全部 / 提交」。
- 顶部显示当前分支与变更数量。
- 保留原有行级 blame、冲突入口、按 hunk 暂存。

## 4. 可配置键位

- 默认读取工作区 `.impor/keybindings.json`。
- 格式：

```json
[
  {"key": "ctrl+alt+g", "command": "global_search"},
  {"key": "ctrl+alt+k", "command": "open_keybindings"}
]
```

- 与内置快捷键重复的键位会被跳过并提示，避免一次按键触发两次。
- 命令面板「键位设置」可直接编辑 JSON，保存后立即重载。
- 支持 `ctrl/alt/shift/win` + 单字符、`enter/esc/tab/space/backspace/delete/insert`、方向键、`home/end/pageup/pagedown`、`F1`–`F24`。
- 非法键位、重复键位、未知命令会在面板提示，不静默失败。

## 5. 测试与覆盖率联动

- 测试树新增「耗时」「覆盖」两列；耗时从测试输出的 `ms/s` 解析。
- 双击用例按 `file.impor:行` 精确打开，不再依赖详情字符串猜位置。
- 覆盖率列读取最近一次行覆盖快照：命中 / 未命中；需要先通过覆盖入口运行一次带覆盖率的执行。未运行覆盖时该列为空，不伪造数据。

## 升级与验证

```powershell
python Impor.py version
python Impor.py run examples\hello.impor
```

已执行：

- `python -m py_compile impor/editor.py impor/keymap.py impor/editor_widgets.py impor/editor_theme.py`
- App 启动冒烟（`_test_app_boot366.py`）
- 多组创建/切换/关闭 + 行内诊断 + F8（`_test_groups366.py`）
- Git 审查台打开与渲染（`_test_git366.py`）
- 测试树四列与耗时解析（`_test_tree366.py`）
- 全量 `python run_tests.py` **完整跑到结束**：573 tests，455.988s，**13 failures / 21 errors**（日志 `_test_baseline_366.log`）。这是当前真实基线，不是全绿。

## 结构收敛（不改变 3.6.6 版本号）

- **版本唯一真源**：新增 `VERSION=3.6.6`；`Impor.py version` 打印“3.6.6 教学稳定线，语法已冻结；v6.x / 7.3.0 为历史实验线”。`docs/版本对齐与语法冻结-3.6.6.md` 说明编号关系。
- **三端矩阵**：`docs/三端能力矩阵-3.6.6.md`。JSON / 正则 / lambda 已实测 C 后端可构建并运行；`filelist` / FFI 在 C 后端构建前明确拒绝；自举端对子集外语法明确报错。
- **编辑器拆分**：Tk 自绘控件层拆到 `impor/editor_widgets.py`，主题色板拆到 `impor/editor_theme.py`；`editor.py` 从 9,590 行降到 9,058 行。
- **测试可复现**：`run_tests.py` 不再挂死，能跑完并给出数字；但基线仍为红色，本版不宣称“全绿”。

## 已知边界

- 第二组不参与会话恢复：重启后标签回到主组。
- 覆盖率列是“最近一次覆盖运行”的映射，不是每个测试独立覆盖率；要做逐测试覆盖率需要测试进程单独回传快照，本版未实现。
- 全量测试仍有 13 failures / 21 errors；主要集中在 `v2.9` 旧语法兼容、`v4.3` 样式 lint 与 LSP 旧用例，本版只完成“可跑完、可复现”，未清零。
- 本目录不是 Git 仓库，无法创建 Git tag；用 `VERSION` + 发布包 SHA256 作为发布证据。
