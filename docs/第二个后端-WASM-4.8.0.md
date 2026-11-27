# 第二个后端：WebAssembly（4.8.0）

## 一句话

`impor wasm hello.impor -o hello.wasm` 直接把 Impor 源码编成一份标准 WASM 二进制——**没有 LLVM、没有 wat 文本、没有 C 工具链、不装 emscripten**，产物扔进浏览器、Node、或任何 WASM 运行时就能跑。

这是 Impor 的**第二个后端**（第一个是 C 后端）。存在的理由不是性能，是**可移植性**：C 后端要 gcc/tcc，解释器要 Python，WASM 后端两样都不要。

## 为什么要手写二进制

本机实测：`deno`、`wasmtime`、`wasmer`、`wat2wasm`、`clang` 全部缺失。要在这种环境里拿到一个真能跑的第二后端，唯一的路是自己出字节码：

- `impor/wasm.py` 里实现 LEB128（signed/unsigned）、section、vec、name 四个编码器；
- 类型段/函数段/表段/内存段/全局段/导出段/代码段/数据段逐段手工拼装；
- 64 位整数语义全部自己实现：`iadd`/`isub`/`imul`/`ineg`/`imod` 五个 helper 函数，每个都带**溢出检测**（溢出即调 `overflow` import，对应 Impor 的 E1012，和解释器、C 后端同一口径）。

所以 WASM 后端和 C 后端是**同一个语义模型的两个独立实现**，不是「C 后端换个壳」。

## 用法

```bash
# 编成 .wasm
python -m impor wasm bench/suite/fib25.impor -o fib25.wasm

# 顺便生成一个内嵌 wasm 的单文件演示页，双击即可在浏览器里跑
python -m impor wasm bench/suite/fib25.impor -o fib25.wasm --html wasm-fib25.html
```

Node 里跑：

```bash
node tools/wasm_run.js fib25.wasm 75025     # 第三参数是期望输出，不符则 exit 2
```

浏览器里跑：`--html` 生成的页面自带 loader，`WebAssembly.instantiate` 之后调导出的 `_start`，`print_*` 由 JS 侧提供。

## 覆盖与拒绝（实测，不是估计）

`bench/caps/` 十个探针跑三后端，`tools/capability_gate.py` 逐格断言：

| 能力 | 解释器 | C 后端 | WASM 后端 |
|------|--------|--------|-----------|
| 整数 int64 全宽 | 支持 | 支持 | **支持** |
| 浮点 | 支持 | 支持 | 拒绝（明确） |
| 字符串（拼接/len/重复） | 支持 | 支持 | 拒绝（明确） |
| 列表 | 支持 | 支持 | 拒绝（明确） |
| 字典 | 支持 | 支持 | 拒绝（明确） |
| 类 | 支持 | 支持 | 拒绝（明确） |
| 闭包 | 支持 | 支持 | 拒绝（明确） |
| 泛型 | 支持 | 支持 | 拒绝（明确，需单态化） |
| 异常 | 支持 | 支持 | 拒绝（明确） |
| 内建转换 int()/str() | 支持 | 支持 | 拒绝（明确） |

WASM 后端当前覆盖的是**数值快路径**：int64 算术、比较、bool、`if`/`while`/`for`、函数定义与递归、`print`（数值与字符串字面量）。字符串只支持**字面量**（进 data 段，从偏移 1024 起），不支持任何字符串运算。

拒绝是**编译期**的：`WasmUnsupported` 带行列号，CLI 打印「WASM 后端不支持这段代码（第 N 行）：原因」，退出码 3，并提示改用 `impor build` / `impor run`。不会生成一份「看着能跑、结果不对」的 wasm。

## 实测数字

四个数值基准 + 一个字符串用例，全部与解释器输出一致：

| 用例 | 期望输出 | wasm 大小 | 结果 |
|------|----------|-----------|------|
| fib25（fib(25) 递归） | 75025 | 482 B | MATCH |
| sum2m（200 万次累加） | 1999999000000 | 536 B | MATCH |
| nested1m（双层循环 100 万） | 1000000 | 608 B | MATCH |
| mod1m（100 万次 `%` 累加） | 2999997 | 539 B | MATCH |
| wasm_str（字符串字面量 print） | `a = 3` / `sq(7) = 49` / `7` | 513 B | MATCH |

一个 100 万次循环的程序 500 多字节——因为不带任何运行时（不开 LLVM 的堆、不带分配器，连 panic 字符串都没有）。

端到端耗时（Node，9 轮取最小/中位，**含 node 冷启动**）：

| 场景 | 最小 | 中位 |
|------|------|------|
| node 空脚本启动基线 | 0.1184 s | 0.1338 s |
| fib25 | 0.1310 s | 0.1383 s |
| nested1m | 0.1277 s | 0.1293 s |
| mod1m | 0.1368 s | 0.1465 s |
| sum2m | 0.1181 s | 0.1256 s |

结论要说清：**启动占绝对大头**。减去基线，WASM 本体跑 100 万级循环是毫秒级（抖动范围内 5–15 ms）。WASM 后端的卖点不是速度，是「同一个 .impor 文件，不装任何工具链也能在浏览器里跑起来」。

## 已知限制（明确写出来）

1. 只有 `i64` 一种值类型：函数返回值统一 `i64`，所以**多类型签名（泛型、返回字符串）表达不了**，这类必须拒绝——这是设计边界，不是待修 bug。
2. 无 `break`/`continue`/`and-or` 短路：块栈与跳转标签还没做，现在一律拒绝。
3. 内存固定 1 页（64 KiB），字符串区从 1024 起，总字符串长度上限约 63 KiB。
4. 没有 `f64`：浮点要新增一组 `f64.*` 指令与类型分支，留到 4.8.1。
5. 只有一个 `_start` 导出，没有模块间调用、没有 host 回调注册表。

## 代码结构

```
impor/wasm.py          后端本体（编码器 → 类型/函数/全局/导出/代码/数据段）
tools/wasm_build.py    独立 CLI（调试用，等价于 impor wasm）
tools/wasm_run.js      Node 运行器（可传期望输出做比对）
tools/capability_gate.py  三后端能力矩阵门禁
bench/caps/*.impor     十个能力探针
```

想扩一个内建（比如 `len`）：在 `wasm.py` 的调用分派处加一个名字，若参数是字符串字面量就能在编译期算出长度、直接压 `i64.const`——这是 WASM 后端最省力的扩展方向（常量折叠代替运行时）。
