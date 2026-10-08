"""Drag this file into Maya 2022-2027 (Python 3) to install the tool."""

import importlib
import importlib.util
import ast
import os
import shutil
import stat
import sys
import tempfile
import uuid


def _check_destination(path):
    current = os.path.abspath(path)
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            info = None
        if info and (stat.S_ISLNK(info.st_mode)
                     or getattr(info, "st_file_attributes", 0) & 0x400):
            raise RuntimeError("插件目录不能是符号链接或重定向目录。")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def install():
    import maya.cmds as cmds
    import maya.mel as mel

    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "cylinder_resample")
    if not os.path.isdir(source):
        source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "cylinder_resample")
    if not all(os.path.isfile(os.path.join(source, name))
               for name in ("__init__.py", "core.py", "spacing.py", "adapter.py", "ui.py", "mesh_command.py",
                            "updater.py", "update_ui.py", "config.json", "compat.py")):
        raise RuntimeError("请先完整解压 CylinderResample 工具包，再将 install.py 拖入 Maya。")
    spec = importlib.util.spec_from_file_location("_cylinder_resample_install_compat", os.path.join(source, "compat.py"))
    compatibility = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compatibility)
    compatibility.ensure_supported()
    scripts = cmds.internalVar(userScriptDir=True)
    destination = os.path.join(scripts, "cylinder_resample")
    old_modules = {name: module for name, module in sys.modules.copy().items()
                   if name == "cylinder_resample" or name.startswith("cylinder_resample.")}
    old_ui = old_modules.get("cylinder_resample.ui")
    if old_ui and getattr(old_ui, "_UPDATING", False):
        raise RuntimeError("插件正在热更新，请结束后再手动安装。")
    # Finish the old preview using its original session before upgrading.
    if cmds.window("CylinderResampleWindow", exists=True):
        cmds.deleteUI("CylinderResampleWindow")
    records = getattr(getattr(old_ui, "_SESSION", None), "preview_records", [])
    os.makedirs(scripts, exist_ok=True)
    _check_destination(destination)
    backup = None
    staged = None
    installed = False
    lock_path = os.path.join(scripts, ".cylinder_resample-update.lock")
    lock_marker = uuid.uuid4().hex.encode("ascii")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError("另一次更新尚未结束，请稍后安装。") from None
    try:
        with os.fdopen(descriptor, "wb") as lock_stream:
            lock_stream.write(lock_marker)
        if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(destination)):
            staged = tempfile.mkdtemp(prefix=".cylinder_resample.install-stage-", dir=scripts)
            for current, directories, files in os.walk(source):
                directories[:] = [name for name in directories if name != "__pycache__"]
                for name in files:
                    if not name.endswith(".py") and name != "config.json":
                        continue
                    original = os.path.join(current, name)
                    if os.path.islink(original):
                        raise RuntimeError("安装包不能包含符号链接。")
                    relative = os.path.relpath(original, source)
                    target = os.path.join(staged, relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if name.endswith(".py"):
                        with open(original, encoding="utf-8-sig") as stream:
                            ast.parse(stream.read(), filename=relative)
                    shutil.copy2(original, target)
            if os.path.exists(destination):
                backup = os.path.join(scripts, "cylinder_resample.install-backup-" + uuid.uuid4().hex[:12])
                os.replace(destination, backup)
            os.replace(staged, destination)
            staged = None
            installed = True
        if scripts in sys.path:
            sys.path.remove(scripts)
        sys.path.insert(0, scripts)
        for name in old_modules:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        import cylinder_resample
        cylinder_resample.show()
        new_ui = sys.modules["cylinder_resample.ui"]
        new_ui._SESSION.preview_records = records
    except Exception:
        if cmds.window("CylinderResampleWindow", exists=True):
            try:
                cmds.deleteUI("CylinderResampleWindow")
            except Exception:
                cmds.warning("安装窗口无法清理，仍将恢复原插件文件。")
        if installed and os.path.isdir(destination):
            failed = os.path.join(scripts, "cylinder_resample.install-failed-" + uuid.uuid4().hex[:12])
            os.replace(destination, failed)
        if backup and os.path.isdir(backup) and not os.path.exists(destination):
            os.replace(backup, destination)
        for name in list(sys.modules):
            if name == "cylinder_resample" or name.startswith("cylinder_resample."):
                del sys.modules[name]
        sys.modules.update(old_modules)
        importlib.invalidate_caches()
        raise
    finally:
        if staged and os.path.isdir(staged):
            parent = os.path.normcase(os.path.realpath(scripts))
            resolved = os.path.normcase(os.path.realpath(staged))
            if os.path.commonpath((parent, resolved)) == parent and resolved != parent:
                shutil.rmtree(staged)
        if os.path.isfile(lock_path) and not os.path.islink(lock_path):
            with open(lock_path, "rb") as stream:
                owns_lock = stream.read(128) == lock_marker
            if owns_lock:
                os.remove(lock_path)

    shelf_root = mel.eval("$crShelfRoot = $gShelfTopLevel")
    shelf = "CylinderTools"
    if not cmds.shelfLayout(shelf, exists=True):
        cmds.shelfLayout(shelf, parent=shelf_root)
    for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        if cmds.objectTypeUI(child) == "shelfButton" and cmds.shelfButton(child, query=True, docTag=True) == "cylinder_resample":
            cmds.deleteUI(child)
    cmds.shelfButton(parent=shelf, label="重分段", image="polyCylinder.png",
                     imageOverlayLabel="CR", annotation="圆柱重分段：选中圆周边环后打开工具",
                     sourceType="python", command="import cylinder_resample; cylinder_resample.show()",
                     docTag="cylinder_resample")
    cmds.shelfTabLayout(shelf_root, edit=True, selectTab=shelf)
    mel.eval("saveAllShelves $gShelfTopLevel;")


def onMayaDroppedPythonFile(*_args):
    install()


if __name__ == "__main__":
    install()
