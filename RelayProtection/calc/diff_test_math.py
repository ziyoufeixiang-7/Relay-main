"""Mathematical adapter for the differential-test API.

This module converts the existing front-end differential-test settings into
physical test-set currents.  It deliberately contains no EngineCtrl or FPGA
code.  All current phasors use the same amplitude convention as the
differential API and ``params.statics``; there is no RMS/peak conversion here.

The relay-side forward model of each 6I winding is::

    J = Kp * M * X

where ``J`` is the relay's internal three-element current and ``X`` is the
three-current test-set output.  For M1..M12 the inverse is underdetermined.
Balanced ABC quantities use the Moore-Penrose/minimum-norm solution
``X = M.T * J / Kp`` (common mode c = 0).  A/B/C and their corresponding
phase-loop tests use the established ONLLY sparse representative when the
matrix has a null space; this preserves the field-validated single-terminal
6I injection convention without changing the relay-side result.
"""

from __future__ import annotations

import cmath
from dataclasses import dataclass
import math
from typing import Any, Dict, Sequence


_EPS = 1e-12
_VERIFY_TOL = 1e-8
_ZERO3 = (0j, 0j, 0j)


class DifferentialMathError(ValueError):
    """The requested API point or differential configuration is invalid."""


@dataclass(frozen=True)
class EquationConfig:
    id_equation: str
    ir_equation: str
    k_factor: float


@dataclass(frozen=True)
class ScalarSolution:
    """One relay element's compensated-domain currents."""

    winding1: complex
    winding2: complex
    target_id: float
    target_ir: float
    actual_id: float
    actual_ir: float


@dataclass(frozen=True)
class CompensationProfile:
    matrix1: int
    matrix2: int
    kp1: float
    kp2: float


@dataclass(frozen=True)
class SixCurrentSolution:
    """6I logical-side currents before terminal/channel mapping."""

    prefault1: tuple[complex, complex, complex]
    prefault2: tuple[complex, complex, complex]
    fault1: tuple[complex, complex, complex]
    fault2: tuple[complex, complex, complex]
    scalar: ScalarSolution
    profile: CompensationProfile


@dataclass(frozen=True)
class TwoCurrentSolution:
    """2I logical terminal-total currents before parallel-source sharing."""

    prefault1: complex
    prefault2: complex
    fault1: complex
    fault2: complex
    scalar: ScalarSolution


@dataclass(frozen=True)
class SixHarmonicSolution:
    fundamental1: tuple[complex, complex, complex]
    fundamental2: tuple[complex, complex, complex]
    harmonic1: tuple[complex, complex, complex]
    harmonic2: tuple[complex, complex, complex]
    profile: CompensationProfile


@dataclass(frozen=True)
class TwoHarmonicSolution:
    fundamental1: complex
    fundamental2: complex
    harmonic1: complex
    harmonic2: complex


_ONE_THIRD = 0.3333333333333333
_TWO_THIRDS = 0.6666666666666666
_SQRT3_THIRDS = 0.5773502691896257


# M0 is the identity.  M12 is the separate 0-degree zero-sequence-removing
# projection; it must never be reduced to M0 with ``% 12``.
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


# Canonical relay-domain targets.  A single-phase element and its corresponding
# phase-to-phase element intentionally share a target.  The 6I generator may
# rotate these three canonical phase labels together to reproduce the
# field-validated ONLLY terminal convention; Id/Ir magnitudes are unchanged.
PHASE_TARGETS = {
    "A": (1 + 0j, -1 + 0j, 0j),
    "AB": (1 + 0j, -1 + 0j, 0j),
    "B": (0j, 1 + 0j, -1 + 0j),
    "BC": (0j, 1 + 0j, -1 + 0j),
    "C": (-1 + 0j, 0j, 1 + 0j),
    "CA": (-1 + 0j, 0j, 1 + 0j),
    "ABC": (
        1 + 0j,
        cmath.rect(1.0, math.radians(-120.0)),
        cmath.rect(1.0, math.radians(120.0)),
    ),
}

_SINGLE_PHASE_UNIT_TARGETS = {
    "A": (1 + 0j, 0j, 0j),
    "B": (0j, 1 + 0j, 0j),
    "C": (0j, 0j, 1 + 0j),
}


