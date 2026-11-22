"""Check exact and adaptive UV reduction through the mesh-building entry point."""

import copy
import math
import unittest

from test_resample import cylinder, face_uvs, output_ring
from test_reduction_uv import cyclic_strip_uvs, disk_uvs
from cylinder_resample import core


def tiled_faces(faces, unmapped=()):
    """A distinct, disjoint UV patch per face so cross-chart samples are visible."""
    data = {"u": [], "v": [], "ids": [], "counts": []}
    for fi, face in enumerate(faces):
        if fi in unmapped:
            data["counts"].append(0)
            continue
        data["counts"].append(len(face))
        for j, _ in enumerate(face):
            angle = math.tau * j / len(face)
            data["ids"].append(len(data["u"]))
            data["u"].append(2.0 * fi + 0.5 + 0.4 * math.cos(angle))
            data["v"].append(0.5 + 0.4 * math.sin(angle))
    return data


class UVTransferTests(unittest.TestCase):
    def test_per_face_uvs_survive_odd_reduction_without_stretching_between_tiles(self):
        points, faces, seed = cylinder(n=20, profile=((1, 0), (.8, .1), (.8, 1), (1, 1.1)), caps="fan")
        source = {"lightmap": tiled_faces(faces)}
        before = copy.deepcopy(source)
        for mode in ("uniform", "contour", "circle"):
            with self.subTest(mode=mode):
                result = core.resample_mesh(points, faces, seed, 9, source,
                                            shape_mode=mode, uv_policy="adaptive")
                self.assertEqual([len(ring) for ring in result["new_rings"]], [9] * 4)
                self.assertTrue(result["stats"]["uv_transfer_used"])
                self.assertGreaterEqual(result["stats"]["relaxed_uv_count"], 11)
                self.assertEqual(result["stats"]["uv_transfer_faces"]["lightmap"], 45)
                for face, ids, coords in face_uvs(result, "lightmap"):
                    self.assertEqual(len(face), len(ids))
                    self.assertEqual(len({math.floor(u) for u, v in coords}), 1)
                    area = sum(u * coords[(j + 1) % len(coords)][1] -
                               v * coords[(j + 1) % len(coords)][0]
                               for j, (u, v) in enumerate(coords))
                    self.assertGreater(abs(area), 1.0e-9)
                if mode == "circle":
                    self.assertAlmostEqual(result["stats"]["worst_spacing_ratio"], 1.0)
                self.assertEqual(source, before)

    def test_adaptive_and_strict_match_for_compatible_uvs_including_ngon(self):
        for caps in ("ngon", "fan"):
            points, faces, seed = cylinder(n=20, caps=caps)
            analysis = core.analyze_mesh(points, faces, seed)
            source = {"map1": disk_uvs(points, faces, analysis, cyclic=True, seams=(3, 12))}
            strict = core.resample_mesh(points, faces, seed, 9, source)
            adaptive = core.resample_mesh(points, faces, seed, 9, source, uv_policy="adaptive")
            for key in ("samples", "faces", "face_sources", "uv_sets", "points", "protected_columns"):
                self.assertEqual(strict[key], adaptive[key])
            self.assertFalse(adaptive["stats"]["uv_transfer_used"])
            self.assertEqual(adaptive["stats"]["relaxed_uv_count"], 0)

    def test_current_uv_set_has_priority_when_two_complete_sets_compete(self):
        points, faces, seed = cylinder(n=16)
        source = {"visible": cyclic_strip_uvs(faces, n=16, seams=(1, 5, 8, 11, 15)),
                  "secondary": cyclic_strip_uvs(faces, n=16, seams=(2, 7, 12))}
        for name in source:
            result = core.resample_mesh(points, faces, seed, 5, source, uv_policy="adaptive",
                                        preferred_uv_set=name)
            isolated = core.resample_mesh(points, faces, seed, 5, {name: source[name]})
            self.assertEqual(result["uv_sets"][name], isolated["uv_sets"][name])
            self.assertEqual(result["stats"]["uv_transfer_faces"][name], 0)
            other = next(key for key in source if key != name)
            self.assertGreater(result["stats"]["uv_transfer_faces"][other], 0)

    def test_nonzero_side_seam_survives_a_fragmented_fan_in_the_same_set(self):
        points, faces, seed = cylinder(n=20, caps="fan")
        strip = cyclic_strip_uvs(faces[:20], n=20, seams=(7, 16))
        fan = tiled_faces(faces[20:])
        base = len(strip["u"])
        for key in ("u", "v", "counts"):
            strip[key].extend(fan[key])
        strip["ids"].extend(base + v for v in fan["ids"])
        result = core.resample_mesh(points, faces, seed, 9, {"map1": strip}, uv_policy="adaptive")
        self.assertEqual(result["protected_columns"], [7, 16])
        self.assertEqual(result["stats"]["uv_transfer_faces"]["map1"], 18)
        self.assertEqual(len(output_ring(result)), 9)

    def test_mapped_and_unmapped_face_donors_are_consistent_across_all_corners(self):
        points, faces, seed = cylinder(n=12)
        # Each group of three is unmapped, mapped, or tied within one donor.
        data = tiled_faces(faces, unmapped={0, 1, 2, 4, 5, 9, 10, 11})
        result = core.resample_mesh(points, faces, seed, 4, {"map1": data}, uv_policy="adaptive")
        # Unmapped run of length two wins over adjacent one-column mapped patch.
        self.assertEqual(result["uv_sets"]["map1"]["counts"], [0, 0, 4, 0])
        self.assertEqual(len(result["uv_sets"]["map1"]["ids"]), 4)

    def test_strict_refusal_and_hard_material_minimum_are_independent(self):
        points, faces, seed = cylinder(n=20)
        data = {"map1": tiled_faces(faces)}
        with self.assertRaises(core.ConstraintError) as strict:
            core.resample_mesh(points, faces, seed, 7, data)
        self.assertEqual(strict.exception.minimum_count, 20)
        pins = (0, 2, 4, 6, 8, 10, 12, 14)
        with self.assertRaises(core.ConstraintError) as adaptive:
            core.resample_mesh(points, faces, seed, 7, data, uv_policy="adaptive", protected_columns=pins)
        self.assertEqual(adaptive.exception.minimum_count, len(pins))
        result = core.resample_mesh(points, faces, seed, 8, data, uv_policy="adaptive", protected_columns=pins)
        self.assertEqual(result["samples"], list(pins))
        self.assertGreater(result["stats"]["relaxed_uv_count"], 0)
