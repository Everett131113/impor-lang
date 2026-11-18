# -*- coding: utf-8 -*-
"""
Impor (I++) 运行时支撑库 —— 供「编译产物」引用。

impor compile 生成的 .py 文件顶部会：
    from impor.runtime import *
    然后直接使用这里的函数（range/vec2/clamp/...）。

注意：本模块刻意用 `_b.` 前缀访问 Python 内建函数，
避免被同名 Impor 内建（range/type/open/hash/...）遮蔽。
"""

import sys
import os
import time
import random
import string
import hashlib
import math
import json
import importlib
import re
import ctypes
import struct
import builtins as _b

from .builtin_manifest import RUNTIME_BUILTINS, check_runtime_namespace

__all__ = [
    "ImporException", "UserError", "ExitScriptExc", "Vec2", "Color",
    "range", "type", "type_of", "to_num", "to_str", "inputstr", "expect",
    "clamp", "lerp", "remap", "sign", "distance", "angle",
    "randi", "randstr", "seed", "trim", "upper", "lower",
    "strpad", "strcut", "strjoin", "hash",
    "arrappend", "arrpop", "arrfind", "arrfill", "arrdup", "arrrev",
    "shuffle", "max_arr", "min_arr", "sum_arr",
    "vec2", "rgb", "frame", "fps",
    "now", "clock", "sleep", "wait", "pause",
    "watch", "snapshot", "breakpoint", "import_path",
    "open", "readln", "writeln", "filemv", "filecat",
    "fileexists", "filemkdir", "fileread", "filewrite",
    "json_encode", "json_decode",
    "defer_enter", "defer", "defer_exit",
    "args", "cls", "tick_frame", "err_name", "err_attr", "load_module",
    "load_rel_module", "star_import", "install_tb_map",
] + sorted(RUNTIME_BUILTINS)


# ---------------- 异常 ----------------
class ExitScriptExc(BaseException):
    pass


class ImporException(Exception):
    def __init__(self, name, msg, lineno=0, col=0):
        self.name = name
        self.msg = msg
        self.lineno = lineno
        self.col = col          # v2.11：列号（1 起，0=未知）
        super().__init__(f"{name}: {msg}")


class UserError(ImporException):
    """用户 exception 声明出的异常类型的基类。"""
    def __init__(self, msg):
        super().__init__(_b.type(self).__name__, str(msg))


def err_name(e):
    return _b.type(e).__name__


# ---------------- defer 运行时（v3.7.8） ----------------
_defer_frames = []


def defer_enter():
    """进入一个函数/模块作用域的延迟栈。"""
    frame = []
    _defer_frames.append(frame)
    return frame


def defer(fn):
    """把清理闭包注册到当前作用域；退出时按 LIFO 执行。"""
    if not _defer_frames:
        raise RuntimeError("defer 不在函数或模块作用域内")
    _defer_frames[-1].append(fn)


def defer_exit(frame):
    """执行当前作用域注册的清理闭包，并保证帧一定弹出。"""
    try:
        while frame:
            fn = frame.pop()
            fn()
    finally:
        if _defer_frames:
            if _defer_frames[-1] is frame:
                _defer_frames.pop()
            else:
                try:
                    _defer_frames.remove(frame)
                except ValueError:
                    pass


def err_attr(e, attr):
    if hasattr(e, attr):
        return getattr(e, attr)
    return str(e)


def raise_from(e, cause):
    """raise Err("m", from old)：携带原始异常再抛出（转译产物用）。"""
    e.cause = cause
    raise e


# ---------------- 编译产物行号映射 ----------------
_tb_line_map = {}
_tb_gen_file = ""
_tb_src_name = ""
_tb_src_lines = []
_orig_excepthook = sys.excepthook


def install_tb_map(line_map, gen_file, src_name="<impor>", src_lines=None):
    """编译产物末尾调用：安装 {py行号: impor行号} 映射 + 源码行。

    之后未捕获异常输出 Impor 风格报错：文件:行 + 源码片段 + ^ 波浪线，
    不再打印 Python 内部堆栈（设 IMPOR_DEBUG=1 可查看完整调试栈）。
    src_lines：原始 .impor 源码按行拆分，用于渲染出错源码片段。
    """
    global _tb_line_map, _tb_gen_file, _tb_src_name, _tb_src_lines
    _tb_line_map = line_map
    _tb_gen_file = gen_file
    _tb_src_name = src_name
    _tb_src_lines = src_lines or []
    sys.excepthook = _impor_excepthook


