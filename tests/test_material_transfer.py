"""Exercise shader writes and read-back with Maya-style per-instance tables."""

import re
import unittest
from unittest.mock import Mock, patch

import test_ui_state as state


class MaterialTransferTests(unittest.TestCase):
    def setUp(self):
        self.adapter = state.load_adapter_module()
        self.dag = Mock()
        self.dag.instanceNumber.return_value = 0
        self.mesh = Mock()
        self.groups = []
        self.writes = []
        self.hide_failure = False
        self.mesh.getConnectedShaders.side_effect = self.read
        for owner, name, value in (
                (self.adapter, "_dag", lambda shape: self.dag),
                (self.adapter.om, "MFnMesh", lambda dag: self.mesh),
                (self.adapter.om, "MFnDependencyNode", lambda text: Mock(absoluteName=lambda: text)),
                (self.adapter.cmds, "sets", self.assign)):
            handle = patch.object(owner, name, value, create=True)
            handle.start()
            self.addCleanup(handle.stop)

    def read(self, instance):
        self.assertEqual(instance, 0)
        names = sorted({name for name in self.groups if name is not None}, reverse=True)
        return names, [names.index(group) if group else -1 for group in self.groups]

    def assign(self, members, edit, forceElement=None, remove=None):
        self.assertTrue(edit)
        self.writes.append((members, forceElement, remove))
        if remove:
            self.groups = [None if value == remove else value for value in self.groups]
        elif not self.hide_failure:
            if members == "|result":
                self.groups = [forceElement] * len(self.groups)
            else:
                for member in members:
                    match = re.fullmatch(r"\|result\.f\[(\d+)(?::(\d+))?\]", member)
                    start, end = int(match[1]), int(match[2] or match[1])
                    self.groups[start:end + 1] = [forceElement] * (end + 1 - start)

    def write(self, materials, assignments, sources):
        self.mesh.numPolygons = len(sources)
        self.groups = [":initialShadingGroup"] * len(sources)
        self.adapter._write_materials("|result", "|result|shape",
                                      dict(materials=materials, assignments=assignments), sources)

    def test_reduce_reuses_distinct_namespaced_groups_and_keeps_unassigned_faces(self):
        self.write([":one:metalSG", ":two:metalSG"], [1, 1, -1, 0, 1, 0], [1, 2, 3, 5])
        self.assertEqual(self.groups, [":two:metalSG", None, ":one:metalSG", ":one:metalSG"])
        self.assertIn((["|result.f[2:3]"], ":one:metalSG", None), self.writes)

    def test_whole_mesh_material_and_fully_unassigned_mesh(self):
        self.write([":paintSG"], [0, 0, 0, 0], [0, 2, 3])
        self.assertEqual(self.writes, [("|result", ":paintSG", None)])
        self.write([], [-1, -1, -1], [1, 2])
        self.assertEqual(self.groups, [None, None])

    def test_failed_maya_assignment_cannot_pass_verification(self):
        self.hide_failure = True
        with self.assertRaisesRegex(self.adapter.ToolError, "逐面材质"):
            self.write([":paintSG"], [0, 0, 0], [0, 2])

    def test_lost_material_stops_result_before_any_assignment(self):
        with self.assertRaisesRegex(self.adapter.ToolError, "全部材质分区"):
            self.write([":baseSG", ":trimSG"], [0, 1, 0], [0, 2])
        self.assertEqual(self.writes, [])

    def test_empty_native_table_and_corrupt_indices(self):
        self.mesh.numPolygons = 3
        self.mesh.getConnectedShaders.side_effect = None
        self.mesh.getConnectedShaders.return_value = ([], [])
        self.assertEqual(self.adapter._shader_table(self.mesh, 0), ([], [-1] * 3))
        self.mesh.getConnectedShaders.return_value = ([":paintSG"], [0, -2, 0])
        with self.assertRaises(self.adapter.ToolError):
            self.adapter._shader_table(self.mesh, 0)
