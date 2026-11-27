# -*- coding: utf-8 -*-
"""Impor 并发运行库：线程 / 互斥锁 / 原子量（解释器与 Python 转译产物共用）。

C 后端在 `impor/c_runtime.c` 里有等价实现（`rt_thread_*` / `rt_mutex_*` /
`rt_atomic_*`）。两端口径必须一致，改动任何一处都要同步另一处：

- 句柄都是整数 id，跨种类**不通用**（线程 id 不能当锁用）。
- `thread_spawn(fn)` 的 `fn` 必须无参；`thread_join(h)` 返回 `fn()` 的返回值，
  重复 join 返回同一结果，不会重复等待。
- `atomic_add/atomic_sub/atomic_exchange` 返回**旧值**（与 C 的
  `__sync_fetch_and_add` / `InterlockedExchange64` 一致）。
- `atomic_cas(a, expect, new)` 成功返回 true，失败返回 false（不刷新值）。

线程安全约定（与 C 后端完全一致，别指望语言层帮你兜住）：

- 原子量只保证**单个 64 位整数**的读改写不撕裂，不提供事务。
- 列表 / 字典 / 对象等可变容器跨线程共享时，**必须自己用 mutex 包住**；
  Impor 的 C 运行库不做引用计数，裸并发改同一个数组属于未定义行为。
"""

import threading

_INT64_MAX = (1 << 63) - 1
_INT64_MIN = -(1 << 63)

_lock = threading.Lock()
_threads = {}
_mutexes = {}
_atoms = {}
_conds = {}
_next = [0]


