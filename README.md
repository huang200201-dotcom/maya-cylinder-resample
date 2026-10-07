# Maya 圆柱重分段

适用于 Windows / Maya 2024 的 Python 工具。对已删除创建历史、已经加工过的圆柱和管体重新设置圆周段数，并尽量保留 UV 布局、材质分区和边的软硬状态。

当前版本：**0.3.0**。运行时不需要额外安装 Python 库。该版本为试用版，实际 Maya 场景、界面及撤销流程仍需实机验证。

## 安装与使用

1. 从 [最新正式发布](https://github.com/huang200201-dotcom/maya-cylinder-resample/releases/latest) 下载 `CylinderResample_Maya2024_v*.zip` 并完整解压。
2. 将解压目录中的 `install.py` 拖入 Maya 三维视图。
3. 点击 `CylinderTools` 工具架的 `CR` 按钮，在边模式选择完整圆周边环，然后分析并生成预览。
4. 确认后保留结果副本，或取消预览。原模型的几何与 UV 不会被覆盖。

不建议直接下载源码 ZIP 安装。源码采用 `src/` 布局；可先在仓库根目录运行 `python tools/build_release.py` 生成可拖拽安装的发布包。

## 从 GitHub 热更新

在插件窗口点击“检查 GitHub 更新”。发现新正式版本后，确认安装，再下载并验证发布包，备份旧文件并切换到新版。更新完成后重新分析模型；不需要重启 Maya。更新前应先保留或取消预览，正在运行的操作不能跨版本继续。

从 0.1 / 0.2 升级到 0.3，需要先下载本次发布包并拖入 `install.py` 一次；旧版本没有更新器。之后即可在窗口内检查并确认后续更新。

更新来源固定为本仓库的 **正式 GitHub Releases**，不直接执行 `main` 分支中的开发代码，不自动更新，也不下载草稿或预发布版本。公开仓库不需要 GitHub 令牌。网络错误、损坏的包或安装失败不会被当成更新成功。

SHA-256 校验用于发现下载损坏和文件不一致，不是发布者数字签名；仓库发布权限本身仍是信任边界。详见 [安全说明](SECURITY.md)。

## 三种模式

| 模式 | 适合的情况 | 主要取舍 |
| --- | --- | --- |
| 保留原轮廓 | 加工后的倒角、台阶、凹槽 | 增段固定旧角点，新增段沿圆周均衡分散，但不能保证任意段数等距 |
| 均匀重采样 | 原分段不均匀，希望间距更一致 | 按所选圈弧长采样，未受保护的旧角点可能移动或略过 |
| 圆形拟合 | 近圆截面，希望增段后更圆 | 重新拟合每个截面；明显变形、椭圆或非共面截面不适用 |

UV 接缝、材质边界及受保护硬边会固定部分采样列。固定所有旧角点和任意目标段数精确等距通常不能同时满足。

## 支持范围

- 连续四边形环带，各圈点数一致；支持整圈倒角、台阶、变径与环形凹槽。
- 开放端口、单个 N-gon 端盖、单中心点三角扇端盖。
- 保留全部 UV 集及当前 UV 集，接缝两侧保持独立，不重新展开或打包。
- 支持同一网格中的不相连组件；原模型保留，结果是独立静态网格。

不支持侧面开孔、局部侧向挤出分支、各圈点数不同、杂乱三角面、复杂端盖、非流形或闭合环面。顶点色、锁定法线、蒙皮权重、变形器和动画连接不转移。保留 UV 布局不等于逐像素保持贴图表现，减段尤其可能损失细节。

[完整使用说明](docs/user-guide.md) · [架构说明](docs/architecture.md) · [发布维护流程](docs/releasing.md) · [更新记录](CHANGELOG.md)

## 开发与测试

```powershell
python -m unittest discover -s tests -v
python tools/build_release.py --output-dir dist
python tools/build_release.py --verify-only --output-dir dist
```

测试覆盖纯几何计算、模拟界面会话与更新下载/校验/安装逻辑。GitHub Actions 在 Windows 和 Ubuntu 的 Python 3.10、3.11、3.12 上运行这些测试，但不包含 Maya 原生场景 API。

本机 Maya 后台程序导入 Autodesk DLL 时出现 `WinError 1114` 初始化错误，因此实际 Maya 界面、撤销/重做及热更新的完整端到端验证尚未完成。发布包提供 `check_in_maya.py`，可拖入正常打开的 Maya 进行临时模型兼容自检；该自检不替代完整界面测试。

## 仓库结构

```text
src/cylinder_resample/  几何核心、Maya 适配、界面与更新器
tests/                 纯计算与模拟测试，不依赖 Maya DLL
tools/                 可复现发布包构建与验证
docs/                  用户说明、架构及发布维护流程
examples/              可导入 Maya 的 OBJ 对比模型
.github/               自动测试、标签发布及问题模板
install.py             发布包的 Maya 拖拽安装入口
check_in_maya.py       Maya 内兼容自检入口
```

维护与贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。
