"""Inverse test-current generation followed by mandatory forward validation."""

from __future__ import annotations

from dataclasses import dataclass
import math
import cmath
from typing import Literal, Sequence, cast

from .equations import EquationConfig, ScalarSolution, solve_scalar
from .matrices import (
    Phasor3,
    ZERO3,
    add,
    apply_matrix,
    as_phasor3,
    compensation_matrix,
    phase_loop,
    positive_sequence,
    rotate_channels,
    scale,
)
from .model import DifferentialMeasurement, RelayModel, ResidualRelayModel, WindingModel

TEST_PHASES = ("A", "B", "C", "AB", "BC", "CA", "ABC")


@dataclass(frozen=True)
class GeneratedPoint:
    injected1: Phasor3
    injected2: Phasor3
    target_basis: Phasor3
    scalar: ScalarSolution
    measurement: DifferentialMeasurement
    test_phase: str


@dataclass(frozen=True)
class StatePair:
    prefault1: Phasor3
    prefault2: Phasor3
    fault1: Phasor3
    fault2: Phasor3
    steady_prefault: DifferentialMeasurement
    checked_quantity: DifferentialMeasurement
    mode: int


@dataclass(frozen=True)
class HarmonicPoint:
    order: int
    fundamental1: Phasor3
    fundamental2: Phasor3
    harmonic1: Phasor3
    harmonic2: Phasor3
    requested_content: float
    measured_content: tuple[float, float, float]
    apply_side: str
    error_type: str
    relative_angle_deg: float
    spatial_mode: str


@dataclass(frozen=True)
class ThroughCurrentPoint:
    injected1: Phasor3
    injected2: Phasor3
    measurement: DifferentialMeasurement
    compensated_current: Phasor3


def _matrix_single_phase_basis(matrix_number: int) -> Phasor3:
    """Logical basis produced by the established sparse A-terminal method."""

    if matrix_number == 0:
        return (1 + 0j, 0j, 0j)
    if matrix_number % 2 == 1:
        candidate = apply_matrix(
            compensation_matrix(matrix_number),
            (math.sqrt(3) + 0j, 0j, 0j),
        )
        peak = max(abs(value) for value in candidate)
        return scale(candidate, 1.0 / peak)
    # Even/double-delta matrices cannot create a unit two-element target from
    # one physical phase.  Use an observable phase loop with no zero sequence.
    return phase_loop(0, 1)


def _basis_for_matrix(test_phase: str, matrix_number: int) -> Phasor3:
    if test_phase not in TEST_PHASES:
        raise ValueError(f"unknown test phase: {test_phase}")
    if test_phase in ("AB", "BC", "CA"):
        indices = {"AB": (0, 1), "BC": (1, 2), "CA": (2, 0)}[test_phase]
        return phase_loop(*indices)
    if test_phase == "ABC":
        return positive_sequence()

    offset = {"A": 0, "B": 1, "C": 2}[test_phase]
    return rotate_channels(_matrix_single_phase_basis(matrix_number), offset)


def target_basis(test_phase: str, model: RelayModel) -> Phasor3:
    """Return a shared *compensated-domain* pattern for a two-side shot.

    If either winding rejects zero sequence, A/B/C cannot be pure unit vectors;
    the pattern follows the established sparse single-terminal convention and
    therefore energizes two relay elements.  It must not be interpreted as a
    universal physical-fault network model.
    """

    matrices = (model.winding1.matrix_number, model.winding2.matrix_number)
    reference = next((number for number in matrices if number != 0), 0)
    return _basis_for_matrix(test_phase, reference)


def _raw_from_compensated(
    winding: WindingModel,
    target: Sequence[complex],
    *,
    inverse_solution: Literal["minimum_norm", "sparse"],
) -> Phasor3:
    return winding.decompensate(target, solution=inverse_solution)


