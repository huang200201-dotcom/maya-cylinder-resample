# Maya 圆柱重分段

面向 Windows / Maya 2022 至 2027 的 Python 3 工具。对已删除创建历史、已经加工过的圆柱和管体重新设置圆周段数，并尽量保留 UV 布局、材质分区和边的软硬状态。

当前版本：**0.4.3**。运行时不需要额外安装 Python 库。已适配 Python 3.7 至 3.13；Maya 2022 必须使用 Python 3 模式，不支持 Python 2。该版本仍为试用版，六个 Maya 版本的原生场景、界面、撤销和热更新尚未逐版完成实机验证。

## 安装与使用

1. 从 [最新正式发布](https://github.com/huang200201-dotcom/maya-cylinder-resample/releases/latest) 下载 `CylinderResample_Maya2022-2027_v*.zip` 并完整解压。
2. 将解压目录中的 `install.py` 拖入 Maya 三维视图。
3. 点击 `CylinderTools` 工具架的 `CR` 按钮，在边模式选择完整圆周边环，然后分析并生成预览。
4. 确认后保留结果副本，或取消预览。原模型的几何与 UV 不会被覆盖。

不建议直接下载源码 ZIP 安装。源码采用 `src/` 布局；可先在仓库根目录运行 `python tools/build_release.py` 生成可拖拽安装的发布包。发布附件同时保留 `CylinderResample_Maya2024_v*.zip`，与通用名称的 ZIP 字节完全一致，仅用于兼容旧版热更新器，文件名不表示仅支持 2024。

## Maya 版本

| Maya | 自带 Python 主次版本 | 本插件要求 |
| --- | --- | --- |
| 2022 | 3.7 | Python 3 模式，不能使用 Python 2 |
| 2023 | 3.9 | Python 3 |
| 2024 | 3.10 | Python 3 |
| 2025、2026 | 3.11 | Python 3 |
| 2027 | 3.13 | Python 3 |

版本依据 Autodesk 的 [2022 组件说明](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/Open-Source-Components/2022-Open-Source-Components.html)、[2023 Python 更新](https://help.autodesk.com/cloudhelp/2023/ENU/Maya-WhatsNewPR/files/GUID-DF43840B-4DB1-43F8-BFD1-97D8D031B91D.htm)、[2024 组件说明](https://help.autodesk.com/cloudhelp/2024/ENU/Maya-SDK/files/Open-Source-Components/Maya_SDK_Open_Source_Components_2024_Open_Source_Components_html.html)、[2025 组件说明](https://help.autodesk.com/cloudhelp/2025/ENU/Maya-DEVHELP/files/Maya_DEVHELP_Open_Source_Components_html.html)、[2026 组件说明](https://help.autodesk.com/cloudhelp/2026/ENU/Maya-DEVHELP/files/Maya_DEVHELP_Open_Source_Components_html.html) 和 [2027 API 更新](https://blog.autodesk.io/maya-2027-api-update-guide/)。补丁版可能随 Maya 更新而变化。此表为适配目标，不是原生 Maya 实机验收记录。

## 从 GitHub 热更新

在插件窗口点击“检查 GitHub 更新”。发现新正式版本后，确认安装，再下载并验证发布包，备份旧文件并切换到新版。更新完成后重新分析模型；不需要重启 Maya。更新前应先保留或取消预览，正在运行的操作不能跨版本继续。

更新弹窗固定为 560 × 460，日志完整显示在滚动区，操作按钮始终留在底部。旧版若因日志过长导致按钮在屏幕外，请关闭弹窗，下载最新发布包并拖入 `install.py` 完成首次修复。

从 0.1 / 0.2 升级到当前版，需要先下载本次发布包并拖入 `install.py` 一次；旧版本没有更新器。之后即可在窗口内检查并确认后续更新。

0.3.x 的 Maya 2024 用户可直接检查更新至当前版。其他 Maya 版本建议先下载通用发布包并拖入 `install.py` 一次；尤其 Maya 2022 的旧更新器不兼容 Python 3.7，须手动完成首次升级。安装后的更新检查会按正在运行的 Maya 版本验证清单范围。

0.3.1 用户遇到 `name '__file__' is not defined` 或创建网格命令不存在时，可在窗口内检查更新至当前版；也可下载新版发布包并拖入 `install.py` 重新安装。

更新来源固定为本仓库的 **正式 GitHub Releases**，不直接执行 `main` 分支中的开发代码，不自动更新，也不下载草稿或预发布版本。公开仓库不需要 GitHub 令牌。网络错误、损坏的包或安装失败不会被当成更新成功。

SHA-256 校验用于发现下载损坏和文件不一致，不是发布者数字签名；仓库发布权限本身仍是信任边界。详见 [安全说明](SECURITY.md)。

## 三种模式

| 模式 | 适合的情况 | 主要取舍 |
| --- | --- | --- |
| 保留原轮廓 | 加工后的倒角、台阶、凹槽 | 增段固定旧角点，新增段沿圆周均衡分散，但不能保证任意段数等距 |
| 均匀重采样 | 原分段不均匀，希望间距更一致 | 按所选圈弧长采样，未受保护的旧角点可能移动或略过 |
| 圆形拟合 | 近圆截面，希望增段后更圆 | 每圈独立按角度采样，共享受保护区间的段数；明显变形、椭圆或非共面截面不适用 |

UV 接缝、材质边界及受保护硬边会固定部分采样列。固定所有旧角点和任意目标段数精确等距通常不能同时满足。

分析区显示各 UV 集、材质与硬边的限制数量。未整理的备用 UV 集也可能阻止减段，因为开启“保留 UV”会保留全部 UV 集。关闭该选项会取消全部 UV 接缝限制，结果不生成 UV；材质边界及仍勾选的“保留硬边”继续独立保护。

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

测试覆盖纯几何计算、模拟界面会话与更新下载/校验/安装逻辑。GitHub Actions 在 Windows 2022 和 Ubuntu 22.04 的 Python 3.7、3.9、3.10、3.11、3.13 上运行真实解释器测试及发布包验证，但不包含 Maya 原生场景 API。Ubuntu 测试通过不表示插件已完成 Linux Maya 适配。

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
