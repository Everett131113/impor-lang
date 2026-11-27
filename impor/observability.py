# -*- coding: utf-8 -*-
"""Impor 内部“被吞异常”诊断记录。

默认写入系统临时目录的 impor_swallowed.log（单文件上限 256 KB，超出轮转 .1）。
设置 IMPOR_TRACE_SWALLOW=0 可关闭；设置 IMPOR_DEBUG=1 时同时打印到 stderr。
只应在 except 块内调用；不改变原控制流。
"""
import os
import sys
import tempfile
import time

_LOG = os.path.join(tempfile.gettempdir(), "impor_swallowed.log")
_MAX = 256 * 1024


def swallowed_log_path():
    """当前被吞异常日志路径（诊断面板与 issue 报告都指向这里）。"""
    return _LOG


def swallowed_tail(limit=120):
    """返回最近 limit 行原文；先读轮转文件 .1 的末尾补齐，再读当前文件。"""
    out = []
    for p in (_LOG + ".1", _LOG):
        try:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                out.extend([ln.rstrip("\n") for ln in f.readlines()])
        except OSError:
            continue
    return out[-limit:] if limit else out


def swallowed_counts():
    """按 area 汇总条数，用于诊断面板「哪里最常吞异常」一眼看出来。"""
    counts = {}
    for line in swallowed_tail(0):
        parts = line.split(" ")
        if len(parts) >= 4:
            counts[parts[2]] = counts.get(parts[2], 0) + 1
    return counts


def note_swallowed(area, exc=None):
    if os.environ.get("IMPOR_TRACE_SWALLOW", "1") == "0":
        return
    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        return
    try:
        if os.path.exists(_LOG) and os.path.getsize(_LOG) > _MAX:
            os.replace(_LOG, _LOG + ".1")
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s %s: %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), area,
                type(exc).__name__, exc))
    except OSError:
        pass
    if os.environ.get("IMPOR_DEBUG"):
        print("[impor] 已忽略异常 %s: %s: %s" % (area, type(exc).__name__, exc),
              file=sys.stderr)
