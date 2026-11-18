# 发布到 VS Code Marketplace

本目录已是可发布的扩展工程：`package.json` 含 `publisher`、`version`、`license`、`icon`、
`keywords`、`galleryBanner`、`categories`、`engines`；同级含 `README.md`、`LICENSE.txt`、
`CHANGELOG.md`、`icon.png`、`.vscodeignore`。

## 0. 已生成的本地安装包

```powershell
cd vscode-impor
npx --yes @vscode/vsce package
# => vscode-impor-4.2.0.vsix
code --install-extension .\vscode-impor-4.2.0.vsix
```

VSIX 不走市场，可直接分发给内部用户；升级时安装同 publisher/name 的新 VSIX 会覆盖旧版本。

## 1. 前置条件（只需一次）

1. 注册 [Azure DevOps](https://dev.azure.com) 账号，创建 Personal Access Token：
   - Scope 选 **Marketplace → Manage**（至少 `Acquire` + `Manage`）。
   - 复制 PAT，保存到密码管理器，不要提交到仓库。
2. 创建 publisher（ID 必须与 `package.json` 的 `publisher` 一致；本工程为 `impor-lang`）：
   ```powershell
   npx --yes @vscode/vsce create-publisher impor-lang
   ```
   若 `impor-lang` 已被占用，改 `package.json` 的 `publisher` 为新 ID 后重新打包。
3. 确认 `README.md` 中的相对链接与图片在 VSIX 内可解析（`vsce ls` 检查）。

## 2. 发布

```powershell
cd vscode-impor
npm install
$env:VSCE_PAT = "<PAT>"        # 当前 PowerShell 会话内有效
npx --yes @vscode/vsce publish
```

`vsce publish` 会读取 `package.json` 的 `version`；同一版本不能重复发布，改版本号后再发。
没有 PAT 时可用 Microsoft Entra ID 登录替代（浏览器完成一次授权即可，不需要手动创建 token）：

```powershell
npx --yes @vscode/vsce publish --azure-credential --allow-missing-repository
```

也可以发布已打好的包：

```powershell
npx --yes @vscode/vsce publish --packagePath .\vscode-impor-4.2.0.vsix
```

发布后约几分钟可在市场搜索 `Impor (I++) Language`；扩展页会显示 README、CHANGELOG、
MIT 许可证与 128×128 图标。

## 3. CI 自动发布（可选）

仓库已提供 `.github/workflows/publish-vscode.yml`：手动触发，读取仓库 Secret
`VSCE_PAT`，执行 `npm install` + `vsce publish`。配置步骤：

1. GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret。
2. 名称 `VSCE_PAT`，值为上面的 PAT。
3. Actions → “Publish VS Code Extension” → Run workflow。

不要在 workflow 或日志里 echo PAT；`vsce` 只从环境变量读取。

## 4. 校验与回滚

```powershell
npx --yes @vscode/vsce ls                  # 查看被打包的文件
npx --yes @vscode/vsce show impor-lang.vscode-impor
```

- 发布错误版本：`vsce unpublish impor-lang.vscode-impor` 会下架整个扩展，慎用；
  更安全的做法是立即发布更高版本的修复包。
- 扩展 ID 是 `impor-lang.vscode-impor`，改名会导致用户已安装版本无法自动升级。

## 5. 本工程已做的发布准备

| 项 | 状态 |
|---|---|
| `LICENSE`（MIT） | 已放入扩展目录 |
| `icon.png`（128×128，取自 `impor/assets/icons/impor-128.png`） | 已放入 |
| `CHANGELOG.md` | 已写 3.5.0 / 3.9.0 / 3.10.0 / 4.1.0 |
| `.vscodeignore` | 已排除 `.vscode`、sourcemap、pycache、测试 |
| `dependencies` | `vscode-languageclient` 已从 devDependencies 移到运行时依赖，VSIX 内置 |
| `repository` 字段 | 暂无公开仓库 URL，未填写（vsce 仅警告，不影响打包/发布） |
| `.vsix` | 已实际打出：`vscode-impor-4.2.0.vsix`（322 文件，约 471 KB） |
