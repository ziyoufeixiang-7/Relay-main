import inspect
import math
import random
import unittest

import DiffCalc as core
from DiffCalc import CalcStatus, DiffProfile, Phasor, calcOutputSolution


ZERO = (Phasor(0.0, 0.0),) * 3


def forward(matrixNumber, kp, current):
    matrix = core.MATRIX_TABLE[matrixNumber]
    transformed = tuple(
        Phasor(
            sum(matrix[i][j] * current[j].re for j in range(3)),
            sum(matrix[i][j] * current[j].im for j in range(3)),
        )
        for i in range(3)
    )
    return tuple(Phasor(kp * value.re, kp * value.im) for value in transformed)


class DiffCalcTests(unittest.TestCase):
    def assertVectorAlmostEqual(self, actual, expected, tolerance=1e-12):
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got.re, wanted.re, delta=tolerance)
            self.assertAlmostEqual(got.im, wanted.im, delta=tolerance)

    def testM0RestoresAnyTargetAndHasUniqueSolution(self):
        side1 = (
            Phasor(1.2, -0.7),
            Phasor(3.4, 5.6),
            Phasor(-8.9, 2.3),
        )
        side2 = (
            Phasor(-2.0, 4.0),
            Phasor(0.25, -0.5),
            Phasor(7.0, 9.0),
        )
        result = calcOutputSolution(DiffProfile(0, 0, 2.0, 0.5), (side1, side2))

        self.assertEqual(result.status, CalcStatus.OK)
        self.assertFalse(result.side1.hasCommon)
        self.assertFalse(result.side2.hasCommon)
        self.assertVectorAlmostEqual(
            result.side1.base,
            tuple(Phasor(value.re / 2.0, value.im / 2.0) for value in side1),
        )
        self.assertVectorAlmostEqual(
            result.side2.base,
            tuple(Phasor(value.re / 0.5, value.im / 0.5) for value in side2),
        )
        self.assertVectorAlmostEqual(result.side1.residual, ZERO)
        self.assertVectorAlmostEqual(result.side2.residual, ZERO)

    def testFrozenMatricesMatchStandardDefinition(self):
        offsets = (
            (0.0, 120.0, -120.0),
            (-120.0, 0.0, 120.0),
            (120.0, -120.0, 0.0),
        )
        self.assertEqual(
            core.MATRIX_TABLE[0],
            ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        )
        for matrixNumber in range(1, 13):
            for row in range(3):
                for column in range(3):
                    expected = (2.0 / 3.0) * math.cos(
                        math.radians(matrixNumber * 30.0 + offsets[row][column])
                    )
                    self.assertAlmostEqual(
                        core.MATRIX_TABLE[matrixNumber][row][column],
                        expected,
                        delta=5e-16,
                    )

    def testM1ReachableTargetInvertsAndCloses(self):
        target = (Phasor(1.0, 0.0), Phasor(-1.0, 0.0), Phasor(0.0, 0.0))
        result = calcOutputSolution(DiffProfile(1, 0, 1.0, 1.0), (target, ZERO))

        self.assertEqual(result.status, CalcStatus.OK)
        self.assertTrue(result.side1.hasCommon)
        self.assertVectorAlmostEqual(
            result.side1.base,
            (
                Phasor(core.MATRIX_TABLE[1][0][0], 0.0),
                Phasor(-2.0 * core.MATRIX_TABLE[1][0][0], 0.0),
                Phasor(core.MATRIX_TABLE[1][0][0], 0.0),
            ),
        )
        self.assertVectorAlmostEqual(forward(1, 1.0, result.side1.base), target)

    def testM1RejectsUnreachableTarget(self):
        target = (Phasor(1.0, 0.0), Phasor(0.0, 0.0), Phasor(0.0, 0.0))
        result = calcOutputSolution(DiffProfile(1, 0, 1.0, 1.0), (target, ZERO))

        self.assertEqual(result.status, CalcStatus.UNREACHABLE_TARGET)
        self.assertEqual(result.failedSide, 1)
        self.assertIsNone(result.side1)

    def testPositiveSequencePassesEveryNonzeroMatrix(self):
        target = (
            Phasor(1.0, 0.0),
            Phasor(-0.5, -math.sqrt(3.0) / 2.0),
            Phasor(-0.5, math.sqrt(3.0) / 2.0),
        )
        for matrixNumber in range(1, 13):
            with self.subTest(matrixNumber=matrixNumber):
                result = calcOutputSolution(
                    DiffProfile(matrixNumber, matrixNumber, 1.0, 1.0),
                    (target, target),
                )
                self.assertEqual(result.status, CalcStatus.OK)
                self.assertVectorAlmostEqual(result.side1.residual, ZERO)
                self.assertVectorAlmostEqual(result.side2.residual, ZERO)

    def testReachabilityToleranceScalesWithLargeTarget(self):
        amplitude = 1e6
        target = tuple(
            Phasor(
                amplitude * math.cos(math.radians(angle)),
                amplitude * math.sin(math.radians(angle)),
            )
            for angle in (0.0, -120.0, 120.0)
        )
        self.assertGreater(abs(sum(value.re for value in target)), 1e-12)

        result = calcOutputSolution(DiffProfile(1, 12, 1.0, 2.0), (target, target))
        self.assertEqual(result.status, CalcStatus.OK)
        self.assertVectorAlmostEqual(result.side1.residual, ZERO, tolerance=1e-9)
        self.assertVectorAlmostEqual(result.side2.residual, ZERO, tolerance=1e-9)

        unreachable = (
            Phasor(1e6, 0.0),
            Phasor(-1e6, 0.0),
            Phasor(2e-6, 0.0),
        )
        result = calcOutputSolution(DiffProfile(1, 0, 1.0, 1.0), (unreachable, ZERO))
        self.assertEqual(result.status, CalcStatus.UNREACHABLE_TARGET)

    def testCommonModeDoesNotChangeForwardTarget(self):
        rng = random.Random(20260904)
        for matrixNumber in range(1, 13):
            for _ in range(20):
                a = Phasor(rng.uniform(-10.0, 10.0), rng.uniform(-10.0, 10.0))
                b = Phasor(rng.uniform(-10.0, 10.0), rng.uniform(-10.0, 10.0))
                c = Phasor(-a.re - b.re, -a.im - b.im)
                target = (a, b, c)
                kp = rng.uniform(0.2, 4.0)
                result = calcOutputSolution(
                    DiffProfile(matrixNumber, 0, kp, 1.0), (target, ZERO)
                )
                self.assertEqual(result.status, CalcStatus.OK)

                common = Phasor(rng.uniform(-20.0, 20.0), rng.uniform(-20.0, 20.0))
                shifted = tuple(
                    Phasor(value.re + common.re, value.im + common.im)
                    for value in result.side1.base
                )
                self.assertVectorAlmostEqual(
                    forward(matrixNumber, kp, shifted), target, tolerance=1e-10
                )

    def testUnequalNonunitKpValuesClose(self):
        side1 = (Phasor(2.0, 1.0), Phasor(-0.5, -3.0), Phasor(-1.5, 2.0))
        side2 = (Phasor(-4.0, 0.5), Phasor(1.0, 2.0), Phasor(3.0, -2.5))
        result = calcOutputSolution(DiffProfile(3, 11, 0.73, 2.4), (side1, side2))

        self.assertEqual(result.status, CalcStatus.OK)
        self.assertVectorAlmostEqual(forward(3, 0.73, result.side1.base), side1)
        self.assertVectorAlmostEqual(forward(11, 2.4, result.side2.base), side2)

    def testM0AndM12HaveDifferentDomains(self):
        target = (Phasor(1.0, 0.0), Phasor(0.0, 0.0), Phasor(0.0, 0.0))
        m0 = calcOutputSolution(DiffProfile(0, 0, 1.0, 1.0), (target, ZERO))
        m12 = calcOutputSolution(DiffProfile(12, 0, 1.0, 1.0), (target, ZERO))

        self.assertEqual(m0.status, CalcStatus.OK)
        self.assertFalse(m0.side1.hasCommon)
        self.assertEqual(m12.status, CalcStatus.UNREACHABLE_TARGET)

    def testInvalidProfilesAndTargetsAreRejected(self):
        for profile in (
            DiffProfile(-1, 0, 1.0, 1.0),
            DiffProfile(13, 0, 1.0, 1.0),
            DiffProfile(True, 0, 1.0, 1.0),
            DiffProfile(0, 0, 0.0, 1.0),
            DiffProfile(0, 0, -1.0, 1.0),
            DiffProfile(0, 0, math.nan, 1.0),
            DiffProfile(0, 0, math.inf, 1.0),
        ):
            with self.subTest(profile=profile):
                self.assertEqual(
                    calcOutputSolution(profile, (ZERO, ZERO)).status,
                    CalcStatus.INVALID_CONFIG,
                )

        for target in (
            (ZERO,),
            ((Phasor(0.0, 0.0),) * 2, ZERO),
            ((0.0, 0.0, 0.0), ZERO),
            ((Phasor(math.nan, 0.0),) * 3, ZERO),
            ((Phasor(0.0, math.inf),) * 3, ZERO),
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    calcOutputSolution(DiffProfile(0, 0, 1.0, 1.0), target).status,
                    CalcStatus.INVALID_TARGET,
                )

    def testNonfiniteCalculationReturnsNumericError(self):
        target = (Phasor(1.0, 0.0), Phasor(-1.0, 0.0), Phasor(0.0, 0.0))
        result = calcOutputSolution(
            DiffProfile(1, 0, 5e-324, 1.0),
            (target, ZERO),
        )
        self.assertEqual(result.status, CalcStatus.NUMERIC_ERROR)
        self.assertEqual(result.failedSide, 1)

    def testRandomZeroSumTargetsHaveOnlyFloatingPointResidual(self):
        rng = random.Random(8675309)
        for matrixNumber in range(1, 13):
            for _ in range(200):
                def randomTarget():
                    a = Phasor(rng.uniform(-100.0, 100.0), rng.uniform(-100.0, 100.0))
                    b = Phasor(rng.uniform(-100.0, 100.0), rng.uniform(-100.0, 100.0))
                    return (a, b, Phasor(-a.re - b.re, -a.im - b.im))

                side1, side2 = randomTarget(), randomTarget()
                kp1, kp2 = rng.uniform(0.05, 20.0), rng.uniform(0.05, 20.0)
                result = calcOutputSolution(
                    DiffProfile(matrixNumber, matrixNumber, kp1, kp2),
                    (side1, side2),
                )
                self.assertEqual(result.status, CalcStatus.OK)
                self.assertVectorAlmostEqual(result.side1.residual, ZERO, tolerance=1e-10)
                self.assertVectorAlmostEqual(result.side2.residual, ZERO, tolerance=1e-10)

    def testCoreContainsNoFixedPointRuntime(self):
        source = inspect.getsource(core)
        for removedName in (
            "CURRENT_SHIFT",
            "KP_SHIFT",
            "MATRIX_SHIFT",
            "CURRENT_SCALE",
            "KP_SCALE",
            "MATRIX_SCALE",
            "INT32_MIN",
            "INT32_MAX",
            "INT64_MIN",
            "INT64_MAX",
            "_roundDiv",
            "_roundShift",
            "_checkedInt32",
            "_checkedInt64",
        ):
            self.assertNotIn(removedName, source)
        self.assertTrue(
            all(isinstance(value, float) for matrix in core.MATRIX_TABLE for row in matrix for value in row)
        )
        self.assertEqual(
            core.__all__,
            (
                "Phasor",
                "DiffProfile",
                "CalcStatus",
                "SideSolution",
                "OutputSolution",
                "calcOutputSolution",
            ),
        )


if __name__ == "__main__":
    unittest.main()
