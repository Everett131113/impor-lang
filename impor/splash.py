# -*- coding: utf-8 -*-
"""启动过场动画（v3.6，P2 之后的界面暖场）。

打开图形编辑器前先显示一个无边框、自绘的启动画面：
    IMPOR IS COMING
    语言宪法 P2 · 人类友好的诊断系统

设计原则：
  - 纯 tk.Canvas 自绘，不依赖 ttk / 图片资源，任何主题下都不会出现白边；
  - 不阻塞：所有动画都走 after()，主窗口在后台构建完成后才淡出；
  - 可跳过：单击 / Esc / Space 立即结束；
  - 可关闭：设置 IMPOR_NO_SPLASH=1（自动化截图、回归时用）。
"""

import os
import time
import tkinter as tk
import tkinter.font as tkfont

from . import __version__
from .icons import apply_window_icon

BG = "#0b0e14"
PANEL = "#0f141c"
BORDER = "#1c232e"
FG = "#eef2f8"
MUTED = "#6b7688"
MUTED_DIM = "#4d5666"
ACCENT = "#7aa2f7"
ACCENT_DIM = "#1b2b45"
GREEN = "#9ece6a"

W, H = 620, 360
BAR_W, BAR_H = 420, 4


def splash_enabled():
    """环境变量 IMPOR_NO_SPLASH=1 / true 时跳过过场。"""
    v = (os.environ.get("IMPOR_NO_SPLASH") or "").strip().lower()
    return v not in ("1", "true", "yes", "on")


def _font(size, weight="normal", family=None):
    return (family or "Segoe UI", size, weight)


