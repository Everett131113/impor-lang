# -*- coding: utf-8 -*-
"""
Impor (I++) 解释器。

作用域语义（v2，Python 式）：
  - 顶层赋值写入全局环境
  - 函数内赋值默认创建/写入当前函数的局部环境
  - nonlocal set x = v : 修改最近外层函数的局部变量
  - global set x = v  : 直接写全局环境
  - x = v / x += v     : 顶层=全局；函数内=局部（x += v 就近更新找到的绑定）

异常体系：
  - ImporException(name, msg, lineno) 为语言层异常
  - 内建函数抛出的 Python 异常会在进入 try 或顶层时包装成 ImporException
  - exception 声明自定义异常类型；raise 抛出；catch Type e / catch e 捕获
"""

import os
import sys
import threading
import time

from .ast_nodes import (
    Num, Str, FStr, Bool, Null, Var, Pipe, ExprStmt, AttrGet, AttrSet, BinOp, UnaryOp, TernaryExpr,
    Assign, CompoundAssign, Print, If, While, For, Def, Return, Call,
    NewInstance, SuperCall, Break, Continue, ExitScript, ClsOut,
    ListLit, DictLit, IndexGet, IndexSet, Slice, ImportNode, FromImportNode,
    ExportNode, Block, Defer, TryCatch,
    ClassDef, ExceptionDef, Raise, AssertNode, MatchNode, BindingPattern, AdtPattern, Lambda,
    Await, TypeTest,
)
from .lexer import tokenize
from .parser import Parser
from .memory import MemoryManager


# ---------------- 控制流异常 ----------------
class BreakExc(Exception):
    pass


class ContinueExc(Exception):
    pass


class ExitScriptExc(BaseException):
    pass


class ReturnExc(Exception):
    def __init__(self, value):
        super().__init__()
        self.value = value


# ---------------- 运行时对象 ----------------
class Closure:
    __slots__ = ("params", "body", "outer_env", "bound_self", "bound_cls",
                 "is_method", "is_async", "interp", "name", "tail_expr", "head_len")

    # 4.2：解释器本身不是重入的（cur_env / call_stack 是实例属性），
    # 所以 thread_spawn 会给每个线程准备一份执行副本放进这个线程局部槽；
    # Closure 调用时优先用副本，避免两个线程互相踩 cur_env。
    _thread_interp = threading.local()
    def __init__(self, params, body, outer_env, interp=None, name=None):
        self.params = params
        self.body = body
        self.outer_env = outer_env
        self.bound_self = None
        self.bound_cls = None
        self.is_method = False
        self.is_async = False
        self.interp = interp
        self.name = name
        # v6.2 性能：函数每次调用都要判断隐式返回并切 body 列表，改为创建时算好
        self.tail_expr = bool(body and isinstance(body[-1], (Call, ExprStmt)))
        self.head_len = len(body) - 1 if self.tail_expr else len(body)

    def __call__(self, *args):
        """让 Closure 可直接调用（编译产物从模块取函数后调用）。"""
        if len(args) != len(self.params):
            raise ImporException("CallError",
                                 f"函数需要 {len(self.params)} 个参数，给了 {len(args)} 个", 0)
        interp = getattr(Closure._thread_interp, "inst", None) or self.interp
        if interp is None:
            from .runtime import _get_interp
            interp = _get_interp()
        return interp._call_closure(self, list(args), None)


class Task:
    """单线程协程任务：保存闭包和参数，await / run_tasks 时按顺序执行。"""
    __slots__ = ("closure", "args", "kwargs", "done", "result", "error")
    def __init__(self, closure, args=None, kwargs=None):
        self.closure = closure
        self.args = list(args or [])
        self.kwargs = dict(kwargs or {})
        self.done = False
        self.result = None
        self.error = None

    def run(self):
        if self.done:
            if self.error is not None:
                raise self.error
            return self.result
        self.done = True
        try:
            interp = self.closure.interp
            if interp is None:
                from .runtime import _get_interp
                interp = _get_interp()
            self.result = interp._call_closure(self.closure, self.args, None, self.kwargs)
        except Exception as exc:
            self.error = exc
            raise
        return self.result

    def __repr__(self):
        return "<Task %s%s>" % (getattr(self.closure, "name", ""), " done" if self.done else "")


class ImporException(Exception):
    def __init__(self, name, msg, lineno=0, cause=None, col=0):
        self.name = name
        self.msg = msg
        self.lineno = lineno
        self.col = col          # v2.10：列号（1 起，0=未知）
        self.cause = cause      # raise ... from 携带的原始异常
        self.stack = None       # 抛出时快照的调用栈帧（Frame 列表，main→抛出点）
        super().__init__(f"{name}: {msg}")


class ImporRuntimeError(ImporException):
    """v2.10 运行时错误：解释器把 Python 底层异常包装成此类后向外抛出。

    携带脚本源码位置（lineno/col/filename），隐藏 Python 内部堆栈；
    继承 ImporException，旧的 except ImporException / except Exception 捕获保持兼容。
    cause：被包装的 Python 原始异常（仅 IMPOR_DEBUG=1 时展示）。
    """

    def __init__(self, name, msg, lineno=0, col=0, filename=None, cause=None):
        super().__init__(name, msg, lineno, cause, col)
        self.filename = filename

    def __str__(self):
        pos = f"第{self.lineno}行" + (f" 列{self.col}" if self.col else "")
        return f"{pos}: {self.name}: {self.msg}"


class Frame:
    """调用栈帧：函数名 + 局部环境 + 当前行号。"""
    __slots__ = ("name", "env", "lineno", "is_method")
    def __init__(self, name, env, lineno, is_method=False):
        self.name = name
        self.env = env
        self.lineno = lineno
        self.is_method = is_method

    def __str__(self):
        return f"{self.name} (第{self.lineno}行)"


class UserExceptionType:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"<异常类型 {self.name}>"


class ImporClass:
    def __init__(self, name, base, methods, class_fields, private=None, protected=None):
        self.name = name
        self.base = base
        self.methods = methods
        self.class_fields = class_fields
        # v7.3：显式 private/protected 成员名（不含 public）
        self.private_names = set(private or ())
        self.protected_names = set(protected or ())

    def __str__(self):
        return f"<类 {self.name}>"


class ImporInstance:
    def __init__(self, cls_obj):
        self.cls = cls_obj
        self.instance_fields = {}

    def __str__(self):
        return f"<{self.cls.name} 实例>"


class SuperProxy:
    """super() 返回的代理：方法查找沿父类链。"""
    def __init__(self, instance, base_cls):
        self.instance = instance
        self.base_cls = base_cls


