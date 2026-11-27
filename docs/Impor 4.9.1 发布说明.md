# Impor 4.9.1 发布说明

> 基线：4.9.0（WASM 的 f64 / 字符串 / break-continue / 短路 + 浏览器演示页）
> 本版两件事：**WASM 能力矩阵 3/10 → 6/10**、**新增 `have` 类型判定语法**。

## 一、WASM 后端补到 6/10

| 探针 | 4.9.0 | 4.9.1 | 输出 |
| --- | --- | --- | --- |
| `04_list` | 拒绝 | 支持 | `3 5` |
| `05_dict` | 拒绝 | 支持 | `7 2` |
| `06_class` | 拒绝 | 支持 | `7` |

- **列表**：字面量、`len()`、`a[i]` 读取、`a[i] = v` 写入。句柄 `(ptr<<32)|元素个数`，每元素 8 字节。
- **字典**：字面量（键为字符串字面量）、`d["k"]`、`len()`。每条 16 字节「键值对」，查找是线性扫描 + 已有的 `str_eq`。
- **类**：`class` / `def __init__` / `set self.x` / 方法调用 / `new P(...)`。对象是裸内存块，字段顺序按 `__init__` 里 `set self.x` 的书写顺序定死，方法排在「顶层函数之后、main 之前」，`self` 是方法局部 0。
- 新增 3 个 helper：`alloc`（bump，索引 14）、`list_get`（15）、`dict_get`（16）；内存从 4 页提到 16 页（1 MiB）。
- 边界一律 **trap**：越界/负下标/字典未命中。解释器在这些情况下抛异常，两边都不会返回一个错误的值。
- 明确拒绝：继承、接口、类属性、`print` 容器或对象、字典的非字面量键、方法当值取。

## 二、新语法：`x have <类型名>`

```
var a = 3
if a have int:
    print(a)          # → 3
```

- 右侧接受 `int / float / str / bool / list / dict / 任意类名 / object / instance`；未知类型名直接报错。
- 语法挂在比较层，所以 `a have int and b have str`、`(a have int) == True` 都成立。
- **软关键字**：不进 `KEYWORDS`，`var have = 1` 照旧可用（有单测钉住）。
- 三后端：
  - 解释器：运行期真判（与 `typename()` 同口径，`bool` 独立：`1 have float` 假、`True have int` 假）。
  - C 后端：运行库 `rt_have()` 复用 `rt_type()`，真运行期判定，与解释器逐行一致。
  - WASM：编译期折叠。槽里没有运行期 tag —— 但类型是精确推断的，结论与解释器一致；「同变量换类型」的写法本来就先被拒绝。

## 三、实测

| 项 | 结果 |
| --- | --- |
| `tools/wasm_batch.py`（16 用例） | **16/16 MATCH** |
| `tools/capability_gate.py --record` | 解释器 11/11、C 后端 11/11、**WASM 6/11**（其余 5 项明确拒绝），**零「错误」格** |
| `tests/test_v491.py`（新增 8 项） | 8/8 OK |
| `impor check bench/caps/11_have.impor` | 语法/导入/lint/类型四项 PASS（格式 WARN，探针不追求 `fmt`） |
| `impor wasm bench/caps/06_class.impor --html` | 1023 字节 / 函数 14 / 全局 2，演示页生成成功 |

## 四、未做（如实列出）

1. WASM 仍拒绝闭包、泛型、异常、`int()` / `str()` 转换（矩阵里是「明确拒绝」，不是静默降级）。
2. WASM 容器没有回收：bump 只增不减，长循环里反复建容器会在 1 MiB 堆上耗尽并 trap。
3. 字典键只支持字符串字面量；变量键需要哈希与扩容，未做。
4. 4.9.0 的两项未做保持不变：8 字节 NaN-boxing 本体（Val 仍是 16 字节 tagged union）、Linux/macOS 运行证据（本机无 WSL/docker/qemu/wine）。
