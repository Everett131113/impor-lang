# -*- coding: utf-8 -*-
"""Impor Studio 7.2 的可选扩展层。

这里放置不依赖 editor 内部实现细节的能力：项目级设置、只读插件 API、
多标签集成终端。editor 通过函数级导入使用，避免拖慢冷启动。
"""

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
import zipfile
from tkinter import ttk

PROJECT_DIR = ".impor"
SETTINGS_NAME = "settings.json"


# ---------------------------------------------------------------- 项目设置
def project_settings_path(workspace):
    return os.path.join(os.path.abspath(workspace), PROJECT_DIR, SETTINGS_NAME)


def load_project_settings(workspace):
    """读取 .impor/settings.json；不存在或损坏时返回空字典。"""
    path = project_settings_path(workspace)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_project_settings(workspace, data):
    """把设置写入 .impor/settings.json，返回实际路径。"""
    path = project_settings_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def merged_setting(project, section, key, default=None):
    """兼容平铺与分组两种写法：indent 或 editor.indent。"""
    if not isinstance(project, dict):
        return default
    if key in project:
        return project[key]
    sec = project.get(section)
    if isinstance(sec, dict) and key in sec:
        return sec[key]
    return default


# ---------------------------------------------------------------- 插件 API
class PluginAPI:
    """插件 API：默认只读，写操作需显式授权（v7.3）。

    插件能注册命令、侧栏面板、状态栏项、主题和格式化器，只能读取当前
    文本/文件。写文件必须满足任一条件：
      - 环境变量 IMPOR_PLUGIN_PERMISSIONS 含 `*`、`write` 或插件文件名；
      - 项目设置 `.impor/settings.json` 的 `plugin_write_allow` 列表含插件文件名或 `*`。
    写入路径限制在工作区内，防止插件越界修改系统文件。插件与编辑器同进程，
    因此只加载用户明确放入 .impor/plugins 的本地脚本。
    """

    def __init__(self, app, plugin_id, manager=None):
        self.app = app
        self.plugin_id = plugin_id
        self.manager = manager or getattr(app, "plugins", None)

    # 读取
    def current_text(self):
        pane = self.app.current_pane()
        return pane.text.get("1.0", "end-1c") if pane else ""

    def current_path(self):
        pane = self.app.current_pane()
        return pane.path if pane else None

    def read_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def list_files(self, suffix=""):
        base = getattr(self.app, "workspace", "") or "."
        out = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not suffix or name.endswith(suffix):
                    out.append(os.path.join(root, name))
        return out

    def toast(self, text):
        self.app.toast("[%s] %s" % (self.plugin_id, text))

    # 写入（需授权）
    def _write_allowed(self):
        env = os.environ.get("IMPOR_PLUGIN_PERMISSIONS", "")
        tokens = {x.strip() for x in env.replace(";", ",").split(",") if x.strip()}
        if "*" in tokens or "write" in tokens or self.plugin_id in tokens:
            return True
        try:
            settings = getattr(self.app, "project_settings", {}) or {}
            allow = settings.get("plugin_write_allow") or []
            if isinstance(allow, str):
                allow = [allow]
            allow = {str(x) for x in allow}
            return "*" in allow or self.plugin_id in allow
        except Exception:
            return False

    def _safe_target(self, path):
        base = os.path.abspath(getattr(self.app, "workspace", "") or ".")
        target = os.path.abspath(path)
        try:
            common = os.path.commonpath([base, target])
        except ValueError:
            common = ""
        if common != base:
            raise PermissionError("插件只能写入工作区内的文件：%s" % path)
        return target

    def write_file(self, path, content):
        """授权后写入工作区内文件；返回写入的绝对路径。"""
        if not self._write_allowed():
            raise PermissionError(
                "插件 %s 未获写权限：在 .impor/settings.json 的 plugin_write_allow 中加入它，"
                "或设置 IMPOR_PLUGIN_PERMISSIONS=*" % self.plugin_id)
        target = self._safe_target(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content if content is not None else "")
        self.toast("已写入 %s" % os.path.basename(target))
        return target

    def write_current(self, content):
        """授权后替换当前编辑器内容（未保存文件需先保存）。"""
        pane = self.app.current_pane()
        if not pane or not getattr(pane, "path", None):
            raise PermissionError("当前编辑器没有已保存的文件路径")
        return self.write_file(pane.path, content)

    def register_command(self, title, callback, shortcut=""):
        if self.manager is not None:
            self.manager.commands.append((str(title), str(shortcut or ""), callback))

    def register_panel(self, title, builder):
        if self.manager is not None:
            self.manager.panels.append((str(title), builder))

    def register_status_item(self, text, callback=None):
        if self.manager is not None:
            self.manager.status_items.append((str(text), callback))

    def register_theme(self, name, colors):
        if self.manager is not None and isinstance(colors, dict):
            self.manager.themes[str(name)] = dict(colors)

    def register_formatter(self, name, func):
        if self.manager is not None:
            self.manager.formatters[str(name)] = func


