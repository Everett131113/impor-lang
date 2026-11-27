# Impor 5.0.0 发布说明：AI 数值栈

## 1. 这个版本解决什么

目标：让 Impor 能在不装任何外部库的情况下，**自己训练一个小神经网络**。

做法和 Python 生态同构：解释器负责编排，重计算全部下沉到一个 C 共享库。
语言本体一行未改 —— 关键字仍 55 个，没有新语法、没有语法糖（语法糖冻结承诺继续有效）。

## 2. 组成

| 部分 | 位置 | 内容 |
|---|---|---|
| C 数值内核 | `impor/native/imporai.c` → `imporai.dll` | ndarray（连续 float64）、逐元素运算、矩阵乘、广播、转置、reshape、随机数（含正态） |
| Python 绑定 | `impor/imporai/_native.py` | ctypes 加载 DLL，NDArray 句柄类 |
| 自动求导 | `impor/imporai/_autograd.py` | Tensor、计算图、backward |
| 优化器 / 存取 | `impor/imporai/_optim.py` | SGD、Adam、二进制模型文件 |
| 对外入口 | `impor/imporai/__init__.py` | `import imporai` 即可用 |
| 端到端示例 | `examples/ai_train.impor` | 两层 MLP 二分类 |
| 回归测试 | `tests/test_imporai.py` | 13 项 |

## 3. 实测数字

- 示例训练：损失 **0.1845 → 0.0012**（30 轮），准确率 **40/40**
- 自动求导：matmul 解析梯度与数值梯度（eps=1e-6）一致到小数点后 4 位
- conformance：2/2 OK（1162.8s）
- 测试：`test_imporai` 13/13、`test_v491` 12/12、`test_v372` 4/4、编辑器 10/10
- API 基线：55 关键字 / 124 内建 / 37 模块 / 30 错误码（与 4.9.2 相同）

## 4. 用法速览

```impor
import imporai
imporai.seed(42)
var x = imporai.tensor([[1.0, 2.0]])
var w = imporai.param([[0.5], [1.0]])
var y = imporai.matmul(x, w)
var loss = imporai.sum_all(imporai.mul(y, y))
loss.backward()                 # w.grad 现在有梯度
var opt = imporai.Adam([w], 0.01)
opt.step()
imporai.save_model("m.ia", [w])
```

## 5. 边界（如实列出）

- 仅 CPU、float64；没有 GPU/CUDA 路线
- 自动求导只覆盖训练常用算子（matmul、线性层、tanh、sigmoid、relu、log、求和/均值），不是完整算子库
- 词法暂不支持科学计数法字面量（如 `1e-9`），用等价小数写法
- 模型文件为 Impor 自定义二进制格式（magic "IA1"），不是通用格式
