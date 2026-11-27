# 第二个后端：WASM 4.9.0（f64 / 字符串 / break-continue / 短路）

> 适用：`impor/wasm.py`、`tools/wasm_build.py`、`tools/wasm_run.js`、`tools/wasm_batch.py`
> 上一版：`docs/第二个后端-WASM-4.8.0.md`（只支持整数 + print）

## 1. 这一版做了什么

4.8.0 的 WASM 后端只会整数：字符串只能在 `print` 的实参位置上出现，`and`/`or` 一律拒绝，
`break`/`continue` 直接报"不在子集内"。4.9.0 把四件事补上：

| 能力 | 4.8.0 | 4.9.0 | 实测证据 |
| --- | --- | --- | --- |
| `break` / `continue` | 拒绝 | `for` / `while` 都支持，可多层嵌套 | `bench/suite/flow1.impor`、`flow3.impor` |
| `and` / `or` 短路 | 拒绝 | 表达式层与条件层都是真短路（右边不被求值） | `flow2.impor`：`1 == 2 and (10 / 0) == 0` 不 trap |
| 浮点 `f64` | 拒绝 | 加减乘除、一元负、六种比较、`int → float` 隐式提升 | `bench/suite/wasm_f64.impor`、`bench/caps/02_float.impor` |
| 字符串运算 | 仅字面量 print | `+`（拼接）、`*`（重复）、`==` / `!=`、`len()`、`+=` | `bench/suite/wasm_str2.impor`、`bench/caps/03_str.impor` |

## 2. 值域约定：一个 i64 槽，三种解释

WASM 的 local/global 是有类型的，而 Impor 的变量类型来自推断而不是标注。为了不把同一份代码生成成三套，
4.9.0 定了一条约定：**所有值都放在 i64 里，解释方式由编译期推断决定**。

| 类型 | 槽里的内容 | 解码 |
| --- | --- | --- |
| `int` | 原样 i64 | — |
| `float` | `f64` 的位模式 | `f64.reinterpret_i64`（0xBF） |
| `str` | `(ptr << 32) \| len`，两段各 32 位 | 高 32 位取指针、低 32 位取长度 |

推断策略（`_Compiler.infer_scan`）是**保守**的，宁可拒绝也不猜错：

- 同名变量全文件必须同类型；`int` 可以提升进 `float` 槽（生成侧自动 `convert_i64_s`）。
- 用户函数签名按调用点实参 + `return` 表达式推断；函数先给一个「整数占位」让 `fib` 这类递归能在第一轮收敛，
  若真身是 `float`/`str` 允许一次性升级，再之后定型。
- 无法定类型的地方抛 `WasmUnsupported`，`impor wasm` 打印「WASM 后端不支持这段代码（第 N 行）」并退出码 3。

## 3. 浮点

指令映射：`f64.add 0xA0` / `sub 0xA1` / `mul 0xA2` / `div 0xA3` / `neg 0x9A`；比较 `0x61..0x66`；
`i64` 与 `f64` 互转用 `convert_i64_s 0xB9` + `reinterpret 0xBD/0xBF`。

打印口径对齐解释器（用新增的 `env.print_f64` 导入）：

- 整数值保留 `.0`（`3.0` 而不是 `3`）
- 其余用最短可逆表示（`1.0 / 3.0` → `0.3333333333333333`）
- 布尔结果打印 `True` / `False`（不是 `1` / `0`）

最后一条是这一轮抓到的一个真实口径差异：4.8.0 的 `print(a > b)` 在 WASM 里输出 `1`，解释器输出 `True`。
现在 `_print` 对比较 / `and` / `or` / `not` 表达式直接生成 `True` / `False` 两个字面量（它们在编译期登记进数据段）。

## 4. 字符串

