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


class PanelsMixin:
    def _build_bottom(self, parent):
        tk.Frame(parent, bg=THEME["border"], height=1).pack(fill=tk.X)
        head = tk.Frame(parent, bg=THEME["panel_head"], height=32)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        self.tabbar = {}
        self._tabbars = {}
        for key, label in (("out", "输出"), ("problems", "问题"), ("tests", "测试"), ("watch", "变量"), ("debug", "调试"), ("mem", "内存")):
            wrap = tk.Frame(head, bg=THEME["panel_head"])
            wrap.pack(side=tk.LEFT, fill=tk.Y)
            lbl = tk.Label(wrap, text=label, bg=THEME["panel_head"], fg=THEME["muted"],
                           font=("Microsoft YaHei UI", 9), padx=13, cursor="hand2")
            lbl.pack(side=tk.TOP, pady=(7, 0))
            mark = tk.Frame(wrap, bg=THEME["panel_head"], height=2)
            mark.pack(side=tk.BOTTOM, fill=tk.X)
            lbl.bind("<Button-1>", lambda e, k=key: self.show_bottom(k))
            mark.bind("<Button-1>", lambda e, k=key: self.show_bottom(k))
            self.tabbar[key] = lbl
            self._tabbars[key] = mark
        FlatButton(head, "控制台", command=self.run_in_console, width=64,
                   height=22).pack(side=tk.RIGHT, padx=(0, 6), pady=5)
        FlatButton(head, "独立窗口", command=self.open_out_window, width=72,
                   height=22).pack(side=tk.RIGHT, padx=(0, 6), pady=5)
        FlatButton(head, "清空", command=self.clear_output, width=54, height=22).pack(
            side=tk.RIGHT, padx=10, pady=5)
        tk.Frame(parent, bg=THEME["border_soft"], height=1).pack(fill=tk.X)

        self.taskbar = tk.Frame(parent, bg=THEME["panel_head"], height=30)
        self.taskbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.taskbar.pack_propagate(False)
        tk.Label(self.taskbar, text="$", bg=THEME["panel_head"], fg=THEME["accent"],
                 font=(self.mono, 10, "bold")).pack(side=tk.LEFT, padx=(12, 4))
        self.task_entry = tk.Entry(self.taskbar, bg=THEME["editor"], fg=THEME["fg"],
                                   insertbackground=THEME["accent"], relief="flat",
                                   font=(self.mono, 10))
        self.task_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=4)
        self.task_entry.bind("<Return>", self._task_run_entry)
        self.task_entry.bind("<Up>", self._term_hist_prev)
        self.task_entry.bind("<Down>", self._term_hist_next)
        self.task_entry.bind("<Control-c>", self._term_interrupt)
        self.term_cwd_label = tk.Label(self.taskbar, text=self._term_cwd_text(), bg=THEME["panel_head"],
                                       fg=THEME["muted_dim"], font=(self.mono, 8))
        self.term_cwd_label.pack(side=tk.RIGHT, padx=(0, 8))
        for label, kind in (("测试", "test"), ("构建", "build"), ("运行", "run")):
            tk.Button(self.taskbar, text=label, command=lambda k=kind: self._task_run(k),
                      bg=THEME["panel_alt"], fg=THEME["fg"], relief="flat",
                      padx=8, pady=1, font=("Microsoft YaHei UI", 8)).pack(
                side=tk.RIGHT, padx=(0, 4), pady=4)

        self.bottom_body = tk.Frame(parent, bg=THEME["editor"])
        self.bottom_body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.out = tk.Text(self.bottom_body, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                           bd=0, font=(self.mono, 10), padx=10, pady=8, wrap="word",
                           insertbackground=THEME["accent"])
        self.out.tag_config("info", foreground=THEME["fg"])
        self.out.tag_config("ok", foreground=THEME["green"])
        self.out.tag_config("err", foreground=THEME["red"])
        self.out.tag_config("warn", foreground=THEME["yellow"])
        self.out.tag_config("dim", foreground=THEME["muted"])

        self.problems = tk.Text(self.bottom_body, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                                bd=0, font=(self.mono, 10), padx=10, pady=8, wrap="word")
        self.problems.tag_config("err", foreground=THEME["red"])
        self.problems.tag_config("warn", foreground=THEME["yellow"])
        self.problems.tag_config("dim", foreground=THEME["muted"])
        self.problems.bind("<Double-Button-1>", self._problem_jump)
        self.problems.bind("<Button-3>", self._problem_menu)
        self.problems.bind("<Control-Return>", lambda e: self.explain_problem())

        self.test_wrap = tk.Frame(self.bottom_body, bg=THEME["editor"])
        tbar = tk.Frame(self.test_wrap, bg=THEME["panel_head"])
        tbar.pack(fill=tk.X, pady=(0, 4))
        FlatButton(tbar, "发现用例", command=self.discover_tests, width=74).pack(side=tk.LEFT, padx=(0, 4))
        FlatButton(tbar, "重跑失败", command=self.rerun_failed_tests, width=74).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_test_watch = FlatButton(tbar, "监听: 关", command=self.toggle_test_watch, width=72)
        self.btn_test_watch.pack(side=tk.LEFT, padx=(0, 4))
        self.test_summary = tk.Label(tbar, text="未运行", bg=THEME["panel_head"], fg=THEME["muted"],
                                     font=("Microsoft YaHei UI", 8))
        self.test_summary.pack(side=tk.RIGHT, padx=8)
        self.test_tree = ttk.Treeview(self.test_wrap, columns=("status", "time", "cov", "detail"),
                                      show="tree headings", height=5)
        self.test_tree.heading("#0", text="用例")
        self.test_tree.heading("status", text="结果")
        self.test_tree.heading("time", text="耗时")
        self.test_tree.heading("cov", text="覆盖")
        self.test_tree.heading("detail", text="详情")
        self.test_tree.column("#0", width=230)
        self.test_tree.column("status", width=62)
        self.test_tree.column("time", width=64)
        self.test_tree.column("cov", width=64)
        self.test_tree.column("detail", width=380)
        self.test_tree.pack(fill=tk.BOTH, expand=True)
        self.test_tree.bind("<Double-Button-1>", self._test_jump)

        self.watch_wrap = tk.Frame(self.bottom_body, bg=THEME["editor"])
        wbar = tk.Frame(self.watch_wrap, bg=THEME["panel_head"])
        wbar.pack(fill=tk.X, pady=(0, 4))
        self.watch_entry = tk.Entry(wbar, bg=THEME["editor"], fg=THEME["fg"],
                                    insertbackground=THEME["accent"], relief="flat",
                                    font=(self.mono, 10))
        self.watch_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)
        self.watch_entry.bind("<Return>", lambda e: self.add_watch())
        FlatButton(wbar, "添加监视", command=self.add_watch, width=80).pack(side=tk.LEFT, padx=4)
        FlatButton(wbar, "删除", command=self.remove_watch, width=54).pack(side=tk.LEFT)
        self.watch_tree = ttk.Treeview(self.watch_wrap, columns=("vtype", "val"),
                                       show="tree headings", height=5)
        self.watch_tree.heading("#0", text="变量名")
        self.watch_tree.heading("vtype", text="类型")
        self.watch_tree.heading("val", text="值")
        self.watch_tree.column("#0", width=190)
        self.watch_tree.column("vtype", width=90)
        self.watch_tree.column("val", width=420)
        self.watch_tree.pack(fill=tk.BOTH, expand=True)

        self.mem_text = tk.Text(self.bottom_body, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                                bd=0, font=(self.mono, 10), padx=10, pady=8, wrap="word")

        self.debug_frame = tk.Frame(self.bottom_body, bg=THEME["editor"])
        self._dbg_gate = tk.IntVar(self.root, 0)
        dbgbar = tk.Frame(self.debug_frame, bg=THEME["panel"])
        dbgbar.pack(fill=tk.X, pady=(0, 6))
        for label, mode in (("步入 (F11)", "into"), ("步过 (F10)", "over"),
                            ("步出 (Shift+F11)", "out"), ("继续 (Esc)", None)):
            tk.Button(dbgbar, text=label, command=lambda m=mode: self._dbg_act(m),
                      bg=THEME["panel_alt"], fg=THEME["fg"], relief="flat",
                      padx=10, pady=2).pack(side=tk.LEFT, padx=(0, 6))
        self._exc_pause = True
        self.btn_exc = tk.Button(dbgbar, text="异常暂停: 开", command=self._toggle_exc_pause,
                                 bg=THEME["panel_alt"], fg=THEME["fg"], relief="flat",
                                 padx=8, pady=2)
        self.btn_exc.pack(side=tk.RIGHT, padx=(6, 0))
        self.exc_filter_entry = tk.Entry(dbgbar, width=16, bg=THEME["editor"], fg=THEME["fg"],
                                         insertbackground=THEME["accent"], relief="flat",
                                         font=(self.mono, 9))
        self.exc_filter_entry.pack(side=tk.RIGHT, padx=(4, 6))
        self.exc_filter_entry.insert(0, getattr(self, "_exc_filter", ""))
        self.exc_filter_entry.bind("<KeyRelease>", self._update_exc_filter)
        tk.Label(dbgbar, text="异常过滤", bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.RIGHT)

        evalwrap = tk.Frame(self.debug_frame, bg=THEME["panel"])
        evalwrap.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        tk.Label(evalwrap, text="表达式", bg=THEME["panel"], fg=THEME["accent"],
                 font=(self.mono, 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        self.dbg_eval_entry = tk.Entry(evalwrap, bg=THEME["editor"], fg=THEME["fg"],
                                       insertbackground=THEME["accent"], relief="flat",
                                       font=(self.mono, 10))
        self.dbg_eval_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.dbg_eval_entry.bind("<Return>", self._dbg_eval)
        self.dbg_eval_entry.bind("<Up>", lambda e: self._dbg_history_move(-1))
        self.dbg_eval_entry.bind("<Down>", lambda e: self._dbg_history_move(1))
        self.dbg_eval_out = tk.Label(evalwrap, text="", bg=THEME["panel"], fg=THEME["muted"],
                                     font=(self.mono, 9), anchor="w")
        self.dbg_eval_out.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))

        dbgbody = tk.Frame(self.debug_frame, bg=THEME["editor"])
        dbgbody.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(dbgbody, bg=THEME["editor"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Label(left, text="调用栈", bg=THEME["editor"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.dbg_stack = ttk.Treeview(left, columns=("line", "func"), show="headings", height=5)
        self.dbg_stack.heading("line", text="行")
        self.dbg_stack.heading("func", text="函数")
        self.dbg_stack.column("line", width=60, stretch=False)
        self.dbg_stack.column("func", width=200)
        self.dbg_stack.pack(fill=tk.BOTH, expand=True)
        self.dbg_stack.bind("<<TreeviewSelect>>", self._dbg_select_frame)
        tk.Label(left, text="断点", bg=THEME["editor"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
        bpbar = tk.Frame(left, bg=THEME["editor"])
        bpbar.pack(fill=tk.X)
        FlatButton(bpbar, "条件", command=self._bp_edit_condition, width=48).pack(side=tk.LEFT, padx=(0, 3))
        FlatButton(bpbar, "次数", command=self._bp_edit_hits, width=48).pack(side=tk.LEFT, padx=(0, 3))
        FlatButton(bpbar, "日志", command=self._bp_edit_log, width=48).pack(side=tk.LEFT)
        self.bp_list = ttk.Treeview(left, columns=("info",), show="tree headings", height=3)
        self.bp_list.heading("#0", text="行")
        self.bp_list.heading("info", text="条件 / 日志")
        self.bp_list.column("#0", width=55, stretch=False)
        self.bp_list.column("info", width=190)
        self.bp_list.pack(fill=tk.X)
        self.bp_list.bind("<Double-Button-1>", self._bp_jump)
        self.bp_list.bind("<Delete>", self._bp_delete)
        right = tk.Frame(dbgbody, bg=THEME["editor"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        tk.Label(right, text="当前帧变量（双击可改变量值）", bg=THEME["editor"],
                 fg=THEME["fg"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.dbg_vars = ttk.Treeview(right, columns=("vtype", "val"),
                                     show="tree headings", height=5)
        self.dbg_vars.heading("#0", text="变量")
        self.dbg_vars.heading("vtype", text="类型")
        self.dbg_vars.heading("val", text="值")
        self.dbg_vars.column("#0", width=120)
        self.dbg_vars.column("vtype", width=80)
        self.dbg_vars.column("val", width=260)
        self.dbg_vars.pack(fill=tk.BOTH, expand=True)
        self.dbg_vars.bind("<Double-Button-1>", self._dbg_edit_var)

        self._bottom_widgets = {"out": self.out, "problems": self.problems,
                                "tests": self.test_wrap, "watch": self.watch_wrap,
                                "debug": self.debug_frame, "mem": self.mem_text}
        self._bottom_kind = "out"
        self.show_bottom("out")

    def show_bottom(self, key):
        if not self._bottom_ready:
            if getattr(self, "_building_bottom", False):
                return
            self._ensure_bottom()
        self._bottom_kind = key
        for k, w in self._bottom_widgets.items():
            if k == key:
                w.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
            else:
                w.pack_forget()
        for k, lbl in self.tabbar.items():
            active = k == key
            lbl.configure(fg=THEME["fg_bright"] if active else THEME["muted"],
                          font=("Microsoft YaHei UI", 9, "bold" if active else "normal"))
            self._tabbars[k].configure(bg=THEME["accent"] if active else THEME["panel_head"])
        if key == "mem":
            self._render_memory()

    def _build_status(self):
        bar = tk.Frame(self.root, bg=THEME["panel_head"], height=28)
        bar.grid(row=3, column=0, sticky="ew")
        bar.pack_propagate(False)
        self.status_bar = bar
        tk.Frame(bar, bg=THEME["border_soft"], height=1).pack(side=tk.TOP, fill=tk.X)

        badge = tk.Label(bar, text=" I++ ", bg=THEME["ok_badge"], fg=THEME["green"],
                         font=("Microsoft YaHei UI", 8, "bold"))
        badge.pack(side=tk.LEFT, padx=(12, 9), pady=4)
        self.badge = badge
        self.status_left = tk.Label(bar, text=f"Impor {__version__}", bg=THEME["panel_head"],
                                    fg=THEME["fg"], font=("Microsoft YaHei UI", 8))
        self.status_left.pack(side=tk.LEFT)
        self._status_sep(bar)

        self.status_file = tk.Label(bar, text="未命名", bg=THEME["panel_head"],
                                    fg=THEME["fg_bright"], font=("Microsoft YaHei UI", 8))
        self.status_file.pack(side=tk.LEFT)
        self._status_sep(bar)
        self.status_problems = tk.Label(bar, text="错误 0   警告 0", bg=THEME["panel_head"],
                                        fg=THEME["muted"], font=("Microsoft YaHei UI", 8))
        self.status_problems.pack(side=tk.LEFT)
        self._status_sep(bar)
        self.status_index = tk.Label(bar, text="索引 --", bg=THEME["panel_head"],
                                     fg=THEME["muted"], font=("Microsoft YaHei UI", 8))
        self.status_index.pack(side=tk.LEFT)
        self._status_sep(bar)
        self.status_trust = tk.Label(bar, text="受限模式", bg=THEME["panel_head"],
                                     fg=THEME["yellow"], font=("Microsoft YaHei UI", 8))
        self.status_trust.pack(side=tk.LEFT)
        self._status_sep(bar)

        self.status_mem = tk.Label(bar, text="内存 --", bg=THEME["panel_head"], fg=THEME["muted"],
                                   font=("Microsoft YaHei UI", 8))
        self.status_mem.pack(side=tk.RIGHT, padx=(0, 12))
        self._status_sep(bar, right=True)
        self.status_right = tk.Label(bar, text="Ln 1, Col 1", bg=THEME["panel_head"],
                                     fg=THEME["fg"], font=("Microsoft YaHei UI", 8))
        self.status_right.pack(side=tk.RIGHT)
        self._status_sep(bar, right=True)
        tk.Label(bar, text="空格: 4", bg=THEME["panel_head"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.RIGHT)
        self._status_sep(bar, right=True)
        self.status_nl = tk.Label(bar, text="LF", bg=THEME["panel_head"], fg=THEME["muted"],
                                  font=("Microsoft YaHei UI", 8))
        self.status_nl.pack(side=tk.RIGHT)
        self._status_sep(bar, right=True)
        self.status_enc = tk.Label(bar, text="UTF-8", bg=THEME["panel_head"], fg=THEME["muted"],
                                   font=("Microsoft YaHei UI", 8))
        self.status_enc.pack(side=tk.RIGHT)
        for _lbl in (self.status_enc, self.status_nl):
            _lbl.configure(cursor="hand2")
            _lbl.bind("<Button-1>", self._encoding_menu)
        self._status_sep(bar, right=True)
        tk.Label(bar, text="Impor 脚本", bg=THEME["panel_head"], fg=THEME["cyan"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.RIGHT)
        self.plugin_status_frame = tk.Frame(bar, bg=THEME["panel_head"])
        self.plugin_status_frame.pack(side=tk.RIGHT, padx=(0, 8))

    def _status_sep(self, bar, right=False):
        tk.Frame(bar, bg=THEME["border_soft"], width=1).pack(
            side=tk.RIGHT if right else tk.LEFT, fill=tk.Y, pady=7, padx=9)

    def _encoding_menu(self, event=None):
        pane = self.current_pane()
        if pane is None:
            return
        menu = tk.Menu(self.root, tearoff=0)

        def set_enc(bom, save=False):
            pane._bom = bom
            pane._encoding = "UTF-8 BOM" if bom else "UTF-8"
            pane._dirty = True
            self.mark_dirty(pane)
            self.update_status()
            if save:
                self.save_file()

        def set_nl(nl, save=False):
            pane._newline = nl
            pane._dirty = True
            self.mark_dirty(pane)
            self.update_status()
            if save:
                self.save_file()

        menu.add_command(label="编码：UTF-8（无 BOM）", command=lambda: set_enc(False))
        menu.add_command(label="编码：UTF-8 BOM", command=lambda: set_enc(True))
        menu.add_separator()
        menu.add_command(label="换行：LF", command=lambda: set_nl("LF"))
        menu.add_command(label="换行：CRLF", command=lambda: set_nl("CRLF"))
        menu.add_separator()
        menu.add_command(label="转换并保存", command=lambda: (set_enc(pane._bom), set_nl(pane._newline), self.save_file()))
        if event is not None:
            menu.tk_popup(event.x_root, event.y_root)

    def update_status(self):
        pane = self.current_pane()
        if pane is None:
            return
        try:
            line, col = pane.text.index("insert").split(".")
        except Exception:
            line, col = "1", "1"
        self.status_right.configure(text=f"Ln {line}, Col {int(col) + 1}")
        name = os.path.basename(pane.path) if pane.path else "未命名"
        mark = " ●" if pane._dirty else ""
        self.status_file.configure(text=name + mark)
        try:
            self.status_enc.configure(text=getattr(pane, "_encoding", "UTF-8"))
            self.status_nl.configure(text=getattr(pane, "_newline", "LF"))
        except Exception:
            pass
        try:
            if pane.path:
                rel = rel_quiet(pane.path, ROOT_DIR).split(os.sep)
                crumb = "   ›   ".join(rel)
            else:
                crumb = "工作区   ›   " + pane.tab_title
            self.crumb_left.configure(text=crumb)
            total = int(pane.text.index("end-1c").split(".")[0])
            self.crumb_right.configure(text=f"{total} 行   ·   UTF-8   ·   Impor")
        except Exception:
            pass

    def _tick_memory(self):
        try:
            from .memory import process_memory_report
            rss = process_memory_report()
            m = re.search(r"RSS (\S+ \S+)", rss)
            text = "内存 " + (m.group(1) if m else "--")
            if self._interp is not None:
                text += f" · 分配 {self._interp.memory.alloc_count}"
            self.status_mem.configure(text=text)
            if self._bottom_kind == "mem":
                self._render_memory()
        except Exception:
            pass
        self.root.after(2200, self._tick_memory)

    def _tree_menu(self, e):
        from tkinter import simpledialog
        row = self.tree.identify_row(e.y)
        if not row:
            return
        self.tree.selection_set(row)
        vals = self.tree.item(row, "values")
        kind, path = (vals[0], vals[1]) if len(vals) >= 2 else ("dir", ROOT_DIR)
        parent = path if kind == "dir" else os.path.dirname(path)

        def new_file():
            name = simpledialog.askstring("新建文件", "文件名（含 .impor）：", parent=self.root)
            if not name:
                return
            fp = os.path.join(parent, name)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(HEADER_MARK + "\n// " + name + "\n\n")
            self._fill_tree()
            self.open_path(fp)

        def new_dir():
            name = simpledialog.askstring("新建文件夹", "文件夹名：", parent=self.root)
            if name:
                os.makedirs(os.path.join(parent, name), exist_ok=True)
                self._fill_tree()

        def rename():
            if kind == "dir" and path == ROOT_DIR:
                return
            name = simpledialog.askstring("重命名", "新名称：", initialvalue=os.path.basename(path), parent=self.root)
            if name and name != os.path.basename(path):
                os.rename(path, os.path.join(os.path.dirname(path), name))
                self._fill_tree()

        def delete():
            if kind == "dir" and path == ROOT_DIR:
                return
            if not messagebox.askyesno("删除", "确定删除？\n" + path):
                return
            if kind == "dir":
                shutil.rmtree(path)
            else:
                os.remove(path)
            self._fill_tree()

        menu = tk.Menu(self.root, tearoff=0, bg=THEME["panel"], fg=THEME["fg"],
                       activebackground=THEME["accent_soft"], activeforeground=THEME["fg_bright"])
        menu.add_command(label="新建文件", command=new_file)
        menu.add_command(label="新建文件夹", command=new_dir)
        menu.add_separator()
        menu.add_command(label="重命名", command=rename)
        menu.add_command(label="删除", command=delete)
        menu.add_separator()
        _root_keys = {os.path.normcase(os.path.abspath(r))
                      for r in (getattr(self, "workspace_roots", None) or [])}
        _is_root = kind == "dir" and os.path.normcase(os.path.abspath(path)) in _root_keys
        menu.add_command(label="添加工作区根目录…", command=self.add_workspace_root)
        if _is_root and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(self.workspace)):
            menu.add_command(label="移除该根目录",
                             command=lambda p=path: self.remove_workspace_root(p))
        menu.add_separator()

        def copy_rel():
            try:
                rel = rel_quiet(path, self.workspace)
                self.root.clipboard_clear()
                self.root.clipboard_append(rel)
                self.toast("已复制相对路径：" + rel, "ok")
            except Exception as e:
                self.toast("复制失败：" + str(e))
        menu.add_command(label="复制相对路径", command=copy_rel)
        if kind == "file":
            menu.add_command(label="在右侧编辑器组打开",
                             command=lambda: self.open_path_in_split(path))
        menu.add_command(label="在资源管理器打开", command=lambda: self._reveal_path(path))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _reveal_path(self, path):
        try:
            if os.path.isdir(path):
                os.startfile(path)
            else:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as e:
            self.toast("打开资源管理器失败：" + str(e))

    def open_project(self):
        path = filedialog.askopenfilename(title="打开 .imporproj",
                                          initialdir=self.workspace,
                                          filetypes=[("Impor 项目", ".imporproj"), ("所有文件", "*.*")])
        if not path:
            return
        base = os.path.dirname(os.path.abspath(path))
        self.open_workspace_path(base)

    def create_project_config(self):
        try:
            p = save_project(self.workspace, roots=["."], ignore=["build", "dist", ".impor_recovery"],
                             search_paths=["."], fmt={"indent": self.indent_width, "line_length": 100})
            self.project = load_project(self.workspace, self.workspace)
            self.workspace_roots = list(self.project.get("roots") or [self.workspace])
            self.index = ProjectIndex(self.workspace, roots=self.workspace_roots,
                                      ignore=self.project.get("ignore") or [])
            self.index.refresh()
            self.toast("已生成 " + os.path.basename(p), "ok")
        except Exception as exc:
            self.toast("生成项目配置失败：" + str(exc))

    def save_current_project_settings(self):
        try:
            from .studio_ext import save_project_settings
            data = dict(self.project_settings or {})
            data["theme"] = self.theme_name
            data["font_size"] = int(self.font_size)
            editor = dict(data.get("editor")) if isinstance(data.get("editor"), dict) else {}
            editor.update({"indent": int(self.indent_width), "word_wrap": bool(self.word_wrap),
                           "auto_pair": bool(getattr(self, "auto_pair", True)),
                           "minimap": bool(self.show_minimap),
                           "inlay_hints": bool(getattr(self, "inlay_hints", True)),
                           "semantic_highlighting": bool(getattr(self, "semantic_highlight", True)),
                           "async_diagnostics": bool(getattr(self, "async_diagnostics", True))})
            data["editor"] = editor
            data["format"] = dict(data.get("format")) if isinstance(data.get("format"), dict) else {}
            data["format"]["on_save"] = bool(self.auto_format)
            data["autosave"] = bool(self.autosave)
            path = save_project_settings(self.workspace, data)
            self.project_settings = data
            self.toast("已写入项目设置：" + path, "ok")
            return path
        except Exception as exc:
            self.toast("写入项目设置失败：" + str(exc), "err")
            return None

    def open_project_settings(self):
        import json
        from .studio_ext import project_settings_path, save_project_settings
        path = project_settings_path(self.workspace)
        if not os.path.isfile(path):
            self.save_current_project_settings()
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = "{}"
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("项目设置 · .impor/settings.json")
        win.transient(self.root)
        win.geometry("620x520")
        tk.Label(win, text=path, bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(fill=tk.X, padx=12, pady=(10, 4))
        body = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], insertbackground=THEME["accent"],
                       relief="flat", font=(self.mono, 10), padx=10, pady=8)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        body.insert("1.0", text)
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=12, pady=(0, 10))

        def save():
            try:
                data = json.loads(body.get("1.0", "end-1c"))
                if not isinstance(data, dict):
                    raise ValueError("顶层必须是对象 {}")
                save_project_settings(self.workspace, data)
                self.project_settings = data
                self._apply_project_settings()
                self.toast("项目设置已保存并应用", "ok")
                win.destroy()
            except Exception as exc:
                messagebox.showerror("项目设置无效", str(exc), parent=win)

        FlatButton(bar, "保存并应用", command=save, kind="primary", width=100).pack(side=tk.LEFT, padx=3)
        FlatButton(bar, "取消", command=win.destroy, width=72).pack(side=tk.LEFT, padx=3)

    def _apply_project_settings(self, rebuild_theme=True):
        ps = self.project_settings if isinstance(self.project_settings, dict) else {}
        if not ps:
            return
        if ps.get("font_size"):
            self.font_size = max(9, min(18, int(ps.get("font_size"))))
        editor = ps.get("editor") if isinstance(ps.get("editor"), dict) else {}
        if editor.get("indent"):
            self.indent_width = max(2, min(8, int(editor.get("indent"))))
        if "word_wrap" in editor:
            self.word_wrap = bool(editor.get("word_wrap"))
        if "auto_pair" in editor:
            self.auto_pair = bool(editor.get("auto_pair"))
        if "minimap" in editor:
            self.show_minimap = bool(editor.get("minimap"))
        if "inlay_hints" in editor:
            self.inlay_hints = bool(editor.get("inlay_hints"))
        if "semantic_highlighting" in editor:
            self.semantic_highlight = bool(editor.get("semantic_highlighting"))
        if "async_diagnostics" in editor:
            self.async_diagnostics = bool(editor.get("async_diagnostics"))
        if "large_file_kb" in editor:
            self.large_file_kb = max(256, int(editor.get("large_file_kb") or 2000))
        fmt = ps.get("format") if isinstance(ps.get("format"), dict) else {}
        if fmt.get("on_save") is not None:
            self.auto_format = bool(fmt["on_save"])
        if "autosave" in ps:
            self.autosave = bool(ps.get("autosave"))
        target_theme = ps.get("theme")
        if rebuild_theme and target_theme in ("dark", "light", "hc") and target_theme != self.theme_name:
            self.apply_theme(target_theme)
            return
        for pane in self.panes:
            try:
                pane.text.configure(font=(self.mono, self.font_size),
                                    wrap="word" if self.word_wrap else "none")
                pane._configure_tags()
                pane.highlight()
                pane.redraw_lineno()
            except Exception:
                pass

    # ---------- 多根工作区 ----------
    def _workspace_root_spec(self, root, base):
        root = os.path.abspath(root)
        try:
            rel = rel_quiet(root, base)
        except Exception:
            return root
        if rel == ".":
            return "."
        if not rel.startswith(".." + os.sep) and rel != "..":
            return rel.replace(os.sep, "/")
        return root

    def _persist_workspace_roots(self):
        """把当前根列表写回 .imporproj，保留 ignore / search_paths / format。"""
        project = getattr(self, "project", {}) or {}
        base = project.get("base") or self.workspace
        roots = [self._workspace_root_spec(r, base) for r in self.workspace_roots]
        save_project(base, roots=roots, ignore=project.get("ignore") or [],
                     search_paths=project.get("search_paths") or [],
                     fmt=project.get("format") or None)
        self.project = load_project(base, base)
        self.workspace_roots = list(self.project.get("roots") or [self.workspace])
        self.index = ProjectIndex(self.workspace, roots=self.workspace_roots,
                                  ignore=self.project.get("ignore") or [])
        self.refresh_index_async()
        self._fill_tree()
        self._apply_workspace_title()

    def _apply_workspace_title(self):
        try:
            extra = ""
            roots = getattr(self, "workspace_roots", []) or []
            if len(roots) > 1:
                extra = " · %d 根" % len(roots)
            if not getattr(self, "workspace_trusted", True):
                extra += " · 受限模式"
            self.root.title(f"Impor (I++) {__version__} · {os.path.basename(self.workspace)}" + extra)
        except Exception:
            pass

    def add_workspace_root(self):
        path = filedialog.askdirectory(title="添加工作区根目录", initialdir=self.workspace)
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            self.toast("不是有效目录", "err")
            return
        keys = {os.path.normcase(os.path.abspath(r)) for r in self.workspace_roots}
        if os.path.normcase(path) in keys:
            self.toast("该目录已经在工作区根列表中", "warn")
            return
        self.workspace_roots.append(path)
        self._persist_workspace_roots()
        self.toast("已添加根目录：" + path, "ok")

    def remove_workspace_root(self, path=None):
        if path is None:
            sel = self.tree.selection()
            row = sel[0] if sel else self.tree.focus()
            if not row:
                self.toast("请先在资源管理器选中一个根目录", "warn")
                return
            vals = self.tree.item(row, "values")
            if len(vals) < 2 or vals[0] != "dir":
                self.toast("请选择一个根目录节点", "warn")
                return
            path = vals[1]
        path = os.path.abspath(path)
        current = [os.path.abspath(r) for r in self.workspace_roots]
        key = os.path.normcase(path)
        if key not in {os.path.normcase(r) for r in current}:
            self.toast("该目录不是工作区根目录", "warn")
            return
        if key == os.path.normcase(os.path.abspath(self.workspace)):
            self.toast("主根目录不能移除；请先切换工作区", "warn")
            return
        if len(current) <= 1:
            self.toast("工作区至少保留一个根目录", "warn")
            return
        self.workspace_roots = [r for r in current if os.path.normcase(r) != key]
        self._persist_workspace_roots()
        self.toast("已移除根目录：" + path, "ok")

    def open_workspace_path(self, path):
        global ROOT_DIR
        if not path:
            return
        self.workspace = os.path.abspath(path)
        ROOT_DIR = self.workspace
        self.project = load_project(self.workspace, self.workspace)
        try:
            from .studio_ext import load_project_settings
            self.project_settings = load_project_settings(self.workspace)
        except Exception:
            self.project_settings = {}
        self.workspace_roots = list(self.project.get("roots") or [self.workspace])
        self.index = ProjectIndex(self.workspace, roots=self.workspace_roots,
                                  ignore=self.project.get("ignore") or [])
        self.workspace_trusted = self._workspace_is_trusted(self.workspace)
        self._apply_workspace_trust()
        try:
            if self.plugins:
                if self.workspace_trusted:
                    self.plugins.reload()
                else:
                    self.plugins.reset()
                    self.toast("受限模式：项目插件未加载，信任工作区后启用", "warn")
        except Exception as exc:
            self.toast("插件重载失败：" + str(exc)[:100], "err")
        self.refresh_index_async()
        self.start_index_watcher()
        self._fill_tree()
        self._apply_project_settings()
        self._apply_workspace_title()
        self.toast("工作区：" + self.workspace)

    def _set_index_status(self, text, kind="ok"):
        self._index_status_text = text
        try:
            self.status_index.configure(text=text, fg=THEME["green"] if kind == "ok" else
                                        (THEME["red"] if kind == "err" else THEME["muted"]))
        except Exception:
            pass

    def start_index_watcher(self, interval=2.5):
        """工作区文件监听：外部修改/保存后自动增量刷新符号索引。"""
        self.stop_index_watcher()
        stop = threading.Event()
        self._watch_stop = stop
        index = self.index

        def loop():
            while not stop.wait(interval):
                try:
                    changed = index.refresh()
                except Exception:
                    continue
                if not changed:
                    continue
                try:
                    self.root.after(0, self._on_index_refreshed)
                except Exception:
                    return

        self._watch_thread = threading.Thread(target=loop, name="impor-workspace-watcher", daemon=True)
        self._watch_thread.start()

    def stop_index_watcher(self):
        try:
            if self._watch_stop:
                self._watch_stop.set()
        except Exception:
            pass
        self._watch_stop = None
        self._watch_thread = None

    def _on_index_refreshed(self):
        self._set_index_status("索引已更新", "ok")
        try:
            if getattr(self, "_index_status_text", "") == "索引已更新" and self.current_pane():
                self.status_index.configure(text="索引已更新")
        except Exception:
            pass

    def refresh_index_async(self, on_done=None, force=False):
        """3.2.2：符号索引后台重建，启动/切工作区不再卡住界面。

        多次请求会合并：只保留最后一次，避免切换工作区时旧任务覆盖新索引。
        """
        gen = getattr(self, "_index_gen", 0) + 1
        self._index_gen = gen
        self._set_index_status("索引中…", "muted")

        def worker():
            try:
                changed = self.index.refresh(force=force)
                err = ""
            except Exception as exc:
                changed, err = False, str(exc)
            def finish():
                if gen != getattr(self, "_index_gen", 0):
                    return
                self._index_ready = not err
                if err:
                    self._set_index_status("索引失败", "err")
                    try:
                        self.toast("索引重建失败：" + err[:120], "err")
                    except Exception:
                        pass
                else:
                    try:
                        count = len(self.index.symbols())
                        self._set_index_status("索引 %d 符号" % count, "ok")
                    except Exception:
                        self._set_index_status("索引就绪", "ok")
                if not err and on_done is not None:
                    try:
                        on_done(changed)
                    except Exception:
                        pass
            try:
                self.root.after(0, finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def open_workspace(self):
        path = filedialog.askdirectory(title="打开文件夹作为工作区", initialdir=self.workspace)
        if not path:
            return
        self.open_workspace_path(path)

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        roots = []
        seen = set()
        for root in (getattr(self, "workspace_roots", None) or [ROOT_DIR]):
            root = os.path.abspath(root)
            key = os.path.normcase(root)
            if os.path.isdir(root) and key not in seen:
                seen.add(key)
                roots.append(root)
        if not roots:
            roots = [os.path.abspath(ROOT_DIR)]
        for i, root in enumerate(roots):
            label = "ImporLang" if i == 0 and os.path.basename(root).lower() == "imporlang" else os.path.basename(root)
            root_node = self.tree.insert("", tk.END, text=label, open=(i == 0), values=("dir", root))
            # 常用目录先懒加载一层，其余目录展开时再挂子节点
            for name in ("examples", "docs", "bootstrap", os.path.join("impor", "std"), "tests", "src"):
                path = os.path.join(root, name)
                if os.path.isdir(path):
                    self._tree_add_dir(root_node, path)
            try:
                for fn in sorted(os.listdir(root)):
                    if fn.endswith(".impor"):
                        self.tree.insert(root_node, tk.END, text=fn,
                                         values=("file", os.path.join(root, fn)))
            except OSError:
                pass

    def _tree_add_dir(self, parent, path):
        node = self.tree.insert(parent, tk.END, text=os.path.basename(path), values=("dir", path))
        try:
            if os.listdir(path):
                self.tree.insert(node, tk.END, text="加载中…", values=("placeholder", ""))
        except OSError:
            pass
        return node

    def _tree_expand(self, e=None):
        row = self.tree.focus()
        if not row:
            return
        vals = self.tree.item(row, "values")
        if len(vals) < 2 or vals[0] != "dir":
            return
        path = vals[1]
        kids = self.tree.get_children(row)
        if not (len(kids) == 1 and self.tree.item(kids[0], "values")[:1] == ("placeholder",)):
            return
        self.tree.delete(*kids)
        try:
            entries = sorted(os.scandir(path), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        skip = {"__pycache__", ".git", "node_modules", "build", "dist", ".venv", "venv"}
        for ent in entries:
            if ent.name.startswith(".") or ent.name in skip:
                continue
            try:
                if ent.is_dir():
                    self._tree_add_dir(row, ent.path)
                elif ent.name.endswith((".impor", ".md")):
                    self.tree.insert(row, tk.END, text=ent.name, values=("file", ent.path))
            except OSError:
                continue

    def _on_tree_open(self, e=None):
        sel = self.tree.focus()
        if not sel:
            return
        vals = self.tree.item(sel, "values")
        if vals and vals[0] == "file":
            self.open_path(vals[1])

