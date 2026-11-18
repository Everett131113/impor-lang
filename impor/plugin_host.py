# -*- coding: utf-8 -*-
"""插件独立进程宿主（编辑器 3.1）。

父进程（编辑器）通过 JSON 行协议启动一个子进程来 import/执行插件。插件崩溃、
死循环或异常不会再拖垮编辑器；插件对文件系统的读写、Toast、当前文本等能力
全部通过父进程代理，写权限仍由 `.impor/settings.json` / 环境变量判定。

边界：这是“进程隔离 + Python audit hook 权限强制”，不是操作系统级沙箱。插件代码
仍以当前用户身份运行；未声明的写文件、网络、子进程会在子进程内被 audit hook 阻断，
写入还必须落在工作区内。ctypes/原生扩展仍可能绕过 Python 级钩子。
"""

from __future__ import annotations

import json
import inspect
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional
from .observability import note_swallowed

PROTO_VERSION = 1

UI_NODE_LIMIT = 240
UI_DEPTH_LIMIT = 8


class _UINode:
    """3.2.2：把插件 builder 的 Tk 调用记录成可跨进程传输的 UI 描述。

    只支持面板常用控件与 pack/grid 布局；不执行任何 Tk 代码，
    因此 builder 可以在无显示的子进程中安全运行。
    """

    def __init__(self, kind, text="", **opts):
        self.spec = {"type": kind, "text": str(text)[:4000], "children": []}
        for key in ("command",):
            opts.pop(key, None)
        for key, value in opts.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                self.spec[key] = value if not isinstance(value, str) else value[:4000]
        self._parent = None
        self._root = self
        self._action_cb = None

    def add(self, child):
        """所有节点都委托给根 builder 追加，便于统一编号与限流。"""
        root = getattr(self, "_root", self)
        if root is not self:
            return root.add(child)
        return self

    def _append(self, child):
        if self._parent is not None:
            self._parent.add(child)
        return child

    def _layout(self, mode, **kw):
        layout = {"mode": mode}
        for key in ("fill", "expand", "side", "padx", "pady", "ipadx", "ipady",
                    "row", "column", "sticky", "anchor"):
            if key in kw:
                value = kw[key]
                if isinstance(value, (str, int, float, bool)):
                    layout[key] = value
        if "width" in kw and isinstance(kw["width"], (int, float)):
            self.spec["width"] = int(kw["width"])
        self.spec["layout"] = layout
        return self

    def pack(self, **kw):
        return self._layout("pack", **kw)

    def grid(self, **kw):
        return self._layout("grid", **kw)

    def place(self, **kw):
        return self._layout("pack", **kw)

    def bind(self, *a, **kw):
        return self

    def configure(self, **kw):
        for key, value in kw.items():
            if key == "text":
                self.spec["text"] = str(value)[:4000]
            elif isinstance(value, (str, int, float, bool)):
                self.spec[key] = value
        return self

    config = configure

    def _child(self, kind, text="", **kw):
        if not hasattr(self, "_action_cb"):
            pass
        node = _UINode(kind, text, **{k: v for k, v in kw.items()
                                     if k not in ("command", "variable")})
        node._parent = self
        node._root = getattr(self, "_root", self)
        cmd = kw.get("command")
        if callable(cmd):
            node._action_cb = cmd
        node._root.add(node)
        return node

    def Frame(self, **kw):
        return self._child("frame", **kw)

    def LabelFrame(self, text="", **kw):
        return self._child("labelframe", text, **kw)

    def Label(self, text="", **kw):
        return self._child("label", text, **kw)

    def Button(self, text="", **kw):
        return self._child("button", text, **kw)

    def Entry(self, **kw):
        return self._child("entry", "", **kw)

    def Text(self, **kw):
        return self._child("text", "", **kw)

    def Checkbutton(self, text="", **kw):
        return self._child("checkbutton", text, **kw)

    def Separator(self, **kw):
        return self._child("separator", "", **kw)

    def Treeview(self, columns=(), **kw):
        return self._child("tree", "", columns=list(columns or [])[:12], **kw)

    def StringVar(self, value=""):
        return _UIValue(value)

    def IntVar(self, value=0):
        return _UIValue(value)

    def BooleanVar(self, value=False):
        return _UIValue(value)


