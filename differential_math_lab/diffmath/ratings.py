"""Transformer/CT rating helpers for relay-current normalization."""

from __future__ import annotations

import math
from typing import Literal


def ct_ratio(primary_amperes: float, secondary_amperes: float) -> float:
    """Return the numerical CT ratio ``primary / secondary``."""

    if primary_amperes <= 0 or secondary_amperes <= 0:
        raise ValueError("CT primary and secondary ratings must be positive")
    return primary_amperes / secondary_amperes


def tap_secondary_amperes(
    *,
    power_mva: float,
    line_voltage_kv: float,
    ct_primary_amperes: float,
    ct_secondary_amperes: float,
    ct_connection: Literal["wye", "delta"] = "wye",
) -> float:
    """Calculate a SEL-style winding TAP in relay-secondary amperes.

    ``TAP = MVA * 1000 * CTCON / (sqrt(3) * kV * CTR)`` where CTCON is 1
    for wye-connected CTs and sqrt(3) for delta-connected CTs.  This helper is
    for a model explicitly known to use that convention; other DUTs may define
    their reference/tap current differently.
    """

    if power_mva <= 0 or line_voltage_kv <= 0:
        raise ValueError("power and line voltage ratings must be positive")
    ratio = ct_ratio(ct_primary_amperes, ct_secondary_amperes)
    if ct_connection == "wye":
        connection_factor = 1.0
    elif ct_connection == "delta":
        connection_factor = math.sqrt(3.0)
    else:
        raise ValueError("ct_connection must be 'wye' or 'delta'")
    return (
        power_mva
        * 1000.0
        * connection_factor
        / (math.sqrt(3.0) * line_voltage_kv * ratio)
    )


def kp_from_tap(tap_amperes: float) -> float:
    """Convert a divisor-style TAP to this laboratory's forward multiplier."""

    if tap_amperes <= 0:
        raise ValueError("tap amperes must be positive")
    return 1.0 / tap_amperes
