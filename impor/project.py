# -*- coding: utf-8 -*-
"""Impor (I++) 项目配置：.imporproj。

一个项目可以声明多个源码根、忽略目录、搜索路径和格式化规则。
编辑器、LSP、DAP 都从这里读取同一份配置。
"""
import json
import os

CONFIG_NAME = ".imporproj"


def find_project(start):
    cur = os.path.abspath(start or os.getcwd())
    while True:
        p = os.path.join(cur, CONFIG_NAME)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def load_project(start, fallback=None):
    """返回规范化后的项目配置；找不到 .imporproj 时把 fallback 当唯一根。"""
    path = find_project(start)
    data = {}
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    base = os.path.dirname(path) if path else os.path.abspath(fallback or start or os.getcwd())
    roots = data.get("roots") or ["."]
    roots = [os.path.normpath(r if os.path.isabs(r) else os.path.join(base, r)) for r in roots]
    config = {
        "path": path,
        "base": base,
        "roots": roots,
        "ignore": list(data.get("ignore") or []),
        "search_paths": list(data.get("search_paths") or []),
        "format": dict(data.get("format") or {}),
    }
    return config


def save_project(base, roots=None, ignore=None, search_paths=None, fmt=None):
    p = os.path.join(base, CONFIG_NAME)
    data = {
        "roots": roots or ["."],
        "ignore": ignore or [],
        "search_paths": search_paths or [],
        "format": fmt or {"indent": 4, "line_length": 100},
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p
