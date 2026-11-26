from __future__ import annotations

import unittest

from diffmath.legacy_validation import (
    known_multiphase_matrix_cases,
    known_single_phase_matrix_cases,
)


class LegacySinglePhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = known_single_phase_matrix_cases()

    def test_reported_standard_yd11_and_yy12_cases_close_forward_loop(self):
        selected = [
            item
            for item in self.cases
            if item.phase_correction == "y-side"
            and not item.zero_sequence_correction
        ]
        self.assertEqual(len(selected), 6)
        for item in selected:
            with self.subTest(group=item.group, phase=item.test_phase):
                self.assertTrue(item.passed)

    def test_current_yd11_y_side_plus_zero_sequence_defect_is_detected(self):
        defects = [
            item
            for item in self.cases
            if item.group == "Yd11"
            and item.phase_correction == "y-side"
            and item.zero_sequence_correction
        ]
        self.assertEqual(len(defects), 3)
        for item in defects:
            with self.subTest(phase=item.test_phase):
                self.assertFalse(item.passed)
                self.assertGreater(item.max_id_error, 0.1)

    def test_other_enumerated_yd11_yy12_cases_close_forward_loop(self):
        others = [
            item
            for item in self.cases
            if not (
                item.group == "Yd11"
                and item.phase_correction == "y-side"
                and item.zero_sequence_correction
            )
        ]
        for item in others:
            with self.subTest(
                group=item.group,
                correction=item.phase_correction,
                zero_sequence=item.zero_sequence_correction,
                phase=item.test_phase,
            ):
                self.assertTrue(item.passed)


class LegacyMultiphaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = known_multiphase_matrix_cases()

    def test_current_yd11_multiphase_branch_disagrees_with_profile(self):
        yd = [item for item in self.cases if item.group == "Yd11"]
        self.assertEqual(len(yd), 4)
        self.assertTrue(all(not item.passed for item in yd))

    def test_current_yy12_ab_loops_fail_but_balanced_abc_closes(self):
        yy = {item.test_phase: item for item in self.cases if item.group == "Yy12"}
        self.assertFalse(yy["AB"].passed)
        self.assertFalse(yy["BC"].passed)
        self.assertFalse(yy["CA"].passed)
        self.assertTrue(yy["ABC"].passed)

if __name__ == "__main__":
    unittest.main()
