"""Explicit conversion from mathematical phasors to API-friendly values."""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from .generator import GeneratedPoint, HarmonicPoint
from .matrices import phasor_to_polar


def phasors_to_polar(
    phasors: Iterable[complex], *, amplitude_digits: int = 6, angle_digits: int = 4
) -> list[list[float]]:
    result: list[list[float]] = []
    for phasor in phasors:
        amplitude, angle = phasor_to_polar(phasor, max(amplitude_digits, angle_digits))
        result.append([round(amplitude, amplitude_digits), round(angle, angle_digits)])
    return result


def six_channel_polar(point: GeneratedPoint) -> list[list[float]]:
    """Return six phasors as ``[amplitude, angle_deg]`` without unit conversion.

    The amplitudes retain the caller's convention.  Call ``rms_to_peak`` at the
    hardware boundary only when the source quantities are RMS and the output
    protocol explicitly requires peak values.
    """

    return phasors_to_polar(point.injected1 + point.injected2)


def harmonic_layers(
    point: HarmonicPoint,
) -> tuple[list[dict[str, list[float]]], list[dict[str, list[float]]]]:
    """Return per-channel fundamental/Nth-harmonic layer dictionaries."""

    side1: list[dict[str, list[float]]] = []
    side2: list[dict[str, list[float]]] = []
    for fundamental, harmonic, destination in (
        (point.fundamental1, point.harmonic1, side1),
        (point.fundamental2, point.harmonic2, side2),
    ):
        for base, overtone in zip(fundamental, harmonic, strict=True):
            base_value = phasors_to_polar((base,))[0]
            harmonic_value = phasors_to_polar((overtone,))[0]
            destination.append({"1": base_value, str(point.order): harmonic_value})
    return side1, side2


def rms_to_peak(value: float) -> float:
    """Opt-in boundary conversion; the core solver never calls this implicitly."""

    return value * math.sqrt(2.0)


def peak_to_rms(value: float) -> float:
    return value / math.sqrt(2.0)
