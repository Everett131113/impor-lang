# -*- coding: utf-8 -*-
"""imporai — Impor 的 AI / 数值计算包。

用法（Impor 代码）：

    import imporai
    var x = imporai.tensor([[1.0, 2.0]])
    var w = imporai.param([[2.0], [1.0]])
    var y = imporai.matmul(x, w)

架构：解释器编排，重计算全部在 native/imporai.dll（C，float64）。
"""
import math as _math
import sys

from ._native import NDArray, ia_seed_rng
from . import _autograd
from ._autograd import Tensor
from . import _optim
from ._optim import SGD, Adam, save_model, load_model, load_into


# ---------- 标量数学（阶段 0） ----------

def seed(value):
    ia_seed_rng(int(value))


def exp(x):
    return _math.exp(float(x))


def log(x):
    return _math.log(float(x))


def sqrt(x):
    return _math.sqrt(float(x))


def tanh(x):
    return _math.tanh(float(x))


def powf(x, y):
    return _math.pow(float(x), float(y))


# ---------- 数组构造 ----------

def array(data):
    return NDArray.from_list(data)


def zeros(shape):
    return NDArray.zeros(shape)


def ones(shape):
    return NDArray.ones(shape)


def randn(shape):
    return NDArray.randn(shape)


def uniform(shape, lo=0.0, hi=1.0):
    return NDArray.uniform(shape, lo, hi)


# ---------- 张量与自动求导 ----------

def tensor(data, requires_grad=False):
    return Tensor(data, requires_grad=requires_grad)


def param(data):
    return Tensor(data, requires_grad=True)


def add(x, y):
    # NDArray 走原生算子；Tensor 走自动求导
    if isinstance(x, NDArray):
        return x.add(y)
    if isinstance(y, NDArray):
        return y.add(x)
    return _autograd.add(x, y)


def sub(x, y):
    if isinstance(x, NDArray):
        return x.sub(y)
    if isinstance(y, NDArray):
        # 标量 - 数组
        return y.mul(-1.0).add(x)
    return _autograd.sub(x, y)


def mul(x, y):
    if isinstance(x, NDArray):
        return x.mul(y)
    if isinstance(y, NDArray):
        return y.mul(x)
    return _autograd.mul(x, y)


def matmul(x, y):
    return _autograd.matmul(x, y)


def linear(x, w, b=None):
    return _autograd.linear(x, w, b)


def tanh_t(x):
    return _autograd.tanh(x)


def log_t(x):
    return _autograd.log(x)


def sigmoid(x):
    return _autograd.sigmoid(x)


def relu(x):
    return _autograd.relu(x)


def sum_all(x):
    return _autograd.sum_all(x)


def mean(x):
    return _autograd.mean(x)


# ---------- 数组级便捷函数（无求导场景） ----------

def transpose(a):
    return a.transpose() if isinstance(a, NDArray) else a.data.transpose()


def reshape(a, shape):
    return a.reshape(shape) if isinstance(a, NDArray) else a.data.reshape(shape)


# 让 `import imporai` 这个顶层名字可解析：
# 解释器 import 回退会先做名字映射，这里同时注册 sys.modules 别名兜底。
sys.modules.setdefault("imporai", sys.modules[__name__])

__all__ = [
    "NDArray", "Tensor", "SGD", "Adam",
    "seed", "exp", "log", "sqrt", "tanh", "powf",
    "array", "zeros", "ones", "randn", "uniform",
    "tensor", "param", "add", "sub", "mul", "matmul", "linear",
    "tanh_t", "sigmoid", "relu", "sum_all", "mean",
    "transpose", "reshape", "save_model", "load_model", "load_into",
]
