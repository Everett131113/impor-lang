# Impor (I++) — VS Code 扩展

语法高亮 + LSP 语言服务器（语法诊断、跳转定义、悬停提示、文档符号大纲、运行/格式化命令）。

## 前置要求

- [VS Code](https://code.visualstudio.com) ≥ 1.75
- Python ≥ 3.10，且已安装 Impor 语言（任一方式）：
  ```powershell
  cd ImporLang
  python -m pip install -e .        # 安装为系统命令
  # 或免安装：扩展的 LSP 启动命令可改为 python ImporLang\Impor.py lsp 的等价形式
  ```

## 本地安装（开发模式）

```powershell
cd vscode-impor
npm install                 # 安装 vscode-languageclient
code .                      # 在 VS Code 中打开本目录
F5                          # 扩展开发宿主：打开一个 .impor 文件即可看到高亮与诊断
```

## 打包与分发

```powershell
cd vscode-impor
npm install
npx --yes @vscode/vsce package        # 生成 vscode-impor-4.2.0.vsix
code --install-extension vscode-impor-4.2.0.vsix
```

发布到 VS Code 市场需要 [Publisher 账号](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
与 Personal Access Token（PAT）。完整步骤见同目录 `PUBLISH.md`。

```powershell
$env:VSCE_PAT = "<你的 PAT>"           # 不要写进仓库
npx --yes @vscode/vsce publish         # 按 package.json version 发布
```

市场包必须包含 `README.md`、`LICENSE`、`CHANGELOG.md` 与 128×128 `icon.png`；本目录已全部就位。
`vsce ls` 可预览实际打包文件，`vsce package --out dist/xxx.vsix` 可指定输出路径。

## 功能

| 能力 | 说明 |
|---|---|
| 语法高亮 | TextMate 语法：关键字/内置函数/字符串（含 `r""` 原始串、三引号）/注释/数字/运算符 |
| 语法诊断 | 打开/编辑 .impor 文件时实时报告语法错误与未定义变量 |
| 跳转定义 | 光标置于变量/函数/类上 → F12（或右键“转到定义”） |
| 悬停提示 | 悬停显示符号说明（变量/函数签名/类/导入） |
| 文档符号 | 大纲视图列出顶层 函数/类/变量/异常/导入 |
| 运行 | 命令面板 “Impor: 运行当前脚本” |
| 格式化 | 命令面板 “Impor: 格式化当前脚本”；保存时格式化由 LSP `textDocument/formatting` 提供 |

## LSP 服务器

扩展通过 `python -m impor lsp`（stdio JSON-RPC）连接语言服务器；也可单独在终端启动：

```powershell
python -m impor lsp
```

服务器能力：`textDocumentSync`、`publishDiagnostics`、`definition`、`hover`、`documentSymbol`、`completion`、`rename`、`codeAction`、`formatting`、`rangeFormatting`；诊断基于 impor 静态分析器（与 `impor lint` 同源），格式化与 `impor fmt` 同源。
