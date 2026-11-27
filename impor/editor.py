# -*- coding: utf-8 -*-
"""Impor (I++) 高级图形化代码工作室（v3.2）。

深色现代界面：DWM 深色标题栏 + 圆角窗口、自绘圆角工具栏、资源管理器侧栏、
多标签编辑、token 级多色语法高亮、当前行 / 括号配对、自动缩进与括号补全、
命令面板（Ctrl+Shift+P）、彩色输出 / 问题 / 变量 / 内存四联面板、
状态栏（行列 / 编码 / 内存 / 版本），后台线程运行脚本不冻结界面。

快捷键：Ctrl+N 新建 · Ctrl+O 打开 · Ctrl+S 保存 · Ctrl+Shift+S 另存
        Ctrl+Enter / F5 运行 · Ctrl+B 构建 exe · Ctrl+Shift+P 命令面板
        Ctrl+F 查找 · Ctrl+G 跳行 · Ctrl+W 关闭标签 · Ctrl+Shift+E 侧栏
"""

import fnmatch
import ctypes
import difflib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from . import editor_pane, editor_dialogs, editor_groups, editor_panels, editor_runtime
from . import ai_assist, largefile
from . import i18n
from .editor_pane import EditorPane
from .editor_dialogs import CommandPalette, Finder, AboutDialog, ExampleBrowser
from .editor_groups import EditorGroupsMixin
from .editor_panels import PanelsMixin
from .editor_runtime import RuntimeMixin
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .icons import apply_window_icon
from .lexer import KEYWORDS, BUILTIN_NAMES, HIGHLIGHT_KEYWORDS
from .parser import Parser
from .analysis import analyze, style_lint
from .typecheck import check_source, infer_expression
from .project import load_project, save_project
from .lexer import tokenize
from .interpreter import Interpreter, ImporException, strip_header
from .diagnostics import parse_position, fix_for, explain
from .builtins import BUILTIN_DOCS
from .symbol_index import ProjectIndex
from .keymap import Keymap
from .observability import note_swallowed
from .pathsafe import rel_quiet

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_ROOT = ROOT_DIR
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".impor_studio.json")


def _align_eol(text, pane):
    """对齐文件末尾换行（4.7.3）。

    Tk Text 的 `end` 天然带一个换行，`get_content()` 原样返回时，
    一个「末尾没有换行」的文件每保存一次就会多出一个空行（CRLF 下 +2 字节）。
    这里按打开时记下的真实结尾还原：原本有换行就保证有，原本没有就去掉那个补的。
    """
    if text is None:
        return text
    # 写盘时换行由 open(newline=...) 统一生成；正文里绝不能残留 \r，
    # 否则 CRLF 文件会被写成 \r\r\n（每行多 1 字节，肉眼是「多一个空行」）。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if bool(getattr(pane, "_ends_nl", True)):
        return text if text.endswith("\n") else text + "\n"
    return text[:-1] if text.endswith("\n") else text

SPYP_SUFFIX = ".impor"
SPYP_FILETYPES = [("Impor 脚本", "*.impor"), ("文本", "*.txt"), ("所有文件", "*.*")]
HEADER_MARK = 'important<<DAY<<english("English"):'

_SEM_ID = r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*"
_SEM_DECL = re.compile(r"(?m)^\s*(?P<kind>func|def|class|exception|struct|var|const|let|set)\s+(?P<name>" + _SEM_ID + ")")
_SEM_FOR = re.compile(r"(?m)^\s*for\s+(?P<name>" + _SEM_ID + r")\s+in\b")
_SEM_IMPORT = re.compile(r"(?m)^\s*import\s+(?P<mod>[\w.]+)(?:\s+as\s+(?P<alias>" + _SEM_ID + "))?")
_SEM_FROM = re.compile(r"(?m)^\s*from\s+[\w.]+\s+import\s+(?P<names>[^\n#]+)")
_SEM_FUNC = re.compile(r"(?m)^\s*(?:func|def)\s+(?P<name>" + _SEM_ID + r")\s*\((?P<params>[^)]*)\)")


def semantic_symbol_map(src):
    """语义高亮符号表：优先复用 impor.analysis，解析失败时回退轻量正则。

    优先级：参数 > 局部变量 > 函数 > 类/结构/异常 > 模块。
    同一名字既可能是变量也可能是函数，取更具体的声明。
    """
    table = {}

    def put(name, kind, rank):
        if not name:
            return
        old = table.get(name)
        if old is None or rank >= old[1]:
            table[name] = (kind, rank)

    for m in _SEM_FUNC.finditer(src):
        put(m.group("name"), "func", 3)
        for part in m.group("params").split(","):
            pname = part.strip().split("=", 1)[0].split(":", 1)[0].strip()
            if re.match(r"^" + _SEM_ID + r"$", pname):
                put(pname, "param", 5)
    for m in _SEM_DECL.finditer(src):
        kind = m.group("kind")
        name = m.group("name")
        if kind in ("func", "def"):
            put(name, "func", 3)
        elif kind in ("class", "exception", "struct"):
            put(name, kind if kind != "exception" else "class", 2)
        else:
            put(name, "var", 4)
    for m in _SEM_FOR.finditer(src):
        put(m.group("name"), "var", 4)
    for m in _SEM_IMPORT.finditer(src):
        name = m.group("alias") or m.group("mod").split(".")[-1]
        put(name, "module", 1)
    for m in _SEM_FROM.finditer(src):
        for part in m.group("names").split(","):
            bits = part.strip().split()
            if not bits:
                continue
            name = bits[-1] if len(bits) >= 3 and bits[-2] == "as" else bits[0]
            if re.match(r"^" + _SEM_ID + r"$", name):
                put(name, "module", 1)

    # v3.7.4：能解析时用统一分析器的真实作用域符号表覆盖正则结果；
    # 解析失败（编辑中/语法未闭合）继续用上面的轻量表，保证高亮不中断。
    if len(src) <= 200_000:
        try:
            from .lexer import tokenize
            from .parser import Parser
            from .analysis import analyze
            ast = Parser(tokenize(src)).program()
            an = analyze(ast, src.split("\n"), disabled_rules=("unused-variable",
                                                               "unused-import",
                                                               "undefined-name"))
            kind_map = {"def": "func", "class": "class", "exception": "class",
                        "struct": "struct", "param": "param", "var": "var",
                        "for": "var", "catch": "var", "match": "var",
                        "field": "field", "import": "module", "module": "module"}
            rank_map = {"param": 5, "var": 4, "func": 3, "class": 2,
                        "struct": 2, "field": 2, "module": 1}
            for name, syms in an.symbols.items():
                for sym in syms:
                    kind = kind_map.get(sym.kind)
                    if kind:
                        put(name, kind, rank_map.get(kind, 1))
        except Exception:
            pass
    return table


def split_conflict_markers(text):
    """3.2.3：把带冲突标记的文本拆成普通段与 hunk。

    支持 merge / diff3 两种标记；返回 (segments, hunks)，segments 中
    字符串是普通文本，dict 是一个冲突块。无法闭合的标记按普通文本保留，
    避免保存时丢内容。
    """
    lines = text.splitlines(keepends=True)
    segments, hunks = [], []
    buf = []
    state = 0
    ours, base, theirs = [], [], []
    for line in lines:
        if line.startswith("<<<<<<<"):
            if buf:
                segments.append("".join(buf))
                buf = []
            state, ours, base, theirs = 1, [], [], []
        elif state and line.startswith("|||||||"):
            state = 2
        elif state and line.startswith("======="):
            state = 3
        elif state and line.startswith(">>>>>>>"):
            hunk = {"index": len(hunks), "ours": ours, "base": base, "theirs": theirs}
            segments.append(hunk)
            hunks.append(hunk)
            state = 0
        elif state == 1:
            ours.append(line)
        elif state == 2:
            base.append(line)
        elif state == 3:
            theirs.append(line)
        else:
            buf.append(line)
    if state:
        buf.extend(["<<<<<<< ours\n"] + ours + ["||||||| base\n"] + base + ["=======\n"] + theirs)
    if buf:
        segments.append("".join(buf))
    return segments, hunks


from .editor_theme import THEME, DARK_THEME, LIGHT_THEME, HIGH_CONTRAST_THEME

# 彩虹括号：按嵌套深度循环取色（不用紫，避免与主题冲突）
BRACKET_COLORS = ("#7aa2f7", "#e0af68", "#9ece6a", "#2ac3de")

FLOW_WORDS = {
    "if", "else", "elseif", "while", "for", "def", "func", "class", "return",
    "try", "catch", "finally", "match", "switch", "case", "default", "repeat",
    "exception", "raise", "import", "from", "export", "const", "new", "super",
    "loop", "with", "async", "await", "static", "readonly", "yield", "do",
}

EXAMPLES = [
    ("1. 你好世界", f'''{HEADER_MARK}
// 单行注释 # 也可以
var name = "世界"
var n = 100
print("你好,", name, "数字:", n)
print("1+2*3 =", 1 + 2 * 3)
'''),
    ("2. 变量与三元", f'''{HEADER_MARK}
var a = 100
var b = 200
a += 50          // 复合赋值
var big = a > b ? "a更大" : "b更大"
print(big)
var s = "Impor"
print("大写:", upper(s), "长度:", len(s))
'''),
    ("3. 循环与数组", f'''{HEADER_MARK}
var total = 0
for i in range(1, 6)
    total += i
print("1..5 求和 =", total)

var arr = [10, 20, 30, "abc"]
arr[0] = 99
print(arr)
print("最大:", max_arr(arr), "最小:", min_arr(arr), "和:", sum_arr(arr))
'''),
    ("4. 函数与闭包", f'''{HEADER_MARK}
def add(x, y)
    return x + y

def make_counter(start)
    def inc()
        nonlocal set start = start + 1
        return start
    return inc

print("add:", add(3, 5))
var c = make_counter(0)
print(c())
print(c())
'''),
    ("5. 类与继承 super", f'''{HEADER_MARK}
class Person
    def __init__(name)
        set self.name = name
    def say()
        print("我是", self.name)

class Student(Person)
    def say()
        super().say()
        print("我是学生")

var s = new Student("李四")
s.say()
'''),
    ("6. 异常与 finally", f'''{HEADER_MARK}
exception MyError

try
    raise MyError("出错了！")
catch MyError e
    print("捕获自定义异常:", e.msg)
finally
    print("无论是否异常都执行")
'''),
    ("7. 自动内存管理", f'''{HEADER_MARK}
// v3.2：mem() 查看内存，gc() 手动回收，autogc(N) 调整阈值
print("当前阈值:", autogc())
var arr = [1, 2, 3]
print(mem())
gc()
print(mem())
'''),
    ("8. 标准库 import", f'''{HEADER_MARK}
import math
import random as rnd

print("π =", math.pi)
print("sin(90°) =", math.sin(math.radians(90)))
print("随机数:", rnd.randint(1, 100))
'''),
    ("9. 字典与字符串", f'''{HEADER_MARK}
var dic = {{"name": "小明", "age": 16}}
print(dic["name"], dic["age"])
dic["age"] = 17
print("新年龄:", dic["age"])

var s = """多行
字符串"""
print(s)
print("切割:", strcut("abcdef", 1, 4))
'''),
    ("10. 斐波那契", f'''{HEADER_MARK}
def fib(n)
    if n < 2
        return n
    return fib(n - 1) + fib(n - 2)

for i in range(0, 10)
    print("fib(", i, ") =", fib(i))
'''),
]


