# -*- coding: utf-8 -*-
"""
Impor (I++) 内置函数库。

分类：
  - 基础：input/inputstr/print/len/range/type/to_num/to_str/args
  - 数学工具：clamp/lerp/remap/sign/distance/angle
  - 随机：randi/randstr/seed
  - 字符串：trim/upper/lower/strpad/strcut/strjoin/hash
  - 数组：arrappend/arrpop/arrfind/arrfill/arrdup/arrrev/shuffle/max_arr/min_arr/sum_arr
  - 游戏工具：vec2/rgb/frame/fps
  - 时间：now/clock/sleep/wait/pause
  - 监视与调试：watch/snapshot/breakpoint/import_path/reload
  - 文件（沙盒禁用）：open/readln/writeln/filemv/filecat
"""

import ctypes
import json
import math
import os
import re
import sys
import threading
import time

from .interpreter import (
    ImporException, ImporInstance, UserExceptionType, Closure, Task,
    Vec2, Color, typename, wrap_py_error,
)
from .builtin_manifest import check_interpreter_names
from .observability import note_swallowed


def make_builtins(interp):
    gui_root = interp.gui_root
    sandbox_mode = interp.sandbox_mode

    def _gate(elements, what, bytes_each=8):
        """4.1：分配前过稳定性闸门。关闭时（stab=None）只是一个属性读取。"""
        gov = getattr(interp, "stab", None)
        if gov is not None:
            gov.check_alloc(elements, what, bytes_each)

    def _check_sandbox(name):
        if sandbox_mode and name in ("open", "readln", "writeln", "filemv", "filecat"):
            raise ImporException("SecurityError", f"沙盒禁止函数 {name}", 0)

    # ---------- 基础 ----------
    def builtin_exit(code=0):
        """exit(code)：带退出码结束进程；裸 exit 仍由语法层处理。"""
        raise SystemExit(int(code))

    def builtin_print(*values):
        text = " ".join(str(v) for v in values)
        if interp.log_cb:
            interp.log_cb(text)
        return text

    def builtin_input(prompt=""):
        try:
            if getattr(interp, 'smoke_mode', False):
                return "0"  # --smoke 冒烟：不阻塞等待真实输入
            return _input_impl(gui_root, interp, str(prompt))
        except ImporException:
            raise
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_inputstr(prompt=""):
        return builtin_input(prompt) + "\n"

    def builtin_range(start, end=None, step=1):
        try:
            if end is None:
                # len(range(n)) 是 O(1)：先算长度再决定是否真的建列表
                _gate(len(range(int(start))), "range(...)")
                return list(range(int(start)))
            rng = range(int(start), int(end), int(step))
            _gate(len(rng), "range(...)")
            return list(rng)
        except ImporException:
            raise
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_len(obj):
        try:
            return len(obj)
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_expect(actual, expected, label=""):
        """v4.2：表驱动断言。失败时给出期望/实际值，便于定位哪一行用例。"""
        interp.assert_count += 1
        if actual != expected:
            where = str(label) if label else "expect"
            raise ImporException(
                "AssertionError",
                f"{where}：期望 {expected!r}，实际 {actual!r}", 0)
        return actual

    def builtin_list(it=None):
        try:
            if it is None:
                return []
            if isinstance(it, (int, float)):
                n = int(it)
                _gate(n, "list(n)")
                return [0 for _ in range(n)]
            if hasattr(it, "__len__"):
                _gate(len(it), "list(...)")
            return list(it)
        except ImporException:
            raise
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_type(v):
        return typename(v)

    def builtin_to_num(s):
        try:
            text = str(s).strip()
            if not text:
                return 0
            if "." in text or "e" in text.lower():
                return float(text)
            return int(text)
        except Exception:
            return 0

    def builtin_to_str(v):
        return str(v)

    def builtin_int(v):
        if isinstance(v, bool):
            return 1 if v else 0
        if isinstance(v, (int, float)):
            return int(v)
        try:
            return int(float(str(v).strip()))
        except Exception:
            return 0

    def builtin_args():
        return list(interp.argv)

    # ---------- 数学工具 ----------
    def builtin_clamp(v, lo, hi):
        v = float(v)
        return max(lo, min(hi, v))

    def builtin_lerp(a, b, t):
        a, b, t = float(a), float(b), float(t)
        return a + (b - a) * t

    def builtin_remap(v, in_min, in_max, out_min, out_max):
        v, in_min, in_max, out_min, out_max = (float(x) for x in (v, in_min, in_max, out_min, out_max))
        if in_max == in_min:
            return out_min
        return (v - in_min) / (in_max - in_min) * (out_max - out_min) + out_min

    def builtin_sign(v):
        v = float(v)
        return -1 if v < 0 else (1 if v > 0 else 0)

    def builtin_abs(v):
        """abs(x)：绝对值（v2.13，C 后端已支持，保持双后端一致）。"""
        return abs(v) if isinstance(v, (int, float)) else abs(float(v))

    def builtin_div(a, b):
        """div(a, b)：整数除法，向下取整（对齐 C 后端 rt_div；除零报错，v2.16）。"""
        try:
            ia, ib = int(a), int(b)
        except (TypeError, ValueError):
            raise ImporException("TypeError", "div 需要数值参数", 0)
        if ib == 0:
            raise ImporException("DivisionByZero", "div 除数为 0", 0)
        return ia // ib  # Python // 向下取整，与 C 端 rt_div 修正后一致

    def builtin_min(*vals):
        """min(a, b, ...)：取最小值（v2.13）。"""
        if not vals:
            raise ImporException("CallError", "min 需要至少 1 个参数")
        return min(vals, key=lambda x: float(x))

    def builtin_max(*vals):
        """max(a, b, ...)：取最大值（v2.13）。"""
        if not vals:
            raise ImporException("CallError", "max 需要至少 1 个参数")
        return max(vals, key=lambda x: float(x))

    def builtin_distance(x1, y1, x2, y2):
        return ((float(x2) - float(x1)) ** 2 + (float(y2) - float(y1)) ** 2) ** 0.5

    def builtin_angle(x1, y1, x2, y2):
        import math
        return math.degrees(math.atan2(float(y2) - float(y1), float(x2) - float(x1)))

    # ---------- 随机 ----------
    def builtin_randi(lo, hi):
        import random
        return random.randint(int(lo), int(hi))

    def builtin_randstr(n):
        import random
        import string
        n = int(n)
        _gate(n, "randstr(n)", bytes_each=4)
        pool = string.ascii_letters + string.digits
        return "".join(random.choice(pool) for _ in range(n))

    def builtin_seed(n):
        import random
        random.seed(int(n))

    # ---------- 字符串 ----------
    def builtin_trim(s):
        return str(s).strip()

    def builtin_upper(s):
        # 4.5：与 C 运行时共用一张表（impor/unicode_case.py）。若直接用 Python 的
        # str.upper()，ß→SS 这类一对多映射会让解释器与 exe 的字节不一致。
        from .unicode_case import upper as _uc_upper
        return _uc_upper(str(s))

    def builtin_lower(s):
        from .unicode_case import lower as _uc_lower
        return _uc_lower(str(s))

    def builtin_strpad(s, w, ch):
        _gate(int(w), "strpad 的宽度")
        return str(s).rjust(int(w), str(ch)[:1])

    def builtin_strcut(s, st, ed):
        return str(s)[int(st):int(ed)]

    # ---------- 4.5：码点转换（与 C 运行时 rt_ord/rt_chr 同口径） ----------
    def builtin_ord(s):
        # 返回首字符的 Unicode 码点；与 `s[i]` 的按码点切片口径一致。
        t = str(s)
        if len(t) == 0:
            raise ImporException("ValueError", "ord 的参数不能是空串", 0)
        return ord(t[0])

    def builtin_chr(n):
        cp = int(n)
        if cp < 0 or cp > 0x10FFFF:
            raise ImporException("ValueError", "chr 的码点超出范围（0..1114111）", 0)
        return chr(cp)

    def builtin_strfind(s, sub, start=0):
        """strfind(s, sub, start=0)：返回子串首次出现的字符下标，找不到返回 -1。"""
        return str(s).find(str(sub), int(start))

    def builtin_strlines(s):
        """strlines(s)：按 \n / \r\n 切分成行，返回数组（不保留行尾换行）。"""
        return str(s).splitlines()

    def builtin_filelines(path):
        """filelines(path)：读文件并按行切分；读完不保留整块文本。"""
        try:
            _gate(os.path.getsize(str(path)), "filelines 读取的文件", bytes_each=1)
        except OSError:
            pass
        with open(str(path), "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()

    def builtin_strjoin(lst, sep):
        return str(sep).join(str(x) for x in lst)

    def builtin_strreplace(s, old, new):
        """strreplace(s, old, new)：一次扫描替换全部子串，避免逐字符拼接。"""
        return str(s).replace(str(old), str(new))

    def builtin_hash(s):
        import hashlib
        return hashlib.md5(str(s).encode("utf-8")).hexdigest()

    # ---------- 数组 ----------
    def builtin_arrappend(arr, val):
        arr.append(val)
        return arr

    def builtin_arrpop(arr):
        return arr.pop() if len(arr) else None

    def builtin_arrfind(arr, val):
        return arr.index(val) if val in arr else -1

    def builtin_arrfill(sz, v):
        n = int(sz)
        _gate(n, "arrfill(sz, v)")
        return [v for _ in range(n)]

    def builtin_arrdup(arr):
        _gate(len(arr), "arrdup(...)")
        return list(arr)

    def builtin_arrrev(arr):
        _gate(len(arr), "arrrev(...)")
        return list(reversed(arr))

    def builtin_shuffle(arr):
        import random
        random.shuffle(arr)
        return arr

    def builtin_max_arr(arr):
        return max(arr)

    def builtin_min_arr(arr):
        return min(arr)

    def builtin_sum_arr(arr):
        return sum(arr)

    # ---------- 5.2 新增：字符串补充 ----------
    def builtin_strsplit(s, sep=None, maxsplit=-1):
        """strsplit(s[, sep[, maxsplit]])：按分隔符切串；无分隔符按连续空白切。"""
        text = str(s)
        if sep is None:
            return text.split(maxsplit=int(maxsplit))
        return text.split(str(sep), int(maxsplit))

    def builtin_strcount(s, sub):
        """strcount(s, sub)：子串出现次数（不重叠）。"""
        return str(s).count(str(sub))

    def builtin_strstart(s, prefix):
        """strstart(s, prefix)：是否以 prefix 开头。"""
        return str(s).startswith(str(prefix))

    def builtin_strend(s, suffix):
        """strend(s, suffix)：是否以 suffix 结尾。"""
        return str(s).endswith(str(suffix))

    # ---------- 5.2 新增：数组补充 ----------
    def builtin_arrinsert(arr, idx, val):
        """arrinsert(arr, i, v)：在位置 i 插入，返回新数组。"""
        out = list(arr)
        out.insert(int(idx), val)
        return out

    def builtin_arrremove(arr, val):
        """arrremove(arr, v)：删除首个等于 v 的元素，返回新数组；不存在报错。"""
        out = list(arr)
        try:
            out.remove(val)
        except ValueError:
            raise ImporException("ValueError", "arrremove 找不到元素：%s" % str(val), 0)
        return out

    def builtin_arrsort(arr, reverse=False):
        """arrsort(arr[, reverse])：排序后的新数组，不改原数组。"""
        return sorted(arr, reverse=bool(reverse))

    def builtin_arrcount(arr, val):
        """arrcount(arr, v)：等于 v 的元素个数。"""
        return arr.count(val)

    # ---------- 5.2 新增：数值补充 ----------
    def builtin_round(x, ndigits=None):
        """round(x[, n])：四舍六入五成双；不带 n 返回整数。"""
        if ndigits is None:
            return round(x)
        return round(x, int(ndigits))

    def builtin_floor(x):
        """floor(x)：向下取整。"""
        return math.floor(x)

    def builtin_ceil(x):
        """ceil(x)：向上取整。"""
        return math.ceil(x)

    # ---------- 5.2 新增：逻辑 / 函数式 / 组合 ----------
    def builtin_any(arr):
        """any(arr)：任一元素为真返回 true。"""
        return any(arr)

    def builtin_all(arr):
        """all(arr)：全部元素为真返回 true。"""
        return all(arr)

    def builtin_map(fn, arr):
        """map(fn, arr)：对每个元素调用 fn，收集结果。"""
        return [fn(x) for x in arr]

    def builtin_filter(fn, arr):
        """filter(fn, arr)：保留 fn(x) 为真的元素。"""
        return [x for x in arr if fn(x)]

    def builtin_zip(*arrs):
        """zip(a, b, ...)：按位置组合，长度取最短。"""
        return [list(group) for group in zip(*arrs)]

    def builtin_enumerate(arr):
        """enumerate(arr)：每项变成 [下标, 值]。"""
        return [[i, x] for i, x in enumerate(arr)]

    # ---------- 游戏工具 ----------
    def builtin_vec2(x=0, y=0):
        return Vec2(x, y)

    def builtin_rgb(r, g, b):
        return Color(r, g, b)

    def builtin_frame():
        return interp._frame_count

    def builtin_fps():
        return interp._fps

    # ---------- 时间 ----------
    def builtin_now():
        return time.ctime()

    def builtin_clock():
        return time.time()

    def builtin_sleep(sec):
        try:
            sec = float(sec)
        except Exception as e:
            raise wrap_py_error(e)
        if sec <= 0:
            return
        if gui_root is not None and threading.current_thread() is threading.main_thread():
            _gui_sleep(gui_root, sec)
        else:
            time.sleep(sec)

    def builtin_pause():
        if gui_root is not None and threading.current_thread() is threading.main_thread():
            _gui_pause(gui_root)
        else:
            try:
                input("按回车继续...")
            except EOFError:
                pass

    # ---------- 监视与调试 ----------
    def builtin_watch(*vars):
        for v in vars:
            interp.watch_list.add(str(v))
        if interp.snapshot_cb:
            interp.snapshot_cb(interp.collect_globals(), interp.watch_list)
        return True

    def builtin_snapshot():
        if interp.snapshot_cb:
            interp.snapshot_cb(interp.collect_globals(), interp.watch_list)
        return True

    def builtin_breakpoint():
        if interp.debug_mode:
            if gui_root is not None and threading.current_thread() is threading.main_thread():
                _gui_breakpoint(gui_root, interp)
            elif gui_root is not None:
                if interp.log_cb:
                    interp.log_cb("[断点] 后台运行时暂不支持图形调试面板（改用 impor run-dbg）")
            else:
                print("[断点] 调试暂停，按回车继续...")
                try:
                    input()
                except EOFError:
                    pass
                except KeyboardInterrupt:
                    pass

    def builtin_import_path(p):
        interp.module_search_paths.append(str(p))
        return True

    def builtin_reload(modname):
        return interp.reload_module(str(modname))

    # ---------- 文件（沙盒禁用） ----------
    def builtin_open(fn, mode="r"):
        _check_sandbox("open")
        try:
            return open(fn, mode, encoding="utf-8")
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_readln(fn):
        _check_sandbox("readln")
        try:
            return open(fn, "r", encoding="utf-8").readline()
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_writeln(fn, txt):
        _check_sandbox("writeln")
        try:
            with open(fn, "a", encoding="utf-8") as f:
                f.write(str(txt) + "\n")
            return True
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_filemv(old, new):
        _check_sandbox("filemv")
        try:
            os.rename(old, new)
            return True
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_filecat(f1, f2):
        _check_sandbox("filecat")
        try:
            with open(f1, "r", encoding="utf-8") as a, open(f2, "r", encoding="utf-8") as b:
                return a.read() + b.read()
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_fileread(path):
        """fileread(path)：读取整个文本文件返回字符串（v2.14，自举工具链）。"""
        _check_sandbox("fileread")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_filewrite(path, content):
        """filewrite(path, content)：覆盖写入文本文件（v2.14，自举工具链）。"""
        _check_sandbox("filewrite")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(content))
            return True
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_filewritebytes(path, data):
        """filewritebytes(path, 表)：把 0-255 的整数序列按字节写入文件。

        v3.8 原生工具链用：汇编器/PE 写出器需要生成任意二进制字节，
        普通 filewrite 只能写 UTF-8 文本，无法表示 0x00 等控制字节。
        """
        _check_sandbox("filewritebytes")
        try:
            raw = bytearray()
            for v in list(data):
                n = int(v)
                if n < 0 or n > 255:
                    raise ValueError("字节值必须在 0..255 范围内")
                raw.append(n)
            with open(path, "wb") as f:
                f.write(raw)
            return True
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    def builtin_system(cmd):
        """system(cmd)：执行外部命令，返回退出码（v2.14，自举工具链）。"""
        _check_sandbox("system")
        try:
            return os.system(str(cmd))
        except Exception as e:
            raise ImporException("IOError", str(e), 0)

    # ---------- 自动内存管理（v3.2） ----------
    def builtin_mem():
        """mem()：打印并返回当前内存摘要。"""
        try:
            text = interp.memory.report()
        except Exception as e:
            raise wrap_py_error(e)
        if interp.log_cb:
            interp.log_cb(text)
        return text

    def builtin_memstats():
        """memstats()：返回内存统计字典（alloc/peak/live/collections/...）。"""
        try:
            return interp.memory.stats()
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_gc():
        """gc()：手动触发一次回收，返回被回收的循环对象数。"""
        try:
            freed = interp.memory.collect(reason="manual")
        except Exception as e:
            raise wrap_py_error(e)
        text = ("自动内存回收完成：清理 %d 个循环引用对象（累计 %d 次）"
                "\n  Impor 容器由 CPython 引用计数即时释放，gc() 只处理循环引用与解释器缓存"
                % (freed, interp.memory.collections))
        if interp.log_cb:
            interp.log_cb(text)
        return freed

    def builtin_autogc(limit=None):
        """autogc([limit])：查询/设置自动回收阈值（0 关闭）。"""
        try:
            if limit is not None:
                if float(limit) <= 0:
                    interp.memory.set_enabled(False)
                    return 0
                interp.memory.set_enabled(True)
                return interp.memory.set_threshold(limit)
            return interp.memory.threshold
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_filelist(path):
        """filelist(dir)：返回目录下的文件名数组（不含 . 和 ..）。"""
        try:
            return sorted(os.listdir(path))
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_filemkdir(path):
        """filemkdir(dir)：创建目录，已存在则忽略。"""
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_fileexists(path):
        """fileexists(path)：路径存在返回 true。"""
        try:
            return os.path.exists(path)
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_filesize(path):
        """filesize(path)：返回文件字节数；目录返回 0。"""
        try:
            return os.path.getsize(path) if os.path.isfile(path) else 0
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_exedir():
        """exedir()：当前程序所在目录；供自举编译器定位随包运行时。"""
        try:
            p = os.path.abspath(sys.argv[0]) if getattr(sys, "argv", None) else ""
            return os.path.dirname(p) if p else os.getcwd()
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_json_encode(value):
        """json_encode(v)：把 Impor 值序列化为 JSON 字符串。"""
        try:
            import json
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            raise wrap_py_error(e)

    def builtin_json_decode(text):
        """json_decode(s)：把 JSON 字符串解析为 Impor 值。"""
        try:
            import json
            return json.loads(text)
        except Exception as e:
            raise ImporException("ValueError", f"JSON 解析失败：{e}", 0)

    def builtin_regex_match(text, pattern):
        """regex_match(s, pat)：整串匹配返回 bool。"""
        try:
            import re
            return re.fullmatch(pattern, text) is not None
        except Exception as e:
            raise ImporException("ValueError", f"正则无效：{e}", 0)

    def builtin_regex_find(text, pattern):
        """regex_find(s, pat)：返回全部匹配子串数组。"""
        try:
            import re
            return [m.group(0) for m in re.finditer(pattern, text)]
        except Exception as e:
            raise ImporException("ValueError", f"正则无效：{e}", 0)

    def builtin_regex_replace(text, pattern, repl):
        """regex_replace(s, pat, repl)：替换全部匹配。"""
        try:
            import re
            return re.sub(pattern, repl, text)
        except Exception as e:
            raise ImporException("ValueError", f"正则无效：{e}", 0)

    def builtin_run_tasks(tasks):
        """run_tasks([t1, t2])：按顺序运行协程任务，返回结果数组。"""
        out = []
        for t in list(tasks or []):
            if isinstance(t, Task):
                out.append(t.run())
            else:
                out.append(t)
        return out

    def builtin_task_status(task):
        """task_status(t)：返回 new/done。"""
        if not isinstance(task, Task):
            return "done"
        return "done" if task.done else "new"

    # ---------- 3.2.1 FFI：受控原生库调用 ----------
    _ffi_libs = {}
    _ffi_loaded = set()
    # 3.6.2：受控原生缓冲区。语言层只拿到 token，拿不到任意地址；
    # 缓冲区由运行时托管，脚本退出自动释放，不暴露手动 free。
    _ffi_buffers = {}

    def _ffi_allowlist():
        """FFI 允许清单：默认拒绝一切原生库。

        来源（合并）：环境变量 IMPOR_FFI_ALLOW（逗号或分号分隔的 DLL 文件名）、
        当前脚本同目录/工作区向上查找 `.impor/ffi_allow.json` 的 `allow` 数组。
        """
        allow = set()
        env = os.environ.get("IMPOR_FFI_ALLOW", "")
        for item in re.split(r"[,;]", env):
            item = item.strip()
            if item:
                allow.add(item.lower())
        base = os.path.dirname(os.path.abspath(interp.filename or "."))
        for _ in range(6):
            cfg = os.path.join(base, ".impor", "ffi_allow.json")
            if os.path.isfile(cfg):
                try:
                    with open(cfg, encoding="utf-8") as f:
                        data = json.load(f)
                    for item in (data.get("allow") or []):
                        allow.add(str(item).lower())
                except Exception:
                    note_swallowed("sandbox allow list")
                break
            parent = os.path.dirname(base)
            if parent == base:
                break
            base = parent
        return allow

    def builtin_ffi_open(path):
        """ffi_open("libs/mylib.dll")：加载允许清单内的原生库，返回库句柄名。"""
        raw = str(path)
        base = os.path.dirname(os.path.abspath(interp.filename or "."))
        target = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(base, raw))
        name = os.path.basename(target).lower()
        if name not in _ffi_allowlist():
            active = ", ".join(sorted(_ffi_allowlist())) or "（空）"
            raise ImporException(
                "FFIAccessError",
                "原生库 %s 不在允许清单内；当前生效清单：%s。请在 .impor/ffi_allow.json 的 allow 数组登记，"
                "或设置 IMPOR_FFI_ALLOW 环境变量（逗号或分号分隔）" % (name, active), 0)
        if not os.path.isfile(target):
            raise ImporException("FFIError", "原生库不存在：%s" % target, 0)
        from .runtime import _pe_machine, _host_bits, _PE_MACHINES
        machine = _pe_machine(target)
        if machine is not None and machine in _PE_MACHINES:
            bits, arch = _PE_MACHINES[machine]
            if int(bits[:2]) != _host_bits():
                raise ImporException(
                    "FFIArchError",
                    "位宽不匹配：解释器为 %d 位，目标库为 %s(%s)。"
                    "请用 impor build 产出的 32 位 exe，或用 --c-source 编 64 位库"
                    % (_host_bits(), bits, arch), 0)
        try:
            lib = ctypes.WinDLL(target) if os.name == "nt" else ctypes.CDLL(target)
        except Exception as e:
            raise ImporException("FFIError", "加载原生库失败：%s" % e, 0)
        _ffi_libs[target] = lib
        _ffi_loaded.add(name)
        return target

    def _ffi_buffer(token):
        try:
            key = int(token)
        except Exception:
            raise ImporException("FFIError", "缓冲区 token 必须是整数", 0)
        buf = _ffi_buffers.get(key)
        if buf is None:
            raise ImporException("FFIError", "未知或已释放的缓冲区 token：%s" % key, 0)
        return buf

    def _ffi_addr(token):
        if int(token) == 0:
            return None
        return ctypes.addressof(_ffi_buffer(token))

    def builtin_ffi_alloc(size):
        """ffi_alloc(n)：分配 n 字节受控缓冲区，返回 token；运行时托管，自动释放。"""
        try:
            n = int(size)
        except Exception:
            raise ImporException("FFIError", "ffi_alloc 大小必须是整数", 0)
        if n <= 0:
            raise ImporException("FFIError", "ffi_alloc 大小必须大于 0", 0)
        buf = ctypes.create_string_buffer(n)
        token = len(_ffi_buffers) + 1
        _ffi_buffers[token] = buf
        return token

    def builtin_ffi_alloc_str(value):
        """ffi_alloc_str(s)：分配以 NUL 结尾的 UTF-8 C 字符串缓冲区，返回 token。"""
        raw = str(value).encode("utf-8")
        buf = ctypes.create_string_buffer(raw, len(raw) + 1)
        token = len(_ffi_buffers) + 1
        _ffi_buffers[token] = buf
        return token

    def builtin_ffi_buffer_size(token):
        return len(_ffi_buffer(token))

    def builtin_ffi_read_i64(token, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        if off < 0 or off + 8 > len(buf):
            raise ImporException("FFIError", "读取越界：offset=%d size=%d" % (off, len(buf)), 0)
        return ctypes.c_int64.from_buffer(buf, off).value

    def builtin_ffi_write_i64(token, value, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        if off < 0 or off + 8 > len(buf):
            raise ImporException("FFIError", "写入越界：offset=%d size=%d" % (off, len(buf)), 0)
        ctypes.c_int64.from_buffer(buf, off).value = int(value)
        return int(value)

    def builtin_ffi_read_f64(token, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        if off < 0 or off + 8 > len(buf):
            raise ImporException("FFIError", "读取越界：offset=%d size=%d" % (off, len(buf)), 0)
        return ctypes.c_double.from_buffer(buf, off).value

    def builtin_ffi_write_f64(token, value, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        if off < 0 or off + 8 > len(buf):
            raise ImporException("FFIError", "写入越界：offset=%d size=%d" % (off, len(buf)), 0)
        ctypes.c_double.from_buffer(buf, off).value = float(value)
        return float(value)

    def builtin_ffi_read_str(token, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        if off < 0 or off >= len(buf):
            raise ImporException("FFIError", "字符串读取越界：offset=%d size=%d" % (off, len(buf)), 0)
        raw = bytes(buf)[off:]
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")

    def builtin_ffi_read_str_at(addr):
        """ffi_read_str_at(addr)：读取原生侧返回的 char* 地址（v3.10）。

        这是给 sqlite3_column_text 这类“返回指针”的 API 用的最小能力：
        只读一个 NUL 结尾字符串，不提供结构体布局或指针运算。
        """
        try:
            p = int(addr)
        except Exception:
            raise ImporException("FFIError", "ffi_read_str_at 的地址必须是整数", 0)
        if p == 0:
            return ""
        try:
            value = ctypes.cast(ctypes.c_void_p(p), ctypes.c_char_p).value
        except Exception as e:
            raise ImporException("FFIError", "读取原生字符串失败：%s" % e, 0)
        return (value or b"").decode("utf-8", "replace")

    def builtin_ffi_write_str(token, value, offset=0):
        buf = _ffi_buffer(token)
        off = int(offset)
        raw = str(value).encode("utf-8")
        if off < 0 or off + len(raw) > len(buf):
            raise ImporException("FFIError", "字符串写入越界：offset=%d size=%d" % (off, len(buf)), 0)
        buf[off:off + len(raw)] = raw
        return str(value)

    def builtin_ffi_buffers():
        return len(_ffi_buffers)

    def builtin_ffi_call(lib, func, args=None, ret="int", argtypes=None, calling=None):
        """ffi_call(handle, "func", [args], "int|float|str|bool|ptr|addr|cstr|void", [types], "stdcall"?)。

        3.6.2 起支持受控指针：`ptr` 参数必须是 ffi_alloc / ffi_alloc_str 返回的 token，
        不接受任意整数地址；`cstr` 参数接受普通字符串。C 字符串返回值用 `cstr`。
        """
        target = str(lib)
        if target not in _ffi_libs:
            target = builtin_ffi_open(target)
        if calling not in (None, "", "stdcall", "cdecl"):
            raise ImporException("FFIError", "calling 只支持 stdcall / cdecl", 0)
        fn = getattr(_ffi_libs[target], str(func), None)
        if fn is None:
            raise ImporException("FFIError", "原生库没有函数 %s" % func, 0)
        vals = list(args or [])
        if len(vals) > 8:
            raise ImporException("FFIError", "FFI 最多支持 8 个参数", 0)
        specs = list(argtypes or [])
        pyargs = []
        for i, v in enumerate(vals):
            spec = str(specs[i]).lower() if i < len(specs) else ""
            if spec == "ptr":
                pyargs.append(ctypes.c_void_p(_ffi_addr(v)))
            elif spec == "addr":
                # v3.10：裸地址。若给的是 ffi_alloc token，就转成该缓冲区地址；
                # 否则当作原生侧返回的整数地址直接透传。不提供指针运算。
                try:
                    iv = int(v)
                except Exception:
                    raise ImporException("FFIError", "addr 参数必须是整数地址或 ffi_alloc token", 0)
                pyargs.append(ctypes.c_void_p(
                    _ffi_addr(iv) if iv in _ffi_buffers else iv))
            elif spec == "cstr":
                pyargs.append(ctypes.c_char_p(str(v).encode("utf-8")))
            elif spec == "float":
                pyargs.append(ctypes.c_double(float(v)))
            elif spec in ("i32", "int32"):
                pyargs.append(ctypes.c_int(int(v)))
            elif spec in ("int", "bool"):
                pyargs.append(ctypes.c_longlong(1 if (spec == "bool" and v) else int(v)))
            elif isinstance(v, bool):
                pyargs.append(ctypes.c_int(1 if v else 0))
            elif isinstance(v, int):
                pyargs.append(ctypes.c_longlong(v))
            elif isinstance(v, float):
                pyargs.append(ctypes.c_double(v))
            elif isinstance(v, str):
                pyargs.append(ctypes.c_wchar_p(v))
            else:
                raise ImporException("FFIError", "不支持的 FFI 参数类型", 0)
        rspec = str(ret).lower()
        try:
            fn.argtypes = [type(a) for a in pyargs]
            fn.restype = {
                "int": ctypes.c_longlong, "float": ctypes.c_double,
                "str": ctypes.c_wchar_p, "bool": ctypes.c_int,
                "ptr": ctypes.c_void_p, "cstr": ctypes.c_char_p,
                "void": None,
            }.get(rspec, ctypes.c_longlong)
            out = fn(*pyargs)
        except Exception as e:
            raise ImporException("FFIError", "原生调用失败：%s" % e, 0)
        if rspec == "bool":
            return bool(out)
        if rspec == "void":
            return None
        if rspec == "ptr":
            return int(out or 0)
        if rspec == "cstr":
            return out.decode("utf-8", "replace") if out else ""
        return out

    builtins_map = {
        "print": builtin_print, "exit": builtin_exit,
        "input": builtin_input, "inputstr": builtin_inputstr,
        "range": builtin_range, "len": builtin_len, "list": builtin_list,
        "expect": builtin_expect,
        "type": builtin_type, "type_of": builtin_type, "to_num": builtin_to_num, "to_str": builtin_to_str,
        "str": builtin_to_str, "int": builtin_int,
        "args": builtin_args,
        "clamp": builtin_clamp, "lerp": builtin_lerp, "remap": builtin_remap,
        "sign": builtin_sign, "abs": builtin_abs, "min": builtin_min, "max": builtin_max,
        "div": builtin_div,
        "distance": builtin_distance, "angle": builtin_angle,
        "randi": builtin_randi, "randstr": builtin_randstr, "seed": builtin_seed,
        "trim": builtin_trim, "upper": builtin_upper, "lower": builtin_lower,
        "strpad": builtin_strpad, "strcut": builtin_strcut, "strjoin": builtin_strjoin,
        "ord": builtin_ord, "chr": builtin_chr,
        "hash": builtin_hash,
        "arrappend": builtin_arrappend, "arrpop": builtin_arrpop,
        "arrfind": builtin_arrfind, "arrfill": builtin_arrfill,
        "arrdup": builtin_arrdup, "arrrev": builtin_arrrev, "shuffle": builtin_shuffle,
        "max_arr": builtin_max_arr, "min_arr": builtin_min_arr, "sum_arr": builtin_sum_arr,
        "strsplit": builtin_strsplit, "strcount": builtin_strcount,
        "strstart": builtin_strstart, "strend": builtin_strend,
        "arrinsert": builtin_arrinsert, "arrremove": builtin_arrremove,
        "arrsort": builtin_arrsort, "arrcount": builtin_arrcount,
        "round": builtin_round, "floor": builtin_floor, "ceil": builtin_ceil,
        "any": builtin_any, "all": builtin_all,
        "map": builtin_map, "filter": builtin_filter,
        "zip": builtin_zip, "enumerate": builtin_enumerate,
        "vec2": builtin_vec2, "rgb": builtin_rgb,
        "frame": builtin_frame, "fps": builtin_fps,
        "now": builtin_now, "clock": builtin_clock,
        "sleep": builtin_sleep, "wait": builtin_sleep, "pause": builtin_pause,
        "watch": builtin_watch, "snapshot": builtin_snapshot,
        "breakpoint": builtin_breakpoint, "import_path": builtin_import_path,
        "reload": builtin_reload,
        "open": builtin_open, "readln": builtin_readln, "writeln": builtin_writeln,
        "filemv": builtin_filemv, "filecat": builtin_filecat,
        "fileread": builtin_fileread, "filewrite": builtin_filewrite,
        "filewritebytes": builtin_filewritebytes,
        "filelist": builtin_filelist, "filemkdir": builtin_filemkdir,
        "fileexists": builtin_fileexists, "filesize": builtin_filesize,
        "exedir": builtin_exedir,
        "system": builtin_system,
        "mem": builtin_mem, "memstats": builtin_memstats,
        "gc": builtin_gc, "autogc": builtin_autogc,
        "json_encode": builtin_json_encode, "json_decode": builtin_json_decode,
        "regex_match": builtin_regex_match, "regex_find": builtin_regex_find,
        "regex_replace": builtin_regex_replace,
        "strfind": builtin_strfind,
        "strlines": builtin_strlines,
        "filelines": builtin_filelines,
        "strreplace": builtin_strreplace,
        "run_tasks": builtin_run_tasks, "task_status": builtin_task_status,
        "ffi_open": builtin_ffi_open, "ffi_call": builtin_ffi_call,
        "ffi_alloc": builtin_ffi_alloc, "ffi_alloc_str": builtin_ffi_alloc_str,
        "ffi_buffer_size": builtin_ffi_buffer_size,
        "ffi_read_i64": builtin_ffi_read_i64, "ffi_write_i64": builtin_ffi_write_i64,
        "ffi_read_f64": builtin_ffi_read_f64, "ffi_write_f64": builtin_ffi_write_f64,
        "ffi_read_str": builtin_ffi_read_str, "ffi_write_str": builtin_ffi_write_str,
        "ffi_read_str_at": builtin_ffi_read_str_at,
        "ffi_buffers": builtin_ffi_buffers,
    }
    # 4.2：线程/锁/原子量——实现集中在 rt_concurrency.py，解释器与转译产物共用。
    # thread_spawn 额外绑定当前解释器：新线程会拿到一份执行副本（独立 cur_env）。
    from .rt_concurrency import registry as _conc_registry
    builtins_map.update(_conc_registry())
    builtins_map["thread_spawn"] = (
        lambda fn: _conc_registry()["thread_spawn"](fn, interp))
    missing, extra = check_interpreter_names(builtins_map)
    if missing or extra:
        raise RuntimeError(
            "内建注册表与 builtin_manifest 不一致；"
            f"缺少 {missing}，多出 {extra}"
        )
    return builtins_map


def _input_impl(gui_root, interp, prompt):
    if gui_root is None:
        return input(prompt)
    if threading.current_thread() is not threading.main_thread():
        # 编辑器后台运行线程：把输入对话框投递回主线程执行，避免跨线程操作 Tk
        box = {}
        done = threading.Event()

        def ask():
            try:
                box["v"] = _input_impl(gui_root, interp, prompt)
            finally:
                done.set()

        try:
            gui_root.after(0, ask)
        except Exception:
            return ""
        done.wait()
        return box.get("v", "")
    import tkinter as tk
    from tkinter import ttk
    from .icons import apply_window_icon
    dlg = tk.Toplevel(gui_root)
    dlg.title("Impor input")
    apply_window_icon(dlg)
    dlg.geometry("420x120")
    dlg.transient(gui_root)
    dlg.grab_set()
    ttk.Label(dlg, text=prompt).pack(pady=5)
    e = ttk.Entry(dlg, width=50)
    e.pack(padx=10, pady=5)
    res = [None]
    def ok():
        res[0] = e.get()
        dlg.destroy()
    ttk.Button(dlg, text="确定", command=ok).pack(pady=4)
    e.focus_set()
    gui_root.wait_window(dlg)
    return res[0] if res[0] is not None else ""


def _gui_sleep(root, sec):
    """GUI 模式下的非阻塞 sleep：边刷新事件边等待。"""
    t0 = time.time()
    while time.time() - t0 < sec:
        root.update()
        time.sleep(0.01)


def _gui_pause(root):
    from tkinter import messagebox
    messagebox.showinfo("Impor pause", "已暂停，点击确定继续")


def _gui_breakpoint(root, interp):
    """GUI 断点面板：调用栈 + 当前帧局部变量 + 继续。"""
    import tkinter as tk
    from .icons import apply_window_icon
    win = tk.Toplevel(root)
    win.title("Impor 断点")
    apply_window_icon(win)
    win.transient(root)
    win.grab_set()

    frm = tk.Frame(win, padx=10, pady=8)
    frm.pack(fill=tk.BOTH, expand=True)
    tk.Label(frm, text="调用栈（自顶向下）",
             font=("Microsoft YaHei UI", 10, "bold")).pack(anchor=tk.W)
    stack_txt = tk.Text(frm, height=6, width=58)
    stack_txt.pack(fill=tk.BOTH, expand=True, pady=(2, 6))
    for fr in reversed(interp.call_stack):
        stack_txt.insert(tk.END, f"  {fr.name}（第{fr.lineno}行）\n")
    stack_txt.config(state=tk.DISABLED)

    tk.Label(frm, text="当前帧局部变量",
             font=("Microsoft YaHei UI", 10, "bold")).pack(anchor=tk.W)
    var_txt = tk.Text(frm, height=7, width=58)
    var_txt.pack(fill=tk.BOTH, expand=True, pady=(2, 6))
    items = {k: v for k, v in interp.cur_env.store.items() if not k.startswith("__")}
    for k in sorted(items):
        var_txt.insert(tk.END, f"  {k} = {items[k]!r}\n")
    if not items:
        var_txt.insert(tk.END, "  （无局部变量）\n")
    var_txt.config(state=tk.DISABLED)

    tk.Button(frm, text="继续 (c)", width=12, command=win.destroy).pack(pady=(0, 4))
    win.wait_window()


# ---------------- 内建函数速查表（REPL help() / impor help 用，v2.16） ----------------
BUILTIN_DOCS = {
    "print": "print(a, b, ...)：输出各项，空格分隔，末尾换行。",
    "input": "input(提示)：读取一行并转为数值（无法转换时报错）。",
    "inputstr": "inputstr(提示)：读取一行原始字符串。",
    "len": "len(x)：字符串长度 / 数组元素个数。",
    "expect": "expect(实际, 期望[, 标签])：断言两者相等（表驱动测试用，v4.2）。",
    "range": "range(a, b[, step])：整数序列（配合 for）。",
    "list": "list()：新建空数组；list(n)：新建 n 个 0 的数组。",
    "type": "type(x)：返回值的类型名。",
    "type_of": "type_of(x)：返回值的类型名（type 的别名）。",
    "to_num": "to_num(x)：转数值。",
    "to_str": "to_str(x)：转字符串（等价 str(x)，兼容旧写法）。",
    "str": "str(x)：转字符串（v4.3 推荐写法）。",
    "int": "int(x)：转整数，浮点数截断，无法解析返回 0（v4.3）。",
    "div": "div(a, b)：整数除法，向下取整（v2.16）。",
    "clamp": "clamp(x, lo, hi)：把 x 限制到 [lo, hi]。",
    "abs": "abs(x)：绝对值。",
    "min": "min(a, b, ...)：取最小值。",
    "max": "max(a, b, ...)：取最大值。",
    "sign": "sign(x)：-1 / 0 / 1。",
    "lerp": "lerp(a, b, t)：线性插值。",
    "remap": "remap(x, a1, b1, a2, b2)：数值区间映射。",
    "trim": "trim(s)：去除首尾空白。",
    "upper": "upper(s)：转大写。",
    "lower": "lower(s)：转小写。",
    "strpad": "strpad(s, n[, ch])：补齐到长度 n。",
    "strcut": "strcut(s, st, ed)：截取 [st, ed) 子串。",
    "strjoin": "strjoin(arr[, 分隔符])：数组拼接为字符串。",
    "strreplace": "strreplace(s, 旧, 新)：替换全部子串。",
    "hash": "hash(s)：字符串哈希。",
    "randi": "randi(a, b)：[a, b] 随机整数。",
    "randstr": "randstr(n)：随机 n 位字符串。",
    "seed": "seed(n)：随机种子。",
    "arrappend": "arrappend(arr, v)：末尾追加，返回新数组。",
    "arrpop": "arrpop(arr)：弹出末尾元素。",
    "arrfind": "arrfind(arr, v)：查找元素下标（-1 表示未找到）。",
    "arrfill": "arrfill(arr, v)：全部填充为 v。",
    "arrdup": "arrdup(arr)：浅拷贝。",
    "arrrev": "arrrev(arr)：反转。",
    "shuffle": "shuffle(arr)：洗牌。",
    "max_arr": "max_arr(arr)：数组最大值。",
    "min_arr": "min_arr(arr)：数组最小值。",
    "sum_arr": "sum_arr(arr)：数组求和。",
    "strsplit": "strsplit(s[, sep[, maxsplit]])：切串为数组。",
    "strcount": "strcount(s, sub)：子串出现次数。",
    "strstart": "strstart(s, prefix)：是否以 prefix 开头。",
    "strend": "strend(s, suffix)：是否以 suffix 结尾。",
    "arrinsert": "arrinsert(arr, i, v)：位置 i 插入，返回新数组。",
    "arrremove": "arrremove(arr, v)：删除首个 v，返回新数组。",
    "arrsort": "arrsort(arr[, reverse])：排序后的新数组。",
    "arrcount": "arrcount(arr, v)：等于 v 的元素个数。",
    "round": "round(x[, n])：四舍六入五成双。",
    "floor": "floor(x)：向下取整。",
    "ceil": "ceil(x)：向上取整。",
    "any": "any(arr)：任一为真返回 true。",
    "all": "all(arr)：全部为真返回 true。",
    "map": "map(fn, arr)：逐元素调用 fn。",
    "filter": "filter(fn, arr)：保留 fn(x) 为真的元素。",
    "zip": "zip(a, b, ...)：按位置组合。",
    "enumerate": "enumerate(arr)：每项变成 [下标, 值]。",
    "args": "args()：命令行参数数组。",
    "vec2": "vec2(x, y)：二维向量。",
    "rgb": "rgb(r, g, b)：颜色值（0-255）。",
    "now": "now()：当前时间戳（秒）。",
    "clock": "clock()：进程 CPU 时间。",
    "sleep": "sleep(秒)：暂停。",
    "wait": "wait(秒)：暂停（同 sleep）。",
    "pause": "pause()：暂停等待确认。",
    "watch": "watch(秒)：观察模式延时。",
    "snapshot": "snapshot()：GUI 截屏。",
    "breakpoint": "breakpoint()：调试断点。",
    "import_path": "import_path(p)：向模块搜索路径追加目录。",
    "reload": "reload(模块)：重新加载模块。",
    "open": "open(路径)：打开文本文件。",
    "readln": "readln(文件)：读一行。",
    "writeln": "writeln(路径, 文本)：追加写一行。",
    "filemv": "filemv(旧, 新)：移动/重命名文件。",
    "filecat": "filecat(a, b)：读取两个文件并拼接。",
    "fileread": "fileread(路径)：读取整个文件为字符串。",
    "filewrite": "filewrite(路径, 内容)：覆盖写入文件。",
    "filewritebytes": "filewritebytes(路径, 表)：把 0-255 的整数表按字节写入文件（原生工具链）。",
    "filelist": "filelist(目录)：返回目录下文件名数组。",
    "filemkdir": "filemkdir(目录)：创建目录，已存在则忽略。",
    "fileexists": "fileexists(路径)：路径存在返回 true。",
    "filesize": "filesize(路径)：返回文件字节数，目录返回 0。",
    "exedir": "exedir()：返回当前程序所在目录（自举编译器用于定位随包运行时）。",
    "system": "system(命令)：执行外部命令，返回退出码。",
    "mem": "mem()：打印并返回当前内存摘要（v3.2 自动内存管理）。",
    "memstats": "memstats()：返回内存统计字典（分配/峰值/存活/回收次数）。",
    "gc": "gc()：手动触发一次自动内存回收，返回清理的循环对象数。",
    "autogc": "autogc([阈值])：查询或设置自动回收阈值（0 关闭自动回收）。",
    "json_encode": "json_encode(v)：序列化为 JSON 字符串（中文不转义）。",
    "json_decode": "json_decode(s)：解析 JSON 字符串为数组/字典/值。",
    "regex_match": "regex_match(s, pat)：整串匹配返回真/假。",
    "regex_find": "regex_find(s, pat)：返回全部匹配子串数组。",
    "regex_replace": "regex_replace(s, pat, repl)：替换全部匹配。",
}
