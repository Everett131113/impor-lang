# -*- coding: utf-8 -*-
"""Impor 稳定性内核：五道资源闸门 + 子进程围栏 + 崩溃留证。

默认关闭。带 `--stable`（或环境变量 `IMPOR_STABLE=1`）时打开；不打开时
解释器的行为与 4.0.0 完全一致，且语句入口只多一次 `is None` 判断。

打开后，下面这些「让语言崩掉 / 永久挂起 / 被系统杀」的情况，变成
「带错误码、带行号、可 catch、可继续运行」的熔断：

    E1020  StepLimit     执行步数上限（死循环、无界循环）
    E1021  TimeLimit     墙钟时间上限（卡死、外部阻塞）
    E1022  MemoryLimit   进程内存上限（慢泄漏、累积吃内存）
    E1023  OutputLimit   输出上限（刷屏拖死终端/日志）
    E1024  AllocLimit    单次分配上限（一次要一个巨型容器，分配前拒绝）

设计约束（写进代码而不是只写进文档）：

* 闸门必须排在分配之前：先 `len(range(n))`（O(1)）判断，再决定是否真的建容器，
  否则「检查」本身就把内存吃光了。
* 稳定模式必须关掉「快速循环计划」和转译后端：原实现把整段 while 编译成一个
  Python 循环，一条帧跑完几十万条语句，计量器完全看不到——这是「有闸门但闸门失效」。
* 额度按每次顶层执行重置：REPL/编辑器复用同一解释器，不能拿第 1 条命令的
  步数拖累第 200 条命令。
* 内存只能采样（RSS 是唯一可靠事实），所以是「上限附近熔断」，不是硬配额
  （硬配额要等 OS 级 Job Object，见文档「诚实边界」）。
* 子进程围栏（run_isolated）兜住解释器自身/FFI 的原生崩溃：父进程永远活着。

这一层的目标不是「永不失败」，而是「失败可预期、可定位、可恢复」。
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

MB = 1024 * 1024

# 默认档：工程折中。死循环约 5 秒内熔断；长任务要显式放宽并自担风险。
DEFAULT_LIMITS = {
    "steps": 5000000,      # 语句条数（不是 Python 帧数，三后端可比）
    "seconds": 30.0,       # 墙钟秒
    "memory_mb": 768,      # 工作集 RSS
    "output_kb": 8192,     # 输出总字节（KB）
    "alloc": 8000000,      # 单次分配元素数（约 64 MB 指针数组）
}

# 严格档：作业批改 / 在线运行台 / 自动化评测。
STRICT_LIMITS = {
    "steps": 2000000,
    "seconds": 10.0,
    "memory_mb": 256,
    "output_kb": 1024,
    "alloc": 1000000,
}

_ENV_KEYS = {
    "steps": "IMPOR_LIMIT_STEPS",
    "seconds": "IMPOR_LIMIT_SECONDS",
    "memory_mb": "IMPOR_LIMIT_MEMORY_MB",
    "output_kb": "IMPOR_LIMIT_OUTPUT_KB",
    "alloc": "IMPOR_LIMIT_ALLOC",
}

# 错误名 → 错误码（diagnostics.ERROR_CODES 里也要有同名条目，两处都改）
LIMIT_NAMES = {
    "StepLimit": "E1020",
    "TimeLimit": "E1021",
    "MemoryLimit": "E1022",
    "OutputLimit": "E1023",
    "AllocLimit": "E1024",
}


class Limits(object):
    """一次执行允许消耗的额度。所有值都是「上限」，不是目标值。"""

    __slots__ = ("steps", "seconds", "memory_mb", "output_kb", "alloc")

    def __init__(self, steps=None, seconds=None, memory_mb=None, output_kb=None,
                 alloc=None, base=None):
        src = dict(base or DEFAULT_LIMITS)
        if steps is not None:
            src["steps"] = int(steps)
        if seconds is not None:
            src["seconds"] = float(seconds)
        if memory_mb is not None:
            src["memory_mb"] = int(memory_mb)
        if output_kb is not None:
            src["output_kb"] = int(output_kb)
        if alloc is not None:
            src["alloc"] = int(alloc)
        for k in ("steps", "seconds", "memory_mb", "output_kb", "alloc"):
            setattr(self, k, src[k])

    @classmethod
    def strict(cls):
        return cls(base=STRICT_LIMITS)

    @classmethod
    def from_env(cls, base=None, environ=None):
        """环境变量覆盖：IMPOR_STABLE=1 打开，IMPOR_LIMIT_* 单项覆盖。"""
        env = os.environ if environ is None else environ
        lim = cls(base=base or DEFAULT_LIMITS)
        for field, key in _ENV_KEYS.items():
            raw = (env.get(key) or "").strip()
            if not raw:
                continue
            try:
                if field == "seconds":
                    setattr(lim, field, float(raw))
                else:
                    setattr(lim, field, int(float(raw)))
            except ValueError:
                continue
        return lim

    def as_dict(self):
        return {k: getattr(self, k) for k in ("steps", "seconds", "memory_mb", "output_kb", "alloc")}

    def to_env(self, environ=None):
        env = dict(os.environ if environ is None else environ)
        env["IMPOR_STABLE"] = "1"
        for field, key in _ENV_KEYS.items():
            env[key] = str(getattr(self, field))
        return env

    def __repr__(self):
        return ("Limits(steps=%d, seconds=%.1fs, memory=%dMB, output=%dKB, alloc=%d)"
                % (self.steps, self.seconds, self.memory_mb, self.output_kb, self.alloc))


# ---------------- RSS（进程工作集） ----------------
_rss_fn = None


def _build_rss_fn():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        def _rss():
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            # 当前进程伪句柄：GetCurrentProcess() 返回 -1
            if not kernel32.K32GetProcessMemoryInfo(
                    ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb):
                return 0
            return int(counters.WorkingSetSize)

        return _rss

    try:
        import resource

        def _rss():
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux: KB；macOS: bytes
            return int(usage) * 1024 if sys.platform != "darwin" else int(usage)

        return _rss
    except Exception:
        return lambda: 0


def rss_bytes():
    """当前进程工作集字节数；取不到返回 0（调用方按「未知」处理）。"""
    global _rss_fn
    if _rss_fn is None:
        try:
            _rss_fn = _build_rss_fn()
        except Exception:
            _rss_fn = lambda: 0
    try:
        return _rss_fn() or 0
    except Exception:
        return 0


def _raise_limit(name, msg, lineno=0, col=0, filename=None):
    """用语言自己的错误类型抛出，保证能被 catch、能被四段式诊断渲染。"""
    from .interpreter import ImporRuntimeError
    raise ImporRuntimeError(name, msg, lineno, col, filename)


class Governor(object):
    """计量器。挂在 `Interpreter.stab` 上；None 表示关闭（零开销）。"""

    def __init__(self, limits=None, filename=None):
        self.limits = limits or Limits()
        self.filename = filename
        self.sessions = 0
        self.trip_counts = {}   # 跨多次执行累计（编辑器状态栏/评测报告用）
        self.reset()

    # ---------- 生命周期 ----------
    def reset(self):
        """一次顶层执行开始：清空计数（不动累计的 session 统计）。"""
        self.steps = 0
        self.output_chars = 0
        self.output_bytes = 0
        self.alloc_elems = 0
        self.alloc_calls = 0
        self.peak_rss = 0
        self.elapsed = 0.0
        self._t0 = time.perf_counter()
        self._mem_t0 = 0.0
        self.trips = {}
        self.sessions += 1

    def begin(self):
        """REPL/编辑器：每次顶层执行前调用。"""
        self.reset()

    def finalize(self):
        self.elapsed = time.perf_counter() - self._t0

    def stats(self):
        self.finalize()
        return {
            "sessions": self.sessions,
            "steps": self.steps,
            "elapsed": round(self.elapsed, 3),
            "output_chars": self.output_chars,
            "alloc_elems": self.alloc_elems,
            "alloc_calls": self.alloc_calls,
            "peak_rss_mb": round(self.peak_rss / MB, 1),
            "limits": self.limits.as_dict(),
            "trips": dict(self.trips),
        }

    # ---------- 步数 / 时间 ----------
    def on_stmt(self, lineno=0, col=0):
        self.steps += 1
        if self.steps >= self.limits.steps:
            self._trip("StepLimit",
                       "执行步数达到上限 %d（疑似死循环或无界循环）" % self.limits.steps,
                       lineno, col)
        now = time.perf_counter()
        if now - self._t0 >= self.limits.seconds:
            self._trip("TimeLimit",
                       "运行时间 %.1f 秒超过上限 %.1f 秒（脚本卡住或被外部阻塞）"
                       % (now - self._t0, self.limits.seconds),
                       lineno, col)
        # 内存不必每条语句都采：每 4096 条采样一次，外加每次分配前强制采
        if not (self.steps & 4095):
            self._check_memory(lineno, col)

    def _check_memory(self, lineno=0, col=0):
        used = rss_bytes()
        if used > self.peak_rss:
            self.peak_rss = used
        if used and used >= self.limits.memory_mb * MB:
            self._trip("MemoryLimit",
                       "进程内存 %.0f MB 达到上限 %d MB（数据在累积增长）"
                       % (used / MB, self.limits.memory_mb),
                       lineno, col)

    # ---------- 分配前闸门 ----------
    def check_alloc(self, elements, what="容器", bytes_each=8, lineno=0, col=0):
        """在真正分配之前调用。elements 必须能 O(1) 算出（如 len(range(n))）。"""
        try:
            n = int(elements)
        except (TypeError, ValueError):
            return 0
        if n <= 0:
            return 0
        if n >= self.limits.alloc:
            self._trip("AllocLimit",
                       "单次分配 %s 需要 %d 个元素，超过上限 %d（已在分配前拒绝）"
                       % (what, n, self.limits.alloc),
                       lineno, col)
        self.alloc_elems += n
        self.alloc_calls += 1
        used = rss_bytes()
        if used > self.peak_rss:
            self.peak_rss = used
        need = n * max(1, int(bytes_each))
        if used and used + need >= self.limits.memory_mb * MB:
            self._trip("MemoryLimit",
                       "再分配给 %s 的 %d 个元素（约 %.0f MB）会超过内存上限 %d MB"
                       % (what, n, need / MB, self.limits.memory_mb),
                       lineno, col)
        return n

    # ---------- 输出闸门 ----------
    def note_output(self, text, lineno=0, col=0):
        # 按 UTF-8 字节计：中文一个字符 3 字节，按字符计会把上限放大 3 倍
        raw = text.encode("utf-8", "replace") if isinstance(text, str) else bytes(text)
        n = len(raw)
        self.output_chars += len(text) if isinstance(text, str) else n
        self.output_bytes += n + 1  # +1 = 换行
        if self.output_bytes >= self.limits.output_kb * 1024:
            self._trip("OutputLimit",
                       "输出已写 %d KB，超过上限 %d KB（刷屏或调试打印没关）"
                       % (self.output_bytes // 1024, self.limits.output_kb),
                       lineno, col)

    def guard_logger(self, log_cb):
        """把 print 的落点包一层计量。语言层所有输出都走 log_cb，一处就够。"""
        if log_cb is None:
            return None
        gov = self

        def _guarded(text):
            gov.note_output(text)
            return log_cb(text)

        _guarded.__wrapped__ = log_cb
        return _guarded

    # ---------- 内部 ----------
    def _trip(self, name, msg, lineno=0, col=0):
        self.trips[name] = msg
        self.trip_counts[name] = self.trip_counts.get(name, 0) + 1
        self.finalize()
        _raise_limit(name, msg, lineno, col, self.filename)


def enable(interp, limits=None):
    """给解释器挂上稳定性内核（编辑器 / REPL / 批量评测也走这里）。"""
    gov = Governor(limits or Limits.from_env(), filename=getattr(interp, "filename", None))
    interp.stab = gov
    interp.log_cb = gov.guard_logger(interp.log_cb)
    return gov


def stable_from_env(environ=None):
    """环境变量是否要求打开稳定模式（编辑器、CI 走这条）。"""
    env = os.environ if environ is None else environ
    return (env.get("IMPOR_STABLE") or "").strip().lower() in ("1", "true", "yes", "on")


# ---------------- 子进程围栏 ----------------
KIND_OK = "ok"
KIND_SCRIPT = "script-error"
KIND_SYNTAX = "syntax"
KIND_LIMIT = "circuit-breaker"
KIND_CRASH = "crash"
KIND_TIMEOUT = "timeout"
KIND_OUTPUT = "output-cap"

KIND_TO_CODE = {
    KIND_OK: 0,
    KIND_SCRIPT: 1,
    KIND_SYNTAX: 2,
    KIND_LIMIT: 3,
    KIND_CRASH: 4,
    KIND_TIMEOUT: 5,
    KIND_OUTPUT: 6,
}


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def classify(rc, timed_out, out_capped, stderr_text):
    if timed_out:
        return KIND_TIMEOUT
    if out_capped:
        return KIND_OUTPUT
    for name, code in LIMIT_NAMES.items():
        if name in (stderr_text or "") or code in (stderr_text or ""):
            return KIND_LIMIT
    if rc is None or rc < 0:
        return KIND_CRASH
    if rc == 0:
        return KIND_OK
    if rc == 2:
        return KIND_SYNTAX
    if rc >= 128:
        return KIND_CRASH
    return KIND_SCRIPT


def run_isolated(args, limits=None, timeout=None, cwd=None, python=None,
                 capture_kb=4096, env_extra=None):
    """在子进程里跑 `python -m impor <args>`，父进程永远活着。

    args 形如 ["run", "脚本.impor"]。返回 dict：
      rc / kind / exit_code / stdout / stderr / elapsed / timed_out
    超时（timeout）与输出超限（capture_kb）都会强杀子进程。
    """
    py = python or sys.executable
    root = cwd or repo_root()
    cmd = [py, "-m", "impor"] + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if limits is not None:
        env = (limits or Limits()).to_env(env)
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})

    cap = max(64, int(capture_kb)) * 1024
    t0 = time.perf_counter()
    out_buf, err_buf = [], []
    capped = {"hit": False}

    def _reader(stream, buf):
        chunks = []
        total = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    room = max(0, cap - (total - len(chunk)))
                    if room:
                        chunks.append(chunk[:room])
                    capped["hit"] = True
                    break
                chunks.append(chunk)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            buf.append(b"".join(chunks))

    proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    threads = [threading.Thread(target=_reader, args=(proc.stdout, out_buf), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, err_buf), daemon=True)]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    for t in threads:
        t.join(timeout=3)
    elapsed = time.perf_counter() - t0

    stdout = (out_buf[0] if out_buf else b"").decode("utf-8", "replace")
    stderr = (err_buf[0] if err_buf else b"").decode("utf-8", "replace")
    rc = proc.returncode
    kind = classify(rc, timed_out, capped["hit"], stderr + stdout)
    return {
        "rc": rc,
        "kind": kind,
        "exit_code": KIND_TO_CODE.get(kind, 1),
        "stdout": stdout,
        "stderr": stderr,
        "elapsed": round(elapsed, 3),
        "timed_out": timed_out,
        "output_capped": capped["hit"],
    }


# ---------------- 自检：敌意脚本 ----------------
SELFTEST_CASES = [
    ("step_limit", "死循环", "var i = 0\nwhile true\n    set i = i + 1\n", "E1020",
     {"steps": 300000}),
    ("time_limit", "卡死（每轮睡一下）", "var i = 0\nwhile true\n    sleep(0.05)\n    set i = i + 1\n",
     "E1021", {"seconds": 1.0, "steps": 10 ** 12}),
    ("output_limit", "刷屏输出", 'var i = 0\nwhile true\n    print("这一行会被无限打印")\n    set i = i + 1\n',
     "E1023", {"output_kb": 64}),
    ("alloc_limit", "一次要一个巨型数组", "var a = arrfill(50000000, 0)\nprint(len(a))\n",
     "E1024", {"alloc": 2000000}),
    ("memory_limit", "数组累积吃内存", (
        "var keep = list()\nvar i = 0\nwhile i < 60\n"
        "    arrappend(keep, arrfill(400000, i))\n    set i = i + 1\nprint(len(keep))\n"),
     "E1022", {"memory_mb": None}),   # None → 自检时按「基线 RSS + 96 MB」算
    ("recursion", "无限递归", "def f()\n    return f()\nf()\n", "E1011",
     {"steps": 10 ** 9}),
    ("zero_division", "除零", "var x = 1 / 0\nprint(x)\n", "E1001", {}),
    ("no_false_positive", "大整数不该被误判", "var x = 9223372036854775807 + 1\nprint(x)\n", "E1000", {}),
]


def selftest(limits=None, timeout=25, workdir=None, verbose=False):
    """跑一批敌意脚本，验证「全部被结构化熔断、父进程存活、无 Traceback」。

    返回 dict：{"passed": n, "total": n, "cases": [...], "limits": {...}}
    """
    base_limits = limits or Limits()
    tmp = workdir or tempfile.mkdtemp(prefix="impor-stability-")
    os.makedirs(tmp, exist_ok=True)
    baseline_mb = int(rss_bytes() / MB)
    results = []
    for name, title, src, expect, overrides in SELFTEST_CASES:
        path = os.path.join(tmp, name + ".impor")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        lim = Limits(base=base_limits.as_dict())
        for k, v in overrides.items():
            if k == "memory_mb" and v is None:
                setattr(lim, k, max(64, baseline_mb + 96))
            elif v is not None:
                setattr(lim, k, v)
        r = run_isolated(["run", "--stable", path], limits=lim, timeout=timeout)
        text = (r["stderr"] or "") + (r["stdout"] or "")
        ok = True
        reason = []
        if "Traceback (most recent call last)" in text:
            ok = False
            reason.append("出现 Python Traceback")
        if expect == "E1000":
            ok = ok and r["rc"] == 0
            if r["rc"] != 0:
                reason.append("退出码 %s" % r["rc"])
        else:
            ok = ok and (expect in text)
            if expect not in text:
                reason.append("未出现 %s" % expect)
            ok = ok and "Impor 内部错误" not in text
        item = {"name": name, "title": title, "expect": expect, "ok": ok,
                "rc": r["rc"], "kind": r["kind"], "elapsed": r["elapsed"],
                "reason": "；".join(reason),
                "stderr_tail": (r["stderr"] or "").strip().splitlines()[-3:] if r["stderr"] else []}
        results.append(item)
        if verbose:
            print("[%s] %-14s %-22s %.2fs  %s" % ("PASS" if ok else "FAIL", name, title,
                                                 r["elapsed"], item["reason"]))
    passed = sum(1 for r in results if r["ok"])
    return {
        "passed": passed,
        "total": len(results),
        "cases": results,
        "limits": base_limits.as_dict(),
        "workdir": tmp,
    }


# ---------------- 崩溃留证 ----------------
def crash_log_path():
    home = os.path.expanduser("~")
    return os.path.join(home, ".impor", "crash.log")


def write_crash_log(exc, where="", extra=None):
    """内部错误/原生崩溃的现场：写 ~/.impor/crash.log，返回路径。自身绝不抛异常。"""
    try:
        import traceback
        path = crash_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > 512 * 1024:
            try:
                os.replace(path, path + ".1")
            except Exception:
                pass
        if isinstance(exc, BaseException):
            body = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            head = "%s: %s" % (type(exc).__name__, exc)
        else:
            body = str(exc)
            head = str(exc).splitlines()[0] if str(exc) else "unknown"
        try:
            from .version import __version__
            ver = __version__
        except Exception:
            ver = "?"
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write("=" * 72 + "\n")
            f.write("time: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("where: %s\n" % (where or "?"))
            f.write("impor: %s | python: %s | pid: %s\n"
                    % (ver, sys.version.split()[0], os.getpid()))
            f.write("cwd: %s\n" % os.getcwd())
            if extra:
                try:
                    f.write("extra: %s\n" % json.dumps(extra, ensure_ascii=False, default=str))
                except Exception:
                    pass
            f.write("head: %s\n" % head)
            f.write(body.rstrip() + "\n")
        return path
    except Exception:
        return None


# ---------------- 编辑器加固（4.7.3） ----------------
def editor_limits(seconds=None, environ=None):
    """图形编辑器运行脚本时使用的额度。

    默认比 CLI 宽松：30 秒 / 500 万步。编辑器里跑死循环不该把整个进程拖死，
    但也不该在用户跑一个 40 秒的真实任务时就误报，所以允许配置：
    设置项 `run_seconds`（0 表示完全关闭闸门，回到 4.7.2 行为）。
    """
    env = os.environ if environ is None else environ
    if seconds is None:
        raw = (env.get("IMPOR_EDITOR_RUN_SECONDS") or "").strip()
        try:
            seconds = float(raw) if raw else 30.0
        except ValueError:
            seconds = 30.0
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 30.0
    lim = Limits(base=DEFAULT_LIMITS)
    if seconds <= 0:
        lim.seconds = float("inf")
        lim.steps = 10 ** 15
    else:
        lim.seconds = seconds
    return lim


def kill_tree(pid, timeout=4.0):
    """杀掉 pid 及其全部子孙进程。返回 (ok, detail)。

    为什么不能只 `proc.kill()`：终端里执行的是 `shell=True` 的命令字符串，
    真正干活的是 shell 的孙进程（gcc、编译出来的 exe）。只杀 shell 会让
    孙进程变孤儿继续吃 CPU——这正是「点了停止但还在跑」的成因。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False, "bad pid"
    if pid <= 0:
        return False, "bad pid"
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, text=True, timeout=timeout,
                               errors="replace")
            detail = ((r.stdout or "") + (r.stderr or "")).strip()
            # taskkill 在「进程已退出」时返回 128，这不算失败
            return True, detail or ("rc=%d" % r.returncode)
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            os.kill(pid, signal.SIGKILL)
        return True, "killpg %d" % pid
    except subprocess.TimeoutExpired:
        return False, "taskkill timeout"
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)


