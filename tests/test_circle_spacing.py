"""Every fitted ring uses its own angular measure without moving protected cuts."""

import copy
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cylinder_resample import core
from test_resample import cylinder, distances, face_uvs, output_ring
import test_reduction_uv as uv_helpers


ANGLES = (0, 10, 20, 30, 60, 110, 160, 200, 240, 280, 315, 345)


def shaped_cylinder(caps="fan", ring_count=3):
    profile = tuple((1.0 - 0.1 * i, float(i)) for i in range(ring_count))
    points, faces, seed = cylinder(n=12, profile=profile, caps=caps)
    for ri in range(1, ring_count):
        radius, height = profile[ri]
        for i, angle in enumerate(ANGLES):
            points[ri * 12 + i] = (radius * math.cos(math.radians(angle)),
                                    radius * math.sin(math.radians(angle)), height)
    return points, faces, seed


def angular_gaps(ring):
    angles = [math.atan2(point[1], point[0]) for point in ring]
    return [(angles[(i + 1) % len(ring)] - angle) % (2 * math.pi)
            for i, angle in enumerate(angles)]


def uv_shell_count(data):
    neighbors, cursor = {}, 0
    for count in data["counts"]:
        ids = data["ids"][cursor:cursor + count]
        cursor += count
        for i, uv_id in enumerate(ids):
            other = ids[(i + 1) % len(ids)]
            neighbors.setdefault(uv_id, set()).add(other)
            neighbors.setdefault(other, set()).add(uv_id)
    remaining, count = set(neighbors), 0
    while remaining:
        count += 1
        pending = [remaining.pop()]
        while pending:
            for neighbor in neighbors[pending.pop()]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
    return count


