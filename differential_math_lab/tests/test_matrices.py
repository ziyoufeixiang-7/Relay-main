from __future__ import annotations

import cmath
import math
import unittest

from diffmath.matrices import (
    apply_matrix,
    compensation_matrix,
    inverse_compensation,
    positive_sequence,
)


class CompensationMatrixTests(unittest.TestCase):
    def test_matrix_zero_is_identity(self):
        vector = (1 + 2j, 3 - 4j, -5 + 6j)
        self.assertEqual(apply_matrix(compensation_matrix(0), vector), vector)

    def test_matrix_one_matches_published_coefficients(self):
        root3 = math.sqrt(3)
        expected = (
            (1 / root3, -1 / root3, 0.0),
            (0.0, 1 / root3, -1 / root3),
            (-1 / root3, 0.0, 1 / root3),
        )
        actual = compensation_matrix(1)
        for row in range(3):
            for column in range(3):
                self.assertAlmostEqual(actual[row][column], expected[row][column], places=12)

    def test_every_nonzero_matrix_rejects_zero_sequence(self):
        for number in range(1, 13):
            with self.subTest(matrix=number):
                result = apply_matrix(compensation_matrix(number), (2j, 2j, 2j))
                self.assertLess(max(abs(value) for value in result), 1e-12)

    def test_positive_sequence_rotation(self):
        source = positive_sequence()
        for number in range(1, 13):
            with self.subTest(matrix=number):
                actual = apply_matrix(compensation_matrix(number), source)
                expected_rotation = cmath.rect(1.0, math.radians(number * 30.0))
                for got, original in zip(actual, source, strict=True):
                    self.assertAlmostEqual(abs(got - expected_rotation * original), 0.0, places=10)

    def test_negative_sequence_rotates_in_opposite_direction(self):
        source = (
            1 + 0j,
            cmath.rect(1.0, math.radians(120.0)),
            cmath.rect(1.0, math.radians(-120.0)),
        )
        for number in range(1, 13):
            with self.subTest(matrix=number):
                actual = apply_matrix(compensation_matrix(number), source)
                rotation = cmath.rect(1.0, math.radians(-number * 30.0))
                for got, original in zip(actual, source, strict=True):
                    self.assertAlmostEqual(abs(got - rotation * original), 0.0, places=10)

    def test_inverse_round_trip_for_all_matrices(self):
        target = (1 + 0.5j, -0.25 - 0.1j, -0.75 - 0.4j)
        for number in range(13):
            with self.subTest(matrix=number):
                raw = inverse_compensation(number, target)
                actual = apply_matrix(compensation_matrix(number), raw)
                self.assertLess(max(abs(a - b) for a, b in zip(actual, target)), 1e-10)

    def test_matrix_number_requires_integer(self):
        with self.assertRaises(TypeError):
            compensation_matrix(1.5)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
