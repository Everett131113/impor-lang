# -*- coding: utf-8 -*-
"""5.0.0 回归：imporai 数值/AI 栈。

覆盖：标量数学、NDArray 构造与运算、矩阵乘、自动求导（解析+数值梯度）、
优化器、模型二进制存取、以及 examples/ai_train.impor 端到端可跑。
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PY = sys.executable


class TestScalarMath(unittest.TestCase):
    def test_math_functions(self):
        import impor.imporai as ai
        self.assertAlmostEqual(ai.exp(0.0), 1.0)
        self.assertAlmostEqual(ai.log(1.0), 0.0)
        self.assertAlmostEqual(ai.sqrt(9.0), 3.0)
        self.assertTrue(-1.0 < ai.tanh(0.0) < 1.0)
        self.assertAlmostEqual(ai.powf(2.0, 3.0), 8.0)

    def test_seed_reproducible(self):
        import impor.imporai as ai
        ai.seed(7)
        a = ai.randn([4]).flat()
        ai.seed(7)
        b = ai.randn([4]).flat()
        self.assertEqual(a, b)


class TestNDArray(unittest.TestCase):
    def setUp(self):
        import impor.imporai as ai
        self.ai = ai

    def test_constructors_and_shape(self):
        ai = self.ai
        z = ai.zeros([2, 3])
        self.assertEqual(z.shape, (2, 3))
        self.assertEqual(z.size, 6)
        self.assertEqual(z.flat(), [0.0] * 6)
        o = ai.ones([3])
        self.assertEqual(o.to_list(), [1.0, 1.0, 1.0])
        a = ai.array([[1.0, 2.0], [3.0, 4.0]])
        self.assertEqual(a.to_list(), [[1.0, 2.0], [3.0, 4.0]])

    def test_elementwise_and_matmul(self):
        ai = self.ai
        a = ai.array([[1.0, 2.0], [3.0, 4.0]])
        b = ai.array([[5.0, 6.0], [7.0, 8.0]])
        self.assertEqual(a.add(b).to_list(), [[6.0, 8.0], [10.0, 12.0]])
        self.assertEqual(a.mul(2.0).to_list(), [[2.0, 4.0], [6.0, 8.0]])
        self.assertEqual(a.matmul(b).to_list(),
                         [[19.0, 22.0], [43.0, 50.0]])
        self.assertEqual(a.transpose().to_list(),
                         [[1.0, 3.0], [2.0, 4.0]])
        self.assertEqual(a.reshape([4]).to_list(), [1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(a.sum(), 10.0)

    def test_bias_broadcast(self):
        ai = self.ai
        x = ai.zeros([4, 3])
        bias = ai.array([[1.0, 2.0, 3.0]])
        out = x.add_bias(bias)
        self.assertEqual(out.shape, (4, 3))
        self.assertEqual(out.to_list()[0], [1.0, 2.0, 3.0])
        self.assertEqual(out.sum_axis0().to_list(), [[4.0, 8.0, 12.0]])

    def test_get_set(self):
        ai = self.ai
        a = ai.zeros([2, 2])
        a.set((1, 1), 5.0)
        self.assertEqual(a.get((1, 1)), 5.0)


class TestAutograd(unittest.TestCase):
    def test_matmul_gradient(self):
        import impor.imporai as ai
        x = ai.tensor([[1.0, 2.0], [3.0, 4.0]])
        w = ai.param([[0.5], [1.0]])
        y = ai.matmul(x, w)
        loss = ai.sum_all(ai.mul(y, y))
        loss.backward()
        self.assertEqual(w.grad.to_list(), [[38.0], [54.0]])

    def test_numeric_gradient(self):
        import impor.imporai as ai
        x = ai.tensor([[1.0, 2.0], [3.0, 4.0]])
        w = ai.param([[0.5], [1.0]])

        # 先拿解析梯度
        y0 = ai.matmul(x, w)
        ai.sum_all(ai.mul(y0, y0)).backward()
        analytic = [w.grad.to_list()[r][0] for r in range(2)]

        def loss_value():
            y = x.data.matmul(w.data)
            return y.mul(y).sum()

        eps = 1e-6
        for row in range(2):
            orig = w.data.get((row, 0))
            w.data.set((row, 0), orig + eps)
            plus = loss_value()
            w.data.set((row, 0), orig - eps)
            minus = loss_value()
            w.data.set((row, 0), orig)
            numeric = (plus - minus) / (2 * eps)
            self.assertAlmostEqual(numeric, analytic[row], places=4)

    def test_sub_gradient(self):
        # 回归：两个张量相减再 backward（旧实现误写 g.data 导致 E1006）
        import impor.imporai as ai
        a = ai.param([[1.0, 2.0]])
        b = ai.param([[3.0, 5.0]])
        diff = ai.sub(a, b)
        loss = ai.sum_all(ai.mul(diff, diff))
        loss.backward()
        # d/da = 2(a-b) = [-4,-6]；d/db = -2(a-b) = [4,6]
        self.assertEqual(a.grad.to_list(), [[-4.0, -6.0]])
        self.assertEqual(b.grad.to_list(), [[4.0, 6.0]])

    def test_activation_and_linear(self):
        import impor.imporai as ai
        x = ai.tensor([[0.5, -0.5], [1.0, -1.0]])
        w = ai.param(ai.ones([2, 3]))
        b = ai.param(ai.zeros([1, 3]))
        h = ai.tanh_t(ai.linear(x, w, b))
        loss = ai.sum_all(h)
        loss.backward()
        self.assertIsNotNone(w.grad)
        self.assertIsNotNone(b.grad)
        self.assertEqual(b.grad.shape, (1, 3))


class TestOptimAndIO(unittest.TestCase):
    def test_sgd_step(self):
        import impor.imporai as ai
        w = ai.param([[2.0]])
        w.grad = ai.array([[1.0]])
        opt = ai.SGD([w], lr=0.5)
        opt.step()
        self.assertEqual(w.data.to_list(), [[1.5]])

    def test_adam_step(self):
        import impor.imporai as ai
        w = ai.param([[1.0, 2.0]])
        w.grad = ai.ones([1, 2])
        opt = ai.Adam([w], lr=0.1)
        opt.step()
        self.assertEqual(w.data.shape, (1, 2))

    def test_save_load_roundtrip(self):
        import impor.imporai as ai
        import tempfile
        a = ai.array([[1.0, 2.0], [3.0, 4.0]])
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.ia")
            ai.save_model(p, [a])
            back = ai.load_model(p)
        self.assertEqual(back[0].to_list(), a.to_list())


class TestEndToEndExample(unittest.TestCase):
    def test_ai_train_example_runs(self):
        path = os.path.join(ROOT, "examples", "ai_train.impor")
        self.assertTrue(os.path.exists(path))
        r = subprocess.run([PY, "-m", "impor", "run", path],
                           capture_output=True, text=True, cwd=ROOT, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("准确率 40 / 40", r.stdout)


if __name__ == "__main__":
    unittest.main()