class PluginManager:
    def __init__(self, app):
        self.app = app
        self.plugins = []
        self.commands = []
        self.panels = []
        self.status_items = []
        self.themes = {}
        self.formatters = {}
        self.errors = []
        self.processes = []
        self.isolate = bool(getattr(app, "plugin_isolation", True))
        self._loaded = False

    def home_root(self):
        return os.path.join(os.path.expanduser("~"), ".impor_studio", "plugins")

    def state_path(self):
        return os.path.join(os.path.expanduser("~"), ".impor_studio", "plugins_state.json")

    def _read_state(self):
        try:
            with open(self.state_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_state(self, data):
        path = self.state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def is_enabled(self, path):
        return bool(self._read_state().get(os.path.abspath(path), True))

    def set_enabled(self, path, enabled):
        data = self._read_state()
        data[os.path.abspath(path)] = bool(enabled)
        self._write_state(data)

    def _iter_plugin_files(self, root):
        """扫描插件根：根级 .py + 一级子目录的 plugin.py/main.py/唯一 .py。"""
        if not os.path.isdir(root):
            return []
        out = []
        for name in sorted(os.listdir(root)):
            if name.startswith("_") or name.startswith("."):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path) and name.endswith(".py"):
                out.append(path)
                continue
            if not os.path.isdir(path):
                continue
            for cand in ("plugin.py", "main.py", "__init__.py"):
                p = os.path.join(path, cand)
                if os.path.isfile(p):
                    out.append(p)
                    break
            else:
                pys = [os.path.join(path, x) for x in sorted(os.listdir(path))
                       if x.endswith(".py") and not x.startswith("_")]
                if len(pys) == 1:
                    out.append(pys[0])
        return out

    def discover(self):
        seen, out = set(), []
        for root in self.roots():
            for path in self._iter_plugin_files(root):
                key = os.path.abspath(path)
                if key in seen:
                    continue
                seen.add(key)
                out.append(path)
        return out

    def meta(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in ("plugin", "main", "__init__"):
            name = os.path.basename(os.path.dirname(path))
        version, desc = "0.0.0", ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
            for line in head.split("\n")[:30]:
                m = re.match(r"\s*#\s*impor-name\s*:\s*(.+)", line, re.I)
                if m:
                    name = m.group(1).strip()
                m = re.match(r"\s*#\s*impor-version\s*:\s*(.+)", line, re.I)
                if m:
                    version = m.group(1).strip()
                m = re.match(r"\s*#\s*impor-description\s*:\s*(.+)", line, re.I)
                if m:
                    desc = m.group(1).strip()
        except Exception:
            pass
        manifest = os.path.join(os.path.dirname(path), "plugin.json")
        try:
            if os.path.isfile(manifest):
                with open(manifest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                name = str(data.get("name") or name)
                version = str(data.get("version") or version)
                desc = str(data.get("description") or desc)
        except Exception:
            pass
        perms = self._plugin_permissions(path)
        return {"name": name, "version": version, "description": desc,
                "path": path, "permissions": perms, "enabled": self.is_enabled(path)}

    def install_folder(self, src):
        """复制本地文件夹/单个 .py 到用户插件目录，返回新路径列表。"""
        src = os.path.abspath(src)
        root = self.home_root()
        os.makedirs(root, exist_ok=True)
        installed = []
        if os.path.isfile(src) and src.endswith(".py"):
            dst = os.path.join(root, os.path.basename(src))
            shutil.copy2(src, dst)
            installed.append(dst)
        elif os.path.isdir(src):
            base = os.path.basename(src.rstrip("\\/")) or "plugin"
            dst_dir = os.path.join(root, base)
            if os.path.exists(dst_dir):
                dst_dir = dst_dir + "_%d" % int(time.time())
            shutil.copytree(src, dst_dir)
            pys = self._iter_plugin_files(dst_dir)
            if not pys:
                shutil.rmtree(dst_dir, ignore_errors=True)
                raise ValueError("文件夹里没有可加载的 .py 插件")
            installed.extend(pys)
        else:
            raise ValueError("请选择 .py 文件或插件文件夹")
        self.reload()
        return installed

    def install_zip(self, zip_path):
        """安全解压 zip，安装其中第一个插件包，返回新路径列表。"""
        tmp = tempfile.mkdtemp(prefix="impor_plugin_zip_")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    target = os.path.abspath(os.path.join(tmp, member))
                    if os.path.commonpath([tmp, target]) != tmp:
                        raise ValueError("zip 包含越界路径：%s" % member)
                zf.extractall(tmp)
            entries = [os.path.join(tmp, x) for x in os.listdir(tmp)]
            dirs = [x for x in entries if os.path.isdir(x)]
            files = [x for x in entries if os.path.isfile(x) and x.endswith(".py")]
            if files and not dirs:
                root = self.home_root()
                os.makedirs(root, exist_ok=True)
                out = []
                for f in files:
                    dst = os.path.join(root, os.path.basename(f))
                    shutil.copy2(f, dst)
                    out.append(dst)
                self.reload()
                return out
            src = dirs[0] if len(dirs) == 1 and not files else tmp
            return self.install_folder(src)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def uninstall(self, path):
        """只允许删除插件根内的文件/目录。"""
        path = os.path.abspath(path)
        roots = [os.path.abspath(r) for r in self.roots()]
        inside = False
        for root in roots:
            try:
                if os.path.commonpath([root, path]) == root:
                    inside = True
                    break
            except ValueError:
                continue
        if not inside:
            raise PermissionError("拒绝删除插件根之外的文件：%s" % path)
        if os.path.dirname(path) in roots and os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            parent = os.path.dirname(path)
            in_root = any(os.path.abspath(parent).startswith(root + os.sep) for root in roots)
            if in_root and os.path.isdir(parent):
                shutil.rmtree(parent)
            else:
                os.remove(path)
        data = self._read_state()
        data.pop(path, None)
        self._write_state(data)
        self.reload()

    def reset(self):
        """卸载插件状态但不再加载；切换工作区/进入受限模式时使用。"""
        self.shutdown()
        self.plugins, self.commands, self.panels = [], [], []
        self.status_items, self.themes, self.formatters = [], {}, {}
        self.errors = []
        self._loaded = False

    def reload(self):
        self.reset()
        self.load()

    def roots(self):
        home = self.home_root()
        ws = os.path.join(getattr(self.app, "workspace", "") or ".", PROJECT_DIR, "plugins")
        ps = getattr(self.app, "project_settings", {})
        explicit = ps.get("plugins") if isinstance(ps, dict) else []
        if not isinstance(explicit, list):
            explicit = []
        out = [home, ws]
        for p in explicit:
            try:
                out.append(os.path.abspath(os.path.join(getattr(self.app, "workspace", "."), str(p))))
            except Exception:
                pass
        return out

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        for path in self.discover():
            if not self.is_enabled(path):
                continue
            if self.isolate and not self._wants_inproc(path):
                self._load_isolated(path)
            else:
                self._load_one(path)

    def _wants_inproc(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(2048)
            return "impor-plugin: inproc" in head
        except Exception:
            return False

    def _plugin_permissions(self, path):
        """3.2.1 插件权限清单：插件头部 `# impor-permissions: read,write`。"""
        perms = {"read": True, "write": False, "net": False, "process": False}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(4096)
            for line in head.split("\n")[:20]:
                m = re.match(r"\s*#\s*impor-permissions\s*:\s*(.+)", line, re.I)
                if m:
                    raw = {x.strip().lower() for x in re.split(r"[, ]+", m.group(1)) if x.strip()}
                    perms["write"] = "write" in raw or "all" in raw
                    perms["read"] = "read" in raw or "all" in raw
                    perms["net"] = "net" in raw or "network" in raw
                    perms["process"] = "process" in raw
                    perms["declared"] = sorted(raw)
                    break
        except Exception:
            pass
        return perms

    def _isolation_handler(self, proc, allow_write):
        app = self.app

        def safe_target(path):
            base = os.path.abspath(getattr(app, "workspace", "") or ".")
            target = os.path.abspath(str(path))
            try:
                common = os.path.commonpath([base, target])
            except ValueError:
                common = ""
            if common != base:
                raise PermissionError("插件只能写入工作区内的文件：%s" % path)
            return target

        def handler(op, args, _proc):
            if op == "current_text":
                return proc.context_text or ""
            if op == "current_path":
                return proc.context_path
            if op == "read_file":
                raw = os.path.abspath(str(args.get("path")))
                base = os.path.abspath(getattr(app, "workspace", "") or ".")
                try:
                    common = os.path.commonpath([base, raw])
                except ValueError:
                    common = ""
                if common != base:
                    raise PermissionError("插件默认只能读取工作区内文件：%s" % raw)
                with open(raw, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            if op == "toast":
                text = "[%s] %s" % (proc.plugin_id, args.get("text", ""))
                try:
                    app.root.after(0, lambda t=text: app.toast(t))
                except Exception:
                    pass
                return True
            if op == "write_file":
                if not allow_write():
                    raise PermissionError(
                        "插件 %s 未获写权限：在 .impor/settings.json 的 plugin_write_allow "
                        "中加入它，或设置 IMPOR_PLUGIN_PERMISSIONS=*" % proc.plugin_id)
                target = safe_target(args.get("path"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                from .security import safe_write, AuditLog
                safe_write(target, args.get("content") or "",
                           root=os.path.abspath(getattr(app, "workspace", "") or "."))
                AuditLog(getattr(app, "workspace", ".")).write(
                    "plugin.write", plugin=proc.plugin_id, path=target)
                return target
            if op == "write_current":
                if not allow_write():
                    raise PermissionError("插件未获写权限")
                path = proc.context_path
                if not path:
                    raise PermissionError("当前编辑器没有已保存的文件路径")
                target = safe_target(path)
                from .security import safe_write, AuditLog
                safe_write(target, args.get("content") or "",
                           root=os.path.abspath(getattr(app, "workspace", "") or "."))
                AuditLog(getattr(app, "workspace", ".")).write(
                    "plugin.write_current", plugin=proc.plugin_id, path=target)
                return target
            raise PermissionError("未知代理操作：%s" % op)

        return handler

    def _load_isolated(self, path):
        try:
            from .plugin_host import IsolatedPluginProcess
            probe = PluginAPI(self.app, os.path.basename(path), self)
            allow = probe._write_allowed
            proc = IsolatedPluginProcess(
                path, getattr(self.app, "workspace", ".") or ".",
                handler=None, allow_write=allow())
            proc.permissions = self._plugin_permissions(path)
            proc.handler = self._isolation_handler(proc, allow)
            if not proc.start():
                self.errors.append((os.path.basename(path), proc.error or "隔离宿主启动失败"))
                proc.stop()
                return
            self.processes.append(proc)
            for ev in proc.events:
                kind = ev.get("event")
                if kind == "command":
                    title = str(ev.get("title") or "")
                    shortcut = str(ev.get("shortcut") or "")
                    self.commands.append((title, shortcut, self._isolated_command(proc, title)))
                elif kind == "panel":
                    title = str(ev.get("title") or "插件面板")
                    ui = ev.get("ui") if isinstance(ev.get("ui"), dict) else None
                    self.panels.append((title, self._isolated_panel(title, proc, ui)))
                elif kind == "status":
                    self.status_items.append((str(ev.get("text") or ""), None))
                elif kind == "theme":
                    name = str(ev.get("name") or "")
                    colors = ev.get("colors") if isinstance(ev.get("colors"), dict) else {}
                    self.themes[name] = dict(colors)
                elif kind == "formatter":
                    name = str(ev.get("name") or "")
                    self.formatters[name] = (lambda text, n=name, p=proc: p.invoke_formatter(n, text))
                elif kind == "error":
                    self.errors.append((os.path.basename(path), str(ev.get("error") or "")[:160]))
            self.plugins.append((os.path.basename(path) + "（隔离）", path))
        except Exception as exc:
            self.errors.append((os.path.basename(path), str(exc)[:160]))

    def _isolated_command(self, proc, name):
        def run():
            pane = None
            try:
                pane = self.app.current_pane()
            except Exception:
                pass
            text = pane.text.get("1.0", "end-1c") if pane else ""
            path = getattr(pane, "path", None) if pane else None
            try:
                return proc.call_with_restart("command", {"name": name})
            except Exception as exc:
                self.errors.append((proc.plugin_id, str(exc)[:160]))
                try:
                    self.app.toast("[%s] 命令失败：%s" % (proc.plugin_id, str(exc)[:100]), "err")
                except Exception:
                    pass
                return None
        return run

    def _isolated_panel(self, title, proc, ui=None):
        """3.2.2：父进程渲染子进程 builder 提交的 UI 描述。

        builder 在隔离进程中运行，只记录控件树；没有任何 Tk 对象跨进程，
        因此插件崩溃不会带倒编辑器，父进程也不会执行插件代码。
        """
        def apply_layout(widget, node):
            layout = node.get("layout") or {}
            mode = layout.get("mode") or "pack"
            fill = layout.get("fill")
            if fill == "both":
                fill = tk.BOTH
            elif fill == "x":
                fill = tk.X
            elif fill == "y":
                fill = tk.Y
            side = {"left": tk.LEFT, "right": tk.RIGHT, "top": tk.TOP,
                    "bottom": tk.BOTTOM}.get(layout.get("side"), tk.TOP)
            padx = layout.get("padx", 0)
            pady = layout.get("pady", 0)
            try:
                if mode == "grid":
                    widget.grid(row=int(layout.get("row", 0)), column=int(layout.get("column", 0)),
                                sticky=str(layout.get("sticky", "")) or "",
                                padx=int(padx or 0), pady=int(pady or 0))
                else:
                    widget.pack(side=side, fill=fill, expand=bool(layout.get("expand", False)),
                                padx=int(padx or 0), pady=int(pady or 0))
            except Exception:
                widget.pack(fill=tk.BOTH, expand=True)

        def render(parent, node, depth=0):
            if depth > 12 or not isinstance(node, dict):
                return None
            kind = node.get("type") or "frame"
            text = str(node.get("text") or "")
            bg = getattr(parent, "bg", None) or "#0f141c"
            if kind == "frame":
                widget = tk.Frame(parent, bg=bg)
                apply_layout(widget, node)
                for child in node.get("children", [])[:240]:
                    render(widget, child, depth + 1)
                return widget
            if kind == "labelframe":
                widget = tk.LabelFrame(parent, text=text[:200], bg=bg, fg="#c8d3e0",
                                       bd=1, relief="flat", font=("Microsoft YaHei UI", 9))
                apply_layout(widget, node)
                for child in node.get("children", [])[:240]:
                    render(widget, child, depth + 1)
                return widget
            if kind == "label":
                widget = tk.Label(parent, text=text, bg=bg, fg="#d5dde8", anchor="w",
                                  justify="left", font=("Microsoft YaHei UI", 9))
                apply_layout(widget, node)
                return widget
            if kind == "button":
                def invoke(action=node.get("action")):
                    if not action:
                        return
                    try:
                        proc.call_with_restart("panel_action", {"name": action})
                    except Exception as exc:
                        try:
                            self.errors.append((proc.plugin_id, str(exc)[:160]))
                            self.app.toast("[%s] 面板动作失败：%s" % (proc.plugin_id, str(exc)[:100]), "err")
                        except Exception:
                            pass
                widget = tk.Button(parent, text=text or "执行", command=invoke,
                                   bg="#1b2432", fg="#d5dde8", activebackground="#243044",
                                   activeforeground="#eef2f8", relief="flat", bd=0,
                                   padx=10, pady=4, cursor="hand2",
                                   font=("Microsoft YaHei UI", 9))
                apply_layout(widget, node)
                return widget
            if kind == "entry":
                widget = tk.Entry(parent, bg="#0d1117", fg="#d5dde8", insertbackground="#d5dde8",
                                  relief="flat", font=("Consolas", 9))
                if text:
                    widget.insert(0, text)
                apply_layout(widget, node)
                return widget
            if kind == "text":
                widget = tk.Text(parent, height=6, bg="#0d1117", fg="#d5dde8", relief="flat",
                                 wrap="word", font=("Consolas", 9))
                if text:
                    widget.insert("1.0", text)
                apply_layout(widget, node)
                return widget
            if kind == "checkbutton":
                var = tk.BooleanVar(value=bool(node.get("checked", False)))
                widget = tk.Checkbutton(parent, text=text, variable=var, bg=bg, fg="#d5dde8",
                                        selectcolor="#0d1117", anchor="w", relief="flat",
                                        font=("Microsoft YaHei UI", 9))
                apply_layout(widget, node)
                return widget
            if kind == "separator":
                widget = tk.Frame(parent, bg="#1c232e", height=1)
                if not (node.get("layout") or {}).get("mode"):
                    node = dict(node)
                    node["layout"] = {"mode": "pack", "fill": "x", "pady": 6}
                apply_layout(widget, node)
                return widget
            if kind == "tree":
                columns = node.get("columns") or []
                widget = ttk.Treeview(parent, columns=columns, show="headings", height=6)
                for col in columns[:12]:
                    widget.heading(col, text=str(col))
                    widget.column(col, width=140, stretch=True)
                apply_layout(widget, node)
                return widget
            return None

        def builder(frame, _api=None):
            bg = getattr(frame, "bg", "#0f141c") or "#0f141c"
            wrap = tk.Frame(frame, bg=bg)
            wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
            if ui and ui.get("children"):
                tk.Label(wrap, text=title, bg=bg, fg="#eef2f8", anchor="w",
                         font=("Microsoft YaHei UI", 11, "bold")).pack(fill=tk.X)
                tk.Label(wrap, text="隔离进程 · 控件树由子进程 builder 生成，父进程渲染",
                         bg=bg, fg="#6b7688", anchor="w",
                         font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, pady=(2, 8))
                body = tk.Frame(wrap, bg=bg)
                body.pack(fill=tk.BOTH, expand=True)
                for child in ui.get("children", [])[:240]:
                    render(body, child)
                return wrap
            # 没有 UI 描述（旧插件或构建失败）：保留进程状态与重启入口
            tk.Label(wrap, text=title, bg=bg, fg="#e6edf6", anchor="w",
                     font=("Microsoft YaHei UI", 11, "bold")).pack(fill=tk.X)
            tk.Label(wrap, text="隔离进程宿主 · 插件代码在独立 Python 进程中运行，崩溃会自动重启（最多 3 次）",
                     bg=bg, fg="#9aa7b8", anchor="w", justify="left",
                     font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, pady=(4, 8))
            perms = getattr(proc, "permissions", {}) or {}
            tk.Label(wrap, text="权限清单：" + ("、".join(perms.get("declared") or ["未声明（默认只读工作区）"])),
                     bg=bg, fg="#e0af68", anchor="w", font=("Consolas", 9)).pack(fill=tk.X)
            status_var = tk.StringVar()

            def refresh():
                alive = bool(getattr(proc, "alive", False))
                status_var.set("状态：%s · 重启 %d 次 · 写权限：%s" % (
                    "运行中" if alive else "已退出",
                    getattr(proc, "restarts", 0),
                    "允许（工作区内）" if getattr(proc, "allow_write", False) else "拒绝（默认）"))

            def do_restart():
                ok = proc.restart()
                refresh()
                tk.Label(wrap, text=("已重启" if ok else "重启失败：" + (proc.error or "")),
                         bg=bg, fg="#7dd3a0" if ok else "#f7768e", anchor="w").pack(fill=tk.X)

            tk.Label(wrap, textvariable=status_var, bg=bg, fg="#c8d3e0", anchor="w",
                     font=("Consolas", 9)).pack(fill=tk.X)
            row = tk.Frame(wrap, bg=bg)
            row.pack(fill=tk.X, pady=6)
            tk.Button(row, text="重启插件进程", command=do_restart, bg="#1b2432", fg="#d5dde8",
                      relief="flat", bd=0, padx=10, pady=4, cursor="hand2").pack(side=tk.LEFT)
            refresh()
            return wrap
        return builder

    def shutdown(self):
        for proc in getattr(self, "processes", []):
            try:
                proc.stop()
            except Exception:
                pass
        self.processes = []

    def _load_one(self, path):
        mod_name = "impor_plugin_%s" % os.path.splitext(os.path.basename(path))[0]
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            register = getattr(mod, "register", None)
            if not callable(register):
                self.errors.append((os.path.basename(path), "缺少 register(api)"))
                return
            api = PluginAPI(self.app, os.path.basename(path), self)
            register(api)
            self.plugins.append((os.path.basename(path), path))
        except Exception as exc:
            self.errors.append((os.path.basename(path), str(exc)[:160]))

    def palette_commands(self):
        return [(t, s, cb) for (t, s, cb) in self.commands if callable(cb)]

    def theme_names(self):
        return sorted(self.themes.keys())

    def api_for_title(self, title):
        return PluginAPI(self.app, str(title), self)


# ---------------------------------------------------------------- 多标签终端
PROFILES = {
    "cmd": ["cmd.exe", "/Q"],
    "powershell": ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"],
    "pwsh": ["pwsh.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"],
}


def _profile_command(profile):
    if profile in PROFILES:
        return PROFILES[profile]
    # 允许 settings 里写完整命令行
    return profile


class TerminalSession:
    def __init__(self, master, app, profile="cmd", cwd=None):
        self.app = app
        self.frame = tk.Frame(master, bg="#090c11")
        self.text = tk.Text(self.frame, bg="#090c11", fg="#d5dde8", relief="flat",
                            insertbackground="#7aa2f7", font=(getattr(app, "mono", "Consolas"), 10),
                            wrap="none", padx=8, pady=6)
        sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        bar = tk.Frame(self.frame, bg="#111823")
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, text="$", bg="#111823", fg="#9ece6a",
                 font=(getattr(app, "mono", "Consolas"), 11, "bold")).pack(side=tk.LEFT, padx=(8, 2))
        self.entry = tk.Entry(bar, bg="#17202e", fg="#d5dde8", insertbackground="#7aa2f7",
                              relief="flat", font=(getattr(app, "mono", "Consolas"), 10))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=5, ipady=3)
        self.entry.bind("<Return>", self.send)
        self.entry.bind("<Control-c>", lambda e: self.interrupt())
        self.proc = None
        self._q = queue.Queue()
        self.cwd = cwd or getattr(app, "workspace", os.getcwd())
        self.profile = profile
        self.closed = False
        self._start()
        self._pump()
        self.entry.focus_set()

    def _start(self):
        cmd = _profile_command(self.profile)
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags,
            )
            self._write("Impor 多终端 · %s · %s\n" % (self.profile, self.cwd))
            threading.Thread(target=self._reader, daemon=True).start()
        except Exception as exc:
            self._write("终端启动失败：%s\n" % exc)

    def _reader(self):
        try:
            for line in self.proc.stdout:
                self._q.put(line)
        except Exception:
            pass
        self._q.put(None)

    def _pump(self):
        if self.closed:
            return
        try:
            while True:
                line = self._q.get_nowait()
                if line is None:
                    self._write("\n[进程已结束]\n")
                    return
                self._write(line)
        except queue.Empty:
            pass
        try:
            self.frame.after(60, self._pump)
        except Exception:
            pass

    def _write(self, text):
        try:
            self.text.insert(tk.END, text)
            self.text.see(tk.END)
        except Exception:
            pass

    def send(self, event=None):
        line = self.entry.get()
        if not line:
            return "break"
        self.entry.delete(0, tk.END)
        self._write("$ " + line + "\n")
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
        except Exception as exc:
            self._write("写入失败：%s\n" % exc)
        return "break"

    def interrupt(self):
        try:
            if self.proc:
                self.proc.terminate()
                self._write("^C\n")
        except Exception:
            pass
        return "break"

    def close(self):
        self.closed = True
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


class TerminalWindow(tk.Toplevel):
    """多终端标签窗口：每个标签一个持久 shell，profile 可切换。"""

    def __init__(self, app):
        super().__init__(app.root, bg="#111823")
        self.app = app
        self.title("Impor 多终端")
        self.transient(app.root)
        self.geometry("820x500")
        self.sessions = []
        shell = (app.project_settings or {}).get("shell")
        profile = shell.get("profile", "cmd") if isinstance(shell, dict) else "cmd"
        self._profile = tk.StringVar(value=profile)
        top = tk.Frame(self, bg="#111823")
        top.pack(fill=tk.X)
        tk.Label(top, text="Profile", bg="#111823", fg="#8b98aa").pack(side=tk.LEFT, padx=(8, 4), pady=5)
        combo = ttk.Combobox(top, textvariable=self._profile, values=("cmd", "powershell", "pwsh"),
                             width=12, state="readonly")
        combo.pack(side=tk.LEFT, pady=5)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.add_tab()
        tk.Button(top, text="＋ 新终端", command=self.add_tab, bg="#1b2432", fg="#d5dde8",
                  relief="flat", padx=10, pady=2).pack(side=tk.LEFT, padx=8, pady=5)
        tk.Button(top, text="关闭标签", command=self.close_tab, bg="#1b2432", fg="#d5dde8",
                  relief="flat", padx=10, pady=2).pack(side=tk.LEFT, pady=5)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def add_tab(self, profile=None):
        profile = profile or self._profile.get()
        idx = len(self.sessions) + 1
        session = TerminalSession(self.nb, self.app, profile=profile,
                                  cwd=getattr(self.app, "workspace", os.getcwd()))
        self.sessions.append(session)
        self.nb.add(session.frame, text="终端 %d · %s" % (idx, profile))
        self.nb.select(session.frame)
        return session

    def close_tab(self):
        try:
            idx = self.nb.index(self.nb.select())
        except Exception:
            return
        session = self.sessions.pop(idx)
        session.close()
        self.nb.forget(idx)

    def _on_close(self):
        for s in self.sessions:
            s.close()
        self.destroy()
