# Impor Atlas · 700 个 Impor 独有微库

3.0.0 的 `std.atlas` 扩展包，共 **700 个独立模块**，全部是纯 Impor 源码，按需加载，不进入 `use std` 的默认注入链。

## 结构

- 10 个族：`num` / `seq` / `txt` / `data` / `geom` / `time` / `codec` / `sim` / `teach` / `stat`
- 每族 70 个模块：7 套 API × 10 个变体
- 每个模块 1 个函数、1 个 `export`，无跨模块依赖
- 完整目录：`impor/std/atlas/CATALOG.md`

## 用法

```impor
import std.atlas as atlas
import std.atlas.num_multiple_01 as m

print(atlas.count())          // 700
print(atlas.has("txt_prefix_04"))
print(m.multiple_01(10))      // true
```

`use std` 只会注入 `atlas` 目录入口，不会一次性加载 700 个模块：

```impor
use std
print(atlas.count())
```

## 验证

- `tests/atlas_700_smoke.impor`：700 个模块全部 import，并各调用一次
- 运行：`impor run tests/atlas_700_smoke.impor`
- 预期：`atlas-smoke 700 700`

## 边界

这是“微库矩阵”，不是 700 个重型框架：每个库聚焦一个可复用的小能力，适合组合、教学和快速原型；不包含网络、数据库、GUI 等需要宿主能力的大型库。
