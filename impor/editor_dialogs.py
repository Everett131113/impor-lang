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

class CommandPalette(tk.Toplevel):
    _CAT_ORDER = ("最近/推荐", "文件/项目", "编辑", "导航/符号", "运行/调试",
                  "测试/覆盖率", "Git", "工具/帮助", "其他")

    def __init__(self, app, commands):
        super().__init__(app.root, bg=THEME["panel"])
        self.app = app
        self.commands = commands
        self.filtered = list(commands)
        self._conflicts = {}
        for c in commands:
            hint = (c[1] or "").strip()
            if hint:
                self._conflicts.setdefault(hint, []).append(c[0])
        self._recent = list(getattr(app, "_cmd_recent", []))
        self._context = self._context_titles()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        w, h = 620, 520
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))
        self.configure(highlightbackground=THEME["accent_dim"], highlightthickness=1)

        wrap = tk.Frame(self, bg=THEME["panel_head"], padx=12, pady=12)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.entry = tk.Entry(wrap, bg=THEME["panel_alt"], fg=THEME["fg"],
                              insertbackground=THEME["accent"], relief="flat",
                              font=("Microsoft YaHei UI", 11))
        self.entry.pack(fill=tk.X, ipady=8)
        self.entry.insert(0, "> ")
        list_wrap = tk.Frame(wrap, bg=THEME["panel_head"])
        list_wrap.pack(fill=tk.BOTH, expand=True, pady=(8, 6))
        self.listbox = tk.Listbox(
            list_wrap, bg=THEME["panel_head"], fg=THEME["fg"], selectbackground=THEME["accent_dim"],
            selectforeground=THEME["fg_bright"], relief="flat", bd=0,
            highlightthickness=0, font=("Microsoft YaHei UI", 10), activestyle="none")
        scroll = tk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(wrap, text="↑ ↓ 选择  Enter 执行  Esc 关闭（列表可滚动）",
                 bg=THEME["panel_head"], fg=THEME["muted_dim"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        self._render()
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Down>", self._down)
        self.entry.bind("<Up>", self._up)
        self.entry.bind("<Return>", self._run)
        self.entry.bind("<Escape>", lambda e: self.destroy())
        self.listbox.bind("<Double-Button-1>", self._run)
        self.entry.focus_set()
        self.grab_set()

    def _query(self):
        return self.entry.get().lstrip("> ").strip().lower()

    def _category(self, title):
        if any(k in title for k in ("Git", "blame", "冲突", "暂存", "hunk")):
            return "Git"
        if any(k in title for k in ("测试", "覆盖率", "expect")):
            return "测试/覆盖率"
        if any(k in title for k in ("运行", "构建", "调试", "断点", "日志点", "监视", "内存", "沙盒")):
            return "运行/调试"
        if any(k in title for k in ("打开", "保存", "新建", "关闭", "文件", "项目", "工作区", "编码",
                                    "另存", "磁盘", "移动/重命名文件")):
            return "文件/项目"
        if any(k in title for k in ("查找", "搜索", "跳转", "符号", "引用", "调用", "大纲", "折叠",
                                    "后退", "前进", "焦点", "标签", "导航", "行")):
            return "导航/符号"
        if any(k in title for k in ("替换", "编辑", "代码", "补全", "格式", "重命名", "提取", "片段",
                                    "括号", "注释", "行尾", "撤销")):
            return "编辑"
        if any(k in title for k in ("插件", "主题", "设置", "键位", "安全", "更新", "关于", "帮助",
                                    "屏幕", "UIA", "终端", "包管理", "示例", "语法")):
            return "工具/帮助"
        return "其他"

    def _context_titles(self):
        """按当前编辑器状态推荐命令；只影响排序，不隐藏任何命令。"""
        titles = []
        try:
            pane = self.app.current_pane()
            has_path = bool(pane and getattr(pane, "path", ""))
            has_sel = bool(pane and pane.text.tag_ranges("sel"))
            if getattr(self.app, "_debug_mode", False):
                titles += ["切换调试", "运行到光标", "运行配置", "设置日志点"]
            if has_sel:
                titles += ["运行选中代码", "解释选中代码", "删除当前行/选区"]
            if has_path:
                titles += ["保存", "查找", "跳转定义", "重命名符号", "查找引用"]
            else:
                titles += ["打开文件", "新建标签", "快速打开文件"]
            if hasattr(self.app, "open_git_panel"):
                titles += ["Git 面板", "Git 当前行 blame"]
        except Exception:
            pass
        return set(titles)

    def _conflict_note(self, title, hint):
        hint = (hint or "").strip()
        if not hint:
            return ""
        owners = self._conflicts.get(hint) or []
        if len(owners) > 1 and title in owners:
            others = [t for t in owners if t != title]
            if others:
                return hint + "  ⚠ 与「" + others[0] + "」冲突"
        return hint

    def _render(self):
        self.listbox.delete(0, tk.END)
        q = self._query()
        recent = self._recent

        def score(c):
            title, hint = c[0], (c[1] or "")
            t = title.lower()
            h = hint.lower()
            cat = self._category(title)
            cat_idx = self._CAT_ORDER.index(cat) if cat in self._CAT_ORDER else 99
            if not q:
                if title in self._context:
                    bucket = 0
                elif title in recent:
                    bucket = 1
                else:
                    bucket = 2
                return (bucket, cat_idx,
                        recent.index(title) if title in recent else 99, title)
            if q in t:
                return (0, cat_idx, t.index(q), title)
            if q in h:
                return (1, cat_idx, 0, title)
            pos, gaps = -1, 0
            for ch in q:
                p = t.find(ch, pos + 1)
                if p < 0:
                    return None
                gaps += p - pos - 1
                pos = p
            return (2, cat_idx, gaps, title)

        pairs = []
        for c in self.commands:
            s = score(c)
            if s is not None:
                pairs.append((s, c))
        pairs.sort(key=lambda t: t[0])
        self.filtered = [c for _s, c in pairs]
        last_cat = None
        for title, hint, _cb in self.filtered:
            cat = "最近/推荐" if (not q and (title in self._context or title in recent)) \
                else self._category(title)
            prefix = ("[" + cat + "] ") if not q else ""
            note = self._conflict_note(title, hint)
            label = prefix + "  " + title + ("        " + note if note else "")
            self.listbox.insert(tk.END, label)
        if self.filtered:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)
            self.listbox.activate(0)

    def _on_key(self, e=None):
        if e and e.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self._render()

    def _down(self, e=None):
        if not self.filtered:
            return
        cur = self.listbox.curselection()
        i = (cur[0] + 1) if cur else 0
        i = min(i, len(self.filtered) - 1)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(i)
        self.listbox.activate(i)
        self.listbox.see(i)
        return "break"

    def _up(self, e=None):
        if not self.filtered:
            return
        cur = self.listbox.curselection()
        i = (cur[0] - 1) if cur else 0
        i = max(0, i)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(i)
        self.listbox.activate(i)
        self.listbox.see(i)
        return "break"

    def _run(self, e=None):
        cur = self.listbox.curselection()
        if not cur or not self.filtered:
            self.destroy()
            return "break"
        title, _hint, cb = self.filtered[cur[0]]
        try:
            recent = self.app._cmd_recent
            if title in recent:
                recent.remove(title)
            recent.insert(0, title)
            del recent[24:]
            self.app._save_config()
        except Exception:
            pass
        self.destroy()
        try:
            cb()
        except Exception:
            pass
        return "break"

class Finder(tk.Toplevel):
    """文件快速打开 / 全项目搜索 / 符号大纲 共用的输入+结果面板。"""

    def __init__(self, app, title, items, on_pick, hint="输入以筛选"):
        super().__init__(app.root, bg=THEME["panel"])
        self.app, self.items, self.on_pick = app, items, on_pick
        self.title(title)
        self.transient(app.root)
        self.geometry("720x420")
        self.configure(bg=THEME["panel"])
        apply_window_icon(self)
        tk.Label(self, text=title, bg=THEME["panel"], fg=THEME["fg_bright"],
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(fill=tk.X, padx=14, pady=(12, 4))
        self.entry = tk.Entry(self, bg=THEME["editor"], fg=THEME["fg"], insertbackground=THEME["accent"],
                              relief="flat", font=(app.mono, 11))
        self.entry.pack(fill=tk.X, padx=14, ipady=6)
        self.hint = tk.Label(self, text=hint, bg=THEME["panel"], fg=THEME["muted"],
                             font=("Microsoft YaHei UI", 8), anchor="w")
        self.hint.pack(fill=tk.X, padx=14, pady=(3, 5))
        self.list = tk.Listbox(self, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                               selectbackground=THEME["accent_soft"], selectforeground=THEME["fg_bright"],
                               font=(app.mono, 10), activestyle="none")
        self.list.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        self.entry.bind("<KeyRelease>", self._refresh)
        self.entry.bind("<Return>", self._pick)
        self.entry.bind("<Down>", self._focus_list)
        self.list.bind("<Double-Button-1>", self._pick)
        self.list.bind("<Return>", self._pick)
        self._refresh()
        self.entry.focus_set()

    def _refresh(self, e=None):
        q = self.entry.get().strip().lower()
        self.list.delete(0, tk.END)
        self._shown = []
        for label, payload in self.items:
            if not q or q in label.lower():
                self.list.insert(tk.END, label)
                self._shown.append((label, payload))
                if len(self._shown) >= 120:
                    break
        self.hint.configure(text="%d 项 · 回车打开，双击结果" % len(self._shown))

    def _focus_list(self, e=None):
        if self.list.size():
            self.list.focus_set()
            self.list.selection_clear(0, tk.END)
            self.list.selection_set(0)
        return "break"

    def _pick(self, e=None):
        try:
            sel = self.list.curselection()[0] if self.list.curselection() else 0
            self.on_pick(self._shown[sel][1])
        except Exception:
            pass
        self.destroy()
        return "break"

class AboutDialog(tk.Toplevel):
    """深色自绘「关于」窗口，与主界面同一套视觉语言。"""

    def __init__(self, app):
        super().__init__(app.root, bg=THEME["panel_head"])
        self.app = app
        apply_window_icon(self)
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        w, h = 500, 276
        rx, ry = app.root.winfo_rootx(), app.root.winfo_rooty()
        rw, rh = app.root.winfo_width(), app.root.winfo_height()
        self.geometry("%dx%d+%d+%d" % (w, h, rx + max(0, (rw - w) // 2),
                                       ry + max(0, (rh - h) // 3)))
        self.configure(highlightbackground=THEME["border"], highlightthickness=1)
        self.bind("<Escape>", lambda e: self.destroy())

        head = tk.Frame(self, bg=THEME["titlebar"], height=60)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        logo = tk.Canvas(head, width=34, height=34, bg=THEME["titlebar"], highlightthickness=0)
        logo.pack(side=tk.LEFT, padx=(16, 12), pady=13)
        round_rect(logo, 1, 1, 33, 33, 9, fill=THEME["accent_soft"], outline=THEME["accent"])
        logo.create_text(17, 17, text="I+", fill=THEME["accent"], font=(app.mono, 13, "bold"))
        tk.Label(head, text="Impor (I++)", bg=THEME["titlebar"], fg=THEME["fg_bright"],
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(head, text=f"v{__version__}  ·  Code Studio", bg=THEME["titlebar"],
                 fg=THEME["muted"], font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=12)
        close = tk.Label(head, text="\u00d7", bg=THEME["titlebar"], fg=THEME["muted"],
                         font=("Microsoft YaHei UI", 13), cursor="hand2")
        close.pack(side=tk.RIGHT, padx=16)
        close.bind("<Button-1>", lambda e: self.destroy())

        body = tk.Frame(self, bg=THEME["panel_head"])
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(14, 6))
        for text, color in (
            ("真正的编程语言 · 缩进 + 大括号双语法 · 120 关键字（58 英 / 62 中）", THEME["fg"]),
            ("v3.6 人类友好诊断：四段式「位置 / 问题 / 为什么 / 修复」 · 上下文片段 · 终端彩色", THEME["muted"]),
            ("v3.5 语言宪法：唯一推荐写法表 · 语法糖冻结 · ## 文档注释进悬停与 impor doc", THEME["muted"]),
            ("v3.4 宽容语法（已降级为兼容层）：print 省括号 · ; 分隔 · do..until · unless · 1..N · 中文软别名", THEME["muted"]),
            ("解释器 · Python 转译 · 原生 C 后端 · 自举编译器（A→B→C→D 逐字节一致）", THEME["muted"]),
            ("自动内存管理：阈值 GC · 驻留池 · 托管释放 · 实时内存面板", THEME["muted"]),
            ("编辑器：自绘标签栏 · 彩虹括号 · 缩略图 · 命令面板 · 四联输出面板 · 启动过场", THEME["muted"]),
        ):
            tk.Label(body, text=text, bg=THEME["panel_head"], fg=color,
                     font=("Microsoft YaHei UI", 9), justify="left").pack(anchor="w", pady=2)

        foot = tk.Frame(self, bg=THEME["panel_head"])
        foot.pack(fill=tk.X, padx=18, pady=(0, 14))
        tk.Label(foot, text="MIT License  ·  Impor Team  ·  独立 exe 不依赖 Python",
                 bg=THEME["panel_head"], fg=THEME["muted_dim"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        FlatButton(foot, "关闭", command=self.destroy, width=54, height=24).pack(side=tk.RIGHT)

        head.bind("<Button-1>", self._start_move)
        head.bind("<B1-Motion>", self._on_move)
        self._drag = None
        self.focus_set()

    def _start_move(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _on_move(self, e):
        if not self._drag:
            return
        ox, oy, wx, wy = self._drag
        self.geometry("+%d+%d" % (wx + e.x_root - ox, wy + e.y_root - oy))

class ExampleBrowser(tk.Toplevel):
    """示例浏览器：分类 + 搜索 + 源码预览 + 运行/插入/打开。"""

    CATS = ("全部", "入门", "算法与数据结构", "字符串与字典", "面向对象与异常", "函数与综合", "内置示例")

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        apply_window_icon(self)
        self.title("示例浏览器 · Impor (I++)")
        self.transient(app.root)
        self.geometry("980x620")
        self.configure(bg=THEME["panel"])
        self.items = []
        for it in EXAMPLES:
            title, code = it[0], it[1]
            stem = it[2] if len(it) > 2 else title.replace(". ", "-")
            m = re.search(r"demo_(\d+)", stem)
            n = int(m.group(1)) if m else 0
            if n == 0:
                cat = "内置示例"
            elif n <= 8:
                cat = "入门"
            elif n <= 15:
                cat = "算法与数据结构"
            elif n <= 22:
                cat = "字符串与字典"
            elif n <= 29:
                cat = "面向对象与异常"
            else:
                cat = "函数与综合"
            self.items.append((title, code, stem, cat))
        top = tk.Frame(self, bg=THEME["panel_head"], height=38)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="示例浏览器", bg=THEME["panel_head"], fg=THEME["fg_bright"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT, padx=12)
        self.cat = tk.StringVar(value="全部")
        ttk.Combobox(top, values=self.CATS, textvariable=self.cat, width=14, state="readonly"
                     ).pack(side=tk.LEFT, padx=4, pady=6)
        self.cat.trace_add("write", lambda *a: self._refresh())
        self.q = tk.StringVar()
        ent = tk.Entry(top, textvariable=self.q, bg=THEME["editor"], fg=THEME["fg"],
                       insertbackground=THEME["fg"], bd=0, width=24)
        ent.pack(side=tk.LEFT, padx=6, pady=8, ipady=4)
        ent.bind("<KeyRelease>", lambda e: self._refresh())
        body = tk.Frame(self, bg=THEME["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        left = tk.Frame(body, bg=THEME["sidebar"], width=310)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        self.listbox = tk.Listbox(left, bg=THEME["sidebar"], fg=THEME["fg"],
                                  selectbackground=THEME["accent_dim"], selectforeground=THEME["fg_bright"],
                                  bd=0, highlightthickness=0, font=("Microsoft YaHei UI", 9))
        lsb = tk.Scrollbar(left, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=lsb.set)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        self.listbox.bind("<<ListboxSelect>>", self._show_selected)
        right = tk.Frame(body, bg=THEME["editor"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.desc = tk.Label(right, text="选择一个示例查看说明与源码", bg=THEME["editor"],
                             fg=THEME["muted"], anchor="w", font=("Microsoft YaHei UI", 9))
        self.desc.pack(fill=tk.X, padx=10, pady=(8, 4))
        self.preview = tk.Text(right, bg=THEME["editor"], fg=THEME["fg"], insertbackground=THEME["fg"],
                               bd=0, highlightthickness=0, wrap="none", font=(app.mono, 10),
                               padx=10, pady=6)
        psb = tk.Scrollbar(right, command=self.preview.yview)
        self.preview.configure(yscrollcommand=psb.set)
        psb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview.pack(fill=tk.BOTH, expand=True)
        self.preview.configure(state="disabled")
        bar = tk.Frame(right, bg=THEME["editor"])
        bar.pack(fill=tk.X, pady=8)
        for text, cmd in (("运行", self._run), ("插入当前文件", self._insert),
                          ("打开为新标签", self._open), ("关闭", self.destroy)):
            tk.Button(bar, text=text, command=cmd, bg=THEME["panel"], fg=THEME["fg"],
                      activebackground=THEME["accent_soft"], activeforeground=THEME["fg_bright"],
                      bd=0, padx=12, pady=4, font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=4)
        self._filtered = []
        self._refresh()

    def _refresh(self):
        q = self.q.get().strip().lower()
        cat = self.cat.get()
        self._filtered = [it for it in self.items
                          if (cat == "全部" or it[3] == cat)
                          and (not q or q in it[0].lower() or q in it[2].lower())]
        self.listbox.delete(0, tk.END)
        for it in self._filtered:
            self.listbox.insert(tk.END, f"{it[3]}  ·  {it[0]}")
        if self._filtered:
            self.listbox.selection_set(0)
            self._show_selected()

    def _current(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered):
            return None
        return self._filtered[sel[0]]

    def _show_selected(self, e=None):
        it = self._current()
        if not it:
            return
        self.desc.configure(text=f"{it[0]}  ·  {it[3]}  ·  {it[2]}.impor")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", it[1])
        self.preview.configure(state="disabled")

    def _run(self):
        it = self._current()
        if it:
            self.app.open_new_tab(content=it[1], title=it[2] + ".impor")
            self.app.run_code()

    def _insert(self):
        it = self._current()
        if not it:
            return
        pane = self.app.current_pane()
        if pane is None:
            self.app.open_new_tab(content=it[1], title=it[2] + ".impor")
        else:
            pane.text.insert("insert", it[1])
            pane.focus_text()

    def _open(self):
        it = self._current()
        if it:
            self.app.open_new_tab(content=it[1], title=it[2] + ".impor")

