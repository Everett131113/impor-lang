# RFC 0005：整数语义单一档（int64）+ `check-int` 命令

- 状态：Accepted（4.6.0 落地）
- 关联：`docs/整数语义.md`、`CHANGELOG.md` 4.6.0、`tools/api_baseline.json`

## 背景

4.5.0 的整数口径是"两档并存"：

- 解释器默认按 int64 运算，`--big` 可切到任意精度（Python 语义）；
- C 后端（`ctrans` / `cfast` / `ir-c`）只有 int64，超出即运行时报 `E1012`；
- `impor build FILE --big` **接受这个参数但不改变产物语义**，属于静默降级。

结果是同一个程序在 `impor run --big` 与 `impor build` 下可以有不同的数值行为，
而使用者从命令上看不出来。

## 变更

1. **交付层只保留 int64 一档。**
2. `impor run FILE --big` / `--bignum` 仍然可用（解释器调试档），但会在 stderr 打一行提示：
   "仅解释器可用的调试档……要判定能否按 int64 交付用 `impor check-int`"。
3. `impor build FILE --big` **明确拒绝**，退出码 `EXIT_USAGE(3)`，不再静默降级。
4. **新增 CLI 命令** `impor check-int FILE [--json]`：
   - 静态扫描字面量，判断"能否按 int64 交付"；
   - 逐文件 PASS/FAIL，退出码 0（全部通过）/ 1（有越界）/ 3（用法错误）；
   - `--json` 输出 `{version, files, literals, min, max, violations}`；
   - 一元负号按组合判定：`-9223372036854775808` 合法，`-9223372036854775809` 非法。
5. 四端 E1012 文案对齐：C 运行时补算子名（加法/减法/乘法/取负），
   `rt_diag_why` / `rt_diag_fix` 与解释器逐字一致；`cir` 的超界常量使用同一措辞。

## 兼容性

- **新增** `check-int` 命令：只增不删，不影响既有脚本。
- **收紧** `build --big`：此前该参数被接受但无效果，属"静默错误"；现在显式拒绝。
  依赖它的构建脚本会得到退出码 3 与明确提示，而不是一个语义不符的 exe。
- **不变**：默认 `impor run` 已是 int64；C 后端行为未变。

## 未解决

- `check-int` 只看字面量，运行时溢出仍由运行时报 `E1012`（例如 `a * b` 越界）。
- C 侧不提供 bignum；要任意精度只能用解释器 `--big`（不可交付）。

## 证据

- `%TEMP%\irverify\intcases\*.impor`、`intA.log` / `intA2.log` / `intA3.log`
- 四端实测：解释器 / `build --fast` / `build --no-fast` / `ir --build` 对同一用例
  报同一句 `…（加法）[E1012]`，why/fix 逐字相同，只有外层头部与退出码不同。