- 字面量仍在数据段（`STR_BASE = 1024` 起），值以 `(ptr<<32)|len` 的形式出现在 i64 槽里。
- 运行期拼接/重复走 **bump 分配**：全局 `__heap`（全局索引 0，初值 `HEAP_BASE = 131072`），只增不减。
  内存 4 页（256 KiB），字面量区 + 堆合计够用；超出会 trap（不静默截断）。
- 三个 helper（索引 11/12/13）：
  - `str_concat`：`memory.copy`（`0xFC 0x0A 0x00 0x00`）+ bump，两段拼接
  - `str_repeat`：`str * int`，次数 ≤ 0 返回空串
  - `str_eq`：`la != lb` 先短路，再逐字节 `i32.load8_u 0x2D` 比较
- `len(s)` 不做函数调用，直接 `(v & 0xFFFFFFFF)` 掩出来。
- `s == "x"` / `s != "x"` 用 `str_eq`；其它比较运算符（`<`/`>`）明确拒绝（Impor 的字符串序语义在 C 后端也未定义）。

## 5. 控制流

### 5.1 break / continue

`self.ctrl` 是一个块嵌套栈，每开一层 `block`/`loop` 就压一个标记（`brk` / `cont` / `if` / `blk`）。
`br` 的立即数是**相对当前深度的层数**，所以生成时从栈顶往下数到最近的目标：

```python
def _br(self, tag):
    depth = 0
    for t in reversed(self.ctrl):
        if t == tag:
            return b"\x0c" + uleb(depth)
        depth += 1
    raise WasmUnsupported("break/continue 不在循环体内")
```

- `while`：`block(brk) + loop(cont)`
- `for`：`block(brk) + loop(blk) + block(cont)`，`continue` 的落点放在 body 之后、`i += step` 之前

### 5.2 短路

两层都要处理，缺一层就会"右边被无条件求值"：

- **表达式层**（`x = a and b`）：左值存临时变量 → `i64.ne 0` → `if(result i64)`，真则求右并转 0/1，假则常量。
- **条件层**（`if a and b`）：`if(result i32)` 直接短路，`and` 左真才求右、`or` 左假才求右。

条件层尤其重要：`if n != 0 and 10 / n > 1` 里若右边被无条件求值，`n == 0` 就会除零 trap。
`flow2.impor` 就是拿这个当断言（`1 == 2 and (10 / 0) == 0` 必须不 trap）。

## 6. 实测（4.9.0，Node v22.23.2）

`python tools/wasm_batch.py` 一次跑完 12 个用例，全部与解释器输出逐行一致：

```
MATCH       fib25.impor bytes=800
MATCH       sum2m.impor bytes=857
MATCH       mod1m.impor bytes=860
MATCH       nested1m.impor bytes=932
MATCH       wasm_str.impor bytes=875
MATCH       flow1.impor bytes=876
MATCH       flow2.impor bytes=875
MATCH       flow3.impor bytes=821
MATCH       wasm_f64.impor bytes=951
MATCH       wasm_str2.impor bytes=1051
MATCH       02_float.impor bytes=817
MATCH       03_str.impor bytes=861
TOTAL ok=12 fail=0
```

浏览器侧另有一份证据：`impor wasm bench/suite/wasm_f64.impor -o demo.wasm --html demo.html`
在真实 Chrome 里打开，页面渲染出的输出与 Node 完全一致（截图见交付报告）。

## 7. 仍然明确拒绝的

容器（list/dict）、类、闭包、异常、泛型、浮点取模 `%`、字符串的 `<`/`>` 比较、动态类型（同名变量换类型）。

这些都是**编译期明确拒绝**（退出码 3 + 行号），不是运行期静默算错。

## 8. 复现

```
python tools/wasm_build.py bench/suite/wasm_f64.impor out.wasm
node tools/wasm_run.js out.wasm "3.75
3.0
True
2.5
0.3333333333333333
-1.5"
python tools/wasm_batch.py                     # 12 个用例一次跑完
python -m impor wasm bench/suite/wasm_f64.impor -o demo.wasm --html demo.html
```
