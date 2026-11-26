"""Double-precision inverse compensation for differential-protection tests."""

from dataclasses import dataclass
from enum import IntEnum
from math import isfinite


_EPS = 1e-12


class CalcStatus(IntEnum):
    OK = 0
    INVALID_CONFIG = 1
    INVALID_TARGET = 2
    UNREACHABLE_TARGET = 3
    NUMERIC_ERROR = 4


@dataclass(frozen=True)
class Phasor:
    """Peak complex amplitude: I = re + j*im."""

    re: float
    im: float


@dataclass(frozen=True)
class DiffProfile:
    """Explicit relay compensation profile supplied by an external adapter."""

    matrix1: int
    matrix2: int
    kp1: float
    kp2: float


@dataclass(frozen=True)
class SideSolution:
    """Minimum-norm base solution and its forward-calculation residual."""

    base: tuple[Phasor, Phasor, Phasor]
    hasCommon: bool
    residual: tuple[Phasor, Phasor, Phasor]


@dataclass(frozen=True)
class OutputSolution:
    """Two-side result; failedSide is 0 for global errors, otherwise 1 or 2."""

    status: CalcStatus
    matrix1: int
    matrix2: int
    side1: SideSolution | None
    side2: SideSolution | None
    failedSide: int = 0


_ONE_THIRD = 0.3333333333333333
_TWO_THIRDS = 0.6666666666666666
_SQRT3_THIRDS = 0.5773502691896257


# Frozen standard matrices M0..M12. M0 is the identity matrix; M12 is the
# zero-sequence-removing projection and is intentionally a different entry.
MATRIX_TABLE = (
    (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ),
    (
        (_SQRT3_THIRDS, -_SQRT3_THIRDS, 0.0),
        (0.0, _SQRT3_THIRDS, -_SQRT3_THIRDS),
        (-_SQRT3_THIRDS, 0.0, _SQRT3_THIRDS),
    ),
    (
        (_ONE_THIRD, -_TWO_THIRDS, _ONE_THIRD),
        (_ONE_THIRD, _ONE_THIRD, -_TWO_THIRDS),
        (-_TWO_THIRDS, _ONE_THIRD, _ONE_THIRD),
    ),
    (
        (0.0, -_SQRT3_THIRDS, _SQRT3_THIRDS),
        (_SQRT3_THIRDS, 0.0, -_SQRT3_THIRDS),
        (-_SQRT3_THIRDS, _SQRT3_THIRDS, 0.0),
    ),
    (
        (-_ONE_THIRD, -_ONE_THIRD, _TWO_THIRDS),
        (_TWO_THIRDS, -_ONE_THIRD, -_ONE_THIRD),
        (-_ONE_THIRD, _TWO_THIRDS, -_ONE_THIRD),
    ),
    (
        (-_SQRT3_THIRDS, 0.0, _SQRT3_THIRDS),
        (_SQRT3_THIRDS, -_SQRT3_THIRDS, 0.0),
        (0.0, _SQRT3_THIRDS, -_SQRT3_THIRDS),
    ),
    (
        (-_TWO_THIRDS, _ONE_THIRD, _ONE_THIRD),
        (_ONE_THIRD, -_TWO_THIRDS, _ONE_THIRD),
        (_ONE_THIRD, _ONE_THIRD, -_TWO_THIRDS),
    ),
    (
        (-_SQRT3_THIRDS, _SQRT3_THIRDS, 0.0),
        (0.0, -_SQRT3_THIRDS, _SQRT3_THIRDS),
        (_SQRT3_THIRDS, 0.0, -_SQRT3_THIRDS),
    ),
    (
        (-_ONE_THIRD, _TWO_THIRDS, -_ONE_THIRD),
        (-_ONE_THIRD, -_ONE_THIRD, _TWO_THIRDS),
        (_TWO_THIRDS, -_ONE_THIRD, -_ONE_THIRD),
    ),
    (
        (0.0, _SQRT3_THIRDS, -_SQRT3_THIRDS),
        (-_SQRT3_THIRDS, 0.0, _SQRT3_THIRDS),
        (_SQRT3_THIRDS, -_SQRT3_THIRDS, 0.0),
    ),
    (
        (_ONE_THIRD, _ONE_THIRD, -_TWO_THIRDS),
        (-_TWO_THIRDS, _ONE_THIRD, _ONE_THIRD),
        (_ONE_THIRD, -_TWO_THIRDS, _ONE_THIRD),
    ),
    (
        (_SQRT3_THIRDS, 0.0, -_SQRT3_THIRDS),
        (-_SQRT3_THIRDS, _SQRT3_THIRDS, 0.0),
        (0.0, -_SQRT3_THIRDS, _SQRT3_THIRDS),
    ),
    (
        (_TWO_THIRDS, -_ONE_THIRD, -_ONE_THIRD),
        (-_ONE_THIRD, _TWO_THIRDS, -_ONE_THIRD),
        (-_ONE_THIRD, -_ONE_THIRD, _TWO_THIRDS),
    ),
)


