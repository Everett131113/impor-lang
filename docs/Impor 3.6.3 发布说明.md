# Impor (I++) 3.6.3 发布说明

发布日期：2026-11-14
版本：3.6.3（语法查询版）

## 本次新增

### 1. 语法查询

每个关键字/内建函数都给出四块内容：

- 意思：它做什么。
- 使用格式：标准写法。
- 程序例子：可以直接运行的最小代码。
- 例子说明：逐行解释输出是什么、为什么。

覆盖范围：

- `KEYWORDS` 全部 73 个关键字，一个不漏。
- 软关键字 `match`、`enum`、`struct`、`unless`、`fn`、`repeat`。
- 常用内建函数，包括 `print`、`input`、`len`、`range`、`type`、`str`、`int`、文件、JSON、正则、内存、FFI 等。
- 预留关键字（`with`、`yield`、`defer`、`macro`、`template`）明确标注“当前无可用语法”，不伪造可执行例子。

### 2. 三个入口

命令行：

```powershell
impor syntax print
impor syntax if
impor syntax --search 条件
impor syntax --json print
impor syntax
```

编辑器：

- 按 `F1`，或在命令面板搜索“语法查询”。
- 左侧关键字/内建列表，右侧显示意思、格式、例子、例子说明。
- 输入框支持按名字、意思、例子搜索；可一键复制例子。

REPL：

```text
help(print)
help(if)
```

`help(名字)` 优先显示完整语法条目。

## print 的口径

```text
print（内建函数）

意思
打印：把括号里的内容输出到屏幕，末尾换行。

格式
print("xxx")  /  print(变量)

例子
var a = 1
print("1")
print(a)
print("a")

例子说明
print("1") 括号里是字符串 1，输出 1；
print(a) 括号里是变量 a，输出 a 保存的值 1；
print("a") 括号里是字符串 a，输出 a。
引号只表示字符串，不会把引号一起输出。
```

实际运行：

```text
1
1
a
```

## 验证结果

- 语法数据：`KEYWORDS 73`、`ENTRIES 122`、`missing []`、`EXAMPLES_BAD 0`。
- `impor syntax print` / `--search 条件` / `--json print`：退出码 0。
- `impor release-check`：3 项通过，0 项失败。
- 编辑器语法面板：Toplevel 创建、标题“语法查询”、列表与详情渲染通过。
- 自举编译器 `imporc.exe` 内嵌版本：3.6.3。
- 包内已包含 `impor/syntax_ref.py`、`docs/语法查询.md`、`impor/editor.py`、`impor/cli.py`。

## 文件

- ZIP：`Impor-3.6.3-Windows-x64.zip`，1,155 项，4,853,643 B
  SHA256：`2F7DFECE20C158C6C97F9FC471143FFADE8E130CB004323F76C10C260373E5F3`
- VSIX：`vscode-impor-3.6.3.vsix`，482,219 B
  SHA256：`B2D6FD9C7E001C26E8E5B00B4646DB9684B63A59237E1513AF4AF8BA02D043C5`
- 解压即用：`Impor(I++) 3.6.3\`

## 边界

- 语法查询是查询与教学功能，不改变语言语义。
- 预留关键字仍不可执行；查询结果会明确提示。
- 中文别名（如果/否则/打印等）默认不启用，未作为单独条目列出。
- 3.6.2 的系统编程结论仍为 `NO`；本次未改变系统编程能力。
