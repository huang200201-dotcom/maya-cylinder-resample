"""Geometry and UV contract checks for the Maya-independent resampling core."""

import math
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cylinder_resample.core import analyze_mesh, resample_mesh


def cylinder(n=12, profile=((1.0, 0.0), (1.0, 2.0)), caps=None):
    points = [
        (radius * math.cos(2 * math.pi * i / n),
         radius * math.sin(2 * math.pi * i / n), height)
        for radius, height in profile for i in range(n)
    ]
    faces = [
        [r * n + i, r * n + (i + 1) % n,
         (r + 1) * n + (i + 1) % n, (r + 1) * n + i]
        for r in range(len(profile) - 1) for i in range(n)
    ]
    if caps == "ngon":
        faces += [list(reversed(range(n))),
                  [(len(profile) - 1) * n + i for i in range(n)]]
    elif caps == "fan":
        for ring, top in ((0, False), (len(profile) - 1, True)):
            center = len(points)
            points.append((0.0, 0.0, profile[ring][1]))
            for i in range(n):
                a, b = ring * n + i, ring * n + (i + 1) % n
                faces.append([center, a, b] if top else [center, b, a])
    return points, faces, [(i, (i + 1) % n) for i in range(n)]


def strip_uvs(faces, n=12, ring_count=2, seams=(0,), same_coordinates=False,
               separate_faces=False, missing_faces=()):
    u, v, counts, ids, cache = [], [], [], [], {}
    boundaries = sorted(set(seams) | {0, n})
    for face_id, face in enumerate(faces):
        if face_id in missing_faces:
            counts.append(0)
            continue
        counts.append(len(face))
        if face_id < (ring_count - 1) * n:
            column = face_id % n
            left = max(x for x in boundaries if x <= column)
            right = min(x for x in boundaries if x > column)
            island = face_id if separate_faces else left
            for corner, vertex in enumerate(face):
                angular = column + (1 if corner in (1, 2) else 0)
                uu = angular / n if same_coordinates else (angular - left) / (right - left)
                vv = (vertex // n) / (ring_count - 1)
                key = (island, uu, vv)
                if key not in cache:
                    cache[key] = len(u)
                    u.append(uu)
                    v.append(vv)
                ids.append(cache[key])
        else:
            for corner in range(len(face)):
                angle = 2 * math.pi * corner / len(face)
                ids.append(len(u))
                u.append(0.5 + 0.4 * math.cos(angle))
                v.append(0.5 + 0.4 * math.sin(angle))
    return {"map1": {"u": u, "v": v, "counts": counts, "ids": ids}}


def face_uvs(result, set_name="map1"):
    data = result["uv_sets"][set_name]
    cursor = 0
    for face, count in zip(result["faces"], data["counts"]):
        corner_ids = data["ids"][cursor:cursor + count]
        cursor += count
        yield face, corner_ids, [(data["u"][i], data["v"][i]) for i in corner_ids]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def nonuniform_cylinder(angles=None, profile=((1.0, 0.0), (1.0, 2.0))):
    angles = angles or (0, 10, 20, 30, 60, 110, 160, 200, 240, 280, 315, 345)
    points, faces, seed = cylinder(n=len(angles), profile=profile)
    points = [(radius * math.cos(math.radians(angle)),
               radius * math.sin(math.radians(angle)), height)
              for radius, height in profile for angle in angles]
    return points, faces, seed


def distances(points):
    return [math.dist(point, points[(i + 1) % len(points)])
            for i, point in enumerate(points)]


def output_ring(result, ring=None):
    if ring is None:
        ring = result["analysis"]["seed_ring"]
    return [result["points"][vertex] for vertex in result["new_rings"][ring]]


def sample_arc_lengths(result, source_points):
    lengths = distances(source_points)
    cumulative = [0.0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)
    positions = []
    for value in result["samples"]:
        edge = int(math.floor(value))
        positions.append(cumulative[edge] + (value - edge) * lengths[edge])
    return [(positions[(i + 1) % len(positions)] - position) % cumulative[-1]
            for i, position in enumerate(positions)]


def coefficient_of_variation(values):
    average = sum(values) / len(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values)) / average


