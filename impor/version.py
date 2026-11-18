# -*- coding: utf-8 -*-
"""版本号读取：仓库根 VERSION 为唯一真源，构建/冻结时允许回退基线。"""
import os

_BASE = "3.6.17"


def _read_version():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(os.path.dirname(here), "VERSION"),
                 os.path.join(here, "VERSION")):
        try:
            with open(path, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            continue
    return _BASE


__version__ = _read_version()
