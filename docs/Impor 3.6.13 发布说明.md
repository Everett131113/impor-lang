# Impor 3.6.13 发布说明

发布日期：2026-11-15
版本类型：路径健壮性修复版（语言语法不变）

## 这一版解决什么

3.6.12 在仓库路径（全 ASCII 目录）跑默认 suite 是 566 tests OK，但换到用户桌面安装路径
`C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.12\` 后，有 3 条默认 suite 用例稳定失败。
根因不是测试环境特殊，而是装到任何含空格、括号或中文的目录都会触发的真实 bug：

1. **子进程命令没有完整引用路径。** 空格和括号把 `cmd` 命令行拆断，模块构建直接找不到文件。
2. **C 运行时用 ANSI `fopen` 读 UTF-8 路径。** 中文目录被按 GBK 代码页解释，`fileread` 打不开源码。
3. **`system` 用 `CreateProcessA` 启动 tcc。** 命令行里的 UTF-8 中文路径被当作 ANSI，tcc 无法在中文目录写出 exe。

## 修复内容

### imporc 与测试调用

- `imporc` 调 tcc 时给输出路径加双引号，`run` 模式执行产物路径同样加引号。
- 测试写 `imporc_args.txt` 时统一用双引号包裹脚本/输出路径。
- `cmd /c` 调用 `imporc.cmd` 改为整条命令双引号包裹，避免空格与括号截断。

### C 运行时 Unicode 路径

- `fileread` / `filewrite` / `filewritebytes` 在 Windows 下先把 UTF-8 路径转 UTF-16，再走 `_wfopen`；非 Windows 保持 `fopen`。
- `system` 在 Windows 下先把 UTF-8 命令行转 UTF-16，再走 `CreateProcessW`；非 Windows 保持 `system`。
- 这两处改动覆盖了解释器 C 后端、自举 `imporc.exe` 以及所有由它们生成的 exe。

## 验证结果

- 仓库路径默认 suite：`Ran 566 tests in 140.922s / OK`。
- 桌面路径 `C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.13\`：
  - 目标 3 条：`Ran 3 tests in 3.300s / OK`。
  - 完整默认 suite：`Ran 566 tests in 123.649s / OK`。
- 目标用例：
  - `test_v42_closure.test_imporc_backend`
  - `test_v42_module.test_imporc_module_value`
  - `test_v43_unify.test_from_package_nested`

## 兼容性说明

- 3.6.13 不改变语言语法，不新增/删除关键字，不调整任何已有诊断文案。
- `LEGACY_IDS` 仍是 6 条，没有为这 3 条失败新增任何跳过。
- 路径引号与 Unicode 修复对 ASCII 路径同样生效，不依赖系统区域设置。

## 安装包

- `Impor-3.6.13-Windows-x64.zip`
- `vscode-impor-3.6.13.vsix`
- 桌面目录：`C:\Users\41482\Desktop\总Impor文件\Impor(I++) 3.6.13`

哈希、条目数与大小见仓库根 `_build3613.json`。
