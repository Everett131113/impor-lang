# Impor (I++) for VS Code

## 3.11.0

- 与 Impor 3.11.0 对齐；扩展版本号同步为 3.11.0。
- 语言侧：C ABI 六格矩阵（win32 / win64 / linux64 / linux-arm64 / macos-arm64 / macos-x64），每格校验机器类型与四个导出符号。
- 语言侧：新增 `impor bench-spec`，输出 exe 体积 / 冷启动 / 常驻内存 / 构建耗时。
- 扩展侧功能未改：语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化保持 3.8.0 能力。

## 3.9.0

- 与 Impor 3.9.0 对齐；扩展版本号同步为 3.9.0。
- 语言侧：新增 `impor build --lib --c-source`（64 位 C 源码包）；C 后端支持 `ffi_open` / `ffi_call`；允许清单接受 `,` 与 `;`。
- 语言侧：`--c-source` 生成的 `lib.c` 不含 `main`；`impor run` 与 `impor build` 位宽不一致时抛 `FFIArchError`；`sys-check` 同时报告解释器位宽与产物位宽。
- 新增 GitHub Actions `c-abi-x64.yml`：MSVC x64 与 GCC x64 各编译一次并校验四个导出符号。
- 扩展侧功能未改：语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化保持 3.8.0 能力。

## 3.8.4

- 与 Impor 3.8.4 对齐；扩展版本号同步为 3.8.4。
- 语言侧：`yield` / `with` / 默认参数 / 变参 / 推导式在编译期明确拒绝；Python C 后端补 `try/catch/finally`。
- 扩展侧功能未改：语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化保持 3.8.0 能力。

## 3.8.2

- 与 Impor 3.8.2 对齐；扩展版本号同步为 3.8.2。
- 语言侧新增 `export func` 与 `impor build --lib`（`x.dll + x.h + x.def`），C 后端成为唯一发布路径。
- 扩展侧功能未改：语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化保持 3.8.0 能力。

## 3.8.1

- 与 Impor 3.8.1 对齐；扩展版本号同步为 3.8.1。
- 语言工具链修复：编译产物可在任意目录运行，类型注解在 `run/compile/build` 前检查，`imporc` 的 `-o` 真正生效且错误分支返回 2。
- 扩展侧功能未改：语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化保持 3.8.0 能力。

## 3.8.0

- 与 Impor 3.8.0 对齐；扩展继续提供语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化。
- 语言侧未改语法；编辑器侧新增多根工作区、workspace trust、跨根搜索与布局/会话恢复，并提供独立 PyQt6 内核 3.0 预览入口。

## 3.7.8

- 与 Impor 3.7.8 对齐；扩展继续提供语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化。
- 语言侧新增 `defer` 延迟清理（解释器与 Python 转译），C 后端支持管道 `|>`；LSP 协议未改。

## 3.7.7

- 与 Impor 3.7.7 对齐；扩展继续提供语法高亮与 LSP 诊断/定义/悬停/文档符号/格式化。
- 编辑器侧新增安全跨文件替换（可选纳入未保存缓冲区、二次确认、事务回滚与替换后重载）、递减 `for` 与 AI 输出 token 预算；LSP 协议未改。

## 3.6.6

- 版本与 Impor 3.6.6 对齐；扩展侧继续提供语法高亮、LSP 诊断/定义/悬停/文档符号/格式化。
- 编辑器新增多编辑器组、行内诊断 + F8、Git 变更审查台、可配置键位、测试耗时/覆盖列。

## 4.2.0

- 版本与 Impor 4.2.0 对齐：语言侧新增字典 / 切片三端支持、`expect()` 断言与 `impor test` 临时目录隔离。
- 重新打包 `vscode-impor-4.2.0.vsix`（322 文件）。

## 4.1.0

- 版本与 Impor 4.1.0 对齐：LSP 继续提供诊断、定义跳转、悬停、文档符号与格式化。
- 语言侧新增 C 后端模块导入、`type_of`、`flatten` 与 imporc 嵌套 `switch`；扩展无需改协议即可受益。
- 重新打包 `vscode-impor-4.1.0.vsix`。

## 3.10.0

- 语法高亮：`.impor` 关键字、类型、函数、注释、字符串与数字。
- LSP：语法诊断、跳转到定义、悬停提示、文档符号、全文/选区格式化。
- 运行脚本 `Impor: 运行当前脚本`（调用 `python -m impor`）。
- 格式化命令 `Impor: 格式化当前脚本`（走 LSP `textDocument/formatting`）。
- 扩展图标与发布元数据（MIT LICENSE、`icon.png`、`.vscodeignore`）。

## 3.9.0

- LSP 增加 `documentFormattingProvider` 与 `documentRangeFormattingProvider`。
- 语言服务器 stderr 由客户端消费，避免静默失败。

## 3.5.0

- 首次提供语法高亮、诊断与运行命令。
