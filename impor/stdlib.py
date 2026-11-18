# -*- coding: utf-8 -*-
"""
Impor (I++) 标准库。

import 语句解析顺序：
  1. important.xxx 旧式写法 → 直接映射到同名 Python 标准库模块
  2. 普通名称 → 先尝试 Python 标准库/第三方模块，再尝试 .impor 用户模块

通过 import 可直接使用的 Python 模块（白名单 + 通用回退）：
  math random time os json string sys re datetime collections itertools functools statistics
"""

# 显式白名单（常用模块）
STDLIB_WHITELIST = {
    "math", "random", "time", "os", "json", "string", "sys", "re",
    "datetime", "collections", "itertools", "functools", "statistics",
}


def is_stdlib(name):
    return name in STDLIB_WHITELIST
