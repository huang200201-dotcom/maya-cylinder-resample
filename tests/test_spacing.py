"""Exact-spacing feasibility uses physical measures and genuine fixed pins."""

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cylinder_resample.spacing import analyze_spacing


class SpacingAnalysisTests(unittest.TestCase):
    def test_four_quarter_pins_cannot_support_nine_equal_segments(self):
        result = analyze_spacing([1.0] * 16, (2, 6, 10, 14), 9)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["fixed_pin_count"], 4)
        self.assertEqual(result["interval_fractions"], (0.25,) * 4)
        self.assertEqual(result["ideal_interval_counts"], (2.25,) * 4)
        self.assertEqual(result["compatible_counts"], (8, 12))

    def test_zero_or_one_pin_allows_even_and_odd_counts(self):
        for pins in ((), (5,)):
            for target in (8, 9, 17):
                with self.subTest(pins=pins, target=target):
                    result = analyze_spacing([1.0] * 16, pins, target)
                    self.assertTrue(result["compatible"])
                    self.assertEqual(result["fixed_pin_count"], len(pins))
                    self.assertEqual(result["interval_fractions"], (1.0,))
                    self.assertEqual(result["compatible_counts"], (target - 1, target + 1))

    def test_nonzero_pins_do_not_add_an_artificial_zero_boundary(self):
        result = analyze_spacing([1.0] * 12, (1, 5, 9), 3)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["fixed_pin_count"], 3)
        self.assertEqual(result["ideal_interval_counts"], (1.0, 1.0, 1.0))
        self.assertEqual(result["compatible_counts"], (6,))

    def test_two_opposite_pins_require_even_counts(self):
        odd = analyze_spacing([1.0] * 20, (3, 13), 9)
        even = analyze_spacing([1.0] * 20, (3, 13), 10)
        self.assertFalse(odd["compatible"])
        self.assertEqual(odd["compatible_counts"], (8, 10))
        self.assertTrue(even["compatible"])

    def test_even_column_gaps_do_not_guarantee_equal_numeric_measure(self):
        lengths = [1.0] * 16
        lengths[2] = math.sqrt(2.0)
        result = analyze_spacing(lengths, (2, 6, 10, 14), 8)
        self.assertFalse(result["compatible"])
        self.assertNotEqual(result["interval_fractions"], (0.25,) * 4)
        self.assertEqual(result["compatible_counts"], ())

    def test_uneven_columns_can_have_equal_numeric_intervals(self):
        result = analyze_spacing([1.0, 1.0, 2.0, 4.0], (0, 2, 3), 8)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["interval_fractions"], (0.25, 0.25, 0.5))
        self.assertEqual(result["ideal_interval_counts"], (2.0, 2.0, 4.0))
        self.assertEqual(result["compatible_counts"], (4, 12))

    def test_scaled_measures_produce_identical_diagnostics(self):
        original = analyze_spacing([1.0, 3.0, 1.0, 3.0], (0, 2), 9)
        for scale in (1.0e-250, 1.0e250):
            with self.subTest(scale=scale):
                scaled = analyze_spacing([scale, 3 * scale, scale, 3 * scale], (0, 2), 9)
                self.assertEqual(scaled, original)

    def test_small_roundoff_is_tolerated_but_real_misalignment_is_not(self):
        roundoff = analyze_spacing([1.0, 1.0, 1.0, 1.0 + 1.0e-12], (0, 2), 10)
        shifted = analyze_spacing([1.0, 1.0, 1.0, 1.0 + 1.0e-5], (0, 2), 10)
        self.assertTrue(roundoff["compatible"])
        self.assertFalse(shifted["compatible"])

    def test_duplicate_and_unsorted_pins_do_not_change_intervals(self):
        result = analyze_spacing([1.0] * 16, (14, 2, 6, 10, 2), 9)
        expected = analyze_spacing([1.0] * 16, (2, 6, 10, 14), 9)
        self.assertEqual(result, expected)

    def test_nearby_search_respects_supported_count_bounds(self):
        self.assertEqual(analyze_spacing([1.0] * 8, (), 3)["compatible_counts"], (4,))
        self.assertEqual(analyze_spacing([1.0] * 8, (), 4096)["compatible_counts"], (4095,))
        result = analyze_spacing([1.0] * 16, (0, 4, 8, 12), 4095)
        self.assertEqual(result["compatible_counts"], (4092, 4096))

    def test_more_pins_than_target_is_not_compatible(self):
        result = analyze_spacing([1.0] * 8, range(8), 3)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["compatible_counts"], (8,))

    def test_search_can_be_skipped_for_cross_ring_verification(self):
        result = analyze_spacing([1.0] * 16, (2, 6, 10, 14), 9, find_compatible_counts=False)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["compatible_counts"], ())
        self.assertEqual(result["ideal_interval_counts"], (2.25,) * 4)

    def test_invalid_target_values_are_rejected(self):
        for target in (2, 4097, True, 9.5, "9", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                analyze_spacing([1.0] * 4, (), target)

    def test_invalid_measure_values_are_rejected(self):
        for lengths in ((), (1.0, 1.0), (1.0, 0.0, 1.0),
                        (1.0, -1.0, 1.0), (1.0, float("nan"), 1.0),
                        (1.0, float("inf"), 1.0), (1.0, None, 1.0)):
            with self.subTest(lengths=lengths), self.assertRaises(ValueError):
                analyze_spacing(lengths, (), 9)

    def test_invalid_fixed_columns_are_rejected(self):
        for pins in ((-1,), (4,), (0.5,), (True,), ("0",)):
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                analyze_spacing([1.0] * 4, pins, 9)


if __name__ == "__main__":
    unittest.main()
