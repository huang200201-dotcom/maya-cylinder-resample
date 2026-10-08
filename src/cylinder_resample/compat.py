"""Runtime checks shared by the Maya UI, installer, and update validation."""

import re
import sys


MIN_MAYA = 2022
MAX_MAYA = 2027
MIN_PYTHON = (3, 7)


def maya_year(value):
    if isinstance(value, bool):
        raise ValueError("Maya 版本号无效。")
    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        match = re.fullmatch(r"([0-9]{4})(?:\.[0-9]+)*", value.strip())
        if match is None:
            raise ValueError("Maya 版本号无效。")
        year = int(match.group(1))
    else:
        raise ValueError("Maya 版本号无效。")
    if not 2000 <= year <= 9999:
        raise ValueError("Maya 版本号无效。")
    return year


def ensure_supported():
    if sys.version_info[:2] < MIN_PYTHON:
        raise RuntimeError("插件需要 Python 3.7 或更高版本；Maya 2022 请使用 Python 3 模式。")
    import maya.cmds as cmds
    try:
        year = maya_year(cmds.about(majorVersion=True))
    except (ValueError, TypeError, RuntimeError):
        raise RuntimeError("无法识别当前 Maya 版本，未继续安装或打开插件。") from None
    if not MIN_MAYA <= year <= MAX_MAYA:
        raise RuntimeError("此版本插件适用于 Maya {} 到 {}，当前为 {}。".format(MIN_MAYA, MAX_MAYA, year))
    return year
