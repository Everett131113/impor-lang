# RFC 0003：`div` 从「解释器独有」提升为「运行时内建」

## 1. 为什么要改

`div(a, b)` 是纯算术内建（整数按 floor 除法：`div(-7, 2) = -4`），不依赖解释器状态、
不依赖沙盒、不依赖 FFI，也不读宿主动态环境。但 `impor/builtin_manifest.py` 从 3.6.16
起把它登记在 `INTERPRETER_ONLY_BUILTINS`，注释给的理由是「依赖解释器状态」，与实现不符。

后果是：`impor compile x.impor` 产出的 Python 转译件执行 `div(...)` 会报
`NameError: name 'div' is not defined`——因为 `from impor.runtime import *` 不会导出
`INTERPRETER_ONLY` 面。同一份源码在解释器能跑、转译后不能跑，而 C 后端（`ctrans` 有
`div` 映射）反而能跑。**三端行为不一致，且 bug 落在最不该有差异的算术上。**

## 2. 变更内容

- `impor/builtin_manifest.py`：`div` 从 `INTERPRETER_ONLY_BUILTINS` 移入 `RUNTIME_BUILTINS`。
- `impor/runtime.py`：新增 `div(a, b)` 实现——`int()` 强转失败抛
  `ImporException("TypeError", "div 需要数值参数")`，除数为 0 抛
  `ImporException("DivisionByZero", "div 除数为 0")`，正常路径返回 `ia // ib`（floor）。
  `__all__` 由 `... + sorted(RUNTIME_BUILTINS)` 自动扩展，导入期的
  `check_runtime_namespace` 会校验它确实可调用。
- `tools/api_baseline.json`：`builtins.interpreter_only` 移除 `div`、`builtins.runtime` 追加
  `div`。**内建总数不变（117 → 117）**，只是分组纠正。

没有新增/删除任何内建名字，没有改关键字，没有改错误码。

## 3. 影响谁

- 解释器：无变化（`div` 一直在，语义不变）。
- `impor compile` 转译件：`div` 现在可用（修复）。
- C 后端：无变化（`ctrans` 本来就有 `rt_div` 映射，实测 `div(-7, 2)` 输出 `-4`）。
- `imporc` 自举子集：不在其支持范围内，未动。

## 4. 验收

- `examples/std_demo.impor`、以及自建探针脚本（`div(-7, 2)` / `div(7, 2)`）三路
  （解释器 / `compile` → Python / `build` → exe）输出逐字节一致：`-4 3`。
- `tests.test_v4_backend_parity` 23/23；`conformance/run_conformance.py` 100/100。

## 5. 回滚

把 `div` 移回 `INTERPRETER_ONLY_BUILTINS`、删掉 `runtime.py` 的 `div`、用
`tools/api_baseline.py --update --rfc docs/rfc/0003-div-runtime.md` 刷新基线即可。