class _UIValue:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _UIBuilder(_UINode):
    def __init__(self):
        super().__init__("frame")
        self._root = self
        self._action_seq = 0
        self._actions = {}

    def add(self, child):
        target = child._parent if child._parent is not None else self
        if not isinstance(target, _UINode):
            target = self
        if len(target.spec["children"]) >= UI_NODE_LIMIT:
            return child
        if child._action_cb is not None:
            self._action_seq += 1
            action_id = "act_%d" % self._action_seq
            child.spec["action"] = action_id
            self._actions[action_id] = child._action_cb
        target.spec["children"].append(child.spec)
        return child

    def _depth(self):
        def walk(node, depth):
            if not node.get("children"):
                return depth
            return max(walk(c, depth + 1) for c in node["children"])
        return walk(self.spec, 0)

    def collect_actions(self):
        return dict(self._actions)


def _inside(root, path):
    try:
        return os.path.commonpath([os.path.realpath(root), os.path.realpath(str(path))]) == os.path.realpath(root)
    except (ValueError, OSError):
        return False


def _inside_any(roots, path):
    return any(_inside(root, path) for root in roots)


def _install_guard(workspace, allowed, plugin_path=None):
    """3.2.1：在子进程内用 audit hook 强制权限，而不只是声明。

    默认只允许读工作区与 Python 运行时；写、网络、启动进程必须同时出现在
    权限清单中。写文件还必须落在工作区内，避免插件绕过父进程代理直接落盘。
    """
    allowed = set(allowed or [])
    read_roots = [os.path.realpath(workspace), sys.prefix, sys.base_prefix]
    if plugin_path:
        read_roots.append(os.path.dirname(os.path.realpath(plugin_path)))
    for entry in sys.path:
        if entry:
            read_roots.append(os.path.realpath(entry))

    def hook(event, args):
        if event in ("socket.connect", "socket.bind", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.gethostbyaddr"):
            if "net" not in allowed:
                raise PermissionError("插件未声明 net 权限，已阻断网络访问")
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn",
                     "os.posix_spawn"):
            if "process" not in allowed:
                raise PermissionError("插件未声明 process 权限，已阻断子进程")
        if event == "open" and args:
            path = args[0]
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            writing = ("w" in str(mode) or "a" in str(mode) or "x" in str(mode)
                       or "+" in str(mode))
            if isinstance(flags, int):
                writing = writing or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND |
                                                   os.O_CREAT | os.O_TRUNC))
            if isinstance(path, (str, bytes)):
                if writing:
                    if "write" not in allowed:
                        raise PermissionError("插件未声明 write 权限，已阻断直接写文件")
                    if not _inside(workspace, path):
                        raise PermissionError("插件写入越界，只允许工作区内的文件")
                elif os.fsdecode(path) != os.devnull and not _inside_any(read_roots, path):
                    raise PermissionError("插件读取越界，只允许工作区与运行时文件")
        if event in ("os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes)):
            if not _inside_any(read_roots, args[0]):
                raise PermissionError("插件目录枚举越界，只允许工作区与运行时")
        if event in ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.chown"):
            if "write" not in allowed:
                raise PermissionError("插件未声明 write 权限，已阻断文件系统变更")
            for p in args:
                if isinstance(p, (str, bytes)) and not _inside(workspace, p):
                    raise PermissionError("插件文件系统变更越界，只允许工作区")

    sys.addaudithook(hook)



# ---------------------------------------------------------------- 子进程
class HostAPI:
    def __init__(self, plugin_id, workspace):
        self.plugin_id = plugin_id
        self.workspace = os.path.abspath(workspace)
        self.commands = {}
        self.formatters = {}
        self.panel_actions = {}
        self._rid = 0
        self._proto = sys.stdout
        self._stdin = sys.stdin
        self._closed = False

    def _send(self, obj):
        self._proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proto.flush()

    def _event(self, event, **kw):
        kw.update({"event": event})
        self._send(kw)

    def _request(self, op, args=None, timeout=20):
        self._rid += 1
        rid = self._rid
        self._send({"id": rid, "op": op, "args": args or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._stdin.readline()
            if not line:
                raise RuntimeError("插件宿主连接已关闭")
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == rid:
                if msg.get("error"):
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result")
            if msg.get("op") == "command":
                self._run_command(msg)
            elif msg.get("op") == "panel_action":
                self._run_panel_action(msg)
            elif msg.get("op") == "ping":
                self._send({"id": msg.get("id"), "result": "pong"})
        raise TimeoutError("父进程代理请求超时：%s" % op)

    def _run_command(self, msg):
        name = msg.get("name")
        if name is None:
            name = (msg.get("args") or {}).get("name")
        cb = self.commands.get(name)
        try:
            if not callable(cb):
                raise KeyError("未知命令：%s" % name)
            result = cb()
            if not isinstance(result, (str, int, float, bool, type(None))):
                result = str(result)
            self._send({"id": msg.get("id"), "result": result})
        except Exception as exc:
            self._send({"id": msg.get("id"), "error": str(exc)[:300]})

    def _run_panel_action(self, msg):
        action = msg.get("name") or (msg.get("args") or {}).get("name")
        cb = self.panel_actions.get(action)
        try:
            if not callable(cb):
                raise KeyError("未知面板动作：%s" % action)
            result = cb()
            if not isinstance(result, (str, int, float, bool, type(None))):
                result = str(result)
            self._send({"id": msg.get("id"), "result": result})
        except Exception as exc:
            self._send({"id": msg.get("id"), "error": str(exc)[:300]})

    # ---- 插件可见 API（与同进程 PluginAPI 对齐） ----
    def current_text(self):
        return self._request("current_text") or ""

    def current_path(self):
        return self._request("current_path") or None

    def read_file(self, path):
        return self._request("read_file", {"path": str(path)}) or ""

    def list_files(self, suffix=""):
        out = []
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if not suffix or name.endswith(suffix):
                    out.append(os.path.join(root, name))
        return out

    def toast(self, text):
        return self._request("toast", {"text": str(text)})

    def write_file(self, path, content):
        return self._request("write_file", {"path": str(path), "content": content})

    def write_current(self, content):
        return self._request("write_current", {"content": content})

    def register_command(self, title, callback, shortcut=""):
        self.commands[str(title)] = callback
        self._event("command", title=str(title), shortcut=str(shortcut or ""))

    def register_panel(self, title, builder):
        """3.2.2：在子进程执行 builder，把控件树序列化成 UI 描述交父进程渲染。

        支持 Frame / LabelFrame / Label / Button / Entry / Text / Checkbutton /
        Separator / Treeview 与 pack/grid；不执行任何 Tk 调用，builder 崩溃
        只影响本面板，不影响编辑器。
        """
        ui = {"type": "frame", "children": []}
        try:
            root = _UIBuilder()
            try:
                params = list(inspect.signature(builder).parameters.values())
                npos = len([p for p in params
                            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                            and p.default is p.empty])
                wants_api = npos >= 2 or any(p.name == "api" for p in params)
            except Exception:
                wants_api = True
            if wants_api:
                builder(root, self)
            else:
                builder(root)
            ui = root.spec
            for action_id, cb in root.collect_actions().items():
                self.panel_actions[action_id] = cb
        except Exception as exc:
            ui = {"type": "frame", "children": [
                {"type": "label", "text": "面板构建失败：%s" % str(exc)[:300],
                 "children": []}]}
        self._event("panel", title=str(title), ui=ui)

    def register_status_item(self, text, callback=None):
        self._event("status", text=str(text))

    def register_theme(self, name, colors):
        if isinstance(colors, dict):
            self._event("theme", name=str(name), colors={str(k): str(v) for k, v in colors.items()})

    def register_formatter(self, name, func):
        self.formatters[str(name)] = func
        self._event("formatter", name=str(name))

    def invoke_formatter(self, name, text):
        fn = self.formatters.get(name)
        if not callable(fn):
            raise KeyError("未知格式化器：%s" % name)
        out = fn(text)
        if not isinstance(out, str):
            raise ValueError("格式化器必须返回字符串")
        return out


def _install_tk_shim():
    """3.2.2：把插件里常见的 tk.Label(parent, ...) 写法转成 UI 描述代理。

    子进程没有真实 Tk，插件只能用控件描述；这个 shim 让旧插件不用改写也能
    在隔离进程里构建面板。
    """
    try:
        import tkinter as tk
        names = ("Frame", "LabelFrame", "Label", "Button", "Entry", "Text",
                 "Checkbutton", "Separator", "Treeview")
        for name in names:
            if hasattr(tk, name) and hasattr(_UINode, name):
                setattr(tk, name, (lambda parent, _n=name, **kw:
                                   getattr(parent, _n)(**kw)))
    except Exception:
        pass


def child_main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        return 2
    path, workspace = argv[0], argv[1]
    plugin_id = os.path.basename(path)
    raw = os.environ.get("IMPOR_PLUGIN_PERMISSIONS", "read")
    allowed = {x.strip().lower() for x in raw.replace(";", ",").split(",") if x.strip()}
    if "all" in allowed:
        allowed.update({"read", "write", "net", "process"})
    _install_guard(workspace, allowed, path)
    api = HostAPI(plugin_id, workspace)
    _install_tk_shim()
    # 插件自己 print 会污染协议；协议走保存的 stdout，插件 stdout 改到 stderr。
    sys.stdout = sys.stderr
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "impor_isolated_%s" % os.path.splitext(plugin_id)[0], path)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载插件")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        register = getattr(mod, "register", None)
        if not callable(register):
            raise RuntimeError("缺少 register(api)")
        register(api)
        api._event("ready", plugin=plugin_id, protocol=PROTO_VERSION)
        while True:
            line = api._stdin.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            op = msg.get("op")
            if op == "command":
                api._run_command(msg)
            elif op == "panel_action":
                api._run_panel_action(msg)
            elif op == "formatter":
                try:
                    args = msg.get("args") or {}
                    res = api.invoke_formatter(args.get("name") or msg.get("name"),
                                               args.get("text", ""))
                    api._send({"id": msg.get("id"), "result": res})
                except Exception as exc:
                    api._send({"id": msg.get("id"), "error": str(exc)[:300]})
            elif op == "shutdown":
                break
            elif op == "ping":
                api._send({"id": msg.get("id"), "result": "pong"})
        return 0
    except Exception as exc:
        try:
            sys.stdout = api._proto
            api._event("error", plugin=plugin_id, error=str(exc)[:300])
        except Exception:
            pass
        return 1
    finally:
        try:
            sys.stdout = sys.__stdout__
        except Exception:
            pass


# ---------------------------------------------------------------- 父进程
class IsolatedPluginProcess:
    def __init__(self, path, workspace, handler: Optional[Callable] = None,
                 allow_write=False, python=None):
        self.path = os.path.abspath(path)
        self.plugin_id = os.path.basename(self.path)
        self.workspace = os.path.abspath(workspace)
        self.handler = handler
        self.allow_write = bool(allow_write)
        self.python = python or sys.executable
        self.proc = None
        self.events: List[dict] = []
        self.responses: Dict[int, dict] = {}
        self._cv = threading.Condition()
        self._reader = None
        self._rid = 0
        self.error = ""
        self.alive = False
        self.restarts = 0
        self.context_text = ""
        self.context_path = None
        self.permissions = {"read": True, "write": False, "net": False, "process": False}

    def start(self, timeout=8):
        cmd = [self.python, "-X", "utf8", "-m", "impor.plugin_host",
               self.path, self.workspace, "1" if self.allow_write else "0"]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["IMPOR_PLUGIN_ALLOW_WRITE"] = "1" if self.allow_write else "0"
        perms = dict(getattr(self, "permissions", {}) or {})
        allowed = [k for k in ("read", "net", "process") if perms.get(k)]
        if self.allow_write:
            allowed.append("write")
        env["IMPOR_PLUGIN_PERMISSIONS"] = ",".join(allowed) or "read"
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.workspace, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as exc:
            self.error = str(exc)[:200]
            return False
        self.alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._cv:
                if any(e.get("event") == "ready" for e in self.events):
                    return True
                if not self.alive:
                    break
            time.sleep(0.03)
        self.error = self.error or "插件宿主启动超时"
        self.stop()
        return False

    def _send(self, obj):
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("插件进程已退出")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("op") in ("read_file", "write_file", "current_text", "current_path",
                                      "toast", "write_current"):
                    self._serve_request(msg)
                    continue
                if "id" in msg and msg.get("id") is not None:
                    rid = int(msg["id"])
                    # 3.2.2：父进程请求用负数 id，子进程代理请求用正数 id；
                    # 正数 id 是子进程代理请求的回应，不能塞进父进程等待表。
                    if rid < 0:
                        with self._cv:
                            self.responses[rid] = msg
                            self._cv.notify_all()
                    continue
                if msg.get("event") == "error":
                    self.error = str(msg.get("error") or "")
                with self._cv:
                    self.events.append(msg)
                    self._cv.notify_all()
        except Exception as exc:
            self.error = str(exc)[:200]
        finally:
            self.alive = False
            with self._cv:
                self._cv.notify_all()

    def _serve_request(self, msg):
        rid = msg.get("id")
        op = msg.get("op")
        args = msg.get("args") or {}
        try:
            if self.handler is None:
                raise PermissionError("没有代理处理器")
            result = self.handler(op, args, self)
            self._send({"id": rid, "result": result})
        except Exception as exc:
            try:
                self._send({"id": rid, "error": str(exc)[:300]})
            except Exception:
                note_swallowed("plugin host")

    def request(self, op, args=None, timeout=12):
        self._rid -= 1
        rid = self._rid
        self._send({"id": rid, "op": op, "args": args or {}})
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self.responses:
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError("插件命令超时：%s" % op)
                if not self.alive:
                    raise RuntimeError("插件进程已退出：" + (self.error or "未知原因"))
                self._cv.wait(min(0.1, remain))
            msg = self.responses.pop(rid)
        if msg.get("error"):
            raise RuntimeError(str(msg["error"]))
        return msg.get("result")

    def invoke_command(self, name, context_text="", context_path=None):
        self.context_text = context_text or ""
        self.context_path = context_path
        return self.request("command", {"name": name})

    def invoke_formatter(self, name, text):
        return self.request("formatter", {"name": name, "text": text})

    def stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                self._send({"op": "shutdown"})
                self.proc.wait(timeout=2)
        except Exception:
            try:
                if self.proc and self.proc.poll() is None:
                    self.proc.kill()
            except Exception:
                note_swallowed("plugin host")
        self.alive = False

    def restart(self, timeout=6):
        """3.2.1：子进程崩溃后自动重启（最多 3 次，避免崩溃循环）。"""
        if getattr(self, "restarts", 0) >= 3:
            self.error = "插件连续崩溃 3 次，已停止自动重启"
            return False
        self.restarts = getattr(self, "restarts", 0) + 1
        try:
            self.stop()
        except Exception:
            note_swallowed("plugin host")
        self.events = []
        self.responses = {}
        self.error = ""
        ok = self.start(timeout=timeout)
        if not ok:
            self.error = self.error or "自动重启失败"
        return ok

    def call_with_restart(self, op, args=None, timeout=12, retry=True):
        try:
            return self.request(op, args, timeout=timeout)
        except Exception:
            if not retry or not self.restart():
                raise
            return self.request(op, args, timeout=timeout)


if __name__ == "__main__":
    sys.exit(child_main())
