# RFC 0006：科学计数法字面量与 17 个新内建

状态：已接受（v5.2.0）

## 动机

1. 词法此前不支持科学计数法，写 `0.001` 这类数值只能用小数，参数表达不便。
2. 字符串/数组/数值常用操作存在缺口（切串、插入、排序、取整、函数式组合等）。

## 变更（全部为新增，无删除、无改义）

- 词法：数字支持 `e/E` 指数与正负号，如 `1e3`、`2.5E-4`；`1e` 不构成指数时保持旧行为。
- 新内建 17 个：
  - 字符串：`strsplit` `strcount` `strstart` `strend`
  - 数组：`arrinsert` `arrremove` `arrsort` `arrcount`
  - 数值：`round` `floor` `ceil`
  - 逻辑/函数式/组合：`any` `all` `map` `filter` `zip` `enumerate`

## 兼容性

- 关键字数量不变（仍 55），无新增语法糖。
- 新内建在解释器与转译 Python 产物可用；C 后端暂不支持，已列入 `UNSUPPORTED_BUILTINS_HINT` 给出友好报错。
- 内建总数 124 → 141。

## 测试

`tests/test_v520.py` 20 项；编辑器调试探针 `tools/probe_debug.py` PROBE OK。
