# Impor 标准库真实清单（原文「700 个微库」已撤回）

> **更正声明（2026-11-22）**
> 本文件原文宣称 3.1.0 起提供 `std.atlas` 扩展包、共 700 个独立模块，并给出 `tests/atlas_700_smoke.impor` 的全量冒烟证据。
> **这条不成立。** 在 4.0.0 发布包内实测：
>
> - `impor/std/atlas/` —— 不存在
> - `tests/atlas_700_smoke.impor` —— 不存在
> - `impor/` 下全部 `.py` 与全部 `.impor` 源码里 `atlas` 字样 —— 命中 0
>
> 也就是说没有任何一条加载路径能 `import std.atlas`。原文的「700/700 全部导入并调用」「700 个文件 SHA256 全不同」两条验收描述因此无法复现，一并撤回。
> 凡看到旧文档、旧官网页面写 700 的地方，以本文件为准。

## 现在真的有什么

`impor/std/` 下 17 个文件：1 个包入口 + 16 个可直接 `import` 的模块，全是纯 Impor 源码，合计 114 个导出函数。

| 模块 | 导出函数 | 说明 |
| --- | --- | --- |
| `std.text` | 18 | split / join / trim / upper / lower / replace / contains / starts_with / ends_with / char_at / substring / repeat / pad_left / pad_right / index_of / count_sub / split_lines / is_blank |
| `std.collections` | 14 | map / filter / reduce / reverse / contains / count / uniq / first / last / find / find_index / take / drop / flatten |
| `std.atomic` | 11 | counter / load / store / add / sub / exchange / cas / lock / enter / leave / try_enter / with_lock |
| `std.mathx` | 9 | gcd / lcm / fact / fib / is_prime / sum / avg / min_of / max_of |
| `std.fs` | 9 | read / write / exists / list / mkdir / move / size / read_json / write_json |
| `std.path` | 7 | norm / is_abs / join / basename / dirname / ext / stem |
| `std.thread` | 7 | spawn / join / alive / current / cpus / map_parallel / range_parallel |
| `std.algo` | 6 | sorted / sorted_desc / index_of / bsearch / min_index / max_index |
| `std.stats` | 6 | mean / median / variance / stddev / mode / percentile |
| `std.random` | 5 | between / choice / shuffled / pick / token |
| `std.csv` | 5 | parse_line / parse / join_row / stringify / column |
| `std.sort` | 4 | sort / sort_by / search / is_sorted |
| `std.regex` | 4 | is_match / find_all / replace_all / contains |
| `std.time` | 4 | readable / seconds / sleep_ms / elapsed |
| `std.json` | 3 | parse / stringify / try_parse |
| `std.http` | 2 | get / get_json |

## 用法（只写实测能跑的形式）

```impor
import std.text as text
print(text.trim("  ok  "))
```

```impor
use std          // 注入全部 16 个子模块（见 impor/std/__pkg__.impor 的 export 清单）
```

## 没有的东西

- 没有包仓库：`impor pkg` 只能装本地源码目录或本地 zip；`impor pkg search` 只扫本地（缓存 + 项目 `*.zip`）。
- 没有 `std.atlas`、没有 700 微库、没有 `CATALOG.md`（原文提到的目录同样不存在）。
- 没有协程 / channel / 泛型；`yield` 关键字保留但**明确报错** `E2001`（未实现），不会静默接受。
- 线程有了（`std.thread` + `std.atomic`），但只有「起线程 / join / 锁 / 原子量」这一层，没有调度器与取消。

## 怎么自己核对

```bat
cd "Impor 4.0.0"
dir impor\std
python Impor.py run examples\std_demo.impor --no-native
```
