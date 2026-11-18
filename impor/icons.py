# -*- coding: utf-8 -*-
"""应用图标装载：Windows 用多尺寸 .ico，其他平台退回 PNG。"""
import os
import sys
import tkinter as tk

_ICON_DIR_CACHE = None


def _icon_dir():
    global _ICON_DIR_CACHE
    if _ICON_DIR_CACHE:
        return _ICON_DIR_CACHE
    candidates = []
    env = os.environ.get("IMPOR_ICON_DIR")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "assets", "icons"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "impor", "assets", "icons"))
        candidates.append(os.path.join(meipass, "assets", "icons"))
    for path in candidates:
        if os.path.isdir(path):
            _ICON_DIR_CACHE = path
            return path
    return candidates[-1]


def icon_paths():
    base = _icon_dir()
    return os.path.join(base, "impor.ico"), os.path.join(base, "impor-256.png")


def apply_window_icon(win, root=False):
    """给窗口设置 Impor 图标；失败静默，不影响启动。"""
    ico, png = icon_paths()
    ok = False
    if sys.platform == "win32" and os.path.exists(ico):
        try:
            win.iconbitmap(ico)          # 必须显式设置当前窗口
            ok = True
        except Exception:
            pass
        if root:
            try:
                win.iconbitmap(default=ico)   # 同时设为后续 Toplevel 的默认
            except Exception:
                pass
    try:
        if os.path.exists(png):
            photo = tk.PhotoImage(file=png)
            win.iconphoto(False, photo)
            win._impor_icon_photo = photo   # 防止被 GC
            ok = True
    except Exception:
        pass
    return ok