def _assert_generated_point(point: GeneratedPoint, tolerance: float = 1e-7) -> None:
    active = [index for index, value in enumerate(point.target_basis) if abs(value) > tolerance]
    inactive = [index for index in range(3) if index not in active]
    for index in active:
        basis_magnitude = abs(point.target_basis[index])
        expected_id = point.scalar.target_id * basis_magnitude
        # All built-in phase bases have unit magnitude on active elements.
        # Keep the multiplier explicit for future non-unit custom bases.
        expected_ir = point.scalar.target_ir * basis_magnitude
        if not math.isclose(
            point.measurement.id_values[index], expected_id, rel_tol=tolerance, abs_tol=tolerance
        ):
            raise AssertionError(
                f"forward Id mismatch at element {index}: "
                f"{point.measurement.id_values[index]} != {expected_id}"
            )
        if not math.isclose(
            point.measurement.ir_values[index], expected_ir, rel_tol=tolerance, abs_tol=tolerance
        ):
            raise AssertionError(
                f"forward Ir mismatch at element {index}: "
                f"{point.measurement.ir_values[index]} != {expected_ir}"
            )
    for index in inactive:
        if (
            point.measurement.id_values[index] > tolerance
            or point.measurement.ir_values[index] > tolerance
        ):
            raise AssertionError(f"unexpected differential quantity at inactive element {index}")


def generate_ratio_point(
    model: RelayModel,
    *,
    id_value: float,
    ir_value: float,
    test_phase: str,
    single_phase_sparse: bool = True,
) -> GeneratedPoint:
    """Generate a compensated-domain characteristic shot and verify it.

    The phase name selects a relay-domain algebraic pattern, not a physical
    transformer fault.  Physical faults additionally require a sequence-network
    model, fault side, grounding and impedances.
    """

    scalar = solve_scalar(id_value, ir_value, model.equations)
    basis = target_basis(test_phase, model)
    compensated1 = scale(basis, scalar.winding1)
    compensated2 = scale(basis, scalar.winding2)
    inverse_mode: Literal["minimum_norm", "sparse"] = (
        "sparse" if single_phase_sparse and test_phase in ("A", "B", "C") else "minimum_norm"
    )
    injected1 = _raw_from_compensated(
        model.winding1, compensated1, inverse_solution=inverse_mode
    )
    injected2 = _raw_from_compensated(
        model.winding2, compensated2, inverse_solution=inverse_mode
    )
    measurement = model.measure(injected1, injected2)
    result = GeneratedPoint(
        injected1,
        injected2,
        basis,
        scalar,
        measurement,
        test_phase,
    )
    _assert_generated_point(result)
    return result


def generate_through_current(
    model: RelayModel,
    load_current: float,
    *,
    reference_angle_deg: float = 0.0,
) -> ThroughCurrentPoint:
    """Generate balanced positive-sequence through current with ``Id = 0``."""

    if load_current < 0:
        raise ValueError("load_current must be nonnegative")
    reference = cmath.rect(load_current, math.radians(reference_angle_deg))
    q = positive_sequence(reference)
    if model.equations.id_equation in ("id_sum", "id_sum_sq"):
        q_other = scale(q, -1.0)
    elif model.equations.id_equation in ("id_diff", "id_diff_sq"):
        q_other = q
    else:
        raise ValueError("id_i1 has no two-winding through-current cancellation state")
    injected1 = _raw_from_compensated(
        model.winding1, q, inverse_solution="minimum_norm"
    )
    injected2 = _raw_from_compensated(
        model.winding2, q_other, inverse_solution="minimum_norm"
    )
    measurement = model.measure(injected1, injected2)
    if measurement.max_id > 1e-8:
        raise AssertionError("through-current forward check did not cancel Id")
    return ThroughCurrentPoint(injected1, injected2, measurement, q)


