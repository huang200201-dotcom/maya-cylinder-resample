"""Reduction keeps real UV cuts without pinning duplicate fan-center UVs."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cylinder_resample import core
from test_resample import cylinder, face_uvs, strip_uvs


def cyclic_strip_uvs(faces, n=20, ring_count=2, seams=(1, 5, 9), missing_faces=()):
    pins = sorted(seams)
    u, v, counts, ids, cache = [], [], [], [], {}
    for face_id, face in enumerate(faces):
        if face_id in missing_faces:
            counts.append(0)
            continue
        counts.append(len(face))
        column = face_id % n
        start = max((pin for pin in pins if pin <= column), default=pins[-1])
        index = pins.index(start)
        end = pins[index + 1] if index + 1 < len(pins) else pins[0] + n
        for corner, vertex in enumerate(face):
            angular = column + (1 if corner in (1, 2) else 0)
            if angular < start:
                angular += n
            key = (start, angular, vertex // n)
            if key not in cache:
                cache[key] = len(u)
                u.append((angular - start) / (end - start))
                v.append((vertex // n) / (ring_count - 1))
            ids.append(cache[key])
    return {"u": u, "v": v, "counts": counts, "ids": ids}


def disk_uvs(points, faces, analysis, seams=(0,), same_coordinates=False, cyclic=False):
    """Planar cap rings share UVs; each fan triangle owns its center UV."""
    n = analysis["source_count"]
    band_count = len(analysis["bands"]) * n
    if cyclic:
        data = cyclic_strip_uvs(faces[:band_count], n=n, ring_count=len(analysis["rings"]),
                               seams=seams)
    else:
        data = strip_uvs(faces[:band_count], n=n, ring_count=len(analysis["rings"]),
                         seams=seams, same_coordinates=same_coordinates)["map1"]
    source_ids = list(data["ids"])
    data["counts"], data["ids"] = [], []
    centers = {cap["center"] for cap in analysis["caps"] if cap["kind"] == "fan"}
    cache, offset = {}, 0
    for face_id, face in enumerate(faces):
        data["counts"].append(len(face))
        if len({points[vertex][2] for vertex in face}) != 1:
            data["ids"].extend(source_ids[offset:offset + len(face)])
        else:
            for vertex in face:
                height = points[vertex][2]
                key = (height, vertex, face_id if vertex in centers else None)
                if key not in cache:
                    cache[key] = len(data["u"])
                    data["u"].append(0.5 + 0.25 * points[vertex][0])
                    data["v"].append(0.5 + 0.25 * points[vertex][1])
                data["ids"].append(cache[key])
        if face_id < band_count:
            offset += len(face)
    return data


def corner_id(data, faces, face_id, vertex):
    offset = sum(data["counts"][:face_id]) + faces[face_id].index(vertex)
    return data["ids"][offset]


def duplicate_corner(data, faces, face_id, vertex):
    offset = sum(data["counts"][:face_id]) + faces[face_id].index(vertex)
    original = data["ids"][offset]
    duplicate = len(data["u"])
    data["u"].append(data["u"][original])
    data["v"].append(data["v"][original])
    data["ids"][offset] = duplicate
    return original, duplicate


class ReductionUVTests(unittest.TestCase):
    def model(self, profile=((1.0, 0.0), (1.0, 2.0))):
        points, faces, seed = cylinder(n=20, profile=profile, caps="fan")
        analysis = core.analyze_mesh(points, faces, seed)
        return points, faces, seed, analysis

    def constraints(self, analysis, faces, uv_sets):
        return core._uv_constraints(analysis, core._parse_uvs(uv_sets, faces))

    def assert_valid(self, result, target):
        self.assertTrue(all(len(ring) == target for ring in result["new_rings"]))
        self.assertEqual(len(result["faces"]), len(result["face_sources"]))
        for face in result["faces"]:
            self.assertEqual(len(set(face)), len(face))
            self.assertTrue(all(0 <= vertex < len(result["points"]) for vertex in face))
        for data in result["uv_sets"].values():
            self.assertEqual(data["counts"], [len(face) for face in result["faces"]])
            self.assertEqual(sum(data["counts"]), len(data["ids"]))
            self.assertTrue(all(0 <= uv_id < len(data["u"]) for uv_id in data["ids"]))

    def assert_centers_not_welded(self, result, source, faces, analysis, target):
        for cap in analysis["caps"]:
            source_faces = set(cap["faces"])
            old_ids = {corner_id(source, faces, face_id, cap["center"])
                       for face_id in source_faces}
            center = result["old_to_new"][cap["center"]]
            observed = []
            for old_face, (face, ids, coordinates) in zip(result["face_sources"], face_uvs(result)):
                if old_face in source_faces:
                    observed.append(ids[face.index(center)])
            self.assertEqual(len(observed), target)
            self.assertEqual(len(set(observed)), target)
            self.assertTrue(set(observed).issubset(old_ids))
            self.assertTrue(all((result["uv_sets"]["map1"]["u"][uv_id],
                                 result["uv_sets"]["map1"]["v"][uv_id]) == (0.5, 0.5)
                                for uv_id in observed))

    def assert_planar_cap_layout(self, result, points, faces, analysis):
        expected = {}
        for old_ring, new_ring in zip(analysis["rings"], result["new_rings"]):
            source = [points[vertex] for vertex in old_ring]
            for vertex, sample in zip(new_ring, result["samples"]):
                point = core._polyline_point(source, sample)
                expected[vertex] = (0.5 + 0.25 * point[0], 0.5 + 0.25 * point[1])
        for cap in analysis["caps"]:
            expected[result["old_to_new"][cap["center"]]] = (0.5, 0.5)
        for old_face, (face, ids, coordinates) in zip(result["face_sources"], face_uvs(result)):
            if len({points[vertex][2] for vertex in faces[old_face]}) == 1:
                for vertex, coordinate in zip(face, coordinates):
                    self.assertAlmostEqual(coordinate[0], expected[vertex][0], places=12)
                    self.assertAlmostEqual(coordinate[1], expected[vertex][1], places=12)

    def assert_source_winding(self, result, points, faces):
        for source, face in zip(result["face_sources"], result["faces"]):
            old = [points[vertex] for vertex in faces[source][:3]]
            new = [result["points"][vertex] for vertex in face[:3]]
            old_normal = core._cross(core._sub(old[1], old[0]), core._sub(old[2], old[0]))
            new_normal = core._cross(core._sub(new[1], new[0]), core._sub(new[2], new[0]))
            self.assertGreater(sum(a * b for a, b in zip(old_normal, new_normal)), 0.0)

    def test_duplicate_fan_center_ids_do_not_add_constraints(self):
        points, faces, seed, analysis = self.model()
        uv_sets = {"map1": disk_uvs(points, faces, analysis)}
        self.assertEqual(self.constraints(analysis, faces, uv_sets), {0})

    def test_twenty_segment_disk_caps_reduce_in_all_modes_without_welding(self):
        points, faces, seed, analysis = self.model()
        uv_sets = {"map1": disk_uvs(points, faces, analysis)}
        original = copy.deepcopy((points, faces, seed, uv_sets))
        for target in (12, 10, 6):
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(target=target, mode=mode):
                    result = core.resample_mesh(points, faces, seed, target, uv_sets,
                                                shape_mode=mode)
                    self.assert_valid(result, target)
                    self.assert_centers_not_welded(result, uv_sets["map1"], faces,
                                                  analysis, target)
                    self.assert_planar_cap_layout(result, points, faces, analysis)
                    self.assertEqual((points, faces, seed, uv_sets), original)

    def test_concentric_planar_cap_uv_bands_reduce_in_all_modes(self):
        profile = ((0.35, 0.0), (0.7, 0.0), (1.0, 0.0),
                   (1.0, 2.0), (0.7, 2.0), (0.35, 2.0))
        points, faces, seed, analysis = self.model(profile)
        uv_sets = {"map1": disk_uvs(points, faces, analysis)}
        original = copy.deepcopy((points, faces, seed, uv_sets))
        self.assertEqual(self.constraints(analysis, faces, uv_sets), {0})
        for target in (12, 10, 6):
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(target=target, mode=mode):
                    result = core.resample_mesh(points, faces, seed, target, uv_sets,
                                                shape_mode=mode)
                    self.assert_valid(result, target)
                    self.assert_centers_not_welded(result, uv_sets["map1"], faces,
                                                  analysis, target)
                    self.assert_planar_cap_layout(result, points, faces, analysis)
                    self.assertEqual((points, faces, seed, uv_sets), original)

    def test_even_tiny_real_center_coordinate_jump_is_protected(self):
        points, faces, seed, analysis = self.model()
        data = disk_uvs(points, faces, analysis)
        cap = analysis["caps"][0]
        center_uv = corner_id(data, faces, cap["faces"][4], cap["center"])
        data["u"][center_uv] += 1.0e-14
        constraints = self.constraints(analysis, faces, {"map1": data})
        self.assertEqual(constraints, {0, 4, 5})
        result = core.resample_mesh(points, faces, seed, 6, {"map1": data})
        self.assertTrue({4.0, 5.0}.issubset(result["samples"]))
        self.assertIn(center_uv, result["uv_sets"]["map1"]["ids"])

    def test_alternating_true_center_discontinuities_still_block_reduction(self):
        points, faces, seed, analysis = self.model()
        data = disk_uvs(points, faces, analysis)
        cap = analysis["caps"][0]
        for column, face_id in enumerate(cap["faces"]):
            uv_id = corner_id(data, faces, face_id, cap["center"])
            data["u"][uv_id] += 0.125 * (column % 2)
        self.assertEqual(self.constraints(analysis, faces, {"map1": data}), set(range(20)))
        with self.assertRaises(core.ConstraintError) as caught:
            core.resample_mesh(points, faces, seed, 12, {"map1": data})
        self.assertEqual(caught.exception.minimum_count, 20)
        self.assertIsInstance(caught.exception, core.ResampleError)

    def test_constraint_error_preserves_existing_error_contract(self):
        with self.assertRaises(core.ConstraintError) as caught:
            core._samples(20, 6, range(20))
        self.assertEqual(caught.exception.minimum_count, 20)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIn("20", str(caught.exception))
        points, faces, seed, analysis = self.model()
        with self.assertRaises(core.ResampleError) as invalid:
            core.resample_mesh(points, faces, seed, 2)
        self.assertNotIsInstance(invalid.exception, core.ConstraintError)

    def test_same_coordinate_cap_outer_ids_remain_a_real_cut(self):
        points, faces, seed, analysis = self.model()
        data = disk_uvs(points, faces, analysis)
        cap = analysis["caps"][0]
        vertex = analysis["rings"][cap["ring"]][4]
        old_id, new_id = duplicate_corner(data, faces, cap["faces"][4], vertex)
        self.assertEqual(self.constraints(analysis, faces, {"map1": data}), {0, 4})
        result = core.resample_mesh(points, faces, seed, 6, {"map1": data})
        column = result["samples"].index(4.0)
        new_vertex = result["new_rings"][cap["ring"]][column]
        observed = {ids[face.index(new_vertex)]
                    for old_face, (face, ids, coordinates) in zip(result["face_sources"], face_uvs(result))
                    if old_face in cap["faces"] and new_vertex in face}
        self.assertEqual(observed, {old_id, new_id})

    def test_same_coordinate_side_seam_ids_remain_protected(self):
        points, faces, seed, analysis = self.model()
        data = disk_uvs(points, faces, analysis, seams=(0, 7), same_coordinates=True)
        self.assertEqual(self.constraints(analysis, faces, {"map1": data}), {0, 7})
        result = core.resample_mesh(points, faces, seed, 6, {"map1": data})
        self.assertIn(7.0, result["samples"])
        self.assert_valid(result, 6)

    def test_all_uv_sets_contribute_their_real_constraints(self):
        points, faces, seed, analysis = self.model()
        uv_sets = {"map1": disk_uvs(points, faces, analysis),
                   "lightmap": disk_uvs(points, faces, analysis, seams=(0, 4, 9))}
        cap = analysis["caps"][0]
        changed = corner_id(uv_sets["lightmap"], faces, cap["faces"][6], cap["center"])
        uv_sets["lightmap"]["v"][changed] += 0.25
        self.assertEqual(self.constraints(analysis, faces, {"map1": uv_sets["map1"]}), {0})
        self.assertEqual(self.constraints(analysis, faces, uv_sets), {0, 4, 6, 7, 9})
        result = core.resample_mesh(points, faces, seed, 6, uv_sets)
        self.assertTrue({0.0, 4.0, 6.0, 7.0, 9.0}.issubset(result["samples"]))
        self.assertEqual(set(result["uv_sets"]), {"map1", "lightmap"})
        self.assert_valid(result, 6)
        with self.assertRaises(core.ResampleError):
            core.resample_mesh(points, faces, seed, 4, uv_sets)

    def test_three_nonzero_uv_pins_allow_exactly_three_segments(self):
        points, faces, seed = cylinder(n=20)
        data = cyclic_strip_uvs(faces)
        analysis = core.analyze_mesh(points, faces, seed)
        self.assertEqual(self.constraints(analysis, faces, {"map1": data}), {1, 5, 9})
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                result = core.resample_mesh(points, faces, seed, 3, {"map1": data},
                                            shape_mode=mode)
                self.assertEqual(result["samples"], [1.0, 5.0, 9.0])
                self.assertEqual(result["protected_columns"], [1, 5, 9])
                self.assertEqual(result["stats"]["protected_count"], 3)
                self.assertEqual(result["face_sources"][-1], 15)
                self.assert_valid(result, 3)
                last_uvs = list(face_uvs(result))[-1][2]
                self.assertEqual([point[0] for point in last_uvs], [0.0, 1.0, 1.0, 0.0])

    def test_sampling_wraps_and_sorts_without_adding_an_artificial_pin(self):
        samples = core._samples(20, 6, (5, 9, 15))
        self.assertEqual(samples, sorted(samples))
        self.assertEqual(len(samples), 6)
        self.assertTrue(all(0.0 <= sample < 20.0 for sample in samples))
        self.assertTrue({5.0, 9.0, 15.0}.issubset(samples))
        self.assertNotIn(0.0, samples)
        self.assertLess(samples[0], 5.0)
        with self.assertRaises(core.ConstraintError) as caught:
            core._samples(20, 3, (1, 5, 9, 15))
        self.assertEqual(caught.exception.minimum_count, 4)

    def test_nonuniform_measure_is_balanced_through_the_wrapped_interval(self):
        n, target, pins = 20, 9, (1, 5, 9)
        lengths = [float(index + 1) for index in range(n)]
        samples = core._samples(n, target, pins, lengths=lengths)
        cumulative = [0.0]
        for length in lengths:
            cumulative.append(cumulative[-1] + length)

        def measure(sample):
            column = int(sample)
            return (column // n * cumulative[-1] + cumulative[column % n] +
                    (sample - column) * lengths[column % n])

        self.assertEqual(samples, sorted(samples))
        self.assertEqual(len(samples), target)
        self.assertTrue(set(pins).issubset(samples))
        for start, finish in zip(pins, pins[1:] + (pins[0] + n,)):
            inside = sorted(sample + n if sample < start else sample for sample in samples)
            inside = [sample for sample in inside if start <= sample < finish] + [float(finish)]
            gaps = [measure(b) - measure(a) for a, b in zip(inside, inside[1:])]
            self.assertLess(max(gaps) - min(gaps), 1.0e-10)

    def test_wrapped_uv_interpolation_keeps_the_first_output_vertex_welded(self):
        points, faces, seed, analysis = self.model()
        data = disk_uvs(points, faces, analysis, seams=(5, 9, 15), cyclic=True)
        original = copy.deepcopy((points, faces, seed, data))
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                result = core.resample_mesh(points, faces, seed, 6, {"map1": data},
                                            shape_mode=mode)
                self.assert_valid(result, 6)
                self.assert_source_winding(result, points, faces)
                self.assertLess(result["samples"][0], 5.0)
                self.assertEqual(result["face_sources"][5], 0)
                for source_faces in (analysis["bands"][0]["faces"],
                                     analysis["caps"][0]["faces"],
                                     analysis["caps"][1]["faces"]):
                    for ring_index in (0, 1):
                        vertex = result["new_rings"][ring_index][0]
                        observed = {ids[face.index(vertex)]
                                    for source, (face, ids, coordinates) in
                                    zip(result["face_sources"], face_uvs(result))
                                    if source in source_faces and vertex in face}
                        if observed:
                            self.assertEqual(len(observed), 1)
                self.assert_planar_cap_layout(result, points, faces, analysis)
                self.assertEqual((points, faces, seed, data), original)

    def test_vertex_renumbering_and_reversal_do_not_change_reduction_feasibility(self):
        points, faces, seed = cylinder(n=20)
        uv_sets = {"map1": cyclic_strip_uvs(faces)}
        variants = (list(range(len(points))), list(reversed(range(len(points)))),
                    list(range(7, len(points))) + list(range(7)))
        expected = {tuple(round(value, 9) for value in points[index])
                    for ring in (0, 1) for index in (ring * 20 + 1, ring * 20 + 5, ring * 20 + 9)}
        for ordering in variants:
            mapping = {old: new for new, old in enumerate(ordering)}
            new_points = [points[old] for old in ordering]
            new_faces = [[mapping[vertex] for vertex in face] for face in faces]
            new_seed = [(mapping[a], mapping[b]) for a, b in reversed(seed)]
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(start=ordering[0], mode=mode):
                    result = core.resample_mesh(new_points, new_faces, new_seed, 3, uv_sets,
                                                shape_mode=mode)
                    self.assert_valid(result, 3)
                    self.assert_source_winding(result, new_points, new_faces)
                    self.assertEqual({tuple(round(value, 9) for value in point)
                                      for point in result["points"]}, expected)

    def test_non_uv_protection_uses_only_real_nonzero_columns(self):
        points, faces, seed = cylinder(n=20)
        for keep_uv in (True, False):
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(keep_uv=keep_uv, mode=mode):
                    result = core.resample_mesh(points, faces, seed, 3, preserve_uvs=keep_uv,
                                                protected_columns=(1, 5, 9), shape_mode=mode)
                    self.assertEqual(result["samples"], [1.0, 5.0, 9.0])
                    self.assertEqual(result["protected_columns"], [1, 5, 9])
                    self.assert_valid(result, 3)
        with self.assertRaises(core.ConstraintError) as caught:
            core.resample_mesh(points, faces, seed, 3, preserve_uvs=False,
                               protected_columns=(1, 5, 9, 15))
        self.assertEqual(caught.exception.minimum_count, 4)

    def test_disabling_uv_releases_fragmented_uv_constraints_in_all_modes(self):
        points, faces, seed = cylinder(n=20)
        uv_sets = strip_uvs(faces, n=20, separate_faces=True)
        original = copy.deepcopy(uv_sets)
        with self.assertRaises(core.ConstraintError) as caught:
            core.resample_mesh(points, faces, seed, 12, uv_sets)
        self.assertEqual(caught.exception.minimum_count, 20)
        for target in (12, 10, 6):
            for mode in ("contour", "uniform", "circle"):
                with self.subTest(target=target, mode=mode):
                    with patch.object(core, "_parse_uvs", side_effect=AssertionError("UV parsing disabled")):
                        result = core.resample_mesh(points, faces, seed, target, uv_sets,
                                                    preserve_uvs=False, shape_mode=mode)
                    self.assertEqual(result["uv_sets"], {})
                    self.assert_valid(result, target)
                    self.assertEqual(uv_sets, original)

    def test_disabled_uv_does_not_validate_or_read_malformed_uv_data(self):
        points, faces, seed = cylinder(n=20)
        result = core.resample_mesh(points, faces, seed, 6, {"map1": {"invalid": object()}},
                                    preserve_uvs=False)
        self.assertEqual(result["uv_sets"], {})
        self.assert_valid(result, 6)

    def test_unmapped_and_multiple_uv_sets_keep_nonzero_wrapped_boundaries(self):
        points, faces, seed = cylinder(n=20)
        uv_sets = {"map1": cyclic_strip_uvs(faces),
                   "lightmap": cyclic_strip_uvs(faces, seams=(2, 8), missing_faces=(3,))}
        analysis = core.analyze_mesh(points, faces, seed)
        expected = {1, 2, 3, 4, 5, 8, 9}
        self.assertEqual(self.constraints(analysis, faces, uv_sets), expected)
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                result = core.resample_mesh(points, faces, seed, 7, uv_sets, shape_mode=mode)
                self.assertEqual(set(result["samples"]), {float(value) for value in expected})
                self.assertEqual(result["uv_sets"]["map1"]["counts"], [4] * 7)
                counts = result["uv_sets"]["lightmap"]["counts"]
                self.assertEqual(counts.count(0), 1)
                self.assertEqual([source for source, count in zip(result["face_sources"], counts)
                                  if not count], [3])
        with self.assertRaises(core.ConstraintError) as caught:
            core.resample_mesh(points, faces, seed, 6, uv_sets)
        self.assertEqual(caught.exception.minimum_count, 7)


if __name__ == "__main__":
    unittest.main()
