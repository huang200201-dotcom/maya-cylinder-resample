"""Update orchestration without Maya DLLs, real network, or scene mutations."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import cylinder_resample


class Session:
    def __init__(self):
        self.controls = {"update": "update_button"}
        self.preview_records = [{"fixture": "retained undo handle"}]
        self._busy = False
        self.messages = []
        self.cancels = []
        self.syncs = 0
        self.cancel_error = None

    def message(self, message, error=False):
        self.messages.append((message, error))

    def cancel(self, **kwargs):
        self.cancels.append(kwargs)
        if self.cancel_error:
            raise RuntimeError(self.cancel_error)

    def sync_controls(self):
        self.syncs += 1


class Environment:
    def __init__(self):
        self.window = True
        self.confirm = "取消"
        self.confirmations = []
        self.buttons = []
        self.deferred = []
        self.warnings = []
        self.overlays = []
        self.session = Session()
        self.ui = types.ModuleType("cylinder_resample.ui")
        self.ui.WINDOW = "fixture_window"
        self.ui._SESSION = self.session
        self.ui._UPDATING = False
        self.ui.Session = Session
        self.ui.show = self.show_old_ui
        self.cmds = types.ModuleType("maya.cmds")
        self.cmds.window = lambda *args, **kwargs: self.window
        self.cmds.control = lambda *args, **kwargs: True
        self.cmds.button = lambda name, **kwargs: self.buttons.append(kwargs["enable"])
        self.cmds.confirmDialog = self.confirm_dialog
        self.cmds.deleteUI = self.delete_ui
        self.cmds.warning = self.warnings.append
        self.cmds.inViewMessage = lambda **kwargs: self.overlays.append(kwargs)
        self.utils = types.ModuleType("maya.utils")
        self.utils.executeDeferred = self.deferred.append
        self.maya = types.ModuleType("maya")
        self.maya.cmds, self.maya.utils = self.cmds, self.utils

    def confirm_dialog(self, **kwargs):
        self.confirmations.append(kwargs)
        return self.confirm

    def delete_ui(self, *args, **kwargs):
        self.window = False

    def show_old_ui(self):
        self.window = True
        self.ui._SESSION = Session()


@contextmanager
def loaded_updater_ui():
    environment = Environment()
    with patch.dict(sys.modules, {"maya": environment.maya, "maya.cmds": environment.cmds,
                                 "maya.utils": environment.utils,
                                 "cylinder_resample.ui": environment.ui}), \
            patch.object(cylinder_resample, "ui", environment.ui, create=True):
        spec = importlib.util.spec_from_file_location(
            "cylinder_resample._test_update_ui", ROOT / "src" / "cylinder_resample" / "update_ui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module, environment


def available_release():
    return {"available": True, "version": "0.4.0", "notes": "Fixture update notes",
            "release_url": "https://github.com/fixture/repo/releases/tag/v0.4.0"}


class UpdateUiTests(unittest.TestCase):
    def test_worker_runs_action_then_defers_ui_completion_and_captures_failure(self):
        with loaded_updater_ui() as (module, environment):
            finished = Mock()

            def thread(target, **kwargs):
                return types.SimpleNamespace(start=target)

            with patch.object(module.threading, "Thread", thread):
                module._worker(lambda: "fixture_result", finished)
            finished.assert_not_called()
            self.assertEqual(len(environment.deferred), 1)
            environment.deferred.pop()()
            finished.assert_called_once_with("fixture_result", None)
            finished.reset_mock()

            def fail():
                raise RuntimeError("fixture failure")

            with patch.object(module.threading, "Thread", thread):
                module._worker(fail, finished)
            finished.assert_not_called()
            environment.deferred.pop()()
            finished.assert_called_once_with(None, "fixture failure")

    def test_check_failure_reenables_button_and_clears_job(self):
        with loaded_updater_ui() as (module, environment):
            callbacks = []
            with patch.object(module, "_worker", lambda action, finish: callbacks.append(finish)):
                module.check(environment.session)
            self.assertIsNotNone(module._JOB)
            self.assertEqual(environment.buttons, [False])
            callbacks.pop()(None, "fixture offline")
            self.assertIsNone(module._JOB)
            self.assertEqual(environment.buttons, [False, True])
            self.assertTrue(environment.session.messages[-1][1])
            self.assertEqual(environment.confirmations, [])

    def test_closed_or_replaced_window_does_not_prompt_after_background_check(self):
        for changed in ("closed", "replaced"):
            with self.subTest(changed=changed), loaded_updater_ui() as (module, environment):
                callbacks = []
                with patch.object(module, "_worker", lambda action, finish: callbacks.append(finish)):
                    module.check(environment.session)
                if changed == "closed":
                    environment.window = False
                else:
                    environment.ui._SESSION = Session()
                callbacks.pop()(available_release(), None)
                self.assertEqual(environment.confirmations, [])
                self.assertIsNone(module._JOB)
                self.assertEqual(environment.session.cancels, [])

    def test_cancel_and_release_page_choices_do_not_install(self):
        for answer in ("取消", "打开发布页"):
            with self.subTest(answer=answer), loaded_updater_ui() as (module, environment):
                environment.confirm = answer
                with patch.object(module, "_worker") as worker, patch.object(module.webbrowser, "open") as open_page:
                    module._checked(environment.session, available_release(), None)
                worker.assert_not_called()
                self.assertEqual(environment.session.cancels, [])
                if answer == "打开发布页":
                    open_page.assert_called_once_with(available_release()["release_url"])
                else:
                    open_page.assert_not_called()

    def test_cancel_preview_failure_prevents_install(self):
        with loaded_updater_ui() as (module, environment):
            environment.confirm = "立即更新"
            environment.session.cancel_error = "fixture cannot clean preview"
            with patch.object(module, "_worker") as worker:
                module._checked(environment.session, available_release(), None)
            worker.assert_not_called()
            self.assertFalse(environment.ui._UPDATING)
            self.assertFalse(environment.session._busy)
            self.assertTrue(environment.session.messages[-1][1])

    def test_install_failure_leaves_existing_modules_and_unlocks_modeling(self):
        with loaded_updater_ui() as (module, environment):
            callbacks = []
            environment.confirm = "立即更新"
            with patch.object(module, "_worker", lambda action, finish: callbacks.append(finish)):
                module._checked(environment.session, available_release(), None)
            self.assertTrue(environment.ui._UPDATING)
            self.assertTrue(environment.session._busy)
            self.assertEqual(environment.session.cancels, [{"silent": True}])
            with patch.object(module, "_forget_package") as forget, \
                    patch.object(module.updater, "rollback_install") as rollback:
                callbacks.pop()(None, "fixture download failure")
            forget.assert_not_called()
            rollback.assert_not_called()
            self.assertFalse(environment.ui._UPDATING)
            self.assertFalse(environment.session._busy)
            self.assertIsNone(module._JOB)
            self.assertTrue(environment.session.messages[-1][1])
            self.assertTrue(environment.buttons[-1])
            self.assertTrue(environment.window)

    def test_verified_reload_transfers_undo_handles_and_displays_new_version(self):
        for window_open in (True, False):
            with self.subTest(window_open=window_open), loaded_updater_ui() as (module, environment):
                environment.window = window_open
                environment.ui._UPDATING = True
                environment.session._busy = True
                records = environment.session.preview_records
                new_ui = types.SimpleNamespace(_SESSION=None, Session=Session)

                def show():
                    environment.window = True
                    new_ui._SESSION = Session()

                package = types.SimpleNamespace(__version__="0.4.0", show=show)
                importer = lambda name: package if name == "cylinder_resample" else new_ui
                with patch.object(module, "_forget_package") as forget, \
                        patch.object(module.importlib, "import_module", importer), \
                        patch.object(module.updater, "rollback_install") as rollback:
                    module._installed(environment.session, {"version": "0.4.0", "backup_dir": "fixture_backup"}, None)
                forget.assert_called_once()
                rollback.assert_not_called()
                self.assertFalse(environment.ui._UPDATING)
                self.assertFalse(environment.session._busy)
                self.assertIs(new_ui._SESSION.preview_records, records)
                self.assertEqual(environment.window, window_open)
                self.assertEqual(len(environment.overlays), 1)
                if window_open:
                    self.assertEqual(new_ui._SESSION.syncs, 1)
                    self.assertIn("0.4.0", new_ui._SESSION.messages[-1][0])

    def test_reload_failure_restores_old_modules_backup_and_original_ui(self):
        with loaded_updater_ui() as (module, environment):
            environment.ui._UPDATING = True
            environment.session._busy = True
            records = environment.session.preview_records
            old_marker = types.ModuleType("cylinder_resample.fixture_loaded")
            result = {"version": "0.4.0", "backup_dir": "fixture_backup"}

            def forget():
                sys.modules.pop(old_marker.__name__, None)

            with patch.dict(sys.modules, {old_marker.__name__: old_marker}), \
                    patch.object(module, "_forget_package", forget), \
                    patch.object(module.importlib, "import_module", side_effect=RuntimeError("fixture bad import")), \
                    patch.object(module.traceback, "print_exc"), \
                    patch.object(module.updater, "rollback_install") as rollback:
                module._installed(environment.session, result, None)
                self.assertIs(sys.modules[old_marker.__name__], old_marker)
            rollback.assert_called_once_with(result)
            self.assertTrue(environment.window)
            self.assertIs(environment.ui._SESSION.preview_records, records)
            self.assertTrue(environment.ui._SESSION.messages[-1][1])
            self.assertFalse(environment.ui._UPDATING)
            self.assertFalse(environment.session._busy)
            self.assertEqual(len(environment.warnings), 1)


if __name__ == "__main__":
    unittest.main()
