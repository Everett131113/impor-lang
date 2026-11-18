# -*- coding: utf-8 -*-
"""大文件分块编辑视图（3.2.1）。

Tk Text 本身只绘制可视行，但一次性把几百 MB 文本塞进 Text 仍会卡死。
这里把超阈值文件切成固定窗口：完整行列表留在 Python 内存，Tk Text 只装当前窗口，
滚动到边界时换块，行号始终显示文件中的绝对行号。3.2.1 起窗口可编辑：改动先写回
内存中的完整行列表，保存时整文件安全回写（先写临时文件再原子替换）。
"""

from __future__ import annotations

import os
import tempfile
import threading
import tkinter as tk

from .editor import THEME, EditorPane, semantic_symbol_map
from .largefile import ChunkedDocument
from .observability import note_swallowed

DEFAULT_CHUNK = 3000


class VirtualLargeFilePane(EditorPane):
    def __init__(self, master, app, path, lines, start=0, chunk=None):
        self._virtual_doc = ChunkedDocument(lines, chunk=chunk)
        self._virtual_full_lines = self._virtual_doc.lines
        self._virtual_total = self._virtual_doc.total
        self._virtual_chunk = self._virtual_doc.chunk
        self._virtual_start = max(0, min(int(start), max(0, self._virtual_total - 1)))
        self._virtual_sem_cache = {}
        self._virtual_parse_key = None
        self._virtual_parse_gen = 0
        self._virtual_hl_job = None
        self._virtual_loading = False
        self._virtual_no_index = True
        window = self._virtual_doc.window(self._virtual_start)
        super().__init__(master, app, path=path, content="\n".join(window))
        self._large_file = True
        self._virtual_readonly = False
        self._virtual_window_len = len(window)
        self._virtual_dirty = False
        self._virtual_sync_job = None
        self._dirty = False
        self.text.configure(state="normal", undo=True)
        self.text.bind("<<Modified>>", self._virtual_modified, add="+")
        self._update_virtual_badge()
        self.breadcrumb.bind("<Button-1>", lambda e: self.app.convert_virtual_to_full(self))
        # 3.2.1：构造函数已装好首个窗口，不再延时重灌文本——否则用户在 80ms 内的
        # 编辑会被 _virtual_finish 覆盖掉。
        try:
            self.highlight()
            self.redraw_lineno()
            self._update_virtual_badge()
        except Exception:
            note_swallowed("virtual file read")
        self._schedule_semantic_parse()

    def highlight(self, start_line=None, end_line=None):
        """只高亮当前可视区 ±80 行；滚动不再重跑整块 3000 行。"""
        total_local = max(1, len(self.text.get("1.0", "end-1c").split("\n")))
        if start_line is None or end_line is None:
            try:
                top = int(self.text.index("@0,0").split(".")[0])
                bottom = int(self.text.index("@0,%d" % max(1, self.text.winfo_height())).split(".")[0])
            except Exception:
                top, bottom = 1, total_local
            start_line = max(1, top - 80)
            end_line = min(total_local, bottom + 80)
        super().highlight(start_line=start_line, end_line=end_line)

    def _schedule_highlight(self, delay=90):
        try:
            if self._virtual_hl_job:
                self.after_cancel(self._virtual_hl_job)
        except Exception:
            pass
        self._virtual_hl_job = self.after(delay, self._highlight_if_current)

    def _highlight_if_current(self):
        self._virtual_hl_job = None
        try:
            self.highlight()
        except Exception:
            note_swallowed("virtual highlight")

    def _schedule_semantic_parse(self):
        """后台解析当前窗口符号，完成后回主线程应用；同一窗口只跑一次。"""
        if not getattr(self.app, "semantic_highlight", True):
            return
        key = self._virtual_start
        if key in self._virtual_sem_cache or self._virtual_parse_key == key:
            return
        self._virtual_parse_key = key
        gen = self._virtual_parse_gen
        text = self.text.get("1.0", "end-1c")

        def work():
            try: sem = semantic_symbol_map(text)
            except Exception: sem = {}
            try:
                self.after(0, lambda: self._apply_semantic(key, sem, gen))
            except Exception:
                note_swallowed("virtual parse apply")

        try:
            threading.Thread(target=work, name="impor-large-parse", daemon=True).start()
        except Exception:
            self._virtual_parse_key = None

    def _apply_semantic(self, key, sem, gen):
        self._virtual_parse_key = None
        if gen != self._virtual_parse_gen or key != self._virtual_start:
            return
        self._virtual_sem_cache[key] = sem or {}
        try:
            self.highlight()
        except Exception:
            note_swallowed("virtual sem apply")

    def _invalidate_semantic_parse(self):
        """窗口内容变了：丢弃该窗口缓存，并让在途解析结果失效。"""
        self._virtual_sem_cache.pop(self._virtual_start, None)
        self._virtual_parse_gen += 1
        self._virtual_parse_key = None

    def _virtual_modified(self, event=None):
        try:
            if not self.text.edit_modified():
                return
            self.text.edit_modified(False)
        except Exception:
            return
        self._dirty = True
        self._virtual_dirty = True
        try:
            self.app.mark_dirty(self)
        except Exception:
            note_swallowed("virtual file read")
        if self._virtual_sync_job:
            try:
                self.after_cancel(self._virtual_sync_job)
            except Exception:
                pass
        self._virtual_sync_job = self.after(450, self.flush_virtual)

    def flush_virtual(self, force=False):
        """把当前窗口内容写回完整行列表（不落盘）。

        force=True 时即使 <<Modified>> 事件尚未派发也读取窗口，避免
        「刚敲完就 Ctrl+S」丢掉最后一次编辑。
        """
        self._virtual_sync_job = None
        if not self._virtual_dirty and not force:
            return
        try:
            lines = self.text.get("1.0", "end-1c").split("\n")
        except Exception:
            return
        start = self._virtual_start
        old_n = max(0, self._virtual_window_len)
        self._virtual_window_len = self._virtual_doc.replace_window(start, old_n, lines)
        self._virtual_total = self._virtual_doc.total
        self._virtual_full_lines = self._virtual_doc.lines
        self._virtual_dirty = False
        self._invalidate_semantic_parse()
        self._update_virtual_badge()
        self._schedule_semantic_parse()
        return self._virtual_window_len

    def get_content(self):
        self.flush_virtual(force=True)
        return self._virtual_doc.text()

    def reload_from_disk(self):
        """v3.7.7：批量替换后重新载入磁盘全文，避免保留旧缓冲区。"""
        if self._virtual_sync_job:
            try:
                self.after_cancel(self._virtual_sync_job)
            except Exception:
                pass
            self._virtual_sync_job = None
        # 先把当前窗口的编辑写回旧文档，再替换文档；否则 _load_start 会把旧窗口写进新文档。
        self.flush_virtual(force=True)
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        self._virtual_doc = ChunkedDocument(text.split("\n"), chunk=self._virtual_chunk)
        self._virtual_full_lines = self._virtual_doc.lines
        self._virtual_total = self._virtual_doc.total
        self._virtual_start = 0
        self._virtual_dirty = False
        self._dirty = False
        self._virtual_sem_cache.clear()
        self._invalidate_semantic_parse()
        self._load_start(0, anchor=0.0)
        try:
            self.text.edit_modified(False)
            self._update_virtual_badge()
            self.highlight()
            self.redraw_lineno()
        except Exception:
            note_swallowed("virtual reload")

    def _update_virtual_badge(self):
        end = min(self._virtual_total, self._virtual_start + self._virtual_window_len)
        try:
            self.breadcrumb.configure(
                text="%s  ·  分块编辑 %d-%d / %d 行  ·  逐块自动回写（Ctrl+S 保存整文件）" % (
                    os.path.basename(self.path or "大文件"),
                    self._virtual_start + 1, end, self._virtual_total),
                fg=THEME["orange"])
        except Exception:
            pass

    def _virtual_finish(self):
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n".join(self._virtual_full_lines[
                self._virtual_start:self._virtual_start + self._virtual_chunk]))
            self._virtual_window_len = len(self._virtual_full_lines[
                self._virtual_start:self._virtual_start + self._virtual_chunk])
            self.text.edit_reset()
            self.text.edit_modified(False)
            self._suspend = False
        except Exception:
            pass
        self._update_virtual_badge()
        self.highlight()
        self.redraw_lineno()

    def redraw_lineno(self):
        c = self.lineno
        c.delete("all")
        try:
            top_local = int(self.text.index("@0,0").split(".")[0])
            bottom_local = int(self.text.index("@0,%d" % max(self.text.winfo_height(), 10)).split(".")[0])
        except Exception:
            return
        total_local = max(1, self._virtual_total - self._virtual_start)
        bottom_local = min(bottom_local + 1, total_local)
        cw = max(c.winfo_width(), 92 if getattr(self.app, "inlay_hints", True) else 62)
        ch = max(c.winfo_height(), 10)
        c.create_line(cw - 1, 0, cw - 1, ch, fill=THEME["border_soft"])
        for local in range(max(1, top_local), bottom_local + 1):
            info = self.text.dlineinfo(f"{local}.0")
            if not info:
                continue
            y, hh = info[1], info[3]
            abs_line = self._virtual_start + local
            c.create_text(cw - 12, y + hh // 2, text=str(abs_line), anchor="e",
                          fill=THEME["muted_dim"],
                          font=(self.app.mono, self.app.font_size - 1))

    # ---------- 换块 ----------
    def _on_yscroll(self, first, last):
        try:
            self.scroll.set(first, last)
        except Exception:
            pass
        self.redraw_lineno()
        try:
            self.minimap.redraw_view()
        except Exception:
            pass
        if getattr(self, "_virtual_loading", False):
            return
        try:
            top, bottom = float(first), float(last)
        except Exception:
            return
        if top < 0.12 and self._virtual_start > 0:
            self._shift_virtual(-max(1, self._virtual_chunk // 3), anchor=0.3)
            return
        if bottom > 0.88 and self._virtual_start + self._virtual_chunk < self._virtual_total:
            self._shift_virtual(max(1, self._virtual_chunk // 3), anchor=0.7)
            return
        self._schedule_highlight(110)

    def _shift_virtual(self, delta, anchor=0.5):
        self.flush_virtual()
        new_start = self._virtual_doc.clamp_start(self._virtual_start + int(delta))
        if new_start == self._virtual_start:
            return
        self._load_start(new_start, anchor=anchor)

    def _load_start(self, new_start, anchor=0.5):
        self.flush_virtual()
        new_start = self._virtual_doc.clamp_start(new_start)
        self._virtual_loading = True
        try:
            self._virtual_start = new_start
            window = self._virtual_doc.window(new_start)
            self._virtual_window_len = len(window)
            self._virtual_total = self._virtual_doc.total
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n".join(window))
            try:
                self.text.edit_modified(False)
            except Exception:
                pass
            self._virtual_dirty = False
            if self._virtual_sync_job:
                try:
                    self.after_cancel(self._virtual_sync_job)
                except Exception:
                    pass
                self._virtual_sync_job = None
            self._update_virtual_badge()
            self.text.yview_moveto(anchor)
            self.redraw_lineno()
            self._schedule_highlight(20)
            self._schedule_semantic_parse()
        finally:
            self.after(30, lambda: setattr(self, "_virtual_loading", False))

    def goto_virtual_line(self, line):
        line = max(1, min(int(line), self._virtual_total))
        if not (self._virtual_start < line <= self._virtual_start + self._virtual_chunk):
            new_start = max(0, min(line - 1 - self._virtual_chunk // 3,
                                   max(0, self._virtual_total - self._virtual_chunk)))
            self._load_start(new_start, anchor=0.3)
        local = line - self._virtual_start
        self.text.mark_set("insert", f"{local}.0")
        self.text.see("insert")
        self.text.focus_set()

    # ---------- v3.7.6：跨块查找 / 替换 ----------
    def _virtual_cursor(self):
        """返回整文件 0 起行号与列号。"""
        try:
            local_line, col = self.text.index("insert").split(".")
            line = self._virtual_start + int(local_line) - 1
            return max(0, line), max(0, int(col))
        except Exception:
            return self._virtual_start, 0

    def _virtual_matches(self, needle, start_line=0, start_col=0, limit=None):
        """逐行产出 (整文件 0 起行号, 列号)；不一次性构造全量命中列表。"""
        needle = str(needle or "")
        if not needle:
            return
        lower = needle.casefold()
        size = len(needle)
        total = max(0, int(self._virtual_total))
        for i in range(max(0, int(start_line)), total):
            line = self._virtual_full_lines[i] if i < len(self._virtual_full_lines) else ""
            hay = line.casefold()
            pos = hay.find(lower, max(0, int(start_col)) if i == start_line else 0)
            while pos >= 0:
                yield i, pos
                if limit is not None and limit > 0:
                    limit -= 1
                    if limit <= 0:
                        return
                pos = hay.find(lower, pos + max(1, size))

    def find_all(self, needle):
        """v3.7.6：统计全文命中，只给当前窗口打 tag，避免 26 万行全量标色。"""
        self.flush_virtual()
        self.text.tag_remove("search", "1.0", "end")
        if not needle:
            return 0
        count = 0
        start_line = self._virtual_start
        end_line = start_line + max(0, self._virtual_window_len)
        for line, col in self._virtual_matches(needle):
            count += 1
            if start_line <= line < end_line:
                local = line - start_line + 1
                try:
                    self.text.tag_add("search", f"{local}.{col}",
                                      f"{local}.{col}+{len(needle)}c")
                except Exception:
                    note_swallowed("virtual search tag")
        try:
            self.text.tag_raise("search")
        except Exception:
            pass
        return count

    def _select_virtual_match(self, line, col, needle):
        """跳到命中并选中；line 是整文件 0 起行号。"""
        self.goto_virtual_line(line + 1)
        local = line - self._virtual_start + 1
        start = f"{max(1, local)}.{max(0, int(col))}"
        end = f"{start}+{len(needle)}c"
        try:
            self.text.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        try:
            self.text.tag_add("sel", start, end)
        except Exception:
            note_swallowed("virtual select")
            return
        self.text.mark_set("insert", end)
        self.text.see(start)
        self.text.focus_set()

    def find_next(self, needle):
        """v3.7.6：从当前绝对行列向后找，找不到再从文件头回绕。"""
        needle = str(needle or "")
        if not needle:
            return
        self.flush_virtual()
        line, col = self._virtual_cursor()
        match = next(self._virtual_matches(needle, line, col), None)
        if match is None and (line > 0 or col > 0):
            # 回绕：先找当前行光标之前，再找前面的行。
            for i in range(0, min(line + 1, max(0, self._virtual_total))):
                hay = (self._virtual_full_lines[i] if i < len(self._virtual_full_lines) else "").casefold()
                lower = needle.casefold()
                end = col if i == line else len(hay)
                pos = hay.find(lower, 0, max(0, end))
                if pos >= 0:
                    match = (i, pos)
                    break
        if match is not None:
            self._select_virtual_match(match[0], match[1], needle)

    def replace_all(self, needle, replacement, nocase=True):
        """v3.7.6：在完整行列表上替换，再刷新当前块。返回替换次数。"""
        needle = str(needle or "")
        if not needle:
            return 0
        self.flush_virtual(force=True)
        count = self._virtual_doc.replace_all(needle, replacement, nocase=nocase)
        if not count:
            return 0
        self._virtual_total = self._virtual_doc.total
        self._virtual_full_lines = self._virtual_doc.lines
        self._virtual_start = self._virtual_doc.clamp_start(self._virtual_start)
        window = self._virtual_doc.window(self._virtual_start)
        self._virtual_window_len = len(window)
        self._virtual_loading = True
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n".join(window))
            try:
                self.text.edit_modified(False)
            except Exception:
                pass
        finally:
            self.after(30, lambda: setattr(self, "_virtual_loading", False))
        self._virtual_dirty = True
        self._dirty = True
        try:
            self.app.mark_dirty(self)
        except Exception:
            note_swallowed("virtual replace dirty")
        self._invalidate_semantic_parse()
        self._update_virtual_badge()
        try:
            self.highlight()
            self.redraw_lineno()
        except Exception:
            note_swallowed("virtual replace redraw")
        self._schedule_semantic_parse()
        if self._virtual_sync_job:
            try:
                self.after_cancel(self._virtual_sync_job)
            except Exception:
                pass
        self._virtual_sync_job = self.after(450, self.flush_virtual)
        return count

    def focus_text(self):
        self.text.focus_set()

    def virtual_text(self):
        self.flush_virtual(force=True)
        return self._virtual_doc.text()


def read_large_lines(path):
    """读取大文件为完整行列表；仅在超过阈值、进入分块编辑时调用。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read().split("\n")
