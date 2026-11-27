# -*- coding: utf-8 -*-
"""全项目搜索 / 批量替换面板（Tk UI）。

UI 只负责交互；搜索、diff、事务备份全部在 editor_search.py 中完成。
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .editor_search import (
    FileResult, SearchOptions, apply_results, count_selected, describe,
    find_latest_transaction, prepare_replacements, search_project, undo_transaction,
)
from .pathsafe import rel_quiet

CHECK = "☑"
UNCHECK = "☐"


class SearchReplaceDialog(tk.Toplevel):
    def __init__(self, app, mode="replace", query="", replacement=""):
        super().__init__(app.root)
        self.app = app
        self.mode = mode
        self.results = []
        self.by_path = {}
        self._busy = False
        self.title("全项目搜索与替换 · Impor Studio")
        self.transient(app.root)
        self.geometry("980x660")
        self.minsize(760, 460)
        self.configure(bg="#0f141c")
        try:
            from .icons import apply_window_icon
            apply_window_icon(self)
        except Exception:
            pass
        self._build(query, replacement)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self.apply_selected())
        self.after(60, lambda: self.query.focus_set())

    def _label(self, parent, text, **kw):
        kw.setdefault("bg", "#0f141c")
        kw.setdefault("fg", "#d5dde8")
        kw.setdefault("font", ("Microsoft YaHei UI", 9))
        return tk.Label(parent, text=text, **kw)

    def _entry(self, parent, var, width=40):
        return tk.Entry(parent, textvariable=var, width=width, bg="#0d1117",
                        fg="#eef2f8", insertbackground="#7aa2f7", relief="flat",
                        font=("Cascadia Code", 10))

    def _button(self, parent, text, command, primary=False):
        bg = "#7aa2f7" if primary else "#1b2432"
        fg = "#0b0e14" if primary else "#d5dde8"
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground="#8fb4ff" if primary else "#253246",
                         activeforeground=fg, relief="flat", bd=0,
                         padx=12, pady=5, cursor="hand2",
                         font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"))

    def _build(self, query, replacement):
        top = tk.Frame(self, bg="#0f141c")
        top.pack(fill=tk.X, padx=14, pady=(12, 6))
        self._label(top, "查找", width=7, anchor="w").grid(row=0, column=0, sticky="w")
        self.query_var = tk.StringVar(value=query)
        self.query = self._entry(top, self.query_var, 52)
        self.query.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._label(top, "替换为", width=7, anchor="w").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.replacement_var = tk.StringVar(value=replacement)
        self.replacement = self._entry(top, self.replacement_var, 52)
        self.replacement.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(6, 0))
        top.grid_columnconfigure(1, weight=1)

        opts = tk.Frame(top, bg="#0f141c")
        opts.grid(row=0, column=2, rowspan=2, sticky="nw")
        self.regex_var = tk.BooleanVar(value=(self.mode == "regex"))
        self.case_var = tk.BooleanVar(value=False)
        self.word_var = tk.BooleanVar(value=False)
        self.include_dirty_var = tk.BooleanVar(value=False)
        for text, var in (("正则表达式", self.regex_var), ("区分大小写", self.case_var),
                          ("全词匹配", self.word_var),
                          ("包含未保存缓冲区（会一并保存）", self.include_dirty_var)):
            tk.Checkbutton(opts, text=text, variable=var, bg="#0f141c", fg="#c9d4e3",
                           selectcolor="#0d1117", activebackground="#0f141c",
                           activeforeground="#ffffff", anchor="w").pack(anchor="w")
        self.scope_var = tk.StringVar(value=".impor .md .txt .json .toml")
        row = tk.Frame(top, bg="#0f141c")
        row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._label(row, "范围", width=7, anchor="w").pack(side=tk.LEFT)
        self._entry(row, self.scope_var, 32).pack(side=tk.LEFT)
        self._button(row, "搜索", self.search, primary=True).pack(side=tk.LEFT, padx=(8, 4))
        self.cancel_btn = self._button(row, "取消", self.cancel_search)
        self.cancel_btn.pack(side=tk.LEFT, padx=2)
        self.cancel_btn.configure(state="disabled")
        self._button(row, "全部勾选", lambda: self._set_all(True)).pack(side=tk.LEFT, padx=2)
        self._button(row, "全部取消", lambda: self._set_all(False)).pack(side=tk.LEFT, padx=2)
        self.undo_btn = self._button(row, "撤销上次替换", self.undo_last)
        self.undo_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.status = tk.Label(self, text="输入关键词后回车搜索；勾选文件再替换。",
                               bg="#0f141c", fg="#6b7688", anchor="w",
                               font=("Microsoft YaHei UI", 8))
        self.status.pack(fill=tk.X, padx=16, pady=(2, 4))

        pane = ttk.Panedwindow(self, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        listbox = tk.Frame(pane, bg="#0f141c")
        self.tree = ttk.Treeview(listbox, columns=("sel", "file", "hits", "state"),
                                 show="headings", selectmode="browse")
        self.tree.heading("sel", text="选")
        self.tree.heading("file", text="文件")
        self.tree.heading("hits", text="命中")
        self.tree.heading("state", text="状态")
        self.tree.column("sel", width=34, stretch=False, anchor="center")
        self.tree.column("file", width=560, anchor="w")
        self.tree.column("hits", width=56, stretch=False, anchor="e")
        self.tree.column("state", width=170, anchor="w")
        vs = ttk.Scrollbar(listbox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<MouseWheel>", self._maybe_load_more, add="+")
        self.tree.bind("<Configure>", lambda e: self._maybe_load_more(), add="+")
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Button-1>", self._toggle_click)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._preview())
        pane.add(listbox, weight=3)

        prev = tk.Frame(pane, bg="#0d1117")
        self.preview = tk.Text(prev, bg="#0d1117", fg="#c9d4e3", relief="flat",
                               wrap="none", font=("Cascadia Code", 9), padx=8, pady=6)
        self.preview.tag_config("add", foreground="#9ece6a")
        self.preview.tag_config("del", foreground="#f7768e")
        self.preview.tag_config("hunk", foreground="#7aa2f7")
        ps = ttk.Scrollbar(prev, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=ps.set)
        self.preview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ps.pack(side=tk.RIGHT, fill=tk.Y)
        pane.add(prev, weight=2)

        bottom = tk.Frame(self, bg="#0f141c")
        bottom.pack(fill=tk.X, padx=14, pady=(0, 12))
        self._label(bottom, "范围外文件不会被扫描；二进制/非 UTF-8 会标为失败。",
                    fg="#6b7688", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        self._button(bottom, "关闭", self.destroy).pack(side=tk.RIGHT, padx=4)
        self.more_btn = self._button(bottom, "加载更多", self._load_more_page)
        self.more_btn.pack(side=tk.RIGHT, padx=4)
        self.more_btn.configure(state="disabled")
        self.apply_btn = self._button(bottom, "替换勾选文件", self.apply_selected, primary=True)
        self.apply_btn.pack(side=tk.RIGHT, padx=4)
        self.query.bind("<Return>", lambda e: self.search())
        self.replacement.bind("<KeyRelease>", lambda e: self._preview())
        self.query.bind("<KeyRelease>", lambda e: None)

    def _search_options(self):
        suffixes = tuple(x if x.startswith(".") else "." + x
                         for x in self.scope_var.get().replace(",", " ").split())
        return SearchOptions(query=self.query_var.get(), regex=self.regex_var.get(),
                             case_sensitive=self.case_var.get(),
                             whole_word=self.word_var.get(),
                             suffixes=suffixes or (".impor", ".md"))

    def _dirty_paths(self):
        out = []
        for p in getattr(self.app, "panes", []):
            if getattr(p, "path", None) and getattr(p, "_dirty", False):
                out.append(p.path)
        return out

    def _buffer_texts(self):
        """v3.7.7：仅在显式勾选时返回未保存缓冲区，替换会以缓冲区全文落盘。"""
        if not getattr(self, "include_dirty_var", None) or not self.include_dirty_var.get():
            return {}
        out = {}
        for p in getattr(self.app, "panes", []):
            if not getattr(p, "path", None) or not getattr(p, "_dirty", False):
                continue
            try:
                out[os.path.abspath(p.path)] = p.get_content()
            except Exception:
                try:
                    out[os.path.abspath(p.path)] = p.text.get("1.0", "end-1c")
                except Exception:
                    continue
        return out

    def cancel_search(self):
        """3.2.2：取消当前扫描；已扫描结果保留，不再继续读文件。"""
        event = getattr(self, "_cancel_event", None)
        if event is not None:
            event.set()
        self._set_status("正在取消…已扫描到的结果会保留", warn=True)

    def _load_more_page(self):
        """3.2.2：显式分页，滚动到底部或点按钮都能续接。"""
        if getattr(self, "_render_i", 0) < len(self.results):
            self._render_rows()
        self._update_more_state()

    def _update_more_state(self):
        try:
            more = getattr(self, "_render_i", 0) < len(self.results)
            self.more_btn.configure(state="normal" if more else "disabled")
        except Exception:
            pass

    def search(self):
        if self._busy:
            return
        try:
            opts = self._search_options()
            if not opts.query:
                self._set_status("搜索内容不能为空", warn=True)
                return
            opts.pattern()
        except Exception as exc:
            self._set_status(str(exc), warn=True)
            return
        self._busy = True
        self._search_token = getattr(self, "_search_token", 0) + 1
        token = self._search_token
        self._cancel_event = threading.Event()
        self.apply_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.more_btn.configure(state="disabled")
        self._set_status("正在后台扫描项目…")
        self.tree.delete(*self.tree.get_children())
        self.preview.delete("1.0", "end")
        self.results = []
        self.by_path = {}

        def worker():
            try:
                roots = list(getattr(self.app, "workspace_roots", None) or [self.app.workspace])
                files = list(search_project(roots, opts, dirty_paths=self._dirty_paths(),
                                            should_cancel=self._cancel_event.is_set,
                                            buffer_texts=self._buffer_texts()))
                err = ""
            except Exception as exc:
                files, err = [], str(exc)
            cancelled = self._cancel_event.is_set()
            self.after(0, lambda: self._search_done(files, opts, err, token, cancelled))

        threading.Thread(target=worker, daemon=True).start()

    def _search_done(self, files, opts, err, token=None, cancelled=False):
        if token is not None and token != getattr(self, "_search_token", 0):
            return
        self._busy = False
        self.apply_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        if err:
            self._set_status(err, warn=True)
            return
        # 搜索阶段 after 与 before 相同；先默认勾选可读文件，准备替换后再按是否有变化收敛。
        include_dirty = bool(self.include_dirty_var.get())
        for r in files:
            r.selected = bool(not r.error and (include_dirty or not r.dirty))
        self.results = files
        self.by_path = {os.path.abspath(r.path): r for r in files}
        # 3.2.1：搜索结果虚拟列表——先渲染 200 行，滚动到底部再增量追加，
        # 避免几万条结果一次性建 Treeview 项造成界面卡顿。
        self._render_i = 0
        self._render_batch = 200
        self._render_rows()
        if not files:
            self._set_status("已取消，未扫描到匹配" if cancelled else "没有匹配")
            return
        # 先渲染全部 before->query 的 diff（替代内容为空时显示删除）
        prepare_replacements(self.results, self.replacement_var.get(), opts)
        self._refresh_rows()
        total = len(self.results)
        shown = min(self._render_i, total)
        prefix = "已取消，保留已扫描结果；" if cancelled else ""
        self._set_status(prefix + describe(self.results, self.include_dirty_var.get()) +
                         ("；已显示 %d / %d 个文件，点「加载更多」或滚动续接" % (shown, total) if shown < total else
                          "；勾选后点「替换勾选文件」"))
        self._update_more_state()
        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])

    def _render_rows(self):
        """增量渲染一批搜索结果；滚动接近底部时自动续接。"""
        start = getattr(self, "_render_i", 0)
        end = min(len(self.results), start + getattr(self, "_render_batch", 200))
        for r in self.results[start:end]:
            iid = os.path.abspath(r.path)
            rel = rel_quiet(r.path, self.app.workspace).replace("\\", "/")
            state = ("读取失败" if r.error else
                     ("缓冲区可替换" if r.dirty and self.include_dirty_var.get() else
                      ("未保存修改" if r.dirty else "可替换")))
            self.tree.insert("", tk.END, iid=iid, values=(
                CHECK if r.selected else UNCHECK, rel, r.count, state))
        self._render_i = end
        if end < len(self.results):
            self._set_status("已显示 %d / %d 个文件，点「加载更多」或滚动续接" % (end, len(self.results)))
        self._update_more_state()

    def _maybe_load_more(self, event=None):
        try:
            if self.tree.yview()[1] > 0.92 and getattr(self, "_render_i", 0) < len(self.results):
                self._render_rows()
        except Exception:
            pass

    def _refresh_rows(self):
        for r in self.results:
            iid = os.path.abspath(r.path)
            if not self.tree.exists(iid):
                continue
            rel = rel_quiet(r.path, self.app.workspace).replace("\\", "/")
            state = ("读取失败" if r.error else
                     ("缓冲区可替换" if r.dirty and self.include_dirty_var.get() else
                      ("未保存修改" if r.dirty else "可替换")))
            self.tree.item(iid, values=(CHECK if r.selected else UNCHECK, rel, r.count, state))

    def _set_all(self, value):
        include_dirty = bool(self.include_dirty_var.get())
        for r in self.results:
            if not r.error and (include_dirty or not r.dirty):
                r.selected = bool(value)
        self._refresh_rows()
        self._set_status(describe(self.results, self.include_dirty_var.get()))

    def _toggle_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        r = self.by_path.get(iid)
        if not r or r.error or (r.dirty and not self.include_dirty_var.get()):
            return
        r.selected = not r.selected
        self._refresh_rows()
        self._set_status(describe(self.results, self.include_dirty_var.get()))
        return "break"

    def _preview(self):
        sel = self.tree.selection()
        self.preview.delete("1.0", "end")
        if not sel:
            return
        r = self.by_path.get(sel[0])
        if not r:
            return
        if r.error:
            self.preview.insert("end", "读取失败：" + r.error, "del")
            return
        diff = r.diff()
        if not diff:
            self.preview.insert("end", "该文件没有可替换内容。")
            return
        for line in diff.splitlines():
            tag = "add" if line.startswith("+") and not line.startswith("+++") else (
                "del" if line.startswith("-") and not line.startswith("---") else
                ("hunk" if line.startswith("@@") else ""))
            self.preview.insert("end", line + "\n", tag)

    def _set_status(self, text, warn=False):
        self.status.configure(text=text, fg="#ff9e64" if warn else "#6b7688")

    def apply_selected(self):
        if self._busy:
            return
        files, hits = count_selected(self.results)
        if not files:
            self._set_status("没有勾选任何可替换文件", warn=True)
            return
        if not messagebox.askyesno("批量替换",
                                   "将替换 %d 个文件、%d 处；会先写入事务备份。\n\n继续？" % (files, hits),
                                   parent=self):
            return
        dirty_sel = [r for r in self.results if r.selected and r.changed and r.dirty]
        if dirty_sel:
            if not messagebox.askyesno(
                    "包含未保存缓冲区",
                    "有 %d 个文件含未保存修改。替换会基于缓冲区全文。\n"
                    "写入磁盘会连同这些未保存修改一起保存，然后重新加载编辑器。\n\n继续？" % len(dirty_sel),
                    parent=self):
                return
        try:
            opts = self._search_options()
            prepare_replacements(self.results, self.replacement_var.get(), opts)
            backup = os.path.join(getattr(self.app, "_recovery_dir", self.app.workspace),
                                  "search_replace")
            txn = apply_results(self.app.workspace, self.results, backup)
        except Exception as exc:
            messagebox.showerror("替换失败", str(exc), parent=self)
            self._set_status("替换失败：" + str(exc)[:120], warn=True)
            return
        applied = {os.path.abspath(r.path) for r in self.results
                   if r.selected and r.changed and not r.error}
        self._reload_open_panes(applied)
        self._set_status("已替换 %d 个文件；事务：%s" % (files, os.path.basename(txn)),)
        self.app.toast("批量替换完成：%d 个文件 / %d 处" % (files, hits), "ok")
        # 替换后重新搜索，避免界面显示过期状态
        self.search()

    def _reload_open_panes(self, reload_paths=None):
        reload_paths = {os.path.abspath(p) for p in (reload_paths or [])}
        for pane in list(getattr(self.app, "panes", [])):
            path = getattr(pane, "path", None)
            if not path:
                continue
            if path not in reload_paths and getattr(pane, "_dirty", False):
                continue
            # 分块标签只持有窗口，不能按普通 Text 全文覆盖；批量替换后整篇重载。
            if hasattr(pane, "reload_from_disk"):
                if path in reload_paths:
                    try:
                        pane.reload_from_disk()
                    except Exception:
                        pass
                continue
            try:
                with open(pane.path, "r", encoding="utf-8", errors="replace") as f:
                    data = f.read()
                pane._suspend = True
                pane.text.delete("1.0", "end")
                pane.text.insert("1.0", data)
                pane.text.edit_modified(False)
                pane._suspend = False
                pane._dirty = False
                pane.highlight()
                pane.redraw_lineno()
            except Exception:
                continue
        try:
            self.app.tabstrip.redraw()
        except Exception:
            pass

    def undo_last(self):
        backup = os.path.join(getattr(self.app, "_recovery_dir", self.app.workspace),
                              "search_replace")
        txn = find_latest_transaction(backup)
        if not txn:
            self._set_status("没有可撤销的替换事务", warn=True)
            return
        try:
            restored = undo_transaction(txn, self.app.workspace)
        except Exception as exc:
            messagebox.showerror("撤销失败", str(exc), parent=self)
            return
        self._reload_open_panes()
        self._set_status("已撤销事务，还原 %d 个文件" % restored)
        self.app.toast("已撤销上次批量替换：%d 个文件" % restored, "ok")
        self.search()
