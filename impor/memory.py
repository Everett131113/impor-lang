# -*- coding: utf-8 -*-
"""Impor 自动内存管理（v3.2）。

Impor 解释器的值由 CPython 托管（引用计数 + 分代 GC）。本模块在其之上
提供 Impor 语义层的「自动内存管理」：

  · 分配统计：容器 / 实例 / 闭包 / 模块创建时登记，记录分配次数与峰值；
  · 阈值自动回收：分配数达到阈值即自动 gc.collect() 并清理失效缓存；
  · 缓存清理钩子：解释器注册清理器（失效模块缓存、临时状态等）；
  · 存活采样：stats() 时按类型统计当前存活的 Impor 级对象；
  · 语言内建：mem() / memstats() / gc() / autogc(limit)。

设计取向：安全优先——不接管 CPython 已有回收，只在阈值处触发「额外回收 +
缓存清理」，因此不会误回收正被表达式使用的临时值。
"""

import gc
import sys
import time

# 统计时纳入「Impor 级对象」的类型名（避免 import 解释器造成循环依赖）
_IMPOR_TYPES = (
    "ImporInstance", "ImporClass", "Closure", "Module",
    "Vec2", "Color", "UserExceptionType", "FunctionDef",
)


class MemoryManager:
    """Impor 自动内存管理器（阈值触发 + 缓存清理 + 统计）。"""

    def __init__(self, threshold=20000, enabled=True):
        self.threshold = int(threshold)
        self.enabled = bool(enabled)
        self.alloc_count = 0
        self.alloc_since_gc = 0
        self.peak_alloc = 0
        self.collections = 0
        self.last_gc_time = 0.0
        self.cleaners = []
        self.kinds = {}
        self.bytes_estimate = 0
        self.last_freed = 0
        self.freed_total = 0

    # ---------- 分配登记 ----------
    def alloc(self, kind, size=48):
        """登记一次 Impor 级对象分配；达到阈值时自动回收。"""
        self.alloc_count += 1
        self.alloc_since_gc += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        self.bytes_estimate += int(size)
        if self.alloc_count > self.peak_alloc:
            self.peak_alloc = self.alloc_count
        if self.enabled and self.alloc_since_gc >= self.threshold:
            self.collect(reason="auto")
        return None

    # ---------- 清理钩子 ----------
    def add_cleaner(self, fn):
        if fn not in self.cleaners:
            self.cleaners.append(fn)

    # ---------- 回收 ----------
    def collect(self, reason="manual"):
        """触发一次回收：CPython gc + 注册的缓存清理器。"""
        freed = gc.collect()
        self.last_freed = freed
        self.freed_total += freed
        for fn in list(self.cleaners):
            try:
                fn()
            except Exception:
                pass
        self.collections += 1
        self.alloc_since_gc = 0
        self.last_gc_time = time.time()
        return freed

    def _auto(self):
        if self.enabled and self.alloc_since_gc >= self.threshold:
            self.collect(reason="auto")

    # ---------- 统计 ----------
    @staticmethod
    def live_objects():
        """按类型名统计当前存活的 Impor 级对象（采样，成本较高）。"""
        out = {}
        for obj in gc.get_objects():
            tn = type(obj).__name__
            if tn in _IMPOR_TYPES:
                out[tn] = out.get(tn, 0) + 1
        return out

    def live_count(self):
        n = 0
        for v in self.live_objects().values():
            n += v
        return n

    def stats(self):
        return {
            "alloc": self.alloc_count,
            "peak": self.peak_alloc,
            "live": self.live_count(),
            "live_by_type": self.live_objects(),
            "live_scope": "Impor 实例/类/闭包/模块/函数定义",
            "collections": self.collections,
            "freed": self.last_freed,
            "freed_total": self.freed_total,
            "threshold": self.threshold,
            "auto": self.enabled,
            "since_gc": self.alloc_since_gc,
            "kinds": dict(self.kinds),
        }

    def report(self):
        st = self.stats()
        return ("内存: 累计分配 %d 个对象（峰值 %d） | 存活 %d（%s） | 回收 %d 次（循环引用 %d 个）"
                "\n  说明: list/dict/str 等容器由 CPython 引用计数即时释放，不进入「存活」采样，"
                "因此 set x = 0 后存活数不变是正常的；gc() 处理的是循环引用与解释器缓存。"
                % (st["alloc"], st["peak"], st["live"], st["live_scope"],
                   st["collections"], st["freed_total"]))

    # ---------- 配置 ----------
    def set_threshold(self, limit):
        try:
            limit = int(limit)
        except Exception:
            return self.threshold
        self.threshold = max(1, limit)
        self.alloc_since_gc = 0
        return self.threshold

    def set_enabled(self, flag):
        self.enabled = bool(flag)
        return self.enabled


_default = None


def default_manager():
    """进程级默认管理器（供 REPL / CLI 概览使用）。"""
    global _default
    if _default is None:
        _default = MemoryManager()
    return _default


def process_memory_report():
    """进程内存概况（无解释器上下文时使用）。"""
    mgr = default_manager()
    st = mgr.stats()
    rss = "-"
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        fn = getattr(k32, "K32GetProcessMemoryInfo", None)
        if fn is None:
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if fn(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            rss = "%.1f MB" % (pmc.WorkingSetSize / 1048576.0)
    except Exception:
        pass
    return ("进程内存: RSS %s | Python gc 代: %s | %s"
            % (rss, gc.get_count(), mgr.report()))
