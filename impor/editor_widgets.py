# -*- coding: utf-8 -*-
"""Tk 自绘控件层：按钮、标签栏、活动栏、缩略图、细滚动条。

从 editor.py 拆出，只依赖主题色板和 App 暴露的接口，不持有业务状态。
"""
import os
import tkinter as tk
import tkinter.font as tkfont

from .editor_theme import THEME, DARK_THEME, LIGHT_THEME, HIGH_CONTRAST_THEME

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)

class FlatButton(tk.Canvas):
    """自绘圆角按钮（深色主题，hover 高亮）。"""

    def __init__(self, master, text, command=None, kind="ghost", width=None,
                 height=30, padx=14):
        self._base_bg = master.cget("bg")
        label = text
        w = width or max(58, len(label) * 12 + padx * 2)
        super().__init__(master, width=w, height=height, bg=self._base_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.kind = kind
        self.label = label
        self.command = command
        self._hover = False
        self._active = True
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self._render()

    def _colors(self):
        if not self._active:
            return THEME["panel"], THEME["muted"], THEME["border"]
        if self.kind == "primary":
            bg = THEME["accent_hover"] if self._hover else THEME["accent"]
            return bg, THEME["bg"], bg
        if self.kind == "success":
            bg = THEME["green"] if not self._hover else "#b3e08a"
            return bg, THEME["bg"], bg
        if self.kind == "danger":
            bg = THEME["red"] if not self._hover else "#ffa0b2"
            return bg, THEME["bg"], bg
        bg = THEME["hover"] if self._hover else THEME["panel_alt"]
        return bg, THEME["fg"], THEME["border"]

    def _render(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill, fg, outline = self._colors()
        round_rect(self, 1, 1, w - 1, h - 1, 6, fill=fill, outline=outline)
        self.create_text(w // 2, h // 2, text=self.label, fill=fg,
                         font=("Microsoft YaHei UI", 9))

    def _enter(self, _e=None):
        self._hover = True
        self._render()

    def _leave(self, _e=None):
        self._hover = False
        self._render()

    def _press(self, _e=None):
        self.move("all", 1, 1)

    def _release(self, e=None):
        self._render()
        if self._active and self.command:
            try:
                if 0 <= e.x <= int(self["width"]) and 0 <= e.y <= int(self["height"]):
                    self.command()
            except Exception:
                pass

    def set_active(self, flag):
        self._active = bool(flag)
        self._render()

    def set_text(self, text):
        self.label = text
        self._render()

class TabStrip(tk.Canvas):
    """自绘编辑器标签栏：活动标签顶部 accent 线、未保存圆点、关闭按钮、横向滚动。"""

    HEIGHT = 38
    CLOSE_ZONE = 22

    def __init__(self, master, app, group="main"):
        super().__init__(master, bg=THEME["titlebar"], height=self.HEIGHT,
                         highlightthickness=0, bd=0)
        self.app = app
        self.group = group
        self._font = tkfont.Font(family=app.mono, size=10)
        self._font_bold = tkfont.Font(family=app.mono, size=10, weight="bold")
        self._hover = None
        self._offset = 0
        self._hit = []
        self._new_btn = (0, 0, 0, 0)
        self._drag_pane = None
        self._drag_x = 0
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._drag_motion)
        self.bind("<ButtonRelease-1>", self._drag_end)
        self.bind("<Button-2>", self._middle)
        self.bind("<Button-3>", self._menu)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Configure>", lambda e: self.redraw())

    def _set_hover(self, pane):
        if self._hover is not pane:
            self._hover = pane
            self.redraw()

    def _tab_width(self, pane):
        return self._font.measure(pane.tab_title) + 66

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 10)
        h = self.HEIGHT
        self.create_rectangle(0, 0, w, h, fill=THEME["titlebar"], width=0)
        self.create_line(0, h - 1, w, h - 1, fill=THEME["border"])
        x = 8 - self._offset
        self._hit = []
        for pane in self.app.group_panes(self.group):
            tw = self._tab_width(pane)
            if x + tw < 0 or x > w:
                x += tw
                continue
            active = pane is self.app.active_pane and pane.group == self.group
            if active:
                self.create_rectangle(x, 0, x + tw, h - 1, fill=THEME["tab_active"], width=0)
                self.create_rectangle(x, 0, x + tw, 2, fill=THEME["accent"], width=0)
            else:
                fill = THEME["hover_soft"] if pane is self._hover else THEME["tab_inactive"]
                self.create_rectangle(x, 0, x + tw, h - 1, fill=fill, width=0)
                self.create_line(x + tw, 0, x + tw, h - 1, fill=THEME["border_soft"])
            tx = x + 14
            if pane._dirty:
                self.create_oval(tx - 3, h // 2 - 3, tx + 3, h // 2 + 3,
                                 fill=THEME["orange"], width=0)
                tx += 11
            self.create_text(tx, h // 2, text=pane.tab_title, anchor="w",
                             fill=THEME["fg_bright"] if active else THEME["muted"],
                             font=self._font_bold if active else self._font)
            close_fg = THEME["fg"] if (active or pane is self._hover) else THEME["muted_dim"]
            cx = x + tw - 14
            self.create_line(cx - 4, h // 2 - 4, cx + 4, h // 2 + 4, fill=close_fg)
            self.create_line(cx + 4, h // 2 - 4, cx - 4, h // 2 + 4, fill=close_fg)
            self._hit.append((pane, x, x + tw, cx))
            x += tw
        # 新建按钮
        if x + 26 < w - 10:
            self.create_rectangle(x + 2, h // 2 - 8, x + 22, h // 2 + 8,
                                  fill=THEME["tab_inactive"], outline=THEME["border_soft"])
            self.create_text(x + 12, h // 2, text="+", fill=THEME["muted"],
                             font=("Consolas", 11, "bold"))
            self._new_btn = (x + 2, h // 2 - 8, x + 22, h // 2 + 8)
        else:
            self._new_btn = (0, 0, 0, 0)
        # 右侧：当前文件目录（淡色）
        pane = self.app.active_pane
        if pane is not None and pane.path:
            rel = os.path.relpath(pane.path, ROOT_DIR)
            if len(rel) > 42:
                rel = "…" + rel[-41:]
            self.create_text(w - 12, h // 2, text=rel, anchor="e",
                             fill=THEME["muted_dim"], font=(self.app.mono, 8))

    def _click(self, e):
        for pane, x1, x2, cx in self._hit:
            if x1 <= e.x < x2:
                if abs(e.x - cx) <= 9:
                    self.app.close_pane(pane)
                else:
                    self.app.select_pane(pane)
                    self._drag_pane = pane
                    self._drag_x = e.x
                return
        bx1, by1, bx2, by2 = self._new_btn
        if bx1 <= e.x <= bx2 and by1 <= e.y <= by2:
            self.app.open_new_tab(group=self.group)
            return
        self.app.select_pane(None)

    def _drag_motion(self, e):
        if not self._drag_pane:
            return
        if abs(e.x - self._drag_x) < 18:
            return
        target = None
        for pane, x1, x2, cx in self._hit:
            if x1 <= e.x < x2 and pane.group == self.group:
                target = pane
                break
        if target and target is not self._drag_pane:
            try:
                self.app.panes.remove(self._drag_pane)
                self.app.panes.insert(self.app.panes.index(target), self._drag_pane)
                self.redraw()
            except ValueError:
                pass
        self._drag_x = e.x

    def _drag_end(self, e):
        self._drag_pane = None

    def _middle(self, e):
        for pane, x1, x2, cx in self._hit:
            if x1 <= e.x < x2:
                self.app.close_pane(pane)
                return

    def _menu(self, e):
        target = None
        for pane, x1, x2, cx in self._hit:
            if x1 <= e.x < x2:
                target = pane
                break
        if target is None:
            return
        self.app.select_pane(target)
        menu = tk.Menu(self, tearoff=0, bg=THEME["panel"], fg=THEME["fg"],
                       activebackground=THEME["accent_soft"], activeforeground=THEME["fg_bright"])
        menu.add_command(label="左移标签", command=lambda: self.app._move_tab(-1))
        menu.add_command(label="右移标签", command=lambda: self.app._move_tab(1))
        menu.add_separator()
        menu.add_command(label="复制文件路径", command=lambda: self.app._copy_pane_path(target))
        menu.add_command(label="在资源管理器打开",
                         command=lambda: self.app._reveal_path(target.path) if target.path else None)
        menu.add_separator()
        menu.add_command(label="关闭右侧标签",
                         command=lambda: self.app._close_tabs_right(target))
        menu.add_command(label="关闭其他标签",
                         command=lambda: [self.app.close_pane(p) for p in list(self.app.group_panes(self.group)) if p is not target])
        menu.add_command(label="关闭全部标签",
                         command=lambda: [self.app.close_pane(p) for p in list(self.app.group_panes(self.group))])
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _motion(self, e):
        hover = None
        for pane, x1, x2, cx in self._hit:
            if x1 <= e.x < x2:
                hover = pane
                break
        self._set_hover(hover)

    def _wheel(self, e):
        total = sum(self._tab_width(p) for p in self.app.group_panes(self.group)) + 60
        room = max(0, total - self.winfo_width())
        self._offset = max(0, min(room, self._offset - int(e.delta / 120) * 40))
        self.redraw()

class ActivityBar(tk.Canvas):
    """VS Code 式活动栏：自绘矢量图标 + 选中左侧 accent 竖条。"""

    WIDTH = 46
    ITEM_H = 46

    def __init__(self, master, app, items):
        super().__init__(master, bg=THEME["activity"], width=self.WIDTH,
                         highlightthickness=0, bd=0)
        self.app = app
        self.items = items          # [(key, tooltip, command)]
        self.active = items[0][0]
        self._hover = None
        self._tip = None
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._leave_all())
        self.bind("<Configure>", lambda e: self.redraw())

    def index_of(self, y):
        y0 = 14
        i = int((y - y0) // self.ITEM_H)
        if 0 <= i < len(self.items):
            return i
        return None

    def set_active(self, key):
        self.active = key
        self.redraw()

    def redraw(self):
        self.delete("all")
        h = max(self.winfo_height(), 10)
        self.create_rectangle(0, 0, self.WIDTH, h, fill=THEME["activity"], width=0)
        self.create_line(self.WIDTH - 1, 0, self.WIDTH - 1, h, fill=THEME["border_soft"])
        self._zones = []
        for i, (key, tip, _cmd) in enumerate(self.items):
            cy = 14 + i * self.ITEM_H + self.ITEM_H // 2
            active = key == self.active
            if active:
                self.create_rectangle(0, cy - 14, 3, cy + 14, fill=THEME["accent"], width=0)
            color = THEME["accent"] if active else "#8b95a7"
            if key == self._hover:
                color = THEME["fg"]
            self._draw_icon(key, self.WIDTH // 2, cy, color, active)
            self._zones.append((key, cy - self.ITEM_H // 2, cy + self.ITEM_H // 2))

    def _draw_icon(self, key, cx, cy, color, active):
        c = self
        if key == "explorer":
            for i, lw in enumerate((16, 12, 12)):
                y = cy - 6 + i * 6
                c.create_line(cx - 8, y, cx - 8 + lw, y, fill=color, width=2)
            c.create_rectangle(cx - 9, cy - 8, cx - 5, cy - 4, fill=color, width=0)
        elif key == "search":
            c.create_oval(cx - 9, cy - 9, cx + 3, cy + 3, outline=color, width=2)
            c.create_line(cx + 3, cy + 3, cx + 9, cy + 9, fill=color, width=2)
        elif key == "run":
            c.create_polygon(cx - 7, cy - 9, cx + 9, cy, cx - 7, cy + 9,
                             fill=color, outline=color)
        elif key == "memory":
            c.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, outline=color, width=2)
            c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=color, width=0)
        elif key == "palette":
            for i in range(3):
                y = cy - 6 + i * 6
                c.create_line(cx - 9, y, cx + 9, y, fill=color, width=2)
                c.create_oval(cx + (6 if i == 0 else -2), y - 2,
                              cx + (10 if i == 0 else 2), y + 2, fill=color, width=0)

    def _click(self, e):
        i = self.index_of(e.y)
        if i is None:
            return
        key, _tip, cmd = self.items[i]
        self.set_active(key)
        try:
            cmd()
        except Exception:
            pass

    def _motion(self, e):
        i = self.index_of(e.y)
        key = self.items[i][0] if i is not None else None
        if key != self._hover:
            self._hover = key
            self.redraw()

    def _leave_all(self):
        if self._hover is not None:
            self._hover = None
            self.redraw()

class Minimap(tk.Canvas):
    """编辑器右侧代码缩略图：按行抽象绘制 + 视口指示框，点击/拖动定位。"""

    WIDTH = 74
    PAD = 6

    def __init__(self, master, pane, width=WIDTH):
        super().__init__(master, bg=THEME["minimap"], width=width,
                         highlightthickness=0, bd=0)
        self.pane = pane
        self._dragging = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))
        self.bind("<Configure>", lambda e: self.redraw_map())

    def _lines(self):
        try:
            return self.pane.text.get("1.0", "end-1c").split("\n")
        except Exception:
            return []

    def redraw_map(self):
        self.delete("all")
        h = max(self.winfo_height(), 10)
        w = max(self.winfo_width(), 10)
        self.create_line(0, 0, 0, h, fill=THEME["border_soft"])
        lines = self._lines()
        if not lines:
            return
        n = len(lines)
        if getattr(self.pane, "_large_file", False):
            lines = lines[::max(1, n // 600)][:600]
        step = (h - self.PAD * 2) / max(1, len(lines))
        self._n = n
        self._step = step
        text_w = w - 10
        max_chars = 78.0
        k = text_w / max_chars
        for i, line in enumerate(lines[:1400]):
            s = line.rstrip()
            if not s.strip():
                continue
            y = self.PAD + i * step
            stripped = s.lstrip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
                color = THEME["muted_dim"]
            elif stripped.split(" ")[0] in FLOW_WORDS:
                color = THEME["minimap_kw"]
            else:
                color = THEME["minimap_line"]
            indent = (len(s) - len(stripped)) * k
            length = min(len(stripped) * k, text_w - indent)
            if length < 1.5:
                length = 1.5
            self.create_rectangle(6 + indent, y, 6 + indent + length, y + 1.6,
                                  fill=color, width=0)
        self.redraw_view()

    def redraw_view(self):
        self.delete("view")
        h = max(self.winfo_height(), 10)
        w = max(self.winfo_width(), 10)
        try:
            first, last = self.pane.text.yview()
        except Exception:
            return
        if last - first >= 0.999:
            return
        y1 = self.PAD + first * (h - self.PAD * 2)
        y2 = self.PAD + last * (h - self.PAD * 2)
        self.create_rectangle(2, y1, w - 2, y2, fill=THEME["accent_dim"], stipple="gray25",
                              outline=THEME["minimap_view"], width=1, tags="view")

    def _seek(self, y):
        h = max(self.winfo_height(), 10)
        frac = (y - self.PAD) / max(1, (h - self.PAD * 2))
        self.pane.text.yview_moveto(max(0.0, min(1.0, frac)))

    def _press(self, e):
        self._dragging = True
        self._seek(e.y)

    def _drag(self, e):
        if self._dragging:
            self._seek(e.y)

class MiniScrollbar(tk.Canvas):
    """无箭头自绘滚动条：细轨道 + 圆角滑块，拖动定位。"""

    def __init__(self, master, command, width=11, bg=None):
        super().__init__(master, width=width, bg=bg or THEME["editor"],
                         highlightthickness=0, bd=0)
        self.command = command
        self._first, self._last = 0.0, 1.0
        self._drag = None
        self._hover = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Configure>", lambda e: self.redraw())

    def _set_hover(self, flag):
        self._hover = flag
        self.redraw()

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self.redraw()

    def redraw(self):
        self.delete("all")
        h = max(self.winfo_height(), 10)
        w = max(self.winfo_width(), 11)
        self.create_rectangle(0, 0, w, h, fill=THEME["editor"], width=0)
        span = self._last - self._first
        if span >= 0.999:
            self.create_rectangle(w // 2 - 1, 0, w // 2 + 1, h,
                                  fill=THEME["border_soft"], width=0)
            return
        y1 = int(self._first * h)
        y2 = int(self._last * h)
        if y2 - y1 < 22:
            y2 = y1 + 22
        thumb = "#3c4a63" if self._hover else "#2f3b4d"
        round_rect(self, 3, y1 + 1, w - 3, min(y2, h - 1), 4, fill=thumb, outline="")

    def _press(self, e):
        h = max(self.winfo_height(), 10)
        y1 = self._first * h
        y2 = self._last * h
        if y1 <= e.y <= y2:
            self._drag = e.y - y1
        else:
            span = self._last - self._first
            frac = max(0.0, min(1.0, e.y / h - span / 2))
            self.command("moveto", frac)

    def _drag_move(self, e):
        if self._drag is None:
            return
        h = max(self.winfo_height(), 10)
        frac = max(0.0, min(1.0, (e.y - self._drag) / h))
        self.command("moveto", frac)

    def _release(self, e=None):
        self._drag = None
