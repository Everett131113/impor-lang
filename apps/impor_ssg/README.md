# Impor SSG（静态站点生成器）

用 Impor 把 `content/*.md` + `templates/layout.html` 渲染成 `_site/*.html`。
只用 `std.path` / `std.fs` 和基础字符串内建，解释器与 C 后端都能跑。

## 这个项目证明什么

- 读文件 → 解析 Markdown → 套模板 → 写文件，完整闭环
- 同一份源码既能 `python Impor.py run`，也能 `impor build` 成脱离 Python 的 exe
- 生成自己的文档站，而不是只写 demo

## 跑起来

在仓库根目录：

```powershell
python Impor.py run apps/impor_ssg/ssg.impor
```

不用 Python：

```powershell
apps\impor_ssg\ssg.exe
```

输出在 `apps/impor_ssg/_site/`：`index.html`、`errors.html`、`stdlib.html`、`compat.html`。

重建 exe：

```powershell
python Impor.py build apps/impor_ssg/ssg.impor -o apps/impor_ssg/ssg.exe --no-cache
```

## 输入

- `content/*.md`：一级标题作为页面标题，`##` / `#` / `-` / 代码块会被转成基础 HTML
- `templates/layout.html`：包含 `{{title}}` / `{{nav}}` / `{{body}}` 三个占位符

## 实测

- 解释器：`rc=0`，生成 4 页
- C 后端 exe：`156 KB` 左右，脱离 Python 运行 `rc=0`，4 页全部生成
- `tools/verify_release.py --check app_ssg` 会自动构建并断言“4 页”

## 边界

- Markdown 只支持标题、列表、段落、围栏代码块，不支持表格、链接语法、引用。
- 逐字符扫描实现，适合中小文档；更大的站点建议后续换 `strlines` + `strreplace` 批量处理。
- 输出目录固定为 `apps/impor_ssg/_site`，不做增量构建。
