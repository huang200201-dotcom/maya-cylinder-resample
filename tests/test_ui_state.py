"""Session behavior with stable Maya-like handles and a reversible fake scene.

These checks exercise the actual UI session methods without opening Maya or
touching a user's scene. API integration remains covered by maya_smoke.py.
"""

import importlib.util
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


class FakeNode:
    def __init__(self, path, epoch, preview=False, visible=True):
        self.path = path
        self.epoch = epoch
        self.alive = True
        self.referenced = False
        self.instanced = False
        self.attrs = {"visibility": bool(visible)}
        if preview:
            self.attrs["cylinderResamplePreview"] = False


class FakeHandle:
    def __init__(self, node):
        self.node = node

    def isValid(self):
        return self.node.epoch == SCENE.epoch

    def isAlive(self):
        return self.isValid() and self.node.alive

    def object(self):
        return self.node


class FakeDag:
    def __init__(self, node):
        self.value = node.value if isinstance(node, FakeDag) else node

    def node(self):
        return self.value

    def fullPathName(self):
        if not self.value.alive or self.value.epoch != SCENE.epoch:
            raise RuntimeError("Node no longer exists")
        return self.value.path

    def instanceNumber(self):
        return 0

    def isInstanced(self):
        return self.value.instanced


class FakeScene:
    def __init__(self):
        self.epoch = 0
        self.nodes = []
        self.controls = {
            "count": {"value": 18}, "mode": {"select": 1},
            "uv": {"value": True}, "hard": {"value": True},
            "compare": {"value": False, "enable": False},
            "beside": {"value": False}, "preview": {}, "region": {},
            "keep": {"enable": False}, "cancel": {"enable": False}, "status": {},
        }
        self.selection = None
        self.undo_stack = []
        self.redo_stack = []
        self.chunk = None
        self.warnings = []
        self.result_number = 0
        self.fail_next_build = False
        self.attribute_writes = []

    def add_source(self, name="Source", visible=True):
        node = FakeNode("|%s|%sShape" % (name, name), self.epoch, visible=visible)
        self.nodes.append(node)
        return node

    def find(self, path):
        matches = [node for node in self.nodes
                   if node.path == path and node.alive and node.epoch == self.epoch]
        if not matches:
            raise RuntimeError("Missing node: " + path)
        return matches[-1]

    def state(self):
        return [(node, node.alive, node.path, dict(node.attrs)) for node in self.nodes]

    def restore(self, state):
        included = {node for node, _, _, _ in state}
        for node in self.nodes:
            if node not in included:
                node.alive = False
        for node, alive, path, attrs in state:
            node.alive, node.path, node.attrs = alive, path, dict(attrs)

    def undoInfo(self, **kwargs):
        if kwargs.get("openChunk"):
            if self.chunk is not None:
                raise RuntimeError("Nested undo chunks")
            self.chunk = self.state()
        if kwargs.get("closeChunk"):
            if self.chunk is None:
                raise RuntimeError("No open undo chunk")
            self.undo_stack.append(self.chunk)
            self.redo_stack = []
            self.chunk = None

    def undo(self):
        self.redo_stack.append(self.state())
        self.restore(self.undo_stack.pop())

    def redo(self):
        self.undo_stack.append(self.state())
        self.restore(self.redo_stack.pop())

    def control(self, name, **kwargs):
        return name in self.controls

    def ui_value(self, name, **kwargs):
        data = self.controls[name]
        if kwargs.get("query"):
            return data["select"] if kwargs.get("select") else data["value"]
        if kwargs.get("edit"):
            data.update({key: value for key, value in kwargs.items() if key != "edit"})
        return name

    def objExists(self, path):
        node_path, _, attribute = path.partition(".")
        try:
            node = self.find(node_path)
            return not attribute or attribute in node.attrs
        except RuntimeError:
            return False

    def getAttr(self, path, **kwargs):
        if kwargs.get("settable"):
            return True
        node_path, attribute = path.rsplit(".", 1)
        return self.find(node_path).attrs[attribute]

    def setAttr(self, path, value, **kwargs):
        node_path, attribute = path.rsplit(".", 1)
        self.find(node_path).attrs[attribute] = value
        self.attribute_writes.append(path)

    def delete(self, path):
        self.find(path).alive = False

    def select(self, *args, **kwargs):
        return None

    def reset_scene(self):
        self.epoch += 1
        self.selection = None
        self.undo_stack = []
        self.redo_stack = []
        self.chunk = None


