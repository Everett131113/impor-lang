# -*- coding: utf-8 -*-
"""Impor 编辑器拆分模块（3.6.7）。运行期共享 editor 模块全局名。"""
import os
import sys
import re
import json
import time
import math
import random
import shutil
import subprocess
import threading
import queue
import webbrowser
import difflib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .editor_theme import THEME, DARK_THEME, LIGHT_THEME, HIGH_CONTRAST_THEME
from .editor_widgets import round_rect, FlatButton, TabStrip, ActivityBar, Minimap, MiniScrollbar
from .pathsafe import rel_quiet


class EditorGroupsMixin:
    def group_panes(self, group="main"):
        return [p for p in self.panes if getattr(p, "group", "main") == group]

    def ensure_split_group(self):
        if self.split_frame is not None:
            return self.split_frame
        self.split_frame = tk.Frame(self.editor_split, bg=THEME["editor"])
        self.split_tabstrip = TabStrip(self.split_frame, self, group="split")
        self.split_tabstrip.pack(fill=tk.X)
        head = tk.Frame(self.split_frame, bg=THEME["panel_head"], height=24)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="编辑器组 2", bg=THEME["panel_head"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=10)
        FlatButton(head, "关闭组", command=self.toggle_split_view, width=56, height=20).pack(
            side=tk.RIGHT, padx=4, pady=2)
        self.split_pagehost = tk.Frame(self.split_frame, bg=THEME["editor"])
        self.split_pagehost.pack(fill=tk.BOTH, expand=True)
        self.editor_split.add(self.split_frame, width=460, stretch="always")
        return self.split_frame

    def current_pane(self):
        return self.active_pane if isinstance(self.active_pane, EditorPane) else None

    def select_pane(self, pane):
        group = getattr(pane, "group", "main") if pane is not None else "main"
        if pane is not None and group == "split":
            self.ensure_split_group()
        self.active_pane = pane
        for p in self.panes:
            if getattr(p, "group", "main") != group:
                continue
            try:
                if p is pane:
                    p.pack(fill=tk.BOTH, expand=True)
                else:
                    p.pack_forget()
            except Exception:
                pass
        if pane is not None:
            try:
                pane.focus_text()
            except Exception:
                pass
            # 测试失败行内联标记
            try:
                if getattr(pane, "path", None):
                    fails = getattr(self, "_test_fail_map", {}).get(os.path.abspath(pane.path))
                    if fails:
                        pane.test_fails = set(fails)
                        for ln in fails:
                            pane.text.tag_add("test_fail", f"{ln}.0", f"{ln}.end")
                        pane.text.tag_configure("test_fail", foreground=THEME["red"], underline=True)
                        pane.redraw_lineno()
            except Exception:
                pass
            try:
                payload = getattr(pane, "_lint_payload", None)
                if payload:
                    issues, error, offset = payload
                    self._apply_lint(pane, pane.text.get("1.0", "end-1c"),
                                    issues, error, offset)
            except Exception:
                pass
        try:
            self.tabstrip.redraw()
            if self.split_tabstrip is not None:
                self.split_tabstrip.redraw()
        except Exception:
            pass
        self.update_status()
        self._refresh_outline()

    def _refresh_outline(self):
        try:
            tree = self.outline_tree
            tree.delete(*tree.get_children())
            pane = self.current_pane()
            if not pane:
                return
            for i, line in enumerate(pane.text.get("1.0", "end-1c").split("\n"), 1):
                m = re.match(r"^(\s*)(func|def|class|var|const|let)\s+([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)", line)
                if m:
                    depth = len(m.group(1)) // 4
                    tree.insert("", tk.END, iid=str(i),
                                text="  " * depth + m.group(2) + "  " + m.group(3))
        except Exception:
            pass

    def _outline_jump(self, e=None):
        pane = self.current_pane()
        sel = self.outline_tree.selection()
        if pane and sel:
            line = int(sel[0])
            pane.text.mark_set("insert", f"{line}.0")
            pane.text.see("insert")
            pane.focus_text()

    def close_pane(self, pane):
        if pane is None or pane not in self.panes:
            return
        if pane._dirty:
            if not messagebox.askyesno("关闭标签", "当前文件有未保存的修改，仍要关闭吗？"):
                return
        group = getattr(pane, "group", "main")
        siblings = self.group_panes(group)
        idx = siblings.index(pane) if pane in siblings else 0
        was_active = pane is self.active_pane
        self.panes.remove(pane)
        pane.destroy()
        remain = self.group_panes(group)
        if not remain:
            if group == "split":
                self._destroy_split_group()
                main = self.group_panes("main")
                if main:
                    self.active_pane = None
                    self.select_pane(main[-1])
                elif not self.panes:
                    self.open_new_tab()
                return
            if not self.panes:
                self.active_pane = None
                self.open_new_tab()
                return
            self.active_pane = None
            self.select_pane(self.panes[-1])
            return
        if was_active:
            nxt = remain[idx] if idx < len(remain) else remain[-1]
            self.active_pane = None
            self.select_pane(nxt)
        else:
            self.tabstrip.redraw()
            if self.split_tabstrip is not None:
                self.split_tabstrip.redraw()
            self.update_status()

    def _destroy_split_group(self):
        try:
            for p in list(self.group_panes("split")):
                if p in self.panes:
                    self.panes.remove(p)
                p.destroy()
        except Exception:
            pass
        try:
            if self.split_frame is not None:
                self.editor_split.forget(self.split_frame)
                self.split_frame.destroy()
        except Exception:
            pass
        self.split_frame = None
        self.split_pane = None
        self.split_tabstrip = None
        self.split_pagehost = None

    def open_new_tab(self, content=None, title=None, path=None, force_full=False, group=None):
        # 4.7.3：带 path 且没给内容时，按磁盘真实字节打开并识别 BOM/换行。
        # 旧版只当空标签建，保存时会把 CRLF+BOM 文件静默改写成 LF+无 BOM。
        meta = {}
        if content is None and path:
            try:
                raw = open(path, "rb").read()
                bom = raw.startswith(b"\xef\xbb\xbf")
                crlf = raw.count(b"\r\n")
                meta = {"bom": bom,
                        "ends_nl": raw.endswith(b"\n"),
                        "newline": "CRLF" if (crlf and crlf * 2 >= raw.count(b"\n")) else "LF"}
                content = raw.decode("utf-8-sig" if bom else "utf-8")
            except Exception as exc:
                note_swallowed("editor.new_tab.read", exc)
                content = None
        if content is None:
            content = ""
        if group is None:
            group = getattr(self.active_pane, "group", "main") if self.active_pane is not None else "main"
        if group == "split":
            self.ensure_split_group()
            parent = self.split_pagehost
        else:
            parent = self.pagehost
        pane = EditorPane(parent, self, path=path, content=content)
        if meta:
            pane._encoding = "UTF-8 BOM" if meta["bom"] else "UTF-8"
            pane._newline = meta["newline"]
            pane._bom = meta["bom"]
            pane._ends_nl = meta.get("ends_nl", True)
        pane.group = group
        name = title or (os.path.basename(path) if path else "未命名")
        pane.tab_title = name
        self.panes.append(pane)
        self.select_pane(pane)
        return pane

    def open_virtual_tab(self, path, lines, start=0):
        """以分块编辑方式打开大文件；Tk 只保留当前窗口，行号用绝对行号。"""
        group = getattr(self.active_pane, "group", "main") if self.active_pane is not None else "main"
        try:
            from .editor_virtual import VirtualLargeFilePane
            parent = self.split_pagehost if group == "split" and self.split_pagehost else self.pagehost
            pane = VirtualLargeFilePane(parent, self, path, lines, start=start)
        except Exception as exc:
            messagebox.showerror("大文件打开失败", str(exc))
            return None
        pane.group = group
        pane.tab_title = os.path.basename(path)
        self.panes.append(pane)
        self.select_pane(pane)
        return pane

    def convert_virtual_to_full(self, pane):
        if not getattr(pane, "_virtual_full_lines", None):
            return
        if not messagebox.askyesno(
                "转为完整编辑",
                "将把整个文件读入编辑器（大文件可能卡顿），并切换为完整编辑模式。继续？",
                parent=self.root):
            return
        try:
            idx = self.panes.index(pane)
        except ValueError:
            return
        path = pane.path
        try:
            content = pane.get_content() if getattr(pane, "get_content", None) else pane.virtual_text()
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        new = EditorPane(self.split_pagehost if getattr(pane, "group", "main") == "split" and self.split_pagehost else self.pagehost,
                         self, path=path, content=content)
        new.group = getattr(pane, "group", "main")
        new.tab_title = pane.tab_title
        new._encoding = pane._encoding
        new._newline = pane._newline
        new._bom = pane._bom
        new._large_file = True
        self.panes[idx] = new
        if self.active_pane is pane:
            self.active_pane = None
        pane.destroy()
        self.select_pane(new)
        self.toast("已转为完整编辑（大文件模式）", "warn")

    def mark_dirty(self, pane):
        self.tabstrip.redraw()

    def close_current_tab(self):
        self.close_pane(self.current_pane())

    def open_path_in_split(self, path):
        self.ensure_split_group()
        try:
            raw = open(path, "r", encoding="utf-8", errors="replace").read()
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc))
            return None
        pane = self.open_new_tab(content=raw, title=os.path.basename(path),
                                 path=path, group="split")
        return pane

    def _move_tab(self, delta):
        if not self.panes or not self.active_pane:
            return
        try:
            i = self.panes.index(self.active_pane)
        except ValueError:
            return
        j = max(0, min(len(self.panes) - 1, i + delta))
        if i == j:
            return
        self.panes[i], self.panes[j] = self.panes[j], self.panes[i]
        try:
            self.tabstrip.redraw()
        except Exception:
            pass
        self.select_pane(self.active_pane)

    def _copy_pane_path(self, pane):
        if not pane or not pane.path:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(rel_quiet(pane.path, self.workspace))
        self.toast("已复制路径：" + os.path.basename(pane.path), "ok")

    def _close_tabs_right(self, target):
        if target not in self.panes:
            return
        right = self.panes[self.panes.index(target) + 1:]
        for pane in list(right):
            self.close_pane(pane)

