# Impor 3.7.3 原生数值快路径基准

环境：Windows / CPython 3.11.5 / 随包 tcc，`impor build --fast`。

方法：同一份源码，CPython 直接运行，Impor 先构建为独立 exe；构建不计时，取多次墙钟中位数。

| 用例 | CPython | Impor 快路径 | 倍率 |
|---|---:|---:|---:|
| arith_50m (N=50000000) | 11359.4 ms | 154.9 ms | 73.33x |
| nested_10k (N=10000) | 14255.8 ms | 242.8 ms | 58.71x |
| builtin_20m (N=20000000) | 11928.6 ms | 132.7 ms | 89.87x |
| mod_20m_diag (N=20000000) | 3305.1 ms | 371.5 ms | 8.90x |
| fib_32_diag (N=32) | 510.6 ms | 64.9 ms | 7.87x |

20x 门禁：**PASS**。

注：`mod_20m_diag` 与 `fib_32_diag` 是诊断项，不属于 20x 门禁；取模与递归调用的倍率明显低于循环算术，不能据此宣称“所有脚本 20x”。
