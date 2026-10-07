"""Keep network work off Maya's UI thread and reload on the main thread."""

import importlib
import sys
import threading
import traceback
import webbrowser

import maya.cmds as cmds
import maya.utils as maya_utils

from . import __version__, updater


_JOB = None


def _current(session):
    from . import ui
    return ui._SESSION is session and cmds.window(ui.WINDOW, exists=True)


def _button(session, enabled):
    name = session.controls.get("update", "")
    if cmds.control(name, exists=True):
        cmds.button(name, edit=True, enable=enabled)


def _worker(action, finished):
    def work():
        try:
            result, error = action(), None
        except Exception as exc:
            result, error = None, str(exc)
        maya_utils.executeDeferred(lambda: finished(result, error))
    threading.Thread(target=work, name="CylinderResampleUpdate", daemon=True).start()


def check(session):
    global _JOB
    if _JOB is not None:
        session.message("已有更新操作正在进行，请稍候。")
        return
    _JOB = {"session": session, "installing": False}
    _button(session, False)
    session.message("正在检查 GitHub 发布版本…")
    _worker(lambda: updater.check_for_update(__version__), lambda result, error: _checked(session, result, error))


def _checked(session, release, error):
    global _JOB
    _JOB = None
    _button(session, True)
    if not _current(session):
        return
    if error:
        session.message("检查更新失败：{}".format(error), error=True)
        return
    if not release["available"]:
        session.message("当前版本 {} 已是最新发布版本。".format(__version__))
        return
    answer = cmds.confirmDialog(
        title="圆柱重分段更新",
        message="{} → {}\n\n{}\n\n更新将取消未确认的预览，并保留旧版备份。".format(
            __version__, release["version"], release.get("notes", "")[:2000]),
        button=["立即更新", "打开发布页", "取消"], defaultButton="立即更新",
        cancelButton="取消", dismissString="取消")
    if answer == "打开发布页":
        webbrowser.open(release["release_url"])
        return
    if answer != "立即更新":
        session.message("发现新版本 {}，尚未安装。".format(release["version"]))
        return
    if not _current(session):
        return
    from . import ui
    try:
        session.cancel(silent=True)
    except Exception as exc:
        session.message("预览未能清理，已取消更新：{}".format(exc), error=True)
        return
    ui._UPDATING = True
    session._busy = True
    _JOB = {"session": session, "installing": True}
    _button(session, False)
    session.message("正在下载、校验并安装 {}…".format(release["version"]))
    _worker(lambda: updater.install_release(release), lambda result, failure: _installed(session, result, failure))


def _package_modules():
    return {name: module for name, module in sys.modules.copy().items()
            if name == "cylinder_resample" or name.startswith("cylinder_resample.")}


def _forget_package():
    for name in _package_modules():
        sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _installed(session, result, error):
    global _JOB
    from . import ui
    old_modules = _package_modules()
    had_window = cmds.window(ui.WINDOW, exists=True)
    records = session.preview_records
    _JOB = None
    ui._UPDATING = False
    session._busy = False
    if error:
        _button(session, True)
        session.message("更新失败，原版本仍可使用：{}".format(error), error=True)
        return
    try:
        if had_window:
            cmds.deleteUI(ui.WINDOW)
        _forget_package()
        package = importlib.import_module("cylinder_resample")
        if package.__version__ != result["version"]:
            raise RuntimeError("重新加载后的版本与发布版本不一致。")
        new_ui = importlib.import_module("cylinder_resample.ui")
        if had_window:
            package.show()
            new_ui._SESSION.preview_records = records
            new_ui._SESSION.sync_controls()
            new_ui._SESSION.message("已更新到 {}。旧版备份：{}".format(result["version"], result["backup_dir"]))
        else:
            new_ui._SESSION = new_ui.Session()
            new_ui._SESSION.preview_records = records
        cmds.inViewMessage(amg="圆柱重分段已更新到 {}".format(result["version"]), pos="topCenter", fade=True)
    except Exception:
        traceback.print_exc()
        # The old updater remains referenced even after sys.modules is cleared.
        try:
            if cmds.window(ui.WINDOW, exists=True):
                try:
                    cmds.deleteUI(ui.WINDOW)
                except Exception:
                    traceback.print_exc()
            _forget_package()
            updater.rollback_install(result)
            sys.modules.update(old_modules)
            importlib.invalidate_caches()
            if had_window:
                ui.show()
                ui._SESSION.preview_records = records
                ui._SESSION.sync_controls()
                ui._SESSION.message("新版无法加载，已回退到 {}。".format(__version__), error=True)
            else:
                session.preview_records = records
                ui._SESSION = session
            cmds.warning("新版加载失败，已自动恢复原版本。")
        except Exception as rollback_error:
            cmds.warning("更新回退未能完成，请从备份目录重新安装：{}".format(result["backup_dir"]))
            cmds.warning(str(rollback_error))
