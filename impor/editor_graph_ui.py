# -*- coding: utf-8 -*-
"""依赖图 / 审计面板 UI（编辑器 3.1）。"""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .editor_graph import build_file_graph, package_tree, rel


class DependencyAuditWindow(tk.Toplevel):
    def __init__(self, app, initial_tab="files"):
        super().__init__(app.root)
        self.app = app
        self.report = None
        self.title("项目依赖图与审计 · Impor Studio")
        self.transient(app.root)
        self.geometry("1000x680")
        self.minsize(760, 500)
        self.configure(bg="#0f141c")
        try:
            from .icons import apply_window_icon
            apply_window_icon(self)
        except Exception:
            pass
        self._build()
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(60, self.refresh)
        self.nb.select(0 if initial_tab == "files" else (1 if initial_tab == "pkg" else 2))

    def _label(self, parent, text, **kw):
        kw.setdefault("bg", "#0f141c")
        kw.setdefault("fg", "#d5dde8")
        kw.setdefault("font", ("Microsoft YaHei UI", 9))
        return tk.Label(parent, text=text, **kw)

    def _button(self, parent, text, cmd, primary=False):
        return tk.Button(parent, text=text, command=cmd,
                         bg="#7aa2f7" if primary else "#1b2432",
                         fg="#0b0e14" if primary else "#d5dde8",
                         activebackground="#8fb4ff" if primary else "#253246",
                         activeforeground="#d5dde8", relief="flat", bd=0,
                         padx=11, pady=5, cursor="hand2",
                         font=("Microsoft YaHei UI", 9))

    def _build(self):
        top = tk.Frame(self, bg="#0f141c")
        top.pack(fill=tk.X, padx=14, pady=(12, 6))
        self._label(top, "项目依赖图与审计", font=("Microsoft YaHei UI", 12, "bold"),
                    fg="#eef2f8").pack(side=tk.LEFT)
        self._button(top, "刷新", self.refresh, primary=True).pack(side=tk.RIGHT, padx=4)
        self._button(top, "导出 JSON", self.export_json).pack(side=tk.RIGHT, padx=4)
        self._button(top, "打开选中", self.open_selected).pack(side=tk.RIGHT, padx=4)
        self.summary = tk.Label(self, text="正在扫描…", bg="#0f141c", fg="#6b7688",
                                anchor="w", font=("Microsoft YaHei UI", 8))
        self.summary.pack(fill=tk.X, padx=16, pady=(0, 4))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        self.files_tab = tk.Frame(self.nb, bg="#0f141c")
        self.pkg_tab = tk.Frame(self.nb, bg="#0f141c")
        self.audit_tab = tk.Frame(self.nb, bg="#0f141c")
        self.nb.add(self.files_tab, text="文件依赖")
        self.nb.add(self.pkg_tab, text="包依赖")
        self.nb.add(self.audit_tab, text="审计")

        self.file_tree = ttk.Treeview(self.files_tab, columns=("in", "out", "state"),
                                      show="tree headings", selectmode="browse")
        self.file_tree.heading("#0", text="文件 / 导入")
        self.file_tree.heading("in", text="被导入")
        self.file_tree.heading("out", text="导入")
        self.file_tree.heading("state", text="状态")
        self.file_tree.column("#0", width=520)
        self.file_tree.column("in", width=70, stretch=False, anchor="e")
        self.file_tree.column("out", width=70, stretch=False, anchor="e")
        self.file_tree.column("state", width=150)
        self._attach_scroll(self.files_tab, self.file_tree)
        self.file_tree.bind("<Double-Button-1>", lambda e: self.open_selected())

        self.pkg_tree = ttk.Treeview(self.pkg_tab, columns=("ver", "installed"),
                                     show="tree headings", selectmode="browse")
        self.pkg_tree.heading("#0", text="包")
        self.pkg_tree.heading("ver", text="版本")
        self.pkg_tree.heading("installed", text="状态")
        self.pkg_tree.column("#0", width=520)
        self.pkg_tree.column("ver", width=90, stretch=False)
        self.pkg_tree.column("installed", width=100, stretch=False)
        self._attach_scroll(self.pkg_tab, self.pkg_tree)

        self.audit_tree = ttk.Treeview(self.audit_tab, columns=("level", "kind", "detail"),
                                       show="headings", selectmode="browse")
        self.audit_tree.heading("level", text="级别")
        self.audit_tree.heading("kind", text="类型")
        self.audit_tree.heading("detail", text="详情")
        self.audit_tree.column("level", width=60, stretch=False, anchor="center")
        self.audit_tree.column("kind", width=100, stretch=False)
        self.audit_tree.column("detail", width=780)
        self.audit_tree.tag_configure("错误", foreground="#f7768e")
        self.audit_tree.tag_configure("警告", foreground="#e0af68")
        self.audit_tree.tag_configure("提示", foreground="#7aa2f7")
        self._attach_scroll(self.audit_tab, self.audit_tree)
        self.audit_tree.bind("<Double-Button-1>", lambda e: self.open_selected())

    def _attach_scroll(self, parent, tree):
        vs = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

    def refresh(self):
        self._scan_seq = getattr(self, "_scan_seq", 0) + 1
        seq = self._scan_seq
        self.summary.configure(text="正在扫描项目…", fg="#6b7688")
        self._clear()

        def worker():
            try:
                files = list(self.app._walk_files((".impor",)))
                report = build_file_graph(self.app.workspace, files,
                                          package_dir=self.app.workspace)
                err = ""
            except Exception as exc:
                report, err = None, str(exc)
            self.after(0, lambda: self._done(report, err, seq))

        threading.Thread(target=worker, daemon=True).start()

    def _clear(self):
        for t in (self.file_tree, self.pkg_tree, self.audit_tree):
            t.delete(*t.get_children())

    def _done(self, report, err, seq=None):
        if seq is not None and seq != getattr(self, "_scan_seq", seq):
            return
        if err or report is None:
            self.summary.configure(text="扫描失败：" + (err or "未知错误"), fg="#ff9e64")
            return
        self.report = report
        issues = report.audit_issues()
        self.summary.configure(
            text="%d 个 .impor 文件 · %d 条导入 · %d 个循环 · %d 个未解析 · %d 条审计问题" % (
                len(report.nodes), sum(len(n.imports) for n in report.nodes.values()),
                len(report.cycles), sum(len(n.unresolved) for n in report.nodes.values()),
                len(issues)),
            fg="#9ece6a" if not issues else "#e0af68")
        self._fill_files()
        self._fill_packages()
        self._fill_audit(issues)

    def _fill_files(self):
        report = self.report
        root_id = self.file_tree.insert("", tk.END, text="项目根 · " + os.path.basename(report.root),
                                        values=(len(report.nodes), "", ""), open=True)
        for path, node in sorted(report.nodes.items(), key=lambda kv: rel(report.root, kv[0]).lower()):
            relpath = rel(report.root, path)
            state = "未解析 %d" % len(node.unresolved) if node.unresolved else "正常"
            iid = self.file_tree.insert(root_id, tk.END, text=relpath,
                                        values=(len(report.reverse.get(path, [])),
                                                len(node.imports), state), open=False)
            self.file_tree.item(iid, tags=("bad",) if node.unresolved else ())
            for target in node.resolved:
                self.file_tree.insert(iid, tk.END, text="→ " + rel(report.root, target),
                                      values=("", "", ""))
            for mod, line in node.unresolved:
                self.file_tree.insert(iid, tk.END, text="→ %s（第 %d 行，未解析）" % (mod, line),
                                      values=("", "", "缺失"))
        self.file_tree.tag_configure("bad", foreground="#f7768e")

    def _fill_packages(self):
        report = self.report
        if not report.packages:
            self.pkg_tree.insert("", tk.END, text="没有 impor.lock（先运行 impor pkg lock）")
            return
        rows = package_tree(report)
        parent = ""
        stack = [("", -1)]
        for name, version, installed, depth in rows:
            while stack and stack[-1][1] >= depth:
                stack.pop()
            parent = stack[-1][0] if stack else ""
            iid = self.pkg_tree.insert(parent, tk.END, text=name, values=(version, installed),
                                       open=(depth == 0))
            stack.append((iid, depth))

    def _fill_audit(self, issues):
        for level, kind, detail in issues:
            self.audit_tree.insert("", tk.END, values=(level, kind, detail), tags=(level,))
        if not issues:
            self.audit_tree.insert("", tk.END, values=("通过", "审计", "未发现问题"))

    def open_selected(self):
        index = self.nb.index(self.nb.select())
        tree = self.audit_tree if index == 2 else (self.pkg_tree if index == 1 else self.file_tree)
        if tree is self.audit_tree:
            sel = tree.selection()
            if not sel:
                return
            detail = tree.item(sel[0], "values")[2]
            path = detail.split(":")[0].strip()
            line = 1
            try:
                if ":" in detail:
                    line = int(detail.split(":")[1].split()[0])
            except Exception:
                line = 1
            full = os.path.join(self.app.workspace, path)
            if os.path.isfile(full):
                self.app.open_path_at(full, line)
            return
        if tree is self.pkg_tree or self.report is None:
            return
        sel = tree.selection()
        if not sel:
            return
        text = tree.item(sel[0], "text").replace("→ ", "").replace("（", "|").split("|")[0].strip()
        if text.startswith("项目根"):
            return
        full = os.path.join(self.app.workspace, text.replace("/", os.sep))
        if os.path.isfile(full):
            self.app.open_path_at(full, 1)

    def export_json(self):
        if not self.report:
            self.app.toast("还没有扫描结果")
            return
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("JSON", "*.json")],
                                         initialfile="impor-dependency-audit.json",
                                         initialdir=self.app.workspace)
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.report.to_dict(), f, ensure_ascii=False, indent=2)
                f.write("\n")
            self.app.toast("依赖审计已导出：" + p, "ok")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