class CircleSpacingTests(unittest.TestCase):
    def assert_source_winding(self, result, points, faces):
        for source, face in zip(result["face_sources"], result["faces"]):
            old = [points[vertex] for vertex in faces[source][:3]]
            new = [result["points"][vertex] for vertex in face[:3]]
            before = core._cross(core._sub(old[1], old[0]), core._sub(old[2], old[0]))
            after = core._cross(core._sub(new[1], new[0]), core._sub(new[2], new[0]))
            self.assertGreater(core._dot(before, after), 0.0)

    def assert_ordered_parameters(self, result):
        n = result["analysis"]["source_count"]
        for parameters in result["ring_samples"]:
            self.assertTrue(all(a < b for a, b in zip(parameters, parameters[1:])))
            self.assertLess(parameters[-1] - parameters[0], n)

    def test_odd_targets_are_uniform_on_all_rings_with_different_source_angles(self):
        points, faces, seed = shaped_cylinder(ring_count=5)
        original = copy.deepcopy((points, faces, seed))
        for target in (7, 9, 11):
            with self.subTest(target=target):
                result = core.resample_mesh(points, faces, seed, target,
                                            shape_mode="circle", preserve_uvs=False)
                for ri in range(5):
                    for gap in angular_gaps(output_ring(result, ri)):
                        self.assertAlmostEqual(gap, 2 * math.pi / target, places=11)
                self.assertAlmostEqual(result["stats"]["worst_spacing_ratio"], 1.0, places=11)
                self.assertEqual(len(result["stats"]["per_ring_spacing"]), 5)
                self.assert_ordered_parameters(result)
                self.assert_source_winding(result, points, faces)
                self.assertEqual((points, faces, seed), original)

    def test_different_seed_ring_still_uniformly_resamples_every_ring(self):
        points, faces, _ = shaped_cylinder()
        seed = [(12 + i, 12 + (i + 1) % 12) for i in range(12)]
        result = core.resample_mesh(points, faces, seed, 9,
                                    shape_mode="circle", preserve_uvs=False)
        self.assertEqual(result["ring_samples"][result["analysis"]["seed_ring"]], result["samples"])
        for ri in range(3):
            self.assertAlmostEqual(max(angular_gaps(output_ring(result, ri))), 2 * math.pi / 9)
            self.assertAlmostEqual(min(angular_gaps(output_ring(result, ri))), 2 * math.pi / 9)

    def test_nonzero_pins_keep_corresponding_output_indices_and_interval_spacing(self):
        points, faces, seed = shaped_cylinder()
        pins = (3, 8)
        for target in (7, 9, 11):
            with self.subTest(target=target):
                result = core.resample_mesh(points, faces, seed, target,
                                            shape_mode="circle", preserve_uvs=False,
                                            protected_columns=pins)
                self.assert_ordered_parameters(result)
                self.assertLess(result["ring_samples"][1][0], 0.0)
                for ri, parameters in enumerate(result["ring_samples"]):
                    for pin in pins:
                        output_index = result["samples"].index(float(pin))
                        self.assertEqual(parameters[output_index], float(pin))
                        expected = points[result["analysis"]["rings"][ri][pin]]
                        actual = result["points"][result["new_rings"][ri][output_index]]
                        for observed, value in zip(actual, expected):
                            self.assertAlmostEqual(observed, value, places=11)
                    gaps = angular_gaps(output_ring(result, ri))
                    for left, right in zip(pins, pins[1:] + (pins[0],)):
                        start = result["samples"].index(float(left))
                        end = result["samples"].index(float(right))
                        indices = list(range(start, end if end > start else end + target))
                        intervals = [gaps[index % target] for index in indices]
                        self.assertAlmostEqual(min(intervals), max(intervals), places=11)
                self.assert_source_winding(result, points, faces)

    def test_uv_sampling_uses_each_ring_parameters_without_crossing_charts(self):
        points, faces, seed = shaped_cylinder()
        analysis = core.analyze_mesh(points, faces, seed)
        pins = (3, 8)
        data = uv_helpers.disk_uvs(points, faces, analysis, seams=pins, cyclic=True)
        original = copy.deepcopy(data)
        result = core.resample_mesh(points, faces, seed, 9, {"map1": data}, shape_mode="circle")
        self.assertEqual(uv_shell_count(result["uv_sets"]["map1"]), uv_shell_count(data))
        vertices = {vertex: (ri, j) for ri, ring in enumerate(result["new_rings"])
                    for j, vertex in enumerate(ring)}
        band_faces = {fi for band in analysis["bands"] for fi in band["faces"]}
        cap_faces = {fi for cap in analysis["caps"] for fi in cap["faces"]}
        for source, (face, ids, coordinates) in zip(result["face_sources"], face_uvs(result)):
            if source in band_faces:
                column = source % 12
                start = max((pin for pin in pins if pin <= column), default=pins[-1])
                end = pins[pins.index(start) + 1] if start != pins[-1] else pins[0] + 12
                for vertex, coordinate in zip(face, coordinates):
                    ri, j = vertices[vertex]
                    parameter = result["ring_samples"][ri][j] % 12
                    if parameter < start - 1.0e-8:
                        parameter += 12
                    self.assertAlmostEqual(coordinate[0], (parameter - start) / (end - start), places=11)
                    self.assertAlmostEqual(coordinate[1], ri / 2.0, places=11)
            elif source in cap_faces:
                for vertex, coordinate in zip(face, coordinates):
                    if vertex not in vertices:
                        self.assertEqual(coordinate, (0.5, 0.5))
                        continue
                    ri, j = vertices[vertex]
                    ring = [points[v] for v in analysis["rings"][ri]]
                    point = core._polyline_point(ring, result["ring_samples"][ri][j])
                    self.assertAlmostEqual(coordinate[0], 0.5 + 0.25 * point[0], places=11)
                    self.assertAlmostEqual(coordinate[1], 0.5 + 0.25 * point[1], places=11)
        ring = result["new_rings"][analysis["seed_ring"]]
        new_seed = list(zip(ring, ring[1:] + ring[:1]))
        rebuilt = core.analyze_mesh(result["points"], result["faces"], new_seed)
        uv_constraints = core._uv_constraints(rebuilt, core._parse_uvs(result["uv_sets"], result["faces"]))
        self.assertEqual(uv_constraints, {result["samples"].index(float(pin)) for pin in pins})
        self.assertEqual(data, original)

    def test_ngon_uv_endpoints_use_their_own_ring_parameters(self):
        points, faces, seed = shaped_cylinder(caps="ngon")
        analysis = core.analyze_mesh(points, faces, seed)
        data = uv_helpers.disk_uvs(points, faces, analysis)
        result = core.resample_mesh(points, faces, seed, 9, {"map1": data}, shape_mode="circle")
        cap_faces = {fi for cap in analysis["caps"] for fi in cap["faces"]}
        vertices = {vertex: (ri, j) for ri, ring in enumerate(result["new_rings"])
                    for j, vertex in enumerate(ring)}
        for source, (face, ids, coordinates) in zip(result["face_sources"], face_uvs(result)):
            if source not in cap_faces:
                continue
            for vertex, coordinate in zip(face, coordinates):
                ri, j = vertices[vertex]
                ring = [points[v] for v in analysis["rings"][ri]]
                point = core._polyline_point(ring, result["ring_samples"][ri][j])
                self.assertAlmostEqual(coordinate[0], 0.5 + 0.25 * point[0], places=11)
                self.assertAlmostEqual(coordinate[1], 0.5 + 0.25 * point[1], places=11)
        self.assertEqual(uv_shell_count(result["uv_sets"]["map1"]), uv_shell_count(data))

    def test_reversed_source_winding_preserved(self):
        points, faces, seed = shaped_cylinder()
        faces = [list(reversed(face)) for face in faces]
        result = core.resample_mesh(points, faces, seed, 9,
                                    shape_mode="circle", preserve_uvs=False)
        self.assert_source_winding(result, points, faces)
        for ri in range(3):
            self.assertAlmostEqual(max(angular_gaps(output_ring(result, ri))), 2 * math.pi / 9)

    def test_non_circle_modes_keep_shared_sampling_and_report_worst_ring(self):
        points, faces, seed = shaped_cylinder()
        for mode in ("contour", "uniform"):
            with self.subTest(mode=mode):
                result = core.resample_mesh(points, faces, seed, 9,
                                            shape_mode=mode, preserve_uvs=False)
                self.assertTrue(all(parameters == result["samples"] for parameters in result["ring_samples"]))
                ratios = []
                for ri, stats in enumerate(result["stats"]["per_ring_spacing"]):
                    lengths = distances(output_ring(result, ri))
                    ratio = max(lengths) / min(lengths)
                    self.assertAlmostEqual(stats["spacing_ratio"], ratio)
                    ratios.append(ratio)
                self.assertAlmostEqual(result["stats"]["worst_spacing_ratio"], max(ratios))
                self.assertAlmostEqual(result["stats"]["spacing_ratio"], ratios[result["analysis"]["seed_ring"]])

    def test_quarter_pins_explain_odd_spacing_and_suggest_safe_counts(self):
        points, faces, seed = cylinder(n=12)
        result = core.resample_mesh(points, faces, seed, 9, shape_mode="circle",
                                    preserve_uvs=False, protected_columns=(0, 3, 6, 9))
        self.assertTrue(result["stats"]["spacing_limited_by_constraints"])
        self.assertEqual(result["stats"]["compatible_counts"], (8, 12))
        self.assertEqual(sorted(result["stats"]["per_ring_spacing"][0]["actual_interval_counts"]), [2, 2, 2, 3])
        self.assertTrue(any("整数分段" in warning for warning in result["warnings"]))
        unconstrained = core.resample_mesh(points, faces, seed, 9, shape_mode="circle",
                                           preserve_uvs=False)
        self.assertFalse(unconstrained["stats"]["spacing_limited_by_constraints"])
        self.assertAlmostEqual(unconstrained["stats"]["worst_spacing_ratio"], 1.0)

    def test_individually_compatible_rings_cannot_offer_conflicting_shared_counts(self):
        points, faces, seed = cylinder(n=12)
        upper_angles = (0, 30, 60, 90, 180, 210, 240, 260, 280, 300, 320, 340)
        for i, angle in enumerate(upper_angles):
            points[12 + i] = (math.cos(math.radians(angle)), math.sin(math.radians(angle)), 2.0)
        result = core.resample_mesh(points, faces, seed, 6, shape_mode="circle",
                                    preserve_uvs=False, protected_columns=(0, 4))
        rings = result["stats"]["per_ring_spacing"]
        self.assertFalse(rings[0]["spacing_limited_by_constraints"])
        self.assertTrue(rings[1]["spacing_limited_by_constraints"])
        self.assertEqual(rings[0]["actual_interval_counts"], (2, 4))
        for ideal, expected in zip(rings[1]["ideal_interval_counts"], (3, 3)):
            self.assertAlmostEqual(ideal, expected)
        self.assertTrue(result["stats"]["spacing_limited_by_constraints"])
        self.assertEqual(result["stats"]["compatible_counts"], ())
        self.assertGreater(result["stats"]["worst_spacing_ratio"], 1.8)


if __name__ == "__main__":
    unittest.main()