def _number(value: Any, name: str, *, positive: bool = False,
            nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise DifferentialMathError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DifferentialMathError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise DifferentialMathError(f"{name} must be a finite number")
    if positive and result <= 0.0:
        raise DifferentialMathError(f"{name} must be greater than zero")
    if nonnegative and result < 0.0:
        raise DifferentialMathError(f"{name} must not be negative")
    return result


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off", ""):
            return False
    raise DifferentialMathError("boolean setting has an invalid value")


def equation_from_payload(payload: Dict[str, Any]) -> EquationConfig:
    settings = payload.get("equationSettings", {})
    if not isinstance(settings, dict):
        raise DifferentialMathError("equationSettings must be an object")
    config = EquationConfig(
        str(settings.get("idEquation", "id_sum")),
        str(settings.get("irEquation", "ir_diff_k")),
        _number(settings.get("kFactor", 2.0), "kFactor", positive=True),
    )
    # Validate the enum names before a hardware point is built.
    if config.id_equation not in {
        "id_i1", "id_sum", "id_diff", "id_sum_sq", "id_diff_sq"
    }:
        raise DifferentialMathError(f"unsupported idEquation: {config.id_equation}")
    if config.ir_equation not in {
        "ir_i2", "ir_diff_k", "ir_sum_k", "ir_max_k", "ir_i2_k",
        "ir_id_abs_diff", "ir_sum_div_k", "ir_imax_sum_k",
        "ir_sqrt_cos", "ir_sum_sq_cos", "ir_diff_sq_cos",
    }:
        raise DifferentialMathError(f"unsupported irEquation: {config.ir_equation}")
    return config


def scale_api_targets(id_value: Any, ir_value: Any,
                      config: EquationConfig,
                      payload: Dict[str, Any]) -> tuple[float, float]:
    """Scale pu coordinates while preserving squared-current dimensions."""

    target_id = _number(id_value, "Id", nonnegative=True)
    target_ir = _number(ir_value, "Ir", nonnegative=True)
    settings = payload.get("equationSettings", {})
    is_per_unit = _boolean(settings.get("isPerUnit", False))
    if not is_per_unit:
        return target_id, target_ir
    ie = _number(settings.get("Ie", 1.0), "Ie", positive=True)
    id_scale = ie * ie if config.id_equation.endswith("_sq") else ie
    ir_scale = ie * ie if config.ir_equation in {
        "ir_sum_sq_cos", "ir_diff_sq_cos"
    } else ie
    return target_id * id_scale, target_ir * ir_scale


def _phase_cosine(i1: complex, i2: complex) -> float:
    product = abs(i1) * abs(i2)
    if product == 0.0:
        return 0.0
    return max(-1.0, min(1.0, (i1 * i2.conjugate()).real / product))


def evaluate_pair(i1: complex, i2: complex,
                  config: EquationConfig) -> tuple[float, float]:
    """Forward-calculate exactly the selected Id/Ir enum pair."""

    if config.id_equation == "id_i1":
        id_value = abs(i1)
    elif config.id_equation == "id_sum":
        id_value = abs(i1 + i2)
    elif config.id_equation == "id_diff":
        id_value = abs(i1 - i2)
    elif config.id_equation == "id_sum_sq":
        id_value = abs(i1 + i2) ** 2
    elif config.id_equation == "id_diff_sq":
        id_value = abs(i1 - i2) ** 2
    else:  # Guarded by equation_from_payload; retained for direct callers.
        raise DifferentialMathError(f"unsupported idEquation: {config.id_equation}")

    a, b = abs(i1), abs(i2)
    cosine = _phase_cosine(i1, i2)
    k = config.k_factor
    if config.ir_equation == "ir_i2":
        ir_value = b
    elif config.ir_equation == "ir_diff_k":
        ir_value = abs(i1 - i2) / k
    elif config.ir_equation == "ir_sum_k":
        ir_value = abs(i1 + i2) / k
    elif config.ir_equation == "ir_max_k":
        ir_value = max(a, b) * k
    elif config.ir_equation == "ir_i2_k":
        ir_value = b * k
    elif config.ir_equation == "ir_id_abs_diff":
        ir_value = abs(id_value - a - b)
    elif config.ir_equation == "ir_sum_div_k":
        ir_value = (a + b) / k
    elif config.ir_equation == "ir_imax_sum_k":
        # Literal two-winding reduction of |Imax-sum(other magnitudes)|/K.
        ir_value = abs(a - b) / k
    elif config.ir_equation == "ir_sqrt_cos":
        radicand = -(a * b) * cosine
        if radicand < -_VERIFY_TOL:
            raise DifferentialMathError("ir_sqrt_cos has a negative radicand")
        ir_value = math.sqrt(max(0.0, radicand))
    elif config.ir_equation == "ir_sum_sq_cos":
        ir_value = -(a * b) * cosine
    elif config.ir_equation == "ir_diff_sq_cos":
        ir_value = (a * b) * cosine
    else:
        raise DifferentialMathError(f"unsupported irEquation: {config.ir_equation}")
    if ir_value < -_VERIFY_TOL:
        raise DifferentialMathError("selected restraint equation is negative")
    return id_value, max(0.0, ir_value)


def _require_close(actual: float, target: float, label: str) -> None:
    tolerance = _VERIFY_TOL * max(1.0, abs(target))
    if not math.isclose(actual, target, rel_tol=_VERIFY_TOL, abs_tol=tolerance):
        raise DifferentialMathError(
            f"{label} is constrained to {actual:.12g}, not {target:.12g}"
        )


def _sum_solution(difference: float, total: float) -> tuple[float, float]:
    if total + _VERIFY_TOL < difference:
        raise DifferentialMathError(
            f"current-magnitude sum {total:.12g} is below difference "
            f"{difference:.12g}"
        )
    return (total + difference) / 2.0, max(0.0, (total - difference) / 2.0)


def _product_solution(difference: float, product: float) -> tuple[float, float]:
    if product < 0.0:
        raise DifferentialMathError("required current product is negative")
    b = (-difference + math.sqrt(difference * difference + 4.0 * product)) / 2.0
    return b + difference, b


def _sum_product_solution(total: float, product: float) -> tuple[float, float]:
    discriminant = total * total - 4.0 * product
    if product < 0.0 or discriminant < -_VERIFY_TOL:
        raise DifferentialMathError("requested current sum/product has no real solution")
    root = math.sqrt(max(0.0, discriminant))
    return (total + root) / 2.0, (total - root) / 2.0


def solve_scalar(id_value: float, ir_value: float,
                 config: EquationConfig) -> ScalarSolution:
    """Solve one canonical compensated-domain point and forward-check it."""

    id_value = _number(id_value, "Id", nonnegative=True)
    ir_value = _number(ir_value, "Ir", nonnegative=True)
    id_eq, ir_eq, k = (
        config.id_equation, config.ir_equation, config.k_factor
    )

    if id_eq == "id_i1":
        if ir_eq != "ir_i2":
            raise DifferentialMathError("id_i1 is supported only with ir_i2")
        a, b, sign = id_value, ir_value, -1.0
    else:
        is_square = id_eq in {"id_sum_sq", "id_diff_sq"}
        difference = math.sqrt(id_value) if is_square else id_value
        sign = -1.0 if id_eq in {"id_sum", "id_sum_sq"} else 1.0

        if is_square and ir_eq not in {"ir_sum_sq_cos", "ir_diff_sq_cos"}:
            raise DifferentialMathError(
                "squared Id requires the matching squared-cosine Ir equation"
            )
        if not is_square and ir_eq in {"ir_sum_sq_cos", "ir_diff_sq_cos"}:
            raise DifferentialMathError(
                "squared-cosine Ir requires a squared Id equation"
            )

        if ir_eq == "ir_diff_k":
            if id_eq == "id_sum":
                restraint = k * ir_value
                if difference <= restraint:
                    a, b = _sum_solution(difference, restraint)
                else:
                    a, b = _sum_solution(restraint, difference)
                    sign = 1.0
            else:
                _require_close(difference / k, ir_value, "Ir")
                a, b = difference, 0.0
        elif ir_eq == "ir_sum_k":
            if id_eq == "id_diff":
                restraint = k * ir_value
                if difference <= restraint:
                    a, b = _sum_solution(difference, restraint)
                else:
                    a, b = _sum_solution(restraint, difference)
                    sign = -1.0
            else:
                _require_close(difference / k, ir_value, "Ir")
                a, b = difference, 0.0
        elif ir_eq == "ir_sum_div_k":
            a, b = _sum_solution(difference, k * ir_value)
        elif ir_eq == "ir_max_k":
            maximum = ir_value / k
            if difference <= maximum:
                a, b = maximum, maximum - difference
            elif difference <= 2.0 * maximum:
                a, b = maximum, difference - maximum
                sign *= -1.0
            else:
                raise DifferentialMathError("ir_max_k requires Id <= 2*Ir/K")
        elif ir_eq == "ir_i2_k":
            b = ir_value / k
            a = b + difference
        elif ir_eq == "ir_id_abs_diff":
            # K is intentionally absent from this literal equation.
            b = ir_value / 2.0
            a = b + difference
        elif ir_eq == "ir_imax_sum_k":
            magnitude_difference = k * ir_value
            if difference + _VERIFY_TOL < magnitude_difference:
                raise DifferentialMathError(
                    "two-winding ir_imax_sum_k requires Id >= K*Ir"
                )
            a, b = _sum_solution(magnitude_difference, difference)
            sign *= -1.0
        elif ir_eq == "ir_sqrt_cos":
            sign = -1.0
            if id_eq == "id_sum":
                a, b = _product_solution(difference, ir_value * ir_value)
            else:
                a, b = _sum_product_solution(difference, ir_value * ir_value)
        elif ir_eq == "ir_sum_sq_cos":
            if not is_square or sign > 0.0:
                raise DifferentialMathError("ir_sum_sq_cos requires id_sum_sq")
            a, b = _product_solution(difference, ir_value)
        elif ir_eq == "ir_diff_sq_cos":
            if not is_square or sign < 0.0:
                raise DifferentialMathError("ir_diff_sq_cos requires id_diff_sq")
            a, b = _product_solution(difference, ir_value)
        else:
            raise DifferentialMathError(f"unsupported equation pair: {id_eq} + {ir_eq}")

    i1, i2 = complex(a, 0.0), complex(sign * b, 0.0)
    actual_id, actual_ir = evaluate_pair(i1, i2, config)
    _require_close(actual_id, id_value, "Id")
    _require_close(actual_ir, ir_value, "Ir")
    return ScalarSolution(
        i1, i2, id_value, ir_value, actual_id, actual_ir
    )


def phase_target(test_phase: Any) -> tuple[complex, complex, complex]:
    key = str(test_phase).upper()
    try:
        return PHASE_TARGETS[key]
    except KeyError as exc:
        raise DifferentialMathError(f"unsupported testPhase: {test_phase}") from exc


def _integer_clock(value: Any, name: str) -> int:
    number = _number(value, name)
    integer = int(number)
    if integer != number:
        raise DifferentialMathError(f"{name} must be an integer clock number")
    return integer % 12


def _nonzero_matrix(number: int) -> int:
    reduced = number % 12
    return 12 if reduced == 0 else reduced


def profile_from_payload(payload: Dict[str, Any]) -> CompensationProfile:
    """Adapt the existing UI correction settings to explicit M numbers.

    The current protocol does not carry actual relay matrix numbers, so this
    is necessarily the project's legacy compatibility mapping.  It preserves
    the already validated Yd11/y-side -> M1/M0 and Yy12/y-side -> M11/M11
    cases.  A future protocol should send explicit DUT profile matrix numbers.
    """

    prot = payload.get("protectionSettings", {})
    eq = payload.get("equationSettings", {})
    if not isinstance(prot, dict) or not isinstance(eq, dict):
        raise DifferentialMathError(
            "protectionSettings and equationSettings must be objects"
        )
    letter1 = str(prot.get("vectorGroupLetter1", "y")).lower()
    letter2 = str(prot.get("vectorGroupLetter2", "y")).lower()
    if letter1 not in {"y", "d"} or letter2 not in {"y", "d"}:
        raise DifferentialMathError("vector-group letters must be y or d")

    if "vectorGroupHighSideClock" in prot:
        high_clock = _integer_clock(
            prot.get("vectorGroupHighSideClock", 0),
            "vectorGroupHighSideClock",
        )
        low_clock = _integer_clock(
            prot.get("vectorGroupLowSideClock", 0),
            "vectorGroupLowSideClock",
        )
    else:
        high_clock = 0
        low_clock = _integer_clock(
            prot.get("vectorGroupClock", 0), "vectorGroupClock"
        )

    correction = str(prot.get("phaseCorrection", "none"))
    if correction not in {"none", "y-side", "delta-side"}:
        raise DifferentialMathError(f"unsupported phaseCorrection: {correction}")
    selected = {"none": None, "y-side": "y", "delta-side": "d"}[correction]
    relative = (low_clock - high_clock) % 12
    matched1 = selected is not None and letter1 == selected
    matched2 = selected is not None and letter2 == selected
    matrix1 = matrix2 = 0
    if matched1 and matched2:
        matrix1 = 11
        matrix2 = _nonzero_matrix(11 + relative)
    elif matched1:
        matrix1 = _nonzero_matrix(-relative) if relative else 12
    elif matched2:
        matrix2 = _nonzero_matrix(relative) if relative else 12

    if _boolean(prot.get("zeroSequenceCorrection", False)):
        if letter1 == "y" and matrix1 == 0:
            matrix1 = 12
        if letter2 == "y" and matrix2 == 0:
            matrix2 = 12

    return CompensationProfile(
        matrix1,
        matrix2,
        _number(eq.get("kp1", 1.0), "kp1", positive=True),
        _number(eq.get("kp2", 1.0), "kp2", positive=True),
    )


def _multiply(matrix: Sequence[Sequence[float]],
              current: Sequence[complex], *, transpose: bool = False
              ) -> tuple[complex, complex, complex]:
    return tuple(  # type: ignore[return-value]
        sum(
            (matrix[j][i] if transpose else matrix[i][j]) * current[j]
            for j in range(3)
        )
        for i in range(3)
    )


def _finite_vector(current: Sequence[complex]) -> bool:
    return all(math.isfinite(value.real) and math.isfinite(value.imag)
               for value in current)


def _inverse_minimum_norm(matrix_number: int, kp: float,
                          target: Sequence[complex]
                          ) -> tuple[complex, complex, complex]:
    """Solve J=Kp*M*X, always choosing c=0 for a singular matrix."""

    if matrix_number == 0:
        result = tuple(value / kp for value in target)
    else:
        re_scale = max(1.0, *(abs(value.real) for value in target))
        im_scale = max(1.0, *(abs(value.imag) for value in target))
        if (
            abs(sum(value.real for value in target)) > _EPS * re_scale
            or abs(sum(value.imag for value in target)) > _EPS * im_scale
        ):
            raise DifferentialMathError(
                f"target is unreachable through zero-sequence-removing M{matrix_number}"
            )
        # M+ = M.T for this frozen matrix family.  This is the c=0,
        # Moore-Penrose/minimum-L2-norm solution requested by the experiment.
        result = tuple(
            value / kp
            for value in _multiply(MATRIX_TABLE[matrix_number], target, transpose=True)
        )
    if not _finite_vector(result):
        raise DifferentialMathError("matrix inverse produced a non-finite current")
    calculated = tuple(
        kp * value
        for value in _multiply(MATRIX_TABLE[matrix_number], result)
    )
    scale = max(1.0, *(abs(value) for value in target))
    if max(abs(a - b) for a, b in zip(calculated, target)) > _VERIFY_TOL * scale:
        raise DifferentialMathError(f"M{matrix_number} inverse forward check failed")
    return result  # type: ignore[return-value]


def _inverse_sparse(matrix_number: int, kp: float,
                    target: Sequence[complex]
                    ) -> tuple[complex, complex, complex]:
    """Solve ``J=Kp*M*X`` using the sparsest exact common-mode member.

    M1..M12 remove ``[c, c, c]``.  Starting from the minimum-norm solution,
    subtracting any one of its three components from every channel therefore
    enumerates every solution which makes at least one physical output zero.
    The candidate with the fewest active outputs is selected; squared norm is
    only a deterministic tie-breaker.  M0 is nonsingular and remains unique.
    """

    minimum = _inverse_minimum_norm(matrix_number, kp, target)
    if matrix_number == 0:
        return minimum

    candidates = [minimum]
    for common in minimum:
        candidates.append(
            (
                minimum[0] - common,
                minimum[1] - common,
                minimum[2] - common,
            )
        )
    scale = max(1.0, *(abs(value) for value in minimum))
    zero_tolerance = _EPS * scale

    def score(current: Sequence[complex]) -> tuple[int, float]:
        active = sum(abs(value) > zero_tolerance for value in current)
        norm_squared = sum(abs(value) ** 2 for value in current)
        return active, norm_squared

    result = min(candidates, key=score)
    calculated = tuple(
        kp * value
        for value in _multiply(MATRIX_TABLE[matrix_number], result)
    )
    target_scale = max(1.0, *(abs(value) for value in target))
    if max(abs(a - b) for a, b in zip(calculated, target)) > (
        _VERIFY_TOL * target_scale
    ):
        raise DifferentialMathError(
            f"M{matrix_number} sparse inverse forward check failed"
        )
    return result  # type: ignore[return-value]


def _compensate(matrix_number: int, kp: float,
                current: Sequence[complex]) -> tuple[complex, complex, complex]:
    return tuple(  # type: ignore[return-value]
        kp * value for value in _multiply(MATRIX_TABLE[matrix_number], current)
    )


def _scale_vector(current: Sequence[complex], factor: complex
                  ) -> tuple[complex, complex, complex]:
    return tuple(value * factor for value in current)  # type: ignore[return-value]


def _add_vectors(left: Sequence[complex], right: Sequence[complex]
                 ) -> tuple[complex, complex, complex]:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


_ONLLY_PHASE_OFFSETS = {
    "A": 0,
    "AB": 0,
    "B": 1,
    "BC": 1,
    "C": 2,
    "CA": 2,
}


def _rotate_channels(current: Sequence[complex], offset: int
                     ) -> tuple[complex, complex, complex]:
    rotated = [0j, 0j, 0j]
    for index, value in enumerate(current):
        rotated[(index + offset) % 3] = value
    return tuple(rotated)  # type: ignore[return-value]


def _profile_phase_target(test_phase: Any,
                          profile: CompensationProfile
                          ) -> tuple[complex, complex, complex]:
    """Select the relay-domain phase pattern used by the 6I shot.

    The field-validated ONLLY single-phase convention anchors phase A to the
    first physical terminal of the first applicable odd compensation matrix.
    A and AB intentionally use that same algebraic loop, as do B/BC and C/CA.
    For example, the project's Yd11/y-side profile is M1/M0; M1 acting on a
    single physical A current produces ``(1, 0, -1)``.  Sparse inversion then
    gives one Y-side output and two delta-side outputs (A/C), exactly matching
    the established instrument connection.

    With no compensation matrix, A/B/C retain the original single-terminal
    injection.  Phase-to-phase tests retain their canonical phase loop, and
    ABC keeps the positive-sequence target and minimum-norm inverse.
    """

    key = str(test_phase).upper()
    canonical = phase_target(key)
    if key == "ABC":
        return canonical

    if (
        key in _SINGLE_PHASE_UNIT_TARGETS
        and profile.matrix1 == 0
        and profile.matrix2 == 0
    ):
        return _SINGLE_PHASE_UNIT_TARGETS[key]

    reference = next(
        (
            number
            for number in (profile.matrix1, profile.matrix2)
            if number != 0 and number % 2 == 1
        ),
        0,
    )
    if reference == 0:
        return canonical

    single_terminal = (math.sqrt(3.0) + 0j, 0j, 0j)
    basis = _multiply(MATRIX_TABLE[reference], single_terminal)
    peak = max(abs(value) for value in basis)
    if peak <= _EPS:
        raise DifferentialMathError(
            f"M{reference} cannot define the ONLLY phase reference"
        )
    normalized = tuple(value / peak for value in basis)
    return _rotate_channels(normalized, _ONLLY_PHASE_OFFSETS[key])


def _verify_six_point(profile: CompensationProfile,
                      config: EquationConfig,
                      current1: Sequence[complex],
                      current2: Sequence[complex],
                      basis: Sequence[complex],
                      target_id: float, target_ir: float) -> None:
    comp1 = _compensate(profile.matrix1, profile.kp1, current1)
    comp2 = _compensate(profile.matrix2, profile.kp2, current2)
    for index, basis_value in enumerate(basis):
        actual_id, actual_ir = evaluate_pair(comp1[index], comp2[index], config)
        multiplier = abs(basis_value)
        expected_id = target_id * (multiplier ** 2 if config.id_equation.endswith("_sq") else multiplier)
        expected_ir = target_ir * (
            multiplier ** 2
            if config.ir_equation in {"ir_sum_sq_cos", "ir_diff_sq_cos"}
            else multiplier
        )
        _require_close(actual_id, expected_id, f"phase {index} Id")
        _require_close(actual_ir, expected_ir, f"phase {index} Ir")


def _through_targets(config: EquationConfig, amplitude: float
                     ) -> tuple[tuple[complex, complex, complex],
                                tuple[complex, complex, complex]]:
    positive_sequence = _scale_vector(PHASE_TARGETS["ABC"], amplitude)
    if config.id_equation in {"id_sum", "id_sum_sq"}:
        return positive_sequence, _scale_vector(positive_sequence, -1.0)
    if config.id_equation in {"id_diff", "id_diff_sq"}:
        return positive_sequence, positive_sequence
    if amplitude == 0.0:
        return _ZERO3, _ZERO3
    raise DifferentialMathError("loadCurrent is not defined for id_i1")


def calculate_ratio_6i(payload: Dict[str, Any], *, ir_value: Any,
                       id_value: Any, test_phase: Any, diff_mode: int,
                       load_current: Any, simulate_prefault: bool
                       ) -> SixCurrentSolution:
    config = equation_from_payload(payload)
    target_id, target_ir = scale_api_targets(id_value, ir_value, config, payload)
    scalar = solve_scalar(target_id, target_ir, config)
    profile = profile_from_payload(payload)
    phase_key = str(test_phase).upper()
    basis = _profile_phase_target(phase_key, profile)
    target1 = _scale_vector(basis, scalar.winding1)
    target2 = _scale_vector(basis, scalar.winding2)
    inverse = _inverse_minimum_norm if phase_key == "ABC" else _inverse_sparse
    differential1 = inverse(profile.matrix1, profile.kp1, target1)
    differential2 = inverse(profile.matrix2, profile.kp2, target2)
    _verify_six_point(
        profile, config, differential1, differential2,
        basis, target_id, target_ir,
    )

    load = _number(load_current, "loadCurrent", nonnegative=True)
    if simulate_prefault and load > 0.0:
        load_target1, load_target2 = _through_targets(config, load)
        prefault1 = _inverse_minimum_norm(
            profile.matrix1, profile.kp1, load_target1
        )
        prefault2 = _inverse_minimum_norm(
            profile.matrix2, profile.kp2, load_target2
        )
    else:
        prefault1 = prefault2 = _ZERO3

    if diff_mode == 0:
        fault1, fault2 = differential1, differential2
    elif diff_mode == 2:
        fault1 = _add_vectors(prefault1, differential1)
        fault2 = _add_vectors(prefault2, differential2)
        # Forward-check the actual increment, not the total load-plus-fault.
        delta1 = tuple(a - b for a, b in zip(fault1, prefault1))
        delta2 = tuple(a - b for a, b in zip(fault2, prefault2))
        _verify_six_point(
            profile, config, delta1, delta2,
            basis, target_id, target_ir,
        )
    elif diff_mode == 1:
        raise DifferentialMathError(
            "diffMode=1 zero-sequence differential requires a separate residual-current model"
        )
    else:
        raise DifferentialMathError(f"unsupported diffMode: {diff_mode}")

    return SixCurrentSolution(
        prefault1, prefault2, fault1, fault2, scalar, profile
    )


def _two_current_factor(payload: Dict[str, Any], side: int) -> float:
    prot = payload.get("protectionSettings", {})
    eq = payload.get("equationSettings", {})
    if not isinstance(prot, dict) or not isinstance(eq, dict):
        raise DifferentialMathError(
            "protectionSettings and equationSettings must be objects"
        )
    letter = str(prot.get(f"vectorGroupLetter{side}", "y")).lower()
    if letter not in {"y", "d"}:
        raise DifferentialMathError(
            f"vectorGroupLetter{side} must be y or d"
        )
    correction = str(prot.get("phaseCorrection", "none"))
    selected = {"none": None, "y-side": "y", "delta-side": "d"}.get(correction)
    if selected is None and correction != "none":
        raise DifferentialMathError(f"unsupported phaseCorrection: {correction}")
    kp = _number(eq.get(f"kp{side}", 1.0), f"kp{side}", positive=True)
    # Legacy 2I compatibility: a matched compensation side has a 1/sqrt(3)
    # forward gain, therefore its physical terminal current is sqrt(3)*J/Kp.
    correction_factor = math.sqrt(3.0) if selected == letter else 1.0
    return correction_factor / kp


def calculate_ratio_2i(payload: Dict[str, Any], *, ir_value: Any,
                       id_value: Any, diff_mode: int, load_current: Any,
                       simulate_prefault: bool) -> TwoCurrentSolution:
    config = equation_from_payload(payload)
    target_id, target_ir = scale_api_targets(id_value, ir_value, config, payload)
    scalar = solve_scalar(target_id, target_ir, config)
    factor1, factor2 = _two_current_factor(payload, 1), _two_current_factor(payload, 2)
    differential1 = scalar.winding1 * factor1
    differential2 = scalar.winding2 * factor2

    load = _number(load_current, "loadCurrent", nonnegative=True)
    if simulate_prefault and load > 0.0:
        if config.id_equation in {"id_sum", "id_sum_sq"}:
            load1, load2 = complex(load, 0.0), complex(-load, 0.0)
        elif config.id_equation in {"id_diff", "id_diff_sq"}:
            load1 = load2 = complex(load, 0.0)
        else:
            raise DifferentialMathError("loadCurrent is not defined for id_i1")
        prefault1, prefault2 = load1 * factor1, load2 * factor2
    else:
        prefault1 = prefault2 = 0j

    if diff_mode == 0:
        fault1, fault2 = differential1, differential2
    elif diff_mode == 2:
        fault1, fault2 = prefault1 + differential1, prefault2 + differential2
    elif diff_mode == 1:
        raise DifferentialMathError(
            "diffMode=1 zero-sequence differential is not a 2I phase test"
        )
    else:
        raise DifferentialMathError(f"unsupported diffMode: {diff_mode}")
    return TwoCurrentSolution(
        prefault1, prefault2, fault1, fault2, scalar
    )


def _harmonic_amplitude(fundamental: float, value: Any,
                        error_type: str) -> float:
    harmonic_value = _number(value, "harmonic value", nonnegative=True)
    if error_type == "relative":
        return fundamental * harmonic_value / 100.0
    if error_type == "absolute":
        return harmonic_value
    raise DifferentialMathError(f"unsupported harmonicErrorType: {error_type}")


def _harmonic_values(payload: Dict[str, Any], fundamental_id: Any,
                     harmonic_value: Any) -> tuple[float, float, complex]:
    # Zero is valid for an absolute pure-harmonic point and harmless for a
    # relative point (both layers then remain zero).
    fundamental = _number(fundamental_id, "fundamental Id", nonnegative=True)
    eq = payload.get("equationSettings", {})
    if _boolean(eq.get("isPerUnit", False)):
        fundamental *= _number(eq.get("Ie", 1.0), "Ie", positive=True)
    project = payload.get("projectSettings", {})
    nested_harmonic = project.get("harmonic")
    harmonic = (
        nested_harmonic
        if isinstance(nested_harmonic, dict)
        else project
    )
    error_type = str(harmonic.get("harmonicErrorType", "relative"))
    amplitude = _harmonic_amplitude(fundamental, harmonic_value, error_type)
    angle = _number(harmonic.get("angle", 0.0), "harmonic angle")
    return fundamental, amplitude, cmath.rect(1.0, math.radians(angle))


def calculate_harmonic_6i(payload: Dict[str, Any], *, fundamental_id: Any,
                          harmonic_value: Any, test_phase: Any,
                          apply_side: str) -> SixHarmonicSolution:
    fundamental, harmonic, rotation = _harmonic_values(
        payload, fundamental_id, harmonic_value
    )
    if apply_side not in {"i1", "i2"}:
        raise DifferentialMathError(f"unsupported harmonic applySide: {apply_side}")
    profile = profile_from_payload(payload)
    phase_key = str(test_phase).upper()
    basis = _profile_phase_target(phase_key, profile)
    inverse = _inverse_minimum_norm if phase_key == "ABC" else _inverse_sparse
    fundamental_target = _scale_vector(basis, fundamental)
    harmonic_target = _scale_vector(basis, harmonic * rotation)
    if apply_side == "i1":
        fundamental1 = inverse(
            profile.matrix1, profile.kp1, fundamental_target
        )
        harmonic1 = inverse(
            profile.matrix1, profile.kp1, harmonic_target
        )
        fundamental2 = harmonic2 = _ZERO3
    else:
        fundamental2 = inverse(
            profile.matrix2, profile.kp2, fundamental_target
        )
        harmonic2 = inverse(
            profile.matrix2, profile.kp2, harmonic_target
        )
        fundamental1 = harmonic1 = _ZERO3
    return SixHarmonicSolution(
        fundamental1, fundamental2, harmonic1, harmonic2, profile
    )


def calculate_harmonic_2i(payload: Dict[str, Any], *, fundamental_id: Any,
                          harmonic_value: Any,
                          apply_side: str) -> TwoHarmonicSolution:
    fundamental, harmonic, rotation = _harmonic_values(
        payload, fundamental_id, harmonic_value
    )
    if apply_side not in {"i1", "i2"}:
        raise DifferentialMathError(f"unsupported harmonic applySide: {apply_side}")
    if apply_side == "i1":
        factor = _two_current_factor(payload, 1)
        return TwoHarmonicSolution(
            fundamental * factor, 0j, harmonic * rotation * factor, 0j
        )
    factor = _two_current_factor(payload, 2)
    return TwoHarmonicSolution(
        0j, fundamental * factor, 0j, harmonic * rotation * factor
    )


__all__ = (
    "DifferentialMathError",
    "EquationConfig",
    "ScalarSolution",
    "CompensationProfile",
    "SixCurrentSolution",
    "TwoCurrentSolution",
    "SixHarmonicSolution",
    "TwoHarmonicSolution",
    "MATRIX_TABLE",
    "PHASE_TARGETS",
    "equation_from_payload",
    "evaluate_pair",
    "solve_scalar",
    "phase_target",
    "profile_from_payload",
    "calculate_ratio_6i",
    "calculate_ratio_2i",
    "calculate_harmonic_6i",
    "calculate_harmonic_2i",
)
