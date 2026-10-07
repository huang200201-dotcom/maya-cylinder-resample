# 安全说明

## 更新信任边界

热更新会安装并运行新的 Python 代码。只从 `huang200201-dotcom/maya-cylinder-resample` 的正式 GitHub Releases 获取更新；用户确认后才安装。仓库拥有者及具备发布权限的维护者是可信代码提供方，`main` 分支不是自动更新来源。

下载使用 HTTPS，并验证 GitHub 发布信息、声明的版本、Maya 支持范围、压缩包大小、SHA-256 和包内 Python/配置文件清单。解压拒绝不安全路径。文件切换前备份，失败时回滚。校验和用于检测损坏和不一致，不是数字签名，也不能抵御发布账户被攻破后同时替换压缩包与清单。

公开更新不需要账户访问令牌。不要将 GitHub 令牌写入 `config.json`、脚本、发布包或问题报告。更新器不应上传模型、UV、场景文件或 Maya 选择数据。

## 发现问题

若发现可导致执行非预期代码、越界写文件或破坏场景的漏洞，请通过仓库的 GitHub 私密漏洞报告功能联系维护者；如果该功能尚未启用，先联系仓库拥有者，不在公开 Issue 中附上令牌、私人场景或可直接利用的敏感细节。普通错误可使用 [Bug 模板](https://github.com/huang200201-dotcom/maya-cylinder-resample/issues/new?template=bug_report.yml)。

发布更新前应复核下载来源、归档路径、文件白名单、回滚逻辑及 Maya Undo 命令兼容性。