def _impor_excepthook(etype, evalue, tb):
    if issubclass(etype, ExitScriptExc):
        # exit 语义：静默正常退出
        sys.exit(0)
    impor_lineno = None
    t = tb
    while t is not None:
        if t.tb_frame.f_code.co_filename == _tb_gen_file and t.tb_lineno in _tb_line_map:
            impor_lineno = _tb_line_map[t.tb_lineno]
        t = t.tb_next
    if isinstance(evalue, ImporException):
        name, msg = evalue.name, evalue.msg
    else:
        name, msg = _b.type(evalue).__name__, str(evalue)
    debug_flag = os.environ.get("IMPOR_DEBUG")
    if impor_lineno is not None:
        lines_out = [f"{_tb_src_name}: 第{impor_lineno}行 {name}: {msg}"]
        if 1 <= impor_lineno <= len(_tb_src_lines):
            src_line = _tb_src_lines[impor_lineno - 1]
            lines_out.append("  " + src_line)
            # 编译产物只有行号映射（无列号）：波浪线从行首标记整行出错
            lines_out.append("  " + "^" * _b.max(1, _b.min(len(src_line), 8)))
        if not debug_flag:
            lines_out.append("（设 IMPOR_DEBUG=1 可查看完整调试栈）")
        _b.print("\n".join(lines_out), file=sys.stderr)
        if debug_flag:
            _orig_excepthook(etype, evalue, tb)
        return
    # 找不到映射行：仍屏蔽 Python 栈，只给单行 Impor 风格报错
    if debug_flag:
        _orig_excepthook(etype, evalue, tb)
        return
    _b.print(f"{_tb_src_name}: {name}: {msg}（设 IMPOR_DEBUG=1 可查看完整调试栈）", file=sys.stderr)


# ---------------- 游戏对象 ----------------
class Vec2:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def length(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

    def dist(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def angle_to(self, other):
        return math.degrees(math.atan2(other.y - self.y, other.x - self.x))

    def __add__(self, o):
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o):
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, k):
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __eq__(self, o):
        return isinstance(o, Vec2) and self.x == o.x and self.y == o.y

    def __str__(self):
        return f"({self.x}, {self.y})"


class Color:
    def __init__(self, r, g, b):
        self.r = _b.max(0, _b.min(255, int(r)))
        self.g = _b.max(0, _b.min(255, int(g)))
        self.b = _b.max(0, _b.min(255, int(b)))

    @property
    def hex(self):
        return "#%02X%02X%02X" % (self.r, self.g, self.b)

    def __eq__(self, o):
        return isinstance(o, Color) and (self.r, self.g, self.b) == (o.r, o.g, o.b)

    def __str__(self):
        return self.hex


def typename(v):
    if isinstance(v, Vec2):
        return "vec2"
    if isinstance(v, Color):
        return "color"
    if isinstance(v, _b.bool):
        return "bool"
    if isinstance(v, _b.int):
        return "int"
    if isinstance(v, _b.float):
        return "float"
    if isinstance(v, _b.str):
        return "str"
    if isinstance(v, _b.list):
        return "list"
    if isinstance(v, _b.dict):
        return "dict"
    if isinstance(v, ImporException):
        return "exception"
    return _b.type(v).__name__


# ---------------- 帧统计 ----------------
_frame_count = 0
_fps = 0.0
_fps_t0 = time.time()
_fps_count = 0


def tick_frame():
    global _frame_count, _fps, _fps_t0, _fps_count
    _frame_count += 1
    _fps_count += 1
    now = time.time()
    dt = now - _fps_t0
    if dt >= 0.5:
        _fps = _fps_count / dt
        _fps_t0 = now
        _fps_count = 0


def frame():
    return _frame_count


def fps():
    return _fps


# ---------------- 基础内建 ----------------
def range(start, end=None, step=1):
    if end is None:
        return _b.list(_b.range(int(start)))
    return _b.list(_b.range(int(start), int(end), int(step)))


def type(v):
    return typename(v)


def type_of(v):
    return typename(v)


def to_num(s):
    text = str(s).strip()
    if not text:
        return 0
    if "." in text or "e" in text.lower():
        return _b.float(text)
    return _b.int(text)


def to_str(v):
    return str(v)


_SMOKE_MODE = False


def inputstr(prompt=""):
    if _SMOKE_MODE:
        return "0\n"
    return input(prompt) + "\n"


def expect(actual, expected, label=""):
    """v4.2：表驱动断言；失败消息与解释器 / C 后端同格式。"""
    if actual != expected:
        where = label + "：" if label else ""
        raise AssertionError(f"{where}期望 {expected!r}，实际 {actual!r}")