def generate_state_pair(
    model: RelayModel,
    *,
    id_value: float,
    ir_value: float,
    test_phase: str,
    diff_mode: int,
    load_current: float = 0.0,
    simulate_prefault: bool = True,
) -> StatePair:
    """Build prefault/fault currents for steady (0) or incremental (2) tests.

    Mode 0 does not superimpose load on the fault point.  Mode 2 adds the
    generated differential increment to a balanced through-load prefault.
    Residual mode 1 uses :func:`generate_zero_sequence_point` because it is a
    different protection quantity.
    """

    if diff_mode not in (0, 2):
        raise ValueError("generate_state_pair supports diff_mode 0 or 2")

    if simulate_prefault and load_current > 0:
        through = generate_through_current(model, load_current)
        prefault1, prefault2 = through.injected1, through.injected2
    else:
        prefault1, prefault2 = ZERO3, ZERO3
    prefault_measurement = model.measure(prefault1, prefault2)
    generated = generate_ratio_point(
        model,
        id_value=id_value,
        ir_value=ir_value,
        test_phase=test_phase,
    )
    if diff_mode == 2:
        fault1 = add(prefault1, generated.injected1)
        fault2 = add(prefault2, generated.injected2)
        checked = model.measure_increment(prefault1, prefault2, fault1, fault2)
    else:
        fault1, fault2 = generated.injected1, generated.injected2
        checked = generated.measurement
    return StatePair(
        prefault1,
        prefault2,
        fault1,
        fault2,
        prefault_measurement,
        checked,
        diff_mode,
    )


def generate_zero_sequence_point(
    model: ResidualRelayModel,
    *,
    id_value: float,
    ir_value: float,
) -> tuple[Phasor3, Phasor3, tuple[float, float]]:
    """Generate equal phase currents for a configured I0/3I0 element."""

    scalar = solve_scalar(id_value, ir_value, model.equations)
    injected1 = model.winding1.inject_for_residual(scalar.winding1, model.quantity)
    injected2 = model.winding2.inject_for_residual(scalar.winding2, model.quantity)
    actual = model.measure(injected1, injected2)
    if not math.isclose(actual[0], id_value, rel_tol=1e-8, abs_tol=1e-8):
        raise AssertionError("zero-sequence Id forward check failed")
    if not math.isclose(actual[1], ir_value, rel_tol=1e-8, abs_tol=1e-8):
        raise AssertionError("zero-sequence Ir forward check failed")
    return as_phasor3(injected1), as_phasor3(injected2), actual


