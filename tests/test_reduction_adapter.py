"""Regression checks for reducible fan caps and explicit column constraints."""

import copy
import json
import math
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from cylinder_resample import core
from test_resample import cylinder, strip_uvs
import test_ui_state as state


def fan_snapshot(n=20, all_cap_hard=True):
    points, faces, seed = cylinder(n=n, caps="fan")
    uv_sets = strip_uvs(faces, n=n)
    data = uv_sets["map1"]
    data["ids"] = data["ids"][:n * 4]
    analysis = core.analyze_mesh(points, faces, seed)
    smoothing = {}
    # Maya can assign a distinct UV index to every coincident fan-center corner.
    for cap in analysis["caps"]:
        uv_ids = {}
        for vertex in analysis["rings"][cap["ring"]]:
            uv_ids[vertex] = len(data["u"])
            data["u"].append(0.5 + points[vertex][0] * 0.2)
            data["v"].append(0.5 + points[vertex][1] * 0.2)
            smoothing[tuple(sorted((vertex, cap["center"])))] = not all_cap_hard
        for face_id in cap["faces"]:
            center_id = len(data["u"])
            data["u"].append(0.5)
            data["v"].append(0.5)
            data["ids"].extend(center_id if vertex == cap["center"] else uv_ids[vertex]
                               for vertex in faces[face_id])
    return {"points": points, "faces": faces, "seed_edges": seed,
            "assignments": [0] * len(faces), "smoothing": smoothing,
            "uv_sets": uv_sets, "fingerprint": "reduction-fixture",
            "shape": "|Source|SourceShape", "transform": "|Source",
            "metric_points": list(points), "materials": [], "matrix": [],
            "current_uv_set": "map1", "display_smooth_mesh": 0,
            "smooth_level": 2, "warnings": []}


