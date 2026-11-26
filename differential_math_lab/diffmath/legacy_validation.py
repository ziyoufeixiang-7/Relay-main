"""Read-only mathematical comparison against the current production 6I code."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

from .equations import EquationConfig
from .generator import target_basis
from .matrices import Phasor3, as_phasor3
from .model import DifferentialMeasurement, RelayModel
from .profiles import LegacyProtectionSettings, profile_from_legacy_settings


@dataclass(frozen=True)
class LegacyValidation:
    group: str
    phase_correction: str
    zero_sequence_correction: bool
    test_phase: str
    injected1: Phasor3
    injected2: Phasor3
    measurement: DifferentialMeasurement
    max_id_error: float
    max_ir_error: float
    passed: bool


def _import_production_functions():
    repository = Path(__file__).resolve().parents[2]
    relay_package = repository / "RelayProtection"
    path = str(relay_package)
    if path not in sys.path:
        sys.path.insert(0, path)
    from calc.diff import calculate_physical_currents, solve  # type: ignore

    return solve, calculate_physical_currents


def current_production_point(
    *,
    id_value: float,
    ir_value: float,
    equations: EquationConfig,
    settings: LegacyProtectionSettings,
    test_phase: str,
) -> tuple[Phasor3, Phasor3]:
    if test_phase not in ("A", "B", "C", "AB", "BC", "CA", "ABC"):
        raise ValueError(f"unknown production test phase: {test_phase}")
    solve, calculate = _import_production_functions()
    i1, i2, theta2 = solve(
        id_val=id_value,
        ir_val=ir_value,
        k=equations.k_factor,
        kp1=settings.kp1,
        kp2=settings.kp2,
        id_eq=equations.id_equation,
        ir_eq=equations.ir_equation,
    )
    result1, result2 = calculate(
        i1_prime=i1,
        i2_prime=i2,
        theta2=theta2,
        test_phase=test_phase,
        phase_correction=settings.phase_correction,
        zero_seq_correction=settings.zero_sequence_correction,
        high_clock=settings.high_clock,
        low_clock=settings.low_clock,
        l1_type=settings.letter1,
        l2_type=settings.letter2,
    )
    return as_phasor3(result1), as_phasor3(result2)


def validate_current_point(
    *,
    group: str,
    id_value: float,
    ir_value: float,
    equations: EquationConfig,
    settings: LegacyProtectionSettings,
    test_phase: str,
    tolerance: float = 1e-7,
) -> LegacyValidation:
    model: RelayModel = profile_from_legacy_settings(settings, equations)
    injected1, injected2 = current_production_point(
        id_value=id_value,
        ir_value=ir_value,
        equations=equations,
        settings=settings,
        test_phase=test_phase,
    )
    measurement = model.measure(injected1, injected2)
    basis = target_basis(test_phase, model)
    expected_id = tuple(abs(value) * id_value for value in basis)
    expected_ir = tuple(abs(value) * ir_value for value in basis)
    id_error = max(
        abs(actual - expected)
        for actual, expected in zip(measurement.id_values, expected_id, strict=True)
    )
    ir_error = max(
        abs(actual - expected)
        for actual, expected in zip(measurement.ir_values, expected_ir, strict=True)
    )
    return LegacyValidation(
        group,
        settings.phase_correction,
        settings.zero_sequence_correction,
        test_phase,
        injected1,
        injected2,
        measurement,
        id_error,
        ir_error,
        math.isclose(id_error, 0.0, abs_tol=tolerance)
        and math.isclose(ir_error, 0.0, abs_tol=tolerance),
    )


def known_single_phase_matrix_cases() -> list[LegacyValidation]:
    """Evaluate the two groups reported as field-tested by the user.

    The repository contains no captured hardware run, so these are mathematical
    forward checks, not a replacement for the user's field evidence.
    """

    equations = EquationConfig("id_sum", "ir_diff_k", 2.0)
    cases: list[LegacyValidation] = []
    groups = {
        "Yd11": ("y", "d", 0, 11),
        "Yy12": ("y", "y", 0, 12),
    }
    for group, (letter1, letter2, high, low) in groups.items():
        for correction in ("none", "y-side", "delta-side"):
            for zero_sequence in (False, True):
                settings = LegacyProtectionSettings(
                    letter1,
                    letter2,
                    high,
                    low,
                    correction,
                    zero_sequence,
                )
                for phase in ("A", "B", "C"):
                    cases.append(
                        validate_current_point(
                            group=group,
                            id_value=0.5,
                            ir_value=1.0,
                            equations=equations,
                            settings=settings,
                            test_phase=phase,
                        )
                    )
    return cases


def known_multiphase_matrix_cases() -> list[LegacyValidation]:
    """Audit current AB/BC/CA/ABC output against explicit assumed matrices.

    This detects disagreement between the production multi-phase branch and the
    same matrix-domain model that explains the field-checked single-phase cases.
    It is still a mathematical comparison, not a captured DUT/hardware test.
    """

    equations = EquationConfig("id_sum", "ir_diff_k", 2.0)
    cases: list[LegacyValidation] = []
    groups = {
        "Yd11": ("y", "d", 0, 11),
        "Yy12": ("y", "y", 0, 12),
    }
    for group, (letter1, letter2, high, low) in groups.items():
        settings = LegacyProtectionSettings(
            letter1,
            letter2,
            high,
            low,
            "y-side",
            False,
        )
        for phase in ("AB", "BC", "CA", "ABC"):
            cases.append(
                validate_current_point(
                    group=group,
                    id_value=0.5,
                    ir_value=1.0,
                    equations=equations,
                    settings=settings,
                    test_phase=phase,
                )
            )
    return cases


# Compatibility names make the scope clear at call sites written before the
# all-phase audit was added.
current_production_single_phase = current_production_point
validate_current_single_phase = validate_current_point