SCENE = None


def selected_edge_loop():
    source = SCENE.selection
    if source is None or not source.alive:
        raise ValueError("Select a source")
    return {"shape": source.path, "transform": source.path.rsplit("|", 1)[0],
            "warnings": [], "seed_edges": [], "fingerprint": "fixture",
            "materials": [], "uv_sets": {}, "current_uv_set": "map1",
            "shape_handle": FakeHandle(source)}


def analyze(snapshot):
    return {"source_count": 12, "ring_count": 2, "rings": [list(range(12))] * 2,
            "face_ids": list(range(12)), "seed_ring": 0}


def build_result(snapshot, count, *args, **kwargs):
    live_shape(snapshot)
    if SCENE.fail_next_build:
        SCENE.fail_next_build = False
        raise ValueError("Fixture rejects the next rebuild")
    SCENE.result_number += 1
    node = FakeNode("|Preview%d" % SCENE.result_number, SCENE.epoch, preview=True)
    SCENE.nodes.append(node)
    return {"node": node.path, "warnings": [],
            "result": {"uv_sets": {"map1": {}}, "stats": {
                "min_segment_length": 1.0, "max_segment_length": 1.0,
                "spacing_ratio": 1.0, "protected_count": 1,
                "constraint_columns_count": 1}}}


def live_shape(snapshot):
    handle = snapshot["shape_handle"]
    if not handle.isValid() or not handle.isAlive():
        raise ValueError("The analyzed source has been deleted")
    return handle.object().path


@contextmanager
def undo_chunk(name):
    SCENE.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        SCENE.undoInfo(closeChunk=True)


def load_session_type(return_module=False):
    maya = types.ModuleType("maya")
    cmds = types.ModuleType("maya.cmds")
    for name in ("undoInfo", "objExists", "getAttr", "setAttr", "delete", "select", "control"):
        setattr(cmds, name, lambda *args, _name=name, **kwargs: getattr(SCENE, _name)(*args, **kwargs))
    for name in ("checkBox", "radioButtonGrp", "intSliderGrp", "button", "scrollField"):
        setattr(cmds, name, lambda *args, **kwargs: SCENE.ui_value(*args, **kwargs))
    cmds.warning = lambda message: SCENE.warnings.append(message)
    cmds.referenceQuery = lambda path, **kwargs: SCENE.find(path).referenced
    cmds.exactWorldBoundingBox = lambda path: [0.0, 0.0, 0.0, 1.0, 2.0, 1.0]
    cmds.move = lambda x, y, z, path, **kwargs: SCENE.find(path).attrs.update({"tx": x, "ty": y, "tz": z})
    cmds.waitCursor = lambda **kwargs: None
    cmds.window = lambda *args, **kwargs: False
    cmds.deleteUI = lambda *args, **kwargs: None
    cmds.ls = lambda value=None, **kwargs: [SCENE.find(value).path] if value else []
    fake_adapter = types.ModuleType("cylinder_resample.adapter")
    fake_adapter.om = types.SimpleNamespace(MObjectHandle=FakeHandle, MFnDagNode=FakeDag)
    fake_adapter._dag = lambda path: FakeDag(SCENE.find(path))
    fake_adapter.ToolError = ValueError
    fake_adapter.selected_edge_loop = selected_edge_loop
    fake_adapter.snapshot_mesh = lambda shape, seed: dict(selected_edge_loop(), shape=shape, seed_edges=seed)
    fake_adapter.analyze = analyze
    fake_adapter.build_result = build_result
    fake_adapter.highlight = lambda *args: None
    fake_adapter.live_shape = live_shape
    fake_adapter.undo_chunk = undo_chunk
    fake_adapter.constraint_summary = lambda *args: {"protected_count": 1, "minimum_count": 3}
    maya.cmds = cmds
    module_name = "cylinder_resample._state_test_ui"
    path = ROOT / "src" / "cylinder_resample" / "ui.py"
    with patch.dict(sys.modules, {"maya": maya, "maya.cmds": cmds,
                                 "cylinder_resample.adapter": fake_adapter}):
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module if return_module else module.Session


