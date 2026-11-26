from __future__ import annotations

import unittest

from diffmath.equations import (
    EquationConfig,
    EquationDomainError,
    evaluate_pair,
    scale_api_targets,
    solve_scalar,
)


class EquationTests(unittest.TestCase):
    def assert_solution(self, config: EquationConfig, id_value: float, ir_value: float):
        result = solve_scalar(id_value, ir_value, config)
        self.assertAlmostEqual(result.actual_id, id_value, places=9)
        self.assertAlmostEqual(result.actual_ir, ir_value, places=9)

    def test_supported_linear_equations_forward_check(self):
        cases = (
            ("id_i1", "ir_i2", 1.0, 0.5),
            ("id_sum", "ir_diff_k", 0.5, 1.0),
            ("id_diff", "ir_sum_k", 0.5, 1.0),
            ("id_sum", "ir_sum_div_k", 0.5, 1.0),
            ("id_sum", "ir_max_k", 0.2, 1.0),
            ("id_sum", "ir_i2_k", 0.5, 1.0),
            ("id_sum", "ir_id_abs_diff", 0.5, 1.0),
            ("id_sum", "ir_sqrt_cos", 0.5, 1.0),
        )
        for id_eq, ir_eq, id_value, ir_value in cases:
            with self.subTest(id_eq=id_eq, ir_eq=ir_eq):
                self.assert_solution(EquationConfig(id_eq, ir_eq, 2.0), id_value, ir_value)

    def test_constrained_equation_accepts_only_its_line(self):
        self.assert_solution(EquationConfig("id_diff", "ir_diff_k", 2.0), 0.5, 0.25)
        with self.assertRaises(EquationDomainError):
            solve_scalar(0.5, 1.0, EquationConfig("id_diff", "ir_diff_k", 2.0))

    def test_square_equations_use_squared_units(self):
        self.assert_solution(EquationConfig("id_sum_sq", "ir_sum_sq_cos", 2.0), 0.25, 1.0)
        self.assert_solution(EquationConfig("id_diff_sq", "ir_diff_sq_cos", 2.0), 0.25, 1.0)
        config = EquationConfig("id_sum_sq", "ir_sum_sq_cos", 2.0)
        self.assertEqual(
            scale_api_targets(0.25, 1.0, config, ie=5.0, is_per_unit=True),
            (6.25, 25.0),
        )

    def test_invalid_dimensional_pair_is_rejected(self):
        with self.assertRaises(EquationDomainError):
            solve_scalar(0.5, 1.0, EquationConfig("id_sum_sq", "ir_diff_k", 2.0))

    def test_abb_internal_fault_branch_is_not_rejected(self):
        result = solve_scalar(3.0, 1.0, EquationConfig.abb_615())
        self.assertAlmostEqual(result.winding1.real, 2.5)
        self.assertAlmostEqual(result.winding2.real, 0.5)
        self.assertAlmostEqual(result.actual_id, 3.0)
        self.assertAlmostEqual(result.actual_ir, 1.0)

    def test_maximum_restraint_covers_both_relative_polarity_branches(self):
        result = solve_scalar(1.5, 1.0, EquationConfig.maximum_magnitude())
        self.assertAlmostEqual(result.winding1.real, 1.0)
        self.assertAlmostEqual(result.winding2.real, 0.5)
        self.assertAlmostEqual(result.actual_id, 1.5)
        self.assertAlmostEqual(result.actual_ir, 1.0)

    def test_two_winding_imax_literal_and_sqrt_alternate_branches(self):
        self.assert_solution(
            EquationConfig("id_sum", "ir_imax_sum_k", 2.0), 3.0, 1.0
        )
        self.assert_solution(
            EquationConfig("id_diff", "ir_sqrt_cos", 2.0), 3.0, 1.0
        )

    def test_negative_squared_restraint_domain_is_rejected(self):
        with self.assertRaises(EquationDomainError):
            evaluate_pair(1 + 0j, 1 + 0j, EquationConfig("id_sum_sq", "ir_sum_sq_cos"))
        with self.assertRaises(EquationDomainError):
            evaluate_pair(1 + 0j, -1 + 0j, EquationConfig("id_diff_sq", "ir_diff_sq_cos"))

    def test_nonfinite_coordinates_are_rejected(self):
        with self.assertRaises(EquationDomainError):
            solve_scalar(float("nan"), 1.0, EquationConfig())


if __name__ == "__main__":
    unittest.main()