def _i64(v):
    """把任意数值截成 C 后端的 signed 64 位语义（越界回绕）。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = 0
    n &= 0xFFFFFFFFFFFFFFFF
    if n > _INT64_MAX:
        n -= 1 << 64
    return n


def _new_id(kind):
    with _lock:
        _next[0] += 1
        return _next[0]


def _clone_for_thread(interp):
    """给新线程一份解释器执行副本：独立 cur_env / 调用栈，共享全局环境。

    解释器的 cur_env、call_stack 都是实例属性，两个线程共用一个实例会互相
    覆盖（实测表现为「变量 i 未定义」）。浅拷贝能拿到独立的执行状态，
    同时 global_env / builtins / 内存管理器仍按引用共享——这正是并发想要的：
    全局变量看得见但需要 mutex 保护。
    """
    if interp is None:
        return None
    try:
        import copy

        from .interpreter import Frame

        inst = copy.copy(interp)
        inst.cur_env = inst.global_env
        inst.call_stack = [Frame("<线程>", inst.global_env, 0)]
        inst._cur_self = None
        inst._cur_cls = None
        inst._dbg_last_stop = -1
        # 关键：解释器的 visit 分派表是「绑定到原实例」的方法字典，
        # 浅拷贝会把它们原样带过来 → 副本执行语句时用的是主实例的 cur_env。
        # 这里把所有绑定在 interp 上的方法（字典里的值）重新绑到副本。
        for name, val in list(inst.__dict__.items()):
            if isinstance(val, dict):
                reb = {}
                for k, fn in val.items():
                    if getattr(fn, "__self__", None) is interp:
                        reb[k] = getattr(inst, fn.__name__)
                    else:
                        reb[k] = fn
                inst.__dict__[name] = reb
            elif getattr(val, "__self__", None) is interp:
                inst.__dict__[name] = getattr(inst, val.__name__)
        return inst
    except Exception:
        return None


# ---------------- 线程 ----------------
def thread_spawn(fn, interp=None):
    """起一个新线程执行 `fn()`（无参），返回线程句柄。

    `interp` 为空时（编译产物的调用路径）自动取运行时解释器：
    std 模块函数是解释器 Closure，即使调用方是 Python 代码，线程里也必须
    拿到执行副本，否则两个线程共用 cur_env 会把局部变量互相覆盖
    （实测表现为「变量 v 未定义」）。
    """
    if not callable(fn):
        raise TypeError("thread_spawn 需要可调用的函数，收到 %r" % (fn,))
    if interp is None:
        try:
            from .runtime import _get_interp
            interp = _get_interp()
        except Exception:
            interp = None
    hid = _new_id("thread")
    box = {"result": 0, "error": None, "done": threading.Event()}

    def _run():
        if interp is not None:
            try:
                from .interpreter import Closure

                inst = _clone_for_thread(interp)
                if inst is not None:
                    Closure._thread_interp.inst = inst
            except Exception:
                pass
        try:
            box["result"] = fn()
        except BaseException as e:   # 线程里的异常存起来，join 时抛给调用方
            box["error"] = e
        finally:
            box["done"].set()

    t = threading.Thread(target=_run, name="impor-thread-%d" % hid, daemon=True)
    with _lock:
        _threads[hid] = {"t": t, "box": box}
    t.start()
    return hid


def _thread_box(h):
    try:
        rec = _threads.get(_i64(h))
    except TypeError:
        rec = None
    if rec is None:
        raise KeyError("未知线程句柄 %r（句柄只在 thread_spawn 返回后有效）" % (h,))
    return rec


def thread_join(h):
    """等待线程结束并返回其返回值；线程内异常在 join 处抛出。"""
    rec = _thread_box(h)
    rec["t"].join()
    if rec["box"]["error"] is not None:
        raise RuntimeError("线程内异常：%s" % (rec["box"]["error"],))
    return rec["box"]["result"]


def thread_alive(h):
    return bool(_thread_box(h)["t"].is_alive())


def thread_id():
    return threading.get_ident()


def cpu_count():
    return max(1, _cpu_count())


def _cpu_count():
    import os
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


# ---------------- 互斥锁 ----------------
def mutex_new():
    hid = _new_id("mutex")
    with _lock:
        _mutexes[hid] = threading.Lock()
    return hid


def _mutex(h):
    try:
        m = _mutexes.get(_i64(h))
    except TypeError:
        m = None
    if m is None:
        raise KeyError("未知锁句柄 %r（先调用 mutex_new()）" % (h,))
    return m


def mutex_lock(h):
    _mutex(h).acquire()
    return 0


def mutex_unlock(h):
    m = _mutex(h)
    if not m.locked():
        raise RuntimeError("mutex_unlock：锁未被持有（重复解锁会破坏互斥语义）")
    m.release()
    return 0


def mutex_trylock(h):
    return bool(_mutex(h).acquire(False))


# ---------------- 条件变量 ----------------
# v4.3.1：条件变量把「轮询等待」升级成「挂起 / 唤醒」。口径与 C 后端一致：
#   - cond_new() 返回整数句柄，跨种类不通用；
#   - cond_wait(cond, mutex) 必须由**已持有** mutex 的线程调用；
#     函数内部先释放 mutex、挂起，被唤醒后再重新持有 mutex 才返回；
#   - cond_wait_ms 多一个超时：返回 true=被唤醒，false=超时；
#   - cond_notify 唤醒一个，cond_notify_all 唤醒全部；没有等待者时通知丢失
#     （条件变量不是信号量，状态要自己用变量记住）。
def cond_new():
    hid = _new_id("cond")
    with _lock:
        _conds[hid] = threading.Condition()
    return hid


def _cond(h):
    try:
        c = _conds.get(_i64(h))
    except TypeError:
        c = None
    if c is None:
        raise KeyError("未知条件变量句柄 %r（先调用 cond_new()）" % (h,))
    return c


def _cond_wait_impl(cv, lock, timeout=None):
    """释放 user mutex → 在 cv 上等待 → 重新持有 user mutex。"""
    with cv:
        lock.release()
        try:
            return cv.wait(timeout)
        finally:
            lock.acquire()


def cond_wait(h, m):
    _cond_wait_impl(_cond(h), _mutex(m), None)
    return 0


def cond_wait_ms(h, m, ms):
    got = _cond_wait_impl(_cond(h), _mutex(m), max(0, _i64(ms)) / 1000.0)
    return bool(got)


def cond_notify(h):
    cv = _cond(h)
    with cv:
        cv.notify(1)
    return 0


def cond_notify_all(h):
    cv = _cond(h)
    with cv:
        cv.notify_all()
    return 0


# ---------------- 原子量 ----------------
def atomic_new(v=0):
    hid = _new_id("atom")
    with _lock:
        _atoms[hid] = _i64(v)
    return hid


def _atom_read(h):
    try:
        if _i64(h) in _atoms:
            return _i64(h)
    except TypeError:
        pass
    raise KeyError("未知原子量句柄 %r（先调用 atomic_new()）" % (h,))


def atomic_load(a):
    key = _atom_read(a)
    with _lock:
        return _atoms[key]


def atomic_store(a, v):
    key = _atom_read(a)
    with _lock:
        old = _atoms[key]
        _atoms[key] = _i64(v)
    return old


def atomic_add(a, n):
    key = _atom_read(a)
    with _lock:
        old = _atoms[key]
        _atoms[key] = _i64(old + _i64(n))
    return old


def atomic_sub(a, n):
    key = _atom_read(a)
    with _lock:
        old = _atoms[key]
        _atoms[key] = _i64(old - _i64(n))
    return old


def atomic_exchange(a, v):
    return atomic_store(a, v)


def atomic_cas(a, expect, new):
    key = _atom_read(a)
    with _lock:
        if _atoms[key] == _i64(expect):
            _atoms[key] = _i64(new)
            return True
        return False


# ---------------- 注册表 ----------------
def registry():
    """供 builtins.make_builtins / runtime 复用的名字 -> 函数表。"""
    return {
        "thread_spawn": thread_spawn,
        "thread_join": thread_join,
        "thread_alive": thread_alive,
        "thread_id": thread_id,
        "cpu_count": cpu_count,
        "mutex_new": mutex_new,
        "mutex_lock": mutex_lock,
        "mutex_unlock": mutex_unlock,
        "mutex_trylock": mutex_trylock,
        "cond_new": cond_new,
        "cond_wait": cond_wait,
        "cond_wait_ms": cond_wait_ms,
        "cond_notify": cond_notify,
        "cond_notify_all": cond_notify_all,
        "atomic_new": atomic_new,
        "atomic_load": atomic_load,
        "atomic_store": atomic_store,
        "atomic_add": atomic_add,
        "atomic_sub": atomic_sub,
        "atomic_exchange": atomic_exchange,
        "atomic_cas": atomic_cas,
    }


CONCURRENCY_BUILTINS = tuple(sorted(registry()))