def args():
    return _b.list(sys.argv[1:])


def exedir():
    """当前程序所在目录；编译产物用 sys.argv[0]，与 C 后端 exedir() 对齐。"""
    p = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    return os.path.dirname(p) if p else os.getcwd()


def exit(code=0):  # noqa: A001 - Impor 语言内建名
    raise SystemExit(_b.int(code))


def cls():
    pass


# ---------------- 数学工具 ----------------
def clamp(v, lo, hi):
    return _b.max(lo, _b.min(hi, float(v)))


def lerp(a, b, t):
    return float(a) + (float(b) - float(a)) * float(t)


def remap(v, in_min, in_max, out_min, out_max):
    v, in_min, in_max, out_min, out_max = (_b.float(x) for x in (v, in_min, in_max, out_min, out_max))
    if in_max == in_min:
        return out_min
    return (v - in_min) / (in_max - in_min) * (out_max - out_min) + out_min


def sign(v):
    v = _b.float(v)
    return -1 if v < 0 else (1 if v > 0 else 0)


def distance(x1, y1, x2, y2):
    return ((_b.float(x2) - _b.float(x1)) ** 2 + (_b.float(y2) - _b.float(y1)) ** 2) ** 0.5


def angle(x1, y1, x2, y2):
    return math.degrees(math.atan2(_b.float(y2) - _b.float(y1), _b.float(x2) - _b.float(x1)))


# ---------------- 随机 ----------------
def randi(lo, hi):
    return random.randint(int(lo), int(hi))


def randstr(n):
    pool = string.ascii_letters + string.digits
    return "".join(random.choice(pool) for _ in _b.range(int(n)))


def seed(n):
    random.seed(int(n))


# ---------------- 字符串 ----------------
def trim(s):
    return str(s).strip()


def upper(s):
    return str(s).upper()


def lower(s):
    return str(s).lower()


def strpad(s, w, ch):
    return str(s).rjust(int(w), str(ch)[:1])


def strcut(s, st, ed):
    return str(s)[int(st):int(ed)]


def strjoin(lst, sep):
    return str(sep).join(str(x) for x in lst)


def hash(s):
    return hashlib.md5(str(s).encode("utf-8")).hexdigest()


# ---------------- 数组 ----------------
def arrappend(arr, val):
    arr.append(val)
    return arr


def arrpop(arr):
    return arr.pop() if _b.len(arr) else None


def arrfind(arr, val):
    return arr.index(val) if val in arr else -1


def arrfill(sz, v):
    return [v for _ in _b.range(int(sz))]


def arrdup(arr):
    return _b.list(arr)


def arrrev(arr):
    return _b.list(reversed(arr))


def shuffle(arr):
    random.shuffle(arr)
    return arr


def max_arr(arr):
    return _b.max(arr)


def min_arr(arr):
    return _b.min(arr)


def sum_arr(arr):
    return _b.sum(arr)


# ---------------- 游戏工具 ----------------
def vec2(x=0, y=0):
    return Vec2(x, y)


def rgb(r, g, b):
    return Color(r, g, b)


# ---------------- 时间 ----------------
def now():
    return time.ctime()


def clock():
    return time.time()


def sleep(sec):
    time.sleep(_b.float(sec))


def wait(sec):
    time.sleep(_b.float(sec))


def pause():
    try:
        input("按回车继续...")
    except EOFError:
        pass


# ---------------- 监视与调试（编译后为空操作） ----------------
def watch(*vars):
    return True


def snapshot():
    return True


def breakpoint():
    return None


def import_path(p):
    _rt_search_paths.append(str(p))
    # 持久解释器已存在时同步搜索路径，保证后续 load_module 立即可见
    global _rt_interp
    if _rt_interp is not None and str(p) not in _rt_interp.module_search_paths:
        _rt_interp.module_search_paths.insert(0, str(p))
    return True


_rt_search_paths = [os.getcwd()]


# ---------------- 文件 API ----------------
def open(fn, mode="r"):
    return _b.open(fn, mode, encoding="utf-8")


def readln(fn):
    return _b.open(fn, "r", encoding="utf-8").readline()


def writeln(fn, txt):
    with _b.open(fn, "a", encoding="utf-8") as f:
        f.write(str(txt) + "\n")
    return True


def filemv(old, new):
    os.rename(old, new)
    return True


def filecat(f1, f2):
    with _b.open(f1, "r", encoding="utf-8") as a, _b.open(f2, "r", encoding="utf-8") as b:
        return a.read() + b.read()


def fileexists(path):
    """编译产物用：路径存在返回 true（与解释器内建同名同义）。"""
    return os.path.exists(path)


