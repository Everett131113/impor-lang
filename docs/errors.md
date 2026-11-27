# Impor 错误码手册

每个错误码都是稳定契约：编号不复用、含义不改变。报错最后一行会给出
`文档：docs/errors.md#E1001`，用浏览器或编辑器搜索该锚点即可看到本页对应条目。

## 错误输出必须包含的 5 项

1. 位置：`第 3 行 列 5`
2. 人话原因：`为什么：...`
3. 怎么修：`修复：...`
4. 稳定错误码：`E1009`
5. 文档锚点：`docs/errors.md#E1009`

`tools/error_audit.py` 用 20 条真实 CLI 探针逐项检查；任何一项缺失都会让审计失败。

## 运行错误 E1xxx

| 错误码 | 异常 | 人话标题 | 典型触发 |
|---|---|---|---|
| E1001 | ZeroDivisionError | 除数为 0 | `10 / 0` |
| E1002 | TypeError | 类型不匹配 | `1 + "a"` |
| E1003 | IndexError | 下标越界 | `a[5]`（`a` 只有 2 项） |
| E1004 | KeyError | 字典键不存在 | `d["b"]` |
| E1005 | ValueError | 值不合法 | `num("abc")` |
| E1006 | AttributeError | 属性/方法不存在 | `1.nope` |
| E1007 | AssertionError | 断言失败 | `expect(1, 2)` |
| E1008 | NameError | 名字未定义 | `print(nope)` |
| E1009 | ConstError | 修改了 const | `const A = 1; A = 2` |
| E1010 | ImportError | 模块/名字导入失败 | `import no_such_mod` |
| E1011 | RecursionError | 递归过深 | 无终止条件的递归 |
| E1012 | OverflowError | 整数溢出 | 超过 64 位有符号范围 |
| E1013 | MemoryError | 内存不足 | 一次申请超大列表 |
| E1014 | ContractError | 接口契约不满足 | `implements` 后缺方法 |
| E1015 | ModuleNotFound | 模块文件不存在 | `import no_such_mod`，且错误对象是 ModuleNotFound |
| E1099 | RuntimeError | 未分类运行错误 | 其他运行时错误 |

## 语法/编译错误 E2xxx

| 错误码 | 人话标题 | 典型触发 | 正确写法 |
|---|---|---|---|
| E2001 | 未实现的关键字 | `async def f()`、`await g()`、`defer print("d")` | 删除该写法；`defer` 只支持 `defer { ... }` |
| E2002 | 无法解析的记号 / 期望其他记号 | 漏逗号、括号错位、关键字拼错 | 按提示补齐记号 |
| E2003 | 表达式缺失 | `var x =` 后直接换行 | `var x = 0` |
| E2004 | 括号、引号或代码块未闭合 | 少 `}`、`)` 或结束引号 | 补齐闭合符 |
| E2005 | 缩进层级不一致 | 空格与 Tab 混用 | 同一代码块统一 4 空格 |
| E2006 | 赋值左侧不可写 | `1 = x` | 左边写变量、下标或属性 |
| E2007 | 默认参数 / 变参写法不支持 | `func f(x = 1)` | 在函数体内给默认值，或调用处显式传参 |
| E2099 | 其他语法错误 | 未分类的解析失败 | 看「修复」一行的具体建议 |

## 资源闸门 E102x（仅稳定模式）

只在 `--stable`（或 `IMPOR_STABLE=1`）下产生。不开稳定模式的脚本永远不会看到这几个码——它们是**执行预算**，不是语法错误。

| 错误码 | 异常 | 人话标题 | 典型触发 | 怎么修 |
|---|---|---|---|---|
| E1020 | StepLimit | 语句条数超预算 | `while true` 空转 | 加终止条件；确需长任务用 `--max-steps` 放宽 |
| E1021 | TimeLimit | 执行时间超预算 | `sleep` 卡住、外调不返回 | 缩短等待或拆分；用 `--max-seconds` 放宽 |
| E1022 | MemoryLimit | 内存超预算 | 循环里不停 `arrappend` 大数组 | 流式处理、及时释放；用 `--max-memory` 放宽 |
| E1023 | OutputLimit | 输出超预算 | 循环里 `print` 刷屏 | 改条件打印或写文件；用 `--max-output` 放宽 |
| E1024 | AllocLimit | 单次分配超预算 | `arrfill(50000000, 0)`、`"x" * n` | 分批分配；用 `--max-alloc` 放宽 |

这五个码都在**真正分配/写出之前**判定，不会等到系统 OOM 或被磁盘拒绝。设计与实测见 [`docs/稳定-4.0.md`](稳定-4.0.md)。

## 设计约束

- **E2xxx 在运行前产生**：不会出现“先跑一半再报语法错”。
- **不静默降级**：未实现的关键字不会退化成普通标识符或一次函数调用。
- **新增错误码只能追加**：不得改动已有编号的含义；变更走 `docs/rfc/`。

## 快速定位

```bash
impor explain E1009      # 查运行错误码
impor explain E2001      # 查语法错误码
```

机器可读清单：`tools/api_baseline.py` 生成的 `api_baseline.json` 中的 `error_codes`。

## 错误码锚点

诊断末尾的 `docs/errors.md#E1009` 直接跳到对应锚点。

<a id="E1001"></a>
<a id="E1002"></a>
<a id="E1003"></a>
<a id="E1004"></a>
<a id="E1005"></a>
<a id="E1006"></a>
<a id="E1007"></a>
<a id="E1008"></a>
<a id="E1009"></a>
<a id="E1010"></a>
<a id="E1011"></a>
<a id="E1012"></a>
<a id="E1013"></a>
<a id="E1014"></a>
<a id="E1015"></a>
<a id="E1020"></a>
<a id="E1021"></a>
<a id="E1022"></a>
<a id="E1023"></a>
<a id="E1024"></a>
<a id="E1099"></a>
<a id="E2001"></a>
<a id="E2002"></a>
<a id="E2003"></a>
<a id="E2004"></a>
<a id="E2005"></a>
<a id="E2006"></a>
<a id="E2007"></a>
<a id="E2099"></a>
