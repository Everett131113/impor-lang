# -*- coding: utf-8 -*-
"""用户键位映射：.impor/keybindings.json -> Tk 绑定。

格式：
[
  {"key": "ctrl+alt+g", "command": "global_search"},
  {"key": "f8", "command": "goto_next_problem"},
  {"key": "shift+f8", "command": "goto_prev_problem"}
]
command 是 App 上的方法名（可带 app. 前缀）。只绑定真实存在的方法，
未知方法/重复键/非法键会进入 errors，不会静默吞掉。
"""
import json
import os

_MODS = {"ctrl": "Control", "control": "Control", "alt": "Alt",
         "shift": "Shift", "win": "Win"}
_SPECIAL = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "del": "Delete", "insert": "Insert",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
}
for _i in range(1, 25):
    _SPECIAL["f%d" % _i] = "F%d" % _i


def parse_key(key):
    parts = [p.strip().lower() for p in str(key or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("空键位")
    mods, base = [], parts[-1]
    for p in parts[:-1]:
        if p not in _MODS:
            raise ValueError("未知修饰键：" + p)
        mods.append(_MODS[p])
    if base in _SPECIAL:
        base = _SPECIAL[base]
    elif len(base) == 1:
        base = "KeyPress-" + base
    else:
        raise ValueError("未知按键：" + base)
    seq = "<" + "-".join(mods + [base]) + ">"
    return seq


class Keymap:
    def __init__(self, app):
        self.app = app
        self.path = os.path.join(app.workspace, ".impor", "keybindings.json")
        self.errors = []
        self.items = []
        self._bound = []

    def load(self):
        self.errors = []
        self.items = []
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("顶层必须是数组")
        except Exception as exc:
            self.errors.append("读取失败：" + str(exc)[:120])
            return []
        seen = {}
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                self.errors.append("第 %d 项不是对象" % (i + 1))
                continue
            command = str(row.get("command") or "").strip()
            if command.startswith("app."):
                command = command[4:]
            fn = getattr(self.app, command, None)
            if not callable(fn):
                self.errors.append("第 %d 项未知命令：%s" % (i + 1, command))
                continue
            try:
                seq = parse_key(row.get("key"))
            except Exception as exc:
                self.errors.append("第 %d 项：%s" % (i + 1, exc))
                continue
            if seq in seen:
                self.errors.append("键位冲突：%s 同时绑定 %s 与 %s" %
                                   (seq, seen[seq], command))
                continue
            seen[seq] = command
            self.items.append((seq, command, fn))
        return list(self.items)

    def uninstall(self):
        for seq, fid in self._bound:
            try:
                self.app.root.unbind_all(seq)
            except Exception:
                pass
        self._bound = []

    def install(self):
        self.uninstall()
        self.load()
        for seq, command, fn in self.items:
            try:
                if self.app.root.bind(seq):
                    self.errors.append("键位 %s 与内置快捷键重复，已跳过（避免触发两次）" % seq)
                    continue
            except Exception:
                pass
            def handler(event=None, _fn=fn, _command=command):
                try:
                    _fn()
                except Exception as exc:
                    try:
                        self.app.toast("键位命令失败 %s：%s" % (_command, exc), "warn")
                    except Exception:
                        pass
                return "break"
            try:
                self.app.root.bind_all(seq, handler, add="+")
                self._bound.append((seq, handler))
            except Exception as exc:
                self.errors.append("绑定失败 %s：%s" % (seq, exc))
        return self.errors

    def describe(self):
        return "\n".join("%s  ->  %s" % (seq, cmd) for seq, cmd, _ in self.items) or "（无自定义键位）"
