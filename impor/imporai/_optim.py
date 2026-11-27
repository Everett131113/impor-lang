# -*- coding: utf-8 -*-
"""imporai._optim：优化器与模型二进制存取。"""
import struct

from ._native import NDArray


class SGD:
    def __init__(self, params, lr=0.01):
        self.params = list(params)
        self.lr = float(lr)

    def zero_grad(self):
        for p in self.params:
            p.grad = None

    def step(self):
        for p in self.params:
            if p.grad is None:
                continue
            p.data = p.data.sub(p.grad.mul(self.lr))


class Adam:
    def __init__(self, params, lr=0.01, beta1=0.9, beta2=0.999, eps=1e-8):
        self.params = list(params)
        self.lr = float(lr)
        self.b1 = beta1
        self.b2 = beta2
        self.eps = eps
        self.t = 0
        self.m = [None] * len(self.params)
        self.v = [None] * len(self.params)

    def zero_grad(self):
        for p in self.params:
            p.grad = None

    def step(self):
        self.t += 1
        for i, p in enumerate(self.params):
            if p.grad is None:
                continue
            g = p.grad
            if self.m[i] is None:
                self.m[i] = NDArray.zeros(g.shape)
                self.v[i] = NDArray.zeros(g.shape)
            self.m[i] = self.m[i].mul(self.b1).add(g.mul(1.0 - self.b1))
            self.v[i] = self.v[i].mul(self.b2).add(g.mul(g).mul(1.0 - self.b2))
            mh = self.m[i].div(1.0 - self.b1 ** self.t)
            vh = self.v[i].div(1.0 - self.b2 ** self.t)
            # p -= lr * mh / (sqrt(vh) + eps)
            denom = vh.sqrt().add(self.eps)
            p.data = p.data.sub(mh.div(denom).mul(self.lr))


# ---------- 模型存取 ----------
# 格式（小端）：magic "IA1"，count:int32；
# 每个数组：ndim:int32，shape:int64[ndim]，data:double[prod(shape)]

_MAGIC = b"IA1"


def save_model(path, tensors):
    with open(path, "wb") as f:
        f.write(_MAGIC)
        f.write(struct.pack("<i", len(tensors)))
        for t in tensors:
            arr = t.data if hasattr(t, "data") else t
            shape = arr.shape
            f.write(struct.pack("<i", len(shape)))
            f.write(struct.pack("<%dq" % len(shape), *shape))
            f.write(struct.pack("<%dd" % arr.size, *arr.flat()))


def load_model(path):
    with open(path, "rb") as f:
        magic = f.read(3)
        if magic != _MAGIC:
            raise ValueError("不是 imporai 模型文件：%s" % path)
        (count,) = struct.unpack("<i", f.read(4))
        arrays = []
        for _ in range(count):
            (ndim,) = struct.unpack("<i", f.read(4))
            shape = struct.unpack("<%dq" % ndim, f.read(8 * ndim))
            n = 1
            for d in shape:
                n *= d
            flat = struct.unpack("<%dd" % n, f.read(8 * n))
            arrays.append(NDArray.from_list(list(flat)).reshape(shape))
        return arrays


def load_into(path, tensors):
    """把文件里的数组灌回现有 Tensor 的 data，返回张量列表。"""
    arrays = load_model(path)
    if len(arrays) != len(tensors):
        raise ValueError("参数个数对不上：文件 %d，传入 %d" % (len(arrays), len(tensors)))
    for t, a in zip(tensors, arrays):
        t.data = a
    return tensors
