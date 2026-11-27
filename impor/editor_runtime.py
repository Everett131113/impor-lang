# -*- coding: utf-8 -*-
"""Impor 编辑器拆分模块（3.6.7）。运行期共享 editor 模块全局名。"""
import os
import sys
import re
import json
import time
import math
import random
import shutil
import subprocess
import threading
import queue
import webbrowser
import difflib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .editor_theme import THEME, DARK_THEME, LIGHT_THEME, HIGH_CONTRAST_THEME
from .editor_widgets import round_rect, FlatButton, TabStrip, ActivityBar, Minimap, MiniScrollbar
from .observability import note_swallowed
from .stability import stable_from_env, editor_limits, kill_tree
from .pathsafe import rel_quiet


class RuntimeMixin:
    def run_selection(self):
        pane = self.current_pane()
        if not pane:
            return
        try:
            src = pane.text.get("sel.first", "sel.last")
        except tk.TclError:
            return self.run_code()
        if not src.strip():
            return self.run_code()
        self.run_code(src)

    def open_terminal(self):
        try:
            from .studio_ext import TerminalWindow
            old = getattr(self, "_terminal_win", None)
            if old is not None and old.winfo_exists():
                old.lift()
                old.focus_force()
                return
            self._terminal_win = TerminalWindow(self)
        except Exception as exc:
            messagebox.showerror("终端启动失败", str(exc), parent=self.root)

    def _term_cwd_text(self):
        try:
            if os.path.commonpath([os.path.abspath(self._term_cwd), os.path.abspath(ROOT_DIR)]) == os.path.abspath(ROOT_DIR):
                rel = rel_quiet(self._term_cwd, ROOT_DIR).replace("\\", "/")
                return "cwd: " + (rel if rel != "." else "项目根")
            return "cwd: " + self._term_cwd
        except Exception:
            return "cwd: " + str(getattr(self, "_term_cwd", ROOT_DIR))

    def _term_hist_prev(self, e=None):
        if not self._term_hist:
            return "break"
        self._term_hist_pos = max(0, self._term_hist_pos - 1)
        self.task_entry.delete(0, tk.END)
        self.task_entry.insert(0, self._term_hist[self._term_hist_pos])
        return "break"

    def _term_hist_next(self, e=None):
        if not self._term_hist:
            return "break"
        self._term_hist_pos = min(len(self._term_hist), self._term_hist_pos + 1)
        self.task_entry.delete(0, tk.END)
        if self._term_hist_pos < len(self._term_hist):
            self.task_entry.insert(0, self._term_hist[self._term_hist_pos])
        return "break"

    def _term_interrupt(self, e=None):
        try:
            if self._term_proc is not None and self._term_proc.poll() is None:
                # 4.7.3：kill() 只杀 shell，gcc/exe 会变孤儿继续吃 CPU；
                # 改为杀整棵进程树。
                pid = getattr(self._term_proc, "pid", None)
                ok, detail = (False, "no pid")
                if pid:
                    ok, detail = kill_tree(pid)
                if not ok:
                    try:
                        self._term_proc.kill()
                    except Exception:
                        pass
                self.append_out("^C 已终止当前命令（进程树）" + (("：" + detail[:80]) if detail else ""), "warn")
                return "break"
        except Exception as exc:
            note_swallowed("editor.term.interrupt", exc)
        self.task_entry.insert("insert", "^C")
        return "break"

    def _task_run_entry(self, e=None):
        cmd = (self.task_entry.get() or "").strip()
        if not cmd:
            return "break"
        self.task_entry.delete(0, tk.END)
        if not self._term_hist or self._term_hist[-1] != cmd:
            self._term_hist.append(cmd)
        self._term_hist_pos = len(self._term_hist)
        low = cmd.lower()
        if low in ("pwd", "cwd"):
            self.append_out(self._term_cwd, "dim")
            return "break"
        if low in ("clear", "cls"):
            self.out.delete("1.0", tk.END)
            return "break"
        if low == "cd" or low.startswith("cd "):
            target = cmd[2:].strip().strip('"').strip("'")
            target = os.path.expanduser(target or "~")
            path = target if os.path.isabs(target) else os.path.join(self._term_cwd, target)
            if os.path.isdir(path):
                self._term_cwd = os.path.abspath(path)
                self.term_cwd_label.configure(text=self._term_cwd_text())
                self.append_out("cd " + self._term_cwd, "dim")
            else:
                self.append_out("目录不存在：" + path, "err")
            return "break"
        if cmd.startswith("impor "):
            cmd = '"%s" -m %s' % (sys.executable, cmd)
        self._run_task_cmd(cmd, cwd=self._term_cwd)
        return "break"

    def _task_run(self, kind):
        pane = self.current_pane()
        target = pane.path if pane and pane.path else None
        if not target:
            self.toast("先保存当前文件，再运行任务")
            return
        tasks = (self.project_settings or {}).get("tasks") if isinstance((self.project_settings or {}).get("tasks"), dict) else {}
        tmpl = tasks.get(kind)
        if tmpl:
            try:
                cmd = str(tmpl).format(file=target, workspace=self.workspace,
                                        python=sys.executable, kind=kind)
            except Exception:
                cmd = str(tmpl)
        else:
            cmd = '"%s" -m impor %s "%s"' % (sys.executable, kind, target)
        self._run_task_cmd(cmd, cwd=ROOT_DIR)

    def _run_task_cmd(self, cmd, cwd=None, env=None):
        if not self._ensure_trusted("终端 / 任务"):
            return
        cwd = cwd or self._term_cwd or ROOT_DIR
        self.show_bottom("out")
        self._task_problems = []
        self.append_out("$ " + cmd + "\n", "dim")
        self._set_running(True)
        self._queue.put(("out", "$ " + cmd + "  [" + cwd + "]"))

        def ansi_payload(line):
            raw = line.rstrip("\n")
            kind = None
            if "\x1b[" in raw:
                if re.search(r"\x1b\[(31|91)m", raw):
                    kind = "err"
                elif re.search(r"\x1b\[(32|92)m", raw):
                    kind = "ok"
                elif re.search(r"\x1b\[(33|93)m", raw):
                    kind = "warn"
                raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
            return raw, kind

        def worker():
            try:
                p = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace")
                self._term_proc = p
                for line in p.stdout:
                    raw, kind = ansi_payload(line)
                    self._queue.put(("outtag", (raw, kind)))
                    self._queue.put(("problem", raw))
                rc = p.wait()
                self._queue.put(("out", "\n退出码 %d\n" % rc))
            except Exception as exc:
                self._queue.put(("out", "任务运行失败：%s" % exc))
            finally:
                self._term_proc = None
                self._queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def open_pkg_manager(self):
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("Impor 包管理器")
        win.transient(self.root)
        win.geometry("620x420")
        out = tk.Text(win, bg="#090c11", fg="#d5dde8", relief="flat", font=(self.mono, 9))
        out.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        row = tk.Frame(win, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=10, pady=(0, 10))
        entry = tk.Entry(row, bg="#17202e", fg=THEME["fg"], insertbackground=THEME["accent"],
                         relief="flat", font=(self.mono, 10))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        def cli(args):
            out.insert(tk.END, "$ impor pkg " + " ".join(args) + "\n")
            out.see(tk.END)

            def worker():
                try:
                    p = subprocess.run([sys.executable, "-m", "impor", "pkg"] + args,
                                       cwd=ROOT_DIR, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=60)
                    self.root.after(0, lambda: (out.insert(tk.END, (p.stdout or "") + (p.stderr or "") + "\n"),
                                                out.see(tk.END)))
                except Exception as ex:
                    self.root.after(0, lambda: out.insert(tk.END, str(ex) + "\n"))
            threading.Thread(target=worker, daemon=True).start()

        FlatButton(row, "列表", command=lambda: cli(["list"]), width=62).pack(side=tk.LEFT, padx=3)
        FlatButton(row, "安装", command=lambda: cli(["install", entry.get().strip()]) if entry.get().strip() else None,
                   width=62, kind="primary").pack(side=tk.LEFT, padx=3)
        FlatButton(row, "卸载", command=lambda: cli(["remove", entry.get().strip()]) if entry.get().strip() else None,
                   width=62).pack(side=tk.LEFT, padx=3)
        cli(["list"])

    def run_config(self):
        import json, shlex, subprocess, sys
        cfg_path = os.path.join(self.workspace, ".imporrun")
        try:
            data = json.load(open(cfg_path, encoding="utf-8"))
        except Exception:
            data = {}
        pane = self.current_pane()
        entry = data.get("entry") or (pane.path if pane and pane.path else "")
        args = data.get("args") or ""
        cwd = data.get("cwd") or self.workspace
        env_data = data.get("env") or {}
        env_text = "; ".join("%s=%s" % (k, v) for k, v in env_data.items())
        win = tk.Toplevel(self.root, bg=THEME["panel"])
        apply_window_icon(win)
        win.title("运行配置 · .imporrun")
        win.transient(self.root)
        win.geometry("560x260")
        vars_ = {}
        for label, key, value in (("入口文件", "entry", entry), ("参数", "args", args),
                                  ("工作目录", "cwd", cwd), ("环境变量", "env", env_text)):
            row = tk.Frame(win, bg=THEME["panel"])
            row.pack(fill=tk.X, padx=14, pady=6)
            tk.Label(row, text=label, width=8, anchor="w", bg=THEME["panel"],
                     fg=THEME["fg"]).pack(side=tk.LEFT)
            var = tk.StringVar(value=str(value))
            vars_[key] = var
            tk.Entry(row, textvariable=var, bg=THEME["editor"], fg=THEME["fg"],
                     insertbackground=THEME["fg"], relief="flat").pack(side=tk.LEFT, fill=tk.X, expand=True)

        def collect():
            env = {}
            for part in vars_["env"].get().split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip():
                        env[k.strip()] = v.strip()
            return {"entry": vars_["entry"].get().strip(), "args": vars_["args"].get().strip(),
                    "cwd": vars_["cwd"].get().strip() or self.workspace, "env": env}

        def save():
            with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(collect(), f, ensure_ascii=False, indent=2)
            self.toast("已保存 " + os.path.basename(cfg_path), "ok")

        def run():
            cfg = collect()
            with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            entry_fp = cfg["entry"]
            if not entry_fp:
                self.toast("请填写入口文件")
                return
            if not os.path.isabs(entry_fp):
                entry_fp = os.path.join(self.workspace, entry_fp)
            cmd = [sys.executable, "-X", "utf8",
                   os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Impor.py"), entry_fp]
            cmd += shlex.split(cfg["args"])
            env = os.environ.copy()
            env.update(cfg["env"])
            self._run_task_cmd(subprocess.list2cmdline(cmd), cwd=cfg["cwd"], env=env)
            win.destroy()
        bar = tk.Frame(win, bg=THEME["panel"])
        bar.pack(fill=tk.X, padx=14, pady=10)
        FlatButton(bar, "运行", kind="primary", width=88, command=run).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "保存", width=72, command=save).pack(side=tk.RIGHT, padx=4)
        FlatButton(bar, "取消", width=72, command=win.destroy).pack(side=tk.RIGHT, padx=4)

    def stop_code(self):
        """4.7.3：协作式停止——把剩余额度压到 0，下一条语句处熔断。

        解释器没有 request_stop：它按语句计数，`stab.on_stmt` 每条语句都会
        检查时间/步数额度。把额度压到 0 等价于「下一语句立刻停」，且不需要
        去碰运行线程的栈（那会崩解释器）。
        """
        interp = getattr(self, "_interp", None)
        if interp is None or getattr(interp, "stab", None) is None:
            return False
        self._stop_requested = True
        try:
            interp.stab.limits.seconds = 0.0
            interp.stab.limits.steps = 0
        except Exception:
            return False
        self.append_out("⏹ 已请求停止：下一条语句处中断…", "warn")
        return True

    def run_code(self, src=None, coverage=False):
        if not self._ensure_trusted("运行"):
            return
        if self._running:
            # 4.7.3：运行中再按一次运行键 = 停止（比去找「停止」按钮快）
            if self.stop_code():
                return
            return
        pane = self.current_pane()
        if pane is None:
            return
        if src is None:
            src = pane.text.get("1.0", "end-1c")
        if coverage:
            self._coverage_target = pane.path
            self._last_coverage = None
            self._coverage_exec = set()
            self._coverage_offset = 0
        try:
            pane.text.tag_remove("exec", "1.0", "end")
            pane._exec_line = None
            pane.bp_hits = {}
        except Exception:
            pass
        self.clear_output()
        self.show_bottom("out")
        # 5.1.1：每次运行自动弹出独立输出窗口（已存在则只提窗，不重复创建）
        self.open_out_window()
        self.append_out("▶ 运行 " + (pane.path or "未命名") + " …")
        self._set_running(True)
        if self._debug_mode or pane.breakpoints:
            self._run_sync(src, pane.path, coverage=coverage)
        else:
            threading.Thread(target=self._run_worker,
                             args=(src, pane.path, coverage), daemon=True).start()

    def _run_worker(self, src, path, coverage=False):
        try:
            self._run_worker_inner(src, path, coverage)
        except Exception as e:
            self._queue.put(("out", f"🔴 底层异常：{e}"))
        finally:
            self._stop_requested = False
            self._queue.put(("done", None))

    def _run_worker_inner(self, src, path, coverage=False):
        try:
            try:
                body = strip_header(src)
            except ImporException as e:
                self._queue.put(("out", f"❌ {e.name}: {e.msg}"))
                self._queue.put(("out", f"  修复：{fix_for(e.name, e.msg)}"))
                return
            except Exception as e:
                self._queue.put(("out", f"❌ 头部解析失败: {e}"))
                return
            try:
                ast = Parser(tokenize(body)).program()
            except SyntaxError as e:
                _ln, _col, _msg = parse_position(str(e))
                _loc = (f"第{_ln}行" + (f" 列{_col}" if _col else "") + " " if _ln else "")
                self._queue.put(("out", f"🔴 语法错误：{_loc}{_msg}"))
                self._queue.put(("out", f"  修复：{fix_for('语法错误', _msg)}"))
                return
            except Exception as e:
                self._queue.put(("out", f"🔴 解析失败：{e}"))
                return
            interp = Interpreter(log_cb=lambda t: self._queue.put(("out", t)),
                                 snapshot_cb=lambda g, w: self._queue.put(("watch", (g, set(w)))),
                                 sandbox_mode=self._sandbox_mode,
                                 gui_root=self.root,
                                 debug_mode=False,
                                 filename=path)
            self._interp = interp
            if stable_from_env():
                # 4.1：编辑器里的「安全运行」由环境变量打开（IMPOR_STABLE=1），
                # 每次运行重置额度，熔断后编辑器本身不受影响。
                interp.enable_stability()
            else:
                # 4.7.3：默认也挂一层宽松闸门（30 秒 / 500 万步）。
                # 死循环脚本不再永久占住运行线程、让「运行」键一直灰着；
                # IMPOR_EDITOR_RUN_SECONDS=0 可完全关掉，回到 4.7.2 行为。
                interp.enable_stability(editor_limits())
            self._queue.put(("interp", interp))
            if coverage:
                interp.enable_coverage(True)
            try:
                interp.run(ast)
            except ImporException as err:
                if getattr(self, "_stop_requested", False) and err.name in ("TimeLimit", "StepLimit"):
                    self._queue.put(("out", "⏹ 已停止（用户中止，第%d 行）" % (err.lineno or 0)))
                else:
                    self._queue.put(("out", f"🔴 运行异常 第{err.lineno}行: {err.name}: {err.msg}"))
                    self._queue.put(("out", f"  为什么：{explain(err.name, err.msg)[0]}"))
                    self._queue.put(("out", f"  修复：{fix_for(err.name, err.msg)}"))
                    if getattr(err, "stack", None):
                        for fr in reversed(err.stack):
                            self._queue.put(("out", f"  {fr.name}（第{fr.lineno}行）"))
            except Exception as e:
                self._queue.put(("out", f"🔴 底层异常：{e}"))
            if coverage:
                offset = 1 if src.split("\n", 1)[0].startswith("important<<") else 0
                self._queue.put(("coverage", (path, interp.coverage_snapshot(),
                                              self._coverage_exec_lines(body), offset)))
        except Exception as e:
            self._queue.put(("out", f"🔴 底层异常：{e}"))

    def _run_sync(self, src, path, coverage=False):
        try:
            body = strip_header(src)
        except ImporException as e:
            self.append_out(f"❌ {e.name}: {e.msg}")
            self.append_out(f"  修复：{fix_for(e.name, e.msg)}")
            self._set_running(False)
            return
        try:
            ast = Parser(tokenize(body)).program()
        except SyntaxError as e:
            _ln, _col, _msg = parse_position(str(e))
            _loc = (f"第{_ln}行" + (f" 列{_col}" if _col else "") + " " if _ln else "")
            self.append_out(f"🔴 语法错误：{_loc}{_msg}")
            self.append_out(f"  修复：{fix_for('语法错误', _msg)}")
            self._set_running(False)
            return
        interp = Interpreter(log_cb=self.append_out, snapshot_cb=self._watch_update,
                             sandbox_mode=self._sandbox_mode, gui_root=self.root,
                             debug_mode=True, debug_pause_callback=self._debug_pause,
                             filename=path)
        pane = self.current_pane()
        lead = src[:len(src) - len(src.lstrip())]
        self._dbg_offset = lead.count("\n")
        if pane is not None:
            interp.breakpoints = {ln - self._dbg_offset for ln in pane.breakpoints
                                  if ln - self._dbg_offset > 0}
            interp.breakpoint_conditions = {ln - self._dbg_offset: cond
                                            for ln, cond in getattr(pane, "bp_conditions", {}).items()
                                            if ln - self._dbg_offset > 0}
            interp.breakpoint_min_hits = {ln - self._dbg_offset: n
                                          for ln, n in getattr(pane, "bp_min_hits", {}).items()
                                          if ln - self._dbg_offset > 0}
        self._interp = interp
        self._dbg_interp = interp
        if stable_from_env():
            interp.enable_stability()
        if coverage:
            interp.enable_coverage(True)
        try:
            interp.run(ast)
        except ImporException as err:
            self.append_out(f"🔴 运行异常 第{err.lineno}行: {err.name}: {err.msg}")
            self.append_out(f"  修复：{fix_for(err.name, err.msg)}")
            if getattr(self, "_exc_pause", False) and (self._debug_mode or (pane and pane.breakpoints)):
                self._exception_pause(err)
        except Exception as e:
            self.append_out(f"🔴 底层异常：{e}")
        finally:
            if coverage:
                self._last_coverage = interp.coverage_snapshot()
                self._coverage_target = path
                self._coverage_exec = self._coverage_exec_lines(body)
                self._coverage_offset = 1 if src.split("\n", 1)[0].startswith("important<<") else 0
                for pane in self.panes:
                    pane.redraw_lineno()
            self._set_running(False)
            self._render_memory()

    def _consume_problem(self, line):
        """problem matcher：从任务输出中抓 file:line[:col]，转成可点击问题。"""
        try:
            raw = re.sub(r"\x1b\[[0-9;]*m", "", line or "").strip()
            if not raw:
                return
            m = re.search(r"([A-Za-z]:\\[^:\n]+|[^\s:][^:\n]*?\.(?:impor|py|md|txt)):(\d+)(?::(\d+))?", raw)
            if not m:
                return
            path, ln, col = m.group(1), int(m.group(2)), int(m.group(3) or 1)
            if not hasattr(self, "_task_problems"):
                self._task_problems = []
            key = (path, ln, col, raw[:80])
            if key in self._task_problems:
                return
            self._task_problems.append(key)
            if not self._bottom_ready:
                return
            self.show_bottom("problems")
            self.problems.insert("end", "%s:%d:%d %s\n" % (os.path.basename(path), ln, col, raw), "err")
            self._problem_marks.append((ln, col))
            self._err_count += 1
            self._refresh_problems()
        except Exception:
            note_swallowed("problem mark")

    def _poll_queue(self):
        try:
            for _ in range(200):
                kind, payload = self._queue.get_nowait()
                if kind == "out":
                    self.append_out(payload)
                elif kind == "outtag":
                    self.append_out(payload[0], payload[1])
                elif kind == "problem":
                    self._consume_problem(payload)
                elif kind == "test":
                    self._test_add(payload)
                elif kind == "testdone":
                    try:
                        self.test_summary.configure(text=self._test_summary_text())
                    except Exception:
                        pass
                elif kind == "watch":
                    self._watch_update(*payload)
                elif kind == "interp":
                    self._interp = payload
                elif kind == "coverage":
                    payload = payload if len(payload) == 4 else (payload[0], payload[1], payload[2] if len(payload) == 3 else set(), 0)
                    self._coverage_target, self._last_coverage, self._coverage_exec, self._coverage_offset = payload
                    for pane in self.panes:
                        pane.redraw_lineno()
                elif kind == "done":
                    self._set_running(False)
                    self._render_memory()
                    self.append_out("✓ 运行结束", "ok")
                    self.toast("运行结束", "ok")
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _test_suite_parent(self, name):
        suites = getattr(self, "_test_suites", None)
        if suites is None:
            suites = self._test_suites = {}
        parent = suites.get(name)
        if parent is None or not self.test_tree.exists(parent):
            parent = self.test_tree.insert("", tk.END, text=name, open=True,
                                           values=("用例集", "", "", ""))
            suites[name] = parent
        return parent

    def _test_summary_text(self):
        c = getattr(self, "_test_counts", {"pass": 0, "fail": 0})
        return "通过 %d / 失败 %d / 断言 %d" % (c["pass"], c["fail"],
                                               getattr(self, "_test_asserts", 0))

    def _test_add(self, line):
        line = (line or "").strip()
        if not line:
            return
        if line.startswith("测试结果:"):
            try:
                self.test_summary.configure(text=line.replace("测试结果:", "").strip())
            except Exception:
                pass
            return
        low = line.lower()
        if line.startswith("✓"):
            status = "通过"
        elif line.startswith("✗") or line.startswith("impor: 测试失败") or "AssertionError" in line:
            status = "失败"
        else:
            return
        path = ""
        pm = re.search(r"([A-Za-z]:\\[^（）()]*?\.impor|[\w./\\-]+\.impor)", line)
        if pm:
            path = pm.group(1)
            if not os.path.isabs(path):
                path = os.path.join(ROOT_DIR, path)
            path = os.path.abspath(path)
        suite = rel_quiet(path, ROOT_DIR).replace("\\", "/") if path else "运行"
        parent = self._test_suite_parent(suite)
        text = line
        if path:
            text = line.replace(pm.group(1), "").strip("（）() ：:，,")
        am = re.search(r"(\d+)\s*(?:条)?\s*断言", line)
        if am:
            try:
                self._test_asserts += int(am.group(1))
            except Exception:
                pass
        counts = getattr(self, "_test_counts", None)
        if counts is not None:
            if status == "通过":
                counts["pass"] += 1
            elif status == "失败":
                counts["fail"] += 1
                if path and path not in self._test_failed_files:
                    self._test_failed_files.append(path)
                # 3.2.1：把失败行内联到编辑器 gutter；测试输出形如 file.impor:12
                lm = re.search(r"\.impor:(\d+)|第\s*(\d+)\s*行", line)
                if path and lm:
                    ln = int(lm.group(1) or lm.group(2))
                    self._test_fail_map.setdefault(path, set()).add(ln)
                    for pane in self.panes:
                        if pane.path and os.path.abspath(pane.path) == path:
                            pane.test_fails = self._test_fail_map[path]
                            pane.redraw_lineno()
                            pane.text.tag_add("test_fail", f"{ln}.0", f"{ln}.end")
                            pane.text.tag_configure("test_fail", foreground=THEME["red"], underline=True)
                            break
            try:
                self.test_tree.item(parent, values=("用例集", "", "",
                                                    "通过 %d / 失败 %d" %
                                                    (counts["pass"], counts["fail"])))
            except Exception:
                pass
        tags = ("test_pass",) if status == "通过" else (("test_fail",) if status == "失败" else ())
        try:
            self.test_tree.tag_configure("test_pass", foreground=THEME["green"])
            self.test_tree.tag_configure("test_fail", foreground=THEME["red"])
            self.test_tree.tag_configure("test_cov", foreground=THEME["accent"])
        except Exception:
            pass
        dm = re.search(r"(\d+(?:\.\d+)?)\s*(ms|s)\b", line)
        time_text = (dm.group(1) + " " + dm.group(2)) if dm else ""
        cov_text = ""
        try:
            lm = re.search(r"\.impor:(\d+)|第\s*(\d+)\s*行", line)
            if path and lm and getattr(self, "_last_coverage", None) and \
                    getattr(self, "_coverage_target", None) and \
                    os.path.abspath(self._coverage_target) == path:
                ln = int(lm.group(1) or lm.group(2)) + int(getattr(self, "_coverage_offset", 0) or 0)
                if ln in getattr(self, "_coverage_exec", set()):
                    cov_text = "命中" if (self._last_coverage or {}).get(
                        ln - int(getattr(self, "_coverage_offset", 0) or 0), 0) else "未命中"
        except Exception:
            pass
        item = self.test_tree.insert(parent, tk.END, text=(text or line)[:100],
                                     values=(status, time_text, cov_text, line[:400]), tags=tags)
        ln = 0
        try:
            lm = re.search(r"\.impor:(\d+)|第\s*(\d+)\s*行", line)
            if path and lm:
                ln = int(lm.group(1) or lm.group(2))
        except Exception:
            ln = 0
        self._test_row_meta[item] = (path, ln)
        self.test_tree.see(parent)
        try:
            self.test_summary.configure(text=self._test_summary_text())
        except Exception:
            pass

    def _test_files(self, target=None):
        if isinstance(target, (list, tuple)):
            return [os.path.abspath(p) for p in target if os.path.isfile(p)]
        if target and os.path.isfile(target):
            return [os.path.abspath(target)]
        base = target if target and os.path.isdir(target) else ROOT_DIR
        dirs = [base]
        if target is None:
            tests_dir = os.path.join(ROOT_DIR, "tests")
            if os.path.isdir(tests_dir):
                dirs = [tests_dir]
                ex = os.path.join(ROOT_DIR, "examples")
                if os.path.isdir(ex):
                    dirs.append(ex)
        files = []
        for d in dirs:
            try:
                for name in sorted(os.listdir(d)):
                    if name.endswith(".impor") and "test" in name.lower():
                        files.append(os.path.abspath(os.path.join(d, name)))
            except Exception:
                continue
        return files

    def discover_tests(self):
        files = self._test_files(None)
        self.test_tree.delete(*self.test_tree.get_children())
        self._test_suites = {}
        self._test_counts = {"pass": 0, "fail": 0}
        self._test_asserts = 0
        self._test_failed_files = []
        for fp in files:
            rel = rel_quiet(fp, ROOT_DIR).replace("\\", "/")
            item = self.test_tree.insert("", tk.END, text=rel, open=True,
                                         values=("未运行", "", "", fp))
            self._test_row_meta[item] = (fp, 0)
        self.show_bottom("tests")
        self.test_summary.configure(text="发现 %d 个测试文件" % len(files))
        self.toast("发现 %d 个测试文件（尚未运行）" % len(files), "ok")
        return files

    def rerun_failed_tests(self):
        if not self._test_failed_files:
            self.toast("还没有失败用例可重跑")
            return
        files = list(self._test_failed_files)
        self._test_failed_files = []
        self._run_test_target(files, label="重跑失败")

    def toggle_test_watch(self):
        self._test_watch = not getattr(self, "_test_watch", False)
        try:
            self.btn_test_watch.configure(text="监听: 开" if self._test_watch else "监听: 关")
        except Exception:
            pass
        if self._test_watch:
            self._test_watch_sig = self._test_signature(self._test_last_files or self._test_files(None))
            self._poll_test_watch()
            self.toast("测试监听已开启：文件改动后自动重跑", "ok")
        else:
            if getattr(self, "_test_watch_job", None):
                try:
                    self.root.after_cancel(self._test_watch_job)
                except Exception:
                    pass
                self._test_watch_job = None
            self.toast("测试监听已关闭")

    def _test_signature(self, files):
        sig = {}
        for fp in files:
            try:
                sig[fp] = os.path.getmtime(fp)
            except Exception:
                pass
        return sig

    def _poll_test_watch(self):
        if not getattr(self, "_test_watch", False):
            return
        files = self._test_last_files or self._test_files(None)
        sig = self._test_signature(files)
        if sig and sig != getattr(self, "_test_watch_sig", sig):
            self._test_watch_sig = sig
            if not self._running:
                self._run_test_target(files, label="监听重跑")
        self._test_watch_job = self.root.after(1600, self._poll_test_watch)

    def _test_jump(self, e=None):
        item = self.test_tree.focus()
        if not item:
            return
        path, line = getattr(self, "_test_row_meta", {}).get(item, ("", 0))
        if path and line:
            self.open_path_at(path, int(line))
            return
        detail = self.test_tree.item(item, "values")[3] if len(
            self.test_tree.item(item, "values")) > 3 else ""
        m = re.search(r"([A-Za-z]:\\[^:]+\.impor|[\w./\\-]+\.impor):(\d+)", detail)
        if m:
            path = m.group(1)
            if not os.path.isabs(path):
                path = os.path.join(ROOT_DIR, path)
            self.open_path_at(path, int(m.group(2)))

    def _debug_pause(self, lineno, env):
        full = lineno + getattr(self, "_dbg_offset", 0)
        pane = self.current_pane()
        if pane and full in getattr(pane, "bp_logs", {}):
            expr = pane.bp_logs[full]
            try:
                val = self._dbg_interp.eval_expr_text(expr)
                self.append_out("◆ 日志点 第%d行：%s = %r" % (full, expr, val), "warn")
            except Exception as exc:
                self.append_out("◆ 日志点 第%d行：%s（求值失败：%s）" % (full, expr, exc), "warn")
            return
        if pane:
            pane._exec_line = full
            try:
                store = getattr(env, "store", {}) or {}
                vals = []
                for k, v in store.items():
                    if str(k).startswith("__"):
                        continue
                    sval = repr(v)
                    if len(sval) > 18:
                        sval = sval[:17] + "…"
                    vals.append("%s=%s" % (k, sval))
                pane._debug_inline = {full: " ".join(vals[:2])[:22]}
            except Exception:
                pane._debug_inline = {}
            try:
                pane.text.tag_remove("exec", "1.0", "end")
                pane.text.tag_config("exec", background=THEME["accent_dim"])
                pane.text.tag_add("exec", f"{full}.0", f"{full}.end")
            except Exception:
                pass
            pane.redraw_lineno()
            pane.text.see(f"{full}.0")
        self._show_dbg_panel(full)

    def _show_dbg_panel(self, lineno, exception=False):
        interp = getattr(self, "_dbg_interp", None)
        if interp is None:
            return
        pane = self.current_pane()
        if pane is not None:
            pane.bp_hits[lineno] = pane.bp_hits.get(lineno, 0) + 1
        self.dbg_stack.delete(*self.dbg_stack.get_children())
        src = getattr(self, "_exception_stack", None) if exception else None
        if not src:
            src = interp.call_stack
        self._dbg_frames = list(reversed(src))
        for idx, fr in enumerate(self._dbg_frames):
            self.dbg_stack.insert("", tk.END, iid=str(idx),
                                  values=(fr.lineno + getattr(self, "_dbg_offset", 0), fr.name))
        items = self.dbg_stack.get_children()
        if items:
            self.dbg_stack.selection_set(items[0])
            self.dbg_stack.focus(items[0])
        env = self._dbg_frames[0].env if self._dbg_frames else interp.cur_env
        self._populate_dbg_vars(env.store)
        self._refresh_bp_list()
        try:
            self.dbg_eval_out.configure(text=("异常：%s" % lineno) if exception else "")
        except Exception:
            pass
        self.show_bottom("debug")
        self.status_left.configure(text="调试暂停 · 第 %d 行" % lineno, fg=THEME["yellow"])
        self._dbg_gate.set(0)
        self._dbg_paused = True
        self.root.wait_variable(self._dbg_gate)
        self._dbg_paused = False
        if exception:
            self._exception_stack = None

    def _dbg_act(self, mode):
        interp = getattr(self, "_dbg_interp", None)
        if interp is None or not getattr(self, "_dbg_paused", False):
            return
        if mode == "into":
            interp.step_mode = "into"
        elif mode == "over":
            interp.step_mode = "over"
            interp._dbg_step_target = interp._dbg_depth()
        elif mode == "out":
            interp.step_mode = "out"
            interp._dbg_step_target = interp._dbg_depth() - 1
        else:
            interp.step_mode = None
        self._dbg_gate.set(self._dbg_gate.get() + 1)

    def _dbg_select_frame(self, e=None):
        item = self.dbg_stack.focus()
        if not item or not hasattr(self, "_dbg_frames"):
            return
        try:
            fr = self._dbg_frames[int(item)]
        except (ValueError, IndexError):
            return
        self._populate_dbg_vars(fr.env.store)
        try:
            ln = fr.lineno + getattr(self, "_dbg_offset", 0)
            self.current_pane().text.see(f"{ln}.0")
        except Exception:
            pass

    def _populate_dbg_vars(self, store):
        self.dbg_vars.delete(*self.dbg_vars.get_children())
        idx = 0
        for key in sorted(store):
            if key.startswith("__") or callable(store[key]):
                continue
            val = store[key]
            try:
                vtype = type(val).__name__
                sval = repr(val)
            except Exception:
                vtype, sval = "?", "?"
            if len(sval) > 80:
                sval = sval[:77] + "..."
            iid = "v%d" % idx
            idx += 1
            self.dbg_vars.insert("", tk.END, iid=iid, text=key, values=(vtype, sval), open=False)
            if isinstance(val, dict):
                for n, (k, v) in enumerate(val.items()):
                    if n >= 20:
                        break
                    self.dbg_vars.insert(iid, tk.END, text=str(k),
                                         values=(type(v).__name__, repr(v)[:80]))
            elif isinstance(val, (list, tuple, set)):
                for n, v in enumerate(val):
                    if n >= 20:
                        break
                    self.dbg_vars.insert(iid, tk.END, text="[%d]" % n,
                                         values=(type(v).__name__, repr(v)[:80]))

    def _toggle_exc_pause(self):
        self._exc_pause = not getattr(self, "_exc_pause", True)
        try:
            self.btn_exc.configure(text="异常暂停: 开" if self._exc_pause else "异常暂停: 关")
        except Exception:
            pass

    def _update_exc_filter(self, e=None):
        try:
            self._exc_filter = (self.exc_filter_entry.get() or "").strip()
        except Exception:
            self._exc_filter = ""

    def _exc_allowed(self, err):
        types = [x.strip().lower() for x in getattr(self, "_exc_filter", "").split(",") if x.strip()]
        if not types:
            return True
        name = (getattr(err, "name", "") or "").lower()
        return any(t in name for t in types)

    def _exception_pause(self, err):
        if not self._exc_allowed(err):
            self.append_out("◇ 异常被过滤，不暂停：%s" % getattr(err, "name", "?"), "dim")
            return
        stack = list(getattr(err, "stack", []) or [])
        if not stack:
            return
        self._exception_stack = stack
        ln = stack[-1].lineno + getattr(self, "_dbg_offset", 0)
        pane = self.current_pane()
        if pane:
            try:
                pane.text.tag_remove("exec", "1.0", "end")
                pane.text.tag_add("exec", f"{ln}.0", f"{ln}.end")
                pane.text.see(f"{ln}.0")
            except Exception:
                pass
        self.append_out("◆ 异常暂停：%s 第 %d 行，可在调试页查看调用栈并求值" % (err.name, ln), "warn")
        self._show_dbg_panel(ln, exception=True)

    def _dbg_eval(self, e=None):
        expr = (self.dbg_eval_entry.get() or "").strip()
        if not expr:
            return "break"
        hist = getattr(self, "_dbg_history", None)
        if hist is None:
            hist = self._dbg_history = []
        if expr not in hist:
            hist.append(expr)
            del hist[:-40]
        self._dbg_hist_pos = len(hist)
        interp = getattr(self, "_dbg_interp", None)
        if interp is None:
            return "break"
        frame = None
        try:
            sel = self.dbg_stack.focus()
            frame = self._dbg_frames[int(sel)] if sel else None
        except Exception:
            frame = None
        old = getattr(interp, "cur_env", None)
        try:
            if frame is not None:
                interp.cur_env = frame.env
            val = interp.eval_expr_text(expr)
            self.dbg_eval_out.configure(text="%s = %r" % (expr, val), fg=THEME["green"])
            self.dbg_eval_entry.select_range(0, tk.END)
        except Exception as exc:
            self.dbg_eval_out.configure(text="%s → %s" % (expr, exc), fg=THEME["red"])
        finally:
            try:
                interp.cur_env = old
            except Exception:
                pass
        return "break"

    def _dbg_history_move(self, step):
        hist = getattr(self, "_dbg_history", [])
        if not hist:
            return "break"
        pos = getattr(self, "_dbg_hist_pos", len(hist))
        pos = max(0, min(len(hist), pos + step))
        self._dbg_hist_pos = pos
        self.dbg_eval_entry.delete(0, tk.END)
        if pos < len(hist):
            self.dbg_eval_entry.insert(0, hist[pos])
        return "break"

    def _dbg_edit_var(self, e=None):
        item = self.dbg_vars.focus()
        if not item or not item.startswith("v") or self.dbg_vars.parent(item):
            return
        interp = getattr(self, "_dbg_interp", None)
        frame = None
        try:
            sel = self.dbg_stack.focus()
            frame = self._dbg_frames[int(sel)] if sel else None
        except Exception:
            frame = None
        if interp is None or frame is None:
            return
        name = self.dbg_vars.item(item, "text")
        from tkinter import simpledialog
        new = simpledialog.askstring("修改变量", "把 %s 设为：" % name,
                                     initialvalue=repr(frame.env.store.get(name)), parent=self.root)
        if new is None:
            return
        old = getattr(interp, "cur_env", None)
        try:
            interp.cur_env = frame.env
            val = interp.eval_expr_text(new)
            frame.env.store[name] = val
            self._populate_dbg_vars(frame.env.store)
            self.append_out("已改变量 %s = %r" % (name, val), "ok")
        except Exception as exc:
            self.toast("改变量失败：%s" % exc, "err")
        finally:
            try:
                interp.cur_env = old
            except Exception:
                pass

    def _refresh_bp_list(self):
        try:
            self.bp_list.delete(*self.bp_list.get_children())
        except Exception:
            return
        pane = self.current_pane()
        if not pane:
            return
        for ln in sorted(pane.breakpoints):
            info = pane.bp_conditions.get(ln) or ("日志: " + pane.bp_logs.get(ln, "") if ln in pane.bp_logs else "")
            hit = pane.bp_hits.get(ln, 0)
            if pane.bp_min_hits.get(ln, 0):
                info = (info + "  ") if info else ""
                info += "≥%d 次命中" % pane.bp_min_hits[ln]
            if hit:
                info = (info + "  ") if info else ""
                info += "命中 %d 次" % hit
            self.bp_list.insert("", tk.END, iid=str(ln), text=str(ln), values=(info,))

    def _bp_edit_condition(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        item = self.bp_list.focus()
        if not item:
            self.toast("先在断点列表里选中一行")
            return
        ln = int(item)
        cond = simpledialog.askstring("条件断点", f"第 {ln} 行命中条件（留空表示无条件）：",
                                      initialvalue=pane.bp_conditions.get(ln, ""), parent=self.root)
        if cond is None:
            return
        if cond.strip():
            pane.bp_conditions[ln] = cond.strip()
        else:
            pane.bp_conditions.pop(ln, None)
        pane.breakpoints.add(ln)
        pane.redraw_lineno()
        self._refresh_bp_list()
        self.toast("断点 %d：%s" % (ln, cond.strip() or "无条件"))

    def _bp_edit_hits(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        item = self.bp_list.focus()
        if not item:
            self.toast("先在断点列表里选中一行")
            return
        ln = int(item)
        n = simpledialog.askinteger("命中次数", f"第 {ln} 行第几次命中时暂停（0 表示每次都停）：",
                                    initialvalue=pane.bp_min_hits.get(ln, 0),
                                    minvalue=0, maxvalue=100000, parent=self.root)
        if n is None:
            return
        if n > 0:
            pane.bp_min_hits[ln] = n
        else:
            pane.bp_min_hits.pop(ln, None)
        pane.breakpoints.add(ln)
        pane.redraw_lineno()
        self._refresh_bp_list()
        self.toast("断点 %d：%s" % (ln, ("第 %d 次命中暂停" % n) if n else "每次命中暂停"))

    def _bp_edit_log(self):
        from tkinter import simpledialog
        pane = self.current_pane()
        if not pane:
            return
        item = self.bp_list.focus()
        if not item:
            self.toast("先在断点列表里选中一行")
            return
        ln = int(item)
        expr = simpledialog.askstring("日志断点", f"第 {ln} 行日志表达式（留空关闭）：",
                                      initialvalue=pane.bp_logs.get(ln, ""), parent=self.root)
        if expr is None:
            return
        if expr.strip():
            pane.bp_logs[ln] = expr.strip()
        else:
            pane.bp_logs.pop(ln, None)
        pane.breakpoints.add(ln)
        pane.redraw_lineno()
        self._refresh_bp_list()
        self.toast("日志断点 %d：%s" % (ln, expr.strip() or "关闭"))

    def _bp_jump(self, e=None):
        item = self.bp_list.focus()
        pane = self.current_pane()
        if not item or not pane:
            return
        try:
            pane.text.mark_set("insert", f"{int(item)}.0")
            pane.text.see("insert")
            pane.focus_text()
        except Exception:
            pass

    def _bp_delete(self, e=None):
        item = self.bp_list.focus()
        pane = self.current_pane()
        if not item or not pane:
            return "break"
        try:
            ln = int(item)
            pane.breakpoints.discard(ln)
            pane.bp_conditions.pop(ln, None)
            pane.bp_logs.pop(ln, None)
            pane.redraw_lineno()
            self._refresh_bp_list()
        except Exception:
            pass
        return "break"

