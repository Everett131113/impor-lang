# Impor 4.8.0 发布说明

> 一句话：这一版不加新语法，把「声称」变成「可回归」，并补齐了第二个后端。

上一轮三方实测（Impor / XLang / 灵语言）暴露了四件事：性能数字没有门禁、首次编译 1.46 s 的体感断崖、只有一个能出产的 C 后端、能力边界只写在文档里。4.8.0 逐条收口，四条都落到了文件。

## ① 性能变成门禁，不再是宣称

`tools/bench_gate.py` 把四个基准用例跑成基线，两个口径分别记：

| 口径 | fib25 | sum2m | mod1m | nested1m |
|------|-------|-------|-------|----------|
| `interp`（树遍历解释器，`run --no-native`） | 2.3905 s | 4.5133 s | 3.1627 s | 2.3983 s |
| `native`（C 后端原生 exe，gcc 4.8.1 `-O2` + LTO） | 0.0164 s | 0.0166 s | 0.0164 s | 0.0162 s |

基线存 `bench/baseline.json`，默认阈值 30%：

```bash
python tools/bench_gate.py --record            # 采集基线（写 bench/baseline.json）
python tools/bench_gate.py --check             # 超阈值 / 输出不符 / 跑崩 → exit 1
python tools/bench_gate.py --check --limit 0.2 --runs 5   # 收紧阈值与轮数
```

判断依据不是「跑得快不快」，是**输出对不对 + 有没有比基线慢过 30%**。输出错、进程崩、找不到用例，一律 exit 1——这条比时间更硬。

- **编译器身份进基线**：`baseline.json` 里记 `cc`（本机为 `gcc:gcc.EXE (tdm64-2) 4.8.1`）。没装 x86_64 gcc 时 `impor build` 会退回 32 位 tcc，同一用例实测 0.0354 s vs 0.0224 s——那不是代码退化，是环境不同。所以 `--check` 在编译器身份不符时把 native 口径判为 `SKIP(cc)`，interp 口径照常比；**不会假红，也不会假装通过**。这条是实测踩出来的：在安装目录（PATH 里没有 gcc）第一次跑门禁就报 1.58× REGRESS。

## ② 首次编译的断崖被拆成两级

以前 `impor run` 第一次跑：源码 → C → `gcc -O2 -flto` → exe，实测 **1.46 s**，第一次运行的体感就是这么来的。

4.8.0 改成两级档位（`impor/cli.py` 的 `_native_product`）：

1. 缓存里没有产物时，先编 **`-O1`、关 LTO** 的快速档，立刻能跑（**1.056 s**，省 28%）；
2. 同时把 `.c` 留给后台进程 `impor/native_upgrade.py`，由它去编 `-O2/-O3` + LTO 的满档；
3. 后台编译完成用 `os.replace` 原子替换；目标 exe 被占用时最多重试 6 次 × 0.5 s，失败静默退出（下一次运行再补）；
4. 第二次运行直接命中满档：**0.511 s**。

磁盘上同时存在 `<key(opt=1)>.exe` 与 `<key(opt=原opt)>.exe`，运行优先取满档。`tcc` 与显式 `-O0/-O1` 仍走单级——不假装它们有 LTO。

## ③ 第二个后端：WebAssembly

`impor wasm FILE.impor -o OUT.wasm`，手写 WASM 二进制（无 LLVM / 无 wat2wasm / 无 C 工具链），产物可直接在浏览器或 Node 里跑。四个数值基准 + 字符串用例全部与解释器输出一致，最小 482 B。

细节、能力边界、实测耗时见 `docs/第二个后端-WASM-4.8.0.md`。

## ④ 能力矩阵进 CI

`tools/capability_gate.py` + `bench/caps/*.impor` 十个探针，三后端逐格断言，把「支持 / 明确拒绝 / 算错了」三种情况分开：

- `支持`：跑通且输出与期望逐字一致；
- `拒绝（明确）`：后端在编译期报错（带行列号与原因）；
- `错误`：跑起来了但结果不对——**必须为 0 格才允许发版**。

当前结果：解释器 10/10 支持，C 后端 10/10 支持，WASM 后端 1/10 支持（int64 数值快路径），其余 9 格全部是「明确拒绝」，没有一格是「错误」。矩阵落在 `docs/CAPABILITIES.md`（人看）与 `docs/CAPABILITIES.json`（机读），`--check` 任何一格变化即 exit 1，防止能力悄悄回退或文档悄悄吹牛。

```bash
python tools/capability_gate.py --check     # CI 用
python tools/capability_gate.py --show      # 只看当前实测
```

## 新增文件

| 文件 | 作用 |
|------|------|
| `impor/wasm.py` | WASM 后端（手写二进制编码） |
| `impor/native_upgrade.py` | 后台满档编译助手 |
| `tools/bench_gate.py` | 性能门禁（双口径 + 30% 阈值） |
| `tools/capability_gate.py` | 能力矩阵门禁（三后端 × 十探针） |
| `tools/wasm_build.py`、`tools/wasm_run.js` | wasm 构建/运行调试入口 |
| `bench/suite/*.impor`、`bench/baseline.json` | 性能基准集与基线 |
| `bench/caps/*.impor` | 能力探针 |
| `docs/CAPABILITIES.md`、`docs/CAPABILITIES.json` | 能力矩阵（自动生成） |
| `docs/第二个后端-WASM-4.8.0.md` | WASM 后端设计与边界 |

## 验证

| 项 | 命令 | 结果 |
|----|------|------|
| 10 项回归 | `python D:\ImporWork\regress480.py` | **10/10 PASS**：parity 15.2 s / fmt 0.5 s / version 0.2 s / editor 11.8 s / release 110.2 s（承诺 22/22）/ api 0.2 s / types 0.6 s / ircover 118.2 s（不一致 0）/ jitter 5.6 s（10 轮同哈希）/ conformance 616.1 s（三后端逐字节一致） |
| 性能门禁（空闲机） | `python tools/bench_gate.py --check` | 8 个口径全 OK，比值 0.98–1.06（阈值 +30%） |
| 能力矩阵 | `python tools/capability_gate.py --check` | 与入库矩阵逐格一致，零「错误」格 |
| 打包 | `python tools/pack_release.py` | 产出 `Impor-4.8.0-Windows-x64.zip`（条目数 / 字节数 / SHA256 由脚本打印；每改一次包内容 SHA 就会变，故不写死） |
| 安装 | `python tools\install480.py` | zip 845 = 目录 845，missing 0 / extra 0，VERSION 4.8.0 |

回归未覆盖到、如实标注的部分：WASM 后端只有 Node 验证（本机无浏览器自动化）、WASM 的 f64 与字符串运算未实现、跨平台（Linux/macOS）运行证据仍缺。
