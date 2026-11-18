# -*- coding: utf-8 -*-
"""大文件分块缓冲（v3.7.6）。

Tk Text 只承载当前窗口；完整行列表留在 Python 内存，窗口切换只替换这一段。
本模块不依赖 tkinter，便于对 10 MB 级文件做无界面回归测试。
"""

from __future__ import annotations

import re

DEFAULT_CHUNK_LINES = 3000
MIN_CHUNK_LINES = 200
MAX_WINDOW_CHARS = 220_000


def choose_chunk_lines(lines, sample=None):
    """按平均行长选择窗口行数，限制单窗口字符量，避免 3000 行全是超长行。"""
    if sample is None:
        sample = lines[:80] if lines else []
    lengths = [len(x) for x in sample if x]
    avg = (sum(lengths) / len(lengths)) if lengths else 80.0
    avg = max(20.0, avg)
    by_chars = int(MAX_WINDOW_CHARS / avg)
    return max(MIN_CHUNK_LINES, min(DEFAULT_CHUNK_LINES, by_chars))


def split_chunks(lines, chunk):
    chunk = max(1, int(chunk))
    for i in range(0, len(lines), chunk):
        yield i, lines[i:i + chunk]


class ChunkedDocument:
    """完整行列表 + 当前窗口；所有行号按整文件绝对行号处理。"""

    def __init__(self, lines, chunk=None):
        self.lines = list(lines)
        self.total = len(self.lines)
        self.chunk = int(chunk or choose_chunk_lines(self.lines))
        self.chunk = max(1, min(self.chunk, max(1, self.total)))
        self.start = 0

    def window(self, start=None, chunk=None):
        start = self.start if start is None else int(start)
        chunk = self.chunk if chunk is None else int(chunk)
        start = max(0, min(start, max(0, self.total - 1)))
        return self.lines[start:start + chunk]

    def replace_window(self, start, old_count, new_lines):
        """把窗口写回完整行列表；返回新窗口行数。"""
        start = max(0, min(int(start), max(0, self.total)))
        old_count = max(0, int(old_count))
        new_lines = list(new_lines)
        self.lines[start:start + old_count] = new_lines
        self.total = len(self.lines)
        return len(new_lines)

    def clamp_start(self, start):
        if self.total <= self.chunk:
            return 0
        return max(0, min(int(start), self.total - self.chunk))

    def text(self):
        return "\n".join(self.lines)

    def line(self, line_no):
        """1 起绝对行号。"""
        line_no = max(1, min(int(line_no), max(1, self.total)))
        return self.lines[line_no - 1]

    def replace_all(self, needle, replacement, nocase=True):
        """在完整行列表中做字面量全文替换，返回替换次数。

        跨块窗口只显示当前 3000 行，因此替换必须在这里一次完成，不能只改
        Tk Text。默认不区分大小写，与查找条的 nocase 语义一致。替换文本含
        换行时会重建行列表并更新 total。
        """
        needle = str(needle or "")
        if not needle:
            return 0
        replacement = str(replacement or "").replace("\r\n", "\n").replace("\r", "\n")
        flags = re.IGNORECASE if nocase else 0
        pattern = re.compile(re.escape(needle), flags)
        if "\n" in needle or "\n" in replacement:
            whole = self.text()
            updated, count = pattern.subn(lambda m: replacement, whole)
            if count:
                self.lines = updated.split("\n")
                self.total = len(self.lines)
            return count
        count = 0
        for i, line in enumerate(self.lines):
            updated, n = pattern.subn(lambda m: replacement, line)
            if n:
                self.lines[i] = updated
                count += n
        return count


def should_use_chunked(size_bytes, threshold_kb=2000, mode="auto"):
    """只有 auto 且超过阈值才自动分块；full 完整打开，ask 交给调用方询问。"""
    try:
        threshold = max(1, int(threshold_kb)) * 1024
        return str(mode or "auto") == "auto" and int(size_bytes) > threshold
    except Exception:
        return False
