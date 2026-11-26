#!/usr/bin/env python3
"""Run deterministic mathematical validations and print an audit summary."""

from __future__ import annotations

from collections import Counter

from diffmath import EquationConfig, LegacyProtectionSettings, generate_ratio_point
from diffmath.legacy_validation import (
    known_multiphase_matrix_cases,
    known_single_phase_matrix_cases,
)
from diffmath.matrices import phasor_to_polar
from diffmath.profiles import profile_from_legacy_settings


def format_vector(values):
    return [phasor_to_polar(value, 6) for value in values]


def main() -> int:
    equations = EquationConfig("id_sum", "ir_diff_k", 2.0)
    profiles = {
        "Yd11/y-side": profile_from_legacy_settings(
            LegacyProtectionSettings("y", "d", 0, 11, "y-side", False), equations
        ),
        "Yy12/y-side": profile_from_legacy_settings(
            LegacyProtectionSettings("y", "y", 0, 12, "y-side", False), equations
        ),
    }

    print("Standalone algebraic validation; NOT a DUT, FPGA, or field-hardware test")
    print("Units: caller-selected phasor units before ApiDiffTest RMS/peak conversion")
    print("Generated 6I characteristic shots closed through the explicit forward model")
    print("Coordinates: Id=0.5, Ir=1.0")
    for name, profile in profiles.items():
        print(
            f"\n{name}: matrices "
            f"M{profile.winding1.matrix_number}/M{profile.winding2.matrix_number}"
        )
        for phase in ("A", "AB", "ABC"):
            point = generate_ratio_point(
                profile, id_value=0.5, ir_value=1.0, test_phase=phase
            )
            print(f"  {phase:>3} W1={format_vector(point.injected1)}")
            print(f"      W2={format_vector(point.injected2)}")
            print(f"      Id={tuple(round(x, 6) for x in point.measurement.id_values)}")
            print(f"      Ir={tuple(round(x, 6) for x in point.measurement.ir_values)}")

    legacy = known_single_phase_matrix_cases()
    counts = Counter((item.group, item.passed) for item in legacy)
    print("\nCurrent production single-phase math vs inferred legacy matrix profile")
    for group in ("Yd11", "Yy12"):
        print(
            f"  {group}: PASS={counts[(group, True)]}, "
            f"FAIL={counts[(group, False)]}"
        )
    failures = [item for item in legacy if not item.passed]
    for item in failures:
        print(
            f"  FAIL {item.group} {item.phase_correction} "
            f"ZS={item.zero_sequence_correction} {item.test_phase}: "
            f"Id error={item.max_id_error:.6g}, Ir error={item.max_ir_error:.6g}"
        )

    multiphase = known_multiphase_matrix_cases()
    print("\nCurrent production multi-phase math vs the same inferred profile")
    for item in multiphase:
        status = "PASS" if item.passed else "FAIL"
        print(
            f"  {status} {item.group} {item.test_phase}: "
            f"Id error={item.max_id_error:.6g}, Ir error={item.max_ir_error:.6g}"
        )
    print("\nPASS means algebraic agreement with the stated assumed profile only.")
    print("Known FAIL rows are audit findings, so they do not make this script fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
