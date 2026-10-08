"""Run real geometry paths with the math interface available in Python 3.7."""

import math
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cylinder_resample import core
from test_resample import cylinder, nonuniform_cylinder, strip_uvs


def legacy_math():
    namespace = {name: value for name, value in vars(math).items()
                 if name != "dist"}

    def hypot(x, y):
        return math.hypot(x, y)

    namespace["hypot"] = hypot
    return types.SimpleNamespace(**namespace)


class Python37CoreCompatibilityTests(unittest.TestCase):
    def test_legacy_math_surface_has_no_dist_or_variadic_hypot(self):
        interface = legacy_math()
        self.assertFalse(hasattr(interface, "dist"))
        self.assertEqual(interface.hypot(3, 4), 5)
        with self.assertRaises(TypeError):
            interface.hypot(3, 4, 12)

    def test_all_modes_keep_uv_sets_caps_and_constraints_with_legacy_math(self):
        profile = ((0.8, 0.0), (1.0, 0.2), (1.0, 2.0))
        points, faces, seed = nonuniform_cylinder(profile=profile)
        faces += [list(reversed(range(12))), list(range(24, 36))]
        uv_sets = strip_uvs(faces, ring_count=3, seams=(0, 3, 7))
        uv_sets["lightmap"] = strip_uvs(faces, ring_count=3, seams=(0, 6))["map1"]
        original = repr((points, faces, seed, uv_sets))
        for mode in ("contour", "uniform", "circle"):
            with self.subTest(mode=mode):
                expected = core.resample_mesh(points, faces, seed, 24, uv_sets,
                                              shape_mode=mode, protected_columns=(0, 3, 7))
                with mock.patch.object(core, "math", legacy_math()):
                    result = core.resample_mesh(points, faces, seed, 24, uv_sets,
                                                shape_mode=mode, protected_columns=(0, 3, 7))
                self.assertEqual(result, expected)
                self.assertEqual(len(result["new_rings"]), 3)
                self.assertEqual(len(result["faces"]), 50)
                self.assertEqual(set(result["uv_sets"]), {"map1", "lightmap"})
                self.assertTrue({0, 3, 6, 7}.issubset(result["protected_columns"]))
                for data in result["uv_sets"].values():
                    self.assertEqual(len(data["counts"]), len(result["faces"]))
                    self.assertEqual(sum(data["counts"]), len(data["ids"]))
                    self.assertTrue(all(0 <= uv_id < len(data["u"]) for uv_id in data["ids"]))
                self.assertEqual(repr((points, faces, seed, uv_sets)), original)

    def test_distance_avoids_squared_overflow_and_underflow_with_legacy_math(self):
        with mock.patch.object(core, "math", legacy_math()):
            for scale in (1.0e-300, 1.0e-200, 1.0, 1.0e200, 1.0e300):
                with self.subTest(scale=scale):
                    distance = core._distance((3 * scale, 4 * scale, 12 * scale),
                                              (0.0, 0.0, 0.0))
                    self.assertTrue(math.isfinite(distance))
                    self.assertGreater(distance, 0.0)
                    self.assertAlmostEqual(distance / scale, 13.0, places=12)

    def test_all_modes_and_uvs_survive_extreme_scene_units_with_legacy_math(self):
        points, faces, seed = cylinder(n=8)
        uv_sets = strip_uvs(faces, n=8)
        with mock.patch.object(core, "math", legacy_math()):
            for scale in (1.0e-200, 1.0e200):
                scaled = [tuple(value * scale for value in point) for point in points]
                for mode in ("contour", "uniform", "circle"):
                    with self.subTest(scale=scale, mode=mode):
                        result = core.resample_mesh(scaled, faces, seed, 16, uv_sets,
                                                    shape_mode=mode)
                        self.assertEqual(len(result["points"]), 32)
                        self.assertEqual(len(result["faces"]), 16)
                        self.assertTrue(all(math.isfinite(value)
                                            for point in result["points"] for value in point))
                        self.assertGreater(result["stats"]["min_segment_length"], 0.0)
                        self.assertTrue(math.isfinite(result["stats"]["max_segment_length"]))
                        data = result["uv_sets"]["map1"]
                        self.assertEqual(data["counts"], [4] * 16)
                        self.assertEqual(len(data["ids"]), 64)
                        if mode == "circle":
                            self.assertTrue(all(abs(math.hypot(point[0], point[1]) / scale - 1.0)
                                                < 1.0e-7 for point in result["points"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