def _finiteFloat(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if isfinite(number) else None


def _validMatrix(number) -> bool:
    return isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= 12


def _validProfile(profile) -> bool:
    return (
        isinstance(profile, DiffProfile)
        and _validMatrix(profile.matrix1)
        and _validMatrix(profile.matrix2)
        and (kp1 := _finiteFloat(profile.kp1)) is not None
        and (kp2 := _finiteFloat(profile.kp2)) is not None
        and kp1 > 0.0
        and kp2 > 0.0
    )


def _normaliseTarget(targetCurrent):
    if not isinstance(targetCurrent, (tuple, list)) or len(targetCurrent) != 2:
        return None, 0

    target = []
    for sideNumber, side in enumerate(targetCurrent, 1):
        if not isinstance(side, (tuple, list)) or len(side) != 3:
            return None, sideNumber

        values = []
        for value in side:
            if not isinstance(value, Phasor):
                return None, sideNumber
            re, im = _finiteFloat(value.re), _finiteFloat(value.im)
            if re is None or im is None:
                return None, sideNumber
            values.append(Phasor(re, im))
        target.append(tuple(values))

    return tuple(target), 0


def _multiply(matrix, current, transpose=False):
    return tuple(
        Phasor(
            sum(
                (matrix[j][i] if transpose else matrix[i][j]) * current[j].re
                for j in range(3)
            ),
            sum(
                (matrix[j][i] if transpose else matrix[i][j]) * current[j].im
                for j in range(3)
            ),
        )
        for i in range(3)
    )


def _finiteVector(current) -> bool:
    return all(isfinite(part) for value in current for part in (value.re, value.im))


def _isReachable(matrixNumber, target) -> bool:
    if matrixNumber == 0:
        return True

    reScale = max(1.0, *(abs(value.re) for value in target))
    imScale = max(1.0, *(abs(value.im) for value in target))
    return (
        abs(sum(value.re for value in target)) <= _EPS * reScale
        and abs(sum(value.im for value in target)) <= _EPS * imScale
    )


def _solveSide(matrixNumber, kp, target):
    if not _isReachable(matrixNumber, target):
        return CalcStatus.UNREACHABLE_TARGET, None

    matrix = MATRIX_TABLE[matrixNumber]
    transformed = target if matrixNumber == 0 else _multiply(matrix, target, True)
    base = tuple(Phasor(value.re / kp, value.im / kp) for value in transformed)
    calculated = _multiply(matrix, base)
    calculated = tuple(Phasor(kp * value.re, kp * value.im) for value in calculated)
    residual = tuple(
        Phasor(actual.re - expected.re, actual.im - expected.im)
        for actual, expected in zip(calculated, target)
    )

    if not _finiteVector(base) or not _finiteVector(residual):
        return CalcStatus.NUMERIC_ERROR, None

    return CalcStatus.OK, SideSolution(base, matrixNumber != 0, residual)


def _errorResult(status, matrix1=-1, matrix2=-1, side1=None, failedSide=0):
    return OutputSolution(status, matrix1, matrix2, side1, None, failedSide)


def calcOutputSolution(profile: DiffProfile, targetCurrent) -> OutputSolution:
    """Return both sides' output-current solution spaces.

    All phasors are Peak rectangular values. For M1..M12, each target must have
    zero complex sum within a scale-relative 1e-12 tolerance. The returned
    residual is diagnostic only.
    """

    if not _validProfile(profile):
        return _errorResult(CalcStatus.INVALID_CONFIG)

    matrix1, matrix2 = profile.matrix1, profile.matrix2
    target, failedSide = _normaliseTarget(targetCurrent)
    if target is None:
        return _errorResult(
            CalcStatus.INVALID_TARGET, matrix1, matrix2, failedSide=failedSide
        )
    kp1, kp2 = float(profile.kp1), float(profile.kp2)

    status1, side1 = _solveSide(matrix1, kp1, target[0])
    if status1 != CalcStatus.OK:
        return _errorResult(status1, matrix1, matrix2, failedSide=1)

    status2, side2 = _solveSide(matrix2, kp2, target[1])
    if status2 != CalcStatus.OK:
        return _errorResult(status2, matrix1, matrix2, side1=side1, failedSide=2)

    return OutputSolution(CalcStatus.OK, matrix1, matrix2, side1, side2)


__all__ = (
    "Phasor",
    "DiffProfile",
    "CalcStatus",
    "SideSolution",
    "OutputSolution",
    "calcOutputSolution",
)


if __name__ == "__main__":
    exampleProfile = DiffProfile(matrix1=1, matrix2=0, kp1=1.0, kp2=1.0)
    exampleTarget = (
        (Phasor(1.25, 0.0), Phasor(-1.25, 0.0), Phasor(0.0, 0.0)),
        (Phasor(-0.75, 0.0), Phasor(0.75, 0.0), Phasor(0.0, 0.0)),
    )
    example = calcOutputSolution(exampleProfile, exampleTarget)
    print("status:", example.status.name)
    if example.side1 and example.side2:
        print("side1.base:", example.side1.base)
        print("side1.hasCommon:", example.side1.hasCommon)
        print("side1.residual:", example.side1.residual)
        print("side2.base:", example.side2.base)
        print("side2.hasCommon:", example.side2.hasCommon)
        print("side2.residual:", example.side2.residual)
