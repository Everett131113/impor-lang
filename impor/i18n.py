# -*- coding: utf-8 -*-
"""编辑器界面多语言（简体中文 / 繁体中文 / English）。

设计目标：
- 源码里的文案仍只写一份简体中文，不把 1000+ 条字符串改成 tr(...) 调用；
- 在 Tk 控件参数落盘的最后一步统一翻译（text / title / 菜单 label /
  Notebook 标签 / Treeview 表头），动态 configure 与后续新建控件同样覆盖；
- 语言表是静态 JSON，运行时不联网；缺词时用已翻译短语做最长匹配回退，
  保证动态拼接的“错误 3   警告 1”这类状态也能跟着切换。
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager

ZH_CN = "zh_cn"
ZH_TW = "zh_tw"
EN = "en"

LANGUAGES = (ZH_CN, ZH_TW, EN)
LANGUAGE_NAMES = {
    ZH_CN: "中文（简体）",
    ZH_TW: "中文（繁體）",
    EN: "English",
}
LANGUAGE_BUTTON = {ZH_CN: "语言", ZH_TW: "語言", EN: "Language"}

_ALIASES = {
    "zh": ZH_CN, "zh-cn": ZH_CN, "zh_cn": ZH_CN, "zh-hans": ZH_CN,
    "zh-tw": ZH_TW, "zh_tw": ZH_TW, "zh-hant": ZH_TW, "zh-hk": ZH_TW,
    "en": EN, "en-us": EN, "en_us": EN, "en-gb": EN,
}

_HAS_CJK = re.compile(r"[\u3400-\u9fff]")
_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
LOCALE_FILES = {EN: "en_us.json", ZH_TW: "zh_tw.json"}
_EN_PUNCT = str.maketrans({
    "：": ": ", "，": ", ", "。": ". ", "；": "; ", "、": ", ",
    "！": "!", "？": "?", "（": " (", "）": ") ", "「": '"', "」": '"',
    "《": " <", "》": "> ", "…": "...", "—": "-", "－": "-", "·": " · ",
})


def _normalize_punct(text):
    """英文界面里清理中文标点；连续空格收敛，避免出现“Word：word”这种混排。"""
    text = text.translate(_EN_PUNCT)
    text = text.replace("  ·  ", " · ").replace(" · · ", " · ")
    return text

# 高频词人工校准：机器翻译在脱离上下文时会给出 question / debugging 这类
# 不适合做界面标签的词，这里覆盖成编辑器通用译法。
_OVERRIDES = {
    EN: {
        "新建": "New", "打开": "Open", "保存": "Save", "另存为": "Save As",
        "资源管理器": "Explorer", "全项目搜索": "Search in Project",
        "输出": "Output", "问题": "Problems", "测试": "Tests",
        "变量": "Variables", "调试": "Debug", "内存": "Memory",
        "运行": "Run", "构建": "Build", "语言": "Language",
        "命令面板": "Command Palette", "内核3.0 预览": "Kernel 3.0 Preview",
        "沙盒: 关": "Sandbox: Off", "沙盒: 开": "Sandbox: On",
        "调试: 关": "Debug: Off", "调试: 开": "Debug: On",
        "状态": "Status", "文件": "File", "行": "Line", "列": "Column",
        "类型": "Type", "值": "Value", "详情": "Details", "结果": "Result",
        "覆盖": "Coverage", "耗时": "Time", "用例": "Test case",
        "变量名": "Name", "符号": "Symbol", "引用": "References",
        "代码": "Code", "错误": "Errors", "警告": "Warnings",
        "未命名": "Untitled", "受限模式": "Restricted Mode", "已信任": "Trusted",
        "信任此工作区": "Trust Workspace", "语言已切换": "Language changed",
        "中文（简体）": "中文（简体）", "中文（繁體）": "中文（繁體）",
        "English": "English",
    },
    ZH_TW: {
        "资源管理器": "資源管理器", "全项目搜索": "全專案搜尋",
        "输出": "輸出", "问题": "問題", "测试": "測試", "变量": "變數",
        "调试": "偵錯", "内存": "記憶體", "运行": "執行", "构建": "建置",
        "新建": "新建", "保存": "儲存", "另存为": "另存新檔",
        "设置": "設定", "文件": "檔案", "错误": "錯誤", "警告": "警告",
        "语言": "語言", "语言已切换": "語言已切換",
    },
}

_tables = {}
_phrase_tables = {}
_template_tables = {}
_reverse_tables = {}
_current = ZH_CN
_installed = False
_applying = False
_original_options = None
_original_wm_title = None
_PLACEHOLDER = re.compile(r"%[-+ #0]*[0-9.*]*[diouxXeEfgGrsa%]|\{[^{}]*\}")
_menu_add_names = ("add_command", "add_checkbutton", "add_radiobutton",
                   "insert_command", "insert_checkbutton", "insert_radiobutton")


def normalize_language(value):
    if not value:
        return ZH_CN
    key = str(value).strip().lower().replace("_", "-")
    return _ALIASES.get(key, _ALIASES.get(key.replace("-", "_"), ZH_CN))


def current_language():
    return _current


def language_name(code):
    return LANGUAGE_NAMES.get(normalize_language(code), LANGUAGE_NAMES[ZH_CN])


def button_label(code=None):
    return LANGUAGE_BUTTON.get(normalize_language(code or _current), "语言")


def _load_table(code):
    if code == ZH_CN:
        return {}
    path = os.path.join(_LOCALE_DIR, LOCALE_FILES.get(code, code + ".json"))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        return {}


def _build_phrases(code):
    """按长度降序的短语表；单字不做子串替换，避免把“运行”拆坏。"""
    phrases = {}
    for src, dst in _tables.get(code, {}).items():
        if len(src) >= 2 and _HAS_CJK.search(src):
            phrases[src] = dst
    for src, dst in _OVERRIDES.get(code, {}).items():
        if len(src) >= 2 and _HAS_CJK.search(src):
            phrases[src] = dst
    return sorted(phrases.items(), key=lambda kv: -len(kv[0]))


def _build_templates(code):
    """把带 %s/%d/{} 的条目编译成“整句模板”，匹配运行期拼好的动态文案。"""
    entries = []
    merged = dict(_OVERRIDES.get(code, {}))
    merged.update(_tables.get(code, {}))
    for src, dst in merged.items():
        if not _PLACEHOLDER.search(src):
            continue
        parts, last, groups = [], 0, 0
        for m in _PLACEHOLDER.finditer(src):
            if m.group(0) == "%%":
                continue
            parts.append(re.escape(src[last:m.start()]))
            parts.append("(.+?)")
            groups += 1
            last = m.end()
        parts.append(re.escape(src[last:]))
        if groups:
            try:
                pattern = re.compile("".join(parts) + r"\Z", re.DOTALL)
            except re.error:
                continue
            entries.append((len(src) - groups * 2, pattern, dst, groups))
    entries.sort(key=lambda item: -item[0])
    return entries


def _fill_template(dst, groups):
    out, last, used = [], 0, 0
    for m in _PLACEHOLDER.finditer(dst):
        if m.group(0) == "%%":
            continue
        out.append(dst[last:m.start()])
        out.append(groups[used] if used < len(groups) else "")
        used += 1
        last = m.end()
    out.append(dst[last:])
    return "".join(out)


def set_language(code):
    global _current
    code = normalize_language(code)
    if code not in _tables:
        _tables[code] = _load_table(code)
        _phrase_tables[code] = _build_phrases(code)
        _template_tables[code] = _build_templates(code)
        reverse = {}
        for src, dst in _tables[code].items():
            reverse.setdefault(dst.strip(), src)
        for src, dst in _OVERRIDES.get(code, {}).items():
            reverse.setdefault(dst.strip(), src)
        _reverse_tables[code] = reverse
    _current = code
    return code


def untranslate(text):
    """把界面上的译文还原成简体源文，供下拉框/配置回写这类逻辑使用。"""
    if not isinstance(text, str) or _current == ZH_CN:
        return text
    return _reverse_tables.get(_current, {}).get(text.strip(), text)


def tr(text):
    """翻译一段界面文案；非界面数据（路径/代码/用户内容）不要经过这里。"""
    if not isinstance(text, str) or _current == ZH_CN:
        return text
    if not text or not _HAS_CJK.search(text):
        return text
    over = _OVERRIDES.get(_current, {})
    if text in over:
        return over[text]
    table = _tables.get(_current) or {}
    value = table.get(text)
    if value:
        return _normalize_punct(value) if _current == EN else value
    # 运行期拼好的动态文案：按模板整句匹配，避免逐词替换产生中英混排
    templates = _template_tables.get(_current) or _build_templates(_current)
    for _weight, pattern, dst, groups in templates:
        m = pattern.match(text)
        if m:
            result = _fill_template(dst, m.groups()[:groups])
            return _normalize_punct(result) if _current == EN else result
    # 动态拼接句：用已翻译短语做最长匹配替换
    phrases = _phrase_tables.get(_current) or _build_phrases(_current)
    out = []
    i = 0
    n = len(text)
    while i < n:
        hit = None
        for src, dst in phrases:
            if len(src) > n - i:
                continue
            if text.startswith(src, i):
                hit = (src, dst)
                break
        if hit:
            out.append(hit[1])
            i += len(hit[0])
        else:
            out.append(text[i])
            i += 1
    result = "".join(out)
    return _normalize_punct(result) if _current == EN else result


def install_tk():
    """接管 Tk 的文本参数入口；幂等，可在编辑器启动时安全重复调用。"""
    global _installed, _original_options, _original_wm_title
    if _installed:
        return
    import tkinter as tk
    from tkinter import ttk

    _original_options = tk.BaseWidget._options
    _original_wm_title = tk.Wm.wm_title

    def options(self, cnf, kw=None):
        if _applying or not isinstance(cnf, dict):
            return _original_options(self, cnf, kw)
        cnf = dict(cnf)
        if kw:
            cnf.update(kw)
            kw = None
        for key in ("text", "title"):
            value = cnf.get(key)
            if isinstance(value, str) and _HAS_CJK.search(value):
                try:
                    setattr(self, "_i18n_src_" + key, value)
                except Exception:
                    pass
                if _current != ZH_CN:
                    cnf[key] = tr(value)
        values = cnf.get("values")
        if isinstance(values, (tuple, list)) and any(
                isinstance(v, str) and _HAS_CJK.search(v) for v in values):
            try:
                setattr(self, "_i18n_src_values", tuple(values))
            except Exception:
                pass
            if _current != ZH_CN:
                cnf["values"] = tuple(tr(v) if isinstance(v, str) else v for v in values)
        return _original_options(self, cnf, kw)

    tk.BaseWidget._options = options

    def wm_title(self, string=None):
        if string is None or _applying:
            return _original_wm_title(self, string)
        try:
            setattr(self, "_i18n_src_title", string)
        except Exception:
            pass
        return _original_wm_title(self, tr(string))

    tk.Wm.wm_title = wm_title
    tk.Wm.title = wm_title

    def wrap_menu(name):
        original = getattr(tk.Menu, name)

        def add(self, cnf={}, **kw):
            label = kw.get("label")
            if label is None and isinstance(cnf, dict):
                label = cnf.get("label")
            raw = label.strip("✓ \t") if isinstance(label, str) else label
            if (not _applying and isinstance(label, str) and _HAS_CJK.search(label)
                    and raw not in LANGUAGE_NAMES.values()):
                if "label" in kw:
                    kw["label"] = tr(label)
                elif isinstance(cnf, dict):
                    cnf = dict(cnf)
                    cnf["label"] = tr(label)
            return original(self, cnf, **kw)

        setattr(tk.Menu, name, add)

    for name in _menu_add_names:
        if hasattr(tk.Menu, name):
            wrap_menu(name)

    original_notebook_add = ttk.Notebook.add

    def notebook_add(self, child, **kw):
        if isinstance(kw.get("text"), str):
            srcs = getattr(self, "_i18n_tab_src", None)
            if srcs is None:
                srcs = {}
                try:
                    self._i18n_tab_src = srcs
                except Exception:
                    pass
            if not _applying:
                srcs[child] = kw["text"]
                kw["text"] = tr(kw["text"])
        return original_notebook_add(self, child, **kw)

    ttk.Notebook.add = notebook_add

    original_heading = ttk.Treeview.heading

    def heading(self, column, option=None, **kw):
        if isinstance(kw.get("text"), str):
            heads = getattr(self, "_i18n_heading_src", None)
            if heads is None:
                heads = {}
                try:
                    self._i18n_heading_src = heads
                except Exception:
                    pass
            if not _applying:
                heads[column] = kw["text"]
                kw["text"] = tr(kw["text"])
        return original_heading(self, column, option, **kw)

    ttk.Treeview.heading = heading

    _install_messageboxes()
    _installed = True


def _install_messageboxes():
    from tkinter import messagebox
    names = ("showinfo", "showerror", "showwarning", "askyesno", "askyesnocancel",
             "askokcancel", "askretrycancel", "askquestion")
    for name in names:
        original = getattr(messagebox, name, None)
        if original is None or getattr(original, "_i18n_wrapped", False):
            continue

        def wrapper(title=None, message=None, *args, _orig=original, **kw):
            if isinstance(title, str):
                title = tr(title)
            if isinstance(message, str):
                message = tr(message)
            return _orig(title, message, *args, **kw)

        wrapper._i18n_wrapped = True
        setattr(messagebox, name, wrapper)


def _raw_configure(widget, **kw):
    global _applying
    old = _applying
    _applying = True
    try:
        widget.configure(**kw)
    finally:
        _applying = old


@contextmanager
def _temporary_applying():
    global _applying
    old = _applying
    _applying = True
    try:
        yield
    finally:
        _applying = old


def _walk(widget):
    yield widget
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        yield from _walk(child)


def retranslate(root):
    """按保存的源文案重刷整棵控件树（切换语言时调用）。"""
    if not root:
        return
    for widget in _walk(root):
        try:
            src = getattr(widget, "_i18n_src_text", None)
            if src is not None:
                _raw_configure(widget, text=tr(src))
        except Exception:
            pass
        try:
            src = getattr(widget, "_i18n_src_title", None)
            if src is not None and widget.winfo_toplevel() is not None:
                global _applying
                old = _applying
                _applying = True
                try:
                    _original_wm_title(widget, tr(src))
                finally:
                    _applying = old
        except Exception:
            pass
        # ttk.Combobox 的选项列表与当前值（设置页/任务模板常用）
        try:
            from tkinter import ttk as _ttk
            if isinstance(widget, _ttk.Combobox):
                current = widget.get()
                src = getattr(widget, "_i18n_src_current", None) or untranslate(current)
                if isinstance(src, str) and _HAS_CJK.search(src):
                    widget._i18n_src_current = src
                    with _temporary_applying():
                        widget.set(tr(src))
                src_values = getattr(widget, "_i18n_src_values", None)
                if src_values:
                    widget.configure(values=tuple(
                        tr(v) if isinstance(v, str) else v for v in src_values))
        except Exception:
            pass
        # ttk Notebook 的标签页文案
        try:
            tabs = widget.tabs()
        except Exception:
            tabs = None
        if tabs:
            srcs = getattr(widget, "_i18n_tab_src", None)
            if srcs is None:
                srcs = {}
                for tab in tabs:
                    try:
                        text = widget.tab(tab, "text")
                    except Exception:
                        continue
                    if isinstance(text, str) and _HAS_CJK.search(text):
                        srcs[tab] = text
                try:
                    widget._i18n_tab_src = srcs
                except Exception:
                    pass
            for tab, text in srcs.items():
                try:
                    widget.tab(tab, text=tr(text))
                except Exception:
                    pass
        # ttk Treeview 表头
        try:
            columns = widget["columns"]
        except Exception:
            columns = None
        if columns:
            heads = getattr(widget, "_i18n_heading_src", None)
            if heads is None:
                heads = {}
                for col in ("#0",) + tuple(columns):
                    try:
                        text = widget.heading(col, "text")
                    except Exception:
                        continue
                    if isinstance(text, str) and _HAS_CJK.search(text):
                        heads[col] = text
                try:
                    widget._i18n_heading_src = heads
                except Exception:
                    pass
            for col, text in heads.items():
                try:
                    with _temporary_applying():
                        widget.heading(col, text=tr(text))
                except Exception:
                    pass
        # 自绘按钮
        try:
            if hasattr(widget, "retranslate"):
                widget.retranslate()
        except Exception:
            pass