class ReductionAdapterTests(unittest.TestCase):
    def setUp(self):
        state.SCENE = state.FakeScene()
        self.adapter = state.load_adapter_module()

    def build(self, snapshot, target, mode="contour", preserve_uvs=True,
              preserve_hard_edges=True):
        payloads = []

        def create(**kwargs):
            payloads.append(json.loads(kwargs["data"]))
            return "|Result"

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.adapter, "snapshot_mesh", return_value=snapshot))
            stack.enter_context(patch.object(self.adapter, "_ensure_mesh_command", return_value="createFixture"))
            stack.enter_context(patch.object(self.adapter.cmds, "createFixture", create, create=True))
            for name in ("sets", "setAttr", "addAttr", "select"):
                stack.enter_context(patch.object(self.adapter.cmds, name, Mock(), create=True))
            stack.enter_context(patch.object(self.adapter.cmds, "listRelatives",
                                            return_value=["|Result|ResultShape"], create=True))
            stack.enter_context(patch.object(self.adapter.cmds, "ls", return_value=["|Result"], create=True))
            built = self.adapter.build_result(snapshot, target, mode, preserve_uvs,
                                              preserve_hard_edges, _undo_chunk=False)
        self.assertEqual(len(payloads), 1)
        return built["result"], payloads[0]

    def test_twenty_segment_maya_fan_with_hard_caps_reduces_in_all_modes(self):
        snapshot = fan_snapshot()
        before = copy.deepcopy(snapshot)
        analysis = self.adapter.analyze(snapshot)
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["minimum_count"], 3)
        self.assertEqual(summary["uv_count"], 1)
        self.assertEqual(summary["hard_count"], 0)
        self.assertEqual(summary["ignored_cap_hard_edges"], 40)
        self.assertEqual(self.adapter._protected_columns(snapshot, analysis, True), [])
        for target in (12, 10, 6):
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(target=target, mode=mode):
                    result, payload = self.build(snapshot, target, mode)
                    self.assertEqual([len(ring) for ring in result["new_rings"]], [target, target])
                    self.assertEqual(len(result["points"]), target * 2 + 2)
                    self.assertEqual(len(result["faces"]), target * 3)
                    self.assertEqual(set(result["uv_sets"]), {"map1"})
                    uv = result["uv_sets"]["map1"]
                    self.assertEqual(uv["counts"], [len(face) for face in result["faces"]])
                    self.assertEqual(sum(uv["counts"]), len(uv["ids"]))
                    smoothing = {tuple(edge): smooth for edge, smooth in payload["smoothing"]}
                    for cap in analysis["caps"]:
                        center = result["old_to_new"][cap["center"]]
                        for vertex in result["new_rings"][cap["ring"]]:
                            self.assertFalse(smoothing[tuple(sorted((center, vertex)))])
                    if target in (12, 6):
                        self.assertTrue(any(abs(value - round(value)) > 1e-8
                                            for value in result["samples"]))
                    self.assertEqual(snapshot, before)

    def test_uniform_hard_fan_does_not_override_single_cap_crease(self):
        snapshot = fan_snapshot(all_cap_hard=False)
        analysis = self.adapter.analyze(snapshot)
        cap = analysis["caps"][0]
        source_vertex = analysis["rings"][cap["ring"]][5]
        snapshot["smoothing"][tuple(sorted((source_vertex, cap["center"])))] = False
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["hard_count"], 1)
        self.assertEqual(summary["ignored_cap_hard_edges"], 0)
        self.assertEqual(self.adapter._protected_columns(snapshot, analysis, True), [5])
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                result, payload = self.build(snapshot, 6, mode)
                self.assertIn(5, result["samples"])
                column = result["samples"].index(5)
                radial = tuple(sorted((result["old_to_new"][cap["center"]],
                                       result["new_rings"][cap["ring"]][column])))
                smoothing = {tuple(edge): smooth for edge, smooth in payload["smoothing"]}
                self.assertFalse(smoothing[radial])

    def test_circle_smoothing_uses_each_ring_actual_sampling_parameters(self):
        snapshot = fan_snapshot(n=12)
        angles = (0, 10, 20, 30, 60, 110, 160, 200, 240, 280, 315, 345)
        for column, degrees in enumerate(angles):
            angle = math.radians(degrees)
            snapshot["points"][12 + column] = (math.cos(angle), math.sin(angle), 2.0)
        snapshot["metric_points"] = list(snapshot["points"])
        snapshot["smoothing"][(14, 15)] = False
        result, payload = self.build(snapshot, 9, mode="circle")
        top = result["analysis"]["rings"].index(list(range(12, 24)))
        parameters = result["ring_samples"][top]
        self.assertGreater(parameters[1], 3.0)
        self.assertLess(result["samples"][1], 2.0)
        smoothing = {tuple(edge): smooth for edge, smooth in payload["smoothing"]}
        edge = tuple(sorted(result["new_rings"][top][:2]))
        self.assertFalse(smoothing[edge])
        self.assertAlmostEqual(result["stats"]["worst_spacing_ratio"], 1.0)

    def test_circle_odd_spacing_reports_hard_constraints_with_uv_disabled(self):
        snapshot = fan_snapshot(n=12)
        for column in (0, 3, 6, 9):
            snapshot["smoothing"][(column, column + 12)] = False
        result, _ = self.build(snapshot, 9, mode="circle", preserve_uvs=False)
        self.assertTrue(result["stats"]["spacing_limited_by_constraints"])
        self.assertEqual(result["stats"]["compatible_counts"], (8, 12))
        self.assertTrue(any("UV 接缝 0 列、材质边界 0 列、硬边 4 列" in message
                            for message in result["warnings"]))
        self.assertEqual(result["uv_sets"], {})
        released, _ = self.build(snapshot, 9, mode="circle", preserve_uvs=False,
                                  preserve_hard_edges=False)
        self.assertFalse(released["stats"]["spacing_limited_by_constraints"])
        self.assertAlmostEqual(released["stats"]["worst_spacing_ratio"], 1.0)

    def test_nonplanar_all_hard_cap_keeps_its_twenty_constraints(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        cap = analysis["caps"][0]
        center = cap["center"]
        x, y, z = snapshot["points"][center]
        snapshot["points"][center] = (x, y, z + 0.2)
        self.assertFalse(self.adapter._planar_fan(snapshot, cap))
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["hard_count"], 20)
        self.assertEqual(summary["minimum_count"], 20)
        self.assertEqual(summary["ignored_cap_hard_edges"], 20)
        self.assert_rejected_before_create(snapshot, 10, "硬边：20")

    def assert_rejected_before_create(self, snapshot, target, expected,
                                      preserve_uvs=True, preserve_hard_edges=True):
        before = copy.deepcopy(snapshot)
        with patch.object(self.adapter, "snapshot_mesh", return_value=snapshot), \
                patch.object(self.adapter, "_ensure_mesh_command") as create, \
                patch.object(self.adapter.cmds, "undoInfo") as undo:
            with self.assertRaises(self.adapter.ToolError) as caught:
                self.adapter.build_result(snapshot, target, preserve_uvs=preserve_uvs,
                                          preserve_hard_edges=preserve_hard_edges)
        create.assert_not_called()
        undo.assert_not_called()
        self.assertIsInstance(caught.exception.__cause__, core.ConstraintError)
        self.assertIn(expected, str(caught.exception))
        self.assertEqual(snapshot, before)
        return str(caught.exception)

    def test_actual_twenty_side_hard_edges_report_optional_hard_switch(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        first, second = analysis["rings"]
        for a, b in zip(first, second):
            snapshot["smoothing"][tuple(sorted((a, b)))] = False
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["hard_count"], 20)
        self.assertEqual(summary["minimum_without_hard"], 3)
        message = self.assert_rejected_before_create(snapshot, 10, "硬边：20")
        self.assertIn("取消“保留硬边”", message)
        self.assertIn("仍可保留 UV", message)
        result, payload = self.build(snapshot, 10, preserve_hard_edges=False)
        self.assertEqual(len(result["samples"]), 10)
        self.assertEqual(set(result["uv_sets"]), {"map1"})
        self.assertEqual(payload["smoothing"], [])

    def test_actual_twenty_uv_islands_do_not_suggest_disabling_hard_edges(self):
        snapshot = fan_snapshot()
        snapshot["uv_sets"] = strip_uvs(snapshot["faces"], n=20, separate_faces=True)
        analysis = self.adapter.analyze(snapshot)
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["uv_count"], 20)
        self.assertEqual(summary["hard_count"], 0)
        self.assertEqual(summary["minimum_without_hard"], 20)
        message = self.assert_rejected_before_create(snapshot, 10, "UV 接缝：20")
        self.assertNotIn("取消“保留硬边”", message)
        self.assertIn("真实 UV 分岛或材质边界", message)
        self.assertIn("主要限制 UV 集：map1（20 列）", message)
        result, _ = self.build(snapshot, 10, preserve_uvs=False)
        self.assertEqual(len(result["samples"]), 10)
        self.assertEqual(result["uv_sets"], {})

    def test_side_and_cap_material_boundaries_remain_with_protection_switches_off(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        snapshot["assignments"][:20] = [0] * 7 + [1] * 13
        cap = analysis["caps"][0]
        for column, face in enumerate(cap["faces"]):
            snapshot["assignments"][face] = 0 if column < 9 else 2
        summary = self.adapter.constraint_summary(snapshot, analysis, False, False)
        self.assertEqual(summary["material_count"], 3)
        self.assertEqual(summary["uv_count"], 0)
        self.assertEqual(summary["hard_count"], 0)
        self.assertEqual(self.adapter._protected_columns(snapshot, analysis, False), [0, 7, 9])
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                result, _ = self.build(snapshot, 6, mode, False, False)
                self.assertTrue({0, 7, 9}.issubset(result["samples"]))
                materials = {snapshot["assignments"][face] for face in result["face_sources"]}
                self.assertEqual(materials, {0, 1, 2})

    def test_no_uv_sets_reduce_with_either_uv_switch_state(self):
        snapshot = fan_snapshot()
        snapshot["uv_sets"] = {}
        before = copy.deepcopy(snapshot)
        for preserve_uvs in (False, True):
            with self.subTest(preserve_uvs=preserve_uvs):
                summary = self.adapter.constraint_summary(snapshot, self.adapter.analyze(snapshot),
                                                          preserve_uvs, True)
                self.assertEqual(summary["uv_count"], 0)
                self.assertEqual(summary["minimum_count"], 3)
                result, _ = self.build(snapshot, 6, preserve_uvs=preserve_uvs)
                self.assertEqual(len(result["samples"]), 6)
                self.assertEqual(result["uv_sets"], {})
                self.assertEqual(snapshot, before)

    def test_material_constraints_do_not_mislead_with_hard_switch_hint(self):
        snapshot = fan_snapshot()
        snapshot["assignments"][:20] = [column % 2 for column in range(20)]
        message = self.assert_rejected_before_create(snapshot, 10, "材质边界：20")
        self.assertNotIn("取消“保留硬边”", message)

    def test_cached_and_fresh_summaries_match_every_protection_switch_combination(self):
        snapshot = fan_snapshot()
        snapshot["uv_sets"]["lightmap"] = strip_uvs(snapshot["faces"], n=20,
                                                   separate_faces=True)["map1"]
        snapshot["assignments"][:20] = [0] * 7 + [1] * 13
        snapshot["smoothing"][(5, 25)] = False
        analysis = self.adapter.analyze(snapshot)
        constraints = self.adapter.analyze_constraints(snapshot, analysis)
        self.assertTrue(constraints.uv_analyzed)
        self.assertEqual(constraints.uv_columns, frozenset(range(20)))
        self.assertEqual(constraints.material_columns, frozenset((0, 7)))
        self.assertEqual(constraints.hard_columns, frozenset((5,)))
        self.assertEqual(len(constraints.per_band), 1)
        self.assertEqual(constraints.per_band[0].rings, (0, 1))
        self.assertEqual(dict(constraints.per_band[0].uv_by_set)["map1"], frozenset((0,)))
        self.assertEqual(constraints.per_band[0].material_columns, frozenset((0, 7)))
        self.assertEqual(constraints.per_band[0].hard_columns, frozenset((5,)))
        for keep_uv in (False, True):
            for keep_hard in (False, True):
                with self.subTest(keep_uv=keep_uv, keep_hard=keep_hard):
                    fresh = self.adapter.constraint_summary(snapshot, analysis, keep_uv, keep_hard)
                    with patch.object(core, "_parse_uvs", side_effect=AssertionError("cache reparsed UVs")):
                        cached = self.adapter.constraint_summary(snapshot, analysis, keep_uv, keep_hard,
                                                                 constraints=constraints)
                    self.assertEqual(cached, fresh)
                    self.assertEqual(cached["uv_enabled"], keep_uv)
                    if keep_uv:
                        self.assertEqual(cached["uv_set_counts"], (("lightmap", 20), ("map1", 1)))
                    else:
                        self.assertEqual(cached["uv_by_set"], ())
                        self.assertEqual(cached["uv_set_counts"], ())
                        self.assertEqual(cached["uv_count"], 0)

    def test_constraint_cache_does_not_retain_mutable_uv_working_data(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        parsed = core._parse_uvs(snapshot["uv_sets"], snapshot["faces"])
        with patch.object(core, "_parse_uvs", return_value=parsed):
            constraints = self.adapter.analyze_constraints(snapshot, analysis)
        expected = self.adapter.constraint_summary(snapshot, analysis, constraints=constraints)
        parsed["map1"]["corners"].clear()
        parsed["map1"]["u"].clear()
        self.assertEqual(self.adapter.constraint_summary(snapshot, analysis, constraints=constraints), expected)
        self.assertIs(constraints.snapshot, snapshot)
        self.assertIs(constraints.analysis, analysis)
        with self.assertRaises(AttributeError):
            constraints.uv_columns = frozenset()
        with self.assertRaises(AttributeError):
            constraints.uv_columns.add(7)
        with self.assertRaises(TypeError):
            constraints.uv_by_set[0] = ("bad", frozenset())
        with self.assertRaises(AttributeError):
            constraints.per_band[0].hard_columns.add(7)
        expected["uv_count"] = 100
        self.assertEqual(self.adapter.constraint_summary(snapshot, analysis, constraints=constraints)["uv_count"], 1)

    def test_constraint_cache_rejects_another_owner_or_changed_fingerprint(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        constraints = self.adapter.analyze_constraints(snapshot, analysis)
        for other_snapshot, other_analysis, cache in (
                (copy.deepcopy(snapshot), analysis, constraints),
                (snapshot, copy.deepcopy(analysis), constraints),
                (snapshot, analysis, {})):
            with self.subTest(snapshot=other_snapshot is snapshot, analysis=other_analysis is analysis), \
                    self.assertRaisesRegex(self.adapter.ToolError, "缓存与当前模型不一致"):
                self.adapter.constraint_summary(other_snapshot, other_analysis, constraints=cache)
        snapshot["fingerprint"] = "changed"
        with self.assertRaisesRegex(self.adapter.ToolError, "缓存与当前模型不一致"):
            self.adapter.constraint_summary(snapshot, analysis, constraints=constraints)

    def test_uv_disabled_does_not_parse_even_malformed_uv_data(self):
        snapshot = fan_snapshot()
        snapshot["uv_sets"] = {"broken": None}
        analysis = self.adapter.analyze(snapshot)
        with patch.object(core, "_parse_uvs", side_effect=AssertionError("disabled UVs were parsed")):
            constraints = self.adapter.analyze_constraints(snapshot, analysis, include_uvs=False)
            self.assertFalse(constraints.uv_analyzed)
            self.assertEqual(constraints.uv_by_set, ())
            self.assertEqual(constraints.uv_columns, frozenset())
            fresh = self.adapter.constraint_summary(snapshot, analysis, False, True)
            cached = self.adapter.constraint_summary(snapshot, analysis, False, True, constraints=constraints)
            self.assertEqual(fresh, cached)
            result, _ = self.build(snapshot, 10, preserve_uvs=False)
        self.assertEqual(len(result["samples"]), 10)
        self.assertEqual(result["uv_sets"], {})

    def test_cache_without_uv_analysis_can_enable_uv_with_one_fresh_parse(self):
        snapshot = fan_snapshot()
        analysis = self.adapter.analyze(snapshot)
        constraints = self.adapter.analyze_constraints(snapshot, analysis, include_uvs=False)
        expected = self.adapter.constraint_summary(snapshot, analysis, True, True)
        with patch.object(core, "_parse_uvs", wraps=core._parse_uvs) as parse:
            summary = self.adapter.constraint_summary(snapshot, analysis, True, True, constraints=constraints)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(summary, expected)
        self.assertFalse(constraints.uv_analyzed)
        self.assertEqual(constraints.uv_columns, frozenset())

    def test_no_unrequested_zero_anchor_increases_nonzero_boundary_minimum(self):
        snapshot = fan_snapshot(all_cap_hard=False)
        snapshot["uv_sets"] = {}
        for column in (3, 8, 14):
            snapshot["smoothing"][(column, column + 20)] = False
        analysis = self.adapter.analyze(snapshot)
        summary = self.adapter.constraint_summary(snapshot, analysis)
        self.assertEqual(summary["protected_count"], 3)
        self.assertEqual(summary["minimum_count"], 3)
        result, _ = self.build(snapshot, 3)
        self.assertEqual(result["samples"], [3.0, 8.0, 14.0])

    def test_uv_disabled_does_not_release_independent_side_hard_or_material_constraints(self):
        snapshot = fan_snapshot()
        snapshot["uv_sets"] = strip_uvs(snapshot["faces"], n=20, separate_faces=True)
        snapshot["assignments"][:20] = [column % 2 for column in range(20)]
        snapshot["smoothing"].update({(column, column + 20): False for column in range(20)})
        analysis = self.adapter.analyze(snapshot)
        summary = self.adapter.constraint_summary(snapshot, analysis, False, True)
        self.assertEqual(summary["uv_count"], 0)
        self.assertEqual(summary["hard_count"], 20)
        self.assertEqual(summary["material_count"], 20)
        message = self.assert_rejected_before_create(snapshot, 10, "UV 保护已关闭", preserve_uvs=False)
        self.assertIn("材质边界：20", message)
        self.assertIn("硬边：20", message)
        self.assertNotIn("真实 UV 分岛", message)
        self.assertNotIn("仍可保留 UV", message)
        self.assertNotIn("取消“保留硬边”", message)


class ReductionSessionTests(unittest.TestCase):
    def setUp(self):
        state.SCENE = state.FakeScene()
        self.source = state.SCENE.add_source()
        state.SCENE.selection = self.source
        self.ui = state.load_session_type(return_module=True)
        self.session = self.ui.Session()
        self.session.controls = {name: name for name in state.SCENE.controls}
        self.adapter = state.load_adapter_module()

    def test_real_reduction_constraint_error_keeps_previous_preview_and_source(self):
        self.session.read_selection()
        self.session.preview()
        preview = self.session._preview_node()
        snapshot = fan_snapshot()
        snapshot["shape_handle"] = state.FakeHandle(self.source)
        snapshot["uv_sets"] = strip_uvs(snapshot["faces"], n=20, separate_faces=True)
        self.session.snapshot = snapshot
        self.session.analysis = self.adapter.analyze(snapshot)
        state.SCENE.controls["count"]["value"] = 10
        before = state.SCENE.state()
        writes = list(state.SCENE.attribute_writes)
        records = list(self.session.preview_records)
        with patch.object(self.adapter, "snapshot_mesh", return_value=snapshot), \
                patch.object(self.adapter, "_ensure_mesh_command") as create, \
                patch.object(self.ui.adapter, "build_result", self.adapter.build_result):
            self.session.run(self.session.preview)
        create.assert_not_called()
        self.assertEqual(state.SCENE.state(), before)
        self.assertEqual(state.SCENE.attribute_writes, writes)
        self.assertEqual(self.session.preview_records, records)
        self.assertEqual(self.session._preview_node(), preview)
        self.assertFalse(self.source.attrs["visibility"])
        self.assertIn("UV 接缝：20", state.SCENE.controls["status"]["text"])
        self.assertTrue(state.SCENE.warnings)
        self.assertIsNone(state.SCENE.chunk)

    def test_summary_separates_uv_material_and_real_hard_column_counts(self):
        snapshot = fan_snapshot()
        snapshot["shape_handle"] = state.FakeHandle(self.source)
        self.session.snapshot = snapshot
        self.session.analysis = self.adapter.analyze(snapshot)
        with patch.object(self.ui.adapter, "constraint_summary", self.adapter.constraint_summary), \
                patch.object(self.ui.adapter, "analyze_constraints", self.adapter.analyze_constraints):
            summary = self.session._summary()
        self.assertIn("最低目标段数：3", summary)
        self.assertIn("UV 接缝：1", summary)
        self.assertIn("材质边界：0", summary)
        self.assertIn("硬边：0", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
