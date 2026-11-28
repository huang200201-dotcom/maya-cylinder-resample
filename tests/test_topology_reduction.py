"""Closed hollow cylinders have a final band with the seed ring as its endpoint."""

from collections import Counter
import copy
import unittest

from test_resample import cylinder
from cylinder_resample import core
import test_ui_state as state


def washer(n=20, twist=0):
    profile = ((1.0, 0.0), (1.0, 1.9), (0.94, 2.0), (0.6, 2.0),
               (0.55, 1.9), (0.55, 0.0))
    points, faces, seed = cylinder(n, profile=profile)
    rows = len(profile)
    for i in range(n):
        j = (i + 1) % n
        faces.append([(rows - 1) * n + i, (rows - 1) * n + j,
                      (j + twist) % n, (i + twist) % n])
    # Duplicate the UV row across the profile closure and the column at U=1.
    u = [col / n for row in range(rows + 1) for col in range(n + 1)]
    v = [row / rows for row in range(rows + 1) for col in range(n + 1)]
    uv_ids = []
    for row in range(rows):
        for col in range(n):
            uv = row * (n + 1) + col
            uv_ids.extend([uv, uv + 1, uv + n + 2, uv + n + 1])
    uv_sets = {"map1": {"u": u, "v": v, "ids": uv_ids, "counts": [4] * len(faces)}}
    return points, faces, seed, uv_sets


