"""Three-phase phasor and transformer-differential compensation helpers."""

from __future__ import annotations

import cmath
import math
from typing import Iterable, Literal, Sequence, TypeAlias

Phasor3: TypeAlias = tuple[complex, complex, complex]
Matrix3: TypeAlias = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]

ZERO3: Phasor3 = (0j, 0j, 0j)
IDENTITY3: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


class MatrixDomainError(ValueError):
    """Raised when a requested compensated vector cannot be produced."""


def as_phasor3(values: Iterable[complex]) -> Phasor3:
    result = tuple(complex(value) for value in values)
    if len(result) != 3:
        raise ValueError(f"expected three phasors, got {len(result)}")
    if any(
        not math.isfinite(value.real) or not math.isfinite(value.imag)
        for value in result
    ):
        raise ValueError("phasors must be finite")
    return result  # type: ignore[return-value]


def compensation_matrix(number: int) -> Matrix3:
    """Return the SEL-style compensation Matrix 0..12.

    Matrix 0 is the identity and preserves zero sequence.  Matrices 1..12
    implement a counter-clockwise ``number * 30 degree`` positive-sequence
    rotation and reject zero sequence.  This cosine form is algebraically the
    published odd delta / even double-delta matrix table.  Matrix numbering is
    not universal across relay manufacturers.
    """

    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("compensation matrix number must be an integer")
    if number == 0:
        return IDENTITY3
    if not 1 <= number <= 12:
        raise ValueError(f"compensation matrix must be 0..12, got {number}")

    theta = math.radians(number * 30.0)
    c0 = math.cos(theta)
    cp = math.cos(theta + 2.0 * math.pi / 3.0)
    cm = math.cos(theta - 2.0 * math.pi / 3.0)
    factor = 2.0 / 3.0
    return (
        (factor * c0, factor * cp, factor * cm),
        (factor * cm, factor * c0, factor * cp),
        (factor * cp, factor * cm, factor * c0),
    )


def apply_matrix(matrix: Matrix3, vector: Sequence[complex]) -> Phasor3:
    vector3 = as_phasor3(vector)
    return tuple(
        sum(matrix[row][column] * vector3[column] for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def transpose(matrix: Matrix3) -> Matrix3:
    return tuple(
        tuple(matrix[column][row] for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def add(left: Sequence[complex], right: Sequence[complex]) -> Phasor3:
    return as_phasor3(a + b for a, b in zip(left, right, strict=True))


def subtract(left: Sequence[complex], right: Sequence[complex]) -> Phasor3:
    return as_phasor3(a - b for a, b in zip(left, right, strict=True))


def scale(vector: Sequence[complex], factor: complex) -> Phasor3:
    return as_phasor3(factor * value for value in vector)


def rotate_channels(vector: Sequence[complex], offset: int) -> Phasor3:
    source = as_phasor3(vector)
    result = [0j, 0j, 0j]
    for index, value in enumerate(source):
        result[(index + offset) % 3] = value
    return as_phasor3(result)


def positive_sequence(reference: complex = 1 + 0j) -> Phasor3:
    """Return an ABC positive-sequence set A=0, B=-120, C=+120 degrees."""

    return (
        reference,
        reference * cmath.rect(1.0, math.radians(-120.0)),
        reference * cmath.rect(1.0, math.radians(120.0)),
    )


def phase_loop(first: int, second: int) -> Phasor3:
    result = [0j, 0j, 0j]
    result[first % 3] = 1 + 0j
    result[second % 3] = -1 + 0j
    return as_phasor3(result)


def is_zero_sum(vector: Sequence[complex], tolerance: float = 1e-9) -> bool:
    return abs(sum(as_phasor3(vector))) <= tolerance


def inverse_compensation(
    number: int,
    target: Sequence[complex],
    *,
    solution: Literal["minimum_norm", "sparse"] = "minimum_norm",
    tolerance: float = 1e-9,
) -> Phasor3:
    """Solve ``M(number) * raw = target``.

    Nonzero matrices reject zero sequence and are singular.  Their transpose is
    the Moore-Penrose inverse on the zero-sum subspace.  ``sparse`` adds an
    unobservable common-mode current and chooses the candidate with the most
    zero physical channels; this reproduces single-terminal test-set methods.
    """

    target3 = as_phasor3(target)
    if number == 0:
        return target3
    if not is_zero_sum(target3, tolerance):
        raise MatrixDomainError(
            f"Matrix {number} removes zero sequence; target sum is {sum(target3)!r}"
        )

    minimum = apply_matrix(transpose(compensation_matrix(number)), target3)
    if solution == "minimum_norm":
        result = minimum
    elif solution == "sparse":
        candidates: list[Phasor3] = [minimum]
        # All [z,z,z] vectors lie in the null space.  Setting z=-raw[k]
        # enumerates every candidate that zeros at least one channel.
        for value in minimum:
            candidates.append(as_phasor3(item - value for item in minimum))

        def score(candidate: Phasor3) -> tuple[int, float]:
            nonzero = sum(abs(item) > tolerance for item in candidate)
            norm_sq = sum(abs(item) ** 2 for item in candidate)
            return nonzero, norm_sq

        result = min(candidates, key=score)
    else:
        raise ValueError(f"unknown inverse solution: {solution}")

    residual = subtract(apply_matrix(compensation_matrix(number), result), target3)
    if max(abs(value) for value in residual) > tolerance * 20:
        raise MatrixDomainError(f"inverse residual too large for Matrix {number}: {residual}")
    return result


def phasor_to_polar(value: complex, digits: int = 9) -> tuple[float, float]:
    amplitude = abs(value)
    angle = 0.0 if amplitude <= 10 ** (-digits) else math.degrees(cmath.phase(value))
    return round(amplitude, digits), round(angle, digits)
