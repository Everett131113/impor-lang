# 承诺验收表

> 本表由 `python tools/verify_claims.py` 生成，不要手改。
> 每条承诺对应一个可执行 check；auto 项失败会让 CI 变红。

- 生成时间：2026-11-24 07:48
- 构建标识：`package`
- **当前构建：5.6.0 · 保留字 55 · 内建 124 · 标准库 37 · 错误码 30 · 全仓 .impor 237/237 通过（另有 21 个基线/生成文件按规则跳过）**
- **自动验证：22/22（100%）**
- 人工验收：2 项（未完成前不计入兑现率）

| 结果 | 承诺 | 状态 | 验证方式 |
|---|---|---|---|
| ✅ | 命令行版本号与 VERSION 文件一致 | auto | `verify_release.py --check version` |
| ✅ | 全仓 .impor 已格式化（文件系统扫描，不依赖 Git） | auto | `verify_release.py --check fmt_clean` |
| ✅ | 解压后双击示例 1，能看到「你好，Impor」 | auto | `verify_release.py --check quickstart_hello` |
| ✅ | 示例 2 能读取 sample.txt 并写回副本 | auto | `verify_release.py --check quickstart_read_file` |
| ✅ | 示例 3 在 Windows 上能调用系统 DLL（winsqlite3） | auto | `verify_release.py --check quickstart_dll` |
| ✅ | 单文件 exe 不依赖 Python 可运行，体积 ≤ 320 KB（patch8 起：完整 Val 运行库含浮点 repr，静态链接 strtod 约 +100 KB） | auto | `verify_release.py --check exe` |
| ✅ | C ABI 构建产出 .dll / .h / .def 三个文件 | auto | `verify_release.py --check c_abi` |
| ✅ | std.json / fs / path / regex / time 在解释器下全部可用 | auto | `verify_release.py --check stdlib_interp` |
| ✅ | std.http 能在解释器下完成一次 GET | auto | `verify_release.py --check stdlib_http` |
| ✅ | 六个 std 模块在 C 后端 exe 中同样可用 | auto | `verify_release.py --check stdlib_c` |
| ✅ | 错误信息包含位置 / 原因 / 修复 / 错误码 / 文档锚点五项 | auto | `verify_release.py --check errors_actionable` |
| ✅ | 每个错误码都有可跳转的文档锚点 | auto | `verify_release.py --check error_docs` |
| ✅ | 已发布的 API 面被 api_baseline.json 冻结，漂移即失败 | auto | `verify_release.py --check api_frozen` |
| ✅ | 3.11 行为基线的 20 个公开示例在 4.0 全部通过 | auto | `verify_release.py --check compat_20` |
| ✅ | 所有保留字都真正保留，没有一个是静默接受 | auto | `verify_release.py --check keywords` |
| ✅ | Windows 便携版 ImporPortable.exe 存在且可分发 | auto | `verify_release.py --check portable` |
| ✅ | START_HERE、常见问题与三个双击示例都在包内 | auto | `verify_release.py --check entry_files` |
| ✅ | defer 块写法可用，非块写法明确拒绝 | auto | `verify_release.py --check defer` |
| ✅ | async / await 明确拒绝，不静默接受 | auto | `verify_release.py --check reject_async` |
| ✅ | apps/impor_ssg 能用 Impor 生成 4 页静态站点 | auto | `verify_release.py --check app_ssg` |
| ✅ | apps/impor_action 的 Impor exe 能在 quickstart 上通关 | auto | `verify_release.py --check app_action` |
| ✅ | apps/impor_sqlite_report 处理 10 万行 CSV，峰值内存 < 50 MB | auto | `verify_release.py --check app_sqlite` |
| ⏳ | 陌生人从解压到看到输出 < 180 秒，不装任何东西 | manual | 找一位没用过 Impor 的人，从解压 ZIP 开始计时，到看到「你好，Impor」停止；记录秒数与卡住位置。 |
| ⏳ | 前 10 个真实用户能独立跑通 quickstart，不需要作者解释 | manual | 让用户只看 START_HERE.txt 操作；记录是否需要额外解释、卡在哪一步。 |

## 怎么复现

```bash
python tools/verify_claims.py
python tools/verify_release.py
```

新增承诺时：先在 `tools/verify_release.py` 加 check，再在 `docs/claims.yaml` 加条目；
只写文案不加验证的承诺不算数。