class ClosedTopologyTests(unittest.TestCase):
    def test_closed_profile_from_inner_and_outer_wall_seeds(self):
        points, faces, seed, _ = washer()
        for row in (0, 2, 4):
            for reverse in (False, True):
                oriented = [face[::-1] for face in faces] if reverse else faces
                selected = [(20 * row + a, 20 * row + b) for a, b in seed]
                analysis = core.analyze_mesh(points, oriented, selected)
                self.assertEqual(analysis["caps"], [])
                self.assertEqual(len(analysis["bands"]), 6)
                self.assertEqual(analysis["rings"][analysis["seed_ring"]], list(range(20 * row, 20 * (row + 1))))
                self.assertEqual(analysis["bands"][-1]["rings"], (5, 0))
                self.assertEqual(sorted(fi for band in analysis["bands"] for fi in band["faces"]),
                                 list(range(len(faces))))

    def test_beveled_washer_reduces_and_remains_watertight_with_uvs(self):
        points, faces, seed, uv_sets = washer()
        before = copy.deepcopy((points, faces, uv_sets))
        for mode in ("contour", "uniform", "circle"):
            result = core.resample_mesh(points, faces, seed, 9, uv_sets, shape_mode=mode)
            self.assertEqual(len(result["points"]), 54)
            self.assertEqual(len(result["faces"]), 54)
            edges = Counter(tuple(sorted((a, b))) for face in result["faces"]
                            for a, b in zip(face, face[1:] + face[:1]))
            self.assertEqual(set(edges.values()), {2})
            rebuilt_seed = result["new_rings"][0]
            rebuilt = core.analyze_mesh(result["points"], result["faces"],
                                        list(zip(rebuilt_seed, rebuilt_seed[1:] + rebuilt_seed[:1])))
            self.assertEqual(rebuilt["caps"], [])
            data = core._parse_uvs(result["uv_sets"], result["faces"])
            self.assertEqual(core._uv_constraints(rebuilt, data), {0})
            self.assertEqual(result["uv_sets"]["map1"]["counts"], [4] * 54)
            self.assertEqual([sum(fi // 20 == row for fi in result["face_sources"]) for row in range(6)],
                             [9] * 6)
            # The fully mapped wrap row still has UV V=0 on one side and V=1 on the other.
            for vertex in rebuilt_seed:
                values = {data["map1"]["v"][corners[vertex]] for corners in data["map1"]["corners"]
                          if vertex in corners}
                self.assertEqual(values, {0.0, 1.0})
        self.assertEqual((points, faces, uv_sets), before)

    def test_material_and_hard_edge_on_closing_band_are_preserved(self):
        points, faces, seed, uv_sets = washer()
        analysis = core.analyze_mesh(points, faces, seed)
        closing = analysis["bands"][-1]
        assignments = [0] * len(faces)
        for fi in closing["faces"][5:10]:
            assignments[fi] = 1
        ra, rb = (analysis["rings"][r] for r in closing["rings"])
        snapshot = {"points": points, "faces": faces, "assignments": assignments,
                    "smoothing": {tuple(sorted((ra[15], rb[15]))): False}}
        adapter = state.load_adapter_module()
        material, hard, _ = adapter._column_constraints(snapshot, analysis, True)
        self.assertEqual(material, {5, 10})
        self.assertEqual(hard, {15})
        result = core.resample_mesh(points, faces, seed, 8, uv_sets, protected_columns=material | hard)
        self.assertEqual(sum(assignments[fi] for fi in result["face_sources"]), 2)
        index = result["samples"].index(15.0)
        edge = tuple(sorted(result["new_rings"][r][index] for r in closing["rings"]))
        self.assertFalse(adapter._edge_smoothing(snapshot, result, True)[edge])

    def test_misaligned_profile_closure_is_rejected(self):
        points, faces, seed, _ = washer(twist=1)
        with self.assertRaisesRegex(core.ResampleError, "回接"):
            core.analyze_mesh(points, faces, seed)

    def test_disconnected_nonmanifold_component_is_copied_verbatim(self):
        points, faces, seed, uv_sets = washer()
        start = len(points)
        points.extend([(4, 0, 0), (5, 0, 0), (4, 1, 0), (4, -1, 0), (4, 0, 1)])
        other = [[start + x for x in triangle] for triangle in ((0, 1, 2), (1, 0, 3), (0, 1, 4))]
        first = len(faces)
        faces.extend(other)
        uv = uv_sets["map1"]
        first_uv = len(uv["u"])
        uv["u"].extend([0, 1, 0] * 3)
        uv["v"].extend([0, 0, 1] * 3)
        uv["ids"].extend(range(first_uv, first_uv + 9))
        uv["counts"].extend([3, 3, 3])
        result = core.resample_mesh(points, faces, seed, 9, uv_sets)
        self.assertEqual(result["face_sources"][:3], list(range(first, first + 3)))
        for old_face, rebuilt in zip(other, result["faces"]):
            self.assertEqual([points[i] for i in old_face], [result["points"][i] for i in rebuilt])
        result_uv = result["uv_sets"]["map1"]
        expected_coords = [(uv["u"][i], uv["v"][i]) for i in range(first_uv, first_uv + 9)]
        actual_coords = [(result_uv["u"][i], result_uv["v"][i]) for i in result_uv["ids"][:9]]
        self.assertEqual(actual_coords, expected_coords)
        self.assertEqual(len(set(result_uv["ids"][:9])), 9)
        self.assertEqual(len(result_uv["u"]), len(set(result_uv["ids"])))
        with self.assertRaisesRegex(core.ResampleError, "非流形边"):
            core.analyze_mesh(points, faces, [(start, start + 1), (start + 1, start + 2), (start + 2, start)])

    def test_collapsed_washer_wall_is_rejected_before_rebuild(self):
        points, faces, seed, _ = washer()
        for column in range(20):
            points[20 + column] = points[column]
        with self.assertRaisesRegex(core.ResampleError, "零面积|退化"):
            core.resample_mesh(points, faces, seed, 9)

    def test_zero_thickness_closed_profile_and_thin_valid_washer(self):
        points, faces, seed, _ = washer()
        # Four different coplanar radial strips can each have nonzero area.
        flattened = []
        for i, (x, y, _) in enumerate(points):
            scale = 1.0 + 0.3 * (i // 20)
            x, y = x * scale, y * scale
            flattened.append((x, y, x))
        with self.assertRaisesRegex(core.ResampleError, "厚度为零"):
            core.analyze_mesh(flattened, faces, seed)
        thin = [(x, y, z * 1e-10) for x, y, z in points]
        result = core.resample_mesh(thin, faces, seed, 9)
        self.assertEqual(len(result["faces"]), 54)
