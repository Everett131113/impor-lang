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

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class EditorPane(tk.Frame):
    def __init__(self, master, app, path=None, content=""):
        super().__init__(master, bg=THEME["editor"])
        self.app = app
        self.path = path
        self._dirty = False
        self._hl_job = None
        self._diag_job = None
        self._diag_gen = 0
        self._diag_queue = queue.Queue()
        self._diag_thread = None
        self._diag_poll_job = None
        self._problem_marks = []
        self._lint_offset = 0
        self._lint_payload = None
        self._suspend = False
        self._bdepth = 0
        self._mm_job = None
        self._pop = None
        self._pop_items = []
        self.breakpoints = set()
        self.bp_conditions = {}
        self.bp_logs = {}
        self.bp_hits = {}
        self.bp_min_hits = {}
        self._hint = None
        self._exec_line = None
        self.tab_title = os.path.basename(path) if path else "未命名"
        self._folded = set()
        self._fold_ranges = {}
        self._sem_map = {}
        self._bc_text = ""
        self._bc_items = []
        self._rect_anchor = None
        self._last_occ = None
        self._large_file = False
        self._encoding = "UTF-8"
        self._newline = "LF"
        self._bom = False
        self._diff_undo = []

        self.breadcrumb = tk.Label(self, text="", bg=THEME["editor"], fg=THEME["muted"],
                                   anchor="w", padx=2, cursor="hand2",
                                   font=("Microsoft YaHei UI", 8))
        self.breadcrumb.pack(side=tk.TOP, fill=tk.X, padx=(72, 14), pady=(2, 1))
        self.breadcrumb.bind("<Button-1>", self._breadcrumb_click)
        self.sticky_scope = tk.Label(self, text="", bg=THEME["editor"], fg=THEME["accent"],
                                      anchor="w", padx=2,
                                      font=("Microsoft YaHei UI", 7, "bold"))
        self.sticky_scope.pack(side=tk.TOP, fill=tk.X, padx=(72, 14))

        self.lineno = tk.Canvas(self, width=92 if getattr(app, "inlay_hints", True) else 62,
                                bg=THEME["editor_gutter"], highlightthickness=0)
        self.lineno.pack(side=tk.LEFT, fill=tk.Y)

        self.text = tk.Text(
            self, wrap="word" if getattr(app, "word_wrap", False) else "none",
            undo=True, bg=THEME["editor"], fg=THEME["fg"],
            insertbackground=THEME["accent"], insertwidth=2,
            selectbackground=THEME["selection"], selectforeground="#ffffff",
            font=(app.mono, app.font_size), relief="flat", bd=0,
            padx=12, pady=8, tabs="4c", autoseparators=True, maxundo=-1,
            spacing1=2, spacing2=1, spacing3=2,
        )
        self.scroll = MiniScrollbar(self, self.text.yview, width=11)
        self.text.configure(yscrollcommand=self._on_yscroll)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.minimap = Minimap(self, self)
        self.minimap.pack(side=tk.RIGHT, fill=tk.Y)
        if not getattr(app, "show_minimap", True):
            self.minimap.pack_forget()
        self.scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._configure_tags()
        self._bind_events()
        self._mc_marks = []
        self._key_line_state = None
        self._key_ui_job = None
        self._status_job = None
        self._outline_job = None
        self._bracket_job = None
        self._completion_job = None
        self._sem_dirty = False
        self._sem_job = None
        self._sem_pos_cache = {}
        self._sem_cache_key = None
        self._sem_map_cache = {}
        self._sem_map_key = None
        # 输入时不再自动弹补全（打 p 弹 print）；保留 Ctrl+Space 手动补全。
        self.auto_complete = False
        self._param_job = None
        self._breadcrumb_job = None
        self._sync_job = None
        self._curline_no = None
        self._curline_tag = None
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.after(50, self.highlight)
        self.redraw_lineno()

    # ---------- 标签 ----------
    def _configure_tags(self):
        mono = self.app.mono
        size = self.app.font_size
        t = self.text
        t.tag_config("kw", foreground=THEME["orange"])
        t.tag_config("kw2", foreground=THEME["cyan"])
        t.tag_config("builtin", foreground=THEME["cyan"])
        t.tag_config("string", foreground=THEME["green"])
        t.tag_config("num", foreground=THEME["orange"])
        t.tag_config("comment", foreground=THEME["comment"], font=(mono, size, "italic"))
        t.tag_config("doc", foreground=THEME["doc"], font=(mono, size, "italic"))
        t.tag_config("func", foreground=THEME["accent"])
        t.tag_config("cls", foreground=THEME["yellow"])
        t.tag_config("decl", foreground=THEME["fg"])
        t.tag_config("op", foreground=THEME["muted"])
        t.tag_config("sem", foreground=THEME["cyan"], font=(mono, size, "bold"))
        t.tag_config("sem_var", foreground=THEME["sem_var"])
        t.tag_config("sem_param", foreground=THEME["sem_param"])
        t.tag_config("sem_field", foreground=THEME["sem_field"])
        t.tag_config("sem_module", foreground=THEME["sem_module"])
        for i, color in enumerate(BRACKET_COLORS):
            t.tag_config("br%d" % i, foreground=color)
        t.tag_config("curline", background=THEME["curline"])
        t.tag_config("bracket", background=THEME["bracket"])
        t.tag_config("search", background=THEME["search"])
        t.tag_config("fold", elide=True)
        t.tag_raise("kw")
        t.tag_raise("kw2")
        t.tag_raise("builtin")
        t.tag_raise("string")
        t.tag_raise("num")
        t.tag_raise("comment")
        t.tag_raise("doc")
        t.tag_raise("func")
        t.tag_raise("cls")
        t.tag_raise("sem")
        t.tag_raise("sem_var")
        t.tag_raise("sem_param")
        t.tag_raise("sem_field")
        t.tag_raise("sem_module")
        for i in range(len(BRACKET_COLORS)):
            t.tag_raise("br%d" % i)
        t.tag_raise("bracket")
        t.tag_lower("curline")

    def _bind_events(self):
        t = self.text
        t.bind("<<Modified>>", self._on_modified)
        t.bind("<KeyRelease>", self._on_key)
        t.bind("<ButtonRelease-1>", self._on_key)
        t.bind("<Return>", self._auto_indent)
        t.bind("<Tab>", self._indent)
        t.bind("<Shift-Tab>", self._unindent)
        t.bind("<Control-a>", self._select_all)
        t.bind("<Control-d>", self.select_next_occurrence)
        t.bind("<Control-Shift-D>", self._duplicate_line)
        t.bind("<Alt-Shift-Button-1>", self._rect_start)
        t.bind("<Alt-Shift-B1-Motion>", self._rect_update)
        t.bind("<Alt-Shift-ButtonRelease-1>", lambda e: setattr(self, "_rect_anchor", None))
        t.bind("<Control-slash>", self._toggle_comment)
        t.bind("<Control-Key-/>", self._toggle_comment)
        t.bind("<Control-space>", lambda e: self._show_completion(manual=True))
        t.bind("<Control-Button-1>", self._goto_definition)
        t.bind("<Motion>", self._hover_motion)
        t.bind("<Leave>", lambda e: self._hide_hover())
        t.bind("<Escape>", lambda e: self._hide_completion())
        t.bind("<Alt-Button-1>", self._mc_add)
        t.bind("<KeyPress>", self._mc_type, add="+")
        t.bind("<BackSpace>", self._mc_backspace, add="+")
        t.bind("<KeyPress>", self._auto_pair, add="+")
        t.bind("<BackSpace>", self._smart_backspace, add="+")
        t.bind("<Home>", self._smart_home)
        t.bind("<Control-BackSpace>", self._delete_word_before)
        t.bind("<Control-Shift-K>", self._delete_lines)
        t.bind("<Alt-Up>", lambda e: self._move_lines(-1))
        t.bind("<Alt-Down>", lambda e: self._move_lines(1))
        self.lineno.bind("<Button-1>", self._gutter_click)
        self.lineno.bind("<Button-3>", self._gutter_cond)

    def _mc_add(self, e=None):
        idx = self.text.index(f"@{e.x},{e.y}")
        mark = "mc%d" % len(self._mc_marks)
        self.text.mark_set(mark, idx)
        self.text.mark_gravity(mark, "right")
        self._mc_marks.append(mark)
        self.text.tag_add("mcursor", idx, idx + "+1c")
        self.text.tag_config("mcursor", background=THEME["accent_soft"])
        self.text.insert("insert", "")
        self.text.mark_set("insert", idx)
        return "break"

    def _mc_clear(self):
        for m in self._mc_marks:
            self.text.mark_unset(m)
        self._mc_marks = []
        self.text.tag_remove("mcursor", "1.0", "end")
        self.app.toast("已清除多光标")

    def _mc_type(self, e=None):
        if not self._mc_marks or not e or not e.char or e.state & 0x4:
            return
        if ord(e.char) < 32:
            return
        ch = e.char
        self.after_idle(lambda: self._mc_mirror(ch))

    def _mc_mirror(self, ch):
        for m in self._mc_marks:
            try:
                idx = self.text.index(m)
                self.text.insert(idx, ch)
            except Exception:
                continue

    def _mc_backspace(self, e=None):
        if not self._mc_marks:
            return
        for m in self._mc_marks:
            try:
                idx = self.text.index(m)
                self.text.delete(idx + "-1c", idx)
            except Exception:
                continue
        self.text.delete("insert-1c", "insert")
        return "break"

    def _select_all(self, e=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _duplicate_line(self, e=None):
        line = self.text.get("insert linestart", "insert lineend")
        self.text.insert("insert lineend", "\n" + line)
        return "break"

    def _toggle_comment(self, e=None):
        """Ctrl+/：单行或多行注释/取消注释，保留原缩进。"""
        t = self.text
        try:
            a, b = t.index("sel.first"), t.index("sel.last")
        except tk.TclError:
            a = b = None
        if a:
            start = int(a.split(".")[0])
            end = int(b.split(".")[0])
            if b.endswith(".0") and end > start:
                end -= 1
        else:
            start = end = int(t.index("insert").split(".")[0])
        rows = [t.get(f"{ln}.0", f"{ln}.end") for ln in range(start, end + 1)]
        nonblank = [r for r in rows if r.strip()]
        if not nonblank:
            return "break"
        remove = all(r.lstrip().startswith("#") for r in nonblank)
        for ln in range(start, end + 1):
            content = t.get(f"{ln}.0", f"{ln}.end")
            if not content.strip():
                continue
            indent = len(content) - len(content.lstrip(" \t"))
            if remove:
                if content[indent:].startswith("#"):
                    stop = indent + 1
                    if content[stop:stop + 1] == " ":
                        stop += 1
                    t.delete(f"{ln}.{indent}", f"{ln}.{stop}")
            else:
                t.insert(f"{ln}.{indent}", "# ")
        if a:
            t.tag_remove("sel", "1.0", "end")
            try:
                t.tag_add("sel", f"{start}.0", f"{end}.end")
            except tk.TclError:
                pass
        return "break"

    def _on_yscroll(self, first, last):
        self.scroll.set(first, last)
        self.redraw_lineno()
        try:
            total = int(self.text.index("end-1c").split(".")[0])
            if getattr(self, "_hl_range", None) is not None or total > 400:
                if getattr(self, "_vh_job", None):
                    self.after_cancel(self._vh_job)
                self._vh_job = self.after(80, self.highlight)
        except Exception:
            pass
        try:
            self.minimap.redraw_view()
        except Exception:
            pass

    def _on_modified(self, e=None):
        if self._suspend:
            self.text.edit_modified(False)
            return
        if self.text.edit_modified():
            was_dirty = self._dirty
            self._dirty = True
            self.text.edit_modified(False)
            # 只有“未保存”状态第一次变化时才重画标签栏，后续按键不再碰 Canvas。
            if not was_dirty:
                self.app.mark_dirty(self)
            if self._hl_job:
                self.after_cancel(self._hl_job)
            self._hl_job = self.after(180, self.highlight)
            # 语义着色依赖整份符号索引；输入过程中先沿用上一帧结果，
            # 停下来后再重算一次，避免每个按键都把 UI 线程卡住。
            self._sem_dirty = True
            if self._sem_job:
                try:
                    self.after_cancel(self._sem_job)
                except Exception:
                    pass
            self._sem_job = self.after(900, self._refresh_semantic)
            self._schedule_diagnostics()
            if getattr(self.app, "split_pane", None):
                if self._sync_job:
                    try:
                        self.after_cancel(self._sync_job)
                    except Exception:
                        pass
                self._sync_job = self.after(150, self._flush_sync_live)

    def _flush_sync_live(self):
        self._sync_job = None
        try:
            self.app._sync_live(self)
        except Exception:
            pass

    def _on_key(self, e=None):
        """按键热路径：只做常数级标记，绘制/状态栏/大纲全部推到空闲帧。"""
        try:
            line = int(self.text.index("insert").split(".")[0])
            total = int(self.text.index("end-1c").split(".")[0])
        except Exception:
            return
        state = (line, total)
        if state != getattr(self, "_key_line_state", None):
            self._key_line_state = state
            self._update_current_line()
            if self._key_ui_job:
                try:
                    self.after_cancel(self._key_ui_job)
                except Exception:
                    pass
            self._key_ui_job = self.after_idle(self._flush_key_ui)
        if self._status_job:
            try:
                self.after_cancel(self._status_job)
            except Exception:
                pass
        self._status_job = self.after(50, self._flush_status)
        if self._outline_job:
            try:
                self.after_cancel(self._outline_job)
            except Exception:
                pass
        self._outline_job = self.after(500, self._flush_outline)

        keysym = getattr(e, "keysym", "") if e is not None else ""
        char = getattr(e, "char", "") if e is not None else ""
        if char in "()[]{}" or keysym in ("BackSpace", "Delete", "Left", "Right", "Up", "Down", "Home", "End"):
            if self._bracket_job:
                try:
                    self.after_cancel(self._bracket_job)
                except Exception:
                    pass
            self._bracket_job = self.after_idle(self._flush_bracket)

        if e is not None and getattr(self, "auto_complete", False) and (
                char.isalnum() or char in "_、" or keysym in ("BackSpace", "Delete")):
            if self._completion_job:
                try:
                    self.after_cancel(self._completion_job)
                except Exception:
                    pass
            self._completion_job = self.after(28, self._flush_completion)
        elif e is not None and char in "(,":
            if self._param_job:
                try:
                    self.after_cancel(self._param_job)
                except Exception:
                    pass
            self._param_job = self.after(20, self._flush_param_hint)

    def _flush_key_ui(self):
        self._key_ui_job = None
        try:
            self.redraw_lineno()
        except Exception:
            pass

    def _flush_status(self):
        self._status_job = None
        try:
            self.app.update_status()
        except Exception:
            pass

    def _flush_outline(self):
        self._outline_job = None
        try:
            self.app._refresh_outline()
        except Exception:
            pass

    def _flush_bracket(self):
        self._bracket_job = None
        try:
            self._match_bracket()
        except Exception:
            pass

    def _flush_completion(self):
        self._completion_job = None
        try:
            self._show_completion()
        except Exception:
            pass

    def _flush_param_hint(self):
        self._param_job = None
        try:
            self._show_param_hint()
        except Exception:
            pass


    def lint_now(self):
        """同步诊断：保存 / 显式检查时使用，并作废在途异步结果。"""
        if self._diag_job:
            try:
                self.after_cancel(self._diag_job)
            except Exception:
                pass
            self._diag_job = None
        self._diag_gen += 1
        raw = self.text.get("1.0", "end-1c")
        offset = 1 if raw.split("\n", 1)[0].startswith("important<<") else 0
        src = strip_header(raw)
        issues, error = self.app._collect_lint(src, self.path)
        self.app._apply_lint(self, src, issues, error, offset)

    def _schedule_diagnostics(self):
        if self._suspend or not getattr(self.app, "async_diagnostics", True):
            return
        if self._diag_job:
            try:
                self.after_cancel(self._diag_job)
            except Exception:
                pass
        self._diag_job = self.after(300, self._start_diagnostics)

    def _start_diagnostics(self):
        self._diag_job = None
        raw = self.text.get("1.0", "end-1c")
        offset = 1 if raw.split("\n", 1)[0].startswith("important<<") else 0
        src = strip_header(raw)
        self._diag_gen += 1
        gen = self._diag_gen
        path = self.path

        def worker():
            try:
                issues, error = self.app._collect_lint(src, path)
            except Exception as exc:
                issues, error = [], str(exc)
            self._diag_queue.put((gen, src, issues, error, offset))

        self._diag_thread = threading.Thread(target=worker, name="impor-diagnostics", daemon=True)
        self._diag_thread.start()
        self._poll_diagnostics()

    def _poll_diagnostics(self):
        if self._diag_poll_job:
            try:
                self.after_cancel(self._diag_poll_job)
            except Exception:
                pass
            self._diag_poll_job = None
        try:
            while True:
                gen, src, issues, error, offset = self._diag_queue.get_nowait()
                if gen != self._diag_gen:
                    continue
                if not self.winfo_exists():
                    continue
                self.app._apply_lint(self, src, issues, error, offset)
        except queue.Empty:
            pass
        except Exception:
            pass
        alive = self._diag_thread is not None and self._diag_thread.is_alive()
        if alive or not self._diag_queue.empty():
            try:
                self._diag_poll_job = self.after(150, self._poll_diagnostics)
            except Exception:
                self._diag_poll_job = None

    # ---------- 补全 / 参数提示 ----------
    def _word_before(self):
        line = self.text.get("insert linestart", "insert")
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)$", line)
        return (m.group(1), len(m.group(1))) if m else ("", 0)

    def _symbols(self):
        names = set(BUILTIN_NAMES) | set(BUILTIN_DOCS) | set(KEYWORDS)
        src = self.text.get("1.0", "end-1c")
        for m in re.finditer(r"(?m)^\s*(?:func|def|class|var|const|let)\s+([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)", src):
            names.add(m.group(1))
        for m in re.finditer(r"(?m)^\s*(?:func|def|class)\s+([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)", src):
            names.add(m.group(1))
        for m in re.finditer(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", src):
            names.add(m.group(1).split(".")[-1])
        return names

    def _hide_completion(self):
        for attr in ("_pop", "_hint"):
            win = getattr(self, attr, None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        return "break"

    def _popup(self, items, prefix, on_pick):
        self._hide_completion()
        if not items:
            return
        self._pop_items = items
        top = tk.Toplevel(self.text)
        top.overrideredirect(True)
        top.configure(bg=THEME["border"])
        lb = tk.Listbox(top, width=54, height=min(9, len(items)), bg=THEME["panel_alt"],
                        fg=THEME["fg"], selectbackground=THEME["accent_soft"],
                        selectforeground=THEME["fg_bright"], relief="flat", bd=0,
                        font=(self.app.mono, self.app.font_size), activestyle="none")
        lb.pack(padx=1, pady=1)
        for it in items:
            lb.insert(tk.END, it)
        lb.selection_set(0)
        bbox = self.text.bbox("insert") or (0, 0, 0, 20)
        top.geometry("+%d+%d" % (self.text.winfo_rootx() + bbox[0],
                                 self.text.winfo_rooty() + bbox[1] + bbox[3]))
        lb.bind("<ButtonRelease-1>", lambda e: on_pick(lb.get(tk.ACTIVE)))
        self._pop = top
        self._pop_box = lb
        self._pop_pick = on_pick
        self._pop_prefix = prefix

    def _show_completion(self, manual=False):
        word, n = self._word_before()
        line = self.text.get("insert linestart", "insert")
        member_names = []
        m = re.search(r"([A-Za-z_][\w\u4e00-\u9fff]*)\.\s*$", line)
        if m:
            try:
                ln = int(self.text.index("insert").split(".")[0])
                src = self.text.get("1.0", "end-1c")
                full_line = self.text.get("insert linestart", "insert lineend")
                if re.search(r"\.\s*$", full_line):
                    src = src.replace(full_line, re.sub(r"\.\s*$", ".tmp", full_line), 1)
                info = infer_expression(src, ln, m.start(1) + 1,
                                        index=self.app.index, path=self.path)
                member_names = info.get("members") or []
            except Exception:
                member_names = []
        if not member_names and not manual and n < 1:
            self._hide_completion()
            return
        if "#" in line or '"' in line:
            self._hide_completion()
            return
        all_names = member_names or self._symbols()
        low = word.lower()

        def fuzzy(name):
            n = name.lower()
            if not low:
                return 0
            if n.startswith(low):
                return 0
            i = 0
            for ch in n:
                if i < len(low) and ch == low[i]:
                    i += 1
            return 1 + (len(n) - i) / max(1, len(n)) if i == len(low) else None

        scored = []
        for name in all_names:
            s = fuzzy(name)
            if s is not None:
                scored.append((s, len(name), name))
        names = [x[2] for x in sorted(scored)][:12]
        if not names:
            self._hide_completion()
            return
        items = []
        for name in names:
            doc = BUILTIN_DOCS.get(name, "")
            sig = doc.split("：", 1)[0] if doc else ""
            items.append(name + ("  ·  " + sig if sig else ""))
        self._popup(items, word, lambda picked: self._accept_completion(picked, n))

    def _accept_completion(self, picked, n):
        name = picked.split("  ·  ", 1)[0]
        self.text.delete(f"insert-{n}c", "insert")
        self.text.insert("insert", name)
        self._hide_completion()
        if name in BUILTIN_DOCS or name in BUILTIN_NAMES:
            self.text.insert("insert", "(")
        self.text.focus_set()

    def _accept_current(self):
        if not getattr(self, "_pop", None):
            return False
        try:
            lb = self._pop_box
            picked = lb.get(lb.curselection()[0]) if lb.curselection() else lb.get(0)
            self._accept_completion(picked, len(getattr(self, "_pop_prefix", "")))
        except Exception:
            self._hide_completion()
        return True

    def _show_param_hint(self):
        before = self.text.get("insert linestart", "insert")
        depth, start = 0, -1
        for i in range(len(before) - 1, -1, -1):
            ch = before[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth:
                    depth -= 1
                else:
                    start = i
                    break
        if start < 0:
            return
        wm = re.search(r"([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)\s*$", before[:start])
        word = wm.group(1) if wm else ""
        if not word:
            return
        doc = BUILTIN_DOCS.get(word, "")
        sig = ""
        if not doc:
            try:
                syms = self.app.index.definition(word, prefer_path=self.path)
            except Exception:
                syms = []
            for sym in syms:
                text = (getattr(sym, "text", "") or "").strip()
                m = re.search(r"(?:func|def)\s+" + re.escape(word) + r"\s*\(([^)]*)\)", text)
                if m:
                    sig = word + "(" + m.group(1) + ")"
                    break
            if not sig:
                src = self.text.get("1.0", "end-1c")
                m = re.search(r"(?m)^\s*(?:func|def)\s+" + re.escape(word) +
                              r"\s*\(([^)]*)\)", src)
                if m:
                    sig = word + "(" + m.group(1) + ")"
        if not sig and doc:
            m = re.search(r"\(([^)]*)\)", doc)
            sig = word + "(" + (m.group(1) if m else "") + ")"
        if not sig:
            return
        params = [p.strip() for p in sig[sig.find("(") + 1:sig.rfind(")")].split(",") if p.strip()]
        cur = before[start + 1:].count(",") if start >= 0 else 0
        lines = [sig, ""]
        for i, p in enumerate(params):
            lines.append(("▶ " if i == cur else "   ") + p)
        if not params:
            lines.append("   （无参数）")
        body = "\n".join(lines)
        self._hide_completion()
        self._kill_hint(getattr(self, "_hint", None))
        top = tk.Toplevel(self.text)
        top.overrideredirect(True)
        top.configure(bg=THEME["panel_alt"])
        tk.Label(top, text=body, bg=THEME["panel_alt"], fg=THEME["fg"],
                 font=(self.app.mono, max(9, self.app.font_size - 1), ), padx=9, pady=6,
                 justify="left").pack()
        bbox = self.text.bbox("insert") or (0, 0, 0, 20)
        top.geometry("+%d+%d" % (self.text.winfo_rootx() + bbox[0],
                                 self.text.winfo_rooty() + bbox[1] + bbox[3] + 4))
        self._hint = top
        self.after(5000, lambda: self._kill_hint(top))

    def _kill_hint(self, win):
        if getattr(self, "_hint", None) is win:
            self._hint = None
        try:
            win.destroy()
        except Exception:
            pass

    def _gutter_click(self, e):
        try:
            ln = int(self.text.index("@0,%d" % e.y).split(".")[0])
        except Exception:
            return
        if 18 <= getattr(e, "x", 99) <= 36 and ln in getattr(self, "_fold_ranges", {}):
            self._toggle_fold(ln)
            return
        if ln in self.breakpoints:
            self.breakpoints.discard(ln)
        else:
            self.breakpoints.add(ln)
        self.redraw_lineno()
        self.app.toast(("已设断点：第 %d 行" % ln) if ln in self.breakpoints else ("已取消断点：第 %d 行" % ln))

    def _gutter_cond(self, e):
        from tkinter import simpledialog
        try:
            ln = int(self.text.index("@0,%d" % e.y).split(".")[0])
        except Exception:
            return
        cond = simpledialog.askstring("条件断点", f"第 {ln} 行命中条件（留空表示无条件）：",
                                      initialvalue=self.bp_conditions.get(ln, ""), parent=self)
        if cond is None:
            return
        self.breakpoints.add(ln)
        if cond.strip():
            self.bp_conditions[ln] = cond.strip()
        else:
            self.bp_conditions.pop(ln, None)
        self.redraw_lineno()
        self.app.toast(f"断点 {ln}：" + (cond.strip() or "无条件"))

    def _goto_definition(self, e):
        try:
            idx = self.text.index("@%d,%d" % (e.x, e.y))
            line = int(idx.split(".")[0])
            col = int(idx.split(".")[1])
            text = self.text.get(f"{line}.0", f"{line}.end")
            m = re.search(r"[A-Za-z_][A-Za-z0-9_]*|[一-龥]+", text[max(0, col - 1):])
            if not m:
                return "break"
            word = m.group(0)
            src = self.text.get("1.0", "end-1c").split("\n")
            pat = re.compile(r"^\s*(?:func|def|class|var|const|let)\s+" + re.escape(word) + r"\b")
            for i, ln in enumerate(src, 1):
                if pat.search(ln):
                    self.text.mark_set("insert", f"{i}.0")
                    self.text.see(f"{i}.0")
                    self.text.tag_add("search", f"{i}.0", f"{i}.end")
                    self.after(600, lambda: self.text.tag_remove("search", "1.0", "end"))
                    return "break"
            # 跨文件：走项目级符号索引，不再线性扫全项目
            cur = os.path.abspath(self.path) if self.path else ""
            defs = [s for s in self.app.index.definition(word, self.path) if os.path.abspath(s.path) != cur]
            if defs:
                target = defs[0]
                self.app.open_path_at(target.path, target.line)
                return "break"
            self.app.toast("未找到定义：" + word)
        except Exception:
            pass
        return "break"

    def _hover_motion(self, e):
        try:
            self.after_cancel(getattr(self, "_hover_job", None))
        except Exception:
            pass
        self._hover_xy = (e.x, e.y)
        self._hover_job = self.after(650, self._show_hover)

    def _hide_hover(self):
        try:
            self.after_cancel(getattr(self, "_hover_job", None))
        except Exception:
            pass
        win = getattr(self, "_hover_win", None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            self._hover_win = None

    def _show_hover(self):
        xy = getattr(self, "_hover_xy", None)
        if not xy:
            return
        try:
            idx = self.text.index("@%d,%d" % xy)
            line, col = idx.split(".")
            text = self.text.get(line + ".0", line + ".end")
            m = re.search(r"[A-Za-z_][A-Za-z0-9_]*|[一-龥]+", text[max(0, int(col) - 1):])
            if not m:
                return
            word = m.group(0)
            doc = BUILTIN_DOCS.get(word, "")
            if not doc:
                src = self.text.get("1.0", "end-1c")
                mm = re.search(r"(?m)^\s*(?:func|def|class|var|const|let)\s+" + re.escape(word) + r"\b.*", src)
                doc = mm.group(0).strip() if mm else ""
            if not doc:
                return
            self._hide_hover()
            top = tk.Toplevel(self.text)
            top.overrideredirect(True)
            top.configure(bg=THEME["border"])
            tk.Label(top, text=doc[:220], bg=THEME["panel_alt"], fg=THEME["fg"],
                     font=(self.app.mono, max(9, self.app.font_size - 1)), padx=9, pady=6,
                     justify="left", wraplength=520).pack(padx=1, pady=1)
            top.geometry("+%d+%d" % (self.text.winfo_rootx() + xy[0] + 16,
                                     self.text.winfo_rooty() + xy[1] + 22))
            self._hover_win = top
        except Exception:
            pass

    # ---------- 行号 ----------
    def _inlay_type(self, ln):
        """行尾 inlay：调试暂停时优先显示当前行局部值，否则做字面量类型推断。"""
        dbg = getattr(self, "_debug_inline", None)
        if dbg and ln in dbg:
            return dbg[ln]
        try:
            line = self.text.get("%d.0" % ln, "%d.end" % ln)
        except Exception:
            return ""
        s = line.strip()
        m = re.match(r"(?:(?:var|let|const|set)\s+)?[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*\s*=\s*(.*)", s)
        if not m:
            return ""
        rhs = m.group(1).split("//", 1)[0].strip()
        if re.match(r"[-+]?[0-9]", rhs):
            return "num"
        if rhs.startswith(('"', "'")):
            return "str"
        if rhs in ("true", "false"):
            return "bool"
        if rhs.startswith("["):
            return "arr"
        if rhs.startswith("{"):
            return "dict"
        if rhs.startswith("func") or rhs.startswith("def"):
            return "func"
        if rhs.startswith("new ") or rhs.startswith("class"):
            return "obj"
        return ""

    def redraw_lineno(self):
        c = self.lineno
        c.delete("all")
        try:
            top = int(self.text.index("@0,0").split(".")[0])
            bottom = int(self.text.index("@0,%d" % max(self.text.winfo_height(), 10)).split(".")[0])
        except Exception:
            return
        total = int(self.text.index("end-1c").split(".")[0])
        bottom = min(bottom + 1, total)
        cw = max(c.winfo_width(), 92 if getattr(self.app, "inlay_hints", True) else 62)
        ch = max(c.winfo_height(), 10)
        c.create_line(cw - 1, 0, cw - 1, ch, fill=THEME["border_soft"])
        try:
            cur = int(self.text.index("insert").split(".")[0])
        except Exception:
            cur = -1
        diag = {}
        for m in getattr(self, "_problem_marks", []) or []:
            try:
                diag[m[0]] = "err" if (len(m) < 3 or m[2] == "error") else "warn"
            except Exception:
                pass
        cov = getattr(self.app, "_last_coverage", None)
        cov_exec = getattr(self.app, "_coverage_exec", set())
        cov_offset = int(getattr(self.app, "_coverage_offset", 0) or 0)
        cov_target = getattr(self.app, "_coverage_target", None)
        cov_ok = bool(cov and cov_target and self.path and
                      os.path.abspath(cov_target) == os.path.abspath(self.path))
        for ln in range(max(1, top), bottom + 1):
            info = self.text.dlineinfo(f"{ln}.0")
            if not info:
                continue
            y, hh = info[1], info[3]
            if ln == self._exec_line:
                c.create_text(21, y + hh / 2, text="▶", anchor="w", fill=THEME["green"],
                              font=(self.app.mono, self.app.font_size - 2, "bold"))
            if ln in self.breakpoints:
                cond = ln in getattr(self, "bp_conditions", {})
                c.create_oval(7, y + hh / 2 - 4, 15, y + hh / 2 + 4,
                              fill=THEME["red"], outline=THEME["yellow"] if cond else "",
                              width=1 if cond else 0)
            if ln in getattr(self, "test_fails", ()):
                c.create_text(6, y + hh / 2, text="✖", anchor="w", fill=THEME["red"],
                              font=(self.app.mono, max(7, self.app.font_size - 2), "bold"))
            if ln in getattr(self, "_fold_ranges", {}):
                c.create_text(25, y + hh / 2,
                              text="▾" if ln in self._folded else "▸",
                              anchor="w", fill=THEME["accent"],
                              font=(self.app.mono, self.app.font_size - 2, "bold"))
            if ln == cur:
                c.create_rectangle(0, y, cw - 1, y + hh, fill=THEME["hover_soft"], width=0)
                c.create_rectangle(0, y, 2, y + hh, fill=THEME["curline_mark"], width=0)
            # 3.2.3 覆盖率行号栏：绿=命中，红=可执行但未命中
            if cov_ok and (ln - cov_offset) in cov_exec:
                hit = (cov or {}).get(ln - cov_offset, 0)
                c.create_rectangle(3, y + 1, 5, y + max(2, hh - 1),
                                   fill=THEME["green"] if hit else THEME["red"], width=0)
            # 3.2.3 诊断标记：错误红色下划线，警告黄色下划线
            if ln in diag:
                c.create_rectangle(6, y + max(1, hh - 2), cw - 14, y + max(1, hh - 1),
                                   fill=THEME["red"] if diag[ln] == "err" else THEME["yellow"], width=0)
            if getattr(self.app, "inlay_hints", True) and not getattr(self, "_large_file", False):
                hint = self._inlay_type(ln)
                if hint:
                    c.create_text(cw - 38, y + hh // 2, text=hint, anchor="e",
                                  fill=THEME["doc"],
                                  font=(self.app.mono, max(7, self.app.font_size - 3)))
            if ln == cur:
                c.create_text(cw - 12, y + hh // 2, text=str(ln), anchor="e",
                              fill=THEME["accent"],
                              font=(self.app.mono, self.app.font_size - 1, "bold"))
            else:
                c.create_text(cw - 12, y + hh // 2, text=str(ln), anchor="e",
                              fill=THEME["muted_dim"],
                              font=(self.app.mono, self.app.font_size - 1))

    # ---------- 高亮 ----------
    def highlight(self, start_line=None, end_line=None):
        """重绘语法高亮。

        start_line / end_line 是 1 起行号；虚拟窗口传窗口内相对行号。传入时
        只处理该区间（当前可视区附近），避免 3000 行窗口每次滚动全量重跑。
        """
        self._hl_job = None
        t = self.text
        content = t.get("1.0", "end-1c")
        lines = content.split("\n")
        total_lines = len(lines)
        forced = start_line is not None or end_line is not None
        if forced:
            try:
                start_line = max(1, min(int(start_line or 1), total_lines))
                end_line = max(start_line, min(int(end_line or total_lines), total_lines))
            except Exception:
                start_line, end_line = 1, total_lines
            self._hl_range = (start_line, end_line)
            tag_start, tag_end = f"{start_line}.0", f"{end_line}.end"
        else:
            self._large_file = getattr(self, "_large_file", False) or len(content) > 2_000_000
            # 大文件只重绘可视窗口，避免整篇 tag 反复增删导致卡顿
            large = total_lines > 400 or self._large_file
            if large:
                try:
                    start_line = max(1, int(t.index("@0,0").split(".")[0]) - 60)
                    end_line = min(total_lines, int(t.index("@0,%d" % max(1, t.winfo_height())).split(".")[0]) + 60)
                except Exception:
                    start_line, end_line = 1, total_lines
                self._hl_range = (start_line, end_line)
                tag_start, tag_end = f"{start_line}.0", f"{end_line}.end"
            else:
                start_line, end_line = 1, total_lines
                self._hl_range = None
                tag_start, tag_end = "1.0", "end"
        sem_src = content if not forced else "\n".join(lines[start_line - 1:end_line])
        cache = getattr(self, "_virtual_sem_cache", None)
        if cache is not None:
            self._sem_map = cache.get(getattr(self, "_virtual_start", -1), {})
        else:
            self._sem_map = dict(getattr(self, "_sem_map_cache", {}) or {})
            if (getattr(self.app, "semantic_highlight", True)
                    and not getattr(self, "_sem_dirty", False)
                    and len(content) <= 200_000 and total_lines <= 1200):
                sem_key = (len(content), hash(content))
                if sem_key != getattr(self, "_sem_map_key", None):
                    self._sem_map = semantic_symbol_map(sem_src)
                    self._sem_map_cache = dict(self._sem_map)
                    self._sem_map_key = sem_key
            elif not getattr(self, "_sem_dirty", False):
                # 大文件只做词法高亮，避免打开时整份解析。
                self._sem_map = {}
        self._sem_pos = dict(getattr(self, "_sem_pos_cache", {}) or {})
        if (not forced and not getattr(self, "_virtual_no_index", False)
                and getattr(self.app, "semantic_highlight", True) and getattr(self, "path", "")
                and len(content) <= 200_000 and total_lines <= 1200
                and not getattr(self, "_sem_dirty", False)):
            key = (self.path, len(content), hash(content))
            if key != getattr(self, "_sem_cache_key", None):
                try:
                    self._sem_pos = self.app.index.semantic_positions(self.path, content)
                    self._sem_pos_cache = dict(self._sem_pos)
                    self._sem_cache_key = key
                except Exception:
                    self._sem_pos = {}
        elif not getattr(self, "_sem_dirty", False) and (
                len(content) > 200_000 or total_lines > 1200):
            # 超阈值文件不做整份语义分析，保留词法高亮即可。
            self._sem_pos = {}
        for tag in ("kw", "kw2", "builtin", "string", "num", "comment", "doc", "func", "cls", "decl", "op",
                    "sem", "sem_var", "sem_param", "sem_field", "sem_module", "br0", "br1", "br2", "br3"):
            t.tag_remove(tag, tag_start, tag_end)
        in_block_comment = False
        in_triple = None
        prev_word = ""
        self._bdepth = 0
        for ln in range(start_line, end_line + 1):
            line = lines[ln - 1]
            i, n = 0, len(line)
            while i < n:
                if in_block_comment:
                    j = line.find("*/", i)
                    if j < 0:
                        t.tag_add("comment", f"{ln}.{i}", f"{ln}.end")
                        break
                    t.tag_add("comment", f"{ln}.{i}", f"{ln}.{j + 2}")
                    i, in_block_comment = j + 2, False
                    continue
                if in_triple:
                    j = line.find(in_triple, i)
                    if j < 0:
                        t.tag_add("string", f"{ln}.{i}", f"{ln}.end")
                        break
                    t.tag_add("string", f"{ln}.{i}", f"{ln}.{j + 3}")
                    i, in_triple = j + 3, None
                    continue
                ch = line[i]
                if ch == "#" or line.startswith("//", i):
                    # v3.5：整行 ## 文档注释用独立颜色（与普通注释区分）
                    if line.startswith("##", i) and not line[:i].strip():
                        t.tag_add("doc", f"{ln}.{i}", f"{ln}.end")
                    else:
                        t.tag_add("comment", f"{ln}.{i}", f"{ln}.end")
                    break
                if line.startswith("/*", i):
                    j = line.find("*/", i + 2)
                    if j < 0:
                        t.tag_add("comment", f"{ln}.{i}", f"{ln}.end")
                        in_block_comment = True
                        break
                    t.tag_add("comment", f"{ln}.{i}", f"{ln}.{j + 2}")
                    i = j + 2
                    continue
                if line.startswith('"""', i) or line.startswith("'''", i):
                    q = line[i:i + 3]
                    j = line.find(q, i + 3)
                    if j < 0:
                        t.tag_add("string", f"{ln}.{i}", f"{ln}.end")
                        in_triple = q
                        break
                    t.tag_add("string", f"{ln}.{i}", f"{ln}.{j + 3}")
                    i = j + 3
                    continue
                if ch in ('"', "'"):
                    j = i + 1
                    while j < n:
                        if line[j] == "\\":
                            j += 2
                            continue
                        if line[j] == ch:
                            j += 1
                            break
                        j += 1
                    t.tag_add("string", f"{ln}.{i}", f"{ln}.{j}")
                    i = j
                    continue
                if ch.isdigit():
                    m = re.match(r"[0-9][0-9_]*(\.[0-9_]+)?([eE][+-]?[0-9]+)?", line[i:])
                    j = i + m.end()
                    t.tag_add("num", f"{ln}.{i}", f"{ln}.{j}")
                    i = j
                    continue
                if ch.isalpha() or ch == "_" or "\u4e00" <= ch <= "\u9fff":
                    m = re.match(r"[A-Za-z0-9_\u4e00-\u9fff]+", line[i:])
                    word = m.group(0)
                    j = i + len(word)
                    if word in KEYWORDS or word == "struct":
                        t.tag_add("kw2" if word in FLOW_WORDS else "kw", f"{ln}.{i}", f"{ln}.{j}")
                    elif word in BUILTIN_NAMES:
                        t.tag_add("builtin", f"{ln}.{i}", f"{ln}.{j}")
                    elif getattr(self.app, "semantic_highlight", True) and (ln, i) in getattr(self, "_sem_pos", {}):
                        kind = self._sem_pos[(ln, i)]
                        tag = {"var": "sem_var", "param": "sem_param", "module": "sem_module",
                               "func": "func", "class": "cls", "struct": "cls",
                               "exception": "cls", "for": "sem_var"}.get(kind, "sem")
                        t.tag_add(tag, f"{ln}.{i}", f"{ln}.{j}")
                    elif getattr(self.app, "semantic_highlight", True) and word in getattr(self, "_sem_map", {}):
                        kind = self._sem_map[word][0]
                        tag = {"var": "sem_var", "param": "sem_param", "module": "sem_module",
                               "func": "func", "class": "cls", "struct": "cls"}.get(kind, "sem")
                        t.tag_add(tag, f"{ln}.{i}", f"{ln}.{j}")
                    elif getattr(self.app, "semantic_highlight", True) and word in ("true", "false", "null", "nil", "self", "this", "super"):
                        t.tag_add("sem", f"{ln}.{i}", f"{ln}.{j}")
                    elif getattr(self.app, "semantic_highlight", True) and i > 0 and line[i - 1] == "." and line[j:j + 1] != "(":
                        t.tag_add("sem_field", f"{ln}.{i}", f"{ln}.{j}")
                    elif line[j:j + 1] == "(":
                        t.tag_add("func", f"{ln}.{i}", f"{ln}.{j}")
                    elif prev_word in ("class", "exception", "struct"):
                        t.tag_add("cls", f"{ln}.{i}", f"{ln}.{j}")
                    elif prev_word in ("var", "set", "const", "let"):
                        t.tag_add("decl", f"{ln}.{i}", f"{ln}.{j}")
                    prev_word = word
                    i = j
                    continue
                if ch in "([{":
                    t.tag_add("br%d" % (self._bdepth % len(BRACKET_COLORS)),
                              f"{ln}.{i}", f"{ln}.{i + 1}")
                    self._bdepth += 1
                    i += 1
                    continue
                if ch in ")]}":
                    self._bdepth = max(0, self._bdepth - 1)
                    t.tag_add("br%d" % (self._bdepth % len(BRACKET_COLORS)),
                              f"{ln}.{i}", f"{ln}.{i + 1}")
                    i += 1
                    continue
                m = re.match(r"[+\-*/%<>=!?:&|^~]+", line[i:])
                if m:
                    t.tag_add("op", f"{ln}.{i}", f"{ln}.{i + m.end()}")
                    i += m.end()
                    continue
                i += 1
            prev_word = ""
        self._compute_folds(lines)
        self._reapply_folds()
        self._update_current_line()
        self._match_bracket()
        if getattr(self, "_mm_job", None) is None:
            self._mm_job = self.after(350, self._refresh_minimap)

    def _refresh_semantic(self):
        """输入停止后重算一次语义着色；输入期间不阻塞 UI。"""
        self._sem_job = None
        self._sem_dirty = False
        try:
            self.highlight()
        except Exception:
            pass

    def _refresh_minimap(self):
        self._mm_job = None
        try:
            total = int(self.text.index("end-1c").split(".")[0])
            if total > 6000:
                return
            self.minimap.redraw_map()
        except Exception:
            pass

    def _update_current_line(self):
        t = self.text
        try:
            line = int(t.index("insert").split(".")[0])
        except Exception:
            return
        if line == getattr(self, "_curline_no", None) and getattr(self, "_curline_tag", None):
            return
        prev = getattr(self, "_curline_tag", None)
        if prev:
            try:
                t.tag_remove("curline", prev[0], prev[1])
            except Exception:
                pass
        try:
            start = t.index("insert linestart")
            end = f"{start} +1line"
            t.tag_add("curline", start, end)
            t.tag_lower("curline")
            self._curline_tag = (start, end)
            self._curline_no = line
        except Exception:
            pass
        if self._breadcrumb_job:
            try:
                self.after_cancel(self._breadcrumb_job)
            except Exception:
                pass
        self._breadcrumb_job = self.after(120, self._flush_breadcrumb)

    def _flush_breadcrumb(self):
        self._breadcrumb_job = None
        try:
            self._update_breadcrumb()
        except Exception:
            pass

    @staticmethod
    def _indent_of(line):
        return len(line) - len(line.lstrip(" \t"))

    def _compute_folds(self, lines):
        folds = {}
        n = len(lines)
        starters = ("class ", "def ", "struct ", "if ", "elif ", "else", "for ",
                    "while ", "match ", "try", "catch")
        for i, line in enumerate(lines):
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("//"):
                continue
            if not s.startswith(starters):
                continue
            indent = self._indent_of(line)
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j >= n or self._indent_of(lines[j]) <= indent:
                continue
            end = j
            for k in range(j, n):
                if lines[k].strip() and self._indent_of(lines[k]) <= indent:
                    break
                end = k
            folds[i + 1] = end + 1
        self._fold_ranges = folds

    def _reapply_folds(self):
        t = self.text
        for tag in list(t.tag_names()):
            if tag.startswith("fold_"):
                t.tag_delete(tag)
        self._folded = {s for s in self._folded if s in self._fold_ranges}
        for start in sorted(self._folded):
            end = self._fold_ranges[start]
            tag = "fold_%d" % start
            t.tag_config(tag, elide=True)
            t.tag_add(tag, f"{start + 1}.0", f"{end}.end")

    def _toggle_fold(self, line=None):
        if line is None:
            try:
                line = int(self.text.index("insert").split(".")[0])
            except Exception:
                return
        if line not in self._fold_ranges:
            self.app.toast("当前行没有可折叠块")
            return
        if line in self._folded:
            self._folded.discard(line)
            self.app.toast("已展开第 %d 行" % line)
        else:
            self._folded.add(line)
            self.app.toast("已折叠第 %d 行（%d 行）" % (line, self._fold_ranges[line] - line))
        self._reapply_folds()
        self.redraw_lineno()

    def _update_breadcrumb(self):
        try:
            line = int(self.text.index("insert").split(".")[0])
            lines = self.text.get("1.0", "end-1c").split("\n")
            stack = []
            for i in range(line - 1, -1, -1):
                s = lines[i].strip()
                if s.startswith(("class ", "def ", "struct ")):
                    name = s.split("(", 1)[0].split(":", 1)[0].strip()
                    for prefix in ("class ", "def ", "struct "):
                        if name.startswith(prefix):
                            name = name[len(prefix):].strip()
                            break
                    if name and all(name != n for n, _ in stack):
                        stack.append((name, i + 1))
                        if len(stack) >= 3:
                            break
            base = os.path.basename(self.path) if self.path else "未命名"
            visible = list(reversed(stack))
            text = base + ("  ›  " + "  ›  ".join(n for n, _ in visible) if visible else "")
            if len(text) > 100:
                text = text[:97] + "…"
            self._bc_items = [(base, 1)] + visible
            if text != self._bc_text:
                self._bc_text = text
                self.breadcrumb.configure(text=text)
            try:
                scope = "  ›  ".join(n for n, _ in visible)
                self.sticky_scope.configure(text=("作用域  " + scope) if scope else "")
            except Exception:
                pass
        except Exception:
            pass

    def _breadcrumb_click(self, e=None):
        if not self._bc_items:
            return
        try:
            import tkinter.font as tkfont
            font = tkfont.Font(family="Microsoft YaHei UI", size=8)
            x = max(0, e.x - 2)
            pos = 0
            hit = self._bc_items[-1]
            for item, line in self._bc_items:
                w = font.measure(item)
                if x <= pos + w + 2:
                    hit = (item, line)
                    break
                pos += w + font.measure("  ›  ")
            self.text.mark_set("insert", f"{hit[1]}.0")
            self.text.see("insert")
            self.focus_text()
        except Exception:
            pass

    def select_next_occurrence(self, e=None):
        t = self.text
        selected = None
        try:
            word = t.get("sel.first", "sel.last")
            selected = t.index("sel.last")
        except tk.TclError:
            word, _ = self._word_before()
        word = (word or "").strip()
        if not word or "\n" in word:
            return "break"
        start = self._last_occ or selected or t.index("insert")
        try:
            idx = t.search(re.escape(word), start, stopindex="end", regexp=True)
            if not idx:
                idx = t.search(re.escape(word), "1.0", stopindex="end", regexp=True)
            if idx:
                end = f"{idx}+{len(word)}c"
                t.tag_add("sel", idx, end)
                t.mark_set("insert", end)
                t.see(idx)
                self._last_occ = end
        except Exception:
            pass
        return "break"

    def _rect_start(self, e=None):
        self._rect_anchor = (self.text.index(f"@{e.x},{e.y}"))
        self.text.tag_remove("sel", "1.0", "end")
        return "break"

    def _rect_update(self, e=None):
        if not self._rect_anchor:
            return "break"
        a = self._rect_anchor.split(".")
        b = self.text.index(f"@{e.x},{e.y}").split(".")
        l1, l2 = sorted((int(a[0]), int(b[0])))
        c1, c2 = sorted((int(a[1]), int(b[1])))
        self.text.tag_remove("sel", "1.0", "end")
        for ln in range(l1, l2 + 1):
            end = self.text.index(f"{ln}.end").split(".")
            hi = min(c2, int(end[1]))
            if hi > c1:
                self.text.tag_add("sel", f"{ln}.{c1}", f"{ln}.{hi}")
        return "break"

    def _match_bracket(self):
        t = self.text
        try:
            near = t.get("insert-1c", "insert+1c")
        except Exception:
            return
        if not any(c in "()[]{}" for c in near):
            t.tag_remove("bracket", "1.0", "end")
            return
        t.tag_remove("bracket", "1.0", "end")
        try:
            full = t.get("1.0", "end")
            off = len(t.get("1.0", "insert"))
        except Exception:
            return
        if off >= len(full):
            off = len(full) - 1
        pairs = {"(": ")", "[": "]", "{": "}"}
        rev = {")": "(", "]": "[", "}": "{"}
        ch, pos = None, off
        if 0 <= off < len(full) and full[off] in "([{)]}":
            ch = full[off]
        elif off - 1 >= 0 and full[off - 1] in "([{)]}":
            ch, pos = full[off - 1], off - 1
        if not ch:
            return
        if ch in pairs:
            depth = 0
            for k in range(pos, len(full)):
                if full[k] == ch:
                    depth += 1
                elif full[k] == pairs[ch]:
                    depth -= 1
                    if depth == 0:
                        t.tag_add("bracket", f"1.0 +{pos}c", f"1.0 +{k + 1}c")
                        return
        elif ch in rev:
            depth = 0
            for k in range(pos, -1, -1):
                if full[k] == ch:
                    depth += 1
                elif full[k] == rev[ch]:
                    depth -= 1
                    if depth == 0:
                        t.tag_add("bracket", f"1.0 +{k}c", f"1.0 +{pos + 1}c")
                        return

    # ---------- 编辑增强 ----------
    _PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}

    def _edit_context(self):
        """返回当前行光标前的简单上下文：code / string / comment。"""
        prefix = self.text.get("insert linestart", "insert")
        quote = None
        i = 0
        while i < len(prefix):
            ch = prefix[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            else:
                if ch in ("'", '"'):
                    quote = ch
                elif ch == "#":
                    return "comment"
            i += 1
        return "string" if quote else "code"

    def _auto_pair(self, e=None):
        """自动配对括号/引号：选中则包裹，空位置成对插入，右符号跳过。"""
        if e is None or not getattr(e, "char", ""):
            return None
        if not bool(getattr(self.app, "auto_pair", True)):
            return None
        if getattr(e, "state", 0) & 0x4:  # Ctrl 组合键不拦截
            return None
        if getattr(self, "_mc_marks", None):
            return None
        ch = getattr(e, "char", "")
        if ch not in self._PAIRS:
            return None
        t = self.text
        ctx = self._edit_context()
        nxt = t.get("insert", "insert+1c")
        if ctx != "code":
            if ctx == "string" and ch in ("'", '"') and nxt == ch:
                t.mark_set("insert", "insert+1c")
                return "break"
            return None
        try:
            a, b = t.index("sel.first"), t.index("sel.last")
        except tk.TclError:
            a = b = None
        if a and b and a != b:
            selected = t.get(a, b)
            t.delete(a, b)
            t.insert(a, ch + selected + self._PAIRS[ch])
            inner_a = t.index(f"{a}+1c")
            inner_b = t.index(f"{inner_a}+{len(selected)}c")
            t.tag_add("sel", inner_a, inner_b)
            t.mark_set("insert", inner_b)
            return "break"
        if nxt == ch and ch in (")", "]", "}", '"', "'"):
            t.mark_set("insert", "insert+1c")
            return "break"
        if ch == "'":
            prev = t.get("insert-1c", "insert")
            if prev and (prev.isalnum() or prev == "_"):
                return None
        t.insert("insert", ch + self._PAIRS[ch])
        t.mark_set("insert", "insert-1c")
        return "break"

    def _smart_backspace(self, e=None):
        """空配对上一次退格删掉两边；多光标模式下让原逻辑先处理。"""
        if not bool(getattr(self.app, "auto_pair", True)):
            return None
        if getattr(self, "_mc_marks", None):
            return None
        t = self.text
        try:
            t.index("sel.first")
            return None
        except tk.TclError:
            pass
        if t.compare("insert", "==", "insert linestart"):
            return None
        prev = t.get("insert-1c", "insert")
        nxt = t.get("insert", "insert+1c")
        if prev in "([{" and self._PAIRS.get(prev) == nxt:
            t.delete("insert-1c", "insert+1c")
            return "break"
        if prev and prev == nxt and prev in ("'", '"'):
            t.delete("insert-1c", "insert+1c")
            return "break"
        return None

    def _smart_home(self, e=None):
        """Home 在行首和第一个非空格之间切换。"""
        t = self.text
        line = t.index("insert linestart")
        content = t.get(line, f"{line} lineend")
        first = len(content) - len(content.lstrip(" \t"))
        col = int(t.index("insert").split(".")[1])
        target = 0 if col == first else first
        t.mark_set("insert", f"{line.split('.')[0]}.{target}")
        t.see("insert")
        return "break"

    def _delete_word_before(self, e=None):
        """Ctrl+Backspace：删掉光标前一个词，中文按连续字处理。"""
        t = self.text
        try:
            t.delete("sel.first", "sel.last")
            return "break"
        except tk.TclError:
            pass
        if t.compare("insert", "==", "insert linestart"):
            if t.compare("insert", "==", "1.0"):
                return "break"
            t.delete("insert-1c", "insert")
            return "break"
        prefix = t.get("insert linestart", "insert")
        m = re.search(r"(\s+|[\w\u4e00-\u9fff]+|[^\w\s]+)$", prefix)
        if not m:
            return None
        t.delete(f"insert-{len(m.group(0))}c", "insert")
        return "break"

    def _line_range(self):
        t = self.text
        try:
            a, b = t.index("sel.first"), t.index("sel.last")
        except tk.TclError:
            ln = int(t.index("insert").split(".")[0])
            return ln, ln, False
        start = int(a.split(".")[0])
        end = int(b.split(".")[0])
        if b.endswith(".0") and end > start:
            end -= 1
        return start, end, True

    def _delete_lines(self, e=None):
        """Ctrl+Shift+K：删除当前行或选中行，并保持相邻行完整。"""
        t = self.text
        start, end, _ = self._line_range()
        total = int(t.index("end-1c").split(".")[0])
        try:
            t.edit_separator()
            if end < total:
                t.delete(f"{start}.0", f"{end + 1}.0")
            elif start > 1:
                t.delete(f"{start - 1}.end", f"{end}.end")
            else:
                t.delete("1.0", "end")
            t.edit_separator()
        except tk.TclError:
            pass
        return "break"

    def _move_lines(self, delta, e=None):
        """Alt+↑/↓：上下移动当前行；有选区时整块移动。"""
        t = self.text
        start, end, had_sel = self._line_range()
        total = int(t.index("end-1c").split(".")[0])
        if delta < 0 and start <= 1:
            return "break"
        if delta > 0 and end >= total:
            return "break"
        col = int(t.index("insert").split(".")[1])
        if start == end:
            ln = start
            if delta < 0:
                prev = t.get(f"{ln - 1}.0", f"{ln - 1}.end")
                cur = t.get(f"{ln}.0", f"{ln}.end")
                t.delete(f"{ln - 1}.0", f"{ln}.end")
                t.insert(f"{ln - 1}.0", cur + "\n" + prev)
                t.mark_set("insert", f"{ln - 1}.{col}")
            else:
                nxt_line = t.get(f"{ln + 1}.0", f"{ln + 1}.end")
                cur = t.get(f"{ln}.0", f"{ln}.end")
                t.delete(f"{ln}.0", f"{ln + 1}.end")
                t.insert(f"{ln}.0", nxt_line + "\n" + cur)
                t.mark_set("insert", f"{ln + 1}.{col}")
            t.see("insert")
            return "break"
        lines = t.get("1.0", "end-1c").split("\n")
        block = lines[start - 1:end]
        rest = lines[:start - 1] + lines[end:]
        dest = (start - 2) if delta < 0 else start
        rest[dest:dest] = block
        new_text = "\n".join(rest)
        try:
            t.edit_separator()
            t.delete("1.0", "end")
            t.insert("1.0", new_text)
            t.edit_separator()
            new_start = dest + 1
            new_end = new_start + len(block) - 1
            if had_sel:
                t.tag_add("sel", f"{new_start}.0", f"{new_end}.end")
            else:
                t.mark_set("insert", f"{new_start}.{col}")
            t.see("insert")
        except tk.TclError:
            pass
        return "break"

    def _auto_indent(self, e=None):
        if self._accept_current():
            return "break"
        t = self.text
        line = t.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group(0)
        stripped = line.strip()
        extra = ""
        if stripped.endswith(":") or re.match(
                r"(if|elif|else|elseif|while|for|def|func|class|struct|try|catch|except|finally|match|switch|case|default|repeat|loop|with|do)\b",
                stripped):
            extra = " " * getattr(self.app, "indent_width", 4)
        t.insert("insert", "\n" + indent + extra)
        t.see("insert")
        return "break"

    def _indent(self, e=None):
        if self._accept_current():
            return "break"
        t = self.text
        width = " " * getattr(self.app, "indent_width", 4)
        try:
            sel_start = t.index("sel.first")
            sel_end = t.index("sel.last")
        except tk.TclError:
            sel_start = sel_end = None
        if sel_start:
            start = int(sel_start.split(".")[0])
            end = int(sel_end.split(".")[0])
            if sel_end.endswith(".0") and end > start:
                end -= 1
            for ln in range(start, end + 1):
                t.insert(f"{ln}.0", width)
            t.tag_remove("sel", "1.0", "end")
            try:
                t.tag_add("sel", f"{start}.0", f"{end}.end")
            except tk.TclError:
                pass
            return "break"
        t.insert("insert", width)
        return "break"

    def _unindent(self, e=None):
        t = self.text
        width = getattr(self.app, "indent_width", 4)
        try:
            sel_start = t.index("sel.first")
            sel_end = t.index("sel.last")
        except tk.TclError:
            sel_start = sel_end = None
        if sel_start:
            start = int(sel_start.split(".")[0])
            end = int(sel_end.split(".")[0])
            if sel_end.endswith(".0") and end > start:
                end -= 1
            lines = list(range(start, end + 1))
        else:
            lines = [int(t.index("insert").split(".")[0])]
        for ln in lines:
            head = t.get(f"{ln}.0", f"{ln}.0+{width}c")
            if head.startswith(" " * width):
                t.delete(f"{ln}.0", f"{ln}.0+{width}c")
            elif head.startswith("\t"):
                t.delete(f"{ln}.0", f"{ln}.0+1c")
            else:
                m = re.match(r" +", head)
                if m:
                    t.delete(f"{ln}.0", f"{ln}.0+{len(m.group(0))}c")
        if sel_start:
            t.tag_remove("sel", "1.0", "end")
            try:
                t.tag_add("sel", f"{start}.0", f"{end}.end")
            except tk.TclError:
                pass
        return "break"

    # ---------- 查找 ----------
    def find_all(self, needle):
        self.text.tag_remove("search", "1.0", "end")
        if not needle:
            return 0
        count = 0
        idx = "1.0"
        while True:
            idx = self.text.search(needle, idx, stopindex="end", nocase=True)
            if not idx:
                break
            end = f"{idx}+{len(needle)}c"
            self.text.tag_add("search", idx, end)
            count += 1
            idx = end
        self.text.tag_raise("search")
        return count

    def find_next(self, needle):
        if not needle:
            return
        pos = self.text.search(needle, "insert", stopindex="end", nocase=True)
        if not pos:
            pos = self.text.search(needle, "1.0", stopindex="end", nocase=True)
        if pos:
            self.text.mark_set("insert", f"{pos}+{len(needle)}c")
            self.text.see(pos)
            self.text.focus_set()

    def focus_text(self):
        self.text.focus_set()