class Module:
    """Impor 用户模块 / 目录包命名空间对象。

    元信息：__name__（逻辑名）、__file__（源文件路径）、__is_package__（是否目录包）。
    普通符号读写走内部 _store；元信息不可被模块代码覆盖。
    """

    def __init__(self, store=None, name="", file_path="", is_package=False, state="loaded"):
        object.__setattr__(self, "_store", dict(store) if store else {})
        object.__setattr__(self, "__name__", name)
        object.__setattr__(self, "__file__", file_path)
        object.__setattr__(self, "__is_package__", is_package)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_private", set())
        object.__setattr__(self, "_protected", set())

    def __getattr__(self, name):
        try:
            return self._store[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_") and name in ("_store", "_state", "_private", "_protected"):
            object.__setattr__(self, name, value)
        elif name in ("__name__", "__file__", "__is_package__"):
            object.__setattr__(self, name, value)
        else:
            self._store[name] = value

    def __contains__(self, name):
        return name in self._store

    def __str__(self):
        kind = "包" if self.__is_package__ else "模块"
        return f"<Impor {kind} {self.__name__}>"

    __repr__ = __str__


class Vec2:
    """2D 向量。"""
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def length(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

    def dist(self, other):
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def angle_to(self, other):
        import math
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
    """RGB 颜色。"""
    def __init__(self, r, g, b):
        self.r = max(0, min(255, int(r)))
        self.g = max(0, min(255, int(g)))
        self.b = max(0, min(255, int(b)))

    @property
    def hex(self):
        return "#%02X%02X%02X" % (self.r, self.g, self.b)

    def __eq__(self, o):
        return isinstance(o, Color) and (self.r, self.g, self.b) == (o.r, o.g, o.b)

    def __str__(self):
        return self.hex


class Env:
    def __init__(self, outer=None, store=None):
        self.store = store if store is not None else {}
        self.outer = outer
        self.consts = set()  # v2.17：只读常量名（本层作用域）

    def get(self, name):
        cur = self
        while cur is not None:
            if name in cur.store:
                return cur.store[name]
            cur = cur.outer
        raise ImporException("NameError", f"变量 {name} 未定义")

    def is_const(self, name):
        """沿作用域链查最近绑定是否为 const（最近绑定决定可见性）。"""
        cur = self
        while cur is not None:
            if name in cur.store:
                return name in cur.consts
            cur = cur.outer
        return False

    def check_mutable(self, name, lineno=0, col=0):
        """赋值/复合赋值前检查 const；const 不可修改。"""
        if self.is_const(name):
            raise ImporException("ConstError", f"常量 {name} 不可修改（const 只读）",
                                 lineno, col=col)

    def set_local(self, name, value):
        self.store[name] = value

    def set_global(self, name, value):
        cur = self
        while cur.outer is not None:
            cur = cur.outer
        cur.store[name] = value

    def set_nonlocal(self, name, value):
        cur = self.outer
        while cur is not None:
            if cur.outer is None:
                break  # 到达全局环境
            if name in cur.store:
                cur.store[name] = value
                return
            cur = cur.outer
        raise ImporException("ScopeError", f"nonlocal：找不到外层局部变量 '{name}'")

    def update_nearest(self, name, value, lineno=0, col=0):
        """更新已存在的绑定；找不到就报 NameError，绝不静默创建变量。"""
        cur = self
        while cur is not None:
            if name in cur.store:
                cur.store[name] = value
                return
            cur = cur.outer
        raise ImporException(
            "NameError",
            f"变量 {name} 未声明；首次赋值请写 `var {name} = ...`",
            lineno, col=col)

    def has(self, name):
        cur = self
        while cur is not None:
            if name in cur.store:
                return True
            cur = cur.outer
        return False


# ---------------- 类型名 ----------------
# 调试只停在语句级节点；纯子表达式不触发断点/单步（Call 也排除：
# 赋值/调用行由 Assign/Print 等语句节点承载，裸 f() 语句可用 breakpoint()）
_EXPR_NODES = frozenset((Num, Str, Bool, Var, AttrGet, BinOp, UnaryOp,
                         TernaryExpr, ListLit, DictLit, IndexGet, Slice,
                         NewInstance, SuperCall, BindingPattern, Call))


def typename(v):
    if isinstance(v, ImporInstance):
        return v.cls.name
    if isinstance(v, ImporClass):
        return "class"
    if isinstance(v, Module):
        return "module"
    if isinstance(v, Closure):
        return "func"
    if isinstance(v, Vec2):
        return "vec2"
    if isinstance(v, Color):
        return "color"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, UserExceptionType):
        return "exception"
    return type(v).__name__


def wrap_py_error(e, lineno=0, col=0, filename=None):
    """把 Python 底层异常包装为 ImporRuntimeError（隐藏内部堆栈）。

    ImporException 家族原样返回；其余 Python 异常（ZeroDivisionError/TypeError
    等）统一包装为 ImporRuntimeError，携带脚本源码位置与原始 cause。
    """
    if isinstance(e, ImporException):
        return e
    if isinstance(e, RecursionError):
        return ImporRuntimeError(
            "RecursionDeep",
            "递归过深：超出解释器栈上限（默认约 3200 层，可用 --max-depth 调整）。"
            "请改用循环/迭代实现，或减少递归深度", lineno, col, filename, cause=e)
    return ImporRuntimeError(type(e).__name__, str(e), lineno, col, filename, cause=e)


# v4.1：C 后端（c_runtime.c 的 rt_ck_add/sub/mul 与 rt_neg）用 int64 上下界判定；
# 解释器在 --int64 模式下用同一组常量与同一句文案，保证输出可逐字比对。
I64_MIN = -9223372036854775808
I64_MAX = 9223372036854775807
I64_MSG = "整数溢出：超出 64 位有符号整数范围"


# ---------------- 解释器 ----------------
class Interpreter:
    def __init__(self, log_cb=None, clear_cb=None, snapshot_cb=None,
                 sandbox_mode=False, gui_root=None, debug_mode=False,
                 debug_pause_callback=None, argv=None, filename=None,
                 fast_mode=False, int64_mode=False, max_depth=None):
        self.fast_mode = bool(fast_mode)
        self._fast_disabled = False
        # v4.1：后端对齐模式。False = Python 任意精度（默认，与 4.0.0 一致）；
        # True = 与 C 后端同一 int64 语义：加减乘与字面量越界一律报 OverflowError[E1012]。
        # 用 `impor run --int64` 打开，可在交付前把"两后端答案不一致"变成可检出的错误。
        self.int64_mode = bool(int64_mode)
        _depth_override = max_depth is not None
        if max_depth is None:
            raw = os.environ.get("IMPOR_MAX_DEPTH", "").strip()
            _depth_override = raw.isdigit()
            max_depth = int(raw) if _depth_override else 3200
        self.max_depth = int(max_depth)
        # 这两项都要精确控制语义，而 `_run_fast` 是把 AST 转成 Python 源码执行，
        # 绕过了 visit_* 里的溢出与深度闸门，因此打开任一开关时关掉转译后端的快路径。
        if self.int64_mode or _depth_override:
            self.fast_mode = False
        # 4.1：稳定性内核。None = 关闭（默认，行为与 4.0.0 一致）；打开后五道闸门生效。
        self.stab = None
        self.global_env = Env()
        self.cur_env = self.global_env
        self.log_cb = log_cb
        self.clear_cb = clear_cb
        self.snapshot_cb = snapshot_cb
        self.sandbox_mode = sandbox_mode
        self.smoke_mode = False  # --smoke：input 自动返回，避免冒烟挂起
        self.gui_root = gui_root
        self.debug_mode = debug_mode
        self.debug_pause_callback = debug_pause_callback
        self.argv = list(argv) if argv is not None else []
        self.filename = filename    # v2.10：当前执行脚本名（运行时错误定位用）
        self.assert_count = 0       # v4.2：已执行的 assert 条数（impor test 统计用）
        # 3.2.2 运行时行覆盖：默认关闭，避免常驻开销；run_code(coverage=True) 打开
        self.coverage_enabled = False
        self.coverage = {}
        self.coverage_hits = 0
        # v3.7.8：defer 帧栈。函数/模块进入时压入一个列表，退出时按 LIFO 执行。
        self._defer_frames = []

        # v3.7.1：树遍历与快速后端统一为同一层数（默认 3200，可由
        # --max-depth / IMPOR_MAX_DEPTH 覆盖），避免脚本里有没有 class 改变递归上限。
        # 每层 Impor 调用约消耗 6-7 个 Python 帧，故 Python 帧上限按深度等比放宽。
        _REC_LIMIT = max(200000, self.max_depth * 70)
        if sys.getrecursionlimit() < _REC_LIMIT:
            sys.setrecursionlimit(_REC_LIMIT)
        self._call_depth = 0

        self.module_search_paths = [os_getcwd()]
        # 模块系统（v2.1）：完整缓存 + 安全循环导入 + 包支持
        self.module_cache = {}       # 逻辑名 -> Module（state: loading/loaded/failed）
        self._loading_stack = []     # 正在加载的模块逻辑名栈（reload 保护 / 死循环诊断）
        self._cur_package = None     # 当前正在执行模块的所属包逻辑名（None=主程序）
        self._export_stack = []      # 模块执行期间的 export 名字收集栈
        self.watch_list = set()

        # v3.2：自动内存管理（阈值触发回收 + 缓存清理 + 统计）
        self.memory = MemoryManager()
        self.memory.add_cleaner(self._memory_cleanup)

        # 帧统计（供 frame()/fps()）
        self._frame_count = 0
        self._fps = 0.0
        self._fps_t0 = time.time()
        self._fps_count = 0
        # v6.1 性能：帧采样每 1024 轮一次；出现过 const 后才做可变性检查
        self._tick_sample = 0
        self._any_const = False

        # super() 上下文
        self._cur_self = None
        self._cur_cls = None

        # 调试系统（v2.1）：调用栈 / 断点 / 单步
        self.call_stack = [Frame("<main>", self.global_env, 0)]  # 栈底固定 main 帧
        self.breakpoints = set()      # 行号集合（调试时设置，body 行号）
        self.breakpoint_conditions = {}  # v2.5 条件断点：行号 -> 条件表达式字符串
        self.breakpoint_min_hits = {}    # 命中次数达到阈值才暂停
        self.breakpoint_hit_conditions = {}  # v3.7.4 DAP hitCondition：行号 -> ">=3" / "%5" / "5"
        self._bp_counts = {}
        self.step_mode = None         # None / "into" / "over" / "out"
        self._dbg_step_target = 0     # over/out 的目标调用深度
        self._dbg_last_stop = -1      # 单步已暂停过的行（抑制同行子表达式重复暂停）
        self.exception_pause_callback = None  # v3.7.4 DAP 异常断点
        self.exception_break_mode = None      # None / "uncaught" / "all"

        self._init_builtins()
        self._visit_map = {
            Num: self.visit_Num, Str: self.visit_Str, FStr: self.visit_FStr, Bool: self.visit_Bool,
            Null: self.visit_Null,
            Var: self.visit_Var, AttrGet: self.visit_AttrGet, AttrSet: self.visit_AttrSet,
            BinOp: self.visit_BinOp, Pipe: self.visit_Pipe, UnaryOp: self.visit_UnaryOp,
            TernaryExpr: self.visit_TernaryExpr, Assign: self.visit_Assign,
            CompoundAssign: self.visit_CompoundAssign, Print: self.visit_Print,
            ExprStmt: self.visit_ExprStmt,
            If: self.visit_If, While: self.visit_While, For: self.visit_For,
            Def: self.visit_Def, Return: self.visit_Return, Call: self.visit_Call,
            NewInstance: self.visit_NewInstance, SuperCall: self.visit_SuperCall,
            Break: self.visit_Break, Continue: self.visit_Continue,
            ExitScript: self.visit_ExitScript, ClsOut: self.visit_ClsOut,
            ListLit: self.visit_ListLit, DictLit: self.visit_DictLit,
            IndexGet: self.visit_IndexGet, IndexSet: self.visit_IndexSet,
            Slice: self.visit_Slice,
            ImportNode: self.visit_ImportNode, Block: self.visit_Block,
            Defer: self.visit_Defer,
            FromImportNode: self.visit_FromImportNode,
            ExportNode: self.visit_ExportNode,
            TryCatch: self.visit_TryCatch, ClassDef: self.visit_ClassDef,
            ExceptionDef: self.visit_ExceptionDef, Raise: self.visit_Raise,
            AssertNode: self.visit_AssertNode,
            MatchNode: self.visit_MatchNode, BindingPattern: self.visit_BindingPattern,
            AdtPattern: self.visit_AdtPattern,
            Lambda: self.visit_Lambda, Await: self.visit_Await,
            TypeTest: self.visit_TypeTest,
        }

    # ---------- 运行入口 ----------
    def enable_stability(self, limits=None):
        """4.1：打开稳定性内核（额度/计量/围栏）。返回 Governor，便于读统计。"""
        from .stability import enable as _enable
        gov = _enable(self, limits)
        return gov

    def run(self, node):
        # 4.1：每次顶层执行重置额度；REPL/编辑器复用解释器不会被上一条命令拖累。
        if self.stab is not None:
            self.stab.begin()
        # v3.7.8：模块级 defer 帧。退出（正常/异常/exit）时按 LIFO 执行。
        frame = []
        self._defer_frames.append(frame)
        try:
            if (self.fast_mode and not self._fast_disabled and not self.debug_mode
                    and not self.coverage_enabled and self.snapshot_cb is None
                    and self.stab is None
                    and self._fast_eligible(node)):
                try:
                    self._run_fast(node)
                    return
                except Exception:
                    # 转译后端只负责“能用就快”的路径；转译/编译阶段失败时回退树遍历。
                    # 一旦开始执行则不回退，避免重复打印或重复副作用。
                    self._fast_disabled = True
            try:
                self._exec_stmt(node)
            except (BreakExc, ContinueExc, ExitScriptExc, ReturnExc):
                pass
            except ImporException as e:
                if e.stack is None:
                    e.stack = list(self.call_stack)
                raise
            except Exception as e:
                wrapped = wrap_py_error(e, col=getattr(e, "col", 0), filename=self.filename)
                if wrapped.stack is None:
                    wrapped.stack = list(self.call_stack)
                raise wrapped
        finally:
            try:
                self._run_defer_frame(frame)
            finally:
                self._defer_frames.pop()

    # ---------- 快速执行后端（树遍历之外的可选路径） ----------
    _FAST_OK_NODES = frozenset({
        "Program", "Block", "Def", "Return", "If", "While", "For", "Break",
        "Continue", "Assign", "CompoundAssign", "Print", "ExprStmt",
        "Num", "Str", "Bool", "Null", "Var", "BinOp", "UnaryOp", "TernaryExpr",
        "Call", "ListLit", "DictLit", "IndexGet", "IndexSet", "Slice",
        "FStr", "Lambda",
    })

    def _fast_eligible(self, node):
        """只让语义与 Python 转译后端一致的简单脚本走快速路径。"""
        stack = [node]
        seen = 0
        while stack:
            cur = stack.pop()
            seen += 1
            if seen > 20000:
                return False
            if isinstance(cur, (list, tuple)):
                stack.extend(cur)
                continue
            t = type(cur)
            if t.__name__ == "Assign" and getattr(cur, "scope", "") == "const":
                # const / readonly / static 的只读语义由树遍历解释器兑现；
                # 转译快速路径不认识 const，必须回退，否则 x = 6 会静默改写常量。
                return False
            if t.__name__ in ("Assign", "CompoundAssign") and getattr(cur, "scope", "") in (
                    "update", "global", "nonlocal"):
                # 更新/跨作用域赋值必须走树遍历：Python 直译会把“未声明变量”
                # 静默创建成局部变量，破坏 v3.13 的严格声明语义。
                return False
            if t.__name__ not in self._FAST_OK_NODES:
                if t.__name__ in ("ImportNode", "FromImportNode", "ExportNode",
                                  "ClassDef", "ExceptionDef", "TryCatch", "Raise",
                                  "AssertNode", "MatchNode", "Await", "NewInstance",
                                  "AttrGet", "AttrSet", "Pipe", "TypeTest"):
                    return False
                continue
            for k, v in getattr(cur, "__dict__", {}).items():
                if isinstance(v, (list, tuple)) or hasattr(v, "__dict__"):
                    stack.append(v)
        return True

    def _run_fast(self, node):
        """把 AST 转成 Python 代码并执行（v3.7.0 性能后端）。"""
        from .transpiler import transpile
        code = transpile(node, skip_tick=True)
        ns = {"__name__": "__main__", "__file__": self.filename or "<impor>"}
        # v3.7.1：快速后端直接借用 Python 递归，必须和树遍历的 3200 层闸门对齐。
        # 否则纯递归脚本在快速后端能过 3300 层，加一个 class 回退树遍历却报 3200。
        old_limit = sys.getrecursionlimit()
        from . import runtime as _runtime
        old_smoke = _runtime._SMOKE_MODE
        _runtime._SMOKE_MODE = self.smoke_mode
        try:
            if old_limit > 3250:
                sys.setrecursionlimit(3250)
            exec(compile(code, self.filename or "<impor>", "exec"), ns)
        finally:
            _runtime._SMOKE_MODE = old_smoke
            if sys.getrecursionlimit() != old_limit:
                sys.setrecursionlimit(old_limit)
        # 让调用方仍能从 global_env 读到脚本顶层变量（REPL/工具链兼容）
        for k, v in ns.items():
            if not k.startswith("__"):
                self.global_env.store[k] = v

    def _dbg_depth(self):
        return len(self.call_stack) - 1  # 0 = 主程序

    def eval_expr_text(self, text):
        """v2.5 调试用：解析并求值一段表达式（基于当前环境），供条件断点 / 监视表达式。"""
        from .parser import Parser
        from .lexer import tokenize
        node = Parser(tokenize(text)).expr()
        return self.visit(node)

    def _hit_matches(self, count, spec):
        """DAP hitCondition：5 / %5 / >=5 / >5 / <=5 / <5 / ==5 / !=5。"""
        spec = (spec or "").strip()
        if not spec:
            return True
        try:
            if spec.startswith("%"):
                n = int(spec[1:].strip())
                return n > 0 and count % n == 0
            for op in (">=", "<=", "==", "!=", ">", "<"):
                if spec.startswith(op):
                    n = int(spec[len(op):].strip())
                    return {">=": count >= n, "<=": count <= n, "==": count == n,
                            "!=": count != n, ">": count > n, "<": count < n}[op]
            n = int(spec)
            return n > 0 and count % n == 0
        except Exception:
            return True

    def _dbg_check(self, node):
        """调试检查：断点命中或单步暂停（仅语句执行点调用，子表达式不触发）。"""
        ln = getattr(node, "lineno", 0)
        if ln <= 0:
            return
        depth = self._dbg_depth()
        hit = False
        if ln in self.breakpoints:
            self._bp_counts[ln] = self._bp_counts.get(ln, 0) + 1
            if not self._hit_matches(self._bp_counts[ln], self.breakpoint_hit_conditions.get(ln)):
                return
            minimum = self.breakpoint_min_hits.get(ln, 0)
            if minimum and self._bp_counts[ln] < minimum:
                return
            cond = self.breakpoint_conditions.get(ln)
            if cond is not None:
                try:
                    if not self.eval_expr_text(cond):
                        return
                except Exception:
                    return  # 条件求值失败/异常 → 本次不命中
            hit = True
        elif self.step_mode == "into":
            if ln != self._dbg_last_stop:
                hit = True
                self.step_mode = None
        elif self.step_mode == "over":
            if depth <= self._dbg_step_target and ln != self._dbg_last_stop:
                hit = True
                self.step_mode = None
        elif self.step_mode == "out":
            if depth <= self._dbg_step_target and ln != self._dbg_last_stop:
                hit = True
                self.step_mode = None
        if not hit:
            return
        self._dbg_last_stop = ln
        # 更新当前帧行号后暂停
        if self.call_stack:
            self.call_stack[-1].lineno = ln
        if self.debug_pause_callback:
            self.debug_pause_callback(ln, self.cur_env)

    def _exec_stmt(self, node):
        """执行一条语句（语句位置统一入口，含调试检查与行覆盖）。"""
        gov = self.stab
        if gov is not None:
            # 计数只落在语句入口：子表达式不计数，保证三后端步数可比。
            gov.on_stmt(getattr(node, "lineno", 0), getattr(node, "col", 0))
        if self.debug_mode:
            self._dbg_check(node)
        if self.coverage_enabled:
            ln = getattr(node, "lineno", 0)
            if ln > 0:
                self.coverage[ln] = self.coverage.get(ln, 0) + 1
                self.coverage_hits += 1
        func = self._visit_map[type(node)]
        try:
            return func(node)
        except ImporException as exc:
            if (self.debug_mode and self.exception_break_mode == "all"
                    and self.exception_pause_callback
                    and not getattr(exc, "_dbg_noted", False)):
                exc._dbg_noted = True
                self.exception_pause_callback(exc, getattr(node, "lineno", 0))
            raise

    def enable_coverage(self, enabled=True):
        """3.2.2：开关运行时行覆盖（只统计语句执行点，不侵入表达式）。"""
        self.coverage_enabled = bool(enabled)
        if enabled:
            self.coverage = {}
            self.coverage_hits = 0

    def coverage_snapshot(self):
        """返回 {行号: 命中次数}；只包含实际执行过的语句行。"""
        return dict(self.coverage)

    def visit(self, node):
        func = self._visit_map[type(node)]
        res = func(node)
        if self.snapshot_cb:
            self.snapshot_cb(dict(self.global_env.store), self.watch_list)
        return res

    def _tick_frame(self):
        self._frame_count += 1
        self._fps_count += 1
        self._tick_sample += 1
        if self._tick_sample & 1023:
            return
        now = time.time()
        dt = now - self._fps_t0
        if dt >= 0.5:
            self._fps = self._fps_count / dt
            self._fps_t0 = now
            self._fps_count = 0

    # ---------- 内建 ----------
    def _memory_cleanup(self):
        """自动回收回调：清掉失效的模块缓存与残留加载栈（不触碰运行中的帧）。"""
        try:
            for name, mod in list(self.module_cache.items()):
                if getattr(mod, "state", "") == "failed":
                    self.module_cache.pop(name, None)
        except Exception:
            pass
        if getattr(self, "_loading_stack", None):
            self._loading_stack.clear()

    def _init_builtins(self):
        from .builtins import make_builtins
        for k, v in make_builtins(self).items():
            self.global_env.set_local(k, v)

    def collect_globals(self):
        return dict(self.global_env.store)

    # ---------- 字面量 ----------
    def visit_Num(self, n):
        # v4.1：--int64 模式下，字面量本身越界也要报错（C 后端同样编译期/运行期拒绝）。
        if self.int64_mode:
            return self._i64(n.val, n)
        return n.val

    def _i64(self, v, n, col=None):
        """int64 对齐模式的越界拦截：非 int 原样返回，越界抛 OverflowError[E1012]。"""
        if type(v) is int and (v < I64_MIN or v > I64_MAX):
            raise ImporException("OverflowError", I64_MSG, n.lineno,
                                 col=(col if col is not None else getattr(n, "col", 0)))
        return v

    def visit_Str(self, n):
        return n.val

    def visit_FStr(self, n):
        # v2.9 字符串插值："x = {expr}" → 逐段拼接
        from .lexer import unescape_impor
        out = []
        ei = 0
        for kind, payload in n.parts:
            if kind == "text":
                out.append(unescape_impor(payload))
            else:
                val = self.visit(n.exprs[ei])
                ei += 1
                out.append(str(val))
        return "".join(out)

    def visit_Bool(self, n):
        return n.val

    def visit_Null(self, n):
        return None

    # ---------- 变量与属性 ----------
    def visit_Var(self, n):
        try:
            store = self.cur_env.store
            name = n.name
            if name in store:
                return store[name]
            return self.cur_env.get(name)
        except ImporException as e:
            if not e.lineno:
                e.lineno, e.col, e.filename = n.lineno, n.col, self.filename
            raise

    def _member_owner(self, cls_obj, attr):
        """返回首个声明该成员的类（沿继承链），找不到返回 None。"""
        cur = cls_obj
        while cur is not None:
            if attr in cur.methods or attr in cur.class_fields or \
               attr in cur.private_names or attr in cur.protected_names:
                return cur
            cur = cur.base
        return None

    def _is_subclass(self, cls_obj, base):
        cur = cls_obj
        while cur is not None:
            if cur is base:
                return True
            cur = cur.base
        return False

    def _check_access(self, cls_obj, attr, ln, col):
        """v7.3 访问控制：private 仅类内；protected 类内及子类；public 不限制。"""
        owner = self._member_owner(cls_obj, attr)
        if owner is None:
            return
        if attr in owner.private_names:
            if self._cur_cls is not owner:
                raise ImporException(
                    "AccessError",
                    f"{attr} 是 {owner.name} 的 private 成员，只能在类内部访问",
                    ln, col=col)
        elif attr in owner.protected_names:
            if self._cur_cls is None or not self._is_subclass(self._cur_cls, owner):
                raise ImporException(
                    "AccessError",
                    f"{attr} 是 {owner.name} 的 protected 成员，只能在本类或子类中访问",
                    ln, col=col)

    def _bind_method(self, cls_obj, inst, name, lineno):
        """沿类链查找方法并绑定 self。"""
        cur = cls_obj
        while cur is not None:
            if name in cur.methods:
                mc = cur.methods[name]
                be = Env(outer=mc.outer_env)
                be.set_local("self", inst)
                clo = Closure(mc.params, mc.body, be)
                clo.bound_self = inst
                clo.bound_cls = cur
                clo.is_method = True
                return clo
            cur = cur.base
        return None

    def visit_TypeTest(self, n):
        """4.9.1：`x have int`。

        判定口径就是运行期真实的类型标签（`typename()`），不做隐式转换：
        1 have float 是 False，True have int 也是 False（bool 是独立类型）。
        右侧除内建类型名外，也接受任意类名，用来判"是不是这个类的实例"。
        """
        v = self.visit(n.expr)
        want = n.type_name
        if want in ("int", "float", "str", "bool", "list", "dict",
                    "class", "module", "func", "exception"):
            return typename(v) == want
        if want in ("object", "instance"):
            return isinstance(v, ImporInstance)
        if isinstance(v, ImporInstance):
            cls = v.cls
            while cls is not None:
                if getattr(cls, "name", None) == want:
                    return True
                cls = getattr(cls, "base_cls", None) or getattr(cls, "base", None)
            return False
        raise ImporException("TypeError", "have 的类型名未知：%s" % want,
                             n.lineno, col=n.col)

    def visit_AttrGet(self, n):
        obj = self.visit(n.obj_expr)
        attr = n.attr_name

        if isinstance(obj, ImporInstance):
            self._check_access(obj.cls, attr, n.lineno, n.col)
            if attr in obj.instance_fields:
                return obj.instance_fields[attr]
            bound = self._bind_method(obj.cls, obj, attr, n.lineno)
            if bound is not None:
                return bound
            raise ImporException("AttributeError", f"实例无属性 {attr}", n.lineno, col=n.col)

        if isinstance(obj, SuperProxy):
            if obj.base_cls is not None:
                self._check_access(obj.base_cls, attr, n.lineno, n.col)
                bound = self._bind_method(obj.base_cls, obj.instance, attr, n.lineno)
                if bound is not None:
                    return bound
            if attr in obj.instance.instance_fields:
                self._check_access(obj.instance.cls, attr, n.lineno, n.col)
                return obj.instance.instance_fields[attr]
            raise ImporException("AttributeError", f"父类无属性 {attr}", n.lineno, col=n.col)

        if isinstance(obj, ImporClass):
            self._check_access(obj, attr, n.lineno, n.col)
            if attr in obj.methods:
                return obj.methods[attr]
            if attr in obj.class_fields:
                return obj.class_fields[attr]
            raise ImporException("AttributeError", f"类无属性 {attr}", n.lineno, col=n.col)

        # v4.2：字典方法统一返回数组（与 C 后端一致，避免 dict_keys 之类内部类型外泄）
        if isinstance(obj, dict):
            if attr == "keys":
                return lambda: list(obj.keys())
            if attr == "values":
                return lambda: list(obj.values())
            if attr == "items":
                return lambda: [[k, v] for k, v in obj.items()]
            if attr == "get":
                return lambda k, d=None: obj.get(k, d)
            if attr in ("has", "contains"):
                return lambda k: k in obj

        # Python 对象 / 模块
        if isinstance(obj, Module) and attr in obj._private:
            if os.path.abspath(obj.__file__ or "") != os.path.abspath(self.filename or ""):
                raise ImporException(
                    "AccessError",
                    f"{attr} 是模块 {obj.__name__} 的 private 符号，不能跨文件访问",
                    n.lineno, col=n.col)
        try:
            return getattr(obj, attr)
        except AttributeError:
            raise ImporException("AttributeError", f"对象无属性 {attr}", n.lineno, col=n.col)

    def visit_AttrSet(self, n):
        obj = self.visit(n.obj_expr)
        attr = n.attr_name
        val = self.visit(n.value)

        if isinstance(obj, ImporInstance):
            self._check_access(obj.cls, attr, n.lineno, n.col)
            obj.instance_fields[attr] = val
            return val
        if isinstance(obj, SuperProxy):
            if obj.base_cls is not None:
                self._check_access(obj.base_cls, attr, n.lineno, n.col)
            obj.instance.instance_fields[attr] = val
            return val
        if obj is None or isinstance(obj, (int, float, str, bool, list, tuple, dict)):
            raise ImporException("AttributeError",
                                 f"不能给 {typename(obj)} 类型的值设置属性 {attr}", n.lineno, col=n.col)
        try:
            setattr(obj, attr, val)
            return val
        except AttributeError:
            raise ImporException("AttributeError", f"对象无属性 {attr}", n.lineno, col=n.col)

    # ---------- 运算符 ----------
    def visit_TernaryExpr(self, n):
        return self.visit(n.true_expr) if self.visit(n.cond) else self.visit(n.false_expr)

    def visit_Pipe(self, n):
        # v2.9 管道：x |> f(1) == f(x, 1)；x |> f == f(x)
        rhs = n.right
        if isinstance(rhs, Call):
            new_call = Call(rhs.callee, [n.left] + list(rhs.args), rhs.lineno)
            return self.visit(new_call)
        f = self.visit(rhs)
        return f(self.visit(n.left))

    def _stab_repeat(self, l, r, lineno, col):
        """4.1：字符串/数组重复在分配前预检（"x" * 10**8 是典型的瞬间爆内存）。"""
        if self.stab is not None and isinstance(l, (str, list, tuple)) and type(r) is int:
            size = len(l) * max(0, r)
            self.stab.check_alloc(size, "重复拼接的字符串/数组", bytes_each=4,
                                  lineno=lineno, col=col)

    def visit_BinOp(self, n):
        op = n.op
        # 4.4：`and` / `or` 补上短路求值。此前 parser 已把 `&&`/`||` 归一成 `and`/`or`
        # （见 parser.and_expr / or_expr），但树遍历路径没有对应分支，直接报
        # “未知运算符 and/or”；IR 参考求值器的对拍把它暴露出来（tests/test_ir.py）。
        # 短路语义必须在这里做，不能先求右值：`x != 0 and 1 / x > 1` 靠的就是这个顺序。
        if op in ("and", "or"):
            left = self.visit(n.left)
            if op == "and":
                return self.visit(n.right) if left else left
            return left if left else self.visit(n.right)
        # v2.9 性能：双数字字面量快速路径，跳过两次 visit 查表
        # v4.1：这条路径同样要过 int64 闸门，否则 `print(MAX + 1)` 会绕过溢出检查。
        l, r = n.left, n.right
        if type(l) is Num and type(r) is Num:
            a, b = l.val, r.val
            _i64 = self.int64_mode
            try:
                if op == "+":
                    v = a + b
                    return self._i64(v, n) if _i64 else v
                if op == "-":
                    v = a - b
                    return self._i64(v, n) if _i64 else v
                if op == "*":
                    v = a * b
                    return self._i64(v, n) if _i64 else v
                if op == "/":
                    return a / b
                if op == "%":
                    return a % b
                if op == ">":
                    return a > b
                if op == "<":
                    return a < b
                if op == ">=":
                    return a >= b
                if op == "<=":
                    return a <= b
                if op == "==":
                    return a == b
                if op == "!=":
                    return a != b
            except Exception as e:
                raise wrap_py_error(e, n.lineno, n.col, self.filename)
            raise ImporException("OpError", f"未知运算符 {op}", n.lineno, col=n.col)
        l = self.visit(n.left)
        r = self.visit(n.right)
        # v6.1 性能：整数热路径，跳过通用类型分派
        # v4.1：int64 对齐模式只多一次属性读取与两次比较，关闭时等价于原热路径。
        _i64 = self.int64_mode
        if type(l) is int and type(r) is int:
            if op == "+":
                v = l + r
                return self._i64(v, n) if _i64 else v
            if op == "-":
                v = l - r
                return self._i64(v, n) if _i64 else v
            if op == "<":
                return l < r
            if op == "*":
                v = l * r
                return self._i64(v, n) if _i64 else v
            if op == "/":
                if r == 0:
                    raise ImporException("ZeroDivisionError", "division by zero",
                                         n.lineno, col=n.col)
                return l / r
            if op == "%":
                if r == 0:
                    raise ImporException("ZeroDivisionError", "modulo by zero",
                                         n.lineno, col=n.col)
                return l % r
            if op == ">":
                return l > r
            if op == ">=":
                return l >= r
            if op == "<=":
                return l <= r
            if op == "==":
                return l == r
            if op == "!=":
                return l != r
        try:
            if op == "+":
                # v3.3：字符串与数字不能隐式拼接（与 C 后端语义一致）
                if isinstance(l, str) != isinstance(r, str):
                    raise ImporException(
                        "TypeError",
                        "不能将字符串与数字相加（请用 to_str() 显式转换）",
                        n.lineno, col=n.col)
                return l + r
            if op == "-":
                return l - r
            if op == "*":
                self._stab_repeat(l, r, n.lineno, n.col)
                return l * r
            if op == "/":
                return l / r
            if op == "%":
                return l % r
            if op == ">":
                return l > r
            if op == "<":
                return l < r
            if op == ">=":
                return l >= r
            if op == "<=":
                return l <= r
            if op == "==":
                return l == r
            if op == "!=":
                return l != r
            if op == "and":
                return l and r
            if op == "or":
                return l or r
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)
        raise ImporException("OpError", f"未知运算符 {op}", n.lineno, col=n.col)

    def visit_UnaryOp(self, n):
        c = self.visit(n.child)
        try:
            if n.op in ("not", "!"):
                return not c
            if n.op == "-":
                # v4.1：-LLONG_MIN 在 C 侧报错，解释器 --int64 模式下同样拦截。
                return self._i64(-c, n) if self.int64_mode else -c
            if n.op == "+":
                return c
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)
        raise ImporException("OpError", f"未知单目运算符 {n.op}", n.lineno, col=n.col)

    # ---------- 赋值 ----------
    def visit_Assign(self, n):
        v = self.visit(n.expr)
        if n.scope == 'const':
            # v2.17 const 只读常量：重复声明报错
            if self.cur_env.has(n.name):
                raise ImporException("ConstError",
                                     f"常量 {n.name} 重复声明（const 只能声明一次）",
                                     n.lineno, col=n.col)
            self.cur_env.set_local(n.name, v)
            self.cur_env.consts.add(n.name)
            self._any_const = True
            return v
        if self._any_const:
            self.cur_env.check_mutable(n.name, n.lineno, n.col)
        if n.scope == 'global':
            self.cur_env.set_global(n.name, v)
        elif n.scope == 'nonlocal':
            self.cur_env.set_nonlocal(n.name, v)
        elif n.scope == 'update':
            # v2.10：set 就近更新——沿作用域链更新已存在绑定；无则写入当前环境
            self.cur_env.update_nearest(n.name, v, n.lineno, n.col)
        else:
            self.cur_env.set_local(n.name, v)
        return v

    def visit_CompoundAssign(self, n):
        try:
            old = self.cur_env.get(n.name)
        except ImporException as e:
            if not e.lineno:
                e.lineno, e.col, e.filename = n.lineno, n.col, self.filename
            raise
        if self._any_const:
            self.cur_env.check_mutable(n.name, n.lineno, n.col)
        nv = self.visit(n.expr)
        if n.op == "+=":
            res = old + nv
        elif n.op == "-=":
            res = old - nv
        elif n.op == "*=":
            res = old * nv
        elif n.op == "/=":
            res = old / nv
        elif n.op == "%=":
            res = old % nv
        else:
            raise ImporException("OpError", f"不支持 {n.op}", n.lineno, col=n.col)
        if self.int64_mode:
            res = self._i64(res, n)
        if n.scope == 'global':
            self.cur_env.set_global(n.name, res)
        elif n.scope == 'nonlocal':
            self.cur_env.set_nonlocal(n.name, res)
        else:
            self.cur_env.update_nearest(n.name, res, n.lineno, n.col)
        return res

    # ---------- 语句 ----------
    def visit_ExprStmt(self, n):
        # v2.9 裸表达式语句：求值并丢弃结果（函数末行由 _call_closure 拦截为隐式返回）
        return self.visit(n.expr)

    def visit_Print(self, n):
        parts = [str(self.visit(e)) for e in n.items]
        text = " ".join(parts)
        if self.log_cb:
            self.log_cb(text)
        return text

    def visit_ExitScript(self, n):
        raise ExitScriptExc()

    def visit_ClsOut(self, n):
        if self.clear_cb:
            self.clear_cb()

    def visit_Break(self, n):
        raise BreakExc()

    def visit_Continue(self, n):
        raise ContinueExc()

    def visit_If(self, n):
        if self.visit(n.cond):
            for s in n.body:
                self._exec_stmt(s)
            return
        for cond, bd in n.elseifs:
            if self.visit(cond):
                for s in bd:
                    self._exec_stmt(s)
                return
        for s in n.elseb:
            self._exec_stmt(s)

    _FAST_BIN = {
        "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
        "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "!=": "!=",
    }

    def _fast_expr_src(self, node):
        """v6.1：把纯变量/数字/比较表达式编译成 Python 源码；不支持返回 None。"""
        tp = type(node)
        if tp is Num:
            return repr(node.val)
        if tp is Var:
            return "store[%r]" % node.name
        if tp is Bool:
            return "True" if node.val else "False"
        if tp is BinOp:
            op = self._FAST_BIN.get(node.op)
            if op is None:
                return None
            l = self._fast_expr_src(node.left)
            r = self._fast_expr_src(node.right)
            if l is None or r is None:
                return None
            return "(%s %s %s)" % (l, op, r)
        return None

    def _fast_names(self, node, out):
        tp = type(node)
        if tp is Var:
            out.add(node.name)
        elif tp is BinOp:
            self._fast_names(node.left, out)
            self._fast_names(node.right, out)

    def _fast_while(self, n):
        """v6.1：while + 纯赋值体的专用执行计划，只在本层变量上生效。"""
        plan = getattr(n, "_fast_plan", False)
        if plan is not False:
            return plan
        cond = self._fast_expr_src(n.cond)
        if cond is None:
            n._fast_plan = None
            return None
        names = set()
        self._fast_names(n.cond, names)
        lines = []
        for st in n.body:
            if type(st) is not Assign or st.scope not in (None, "", "local"):
                n._fast_plan = None
                return None
            e = self._fast_expr_src(st.expr)
            if e is None:
                n._fast_plan = None
                return None
            self._fast_names(st.expr, names)
            lines.append("        store[%r] = %s" % (st.name, e))
        if not lines:
            n._fast_plan = None
            return None
        src = "def _run(store, tick):\n    while %s:\n        tick()\n%s\n" % (cond, "\n".join(lines))
        ns = {}
        try:
            exec(src, {}, ns)
        except Exception:
            n._fast_plan = None
            return None
        n._fast_plan = ns["_run"]
        n._fast_names = names
        n._fast_assigned = [st.name for st in n.body]
        return n._fast_plan

    def visit_While(self, n):
        # 4.1：稳定模式必须禁用「快速循环计划」，否则一整段 while 在一个 Python 帧里
        # 跑完几十万条 Impor 语句，计量器完全看不到（有闸门但闸门失效）。
        plan = self._fast_while(n) if (not self.debug_mode and self.stab is None) else None
        if plan is not None:
            store = self.cur_env.store
            if all(name in store for name in n._fast_names) and \
                    not any(self.cur_env.is_const(x) for x in n._fast_assigned):
                try:
                    plan(store, self._tick_frame)
                except ImporException:
                    raise
                except Exception as e:
                    raise wrap_py_error(e, n.lineno, n.col, self.filename)
                return
        cond = n.cond
        body = n.body
        visit = self.visit
        exec_stmt = self._exec_stmt
        tick = self._tick_frame
        while visit(cond):
            tick()
            try:
                for s in body:
                    exec_stmt(s)
            except BreakExc:
                break
            except ContinueExc:
                continue

    def visit_For(self, n):
        itv = self.visit(n.iter_expr)
        try:
            items = list(itv)
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)
        for item in items:
            self._tick_frame()
            self.cur_env.set_local(n.var, item)
            try:
                for s in n.body:
                    self._exec_stmt(s)
            except BreakExc:
                break
            except ContinueExc:
                continue

    def visit_Lambda(self, n):
        body = [ExprStmt(n.expr, getattr(n, "lineno", 0), getattr(n, "col", 0))]
        clo = Closure(n.params, body, self.cur_env, interp=self, name="<lambda>")
        clo.is_async = False
        return clo

    def visit_Def(self, n):
        clo = Closure(n.params, n.body, self.cur_env, interp=self, name=n.name)
        clo.is_async = bool(getattr(n, "is_async", False))
        self.cur_env.set_local(n.name, clo)
        self.memory.alloc("func", 96)

    def visit_Await(self, n):
        value = self.visit(n.expr)
        if isinstance(value, Task):
            return value.run()
        return value

    def visit_Return(self, n):
        raise ReturnExc(self.visit(n.expr) if n.expr is not None else None)

    def _call_closure(self, f, args, n, kwargs=None):
        if self._call_depth >= self.max_depth:
            raise wrap_py_error(
                RecursionError("Impor 调用深度超过 %d 层（--max-depth）" % self.max_depth),
                getattr(n, "lineno", 0), getattr(n, "col", 0),
                self.filename)
        self._call_depth += 1
        old_env = self.cur_env
        old_self, old_cls = self._cur_self, self._cur_cls
        le = Env(outer=f.outer_env)
        self.cur_env = le
        if f.is_method and f.bound_self is not None:
            self._cur_self = f.bound_self
            self._cur_cls = f.bound_cls
        for p, a in zip(f.params, args):
            le.store[p] = a
        for k, v in (kwargs or {}).items():
            le.store[str(k)] = v
        frame = Frame(f.name or "<匿名函数>", le, getattr(n, "lineno", 0), f.is_method)
        self.call_stack.append(frame)
        defer_frame = []
        self._defer_frames.append(defer_frame)
        try:
            body = f.body
            for i in range(f.head_len):
                self._exec_stmt(body[i])
            if f.tail_expr:
                last = body[-1]
                return self.visit(last.expr if isinstance(last, ExprStmt) else last)
            return None
        except ReturnExc as re:
            return re.value
        except ImporException as exc:
            # 异常向上传播时收集当前帧；成功路径不再调用 sys.exc_info
            if getattr(exc, "_stack_kind", None) != "snapshot":
                if exc.stack is None:
                    exc.stack = [frame]
                    exc._stack_kind = "collected"
                else:
                    exc.stack.insert(0, frame)
            raise
        finally:
            try:
                self._run_defer_frame(defer_frame)
            finally:
                self._defer_frames.pop()
                self.call_stack.pop()
                self._call_depth -= 1
                self.cur_env = old_env
                self._cur_self, self._cur_cls = old_self, old_cls

    def visit_Call(self, n):
        f = self.visit(n.callee)
        args = [self.visit(a) for a in n.args]

        if isinstance(f, Closure):
            if len(args) != len(f.params):
                raise ImporException("CallError",
                                     f"函数需要 {len(f.params)} 个参数，给了 {len(args)} 个", n.lineno, col=n.col)
            # 3.2.1：async 调用返回已完成 Task（单线程即时执行）；await 解包，
            # run_tasks 顺序收集。这样解释器与 C 后端（同步语义）状态一致。
            if getattr(f, "is_async", False):
                task = Task(f, args)
                task.run()
                return task
            return self._call_closure(f, args, n)

        if callable(f):
            try:
                return f(*args)
            except ImporException as e:
                # 内建里抛出的错误没有行号（如分配闸门），在调用点补上，保证四段式诊断有位置
                if not e.lineno:
                    e.lineno, e.col = n.lineno, (e.col or n.col)
                raise
            except Exception as e:
                raise wrap_py_error(e, n.lineno, n.col, self.filename)

        raise ImporException("CallError", "不是可调用对象", n.lineno, col=n.col)

    def visit_NewInstance(self, n):
        try:
            cls = self.cur_env.get(n.cls_name)
        except ImporException as e:
            if not e.lineno:
                e.lineno, e.col, e.filename = n.lineno, n.col, self.filename
            raise
        if not isinstance(cls, ImporClass):
            raise ImporException("TypeError", f"{n.cls_name} 不是类", n.lineno, col=n.col)
        inst = ImporInstance(cls)
        self.memory.alloc("instance", 128)
        # 类字段沿继承链累积（基类优先）
        chain = []
        cur = cls
        while cur is not None:
            chain.append(cur)
            cur = cur.base
        for c in reversed(chain):
            for k, v in c.class_fields.items():
                inst.instance_fields[k] = v
        args = [self.visit(a) for a in n.args]
        # 构造函数沿继承链查找
        init = None
        init_cls = None
        cur = cls
        while cur is not None:
            if "__init__" in cur.methods:
                init = cur.methods["__init__"]
                init_cls = cur
                break
            cur = cur.base
        if init is not None:
            old_env = self.cur_env
            old_self, old_cls = self._cur_self, self._cur_cls
            le = Env(outer=init.outer_env)
            le.set_local("self", inst)
            self.cur_env = le
            self._cur_self, self._cur_cls = inst, init_cls
            if len(args) != len(init.params):
                self.cur_env = old_env
                self._cur_self, self._cur_cls = old_self, old_cls
                raise ImporException("CallError",
                                     f"构造函数需要 {len(init.params)} 个参数，给了 {len(args)} 个", n.lineno, col=n.col)
            for p, a in zip(init.params, args):
                le.set_local(p, a)
            try:
                for s in init.body:
                    self._exec_stmt(s)
            except ReturnExc:
                pass
            finally:
                self.cur_env = old_env
                self._cur_self, self._cur_cls = old_self, old_cls
        return inst

    def visit_SuperCall(self, n):
        if self._cur_self is None or self._cur_cls is None:
            raise ImporException("ScopeError", "super() 只能在类方法中使用", n.lineno, col=n.col)
        return SuperProxy(self._cur_self, self._cur_cls.base)

    # ---------- 容器 ----------
    def visit_ListLit(self, n):
        items = [self.visit(i) for i in n.items]
        self.memory.alloc("list", 64 + 8 * len(items))
        return items

    def visit_DictLit(self, n):
        d = {self.visit(k): self.visit(v) for k, v in n.kv}
        self.memory.alloc("dict", 96 + 16 * len(d))
        return d

    def visit_IndexGet(self, n):
        c = self.visit(n.container)
        i = self.visit(n.idx)
        try:
            return c[i]
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)

    def visit_Slice(self, n):
        c = self.visit(n.container)
        start = self.visit(n.start) if n.start is not None else None
        end = self.visit(n.end) if n.end is not None else None
        step = self.visit(n.step) if n.step is not None else None
        try:
            return c[start:end:step]
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)

    def visit_IndexSet(self, n):
        c = self.visit(n.container)
        i = self.visit(n.idx)
        v = self.visit(n.val)
        try:
            c[i] = v
        except Exception as e:
            raise wrap_py_error(e, n.lineno, n.col, self.filename)
        return v

    # ---------- 模块系统（v2.1） ----------
    def _find_user_module_path(self, logical_name):
        """按逻辑名在搜索路径中定位 .impor 模块/包；返回 (路径, 是否包) 或 None。

        v2.12：逻辑名以 std. 开头时优先在内置标准库目录 impor/std 下查找
        （标准库随 Impor 分发，任何项目可用）。"""
        import os
        parts = logical_name.split(".")
        if logical_name == "std" or logical_name.startswith("std."):
            std_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")
            sub = parts[1:] if len(parts) > 1 else []
            for sp in [std_dir] + self.module_search_paths:
                b2 = os.path.join(sp, *sub)
                pkg2 = os.path.join(b2, "__pkg__.impor")
                if os.path.isfile(pkg2):
                    return pkg2, True
                if os.path.isfile(b2 + ".impor"):
                    return b2 + ".impor", False
            return None
        for sp in self.module_search_paths:
            base = os.path.join(sp, *parts)
            pkg_path = os.path.join(base, "__pkg__.impor")
            if os.path.isfile(pkg_path):
                return pkg_path, True
            if os.path.isfile(base + ".impor"):
                return base + ".impor", False
        return None

    def _resolve_logical_name(self, mod_name, rel_level, lineno):
        """把（可能相对）的导入目标解析为完整逻辑名。"""
        if rel_level == 0:
            return mod_name
        if self._cur_package is None:
            raise ImporException("ImportError",
                                 "相对导入（from . / from ..）仅允许在包内使用", lineno)
        pkg_parts = self._cur_package.split(".")
        if len(pkg_parts) < rel_level - 1:
            raise ImporException("ImportError",
                                 f"相对导入超出包层级（{'.' * rel_level}，当前包 {self._cur_package}）", lineno)
        parts = pkg_parts[:len(pkg_parts) - (rel_level - 1)]
        if mod_name:
            parts.extend(mod_name.split("."))
        return ".".join(parts)

    def _exec_module_body(self, mod, full_path, lineno):
        """执行模块源码填充 mod._store，并按 export 规则过滤对外符号。"""
        with open(full_path, "r", encoding="utf-8") as f:
            src = f.read()
        body = strip_header(src)
        ast_mod = Parser(tokenize(body)).program()
        # 3.2.1：跨文件访问控制——模块顶层 private/protected 只在本模块内可见。
        private_names, protected_names = set(), set()
        for st in getattr(ast_mod, "stmts", []) or []:
            vis = getattr(st, "visibility", "")
            nm = getattr(st, "name", None)
            if nm and vis == "private":
                private_names.add(nm)
            elif nm and vis == "protected":
                protected_names.add(nm)
        mod._private = private_names
        mod._protected = protected_names
        # v2.12：模块执行用独立 store（保留全部内部符号），对外可见性
        # 由 mod._store 单独控制——私有辅助函数可被模块内导出函数调用，
        # 外部只能看到 export 的符号（无 export 时维持旧行为全量导出）
        env = Env(outer=self.global_env, store={})
        self._export_stack.append(set())
        old_cur = self.cur_env
        old_pkg = self._cur_package
        old_fn = self.filename
        # 包（__pkg__.impor）的所属包是它自己；子模块的所属包去掉最后一段
        if mod.__is_package__:
            self._cur_package = mod.__name__
        elif "." in mod.__name__:
            self._cur_package = mod.__name__.rsplit(".", 1)[0]
        else:
            self._cur_package = None
        try:
            self.cur_env = env
            # v2.12：模块内部运行时错误定位到模块文件（不再误渲染到主程序行）
            self.filename = full_path
            self.run(ast_mod)
            exports = self._export_stack[-1]
            if exports:
                missing = [nm for nm in exports if nm not in env.store]
                if missing:
                    raise ImporException(
                        "ExportError",
                        f"export 了不存在的名字: {', '.join(missing)}", lineno)
                mod._store.clear()
                for nm in exports:
                    mod._store[nm] = env.store[nm]
            else:
                # 无 export：维持旧行为，全部顶层符号默认对外导出
                mod._store.clear()
                mod._store.update(env.store)
            mod._state = "loaded"
        finally:
            self.cur_env = old_cur
            self._cur_package = old_pkg
            self.filename = old_fn
            self._export_stack.pop()

    def _load_module(self, mod_name, rel_level=0, lineno=0):
        """加载用户 .impor 模块/目录包（带完整缓存与安全循环导入）。

        循环导入：加载前预置 loading 态缓存，循环回导时返回部分初始化的
        模块对象（参考 Python sys.modules 语义），不会死循环。
        """
        logical = self._resolve_logical_name(mod_name, rel_level, lineno)
        cached = self.module_cache.get(logical)
        if cached is not None and cached._state != "failed":
            return cached  # loading（安全循环导入）或 loaded 都直接复用

        found = self._find_user_module_path(logical)
        if found is None:
            raise ImporException("ModuleNotFound", f"模块 {logical} 未找到", lineno)
        full_path, is_pkg = found

        mod = Module(name=logical, file_path=full_path,
                     is_package=is_pkg, state="loading")
        self.module_cache[logical] = mod
        self._loading_stack.append(logical)
        try:
            self._exec_module_body(mod, full_path, lineno)
            # 子模块挂到父包（Python 语义：import a.b 后 a.b 可访问）
            if "." in logical:
                parent_name, leaf = logical.rsplit(".", 1)
                parent = self.module_cache.get(parent_name)
                if isinstance(parent, Module):
                    parent._store[leaf] = mod
        except ImporException:
            mod._state = "failed"
            self.module_cache.pop(logical, None)  # 失败移出缓存，可再次尝试
            raise
        finally:
            self._loading_stack.pop()
        return mod

    def reload_module(self, mod_name):
        """重新加载已加载的模块（原地刷新：既有引用自动看到新内容）。"""
        mod = self.module_cache.get(mod_name)
        if mod is None:
            raise ImporException("ModuleNotFound", f"模块 {mod_name} 未加载", 0)
        if mod._state == "loading":
            raise ImporException("ImportCycleError",
                                 f"模块 {mod_name} 正在加载中，无法 reload", 0)
        found = self._find_user_module_path(mod_name)
        if found is None:
            raise ImporException("ModuleNotFound", f"模块 {mod_name} 未找到", 0)
        full_path, is_pkg = found
        mod._store.clear()
        object.__setattr__(mod, "__file__", full_path)
        object.__setattr__(mod, "__is_package__", is_pkg)
        self._loading_stack.append(mod_name)
        try:
            self._exec_module_body(mod, full_path, 0)
        except ImporException:
            mod._state = "failed"
            raise
        finally:
            self._loading_stack.pop()
        return mod

    def list_modules(self):
        """列出搜索路径下所有可发现的 .impor 模块与包（逻辑名）。"""
        import os
        found = []
        for sp in self.module_search_paths:
            if not os.path.isdir(sp):
                continue
            for root, dirs, files in os.walk(sp):
                dirs[:] = [d for d in dirs if not d.startswith(("__pycache__", "."))]
                rel = os.path.relpath(root, sp)
                for fn in files:
                    if fn == "__pkg__.impor":
                        name = rel.replace(os.sep, ".")
                        found.append(name)
                    elif fn.endswith(".impor") and not fn.startswith("__"):
                        name = fn[:-6]
                        if rel != ".":
                            name = rel.replace(os.sep, ".") + "." + name
                        found.append(name)
        return sorted(set(found))

    def visit_ExportNode(self, n):
        if not self._export_stack:
            raise ImporException("ImportError",
                                 "export 只能在模块文件中使用", n.lineno, col=n.col)
        self._export_stack[-1].update(n.names)

    def visit_ImportNode(self, n):
        import importlib
        name = n.modname
        target = n.alias or name
        if name == "imporai":
            # AI 数值包位于 impor.imporai，暴露为顶层名 imporai
            try:
                mod = importlib.import_module("impor.imporai")
            except Exception as e:
                raise ImporException("ImportError", f"导入 imporai 失败: {e}",
                                     n.lineno, col=n.col)
            self.cur_env.set_local(target, mod)
            return mod
        if n.is_python:
            # 旧式 important.xxx → 宿主 Python 模块
            try:
                mod = importlib.import_module(name)
            except Exception as e:
                raise ImporException("ImportError", f"导入模块 {name} 失败: {e}", n.lineno, col=n.col)
            self.cur_env.set_local(target, mod)
            return mod
        # 新解析顺序：① 用户 .impor 模块/包优先；② 回退宿主 Python 模块
        if self._find_user_module_path(name):
            if "." in name and not n.alias:
                # import pkg.sub：先加载各级父包，再加载目标，绑定顶层名
                parts = name.split(".")
                for i in range(1, len(parts)):
                    pn = ".".join(parts[:i])
                    if pn not in self.module_cache:
                        try:
                            self._load_module(pn, 0, n.lineno)
                        except ImporException:
                            break
                md = self._load_module(name, 0, n.lineno)
                self.cur_env.set_local(parts[0],
                                       self.module_cache.get(parts[0], md))
                return md
            md = self._load_module(name, 0, n.lineno)
            self.cur_env.set_local(target, md)
            return md
        try:
            mod = importlib.import_module(name)
        except ImportError:
            raise ImporException(
                "ModuleNotFound",
                f"模块 {name} 未找到（既无 .impor 模块/包，也不是可导入的 Python 模块）", n.lineno, col=n.col)
        except Exception as e:
            raise ImporException("ImportError", f"导入模块 {name} 失败: {e}", n.lineno, col=n.col)
        self.cur_env.set_local(target, mod)
        return mod

    def visit_FromImportNode(self, n):
        if n.modname == "imporai":
            import importlib
            target_mod = importlib.import_module("impor.imporai")
        else:
            target_mod = self._load_module(n.modname, n.rel_level, n.lineno)
        if n.star:
            # from 模块 import * ：导入该模块所有对外符号
            if isinstance(target_mod, Module):
                items = list(target_mod._store.items())
            else:
                items = [(k, getattr(target_mod, k)) for k in dir(target_mod)
                         if not k.startswith("_")]
            for k, v in items:
                if k.startswith("__") and k.endswith("__"):
                    continue
                self.cur_env.set_local(k, v)
            return target_mod
        for orig, alias in n.names:
            if isinstance(target_mod, Module):
                if orig in getattr(target_mod, "_private", ()) and \
                        os.path.abspath(target_mod.__file__ or "") != os.path.abspath(self.filename or ""):
                    raise ImporException(
                        "AccessError",
                        f"{orig} 是模块 {target_mod.__name__} 的 private 符号，不能跨文件导入",
                        n.lineno, col=n.col)
                if orig not in target_mod._store:
                    # v2.12：from . import x / from .. import x 等纯相对导入——
                    # 包 store 尚无该名时回退加载包内子模块（Python 语义）
                    try:
                        sub_name = self._resolve_logical_name(
                            orig, n.rel_level, n.lineno)
                        v = self._load_module(sub_name, 0, n.lineno)
                    except ImporException:
                        raise ImporException(
                            "ImportError",
                            f"模块 {n.modname or self._cur_package or '<当前包>'} 没有导出 {orig}",
                            n.lineno, col=n.col)
                else:
                    v = target_mod._store[orig]
            else:
                if not hasattr(target_mod, orig):
                    raise ImporException(
                        "ImportError", f"模块 {n.modname} 没有导出 {orig}", n.lineno, col=n.col)
                v = getattr(target_mod, orig)
            self.cur_env.set_local(alias or orig, v)
        return target_mod

    # 兼容旧接口（runtime / 外部代码仍调用 load_user_module）
    def load_user_module(self, mod_name):
        return self._load_module(mod_name, 0, 0)

    # ---------- 块 / 异常 ----------
    def visit_Block(self, n):
        lv = None
        for s in n.stmts:
            lv = self._exec_stmt(s)
        return lv

    def visit_Defer(self, n):
        # v3.7.8：defer 是“作用域注册”，不是立即执行；退出函数/模块时 LIFO 执行。
        if not self._defer_frames:
            raise ImporException("DeferError", "defer 不在函数或模块作用域内",
                                 n.lineno, col=n.col)
        self._defer_frames[-1].append(list(n.body))
        return None

    def _run_defer_frame(self, frame):
        """执行一个作用域内注册的延迟块，后注册先执行。"""
        while frame:
            body = frame.pop()
            for s in body:
                self._exec_stmt(s)

    def visit_TryCatch(self, n):
        try:
            try:
                for s in n.body:
                    self._exec_stmt(s)
            except (BreakExc, ContinueExc, ExitScriptExc, ReturnExc):
                raise
            except ImporException as e:
                if n.err_type is None or n.err_type == e.name:
                    self.cur_env.set_local(n.err_var, e)
                    for s in n.catch_body:
                        self._exec_stmt(s)
                else:
                    raise e
            except Exception as e:
                wrapped = wrap_py_error(e, n.lineno, n.col, self.filename)
                if n.err_type is None or n.err_type == wrapped.name:
                    self.cur_env.set_local(n.err_var, wrapped)
                    for s in n.catch_body:
                        self._exec_stmt(s)
                else:
                    raise wrapped
        finally:
            if n.finally_body:
                for s in n.finally_body:
                    self._exec_stmt(s)

    def visit_ClassDef(self, n):
        methods = {}
        class_fields = {}
        private = set()
        protected = set()
        for stmt in n.body:
            if isinstance(stmt, Def):
                methods[stmt.name] = Closure(stmt.params, stmt.body, self.cur_env,
                                             interp=self, name=stmt.name)
                if getattr(stmt, "visibility", "") == "private":
                    private.add(stmt.name)
                elif getattr(stmt, "visibility", "") == "protected":
                    protected.add(stmt.name)
            elif isinstance(stmt, Assign):
                class_fields[stmt.name] = self.visit(stmt.expr)
                if getattr(stmt, "visibility", "") == "private":
                    private.add(stmt.name)
                elif getattr(stmt, "visibility", "") == "protected":
                    protected.add(stmt.name)
        bc = None
        if n.base_name is not None:
            try:
                bc = self.cur_env.get(n.base_name)
            except ImporException as e:
                if not e.lineno:
                    e.lineno, e.col, e.filename = n.lineno, n.col, self.filename
                raise
            if not isinstance(bc, ImporClass):
                raise ImporException("TypeError", f"{n.base_name} 不是类", n.lineno, col=n.col)
        cls_obj = ImporClass(n.cls_name, bc, methods, class_fields, private, protected)

        # v6.0：implements 真校验——接口里声明的方法必须在类（含基类）中存在
        def _has_method(c, name):
            while c is not None:
                if name in c.methods:
                    return True
                c = c.base
            return False

        for iface_name in getattr(n, "interfaces", []) or []:
            try:
                iface = self.cur_env.get(iface_name)
            except ImporException:
                raise ImporException("ContractError", f"接口 {iface_name} 未定义", n.lineno, col=n.col)
            if not isinstance(iface, ImporClass):
                raise ImporException("ContractError", f"{iface_name} 不是接口/类", n.lineno, col=n.col)
            missing = [m for m in iface.methods if not _has_method(cls_obj, m)]
            if missing:
                raise ImporException(
                    "ContractError",
                    f"{n.cls_name} 实现 {iface_name} 缺少方法：{', '.join(missing)}",
                    n.lineno, col=n.col)
        self.cur_env.set_local(n.cls_name, cls_obj)

    def visit_ExceptionDef(self, n):
        self.cur_env.set_local(n.exc_name, UserExceptionType(n.exc_name))

    def visit_Raise(self, n):
        try:
            exc_obj = self.cur_env.get(n.exc_type_name)
        except ImporException as e:
            if not e.lineno:
                e.lineno, e.col, e.filename = n.lineno, n.col, self.filename
            raise
        if not isinstance(exc_obj, UserExceptionType):
            raise ImporException("RaiseError", f"{n.exc_type_name} 不是用户自定义异常类型", n.lineno, col=n.col)
        msg_val = str(self.visit(n.msg_expr))
        cause = self.visit(n.cause_expr) if n.cause_expr is not None else None
        exc = ImporException(exc_obj.name, msg_val, n.lineno, cause=cause)
        exc.stack = list(self.call_stack)  # 抛出点完整调用栈快照
        exc._stack_kind = "snapshot"
        raise exc

    def visit_AssertNode(self, n):
        # assert 表达式：条件为假抛 AssertionError（可被 try/catch 捕获）
        # v4.2：记录已执行断言数，供 impor test 输出每文件断言统计
        self.assert_count += 1
        val = self.visit(n.cond)
        if not val:
            raise ImporException("AssertionError", "断言失败", n.lineno, col=n.col)

    def visit_BindingPattern(self, n):
        # 模式本身不求值（绑定语义在 visit_MatchNode 处理）；此处仅为分发完整
        return None

    def visit_AdtPattern(self, n):
        return None

    def visit_MatchNode(self, n):
        subj = self.visit(n.subject)
        for pattern, body in n.cases:
            if isinstance(pattern, AdtPattern):
                if not isinstance(subj, dict) or subj.get("__adt") != pattern.tag:
                    continue
                for bind, fname in zip(pattern.fields, pattern.field_names):
                    if bind:
                        self.cur_env.set_local(bind, subj.get(fname))
                for s in body:
                    self._exec_stmt(s)
                return None
            if isinstance(pattern, BindingPattern):
                if pattern.name is not None:
                    self.cur_env.set_local(pattern.name, subj)
                for s in body:
                    self._exec_stmt(s)
                return None
            pat_val = self.visit(pattern)
            if subj == pat_val:
                for s in body:
                    self._exec_stmt(s)
                return None
        if n.elseb is not None:
            for s in n.elseb:
                self._exec_stmt(s)
        return None


