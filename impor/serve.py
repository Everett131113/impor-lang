# -*- coding: utf-8 -*-
"""`impor serve`：常驻执行进程（P2）。

存在意义只有一个——**把每次运行的固定成本摊到 0**。

`impor run` 的 207 ms 里，174 ms 是 Python 启动 + `import impor.cli`，18 ms 是产物进程创建；
这两笔都跟脚本本身无关，却每次都要付。常驻进程把脚本编译/解析一次、产物留在
`~/.impor/run-cache`，之后每次请求只付“调用”的钱：

- 数值脚本：进程内 `LoadLibrary` + `impor_entry`，**1–3 ms**（不再创建任何进程）
- 其它脚本：跑已缓存的完整 C 后端产物，约 16 ms（一次进程创建，仍省掉 Python 启动）
- 最后才回落本进程里的解释器（语义兜底，慢但一定对）

协议：127.0.0.1 上的一行 JSON 请求 / 一行 JSON 响应。端口与 pid 写在
`~/.impor/serve.json`，客户端（`impor-serve-client.exe`）读它连上来。
"""
import json
import os
import socket
import threading
import time

DEFAULT_HOST = "127.0.0.1"


def state_path():
    root = os.environ.get("IMPOR_HOME") or os.path.expanduser("~/.impor")
    return os.path.join(root, "serve.json")


def read_state():
    """返回 {'port': int, 'pid': int}；没有或读不动返回 None。"""
    try:
        with open(state_path(), "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return None
    if not isinstance(st, dict) or not st.get("port"):
        return None
    return st


def write_state(port, pid):
    p = state_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except OSError:
        pass
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": int(port), "pid": int(pid), "since": int(time.time())}, f)
    os.replace(tmp, p)


def clear_state(pid=None):
    p = state_path()
    try:
        if pid is not None:
            st = read_state()
            if st and st.get("pid") != pid:
                return False
        os.unlink(p)
    except OSError:
        return False
    return True


def _capture_call(fn, out_path):
    """在进程内调用 C 入口，把输出收进文件再读出来。

    patch8 起入口签名是 `int impor_entry(const char *out_path)`：传路径进去，
    DLL 在自己的 CRT 里 `freopen(out_path, "w", stdout)`——这是跨 CRT 唯一可靠的做法
    （宿主的 fd 重定向在 DLL 的 CRT 里不一定生效，实测会晚一次调用才落地）。
    """
    rc = fn(out_path.encode("utf-8"))
    try:
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return int(rc), f.read()
    except OSError:
        return int(rc), ""


_DLL_CACHE = {}


def _load_entry(dll):
    """DLL 句柄按路径缓存：同一脚本第二次请求只付一次 `GetProcAddress`。"""
    ent = _DLL_CACHE.get(dll)
    if ent is None:
        import ctypes
        lib = ctypes.CDLL(dll)
        fn = lib.impor_entry
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p]
        ent = (lib, fn)
        _DLL_CACHE[dll] = ent
    return ent[1]


def execute(script, args=(), opt=2):
    """执行一个脚本，返回 (code, out, tier, err, 秒)。不抛异常——错误转成 code=2 与信息。"""
    from .cli import _read_source, _native_dll, _native_product
    from .interpreter import ImporException, parse_source, strip_header

    t0 = time.perf_counter()
    try:
        src = _read_source(script)
        body = strip_header(src)
        ast = parse_source(body, filename=script)
    except ImporException as e:
        return 2, "", "syntax", "第 %s 行: %s: %s" % (getattr(e, "lineno", 0), e.name, e.msg), time.perf_counter() - t0
    except Exception as e:
        return 2, "", "syntax", str(e), time.perf_counter() - t0

    # 档 1：数值脚本 → 进程内调 DLL，不创建任何进程
    if not args:
        try:
            dll = _native_dll(script, src, ast, opt)
        except Exception:
            dll = None
        if dll:
            import tempfile
            fd_tmp, out_path = tempfile.mkstemp(prefix="impor-serve-", suffix=".out")
            os.close(fd_tmp)
            try:
                fn = _load_entry(dll)
                rc, out = _capture_call(fn, out_path)
                return rc, out, "dll", "", time.perf_counter() - t0
            except Exception as e:
                # DLL 加载/调用失败（被覆盖、架构不符、旧签名）：清掉再走下一档
                _DLL_CACHE.pop(dll, None)
                _ = e
            finally:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass

    # 档 2：完整 C 后端产物
    import subprocess
    try:
        exe = _native_product(script, src, ast, opt, "full")
    except Exception:
        exe = None
    if exe:
        try:
            # 常驻服务是脱离控制台起的（DETACHED_PROCESS），此时再 spawn 一个控制台子进程
            # 会让 Windows 为它新建一个 ConHost，实测每次多付 650–760 ms（patch8 修掉）。
            # CREATE_NO_WINDOW 让子进程直接不带控制台，实测回到 60 ms 级。
            r = subprocess.run([exe] + list(args),
                               cwd=os.path.dirname(os.path.abspath(script)) or None,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace",
                               creationflags=(0x08000000 if os.name == "nt" else 0))
            return r.returncode, (r.stdout or ""), "full", (r.stderr or ""), time.perf_counter() - t0
        except Exception:
            pass

    # 档 3：本进程解释器（语义兜底）
    try:
        from .interpreter import Interpreter
        buf = []
        interp = Interpreter(log_cb=lambda *a: buf.append(" ".join(str(p) for p in a)),
                             argv=list(args), filename=script, fast_mode=True)
        interp.run(ast)
        return 0, "\n".join(buf) + ("\n" if buf else ""), "interp", "", time.perf_counter() - t0
    except ImporException as e:
        return 1, "", "interp", "%s: %s" % (e.name, e.msg), time.perf_counter() - t0
    except Exception as e:
        return 1, "", "interp", "%s: %s" % (type(e).__name__, e), time.perf_counter() - t0