def generate_harmonic_point(
    model: RelayModel,
    *,
    fundamental_id: float,
    harmonic_value: float,
    order: int,
    apply_side: Literal["i1", "i2"],
    error_type: Literal["relative", "absolute"],
    test_phase: str,
    relative_angle_deg: float = 0.0,
    spatial_mode: Literal["compensated", "physical_waveform"] = "compensated",
) -> HarmonicPoint:
    """Generate fundamental and Nth-harmonic phasors on one winding.

    Relative values are percentages of the fundamental operate current;
    absolute values use the same current convention as ``fundamental_id``.
    ``spatial_mode='compensated'`` creates the requested content in every
    active compensated relay element.  ``'physical_waveform'`` instead makes
    each energized physical channel's Nth-harmonic angle equal to N times its
    fundamental angle plus ``relative_angle_deg``; the resulting relay-element
    contents need not be equal after phase compensation.
    """

    if not isinstance(order, int) or isinstance(order, bool):
        raise TypeError("harmonic order must be an integer")
    if order < 2:
        raise ValueError("harmonic order must be >= 2")
    if not math.isfinite(fundamental_id) or fundamental_id <= 0:
        raise ValueError("fundamental_id must be positive")
    if not math.isfinite(harmonic_value) or harmonic_value < 0:
        raise ValueError("harmonic_value must be nonnegative")
    if not math.isfinite(relative_angle_deg):
        raise ValueError("relative_angle_deg must be finite")
    if apply_side not in ("i1", "i2"):
        raise ValueError("apply_side must be i1 or i2")
    if error_type not in ("relative", "absolute"):
        raise ValueError("error_type must be relative or absolute")
    if spatial_mode not in ("compensated", "physical_waveform"):
        raise ValueError("unknown harmonic spatial_mode")

    applied_winding = model.winding1 if apply_side == "i1" else model.winding2
    basis = _basis_for_matrix(test_phase, applied_winding.matrix_number)
    harmonic_amplitude = (
        fundamental_id * harmonic_value / 100.0
        if error_type == "relative"
        else harmonic_value
    )
    fundamental_target = scale(basis, fundamental_id)
    harmonic_rotation = cmath.rect(1.0, math.radians(relative_angle_deg))
    harmonic_target = scale(basis, harmonic_amplitude * harmonic_rotation)
    inverse_mode: Literal["minimum_norm", "sparse"] = (
        "sparse" if test_phase in ("A", "B", "C") else "minimum_norm"
    )

    if apply_side == "i1":
        fundamental1 = _raw_from_compensated(
            model.winding1, fundamental_target, inverse_solution=inverse_mode
        )
        if spatial_mode == "compensated":
            harmonic1 = _raw_from_compensated(
                model.winding1, harmonic_target, inverse_solution=inverse_mode
            )
        else:
            ratio = harmonic_amplitude / fundamental_id
            harmonic1 = as_phasor3(
                cmath.rect(
                    abs(value) * ratio,
                    order * cmath.phase(value) + math.radians(relative_angle_deg),
                )
                if abs(value) > 1e-12
                else 0j
                for value in fundamental1
            )
        fundamental2 = harmonic2 = ZERO3
    else:
        fundamental2 = _raw_from_compensated(
            model.winding2, fundamental_target, inverse_solution=inverse_mode
        )
        if spatial_mode == "compensated":
            harmonic2 = _raw_from_compensated(
                model.winding2, harmonic_target, inverse_solution=inverse_mode
            )
        else:
            ratio = harmonic_amplitude / fundamental_id
            harmonic2 = as_phasor3(
                cmath.rect(
                    abs(value) * ratio,
                    order * cmath.phase(value) + math.radians(relative_angle_deg),
                )
                if abs(value) > 1e-12
                else 0j
                for value in fundamental2
            )
        fundamental1 = harmonic1 = ZERO3

    applied_fundamental = fundamental1 if apply_side == "i1" else fundamental2
    applied_harmonic = harmonic1 if apply_side == "i1" else harmonic2
    compensated_fundamental = applied_winding.compensate(applied_fundamental)
    compensated_harmonic = applied_winding.compensate(applied_harmonic)
    contents = cast(
        tuple[float, float, float],
        tuple(
            0.0
            if abs(compensated_fundamental[index]) <= 1e-12
            else 100.0
            * abs(compensated_harmonic[index])
            / abs(compensated_fundamental[index])
            for index in range(3)
        ),
    )
    requested = 100.0 * harmonic_amplitude / fundamental_id
    if spatial_mode == "compensated":
        for index, value in enumerate(basis):
            if abs(value) > 1e-8 and not math.isclose(
                contents[index], requested, rel_tol=1e-8, abs_tol=1e-8
            ):
                raise AssertionError("harmonic-content forward check failed")
    return HarmonicPoint(
        order,
        fundamental1,
        fundamental2,
        harmonic1,
        harmonic2,
        requested,
        contents,
        apply_side,
        error_type,
        relative_angle_deg,
        spatial_mode,
    )


def distribute_parallel(total: complex, channels: int) -> tuple[complex, ...]:
    """Split a requested total current equally among paralleled test outputs."""

    if channels <= 0:
        raise ValueError("channels must be positive")
    return tuple(total / channels for _ in range(channels))


def recombine_parallel(outputs: Sequence[complex]) -> complex:
    return sum(outputs, 0j)
