# -*- coding: utf-8 -*-
"""Windows 屏幕阅读器桥接（编辑器 3.1）。

Tk 8.6 的 Canvas/Text 没有实现 UI Automation provider，单靠 Tk 控件无法让
Narrator/NVDA 读到编辑器正文。这里提供两层可验证的适配：

1. 语义快照：把当前文件、光标、选区、诊断、可见文本写成稳定 JSON，任何
   辅助工具都可以轮询；
2. 原生 EDIT 桥接窗口：在 Tk 窗口里嵌入一个真正的 Win32 EDIT 控件（系统
   自带 UIA/MSAA provider），屏幕阅读器可以聚焦并朗读当前语义快照。

这不是给整个 Tk 自绘界面注册自定义 UIA provider；它的边界在编辑器文档中
写明：可访问的是“语义桥接窗口 + 快捷键路径”，不是每个自绘像素。
"""

from __future__ import annotations

import ctypes
import json
import os
import time
import tkinter as tk
from tkinter import ttk

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
ES_MULTILINE = 0x0004
ES_READONLY = 0x0800
ES_AUTOVSCROLL = 0x0040
WM_SETFONT = 0x0030
EVENT_OBJECT_NAMECHANGE = 0x800C
OBJID_WINDOW = 0
CHILDID_SELF = 0


def _safe_text(widget, start="1.0", end="end-1c"):
    try:
        return widget.get(start, end)
    except Exception:
        return ""


class ControlProvider:
    """3.2.2：逐控件可访问性 provider。

    每个 provider 自带稳定 id、名称、角色、快捷键、状态与边界；屏幕阅读器
    或外部工具可以按 id 枚举，不需要理解 Tk 的控件层级。
    """

    def __init__(self, cid, name, role, group="", accelerator="", widget=None,
                 invoke=None, state=None, description=""):
        self.id = str(cid)
        self.name = str(name)
        self.role = str(role)
        self.group = str(group)
        self.accelerator = str(accelerator or "")
        self.widget = widget
        self._invoke = invoke
        self._state = state
        self.description = str(description)

    def state(self):
        if callable(self._state):
            try:
                return str(self._state())
            except Exception:
                return "未知"
        if self.widget is not None:
            try:
                return "可用" if str(self.widget.cget("state")) != "disabled" else "禁用"
            except Exception:
                return "可用"
        return "可用"

    def focusable(self):
        return self.widget is not None or callable(self._invoke)

    def focus(self):
        if self.widget is not None:
            try:
                self.widget.focus_set()
                return True
            except Exception:
                return False
        return False

    def invoke(self):
        if not callable(self._invoke):
            return False
        self._invoke()
        return True

    def bounds(self):
        if self.widget is None:
            return [0, 0, 0, 0]
        try:
            return [int(self.widget.winfo_rootx()), int(self.widget.winfo_rooty()),
                    int(self.widget.winfo_width()), int(self.widget.winfo_height())]
        except Exception:
            return [0, 0, 0, 0]

    def to_dict(self):
        return {"id": self.id, "name": self.name, "role": self.role,
                "group": self.group, "accelerator": self.accelerator,
                "state": self.state(), "focusable": self.focusable(),
                "bounds": self.bounds(), "description": self.description}