class CylinderResampleTests(unittest.TestCase):
    def assert_valid_mesh(self, result):
        points, faces = result["points"], result["faces"]
        self.assertEqual(len(result["face_sources"]), len(faces))
        for face in faces:
            self.assertGreaterEqual(len(face), 3)
            self.assertEqual(len(set(face)), len(face))
            self.assertTrue(all(0 <= vertex < len(points) for vertex in face))
        for data in result["uv_sets"].values():
            self.assertEqual(len(data["counts"]), len(faces))
            self.assertEqual(sum(data["counts"]), len(data["ids"]))
            self.assertEqual(len(data["u"]), len(data["v"]))
            for face, count in zip(faces, data["counts"]):
                self.assertIn(count, (0, len(face)))
            self.assertTrue(all(0 <= uv < len(data["u"]) for uv in data["ids"]))

    def assert_outward(self, result, z_min=0.0, z_max=2.0):
        for face in result["faces"]:
            vertices = [result["points"][i] for i in face]
            a = vertices[0]
            normal = tuple(sum(cross(point, vertices[(index + 1) % len(vertices)])[axis]
                               for index, point in enumerate(vertices))
                           for axis in range(3))
            if max(p[2] for p in vertices) - min(p[2] for p in vertices) < 1e-8:
                self.assertLess(normal[2], 0) if abs(a[2] - z_min) < 1e-8 else self.assertGreater(normal[2], 0)
            else:
                center = tuple(sum(p[i] for p in vertices) / len(vertices) for i in range(3))
                self.assertGreater(normal[0] * center[0] + normal[1] * center[1], 0)

    def test_identifies_bevel_and_variable_radius_profile(self):
        profile = ((0.75, 0.0), (1.0, 0.1), (1.0, 0.4),
                   (0.6, 0.5), (0.6, 1.4), (1.0, 1.6), (0.9, 2.0))
        points, faces, seed = cylinder(profile=profile)
        analysis = analyze_mesh(points, faces, seed)
        self.assertEqual(analysis["source_count"], 12)
        self.assertEqual(analysis["ring_count"], len(profile))
        self.assertEqual(len(analysis["face_ids"]), len(faces))
        self.assertEqual({v for ring in analysis["rings"] for v in ring}, set(range(len(points))))
        result = resample_mesh(points, faces, seed, 18, shape_mode="circle")
        self.assert_valid_mesh(result)
        self.assertEqual(len(result["points"]), 18 * len(profile))
        for radius, z in profile:
            ring = [p for p in result["points"] if abs(p[2] - z) < 1e-8]
            self.assertEqual(len(ring), 18)
            self.assertTrue(all(abs(math.hypot(p[0], p[1]) - radius) < 1e-7 for p in ring))
        self.assert_outward(result)

    def test_exact_increase_decrease_and_source_face_mapping(self):
        points, faces, seed = cylinder(profile=((1, 0), (1, 0.2), (1, 2)))
        for target in (6, 9, 18, 24):
            with self.subTest(target=target):
                result = resample_mesh(points, faces, seed, target)
                self.assertEqual(len(result["points"]), 3 * target)
                self.assertEqual(len(result["faces"]), 2 * target)
                self.assertTrue(all(0 <= source < len(faces) for source in result["face_sources"]))
                self.assert_valid_mesh(result)
                self.assert_outward(result)

    def test_contour_samples_original_polygon_edges(self):
        points, faces, seed = cylinder(n=4)
        result = resample_mesh(points, faces, seed, 8, shape_mode="contour")
        expected = {(round(x, 6), round(y, 6)) for x, y in
                    ((1, 0), (0.5, 0.5), (0, 1), (-0.5, 0.5),
                     (-1, 0), (-0.5, -0.5), (0, -1), (0.5, -0.5))}
        actual = {(round(p[0], 6), round(p[1], 6)) for p in result["points"] if abs(p[2]) < 1e-8}
        self.assertEqual(actual, expected)

    def test_contour_increase_retains_every_original_corner(self):
        points, faces, seed = cylinder()
        result = resample_mesh(points, faces, seed, 18, shape_mode="contour")
        self.assertEqual(len(result["points"]), 36)
        self.assertTrue({tuple(p) for p in points}.issubset({tuple(p) for p in result["points"]}))
        self.assertTrue(set(range(12)).issubset(set(result["samples"])))

    def test_contour_24_to_36_distributes_extra_splits_around_entire_loop(self):
        points, faces, seed = cylinder(n=24)
        result = resample_mesh(points, faces, seed, 36, shape_mode="contour")
        allocations = [0] * 24
        for sample in result["samples"]:
            allocations[int(math.floor(sample))] += 1
        self.assertEqual(sorted(allocations), [1] * 12 + [2] * 12)
        for start in range(24):
            self.assertEqual(sum(allocations[(start + offset) % 24] for offset in range(4)), 6)
        self.assertEqual(set(range(24)) & set(result["samples"]), set(range(24)))
        self.assert_valid_mesh(result)

    def test_contour_32_to_40_spaces_extra_splits_every_four_columns(self):
        points, faces, seed = cylinder(n=32)
        result = resample_mesh(points, faces, seed, 40, shape_mode="contour")
        split_columns = sorted(int(math.floor(value)) for value in result["samples"]
                               if abs(value - round(value)) > 1e-8)
        self.assertEqual(len(split_columns), 8)
        gaps = [(split_columns[(i + 1) % 8] - column) % 32
                for i, column in enumerate(split_columns)]
        self.assertEqual(gaps, [4] * 8)

    def test_contour_allocation_responds_to_physical_source_edge_lengths(self):
        points, faces, seed = nonuniform_cylinder()
        result = resample_mesh(points, faces, seed, 36, shape_mode="contour")
        lengths = distances(points[:12])
        actual = sample_arc_lengths(result, points[:12])
        equal_column_allocation = [length / 3 for length in lengths for _ in range(3)]
        self.assertLess(coefficient_of_variation(actual),
                        coefficient_of_variation(equal_column_allocation) * 0.6)
        self.assertTrue({tuple(p) for p in points}.issubset({tuple(p) for p in result["points"]}))

    def test_uniform_samples_equal_actual_arc_length_on_nonuniform_polygon(self):
        points, faces, seed = nonuniform_cylinder()
        result = resample_mesh(points, faces, seed, 17, shape_mode="uniform")
        actual = sample_arc_lengths(result, points[:12])
        expected = sum(distances(points[:12])) / 17
        self.assertTrue(all(abs(length - expected) < 1e-8 for length in actual))
        source_edges = distances(points[:12])
        cumulative = [0.0]
        for length in source_edges:
            cumulative.append(cumulative[-1] + length)
        for index, point in enumerate(output_ring(result)):
            distance = index * expected
            edge = max(edge for edge in range(12) if cumulative[edge] <= distance + 1e-10)
            ratio = (distance - cumulative[edge]) / source_edges[edge]
            expected_point = tuple(points[edge][axis] * (1.0 - ratio)
                                   + points[(edge + 1) % 12][axis] * ratio for axis in range(3))
            self.assertLess(math.dist(point, expected_point), 1e-8)
        self.assert_valid_mesh(result)
        self.assert_outward(result)

    def test_uniform_uses_selected_internal_seed_ring_for_arc_metric(self):
        points, faces, seed = nonuniform_cylinder(profile=((1, 0), (1, 1), (1, 2)))
        for index in range(12):
            angle = 2 * math.pi * index / 12
            points[index] = (math.cos(angle), math.sin(angle), 0.0)
        internal_seed = [(12 + a, 12 + b) for a, b in seed]
        result = resample_mesh(points, faces, internal_seed, 18, shape_mode="uniform")
        self.assertEqual(result["analysis"]["seed_ring"], 1)
        actual = sample_arc_lengths(result, points[12:24])
        self.assertLess(max(actual) - min(actual), 1e-8)
        self.assertTrue(any(abs(sample * 1.5 - round(sample * 1.5)) > 1e-5
                            for sample in result["samples"]))

    def test_uniform_measure_uses_world_lengths_on_nonuniformly_scaled_source(self):
        points, faces, seed = cylinder()
        metric_points = [(x * 3.0 + 20, y * 0.7 - 4, z * 1.2 + 6) for x, y, z in points]
        result = resample_mesh(points, faces, seed, 18, shape_mode="uniform", metric_points=metric_points)
        actual = sample_arc_lengths(result, metric_points[:12])
        self.assertLess(max(actual) - min(actual), 1e-8)
        world_ring = [(x * 3.0 + 20, y * 0.7 - 4, z * 1.2 + 6) for x, y, z in output_ring(result)]
        world_edges = distances(world_ring)
        self.assertAlmostEqual(result["stats"]["min_segment_length"], min(world_edges))
        self.assertAlmostEqual(result["stats"]["max_segment_length"], max(world_edges))
        self.assertAlmostEqual(result["stats"]["spacing_ratio"], max(world_edges) / min(world_edges))
        self.assertNotEqual(result["samples"], [i * 12 / 18 for i in range(18)])

    def test_uniform_same_count_rebalances_nonuniform_source_but_contour_is_identity(self):
        points, faces, seed = nonuniform_cylinder()
        uniform = resample_mesh(points, faces, seed, 12, shape_mode="uniform")
        self.assertNotEqual(uniform["samples"], list(range(12)))
        actual = sample_arc_lengths(uniform, points[:12])
        self.assertLess(max(actual) - min(actual), 1e-8)
        contour = resample_mesh(points, faces, seed, 12, shape_mode="contour")
        self.assertEqual(contour["samples"], list(range(12)))
        self.assertEqual({tuple(p) for p in contour["points"]}, {tuple(p) for p in points})

    def test_circle_same_count_rebalances_nonuniform_source_angles(self):
        points, faces, seed = nonuniform_cylinder()
        result = resample_mesh(points, faces, seed, 12, shape_mode="circle")
        ring = output_ring(result)
        angles = [math.atan2(point[1], point[0]) for point in ring]
        gaps = [(angles[(i + 1) % len(angles)] - angle) % (2 * math.pi)
                for i, angle in enumerate(angles)]
        self.assertTrue(all(abs(gap - 2 * math.pi / 12) < 1e-8 for gap in gaps))
        self.assertTrue(all(abs(math.hypot(point[0], point[1]) - 1.0) < 1e-8 for point in ring))

    def test_uniform_multiple_close_pins_keep_uv_boundaries_and_exact_points(self):
        points, faces, seed = nonuniform_cylinder()
        pins = (0, 1, 2, 3, 7)
        uv_sets = strip_uvs(faces, seams=pins)
        result = resample_mesh(points, faces, seed, 17, uv_sets,
                               shape_mode="uniform", protected_columns=pins)
        self.assert_valid_mesh(result)
        self.assertEqual(result["protected_columns"], list(pins))
        self.assertTrue(set(pins).issubset(set(result["samples"])))
        usage = {}
        for face, ids, uvs in face_uvs(result):
            for vertex, uv_id in zip(face, ids):
                usage.setdefault(vertex, set()).add(uv_id)
            self.assertGreaterEqual(min(uv[0] for uv in uvs), -1e-8)
            self.assertLessEqual(max(uv[0] for uv in uvs), 1.0 + 1e-8)
        for column in pins:
            point = points[column]
            vertex = next(index for index, sampled in enumerate(result["points"])
                          if math.dist(point, sampled) < 1e-8)
            self.assertEqual(len(usage[vertex]), 2)
        with self.assertRaises(ValueError):
            resample_mesh(points, faces, seed, 4, uv_sets, shape_mode="uniform")

    def test_spacing_diagnostics_match_output_geometry_and_protection(self):
        points, faces, seed = cylinder(n=24)
        result = resample_mesh(points, faces, seed, 36, shape_mode="contour")
        lengths = distances(output_ring(result))
        stats = result["stats"]
        self.assertAlmostEqual(stats["min_segment_length"], min(lengths))
        self.assertAlmostEqual(stats["max_segment_length"], max(lengths))
        self.assertAlmostEqual(stats["spacing_ratio"], max(lengths) / min(lengths))
        self.assertEqual(stats["protected_count"], 24)
        self.assertEqual(stats["constraint_columns_count"], 0)
        self.assertAlmostEqual(stats["spacing_ratio"], 2.0)

    def test_large_processed_mesh_resampling_stays_practical(self):
        profile = ((0.8, 0), (1, 0.1), (1, 0.3), (0.7, 0.5),
                   (0.7, 1.5), (1, 1.7), (1, 1.9), (0.8, 2))
        points, faces, seed = cylinder(n=1024, profile=profile)
        uv_sets = strip_uvs(faces, n=1024, ring_count=len(profile))
        started = time.perf_counter()
        result = resample_mesh(points, faces, seed, 3072, uv_sets, shape_mode="uniform")
        duration = time.perf_counter() - started
        self.assertEqual(len(result["points"]), 3072 * len(profile))
        self.assertEqual(len(result["faces"]), 3072 * (len(profile) - 1))
        self.assert_valid_mesh(result)
        self.assertLess(duration, 20.0, "Large mesh resampling took %.2f seconds" % duration)

    def test_circle_adds_points_on_radius(self):
        points, faces, seed = cylinder(n=8)
        result = resample_mesh(points, faces, seed, 20, shape_mode="circle")
        self.assertTrue(all(abs(math.hypot(p[0], p[1]) - 1.0) < 1e-7 for p in result["points"]))

    def test_circle_handles_translated_tilted_rings(self):
        points, faces, seed = cylinder()
        angle = math.radians(37)
        cosine, sine = math.cos(angle), math.sin(angle)
        points = [(x + 4, y * cosine - z * sine + 2, y * sine + z * cosine + 8)
                  for x, y, z in points]
        result = resample_mesh(points, faces, seed, 21, shape_mode="circle")
        for x, y, z in result["points"]:
            source_x = x - 4
            source_y = (y - 2) * cosine + (z - 8) * sine
            source_z = -(y - 2) * sine + (z - 8) * cosine
            self.assertAlmostEqual(math.hypot(source_x, source_y), 1.0)
            self.assertTrue(min(abs(source_z), abs(source_z - 2)) < 1e-7)

    def test_circle_rejects_oval_and_nonplanar_rings(self):
        points, faces, seed = cylinder()
        oval = [(1.5 * x, y, z) for x, y, z in points]
        with self.assertRaises(ValueError):
            resample_mesh(oval, faces, seed, 18, shape_mode="circle")
        nonplanar = list(points)
        x, y, z = nonplanar[3]
        nonplanar[3] = (x, y, z + 0.2)
        with self.assertRaises(ValueError):
            resample_mesh(nonplanar, faces, seed, 18, shape_mode="circle")
        self.assert_valid_mesh(resample_mesh(oval, faces, seed, 18, shape_mode="contour"))

    def test_circle_fit_remains_stable_for_small_and_large_scene_units(self):
        points, faces, seed = cylinder(n=8)
        for scale in (1e-12, 1e-6, 1e6, 1e12):
            with self.subTest(scale=scale):
                scaled = [(x * scale, y * scale, z * scale) for x, y, z in points]
                result = resample_mesh(scaled, faces, seed, 20, shape_mode="circle")
                self.assertTrue(all(abs(math.hypot(point[0], point[1]) / scale - 1.0) < 1e-7
                                    for point in result["points"]))
                self.assert_valid_mesh(result)

    def test_zero_length_circumference_edge_is_rejected_before_rebuild(self):
        points, faces, seed = cylinder()
        for offset in (0, 12):
            points[offset + 1] = points[offset]
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                resample_mesh(points, faces, seed, 18, shape_mode=mode)

    def test_zero_area_side_bands_are_rejected_before_rebuild(self):
        points, faces, seed = cylinder(profile=((1, 0), (1, 0)))
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                resample_mesh(points, faces, seed, 18, shape_mode=mode)

    def test_internal_seed_and_reverse_winding_keep_orientation(self):
        points, faces, seed = cylinder(profile=((1, 0), (1, 0.2), (0.8, 1.0), (1, 2)), caps="ngon")
        internal_seed = [(24 + a, 24 + b) for a, b in reversed(seed)]
        reversed_faces = [list(reversed(face)) for face in faces]
        result = resample_mesh(points, reversed_faces, internal_seed, 18)
        self.assertEqual(result["analysis"]["ring_count"], 4)
        for face in result["faces"]:
            vertices = [result["points"][v] for v in face]
            if max(p[2] for p in vertices) - min(p[2] for p in vertices) < 1e-8:
                continue
            a, b, c = vertices[:3]
            normal = cross(tuple(b[i] - a[i] for i in range(3)), tuple(c[i] - a[i] for i in range(3)))
            center = tuple(sum(p[i] for p in vertices) / len(vertices) for i in range(3))
            self.assertLess(normal[0] * center[0] + normal[1] * center[1], 0)

    def test_same_segment_count_is_identity_with_many_constraints(self):
        points, faces, seed = cylinder()
        uv_sets = strip_uvs(faces, seams=(0, 1, 6))
        result = resample_mesh(points, faces, seed, 12, uv_sets, protected_columns=(0, 1, 6))
        self.assertEqual(result["samples"], list(range(12)))
        old_coordinates = {tuple(p) for p in points}
        self.assertEqual({tuple(p) for p in result["points"]}, old_coordinates)
        doubled = resample_mesh(points, faces, seed, 24, uv_sets, shape_mode="circle", protected_columns=(0, 1, 6))
        for value, expected in zip(doubled["samples"], [i / 2 for i in range(24)]):
            self.assertAlmostEqual(value, expected, places=12)

    def test_open_ngon_and_fan_endings(self):
        for cap in (None, "ngon", "fan"):
            with self.subTest(cap=cap):
                points, faces, seed = cylinder(caps=cap)
                result = resample_mesh(points, faces, seed, 18)
                self.assert_valid_mesh(result)
                self.assert_outward(result)
                self.assertEqual(len(result["points"]), 36 + (2 if cap == "fan" else 0))
                self.assertEqual(len(result["faces"]), 18 + (2 if cap == "ngon" else 36 if cap == "fan" else 0))

    def test_concentric_cap_bands_keep_plane_and_winding(self):
        profile = ((0.25, 0.0), (0.6, 0.0), (1.0, 0.0),
                   (1.0, 2.0), (0.6, 2.0), (0.25, 2.0))
        points, faces, seed = cylinder(profile=profile, caps="fan")
        seed = [(24 + a, 24 + b) for a, b in seed]
        result = resample_mesh(points, faces, seed, 18, shape_mode="circle")
        self.assertEqual(result["analysis"]["ring_count"], 6)
        self.assertEqual(len(result["points"]), 110)
        self.assert_valid_mesh(result)
        self.assert_outward(result)

    def test_cap_uv_original_corners_survive_and_inputs_are_unchanged(self):
        for cap in ("ngon", "fan"):
            with self.subTest(cap=cap):
                points, faces, seed = cylinder(caps=cap)
                uv_sets = strip_uvs(faces)
                input_before = repr((points, faces, uv_sets))
                result = resample_mesh(points, faces, seed, 24, uv_sets)
                self.assert_valid_mesh(result)
                self.assertEqual(repr((points, faces, uv_sets)), input_before)
                source = uv_sets["map1"]
                corner_maps, cursor = [], 0
                for face, count in zip(faces, source["counts"]):
                    ids = source["ids"][cursor:cursor + count]
                    cursor += count
                    corner_maps.append({tuple(points[vertex]): (source["u"][uv_id], source["v"][uv_id])
                                        for vertex, uv_id in zip(face, ids)})
                observed = {}
                for old_face, (face, ids, uv) in zip(result["face_sources"], face_uvs(result)):
                    if old_face < 12:
                        continue
                    for vertex, coordinate in zip(face, uv):
                        point = tuple(result["points"][vertex])
                        for original_point, original_uv in corner_maps[old_face].items():
                            if all(abs(point[d] - original_point[d]) < 1e-8 for d in range(3)):
                                self.assertTrue(all(abs(coordinate[d] - original_uv[d]) < 1e-7 for d in range(2)))
                                observed.setdefault(old_face, set()).add(original_point)
                for old_face in range(12, len(faces)):
                    self.assertEqual(observed.get(old_face), set(corner_maps[old_face]))

    def test_single_uv_seam_stays_unwelded(self):
        points, faces, seed = cylinder()
        result = resample_mesh(points, faces, seed, 18, strip_uvs(faces), shape_mode="circle")
        self.assert_valid_mesh(result)
        for face, ids, uv in face_uvs(result):
            self.assertAlmostEqual(uv[0][1], uv[1][1])
            self.assertAlmostEqual(uv[2][1], uv[3][1])
            self.assertLessEqual(max(p[0] for p in uv) - min(p[0] for p in uv), 1 / 18 + 1e-7)
        usage = {}
        for face, ids, uv in face_uvs(result):
            for vertex, uv_id, coordinate in zip(face, ids, uv):
                usage.setdefault(vertex, set()).add(uv_id)
        seams = [vertex for vertex, assigned in usage.items() if len(assigned) > 1]
        self.assertEqual(len(seams), 2)
        for vertex in seams:
            self.assertAlmostEqual(result["points"][vertex][0], 1.0)
            self.assertAlmostEqual(result["points"][vertex][1], 0.0)

    def test_two_uv_islands_keep_their_boundary_and_coordinates(self):
        points, faces, seed = cylinder()
        result = resample_mesh(points, faces, seed, 18, strip_uvs(faces, seams=(0, 6)), shape_mode="circle")
        self.assert_valid_mesh(result)
        for face, ids, uv in face_uvs(result):
            self.assertGreaterEqual(min(p[0] for p in uv), -1e-7)
            self.assertLessEqual(max(p[0] for p in uv), 1 + 1e-7)
            self.assertLessEqual(max(p[0] for p in uv) - min(p[0] for p in uv), 1 / 9 + 1e-7)
        boundaries = [p for p in result["points"] if abs(p[1]) < 1e-8]
        self.assertEqual(len(boundaries), 4)

    def test_equal_coordinates_with_distinct_uv_ids_are_seams(self):
        points, faces, seed = cylinder()
        uv_sets = strip_uvs(faces, seams=(0, 5), same_coordinates=True)
        result = resample_mesh(points, faces, seed, 17, uv_sets)
        original_boundary = points[5]
        vertex = next(i for i, p in enumerate(result["points"]) if all(abs(p[d] - original_boundary[d]) < 1e-8 for d in range(3)))
        uv_ids = {uv_id for face, ids, uv in face_uvs(result) for index, uv_id in zip(face, ids) if index == vertex}
        self.assertEqual(len(uv_ids), 2)

    def test_each_face_separate_uv_island_survives_subdivision(self):
        points, faces, seed = cylinder()
        uv_sets = strip_uvs(faces, separate_faces=True)
        result = resample_mesh(points, faces, seed, 24, uv_sets)
        self.assert_valid_mesh(result)
        self.assertEqual(len(result["faces"]), 24)
        source_ids = {}
        for source, (face, ids, uv) in zip(result["face_sources"], face_uvs(result)):
            source_ids.setdefault(source, set()).update(ids)
        self.assertEqual(len(source_ids), 12)
        for source_a, ids_a in source_ids.items():
            for source_b, ids_b in source_ids.items():
                if source_a != source_b:
                    self.assertFalse(ids_a & ids_b)
        with self.assertRaises(ValueError):
            resample_mesh(points, faces, seed, 6, uv_sets)

    def test_unmapped_faces_remain_unmapped(self):
        points, faces, seed = cylinder()
        result = resample_mesh(points, faces, seed, 24, strip_uvs(faces, missing_faces=(2,)))
        self.assert_valid_mesh(result)
        unmapped = [source for source, count in zip(result["face_sources"], result["uv_sets"]["map1"]["counts"]) if count == 0]
        self.assertTrue(unmapped)
        self.assertEqual(set(unmapped), {2})

    def test_all_uv_sets_preserved(self):
        points, faces, seed = cylinder()
        uv_sets = strip_uvs(faces)
        uv_sets["lightmap"] = strip_uvs(faces, seams=(0, 6))["map1"]
        result = resample_mesh(points, faces, seed, 18, uv_sets)
        self.assertEqual(set(result["uv_sets"]), {"map1", "lightmap"})
        self.assert_valid_mesh(result)

    def test_disconnected_component_and_uvs_unchanged(self):
        points, faces, seed = cylinder()
        original_face_count = len(faces)
        points += [(5, 0, 0), (6, 0, 0), (5, 1, 0)]
        faces.append([24, 25, 26])
        uv_sets = strip_uvs(faces)
        result = resample_mesh(points, faces, seed, 18, uv_sets)
        original = [(5, 0, 0), (6, 0, 0), (5, 1, 0)]
        face_id = result["face_sources"].index(original_face_count)
        self.assertEqual([tuple(result["points"][i]) for i in result["faces"][face_id]], original)
        original_data = uv_sets["map1"]
        old_ids = original_data["ids"][-3:]
        expected_uv = [(original_data["u"][i], original_data["v"][i]) for i in old_ids]
        self.assertEqual(list(face_uvs(result))[face_id][2], expected_uv)

    def test_rejects_open_seed_branch_and_nonmanifold(self):
        points, faces, seed = cylinder()
        for bad_seed in (seed[:-1], seed + [(0, 12)], [(0, 12), (12, 13), (13, 1), (1, 0)]):
            with self.subTest(seed=bad_seed), self.assertRaises(ValueError):
                analyze_mesh(points, faces, bad_seed)
        with self.assertRaises(ValueError):
            analyze_mesh(points, faces + [faces[0]], seed)
        branched_points = points + [(2, 0, 0), (2, 0, 2)]
        with self.assertRaises(ValueError):
            analyze_mesh(branched_points, faces + [[0, 12, 25, 24]], seed)

    def test_rejects_invalid_counts_and_degenerate_input(self):
        points, faces, seed = cylinder()
        for count in (0, 1, 2, -4, None, float("inf"), 6.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                resample_mesh(points, faces, seed, count)
        with self.assertRaises(ValueError):
            analyze_mesh(points, faces, [])

    def test_rejects_caps_sharing_nonmanifold_center_vertex(self):
        points, faces, seed = cylinder(caps="fan")
        faces = [[24 if vertex == 25 else vertex for vertex in face] for face in faces]
        with self.assertRaises(ValueError):
            analyze_mesh(points, faces, seed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