def _handle(req):
    script = req.get("script") or ""
    args = req.get("args") or []
    opt = int(req.get("opt") or 2)
    if not script:
        return {"code": 2, "out": "", "err": "缺少 script", "tier": "none", "ms": 0}
    code, out, tier, err, ms = execute(script, args, opt)
    return {"code": code, "out": out, "err": err, "tier": tier,
            "ms": round(ms * 1000, 2)}


def _recv_line(conn, cap=1 << 16):
    buf = b""
    while len(buf) < cap:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            break
    return buf


def serve(port=0, host=DEFAULT_HOST, ready_cb=None, once=False):
    """阻塞式服务循环。返回实际端口。"""
    # 先把重活导入完：第一次请求不该替整个服务付 import 的钱（实测 60 ms）
    try:
        from . import cli as _warm_cli  # noqa: F401
        from .interpreter import parse_source as _warm_parse  # noqa: F401
    except Exception:
        pass
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(16)
    real_port = srv.getsockname()[1]
    write_state(real_port, os.getpid())
    if ready_cb:
        ready_cb(real_port)
    try:
        while True:
            conn, _ = srv.accept()
            conn.settimeout(600)
            try:
                # 一个连接可以连着发多次请求（客户端 -n N 就是这样测“每次请求”的）
                while True:
                    line = _recv_line(conn)
                    if not line:
                        break
                    req = json.loads(line.decode("utf-8", "replace"))
                    resp = _handle(req)
                    conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
                    if once:
                        break
            except Exception as e:
                try:
                    conn.sendall(json.dumps({"code": 1, "out": "",
                                             "err": "%s: %s" % (type(e).__name__, e),
                                             "tier": "none", "ms": 0},
                                            ensure_ascii=False).encode("utf-8") + b"\n")
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if once:
                break
    finally:
        try:
            srv.close()
        except Exception:
            pass
        clear_state(os.getpid())
    return real_port


def client_request(script, args=(), opt=2, timeout=600):
    """给测试/编辑器用：直接发一次请求（进程内，不起客户端 exe）。"""
    st = read_state()
    if not st:
        return None
    try:
        s = socket.create_connection((DEFAULT_HOST, int(st["port"])), timeout=timeout)
        s.sendall(json.dumps({"script": script, "args": list(args), "opt": opt},
                             ensure_ascii=False).encode("utf-8") + b"\n")
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
        s.close()
        if not data.strip():
            return None
        return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None


def _start_detached(port=0):
    """后台起一个 serve 进程（脱离控制台），返回 state 里的端口。"""
    import subprocess
    import sys
    py = sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entry = os.path.join(root, "Impor.py")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([py, entry, "serve", "--port", str(int(port))],
                     cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(120):
        time.sleep(0.05)
        st = read_state()
        if st:
            return int(st["port"])
    return 0


def _stop(pid=None):
    st = read_state()
    if not st:
        return False
    target = int(st.get("pid") or 0) if pid is None else int(pid)
    if not target:
        return False
    try:
        import subprocess
        subprocess.run(["taskkill", "/PID", str(target), "/F"],
                       capture_output=True, text=True)
    except Exception:
        pass
    clear_state(target)
    return True
