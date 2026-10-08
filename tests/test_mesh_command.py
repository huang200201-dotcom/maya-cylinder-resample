"""Exercise Maya's plugin entry points without a filesystem module context."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "src" / "cylinder_resample" / "mesh_command.py"


class FakePlugin:
    def __init__(self, name):
        self.name = name
        self.version = None
        self.registrations = []
        self.deregistrations = []


class FakeMFnPlugin:
    def __init__(self, plugin, vendor=None, version=None, api=None):
        self.plugin = plugin
        if version is not None:
            self.version = version

    def name(self):
        return self.plugin.name

    @property
    def version(self):
        return self.plugin.version

    @version.setter
    def version(self, value):
        self.plugin.version = value

    def registerCommand(self, name, creator, syntax_creator):
        self.plugin.registrations.append((name, creator, syntax_creator))

    def deregisterCommand(self, name):
        self.plugin.deregistrations.append(name)


class FakeSyntax:
    kString = object()

    def __init__(self):
        self.flags = []

    def addFlag(self, short_name, long_name, flag_type):
        self.flags.append((short_name, long_name, flag_type))


def load_plugin(**extra_globals):
    maya = types.ModuleType("maya")
    api = types.ModuleType("maya.api")
    om = types.ModuleType("maya.api.OpenMaya")
    om.MPxCommand = type("MPxCommand", (), {})
    om.MFnPlugin = FakeMFnPlugin
    om.MSyntax = FakeSyntax
    maya.api = api
    api.OpenMaya = om
    namespace = {"__name__": "maya_plugin_loader"}
    namespace.update(extra_globals)
    modules = {"maya": maya, "maya.api": api, "maya.api.OpenMaya": om}
    with patch.dict(sys.modules, modules):
        exec(compile(PLUGIN_SOURCE.read_text(encoding="utf-8"),
                     str(PLUGIN_SOURCE), "exec"), namespace)
    return namespace


class MeshPluginLoaderTests(unittest.TestCase):
    def test_plugin_loads_without_file_and_declares_maya_api_2(self):
        namespace = load_plugin()
        self.assertNotIn("__file__", namespace)
        self.assertTrue(callable(namespace["initializePlugin"]))
        self.assertTrue(callable(namespace["uninitializePlugin"]))
        self.assertIsNone(namespace["maya_useNewAPI"]())

    def test_versioned_plugin_registers_command_and_matching_version(self):
        namespace = load_plugin()
        plugin = FakePlugin("cr_mesh_v12_34_56_0123456789ab")
        namespace["initializePlugin"](plugin)
        self.assertEqual(plugin.version, "12.34.56")
        self.assertEqual(len(plugin.registrations), 1)
        name, creator, syntax_creator = plugin.registrations[0]
        self.assertEqual(name, "crCreateMesh_cr_mesh_v12_34_56_0123456789ab")
        self.assertIs(creator, namespace["CreateMeshCommand"].creator)
        self.assertIs(syntax_creator, namespace["CreateMeshCommand"].syntax_creator)

    def test_unversioned_plugin_registers_with_fallback_version(self):
        namespace = load_plugin()
        plugin = FakePlugin("mesh_command")
        namespace["initializePlugin"](plugin)
        self.assertEqual(plugin.version, "0.0.0")
        self.assertEqual(plugin.registrations[0][0], "crCreateMesh_mesh_command")

    def test_plugin_identity_is_independent_of_loader_name_and_file(self):
        namespace = load_plugin(__name__="cr_mesh_v8_8_8_wrong",
                                __file__="C:/unrelated/cr_mesh_v9_9_9_wrong.py")
        plugin = FakePlugin("cr_mesh_v0_3_2_actual")
        namespace["initializePlugin"](plugin)
        namespace["uninitializePlugin"](plugin)
        self.assertEqual(plugin.version, "0.3.2")
        self.assertEqual(plugin.registrations[0][0], "crCreateMesh_cr_mesh_v0_3_2_actual")
        self.assertEqual(plugin.deregistrations, [plugin.registrations[0][0]])

    def test_plugin_revisions_deregister_their_own_command(self):
        namespace = load_plugin()
        old = FakePlugin("cr_mesh_v0_3_1_111111111111")
        new = FakePlugin("cr_mesh_v0_3_2_222222222222")
        namespace["initializePlugin"](old)
        namespace["initializePlugin"](new)
        namespace["uninitializePlugin"](old)
        namespace["uninitializePlugin"](new)
        self.assertEqual(old.deregistrations, [old.registrations[0][0]])
        self.assertEqual(new.deregistrations, [new.registrations[0][0]])
        self.assertNotEqual(old.deregistrations, new.deregistrations)

    def test_creator_returns_an_undoable_command(self):
        namespace = load_plugin()
        command_type = namespace["CreateMeshCommand"]
        command = command_type.creator()
        self.assertIsInstance(command, command_type)
        self.assertTrue(command.isUndoable())

    def test_command_syntax_accepts_the_json_data_string(self):
        namespace = load_plugin()
        syntax = namespace["CreateMeshCommand"].syntax_creator()
        self.assertEqual(syntax.flags, [("-d", "-data", FakeSyntax.kString)])

    def test_adapter_cached_source_registers_the_requested_command_without_file(self):
        maya = types.ModuleType("maya")
        api = types.ModuleType("maya.api")
        om = types.ModuleType("maya.api.OpenMaya")
        cmds = types.ModuleType("maya.cmds")
        maya.api, maya.cmds, api.OpenMaya = api, cmds, om
        om.MPxCommand = type("MPxCommand", (), {})
        om.MSyntax = FakeSyntax
        loaded, loads = {}, []

        class RegisteringMFnPlugin(FakeMFnPlugin):
            def registerCommand(self, name, creator, syntax_creator):
                super().registerCommand(name, creator, syntax_creator)
                setattr(cmds, name, lambda **kwargs: None)

        def load_cached_plugin(path, **kwargs):
            plugin_path = Path(path)
            plugin = FakePlugin(plugin_path.stem)
            namespace = {"__name__": "maya_plugin_loader"}
            exec(compile(plugin_path.read_text(encoding="utf-8"),
                         str(plugin_path), "exec"), namespace)
            self.assertNotIn("__file__", namespace)
            namespace["initializePlugin"](plugin)
            loaded[plugin_path.stem] = plugin
            loads.append(plugin_path)

        om.MFnPlugin = RegisteringMFnPlugin
        cmds.pluginInfo = lambda name, **kwargs: name in loaded
        cmds.loadPlugin = load_cached_plugin
        modules = {"maya": maya, "maya.api": api, "maya.api.OpenMaya": om,
                   "maya.cmds": cmds}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, modules), \
                patch.object(sys, "path", [str(ROOT / "src"), *sys.path]):
            from cylinder_resample import __version__
            cmds.internalVar = lambda **kwargs: directory
            spec = importlib.util.spec_from_file_location(
                "cylinder_resample._mesh_loader_test_adapter",
                ROOT / "src" / "cylinder_resample" / "adapter.py")
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            command = adapter._ensure_mesh_command()
            self.assertTrue(callable(getattr(cmds, command)))
            self.assertEqual(len(loads), 1)
            plugin = loaded[loads[0].stem]
            self.assertEqual(command, "crCreateMesh_" + loads[0].stem)
            self.assertEqual(plugin.registrations[0][0], command)
            self.assertEqual(plugin.version, __version__)
            self.assertEqual(loads[0].read_bytes(), PLUGIN_SOURCE.read_bytes())
            self.assertEqual(adapter._ensure_mesh_command(), command)
            self.assertEqual(len(loads), 1)


if __name__ == "__main__":
    unittest.main()
