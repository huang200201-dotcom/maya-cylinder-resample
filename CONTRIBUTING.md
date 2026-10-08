# 维护与贡献

## 开发环境

代码最低兼容 Python 3.7，适配 Windows / Maya 2022 至 2027；Maya 2022 只支持 Python 3 模式。几何核心与更新器使用标准库，不引入运行时依赖。Maya API 仅在 Maya 相关模块中导入，常规测试不应要求 Autodesk DLL。

GitHub Actions 在 Windows 2022 和 Ubuntu 22.04 使用 Python 3.7、3.9、3.10、3.11、3.13 运行测试与构建验证。必须实际运行旧解释器，不得仅依赖新 Python 的语法静态检查或因缺少新版接口跳过测试。`compat.py` 集中管理最低运行环境和 Maya 版本范围；新增接口要检查 Python 3.7 可用性。构建依赖 `setuptools>=61`，安装插件本身不需要 pip 或 setuptools。

```powershell
python -m unittest discover -s tests -v
python tools/build_release.py --output-dir dist
python tools/build_release.py --verify-only --output-dir dist
```

修改 `core.py` 时增加对应几何/UV 测试；修改预览或撤销逻辑时同时补充模拟会话测试与 Maya 实机验证步骤；修改更新器时覆盖网络失败、损坏包、路径穿越、文件切换、回滚，以及当前 Maya 主版本和发布清单范围的匹配。

## 提交与审核

1. 从 `main` 创建有明确目的的分支。
2. 保持改动聚焦，不混入无关模型、缓存、日志或访问令牌。
3. 更新测试和相应文档，列出不能执行的验证，不把模拟测试写成实机验证。
4. 提交 Pull Request，说明问题、实现及验证结果。

未经明确约定，不修改发布仓库、更新信任边界、Maya 支持版本或现有数据兼容性。不要在发布工作流、代码和日志中提交凭据。

## 版本与发布

`__version__` 是唯一代码版本来源；同步修改 `CHANGELOG.md` 与 README 的版本说明。发布标签和构建清单必须与它相同。已发布版本不得用不同内容覆盖，修复必须发布新的补丁版本。

详细流程见 [docs/releasing.md](docs/releasing.md)。安全问题按 [SECURITY.md](SECURITY.md) 处理。
