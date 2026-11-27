# -*- coding: utf-8 -*-
"""imporai._native：imporai.dll 的 ctypes 绑定与 NDArray 包装。

NDArray 只是一个句柄壳：数据在 C 库里，所有重计算走 DLL。
"""
import ctypes
import os

_DLL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "native", "imporai.dll"))
if not os.path.exists(_DLL_PATH):
    # 打包/安装目录里若布局不同，再按 PATH 找一次
    _dll = ctypes.CDLL("imporai.dll")
else:
    _dll = ctypes.CDLL(_DLL_PATH)

c_i64_p = ctypes.POINTER(ctypes.c_int64)
c_f64_p = ctypes.POINTER(ctypes.c_double)


def _shape_arg(shape):
    arr = (ctypes.c_int64 * len(shape))(*[int(x) for x in shape])
    return arr


def _bind(name, restype, argtypes):
    fn = getattr(_dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


ia_free = _bind("ia_free", None, [ctypes.c_void_p])
ia_ndim = _bind("ia_ndim", ctypes.c_int, [ctypes.c_void_p])
ia_size = _bind("ia_size", ctypes.c_int64, [ctypes.c_void_p])
ia_shape_at = _bind("ia_shape_at", ctypes.c_int64, [ctypes.c_void_p, ctypes.c_int])
ia_data_ptr = _bind("ia_data_ptr", ctypes.c_void_p, [ctypes.c_void_p])
ia_seed_rng = _bind("ia_seed_rng", None, [ctypes.c_uint64])
ia_zeros = _bind("ia_zeros", ctypes.c_void_p, [ctypes.c_int, c_i64_p])
ia_ones = _bind("ia_ones", ctypes.c_void_p, [ctypes.c_int, c_i64_p])
ia_from_buffer = _bind("ia_from_buffer", ctypes.c_void_p,
                       [c_f64_p, ctypes.c_int, c_i64_p])
ia_copy = _bind("ia_copy", ctypes.c_void_p, [ctypes.c_void_p])
ia_get = _bind("ia_get", ctypes.c_double, [ctypes.c_void_p, c_i64_p])
ia_set = _bind("ia_set", None,
               [ctypes.c_void_p, c_i64_p, ctypes.c_double])
ia_reshape = _bind("ia_reshape", ctypes.c_void_p,
                   [ctypes.c_void_p, ctypes.c_int, c_i64_p])
ia_transpose2d = _bind("ia_transpose2d", ctypes.c_void_p, [ctypes.c_void_p])
ia_elem = _bind("ia_elem", ctypes.c_void_p,
                [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int])
ia_scalar = _bind("ia_scalar", ctypes.c_void_p,
                  [ctypes.c_void_p, ctypes.c_double, ctypes.c_int])
ia_apply = _bind("ia_apply", ctypes.c_void_p,
                 [ctypes.c_void_p, ctypes.c_int])
ia_matmul = _bind("ia_matmul", ctypes.c_void_p,
                  [ctypes.c_void_p, ctypes.c_void_p])
ia_sum = _bind("ia_sum", ctypes.c_double, [ctypes.c_void_p])
ia_randn = _bind("ia_randn", ctypes.c_void_p, [ctypes.c_int, c_i64_p])
ia_rand_uniform = _bind("ia_rand_uniform", ctypes.c_void_p,
                        [ctypes.c_int, c_i64_p, ctypes.c_double, ctypes.c_double])
ia_add_bias = _bind("ia_add_bias", ctypes.c_void_p,
                    [ctypes.c_void_p, ctypes.c_void_p])
ia_sum_axis0 = _bind("ia_sum_axis0", ctypes.c_void_p,
                     [ctypes.c_void_p])


def _flatten(nested):
    out = []
    if isinstance(nested, (list, tuple)):
        for x in nested:
            out.extend(_flatten(x))
    else:
        out.append(float(nested))
    return out


def _infer_shape(nested):
    shape = []
    cur = nested
    while isinstance(cur, (list, tuple)):
        shape.append(len(cur))
        cur = cur[0] if cur else None
    return tuple(shape)


class NDArray:
    """连续 float64 多维数组，句柄指向 C 库的 IAArray。"""

    __slots__ = ("_ptr",)

    def __init__(self, ptr):
        if not ptr:
            raise MemoryError("imporai 分配失败")
        self._ptr = int(ptr)

    def __del__(self):
        try:
            ia_free(getattr(self, "_ptr", 0))
        except Exception:
            pass

    # ---------- 基本属性 ----------

    @property
    def ptr(self):
        return self._ptr

    @property
    def ndim(self):
        return ia_ndim(self._ptr)

    @property
    def size(self):
        return int(ia_size(self._ptr))

    @property
    def shape(self):
        return tuple(int(ia_shape_at(self._ptr, d))
                     for d in range(self.ndim))

    def _data_view(self):
        p = ia_data_ptr(self._ptr)
        return ctypes.cast(p, c_f64_p)

    def flat(self):
        view = self._data_view()
        return [view[i] for i in range(self.size)]

    def to_list(self):
        flat = self.flat()
        shape = self.shape

        def build(offset, dim):
            n = shape[dim]
            if dim == len(shape) - 1:
                return flat[offset:offset + n]
            span = 1
            for d in shape[dim + 1:]:
                span *= d
            out = []
            for k in range(n):
                out.append(build(offset + k * span, dim + 1))
            return out

        return build(0, 0)

    def copy(self):
        return NDArray(ia_copy(self._ptr))

    # ---------- 构造 ----------

    @classmethod
    def zeros(cls, shape):
        shape = tuple(shape)
        sa = _shape_arg(shape)
        return cls(ia_zeros(len(shape), sa))

    @classmethod
    def ones(cls, shape):
        shape = tuple(shape)
        sa = _shape_arg(shape)
        return cls(ia_ones(len(shape), sa))

    @classmethod
    def from_list(cls, nested):
        if not isinstance(nested, (list, tuple)):
            nested = [nested]
        shape = _infer_shape(nested)
        flat = _flatten(nested)
        expected = 1
        for d in shape:
            expected *= d
        if expected != len(flat):
            raise ValueError("嵌套列表形状不齐：%s 元素数 %d" % (shape, len(flat)))
        buf = (ctypes.c_double * len(flat))(*flat)
        sa = _shape_arg(shape)
        return cls(ia_from_buffer(buf, len(shape), sa))

    @classmethod
    def randn(cls, shape):
        shape = tuple(shape)
        sa = _shape_arg(shape)
        return cls(ia_randn(len(shape), sa))

    @classmethod
    def uniform(cls, shape, lo=0.0, hi=1.0):
        shape = tuple(shape)
        sa = _shape_arg(shape)
        return cls(ia_rand_uniform(len(shape), sa, float(lo), float(hi)))

    # ---------- 索引 ----------

    def get(self, idx):
        idx = tuple(int(x) for x in idx)
        ia = (ctypes.c_int64 * len(idx))(*idx)
        return float(ia_get(self._ptr, ia))

    def set(self, idx, value):
        idx = tuple(int(x) for x in idx)
        ia = (ctypes.c_int64 * len(idx))(*idx)
        ia_set(self._ptr, ia, float(value))

    # ---------- 变形 ----------

    def reshape(self, shape):
        shape = tuple(shape)
        sa = _shape_arg(shape)
        return NDArray(ia_reshape(self._ptr, len(shape), sa))

    def transpose(self):
        return NDArray(ia_transpose2d(self._ptr))

    # ---------- 运算 ----------

    def elem(self, other, op):
        if isinstance(other, NDArray):
            return NDArray(ia_elem(self._ptr, other._ptr, op))
        return NDArray(ia_scalar(self._ptr, float(other), op))

    def add(self, other):
        return self.elem(other, 0)

    def sub(self, other):
        return self.elem(other, 1)

    def mul(self, other):
        return self.elem(other, 2)

    def div(self, other):
        return self.elem(other, 3)

    def apply(self, fn):
        return NDArray(ia_apply(self._ptr, fn))

    def exp(self):
        return self.apply(0)

    def log(self):
        return self.apply(1)

    def tanh(self):
        return self.apply(2)

    def sqrt(self):
        return self.apply(3)

    def relu(self):
        return self.apply(4)

    def sigmoid(self):
        return self.apply(5)

    def matmul(self, other):
        return NDArray(ia_matmul(self._ptr, other._ptr))

    def add_bias(self, bias):
        # self (m,k) + bias (1,k)
        return NDArray(ia_add_bias(self._ptr, bias._ptr))

    def sum_axis0(self):
        return NDArray(ia_sum_axis0(self._ptr))

    def sum(self):
        return float(ia_sum(self._ptr))

    # 运算符重载（Python 层内部用，Impor 代码走命名函数更稳）
    def __add__(self, other):
        return self.add(other)

    def __sub__(self, other):
        return self.sub(other)

    def __mul__(self, other):
        return self.mul(other)

    def __truediv__(self, other):
        return self.div(other)

    def __matmul__(self, other):
        return self.matmul(other)

    def __repr__(self):
        return "NDArray(shape=%s)" % (self.shape,)
