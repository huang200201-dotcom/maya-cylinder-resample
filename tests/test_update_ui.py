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
        self.dialog_form = "fixture_dialog|form"
        self.dialog_controls = []
        self.form_edits = []
        self.focuses = []
        self.dismissals = []
        self.before_dialog_return = None
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
        self.maya_version = "2024"
        self.version_reads = []
        self.cmds.about = self.about
        self.cmds.window = lambda *args, **kwargs: self.window
        self.cmds.control = lambda *args, **kwargs: True
        self.cmds.button = self.button
        self.cmds.layoutDialog = self.layout_dialog
        self.cmds.setParent = self.set_parent
        self.cmds.formLayout = self.form_layout
        self.cmds.text = lambda **kwargs: self.create_control("text", kwargs)
        self.cmds.scrollField = lambda **kwargs: self.create_control("scrollField", kwargs)
        self.cmds.setFocus = self.focuses.append
        self.cmds.deleteUI = self.delete_ui
        self.cmds.warning = self.warnings.append
        self.cmds.inViewMessage = lambda **kwargs: self.overlays.append(kwargs)
        self.utils = types.ModuleType("maya.utils")
        self.utils.executeDeferred = self.deferred.append
        self.maya = types.ModuleType("maya")
        self.maya.cmds, self.maya.utils = self.cmds, self.utils

    def about(self, **kwargs):
        self.version_reads.append(kwargs)
        return self.maya_version

    def create_control(self, kind, kwargs):
        name = "{}|{}{}".format(self.dialog_form, kind, len(self.dialog_controls))
        self.dialog_controls.append({"name": name, "kind": kind, "options": kwargs})
        return name

    def button(self, name=None, **kwargs):
        if kwargs.get("edit"):
            self.buttons.append(kwargs["enable"])
            return name
        return self.create_control("button", kwargs)

    def set_parent(self, **kwargs):
        if kwargs != {"query": True}:
            raise AssertionError("Unexpected setParent request: {}".format(kwargs))
        return self.dialog_form

    def form_layout(self, name, **kwargs):
        if name != self.dialog_form or not kwargs.get("edit"):
            raise AssertionError("Unexpected formLayout edit")
        self.form_edits.append(kwargs)
        return name

    def layout_dialog(self, **kwargs):
        if "dismiss" in kwargs:
            self.dismissals.append(kwargs["dismiss"])
            return
        self.confirmations.append(kwargs)
        kwargs["ui"]()
        if self.before_dialog_return:
            self.before_dialog_return()
        for control in self.dialog_controls:
            if control["kind"] == "button" and control["options"]["label"] == self.confirm:
                control["options"]["command"]()
                return self.dismissals[-1]
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
    def test_long_release_notes_are_complete_in_a_read_only_wrapping_field(self):
        with loaded_updater_ui() as (module, environment):
            release = available_release()
            release["notes"] = ("A full changelog entry.\n" * 1000) + "FINAL_CHANGELOG_ENTRY"
            module._release_dialog_contents(release)
            fields = [control for control in environment.dialog_controls
                      if control["kind"] == "scrollField"]
            self.assertEqual(len(fields), 1)
            options = fields[0]["options"]
            self.assertEqual(options["text"], release["notes"])
            self.assertTrue(options["text"].endswith("FINAL_CHANGELOG_ENTRY"))
            self.assertFalse(options["editable"])
            self.assertTrue(options["wordWrap"])
            self.assertEqual(options["insertionPosition"], 0)

    def test_missing_or_empty_release_notes_display_a_readable_placeholder(self):
        for notes in (None, "", "missing"):
            with self.subTest(notes=notes), loaded_updater_ui() as (module, environment):
                release = available_release()
                if notes == "missing":
                    release.pop("notes")
                else:
                    release["notes"] = notes
                module._release_dialog_contents(release)
                field = next(control for control in environment.dialog_controls
                             if control["kind"] == "scrollField")
                self.assertEqual(field["options"]["text"], "该版本未提供更新说明。")

    def test_dialog_size_is_fixed_independently_of_changelog_length(self):
        dimensions = []
        for notes in ("Short notes", "Long changelog entry.\n" * 1000):
            with self.subTest(length=len(notes)), loaded_updater_ui() as (module, environment):
                release = available_release()
                release["notes"] = notes
                module._release_dialog_contents(release)
                sizes = [(edit["width"], edit["height"]) for edit in environment.form_edits
                         if "width" in edit and "height" in edit]
                self.assertEqual(sizes, [(560, 460)])
                dimensions.append(sizes)
        self.assertEqual(dimensions[0], dimensions[1])

    def test_only_notes_scroll_and_all_actions_remain_anchored_at_the_bottom(self):
        with loaded_updater_ui() as (module, environment):
            module._release_dialog_contents(available_release())
            controls = environment.dialog_controls
            self.assertTrue(all(control["options"]["parent"] == environment.dialog_form
                                for control in controls))
            buttons = [control for control in controls if control["kind"] == "button"]
            self.assertEqual([button["options"]["label"] for button in buttons],
                             ["立即更新", "打开发布页", "取消"])
            self.assertTrue(all(button["options"]["height"] == 32 for button in buttons))
            notes = next(control["name"] for control in controls if control["kind"] == "scrollField")
            heading, notice = [control["name"] for control in controls if control["kind"] == "text"]
            attached = environment.form_edits[-1]
            for button in buttons:
                self.assertIn((button["name"], "bottom", 12), attached["attachForm"])
            self.assertIn((notes, "top", 8, heading), attached["attachControl"])
            self.assertIn((notes, "bottom", 8, notice), attached["attachControl"])
            self.assertIn((notice, "bottom", 10, buttons[0]["name"]), attached["attachControl"])
            self.assertEqual(attached["attachPosition"],
                             [(buttons[0]["name"], "right", 4, 33),
                              (buttons[1]["name"], "left", 4, 33),
                              (buttons[1]["name"], "right", 4, 66),
                              (buttons[2]["name"], "left", 4, 66)])

    def test_each_action_button_dismisses_with_its_own_choice_and_no_side_effects(self):
        with loaded_updater_ui() as (module, environment):
            with patch.object(module, "_worker") as worker, patch.object(module.webbrowser, "open") as open_page:
                module._release_dialog_contents(available_release())
                buttons = [control for control in environment.dialog_controls if control["kind"] == "button"]
                for button in buttons:
                    button["options"]["command"]("fixture Maya callback argument")
                worker.assert_not_called()
                open_page.assert_not_called()
            self.assertEqual(environment.dismissals, ["立即更新", "打开发布页", "取消"])
            self.assertEqual(environment.session.cancels, [])

    def test_cancel_receives_default_keyboard_focus(self):
        with loaded_updater_ui() as (module, environment):
            module._release_dialog_contents(available_release())
            cancel = next(control["name"] for control in environment.dialog_controls
                          if control["kind"] == "button" and control["options"]["label"] == "取消")
            self.assertEqual(environment.focuses, [cancel])

    def test_fixed_modal_dialog_uses_only_flags_supported_by_each_maya_version(self):
        for year in range(2022, 2028):
            with self.subTest(year=year), loaded_updater_ui() as (module, environment):
                self.assertEqual(module._release_dialog(available_release(), year), "取消")
                self.assertEqual(len(environment.confirmations), 1)
                options = environment.confirmations[0]
                self.assertEqual(options["title"], "圆柱重分段更新")
                self.assertTrue(callable(options["ui"]))
                if year >= 2025:
                    self.assertIs(options["resizable"], False)
                else:
                    self.assertNotIn("resizable", options)

    def test_close_button_and_unknown_dialog_results_are_normalized_to_cancel(self):
        for answer in ("dismiss", None, "", "unexpected answer", "取消"):
            with self.subTest(answer=answer), loaded_updater_ui() as (module, environment):
                environment.confirm = answer
                self.assertEqual(module._release_dialog(available_release(), 2024), "取消")

    def test_close_button_and_unknown_dialog_results_do_not_install_or_open_a_page(self):
        for answer in ("dismiss", None, "unexpected answer"):
            with self.subTest(answer=answer), loaded_updater_ui() as (module, environment):
                environment.confirm = answer
                with patch.object(module, "_worker") as worker, patch.object(module.webbrowser, "open") as open_page:
                    module._checked(environment.session, available_release(), None)
                worker.assert_not_called()
                open_page.assert_not_called()
                self.assertEqual(environment.session.cancels, [])
                self.assertFalse(environment.ui._UPDATING)
                self.assertFalse(environment.session._busy)
                self.assertIsNone(module._JOB)

    def test_install_waits_for_the_modal_dialog_to_return_explicit_confirmation(self):
        with loaded_updater_ui() as (module, environment):
            environment.confirm = "立即更新"
            with patch.object(module, "_worker") as worker:
                def before_return():
                    worker.assert_not_called()
                    self.assertEqual(environment.session.cancels, [])
                    self.assertFalse(environment.ui._UPDATING)
                    self.assertFalse(environment.session._busy)
                environment.before_dialog_return = before_return
                module._checked(environment.session, available_release(), None)
            worker.assert_called_once()
            self.assertEqual(environment.session.cancels, [{"silent": True}])

    def test_window_closed_or_replaced_while_dialog_is_open_prevents_install(self):
        for changed in ("closed", "replaced"):
            with self.subTest(changed=changed), loaded_updater_ui() as (module, environment):
                environment.confirm = "立即更新"

                def before_return():
                    if changed == "closed":
                        environment.window = False
                    else:
                        environment.ui._SESSION = Session()

                environment.before_dialog_return = before_return
                with patch.object(module, "_worker") as worker:
                    module._checked(environment.session, available_release(), None)
                worker.assert_not_called()
                self.assertEqual(environment.session.cancels, [])
                self.assertFalse(environment.ui._UPDATING)

    def test_maya_version_is_captured_before_the_check_worker(self):
        for year in range(2022, 2028):
            with self.subTest(year=year), loaded_updater_ui() as (module, environment):
                environment.maya_version = str(year)
                actions = []
                with patch.object(module, "_worker", lambda action, finish: actions.append(action)):
                    module.check(environment.session)
                self.assertEqual(environment.version_reads, [{"majorVersion": True}])
                with patch.object(environment.cmds, "about", side_effect=AssertionError("Maya queried from worker")), \
                        patch.object(module.updater, "check_for_update") as check:
                    actions.pop()()
                check.assert_called_once_with(module.__version__, maya_version=year)

    def test_maya_version_is_captured_before_the_install_worker(self):
        for year in range(2022, 2028):
            with self.subTest(year=year), loaded_updater_ui() as (module, environment):
                environment.maya_version = str(year)
                environment.confirm = "立即更新"
                actions = []
                release = available_release()
                with patch.object(module, "_worker", lambda action, finish: actions.append(action)):
                    module._checked(environment.session, release, None)
                self.assertEqual(environment.version_reads, [{"majorVersion": True}])
                with patch.object(environment.cmds, "about", side_effect=AssertionError("Maya queried from worker")), \
                        patch.object(module.updater, "install_release") as install:
                    actions.pop()()
                install.assert_called_once_with(release, maya_version=year)

    def test_unsupported_maya_does_not_start_update_or_cancel_preview(self):
        with loaded_updater_ui() as (module, environment):
            environment.maya_version = "2028"
            environment.confirm = "立即更新"
            with patch.object(module, "_worker") as worker:
                module.check(environment.session)
                module._checked(environment.session, available_release(), None)
            worker.assert_not_called()
            self.assertEqual(environment.session.cancels, [])
            self.assertFalse(environment.ui._UPDATING)

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
