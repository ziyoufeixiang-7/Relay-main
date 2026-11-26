"""Relay compensation profiles and legacy front-end setting adapter."""

from __future__ import annotations

from dataclasses import dataclass

from .equations import EquationConfig
from .model import RelayModel, WindingModel


@dataclass(frozen=True)
class LegacyProtectionSettings:
    letter1: str
    letter2: str
    high_clock: int = 0
    low_clock: int = 0
    phase_correction: str = "none"
    zero_sequence_correction: bool = False
    kp1: float = 1.0
    kp2: float = 1.0

    def __post_init__(self) -> None:
        if self.letter1.lower() not in ("y", "d"):
            raise ValueError("letter1 must be y or d")
        if self.letter2.lower() not in ("y", "d"):
            raise ValueError("letter2 must be y or d")
        if self.phase_correction not in ("none", "y-side", "delta-side"):
            raise ValueError("phase_correction must be none, y-side or delta-side")


def explicit_profile(
    *,
    matrix1: int,
    matrix2: int,
    kp1: float,
    kp2: float,
    equations: EquationConfig,
    polarity1: int = 1,
    polarity2: int = 1,
    channel_order1: tuple[int, int, int] = (0, 1, 2),
    channel_order2: tuple[int, int, int] = (0, 1, 2),
    channel_polarities1: tuple[int, int, int] = (1, 1, 1),
    channel_polarities2: tuple[int, int, int] = (1, 1, 1),
    name: str = "explicit matrix profile",
) -> RelayModel:
    """Build a forward model from explicit SEL-style matrix/TAP settings.

    This is the preferred calculation API because matrix numbers are
    device settings when the DUT uses this matrix family, whereas transformer
    nameplate vector group alone is not enough to determine what the relay sees.
    """

    return RelayModel(
        WindingModel(
            matrix1,
            kp1,
            polarity1,
            "W1",
            channel_order1,
            channel_polarities1,
        ),
        WindingModel(
            matrix2,
            kp2,
            polarity2,
            "W2",
            channel_order2,
            channel_polarities2,
        ),
        equations,
        name,
    )


def _nonzero_matrix(number: int) -> int:
    reduced = number % 12
    return 12 if reduced == 0 else reduced


def _legacy_matrix_pair(settings: LegacyProtectionSettings) -> tuple[int, int]:
    """Map the existing UI's correction semantics to matrix numbers.

    The relative clock condition for ABC sequence is ``M2 - M1 = low-high``.
    For a selected correction side:

    * one matched winding receives the whole relative rotation;
    * when both windings match, Matrix 11 is the reference so a single-terminal
      injection uses the established sqrt(3) method and zero sequence is removed;
    * ``zero_sequence_correction`` promotes an otherwise-uncompensated Y side
      from Matrix 0 to Matrix 12.

    This adapter captures the legacy interface.  If the protected relay exposes
    actual matrix numbers, use :func:`explicit_profile` instead.
    """

    l1 = settings.letter1.lower()
    l2 = settings.letter2.lower()
    relative = (settings.low_clock - settings.high_clock) % 12
    selected = {
        "none": None,
        "y-side": "y",
        "delta-side": "d",
    }[settings.phase_correction]

    matched1 = selected is not None and l1 == selected
    matched2 = selected is not None and l2 == selected
    matrix1 = 0
    matrix2 = 0

    if matched1 and matched2:
        matrix1 = 11
        matrix2 = _nonzero_matrix(11 + relative)
    elif matched1:
        matrix1 = (-relative) % 12
        matrix1 = _nonzero_matrix(matrix1) if relative else 12
    elif matched2:
        matrix2 = _nonzero_matrix(relative) if relative else 12

    if settings.zero_sequence_correction:
        if l1 == "y" and matrix1 == 0:
            matrix1 = 12
        if l2 == "y" and matrix2 == 0:
            matrix2 = 12

    return matrix1, matrix2


def profile_from_legacy_settings(
    settings: LegacyProtectionSettings,
    equations: EquationConfig,
) -> RelayModel:
    matrix1, matrix2 = _legacy_matrix_pair(settings)
    label = (
        f"legacy {settings.letter1}{settings.letter2}"
        f"{settings.low_clock % 12} {settings.phase_correction}"
        f" zs={settings.zero_sequence_correction}"
    )
    return explicit_profile(
        matrix1=matrix1,
        matrix2=matrix2,
        kp1=settings.kp1,
        kp2=settings.kp2,
        equations=equations,
        name=label,
    )


def assumed_sel_profile(
    *,
    letter1: str,
    letter2: str,
    high_clock: int = 0,
    low_clock: int = 0,
    kp1: float = 1.0,
    kp2: float = 1.0,
    equations: EquationConfig,
) -> RelayModel:
    """Create an explicitly assumed SEL-style engineering profile.

    Assumptions: ABC sequence, standard phase-to-bushing mapping, wye CTs and
    differential CT polarity.  If a delta winding exists it is Matrix 0 and the
    wye winding carries compensation.  For Y/Y, W1 uses Matrix 11 and W2 is
    rotated relative to it.  These assumptions must be checked against the
    actual DUT and installation before hardware use.  This helper is never a
    substitute for the relay's settings/manual.
    """

    l1 = letter1.lower()
    l2 = letter2.lower()
    relative = (low_clock - high_clock) % 12
    if l1 not in ("y", "d") or l2 not in ("y", "d"):
        raise ValueError("letters must be y or d")

    if l1 == "d" and l2 == "y":
        matrices = (0, _nonzero_matrix(relative))
    elif l1 == "y" and l2 == "d":
        matrices = (_nonzero_matrix(-relative), 0)
    elif l1 == "y" and l2 == "y":
        matrices = (11, _nonzero_matrix(11 + relative))
    else:
        matrices = (0, relative)

    return explicit_profile(
        matrix1=matrices[0],
        matrix2=matrices[1],
        kp1=kp1,
        kp2=kp2,
        equations=equations,
        name=f"assumed SEL-style {letter1}{letter2}{relative}",
    )
