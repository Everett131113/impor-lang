# -*- coding: utf-8 -*-
"""imporai._autograd：自动求导。

Tensor 包装 NDArray，记录由哪些父张量、经什么算子产生；
backward() 沿拓扑序把梯度一路累加回叶子。
"""
from ._native import NDArray


class Tensor:
    __slots__ = ("data", "grad", "requires_grad", "_prev", "_backward")

    def __init__(self, data, requires_grad=False, _prev=(), _backward=None):
        if isinstance(data, NDArray):
            self.data = data
        else:
            self.data = NDArray.from_list(data)
        self.requires_grad = requires_grad
        self.grad = None
        self._prev = list(_prev)
        self._backward = _backward

    # ---------- 基本 ----------

    def zero_grad(self):
        self.grad = None

    def _accum(self, g):
        if self.grad is None:
            self.grad = g.copy()
        else:
            self.grad = self.grad.add(g)

    @property
    def shape(self):
        return self.data.shape

    def backward(self, grad=None):
        if grad is None:
            # 标量起点：全 1
            grad = NDArray.ones(self.data.shape)
        topo = []
        visited = set()

        def build(t):
            if id(t) in visited:
                return
            visited.add(id(t))
            for p in t._prev:
                build(p)
            topo.append(t)

        build(self)
        self.grad = grad.copy()
        for t in reversed(topo):
            if t._backward is not None:
                t._backward(t.grad)

    def __repr__(self):
        return "Tensor(shape=%s, grad=%s)" % (self.data.shape, self.grad is not None)


def _wrap_op(out, prev, backward):
    rg = any(p.requires_grad for p in prev)
    return Tensor(out, requires_grad=rg, _prev=[p for p in prev if p.requires_grad],
                  _backward=backward if rg else None)


# ---------- 算子 ----------

def add(x, y):
    is_scalar = not isinstance(y, Tensor)
    out = x.data.add(y.data if not is_scalar else y)

    def bw(g):
        x._accum(g)
        if not is_scalar:
            y._accum(g)

    return _wrap_op(out, (x,) if is_scalar else (x, y), bw)


def sub(x, y):
    is_scalar = not isinstance(y, Tensor)
    out = x.data.sub(y if is_scalar else y.data)

    def bw(g):
        x._accum(g)
        if not is_scalar:
            y._accum(g.mul(-1.0))

    return _wrap_op(out, (x, y), bw)


def log(x):
    out = x.data.log()

    def bw(g):
        # d log(u)/du = 1/u
        x._accum(g.div(x.data))

    return _wrap_op(out, (x,), bw)


def mul(x, y):
    # 逐元素乘；支持标量
    is_scalar = not isinstance(y, Tensor)
    yd = y if is_scalar else y.data
    out = x.data.mul(yd)

    def bw(g):
        x._accum(g.mul(yd))
        if not is_scalar:
            y._accum(g.mul(x.data))

    return _wrap_op(out, (x,) if is_scalar else (x, y), bw)


def matmul(x, y):
    out = x.data.matmul(y.data)

    def bw(g):
        # g: (m,n); x: (m,k); y: (k,n)
        x._accum(g.matmul(y.data.transpose()))
        y._accum(x.data.transpose().matmul(g))

    return _wrap_op(out, (x, y), bw)


def linear(x, w, b=None):
    """线性层：x @ w + b，偏置 (1,k) 自动广播。"""
    t = matmul(x, w)
    if b is None:
        return t

    out = t.data.add_bias(b.data)

    def bw(g):
        # 矩阵部分梯度（matmul 已在 t 的 backward 里处理），这里只补偏置：
        # 把行梯度沿第 0 轴求和回 (1,k)
        b._accum(g.sum_axis0())

    rg = t.requires_grad or b.requires_grad
    prev = []
    if t.requires_grad:
        prev.extend(t._prev)
    if b.requires_grad:
        prev.append(b)

    def full_bw(g):
        if t.requires_grad:
            # 复用 matmul 产生的 backward 逻辑，直接把梯度送回 x / w
            t._backward(g)
        if b.requires_grad:
            bw(g)

    return Tensor(out, requires_grad=rg, _prev=prev,
                  _backward=full_bw if rg else None)


def _apply_activation(x, fn, grad_fn):
    out = getattr(x.data, fn)()

    def bw(g):
        local = grad_fn(out)
        x._accum(g.mul(local))

    return _wrap_op(out, (x,), bw)


def tanh(x):
    return _apply_activation(
        x, "tanh", lambda out: _scalar_map(out, lambda v: 1.0 - v * v))


def sigmoid(x):
    return _apply_activation(
        x, "sigmoid", lambda out: _scalar_map(out, lambda v: v * (1.0 - v)))


def relu(x):
    return _apply_activation(
        x, "relu", lambda out: _scalar_map(out, lambda v: 1.0 if v > 0 else 0.0))


def _scalar_map(arr, fn):
    flat = arr.flat()
    return NDArray.from_list([fn(v) for v in flat]).reshape(arr.shape)


def sum_all(x):
    out = NDArray.from_list([x.data.sum()])

    def bw(g):
        v = g.flat()[0]
        x._accum(NDArray.ones(x.data.shape).mul(v))

    return _wrap_op(out, (x,), bw)


def mean(x):
    n = x.data.size
    t = sum_all(x)
    return mul(t, 1.0 / n)
