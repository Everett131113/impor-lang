# Impor (I++) Conformance 三后端一致性测试

同一份 `.impor` 脚本，分别由三条执行路径运行，要求 **stdout 逐字节一致**：

| 后端 | 命令 | 说明 |
| --- | --- | --- |
| 解释器 | `python Impor.py run case.impor` | Python 实现，语言语义基准 |
| C 后端 | `python Impor.py build case.impor -o out.exe` | `ctrans.py` 生成 C，`tcc` 编译 |
| 自举编译器 | `impor/imporc.exe build case.impor -o out.exe` | 参数经 `imporc_args.txt` 传递 |

任何一处输出不同，脚本即以非零码退出，并在报告中给出首个差异行。

## 运行

```bash
python conformance/run_conformance.py          # 全部用例，打印逐条结果
python conformance/run_conformance.py --ci     # CI 模式：有不一致则 exit 1
python conformance/run_conformance.py --only 05
python conformance/run_conformance.py --json conformance/report.json
```

Windows 环境需要 `impor/tcc/win32/tcc.exe` 与 `impor/imporc.exe`（仓库自带）。
`tests/test_conformance.py` 会把全量比对挂到 `python run_tests.py` / GitHub Actions 上；
非 Windows 平台自动跳过。

## 用例清单（100 个，全部位于三后端公共子集）

| 编号 | 覆盖点 |
| --- | --- |
| 01 | 整数四则与优先级 |
| 02 | 浮点运算与浮点除法 |
| 03 | 字符串拼接 |
| 04 | ASCII 字符串 `len`（含空串） |
| 05 | Unicode 字符串 `len` / 下标 / `strcut`（按字符，非字节） |
| 06 | `1 == true` 等布尔与数值比较 |
| 07 | 字符串重复 `"ab" * 3`（含 0 次） |
| 08 | `for i in range(n)` |
| 09 | `while` 累加 |
| 10 | `if / else` |
| 11 | 函数定义与嵌套调用 |
| 12 | 递归（阶乘） |
| 13 | 数组字面量、`len`、下标、遍历求和 |
| 14 | `set arr[i] = v` 下标赋值 |
| 15 | 多层嵌套调用 |
| 16 | 字符串比较与相等 |
| 17 | `div()` 整除与 `%` 取模 |
| 18 | `type()` 结果 |
| 19 | `print` 多参数分隔 |
| 20 | 综合（斐波那契 + 字符串累加 + `to_str`） |
| 21 | `struct` 字段记录（构造传参 + 字段读写） |
| 22 | `enum` 枚举（自动递增 + 显式值 + 成员比较） |
| 23 | `switch` / `match`（enum 常量、`case Color.MEMBER`、绑定模式 `case v`、负数、`default`） |

24–100 是 3.6.17 新增的参数化扩展：算术 7、字符串 7、循环 7、递归函数 7、数组 7、struct 7、enum 7、switch 7、布尔比较 7、type/to_str 7、综合迭代 7。生成器见 `tools/gen_conformance.py`，只使用三后端已支持的公共子集；新增用例必须先在 `run_conformance.py --only <编号>` 下三端逐字节通过，才允许留在 `cases/`。

发版门禁：`tests/test_conformance.py` 要求用例数 ≥100 且 `run_conformance.py --ci` 退出码为 0；`run_tests.py` 与 `.github/workflows/ci.yml` 的 Windows job 都会执行这条门禁。

## 限制

- 用例刻意避开三后端的已知差异区：`try / catch`、自定义异常、复合赋值 `+=`（imporc 尚不支持）；`imporc` 的 `switch` 暂不支持**嵌套 switch**（`case` 体里再写 `switch`），嵌套场景请用解释器或 C 后端。
- **整数在四条路径上统一为 int64**（v4.3.2 口径）：C 后端、数值快路径、默认解释器、`--int64` 自检模式越界一律报 `E1012`，不静默回绕。任意精度只剩一个入口：`impor run FILE.impor --big`（C 产物与自适应快路径不提供大整数）。conformance 的 100 个用例本身不覆盖溢出边界，溢出由 `tests/test_v4_backend_parity.py` 与 `run` 的 E1012 诊断锁定。
- 浮点格式化由各自运行时决定，用例只使用能稳定一致的形式。
- **类型注解错误是负向诊断，不进本 runner**（这里只比三端成功 stdout）；由 `tests/test_annotation_gate.py` 的 20 条正反用例锁定，见 `docs/类型注解边界.md`。
- **路径含空格**：`imporc_args.txt` 按空格切分参数，脚本/输出路径必须用双引号包裹（`build "D:\My Dir\a.impor" -o "D:\My Dir\out.exe"`）。v3.3 起 imporc 支持引号；本 runner 已按此格式写入。
