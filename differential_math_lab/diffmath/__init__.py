"""Standalone transformer-differential calculation and validation library.

This package deliberately has no dependency on the FPGA/control path.  Values are
phasors in one caller-selected convention (normally RMS); no implicit sqrt(2)
conversion is performed here.
"""

from .equations import (
    EquationConfig,
    EquationDomainError,
    ScalarSolution,
    evaluate_pair,
    scale_api_targets,
    solve_scalar,
)
from .generator import (
    GeneratedPoint,
    HarmonicPoint,
    StatePair,
    generate_harmonic_point,
    generate_ratio_point,
    generate_state_pair,
    generate_through_current,
    generate_zero_sequence_point,
)
from .matrices import compensation_matrix
from .model import (
    DifferentialMeasurement,
    RelayModel,
    ResidualRelayModel,
    ResidualWindingModel,
    WindingModel,
)
from .profiles import (
    LegacyProtectionSettings,
    explicit_profile,
    profile_from_legacy_settings,
)
from .output import harmonic_layers, peak_to_rms, phasors_to_polar, rms_to_peak, six_channel_polar
from .ratings import ct_ratio, kp_from_tap, tap_secondary_amperes
from .two_current import (
    ScalarWinding,
    TwoCurrentModel,
    TwoCurrentPoint,
    generate_2i_point,
    legacy_2i_model,
)

__all__ = [
    "DifferentialMeasurement",
    "EquationConfig",
    "EquationDomainError",
    "GeneratedPoint",
    "HarmonicPoint",
    "LegacyProtectionSettings",
    "RelayModel",
    "ResidualRelayModel",
    "ResidualWindingModel",
    "ScalarSolution",
    "ScalarWinding",
    "StatePair",
    "WindingModel",
    "compensation_matrix",
    "evaluate_pair",
    "explicit_profile",
    "generate_harmonic_point",
    "generate_2i_point",
    "generate_ratio_point",
    "generate_state_pair",
    "generate_through_current",
    "generate_zero_sequence_point",
    "harmonic_layers",
    "peak_to_rms",
    "phasors_to_polar",
    "profile_from_legacy_settings",
    "legacy_2i_model",
    "ct_ratio",
    "kp_from_tap",
    "scale_api_targets",
    "six_channel_polar",
    "rms_to_peak",
    "solve_scalar",
    "tap_secondary_amperes",
    "TwoCurrentModel",
    "TwoCurrentPoint",
]