_HOOKED = {"done": False}


def install_crash_hooks():
    """把「非 Tk 回调」的崩溃也写进 crash.log。

    Tk 回调已由 `root.report_callback_exception` 接住；但后台线程（运行脚本、
    索引重建）崩掉时默认只往 stderr 打一行，编辑器窗口还在、现场已丢。
    这里补 sys.excepthook + threading.excepthook，幂等。
    """
    if _HOOKED["done"]:
        return False
    _HOOKED["done"] = True

    prev_sys = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        try:
            write_crash_log(exc_value, where="sys.excepthook")
        except Exception:
            pass
        try:
            prev_sys(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _sys_hook

    try:
        prev_th = threading.excepthook

        def _thread_hook(args):
            thread = getattr(args, "thread", None)
            name = getattr(thread, "name", "?") if thread is not None else "?"
            try:
                write_crash_log(getattr(args, "exc_value", None),
                                where="thread:%s" % name)
            except Exception:
                pass
            try:
                prev_th(args)
            except Exception:
                pass

        threading.excepthook = _thread_hook
    except Exception:
        pass
    return True


def crash_log_tail(limit=20):
    """读 crash.log 最后若干行，供编辑器启动时提示「上次有内部错误」。"""
    try:
        path = crash_log_path()
        if not os.path.isfile(path):
            return []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-int(limit):]]
    except Exception:
        return []