def load_adapter_module():
    maya = types.ModuleType("maya")
    api = types.ModuleType("maya.api")
    om = types.ModuleType("maya.api.OpenMaya")
    cmds = types.ModuleType("maya.cmds")
    cmds.undoInfo = lambda **kwargs: SCENE.undoInfo(**kwargs)
    om.MDagPath = types.SimpleNamespace(getAllPathsTo=lambda node: [FakeDag(node)])
    maya.api, maya.cmds, api.OpenMaya = api, cmds, om
    path = ROOT / "src" / "cylinder_resample" / "adapter.py"
    with patch.dict(sys.modules, {"maya": maya, "maya.api": api,
                                 "maya.api.OpenMaya": om, "maya.cmds": cmds}):
        spec = importlib.util.spec_from_file_location("cylinder_resample._state_test_adapter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        global SCENE
        SCENE = FakeScene()
        self.adapter = load_adapter_module()

    def test_mesh_command_cache_reuses_loaded_revision_and_keeps_old_revision_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "template"
            source.mkdir()
            template = source / "mesh_command.py"
            template.write_text("COMMAND = 'fixture'\n", encoding="utf-8")
            loaded, loads = set(), []

            def load_plugin(path, **kwargs):
                loaded.add(Path(path).stem)
                loads.append(path)
                setattr(self.adapter.cmds, "crCreateMesh_" + Path(path).stem, lambda **kwargs: None)

            commands = self.adapter.cmds
            with patch.object(self.adapter, "__file__", str(source / "adapter.py")), \
                    patch.object(commands, "internalVar", lambda **kwargs: str(root), create=True), \
                    patch.object(commands, "pluginInfo", lambda name, **kwargs: name in loaded, create=True), \
                    patch.object(commands, "loadPlugin", load_plugin, create=True):
                original = self.adapter._ensure_mesh_command()
                repeated = self.adapter._ensure_mesh_command()
                self.assertEqual(original, repeated)
                self.assertEqual(len(loads), 1)
                template.write_text("COMMAND = 'changed_fixture'\n", encoding="utf-8")
                changed = self.adapter._ensure_mesh_command()
                self.assertNotEqual(original, changed)
                self.assertEqual(len(loads), 2)
                self.assertEqual(len(loaded), 2)
                self.assertEqual(Path(loads[0]).read_text(encoding="utf-8"), "COMMAND = 'fixture'\n")
                self.assertEqual(Path(loads[1]).read_text(encoding="utf-8"), "COMMAND = 'changed_fixture'\n")
                with patch("cylinder_resample.__version__", "99.0.0"):
                    next_version = self.adapter._ensure_mesh_command()
                self.assertNotEqual(changed, next_version)
                self.assertEqual(len(loaded), 3)

    def test_mesh_command_cache_rejects_corrupted_cached_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mesh_command.py").write_text("COMMAND = 'fixture'\n", encoding="utf-8")
            commands = self.adapter.cmds

            def load_plugin(path, **kwargs):
                setattr(commands, "crCreateMesh_" + Path(path).stem, lambda **kwargs: None)

            with patch.object(self.adapter, "__file__", str(root / "adapter.py")), \
                    patch.object(commands, "internalVar", lambda **kwargs: str(root), create=True), \
                    patch.object(commands, "pluginInfo", lambda *args, **kwargs: False, create=True), \
                    patch.object(commands, "loadPlugin", load_plugin, create=True):
                self.adapter._ensure_mesh_command()
                plugin = next((root / "CylinderResample" / "plugins").glob("*.py"))
                plugin.write_text("COMMAND = 'tampered'\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.adapter._ensure_mesh_command()

    def test_mesh_command_load_failure_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            commands = self.adapter.cmds
            with patch.object(commands, "internalVar", return_value=directory, create=True), \
                    patch.object(commands, "pluginInfo", return_value=False, create=True), \
                    patch.object(commands, "loadPlugin", side_effect=RuntimeError("fixture plugin failure"), create=True):
                with self.assertRaisesRegex(self.adapter.ToolError, "创建网格命令加载失败.*fixture plugin failure"):
                    self.adapter._ensure_mesh_command()

    def test_mesh_command_missing_registration_does_not_return_unusable_name(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded), tempfile.TemporaryDirectory() as directory:
                commands = self.adapter.cmds
                with patch.object(commands, "internalVar", return_value=directory, create=True), \
                        patch.object(commands, "pluginInfo", return_value=loaded, create=True), \
                        patch.object(commands, "loadPlugin", return_value=None, create=True):
                    with self.assertRaisesRegex(self.adapter.ToolError, "创建网格命令未成功注册"):
                        self.adapter._ensure_mesh_command()

    def test_snapshot_handle_resolves_renamed_source_and_rejects_deleted_source(self):
        source = SCENE.add_source()
        snapshot = {"shape": source.path, "shape_handle": FakeHandle(source), "instance_number": 0}
        source.path = "|Renamed|MeshShape"
        self.assertEqual(self.adapter.live_shape(snapshot), source.path)
        source.alive = False
        with self.assertRaises(ValueError):
            self.adapter.live_shape(snapshot)

    def test_material_and_hard_edge_pins_survive_resampling_and_smoothing_mapping(self):
        from test_resample import cylinder
        from cylinder_resample.core import analyze_mesh, resample_mesh
        points, faces, seed = cylinder()
        analysis = analyze_mesh(points, faces, seed)
        snapshot = {"points": points, "faces": faces, "seed_edges": seed,
                    "assignments": [0] * 6 + [1] * 6,
                    "smoothing": {(5, 17): False}, "uv_sets": {}}
        columns = self.adapter._protected_columns(snapshot, analysis, True)
        self.assertEqual(columns, [0, 5, 6])
        result = resample_mesh(points, faces, seed, 9, shape_mode="circle", protected_columns=columns)
        self.assertTrue(set(columns).issubset(result["samples"]))
        smoothing = self.adapter._edge_smoothing(snapshot, result, True)
        index = result["samples"].index(5)
        edge = tuple(sorted((result["new_rings"][0][index], result["new_rings"][1][index])))
        self.assertFalse(smoothing[edge])
        self.assertEqual(self.adapter._protected_columns(snapshot, analysis, False), [0, 6])

    def test_fan_cap_radial_hard_edge_survives_resampling(self):
        from test_resample import cylinder
        from cylinder_resample.core import analyze_mesh, resample_mesh
        points, faces, seed = cylinder(caps="fan")
        analysis = analyze_mesh(points, faces, seed)
        snapshot = {"points": points, "faces": faces, "seed_edges": seed,
                    "assignments": [0] * len(faces), "smoothing": {(5, 24): False}, "uv_sets": {}}
        columns = self.adapter._protected_columns(snapshot, analysis, True)
        self.assertEqual(columns, [5])
        result = resample_mesh(points, faces, seed, 9, shape_mode="circle", protected_columns=columns)
        smoothing = self.adapter._edge_smoothing(snapshot, result, True)
        index = result["samples"].index(5)
        radial = tuple(sorted((result["old_to_new"][24], result["new_rings"][0][index])))
        self.assertFalse(smoothing[radial])

    def test_constraint_minimum_accounts_for_uv_material_and_hard_boundaries(self):
        from test_resample import cylinder, strip_uvs
        from cylinder_resample.core import analyze_mesh
        points, faces, seed = cylinder()
        analysis = analyze_mesh(points, faces, seed)
        snapshot = {"points": points, "faces": faces, "seed_edges": seed,
                    "assignments": [0] * 6 + [1] * 6, "smoothing": {(5, 17): False},
                    "uv_sets": strip_uvs(faces, seams=(0, 1, 2, 3))}
        summary = self.adapter.constraint_summary(snapshot, analysis, True, True)
        self.assertEqual(summary["protected_count"], 6)
        self.assertEqual(summary["minimum_count"], 6)
        self.assertEqual(self.adapter.constraint_summary(snapshot, analysis, False, False)["minimum_count"], 3)

    def test_undo_chunk_closes_after_error_and_scene_state_can_be_restored(self):
        source = SCENE.add_source()
        with self.assertRaises(RuntimeError):
            with self.adapter.undo_chunk("Fixture"):
                source.attrs["visibility"] = False
                raise RuntimeError("Fixture error")
        self.assertIsNone(SCENE.chunk)
        self.assertFalse(source.attrs["visibility"])
        SCENE.undo()
        self.assertTrue(source.attrs["visibility"])


class SessionStateTests(unittest.TestCase):
    def setUp(self):
        global SCENE
        SCENE = FakeScene()
        self.source = SCENE.add_source()
        SCENE.selection = self.source
        self.ui_module = load_session_type(return_module=True)
        self.session = self.ui_module.Session()
        self.session.controls = {name: name for name in SCENE.controls}

    def start_preview(self):
        self.session.read_selection()
        self.session.preview()
        return SCENE.find(self.session._preview_node())

    def test_cancel_restores_source_after_parent_rename(self):
        preview = self.start_preview()
        self.assertFalse(self.source.attrs["visibility"])
        self.source.path = "|Renamed|SourceShape"
        self.session.cancel(silent=True)
        self.assertTrue(self.source.attrs["visibility"])
        self.assertFalse(preview.alive)

    def test_parent_rename_allows_preview_rebuild_with_stable_source_handle(self):
        first = self.start_preview()
        self.source.path = "|Renamed|SourceShape"
        self.session.preview()
        second = SCENE.find(self.session._preview_node())
        self.assertFalse(first.alive)
        self.assertTrue(second.alive)
        self.session.cancel(silent=True)
        self.assertTrue(self.source.attrs["visibility"])

    def test_rebuild_failure_retains_last_successful_preview_and_hidden_source(self):
        preview = self.start_preview()
        SCENE.fail_next_build = True
        with self.assertRaises(ValueError):
            self.session.preview()
        self.assertTrue(preview.alive)
        self.assertEqual(self.session._preview_node(), preview.path)
        self.assertFalse(self.source.attrs["visibility"])
        self.assertIsNone(SCENE.chunk)
        self.session.cancel(silent=True)
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])

    def test_cancel_retains_originally_hidden_source_visibility(self):
        self.source.attrs["visibility"] = False
        self.start_preview()
        SCENE.controls["compare"]["value"] = True
        self.session.compare()
        self.assertTrue(self.source.attrs["visibility"])
        self.session.cancel(silent=True)
        self.assertFalse(self.source.attrs["visibility"])

    def test_referenced_source_preview_compare_and_cancel_do_not_create_visibility_edits(self):
        self.source.referenced = True
        self.start_preview()
        SCENE.controls["compare"]["value"] = True
        self.session.compare()
        self.session.cancel(silent=True)
        source_attr = self.source.path + ".visibility"
        self.assertNotIn(source_attr, SCENE.attribute_writes)
        self.assertTrue(self.source.attrs["visibility"])

    def test_preview_update_undo_revives_first_result_and_cancel_removes_it(self):
        first = self.start_preview()
        SCENE.controls["count"]["value"] = 24
        self.session.preview()
        second = SCENE.find(self.session._preview_node())
        self.assertFalse(first.alive)
        self.assertTrue(second.alive)
        SCENE.undo()
        self.assertTrue(first.alive)
        self.assertFalse(second.alive)
        self.session.sync_controls()
        self.assertEqual(self.session._preview_node(), first.path)
        self.session.cancel(silent=True)
        self.assertFalse(first.alive)
        self.assertFalse(second.alive)
        self.assertTrue(self.source.attrs["visibility"])

    def test_undo_redo_sync_callbacks_leave_scene_and_undo_queue_unchanged(self):
        self.start_preview()
        self.session.preview()
        SCENE.undo()
        state = SCENE.state()
        undo_count, redo_count = len(SCENE.undo_stack), len(SCENE.redo_stack)
        self.session.sync_controls()
        self.assertEqual(SCENE.state(), state)
        self.assertEqual((len(SCENE.undo_stack), len(SCENE.redo_stack)), (undo_count, redo_count))
        SCENE.redo()
        state = SCENE.state()
        undo_count, redo_count = len(SCENE.undo_stack), len(SCENE.redo_stack)
        self.session.sync_controls()
        self.assertEqual(SCENE.state(), state)
        self.assertEqual((len(SCENE.undo_stack), len(SCENE.redo_stack)), (undo_count, redo_count))

    def test_cancel_can_be_undone_then_canceled_again_without_hidden_source(self):
        preview = self.start_preview()
        self.session.cancel(silent=True)
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])
        SCENE.undo()
        self.assertTrue(preview.alive)
        self.assertFalse(self.source.attrs["visibility"])
        self.session.cancel(silent=True)
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])

    def test_undo_keep_then_cancel_restores_source_without_deleting_kept_other_results(self):
        first = self.start_preview()
        self.session.keep()
        self.assertTrue(first.alive)
        self.assertFalse(first.attrs["cylinderResamplePreview"])
        self.assertTrue(self.source.attrs["visibility"])
        self.session.preview()
        second = SCENE.find(self.session._preview_node())
        self.session.keep()
        SCENE.undo()
        self.assertTrue(second.attrs["cylinderResamplePreview"])
        self.session.cancel(silent=True)
        self.assertFalse(second.alive)
        self.assertTrue(first.alive)
        self.assertTrue(self.source.attrs["visibility"])

    def test_source_switch_then_undo_cleans_preview_owned_by_original_source(self):
        first = self.start_preview()
        second_source = SCENE.add_source("SecondSource")
        SCENE.selection = second_source
        self.session.read_selection()
        self.assertFalse(first.alive)
        self.assertTrue(self.source.attrs["visibility"])
        SCENE.undo()
        self.assertTrue(first.alive)
        self.assertFalse(self.source.attrs["visibility"])
        self.session.cancel(silent=True)
        self.assertFalse(first.alive)
        self.assertTrue(self.source.attrs["visibility"])
        self.assertTrue(second_source.attrs["visibility"])

    def test_source_switch_undo_compare_and_keep_use_original_preview_source(self):
        first = self.start_preview()
        second_source = SCENE.add_source("SecondSource")
        SCENE.selection = second_source
        self.session.read_selection()
        SCENE.undo()
        second_source.attrs["visibility"] = False
        SCENE.controls["compare"]["value"] = True
        self.session.compare()
        self.assertTrue(self.source.attrs["visibility"])
        self.assertFalse(second_source.attrs["visibility"])
        self.assertFalse(first.attrs["visibility"])
        self.session.keep()
        self.assertTrue(self.source.attrs["visibility"])
        self.assertFalse(second_source.attrs["visibility"])
        self.assertTrue(first.alive)
        self.assertTrue(first.attrs["visibility"])
        self.assertFalse(first.attrs["cylinderResamplePreview"])

    def test_close_after_deleted_source_still_removes_preview(self):
        preview = self.start_preview()
        self.source.alive = False
        self.session.close()
        self.assertFalse(preview.alive)

    def test_close_after_preview_was_deleted_restores_hidden_original(self):
        preview = self.start_preview()
        preview.alive = False
        self.assertFalse(self.source.attrs["visibility"])
        self.session.close()
        self.assertTrue(self.source.attrs["visibility"])

    def test_undo_keep_sync_then_manual_preview_delete_cancel_restores_original(self):
        preview = self.start_preview()
        self.session.keep()
        SCENE.undo()
        self.assertTrue(preview.attrs["cylinderResamplePreview"])
        self.assertFalse(self.source.attrs["visibility"])
        self.session.sync_controls()
        preview.alive = False
        self.session.cancel(silent=True)
        self.assertTrue(self.source.attrs["visibility"])

    def test_redo_creation_sync_then_manual_preview_delete_cancel_restores_original(self):
        preview = self.start_preview()
        SCENE.undo()
        self.session.sync_controls()
        SCENE.redo()
        self.assertTrue(preview.alive)
        self.assertTrue(preview.attrs["cylinderResamplePreview"])
        self.assertFalse(self.source.attrs["visibility"])
        self.session.sync_controls()
        preview.alive = False
        self.session.cancel(silent=True)
        self.assertTrue(self.source.attrs["visibility"])

    def test_close_after_cancel_preserves_later_user_visibility_change(self):
        self.start_preview()
        self.session.cancel(silent=True)
        self.source.attrs["visibility"] = False
        self.session.close()
        self.assertFalse(self.source.attrs["visibility"])

    def test_close_after_keep_preserves_later_user_visibility_change(self):
        self.start_preview()
        self.session.keep()
        self.source.attrs["visibility"] = False
        self.session.close()
        self.assertFalse(self.source.attrs["visibility"])

    def test_undo_preview_creation_sync_then_user_hide_is_preserved_by_cancel(self):
        preview = self.start_preview()
        SCENE.undo()
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])
        self.session.sync_controls()
        self.source.attrs["visibility"] = False
        self.session.cancel(silent=True)
        self.assertFalse(self.source.attrs["visibility"])

    def test_reopening_after_close_undo_recovers_resurrected_preview_ownership(self):
        preview = self.start_preview()
        self.ui_module._SESSION = self.session
        self.session.close()
        SCENE.undo()
        self.assertTrue(preview.alive)
        self.assertFalse(self.source.attrs["visibility"])
        with patch.object(self.ui_module.Session, "make_window", lambda session: "FixtureWindow"), \
                patch.object(self.ui_module, "ensure_supported", return_value=2024):
            self.ui_module.show()
        reopened = self.ui_module._SESSION
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])
        SCENE.undo()
        self.assertEqual(reopened._preview_node(), preview.path)
        reopened.close()
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])

    def test_scene_reset_clears_session_and_does_not_modify_new_name_collision(self):
        self.start_preview()
        old_source_path = self.source.path
        old_preview_path = self.session._preview_node()
        SCENE.reset_scene()
        new_source = SCENE.add_source(visible=False)
        new_preview = FakeNode(old_preview_path, SCENE.epoch, preview=True, visible=False)
        new_preview.attrs["cylinderResamplePreview"] = True
        SCENE.nodes.append(new_preview)
        self.assertEqual(new_source.path, old_source_path)
        self.session.scene_changed()
        self.assertIsNone(self.session.snapshot)
        self.assertIsNone(self.session._preview_node())
        self.session.close()
        self.assertFalse(new_source.attrs["visibility"])
        self.assertTrue(new_preview.alive)
        self.assertFalse(new_preview.attrs["visibility"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
