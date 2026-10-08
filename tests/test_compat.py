"""Maya year detection and UI preflight without Autodesk DLLs."""

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
from cylinder_resample import compat


@contextmanager
def maya_environment(version):
    cmds = Mock()
    cmds.about.return_value = version
    cmds.optionVar.return_value = False
    maya = types.ModuleType("maya")
    maya.cmds = cmds
    adapter = types.ModuleType("cylinder_resample.adapter")
    with patch.dict(sys.modules, {"maya": maya, "maya.cmds": cmds,
                                 "cylinder_resample.adapter": adapter}), \
            patch.object(cylinder_resample, "adapter", adapter, create=True):
        yield cmds


def ui_module():
    spec = importlib.util.spec_from_file_location("cylinder_resample._compat_ui",
                                                ROOT / "src" / "cylinder_resample" / "ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_maya_year_accepts_years_and_point_releases(self):
        for year in range(2022, 2028):
            for value in (year, str(year), str(year) + ".3", str(year) + ".2.1"):
                with self.subTest(value=value):
                    self.assertEqual(compat.maya_year(value), year)

    def test_maya_year_rejects_ambiguous_or_invalid_values(self):
        for value in (None, True, False, 2024.0, [], {}, "", "Maya 2024", "2024beta",
                      "20241", "2024..2", "2024.2junk", 1999, 10000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compat.maya_year(value)

    def test_supported_maya_and_python_versions_use_major_version_query(self):
        versions = ((3, 7, 7), (3, 9, 7), (3, 10, 8), (3, 11, 4), (3, 11, 9), (3, 13, 3))
        for year, python in zip(range(2022, 2028), versions):
            with self.subTest(year=year), maya_environment(str(year)) as cmds, \
                    patch.object(compat.sys, "version_info", python):
                self.assertEqual(compat.ensure_supported(), year)
                cmds.about.assert_called_once_with(majorVersion=True)

    def test_python_2_and_older_python_3_fail_before_maya_query(self):
        for version in ((2, 7, 11), (3, 6, 9)):
            with self.subTest(version=version), maya_environment("2022") as cmds, \
                    patch.object(compat.sys, "version_info", version):
                with self.assertRaisesRegex(RuntimeError, "Python 3.7"):
                    compat.ensure_supported()
                cmds.about.assert_not_called()

    def test_unsupported_or_unidentifiable_maya_is_rejected(self):
        for value in ("2021", "2028", "invalid", None):
            with self.subTest(value=value), maya_environment(value):
                with self.assertRaises(RuntimeError):
                    compat.ensure_supported()

    def test_window_title_uses_the_actual_maya_version(self):
        for year in range(2022, 2028):
            with self.subTest(year=year), maya_environment(str(year)) as cmds:
                module = ui_module()
                module.Session().make_window()
                self.assertIn("Maya " + str(year), cmds.window.call_args[1]["title"])

    def test_show_rejects_unsupported_maya_before_touching_existing_ui(self):
        with maya_environment("2028") as cmds:
            module = ui_module()
            module._SESSION = Mock()
            with self.assertRaises(RuntimeError):
                module.show()
            cmds.window.assert_not_called()
            cmds.deleteUI.assert_not_called()
            module._SESSION.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