def build_control_providers(app):
    """枚举编辑器可聚焦控件；缺失的控件直接跳过，不抛异常。"""
    out = []

    try:
        app._ensure_bottom()
    except Exception:
        pass

    def add(cid, name, role, group, accel="", widget=None, invoke=None,
            state=None, description=""):
        out.append(ControlProvider(cid, name, role, group, accel, widget,
                                   invoke, state, description))

    pane = None
    try:
        pane = app.current_pane()
    except Exception:
        pane = None
    add("editor.text", "编辑器正文", "文档", "编辑器", "Ctrl+1",
        widget=getattr(pane, "text", None), invoke=(pane.focus_text if pane else None),
        state=lambda: ("只读" if getattr(pane, "_virtual_readonly", False) else
                       ("未保存" if getattr(pane, "_dirty", False) else "已保存")),
        description="当前文件文本与光标位置")
    add("explorer.tree", "资源管理器", "树", "导航", "Ctrl+2",
        widget=getattr(app, "tree", None), invoke=(lambda: app.tree.focus_set()
                                                   if getattr(app, "tree", None) else None))
    for cid, attr, label, role in (("problems.tree", "problems", "问题列表", "列表"),
                                   ("output.text", "output", "输出面板", "文本"),
                                   ("test.tree", "test_tree", "测试树", "树"),
                                   ("task.entry", "task_entry", "任务命令输入", "编辑框"),
                                   ("memory.text", "memory", "内存面板", "文本")):
        widget = getattr(app, attr, None)
        add(cid, label, role, "底部面板", "Ctrl+3",
            widget=widget, invoke=(widget.focus_set if widget is not None else None))
    add("palette.input", "命令面板", "编辑框", "命令", "Ctrl+Shift+P",
        invoke=getattr(app, "open_palette", None))
    add("search.dialog", "全项目搜索", "对话框", "命令", "Ctrl+Shift+F",
        invoke=getattr(app, "global_search", None))
    add("run.current", "运行当前脚本", "按钮", "运行", "Ctrl+Enter",
        invoke=getattr(app, "run_code", None))
    add("run.tests", "运行测试", "按钮", "运行", "Ctrl+Shift+T",
        invoke=getattr(app, "run_tests", None))
    add("run.coverage", "运行时行覆盖率", "按钮", "运行", "",
        invoke=getattr(app, "show_coverage", None))
    add("file.save", "保存当前文件", "按钮", "文件", "Ctrl+S",
        invoke=getattr(app, "save_file", None))
    add("file.open", "打开文件", "按钮", "文件", "Ctrl+O",
        invoke=getattr(app, "open_file", None))
    add("view.hc", "高对比主题", "按钮", "视图", "",
        invoke=lambda: app.apply_theme("hc"))
    add("a11y.bridge", "屏幕阅读器桥接", "窗口", "辅助功能", "",
        invoke=getattr(app, "open_uia_bridge", None))
    return out



def build_snapshot(app):
    try:
        from . import __version__ as app_version
    except Exception:
        app_version = ""
    pane = None
    try:
        pane = app.current_pane()
    except Exception:
        pane = None
    file_path = getattr(pane, "path", None) if pane else None
    line = column = 1
    selected = ""
    current_line = ""
    visible = ""
    if pane is not None:
        try:
            index = str(pane.text.index("insert"))
            line, column = (int(x) for x in index.split("."))
            current_line = _safe_text(pane.text, "%d.0" % line, "%d.end" % line)
        except Exception:
            pass
        try:
            selected = pane.text.get("sel.first", "sel.last")
        except Exception:
            selected = ""
        try:
            top = int(pane.text.index("@0,0").split(".")[0])
            bottom = int(pane.text.index("@0,%d" % max(pane.text.winfo_height(), 10)).split(".")[0])
            visible = _safe_text(pane.text, "%d.0" % top, "%d.end" % bottom)
        except Exception:
            visible = ""
    diagnostics = []
    try:
        problems = getattr(app, "problems", None)
        if problems is not None and hasattr(problems, "get_children"):
            for iid in problems.get_children()[:20]:
                vals = problems.item(iid, "values")
                diagnostics.append(" ".join(str(v) for v in vals if v))
        else:
            for item in (problems or []):
                diagnostics.append(str(item))
    except Exception:
        pass
    controls = []
    try:
        controls = [p.to_dict() for p in build_control_providers(app)]
    except Exception:
        controls = []
    return {
        "app": "Impor Studio",
        "version": str(app_version or ""),
        "updated_at": time.time(),
        "file": os.path.abspath(file_path) if file_path else "",
        "file_name": os.path.basename(file_path) if file_path else "未命名",
        "line": line,
        "column": column + 1,
        "selection": selected[:4000],
        "dirty": bool(getattr(pane, "_dirty", False)) if pane else False,
        "readonly": bool(getattr(pane, "_virtual_readonly", False)) if pane else False,
        "large_file": bool(getattr(pane, "_large_file", False)) if pane else False,
        "current_line": current_line,
        "visible_text": visible,
        "diagnostics": diagnostics[:20],
        "controls": controls,
        "mode": ("分块编辑" if pane and getattr(pane, "_virtual_full_lines", None) else "编辑"),
    }


def format_snapshot(s):
    lines = [
        "%s %s · 屏幕阅读器桥接" % (s.get("app", "Impor Studio"), s.get("version", "")),
        "文件：%s%s" % (s.get("file_name") or "未命名",
                        "（未保存修改）" if s.get("dirty") else ""),
        "路径：%s" % (s.get("file") or "（未保存）"),
        "光标：第 %d 行，第 %d 列；模式：%s" % (s.get("line", 1), s.get("column", 1),
                                              s.get("mode", "编辑")),
        "当前行：%s" % (s.get("current_line") or ""),
    ]
    if s.get("selection"):
        lines.append("选区：%s" % s["selection"].replace("\n", " ⏎ ")[:500])
    if s.get("diagnostics"):
        lines.append("诊断：%s" % "；".join(s["diagnostics"][:6]))
    lines.append("")
    lines.append("可见文本：")
    lines.append(s.get("visible_text") or "")
    return "\n".join(lines)


