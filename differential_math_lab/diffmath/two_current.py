"""Two-current (phase differential) scalar model and terminal fan-out."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .equations import EquationConfig, ScalarSolution, evaluate_pair, solve_scalar
from .generator import distribute_parallel, recombine_parallel


@dataclass(frozen=True)
class ScalarWinding:
    """Scalar forward gain: internal current = gain * injected total."""

    gain: float
    polarity: int = 1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.gain, (int, float))
            or not math.isfinite(self.gain)
            or self.gain <= 0
        ):
            raise ValueError("gain must be positive")
        if (
            not isinstance(self.polarity, int)
            or isinstance(self.polarity, bool)
            or self.polarity not in (-1, 1)
        ):
            raise ValueError("polarity must be +1 or -1")


@dataclass(frozen=True)
class TwoCurrentModel:
    winding1: ScalarWinding
    winding2: ScalarWinding
    equations: EquationConfig

    def measure(self, injected1: complex, injected2: complex) -> tuple[float, float]:
        internal1 = self.winding1.polarity * self.winding1.gain * injected1
        internal2 = self.winding2.polarity * self.winding2.gain * injected2
        return evaluate_pair(internal1, internal2, self.equations)


@dataclass(frozen=True)
class TwoCurrentPoint:
    side1_total: complex
    side2_total: complex
    side1_outputs: tuple[complex, ...]
    side2_outputs: tuple[complex, ...]
    scalar: ScalarSolution
    actual_id: float
    actual_ir: float


def generate_2i_point(
    model: TwoCurrentModel,
    *,
    id_value: float,
    ir_value: float,
    side1_parallel_outputs: int = 1,
    side2_parallel_outputs: int = 1,
) -> TwoCurrentPoint:
    scalar = solve_scalar(id_value, ir_value, model.equations)
    total1 = scalar.winding1 / (model.winding1.polarity * model.winding1.gain)
    total2 = scalar.winding2 / (model.winding2.polarity * model.winding2.gain)
    outputs1 = distribute_parallel(total1, side1_parallel_outputs)
    outputs2 = distribute_parallel(total2, side2_parallel_outputs)
    recombined1 = recombine_parallel(outputs1)
    recombined2 = recombine_parallel(outputs2)
    actual_id, actual_ir = model.measure(recombined1, recombined2)
    if not math.isclose(actual_id, id_value, rel_tol=1e-8, abs_tol=1e-8):
        raise AssertionError("2I Id forward check failed")
    if not math.isclose(actual_ir, ir_value, rel_tol=1e-8, abs_tol=1e-8):
        raise AssertionError("2I Ir forward check failed")
    return TwoCurrentPoint(
        total1,
        total2,
        outputs1,
        outputs2,
        scalar,
        actual_id,
        actual_ir,
    )


def legacy_2i_model(
    *,
    letter1: str,
    letter2: str,
    phase_correction: str,
    kp1: float,
    kp2: float,
    equations: EquationConfig,
) -> TwoCurrentModel:
    """Reproduce the existing 2I sqrt(3) compensation as a forward gain.

    The existing inverse multiplies a matched side by sqrt(3), which means its
    implied relay forward gain is 1/sqrt(3).  This is an adapter, not a claim
    that every relay uses that gain for a given winding letter.
    """

    selected = {"none": None, "y-side": "y", "delta-side": "d"}.get(
        phase_correction
    )
    if phase_correction not in ("none", "y-side", "delta-side"):
        raise ValueError("unknown phase correction")
    gain1 = kp1 / math.sqrt(3) if selected == letter1.lower() else kp1
    gain2 = kp2 / math.sqrt(3) if selected == letter2.lower() else kp2
    return TwoCurrentModel(
        ScalarWinding(gain1),
        ScalarWinding(gain2),
        equations,
    )
