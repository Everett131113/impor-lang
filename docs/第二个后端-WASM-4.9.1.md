# 第二个后端：WASM 4.9.1（列表 / 字典 / 类 / `have`）

> 适用：`impor/wasm.py`、`tools/wasm_build.py`、`tools/wasm_run.js`、`tools/wasm_batch.py`
> 上一版：`docs/第二个后端-WASM-4.9.0.md`（f64 / 字符串 / break-continue / 短路）

## 1. 这一版做了什么

| 能力 | 4.9.0 | 4.9.1 | 实测证据 |
| --- | --- | --- | --- |
| `list` | 拒绝 | 字面量、`len()`、下标读取 `a[i]`、下标赋值 `a[i] = v` | `bench/caps/04_list.impor` → `3 5` |
| `dict` | 拒绝 | 字面量（键必须是字符串字面量）、`d["k"]`、`len()` | `bench/caps/05_dict.impor` → `7 2` |
| `class` | 拒绝 | `def __init__` / `set self.x` / 方法调用 / `new P(...)` | `bench/caps/06_class.impor` → `7` |
| `have` | 不支持 | `x have int` 等类型判定（三后端都支持） | `bench/caps/11_have.impor` |

能力矩阵从 **3/10 → 6/10**（`04_list` / `05_dict` / `06_class` 转「支持」），仍**零「错误」格**。

## 2. 容器布局

沿用「所有值放 i64」的约定，容器本身是一个句柄：

| 类型 | 句柄 | 内存布局 |
| --- | --- | --- |
| `list` | `(ptr << 32) \| 元素个数` | 每元素 8 字节，`[e0, e1, …]` |
| `dict` | `(ptr << 32) \| 键值对数` | 每条 16 字节，`[(key, value), …]`，key 是「已打包的字符串值」 |
| 对象 | `(ptr << 32)` | 每字段 8 字节，顺序见 §3 |

- `len(x)` 仍然**不发调用**：容器句柄的低 32 位就是长度，`& 0xFFFFFFFF` 掩出来即可（字符串与容器同一套）。
- 元素类型在推断阶段定死（`_unify`）：全 `int` 得 `int`，`int`+`float` 混得 `float`，其余不一致就明确拒绝 —— 不允许静默按 int 存 float。
- 下标读走 helper，**越界和负下标一律 trap**（`unreachable`）。解释器这时抛 `IndexError`，两边都不会给一个错误的值。
- 字典的键查找是**线性扫描 + `str_eq`**，不是哈希表；`d["k"]` 未命中同样 trap（解释器抛 `KeyError`）。
- 字典键只接受字符串字面量（`{"k": 7}`）。变量键不做 —— 需要哈希与扩容，留给下一版。

## 3. 类

```
class P
    def __init__(x)
        set self.x = x
    def get()
        return self.x
var p = new P(7)
print(p.get())
```

- **对象是裸内存块**：字段集合在扫描阶段从 `__init__` 里所有 `set self.<字段>` 收集，顺序就是书写顺序；
  字段下标 = `8 * 顺序号`。没有运行期字段表，所以字段必须在 `__init__` 里出现。
- 字段类型在推断阶段按赋值表达式的类型定（默认 `int`）；`float`/`str`/容器字段都能存，存的仍是同一个 i64 槽。
- `new P(a)`：分配 → 字段清零 → 调 `__init__`（`self` 作为第 0 个实参）→ 丢弃返回值 → 返回句柄。
- 方法在索引空间里排在「顶层函数之后、`main` 之前」，`self` 是方法的局部 0；方法内局部变量的类型推断用**独立作用域**，
  不同方法的同名局部变量互不污染。
- **明确拒绝**：继承、接口、类体里的类属性、方法当值取用（`p.get` 不跟括号）、`print` 一个对象。
  理由一样：这些都需要运行期类型信息，WASM 后端里没有。

## 4. `have`

`x have T` 语法在比较层，所以 `if a have int:` 和 `a have int and b have str` 都成立。

| 后端 | 实现 | 口径 |
| --- | --- | --- |
| 解释器 | `visit_TypeTest` → `typename(v) == want` | 运行期真判；`bool` 独立（`1 have float` 假、`True have int` 假） |
| C 后端 | 运行库 `rt_have(Val, const char *)`，内部复用 `rt_type()` | 运行期真判，与解释器逐行一致 |
| WASM | 编译期折叠成常量 | 槽里没有运行期 tag，只能按推断类型判 |

WASM 侧的折叠是**精确**的：`int` 不冒充 `float`、`str` 不冒充 `int`、`list` 不冒充 `dict`，
类实例判 `p have P` / `p have object` 都对。唯一差别是「同一个变量先后是不同类型」——
那种写法在 WASM 后端里本来就会先报「变量必须类型稳定」，所以不会出现两种结论。

右边接受 `int / float / str / bool / list / dict / 任意类名 / object / instance`；未知类型名抛错，不静默给 `False`。

## 5. 实测（4.9.1，Node v22.23.2）

`python tools/wasm_batch.py` 现在 16 个用例：

```
MATCH       fib25.impor bytes=978
MATCH       sum2m.impor bytes=1035
MATCH       mod1m.impor bytes=1038
MATCH       nested1m.impor bytes=1110
MATCH       wasm_str.impor bytes=1053
MATCH       flow1.impor bytes=1054
MATCH       flow2.impor bytes=1053
MATCH       flow3.impor bytes=999
MATCH       wasm_f64.impor bytes=1129
MATCH       wasm_str2.impor bytes=1229
MATCH       02_float.impor bytes=995
MATCH       03_str.impor bytes=1039
MATCH       04_list.impor bytes=1031
MATCH       05_dict.impor bytes=1057
MATCH       06_class.impor bytes=1023
MATCH       11_have.impor bytes=1291
TOTAL ok=16 fail=0
```

`11_have.impor` 在 WASM 与解释器的输出完全相同：

```
3
False
True
True
True
True 7 True
```

## 6. 仍然明确拒绝的

闭包、泛型、异常、`int()` / `str()` 转换、浮点取模 `%`、字符串的 `<`/`>` 比较、继承/接口/类属性、
`print` 容器或对象、字典的非字面量键。

这些都是**编译期明确拒绝**（行号 + 原因），不是运行期静默算错。

已写进文档的限制：bump 分配只增不减，1 MiB 堆（16 页）；长循环里反复建容器会耗尽并 trap。

## 7. 复现

```
python tools/wasm_build.py bench/caps/04_list.impor out.wasm
node tools/wasm_run.js out.wasm "3 5"
python tools/wasm_build.py bench/caps/05_dict.impor out.wasm
node tools/wasm_run.js out.wasm "7 2"
python tools/wasm_build.py bench/caps/06_class.impor out.wasm
node tools/wasm_run.js out.wasm "7"
python tools/wasm_build.py bench/caps/11_have.impor out.wasm
node tools/wasm_run.js out.wasm "3
False
True
True
True
True 7 True"
python tools/wasm_batch.py                     # 16 个用例一次跑完
```
