# -*- coding: utf-8 -*-
"""跨盘安全的路径显示（4.7.3）。

`os.path.relpath` 在两个盘符之间抛 ValueError（"path is on mount 'C:', start on
mount 'D:'"）。编辑器用它来把路径缩短成面包屑/标签名，一旦用户把工作区放在
D:、打开的文件在 C:（临时文件、桌面文件），每次刷新标签都会抛异常——
轻则标签名不刷新，重则保存流程被打断。

这里统一兜底：跨盘时退回绝对路径，绝不抛。
"""
import os


def rel_quiet(path, base, fallback=None):
    """等同 os.path.relpath，但跨盘/非法参数时退化为绝对路径而不抛异常。"""
    if not path:
        return "" if fallback is None else fallback
    try:
        return os.path.relpath(str(path), str(base))
    except (ValueError, TypeError, OSError):
        try:
            return os.path.abspath(str(path))
        except Exception:
            return str(path)


def rel_slash(path, base):
    """同 rel_quiet，并把分隔符统一成 /（用于界面展示与 git 路径）。"""
    return rel_quiet(path, base).replace("\\", "/")