# ---------------- 工具函数 ----------------
def os_getcwd():
    import os
    return os.getcwd()


def strip_header(src, strict=False, warn=False):
    """去掉文件头标记，返回可解析的代码体。

    - 有头部：剥离后返回；
    - 无头部：strict=True 时抛 FormatError（旧行为）；否则原样返回，
      warn=True 时向 stderr 输出一条提示（头部自 v2.3 起为可选）。
    """
    HEADER_MARK = 'important<<DAY<<english("English"):'
    stripped = src.lstrip()
    if stripped.startswith(HEADER_MARK):
        return stripped[len(HEADER_MARK):]
    if strict:
        raise ImporException("FormatError", f"缺少头部标记 {HEADER_MARK}")
    if warn and os.environ.get("IMPOR_HEADER_HINT", "").strip() in ("1", "true", "yes", "on"):
        # v4.1：头部标记自 v2.3.1 起就只是可选版本标记；默认不再对每个新文件打提示，
        # 需要旧行为时置 IMPOR_HEADER_HINT=1。版本标记的新家是 impor.toml。
        import sys as _sys
        print('Impor 提示: 未找到可选的头部标记 important<<DAY<<english("English"):'
              '（不影响运行；已不再推荐写进源码，版本信息请放 impor.toml）', file=_sys.stderr)
    return stripped


def parse_source(body, filename=None):
    """把代码体解析成 AST；filename 用于语法错误定位。"""
    tokens = tokenize(body)
    return Parser(tokens, filename).program()
