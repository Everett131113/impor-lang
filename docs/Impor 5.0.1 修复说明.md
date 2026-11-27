# Impor 5.0.1 修复说明

## 问题

5.0.0 中，只要计算图里出现**两个张量相减**（例如均方误差写法 `imporai.sub(pred, y)`），
调用 `loss.backward()` 就会报错：

```
AttributeError: 'NDArray' object has no attribute 'data'
```

## 根因

`impor/imporai/_autograd.py` 的 `sub` 反向传播闭包里误写了 `g.data`。
反向传播传入的梯度 `g` 本身就是 NDArray，没有 `.data` 属性（`.data` 是 Tensor 才有的）。

## 修复

- `g.data.mul(-1.0)` 改为 `g.mul(-1.0)`
- 新增回归测试 `test_sub_gradient`：对 L = sum((a-b)^2) 校验
  dL/da = 2(a-b)、dL/db = -2(a-b)，防止同类问题再次漏网
- `tests/test_imporai.py` 从 13 项增至 **14 项，全部 OK**

## 验证

- 全量门禁：格式 241/241、API 基线 55-124-37-30 一致、回归合集 30/30 OK
- 触发该 bug 的最小训练程序（含 sub 的均方误差）现在可完整跑完并收敛
- 语言本体零改动，无新增语法或语法糖
