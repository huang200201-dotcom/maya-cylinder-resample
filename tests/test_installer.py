"""Drag-in installer staging and recovery without real Maya modules."""

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def package_fixture(parent, version, load_failure=False, invalid_syntax=False):
    package = parent / "cylinder_resample"
    package.mkdir(parents=True)
    initialization = '__version__ = "%s"\ndef show():\n' % version
    initialization += ("    raise RuntimeError('fixture reload failed')\n" if load_failure
                       else "    from . import ui\n    return ui._SESSION\n")
    (package / "__init__.py").write_text(initialization, encoding="utf-8")
    (package / "ui.py").write_text(
        "class Session:\n    preview_records = []\n_SESSION = Session()\n", encoding="utf-8")
    for name in ("core.py", "adapter.py", "mesh_command.py", "updater.py", "update_ui.py"):
        (package / name).write_text("VALUE = 1\n", encoding="utf-8")
    if invalid_syntax:
        (package / "core.py").write_text("def broken(:\n", encoding="utf-8")
    (package / "config.json").write_text('{"repository":"fixture/repo"}', encoding="utf-8")
    return package


def tree_bytes(path):
    return {item.relative_to(path).as_posix(): item.read_bytes()
            for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts}


@contextmanager
def installer_environment(directory, source_options=None):
    root = Path(directory)
    source = package_fixture(root / "source" / "src", "0.4.0", **(source_options or {}))
    scripts = root / "user_scripts"
    scripts.mkdir()
    destination = package_fixture(scripts, "0.3.0")
    old_package = types.ModuleType("cylinder_resample")
    old_package.__version__ = "0.3.0"
    old_ui = types.ModuleType("cylinder_resample.ui")
    old_ui._UPDATING = False
    old_ui._SESSION = types.SimpleNamespace(preview_records=[{"fixture": "original undo handle"}])
    old_package.ui = old_ui
    commands = types.ModuleType("maya.cmds")
    commands.internalVar = lambda **kwargs: str(scripts)
    commands.window = lambda *args, **kwargs: False
    commands.deleteUI = lambda *args, **kwargs: None
    commands.shelfLayout = lambda *args, **kwargs: [] if kwargs.get("query") else False
    commands.shelfButton = lambda *args, **kwargs: "fixture_shelf_button"
    commands.shelfTabLayout = lambda *args, **kwargs: None
    commands.objectTypeUI = lambda *args, **kwargs: "shelfButton"
    mel = types.ModuleType("maya.mel")
    mel.eval = lambda *args: "fixture_shelf_root"
    maya = types.ModuleType("maya")
    maya.cmds, maya.mel = commands, mel
    injected = {"cylinder_resample": old_package, "cylinder_resample.ui": old_ui,
                "maya": maya, "maya.cmds": commands, "maya.mel": mel}
    with patch.dict(sys.modules, injected), patch.object(sys, "path", list(sys.path)):
        spec = importlib.util.spec_from_file_location("_test_drag_installer", ROOT / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installer.__file__ = str(root / "source" / "install.py")
        yield installer, source, destination, old_package, old_ui


class DragInstallerTests(unittest.TestCase):
    def test_manual_upgrade_uses_staged_files_retains_backup_and_transfers_records(self):
        with tempfile.TemporaryDirectory() as directory, installer_environment(directory) as values:
            installer, source, target, old_package, old_ui = values
            old_bytes = tree_bytes(target)
            installer.install()
            self.assertEqual(tree_bytes(target), tree_bytes(source))
            self.assertEqual(sys.modules["cylinder_resample"].__version__, "0.4.0")
            self.assertIs(sys.modules["cylinder_resample.ui"]._SESSION.preview_records,
                          old_ui._SESSION.preview_records)
            backups = list(target.parent.glob("cylinder_resample.install-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(tree_bytes(backups[0]), old_bytes)
            self.assertFalse((target.parent / ".cylinder_resample-update.lock").exists())
            self.assertEqual(list(target.parent.glob(".cylinder_resample.install-stage-*")), [])

    def test_failed_reload_restores_original_files_and_loaded_modules(self):
        with tempfile.TemporaryDirectory() as directory, \
                installer_environment(directory, {"load_failure": True}) as values:
            installer, source, target, old_package, old_ui = values
            original = tree_bytes(target)
            with self.assertRaisesRegex(RuntimeError, "fixture reload failed"):
                installer.install()
            self.assertEqual(tree_bytes(target), original)
            self.assertIs(sys.modules["cylinder_resample"], old_package)
            self.assertIs(sys.modules["cylinder_resample.ui"], old_ui)
            self.assertEqual(len(list(target.parent.glob("cylinder_resample.install-failed-*"))), 1)
            self.assertFalse((target.parent / ".cylinder_resample-update.lock").exists())

    def test_invalid_source_python_does_not_replace_original(self):
        with tempfile.TemporaryDirectory() as directory, \
                installer_environment(directory, {"invalid_syntax": True}) as values:
            installer, source, target, old_package, old_ui = values
            original = tree_bytes(target)
            with self.assertRaises(SyntaxError):
                installer.install()
            self.assertEqual(tree_bytes(target), original)
            self.assertIs(sys.modules["cylinder_resample"], old_package)
            self.assertEqual([path.name for path in target.parent.iterdir()], ["cylinder_resample"])

    def test_staging_swap_failure_restores_old_install_and_cleans_owned_lock(self):
        with tempfile.TemporaryDirectory() as directory, installer_environment(directory) as values:
            installer, source, target, old_package, old_ui = values
            original = tree_bytes(target)
            real_replace = os.replace

            def interrupted(source, destination):
                if Path(source).name.startswith(".cylinder_resample.install-stage-"):
                    raise OSError("fixture interrupted install")
                return real_replace(source, destination)

            with patch.object(installer.os, "replace", interrupted):
                with self.assertRaisesRegex(OSError, "fixture interrupted install"):
                    installer.install()
            self.assertEqual(tree_bytes(target), original)
            self.assertIs(sys.modules["cylinder_resample"], old_package)
            self.assertEqual([path.name for path in target.parent.iterdir()], ["cylinder_resample"])

    def test_in_progress_update_or_foreign_lock_prevents_manual_upgrade(self):
        for cause in ("updating", "lock"):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as directory, \
                    installer_environment(directory) as values:
                installer, source, target, old_package, old_ui = values
                original = tree_bytes(target)
                if cause == "updating":
                    old_ui._UPDATING = True
                else:
                    (target.parent / ".cylinder_resample-update.lock").write_bytes(b"another updater")
                with self.assertRaises(RuntimeError):
                    installer.install()
                self.assertEqual(tree_bytes(target), original)
                self.assertIs(sys.modules["cylinder_resample"], old_package)
                if cause == "lock":
                    self.assertEqual((target.parent / ".cylinder_resample-update.lock").read_bytes(), b"another updater")


if __name__ == "__main__":
    unittest.main()