class UIABridge(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.hwnd = None
        self._last_text = ""
        self._job = None
        self.title("Impor 可访问性桥接 · 屏幕阅读器")
        self.geometry("900x620")
        self.transient(app.root)
        self.configure(bg="#0f141c")
        try:
            from .icons import apply_window_icon
            apply_window_icon(self)
        except Exception:
            pass
        top = tk.Frame(self, bg="#0f141c")
        top.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(top, text="屏幕阅读器语义桥接（原生 EDIT 控件）", bg="#0f141c",
                 fg="#eef2f8", font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(side=tk.LEFT)
        tk.Button(top, text="刷新", command=self.refresh, bg="#7aa2f7", fg="#0b0e14",
                  relief="flat", bd=0, padx=10, pady=4, cursor="hand2").pack(side=tk.RIGHT, padx=3)
        tk.Button(top, text="打开快照 JSON", command=self.open_json, bg="#1b2432", fg="#d5dde8",
                  relief="flat", bd=0, padx=10, pady=4, cursor="hand2").pack(side=tk.RIGHT, padx=3)
        tk.Button(top, text="高对比主题", command=lambda: self.app.apply_theme("hc"),
                  bg="#1b2432", fg="#ffd166", relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2").pack(side=tk.RIGHT, padx=3)
        self.status = tk.Label(self, text="正在初始化原生控件…", bg="#0f141c", fg="#6b7688",
                               anchor="w", font=("Microsoft YaHei UI", 8))
        self.status.pack(fill=tk.X, padx=14)
        body = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg="#0f141c", sashwidth=4, bd=0)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 10))
        left = tk.Frame(body, bg="#0d1117")
        tk.Label(left, text="可聚焦控件树", bg="#0d1117", fg="#7aa2f7", anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold"), padx=8, pady=6).pack(fill=tk.X)
        self.controls = ttk.Treeview(left, columns=("role", "state", "accel"),
                                     show="tree headings", height=20)
        self.controls.heading("#0", text="控件")
        self.controls.heading("role", text="角色")
        self.controls.heading("state", text="状态")
        self.controls.heading("accel", text="快捷键")
        self.controls.column("#0", width=180)
        self.controls.column("role", width=70, anchor="center")
        self.controls.column("state", width=70, anchor="center")
        self.controls.column("accel", width=90, anchor="center")
        self.controls.pack(fill=tk.BOTH, expand=True)
        self._build_control_tree()
        body.add(left, minsize=210, width=240)
        self.host = tk.Frame(body, bg="#0d1117", highlightthickness=1,
                             highlightbackground="#1c232e")
        body.add(self.host, minsize=320)
        self.host.bind("<Configure>", lambda e: self._resize())
        self.fallback = tk.Text(self.host, bg="#0d1117", fg="#c9d4e3", relief="flat",
                                wrap="word", font=("Microsoft YaHei UI", 10), padx=10, pady=8)
        self.fallback.pack(fill=tk.BOTH, expand=True)
        self.after(80, self._create_native)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Destroy>", self._on_destroy)

    def _build_control_tree(self):
        """3.2.2：按 provider 注册表重建控件树，每个控件带角色/状态/快捷键。"""
        tree = self.controls
        tree.delete(*tree.get_children())
        self._providers = {}
        self._provider_order = []
        groups = {}
        for prov in build_control_providers(self.app):
            self._providers[prov.id] = prov
            self._provider_order.append(prov.id)
            gid = groups.get(prov.group)
            if gid is None:
                gid = tree.insert("", tk.END, text=prov.group or "其他", open=True)
                groups[prov.group] = gid
            iid = tree.insert(gid, tk.END, iid=prov.id, text=prov.name,
                              values=(prov.role, prov.state(), prov.accelerator))
        tree.bind("<Return>", self._invoke_control)
        tree.bind("<Double-Button-1>", self._invoke_control)
        tree.bind("<<TreeviewSelect>>", self._describe_control)
        tree.bind("<Control-Tab>", lambda e: self._move_focus(1))
        tree.bind("<Shift-Tab>", lambda e: self._move_focus(-1))

    def _describe_control(self, event=None):
        prov = self._providers.get(self.controls.focus())
        if prov is None:
            return
        self.status.configure(
            text="%s · 角色 %s · 状态 %s%s" % (
                prov.name, prov.role, prov.state(),
                (" · " + prov.accelerator) if prov.accelerator else ""),
            fg="#9ece6a")

    def _move_focus(self, delta):
        order = getattr(self, "_provider_order", [])
        if not order:
            return "break"
        sel = self.controls.focus()
        try:
            idx = order.index(sel)
        except ValueError:
            idx = -1 if delta > 0 else 0
        idx = (idx + delta) % len(order)
        iid = order[idx]
        self.controls.selection_set(iid)
        self.controls.focus(iid)
        self.controls.see(iid)
        self._describe_control()
        return "break"

    def _invoke_control(self, event=None):
        prov = self._providers.get(self.controls.focus())
        if prov is None:
            return
        try:
            if prov.focusable() and prov.invoke():
                self.status.configure(text="已执行：" + prov.name + " · " + prov.role, fg="#9ece6a")
            elif prov.focusable() and prov.focus():
                self.status.configure(text="已聚焦：" + prov.name + " · " + prov.role, fg="#9ece6a")
            else:
                self.status.configure(text="该控件不可执行：" + prov.name, fg="#ff9e64")
        except Exception as exc:
            self.status.configure(text="执行失败：" + str(exc)[:100], fg="#ff9e64")

    def _create_native(self):
        try:
            user32 = ctypes.windll.user32
            user32.CreateWindowExW.restype = ctypes.c_void_p
            user32.CreateWindowExW.argtypes = [
                ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            user32.SetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            user32.SetWindowTextW.restype = ctypes.c_int
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                            ctypes.c_void_p, ctypes.c_void_p]
            user32.SendMessageW.restype = ctypes.c_void_p
            user32.MoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int]
            user32.NotifyWinEvent.argtypes = [ctypes.c_uint, ctypes.c_void_p,
                                              ctypes.c_int, ctypes.c_int]
            font = None
            try:
                gdi32 = ctypes.windll.gdi32
                gdi32.CreateFontW.restype = ctypes.c_void_p
                gdi32.CreateFontW.argtypes = [
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                    ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                    ctypes.c_wchar_p]
                font = gdi32.CreateFontW(
                    -18, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, "Microsoft YaHei UI")
            except Exception:
                font = None
            parent = self.host.winfo_id()
            self.hwnd = user32.CreateWindowExW(
                0, "EDIT", "",
                WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
                0, 0, max(100, self.host.winfo_width()), max(100, self.host.winfo_height()),
                parent, 0, 0, 0)
            if self.hwnd:
                self.fallback.pack_forget()
                if font:
                    try:
                        user32.SendMessageW(self.hwnd, WM_SETFONT, font, True)
                    except Exception:
                        pass
                self.status.configure(text="原生 EDIT 已就绪；Narrator/NVDA 可直接聚焦本窗口。",
                                      fg="#9ece6a")
            else:
                self.status.configure(text="原生控件创建失败，已回退到 Tk 文本。", fg="#ff9e64")
        except Exception as exc:
            self.status.configure(text="原生控件不可用：" + str(exc)[:80], fg="#ff9e64")
        self._tick()

    def _resize(self):
        if self.hwnd:
            try:
                w, h = max(80, self.host.winfo_width()), max(60, self.host.winfo_height())
                ctypes.windll.user32.MoveWindow(self.hwnd, 0, 0, w, h, True)
            except Exception:
                pass

    def refresh(self):
        snapshot = build_snapshot(self.app)
        text = format_snapshot(snapshot)
        try:
            backup = os.path.join(getattr(self.app, "_recovery_dir", self.app.workspace),
                                  "accessibility.json")
            with open(backup, "w", encoding="utf-8", newline="\n") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
                f.write("\n")
            controls_path = os.path.join(os.path.dirname(backup), "control_providers.json")
            with open(controls_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"version": snapshot.get("version"), "controls": snapshot.get("controls", [])},
                          f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception:
            pass
        if text != self._last_text:
            self._last_text = text
            if self.hwnd:
                try:
                    ctypes.windll.user32.SetWindowTextW(self.hwnd, text)
                    ctypes.windll.user32.NotifyWinEvent(
                        EVENT_OBJECT_NAMECHANGE, self.hwnd, OBJID_WINDOW, CHILDID_SELF)
                except Exception:
                    pass
            else:
                try:
                    self.fallback.delete("1.0", "end")
                    self.fallback.insert("1.0", text)
                except Exception:
                    pass

    def _tick(self):
        if not self.winfo_exists():
            return
        self.refresh()
        self._job = self.after(900, self._tick)

    def open_json(self):
        path = os.path.join(getattr(self.app, "_recovery_dir", self.app.workspace),
                            "accessibility.json")
        try:
            if not os.path.isfile(path):
                self.refresh()
            os.startfile(path)
        except Exception as exc:
            self.status.configure(text="打开失败：" + str(exc)[:80], fg="#ff9e64")

    def _on_destroy(self, event=None):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
