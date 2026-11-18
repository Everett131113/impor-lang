# -*- coding: utf-8 -*-
"""Impor (I++) 标准 DAP 调试适配器（stdio）。

VS Code 通过 `python -m impor dap` 启动，复用解释器既有断点/单步/调用栈。
协议范围：initialize / launch / setBreakpoints / configurationDone /
threads / stackTrace / scopes / variables / evaluate / continue /
next / stepIn / stepOut / disconnect。
"""
import json
import os
import re
import sys
import threading

from .interpreter import ImporException, Interpreter, strip_header
from .parser import Parser
from .lexer import tokenize


class DAPServer:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self._seq = 0
        self._lock = threading.Lock()
        self.program = ""
        self.source = ""
        self.body = ""
        self.head_skip = 0
        self.breakpoints = {}
        self.bp_specs = {}          # abs path -> list of {line, condition, hitCondition, logMessage}
        self.log_points = {}        # body line -> log message
        self.exception_mode = None  # None / "uncaught" / "all"
        self.stop_on_entry = False
        self.interp = None
        self.thread = None
        self.paused = threading.Event()
        self.state = "waiting"
        self.cur_line = 1
        self.cur_env = {}
        self.started = False
        self.killed = False

    # ---------- 协议 ----------
    def _read(self):
        headers = {}
        while True:
            line = self.stdin.readline()
            if not line:
                return None
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        if n <= 0:
            return None
        return json.loads(self.stdin.read(n).decode("utf-8", "replace"))

    def _send(self, payload):
        with self._lock:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.stdout.write(("Content-Length: %d\r\n\r\n" % len(data)).encode("ascii"))
            self.stdout.write(data)
            self.stdout.flush()

    def _reply(self, req, body=None):
        msg = {"type": "response", "request_seq": req.get("seq", 0),
               "success": True, "command": req.get("command", "")}
        if body:
            msg["body"] = body
        self._send(msg)

    def _event(self, name, body=None):
        self._send({"type": "event", "event": name, "body": body or {}})

    def serve(self):
        while not self.killed:
            req = self._read()
            if req is None:
                break
            self._dispatch(req)
        return 0

    def _dispatch(self, req):
        cmd = req.get("command", "")
        try:
            fn = getattr(self, "_cmd_" + cmd, None)
            if fn:
                fn(req)
            else:
                self._reply(req)
        except Exception as exc:
            self._send({"type": "response", "request_seq": req.get("seq", 0),
                        "success": False, "command": cmd,
                        "message": str(exc)})

    def _cmd_initialize(self, req):
        self._reply(req, {
            "supportsConfigurationDoneRequest": True,
            "supportsSetVariable": False,
            "supportsConditionalBreakpoints": True,
            "supportsHitConditionalBreakpoints": True,
            "supportsLogPoints": True,
            "supportsEvaluateForHovers": True,
            "supportsTerminateRequest": False,
            "exceptionBreakpointFilters": [
                {"filter": "uncaught", "label": "未捕获异常", "default": False},
                {"filter": "all", "label": "所有异常（含被 catch 捕获）", "default": False},
            ],
        })
        self._event("initialized")

    def _cmd_launch(self, req):
        args = req.get("arguments") or {}
        self.program = os.path.abspath(args.get("program") or "")
        self.stop_on_entry = bool(args.get("stopOnEntry"))
        try:
            with open(self.program, "r", encoding="utf-8") as f:
                self.source = f.read()
        except Exception as exc:
            self._reply(req, {})
            self._event("output", {"category": "stderr", "output": "无法读取程序：%s\n" % exc})
            self._event("terminated")
            return
        self.body = strip_header(self.source)
        self.head_skip = len(self.source.split("\n")) - len(self.body.split("\n"))
        self._reply(req, {})

    def _cmd_setBreakpoints(self, req):
        args = req.get("arguments") or {}
        source = args.get("source") or {}
        path = os.path.abspath(source.get("path") or self.program)
        specs = []
        for b in args.get("breakpoints") or []:
            try:
                line = int(b.get("line", 0)) - self.head_skip
            except Exception:
                continue
            if line <= 0:
                continue
            specs.append({
                "line": line,
                "condition": (b.get("condition") or "").strip(),
                "hitCondition": (b.get("hitCondition") or "").strip(),
                "logMessage": (b.get("logMessage") or "").strip(),
            })
        self.bp_specs[path] = specs
        self.breakpoints[path] = sorted({s["line"] for s in specs})
        body = {"breakpoints": [
            {"verified": True, "line": s["line"] + self.head_skip,
             "message": " / ".join(x for x in (
                 "条件断点" if s["condition"] else "",
                 ("命中条件 " + s["hitCondition"]) if s["hitCondition"] else "",
                 "日志点" if s["logMessage"] else "") if x)}
            for s in specs
        ]}
        self._reply(req, body)

    def _cmd_setExceptionBreakpoints(self, req):
        filters = (req.get("arguments") or {}).get("filters") or []
        if "all" in filters:
            self.exception_mode = "all"
        elif "uncaught" in filters:
            self.exception_mode = "uncaught"
        else:
            self.exception_mode = None
        if self.interp:
            self.interp.exception_break_mode = self.exception_mode
        self._reply(req, {"breakpoints": [
            {"verified": True, "id": f, "label": "所有异常" if f == "all" else "未捕获异常"}
            for f in filters
        ]})

    def _cmd_configurationDone(self, req):
        self._reply(req, {})
        if not self.started:
            self.started = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        try:
            tree = Parser(tokenize(self.body)).program()
            interp = Interpreter()
            self.interp = interp
            interp.debug_mode = True
            interp.filename = self.program
            interp.debug_pause_callback = self._pause
            bps = set()
            conds, mins, hits, logs = {}, {}, {}, {}
            for path, specs in self.bp_specs.items():
                if os.path.abspath(path) != self.program:
                    continue
                for spec in specs:
                    ln = spec["line"]
                    bps.add(ln)
                    if spec["condition"]:
                        conds[ln] = spec["condition"]
                    if spec["hitCondition"]:
                        hits[ln] = spec["hitCondition"]
                    if spec["logMessage"]:
                        logs[ln] = spec["logMessage"]
            interp.breakpoints = bps
            interp.breakpoint_conditions = conds
            interp.breakpoint_hit_conditions = hits
            interp.exception_break_mode = self.exception_mode
            interp.exception_pause_callback = self._pause_exception
            self.log_points = logs
            if self.stop_on_entry:
                interp.breakpoints.add(1)
            interp.run(tree)
            self.state = "terminated"
            self._event("terminated")
        except ImporException as exc:
            if self.exception_mode == "uncaught":
                self._pause_exception(exc, getattr(exc, "lineno", 0))
            self.state = "terminated"
            self._event("output", {"category": "stderr", "output": str(exc) + "\n"})
            self._event("terminated")
        except Exception as exc:
            self.state = "terminated"
            self._event("output", {"category": "stderr", "output": str(exc) + "\n"})
            self._event("terminated")

    def _log_message(self, message, env):
        def repl(m):
            try:
                return str(self.interp.eval_expr_text(m.group(1))) if self.interp else m.group(0)
            except Exception:
                return "<" + m.group(1) + ">"
        return re.sub(r"\{([^{}]+)\}", repl, message)

    def _pause(self, lineno, env):
        if self.killed:
            return
        if lineno in self.log_points:
            self._event("output", {"category": "console", "output":
                                   self._log_message(self.log_points[lineno], env) + "\n"})
            return
        self.cur_line = lineno + self.head_skip
        self.cur_env = env
        self.state = "paused"
        self.paused.clear()
        self._event("stopped", {"reason": "breakpoint", "threadId": 1,
                                "allThreadsStopped": True})
        self.paused.wait(3600)

    def _pause_exception(self, exc, lineno):
        if self.killed:
            return
        self.cur_line = max(1, (lineno or 0) + self.head_skip)
        self.cur_env = getattr(self.interp, "cur_env", {}) if self.interp else {}
        self.state = "paused"
        self.paused.clear()
        self._event("stopped", {"reason": "exception", "threadId": 1,
                                "allThreadsStopped": True, "text": str(exc),
                                "description": getattr(exc, "name", "异常")})
        self.paused.wait(3600)

    # ---------- 调试命令 ----------
    def _resume(self):
        self.state = "running"
        self.paused.set()

    def _cmd_continue(self, req):
        if self.interp:
            self.interp.step_mode = None
        self._resume()
        self._reply(req, {"allThreadsContinued": True})

    def _cmd_next(self, req):
        if self.interp:
            self.interp.step_mode = "over"
            self.interp._dbg_step_target = self.interp._dbg_depth()
        self._resume()
        self._reply(req, {})

    def _cmd_stepIn(self, req):
        if self.interp:
            self.interp.step_mode = "into"
        self._resume()
        self._reply(req, {})

    def _cmd_stepOut(self, req):
        if self.interp:
            self.interp.step_mode = "out"
            self.interp._dbg_step_target = self.interp._dbg_depth() - 1
        self._resume()
        self._reply(req, {})

    def _cmd_threads(self, req):
        self._reply(req, {"threads": [{"id": 1, "name": "Impor 主线程"}]})

    def _cmd_stackTrace(self, req):
        frames = []
        if self.interp:
            stack = list(self.interp.call_stack)
            for i, fr in enumerate(reversed(stack)):
                line = (fr.lineno or 0) + self.head_skip
                frames.append({"id": i + 1, "name": fr.name or "<main>",
                               "line": max(1, line), "column": 1,
                               "source": {"path": self.program, "name": os.path.basename(self.program)}})
        self._reply(req, {"stackFrames": frames, "totalFrames": len(frames)})

    def _cmd_scopes(self, req):
        self._reply(req, {"scopes": [{"name": "局部变量", "variablesReference": 1,
                                      "expensive": False}]})

    def _cmd_variables(self, req):
        env = self.cur_env or {}
        if hasattr(env, "store"):
            merged = {}
            cur = env
            chain = []
            while cur is not None:
                chain.append(cur)
                cur = getattr(cur, "outer", None)
            for frame_env in reversed(chain):
                merged.update(getattr(frame_env, "store", {}) or {})
            env = merged
        vals = []
        for name, val in sorted(env.items(), key=lambda kv: str(kv[0])):
            name = str(name)
            if name.startswith("__") or callable(val):
                continue
            try:
                text = repr(val)
            except Exception:
                text = "<无法显示>"
            vals.append({"name": name, "value": text[:300],
                         "type": type(val).__name__, "variablesReference": 0})
        self._reply(req, {"variables": vals})

    def _cmd_evaluate(self, req):
        expr = (req.get("arguments") or {}).get("expression", "")
        val = ""
        try:
            if self.interp:
                val = repr(self.interp.eval_expr_text(expr))
        except Exception as exc:
            val = "错误：" + str(exc)
        self._reply(req, {"result": val, "variablesReference": 0})

    def _cmd_disconnect(self, req):
        self.killed = True
        self.paused.set()
        self._reply(req, {})
        self._event("terminated")


def main_dap(args):
    return DAPServer().serve()
