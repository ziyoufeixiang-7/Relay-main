#!/usr/bin/env python3
"""Small executable example of the forward-model-first workflow."""

from __future__ import annotations

from diffmath import EquationConfig, explicit_profile, generate_ratio_point
from diffmath.output import six_channel_polar


def main() -> None:
    # Example assumption only: the DUT is explicitly configured as SEL-style
    # Matrix 1 on W1 and Matrix 0 on W2.  Replace these with actual DUT settings.
    relay = explicit_profile(
        matrix1=1,
        matrix2=0,
        kp1=1.0,
        kp2=1.0,
        equations=EquationConfig.abb_615(),
        name="example M1/M0 relay",
    )

    point = generate_ratio_point(
        relay,
        id_value=0.5,
        ir_value=1.0,
        test_phase="AB",
    )
    print("[Ia, Ib, Ic, Ix, Iy, Iz] =")
    print(six_channel_polar(point))
    print("relay-domain (Ir, Id) by element =")
    print(point.measurement.phase_pairs)

    # A captured six-current vector can be checked in the opposite direction:
    checked = relay.measure(point.injected1, point.injected2)
    assert checked == point.measurement


if __name__ == "__main__":
    main()

