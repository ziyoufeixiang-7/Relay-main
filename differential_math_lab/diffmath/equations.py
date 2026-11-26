"""Forward and inverse scalar differential-current equations.

The string equations mirror the existing front-end enum names.  They are kept
literal here because restraint-current definitions are relay-manufacturer
specific; this module does not silently substitute one definition for another.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


class EquationDomainError(ValueError):
    """The requested (Id, Ir) point has no canonical real-current solution."""


@dataclass(frozen=True)
class EquationConfig:
    id_equation: str = "id_sum"
    ir_equation: str = "ir_diff_k"
    k_factor: float = 2.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.k_factor) or self.k_factor <= 0:
            raise ValueError("k_factor must be positive")

    @classmethod
    def abb_615(cls) -> "EquationConfig":
        """ABB 615 example: ``Id=|I1+I2|``, ``Ir=|I1-I2|/2``."""

        return cls("id_sum", "ir_diff_k", 2.0)

    @classmethod
    def scaled_magnitude_sum(cls, scale_factor: float = 0.5) -> "EquationConfig":
        """SEL-style example ``Ir=k(|I1|+|I2|)``.

        The front-end enum represents the scale as a divisor, hence
        ``k_factor = 1 / scale_factor`` in this explicit adapter.
        """

        if scale_factor <= 0:
            raise ValueError("restraint scale factor must be positive")
        return cls("id_sum", "ir_sum_div_k", 1.0 / scale_factor)

    @classmethod
    def maximum_magnitude(cls) -> "EquationConfig":
        """Example ``Id=|I1+I2|``, ``Ir=max(|I1|,|I2|)``."""

        return cls("id_sum", "ir_max_k", 1.0)


@dataclass(frozen=True)
class ScalarSolution:
    winding1: complex
    winding2: complex
    target_id: float
    target_ir: float
    actual_id: float
    actual_ir: float


def _phase_cosine(i1: complex, i2: complex) -> float:
    product = abs(i1) * abs(i2)
    if product == 0:
        return 0.0
    value = (i1 * i2.conjugate()).real / product
    return max(-1.0, min(1.0, value))


def evaluate_pair(i1: complex, i2: complex, config: EquationConfig) -> tuple[float, float]:
    """Calculate exactly the selected Id and Ir for one compensated element."""

    id_eq = config.id_equation
    ir_eq = config.ir_equation
    k = config.k_factor

    if id_eq == "id_i1":
        id_value = abs(i1)
    elif id_eq == "id_sum":
        id_value = abs(i1 + i2)
    elif id_eq == "id_diff":
        id_value = abs(i1 - i2)
    elif id_eq == "id_sum_sq":
        id_value = abs(i1 + i2) ** 2
    elif id_eq == "id_diff_sq":
        id_value = abs(i1 - i2) ** 2
    else:
        raise ValueError(f"unsupported id equation: {id_eq}")

    a = abs(i1)
    b = abs(i2)
    cosine = _phase_cosine(i1, i2)

    if ir_eq == "ir_i2":
        ir_value = b
    elif ir_eq == "ir_diff_k":
        ir_value = abs(i1 - i2) / k
    elif ir_eq == "ir_sum_k":
        ir_value = abs(i1 + i2) / k
    elif ir_eq == "ir_max_k":
        ir_value = max(a, b) * k
    elif ir_eq == "ir_i2_k":
        ir_value = b * k
    elif ir_eq == "ir_id_abs_diff":
        ir_value = abs(id_value - a - b)
    elif ir_eq == "ir_sum_div_k":
        ir_value = (a + b) / k
    elif ir_eq == "ir_imax_sum_k":
        # With exactly two windings, |Imax - sum(other magnitudes)| / K.
        # A multiwinding relay needs all restraint inputs and is outside 6I.
        ir_value = abs(a - b) / k
    elif ir_eq == "ir_sqrt_cos":
        radicand = -(a * b) * cosine
        if radicand < -1e-10:
            raise EquationDomainError(
                "ir_sqrt_cos is not real for the selected relative phase"
            )
        ir_value = math.sqrt(max(0.0, radicand))
    elif ir_eq == "ir_sum_sq_cos":
        ir_value = -(a * b) * cosine
    elif ir_eq == "ir_diff_sq_cos":
        ir_value = (a * b) * cosine
    else:
        raise ValueError(f"unsupported ir equation: {ir_eq}")
    if ir_value < -1e-10:
        raise EquationDomainError(
            f"selected restraint formula is negative at this relative phase: {ir_value}"
        )
    return id_value, max(0.0, ir_value)


def _check_nonnegative(id_value: float, ir_value: float) -> None:
    if not math.isfinite(id_value) or not math.isfinite(ir_value):
        raise EquationDomainError("Id and Ir targets must be finite")
    if id_value < 0 or ir_value < 0:
        raise EquationDomainError("Id and Ir targets must be nonnegative")


def _sum_solution(difference: float, total: float) -> tuple[float, float]:
    if total + 1e-10 < difference:
        raise EquationDomainError(
            f"requires winding-current sum >= difference ({total} < {difference})"
        )
    return (total + difference) / 2.0, max(0.0, (total - difference) / 2.0)


def _product_solution(difference: float, product: float) -> tuple[float, float]:
    if product < 0:
        raise EquationDomainError("required winding-current product is negative")
    b = (-difference + math.sqrt(difference * difference + 4.0 * product)) / 2.0
    return b + difference, b


def _sum_product_solution(total: float, product: float) -> tuple[float, float]:
    if product < 0:
        raise EquationDomainError("required winding-current product is negative")
    discriminant = total * total - 4.0 * product
    if discriminant < -1e-10:
        raise EquationDomainError(
            f"requires total^2 >= 4*product ({total * total} < {4 * product})"
        )
    root = math.sqrt(max(0.0, discriminant))
    return (total + root) / 2.0, (total - root) / 2.0


def _require_close(actual: float, target: float, label: str) -> None:
    tolerance = 1e-8 * max(1.0, abs(target))
    if not math.isclose(actual, target, rel_tol=1e-8, abs_tol=tolerance):
        raise EquationDomainError(f"{label} is constrained to {actual}, not requested {target}")


def solve_scalar(id_value: float, ir_value: float, config: EquationConfig) -> ScalarSolution:
    """Solve one canonical two-winding test point, then forward-check it.

    The solver first uses the usual through-current branch, then changes the
    relative polarity when that is required to cover the other valid part of
    an equation's domain.  Every result is passed back through
    :func:`evaluate_pair`; constrained or impossible combinations are rejected
    instead of silently falling back to a different formula.
    """

    _check_nonnegative(id_value, ir_value)
    id_eq = config.id_equation
    ir_eq = config.ir_equation
    k = config.k_factor

    if id_eq == "id_i1":
        if ir_eq != "ir_i2":
            raise EquationDomainError("id_i1 is supported only with ir_i2")
        a, b, sign = id_value, ir_value, -1.0
    else:
        is_square = id_eq in ("id_sum_sq", "id_diff_sq")
        if id_eq not in ("id_sum", "id_diff", "id_sum_sq", "id_diff_sq"):
            raise ValueError(f"unsupported id equation: {id_eq}")
        difference = math.sqrt(id_value) if is_square else id_value
        sign = -1.0 if id_eq in ("id_sum", "id_sum_sq") else 1.0

        if is_square and ir_eq not in ("ir_sum_sq_cos", "ir_diff_sq_cos"):
            raise EquationDomainError(
                "squared Id equations require their matching squared-cosine Ir equation"
            )
        if not is_square and ir_eq in ("ir_sum_sq_cos", "ir_diff_sq_cos"):
            raise EquationDomainError(
                "squared-cosine Ir equations require a squared Id equation"
            )

        if ir_eq == "ir_diff_k":
            if id_eq == "id_sum":
                restraint_vector = k * ir_value
                if difference <= restraint_vector:
                    a, b = _sum_solution(difference, restraint_vector)
                else:
                    # Same-angle branch: Id=a+b, K*Ir=a-b.
                    a, b = _sum_solution(restraint_vector, difference)
                    sign = 1.0
            else:
                # Id and K*Ir are the same vector difference for id_diff.
                _require_close(difference / k, ir_value, "Ir")
                a, b = difference, 0.0
        elif ir_eq == "ir_sum_k":
            if id_eq == "id_diff":
                restraint_vector = k * ir_value
                if difference <= restraint_vector:
                    a, b = _sum_solution(difference, restraint_vector)
                else:
                    # Opposed branch: Id=a+b, K*Ir=a-b.
                    a, b = _sum_solution(restraint_vector, difference)
                    sign = -1.0
            else:
                # Id and K*Ir are the same vector sum for id_sum.
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
                raise EquationDomainError("ir_max_k requires Id <= 2*Ir/K")
        elif ir_eq == "ir_i2_k":
            b = ir_value / k
            a = b + difference
        elif ir_eq == "ir_id_abs_diff":
            b = ir_value / 2.0
            a = b + difference
        elif ir_eq == "ir_imax_sum_k":
            magnitude_difference = k * ir_value
            if difference + 1e-10 < magnitude_difference:
                raise EquationDomainError("two-winding ir_imax_sum_k requires Id >= K*Ir")
            a, b = _sum_solution(magnitude_difference, difference)
            # Choose the branch in which Id is the magnitude sum.  At equality
            # b=0 and either relative polarity is equivalent.
            sign *= -1.0
        elif ir_eq == "ir_sqrt_cos":
            if id_eq == "id_sum":
                sign = -1.0
                a, b = _product_solution(difference, ir_value**2)
            else:
                sign = -1.0
                a, b = _sum_product_solution(difference, ir_value**2)
        elif ir_eq == "ir_sum_sq_cos":
            if not is_square or sign > 0:
                raise EquationDomainError("ir_sum_sq_cos requires id_sum_sq")
            a, b = _product_solution(difference, ir_value)
        elif ir_eq == "ir_diff_sq_cos":
            if not is_square or sign < 0:
                raise EquationDomainError("ir_diff_sq_cos requires id_diff_sq")
            a, b = _product_solution(difference, ir_value)
        else:
            raise EquationDomainError(
                f"unsupported equation pair: {id_eq} + {ir_eq}"
            )

    i1 = complex(a, 0.0)
    i2 = complex(sign * b, 0.0)
    actual_id, actual_ir = evaluate_pair(i1, i2, config)
    _require_close(actual_id, id_value, "Id")
    _require_close(actual_ir, ir_value, "Ir")
    return ScalarSolution(i1, i2, id_value, ir_value, actual_id, actual_ir)


def scale_api_targets(
    id_value: float,
    ir_value: float,
    config: EquationConfig,
    *,
    ie: float,
    is_per_unit: bool,
) -> tuple[float, float]:
    """Convert API coordinates to physical equation units.

    Linear current coordinates scale with ``Ie``.  Squared coordinates scale
    with ``Ie**2``.  No RMS/peak conversion belongs in this operation.
    """

    if not is_per_unit:
        return id_value, ir_value
    if ie <= 0:
        raise ValueError("Ie must be positive in per-unit mode")
    id_factor = ie**2 if config.id_equation.endswith("_sq") else ie
    ir_factor = ie**2 if config.ir_equation in (
        "ir_sum_sq_cos",
        "ir_diff_sq_cos",
    ) else ie
    return id_value * id_factor, ir_value * ir_factor
