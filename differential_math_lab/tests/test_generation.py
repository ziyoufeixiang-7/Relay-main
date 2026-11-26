from __future__ import annotations

import unittest

from diffmath import (
    EquationConfig,
    LegacyProtectionSettings,
    ResidualRelayModel,
    ResidualWindingModel,
    explicit_profile,
    generate_harmonic_point,
    generate_ratio_point,
    generate_state_pair,
    generate_through_current,
    generate_zero_sequence_point,
    profile_from_legacy_settings,
)
from diffmath.two_current import generate_2i_point, legacy_2i_model


class SixCurrentGenerationTests(unittest.TestCase):
    def setUp(self):
        self.equations = EquationConfig("id_sum", "ir_diff_k", 2.0)

    def assert_all_phases_close(self, model):
        for phase in ("A", "B", "C", "AB", "BC", "CA", "ABC"):
            with self.subTest(model=model.name, phase=phase):
                point = generate_ratio_point(
                    model,
                    id_value=0.5,
                    ir_value=1.0,
                    test_phase=phase,
                )
                active = [
                    index
                    for index, value in enumerate(point.target_basis)
                    if abs(value) > 1e-8
                ]
                for index in active:
                    self.assertAlmostEqual(point.measurement.id_values[index], 0.5, places=8)
                    self.assertAlmostEqual(point.measurement.ir_values[index], 1.0, places=8)

    def test_yd11_y_side_all_single_and_multiphase_points(self):
        model = profile_from_legacy_settings(
            LegacyProtectionSettings("y", "d", 0, 11, "y-side", False),
            self.equations,
        )
        self.assertEqual((model.winding1.matrix_number, model.winding2.matrix_number), (1, 0))
        self.assert_all_phases_close(model)

    def test_yy12_y_side_all_single_and_multiphase_points(self):
        model = profile_from_legacy_settings(
            LegacyProtectionSettings("y", "y", 0, 12, "y-side", False),
            self.equations,
        )
        self.assertEqual((model.winding1.matrix_number, model.winding2.matrix_number), (11, 11))
        self.assert_all_phases_close(model)

    def test_all_sel_style_matrix_pairs_and_phase_patterns_close(self):
        for matrix1 in range(13):
            for matrix2 in range(13):
                model = explicit_profile(
                    matrix1=matrix1,
                    matrix2=matrix2,
                    kp1=1.0,
                    kp2=1.0,
                    equations=self.equations,
                )
                for phase in ("A", "B", "C", "AB", "BC", "CA", "ABC"):
                    with self.subTest(matrix1=matrix1, matrix2=matrix2, phase=phase):
                        point = generate_ratio_point(
                            model,
                            id_value=0.5,
                            ir_value=1.0,
                            test_phase=phase,
                        )
                        for index, basis in enumerate(point.target_basis):
                            self.assertAlmostEqual(
                                point.measurement.id_values[index],
                                0.5 * abs(basis),
                                places=7,
                            )
                            self.assertAlmostEqual(
                                point.measurement.ir_values[index],
                                abs(basis),
                                places=7,
                            )

    def test_incremental_mode_adds_delta_to_prefault_load(self):
        model = profile_from_legacy_settings(
            LegacyProtectionSettings("y", "d", 0, 11, "y-side", False),
            self.equations,
        )
        states = generate_state_pair(
            model,
            id_value=0.6,
            ir_value=1.2,
            test_phase="ABC",
            diff_mode=2,
            load_current=0.8,
        )
        self.assertLess(states.steady_prefault.max_id, 1e-9)
        for value in states.checked_quantity.id_values:
            self.assertAlmostEqual(value, 0.6, places=8)
        for value in states.checked_quantity.ir_values:
            self.assertAlmostEqual(value, 1.2, places=8)

    def test_harmonic_ratio_survives_compensation(self):
        model = profile_from_legacy_settings(
            LegacyProtectionSettings("y", "d", 0, 11, "y-side", False),
            self.equations,
        )
        point = generate_harmonic_point(
            model,
            fundamental_id=2.0,
            harmonic_value=15.0,
            order=2,
            apply_side="i1",
            error_type="relative",
            test_phase="ABC",
        )
        self.assertEqual(point.order, 2)
        for content in point.measured_content:
            self.assertAlmostEqual(content, 15.0, places=8)

    def test_harmonic_ratio_is_current_ratio_even_for_squared_id(self):
        model = explicit_profile(
            matrix1=1,
            matrix2=0,
            kp1=1.0,
            kp2=1.0,
            equations=EquationConfig("id_sum_sq", "ir_sum_sq_cos", 2.0),
        )
        point = generate_harmonic_point(
            model,
            fundamental_id=2.0,
            harmonic_value=15.0,
            order=2,
            apply_side="i1",
            error_type="relative",
            test_phase="ABC",
            relative_angle_deg=37.0,
        )
        for content in point.measured_content:
            self.assertAlmostEqual(content, 15.0, places=8)

    def test_single_side_harmonic_basis_depends_only_on_applied_matrix(self):
        model = explicit_profile(
            matrix1=0,
            matrix2=1,
            kp1=1.0,
            kp2=1.0,
            equations=self.equations,
        )
        point = generate_harmonic_point(
            model,
            fundamental_id=2.0,
            harmonic_value=15.0,
            order=2,
            apply_side="i1",
            error_type="relative",
            test_phase="A",
        )
        self.assertGreater(abs(point.fundamental1[0]), 1.9)
        self.assertLess(abs(point.fundamental1[1]), 1e-10)
        self.assertLess(abs(point.fundamental1[2]), 1e-10)

    def test_invalid_harmonic_enums_are_rejected(self):
        model = explicit_profile(
            matrix1=0,
            matrix2=0,
            kp1=1.0,
            kp2=1.0,
            equations=self.equations,
        )
        with self.assertRaises(ValueError):
            generate_harmonic_point(
                model,
                fundamental_id=1.0,
                harmonic_value=15.0,
                order=2,
                apply_side="i1",
                error_type="typo",  # type: ignore[arg-type]
                test_phase="A",
            )

    def test_through_current_uses_id_equation_polarity(self):
        for id_equation, ir_equation in (
            ("id_sum", "ir_diff_k"),
            ("id_diff", "ir_sum_k"),
            ("id_sum_sq", "ir_sum_sq_cos"),
            ("id_diff_sq", "ir_diff_sq_cos"),
        ):
            with self.subTest(id_equation=id_equation):
                model = explicit_profile(
                    matrix1=1,
                    matrix2=11,
                    kp1=1.0,
                    kp2=1.0,
                    equations=EquationConfig(id_equation, ir_equation, 2.0),
                )
                point = generate_through_current(model, 0.8)
                self.assertLess(point.measurement.max_id, 1e-8)

    def test_channel_order_and_polarity_close_inverse_loop(self):
        model = explicit_profile(
            matrix1=1,
            matrix2=0,
            kp1=1.0,
            kp2=1.0,
            channel_order1=(0, 2, 1),
            channel_polarities1=(1, -1, 1),
            equations=self.equations,
        )
        point = generate_ratio_point(
            model, id_value=0.5, ir_value=1.0, test_phase="ABC"
        )
        for actual in point.measurement.id_values:
            self.assertAlmostEqual(actual, 0.5, places=8)

    def test_explicit_zero_sequence_model(self):
        residual = ResidualRelayModel(
            ResidualWindingModel(2.0),
            ResidualWindingModel(0.5),
            self.equations,
            "3I0",
        )
        _, _, actual = generate_zero_sequence_point(residual, id_value=0.5, ir_value=1.0)
        self.assertAlmostEqual(actual[0], 0.5, places=8)
        self.assertAlmostEqual(actual[1], 1.0, places=8)

    def test_residual_channel_polarity_is_used_in_both_directions(self):
        residual = ResidualRelayModel(
            ResidualWindingModel(2.0, channel_polarities=(1, -1, 1)),
            ResidualWindingModel(0.5, channel_order=(0, 2, 1)),
            self.equations,
            "I0",
        )
        _, _, actual = generate_zero_sequence_point(residual, id_value=0.5, ir_value=1.0)
        self.assertAlmostEqual(actual[0], 0.5, places=8)
        self.assertAlmostEqual(actual[1], 1.0, places=8)


class TwoCurrentGenerationTests(unittest.TestCase):
    def test_legacy_gain_and_parallel_distribution_close_forward_loop(self):
        equations = EquationConfig("id_sum", "ir_diff_k", 2.0)
        model = legacy_2i_model(
            letter1="y",
            letter2="d",
            phase_correction="y-side",
            kp1=1.2,
            kp2=0.8,
            equations=equations,
        )
        point = generate_2i_point(
            model,
            id_value=0.5,
            ir_value=1.0,
            side1_parallel_outputs=2,
            side2_parallel_outputs=3,
        )
        self.assertAlmostEqual(sum(point.side1_outputs), point.side1_total)
        self.assertAlmostEqual(sum(point.side2_outputs), point.side2_total)
        self.assertAlmostEqual(point.actual_id, 0.5)
        self.assertAlmostEqual(point.actual_ir, 1.0)


if __name__ == "__main__":
    unittest.main()
