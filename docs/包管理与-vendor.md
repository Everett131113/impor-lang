# 包管理与 vendor（装完即可 `import`）

本文只描述**已经用命令实测通过**的行为，不写计划。`VERSION` 仍为 4.0.0，本节内容属于未发布的 4.0.1 改动。

## 1. 三条命令的语义

| 场景 | 命令 | 结果 |
| --- | --- | --- |
| 在项目内安装 | `impor pkg install <名字或目录>` | 装进全局缓存 `~/.impor/pkg/<名字>`，同时拷一份到 `<项目>/lib/<名字>/`，并把搜索路径写进 `impor.toml` |
| 在项目内只要缓存 | `impor pkg install <…> --no-vendor` | 只装全局缓存，不改 `impor.toml`、不往 `lib/` 放副本 |
| 在没有 `impor.toml` 的目录安装 | `impor pkg install <…>` | 只装全局缓存，**不生成** `impor.toml` / `impor.lock`，打印"未检测到项目"提示 |

全局缓存的默认位置由 `impor/config.py` 的 `pkg_cache_dir()` 决定（`~/.impor/pkg`），可用环境变量覆盖。

## 2. 项目内安装长什么样

```
myproj/
├─ impor.toml        # [module_search_paths] paths = ["./lib", "./lib/pkgx"]
├─ impor.lock
├─ lib/
│  └─ pkgx/          # 包的副本（vendor）
│     ├─ __pkg__.impor   # 包形态；或 pkgx.impor（模块形态）
│     └─ core.impor
└─ main.impor
```

`main.impor` 里直接写：

```impor
import pkgx
print(pkgx.core.hi("X"))
```

不需要 `import_path(...)`，也不需要手工改 `impor.toml`——`pkg install` 已经把 `./lib` 补进去了。

## 3. 两种包形态都能解析

`impor/interpreter.py` 的模块解析对同一段名字依次尝试：

1. `<搜索根>/<parts...>/__pkg__.impor` —— 包形态，子模块是同级 `.impor` 文件；
2. `<搜索根>/<parts...>.impor` —— 单文件模块形态。

因此 `lib/pkgx/__pkg__.impor` 与 `lib/hello/hello.impor` 都能被 `import` 命中；后者必须把**包目录本身**（`./lib/<名字>`）作为搜索根，这正是 `pkg install` 会自动补 `./lib/<名字>` 的原因。

## 4. 全局缓存为什么现在也能直接 `import`

旧行为只有缓存根一个搜索路径，而缓存里放的是 `<名字>/<名字>.impor` 这种单文件模块布局，`<缓存根>/<名字>.impor` 并不存在，于是"装成功了却报 `E1015`"。

现在 `apply_config()` 在 append 缓存根之后，会再把缓存下**每一个包目录**逐个 append 进 `module_search_paths`，单文件模块布局和包布局都命中。

## 5. `impor.toml` 的编辑方式

- `[dependencies]` 增删依赖、`[module_search_paths]` 增删路径，都是**按行编辑**，保留注释、空行和原有顺序。
- 此前是整篇 `_write_toml` 重写，会把用户写的注释冲掉（本轮已修复，并有断言锁定"注释仍在"）。

## 6. 卸载

`impor pkg remove <名字>` 会同时删除：全局缓存里的 `<名字>/`、`<项目>/lib/<名字>/`（存在项目时）、以及 `impor.toml` 的 `[dependencies]` 条目。删除后同一份 `main.impor` 的 `import` 会回到 `E1015`，不会留下半可用状态。

## 7. 已知边界（未做）

- 没有 `impor pkg update` 语义上的"升级并同步 vendor"：`upgrade` 只动缓存，`<项目>/lib/` 里的副本需要重新 `install` 才会更新。
- `dist/mylib-1.0.0.zip` 这类 registry 包仍是 290 B 占位，`pkg publish/pack` 的产物没有和 vendor 行为做过联合验证。
- vendor 默认开启会把 `lib/<名字>/` 带进版本库；若不希望如此，用 `--no-vendor`，或把 `lib/` 写进 `.gitignore`。
