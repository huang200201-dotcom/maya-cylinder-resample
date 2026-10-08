"""Constraint diagnostics and cache lifecycle without Maya DLLs."""

import types
import unittest
from unittest.mock import Mock, patch

import test_ui_state as state


class ConstraintUiTests(unittest.TestCase):
    def setUp(self):
        state.SCENE = state.FakeScene()
        self.scene = state.SCENE
        self.source = self.scene.add_source()
        self.scene.selection = self.source
        self.ui = state.load_session_type(return_module=True)
        self.session = self.ui.Session()
        self.session.controls = {name: name for name in self.scene.controls}
        self.analyzer = Mock(side_effect=self.build_constraints)
        self.summarizer = Mock(side_effect=self.summarize_constraints)
        self.preferences = []
        for target, name, replacement in (
                (self.ui.adapter, "analyze_constraints", self.analyzer),
                (self.ui.adapter, "constraint_summary", self.summarizer),
                (self.ui.cmds, "optionVar", lambda **kwargs: self.preferences.append(kwargs))):
            patcher = patch.object(target, name, replacement, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def build_constraints(snapshot, analysis, include_uvs=True):
        return types.SimpleNamespace(
            snapshot=snapshot, topology=analysis, uv_analyzed=include_uvs,
            uv_sets=(("map1", frozenset(range(6))),
                     ("lightmap", frozenset((4, 5, 6, 7)))) if include_uvs else (),
            materials=frozenset((6, 8)), hard=frozenset((8, 9)))

    def summarize_constraints(self, snapshot, analysis, preserve_uvs, preserve_hard_edges, constraints=None):
        self.assertIsNotNone(constraints)
        self.assertIs(constraints.snapshot, snapshot)
        self.assertIs(constraints.topology, analysis)
        self.assertTrue(not preserve_uvs or constraints.uv_analyzed)
        uv = set().union(*(columns for _, columns in constraints.uv_sets)) if preserve_uvs else set()
        hard = constraints.hard if preserve_hard_edges else set()
        protected = uv.union(constraints.materials, hard)
        return {
            "protected_count": len(protected), "minimum_count": max(3, len(protected)),
            "uv_count": len(uv), "hard_count": len(hard),
            "material_count": len(constraints.materials),
            "uv_set_counts": tuple((name, len(columns)) for name, columns in constraints.uv_sets)
            if preserve_uvs else (),
            "ignored_cap_hard_edges": 3 if preserve_hard_edges else 0,
        }

    def read(self):
        self.session.read_selection()
        return self.session._constraint_analysis

    def status(self):
        return self.scene.controls["status"]["text"]

    def test_read_selection_precomputes_and_reuses_the_same_constraint_object(self):
        cache = self.read()
        self.analyzer.assert_called_once_with(
            self.session.snapshot, self.session.analysis, include_uvs=True)
        self.assertIs(cache.snapshot, self.session.snapshot)
        self.assertIs(cache.topology, self.session.analysis)
        self.assertIs(self.session._constraint_snapshot, self.session.snapshot)
        self.assertIs(self.session._constraint_topology, self.session.analysis)
        self.assertIs(self.summarizer.call_args[1]["constraints"], cache)
        for _ in range(10):
            self.session._summary()
        self.assertEqual(self.analyzer.call_count, 1)
        self.assertTrue(all(call[1]["constraints"] is cache for call in self.summarizer.call_args_list))

    def test_count_mode_beside_and_hard_edge_changes_do_not_repeat_analysis(self):
        cache = self.read()
        for index in range(24):
            self.scene.controls["count"]["value"] = 3 + index
            self.scene.controls["mode"]["select"] = 1 + index % 3
            self.scene.controls["beside"]["value"] = bool(index % 2)
            self.scene.controls["hard"]["value"] = bool(index % 2)
            self.session.params_changed()
            self.session._summary()
        self.session.set_count(0.5)
        self.session.set_count(2)
        self.assertEqual(self.analyzer.call_count, 1)
        self.assertIs(self.session._constraint_analysis, cache)
        self.assertTrue(self.preferences)

    def test_uv_disabled_first_analysis_skips_uv_scan_and_reports_it_disabled(self):
        self.scene.controls["uv"]["value"] = False
        cache = self.read()
        self.assertFalse(cache.uv_analyzed)
        self.analyzer.assert_called_once_with(
            self.session.snapshot, self.session.analysis, include_uvs=False)
        text = self.status()
        self.assertIn("UV 已关闭", text)
        self.assertIn("结果不生成 UV", text)
        self.assertIn("UV 接缝：0", text)
        self.assertIn("材质边界：2", text)
        self.assertIn("硬边：2", text)
        self.assertIn("最低目标段数：3", text)
        self.assertNotIn("各 UV 集约束", text)

    def test_enabling_uv_once_completes_cache_and_future_toggles_reuse_it(self):
        self.scene.controls["uv"]["value"] = False
        partial = self.read()
        self.scene.controls["uv"]["value"] = True
        self.session.params_changed()
        complete = self.session._constraint_analysis
        self.assertIsNot(complete, partial)
        self.assertTrue(complete.uv_analyzed)
        self.assertEqual(self.analyzer.call_count, 2)
        self.assertEqual(self.analyzer.call_args[1], {"include_uvs": True})
        for keep_uv in (False, True, False, True):
            self.scene.controls["uv"]["value"] = keep_uv
            self.session.params_changed()
            self.session._summary()
            self.assertIs(self.session._constraint_analysis, complete)
        self.assertEqual(self.analyzer.call_count, 2)

    def test_disabling_uv_releases_only_uv_and_reuses_a_complete_cache(self):
        cache = self.read()
        self.scene.controls["uv"]["value"] = False
        self.session.params_changed()
        text = self.status()
        self.assertIn("约束列：3", text)
        self.assertIn("UV 接缝：0", text)
        self.assertIn("材质边界：2", text)
        self.assertIn("硬边：2", text)
        self.assertIs(self.session._constraint_analysis, cache)
        self.assertEqual(self.analyzer.call_count, 1)
        self.assertEqual(self.summarizer.call_args[0][2:], (False, True))
        self.scene.controls["hard"]["value"] = False
        self.session.params_changed()
        self.assertIn("约束列：2", self.status())
        self.assertIn("材质边界：2", self.status())
        self.assertIn("硬边：0", self.status())
        self.assertEqual(self.analyzer.call_count, 1)

    def test_explicit_reanalysis_of_the_same_source_creates_a_fresh_cache(self):
        first = self.read()
        first_snapshot, first_topology = self.session.snapshot, self.session.analysis
        second = self.read()
        self.assertEqual(self.analyzer.call_count, 2)
        self.assertIsNot(second, first)
        self.assertIsNot(self.session.snapshot, first_snapshot)
        self.assertIsNot(self.session.analysis, first_topology)
        self.assertIs(second.snapshot, self.session.snapshot)
        self.assertIs(second.topology, self.session.analysis)

    def test_changing_source_replaces_cache_and_cleans_the_previous_preview(self):
        first = self.read()
        self.session.preview()
        preview = self.scene.find(self.session._preview_node())
        second_source = self.scene.add_source("SecondSource")
        self.scene.selection = second_source
        second = self.read()
        self.assertIsNot(second, first)
        self.assertEqual(self.analyzer.call_count, 2)
        self.assertEqual(second.snapshot["shape"], second_source.path)
        self.assertFalse(preview.alive)
        self.assertTrue(self.source.attrs["visibility"])
        self.assertTrue(second_source.attrs["visibility"])

    def test_scene_changed_discards_cache_before_a_new_scene_is_analyzed(self):
        first = self.read()
        self.session.preview()
        self.scene.reset_scene()
        replacement = self.scene.add_source()
        self.scene.selection = replacement
        self.session.scene_changed()
        self.assertIsNone(self.session._constraint_analysis)
        self.assertIsNone(self.session._constraint_snapshot)
        self.assertIsNone(self.session._constraint_topology)
        self.assertIsNone(self.session.snapshot)
        self.assertIsNone(self.session.analysis)
        self.assertEqual(self.session._summary(), "等待分析")
        self.assertEqual(self.analyzer.call_count, 1)
        second = self.read()
        self.assertIsNot(second, first)
        self.assertEqual(self.analyzer.call_count, 2)
        self.assertTrue(replacement.attrs["visibility"])

    def test_replacing_snapshot_or_topology_invalidates_the_lazy_cache(self):
        first = self.read()
        self.session.snapshot = dict(self.session.snapshot)
        self.session._summary()
        second = self.session._constraint_analysis
        self.assertIsNot(second, first)
        self.assertIs(second.snapshot, self.session.snapshot)
        self.session.analysis = dict(self.session.analysis)
        self.session._summary()
        third = self.session._constraint_analysis
        self.assertIsNot(third, second)
        self.assertIs(third.topology, self.session.analysis)
        self.assertEqual(self.analyzer.call_count, 3)

    def test_failed_new_constraint_analysis_preserves_old_analysis_and_preview(self):
        cache = self.read()
        self.session.preview()
        snapshot, topology = self.session.snapshot, self.session.analysis
        preview = self.scene.find(self.session._preview_node())
        previous_state = self.scene.state()
        undo_count = len(self.scene.undo_stack)
        second_source = self.scene.add_source("SecondSource")
        self.scene.selection = second_source
        self.analyzer.side_effect = RuntimeError("Fixture constraint analysis failed")
        with self.assertRaisesRegex(RuntimeError, "constraint analysis failed"):
            self.session.read_selection()
        self.assertIs(self.session.snapshot, snapshot)
        self.assertIs(self.session.analysis, topology)
        self.assertIs(self.session._constraint_analysis, cache)
        self.assertIs(self.session._constraint_snapshot, snapshot)
        self.assertIs(self.session._constraint_topology, topology)
        self.assertTrue(preview.alive)
        self.assertEqual(self.session._preview_node(), preview.path)
        self.assertFalse(self.source.attrs["visibility"])
        self.assertTrue(second_source.attrs["visibility"])
        self.assertEqual(len(self.scene.undo_stack), undo_count)
        self.assertEqual(self.scene.state()[:-1], previous_state)
        self.session._summary()
        self.assertEqual(self.analyzer.call_count, 2)

    def test_failed_uv_cache_completion_preserves_preview_and_can_be_retried(self):
        self.scene.controls["uv"]["value"] = False
        partial = self.read()
        self.session.preview()
        preview = self.scene.find(self.session._preview_node())
        snapshot, topology = self.session.snapshot, self.session.analysis
        self.scene.controls["uv"]["value"] = True
        self.analyzer.side_effect = ValueError("Fixture UV scan failed")
        with self.assertRaisesRegex(ValueError, "UV scan failed"):
            self.session.params_changed()
        self.assertIs(self.session._constraint_analysis, partial)
        self.assertIs(self.session.snapshot, snapshot)
        self.assertIs(self.session.analysis, topology)
        self.assertTrue(preview.alive)
        self.assertFalse(self.source.attrs["visibility"])
        self.analyzer.side_effect = self.build_constraints
        self.session.params_changed()
        self.assertTrue(self.session._constraint_analysis.uv_analyzed)
        self.assertIsNot(self.session._constraint_analysis, partial)
        self.assertTrue(preview.alive)
        self.assertIn("当前预览尚未更新", self.status())

    def test_summary_reports_each_uv_set_and_overlapping_boundary_counts(self):
        self.read()
        self.session.snapshot["warnings"].append("Fixture source warning")
        text = self.session._summary()
        self.assertIn("各 UV 集约束：map1：6 列；lightmap：4 列", text)
        self.assertIn("UV 接缝：8", text)
        self.assertIn("材质边界：2", text)
        self.assertIn("硬边：2", text)
        self.assertIn("列，可重叠", text)
        self.assertIn("约束列：10", text)
        self.assertIn("最低目标段数：10", text)
        self.assertIn("共面端盖冗余硬边：3 条，不锁定段数", text)
        self.assertIn("Fixture source warning", text)

    def test_minimum_and_low_target_warning_change_immediately_with_toggles(self):
        self.read()
        self.scene.controls["count"]["value"] = 5
        self.session.params_changed()
        self.assertIn("最低目标段数：10", self.status())
        self.assertIn("当前目标低于最低段数", self.status())
        self.scene.controls["uv"]["value"] = False
        self.session.params_changed()
        self.assertIn("最低目标段数：3", self.status())
        self.assertNotIn("当前目标低于最低段数", self.status())
        self.scene.controls["hard"]["value"] = False
        self.session.params_changed()
        self.assertIn("最低目标段数：3", self.status())
        self.assertIn("材质边界：2", self.status())
        self.assertIn("硬边：0", self.status())
        self.scene.controls["uv"]["value"] = True
        self.session.params_changed()
        self.assertIn("最低目标段数：9", self.status())
        self.assertIn("当前目标低于最低段数", self.status())
        self.assertEqual(self.analyzer.call_count, 1)


if __name__ == "__main__":
    unittest.main()
