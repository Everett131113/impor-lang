# Impor 生产化基线

本目录不是“已经生产可用”的证明，而是生产化的**可验收路线**与当前状态清单。  
任何一项只有通过对应验收标准，才能从 `TODO` 改为 `DONE`。

## 三阶段状态

| 阶段 | 目标 | 当前状态 |
|---|---|---|
| 1 原生后端与内存模型 | 脱离 TinyCC 依赖，能生成可部署的原生二进制 | 未完成：已有 C 后端/自举编译器；内存策略定为**自动管理**，不暴露手动 `free`；尚无 LLVM/Cranelift 后端 |
| 2 标准库与包生态 | 能写真实服务、能用成熟库 | 部分完成：已有 pkg 管理器、LSP、测试、格式化、JSON/正则等；缺 HTTP/TLS/SQL/连接池/中央注册表 |
| 3 治理与发布 | 多人协作、长期维护、安全响应 | 基线完成：本目录 + `SECURITY.md` + `CONTRIBUTING.md` + `CODEOWNERS` + `impor release-check` |

## 生产化的定义

只有同时满足以下条件，才允许对外称“生产可用”：

1. 一个真实服务连续运行 30 天，有监控、日志、回滚与故障演练记录。
2. 三端（解释器 / C 后端 / 自举编译器）过同一套 conformance，且差异有明确白名单。
3. 依赖有锁文件、版本约束、审计与撤回机制。
4. 破坏性变更走 RFC，至少一个版本的弃用期。
5. 有安全响应流程与已知问题清单。

## 发布门禁

```powershell
python Impor.py release-check
python Impor.py release-check --full --json release-report.json
```

快速门禁检查版本一致性、必备文件、conformance 用例数量；`--full` 追加 `doctor`、`conformance --ci` 和单元测试。

## 当前明确未完成项

- LLVM/Cranelift 原生后端、优化等级、调试信息、交叉编译。
- 内存模型（GC / 手动 / 区域分配）与热路径分配规则。
- 稳定 C ABI、结构体布局、回调、共享库导出。
- HTTP/TLS/SQL/连接池/迁移/指标/健康检查标准库。
- 中央包注册表、命名空间、签名、审计、撤回。
- 多线程、锁、原子、channel。
- LTS、SBOM、可复现构建、CVE 流程。
