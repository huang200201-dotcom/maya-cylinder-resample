# 发布维护

## 版本规范

使用 `MAJOR.MINOR.PATCH` 稳定版号。`src/cylinder_resample/__init__.py` 的 `__version__` 是唯一构建来源。标签必须为 `v<版本>`，不覆盖已发布版本，不复用版本号发布不同内容。

修改兼容行为或功能通常升次版本，兼容修复升补丁版本。Maya 支持范围、清单 schema 或安装布局变化需同时评估更新器兼容性。不能仅修改仓库 `main` 后期待用户自动收到更新。

## 本地检查

1. 完成代码与对应测试，同步 README 和 CHANGELOG。
2. 运行纯 Python 测试和构建验证。
3. 在可用的 Maya 2022 至 2027 中逐版验证安装、重分段、UV/材质、预览、Undo/Redo、热更新及失败恢复，Maya 2022 使用 Python 3 模式，并记录未完成项。

```powershell
python -m unittest discover -s tests -v
python tools/build_release.py --output-dir dist
python tools/build_release.py --verify-only --output-dir dist
```

构建无需导入 Maya，工具自身兼容 Python 3.7。打包文本统一为 LF 换行，ZIP 时间戳、权限和排序固定；相同文本内容在 Windows/Linux 构建的包一致。生成的 `dist/` 不提交到 Git，只作为 Release 附件。

每次构建生成通用命名 `CylinderResample_Maya2022-2027_v<版本>.zip` 与历史命名 `CylinderResample_Maya2024_v<版本>.zip`，字节完全一致，各自有 `.sha256`。更新清单仍引用历史名称，兼容已经安装的 0.3.x 更新器。不要删除历史别名、单独重新打包别名，或仅上传通用包。清单的支持范围为 2022 至 2027，新更新器按当前 Maya 版本校验。

## 自动发布

将代码提交到 `main` 后，创建并推送匹配版本的标签：

```powershell
git tag v0.4.0
git push origin main
git push origin v0.4.0
```

示例中的版本须替换为实际 `__version__`。`.github/workflows/release.yml` 检查标签版本，在 Windows 2022 和 Ubuntu 22.04 的 Python 3.7、3.9、3.10、3.11、3.13 上运行测试，再构建/验证归档并创建正式 GitHub Release，上传两个 ZIP、清单与对应校验文件。Python 3.7 分别使用官方 Actions 可获得的 Windows 3.7.9 和 Ubuntu 3.7.17；旧解释器测试不可静默跳过。测试或验证失败时不发布。不要把草稿/预发布用作稳定更新渠道。

CI 使用 GitHub 官方 `actions/checkout` 和 `actions/setup-python`，常规测试权限为 `contents: read`，只有发布工作流允许 `contents: write`。发布使用仓库的短期 `GITHUB_TOKEN`，不在代码中保存个人令牌。

## 手动恢复发布

只有在自动发布不可用并且相同检查已完成时，才手动发布同一构建产物：

```powershell
gh release create v0.4.0 dist/CylinderResample_Maya2022-2027_v0.4.0.zip dist/CylinderResample_Maya2022-2027_v0.4.0.zip.sha256 dist/CylinderResample_Maya2024_v0.4.0.zip dist/CylinderResample_Maya2024_v0.4.0.zip.sha256 dist/update-manifest.json --title "0.4.0" --notes-file CHANGELOG.md --verify-tag
```

`--verify-tag` 要求标签已推送。发布完成后检查 Releases 附件、公开下载及插件中“检查 GitHub 更新”是否识别版本。SHA-256 不是数字签名；保管发布权限并启用账户双因素认证。

## 回滚原则

发现已发布版本有问题时，发布更高的新补丁版包含修复或回退，不覆盖已有附件。用户更新失败时应恢复当前安装；手动回退可退出 Maya 后拖入此前发布包重新安装。不要在 Maya 仍运行时删除旧命令缓存，以免 Undo 引用失效。

目前 Maya 2022 至 2027 的原生端到端验证尚未逐版完成，本机 Autodesk DLL 初始化失败。发布说明需持续保留这一限制，直到得到可复现的逐版实机验证记录。GitHub 正式 Release 是热更新渠道的发布状态，不表示原生实机验收已经完成。