def filelist(path):
    """编译产物用：返回目录下文件名数组（排序，与解释器一致）。"""
    try:
        return sorted(os.listdir(path))
    except Exception as e:
        raise ImporException("IOError", str(e), 0)


def regex_match(text, pattern):
    """编译产物用：整串匹配返回 bool。"""
    try:
        return re.fullmatch(pattern, text) is not None
    except Exception as e:
        raise ImporException("ValueError", str(e), 0)


def dict_has(obj, key):
    """编译产物用：d.has(k) / d.contains(k)。"""
    try:
        return key in obj
    except Exception as e:
        raise ImporException("ValueError", str(e), 0)


def filemkdir(path):
    """编译产物用：创建目录，已存在则忽略。"""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        raise ImporException("IOError", str(e), 0)


def fileread(path):
    """编译产物用：UTF-8 读取整个文本文件。"""
    try:
        with _b.open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise ImporException("IOError", str(e), 0)


def filewrite(path, content):
    """编译产物用：UTF-8 覆盖写入文本文件。"""
    try:
        with _b.open(path, "w", encoding="utf-8") as f:
            f.write(str(content))
        return True
    except Exception as e:
        raise ImporException("IOError", str(e), 0)


def json_encode(value):
    """编译产物用：序列化为紧凑 JSON，中文不转义。"""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        raise ImporException("ValueError", str(e), 0)


def json_decode(text):
    """编译产物用：解析 JSON 字符串。"""
    try:
        return json.loads(text)
    except Exception as e:
        raise ImporException("ValueError", "JSON 解析失败：" + str(e), 0)


# ---------------- 模块加载 ----------------
_rt_interp = None


def _get_interp():
    global _rt_interp
    if _rt_interp is None:
        from .interpreter import Interpreter
        _rt_interp = Interpreter(log_cb=print)
        for p in reversed(_rt_search_paths):
            if p not in _rt_interp.module_search_paths:
                _rt_interp.module_search_paths.insert(0, p)
    return _rt_interp


def load_module(name):
    """import 用户模块：先找 .impor 用户模块/包，再回退宿主 Python 模块。

    编译产物经由本函数获得与解释器一致的模块语义（缓存 / 包 / export / 循环导入）。
    """
    interp = _get_interp()
    if interp._find_user_module_path(name):
        return interp._load_module(name, 0, 0)
    try:
        return importlib.import_module(name)
    except ImportError:
        return interp._load_module(name, 0, 0)


def load_rel_module(level, name=""):
    """相对导入（编译产物用）：从调用者源文件目录推断所属包。"""
    import inspect
    frame = inspect.currentframe()
    try:
        caller = frame.f_back
        mod_file = caller.f_globals.get("__file__", "") if caller else ""
    finally:
        del frame
    if not mod_file:
        raise ImporException("ImportError", "无法推断相对导入的包上下文", 0)
    mod_dir = os.path.dirname(os.path.abspath(mod_file))
    # 向上收集含 __pkg__.impor 的包层级
    pkg_parts = []
    d = mod_dir
    while True:
        if os.path.isfile(os.path.join(d, "__pkg__.impor")):
            pkg_parts.insert(0, os.path.basename(d))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        else:
            break
    if not pkg_parts:
        raise ImporException("ImportError", "相对导入仅允许在包内使用", 0)
    if level - 1 >= len(pkg_parts):
        raise ImporException("ImportError", "相对导入超出包层级", 0)
    parts = pkg_parts[:len(pkg_parts) - (level - 1)]
    if name:
        parts.extend(name.split("."))
    return load_module(".".join(parts))


def star_import(mod, ns):
    """from 模块 import *（编译产物用）：把模块对外符号拷入命名空间。"""
    if isinstance(mod, dict):
        items = list(mod.items())
    elif hasattr(mod, "_store"):
        items = list(mod._store.items())
    else:
        items = [(k, getattr(mod, k)) for k in dir(mod) if not k.startswith("_")]
    for k, v in items:
        if k.startswith("__") and k.endswith("__"):
            continue
        ns[k] = v
    return mod


def _runtime_ffi_allow():
    allow = set()
    env = os.environ.get("IMPOR_FFI_ALLOW", "")
    for item in re.split(r"[,;]", env):
        s = item.strip().lower()
        if s:
            allow.add(s)
    base = os.getcwd()
    for _ in range(6):
        cfg = os.path.join(base, ".impor", "ffi_allow.json")
        if os.path.isfile(cfg):
            try:
                with _b.open(cfg, encoding="utf-8") as f:
                    data = json.load(f)
                for item in (data.get("allow") or []):
                    allow.add(str(item).strip().lower())
            except Exception:
                pass
            break
        parent = os.path.dirname(base)
        if parent == base:
            break
        base = parent
    return allow