def _load_demo_examples():
    """v6.6：把 examples/demo_01…demo_40 动态接入示例面板，文件是唯一真源。"""
    out = []
    demo_dir = os.path.join(ROOT_DIR, "examples")
    try:
        names = sorted(n for n in os.listdir(demo_dir)
                       if n.startswith("demo_") and n.endswith(".impor"))
    except OSError:
        return out
    for name in names:
        path = os.path.join(demo_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
        except OSError:
            continue
        label = ""
        for line in code.splitlines():
            s = line.strip()
            if s.startswith("//"):
                label = s[2:].strip()
                break
        stem = name[:-6]
        out.append((f"{len(EXAMPLES) + len(out) + 1}. {label or stem}", code, stem))
    return out


EXAMPLES.extend(_load_demo_examples())


# ---------------- 平台美化 ----------------
def enable_dpi_awareness():
    """开启 DPI 感知，避免高分屏下窗口尺寸换算错误、界面被屏幕边缘裁掉。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        # 独立 AppUserModelID，避免任务栏沿用 pythonw.exe 的 Python 文档图标
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Impor.I++." + __version__)
    except Exception:
        pass


def enable_dwm(root):
    """Windows 11：深色标题栏、深色边框、圆角窗口。失败静默降级。"""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            hwnd = root.winfo_id()
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

        def set_attr(attr, value, size=4):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), size)

        dark = ctypes.c_int(1)
        set_attr(20, dark)                    # DWMWA_USE_IMMERSIVE_DARK_MODE
        caption = ctypes.c_int(0x00140E0B)    # #0b0e14 -> COLORREF
        set_attr(35, caption)                 # DWMWA_CAPTION_COLOR
        border = ctypes.c_int(0x0030261F)     # #1f2630
        set_attr(34, border)                  # DWMWA_BORDER_COLOR
        text = ctypes.c_int(0x00D9D1C9)       # #c9d1d9
        set_attr(36, text)                    # DWMWA_TEXT_COLOR
        corner = ctypes.c_int(2)              # DWMWCP_ROUND
        set_attr(33, corner)                  # DWMWA_WINDOW_CORNER_PREFERENCE
        try:
            # Win11 22H2+：Mica 半透明材质背景（系统不支持时该属性无效，静默降级）
            backdrop = ctypes.c_int(2)        # DWMSBT_MAINWINDOW (Mica)
            set_attr(38, backdrop)            # DWMWA_SYSTEMBACKDROP_TYPE
        except Exception:
            pass
    except Exception:
        pass


def work_area(root):
    """当前显示器工作区（排除任务栏），失败回退整屏尺寸。"""
    if sys.platform == "win32":
        try:
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            r = RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
            w, h = r.right - r.left, r.bottom - r.top
            if w > 400 and h > 300:
                return r.left, r.top, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def preferred_mono(root):
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for name in ("Cascadia Code", "JetBrains Mono", "Fira Code", "Consolas"):
        if name in fams:
            return name
    return "Consolas"


from .editor_widgets import (
    round_rect, FlatButton, TabStrip, ActivityBar, Minimap, MiniScrollbar,
)


# ---------------- 编辑面板 ----------------


# ---------------- 命令面板 ----------------


# ---------------- 关于窗口 ----------------




# ---------------- 主应用 ----------------


class App(EditorGroupsMixin, PanelsMixin, RuntimeMixin):
    def __init__(self, root):
        enable_dpi_awareness()
        i18n.install_tk()
        self.root = root
        apply_window_icon(self.root, root=True)
        self.root.title(f"Impor (I++) {__version__} · Code Studio")
        ax, ay, aw, ah = work_area(root)
        w, h = min(1400, aw - 80), min(880, ah - 84)
        self.root.geometry("%dx%d+%d+%d" % (w, h, ax + max(0, (aw - w) // 2), ay + max(0, (ah - h) // 5)))
        self.root.minsize(min(980, w), min(620, h))
        self.mono = preferred_mono(root)
        self.config_path = CONFIG_PATH
        self.cfg = {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.cfg = json.load(f)
        except Exception:
            self.cfg = {}
        self.lang = i18n.set_language(self.cfg.get("language") or i18n.ZH_CN)
        self.font_family = str(self.cfg.get("font_family") or self.mono)
        self.mono = self.font_family
        self.show_minimap = bool(self.cfg.get("minimap", True))
        self.watch_exprs = [str(x) for x in (self.cfg.get("watches") or []) if str(x).strip()]
        self.theme_name = self.cfg.get("theme") or ("light" if os.environ.get("IMPOR_THEME") == "light" else "dark")
        global ROOT_DIR
        ws = self.cfg.get("workspace")
        self.workspace = ws if ws and os.path.isdir(ws) else ROOT_DIR
        ROOT_DIR = self.workspace
        self.trusted_workspaces = []
        for raw in (self.cfg.get("trusted_workspaces") or []):
            try:
                self.trusted_workspaces.append(os.path.normcase(os.path.abspath(str(raw))))
            except Exception:
                continue
        self.workspace_trusted = self._workspace_is_trusted(self.workspace)
        self._harden_notes = []
        try:
            from .security import harden_process, AuditLog
            self._harden_notes = harden_process()
            AuditLog(self.workspace).write("app.start", version=__version__,
                                           hardening=self._harden_notes)
        except Exception:
            note_swallowed("audit/harden")
        self.project = load_project(self.workspace, self.workspace)
        try:
            from .studio_ext import load_project_settings
            self.project_settings = load_project_settings(self.workspace)
        except Exception:
            self.project_settings = {}
        sync_cfg = self.cfg.get("sync") if isinstance(self.cfg.get("sync"), dict) else {}
        try:
            from .editor_sync import default_sync_dir, pull_snapshot, snapshot_path
            self.sync_enabled = bool(sync_cfg.get("enabled", False))
            self.sync_dir = os.path.abspath(os.path.expanduser(
                str(sync_cfg.get("path") or default_sync_dir())))
            self.sync_strategy = str(sync_cfg.get("strategy") or "newer")
            if self.sync_enabled and os.path.isfile(snapshot_path(self.sync_dir)):
                cfg2, proj2, _snap = pull_snapshot(
                    self.sync_dir, self.cfg, self.project_settings, __version__,
                    strategy=self.sync_strategy)
                self.cfg.update(cfg2)
                self.project_settings.update(proj2)
                self._sync_pulled = True
        except Exception as exc:
            self._sync_error = str(exc)[:120]
            self.sync_enabled = False
            self.sync_dir = ""
            self.sync_strategy = "newer"
        self.workspace_roots = list(self.project.get("roots") or [self.workspace])
        self.index = ProjectIndex(self.workspace, roots=self.workspace_roots,
                                  ignore=self.project.get("ignore") or [])
        self._watch_stop = None
        self._watch_thread = None
        self._index_status_text = "索引 --"
        try:
            self.refresh_index_async()
        except Exception:
            note_swallowed("refresh index")
        try:
            self.start_index_watcher()
        except Exception:
            pass
        ps = self.project_settings if isinstance(self.project_settings, dict) else {}
        ed = ps.get("editor") if isinstance(ps.get("editor"), dict) else {}
        if ps.get("theme") in ("dark", "light", "hc"):
            self.theme_name = ps.get("theme")
        THEME.clear()
        THEME.update(HIGH_CONTRAST_THEME if self.theme_name == "hc"
                     else (LIGHT_THEME if self.theme_name == "light" else DARK_THEME))
        self.font_size = int(ps.get("font_size") or self.cfg.get("font_size", 11))
        self.indent_width = int(ed.get("indent")
                                or self.project.get("format", {}).get("indent", self.cfg.get("indent_width", 4)))
        self.word_wrap = bool(ed.get("word_wrap", self.cfg.get("word_wrap", False)))
        self.auto_pair = bool(ed.get("auto_pair", self.cfg.get("auto_pair", True)))
        self.show_minimap = bool(ed.get("minimap", self.cfg.get("minimap", True)))
        self.inlay_hints = bool(ed.get("inlay_hints", self.cfg.get("inlay_hints", True)))
        self.semantic_highlight = bool(ed.get("semantic_highlighting", True))
        self.async_diagnostics = bool(ed.get("async_diagnostics", True))
        self.large_file_kb = int(ed.get("large_file_kb", self.cfg.get("large_file_kb", 2000)) or 2000)
        self.large_file_mode = str(ed.get("large_file_mode", self.cfg.get("large_file_mode", "auto")) or "auto")
        self.ai_cfg = ai_assist.normalize_config(self.cfg.get("ai") or {})
        self.screen_reader = bool(self.cfg.get("screen_reader", False))
        self.plugin_isolation = bool(self.cfg.get("plugin_isolation", True))
        self._uia_bridge = None
        self.panes = []
        self.active_pane = None
        self._queue = queue.Queue()
        self._running = False
        self._stop_requested = False
        self._interp = None
        self._debug_mode = False
        self._sandbox_mode = False
        self._freeze = False
        self._last_error = ""
        self._find_visible = False
        self._err_count = 0
        self._warn_count = 0
        self._problem_marks = []
        self.auto_format = os.environ.get("IMPOR_AUTO_FMT", "1") != "0"
        self._recent = [str(p) for p in (self.cfg.get("recent") or []) if os.path.isfile(str(p))][:12]
        self._cmd_recent = [str(x) for x in (self.cfg.get("recent_commands") or [])][:24]
        self._term_cwd = ROOT_DIR
        self._term_hist = []
        self._term_hist_pos = 0
        self._term_proc = None
        self._test_watch = False
        self._test_watch_job = None
        self._test_last_files = []
        self._test_failed_files = []
        self._test_asserts = 0
        self._test_counts = {"pass": 0, "fail": 0}
        self._test_suites = {}
        self._test_fail_map = {}
        self._test_row_meta = {}
        self._test_watch_sig = {}
        self._watch_g = {}
        self._watch_w = {}
        self._dbg_frames = []
        self._refactor_undo = None
        self._nav_back = []
        self._nav_forward = []
        self._nav_silent = False
        self._exc_filter = ""
        self.autosave = bool(self.cfg.get("autosave", True))
        # 4.7.3：崩溃恢复副本固定在用户目录，不跟 workspace 走——
        # workspace 可能是只读目录/网络盘，写不进去就等于「崩溃兜底」静默失效；
        # 放在 ~/.impor 下还有一个好处：换项目重开也能把上次没存的内容捞回来。
        self._recovery_dir = os.path.join(os.path.expanduser("~"), ".impor", "recovery")
        try:
            os.makedirs(self._recovery_dir, exist_ok=True)
        except Exception:
            pass
        self._toast_win = None
        self._bottom_ready = False
        self.bottom = None
        self._startup_done = False
        self._progressive = os.environ.get("IMPOR_NO_PROGRESSIVE", "0") != "1"
        self._plugin_status_labels = []
        try:
            from .studio_ext import PluginManager
            self.plugins = PluginManager(self)
        except Exception:
            self.plugins = None

        self.root.configure(bg=THEME["bg"])
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_styles()
        self._build_toolbar()
        self._build_trust_bar()
        self._build_main()
        self._build_status()
        self._build_shortcuts()
        self._apply_workspace_trust()
        self._apply_workspace_title()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(60, lambda: enable_dwm(self.root))
        if self._progressive:
            # 先把外壳交给 Tk 事件循环；等 splash 淡出、首帧真正绘制后，
            # 再补齐底部面板/会话恢复/插件，避免 600ms 级的同步建控件挡住首帧。
            self.root.after(420, self._finish_startup)
        else:
            self._finish_startup()

    def _finish_startup(self):
        if self._startup_done:
            return
        self._startup_done = True
        self._ensure_bottom()
        self._restore_session()
        self._poll_queue()
        self._tick_memory()
        self._after_safe(30000, self._autosave_tick)
        self._after_safe(3000, self._recovery_tick)
        self._file_mtimes = {}
        self._external_warned = set()
        self.root.after(1200, self._watch_files)
        try:
            if self.plugins and self.workspace_trusted:
                self.plugins.load()
                self._refresh_plugin_ui()
            elif self.plugins:
                self.toast("受限模式：项目插件未加载，信任工作区后启用", "warn")
        except Exception as exc:
            self.toast("插件加载失败：" + str(exc)[:100])
        if str(self.cfg.get("auto_update", "1")) != "0":
            self.root.after(7000, self._auto_update_check)
        if getattr(self, "screen_reader", False):
            self.root.after(900, self.open_uia_bridge)
        try:
            self.keymap = Keymap(self)
            self.keymap.install()
            if self.keymap.errors:
                self.root.after(1500, lambda: self.toast(
                    "键位配置：" + self.keymap.errors[0], "warn"))
        except Exception as exc:
            self.keymap = None
            self.toast("键位配置加载失败：" + str(exc)[:100], "warn")

    def _refresh_plugin_ui(self):
        if not self.plugins:
            return
        # 侧栏面板：插件 builder 返回一个 Tk 控件；只读 API 不提供写文件能力
        try:
            existing = [self.plugin_nb.tab(t, "text") for t in self.plugin_nb.tabs()]
        except Exception:
            existing = []
        for title, builder in getattr(self.plugins, "panels", []):
            if title in existing:
                continue
            try:
                frame = tk.Frame(self.plugin_nb, bg=THEME["sidebar"])
                widget = builder(frame, self.plugins.api_for_title(title) if hasattr(self.plugins, "api_for_title") else None)
                if isinstance(widget, tk.Widget) and widget.master is not frame:
                    widget.pack(fill=tk.BOTH, expand=True)
                self.plugin_nb.add(frame, text=title)
            except Exception as exc:
                self.plugins.errors.append((title, str(exc)[:120]))
        # 状态栏项
        try:
            for w in self.plugin_status_frame.winfo_children():
                w.destroy()
            for text, callback in getattr(self.plugins, "status_items", []):
                lbl = tk.Label(self.plugin_status_frame, text=text, bg=THEME["panel_head"],
                               fg=THEME["accent"], font=("Microsoft YaHei UI", 8))
                lbl.pack(side=tk.LEFT, padx=(4, 0))
                if callback:
                    lbl.configure(cursor="hand2")
                    lbl.bind("<Button-1>", lambda e, cb=callback: cb())
        except Exception:
            pass
        # 有插件面板时才占用侧栏底部空间；没有插件时不显示空 Notebook。
        try:
            if getattr(self.plugins, "panels", []):
                if not self.plugin_nb.winfo_manager():
                    self.plugin_nb.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))
            else:
                self.plugin_nb.pack_forget()
        except Exception:
            pass

    def _ensure_bottom(self):
        if self.bottom is not None and self._bottom_ready:
            return self.bottom
        if getattr(self, "_building_bottom", False):
            return self.bottom
        self._building_bottom = True
        try:
            self.bottom = tk.Frame(self.center, bg=THEME["panel"])
            self._build_bottom(self.bottom)
            self.center.add(self.bottom, height=230, minsize=120)
            self._bottom_ready = True
            self.show_bottom(self._bottom_kind if hasattr(self, "_bottom_kind") else "out")
        finally:
            self._building_bottom = False
        return self.bottom

    # ---------- 外部文件变更自动重载 ----------
    def _watch_files(self):
        self.root.after(1200, self._watch_files)
        if self._running:
            return
        for pane in list(self.panes):
            path = getattr(pane, "path", None)
            if not path or not os.path.exists(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                continue
            old = self._file_mtimes.get(path)
            if old is None:
                self._file_mtimes[path] = mtime
                continue
            if mtime == old:
                continue
            if getattr(pane, "_dirty", False):
                if path not in self._external_warned:
                    self._external_warned.add(path)
                    self.toast("外部已修改但你有未保存内容：%s（保留你的版本）" %
                               os.path.basename(path), "warn")
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
                pos = pane.text.index("insert")
                yview = pane.text.yview()[0]
                pane.text.delete("1.0", "end")
                pane.text.insert("1.0", data)
                pane.text.edit_reset()
                pane.text.edit_modified(False)
                pane._dirty = False
                pane.highlight()
                pane.redraw_lineno()
                try:
                    pane.text.mark_set("insert", pos)
                    pane.text.yview_moveto(yview)
                except Exception:
                    pass
                pane.minimap.redraw_map()
                self._file_mtimes[path] = mtime
                self._external_warned.discard(path)
                self.toast("已重新载入外部修改：%s" % os.path.basename(path), "ok")
            except Exception:
                pass

    def _layout_snapshot(self):
        """保存布局与会话：窗口、sash、双组标签、活动标签、光标和滚动。"""
        layout = {}
        try:
            layout["geometry"] = self.root.geometry()
        except Exception:
            pass
        try:
            layout["paned"] = [int(self.paned.sash_coord(i)[0])
                               for i in range(max(0, len(self.paned.panes()) - 1))]
        except Exception:
            pass
        try:
            layout["center"] = [int(self.center.sash_coord(i)[1])
                                for i in range(max(0, len(self.center.panes()) - 1))]
        except Exception:
            pass
        groups = {}
        cursor = {}
        scroll = {}
        active = {}
        for pane in list(getattr(self, "panes", [])):
            group = getattr(pane, "group", "main")
            path = getattr(pane, "path", None)
            if path:
                path = os.path.abspath(path)
                groups.setdefault(group, [])
                if path not in groups[group]:
                    groups[group].append(path)
                try:
                    line, col = pane.text.index("insert").split(".")
                    cursor[path] = [int(line), int(col)]
                except Exception:
                    pass
                try:
                    scroll[path] = float(pane.text.yview()[0])
                except Exception:
                    pass
            if pane is getattr(self, "active_pane", None) and path:
                active[group] = path
        layout["groups"] = groups
        layout["active"] = active
        layout["cursor"] = cursor
        layout["scroll"] = scroll
        layout["split_open"] = bool(getattr(self, "split_frame", None))
        return layout

    def _restore_session(self):
        layout = self.cfg.get("layout") if isinstance(self.cfg.get("layout"), dict) else {}
        groups = layout.get("groups") if isinstance(layout.get("groups"), dict) else {}
        main_paths = [p for p in (groups.get("main") or []) if isinstance(p, str) and os.path.exists(p)]
        split_paths = [p for p in (groups.get("split") or []) if isinstance(p, str) and os.path.exists(p)]
        if not main_paths and not split_paths:
            main_paths = [p for p in self.cfg.get("tabs", []) if isinstance(p, str) and os.path.exists(p)]
        opened = 0
        folds = self.cfg.get("folds", {}) if isinstance(self.cfg.get("folds"), dict) else {}
        opened_panes = []
        for path in main_paths:
            try:
                pane = self.open_path(path)
                if pane:
                    opened_panes.append(pane)
                    opened += 1
            except Exception:
                continue
        if layout.get("split_open") and not split_paths:
            try:
                self.ensure_split_group()
            except Exception:
                pass
        if split_paths:
            self.ensure_split_group()
            for path in split_paths:
                try:
                    pane = self.open_path_in_split(path)
                    if pane:
                        opened_panes.append(pane)
                        opened += 1
                except Exception:
                    continue
        for pane in opened_panes:
            path = getattr(pane, "path", None)
            if path and path in folds:
                try:
                    pane._folded = set(folds.get(path) or [])
                    pane._reapply_folds()
                    pane.redraw_lineno()
                except Exception:
                    pass
        if not opened:
            self.open_new_tab()
        else:
            active_main = layout.get("active", {}).get("main") if isinstance(layout.get("active"), dict) else None
            active_split = layout.get("active", {}).get("split") if isinstance(layout.get("active"), dict) else None
            target = None
            for pane in opened_panes:
                path = getattr(pane, "path", None)
                group = getattr(pane, "group", "main")
                if (group == "split" and path and os.path.abspath(path) == active_split) or \
                   (group == "main" and path and os.path.abspath(path) == active_main):
                    target = pane
                    if group == "main":
                        break
            if target is not None:
                self.select_pane(target)
            self.toast("已恢复上次会话")
        self.root.after(160, lambda: self._restore_layout_state(layout))

    def _restore_layout_state(self, layout=None):
        layout = layout if isinstance(layout, dict) else {}
        try:
            geo = layout.get("geometry")
            if isinstance(geo, str) and geo:
                self.root.geometry(geo)
        except Exception:
            pass
        try:
            for i, x in enumerate(layout.get("paned") or []):
                self.paned.sash_place(i, int(x), 1)
        except Exception:
            pass
        try:
            for i, y in enumerate(layout.get("center") or []):
                self.center.sash_place(i, 1, int(y))
        except Exception:
            pass
        for pane in list(getattr(self, "panes", [])):
            path = getattr(pane, "path", None)
            if not path:
                continue
            path = os.path.abspath(path)
            cursor = (layout.get("cursor") or {}).get(path)
            if cursor:
                try:
                    pane.text.mark_set("insert", "%d.%d" % (int(cursor[0]), int(cursor[1])))
                except Exception:
                    pass
            scroll = (layout.get("scroll") or {}).get(path)
            if scroll is not None:
                try:
                    pane.text.yview_moveto(float(scroll))
                except Exception:
                    pass
            try:
                pane.minimap.redraw_map()
            except Exception:
                pass

    def _save_config(self):
        tabs = [p.path for p in self.panes if p.path and os.path.exists(p.path)]
        folds = {}
        for p in self.panes:
            if p.path:
                folds[p.path] = sorted(p._folded)
        cfg = {
            "theme": self.theme_name,
            "language": getattr(self, "lang", i18n.ZH_CN),
            "font_size": self.font_size,
            "indent_width": self.indent_width,
            "word_wrap": self.word_wrap,
            "auto_pair": bool(getattr(self, "auto_pair", True)),
            "autosave": self.autosave,
            "workspace": self.workspace,
            "font_family": self.mono,
            "minimap": self.show_minimap,
            "watches": list(self.watch_exprs),
            "tabs": tabs,
            "folds": folds,
            "layout": self._layout_snapshot(),
            "trusted_workspaces": list(getattr(self, "trusted_workspaces", [])),
            "recent": list(self._recent[:12]),
            "recent_commands": list(self._cmd_recent[:24]),
            "plugin_isolation": bool(getattr(self, "plugin_isolation", True)),
            "screen_reader": bool(getattr(self, "screen_reader", False)),
            "large_file_kb": int(getattr(self, "large_file_kb", 2000)),
            "large_file_mode": str(getattr(self, "large_file_mode", "auto")),
            "ai": dict(getattr(self, "ai_cfg", ai_assist.DEFAULT_CONFIG)),
            "sync": {"enabled": bool(getattr(self, "sync_enabled", False)),
                     "path": getattr(self, "sync_dir", ""),
                     "strategy": getattr(self, "sync_strategy", "newer")},
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        if getattr(self, "sync_enabled", False):
            try:
                from .editor_sync import push_snapshot
                sync_cfg = dict(cfg)
                if isinstance(sync_cfg.get("ai"), dict):
                    sync_cfg["ai"] = dict(sync_cfg["ai"])
                    sync_cfg["ai"]["api_key"] = ""  # 设置同步不携带密钥
                push_snapshot(self.sync_dir, sync_cfg, self.project_settings, __version__,
                              strategy=getattr(self, "sync_strategy", "newer"))
            except Exception:
                pass

    # ---------- 样式 ----------
    def _build_styles(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TNotebook", background=THEME["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=THEME["panel"], foreground=THEME["muted"],
                     padding=(14, 6), borderwidth=0)
        st.map("TNotebook.Tab",
               background=[("selected", THEME["editor"])],
               foreground=[("selected", THEME["fg"])])
        st.configure("Treeview", background=THEME["sidebar"], fieldbackground=THEME["sidebar"],
                     foreground=THEME["fg"], borderwidth=0, rowheight=25)
        st.configure("Explorer.Treeview", background=THEME["sidebar"],
                     fieldbackground=THEME["sidebar"], foreground=THEME["fg"],
                     borderwidth=0, relief="flat", rowheight=25)
        st.map("Explorer.Treeview", background=[("selected", THEME["accent_dim"])],
               foreground=[("selected", THEME["fg_bright"])])
        try:
            st.layout("Explorer.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except Exception:
            pass
        st.configure("TScrollbar", background=THEME["hover"], troughcolor=THEME["sidebar"],
                     bordercolor=THEME["sidebar"], arrowcolor=THEME["muted"],
                     width=10, arrowsize=10)
        st.configure("TPanedwindow", background=THEME["bg"])
        st.configure("Sash", background=THEME["border"], sashthickness=1)

    # ---------- 工具栏 ----------
    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=THEME["titlebar"], height=52)
        bar.grid(row=0, column=0, sticky="ew")
        bar.pack_propagate(False)
        self.toolbar = bar

        logo = tk.Canvas(bar, width=30, height=30, bg=THEME["titlebar"], highlightthickness=0)
        logo.pack(side=tk.LEFT, padx=(16, 11), pady=11)
        round_rect(logo, 1, 1, 29, 29, 8, fill=THEME["accent_soft"], outline=THEME["accent"])
        logo.create_text(15, 15, text="I+", fill=THEME["accent"], font=(self.mono, 12, "bold"))

        brand = tk.Frame(bar, bg=THEME["titlebar"])
        brand.pack(side=tk.LEFT)
        tk.Label(brand, text="Impor Studio", bg=THEME["titlebar"], fg=THEME["fg_bright"],
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(brand, text=f"I++ {__version__}  ·  Code Studio", bg=THEME["titlebar"],
                 fg=THEME["muted_dim"], font=("Microsoft YaHei UI", 7)).pack(anchor="w")

        tk.Frame(bar, bg=THEME["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=13, pady=13)

        for text, cb in (("新建", lambda: self.open_new_tab()),
                         ("打开", self.open_file),
                         ("保存", self.save_file)):
            FlatButton(bar, text, command=cb).pack(side=tk.LEFT, padx=2, pady=11)
        tk.Frame(bar, bg=THEME["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=9, pady=13)

        self.btn_run = FlatButton(bar, "\u25b6 运行", command=self.run_code, kind="success", width=90)
        self.btn_run.pack(side=tk.LEFT, padx=2, pady=11)
        self.btn_build = FlatButton(bar, "\u26a1 构建", command=self.build_exe, kind="primary", width=90)
        self.btn_build.pack(side=tk.LEFT, padx=2, pady=11)

        right = tk.Frame(bar, bg=THEME["titlebar"])
        right.pack(side=tk.RIGHT, padx=12)
        self.btn_lang = FlatButton(right, i18n.button_label(), command=self.show_language_menu,
                                   width=82)
        self.btn_lang.pack(side=tk.RIGHT, padx=4, pady=11)
        FlatButton(right, "⌘ 命令面板", command=self.open_palette, width=112).pack(side=tk.RIGHT, padx=4, pady=11)
        FlatButton(right, "内核3.0 预览", command=self.launch_kernel3_preview,
                   width=104).pack(side=tk.RIGHT, padx=4, pady=11)
        self.btn_sandbox = FlatButton(right, "沙盒: 关", command=self.toggle_sandbox, width=84)
        self.btn_sandbox.pack(side=tk.RIGHT, padx=4, pady=11)
        self.btn_debug = FlatButton(right, "调试: 关", command=self.toggle_debug, width=84)
        self.btn_debug.pack(side=tk.RIGHT, padx=4, pady=11)

    # ---------- 界面语言 ----------
    def show_language_menu(self):
        """单独“语言”按钮：三选一，立即切换并记住偏好。"""
        menu = tk.Menu(self.root, tearoff=0, bg=THEME["panel"], fg=THEME["fg"],
                       activebackground=THEME["accent_soft"],
                       activeforeground=THEME["fg_bright"])
        for code in i18n.LANGUAGES:
            mark = "✓ " if code == self.lang else "   "
            menu.add_command(
                label=mark + i18n.language_name(code),
                command=lambda c=code: self.set_language(c))
        try:
            x = self.btn_lang.winfo_rootx()
            y = self.btn_lang.winfo_rooty() + self.btn_lang.winfo_height() + 4
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def set_language(self, code):
        code = i18n.normalize_language(code)
        if code == self.lang:
            return
        self.lang = i18n.set_language(code)
        self.cfg["language"] = self.lang
        self._save_config()
        i18n.retranslate(self.root)
        # 自绘按钮不在 Tk 文本参数里，单独重绘；源文案始终保留“语言”。
        self.btn_lang.set_text("语言")
        try:
            self.update_status()
        except Exception:
            pass
        try:
            self._apply_workspace_trust()
            self._apply_workspace_title()
        except Exception:
            pass
        self.toast("语言已切换：" + i18n.language_name(self.lang), "ok")

    # ---------- 工作区信任 ----------
    @staticmethod
    def _trust_key(path):
        return os.path.normcase(os.path.abspath(str(path)))

    def _workspace_is_trusted(self, path):
        try:
            return self._trust_key(path) in self.trusted_workspaces
        except Exception:
            return True

    def _build_trust_bar(self):
        """未信任工作区的受限模式横幅；不阻断浏览，只阻断执行类操作。"""
        self.trust_bar = tk.Frame(self.root, bg=THEME["panel_alt"], height=30)
        self.trust_bar.grid(row=1, column=0, sticky="ew")
        self.trust_bar.grid_propagate(False)
        tk.Frame(self.trust_bar, bg=THEME["yellow"], height=1).pack(side=tk.TOP, fill=tk.X)
        self.trust_label = tk.Label(
            self.trust_bar, text="受限模式：此工作区尚未信任，运行 / 构建 / 终端 / 项目插件已禁用",
            bg=THEME["panel_alt"], fg=THEME["yellow"],
            font=("Microsoft YaHei UI", 8), anchor="w")
        self.trust_label.pack(side=tk.LEFT, padx=(14, 8), pady=5)
        FlatButton(self.trust_bar, "信任此工作区", command=self.trust_current_workspace,
                   kind="primary", width=104, height=22).pack(side=tk.RIGHT, padx=10, pady=4)
        self.trust_bar.grid_remove()

    def _apply_workspace_trust(self):
        try:
            if self.workspace_trusted:
                self.trust_bar.grid_remove()
            else:
                self.trust_bar.grid()
                self.trust_label.configure(
                    text="受限模式：%s 尚未信任，运行 / 构建 / 终端 / 项目插件已禁用"
                         % os.path.basename(self.workspace))
        except Exception:
            pass
        try:
            self.status_trust.configure(
                text="已信任" if self.workspace_trusted else "受限模式",
                fg=THEME["green"] if self.workspace_trusted else THEME["yellow"])
        except Exception:
            pass

    def trust_current_workspace(self):
        key = self._trust_key(self.workspace)
        if key not in self.trusted_workspaces:
            self.trusted_workspaces.append(key)
        self.workspace_trusted = True
        self._save_config()
        self._apply_workspace_trust()
        self._apply_workspace_title()
        try:
            if self.plugins:
                self.plugins.load()
                self._refresh_plugin_ui()
        except Exception as exc:
            self.toast("插件加载失败：" + str(exc)[:100], "err")
        self.toast("已信任此工作区：" + self.workspace, "ok")

    def _ensure_trusted(self, action="此操作"):
        if getattr(self, "workspace_trusted", True):
            return True
        msg = "受限模式：%s 尚未信任，%s 已禁用。请先点击顶部「信任此工作区」。" % (
            os.path.basename(self.workspace), action)
        try:
            self.toast(msg, "err")
        except Exception:
            pass
        try:
            self.trust_bar.grid()
        except Exception:
            pass
        return False

    def launch_kernel3_preview(self):
        """启动独立的 Qt 编辑内核预览；不替换当前 Tk 面板。"""
        try:
            import PyQt6.QtWidgets  # noqa: F401
        except Exception:
            messagebox.showinfo(
                "内核 3.0 预览",
                "当前环境没有 PyQt6，无法启动内核预览。\n\n"
                "安装命令：python -m pip install PyQt6")
            return
        pane = self.current_pane()
        path = getattr(pane, "path", "") if pane is not None else ""
        args = [sys.executable, "-m", "impor.kernel3"]
        if path and os.path.isfile(path):
            args.append(path)
        else:
            self.toast("当前文件尚未保存，内核预览将打开空白窗口", "warn")
        env = dict(os.environ)
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = INSTALL_ROOT + ((";" + old) if old else "")
        try:
            subprocess.Popen(args, cwd=INSTALL_ROOT, env=env)
        except Exception as exc:
            messagebox.showerror("启动失败", "无法启动内核 3.0 预览：\n" + str(exc))
            return
        self.toast("已启动编辑内核 3.0 预览（独立窗口）", "ok")

    # ---------- 主体 ----------
    def _build_main(self):
        self.paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=THEME["bg"],
                                    sashwidth=4, bd=0, sashrelief="flat")
        self.paned.grid(row=2, column=0, sticky="nsew")

        self.activity = ActivityBar(self.paned, self, [
            ("explorer", "资源管理器", self.toggle_sidebar),
            ("search", "全项目搜索", self.global_search),
            ("run", "运行", self.run_code),
            ("memory", "内存", lambda: self.show_bottom("mem")),
            ("palette", "命令面板", self.open_palette),
        ])
        self.paned.add(self.activity, minsize=46, width=46)

        side = tk.Frame(self.paned, bg=THEME["sidebar"])
        head = tk.Frame(side, bg=THEME["panel_head"], height=32)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="资源管理器", bg=THEME["panel_head"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 8, "bold")).pack(side=tk.LEFT, padx=12)
        _minus = tk.Label(head, text="－", bg=THEME["panel_head"], fg=THEME["muted"],
                          font=("Microsoft YaHei UI", 9), cursor="hand2")
        _minus.pack(side=tk.RIGHT, padx=(0, 2))
        _minus.bind("<Button-1>", lambda e: self.remove_workspace_root())
        _plus = tk.Label(head, text="＋", bg=THEME["panel_head"], fg=THEME["fg_bright"],
                         font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
        _plus.pack(side=tk.RIGHT, padx=(0, 2))
        _plus.bind("<Button-1>", lambda e: self.add_workspace_root())
        tk.Label(head, text="···", bg=THEME["panel_head"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.RIGHT, padx=10)
        self.tree = ttk.Treeview(side, show="tree", selectmode="browse", style="Explorer.Treeview")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=(6, 6), pady=(6, 6))
        self.plugin_nb = ttk.Notebook(side, height=180)
        self.tree.bind("<Double-Button-1>", self._on_tree_open)
        self.tree.bind("<Return>", self._on_tree_open)
        self.tree.bind("<Button-3>", self._tree_menu)
        self.tree.bind("<<TreeviewOpen>>", self._tree_expand)
        side.pack_propagate(False)
        side.configure(width=212)
        self.sidebar_ref = side
        self.paned.add(side, minsize=150, width=212)
        self._fill_tree()

        self.center = tk.PanedWindow(self.paned, orient=tk.VERTICAL, bg=THEME["bg"],
                                     sashwidth=4, bd=0, sashrelief="flat")
        self.paned.add(self.center, stretch="always")

        outline = tk.Frame(self.paned, bg=THEME["sidebar"])
        tk.Label(outline, text="OUTLINE", bg=THEME["panel_head"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 8, "bold"), anchor="w").pack(fill=tk.X, ipady=7, padx=10)
        self.outline_tree = ttk.Treeview(outline, show="tree", selectmode="browse",
                                         style="Explorer.Treeview")
        self.outline_tree.pack(fill=tk.BOTH, expand=True, padx=(6, 6), pady=(6, 6))
        self.outline_tree.bind("<Double-Button-1>", self._outline_jump)
        self.outline_tree.bind("<Return>", self._outline_jump)
        self.outline_panel = outline
        self.paned.add(outline, minsize=150, width=190)

        self.editor_area = tk.Frame(self.center, bg=THEME["editor"])
        self.tabstrip = TabStrip(self.editor_area, self)
        self.tabstrip.pack(fill=tk.X)
        crumb = tk.Frame(self.editor_area, bg=THEME["titlebar"], height=26)
        crumb.pack(fill=tk.X)
        crumb.pack_propagate(False)
        self.crumb_left = tk.Label(crumb, text="", bg=THEME["titlebar"], fg=THEME["muted"],
                                   font=("Microsoft YaHei UI", 8), anchor="w")
        self.crumb_left.pack(side=tk.LEFT, padx=14)
        self.crumb_right = tk.Label(crumb, text="", bg=THEME["titlebar"], fg=THEME["muted_dim"],
                                    font=("Microsoft YaHei UI", 8))
        self.crumb_right.pack(side=tk.RIGHT, padx=14)
        tk.Frame(self.editor_area, bg=THEME["border_soft"], height=1).pack(fill=tk.X)
        self.editor_split = tk.PanedWindow(self.editor_area, orient=tk.HORIZONTAL,
                                           bg=THEME["bg"], sashwidth=4, bd=0, sashrelief="flat")
        self.editor_split.pack(fill=tk.BOTH, expand=True)
        self.pagehost = tk.Frame(self.editor_split, bg=THEME["editor"])
        self.editor_split.add(self.pagehost, stretch="always")
        self.split_frame = None
        self.split_pane = None
        self.split_tabstrip = None
        self.split_pagehost = None
        self.center.add(self.editor_area, height=420, stretch="always")

        self.bottom = None
        self._bottom_ready = False
        # 底部六联面板（输出/问题/测试/变量/调试/内存）在 7.2 中延迟到
        # 外壳可见后构建，避免同步 UI 创建阻塞首帧。



    # ---------- 状态栏 ----------





    # ---------- 资源管理器 ----------


















    # ---------- 标签 ----------















    def open_path(self, path, force_full=False):
        path = os.path.abspath(path)
        if path not in self._recent:
            self._recent.insert(0, path)
            self._recent = self._recent[:12]
        for pane in self.panes:
            if pane.path and os.path.abspath(pane.path) == os.path.abspath(path):
                self.select_pane(pane)
                return pane
        size = 0
        threshold = 2000 * 1024
        try:
            threshold = max(1, int(getattr(self, "large_file_kb", 2000))) * 1024
            size = os.path.getsize(path)
            if largefile.should_use_chunked(size, self.large_file_kb,
                                            getattr(self, "large_file_mode", "auto")) and not force_full:
                try:
                    from .editor_virtual import read_large_lines
                    lines = read_large_lines(path)
                    with open(path, "rb") as fh:
                        raw_head = fh.read(4096)
                except Exception as exc:
                    messagebox.showerror("读取失败", str(exc))
                    return None
                pane = self.open_virtual_tab(path, lines)
                if pane is None:
                    return None
                pane._encoding = "UTF-8"
                pane._newline = "CRLF" if b"\r\n" in raw_head else "LF"
                pane._bom = raw_head.startswith(b"\xef\xbb\xbf")
                if hasattr(self, "_file_mtimes"):
                    try:
                        self._file_mtimes[path] = os.path.getmtime(path)
                        self._external_warned.discard(path)
                    except Exception:
                        pass
                self.show_bottom("out")
                self._set_out("已自动分块编辑 %s（%d 行）；Ctrl+S 原子写回整文件，"
                              "需要全文视图可点面包屑或命令面板「切换大文件为完整编辑」。\n"
                              % (path, len(lines)), "dim")
                self.toast("大文件：已自动分块编辑，只渲染当前窗口", "ok")
                return pane
            if size > threshold and not force_full and str(
                    getattr(self, "large_file_mode", "auto")) == "ask":
                ans = messagebox.askyesnocancel(
                    "大文件",
                    f"该文件约 {size / 1024 / 1024:.1f} MB。\n\n"
                    "是：大文件分块编辑（只保留当前窗口，行号绝对，改动逐块回写）\n"
                    "否：完整编辑（可能明显卡顿）\n"
                    "取消：不打开")
                if ans is None:
                    self.toast("已取消打开大文件")
                    return None
                if ans:
                    try:
                        from .editor_virtual import read_large_lines
                        lines = read_large_lines(path)
                        with open(path, "rb") as fh:
                            raw_head = fh.read(4096)
                    except Exception as exc:
                        messagebox.showerror("读取失败", str(exc))
                        return None
                    pane = self.open_virtual_tab(path, lines)
                    if pane is None:
                        return None
                    pane._encoding = "UTF-8"
                    pane._newline = "CRLF" if b"\r\n" in raw_head else "LF"
                    pane._bom = raw_head.startswith(b"\xef\xbb\xbf")
                    self.show_bottom("out")
                    self._set_out(f"已以虚拟滚动打开 {path}（{len(lines)} 行）\n", "dim")
                    return pane
        except Exception as exc:
            # 大文件走分块分支失败时不要悄悄退回全文打开，否则 10 MB 会直接卡死
            if size > threshold and not force_full:
                messagebox.showerror("打开大文件失败", str(exc))
                return None
            size = 0
        try:
            raw = open(path, "rb").read()
            bom = raw.startswith(b"\xef\xbb\xbf")
            content = raw.decode("utf-8-sig" if bom else "utf-8")
            newline = "CRLF" if raw.count(b"\r\n") * 2 >= raw.count(b"\n") and raw.count(b"\r\n") else "LF"
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return None
        pane = self.open_new_tab(content=content, path=path)
        pane._encoding = "UTF-8 BOM" if bom else "UTF-8"
        pane._newline = newline
        pane._bom = bom
        pane._ends_nl = raw.endswith(b"\n")
        if size > getattr(self, "large_file_kb", 2000) * 1024:
            pane._large_file = True
            self.toast("大文件模式：只高亮可视区，缩略图已降采样", "warn")
        if hasattr(self, "_file_mtimes"):
            try:
                self._file_mtimes[path] = os.path.getmtime(path)
                self._external_warned.discard(path)
            except Exception:
                pass
        self.show_bottom("out")
        self._set_out(f"已打开 {path}\n", "dim")
        return pane

    def open_file(self):
        p = filedialog.askopenfilename(filetypes=SPYP_FILETYPES, initialdir=ROOT_DIR)
        if p:
            self.open_path(p)

    def save_file(self):
        pane = self.current_pane()
        if pane is None:
            return
        if getattr(pane, "_virtual_readonly", False):
            self.toast("大文件虚拟滚动为只读；点击横条「转为完整编辑」后再保存", "warn")
            return
        if not pane.path:
            return self.save_file_as()
        try:
            if getattr(pane, "flush_virtual", None):
                pane.flush_virtual()
            if getattr(self, "auto_format", True) and not getattr(pane, "_large_file", False):
                try:
                    from .fmt import fmt_source
                    raw = pane.text.get("1.0", "end-1c")
                    pretty = fmt_source(raw)
                    if pretty != raw:
                        pos = pane.text.index("insert")
                        pane.text.delete("1.0", "end-1c")
                        pane.text.insert("1.0", pretty)
                        try:
                            pane.text.mark_set("insert", pos)
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                old_mtime = self._file_mtimes.get(pane.path)
                disk_mtime = os.path.getmtime(pane.path)
                if old_mtime and disk_mtime != old_mtime:
                    ans = messagebox.askyesnocancel(
                        "保存冲突",
                        "磁盘文件已被外部修改。\n\n是：先看差异\n否：覆盖保存\n取消：放弃保存\n\n" + pane.path,
                        parent=self.root)
                    if ans is None:
                        return
                    if ans:
                        self.show_diff()
                        return
            except OSError:
                pass
            enc = "utf-8-sig" if getattr(pane, "_bom", False) else "utf-8"
            nl = "\r\n" if getattr(pane, "_newline", "LF") == "CRLF" else "\n"
            full = pane.get_content() if getattr(pane, "get_content", None) else pane.text.get("1.0", "end-1c")
            full = _align_eol(full, pane)
            # 3.2.1：先写同目录临时文件再原子替换，避免断电/崩溃留下半截文件。
            tmp_fd, tmp_path = tempfile.mkstemp(prefix=".impor_save_", dir=os.path.dirname(pane.path) or ".")
            try:
                with os.fdopen(tmp_fd, "w", encoding=enc, newline=nl) as f:
                    f.write(full)
                os.replace(tmp_path, pane.path)
            except Exception as exc:
                note_swallowed("editor.save", exc)
                try:
                    os.unlink(tmp_path)
                except Exception as exc2:
                    note_swallowed("editor.save.tmp_cleanup", exc2)
                raise
            try:
                from .security import AuditLog
                AuditLog(getattr(self, "workspace", os.path.dirname(pane.path))).write(
                    "file.save", path=os.path.abspath(pane.path), bytes=len(full),
                    atomic=True)
            except Exception as exc:
                note_swallowed("editor.save.audit", exc)
            pane._dirty = False
            try:
                self._file_mtimes[pane.path] = os.path.getmtime(pane.path)
                self._external_warned.discard(pane.path)
            except Exception:
                pass
            if not getattr(pane, "_large_file", False):
                self.mark_dirty(pane)
                self._lint_pane(pane)
            else:
                # 3.2.1：大文件保存后不做全量 AST/lint，避免 2MB+ 文件卡死 UI。
                self._err_count = 0
                self._warn_count = 0
                try:
                    self.problems.delete("1.0", "end")
                    self.problems.insert("end", "大文件：跳过全量诊断（可对单个窗口运行检查）。\n", "dim")
                    self._refresh_problems()
                except Exception:
                    pass
            self.update_status()
            self._set_out(f"已保存 {pane.path}\n", "ok")
            self.toast("已保存  " + os.path.basename(pane.path), "ok")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    # ---------- 4.7.3 崩溃安全：定时器保护 + 恢复副本 ----------
    def _after_safe(self, ms, fn, *args):
        """定时器链保护：一次 tick 抛异常不再让整条 after 链静默断掉。

        默认 Tk 的 after 回调抛异常只会交给 report_callback_exception，
        链本身不会重排——自动保存/高亮/文件监视会就此永久失效且无任何提示。
        """
        def _wrapped():
            try:
                fn(*args)
            except Exception as exc:
                try:
                    note_swallowed("editor.after:%s" % getattr(fn, "__name__", "?"), exc)
                except Exception:
                    pass
                try:
                    from .stability import write_crash_log
                    write_crash_log(exc, where="editor.after")
                except Exception:
                    pass
                try:
                    self.root.after(ms, _wrapped)
                except Exception:
                    pass

        try:
            return self.root.after(ms, _wrapped)
        except Exception:
            return None

    def _recovery_path(self):
        """恢复副本目录：先本地 .impor_recovery，装到只读目录时退到 ~/.impor/recovery。

        4.7.3：不再依赖 __init__ 里那次 makedirs —— 目录不在就等于「崩溃兜底」失效，
        而失败还被静默吞掉。这里每写一次都确保目录真的存在。
        """
        for d in (getattr(self, "_recovery_dir", None)
                  or os.path.join(os.path.expanduser("~"), ".impor", "recovery"),
                  os.path.join(ROOT_DIR, ".impor_recovery")):
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except Exception:
                continue
        return os.path.join(os.path.expanduser("~"), ".impor", "recovery")

    def _flush_recovery(self, reason="auto"):
        """把所有「未保存」标签写成恢复副本（.impor 正文 + .json 元信息）。

        只写 `.impor_recovery`，绝不碰用户原文件。3 秒级落盘后，
        即便进程被强杀/断电，最多丢最后 3 秒输入。
        """
        written = 0
        rdir = self._recovery_path()
        for pane in self.panes:
            if not getattr(pane, "_dirty", False):
                continue
            try:
                body = pane.text.get("1.0", "end-1c")
            except Exception:
                continue
            base = os.path.join(rdir, "recover_%s" % id(pane))
            try:
                with open(base + ".impor", "w", encoding="utf-8", newline="\n") as f:
                    f.write(body)
                with open(base + ".json", "w", encoding="utf-8", newline="\n") as f:
                    json.dump({"path": pane.path or "",
                               "title": getattr(pane, "tab_title", "") or getattr(pane, "title", ""),
                               "bom": bool(getattr(pane, "_bom", False)),
                               "ends_nl": bool(getattr(pane, "_ends_nl", True)),
                               "newline": getattr(pane, "_newline", "LF"),
                               "reason": reason, "ts": time.time()},
                              f, ensure_ascii=False)
                written += 1
            except Exception as exc:
                note_swallowed("editor.recovery.write", exc)
        return written

    def _recovery_tick(self):
        """3 秒一次：只写恢复副本。开销与脏标签数量成正比，不扫全盘。"""
        try:
            self._flush_recovery("tick")
        except Exception:
            pass
        self._after_safe(3000, self._recovery_tick)

    def _autosave_tick(self):
        try:
            if self.autosave:
                for pane in self.panes:
                    if not pane._dirty:
                        continue
                    if pane.path:
                        enc = "utf-8-sig" if getattr(pane, "_bom", False) else "utf-8"
                        nl = "\r\n" if getattr(pane, "_newline", "LF") == "CRLF" else "\n"
                        full = pane.get_content() if getattr(pane, "get_content", None) else pane.text.get("1.0", "end-1c")
                        full = _align_eol(full, pane)
                        with open(pane.path, "w", encoding=enc, newline=nl) as f:
                            f.write(full)
                        pane._dirty = False
                        try:
                            self._file_mtimes[pane.path] = os.path.getmtime(pane.path)
                        except Exception:
                            pass
                    else:
                        p = os.path.join(self._recovery_path(), f"recover_{id(pane)}.impor")
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(pane.text.get("1.0", "end-1c"))
                self.mark_dirty(None)
        except Exception as exc:
            note_swallowed("editor.autosave", exc)
        self._after_safe(30000, self._autosave_tick)

    def restore_recovery(self):
        items = []
        rdir = self._recovery_path()
        try:
            for fn in sorted(os.listdir(rdir)):
                if fn.endswith(".impor"):
                    p = os.path.join(rdir, fn)
                    items.append((fn, p))
        except Exception:
            pass
        if not items:
            self.toast("没有可恢复内容")
            return
        for fn, p in items:
            try:
                with open(p, encoding="utf-8") as f:
                    content = f.read()
                meta = {}
                meta_path = p[:-len(".impor")] + ".json"
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f) or {}
                except Exception:
                    pass
                title = meta.get("title") or os.path.basename(meta.get("path") or "") or ("恢复-" + fn)
                pane = self.open_new_tab(content=content, title=str(title))
                # 4.7.3：把原文件路径与编码特征一并接回，恢复后 Ctrl+S 可直接存回原文件。
                if meta.get("path"):
                    pane.path = str(meta["path"])
                pane._bom = bool(meta.get("bom", False))
                pane._newline = meta.get("newline", "LF")
                pane._ends_nl = bool(meta.get("ends_nl", True))
                pane._encoding = "UTF-8 BOM" if pane._bom else "UTF-8"
                pane._dirty = True
                os.remove(p)
                try:
                    os.remove(meta_path)
                except Exception:
                    pass
            except Exception as exc:
                note_swallowed("editor.recovery.restore", exc)
        self.toast("已恢复 %d 个未保存文件" % len(items), "ok")

    def save_file_as(self):
        pane = self.current_pane()
        if pane is None:
            return
        if getattr(pane, "_virtual_readonly", False):
            self.toast("大文件虚拟滚动为只读；先转为完整编辑再另存", "warn")
            return
        p = filedialog.asksaveasfilename(defaultextension=SPYP_SUFFIX, filetypes=SPYP_FILETYPES,
                                         initialdir=ROOT_DIR)
        if not p:
            return
        pane.path = p
        pane.tab_title = os.path.basename(p)
        self.save_file()
        self.tabstrip.redraw()

    def show_diff(self, focus=0):
        pane = self.current_pane()
        if pane is None or not pane.path or not os.path.exists(pane.path):
            self.toast("当前标签没有磁盘文件可对比")
            return
        try:
            with open(pane.path, "r", encoding="utf-8-sig") as f:
                disk_text = f.read()
            disk = disk_text.splitlines()
        except Exception as e:
            messagebox.showerror("对比失败", str(e))
            return
        cur = pane.text.get("1.0", "end-1c").splitlines()
        groups = list(difflib.SequenceMatcher(None, disk, cur).get_grouped_opcodes(3))
        state = {"idx": max(0, min(int(focus or 0), len(groups) - 1)) if groups else 0}
        diff = list(difflib.unified_diff(disk, cur, fromfile="磁盘 " + os.path.basename(pane.path),
                                         tofile="编辑器", lineterm=""))
        win = tk.Toplevel(self.root)
        apply_window_icon(win)
        win.title("与磁盘对比 · " + os.path.basename(pane.path))
        win.geometry("980x560")
        win.configure(bg=THEME["editor"])
        bar = tk.Frame(win, bg=THEME["panel_head"], height=32)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        hdr = tk.Label(bar, text=("无差异" if not diff else
                                  "共 %d 行差异 · 第 %d/%d 块" % (len(diff) - 2, state["idx"] + 1, len(groups))),
                       bg=THEME["panel_head"], fg=THEME["fg"], font=("Microsoft YaHei UI", 9, "bold"))
        hdr.pack(side=tk.LEFT, padx=12)

        btnbar = tk.Frame(win, bg=THEME["panel"])
        btnbar.pack(side=tk.BOTTOM, fill=tk.X, pady=6)

        txt = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], insertbackground=THEME["fg"],
                      font=(self.mono, 10), wrap="none", bd=0, padx=10, pady=8)
        sb = tk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.tag_config("add", foreground=THEME["green"])
        txt.tag_config("del", foreground=THEME["red"])
        txt.tag_config("hunk", foreground=THEME["accent"])
        for line in (diff or ["（编辑器内容与磁盘一致）"]):
            tag = "add" if line.startswith("+") and not line.startswith("+++") else \
                  "del" if line.startswith("-") and not line.startswith("---") else \
                  "hunk" if line.startswith("@@") else ""
            txt.insert(tk.END, line + "\n", tag)

        def adopt_disk():
            pos = pane.text.index("insert")
            pane.text.delete("1.0", "end-1c")
            pane.text.insert("1.0", disk_text)
            try:
                pane.text.mark_set("insert", pos)
            except Exception:
                pass
            pane._dirty = False
            pane.highlight()
            pane.redraw_lineno()
            try:
                self._file_mtimes[pane.path] = os.path.getmtime(pane.path)
            except Exception:
                pass
            self.toast("已载入磁盘版本", "ok")
            win.destroy()

        def save_editor():
            try:
                enc = "utf-8-sig" if getattr(pane, "_bom", False) else "utf-8"
                nl = "\r\n" if getattr(pane, "_newline", "LF") == "CRLF" else "\n"
                with open(pane.path, "w", encoding=enc, newline=nl) as f:
                    f.write(pane.text.get("1.0", "end-1c"))
                pane._dirty = False
                self._file_mtimes[pane.path] = os.path.getmtime(pane.path)
                self._lint_pane(pane)
                self.toast("已用编辑器版本覆盖磁盘", "ok")
                win.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=win)

        def apply_hunk():
            if not groups:
                return
            idx = state["idx"]
            pane._diff_undo.append(pane.text.get("1.0", "end-1c"))
            del pane._diff_undo[:-30]
            lines = pane.text.get("1.0", "end-1c").split("\n")
            for tag, i1, i2, j1, j2 in reversed(groups[idx]):
                if tag != "equal":
                    lines[i1:i2] = disk[j1:j2]
            pane.text.delete("1.0", "end-1c")
            pane.text.insert("1.0", "\n".join(lines))
            pane._dirty = True
            pane.highlight()
            pane.redraw_lineno()
            win.destroy()
            self.show_diff(focus=idx)

        def undo_hunk():
            if not pane._diff_undo:
                self.toast("没有可撤销的块操作")
                return
            pane.text.delete("1.0", "end-1c")
            pane.text.insert("1.0", pane._diff_undo.pop())
            pane._dirty = True
            pane.highlight()
            pane.redraw_lineno()
            win.destroy()
            self.show_diff(focus=0)

        def goto_hunk(delta):
            state["idx"] = max(0, min(len(groups) - 1, state["idx"] + delta))
            win.destroy()
            self.show_diff(focus=state["idx"])

        def next_hunk():
            idx = txt.search("@@", "insert", stopindex="end")
            if not idx:
                idx = txt.search("@@", "1.0", stopindex="end")
            if idx:
                txt.see(idx)
                txt.mark_set("insert", idx)
                txt.tag_add("sel", idx, idx + " lineend")

        FlatButton(btnbar, "上一块", command=lambda: goto_hunk(-1), width=70).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "采用磁盘版当前块", command=apply_hunk, kind="primary", width=136
                   ).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "撤销上一块", command=undo_hunk, width=104).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "保留编辑器版", command=lambda: goto_hunk(1), width=104
                   ).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "下一块", command=lambda: goto_hunk(1), width=70).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "保存编辑器版", command=save_editor, width=104).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "全部载入磁盘", command=adopt_disk, width=104).pack(side=tk.LEFT, padx=4)
        FlatButton(btnbar, "关闭", command=win.destroy, width=64).pack(side=tk.RIGHT, padx=6)
        txt.configure(state="disabled")

    # ---------- 输出 ----------
    def _set_out(self, text, kind="info"):
        self.out.insert(tk.END, text, kind)
        self.out.see(tk.END)
        win = getattr(self, "_out_win", None)
        if win is not None and win.winfo_exists():
            self._out_win_text.insert(tk.END, text, kind)
            self._out_win_text.see(tk.END)

    def open_out_window(self):
        """把输出弹到独立窗口，与底部输出实时同步（含颜色）。"""
        win = getattr(self, "_out_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("Impor 输出")
        win.configure(bg=THEME["panel"])
        win.geometry("780x460")
        bar = tk.Frame(win, bg=THEME["panel_head"], height=32)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        FlatButton(bar, "清空", command=self.clear_output, width=54,
                   height=22).pack(side=tk.RIGHT, padx=(6, 10), pady=5)
        txt = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                      bd=0, font=(self.mono, 10), padx=10, pady=8, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)
        for tag, color in (("info", THEME["fg"]), ("ok", THEME["green"]),
                           ("err", THEME["red"]), ("warn", THEME["yellow"]),
                           ("dim", THEME["muted"])):
            txt.tag_config(tag, foreground=color)
        # 把已有输出原样复制，再按源控件的 tag 区间逐段补上颜色（内容一致，索引一一对应）
        txt.insert("1.0", self.out.get("1.0", "end-1c"))
        for tag_name in ("info", "ok", "err", "warn", "dim"):
            ranges = self.out.tag_ranges(tag_name)
            for i in range(0, len(ranges), 2):
                txt.tag_add(tag_name,
                            self.out.index(ranges[i]),
                            self.out.index(ranges[i + 1]))
        txt.see(tk.END)

        def close_out_win():
            self._out_win = None
            self._out_win_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_out_win)
        self._out_win = win
        self._out_win_text = txt

    def run_in_console(self):
        """在一个全新的系统控制台窗口里运行当前代码（像 Python 那样独立黑窗口）。"""
        pane = self.current_pane()
        if pane is None or not self._ensure_trusted("控制台运行"):
            return
        src = pane.text.get("1.0", "end-1c")
        import tempfile
        tmp = os.path.join(tempfile.gettempdir(), "impor_console_run.impor")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        bat = os.path.join(tempfile.gettempdir(), "impor_console_run.bat")
        py = sys.executable
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            'cd /d "%s"' % os.getcwd(),
            'echo ^> 运行 %s' % (pane.path or "未命名"),
            '"%s" -m impor run "%s"' % (py, tmp),
            "echo.",
            "echo 运行结束，按任意键关闭窗口 …",
            "pause >nul",
        ]
        with open(bat, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(lines))
        # CREATE_NEW_CONSOLE = 0x10
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x10)

    def append_out(self, text, kind=None):
        if kind is None:
            low = text.lower()
            kind = "info"
            if "❌" in text or "🔴" in text or "error" in low or "异常" in text:
                kind = "err"
            elif text.startswith("✓") or text.startswith("c 运行") or "成功" in text:
                kind = "ok"
            elif "警告" in text or text.startswith("⚠"):
                kind = "warn"
        self._set_out(text + "\n", kind)
        if kind == "err":
            self._last_error = text
            self._err_count += 1
            self.problems.insert(tk.END, text + "\n", "err")
            self.problems.see(tk.END)
        elif kind == "warn":
            self._warn_count += 1
        self._refresh_problems()

    def _refresh_problems(self):
        try:
            self.status_problems.configure(
                text=f"错误 {self._err_count}   警告 {self._warn_count}",
                fg=THEME["red"] if self._err_count else THEME["muted"])
        except Exception:
            pass

    def _collect_lint(self, src, path):
        """纯计算诊断，不碰 Tk；可在后台线程调用。"""
        try:
            ast = Parser(tokenize(src)).program()
            res = analyze(ast, src.split("\n"))
            issues = list(getattr(res, "issues", res) or [])
        except Exception as exc:
            return [], str(exc)
        try:
            issues += [i for i in style_lint(src, os.path.basename(path or "untitled"))]
        except Exception:
            pass
        # v7.3：类型 / 穷尽性 / 访问控制诊断并入「问题」面板
        try:
            import types
            for ti in check_source(src, None, path or ""):
                issues.append(types.SimpleNamespace(
                    line=ti.line, col=ti.col, severity="error",
                    msg="%s（%s）" % (ti.msg, ti.code)))
        except Exception:
            pass
        return issues, None

    def _apply_lint(self, pane, src, issues, error=None, offset=0):
        """应用诊断：每个 pane 保留自己的标记；面板只跟随当前 pane。"""
        marks = []
        display_error = error
        if error:
            # 解析器在 EOF 用 len(lines)+1 占位，会显示“第9999行”；回落到最后一个有效行。
            lines = src.rstrip("\n").split("\n")
            last_line = max(1, len(lines))
            m = re.search(r"第\s*(\d+)\s*行", str(error))
            err_line = int(m.group(1)) if m else 1
            if err_line > last_line:
                err_line = last_line
                display_error = re.sub(r"第\s*\d+\s*行", f"第{err_line}行", str(error))
            marks = [(err_line, 1, "error")]
        else:
            for issue in issues[:80]:
                marks.append((issue.line + offset, issue.col, issue.severity))
        pane._problem_marks = marks
        pane._lint_offset = offset
        pane._lint_payload = (list(issues), error, offset)
        self._apply_inline_diagnostics(pane, marks)
        if pane is self.current_pane():
            self.problems.delete("1.0", "end")
            if error:
                self.problems.insert("end", display_error + "\n", "err")
                self._err_count = 1
                self._warn_count = 0
            else:
                name = os.path.basename(pane.path or "未命名")
                for issue in issues[:80]:
                    line = issue.line + offset
                    text = f"{name}:{line}:{issue.col} [{issue.severity}] {issue.msg}\n"
                    tag = "err" if issue.severity == "error" else "warn"
                    self.problems.insert("end", text, tag)
                self._err_count = sum(1 for i in issues if i.severity == "error")
                self._warn_count = sum(1 for i in issues if i.severity != "error")
                if not issues:
                    self.problems.insert("end", "没有发现问题。\n", "dim")
            self._problem_marks = marks
            self._refresh_problems()
        for p in self.panes:
            try:
                p.redraw_lineno()
            except Exception:
                pass

    def _apply_inline_diagnostics(self, pane, marks):
        """3.6.6：把诊断画进文本行内，而不只留在底部问题面板。"""
        text = getattr(pane, "text", None)
        if text is None:
            return
        for tag in ("diag_error_line", "diag_warn_line",
                    "diag_error_span", "diag_warn_span"):
            try:
                text.tag_remove(tag, "1.0", "end")
            except Exception:
                pass
        light = getattr(self, "theme_name", "dark") == "light"
        try:
            text.tag_configure("diag_error_line",
                               background="#ffe5e5" if light else "#3b1b20")
            text.tag_configure("diag_warn_line",
                               background="#fff4cf" if light else "#3a331b")
            text.tag_configure("diag_error_span", underline=True, foreground=THEME["red"])
            text.tag_configure("diag_warn_span", underline=True, foreground=THEME["yellow"])
        except Exception:
            pass
        for m in marks or []:
            try:
                ln, col = int(m[0]), int(m[1])
            except Exception:
                continue
            sev = "error" if len(m) < 3 or m[2] == "error" else "warn"
            try:
                if ln < 1 or ln > int(text.index("end-1c").split(".")[0]):
                    continue
                text.tag_add("diag_%s_line" % sev, f"{ln}.0", f"{ln}.end")
                start = f"{ln}.{max(0, col - 1)}"
                end = f"{ln}.{max(1, col + 1)}"
                text.tag_add("diag_%s_span" % sev, start, end)
            except Exception:
                pass
        for tag in ("diag_error_line", "diag_warn_line", "test_fail"):
            try:
                text.tag_raise(tag)
            except Exception:
                pass

    def goto_next_problem(self, delta=1):
        """F8 / Shift+F8：在当前文件的问题之间跳转，循环。"""
        pane = self.current_pane()
        if pane is None:
            return
        marks = sorted({(int(m[0]), int(m[1])) for m in (getattr(pane, "_problem_marks", []) or [])
                        if len(m) >= 2}, key=lambda x: (x[0], x[1]))
        if not marks:
            self.toast("当前文件没有问题", "ok")
            return
        try:
            cur = int(pane.text.index("insert").split(".")[0])
        except Exception:
            cur = 1
        if delta >= 0:
            candidate = [m for m in marks if m[0] > cur] or marks
        else:
            candidate = [m for m in marks if m[0] < cur] or marks
        line, col = candidate[0] if delta >= 0 else candidate[-1]
        try:
            pane.text.mark_set("insert", f"{line}.{max(0, col - 1)}")
            pane.text.see("insert")
            pane.focus_text()
            self.show_bottom("problems")
            self.update_status()
        except Exception:
            pass
        self.toast("问题 %d/%d · 第 %d 行" % (
            marks.index((line, col)) + 1, len(marks), line), "warn")

    def goto_prev_problem(self):
        return self.goto_next_problem(-1)

    def open_keybindings(self):
        path = getattr(getattr(self, "keymap", None), "path", None) or os.path.join(
            self.workspace, ".impor", "keybindings.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.isfile(path):
            sample = [
                {"key": "ctrl+alt+g", "command": "global_search"},
                {"key": "ctrl+alt+k", "command": "open_keybindings"},
            ]
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(sample, f, ensure_ascii=False, indent=2)
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("键位设置 · Impor")
        win.geometry("760x560")
        tk.Label(win, text="命令是 App 方法名；保存后立即重载。键位示例：ctrl+shift+p / f8 / alt+enter",
                 bg=THEME["panel_head"], fg=THEME["muted"], anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, ipady=7, padx=10)
        txt = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], insertbackground=THEME["fg"],
                      relief="flat", font=(self.mono, 10), padx=12, pady=10)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        try:
            txt.insert("1.0", open(path, "r", encoding="utf-8").read())
        except Exception:
            pass
        status = tk.Label(win, text="", bg=THEME["panel"], fg=THEME["muted"], anchor="w")
        status.pack(fill=tk.X, padx=12)

        def save():
            try:
                data = json.loads(txt.get("1.0", "end-1c"))
                if not isinstance(data, list):
                    raise ValueError("顶层必须是数组")
            except Exception as exc:
                status.configure(text="JSON 错误：" + str(exc), fg=THEME["red"])
                return
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if self.keymap is not None:
                errs = self.keymap.install()
                status.configure(text=("已保存；%d 条键位" % len(self.keymap.items)) +
                                 ("；" + errs[0] if errs else ""),
                                 fg=THEME["yellow"] if errs else THEME["green"])
            else:
                status.configure(text="已保存；重启后生效", fg=THEME["green"])
            self.toast("键位已重载", "ok")

        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 8))
        FlatButton(bar, "保存并重载", command=save, kind="primary", width=100).pack(side=tk.LEFT)
        FlatButton(bar, "打开配置文件", command=lambda: os.startfile(path), width=100
                   ).pack(side=tk.LEFT, padx=6)
        FlatButton(bar, "关闭", command=win.destroy, width=72).pack(side=tk.RIGHT)

    def _lint_pane(self, pane):
        """同步诊断入口：保存 / 显式检查时由 App 调用，具体状态在 pane 上。"""
        pane.lint_now()

    def check_project_quality(self):
        """v7.3：遍历工作区 .impor，聚合类型 / 穷尽性 / 访问控制 / lint。"""
        from .analysis import analyze
        from .typecheck import check_source
        files = []
        skip = {".git", ".impor_recovery", "build", "dist", "node_modules", "__pycache__", ".impor"}
        for root, dirs, names in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in names:
                if fn.endswith(".impor"):
                    files.append(os.path.join(root, fn))
        self.problems.delete("1.0", "end")
        self._problem_marks = []
        total_err = 0
        total_warn = 0
        shown = 0
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    src = strip_header(f.read())
            except OSError:
                continue
            try:
                ast = Parser(tokenize(src)).program()
                an = analyze(ast, src.split("\n"))
                issues = list(getattr(an, "issues", []) or [])
            except Exception as e:
                issues = []
                self.problems.insert("end", f"{os.path.basename(path)}: 解析失败 {e}\n", "err")
                self._problem_marks.append((path, 1, 1))
                total_err += 1
                continue
            for ti in check_source(src, None, path):
                issues.append(type("TI", (), {"line": ti.line, "col": ti.col, "severity": "error", "msg": ti.msg})())
            for issue in issues:
                total_err += 1 if issue.severity == "error" else 0
                total_warn += 0 if issue.severity == "error" else 1
                if shown < 300:
                    rel = rel_quiet(path, self.workspace)
                    self.problems.insert(
                        "end", f"{rel}:{issue.line}:{issue.col} [{issue.severity}] {issue.msg}\n",
                        "err" if issue.severity == "error" else "warn")
                    self._problem_marks.append((path, issue.line, issue.col))
                    shown += 1
        self._err_count = total_err
        self._warn_count = total_warn
        header = f"项目质量检查：{len(files)} 个文件，错误 {total_err}，警告 {total_warn}\n\n"
        self.problems.insert("1.0", header, "dim")
        self.show_bottom("problems")
        self._refresh_problems()
        try:
            self.status_left.configure(text=f"项目质量：{total_err} 错误 / {total_warn} 警告",
                                       fg=THEME["yellow"] if total_err else THEME["fg"])
        except Exception:
            pass

    def open_syntax_reference(self, initial=""):
        """v3.6.3：语法查询面板——关键字/内建函数的意思、格式、例子、例子说明。"""
        from . import syntax_ref
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("语法查询")
        win.transient(self.root)
        win.geometry("980x640")
        top = tk.Frame(win, bg=THEME["panel_head"], height=44)
        top.pack(fill=tk.X)
        tk.Label(top, text="语法查询", bg=THEME["panel_head"], fg=THEME["fg_bright"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side=tk.LEFT, padx=12)
        tk.Label(top, text="关键字 / 内建函数", bg=THEME["panel_head"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        q = tk.StringVar(value=initial)
        ent = tk.Entry(top, textvariable=q, bg=THEME["editor"], fg=THEME["fg"],
                       insertbackground=THEME["accent"], relief="flat",
                       font=("Microsoft YaHei UI", 10))
        ent.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=12, pady=8)
        body = tk.Frame(win, bg=THEME["panel"])
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg=THEME["panel_head"], width=250)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        lb = tk.Listbox(left, bg=THEME["panel_head"], fg=THEME["fg"], relief="flat",
                        selectbackground=THEME["accent_dim"], selectforeground=THEME["fg_bright"],
                        font=("Microsoft YaHei UI", 10), activestyle="none")
        sb = tk.Scrollbar(left, command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        right = tk.Frame(body, bg=THEME["editor"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        info = tk.Label(right, text="", bg=THEME["editor"], fg=THEME["muted"],
                        anchor="w", font=("Microsoft YaHei UI", 9))
        info.pack(fill=tk.X, padx=14, pady=(8, 0))
        text = tk.Text(right, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="word", font=("Microsoft YaHei UI", 10), padx=14, pady=10,
                       insertbackground=THEME["fg"])
        text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        text.tag_configure("h", foreground=THEME["accent"], font=("Microsoft YaHei UI", 11, "bold"))
        text.tag_configure("k", foreground=THEME["yellow"], font=("Microsoft YaHei UI", 10, "bold"))
        text.tag_configure("code", foreground=THEME["green"], font=("Consolas", 10),
                           background=THEME["panel_alt"], lmargin1=10, lmargin2=10)
        bar = tk.Frame(right, bg=THEME["editor"])
        bar.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Button(bar, text="复制例子", command=lambda: self._copy_syntax_example(lb, text),
                  bg=THEME["panel_alt"], fg=THEME["fg"], relief="flat",
                  activebackground=THEME["hover"], activeforeground=THEME["fg_bright"],
                  font=("Microsoft YaHei UI", 9), padx=12, pady=4).pack(side=tk.LEFT)
        tk.Label(bar, text="选中条目查看；输入框可搜名字、意思、例子", bg=THEME["editor"],
                 fg=THEME["muted_dim"], font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=10)

        current = {"entry": None}

        def show(_event=None):
            sel = lb.curselection()
            if not sel:
                return
            name = lb.get(sel[0]).split()[0]
            e = syntax_ref.get(name)
            if not e:
                return
            current["entry"] = e
            info.configure(text="%s · %s%s" % (
                e["name"], e["kind"], " · 预留" if e.get("status") == "reserved" else ""))
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", e["name"] + "\n", "h")
            for label, key in (("意思", "meaning"), ("格式", "format"),
                               ("例子", "example"), ("例子说明", "explain")):
                text.insert("end", "\n" + label + "\n", "k")
                if key == "example":
                    text.insert("end", e[key] + "\n", "code")
                else:
                    text.insert("end", e[key] + "\n")
            text.configure(state="disabled")

        def refresh(_event=None):
            qv = q.get().strip()
            hits = syntax_ref.search(qv)
            lb.delete(0, "end")
            for e in hits:
                mark = " [预留]" if e.get("status") == "reserved" else ""
                lb.insert("end", e["name"].ljust(12) + "  " + e["kind"] + mark)
            if hits:
                lb.selection_clear(0, "end")
                lb.selection_set(0)
                lb.activate(0)
                show()
            else:
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.insert("end", "没有找到匹配的语法条目。\n")
                text.configure(state="disabled")

        lb.bind("<<ListboxSelect>>", show)
        ent.bind("<KeyRelease>", refresh)
        ent.bind("<Return>", lambda e: (lb.selection_clear(0, "end"), lb.selection_set(0), show()))
        win.bind("<Escape>", lambda e: win.destroy())
        refresh()
        ent.focus_set()

    def _copy_syntax_example(self, lb, text):
        try:
            sel = lb.curselection()
            if not sel:
                return
            name = lb.get(sel[0]).split()[0]
            from .syntax_ref import get
            e = get(name)
            if e:
                self.root.clipboard_clear()
                self.root.clipboard_append(e["example"])
                self.toast("已复制 " + name + " 的例子")
        except Exception:
            pass

    def open_teaching(self):
        """v7.3：打开内置 12 课教学大纲。"""
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "docs", "教学大纲.md")
        if os.path.isfile(path):
            self.open_path(path)
        else:
            self.set_status("缺少 docs/教学大纲.md")

    def _problem_menu(self, event=None):
        try:
            self.problems.mark_set("insert", self.problems.index("@%d,%d" % (event.x, event.y)))
        except Exception:
            pass
        menu = tk.Menu(self.root, tearoff=0, bg=THEME["panel"], fg=THEME["fg"],
                       activebackground=THEME["hover"], activeforeground=THEME["fg_bright"])
        menu.add_command(label="解释这条诊断（为什么 / 怎么修）", command=self.explain_problem)
        menu.add_command(label="快速修复当前行", command=self.quick_fix_smart)
        menu.add_command(label="批量修复本文件", command=self.quick_fix_all)
        menu.add_command(label="跳到问题位置", command=self._problem_jump)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def explain_problem(self):
        """3.2.1：每条诊断给出「为什么 / 怎么修」，并直接接入 Quick Fix。"""
        from .diagnostics import explain
        try:
            idx = self.problems.index("insert")
            line_no = int(str(idx).split(".")[0])
            raw = self.problems.get(f"{line_no}.0", f"{line_no}.end").strip()
        except Exception:
            raw = ""
        if not raw or raw.startswith("没有发现"):
            self.toast("先在问题列表中选择一条诊断")
            return
        m = re.search(r"\[(E\d{4})\]\s+(.*)$", raw)
        code = m.group(1) if m else ""
        msg = (m.group(2) if m else re.sub(r"^.+?:", "", raw, count=1)).strip()
        from .diagnostics import code_info
        info = code_info(code) if code else None
        why, fix = explain(code or "", msg)
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("诊断解释" + ((" · " + code) if code else ""))
        win.transient(self.root)
        win.geometry("680x400")
        head = ("%s  %s" % (code, info["title"])) if info else msg
        tk.Label(win, text=head, bg=THEME["panel_head"], fg=THEME["red"], anchor="w",
                 wraplength=650, justify="left",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill=tk.X, padx=12, pady=(12, 6))
        body = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="word", font=("Microsoft YaHei UI", 10), padx=10, pady=10)
        body.pack(fill=tk.BOTH, expand=True, padx=12)
        body.insert("end", "原文\n", "h")
        body.insert("end", msg + "\n\n")
        body.insert("end", "为什么\n", "h")
        body.insert("end", why + "\n\n")
        body.insert("end", "怎么修\n", "h")
        body.insert("end", fix + "\n")
        if info and info.get("example"):
            body.insert("end", "\n最小示例\n", "h")
            body.insert("end", info["example"] + "\n", "code")
        body.tag_configure("h", foreground=THEME["accent"],
                           font=("Microsoft YaHei UI", 10, "bold"))
        body.tag_configure("code", foreground=THEME["green"], font=(self.mono, 9))
        body.configure(state="disabled")
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=12, pady=8)
        FlatButton(bar, "快速修复当前行", kind="primary", width=150,
                   command=lambda: (win.destroy(), self.quick_fix_smart())).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "关闭", command=win.destroy, width=80).pack(side=tk.RIGHT, padx=4)

    def _problem_jump(self, e=None):
        try:
            idx = self.problems.index("insert")
            ln = int(str(idx).split(".")[0])
            mark = self._problem_marks[ln - 1]
            if len(mark) >= 3:
                path, line, col = mark[0], mark[1], mark[2]
                pane = self.open_path(path)
            else:
                line, col = mark[0], mark[1]
                pane = self.current_pane()
            if pane:
                pane.text.mark_set("insert", f"{line}.{max(0, col - 1)}")
                pane.text.see("insert")
                pane.focus_text()
        except Exception:
            pass
        return "break"

    # ---------- 浮层提示 ----------
    def toast(self, text, kind="info"):
        """窗口右下角短暂浮出提示（无边框 Toplevel），1.7s 后自动消失。"""
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None
        color = {"info": THEME["fg_bright"], "ok": THEME["green"],
                 "err": THEME["red"], "warn": THEME["yellow"]}.get(kind, THEME["fg_bright"])
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        frame = tk.Frame(win, bg=THEME["toast"], highlightbackground=THEME["border"],
                         highlightthickness=1)
        frame.pack()
        tk.Label(frame, text=text, bg=THEME["toast"], fg=color,
                 font=("Microsoft YaHei UI", 9)).pack(padx=20, pady=10)
        self.root.update_idletasks()
        tw, th = win.winfo_reqwidth(), win.winfo_reqheight()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        win.geometry("%dx%d+%d+%d" % (tw, th, rx + rw - tw - 26, ry + rh - th - 52))
        self._toast_win = win
        self.root.after(1700, lambda: self._hide_toast(win))

    def _hide_toast(self, win):
        try:
            win.destroy()
        except Exception:
            pass
        if self._toast_win is win:
            self._toast_win = None

    def clear_output(self):
        self.out.delete("1.0", tk.END)
        self.problems.delete("1.0", tk.END)
        self.watch_tree.delete(*self.watch_tree.get_children())
        win = getattr(self, "_out_win", None)
        if win is not None and win.winfo_exists():
            self._out_win_text.delete("1.0", tk.END)
        self._err_count = 0
        self._warn_count = 0
        self._refresh_problems()

    # ---------- 内存面板 ----------
    def _render_memory(self):
        self.mem_text.delete("1.0", tk.END)
        try:
            from .memory import process_memory_report
            self.mem_text.insert(tk.END, process_memory_report() + "\n\n")
        except Exception:
            pass
        if self._interp is not None:
            st = self._interp.memory.stats()
            self.mem_text.insert(tk.END, "本次运行内存统计\n", "info")
            self.mem_text.insert(tk.END, "  分配对象数   %d\n" % st["alloc"])
            self.mem_text.insert(tk.END, "  峰值         %d\n" % st["peak"])
            self.mem_text.insert(tk.END, "  当前存活     %d\n" % st["live"])
            self.mem_text.insert(tk.END, "  回收次数     %d\n" % st["collections"])
            self.mem_text.insert(tk.END, "  自动阈值     %d（%s）\n" % (st["threshold"], "开" if st["auto"] else "关"))
            kinds = st["live_by_type"]
            if kinds:
                self.mem_text.insert(tk.END, "  分类         " + "、".join(
                    f"{k} {v}" for k, v in sorted(kinds.items())) + "\n")
        else:
            self.mem_text.insert(tk.END, "\n运行脚本后这里显示本次分配 / 回收 / 存活统计。", "dim")
        self.mem_text.tag_config("dim", foreground=THEME["muted"])

    # ---------- 运行 ----------
    def _set_running(self, flag):
        self._running = flag
        self.btn_run.set_text("● 运行中" if flag else "▶ 运行")
        self.btn_run.set_active(not flag)
        if flag:
            self.status_left.configure(text="运行中 …", fg=THEME["yellow"])
            self.badge.configure(bg=THEME["accent_dim"], fg=THEME["yellow"])
        else:
            self.status_left.configure(text=f"Impor {__version__}", fg=THEME["fg"])
            self.badge.configure(bg=THEME["ok_badge"], fg=THEME["green"])
            for pane in self.panes:
                try:
                    pane._exec_line = None
                    pane._debug_inline = {}
                    pane.text.tag_remove("exec", "1.0", "end")
                    pane.redraw_lineno()
                except Exception:
                    pass




























    def add_watch(self):
        expr = (self.watch_entry.get() or "").strip()
        if not expr:
            return
        if expr not in self.watch_exprs:
            self.watch_exprs.append(expr)
            self._save_config()
        self.watch_entry.delete(0, tk.END)
        self._refresh_watch_view()
        self.toast("已添加监视：" + expr, "ok")

    def remove_watch(self):
        item = self.watch_tree.focus()
        if not item or not item.startswith("w:"):
            self.toast("先选中一条手工监视表达式")
            return
        expr = item[2:]
        if expr in self.watch_exprs:
            self.watch_exprs.remove(expr)
            self._save_config()
        self._refresh_watch_view()

    def _watch_value(self, expr):
        interp = getattr(self, "_dbg_interp", None)
        if interp is None:
            return "等待调试"
        old = getattr(interp, "cur_env", None)
        frames = getattr(self, "_dbg_frames", None)
        try:
            if frames:
                interp.cur_env = frames[0].env
            val = interp.eval_expr_text(expr)
            s = repr(val)
            return s[:117] + "..." if len(s) > 120 else s
        except Exception as exc:
            return "求值失败: %s" % exc
        finally:
            try:
                interp.cur_env = old
            except Exception:
                pass

    def _refresh_watch_view(self):
        g, w = getattr(self, "_watch_g", {}), getattr(self, "_watch_w", {})
        self._watch_update(g, w)

    def _watch_update(self, g, w):
        self._watch_g, self._watch_w = g, w
        self.watch_tree.delete(*self.watch_tree.get_children())
        for expr in self.watch_exprs:
            self.watch_tree.insert("", tk.END, iid="w:" + expr, text="⌕ " + expr,
                                   values=("watch", self._watch_value(expr)))
        names = sorted(set(w) | set(g.keys()))
        for name in names:
            if name.startswith("__") or name not in g:
                continue
            val = g[name]
            if callable(val):
                continue
            try:
                vtype = type(val).__name__
                sval = repr(val)
            except Exception:
                vtype, sval = "?", "?"
            if len(sval) > 120:
                sval = sval[:117] + "..."
            self.watch_tree.insert("", tk.END, iid=name, text=name, values=(vtype, sval))

    # ---------- 调试 ----------


















    # ---------- 功能命令 ----------
    def toggle_debug(self):
        self._debug_mode = not self._debug_mode
        self.btn_debug.set_text("调试: 开" if self._debug_mode else "调试: 关")
        if self._debug_mode:
            self._set_out("调试模式：run 在当前窗口同步执行，支持 breakpoint()/单步面板\n", "warn")

    def toggle_sandbox(self):
        self._sandbox_mode = not self._sandbox_mode
        self.btn_sandbox.set_text("沙盒: 开" if self._sandbox_mode else "沙盒: 关")
        if self._sandbox_mode:
            self._set_out("沙盒模式：已禁用文件高危 API\n", "warn")

    def toggle_freeze(self):
        self._freeze = not self._freeze
        self._set_out("变量监视器已冻结\n" if self._freeze else "变量监视器已恢复\n", "dim")

    def build_exe(self):
        if not self._ensure_trusted("构建"):
            return
        pane = self.current_pane()
        if pane is None:
            return
        if not pane.path:
            self.save_file_as()
            if not pane.path:
                return
        self.save_file()
        self.clear_output()
        self.show_bottom("out")
        self._set_out(f"⚡ 构建 {os.path.basename(pane.path)} …\n", "dim")

        def work():
            try:
                r = subprocess.run([sys.executable, os.path.join(INSTALL_ROOT, "Impor.py"), "build", pane.path],
                                   cwd=self.workspace, capture_output=True, text=True, timeout=600)
                text = (r.stdout or "") + (r.stderr or "")
                self._queue.put(("out", text.strip()))
                self._queue.put(("out", "✓ 构建完成（原生 exe，已嵌入 I++ 图标，运行不依赖 Python）" if r.returncode == 0 else "❌ 构建失败"))
            except Exception as e:
                self._queue.put(("out", f"❌ 构建异常：{e}"))
            finally:
                self._queue.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    def compile_to_python(self):
        pane = self.current_pane()
        if pane is None:
            return
        try:
            body = strip_header(pane.text.get("1.0", "end-1c"), warn=True)
            ast = Parser(tokenize(body)).program()
            from .transpiler import transpile
            src_lines = pane.text.get("1.0", "end-1c").split("\n")
            py = transpile(ast, source_name=pane.path or "<impor>", src_lines=src_lines)
        except Exception as e:
            messagebox.showerror("编译失败", str(e))
            return
        out = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python 文件", "*.py")])
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(py)
            self._set_out(f"✓ 已生成 {out}\n", "ok")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def goto_line(self):
        pane = self.current_pane()
        if pane is None:
            return
        from tkinter import simpledialog
        line = simpledialog.askinteger("跳行", "行号：", parent=self.root)
        if line:
            if getattr(pane, "_virtual_readonly", False) and hasattr(pane, "goto_virtual_line"):
                pane.goto_virtual_line(line)
            else:
                pane.text.mark_set("insert", f"{line}.0")
                pane.text.see(f"{line}.0")
                pane.focus_text()

    def toggle_find(self):
        if self._find_visible:
            self._find_bar.pack_forget()
            self._find_visible = False
            return
        if not hasattr(self, "_find_bar"):
            self._find_bar = tk.Frame(self.editor_area, bg=THEME["panel_head"], height=38)
            self._find_bar.pack_propagate(False)
            self._find_entry = tk.Entry(self._find_bar, bg="#17202e", fg=THEME["fg"],
                                        insertbackground=THEME["accent"], relief="flat",
                                        highlightthickness=1, highlightbackground=THEME["border"],
                                        font=(self.mono, 10), width=44)
            self._find_entry.pack(side=tk.LEFT, padx=(14, 8), pady=7, ipady=2)
            FlatButton(self._find_bar, "下一个", command=self._find_next, width=62, height=24).pack(
                side=tk.LEFT, padx=4, pady=6)
            FlatButton(self._find_bar, "跨块替换", command=self._find_replace, width=74, height=24).pack(
                side=tk.LEFT, padx=2, pady=6)
            FlatButton(self._find_bar, "关闭", command=self.toggle_find, width=52, height=24).pack(
                side=tk.LEFT, padx=(2, 12), pady=6)
            tk.Frame(self._find_bar, bg=THEME["border_soft"], height=1).pack(side=tk.BOTTOM, fill=tk.X)
            self._find_entry.bind("<Return>", lambda e: self._find_next())
            self._find_entry.bind("<Escape>", lambda e: self.toggle_find())
            self._find_entry.bind("<KeyRelease>", lambda e: self._find_live())
        self._find_bar.pack(fill=tk.X, before=self.tabstrip)
        self._find_visible = True
        self._find_entry.focus_set()

    def _find_live(self):
        pane = self.current_pane()
        if pane:
            pane.find_all(self._find_entry.get())

    def _find_next(self):
        pane = self.current_pane()
        if pane:
            pane.find_next(self._find_entry.get())

    def _find_replace(self):
        """v3.7.6：分块编辑下对完整文件做一次字面量替换。"""
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        needle = self._find_entry.get()
        if not needle:
            self.toast("先在查找框输入要替换的内容", "warn")
            return
        if not hasattr(pane, "replace_all"):
            self.toast("当前文件不是分块编辑；整项目替换请用 Ctrl+Shift+F", "warn")
            return
        replacement = simpledialog.askstring(
            "跨块替换", "把“%s”全部替换为（区分大小写关闭，作用于整个文件）：" % needle,
            parent=self.root)
        if replacement is None:
            return
        try:
            count = pane.replace_all(needle, replacement, nocase=True)
        except Exception as exc:
            messagebox.showerror("替换失败", str(exc), parent=self.root)
            return
        if count:
            self.toast("已在完整文件中替换 %d 处（含当前块外）" % count, "ok")
        else:
            self.toast("完整文件中没有找到“%s”" % needle, "dim")

    def _walk_files(self, exts):
        skip = {".git", "node_modules", "__pycache__", "build", "dist", ".venv", "venv"}
        root = os.path.abspath(getattr(self, "workspace", "") or ROOT_DIR)
        if not os.path.isdir(root):
            root = ROOT_DIR
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in files:
                if any(fn.endswith(x) for x in exts):
                    yield os.path.join(base, fn)

    def _insert_text(self, text):
        pane = self.current_pane()
        if not pane:
            return
        pane.text.insert("insert", text)
        pane.text.focus_set()

    def insert_snippet(self):
        snips = [
            ("函数", 'def name(args):\n    # TODO\n    return 0\n'),
            ("类", 'class Name:\n    def init():\n        # TODO\n'),
            ("条件", 'if cond:\n    # TODO\nelse:\n    # TODO\n'),
            ("循环", 'for i in 1..10:\n    print(i)\n'),
            ("异常", 'try:\n    # TODO\ncatch e:\n    print(e)\n'),
            ("测试", 'expect(actual, expected, "case")\n'),
            ("接口", 'interface Name {\n    def method() {\n        return 0\n    }\n}\n'),
            ("记录", 'record Name {\n    var x = 0\n}\n'),
            ("命名空间", 'namespace Name {\n    var x = 0\n}\n'),
            ("匿名函数", 'var f = lambda (x): x * 2\n'),
        ]
        Finder(self, "插入代码片段", snips, lambda code: self._insert_text(code),
               "双击片段插入到光标处")

    def append_to_selected_lines(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        text = simpledialog.askstring("多行行尾插入", "要在选中各行末尾追加的内容：", parent=self.root)
        if text is None:
            return
        try:
            a = int(pane.text.index("sel.first").split(".")[0])
            b = int(pane.text.index("sel.last").split(".")[0])
        except Exception:
            self.toast("先选中多行")
            return
        for ln in range(a, b + 1):
            pane.text.insert(f"{ln}.end", text)
        pane.text.tag_add("sel", f"{a}.0", f"{b}.end")
        pane.highlight()
        self.mark_dirty(pane)

    # ---------- 3.2.1：通用重构预览（先 diff 再写入） ----------
    @staticmethod
    def _text_offset(text, index):
        try:
            line, col = [int(x) for x in str(index).split(".")[:2]]
        except Exception:
            return 0
        lines = text.split("\n")
        line = max(1, min(line, len(lines) or 1))
        return sum(len(x) + 1 for x in lines[:line - 1]) + max(0, col)

    def _preview_text_change(self, title, before, after, apply_fn, note=""):
        """显示统一 diff；用户点「应用」后才真正修改。"""
        if before == after:
            self.toast("没有产生任何修改")
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title(title)
        win.transient(self.root)
        win.geometry("900x620")
        tk.Label(win, text=note or "预览：确认无误后再写入编辑器",
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        frame = tk.Frame(win, bg=THEME["panel"])
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        txt = tk.Text(frame, wrap="none", bg=THEME["editor"], fg=THEME["fg"],
                      insertbackground=THEME["fg"], font=(self.mono, 10),
                      relief="flat", padx=8, pady=8)
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        diff = "".join(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile="修改前", tofile="修改后", n=3))
        if not diff:
            diff = "（仅行尾/空白差异）\n"
        txt.insert("1.0", diff[:200000])
        txt.tag_configure("add", foreground=THEME["green"])
        txt.tag_configure("del", foreground=THEME["red"])
        for i, line in enumerate(diff.splitlines(), 1):
            if line.startswith("+"):
                txt.tag_add("add", f"{i}.0", f"{i}.end")
            elif line.startswith("-"):
                txt.tag_add("del", f"{i}.0", f"{i}.end")
        txt.configure(state="disabled")
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=8)
        FlatButton(bar, "应用修改", kind="primary", width=120,
                   command=lambda: (win.destroy(), apply_fn(after))).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", command=win.destroy, width=72).pack(side=tk.RIGHT, padx=4)

    def _replace_pane_text(self, pane, text, label):
        try:
            pane.text.delete("1.0", "end-1c")
            pane.text.insert("1.0", text)
            pane.text.edit_reset()
            pane.text.edit_modified(False)
            pane.highlight()
            self.mark_dirty(pane)
            pane._dirty = True
            self.toast("已应用：" + label, "ok")
        except Exception as exc:
            messagebox.showerror("应用失败", str(exc))

    def rename_symbol(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        word, _ = pane._word_before()
        if not word:
            self.toast("把光标放到要重命名的符号上")
            return
        new = simpledialog.askstring("重命名符号", f"将「{word}」重命名为：", parent=self.root)
        if not new or new.strip() == word:
            return
        new = new.strip()
        try:
            line = int(pane.text.index("insert").split(".")[0])
        except Exception:
            line = None
        self.index.refresh()
        changes = self.index.rename_edits_scoped(word, new, pane.path, line)
        if not changes:
            self.toast("符号索引里没有找到同作用域引用：" + word)
            return
        self._show_rename_preview(word, new, changes)

    def _show_rename_preview(self, word, new, changes):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("重命名预览 · %s → %s" % (word, new))
        win.transient(self.root)
        win.geometry("760x460")
        total = sum(len(v) for v in changes.values())
        tk.Label(win, text="将修改 %d 个文件、%d 处" % (len(changes), total),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        tree = ttk.Treeview(win, columns=("file", "line", "code"), show="headings")
        for col, text, width in (("file", "文件", 300), ("line", "行", 52), ("code", "代码", 360)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col in ("file", "code")))
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        for fp, edits in changes.items():
            try:
                lines = open(fp, "r", encoding="utf-8", errors="replace").read().split("\n")
            except Exception:
                lines = []
            for e in edits:
                code = lines[e["line"] - 1] if 0 <= e["line"] - 1 < len(lines) else ""
                tree.insert("", tk.END, values=(rel_quiet(fp, self.workspace), e["line"], code.strip()[:120]))
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=8)
        FlatButton(bar, "应用重命名", kind="primary", width=120,
                   command=lambda: (win.destroy(), self._apply_rename(word, new, changes))
                   ).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", command=win.destroy, width=72).pack(side=tk.RIGHT, padx=4)

    def _apply_rename(self, word, new, changes):
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])")
        snapshot = {}
        for fp in changes:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    snapshot[os.path.abspath(fp)] = f.read()
            except Exception:
                pass
        self._refactor_undo = {"label": "重命名 %s → %s" % (word, new), "files": snapshot}
        total = 0
        changed = set()
        for fp, edits in changes.items():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    lines = f.read().split("\n")
            except Exception:
                continue
            for e in sorted(edits, key=lambda x: (x["line"], x["col"]), reverse=True):
                ln, col = e["line"] - 1, e["col"]
                if 0 <= ln < len(lines) and lines[ln][col:col + len(word)] == word:
                    lines[ln] = lines[ln][:col] + new + lines[ln][col + len(word):]
                    total += 1
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write("\n".join(lines))
            changed.add(os.path.abspath(fp))
        for p in self.panes:
            if p.path and os.path.abspath(p.path) in changed:
                p.text.delete("1.0", "end")
                p.text.insert("1.0", pat.sub(new, p.text.get("1.0", "end-1c")))
                p.highlight()
        self._fill_tree()
        self.toast(f"已按符号索引重命名 {word} → {new}，共 {total} 处")

    def move_project_file(self):
        """3.2.1：移动/重命名文件 + 统一引用预览 + 一键撤销。"""
        from tkinter import filedialog
        pane = self.current_pane()
        src = getattr(pane, "path", None) if pane else None
        if not src or not os.path.isfile(src):
            self.toast("先在编辑器中打开要移动的文件")
            return
        base = os.path.abspath(getattr(self, "workspace", ".") or ".")
        dst = filedialog.asksaveasfilename(
            parent=self.root, title="移动或重命名文件",
            initialdir=os.path.dirname(src), initialfile=os.path.basename(src),
            defaultextension=".impor")
        if not dst:
            return
        dst = os.path.abspath(dst)
        try:
            if os.path.commonpath([base, dst]) != base:
                messagebox.showerror("拒绝移动", "目标必须在当前工作区内：\n" + dst)
                return
        except ValueError:
            messagebox.showerror("拒绝移动", "目标路径无效")
            return
        if os.path.exists(dst):
            messagebox.showerror("拒绝移动", "目标已存在：\n" + dst)
            return
        if os.path.abspath(src) == dst:
            return
        old_stem = os.path.splitext(os.path.basename(src))[0]
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        changes = {}
        skip = {".git", ".impor_recovery", "build", "dist", "node_modules", "__pycache__"}
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in names:
                if not fn.endswith(".impor"):
                    continue
                fp = os.path.abspath(os.path.join(root, fn))
                if fp == os.path.abspath(src):
                    continue
                try:
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
                new_text = re.sub(
                    r"(?m)^(\s*(?:from|import)\s+[\w./\\]*?)" + re.escape(old_stem) + r"\b",
                    lambda m: m.group(1) + new_stem, text)
                if new_text != text:
                    changes[fp] = (text, new_text)
        if changes:
            before = ""
            after = ""
            for fp, (old, new) in changes.items():
                rel = rel_quiet(fp, base)
                before += "=== %s（修改前） ===\n%s\n" % (rel, old)
                after += "=== %s（修改后） ===\n%s\n" % (rel, new)
            note = "将移动 %s → %s，并更新 %d 个文件的 import 引用" % (
                os.path.basename(src), os.path.basename(dst), len(changes))
        else:
            before = "移动：%s → %s\n" % (src, dst)
            after = before + "\n没有找到需要更新的 import 引用。\n"
            note = "将移动 %s → %s（无引用变更）" % (os.path.basename(src), os.path.basename(dst))

        def apply(new_text):
            backups = {}
            try:
                for fp, (old, new) in changes.items():
                    backups[fp] = old
                    with open(fp, "w", encoding="utf-8", newline="") as f:
                        f.write(new)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            except Exception as exc:
                for fp, old in backups.items():
                    try:
                        with open(fp, "w", encoding="utf-8", newline="") as f:
                            f.write(old)
                    except Exception:
                        pass
                messagebox.showerror("移动失败", str(exc))
                return
            for fp, old in changes.items():
                for p in self.panes:
                    if p.path and os.path.abspath(p.path) == fp:
                        p.text.delete("1.0", "end")
                        p.text.insert("1.0", changes[fp][1])
                        p.highlight()
            self._refactor_undo = {"label": "移动文件 " + os.path.basename(dst),
                                   "files": backups,
                                   "moves": [(dst, src)]}
            try:
                self.index.refresh(force=True)
            except Exception:
                pass
            self._fill_tree()
            self.toast("已移动并更新 %d 个引用" % len(changes), "ok")

        self._preview_text_change("移动文件预览 · " + os.path.basename(dst),
                                  before, after, apply, note)

    def undo_refactor(self):
        undo = getattr(self, "_refactor_undo", None)
        if not undo:
            self.toast("没有可撤销的项目级重构")
            return
        if not messagebox.askyesno("撤销重构", "撤销「%s」？\n将还原 %d 个文件。" %
                                   (undo["label"], len(undo["files"]))):
            return
        restored = 0
        for fp, data in undo["files"].items():
            try:
                with open(fp, "w", encoding="utf-8", newline="") as f:
                    f.write(data)
                restored += 1
            except Exception:
                continue
        for cur, old in reversed(undo.get("moves") or []):
            try:
                if os.path.exists(cur) and not os.path.exists(old):
                    os.rename(cur, old)
            except Exception:
                continue
        for pane in self.panes:
            if pane.path and os.path.abspath(pane.path) in undo["files"]:
                pane.text.delete("1.0", "end")
                pane.text.insert("1.0", undo["files"][os.path.abspath(pane.path)])
                pane.highlight()
        self._refactor_undo = None
        try:
            self.index.refresh(force=True)
        except Exception:
            pass
        self._fill_tree()
        self.toast("已撤销重构：还原 %d 个文件" % restored, "ok")

    def show_dependency_graph(self):
        from .editor_graph_ui import DependencyAuditWindow
        DependencyAuditWindow(self, initial_tab="files")

    def show_dependency_audit(self):
        from .editor_graph_ui import DependencyAuditWindow
        DependencyAuditWindow(self, initial_tab="audit")

    def _coverage_exec_lines(self, src):
        """3.2.2：从 AST 提取可执行语句行，作为行覆盖率分母。"""
        from . import ast_nodes as A
        stmt_names = {
            "Assign", "CompoundAssign", "ExprStmt", "Print", "If", "While", "For",
            "Def", "Return", "Break", "Continue", "ExitScript", "ClsOut",
            "ImportNode", "FromImportNode", "ExportNode", "TryCatch", "ClassDef",
            "ExceptionDef", "Raise", "AssertNode", "MatchNode", "Block",
        }
        lines = set()
        try:
            ast = Parser(tokenize(strip_header(src))).program()
            stack = [ast]
            while stack:
                node = stack.pop()
                if node is None:
                    continue
                if type(node).__name__ in stmt_names:
                    ln = getattr(node, "lineno", 0)
                    if ln > 0:
                        lines.add(ln)
                if isinstance(node, (list, tuple)):
                    stack.extend(node)
                elif hasattr(node, "__dict__") and not isinstance(node, (str, bytes, int, float, bool)):
                    for value in node.__dict__.values():
                        if isinstance(value, (list, tuple, A.AST)):
                            stack.append(value)
        except Exception:
            pass
        return lines

    def show_coverage(self):
        """3.2.2：运行时行覆盖率（解释器逐语句命中，不是静态调用近似）。"""
        pane = self.current_pane()
        cov = getattr(self, "_last_coverage", None)
        target = getattr(self, "_coverage_target", None)
        src = pane.text.get("1.0", "end-1c") if pane is not None else ""
        offset = int(getattr(self, "_coverage_offset", 0) or 0)
        lines = {ln + offset for ln in self._coverage_exec_lines(src)}
        if cov is not None and target and pane is not None and os.path.abspath(target) == os.path.abspath(pane.path or ""):
            lines.update(ln + offset for ln in cov.keys())
        total = len(lines)
        hit = sum(1 for ln in lines if cov and cov.get(ln - offset, 0) > 0) if cov else 0
        pct = (hit * 100.0 / total) if total else 0.0

        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("运行时行覆盖率 · Impor")
        win.transient(self.root)
        win.geometry("900x600")
        head = tk.Frame(win, bg=THEME["panel_head"])
        head.pack(fill=tk.X)
        title = ("行覆盖率 %.1f%%（%d / %d 可执行行）· 解释器逐语句命中" %
                 (pct, hit, total)) if cov else "尚无运行时数据：运行一次带覆盖率的执行"
        tk.Label(head, text=title, bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT, padx=10, pady=7)
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(8, 0))
        FlatButton(bar, "运行并统计覆盖率", kind="primary", width=140,
                   command=lambda: (win.destroy(), self.run_code(coverage=True))).pack(side=tk.LEFT)
        if cov:
            FlatButton(bar, "跳下一个未覆盖", width=124,
                       command=lambda: (win.destroy(), self.next_uncovered())).pack(side=tk.LEFT, padx=6)
            FlatButton(bar, "清空数据", width=88,
                       command=lambda: (setattr(self, "_last_coverage", None),
                                        win.destroy(), self.show_coverage())).pack(side=tk.LEFT, padx=6)
        FlatButton(bar, "关闭", width=72, command=win.destroy).pack(side=tk.RIGHT)

        tree = ttk.Treeview(win, columns=("line", "hits", "state", "code"), show="headings")
        for col, text, width in (("line", "行", 60), ("hits", "命中", 70),
                                 ("state", "状态", 80), ("code", "源码", 600)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col == "code"))
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        tree.tag_configure("hit", foreground=THEME["green"])
        tree.tag_configure("miss", foreground=THEME["red"])
        source_lines = src.split("\n")
        for ln in sorted(lines):
            count = (cov or {}).get(ln - offset, 0)
            code = source_lines[ln - 1].strip() if 0 < ln <= len(source_lines) else ""
            tree.insert("", tk.END, values=(ln, count, "已执行" if count else "未执行", code),
                        tags=("hit",) if count else ("miss",))
        def jump(_event=None):
            item = tree.focus()
            if item and pane is not None:
                self.open_path_at(pane.path, int(tree.item(item, "values")[0]))
        tree.bind("<Double-Button-1>", jump)
        if cov:
            tk.Label(win, text="统计范围：当前文件；表达式不单独计行，只记录语句执行点。",
                     bg=THEME["panel"], fg=THEME["muted"], anchor="w").pack(fill=tk.X, padx=12, pady=(0, 6))

    def next_uncovered(self):
        """3.2.3：跳到当前文件下一个未覆盖的可执行行。"""
        pane = self.current_pane()
        if pane is None:
            return
        cov = getattr(self, "_last_coverage", None)
        target = getattr(self, "_coverage_target", None)
        if not cov or not target or not pane.path or os.path.abspath(target) != os.path.abspath(pane.path):
            self.toast("当前文件还没有覆盖率数据，请先运行并统计覆盖率", "warn")
            return
        lines = sorted(ln for ln in getattr(self, "_coverage_exec", set()) if not cov.get(ln, 0))
        if not lines:
            self.toast("当前文件没有未覆盖的可执行行", "ok")
            return
        try:
            cur = int(pane.text.index("insert").split(".")[0])
        except Exception:
            cur = 1
        offset = int(getattr(self, "_coverage_offset", 0) or 0)
        cur_body = cur - offset
        nxt = next((ln for ln in lines if ln > cur_body), lines[0])
        nxt_display = nxt + offset
        pane.text.mark_set("insert", "%d.0" % nxt_display)
        pane.text.see("insert")
        pane.focus_text()
        self.toast("跳到未覆盖行 %d" % nxt_display, "info")

    def show_plugin_manager(self):
        """3.2.3：本地插件管理器（安装 / 启停 / 权限 / 版本 / 卸载）。"""
        try:
            self.plugins.load()
        except Exception as exc:
            messagebox.showerror("插件管理器", str(exc))
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("插件管理器 · Impor")
        win.transient(self.root)
        win.geometry("980x620")
        tk.Label(win, text="本地插件：文件夹 / zip 安装，启停即时生效；隔离进程仍受权限清单约束。",
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, padx=10, pady=7)

        body = tk.Frame(win, bg=THEME["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        tree = ttk.Treeview(body, columns=("state", "name", "version", "perms", "path"),
                            show="headings", height=12)
        for col, text, width in (("state", "状态", 70), ("name", "名称", 160),
                                 ("version", "版本", 80), ("perms", "权限", 170),
                                 ("path", "路径", 480)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col == "path"))
        tree.pack(fill=tk.BOTH, expand=True)
        tree.tag_configure("off", foreground=THEME["muted"])
        detail = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                         font=(self.mono, 9), padx=8, pady=6, height=7, wrap="word")
        detail.pack(fill=tk.X, padx=10, pady=(0, 6))
        detail.configure(state="disabled")
        rows = {}

        def refresh():
            tree.delete(*tree.get_children())
            rows.clear()
            for i, p in enumerate(self.plugins.discover()):
                try:
                    meta = self.plugins.meta(p)
                except Exception as exc:
                    meta = {"name": os.path.basename(p), "version": "?", "path": p,
                            "enabled": False, "permissions": {}, "description": str(exc)}
                iid = "p%d" % i
                rows[iid] = meta
                perms = meta.get("permissions") or {}
                declared = "、".join(perms.get("declared") or []) or "默认（只读工作区）"
                tree.insert("", tk.END, iid=iid,
                            values=("启用" if meta.get("enabled", True) else "禁用",
                                    meta.get("name", ""), meta.get("version", ""),
                                    declared, meta.get("path", "")),
                            tags=() if meta.get("enabled", True) else ("off",))
            if self.plugins.errors:
                self.toast("有 %d 个插件加载失败，详见插件管理器" % len(self.plugins.errors), "warn")

        def selected():
            sel = tree.selection()
            return rows.get(sel[0]) if sel else None

        def on_select(e=None):
            meta = selected()
            if not meta:
                return
            perms = meta.get("permissions") or {}
            text = ("%s  %s\n%s\n\n权限：读=%s 写=%s 网络=%s 子进程=%s\n声明：%s\n路径：%s" % (
                meta.get("name"), meta.get("version"), meta.get("description") or "（无描述）",
                "允许" if perms.get("read") else "拒绝",
                "允许" if perms.get("write") else "拒绝",
                "允许" if perms.get("net") else "拒绝",
                "允许" if perms.get("process") else "拒绝",
                "、".join(perms.get("declared") or []) or "未声明（默认只读工作区）",
                meta.get("path")))
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", text)
            detail.configure(state="disabled")

        def install_folder():
            p = filedialog.askdirectory(title="选择插件文件夹", parent=win)
            if not p:
                return
            try:
                got = self.plugins.install_folder(p)
                self.plugins.reload()
                self.toast("已安装 %d 个插件文件" % len(got), "ok")
            except Exception as exc:
                messagebox.showerror("安装失败", str(exc), parent=win)
                return
            refresh()

        def install_zip():
            p = filedialog.askopenfilename(title="选择插件 zip", parent=win,
                                           filetypes=[("ZIP", "*.zip"), ("所有文件", "*.*")])
            if not p:
                return
            try:
                got = self.plugins.install_zip(p)
                self.plugins.reload()
                self.toast("已安装 %d 个插件文件" % len(got), "ok")
            except Exception as exc:
                messagebox.showerror("安装失败", str(exc), parent=win)
                return
            refresh()

        def toggle():
            meta = selected()
            if not meta:
                return
            try:
                self.plugins.set_enabled(meta["path"], not meta.get("enabled", True))
                self.plugins.reload()
            except Exception as exc:
                messagebox.showerror("切换失败", str(exc), parent=win)
                return
            refresh()
            self.toast("已%s：%s" % ("启用" if not meta.get("enabled", True) else "禁用",
                                    meta.get("name")), "ok")

        def uninstall():
            meta = selected()
            if not meta:
                return
            if not messagebox.askyesno("卸载插件", "确定卸载 %s？\n%s" % (meta.get("name"), meta["path"]),
                                       parent=win):
                return
            try:
                self.plugins.uninstall(meta["path"])
                self.plugins.reload()
            except Exception as exc:
                messagebox.showerror("卸载失败", str(exc), parent=win)
                return
            refresh()
            on_select()
            self.toast("已卸载：" + meta.get("name", ""), "ok")

        def reload_all():
            self.plugins.reload()
            refresh()
            on_select()
            self.toast("插件已重新加载", "ok")

        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 8))
        FlatButton(bar, "安装文件夹", kind="primary", width=110, command=install_folder).pack(side=tk.LEFT)
        FlatButton(bar, "安装 ZIP", kind="primary", width=96, command=install_zip).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "启用 / 禁用", width=110, command=toggle).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "重新加载", width=96, command=reload_all).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "卸载", width=84, command=uninstall).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "关闭", width=72, command=win.destroy).pack(side=tk.RIGHT)

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-Button-1>", lambda e: toggle())
        refresh()
        if tree.get_children():
            tree.selection_set(tree.get_children()[0])
            on_select()

    def show_security_center(self):
        """3.2.1 安全中心：策略、威胁模型、残余风险与审计日志。"""
        try:
            from .security import SECURITY_VERSION, THREAT_MODEL, AuditLog, THREAT_MODEL as TM
        except Exception as exc:
            messagebox.showerror("安全中心", str(exc))
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("安全中心 · Impor %s" % SECURITY_VERSION)
        win.transient(self.root)
        win.geometry("900x660")
        tk.Label(win, text="安全版本 %s · 默认拒绝 · 应用层边界（不是 OS 沙箱）" % SECURITY_VERSION,
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill=tk.X, ipady=7, padx=10)
        body = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="word", font=("Microsoft YaHei UI", 9), padx=12, pady=10)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        body.tag_configure("h", foreground=THEME["accent"],
                           font=("Microsoft YaHei UI", 9, "bold"), spacing1=8)
        body.tag_configure("risk", foreground=THEME["orange"])

        def sec(title):
            body.insert("end", title + "\n", "h")

        sec("已启用的防护")
        for item in THREAT_MODEL.get("mitigations", []):
            body.insert("end", "  · " + item + "\n")
        body.insert("end", "  进程加固：" + ("；".join(self._harden_notes) or "无需调整") + "\n")
        sec("信任边界")
        for item in THREAT_MODEL.get("trust_boundaries", []):
            body.insert("end", "  · " + item + "\n")
        sec("残余风险（必须知晓）")
        for item in THREAT_MODEL.get("residual_risks", []):
            body.insert("end", "  ! " + item + "\n", "risk")
        sec("最近审计（.impor/audit.log）")
        try:
            rows = AuditLog(self.workspace).tail(20)
            for r in rows:
                body.insert("end", "  %s  %s\n" % (r.get("ts"), r.get("event") + "  " +
                                                   str({k: v for k, v in r.items()
                                                        if k not in ("ts", "event")})))
            if not rows:
                body.insert("end", "  （暂无记录）\n")
        except Exception:
            body.insert("end", "  （读取失败）\n")
        body.configure(state="disabled")
        FlatButton(win, "关闭", command=win.destroy, width=90).pack(pady=6)

    def show_unused_symbols(self):
        try:
            self.index.refresh()
        except Exception:
            pass
        candidates = []
        for sym in self.index.symbols():
            if sym.name.startswith("__"):
                continue
            refs = self.index.references(sym.name)
            defs = self.index.definition(sym.name)
            if len(refs) <= max(1, len(defs)):
                candidates.append((sym, len(refs)))
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("可能未使用的符号")
        win.transient(self.root)
        win.geometry("700x480")
        tv = ttk.Treeview(win, columns=("kind", "path", "line", "refs"),
                          show="tree headings")
        tv.heading("#0", text="符号")
        tv.heading("kind", text="类型")
        tv.heading("path", text="文件")
        tv.heading("line", text="行")
        tv.heading("refs", text="引用")
        tv.column("#0", width=170)
        tv.column("kind", width=70, stretch=False)
        tv.column("path", width=300)
        tv.column("line", width=55, stretch=False)
        tv.column("refs", width=55, stretch=False)
        tv.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 4))
        seen = set()
        for sym, n in candidates[:500]:
            key = (sym.name, sym.path, sym.line)
            if key in seen:
                continue
            seen.add(key)
            tv.insert("", tk.END, text=sym.name,
                      values=(sym.kind, rel_quiet(sym.path, ROOT_DIR).replace("\\", "/"),
                              sym.line, n))
        tk.Label(win, text="判定规则：引用次数不超过声明次数（启发式，可能包含导出符号）",
                 bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12, pady=(0, 8))

    def project_replace(self):
        from .editor_search_ui import SearchReplaceDialog
        pane = self.current_pane()
        query = ""
        try:
            if pane:
                query = pane.text.get("sel.first", "sel.last").split("\n")[0][:120]
        except Exception:
            query = ""
        SearchReplaceDialog(self, mode="replace", query=query)

    def run_tests(self):
        self._run_test_target(None)

    def run_tests_current(self):
        pane = self.current_pane()
        if not pane or not pane.path:
            self.toast("当前文件还没保存，无法定位测试")
            return
        self._run_test_target(pane.path)

    def _run_test_target(self, target=None, label=None):
        self.show_bottom("tests")
        if label:
            desc = label
        elif isinstance(target, (list, tuple)):
            desc = " %d 个文件" % len(target)
        else:
            desc = rel_quiet(target, ROOT_DIR) if target else ""
        self.append_out("$ python -m impor test %s\n" % desc)
        self._set_running(True)
        self.test_tree.delete(*self.test_tree.get_children())
        self._test_suites = {}
        self._test_counts = {"pass": 0, "fail": 0}
        self._test_asserts = 0
        self._test_failed_files = []
        try:
            self.test_summary.configure(text="运行中…")
        except Exception:
            pass
        if target is None:
            files = self._test_files(None)
        elif isinstance(target, (list, tuple)):
            files = [os.path.abspath(p) for p in target if os.path.isfile(p)]
        elif os.path.isfile(target):
            files = [os.path.abspath(target)]
        else:
            files = self._test_files(target)
        self._test_last_files = list(files)
        if getattr(self, "_test_watch", False):
            self._test_watch_sig = self._test_signature(files)
        if not files:
            self._set_running(False)
            self.test_summary.configure(text="未找到测试文件")
            self.append_out("未找到测试文件（tests/ 或 examples/ 下需有文件名含 test 的 .impor）", "warn")
            return

        def worker():
            rc_all = 0
            try:
                for fp in files:
                    cmd = [sys.executable, "-X", "utf8", "-m", "impor", "test", fp]
                    r = subprocess.run(cmd, cwd=ROOT_DIR,
                                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       timeout=300)
                    for line in (r.stdout or r.stderr or "").splitlines():
                        self._queue.put(("out", line))
                        self._queue.put(("test", line))
                    rc_all = rc_all or r.returncode
                self._queue.put(("out", "\n退出码 %d\n" % rc_all))
                self._queue.put(("testdone", rc_all))
            except Exception as exc:
                self._queue.put(("out", "测试运行失败：%s" % exc))
            finally:
                self._queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def regex_replace(self):
        from .editor_search_ui import SearchReplaceDialog
        pane = self.current_pane()
        query = ""
        try:
            if pane:
                query = pane.text.get("sel.first", "sel.last").split("\n")[0][:120]
        except Exception:
            query = ""
        SearchReplaceDialog(self, mode="regex", query=query)

    def goto_definition(self):
        pane = self.current_pane()
        if not pane:
            return
        try:
            word = pane.text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            word, _ = pane._word_before()
        if not word:
            self.toast("把光标放到符号上，或先选中符号名")
            return
        try:
            self.index.refresh()
            defs = self.index.definition(word, prefer_path=pane.path)
        except Exception as exc:
            self.toast("符号索引查询失败：%s" % exc)
            return
        if not defs:
            self.toast("没有找到定义：" + word)
            return
        target = min(defs, key=lambda s: (0 if s.path == os.path.abspath(pane.path or "") else 1, s.line))
        self.open_path_at(target.path, target.line)
        self.toast("跳转到 %s:%d" % (rel_quiet(target.path, self.workspace), target.line), "ok")

    def _hover_symbol(self):
        pane = self.current_pane()
        if not pane:
            return
        word, _ = pane._word_before()
        if not word:
            self.toast("把光标放到符号上")
            return
        try:
            self.index.refresh()
            sym = self.index.symbol_at(word, pane.path, int(pane.text.index("insert").split(".")[0]))
        except Exception as exc:
            self.toast("符号索引查询失败：%s" % exc)
            return
        if not sym:
            if word in BUILTIN_DOCS:
                text = "内建 `%s`\n%s" % (word, BUILTIN_DOCS[word][:180])
            else:
                self.toast("没有找到符号：" + word)
                return
        else:
            text = "%s `%s`" % (sym.kind, sym.name)
            if getattr(sym, "typ", "") and sym.typ != "?":
                text += "  : %s" % sym.typ
            text += "\n作用域 %s" % (sym.scope or "<module>")
            text += "\n%s:%d" % (rel_quiet(sym.path, self.workspace), sym.line)
            if getattr(sym, "text", ""):
                text += "\n" + sym.text[:180]
        try:
            idx = pane.text.index("insert")
            line, col = (int(x) for x in idx.split("."))
            info = infer_expression(pane.text.get("1.0", "end-1c"), line, col,
                                    index=self.index, path=pane.path)
            if info:
                if info.get("type") and info["type"] != "?":
                    text += "\n推断类型 : %s" % info["type"]
                if info.get("members"):
                    text += "\n成员 : " + "、".join(info["members"][:12])
        except Exception:
            pass
        pop = tk.Toplevel(self.root, bg=THEME["panel_head"])
        pop.overrideredirect(True)
        tk.Label(pop, text=text, justify="left", bg=THEME["panel_head"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 9), padx=10, pady=7).pack()
        try:
            x, y, _, h = pane.text.bbox("insert")
            pop.geometry("+%d+%d" % (pane.text.winfo_rootx() + x + 20, pane.text.winfo_rooty() + y + h + 4))
        except Exception:
            pop.geometry("+%d+%d" % (self.root.winfo_pointerx() + 12, self.root.winfo_pointery() + 12))
        pop.after(3500, pop.destroy)

    def signature_help(self):
        pane = self.current_pane()
        if not pane:
            return
        line_no, col = map(int, pane.text.index("insert").split("."))
        before = pane.text.get("%d.0" % line_no, "insert")
        depth, start = 0, -1
        for i in range(len(before) - 1, -1, -1):
            if before[i] == ")":
                depth += 1
            elif before[i] == "(":
                if depth:
                    depth -= 1
                else:
                    start = i
                    break
        if start < 0:
            self.toast("光标不在函数调用参数里")
            return
        m = re.search(r"([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)\s*$", before[:start])
        if not m:
            self.toast("没有识别到函数名")
            return
        name = m.group(1)
        try:
            self.index.refresh()
            defs = self.index.definition(name, prefer_path=pane.path)
        except Exception as exc:
            self.toast("符号索引查询失败：%s" % exc)
            return
        sig = ""
        for d in defs:
            sm = re.search(r"(?:func|def)\s+" + re.escape(name) + r"\s*\(([^)]*)\)", d.text or "")
            if sm:
                sig = name + "(" + sm.group(1) + ")"
                break
        if not sig:
            self.toast("没有找到函数签名：" + name)
            return
        params = [p.strip() for p in sig[sig.find("(") + 1:sig.rfind(")")].split(",") if p.strip()]
        active = min(before[start + 1:].count(","), max(0, len(params) - 1))
        win = tk.Toplevel(self.root, bg=THEME["panel_head"])
        win.overrideredirect(True)
        tk.Label(win, text=sig, bg=THEME["panel_head"], fg=THEME["fg"],
                 font=(self.mono, 11, "bold"), padx=12, pady=8).pack(anchor="w")
        if params:
            tk.Label(win, text="当前参数：%s" % params[active], bg=THEME["panel_head"],
                     fg=THEME["accent"], font=("Microsoft YaHei UI", 9), padx=12).pack(anchor="w")
        try:
            x, y, _, h = pane.text.bbox("insert")
            win.geometry("+%d+%d" % (pane.text.winfo_rootx() + x + 12, pane.text.winfo_rooty() + y + h + 4))
        except Exception:
            win.geometry("+%d+%d" % (self.root.winfo_pointerx() + 12, self.root.winfo_pointery() + 12))
        win.after(3500, win.destroy)

    def find_references(self):
        self._symbol_refs_dialog(False)

    def call_hierarchy(self):
        self._symbol_refs_dialog(True)

    def _symbol_refs_dialog(self, calls):
        pane = self.current_pane()
        if not pane:
            return
        try:
            word = pane.text.get("sel.first", "sel.last").strip()
        except tk.TclError:
            word, _ = pane._word_before()
        if not word:
            self.toast("把光标放到符号上，或先选中符号名")
            return
        try:
            self.index.refresh()
            defs = self.index.definition(word, prefer_path=pane.path)
            refs = self.index.references(word)
        except Exception as exc:
            self.toast("符号索引查询失败：%s" % exc)
            return
        if not defs and not refs:
            self.toast("没有找到符号：" + word)
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title(("调用层级 · " if calls else "查找引用 · ") + word)
        win.transient(self.root)
        win.geometry("820x420")
        tk.Label(win, text=(("调用层级 · " if calls else "查找引用 · ") + word +
                            "    定义 %d · 引用 %d" % (len(defs), len(refs))),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        tree = ttk.Treeview(win, columns=("kind", "file", "line", "code"),
                            show="tree headings")
        tree.heading("#0", text="#")
        tree.heading("kind", text="类型")
        tree.heading("file", text="文件")
        tree.heading("line", text="行")
        tree.heading("code", text="代码")
        tree.column("#0", width=40, stretch=False)
        tree.column("kind", width=64, stretch=False)
        tree.column("file", width=230)
        tree.column("line", width=52, stretch=False)
        tree.column("code", width=400)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        seen = set()
        rows = []

        def add(kind, path, line, code):
            key = (os.path.abspath(path), line)
            if key in seen:
                return
            seen.add(key)
            rows.append((os.path.abspath(path), line))
            tree.insert("", tk.END, text=str(len(rows)), values=(
                kind, rel_quiet(path, ROOT_DIR), line, code[:120]))

        for d in defs:
            try:
                lines = open(d.path, "r", encoding="utf-8").read().split("\n")
                code = lines[d.line - 1] if 0 <= d.line - 1 < len(lines) else ""
            except Exception:
                code = ""
            add("定义", d.path, d.line, code)
        for path, line, _col in refs:
            try:
                lines = open(path, "r", encoding="utf-8").read().split("\n")
                code = lines[line - 1] if 0 <= line - 1 < len(lines) else ""
            except Exception:
                code = ""
            add("调用" if calls else "引用", path, line, code)

        def jump(e=None):
            item = tree.focus()
            if not item:
                return
            idx = int(tree.item(item, "text")) - 1
            if 0 <= idx < len(rows):
                path, line = rows[idx]
                self.open_path_at(path, line)
                win.destroy()
        tree.bind("<Double-Button-1>", jump)
        tree.bind("<Return>", jump)

    def quick_fix(self):
        from .fmt import fmt_source
        pane = self.current_pane()
        if not pane:
            return
        raw = pane.text.get("1.0", "end-1c")
        clean = "\n".join(line.rstrip() for line in raw.split("\n"))
        try:
            fixed = fmt_source(clean, getattr(self, "indent_width", 4), 100)
        except Exception:
            fixed = clean
        pane.text.delete("1.0", "end")
        pane.text.insert("1.0", fixed)
        pane.highlight()
        self._lint_pane(pane)
        self.toast("已整理：去行尾空格 + 格式化 + 重新检查")

    def quick_fix_all(self):
        """按真实 lint/类型诊断逐条修复，支持一次处理整文件。"""
        pane = self.current_pane()
        if not pane:
            return
        raw = pane.text.get("1.0", "end-1c")
        body = strip_header(raw)
        offset = len(raw.split("\n")) - len(body.split("\n"))
        try:
            ast = Parser(tokenize(body)).program()
            res = analyze(ast, body.split("\n"))
            issues = list(getattr(res, "issues", []) or [])
            try:
                issues += list(style_lint(body, pane.path) or [])
            except Exception:
                pass
        except Exception as exc:
            self.toast("先修语法错误：" + str(exc))
            return
        lines = raw.split("\n")
        fixes = 0
        try:
            pane.text.edit_separator()
        except Exception:
            pass
        try:
            for tip in check_source(raw, self.index, pane.path):
                if tip.fix:
                    ln, new_line = tip.fix
                    if 0 <= ln < len(lines):
                        lines[ln] = new_line
                        fixes += 1
        except Exception:
            pass
        for iss in sorted(issues, key=lambda i: (i.line, i.col), reverse=True):
            ln = iss.line - 1 + offset
            if ln < 0 or ln >= len(lines):
                continue
            code = iss.code
            if code == "unused-import":
                del lines[ln]
                fixes += 1
                continue
            if code == "unreachable":
                del lines[ln]
                fixes += 1
                continue
            if code == "unused-variable":
                m = re.match(r"\s*(?:var|const|let)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", lines[ln])
                if not m:
                    continue
                name = m.group(1)
                if len(re.findall(r"(?<![\w\u4e00-\u9fff])" + re.escape(name) + r"(?![\w\u4e00-\u9fff])", raw)) == 1:
                    del lines[ln]
                    fixes += 1
                continue
            if code == "undefined-name":
                tail = lines[ln][max(0, iss.col):]
                stem = re.match(r"([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", tail)
                if not stem:
                    continue
                name = stem.group(1)
                defs = self.index.definition(name)
                if defs:
                    rel = rel_quiet(defs[0].path, self.workspace)
                    mod = os.path.splitext(os.path.basename(rel))[0]
                    header = 1 if lines and lines[0].strip().startswith("important<<") else 0
                    lines.insert(header, f"from {mod} import {name}")
                    fixes += 1
                elif re.match(r"\s*" + re.escape(name) + r"\s*=[^=]", lines[ln]):
                    lines[ln] = re.sub(r"^(\s*)" + re.escape(name), r"\1var " + name, lines[ln], count=1)
                    fixes += 1
                else:
                    lines.insert(ln, "var " + name + " = 0")
                    fixes += 1
        # 3.2.2：文件末尾未闭合的大括号，一次补齐
        body_text = "\n".join(lines)
        opens = body_text.count("{") - body_text.count("}")
        if opens > 0:
            lines.extend(["}" for _ in range(min(opens, 20))])
            fixes += opens
        if not fixes:
            self.toast("没有可自动修复的诊断")
            return
        auto = True
        try:
            auto = bool(pane.text.cget("autoseparators"))
            pane.text.configure(autoseparators=False)
            pane.text.edit_separator()
        except Exception:
            pass
        pane.text.delete("1.0", "end")
        pane.text.insert("1.0", "\n".join(lines))
        try:
            pane.text.edit_separator()
            pane.text.configure(autoseparators=auto)
        except Exception:
            pass
        pane.highlight()
        self.mark_dirty(pane)
        self._lint_pane(pane)
        self.toast(f"已批量修复 {fixes} 条诊断（Ctrl+Z 可整体撤销）", "ok")

    def quick_fix_smart(self):
        """3.2.2：单行 Quick Fix 扩面，并在 Tk undo 栈里形成一个可撤销编辑单元。"""
        pane = self.current_pane()
        if not pane:
            return
        t = pane.text
        idx = t.index("insert")
        line_no = idx.split(".")[0]
        line = t.get(f"{line_no}.0", f"{line_no}.end")
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            self.toast("当前行没有可修复的问题")
            return
        indent = line[:len(line) - len(line.lstrip())]
        auto = True
        try:
            auto = bool(t.cget("autoseparators"))
            t.configure(autoseparators=False)
            t.edit_separator()
        except Exception:
            pass
        applied = ""
        # 1) 常见关键字拼写错误
        typos = {"fuc": "func", "funciton": "func", "retrun": "return", "reutrn": "return",
                 "whlie": "while", "whil": "while", "els": "else", "improt": "import",
                 "calss": "class", "ture": "true", "fasle": "false", "nul": "null"}
        m_typo = re.match(r"\s*(%s)\b" % "|".join(typos), stripped, re.I)
        if m_typo:
            word = m_typo.group(1)
            t.delete(f"{line_no}.0", f"{line_no}.end")
            t.insert(f"{line_no}.0", indent + re.sub(r"\b%s\b" % re.escape(word),
                                                     typos[word.lower()], stripped, count=1))
            applied = "已修正关键字 " + word
        # 2) 未声明的赋值补 var
        elif re.match(r"[A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*\s*=[^=]", stripped) and \
                not re.match(r"(var|set|const|let|import|from)\b", stripped):
            t.insert(f"{line_no}.0", indent + "var ")
            applied = "已补 var 声明"
        # 3) if / while 条件里误用单个 =
        elif re.match(r"(if|while|elif)\b", stripped) and "==" not in stripped and \
                re.search(r"[^=!<>]=(?!=)", stripped):
            t.delete(f"{line_no}.0", f"{line_no}.end")
            t.insert(f"{line_no}.0", indent + re.sub(r"[^=!<>]=(?!=)", lambda mm: mm.group(0)[0] + "==",
                                                     stripped, count=1))
            applied = "条件里的 = 已改为 =="
        # 4) 括号 / 方括号 / 大括号不配对
        elif stripped.count("(") > stripped.count(")") and stripped.count("[") == stripped.count("]"):
            t.insert(f"{line_no}.end", ")")
            applied = "已补右括号"
        elif stripped.count("[") > stripped.count("]"):
            t.insert(f"{line_no}.end", "]")
            applied = "已补右方括号"
        elif stripped.count("{") > stripped.count("}"):
            t.insert(f"{line_no}.end", " }")
            applied = "已补右大括号"
        # 5) 字符串引号未闭合（单双引号分别计数）
        elif stripped.count('"') % 2 == 1 and stripped.count("'") % 2 == 0:
            t.insert(f"{line_no}.end", '"')
            applied = "已补双引号"
        elif stripped.count("'") % 2 == 1 and stripped.count('"') % 2 == 0:
            t.insert(f"{line_no}.end", "'")
            applied = "已补单引号"
        # 6) 只出现一次的 var 声明，删除整行
        elif re.match(r"(var|const|let)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", stripped):
            name = re.match(r"(?:var|const|let)\s+([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", stripped).group(1)
            if len(re.findall(r"(?<![\w\u4e00-\u9fff])" + re.escape(name) + r"(?![\w\u4e00-\u9fff])",
                              t.get("1.0", "end-1c"))) == 1:
                t.delete(f"{line_no}.0", f"{line_no}.end+1c")
                applied = "已删除未使用变量 " + name
            else:
                self.toast("这一行没有可自动修复的问题")
        else:
            self.toast("这一行没有可自动修复的问题")
        try:
            t.edit_separator()
            t.configure(autoseparators=auto)
        except Exception:
            pass
        if applied:
            pane.highlight()
            self.mark_dirty(pane)
            self.toast(applied + "（Ctrl+Z 可撤销）")

    def explain_selection(self):
        pane = self.current_pane()
        if not pane:
            return
        try:
            code = pane.text.get("sel.first", "sel.last")
        except Exception:
            code = pane.text.get("1.0", "end-1c")
        from .analysis import analyze, style_lint
        from .interpreter import strip_header
        lines = []
        try:
            ast = Parser(tokenize(strip_header(code))).program()
            res = analyze(ast, strip_header(code).split("\n"))
            issues = list(getattr(res, "issues", res) or [])
            lines.append("语法检查：通过" if not issues else "发现 %d 个问题：" % len(issues))
            for it in issues[:20]:
                lines.append("  第%d行 [%s] %s" % (it.line, it.severity, it.msg))
        except Exception as exc:
            lines.append("语法检查未通过：%s" % exc)
        try:
            for it in style_lint(strip_header(code), os.path.basename(pane.path or "untitled"))[:20]:
                lines.append("  风格 第%d行 %s" % (it.line, it.msg))
        except Exception:
            pass
        win = tk.Toplevel(self.root)
        win.title("解释选中代码 · 本地静态分析")
        win.geometry("720x460")
        win.configure(bg=THEME["bg"])
        txt = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                      font=(self.mono, 10), padx=12, pady=10, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    # ---------- v3.7.6：可选 AI 内联助手（流式 / 取消） ----------
    def _ai_config(self):
        self.ai_cfg = ai_assist.normalize_config(getattr(self, "ai_cfg", {}) or {})
        return self.ai_cfg

    def _ai_selection(self, pane):
        try:
            code = pane.text.get("sel.first", "sel.last")
            start = pane.text.index("sel.first")
            end = pane.text.index("sel.last")
        except Exception:
            self.toast("先选中要交给 AI 的代码", "warn")
            return None
        if not code.strip():
            self.toast("选区为空", "warn")
            return None
        return code, start, end

    def _ai_preflight(self):
        cfg = self._ai_config()
        ok, why = ai_assist.readiness(cfg)
        if ok:
            return cfg
        if not cfg.get("enabled"):
            if messagebox.askyesno(
                    "AI 助手默认关闭",
                    "AI 内联助手默认关闭。开启后，只有你主动触发并确认时，"
                    "才会把当前选中代码发送到你配置的 API。\n\n现在打开设置？",
                    parent=self.root):
                self.show_ai_settings(initial_enable=True)
            return None
        if not cfg.get("privacy_ack"):
            text = ai_assist.privacy_text(cfg, cfg.get("max_chars"))
            if not messagebox.askyesno("AI 隐私确认", text + "\n\n确认并允许本次发送？",
                                       parent=self.root):
                return None
            cfg["privacy_ack"] = True
            self.ai_cfg = cfg
            self._save_config()
            return cfg
        messagebox.showwarning("AI 助手未就绪", why, parent=self.root)
        if "key" in why or "endpoint" in why:
            self.show_ai_settings()
        return None

    def ai_explain_selection(self):
        self._ai_request("explain")

    def ai_fix_selection(self):
        self._ai_request("fix")

    def ai_generate_tests(self):
        self._ai_request("tests")

    def _ai_request(self, action):
        pane = self.current_pane()
        if not pane:
            return
        picked = self._ai_selection(pane)
        if picked is None:
            return
        code, start, end = picked
        vstart = getattr(pane, "_virtual_start", None)
        cfg = self._ai_preflight()
        if cfg is None:
            return
        streaming = bool(cfg.get("stream", True))
        cancel = self._ai_stream_open(action) if streaming else None
        try:
            self.set_status("AI 请求中 · 只发送当前选区" + (" · 流式" if streaming else ""))
        except Exception:
            pass
        self.toast("AI 请求中（只发送选区）…", "dim")

        def work():
            try:
                if streaming:
                    result = ai_assist.request_suggestion_stream(
                        cfg, action, code, filename=pane.path or "", language="impor",
                        on_delta=lambda text: self.root.after(
                            0, lambda t=text: self._ai_stream_append(t)),
                        cancel_event=cancel)
                else:
                    result = ai_assist.request_suggestion(
                        cfg, action, code, filename=pane.path or "", language="impor")
            except ai_assist.AICancelled:
                try:
                    self.root.after(0, lambda: self._ai_stream_cancel_done(True))
                except Exception:
                    pass
                return
            except Exception as exc:
                msg = str(exc)[:240]
                try:
                    self.root.after(0, lambda: self._ai_stream_cancel_done(False, msg))
                except Exception:
                    pass
                return
            if cancel is not None and cancel.is_set():
                try:
                    self.root.after(0, lambda: self._ai_stream_cancel_done(True))
                except Exception:
                    pass
                return
            try:
                self.root.after(0, lambda: self._ai_stream_finish(
                    pane, action, start, end, result, expected=code, vstart=vstart))
            except Exception:
                pass

        threading.Thread(target=work, name="impor-ai-request", daemon=True).start()

    def _ai_stream_open(self, action):
        """v3.7.6：流式请求的进度窗，带取消按钮；返回 threading.Event。"""
        cancel = threading.Event()
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title(ai_assist.ACTION_TITLES.get(action, "AI 请求") + " · 流式")
        win.transient(self.root)
        win.geometry("720x460")
        tk.Label(win, text="正在接收模型输出；可随时取消。fix / tests 仍会在完整结果后先给 diff 预览。",
                 bg=THEME["panel"], fg=THEME["muted"], anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, padx=14, pady=(12, 4))
        body = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="word", font=("Cascadia Code", 9), padx=10, pady=8)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        status = tk.Label(win, text="等待首个片段…", bg=THEME["panel"], fg=THEME["muted"],
                          anchor="w", font=("Microsoft YaHei UI", 8))
        status.pack(fill=tk.X, padx=14)
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=14, pady=(4, 12))
        state = {"win": win, "body": body, "status": status, "cancel": cancel,
                 "closed": False, "cancelled": False, "chars": 0}

        def close():
            cancel.set()
            state["cancelled"] = True
            state["closed"] = True
            try:
                win.destroy()
            except Exception:
                pass
            if getattr(self, "_ai_stream_state", None) is state:
                self._ai_stream_state = None

        def do_cancel():
            cancel.set()
            state["cancelled"] = True
            try:
                btn.configure(state="disabled", text="取消中…")
                status.configure(text="已请求取消；等待服务端下一段或超时", fg=THEME["orange"])
            except Exception:
                pass
            win.after(250, close)

        btn = FlatButton(bar, "取消", command=do_cancel, width=72)
        btn.pack(side=tk.RIGHT, padx=4)
        tk.Label(bar, text="流式内容先显示在这里；确认前不会改动文件。", bg=THEME["panel"],
                 fg=THEME["muted"], font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        win.protocol("WM_DELETE_WINDOW", close)
        self._ai_stream_state = state
        return cancel

    def _ai_stream_append(self, text):
        state = getattr(self, "_ai_stream_state", None)
        if not state or state.get("closed") or not text:
            return
        try:
            body = state["body"]
            body.configure(state="normal")
            body.insert("end", text)
            body.see("end")
            body.configure(state="disabled")
            state["chars"] += len(text)
            state["status"].configure(text="接收中 · %d 字符" % state["chars"])
        except Exception:
            note_swallowed("ai stream append")

    def _ai_stream_close(self):
        state = getattr(self, "_ai_stream_state", None)
        if not state:
            return
        state["closed"] = True
        self._ai_stream_state = None
        try:
            state["win"].destroy()
        except Exception:
            note_swallowed("ai stream close")

    def _ai_stream_cancel_done(self, cancelled, message=""):
        state = getattr(self, "_ai_stream_state", None)
        was_closed = bool(state and state.get("closed"))
        self._ai_stream_close()
        if cancelled:
            if not was_closed:
                self.toast("AI 请求已取消；未改动文件", "dim")
        else:
            self.toast("AI 失败：" + (message or "未知错误"), "warn")

    def _ai_stream_finish(self, pane, action, start, end, result, expected=None, vstart=None):
        state = getattr(self, "_ai_stream_state", None)
        if not state or state.get("closed") or state.get("cancelled"):
            return
        self._ai_stream_close()
        self._ai_show_result(pane, action, start, end, result, expected=expected, vstart=vstart)

    def _ai_show_result(self, pane, action, start, end, result, expected=None, vstart=None):
        try:
            if not pane.winfo_exists():
                return
        except Exception:
            return
        explanation = (result.get("explanation") or "").strip()
        if action == "explain":
            self._show_ai_text("AI 解释选中代码", explanation or result.get("raw") or "（模型没有返回解释）")
            return
        if action == "fix":
            new_code = ai_assist.clean_code_block(result.get("replacement"))
            if not new_code.strip():
                self.toast("模型没有返回可替换的代码", "warn")
                return
            if vstart is not None and getattr(pane, "_virtual_start", None) != vstart:
                self.toast("大文件窗口已切换，未应用修改；请回到原选区重新触发", "warn")
                return
            try:
                current = pane.text.get(start, end)
            except Exception:
                self.toast("选区已失效，请重新选中后再试", "warn")
                return
            if expected is not None and current != expected:
                self.toast("选区在请求期间已变化，未应用修改；请重新选中后再试", "warn")
                return
            if new_code == current:
                self.toast("模型认为选区无需修改", "ok")
                return

            def apply(new_text):
                try:
                    pane.text.delete(start, end)
                    pane.text.insert(start, new_text)
                    pane.text.edit_separator()
                    pane.highlight()
                    self.mark_dirty(pane)
                    pane._dirty = True
                    self.toast("已应用 AI 修复（Ctrl+Z 可撤销）", "ok")
                except Exception as exc:
                    messagebox.showerror("应用失败", str(exc))

            self._preview_text_change("AI 修复预览", pane.text.get(start, end), new_code, apply,
                                      (explanation or "确认后替换当前选区") +
                                      "\n只替换请求时的选区，不动选区外内容")
            return
        if action == "tests":
            tests = ai_assist.clean_code_block(result.get("tests"))
            if not tests.strip():
                self.toast("模型没有返回测试代码", "warn")
                return

            def open_tests(text):
                name = "test_" + os.path.splitext(os.path.basename(pane.path or "selection"))[0] + ".impor"
                new = self.open_new_tab(content=text, title=name)
                if new is not None:
                    new._dirty = True
                    self.mark_dirty(new)
                    self.toast("已生成测试标签，保存后即可运行", "ok")

            self._preview_text_change("AI 生成测试预览", "", tests, open_tests,
                                      (explanation or "测试代码将写入新标签") +
                                      "\n不会自动覆盖任何现有文件")

    def _show_ai_text(self, title, text):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title(title)
        win.transient(self.root)
        win.geometry("760x520")
        body = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="word", font=("Microsoft YaHei UI", 10), padx=12, pady=10)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        body.insert("1.0", text)
        body.configure(state="disabled")
        FlatButton(win, "关闭", command=win.destroy, width=80).pack(pady=(0, 10))

    def show_ai_settings(self, initial_enable=False):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("AI 助手设置 · 可选 / 默认关闭")
        win.transient(self.root)
        win.geometry("560x590")
        cfg = self._ai_config()
        enabled = tk.BooleanVar(value=bool(cfg.get("enabled")) or bool(initial_enable))
        ack = tk.BooleanVar(value=bool(cfg.get("privacy_ack")))
        stream = tk.BooleanVar(value=bool(cfg.get("stream", True)))
        endpoint = tk.StringVar(value=cfg.get("endpoint"))
        model = tk.StringVar(value=cfg.get("model"))
        key = tk.StringVar(value=cfg.get("api_key"))
        max_chars = tk.IntVar(value=int(cfg.get("max_chars", ai_assist.DEFAULT_MAX_CHARS)))
        max_tokens = tk.IntVar(value=int(cfg.get("max_tokens", 1200)))
        tk.Label(win, text="可选 AI 内联助手", bg=THEME["panel"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(fill=tk.X, padx=18, pady=(16, 4))
        tk.Label(win, text="默认关闭。开启后仅在手动执行「AI 解释 / 修复 / 生成测试」时，"
                           "把当前选区发送到你配置的 API。",
                 bg=THEME["panel"], fg=THEME["muted"], wraplength=510, justify="left",
                 anchor="w", font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, padx=18)
        tk.Checkbutton(win, text="启用 AI 内联助手（需要下面两项都确认）", variable=enabled,
                       bg=THEME["panel"], fg=THEME["fg"], selectcolor=THEME["editor"],
                       activebackground=THEME["panel"], activeforeground=THEME["fg"]
                       ).pack(anchor="w", padx=18, pady=(10, 2))
        tk.Checkbutton(win, text="隐私确认：我了解选中代码会发送到配置的 API，且其中可能含敏感信息",
                       variable=ack, bg=THEME["panel"], fg=THEME["fg"], selectcolor=THEME["editor"],
                       activebackground=THEME["panel"], activeforeground=THEME["fg"]
                       ).pack(anchor="w", padx=18, pady=(0, 8))
        tk.Checkbutton(win, text="流式输出（SSE；服务端不支持时可关掉改为整段返回）",
                       variable=stream, bg=THEME["panel"], fg=THEME["fg"],
                       selectcolor=THEME["editor"], activebackground=THEME["panel"],
                       activeforeground=THEME["fg"]).pack(anchor="w", padx=18, pady=(0, 8))
        for label, var in (("Endpoint", endpoint), ("Model", model), ("API key", key)):
            row = tk.Frame(win, bg=THEME["panel"])
            row.pack(fill=tk.X, padx=18, pady=4)
            tk.Label(row, text=label, width=10, anchor="w", bg=THEME["panel"],
                     fg=THEME["fg"]).pack(side=tk.LEFT)
            entry = tk.Entry(row, textvariable=var, width=42, bg=THEME["editor"], fg=THEME["fg"],
                             insertbackground=THEME["accent"], relief="flat",
                             show="*" if label == "API key" else "")
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=4)
        tk.Label(row, text="选区上限", width=10, anchor="w", bg=THEME["panel"],
                 fg=THEME["fg"]).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=200, to=60000, increment=1000, textvariable=max_chars,
                    width=10).pack(side=tk.LEFT)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=4)
        tk.Label(row, text="输出上限", width=10, anchor="w", bg=THEME["panel"],
                 fg=THEME["fg"]).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=64, to=8192, increment=100, textvariable=max_tokens,
                    width=10).pack(side=tk.LEFT)
        tk.Label(win, text="API key 也可用环境变量 IMPOR_AI_API_KEY；优先读取环境变量。\n"
                           "key 会明文保存在 ~/.impor_studio.json，请自行评估风险；不建议在共享电脑填写。",
                 bg=THEME["panel"], fg=THEME["muted"], wraplength=510, justify="left",
                 anchor="w", font=("Microsoft YaHei UI", 8)).pack(fill=tk.X, padx=18, pady=(8, 2))

        def save():
            if enabled.get() and not ack.get():
                messagebox.showwarning("隐私确认", "开启 AI 前必须勾选隐私确认。", parent=win)
                return
            if enabled.get() and not (str(key.get()).strip() or os.environ.get("IMPOR_AI_API_KEY")):
                messagebox.showwarning("缺少 API key", "请填写 API key，或设置环境变量 IMPOR_AI_API_KEY。",
                                       parent=win)
                return
            self.ai_cfg = ai_assist.normalize_config({
                "enabled": bool(enabled.get()),
                "privacy_ack": bool(ack.get()),
                "endpoint": endpoint.get().strip(),
                "model": model.get().strip(),
                "api_key": key.get().strip(),
                "max_chars": int(max_chars.get() or ai_assist.DEFAULT_MAX_CHARS),
                "max_tokens": int(max_tokens.get() or 1200),
                "stream": bool(stream.get()),
            })
            self._save_config()
            win.destroy()
            self.toast("AI 设置已保存" + ("（已开启）" if self.ai_cfg.get("enabled") else "（仍关闭）"), "ok")

        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=18, pady=14)
        FlatButton(bar, "保存", kind="primary", command=save, width=88).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", command=win.destroy, width=72).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "清除 key", command=lambda: key.set(""), width=88).pack(side=tk.RIGHT, padx=4)

    def convert_current_virtual_to_full(self):
        pane = self.current_pane()
        if pane is None or not getattr(pane, "_virtual_full_lines", None):
            self.toast("当前标签不是大文件分块编辑模式", "warn")
            return
        self.convert_virtual_to_full(pane)

    def extract_variable(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        try:
            sel = pane.text.get("sel.first", "sel.last")
        except Exception:
            self.toast("先选中要提取的表达式")
            return
        sel = sel.strip()
        if not sel:
            self.toast("先选中要提取的表达式")
            return
        name = simpledialog.askstring("提取变量", "变量名：", initialvalue="tmp", parent=self.root)
        if not name:
            return
        t = pane.text
        start = t.index("sel.first")
        end = t.index("sel.last")
        indent = re.match(r"[ \t]*", t.get(f"{start} linestart", start)).group(0)
        e = pane.text.index("sel.last")
        start_line = t.index(f"{start} linestart")
        start_off = self._text_offset(t.get("1.0", "end-1c"), start)
        line_start_off = self._text_offset(t.get("1.0", "end-1c"), start_line)
        after = (t.get("1.0", "end-1c")[:line_start_off]
                 + f"{indent}var {name} = {sel}\n"
                 + t.get("1.0", "end-1c")[line_start_off:start_off]
                 + name
                 + t.get("1.0", "end-1c")[self._text_offset(t.get("1.0", "end-1c"), end):])

        def apply(new_text):
            self._replace_pane_text(pane, new_text, "提取变量 " + name)

        self._preview_text_change("提取变量预览 · " + name,
                                  t.get("1.0", "end-1c"), after, apply,
                                  "将选中的表达式提升为前一行变量，并在原位置用变量名替代")

    def extract_function(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        try:
            sel = pane.text.get("sel.first", "sel.last")
        except Exception:
            self.toast("先选中要提取的代码")
            return
        if not sel.strip():
            self.toast("先选中要提取的代码")
            return
        name = simpledialog.askstring("提取函数", "函数名：", initialvalue="新函数", parent=self.root)
        if not name:
            return
        t = pane.text
        start = t.index("sel.first")
        end = t.index("sel.last")
        indent = re.match(r"[ \t]*", t.get(f"{start} linestart", start)).group(0)
        body_indent = indent + " " * max(2, getattr(self, "indent_width", 4))
        body = "\n".join(body_indent + ln.lstrip() if ln.strip() else "" for ln in sel.split("\n"))
        block = f"{indent}def {name}():\n{body}\n"
        full = t.get("1.0", "end-1c")
        start_off = self._text_offset(full, start)
        end_off = self._text_offset(full, end)
        line_start_off = self._text_offset(full, t.index(f"{start} linestart"))
        after = (full[:line_start_off] + block + full[line_start_off:start_off]
                 + f"{name}()" + full[end_off:])

        def apply(new_text):
            self._replace_pane_text(pane, new_text, "提取函数 " + name)

        self._preview_text_change("提取函数预览 · " + name,
                                  full, after, apply,
                                  "将在选中代码前插入函数定义，并把原代码替换为函数调用")

    def generate_expect_test(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        try:
            expr = pane.text.get("sel.first", "sel.last").strip()
        except Exception:
            expr = ""
        if not expr:
            self.toast("先选中要断言的表达式")
            return
        expected = simpledialog.askstring("生成测试", "期望值：", initialvalue="0", parent=self.root)
        if expected is None:
            return
        label = expr.replace('"', "'")[:40]
        code = f'\nexpect({expr}, {expected}, "{label}")\n'
        pane.text.insert("insert lineend", code)
        pane.highlight()
        self.mark_dirty(pane)
        self.toast("已生成 expect 测试")

    def complete_import(self):
        pane = self.current_pane()
        if not pane:
            return
        word, _ = pane._word_before()
        if not word:
            self.toast("把光标放到要导入的名字上")
            return
        cur = os.path.abspath(pane.path) if pane.path else ""
        found = None
        for fp in self._walk_files((".impor",)):
            if os.path.abspath(fp) == cur:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    src = f.read()
            except Exception:
                continue
            if re.search(r"(?m)^\s*(?:func|class|var|const|let)\s+" + re.escape(word) + r"\b", src):
                found = fp
                break
        if not found:
            self.toast("没找到 " + word + " 的定义")
            return
        try:
            rel = rel_quiet(found, ROOT_DIR)
        except Exception:
            rel = os.path.basename(found)
        module = rel[:-6].replace(os.sep, ".") if rel.endswith(".impor") else rel
        line = f"from {module} import {word}"
        current = pane.text.get("1.0", "end-1c")
        if line in current:
            self.toast("已经导入过了")
            return
        first = current.split("\n", 1)[0]
        if first.startswith("important<<"):
            pane.text.insert("2.0", line + "\n")
        else:
            pane.text.insert("1.0", line + "\n")
        pane.highlight()
        self.mark_dirty(pane)
        self.toast(f"已补全：{line}")

    def _git(self, args):
        git = shutil.which("git") or r"D:\Git\cmd\git.exe"
        return subprocess.run([git, "-C", ROOT_DIR] + args, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def git_blame_current(self):
        pane = self.current_pane()
        if not pane or not pane.path:
            self.toast("先打开一个已保存文件", "warn")
            return
        try:
            line = int(pane.text.index("insert").split(".")[0])
        except Exception:
            line = 1
        r = self._git(["blame", "-L", "%d,%d" % (line, line), "--porcelain", "--", pane.path])
        if r.returncode != 0:
            self.toast("blame 失败：" + (r.stderr or r.stdout)[:120], "err")
            return
        sha = author = ""
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("author "):
                author = ln[7:]
            elif ln and not ln.startswith(("author-", "committer", "summary", "boundary", "previous", "\t")) and len(ln.split()) >= 3:
                sha = ln.split()[0]
        msg = "第 %d 行 · %s · %s" % (line, author or "未知作者", sha[:10] or "未提交")
        self.show_bottom("out")
        self.append_out("blame " + os.path.basename(pane.path) + ":" + str(line) + "  " + msg + "\n", "dim")
        self.toast(msg, "ok")

    def git_conflicts(self):
        r = self._git(["diff", "--name-only", "--diff-filter=U"])
        files = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("Git 冲突入口")
        win.transient(self.root)
        win.geometry("620x360")
        tk.Label(win, text=(("未合并文件 %d 个（双击打开）" % len(files)) if files else "当前没有未合并冲突"),
                 bg=THEME["panel"], fg=THEME["fg"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=10)
        lst = tk.Listbox(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                         font=(self.mono, 10))
        lst.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        for f in files:
            lst.insert(tk.END, f)

        def open_sel(e=None):
            sel = lst.curselection()
            if not sel:
                return
            self.open_conflict_merge(os.path.join(ROOT_DIR, files[sel[0]]), files[sel[0]])
            win.destroy()

        lst.bind("<Double-Button-1>", open_sel)
        tk.Label(win, text="提示：双击进入三栏合并；base/ours/theirs 逐块接受，保存前做语法校验。",
                 bg=THEME["panel"], fg=THEME["muted"], font=("Microsoft YaHei UI", 8),
                 anchor="w").pack(fill=tk.X, padx=12, pady=(0, 8))

    def _git_merge_text(self, path):
        """用三个 stage 跑 git merge-file --diff3，返回带 base 的冲突文本。"""
        repo_path = rel_quiet(os.path.abspath(path), ROOT_DIR).replace("\\", "/")
        parts = []
        for stage in (2, 1, 3):  # ours, base, theirs
            r = self._git(["show", ":%d:%s" % (stage, repo_path)])
            if r.returncode != 0:
                if stage == 1:
                    parts.append("")
                else:
                    raise RuntimeError((r.stderr or r.stdout or "stages 缺失").strip()[:300])
            else:
                parts.append(r.stdout or "")
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="impor_merge_")
        try:
            files = []
            for i, text in enumerate(parts):
                p = os.path.join(tmpdir, ("ours", "base", "theirs")[i] + ".impor")
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                files.append(p)
            git = shutil.which("git") or r"D:\Git\cmd\git.exe"
            r = subprocess.run([git, "merge-file", "-p", "--diff3",
                                "-L", "ours", "-L", "base", "-L", "theirs",
                                files[0], files[1], files[2]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode not in (0, 1):
                raise RuntimeError((r.stderr or r.stdout or "merge-file 失败").strip()[:300])
            return r.stdout or ""
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def open_conflict_merge(self, path, repo_path=None):
        """3.2.3：base / ours / theirs 三栏合并，逐 hunk 接受。"""
        try:
            merged = self._git_merge_text(path)
        except Exception as exc:
            messagebox.showerror("三方合并", "读取冲突阶段失败：%s" % exc, parent=self.root)
            return
        segments, hunks = split_conflict_markers(merged)
        if not hunks:
            self.toast("这个文件没有可解析的冲突块", "warn")
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("三方合并 · " + os.path.basename(path))
        win.transient(self.root)
        win.geometry("1240x780")

        head = tk.Frame(win, bg=THEME["panel_head"])
        head.pack(fill=tk.X)
        tk.Label(head, text="%s · %d 个冲突块" % (repo_path or os.path.basename(path), len(hunks)),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT, padx=10, pady=7)
        tk.Label(head, text="绿色=已选择，灰色=未解决；保存前会做语法校验",
                 bg=THEME["panel_head"], fg=THEME["muted"], anchor="e",
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.RIGHT, padx=10)

        body = tk.Frame(win, bg=THEME["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        left = tk.Frame(body, bg=THEME["panel"])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        tk.Label(left, text="冲突块", bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        lst = tk.Listbox(left, width=22, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                         font=(self.mono, 9), exportselection=False)
        lst.pack(fill=tk.Y, expand=True)
        for i, _ in enumerate(hunks):
            lst.insert(tk.END, "%02d  %s" % (i + 1, "未解决"))
        lst.itemconfigure(0, foreground=THEME["yellow"])

        cols = tk.Frame(body, bg=THEME["panel"])
        cols.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        labels = ("BASE（共同祖先）", "OURS（当前分支）", "THEIRS（合入分支）")
        boxes = []
        for i, text in enumerate(labels):
            col = tk.Frame(cols, bg=THEME["panel"])
            col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
            tk.Label(col, text=text, bg=THEME["panel"], fg=THEME["muted"], anchor="w",
                     font=("Microsoft YaHei UI", 8, "bold")).pack(fill=tk.X)
            box = tk.Text(col, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                          font=(self.mono, 9), padx=6, pady=4, wrap="none", height=24)
            box.pack(fill=tk.BOTH, expand=True)
            box.configure(state="disabled")
            boxes.append(box)
        for b in boxes[1:]:
            b.bind("<MouseWheel>", lambda e, s=boxes[0]: (s.yview_scroll(int(-e.delta / 120), "units"), "break")[1])

        preview_head = tk.Frame(win, bg=THEME["panel"])
        preview_head.pack(fill=tk.X, padx=10, pady=(0, 2))
        tk.Label(preview_head, text="合并结果预览", bg=THEME["panel"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 8, "bold")).pack(side=tk.LEFT)
        status = tk.Label(preview_head, text="", bg=THEME["panel"], fg=THEME["muted"],
                          font=("Microsoft YaHei UI", 8))
        status.pack(side=tk.RIGHT)
        preview = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                          font=(self.mono, 9), padx=8, pady=6, wrap="none", height=10)
        preview.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 6))
        preview.configure(state="disabled")

        choices = [None] * len(hunks)

        def current_text():
            out = []
            for seg in segments:
                if isinstance(seg, dict):
                    choice = choices[seg["index"]]
                    if choice is None:
                        out.append("<<<<<<< ours\n" + "".join(seg["ours"]))
                        if seg["base"]:
                            out.append("||||||| base\n" + "".join(seg["base"]))
                        out.append("=======\n" + "".join(seg["theirs"]) + ">>>>>>> theirs\n")
                    elif choice == "both":
                        out.append("".join(seg["ours"]) + "".join(seg["theirs"]))
                    else:
                        out.append("".join(seg.get(choice, [])))
                else:
                    out.append(seg)
            return "".join(out)

        def refresh():
            sel = lst.curselection()
            sel = sel[0] if sel else 0
            text = current_text()
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", text)
            preview.configure(state="disabled")
            unresolved = sum(1 for c in choices if c is None)
            status.configure(text=("还有 %d 个冲突未解决" % unresolved) if unresolved
                             else "全部冲突已解决，可以保存",
                             fg=THEME["yellow"] if unresolved else THEME["green"])
            lst.delete(0, tk.END)
            for i, c in enumerate(choices):
                lst.insert(i, "%02d  %s" % (i + 1, "已解决" if c is not None else "未解决"))
                lst.itemconfigure(i, foreground=THEME["green"] if c is not None else THEME["yellow"])
            if hunks:
                lst.selection_clear(0, tk.END)
                lst.selection_set(min(sel, len(hunks) - 1))
                lst.activate(min(sel, len(hunks) - 1))

        def show_hunk(e=None):
            sel = lst.curselection()
            if not sel:
                return
            h = hunks[sel[0]]
            for box, key in zip(boxes, ("base", "ours", "theirs")):
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("1.0", "".join(h[key]))
                box.configure(state="disabled")

        def choose(kind):
            sel = lst.curselection()
            if not sel:
                return
            choices[sel[0]] = kind
            refresh()
            if sel[0] + 1 < len(hunks):
                lst.selection_clear(0, tk.END)
                lst.selection_set(sel[0] + 1)
                lst.activate(sel[0] + 1)
                show_hunk()

        def save(stage=False):
            unresolved = sum(1 for c in choices if c is None)
            if unresolved:
                messagebox.showwarning("三方合并", "还有 %d 个冲突未解决，先逐块选择。" % unresolved, parent=win)
                return
            text = current_text()
            try:
                Parser(tokenize(strip_header(text))).program()
            except Exception as exc:
                messagebox.showerror("语法校验未通过", "保存已取消：\n%s" % exc, parent=win)
                return
            tmp = path + ".impor-merge.tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                os.replace(tmp, path)
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=win)
                return
            if stage:
                repo = repo_path or rel_quiet(path, ROOT_DIR).replace("\\", "/")
                r = self._git(["add", "--", repo])
                if r.returncode != 0:
                    messagebox.showwarning("已保存，但 git add 失败",
                                          (r.stderr or r.stdout)[:500], parent=win)
            for pane in self.panes:
                if pane.path and os.path.abspath(pane.path) == os.path.abspath(path):
                    pane.text.configure(state="normal")
                    pane.text.delete("1.0", "end")
                    pane.text.insert("1.0", text)
                    pane.text.edit_modified(False)
                    pane.text.configure(state="normal")
                    pane.highlight()
                    pane._dirty = False
            self.toast("冲突已解决并保存：" + os.path.basename(path), "ok")
            win.destroy()

        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 8))
        FlatButton(bar, "采用 OURS", kind="primary", width=100,
                   command=lambda: choose("ours")).pack(side=tk.LEFT)
        FlatButton(bar, "采用 THEIRS", kind="primary", width=100,
                   command=lambda: choose("theirs")).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "采用 BASE", width=96,
                   command=lambda: choose("base")).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "保留双方", width=96,
                   command=lambda: choose("both")).pack(side=tk.LEFT, padx=4)
        FlatButton(bar, "保存工作区", width=110,
                   command=lambda: save(False)).pack(side=tk.RIGHT)
        FlatButton(bar, "保存并 git add", kind="primary", width=124,
                   command=lambda: save(True)).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "关闭", width=72, command=win.destroy).pack(side=tk.RIGHT, padx=4)

        lst.bind("<<ListboxSelect>>", show_hunk)
        refresh()
        lst.selection_set(0)
        show_hunk()

    def _git_hunks(self, path):
        """取当前文件 diff，拆成可单独 git apply --cached 的 hunk 列表。"""
        r = self._git(["diff", "--unified=0", "--", path])
        if r.returncode != 0 or not (r.stdout or "").strip():
            return []
        lines = (r.stdout or "").splitlines(True)
        header = []
        hunks = []
        cur = None
        for ln in lines:
            if ln.startswith("@@"):
                if cur:
                    hunks.append(cur)
                cur = [ln]
            elif cur is not None:
                cur.append(ln)
            else:
                header.append(ln)
        if cur:
            hunks.append(cur)
        out = []
        for h in hunks:
            if not h:
                continue
            out.append(("".join(header) + "".join(h), h[0].strip()))
        return out

    def git_stage_hunk(self):
        pane = self.current_pane()
        if not pane or not pane.path:
            self.toast("先打开一个已保存文件", "warn")
            return
        hunks = self._git_hunks(rel_quiet(pane.path, self.workspace))
        if not hunks:
            self.toast("当前文件没有可暂存的差异", "warn")
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("按 hunk 暂存 · " + os.path.basename(pane.path))
        win.transient(self.root)
        win.geometry("760x500")
        tk.Label(win, text="选择差异块，双击暂存到 index（不会提交）。",
                 bg=THEME["panel"], fg=THEME["fg"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=8)
        lst = tk.Listbox(win, height=8, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                         font=(self.mono, 9))
        lst.pack(fill=tk.X, padx=12, pady=(0, 6))
        for i, (_, label) in enumerate(hunks):
            lst.insert(tk.END, "%02d  %s" % (i + 1, label))
        body = tk.Text(win, bg="#090c11", fg=THEME["fg"], relief="flat", font=(self.mono, 9),
                       padx=8, pady=6)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        def show_hunk(e=None):
            sel = lst.curselection()
            if not sel:
                return
            body.delete("1.0", "end")
            body.insert("1.0", hunks[sel[0]][0])

        def stage(e=None):
            sel = lst.curselection()
            if not sel:
                return
            import tempfile
            patch = hunks[sel[0]][0]
            fd, name = tempfile.mkstemp(suffix=".patch", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    f.write(patch)
                r = self._git(["apply", "--cached", "--unidiff-zero", name])
                if r.returncode == 0:
                    self.toast("已暂存 hunk #%d" % (sel[0] + 1), "ok")
                    win.destroy()
                else:
                    messagebox.showerror("暂存失败", (r.stderr or r.stdout)[:500], parent=win)
            finally:
                try:
                    os.remove(name)
                except Exception:
                    pass

        lst.bind("<<ListboxSelect>>", show_hunk)
        lst.bind("<Double-Button-1>", stage)
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=12, pady=(0, 10))
        FlatButton(bar, "暂存选中 hunk", command=stage, kind="primary", width=110).pack(side=tk.LEFT)
        FlatButton(bar, "关闭", command=win.destroy, width=64).pack(side=tk.LEFT, padx=6)
        if hunks:
            lst.selection_set(0)
            show_hunk()

    def open_git_panel(self):
        """3.6.6：Git 变更审查台——变更文件树 + 文件级 diff + 暂存/取消暂存/提交。"""
        win = tk.Toplevel(self.root, bg=THEME["bg"])
        apply_window_icon(win)
        win.title("Git 变更审查台 · Impor")
        win.geometry("1020x640")
        head = tk.Frame(win, bg=THEME["panel_head"], height=34)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        branch = tk.Label(head, text="分支 --", bg=THEME["panel_head"], fg=THEME["fg"],
                          font=("Microsoft YaHei UI", 9, "bold"))
        branch.pack(side=tk.LEFT, padx=12)
        body = tk.PanedWindow(win, orient=tk.HORIZONTAL, bg=THEME["bg"],
                              sashwidth=4, bd=0, sashrelief="flat")
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 4))
        left = tk.Frame(body, bg=THEME["sidebar"])
        right = tk.Frame(body, bg=THEME["editor"])
        body.add(left, minsize=250, width=320)
        body.add(right, stretch="always")

        tree = ttk.Treeview(left, columns=("state",), show="tree headings",
                            selectmode="browse")
        tree.heading("#0", text="变更文件")
        tree.heading("state", text="状态")
        tree.column("#0", width=230)
        tree.column("state", width=64, anchor="center")
        tree.pack(fill=tk.BOTH, expand=True, padx=(6, 6), pady=6)
        tree.tag_configure("staged", foreground=THEME["green"])
        tree.tag_configure("modified", foreground=THEME["yellow"])
        tree.tag_configure("untracked", foreground=THEME["accent"])

        diff = tk.Text(right, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       font=(self.mono, 10), padx=12, pady=8, wrap="none")
        diff.pack(fill=tk.BOTH, expand=True)
        diff.tag_configure("add", foreground=THEME["green"])
        diff.tag_configure("del", foreground=THEME["red"])
        diff.tag_configure("hunk", foreground=THEME["accent"])
        diff.tag_configure("meta", foreground=THEME["muted"])

        state = {"path": None}
        status_map = {"M": "修改", "A": "新增", "D": "删除", "R": "重命名",
                      "C": "复制", "U": "冲突", "??": "未跟踪", "!!": "忽略"}

        def show_file(path=None):
            path = path or state.get("path")
            if not path:
                return
            state["path"] = path
            diff.configure(state="normal")
            diff.delete("1.0", "end")
            rel = rel_quiet(path, self.workspace).replace("\\", "/")
            r = self._git(["diff", "--", rel])
            text = r.stdout or ""
            if not text.strip():
                r2 = self._git(["diff", "--cached", "--", rel])
                text = r2.stdout or ""
            if not text.strip() and os.path.isfile(path):
                try:
                    lines = open(path, "r", encoding="utf-8", errors="replace").read().splitlines()
                    diff.insert("end", "未跟踪文件（全部内容视为新增）\n", "meta")
                    for ln in lines:
                        diff.insert("end", "+ " + ln + "\n", "add")
                    diff.configure(state="disabled")
                    return
                except Exception as exc:
                    text = str(exc)
            if not text.strip():
                text = "（该文件没有可显示的差异）"
            for ln in text.splitlines():
                tag = "meta"
                if ln.startswith("@@") or ln.startswith("diff ") or ln.startswith("index "):
                    tag = "hunk" if ln.startswith("@@") else "meta"
                elif ln.startswith("+") and not ln.startswith("+++"):
                    tag = "add"
                elif ln.startswith("-") and not ln.startswith("---"):
                    tag = "del"
                diff.insert("end", ln + "\n", tag)
            diff.configure(state="disabled")

        def refresh(keep=None):
            r = self._git(["status", "--porcelain", "-uall"])
            rows = []
            for raw in (r.stdout or "").splitlines():
                if len(raw) < 4:
                    continue
                code, path = raw[:2], raw[3:].strip()
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                rows.append((code, path))
            tree.delete(*tree.get_children())
            for code, rel in rows:
                full = os.path.abspath(os.path.join(ROOT_DIR, rel))
                state_txt = status_map.get(code.strip() or code[:1], code.strip() or "?")
                tag = "staged" if code[0] in "MADR" else ("untracked" if code == "??" else "modified")
                iid = rel
                if tree.exists(iid):
                    iid = rel + "#" + str(len(tree.get_children()))
                tree.insert("", tk.END, iid=iid, text=rel, values=(state_txt,),
                            tags=(tag,))
                tree.set(iid, "state", state_txt)
            rb = self._git(["branch", "--show-current"])
            branch.configure(text="分支 " + ((rb.stdout or "").strip() or "（无）") +
                             "   ·   %d 项变更" % len(rows))
            if rows:
                first = os.path.abspath(os.path.join(self.workspace, rows[0][1]))
                show_file(first)
            else:
                state["path"] = None
                diff.configure(state="normal")
                diff.delete("1.0", "end")
                diff.insert("1.0", "工作区干净", "meta")
                diff.configure(state="disabled")

        def pick(_e=None):
            item = tree.focus()
            if item:
                rel = tree.item(item, "text")
                show_file(os.path.abspath(os.path.join(ROOT_DIR, rel)))

        def stage():
            if state["path"]:
                rel = rel_quiet(state["path"], ROOT_DIR)
                self._git(["add", "--", rel])
                refresh()

        def unstage():
            if state["path"]:
                rel = rel_quiet(state["path"], ROOT_DIR)
                self._git(["reset", "HEAD", "--", rel])
                refresh()

        def stage_all():
            self._git(["add", "-A"])
            refresh()

        def commit():
            from tkinter import simpledialog
            msg = simpledialog.askstring("提交", "提交说明：", parent=win)
            if msg:
                self._git(["commit", "-m", msg])
                refresh()

        tree.bind("<<TreeviewSelect>>", pick)
        tree.bind("<Double-Button-1>", lambda e: state["path"] and self.open_path(state["path"]))
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 8))
        for label, cmd, kind in (("刷新", refresh, "normal"), ("暂存选中", stage, "normal"),
                                 ("取消暂存", unstage, "normal"), ("暂存全部", stage_all, "normal"),
                                 ("提交", commit, "primary"), ("打开文件",
                                  lambda: state["path"] and self.open_path(state["path"]), "normal"),
                                 ("关闭", win.destroy, "normal")):
            FlatButton(bar, label, command=cmd, kind=kind, width=86).pack(side=tk.LEFT, padx=3)
        refresh()

    def _open_git_panel_basic(self):
        win = tk.Toplevel(self.root)
        win.title("Git 面板")
        win.geometry("700x480")
        win.configure(bg=THEME["bg"])
        txt = tk.Text(win, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                      font=(self.mono, 10), padx=10, pady=8)
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 4))
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=(0, 10))

        def refresh():
            r = self._git(["status", "--porcelain"])
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", (r.stdout or r.stderr or "（空）").strip() or "工作区干净")
            txt.configure(state="disabled")

        def do_init():
            r = self._git(["init"])
            txt.configure(state="normal")
            txt.insert("end", "\n" + (r.stdout or r.stderr))
            txt.configure(state="disabled")

        def do_add():
            r = self._git(["add", "-A"])
            refresh()
            txt.configure(state="normal")
            txt.insert("end", "\n" + (r.stdout or r.stderr))
            txt.configure(state="disabled")

        def do_commit():
            from tkinter import simpledialog
            msg = simpledialog.askstring("提交", "提交说明：", parent=win)
            if not msg:
                return
            r = self._git(["commit", "-m", msg])
            refresh()
            txt.configure(state="normal")
            txt.insert("end", "\n" + (r.stdout or r.stderr or "已提交"))
            txt.configure(state="disabled")

        def do_diff():
            r = self._git(["diff", "--"])
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", (r.stdout or r.stderr or "（没有差异）").strip() or "（没有差异）")
            txt.configure(state="disabled")

        def do_branch():
            r = self._git(["branch", "--show-current"])
            txt.configure(state="normal")
            txt.insert("end", "\n当前分支：" + ((r.stdout or "").strip() or "（无）") + "\n")
            txt.configure(state="disabled")

        def do_switch():
            from tkinter import simpledialog
            name = simpledialog.askstring("切换分支", "分支名：", parent=win)
            if not name:
                return
            r = self._git(["checkout", name])
            refresh()
            txt.configure(state="normal")
            txt.insert("end", "\n" + (r.stdout or r.stderr))
            txt.configure(state="disabled")

        for label, cmd in (("刷新", refresh), ("查看差异", do_diff), ("当前分支", do_branch),
                           ("切换分支", do_switch), ("初始化", do_init), ("暂存全部", do_add), ("提交", do_commit)):
            FlatButton(bar, label, command=cmd, width=80).pack(side=tk.LEFT, padx=3, pady=6)
        bar2 = tk.Frame(win, bg=THEME["panel"])
        bar2.pack(fill=tk.X, padx=10, pady=(0, 10))
        for label, cmd in (("行级 blame", self.git_blame_current), ("冲突入口", self.git_conflicts),
                           ("按 hunk 暂存", self.git_stage_hunk)):
            FlatButton(bar2, label, command=cmd, width=100).pack(side=tk.LEFT, padx=3, pady=4)
        refresh()

    def _clear_multi_cursor(self):
        pane = self.current_pane()
        if pane:
            pane._mc_clear()




    def quick_open(self):
        base = os.path.abspath(getattr(self, "workspace", "") or ROOT_DIR)
        items = [(f"最近 · {rel_quiet(p, base)}", p) for p in self._recent
                 if os.path.exists(p)]
        for p in self._walk_files((".impor", ".md", ".py", ".json")):
            rel = rel_quiet(p, base)
            items.append((rel, p))
        Finder(self, "快速打开 · Ctrl+P", items, lambda p: self.open_path(p),
               "输入文件名或路径片段")

    def global_search(self):
        from .editor_search_ui import SearchReplaceDialog
        pane = self.current_pane()
        selected = ""
        try:
            if pane:
                selected = pane.text.get("sel.first", "sel.last")
        except Exception:
            selected = ""
        SearchReplaceDialog(self, mode="search", query=(selected or "").split("\n")[0][:120])

    def _current_nav_pos(self):
        pane = self.current_pane()
        if not pane or not pane.path:
            return None
        try:
            line = int(str(pane.text.index("insert")).split(".")[0])
        except Exception:
            line = 1
        return (os.path.abspath(pane.path), line)

    def open_path_at(self, path, line):
        if not getattr(self, "_nav_silent", False):
            cur = self._current_nav_pos()
            if cur and (not self._nav_back or self._nav_back[-1] != cur):
                self._nav_back.append(cur)
                del self._nav_back[:-50]
                self._nav_forward.clear()
        pane = self.open_path(path)
        if pane:
            pane.text.mark_set("insert", f"{line}.0")
            pane.text.see("insert")
            pane.focus_text()

    def nav_back(self):
        if not self._nav_back:
            self.toast("没有可后退的位置")
            return
        cur = self._current_nav_pos()
        if cur:
            self._nav_forward.append(cur)
            del self._nav_forward[:-50]
        path, line = self._nav_back.pop()
        self._nav_silent = True
        try:
            self.open_path_at(path, line)
        finally:
            self._nav_silent = False
        self.toast("后退：%s:%d" % (os.path.basename(path), line))

    def nav_forward(self):
        if not self._nav_forward:
            self.toast("没有可前进的位置")
            return
        cur = self._current_nav_pos()
        if cur:
            self._nav_back.append(cur)
            del self._nav_back[:-50]
        path, line = self._nav_forward.pop()
        self._nav_silent = True
        try:
            self.open_path_at(path, line)
        finally:
            self._nav_silent = False
        self.toast("前进：%s:%d" % (os.path.basename(path), line))

    def outline(self):
        pane = self.current_pane()
        if not pane:
            return
        items = []
        try:
            if pane.path:
                self.index.update_text(pane.path, pane.text.get("1.0", "end-1c"))
            syms = self.index.symbols(pane.path) if pane.path else []
        except Exception:
            syms = []
        if syms:
            for s in syms:
                if s.kind in ("func", "class", "exception", "struct", "var", "import"):
                    extra = ("  : " + s.typ) if s.typ and s.typ != "?" else ""
                    items.append((f"{s.line:>4}  {s.kind} {s.name}{extra}   [{s.scope}]", s.line))
        else:
            for i, line in enumerate(pane.text.get("1.0", "end-1c").split("\n"), 1):
                m = re.match(r"^(\s*)(func|def|class|var|const|let)\s+([A-Za-z_][A-Za-z0-9_]*|[一-龥]+)", line)
                if m:
                    items.append((f"{i:>4}  {m.group(2)} {m.group(3)}", i))
        Finder(self, "符号大纲 · 文档符号", items,
               lambda ln: (pane.text.mark_set("insert", f"{ln}.0"), pane.text.see("insert")),
               "AST 文档符号：类型、作用域与声明行")

    def project_outline(self):
        try:
            self.index.refresh()
            syms = self.index.symbols()
        except Exception as e:
            self.toast("符号索引失败：" + str(e)[:60])
            return
        items = []
        for s in syms:
            rel = rel_quiet(s.path, self.workspace).replace("\\", "/")
            items.append((f"{rel}:{s.line}  {s.kind} {s.name}", (s.path, s.line)))
        Finder(self, "项目符号索引 · 全工作区", items,
               lambda t: self.open_path_at(t[0], t[1]), "跨文件函数、类、结构体与变量")

    def project_references(self):
        try:
            self.index.refresh()
            syms = self.index.symbols()
        except Exception as e:
            self.toast("符号索引失败：" + str(e)[:60])
            return
        defs = {}
        for s in syms:
            defs.setdefault(s.name, []).append(s)
        items = []
        for name, ds in defs.items():
            try:
                n = len(self.index.references(name))
            except Exception:
                n = 0
            d = ds[0]
            rel = rel_quiet(d.path, self.workspace).replace("\\", "/")
            items.append((f"{name}  {d.kind}  引用 {n}  {rel}:{d.line}", (d.path, d.line)))
        items.sort(key=lambda t: (-int(t[0].split("引用 ")[1].split("  ")[0]), t[0].lower()))
        Finder(self, "项目引用总览 · 按引用数排序", items,
               lambda t: self.open_path_at(t[0], t[1]), "全工作区符号与引用次数")

    def project_diagnostics(self):
        from .analysis import analyze, style_lint
        files = list(self._walk_files((".impor",)))
        rows = []
        for fp in files[:800]:
            try:
                raw = open(fp, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            src = strip_header(raw)
            try:
                ast = Parser(tokenize(src)).program()
                issues = list(getattr(analyze(ast, src.split("\n")), "issues", []) or [])
                issues += list(style_lint(src, os.path.basename(fp)) or [])
            except Exception as e:
                issues = [type("E", (), {"line": 1, "col": 0, "severity": "error",
                                          "msg": str(e)[:160]})()]
            for it in issues[:40]:
                rows.append((getattr(it, "severity", "warning"), os.path.abspath(fp),
                             getattr(it, "line", 1), getattr(it, "col", 1),
                             getattr(it, "msg", "")))
        rows.sort(key=lambda r: (0 if r[0] == "error" else 1, r[1], r[2]))
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("项目诊断总览")
        win.transient(self.root)
        win.geometry("920x500")
        errs = sum(1 for r in rows if r[0] == "error")
        tk.Label(win, text="扫描 %d 个文件 · 错误 %d · 其他 %d" % (len(files), errs, len(rows) - errs),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        tree = ttk.Treeview(win, columns=("sev", "file", "line", "msg"), show="headings")
        for col, text, width in (("sev", "级别", 64), ("file", "文件", 300),
                                 ("line", "行", 52), ("msg", "说明", 460)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col in ("file", "msg")))
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        for r in rows:
            tree.insert("", tk.END, values=(r[0], rel_quiet(r[1], self.workspace), r[2], r[4][:150]))

        def jump(e=None):
            item = tree.focus()
            if not item:
                return
            vals = tree.item(item, "values")
            rel = vals[1]
            for r in rows:
                if rel_quiet(r[1], self.workspace) == rel and str(r[2]) == str(vals[2]):
                    self.open_path_at(r[1], r[2])
                    break
        tree.bind("<Double-Button-1>", jump)
        tree.bind("<Return>", jump)

    def normalize_encodings(self):
        directory = filedialog.askdirectory(title="选择要规范化的目录", initialdir=self.workspace)
        if not directory:
            return
        skip = {".git", "node_modules", "__pycache__", "build", "dist", ".venv", "venv"}
        found = []
        for base, dirs, names in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in names:
                if not fn.endswith((".impor", ".imporproj")):
                    continue
                fp = os.path.join(base, fn)
                try:
                    data = open(fp, "rb").read()
                except Exception:
                    continue
                bom = data.startswith(b"\xef\xbb\xbf")
                crlf = b"\r\n" in data
                if bom or crlf or b"\r" in data:
                    found.append((fp, ("BOM " if bom else "") + ("CRLF" if crlf else ("CR" if b"\r" in data else "LF"))))
        if not found:
            self.toast("没有需要规范化的文件（已是 UTF-8 无 BOM + LF）")
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("批量规范化编码 · UTF-8 无 BOM + LF")
        win.transient(self.root)
        win.geometry("720x440")
        tk.Label(win, text="将把 %d 个文件统一为 UTF-8（无 BOM）+ LF，写回前请确认。" % len(found),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        tree = ttk.Treeview(win, columns=("file", "now", "to"), show="headings")
        for col, text, width in (("file", "文件", 480), ("now", "当前", 90), ("to", "目标", 120)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col == "file"))
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        for fp, now in found:
            tree.insert("", tk.END, values=(rel_quiet(fp, self.workspace), now, "UTF-8 · LF"))
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=8)

        def apply():
            changed = 0
            for fp, _ in found:
                try:
                    text = open(fp, "rb").read().decode("utf-8-sig")
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                    open(fp, "wb").write(text.encode("utf-8"))
                    changed += 1
                except Exception:
                    continue
            for pane in self.panes:
                if pane.path and os.path.abspath(pane.path) in {os.path.abspath(x[0]) for x in found} and not pane._dirty:
                    try:
                        text = open(pane.path, "rb").read().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                        pane.text.delete("1.0", "end")
                        pane.text.insert("1.0", text)
                        pane._encoding, pane._newline, pane._bom = "UTF-8", "LF", False
                        pane.highlight()
                    except Exception:
                        pass
            win.destroy()
            self.toast("已规范化 %d 个文件" % changed, "ok")
        FlatButton(bar, "规范化", command=apply, kind="primary", width=104).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", command=win.destroy, width=72).pack(side=tk.RIGHT, padx=4)

    def project_quick_fix(self):
        from .fmt import fmt_source
        import difflib
        found = []
        for fp in list(self._walk_files((".impor",)))[:800]:
            try:
                raw = open(fp, "r", encoding="utf-8", errors="replace").read()
                fixed = fmt_source(raw, self.indent_width, 100).replace("\r\n", "\n")
                if not fixed.endswith("\n"):
                    fixed += "\n"
                if fixed != raw.replace("\r\n", "\n"):
                    found.append((fp, fixed, raw))
            except Exception:
                continue
        if not found:
            self.toast("全项目已是格式规范状态")
            return
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("项目级快速修复 · 格式化预览")
        win.transient(self.root)
        win.geometry("1000x600")
        tk.Label(win, text="共 %d 个文件会变化；右侧为选中文件的 unified diff。" % len(found),
                 bg=THEME["panel_head"], fg=THEME["fg"], anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(fill=tk.X, ipady=6, padx=10)
        body = tk.PanedWindow(win, orient=tk.HORIZONTAL, bg=THEME["bg"], sashwidth=4)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        left = tk.Frame(body, bg=THEME["panel"])
        body.add(left, width=260)
        tree = ttk.Treeview(left, columns=("file",), show="headings")
        tree.heading("file", text="文件")
        tree.column("file", width=250)
        tree.pack(fill=tk.BOTH, expand=True)
        right = tk.Frame(body, bg=THEME["editor"])
        body.add(right, stretch="always")
        diff = tk.Text(right, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                       wrap="none", font=("Consolas", 9))
        diff.pack(fill=tk.BOTH, expand=True)
        for i, (fp, _, _) in enumerate(found):
            tree.insert("", tk.END, iid=str(i), values=(rel_quiet(fp, self.workspace),))

        def show(e=None):
            item = tree.focus()
            if not item:
                return
            fp, fixed, raw = found[int(item)]
            d = list(difflib.unified_diff(raw.replace("\r\n", "\n").splitlines(),
                                          fixed.splitlines(), fromfile="a/" + os.path.basename(fp),
                                          tofile="b/" + os.path.basename(fp), lineterm="", n=2))
            diff.delete("1.0", "end")
            diff.insert("end", "\n".join(d[:1200]))
        tree.bind("<<TreeviewSelect>>", show)
        if found:
            tree.selection_set("0"); show()
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=10, pady=8)

        def apply():
            changed = 0
            for fp, fixed, _ in found:
                try:
                    open(fp, "wb").write(fixed.encode("utf-8"))
                    changed += 1
                except Exception:
                    continue
            for pane in self.panes:
                for fp, fixed, _ in found:
                    if pane.path and os.path.abspath(pane.path) == os.path.abspath(fp) and not pane._dirty:
                        pane.text.delete("1.0", "end")
                        pane.text.insert("1.0", fixed)
                        pane.highlight()
            win.destroy()
            self.toast("已修复 %d 个文件" % changed, "ok")
        FlatButton(bar, "应用全部", kind="primary", width=104, command=apply).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", width=72, command=win.destroy).pack(side=tk.RIGHT, padx=4)

    def call_graph(self):
        try:
            self.index.refresh()
            syms = [s for s in self.index.symbols() if s.kind == "func"]
        except Exception as e:
            self.toast("符号索引失败：" + str(e)[:60])
            return
        defs = {}
        for s in syms:
            defs.setdefault(s.name, s)
        known = set(defs)
        edges = {}
        text_cache = {}
        for name, s in defs.items():
            try:
                if s.path not in text_cache:
                    text_cache[s.path] = open(s.path, "r", encoding="utf-8", errors="replace").read().split("\n")
                lines = text_cache[s.path]
            except Exception:
                continue
            base = len(lines[s.line - 1]) - len(lines[s.line - 1].lstrip()) if 0 <= s.line - 1 < len(lines) else 0
            out = set()
            for j in range(s.line, len(lines)):
                line = lines[j]
                if not line.strip():
                    continue
                ind = len(line) - len(line.lstrip())
                if ind <= base:
                    break
                for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", line):
                    callee = m.group(1)
                    if callee in known and callee != name:
                        out.add(callee)
            edges[name] = out
        reverse = {n: set() for n in known}
        for caller, callees in edges.items():
            for callee in callees:
                reverse[callee].add(caller)
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("调用图 · %d 个函数 · %d 条边" % (len(known), sum(len(v) for v in edges.values())))
        win.transient(self.root)
        win.geometry("980x560")
        bar = tk.Frame(win, bg=THEME["panel_head"])
        bar.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(bar, text="筛选", bg=THEME["panel_head"], fg=THEME["fg"]).pack(side=tk.LEFT)
        query = tk.StringVar()
        tk.Entry(bar, textvariable=query, bg=THEME["editor"], fg=THEME["fg"],
                 insertbackground=THEME["fg"], relief="flat", width=24).pack(side=tk.LEFT, padx=6)
        body = tk.PanedWindow(win, orient=tk.HORIZONTAL, bg=THEME["bg"], sashwidth=4)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        left = tk.Frame(body, bg=THEME["panel"])
        body.add(left, width=300)
        tree = ttk.Treeview(left, columns=("name", "out", "file"), show="headings")
        for col, text, width in (("name", "函数", 150), ("out", "调用", 44), ("file", "文件", 130)):
            tree.heading(col, text=text)
            tree.column(col, width=width, stretch=(col == "name"))
        tree.pack(fill=tk.BOTH, expand=True)
        right = tk.Frame(body, bg=THEME["editor"])
        body.add(right, stretch="always")
        detail = tk.Text(right, bg=THEME["editor"], fg=THEME["fg"], relief="flat",
                         wrap="none", font=("Microsoft YaHei UI", 9), padx=8, pady=6)
        detail.pack(fill=tk.BOTH, expand=True)
        rows = []

        def show(name):
            s = defs[name]
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("end", "← 调用者（谁调用 %s）\n" % name, "hdr")
            if reverse[name]:
                for caller in sorted(reverse[name]):
                    cs = defs[caller]
                    detail.insert("end", "  %s  %s:%d\n" % (caller, rel_quiet(cs.path, self.workspace), cs.line))
            else:
                detail.insert("end", "  （没有调用者）\n", "dim")
            detail.insert("end", "\n→ 被调用（%s 调用谁）\n" % name, "hdr")
            if edges[name]:
                for callee in sorted(edges[name]):
                    cs = defs[callee]
                    detail.insert("end", "  %s  %s:%d\n" % (callee, rel_quiet(cs.path, self.workspace), cs.line))
            else:
                detail.insert("end", "  （没有调用其他函数）\n", "dim")
            detail.tag_config("hdr", foreground=THEME["accent"])
            detail.tag_config("dim", foreground=THEME["muted"])
            detail.configure(state="disabled")

        def fill(*_):
            tree.delete(*tree.get_children())
            rows.clear()
            q = query.get().strip().lower()
            for name in sorted(known):
                if q and q not in name.lower():
                    continue
                s = defs[name]
                rows.append((name, s.path, s.line))
                tree.insert("", tk.END, text=name,
                            values=(name, len(edges[name]),
                                    os.path.basename(s.path)))
            if rows:
                tree.selection_set(tree.get_children()[0])
                show(rows[0][0])

        def pick(e=None):
            item = tree.focus()
            if not item:
                return
            name = tree.item(item, "values")[0]
            show(name)

        def jump(e=None):
            idx = detail.index("insert")
            line = detail.get("%s.0" % idx.split(".")[0], "%s.end" % idx.split(".")[0]).strip()
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(\S+):(\d+)", line)
            if m:
                self.open_path_at(os.path.join(self.workspace, m.group(2)), int(m.group(3)))
                win.destroy()

        def open_def(e=None):
            item = tree.focus()
            if item:
                name = tree.item(item, "values")[0]
                s = defs[name]
                self.open_path_at(s.path, s.line)
                win.destroy()

        tree.bind("<<TreeviewSelect>>", pick)
        tree.bind("<Double-Button-1>", open_def)
        detail.bind("<Double-Button-1>", jump)
        query.trace_add("write", fill)
        fill()

    def open_palette(self):
        cmds = [
            ("运行当前脚本", "Ctrl+Enter", self.run_code),
            ("构建独立可执行", "Ctrl+B", self.build_exe),
            ("新建标签", "Ctrl+N", lambda: self.open_new_tab()),
            ("打开文件", "Ctrl+O", self.open_file),
            ("打开文件夹作为工作区", "", self.open_workspace),
            ("添加工作区根目录", "", self.add_workspace_root),
            ("移除选中的根目录", "", self.remove_workspace_root),
            ("信任当前工作区", "", self.trust_current_workspace),
            ("编辑内核 3.0 预览（Qt）", "", self.launch_kernel3_preview),
            ("打开 .imporproj 项目", "", self.open_project),
            ("生成 .imporproj", "", self.create_project_config),
            ("保存", "Ctrl+S", self.save_file),
            ("另存为", "Ctrl+Shift+S", self.save_file_as),
            ("关闭当前标签", "Ctrl+W", self.close_current_tab),
            ("编译为 Python", "", self.compile_to_python),
            ("查找", "Ctrl+F", self.toggle_find),
            ("快速打开文件", "Ctrl+P", self.quick_open),
            ("全项目搜索", "Ctrl+Shift+F", self.global_search),
            ("全项目搜索与替换（预览/事务）", "Ctrl+Shift+H", self.project_replace),
            ("符号大纲", "Ctrl+Shift+O", self.outline),
            ("跳转定义", "F12", self.goto_definition),
            ("悬停符号信息", "Ctrl+K", self._hover_symbol),
            ("函数签名 / 参数提示", "Ctrl+Shift+Space", self.signature_help),
            ("项目符号索引", "", self.project_outline),
            ("项目引用总览", "", self.project_references),
            ("调用图", "", self.call_graph),
            ("项目诊断总览", "", self.project_diagnostics),
            ("项目级快速修复", "", self.project_quick_fix),
            ("后退", "Alt+Left", self.nav_back),
            ("前进", "Alt+Right", self.nav_forward),
            ("批量规范化编码", "", self.normalize_encodings),
            ("切换断点", "F9", self.toggle_breakpoint),
            ("运行配置", "Ctrl+Shift+F5", self.run_config),
            ("折叠/展开当前块", "点击行号左侧箭头", self.toggle_fold_here),
            ("展开全部折叠", "", self.unfold_all),
            ("保存时自动格式化", "", lambda: self._toggle_auto_format()),
            ("编辑器设置", "", self.show_settings),
            ("内部诊断（崩溃 / 被吞异常）", "", self.show_diagnostics),
            ("左右拆分视图", "Ctrl+\\", self.toggle_split_view),
            ("切换深色/浅色主题", "", self.toggle_theme),
            ("运行选中代码", "Ctrl+Shift+Return", self.run_selection),
            ("集成终端", "Ctrl+`", self.open_terminal),
            ("包管理器", "", self.open_pkg_manager),
            ("跳转到行", "Ctrl+G", self.goto_line),
            ("清空输出", "", self.clear_output),
            ("切换沙盒", "", self.toggle_sandbox),
            ("切换调试", "", self.toggle_debug),
            ("冻结/恢复变量监视", "", self.toggle_freeze),
            ("内存统计", "", lambda: self.show_bottom("mem")),
            ("重命名符号", "F2 / Ctrl+Shift+R", self.rename_symbol),
            ("撤销上次项目重构", "", self.undo_refactor),
            ("项目依赖图", "", self.show_dependency_graph),
            ("项目依赖与离线审计", "", self.show_dependency_audit),
            ("项目质量检查（类型+lint）", "", self.check_project_quality),
            ("教学大纲（12 课）", "", self.open_teaching),
            ("语法查询（关键字/内建）", "F1", self.open_syntax_reference),
            ("可能未使用的符号", "", self.show_unused_symbols),
            ("发现测试用例", "", self.discover_tests),
            ("重跑失败测试", "", self.rerun_failed_tests),
            ("测试监听开关", "", self.toggle_test_watch),
            ("查找引用", "Shift+F12", self.find_references),
            ("调用层级", "Alt+F12", self.call_hierarchy),
            ("运行当前文件测试", "F6", self.run_tests_current),
            ("底部命令条", "", lambda: self.task_entry.focus_set()),
            ("项目批量替换", "", self.project_replace),
            ("正则批量替换", "", self.regex_replace),
            ("快速修复当前行", "", self.quick_fix_smart),
            ("清除多光标", "Esc Esc", self._clear_multi_cursor),
            ("插入代码片段", "", self.insert_snippet),
            ("自动配对括号与引号开关", "", self.toggle_auto_pair),
            ("上移当前行/选区", "Alt+Up", lambda: self.current_pane() and self.current_pane()._move_lines(-1)),
            ("下移当前行/选区", "Alt+Down", lambda: self.current_pane() and self.current_pane()._move_lines(1)),
            ("删除当前行/选区", "Ctrl+Shift+K", lambda: self.current_pane() and self.current_pane()._delete_lines()),
            ("多行行尾插入", "", self.append_to_selected_lines),
            ("快速整理当前文件", "Shift+Alt+F", self.quick_fix),
            ("提取变量", "", self.extract_variable),
            ("提取函数", "", self.extract_function),
            ("移动/重命名文件（含引用预览）", "", self.move_project_file),
            ("运行时行覆盖率", "", self.show_coverage),
            ("运行并统计覆盖率", "", lambda: self.run_code(coverage=True)),
            ("生成 expect 测试", "", self.generate_expect_test),
            ("补全 import", "", self.complete_import),
            ("解释选中代码", "", self.explain_selection),
            ("AI 解释选中代码（可选）", "", self.ai_explain_selection),
            ("AI 修复选中代码（diff 预览）", "", self.ai_fix_selection),
            ("AI 生成测试（diff 预览）", "", self.ai_generate_tests),
            ("AI 助手设置（默认关闭）", "", self.show_ai_settings),
            ("切换大文件为完整编辑", "", self.convert_current_virtual_to_full),
            ("运行测试", "Ctrl+Shift+T", self.run_tests),
            ("Git 面板", "", self.open_git_panel),
            ("Git 当前行 blame", "", self.git_blame_current),
            ("Git 冲突入口", "", self.git_conflicts),
            ("Git 三方合并", "", lambda: self.open_conflict_merge(
                self.current_pane().path, None) if self.current_pane() and self.current_pane().path else self.toast("先打开冲突文件", "warn")),
            ("插件管理器", "", self.show_plugin_manager),
            ("Git 按 hunk 暂存", "", self.git_stage_hunk),
            ("多终端标签", "Ctrl+`", self.open_terminal),
            ("项目设置 .impor/settings.json", "", self.open_project_settings),
            ("高对比主题", "", lambda: self.apply_theme("hc")),
            ("安全中心（威胁模型 / 审计）", "", self.show_security_center),
            ("键盘焦点：编辑器", "Ctrl+1", lambda: self.current_pane() and self.current_pane().focus_text()),
            ("键盘焦点：资源管理器", "Ctrl+2", lambda: self.tree.focus_set()),
            ("键盘焦点：底部面板", "Ctrl+3", lambda: self._ensure_bottom() and self.task_entry.focus_set()),
            ("屏幕阅读器桥接（UIA）", "Ctrl+Alt+A", self.open_uia_bridge),
            ("标签左移", "Ctrl+Shift+PageUp", lambda: self._move_tab(-1)),
            ("标签右移", "Ctrl+Shift+PageDown", lambda: self._move_tab(1)),
            ("示例浏览器", "", self.open_example_browser),
            ("与磁盘对比", "", self.show_diff),
            ("检查更新", "", self.check_update),
            ("运行到光标", "Ctrl+F5", self.run_to_cursor),
            ("设置日志点", "Ctrl+Shift+B", self.set_logpoint),
            ("批量修复诊断", "Ctrl+.", self.quick_fix_all),
            ("下一个问题", "F8", lambda: self.goto_next_problem(1)),
            ("上一个问题", "Shift+F8", lambda: self.goto_next_problem(-1)),
            ("键位设置（keybindings.json）", "", self.open_keybindings),
            ("当前文件在右侧编辑器组打开", "", lambda: self.open_path_in_split(self.current_pane().path)
                if self.current_pane() and self.current_pane().path else self.toast("当前文件没有磁盘路径", "warn")),
            ("重建符号索引", "", lambda: (self.index.refresh(force=True), self.toast("符号索引已重建"))),
            ("关于 Impor", "", self.show_about),
        ]
        try:
            if self.plugins:
                self.plugins.load()
                cmds.extend(self.plugins.palette_commands())
                for name in self.plugins.theme_names():
                    cmds.append(("插件主题：" + name, "", lambda n=name: self.apply_plugin_theme(n)))
                for name in sorted(getattr(self.plugins, "formatters", {})):
                    cmds.append(("插件格式化：" + name, "", lambda n=name: self.apply_plugin_formatter(n)))
        except Exception:
            pass
        try:
            tasks = (self.project_settings or {}).get("tasks")
            if isinstance(tasks, dict):
                for kind in sorted(tasks.keys()):
                    cmds.append(("任务模板：" + kind, "", lambda k=kind: self._task_run(k)))
        except Exception:
            pass
        for item in EXAMPLES:
            title, code = item[0], item[1]
            fname = item[2] if len(item) > 2 else title.replace(". ", "-")
            cmds.append(("示例：" + title, "", lambda c=code, n=fname: self.open_new_tab(content=c, title=n + ".impor")))
        return CommandPalette(self, cmds)

    def _toggle_auto_format(self):
        self.auto_format = not self.auto_format
        self.toast("保存时自动格式化：" + ("开" if self.auto_format else "关"))

    def toggle_auto_pair(self):
        self.auto_pair = not bool(getattr(self, "auto_pair", True))
        self._save_config()
        self.toast("自动配对括号与引号：" + ("开" if self.auto_pair else "关"), "ok")

    def open_example_browser(self):
        ExampleBrowser(self)

    def _read_update_manifest(self):
        import json
        import urllib.request
        site = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
        manifest = os.path.join(site, "update.json")
        try:
            remote = os.environ.get("IMPOR_UPDATE_URL", "").strip()
            if remote:
                with urllib.request.urlopen(remote, timeout=6) as r:
                    return json.loads(r.read().decode("utf-8"))
            if os.path.isfile(manifest):
                with open(manifest, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            return {}
        return {}

    def _auto_update_check(self):
        """启动 7 秒后静默检查；不弹窗、不阻塞 UI，只点亮状态栏入口。"""
        def worker():
            data = self._read_update_manifest()
            if not data:
                return
            latest = str(data.get("version") or "")
            try:
                cur = tuple(int(x) for x in __version__.split("."))
                new = tuple(int(x) for x in latest.split("."))
            except Exception:
                return
            if new <= cur:
                return
            self.root.after(0, lambda: self._notify_update_available(latest))

        threading.Thread(target=worker, daemon=True).start()

    def _notify_update_available(self, latest):
        try:
            self.status_left.configure(text=f"发现新版本 {latest} · 点击更新", cursor="hand2")
            self.status_left.bind("<Button-1>", lambda e: self.check_update())
        except Exception:
            pass
        self.toast(f"发现 Impor 新版本 {latest}；状态栏点击“检查更新”安装", "warn")

    def _update_progress_window(self, latest):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("Impor 更新 " + latest)
        win.transient(self.root)
        win.geometry("420x120")
        tk.Label(win, text=f"正在下载 Impor {latest}…", bg=THEME["panel"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(14, 6))
        bar = ttk.Progressbar(win, mode="determinate", maximum=100)
        bar.pack(fill=tk.X, padx=16)
        pct = tk.Label(win, text="0%", bg=THEME["panel"], fg=THEME["muted"],
                       font=("Microsoft YaHei UI", 8))
        pct.pack(anchor="e", padx=16, pady=(2, 6))

        class _P:
            def set(_self, value):
                try:
                    bar["value"] = value
                    pct.configure(text="%d%%" % int(value))
                    win.update_idletasks()
                except Exception:
                    pass

            def close(_self):
                try:
                    win.destroy()
                except Exception:
                    pass
        return _P()

    def check_update(self):
        import json
        import urllib.request
        site = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
        manifest = os.path.join(site, "update.json")
        data = {}
        try:
            remote = os.environ.get("IMPOR_UPDATE_URL", "").strip()
            if remote:
                with urllib.request.urlopen(remote, timeout=6) as r:
                    data = json.loads(r.read().decode("utf-8"))
            elif os.path.isfile(manifest):
                with open(manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception as e:
            self.toast("检查更新失败：" + str(e)[:80])
            return
        if not data:
            self.toast("没有可用的更新清单")
            return
        latest = str(data.get("version") or __version__)
        try:
            cur = tuple(int(x) for x in __version__.split("."))
            new = tuple(int(x) for x in latest.split("."))
        except Exception:
            cur = new = (0,)
        if new <= cur:
            self.toast(f"已是最新版本 {__version__}")
            return
        minv = str(data.get("min_version") or "")
        if minv:
            try:
                if cur < tuple(int(x) for x in minv.split(".")):
                    messagebox.showerror("需要先升级", f"该更新要求最低版本 {minv}，当前 {__version__}。",
                                         parent=self.root)
                    return
            except Exception:
                pass
        vsix = data.get("vsix") or ("vscode-impor-%s.vsix" % latest)
        target = os.path.join(site, os.path.basename(vsix))
        previous = self._previous_vsix(target)
        if not messagebox.askyesno(
                "发现新版本",
                f"当前 {__version__}，可用 {latest}。\n\n现在下载并安装？", parent=self.root):
            return
        part = target + ".part"
        prog = self._update_progress_window(latest)
        try:
            url = data.get("url")
            if url:
                with urllib.request.urlopen(url, timeout=20) as r, open(part, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    got = 0
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = int(got * 100 / total)
                            prog.set(pct)
                            self.status_left.configure(text="更新下载 %d%%" % pct)
                            self.root.update_idletasks()
                self.status_left.configure(text=f"Impor {__version__}")
                prog.close()
                if data.get("sha256"):
                    import hashlib
                    h = hashlib.sha256(open(part, "rb").read()).hexdigest()
                    if h.lower() != str(data["sha256"]).lower():
                        os.remove(part)
                        messagebox.showerror("更新失败", "SHA256 校验不匹配，已丢弃安装包。", parent=self.root)
                        return
                os.replace(part, target)
            if not os.path.isfile(target):
                messagebox.showerror("更新失败", "找不到安装包：\n" + target, parent=self.root)
                return
            try:
                import subprocess
                code_cli = shutil.which("code")
                if not code_cli:
                    os.startfile(target)
                    self.toast("已打开安装包（无法检测安装结果）", "ok")
                    return
                r = subprocess.run([code_cli, "--install-extension", target],
                                   capture_output=True, timeout=90)
                if r.returncode != 0:
                    self._rollback_vsix(previous, latest)
                    return
                self.toast("已调用 VS Code 安装 " + latest, "ok")
                if messagebox.askyesno("更新完成",
                                       "VSIX 已安装。是否立即尝试重新加载 VS Code 窗口？",
                                       parent=self.root):
                    self._reload_vscode()
            except Exception as e:
                self._rollback_vsix(previous, latest)
                messagebox.showerror("更新失败", "安装失败：%s\n已尝试回滚旧版本。" % e, parent=self.root)
        except Exception as e:
            prog.close()
            messagebox.showerror("更新失败", str(e), parent=self.root)

    def _reload_vscode(self):
        """VS Code CLI 不保证支持 reload 命令；失败时如实提示手动重启。"""
        code_cli = shutil.which("code")
        if not code_cli:
            self.toast("未检测到 code 命令，请手动重启 VS Code", "warn")
            return
        try:
            r = subprocess.run([code_cli, "--command", "workbench.action.reloadWindow"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                self.toast("已请求 VS Code 重新加载窗口", "ok")
            else:
                self.toast("CLI 不支持自动重载，请手动重启 VS Code", "warn")
        except Exception as exc:
            self.toast("自动重载失败：%s；请手动重启 VS Code" % str(exc)[:60], "warn")

    def _previous_vsix(self, exclude):
        site = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
        try:
            names = [n for n in os.listdir(site) if n.startswith("vscode-impor-") and n.endswith(".vsix")]
        except Exception:
            return None
        items = []
        for n in names:
            p = os.path.join(site, n)
            if os.path.abspath(p) == os.path.abspath(exclude):
                continue
            try:
                items.append((os.path.getmtime(p), p))
            except Exception:
                pass
        return max(items)[1] if items else None

    def _rollback_vsix(self, previous, latest):
        if not previous or not os.path.isfile(previous):
            messagebox.showerror("更新失败", "安装 %s 失败，且没有可回滚的旧 VSIX。" % latest,
                                 parent=self.root)
            return
        try:
            import subprocess
            code_cli = shutil.which("code")
            if code_cli:
                r = subprocess.run([code_cli, "--install-extension", previous],
                                   capture_output=True, timeout=90)
                if r.returncode == 0:
                    self.toast("安装失败，已回滚到 " + os.path.basename(previous), "warn")
                else:
                    messagebox.showerror("回滚失败", "旧版本安装也失败：\n" + previous, parent=self.root)
            else:
                os.startfile(previous)
        except Exception as e:
            messagebox.showerror("回滚失败", str(e), parent=self.root)

    def show_diagnostics(self):
        """4.7.3：内部诊断面板。

        493 处 except 里被吞掉的异常以前只有写日志、没人看。这里把
        「哪里最常吞」「崩溃现场」「最近记录」摊在一个窗口里，
        并给出一键打开日志目录，让编辑器出问题时能自证而不是靠猜。
        """
        from .observability import swallowed_log_path, swallowed_tail, swallowed_counts
        from .stability import crash_log_path, crash_log_tail
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("内部诊断 · 崩溃 / 被吞异常")
        win.geometry("940x600")
        win.transient(self.root)
        head = tk.Frame(win, bg=THEME["panel_head"], height=34)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        tk.Label(head, text="内部诊断", bg=THEME["panel_head"], fg=THEME["fg"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT, padx=12)
        body = tk.Frame(win, bg=THEME["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt = tk.Text(body, wrap="none", bg=THEME["editor"], fg=THEME["fg"],
                      insertbackground=THEME["fg"], font=(self.mono, 9), relief="flat")
        sb = tk.Scrollbar(body, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lines = []
        try:
            counts = swallowed_counts()
            lines.append("=== 被吞异常高频区域（累计 %d 条）===" % sum(counts.values()))
            if counts:
                for area, n in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
                    lines.append("  %6d  %s" % (n, area))
            else:
                lines.append("  （无记录）")
            lines.append("")
            cp = crash_log_path()
            lines.append("=== 崩溃现场（%s）===" % cp)
            lines.extend(crash_log_tail(20) or ["  （无）"])
            lines.append("")
            lines.append("=== 最近被吞异常（%s）===" % swallowed_log_path())
            lines.extend(swallowed_tail(120) or ["  （无）"])
        except Exception as exc:
            lines.append("诊断信息读取失败：%r" % (exc,))
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        foot = tk.Frame(win, bg=THEME["panel"])
        foot.pack(fill=tk.X, padx=8, pady=(0, 8))

        def open_dir():
            try:
                os.startfile(os.path.dirname(swallowed_log_path()))
            except Exception:
                pass

        FlatButton(foot, "打开日志目录", command=open_dir, width=112).pack(side=tk.LEFT)
        FlatButton(foot, "关闭", command=win.destroy, width=72).pack(side=tk.RIGHT)

    def show_settings(self):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("编辑器设置")
        win.transient(self.root)
        try:
            _ax, _ay, _aw, ah = work_area(self.root)
            _h = max(620, min(900, ah - 32))
        except Exception:
            _h = 820
        win.geometry("500x%d" % _h)
        size = tk.IntVar(value=self.font_size)
        indent = tk.IntVar(value=self.indent_width)
        large_kb = tk.IntVar(value=max(256, int(getattr(self, "large_file_kb", 2000))))
        large_mode = tk.StringVar(value={"auto": "自动分块", "full": "始终完整打开",
                                         "ask": "打开时询问"}.get(
            getattr(self, "large_file_mode", "auto"), "自动分块"))
        fmt = tk.BooleanVar(value=self.auto_format)
        save = tk.BooleanVar(value=self.autosave)
        wrap = tk.BooleanVar(value=self.word_wrap)
        pair = tk.BooleanVar(value=bool(getattr(self, "auto_pair", True)))
        mini = tk.BooleanVar(value=self.show_minimap)
        theme = tk.StringVar(value={"hc": "高对比", "light": "浅色"}.get(self.theme_name, "深色"))
        family = tk.StringVar(value=self.mono)
        project_save = tk.BooleanVar(value=False)
        reader = tk.BooleanVar(value=bool(getattr(self, "screen_reader", False)))
        iso = tk.BooleanVar(value=bool(getattr(self, "plugin_isolation", True)))
        families = ["Cascadia Code", "Consolas", "JetBrains Mono", "Sarasa Mono SC",
                    "Microsoft YaHei UI", "Courier New"]
        if self.mono not in families:
            families.insert(0, self.mono)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="主题", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        ttk.Combobox(row, values=("深色", "浅色", "高对比"), textvariable=theme, width=12,
                     state="readonly").pack(side=tk.LEFT)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="字体", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        ttk.Combobox(row, values=families, textvariable=family, width=18,
                     state="readonly").pack(side=tk.LEFT)
        for text, var, lo, hi in (("字号", size, 9, 18), ("缩进空格", indent, 2, 8)):
            row = tk.Frame(win, bg=THEME["panel"])
            row.pack(fill=tk.X, padx=18, pady=6)
            tk.Label(row, text=text, bg=THEME["panel"], fg=THEME["fg"], width=10,
                     anchor="w").pack(side=tk.LEFT)
            ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=8).pack(side=tk.LEFT)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="大文件阈值", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=256, to=65536, increment=256, textvariable=large_kb,
                    width=8).pack(side=tk.LEFT)
        tk.Label(row, text="KB（超过后按下方模式处理）", bg=THEME["panel"],
                 fg=THEME["muted"], font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(6, 0))
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="大文件打开", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        ttk.Combobox(row, values=("自动分块", "始终完整打开", "打开时询问"),
                     textvariable=large_mode, width=18, state="readonly").pack(side=tk.LEFT)
        tk.Label(row, text="自动分块可跨块查找/替换", bg=THEME["panel"],
                 fg=THEME["muted"], font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(6, 0))
        for text, var in (("保存时自动格式化", fmt), ("30 秒自动保存", save),
                          ("自动换行", wrap), ("自动配对括号与引号", pair),
                          ("显示缩略图", mini),
                          ("启动时打开屏幕阅读器桥接（UIA）", reader),
                          ("插件独立进程隔离（重启生效）", iso)):
            tk.Checkbutton(win, text=text, variable=var, bg=THEME["panel"], fg=THEME["fg"],
                           selectcolor=THEME["editor"], activebackground=THEME["panel"],
                           activeforeground=THEME["fg"]).pack(anchor="w", padx=18, pady=4)
        tk.Checkbutton(win, text="保存到项目 .impor/settings.json（覆盖全局）",
                       variable=project_save, bg=THEME["panel"], fg=THEME["fg"],
                       selectcolor=THEME["editor"], activebackground=THEME["panel"],
                       activeforeground=THEME["fg"]).pack(anchor="w", padx=18, pady=4)

        sync_on = tk.BooleanVar(value=bool(getattr(self, "sync_enabled", False)))
        sync_path = tk.StringVar(value=getattr(self, "sync_dir", "") or "")
        sync_strategy = tk.StringVar(value={"local": "本地优先", "remote": "远端优先"}.get(
            getattr(self, "sync_strategy", "newer"), "较新者优先"))
        tk.Checkbutton(win, text="启用设置同步（云盘目录，跨设备）",
                       variable=sync_on, bg=THEME["panel"], fg=THEME["fg"],
                       selectcolor=THEME["editor"], activebackground=THEME["panel"],
                       activeforeground=THEME["fg"]).pack(anchor="w", padx=18, pady=(8, 2))
        srow = tk.Frame(win, bg=THEME["panel"])
        srow.pack(fill=tk.X, padx=18, pady=2)
        tk.Label(srow, text="同步目录", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        tk.Entry(srow, textvariable=sync_path, width=24, bg=THEME["editor"],
                 fg=THEME["fg"], insertbackground=THEME["accent"], relief="flat"
                 ).pack(side=tk.LEFT, padx=(0, 4))
        FlatButton(srow, "选择", command=lambda: sync_path.set(
            filedialog.askdirectory(initialdir=getattr(self, "sync_dir", "") or self.workspace)
            or sync_path.get()), width=52).pack(side=tk.LEFT, padx=2)
        ttk.Combobox(srow, values=("较新者优先", "本地优先", "远端优先"),
                     textvariable=sync_strategy, width=11, state="readonly").pack(
            side=tk.LEFT, padx=(6, 0))
        srow2 = tk.Frame(win, bg=THEME["panel"])
        srow2.pack(fill=tk.X, padx=18, pady=(0, 4))
        sync_status = tk.Label(srow2, text="", bg=THEME["panel"], fg=THEME["muted"],
                               anchor="w", font=("Microsoft YaHei UI", 8))
        sync_status.pack(side=tk.LEFT)

        def _sync_apply_vars():
            self.sync_enabled = bool(sync_on.get())
            self.sync_dir = os.path.abspath(os.path.expanduser(sync_path.get().strip())) \
                if sync_path.get().strip() else getattr(self, "sync_dir", "")
            self.sync_strategy = {"本地优先": "local", "远端优先": "remote"}.get(
                i18n.untranslate(sync_strategy.get()), "newer")

        def do_push():
            _sync_apply_vars()
            try:
                from .editor_sync import push_snapshot
                self._save_config()
                p = push_snapshot(self.sync_dir, self.cfg, self.project_settings,
                                  __version__, strategy=self.sync_strategy)
                sync_status.configure(text="已上传：" + os.path.basename(p), fg=THEME["green"])
            except Exception as exc:
                sync_status.configure(text="上传失败：" + str(exc)[:80], fg=THEME["red"])

        def do_pull():
            _sync_apply_vars()
            try:
                from .editor_sync import pull_snapshot
                cfg2, proj2, _snap = pull_snapshot(self.sync_dir, self.cfg,
                                                  self.project_settings, __version__,
                                                  strategy=self.sync_strategy)
                self.cfg.update(cfg2)
                self.project_settings.update(proj2)
                size.set(int(cfg2.get("font_size", self.font_size)))
                indent.set(int(cfg2.get("indent_width", self.indent_width)))
                wrap.set(bool(cfg2.get("word_wrap", self.word_wrap)))
                mini.set(bool(cfg2.get("minimap", self.show_minimap)))
                family.set(str(cfg2.get("font_family", self.mono)))
                large_kb.set(max(256, int(cfg2.get("large_file_kb", self.large_file_kb))))
                large_mode.set({"auto": "自动分块", "full": "始终完整打开",
                                "ask": "打开时询问"}.get(
                    cfg2.get("large_file_mode", self.large_file_mode), "自动分块"))
                theme.set({"hc": "高对比", "light": "浅色"}.get(
                    cfg2.get("theme", self.theme_name), "深色"))
                apply(close=False)
                sync_status.configure(text="已拉取并应用远端设置", fg=THEME["green"])
            except Exception as exc:
                sync_status.configure(text="拉取失败：" + str(exc)[:80], fg=THEME["red"])

        FlatButton(srow2, "立即上传", command=do_push, width=72).pack(side=tk.LEFT, padx=(0, 4))
        FlatButton(srow2, "立即拉取", command=do_pull, width=72).pack(side=tk.LEFT)

        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="AI 助手", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text="默认关闭 · 可选 API", bg=THEME["panel"], fg=THEME["muted"],
                 font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        FlatButton(row, "AI 设置", command=self.show_ai_settings, width=72).pack(side=tk.LEFT, padx=8)

        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=18, pady=6)
        tk.Label(row, text="工作区", bg=THEME["panel"], fg=THEME["fg"], width=10,
                 anchor="w").pack(side=tk.LEFT)
        tk.Entry(row, textvariable=tk.StringVar(value=self.workspace), state="readonly",
                 width=26, bg=THEME["editor"], fg=THEME["muted"], relief="flat"
                 ).pack(side=tk.LEFT, padx=(0, 6))
        FlatButton(row, "选择", command=lambda: (win.destroy(), self.open_workspace()),
                   width=52).pack(side=tk.LEFT)

        def restore_defaults():
            size.set(11)
            indent.set(4)
            fmt.set(True)
            save.set(True)
            wrap.set(False)
            pair.set(True)
            mini.set(True)
            large_kb.set(2000)
            large_mode.set("自动分块")
            theme.set("深色")
            family.set("Cascadia Code")

        def apply(close=True):
            self.font_size = max(9, min(18, int(size.get())))
            self.indent_width = max(2, min(8, int(indent.get())))
            self.auto_format = bool(fmt.get())
            self.autosave = bool(save.get())
            self.word_wrap = bool(wrap.get())
            self.auto_pair = bool(pair.get())
            self.show_minimap = bool(mini.get())
            self.screen_reader = bool(reader.get())
            self.plugin_isolation = bool(iso.get())
            try:
                kb = int(large_kb.get() or 2000)
            except Exception:
                kb = int(getattr(self, "large_file_kb", 2000) or 2000)
            self.large_file_kb = max(256, min(65536, kb))
            self.large_file_mode = {"自动分块": "auto", "始终完整打开": "full",
                                    "打开时询问": "ask"}.get(
                                        i18n.untranslate(large_mode.get()), "auto")
            self.mono = family.get().strip() or self.mono
            new_theme = {"浅色": "light", "高对比": "hc"}.get(
                i18n.untranslate(theme.get()), "dark")
            if new_theme != self.theme_name:
                self.apply_theme(new_theme)
            else:
                for pane in self.panes:
                    pane.text.configure(font=(self.mono, self.font_size),
                                        wrap="word" if self.word_wrap else "none")
                    pane._configure_tags()
                    pane.highlight()
                    pane.redraw_lineno()
                    if self.show_minimap and not pane.minimap.winfo_manager():
                        pane.minimap.pack(side=tk.RIGHT, fill=tk.Y, before=pane.scroll)
                    elif not self.show_minimap and pane.minimap.winfo_manager():
                        pane.minimap.pack_forget()
                for w in (self.out, self.problems, self.mem_text):
                    try:
                        w.configure(font=(self.mono, 10))
                    except Exception:
                        pass
            _sync_apply_vars()
            self._save_config()
            if project_save.get():
                self.save_current_project_settings()
            if close:
                win.destroy()
                self.toast("设置已应用", "ok")

        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(pady=14)
        FlatButton(row, "应用", command=apply, kind="primary", width=84).pack(side=tk.LEFT, padx=4)
        FlatButton(row, "取消", command=win.destroy, width=84).pack(side=tk.LEFT, padx=4)
        FlatButton(row, "恢复默认", command=restore_defaults, width=84).pack(side=tk.LEFT, padx=4)
        FlatButton(row, "打开配置", command=lambda: os.startfile(CONFIG_PATH), width=84
                   ).pack(side=tk.LEFT, padx=4)
        i18n.retranslate(win)


    def show_about(self):
        AboutDialog(self)

    def open_uia_bridge(self):
        """打开原生 EDIT 屏幕阅读器桥接；同一时间只保留一个。"""
        try:
            if self._uia_bridge is not None and self._uia_bridge.winfo_exists():
                self._uia_bridge.lift()
                self._uia_bridge.focus_set()
                return self._uia_bridge
        except Exception:
            self._uia_bridge = None
        try:
            from .editor_uia import UIABridge
            self._uia_bridge = UIABridge(self)
            self.toast("可访问性桥接已打开：Ctrl+Alt+A 可再次聚焦", "ok")
            return self._uia_bridge
        except Exception as exc:
            self.toast("可访问性桥接打开失败：" + str(exc)[:100], "warn")
            return None

    def toggle_fold_here(self):
        pane = self.current_pane()
        if pane:
            pane._toggle_fold()

    def unfold_all(self):
        pane = self.current_pane()
        if pane:
            pane._folded.clear()
            pane._reapply_folds()
            pane.redraw_lineno()
            self.toast("已展开当前文件全部折叠")

    def toggle_breakpoint(self):
        pane = self.current_pane()
        if not pane:
            return
        try:
            ln = int(pane.text.index("insert").split(".")[0])
        except Exception:
            return
        if ln in pane.breakpoints:
            pane.breakpoints.discard(ln)
            self.toast(f"已取消断点：第 {ln} 行")
        else:
            pane.breakpoints.add(ln)
            self.toast(f"已设断点：第 {ln} 行", "ok")
        pane.redraw_lineno()
        self._refresh_bp_list()

    def set_logpoint(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        ln = int(pane.text.index("insert").split(".")[0])
        expr = simpledialog.askstring("日志点", f"第 {ln} 行每次执行时记录：",
                                      initialvalue=pane.bp_logs.get(ln, ""), parent=self.root)
        if expr is None:
            return
        if not expr.strip():
            pane.bp_logs.pop(ln, None)
            self.toast("已取消日志点")
            return
        pane.bp_logs[ln] = expr.strip()
        pane.breakpoints.add(ln)
        pane.redraw_lineno()
        self.toast(f"日志点：第 {ln} 行 → {expr.strip()}", "ok")

    def run_to_cursor(self):
        pane = self.current_pane()
        if not pane:
            return
        ln = int(pane.text.index("insert").split(".")[0])
        pane.breakpoints.add(ln)
        pane.redraw_lineno()
        self.toast(f"运行到第 {ln} 行", "ok")
        self.run_code()

    def debug_step(self, mode):
        interp = getattr(self, "_dbg_interp", None)
        if not interp:
            self.toast("先按 F5 运行到断点，再单步")
            return
        interp.step_mode = mode
        if mode == "over":
            interp._dbg_step_target = interp._dbg_depth()
        elif mode == "out":
            interp.step_mode = "out"
            interp._dbg_step_target = interp._dbg_depth() - 1
        if getattr(self, "_dbg_paused", False):
            self._dbg_gate.set(self._dbg_gate.get() + 1)
        self.toast("调试：" + {"over": "步过", "into": "步入", "out": "步出"}[mode])

    # ---------- 快捷键 ----------
    def _build_shortcuts(self):
        bind = self.root.bind
        # 可访问性：不依赖鼠标在编辑器/资源管理器/底部命令条之间移动焦点
        bind("<Control-Key-1>", lambda e: self.focus_editor())
        bind("<Control-Key-2>", lambda e: self.tree.focus_set())
        bind("<Control-Key-3>", lambda e: (self._ensure_bottom(), self.task_entry.focus_set()))
        bind("<Control-Key-4>", lambda e: self.show_bottom("problems"))
        bind("<Control-Key-5>", lambda e: self.open_palette())
        bind("<Control-n>", lambda e: self.open_new_tab())
        bind("<Control-o>", lambda e: self.open_file())
        bind("<Control-s>", lambda e: self.save_file())
        bind("<Control-Shift-S>", lambda e: self.save_file_as())
        bind("<Control-w>", lambda e: self.close_current_tab())
        bind("<Control-Return>", lambda e: self.run_code())
        bind("<Control-Shift-Return>", lambda e: self.run_selection())
        bind("<Control-comma>", lambda e: self.show_settings())
        bind("<Control-backslash>", lambda e: self.toggle_split_view())
        bind("<Control-grave>", lambda e: self.open_terminal())
        bind("<F5>", lambda e: self.run_code())
        bind("<F1>", lambda e: self.open_syntax_reference())
        bind("<Control-Shift-F5>", lambda e: self.run_config())
        bind("<Control-Alt-a>", lambda e: self.open_uia_bridge())
        bind("<Control-b>", lambda e: self.build_exe())
        bind("<Control-Shift-P>", lambda e: self.open_palette())
        bind("<Control-f>", lambda e: self.toggle_find())
        bind("<Control-g>", lambda e: self.goto_line())
        bind("<F12>", lambda e: self.goto_definition())
        bind("<Control-k>", lambda e: self._hover_symbol())
        bind("<Control-Shift-space>", lambda e: self.signature_help())
        bind("<Control-Shift-E>", lambda e: self.toggle_sidebar())
        bind("<Control-p>", lambda e: self.quick_open())
        bind("<Control-Shift-F>", lambda e: self.global_search())
        bind("<Control-Shift-H>", lambda e: self.project_replace())
        bind("<Control-Shift-O>", lambda e: self.outline())
        bind("<F9>", lambda e: self.toggle_breakpoint())
        bind("<Control-F5>", lambda e: self.run_to_cursor())
        bind("<Control-Shift-B>", lambda e: self.set_logpoint())
        bind("<Control-period>", lambda e: self.quick_fix_all())
        bind("<F10>", lambda e: self.debug_step("over"))
        bind("<F11>", lambda e: self.debug_step("into"))
        bind("<Shift-F11>", lambda e: self.debug_step("out"))
        bind("<Control-plus>", lambda e: self._zoom(1))
        bind("<Control-minus>", lambda e: self._zoom(-1))
        bind("<Control-Shift-R>", lambda e: self.rename_symbol())
        bind("<F2>", lambda e: self.rename_symbol())
        bind("<Shift-F12>", lambda e: self.find_references())
        bind("<Alt-F12>", lambda e: self.call_hierarchy())
        bind("<Alt-Left>", lambda e: self.nav_back())
        bind("<Alt-Right>", lambda e: self.nav_forward())
        bind("<F6>", lambda e: self.run_tests_current())
        bind("<Shift-Alt-F>", lambda e: self.quick_fix())
        bind("<Control-Shift-T>", lambda e: self.run_tests())
        bind("<Control-Shift-Prior>", lambda e: self._move_tab(-1))
        bind("<Control-Shift-Next>", lambda e: self._move_tab(1))
        bind("<Control-Shift-K>", lambda e: self.current_pane() and self.current_pane()._delete_lines())
        bind("<Alt-Up>", lambda e: self.current_pane() and self.current_pane()._move_lines(-1))
        bind("<Alt-Down>", lambda e: self.current_pane() and self.current_pane()._move_lines(1))
        bind("<F8>", lambda e: self.goto_next_problem(1))
        bind("<Shift-F8>", lambda e: self.goto_next_problem(-1))
        bind("<Alt-Return>", lambda e: self.quick_fix_smart())

    def toggle_split_view(self):
        if self.split_frame is not None:
            dirty = [p for p in self.group_panes("split") if getattr(p, "_dirty", False)]
            if dirty and not messagebox.askyesno(
                    "关闭编辑器组", "第二组有 %d 个未保存文件，仍要关闭吗？" % len(dirty)):
                return
            self._destroy_split_group()
            main = self.group_panes("main")
            if main:
                self.active_pane = None
                self.select_pane(main[-1])
            return
        self.ensure_split_group()
        self.open_new_tab(group="split", title="组 2 · 未命名")
        self.toast("已创建独立编辑器组 2：在资源管理器打开文件会进入当前组", "ok")

    def _sync_live(self, src):
        if getattr(self, "_sync_live_busy", False) or not self.split_pane:
            return
        if src is self.active_pane:
            peer = self.split_pane
        elif src is self.split_pane:
            peer = self.active_pane
        else:
            return
        if not src.path or not peer.path:
            return
        if os.path.abspath(src.path) != os.path.abspath(peer.path):
            return
        self._sync_live_busy = True
        try:
            idx = peer.text.index("insert")
            yv = peer.text.yview()[0]
            peer._suspend = True
            peer.text.delete("1.0", "end")
            peer.text.insert("1.0", src.text.get("1.0", "end-1c"))
            peer.text.edit_modified(False)
            peer._suspend = False
            try:
                peer.text.mark_set("insert", idx)
            except Exception:
                peer.text.mark_set("insert", "1.0")
            peer.text.yview_moveto(yv)
            peer.redraw_lineno()
        finally:
            self._sync_live_busy = False

    def _sync_split(self):
        src = self.current_pane()
        if src and self.split_pane:
            self.split_pane.text.delete("1.0", "end")
            self.split_pane.text.insert("1.0", src.text.get("1.0", "end-1c"))
            self.split_pane.path = src.path
            self.split_pane.highlight()

    def _sync_split_back(self):
        src = self.split_pane
        dst = self.current_pane()
        if src and dst:
            dst.text.delete("1.0", "end")
            dst.text.insert("1.0", src.text.get("1.0", "end-1c"))
            dst.path = src.path
            dst.highlight()
            self.mark_dirty(dst)
            self.toast("已从右侧同步到左侧")

    def _open_split_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="在右侧打开文件", initialdir=ROOT_DIR,
            filetypes=[("Impor 文件", "*.impor"), ("所有文件", "*.*")])
        if not path or not self.split_pane:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as exc:
            self.toast("打开失败：" + str(exc), "err")
            return
        self.split_pane.text.delete("1.0", "end")
        self.split_pane.text.insert("1.0", content)
        self.split_pane.path = os.path.abspath(path)
        self.split_pane.highlight()

    def apply_plugin_theme(self, name):
        if not self.plugins or name not in getattr(self.plugins, "themes", {}):
            self.toast("插件主题不存在：" + str(name), "err")
            return
        self.theme_name = "plugin:" + name
        THEME.clear()
        THEME.update(DARK_THEME)
        THEME.update(self.plugins.themes[name])
        self._rebuild_ui()
        self.toast("插件主题：" + name, "ok")

    def apply_plugin_formatter(self, name):
        fn = getattr(self.plugins, "formatters", {}).get(name) if self.plugins else None
        if not callable(fn):
            self.toast("插件格式化器不存在：" + str(name), "err")
            return
        pane = self.current_pane()
        if not pane:
            return
        try:
            src = pane.text.get("1.0", "end-1c")
            out = fn(src)
            if not isinstance(out, str):
                raise ValueError("格式化器必须返回字符串")
            pane.text.delete("1.0", "end")
            pane.text.insert("1.0", out)
            pane.highlight()
            self.mark_dirty(pane)
            self.toast("已应用插件格式化：" + name, "ok")
        except Exception as exc:
            self.toast("插件格式化失败：" + str(exc)[:80], "err")

    def toggle_theme(self):
        self.apply_theme({"dark": "light", "light": "hc"}.get(self.theme_name, "dark"))

    def apply_theme(self, name):
        if name == self.theme_name:
            return
        self.theme_name = name
        THEME.clear()
        if name == "hc":
            THEME.update(HIGH_CONTRAST_THEME)
        elif name == "light":
            THEME.update(LIGHT_THEME)
        elif name.startswith("plugin:") and self.plugins:
            THEME.update(DARK_THEME)
            THEME.update(self.plugins.themes.get(name.split(":", 1)[1], {}))
        else:
            THEME.update(DARK_THEME)
        self._rebuild_ui()

    def _rebuild_ui(self):
        tabs = []
        for pane in self.panes:
            tabs.append((pane.path, pane.text.get("1.0", "end-1c"),
                         pane.tab_title, set(getattr(pane, "breakpoints", set())),
                         pane.text.index("insert"), pane.text.yview()[0]))
        active = 0
        try:
            active = self.panes.index(self.active_pane)
        except Exception:
            pass
        for w in list(self.root.winfo_children()):
            if w.winfo_class() != "Toplevel":
                w.destroy()
        self.panes = []
        self.active_pane = None
        self.split_frame = None
        self.split_pane = None
        self.split_tabstrip = None
        self.split_pagehost = None
        self._find_visible = False
        for attr in ("_find_bar", "_find_entry"):
            if hasattr(self, attr):
                delattr(self, attr)
        self._build_styles()
        self._build_toolbar()
        self._build_trust_bar()
        self._build_main()
        self._build_status()
        self._build_shortcuts()
        self._apply_workspace_trust()
        self._ensure_bottom()
        for i, (path, content, title, bps, cursor, yview) in enumerate(tabs):
            pane = self.open_new_tab(content=content, title=title, path=path)
            pane.breakpoints.update(bps)
            try:
                pane.text.mark_set("insert", cursor)
                pane.text.yview_moveto(yview)
            except Exception:
                pass
        if tabs:
            try:
                self.select_pane(self.panes[min(active, len(self.panes) - 1)])
            except Exception:
                pass
        self._refresh_plugin_ui()
        self.toast("主题：" + {"light": "浅色", "hc": "高对比"}.get(self.theme_name, "深色"))

    def toggle_sidebar(self):
        try:
            if self.paned.pane(self.sidebar_ref).winfo_ismapped():
                self.paned.forget(self.sidebar_ref)
            else:
                self.paned.add(self.sidebar_ref, before=self.center, minsize=150, width=210)
        except Exception:
            pass

    def focus_editor(self):
        pane = self.current_pane()
        if pane is None:
            self.open_new_tab()
            return None
        try:
            pane.focus_text()
        except Exception:
            try:
                pane.text.focus_set()
            except Exception:
                pass
        return pane

    def _zoom(self, delta):
        self.font_size = max(9, min(18, self.font_size + delta))
        for pane in self.panes:
            pane.text.configure(font=(self.mono, self.font_size))
            pane._configure_tags()
            pane.highlight()

    # ---------- 关闭 ----------
    def on_close(self):
        dirty = [p for p in self.panes if p._dirty]
        # 4.7.3：先把现场写进恢复副本，再问是否退出。用户误点「是」也能还原。
        try:
            if dirty:
                self._flush_recovery("close")
        except Exception as exc:
            note_swallowed("editor.close.recovery", exc)
        if dirty:
            if not messagebox.askyesno("退出", f"有 {len(dirty)} 个文件未保存，仍要退出吗？\n（已写入恢复副本，下次可从「恢复未保存文件」找回）"):
                return
        self._save_config()
        self.stop_index_watcher()
        try:
            if self.plugins:
                self.plugins.shutdown()
        except Exception:
            pass
        self.root.destroy()


def _reveal_root(root):
    """过场结束：显示主窗口并补一次深色标题栏设置。"""
    try:
        root.update_idletasks()
        enable_dwm(root)          # 先给隐藏窗口设深色标题栏，避免显示后再刷一次
        root.deiconify()
        root.update_idletasks()
        root.update()             # 处理首次暴露 / 布局事件，避免露出空白编辑区
        root.lift()
    except Exception:
        pass
    root.after(120, lambda: (root.focus_force(), enable_dwm(root)))
    root.after(400, lambda: enable_dwm(root))


_split_modules = (editor_pane, editor_dialogs, editor_groups, editor_panels, editor_runtime)
_split_ns = {k: v for k, v in globals().items() if k != "__builtins__"}
for _m in _split_modules:
    _m.__dict__.update(_split_ns)


def _editor_smoke(app, root):
    """4.7.3：无交互冒烟——新建 → 写盘 → 回读 → 恢复副本，验证编辑器基本链路。

    用 `IMPOR_EDITOR_SMOKE=1` 触发；返回进程退出码（0 通过）。
    步骤轨迹写临时目录 impor-editor-smoke-trace.txt（卡住时能指出卡在哪）。
    """
    steps = []
    trace_path = os.path.join(tempfile.gettempdir(), "impor-editor-smoke-trace.txt")

    def trace(msg):
        try:
            with open(trace_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("%s %.3f %s\n" % (time.strftime("%H:%M:%S"), time.time() % 60, msg))
        except Exception:
            pass

    trace("start")
    tmpdir = tempfile.mkdtemp(prefix="impor-editor-smoke-")
    tmp = os.path.join(tmpdir, "smoke.impor")
    code = 1
    try:
        # 崩溃恢复闭环：用 IMPOR_EDITOR_EXPECT_RESTORE=<标记> 让冒烟先跑「重开恢复」
        expect = (os.environ.get("IMPOR_EDITOR_EXPECT_RESTORE") or "").strip()
        if expect:
            trace("restore-start")
            app.restore_recovery()
            hit = None
            for pn in list(app.panes):
                try:
                    body = pn.text.get("1.0", "end-1c")
                except Exception:
                    continue
                if expect in body:
                    hit = pn
                    break
            if hit is None:
                raise RuntimeError("重开后未找回含 %s 的标签" % expect)
            if not getattr(hit, "path", None):
                raise RuntimeError("恢复后未接回原文件路径")
            steps.append("restore")
            trace("restore")
        pane = app.open_new_tab(content='print("编辑器冒烟")\n', title="smoke.impor",
                                path=tmp, force_full=True)
        if pane is None:
            raise RuntimeError("open_new_tab 返回 None")
        steps.append("new-tab")
        trace("new-tab")
        app.save_file()
        trace("saved")
        with open(tmp, encoding="utf-8") as f:
            got = f.read()
        if "编辑器冒烟" not in got:
            raise RuntimeError("保存后回读内容缺失（%d 字节）" % len(got))
        steps.append("save-readback")
        trace("save-readback")
        pane._dirty = True
        if app._flush_recovery("smoke") < 1:
            raise RuntimeError("恢复副本未写入")
        # 恢复副本里必须真有刚才的正文，否则「崩溃恢复」只是空文件
        rec = os.path.join(app._recovery_path(), "recover_%s.impor" % id(pane))
        with open(rec, encoding="utf-8") as f:
            if "编辑器冒烟" not in f.read():
                raise RuntimeError("恢复副本内容与编辑区不一致")
        steps.append("recovery")
        trace("recovery")
        # 编码保真：BOM + CRLF 文件 保存后必须字节一致（不能被悄悄改写）
        p2 = os.path.join(tmpdir, "crlf.impor")
        with open(p2, "wb") as f:
            f.write(b"\xef\xbb\xbf" + "var a = 1\r\nprint(a)\r\n".encode("utf-8"))
        before = open(p2, "rb").read()
        trace("crlf-written")
        pane2 = app.open_new_tab(path=p2, force_full=True)
        trace("crlf-opened")
        if pane2 is None:
            raise RuntimeError("打开 CRLF 文件失败")
        if not getattr(pane2, "_bom", False):
            raise RuntimeError("未识别 BOM")
        if getattr(pane2, "_newline", "LF") != "CRLF":
            raise RuntimeError("未识别 CRLF")
        pane2._dirty = True
        app.save_file()
        trace("crlf-saved")
        after = open(p2, "rb").read()
        if after != before:
            raise RuntimeError("BOM/CRLF 往返被改写（%d → %d 字节）"
                               % (len(before), len(after)))
        steps.append("encoding-roundtrip")
        trace("encoding-roundtrip")
        # 定时器链自愈：一次 tick 抛异常后必须仍被重排，否则自动保存会永久失效
        hits = {"n": 0}

        def _boom():
            hits["n"] += 1
            if hits["n"] == 1:
                raise RuntimeError("smoke-after-boom")

        app._after_safe(150, _boom)
        deadline = time.time() + 1.5
        while time.time() < deadline and hits["n"] < 2:
            try:
                root.update()
            except Exception:
                break
            time.sleep(0.02)
        if hits["n"] < 2:
            raise RuntimeError("定时器链未自愈（只触发 %d 次）" % hits["n"])
        steps.append("after-chain")
        trace("after-chain")
        # 可观测入口：诊断面板必须能打开且不阻塞（否则「被吞异常」等于没人看）
        app.show_diagnostics()
        diag_win = None
        for ch in app.root.winfo_children():
            if isinstance(ch, tk.Toplevel) and "诊断" in (ch.title() or ""):
                diag_win = ch
                break
        if diag_win is None:
            raise RuntimeError("诊断面板未能打开")
        diag_win.destroy()
        steps.append("diagnostics")
        trace("diagnostics")
        print("SMOKE PASS: " + ",".join(steps), flush=True)
        trace("pass")
        code = 0
    except Exception as exc:
        print("SMOKE FAIL: %s: %s" % (type(exc).__name__, exc), flush=True)
        try:
            import traceback
            traceback.print_exc()
        except Exception:
            pass
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return code


def _editor_crash_drill(app, root, marker):
    """4.7.3：崩溃演练——做一个脏标签，写恢复副本后喊 ARMED，等外部强杀。

    配合 tests/test_editor_headless.py：外部 kill -9 后检查 .impor_recovery
    里仍有含 marker 的正文，再跑 IMPOR_EDITOR_EXPECT_RESTORE 验证重开能找回。
    自愈超时 90s（万一没人来杀，不会留僵尸进程）。
    """
    trace_path = os.path.join(tempfile.gettempdir(), "impor-editor-drill-trace.txt")

    def trace(msg):
        try:
            with open(trace_path, "a", encoding="utf-8", newline="\n") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass
    ddir = tempfile.mkdtemp(prefix="impor-drill-")
    dpath = os.path.join(ddir, "drill.impor")

    def arm():
        try:
            with open(dpath, "w", encoding="utf-8", newline="\n") as f:
                f.write("var a = 0\n")
            pane = app.open_new_tab(path=dpath, force_full=True)
            pane.text.delete("1.0", "end")
            pane.text.insert("1.0", "// %s\nvar a = 1\n" % marker)
            pane._dirty = True
            written = app._flush_recovery("drill")
            trace("armed written=%d rdir=%s attr=%s ROOT_DIR=%s file=%s appmod=%s"
                  % (written, app._recovery_path(), getattr(app, "_recovery_dir", None),
                     ROOT_DIR, __file__, type(app).__module__))
            print("DRILL ARMED %s PATH %s RDIR %s" % (marker, dpath, app._recovery_path()), flush=True)
        except Exception as exc:
            trace("arm-fail %r" % (exc,))
            print("DRILL FAIL %r" % (exc,), flush=True)

    root.after(1500, arm)
    root.after(90000, root.destroy)


def main():
    enable_dpi_awareness()
    # 4.7.3：非 Tk 回调（后台线程 / 主线程兜底）的崩溃也进 crash.log
    try:
        from .stability import install_crash_hooks
        install_crash_hooks()
    except Exception:
        pass
    try:
        root = tk.Tk()
    except Exception as e:
        print(f"错误：无法创建窗口（{e}）。请安装 tkinter：conda install tk")
        return 1
    # 4.1：Tk 回调里的异常不再只打一行 traceback 就消失——写 crash.log，留现场。
    from .stability import write_crash_log

    def _tk_exception(exc_type, exc_value, exc_tb):
        try:
            path = write_crash_log(exc_value, where="editor.callback")
            if path:
                print("Impor 内部错误现场已写入 %s" % path, file=sys.stderr)
        except Exception:
            pass
        try:
            import traceback
            traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    root.report_callback_exception = _tk_exception
    # 4.7.3：崩溃演练（IMPOR_EDITOR_CRASH_DRILL=<标记>）——写出恢复副本后等待被强杀
    drill = (os.environ.get("IMPOR_EDITOR_CRASH_DRILL") or "").strip()
    if drill:
        root.withdraw()
        app = App(root)
        _editor_crash_drill(app, root, drill)
        root.mainloop()
        return 0
    # 4.7.3：无交互冒烟（IMPOR_EDITOR_SMOKE=1）——不显示窗口，跑完即退出。
    if (os.environ.get("IMPOR_EDITOR_SMOKE") or "").strip().lower() in ("1", "true", "yes", "on"):
        root.withdraw()
        app = App(root)
        result = {"code": 1}
        root.after(900, lambda: result.__setitem__("code", _editor_smoke(app, root)))
        root.mainloop()
        return int(result["code"] or 0)
    # v3.6：先玩过场再进主界面（IMPOR_NO_SPLASH=1 可跳过）
    root.withdraw()
    from .splash import show_splash
    splash = show_splash(root)
    App(root)
    if splash is None:
        _reveal_root(root)
    else:
        splash.set_on_done(lambda: _reveal_root(root))
        splash.start_animation()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        write_crash_log(e, where="editor.mainloop")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
