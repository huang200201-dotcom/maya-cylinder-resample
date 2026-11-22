"""Scoped secondary UV texture links with sparse Maya UV indices."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cylinder_resample.material_uv_links import copy_texture_uv_links


class TextureLinkTests(unittest.TestCase):
    def setUp(self):
        self.source, self.output = "|asset|sourceShape", "|result|outputShape"
        self.sets = {self.source: {0: "beauty", 5: "lightmap", 8: "mask"},
                     self.output: {0: "beauty", 1: "mask", 2: "lightmap"}}
        self.plugs = {"{}.uvSet[{}].uvSetName".format(shape, index): name
                      for shape, names in self.sets.items() for index, name in names.items()}
        self.source_plug = self.source + ".uvSet[5].uvSetName"
        self.output_plug = self.output + ".uvSet[2].uvSetName"
        self.bindings = {self.source_plug: [":character:albedo", "unusedTexture"],
                         self.source + ".uvSet[0].uvSetName": ["sceneDefaultTexture"]}
        self.cmds = Mock()
        self.cmds.polyUVSet.side_effect = lambda shape, **kwargs: list(self.sets[shape])
        self.cmds.getAttr.side_effect = self.plugs.__getitem__
        self.cmds.listHistory.return_value = ["usedSG", "surface", "character:albedo", "sceneDefaultTexture"]

        def uv_link(uvSet, query=False, make=False, texture=None):
            if query:
                return self.bindings.get(uvSet)
            self.assertTrue(make)
            self.assertTrue(uvSet.startswith(self.output + "."))
            self.bindings.setdefault(uvSet, []).append(texture)

        self.cmds.uvLink.side_effect = uv_link

    def copy(self, sets=("beauty", "lightmap", "mask"), groups=(":usedSG",)):
        return copy_texture_uv_links(self.cmds, self.source, self.output, sets, groups)

    def test_copies_only_assigned_texture_and_resolves_destination_set_by_name(self):
        before = copy.deepcopy(self.bindings)
        self.assertEqual(self.copy(), 1)
        self.assertEqual(self.bindings, dict(before, **{self.output_plug: [":character:albedo"]}))
        self.assertEqual(self.copy(), 0)
        self.assertEqual(self.bindings[self.output_plug], [":character:albedo"])

    def test_skips_implicit_single_or_disabled_uv_and_unassigned_materials(self):
        for sets, groups in (((), ("sg",)), (("map1",), ("sg",)), (("map1", "detail"), ())):
            self.assertEqual(self.copy(sets=sets, groups=groups), 0)
        self.cmds.polyUVSet.assert_not_called()
        self.cmds.uvLink.assert_not_called()

    def test_prunes_unrelated_textures_before_querying_output(self):
        self.cmds.listHistory.return_value = ["differentShader"]
        self.assertEqual(self.copy(), 0)
        self.assertEqual(self.cmds.polyUVSet.call_count, 1)

    def test_ambiguous_texture_on_multiple_sets_fails_before_writing(self):
        before = copy.deepcopy(self.bindings)
        self.bindings[self.source + ".uvSet[8].uvSetName"] = "character:albedo"
        with self.assertRaisesRegex(RuntimeError, "多个 UV 集"):
            self.copy()
        self.assertNotIn(self.output_plug, self.bindings)
        self.assertEqual(self.bindings[self.source_plug], before[self.source_plug])

    def test_missing_and_unapplied_target_associations_report_failure(self):
        self.sets[self.output].pop(2)
        with self.assertRaisesRegex(RuntimeError, "缺少贴图所需的 UV 集"):
            self.copy()
        self.sets[self.output][2] = "lightmap"
        self.cmds.uvLink.side_effect = lambda uvSet, **kwargs: self.bindings.get(uvSet)
        with self.assertRaisesRegex(RuntimeError, "关联校验失败"):
            self.copy()