_runtime_ffi_libs = {}

_PE_MACHINES = {
    0x014c: ("32 位", "i386"),
    0x8664: ("64 位", "x86-64"),
    0xaa64: ("64 位", "ARM64"),
    0x01c4: ("32 位", "ARM"),
}


def _pe_machine(path):
    """读 Windows PE/COFF 的 Machine 字段；非 PE 或读失败返回 None。"""
    try:
        with _b.open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3c)
            e_lfanew = struct.unpack("<I", f.read(4))[0]
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return None
            f.seek(e_lfanew + 4)
            machine = struct.unpack("<H", f.read(2))[0]
            return machine
    except Exception:
        return None


def _host_bits():
    return 8 * ctypes.sizeof(ctypes.c_void_p)


def _runtime_ffi_load(path):
    raw = str(path)
    target = os.path.abspath(raw)
    name = os.path.basename(target).lower()
    if name not in _runtime_ffi_allow():
        raise ImporException(
            "FFIAccessError",
            "原生库 %s 不在允许清单内；请在 .impor/ffi_allow.json 的 allow 数组登记，"
            "或设置 IMPOR_FFI_ALLOW 环境变量" % name, 0)
    if not os.path.isfile(target):
        raise ImporException("FFIError", "原生库不存在：%s" % target, 0)
    machine = _pe_machine(target)
    if machine is not None and machine in _PE_MACHINES:
        bits, arch = _PE_MACHINES[machine]
        if int(bits[:2]) != _host_bits():
            raise ImporException(
                "FFIArchError",
                "位宽不匹配：解释器为 %d 位，目标库为 %s(%s)。"
                "请用 impor build 产出的 32 位 exe，或用 --c-source 编 64 位库"
                % (_host_bits(), bits, arch), 0)
    if target in _runtime_ffi_libs:
        return _runtime_ffi_libs[target]
    try:
        lib = ctypes.WinDLL(target) if os.name == "nt" else ctypes.CDLL(target)
    except Exception as e:
        raise ImporException("FFIError", "加载原生库失败：%s" % e, 0)
    _runtime_ffi_libs[target] = lib
    return lib


def ffi_open(path):
    """编译产物（Python 后端）的 ffi_open：加载允许清单内的原生库。"""
    lib = _runtime_ffi_load(path)
    return os.path.abspath(str(path))


def ffi_call(lib, func, args=None, ret="int", argtypes=None):
    """编译产物（Python 后端）的 ffi_call。C 后端同名内建走强类型代码生成。"""
    handle = _runtime_ffi_load(lib)
    fn = getattr(handle, str(func), None)
    if fn is None:
        raise ImporException("FFIError", "原生库 %s 中找不到函数 %s" % (lib, func), 0)
    vals = list(args or [])
    if argtypes is None:
        ts = []
        for v in vals:
            if isinstance(v, str):
                ts.append("cstr")
            elif isinstance(v, bool):
                ts.append("bool")
            elif isinstance(v, float):
                ts.append("float")
            else:
                ts.append("int")
    else:
        ts = [str(x).lower() for x in argtypes]
    if len(ts) != len(vals):
        raise ImporException("FFIError", "argtypes 数量与参数个数不一致", 0)
    cmap = {"int": ctypes.c_int, "bool": ctypes.c_int, "float": ctypes.c_double,
            "cstr": ctypes.c_char_p, "str": ctypes.c_char_p}
    fn.argtypes = [cmap[t] for t in ts]
    r = str(ret or "int").lower()
    fn.restype = {"int": ctypes.c_longlong, "float": ctypes.c_double,
                  "bool": ctypes.c_int, "cstr": ctypes.c_char_p,
                  "str": ctypes.c_char_p, "void": None}.get(r, ctypes.c_longlong)
    out = fn(*vals)
    if r in ("cstr", "str"):
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
    if r == "bool":
        return bool(out)
    if r == "void":
        return None
    return out


# v3.6.16：运行时内建自检。任何新内建只登记在 builtin_manifest 却没在本模块
# 实现，导入 impor.runtime 时就直接失败，不会拖到 PyInstaller 产物双击才暴露。
_runtime_missing = check_runtime_namespace(globals())
if _runtime_missing:
    raise ImportError(
        "impor.runtime 缺少已登记内建: " + ", ".join(_runtime_missing)
    )
