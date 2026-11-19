# CI 证据（2026-11-19）

仓库当前为 **private**：匿名 `api.github.com`、shields 徽章、Actions 运行页对外均不可访问。
以下记录来自仓库主账号在 GitHub Actions 中看到的页面，截图同目录。

## CI #15（当前提交 `2bed78c`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44418086033
- 标题: `fix: five-minute check runs on PowerShell 5.1 (BOM + sampled conformance); honest CI evidence for private repo`
- 结论: `success`，8m14s，4 个 job 成功
- 截图: `ci-2026-11-19-ci15.png`

## C ABI matrix #15（当前提交 `2bed78c`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44418085903
- 结论: `success`
- 四格定义与 #14 相同：MSVC x64 / GCC x64 / TCC win32 / GCC linux-arm64

## 基线 CI #14 / C ABI #14（提交 `aa78f10`）

- Run: https://github.com/Everett131113/impor-lang/actions/runs/44343129880
- 标题: `ci: macOS C ABI optional workflow, push gate = 4 verified grids`
- 结论: `success`，4 个 job 成功
- Job:
  - Windows py3.11 full — success
  - Windows py3.12 full — success
  - Ubuntu py3.11 smoke — success
  - Ubuntu py3.12 smoke — success
- 截图: `ci-2026-11-19-ci14.png`

- C ABI Run: https://github.com/Everett131113/impor-lang/actions/runs/44343130120
- 结论: `success`
- Job:
  - MSVC x64 (windows-latest) — success
  - GCC x64 (ubuntu-latest) — success
  - TCC win32 (windows-latest) — success
  - GCC linux-arm64 (ubuntu-latest) — success
- 截图: `ci-2026-11-19-cabi14.png`

## macOS 两格

`c-abi-macos.yml` 为 `workflow_dispatch` 手动触发。当前账号 macOS runner 额度不可用，
`macos-13` 已退役、`macos-14` 解析失败、`macos-15-intel` 排队后被取消。因此不计入 push 门禁，
也不对外宣称已通过。

## 对外口径

仓库保持 private 时，只能写：

> CI 四格已定义，运行记录见仓库 Actions（私有），截图见 `docs/`。

仓库转为 public 后，才可以使用 shields / Actions 徽章作为外部可核验证据。