class SplashScreen:
    """自绘启动画面。show() 立即可见，finish() 在动画结束后回调。"""

    HOLD_MS = 280
    FADE_OUT_MS = 120
    FRAME_MS = 16

    def __init__(self, root):
        self.root = root
        self.top = tk.Toplevel(root)
        apply_window_icon(self.top)
        self.top.withdraw()
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
        except Exception:
            pass
        self.top.configure(bg=BG)
        self.canvas = tk.Canvas(self.top, width=W, height=H, bg=BG,
                                highlightthickness=1, highlightbackground=BORDER)
        self.canvas.pack(fill="both", expand=True)

        self._done = False
        self._exiting = False
        self._t0 = None
        self._exit_t0 = None
        self._dot_i = 0
        self._phase = -1
        self._on_done = None
        self._skip_requested = False

        self._center()
        self._draw()
        self._bind()

    # ---------- 位置与绘制 ----------
    def _center(self):
        try:
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            x = max(0, (sw - W) // 2)
            y = max(0, (sh - H) // 2 - 24)      # 视觉重心略高于正中
            self.top.geometry("%dx%d+%d+%d" % (W, H, x, y))
        except Exception:
            pass

    def _draw(self):
        c = self.canvas

        # 顶部强调线
        c.create_rectangle(0, 0, W, 3, fill=ACCENT, outline="")
        # 背景细分：左侧竖条 + 右下淡光斑
        c.create_rectangle(0, 3, 6, H, fill=PANEL, outline="")
        c.create_oval(W - 190, H - 150, W + 90, H + 90, fill="#101927", outline="")

        # 品牌区
        c.create_rectangle(48, 86, 148, 186, fill=ACCENT_DIM, outline=ACCENT)
        c.create_text(98, 130, text="I++", font=_font(34, "bold"), fill=FG)
        c.create_text(98, 166, text="IMPOR", font=_font(9, "bold"), fill=ACCENT)

        c.create_text(178, 96, text="IMPOR (I++)", font=_font(10, "bold"),
                      fill=MUTED, anchor="w")
        self._title = c.create_text(178, 128, text="IMPOR IS COMING",
                                    font=_font(24, "bold"), fill=FG, anchor="w")
        c.create_text(178, 164, text="语言宪法 P2 · 人类友好的诊断系统",
                      font=_font(11), fill=MUTED, anchor="w")
        c.create_text(178, 188, text="四段式诊断 · 上下文片段 · 终端彩色",
                      font=_font(10), fill=MUTED_DIM, anchor="w")

        # 进度条
        c.create_rectangle(48, 246, 48 + BAR_W, 246 + BAR_H, fill=BORDER, outline="")
        self._bar = c.create_rectangle(48, 246, 48, 246 + BAR_H, fill=ACCENT, outline="")
        self._pct = c.create_text(48 + BAR_W, 236, text="0%", font=_font(10),
                                  fill=MUTED, anchor="e")
        self._status = c.create_text(48, 274, text="正在装载诊断知识库…",
                                     font=_font(10), fill=MUTED, anchor="w")
        c.create_text(W - 48, 274, text="v" + __version__, font=_font(10),
                      fill=MUTED_DIM, anchor="e")
        c.create_text(48, H - 26, text="单击 / Esc 跳过", font=_font(9),
                      fill=MUTED_DIM, anchor="w")
        c.create_text(W - 48, H - 26, text="独立 exe 不依赖 Python",
                      font=_font(9), fill=MUTED_DIM, anchor="e")

    def _bind(self):
        for seq in ("<Button-1>", "<Escape>", "<space>"):
            self.top.bind(seq, lambda e: self.finish())

    # ---------- 显示与动画 ----------
    def show(self):
        """立刻把首帧画出来（此时还不启动动画，等主界面构建完再 start_animation）。"""
        self.top.deiconify()
        self.top.lift()
        self._set_alpha(1.0)
        try:
            self.top.update_idletasks()
            self.top.update()
        except Exception:
            pass
        return self

    def start_animation(self):
        """主界面构建完成后调用：从 0 开始跑进度，结束后淡出。"""
        if self._t0 is not None or self._done:
            return
        if self._skip_requested:
            self._begin_exit(fast=True)
            return
        self._t0 = time.monotonic()
        self.top.after(self.FRAME_MS, self._tick)
        self.top.after(340, self._cycle_dots)

    def _set_alpha(self, a):
        try:
            self.top.attributes("-alpha", max(0.0, min(1.0, a)))
        except Exception:
            pass

    def _tick(self):
        if self._done:
            return
        now = time.monotonic()
        el = (now - self._t0) * 1000.0

        # 淡入已由 show() 直接设为不透明，这里只推进进度
        prog = min(1.0, el / self.HOLD_MS)
        eased = 1 - (1 - prog) ** 2
        width = int(BAR_W * eased)
        try:
            self.canvas.coords(self._bar, 48, 246, 48 + width, 246 + BAR_H)
            self.canvas.itemconfigure(self._pct, text="%d%%" % int(eased * 100))
        except Exception:
            pass

        phase = 0 if prog < 0.34 else (1 if prog < 0.72 else 2)
        if phase != self._phase:
            self._phase = phase
            text = ("正在装载诊断知识库…", "正在构建编辑器界面…", "准备就绪，马上开始")[phase]
            try:
                self.canvas.itemconfigure(self._status, text=text)
            except Exception:
                pass
            if phase == 2:
                try:
                    self.canvas.itemconfigure(self._bar, fill=GREEN)
                except Exception:
                    pass

        if el >= self.HOLD_MS:
            self._begin_exit()
            return
        self.top.after(self.FRAME_MS, self._tick)

    def _cycle_dots(self):
        if self._done or self._exiting:
            return
        self._dot_i = (self._dot_i + 1) % 4
        try:
            self.canvas.itemconfigure(self._title,
                                      text="IMPOR IS COMING" + "." * self._dot_i)
        except Exception:
            pass
        self.top.after(340, self._cycle_dots)

    # ---------- 结束 ----------
    def set_on_done(self, cb):
        """注册淡出结束后的回调（在主窗口 deiconify 前调用）。"""
        self._on_done = cb

    def finish(self):
        """请求结束过场（单击 / Esc / Space 触发）。"""
        if self._done or self._exiting:
            return
        if self._t0 is None:
            self._skip_requested = True
            return
        self._begin_exit(fast=True)

    def _begin_exit(self, fast=False):
        if self._done or self._exiting:
            return
        self._exiting = True
        self._exit_t0 = time.monotonic()
        self._fade_ms = 90 if fast else self.FADE_OUT_MS
        self._fade_step()

    def _fade_step(self):
        if self._done:
            return
        el = (time.monotonic() - self._exit_t0) * 1000.0
        k = min(1.0, el / max(1, self._fade_ms))
        self._set_alpha(1.0 - k)
        if k >= 1.0:
            self._destroy()
            return
        self.top.after(self.FRAME_MS, self._fade_step)

    def _destroy(self):
        if self._done:
            return
        self._done = True
        try:
            self.top.destroy()
        except Exception:
            pass
        cb = getattr(self, "_on_done", None)
        if cb:
            try:
                cb()
            except Exception:
                pass


def show_splash(root):
    """按环境变量决定是否显示过场；返回 SplashScreen 或 None。"""
    if not splash_enabled():
        return None
    try:
        return SplashScreen(root).show()
    except Exception:
        return None
