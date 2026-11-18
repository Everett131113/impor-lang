# 基准结果（本机）

> `v5.6 / v6.1 / v6.2` 是历史性能优化轮次的内部标签，**不是发行版号**；对外发行版只有 `VERSION`（当前 3.7.3）。

## 3.7.3 原生数值快路径

- 环境：Windows / CPython 3.11.5 / 随包 tcc（`-O2`）/ `impor build --fast`
- 方法：同一份源码，CPython 直接运行，Impor 先构建为独立 exe；构建不计时，取多次墙钟中位数
- 日期：2026-11-15
- 完整数据：[native20.md](native20.md)；脚本：[bench_native20.py](bench_native20.py)

| 用例 | CPython | Impor 快路径 | 倍率 |
|---|---:|---:|---:|
| arith_50m (N=50000000) | 11359.4 ms | 154.9 ms | 73.33x |
| nested_10k (N=10000) | 14255.8 ms | 242.8 ms | 58.71x |
| builtin_20m (N=20000000) | 11928.6 ms | 132.7 ms | 89.87x |
| mod_20m_diag (N=20000000) | 3305.1 ms | 371.5 ms | 8.90x |
| fib_32_diag (N=32) | 510.6 ms | 64.9 ms | 7.87x |

20x 门禁：**PASS**（三项纯整数循环）。`mod` 与 `fib` 是诊断项，不计入门禁；它们说明 20x 不能推广到所有脚本。

## 3.7.2 解释器基准（历史）

- 环境：Windows / Python 3.11.5 / Impor 解释器路径
- 方法：`benchmarks/bench_compare.py`，每项取墙钟时间，含进程启动
- 日期：2026-11-09

| 用例 | v5.6 | v6.1 | v6.2 | v6.1→v6.2 |
|---|---:|---:|---:|---:|
| loop_1e6 | 8042.1 ms | 799.8 ms | 731.9 ms | 8.5% |
| str_2000 | 525.6 ms | 283.4 ms | 249.1 ms | 12.1% |
| fib_22 | 1293.4 ms | 694.6 ms | 648.4 ms | 6.7% |

纯解释执行（不含解析与进程启动，`impor bench`）：**0.581 s → 0.520–0.563 s**（多次实测）。

启动侧：`run` 不再导入 `impor.pkgmgr`，`python -X importtime` 少一项 95.7 ms 的累计导入（含 `urllib.request` / `http.client` / `ssl` / `zipfile`）。

结论：v6.2 的主要收益来自函数调用帧开销和启动导入，而不是执行模型变化。100 万循环仍在 1 秒内，递归仍明显慢于 CPython；计算密集场景继续优先 `impor build` 走 C 后端。
