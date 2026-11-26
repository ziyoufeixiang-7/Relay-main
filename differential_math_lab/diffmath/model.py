"""Forward models: what a differential relay calculates from injected currents."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Sequence

from .equations import EquationConfig, evaluate_pair
from .matrices import (
    Phasor3,
    apply_matrix,
    as_phasor3,
    compensation_matrix,
    inverse_compensation,
    scale,
    subtract,
)


@dataclass(frozen=True)
class WindingModel:
    """One relay restraint input.

    ``kp`` is defined unambiguously in this laboratory as:

        compensated = Matrix * (polarity * kp * injected)

    If a device manual gives TAP amperes instead, use ``kp = 1 / TAP``.
    ``polarity`` models CT/wiring reversal and must be +1 or -1.
    """

    matrix_number: int
    kp: float
    polarity: int = 1
    label: str = ""
    channel_order: tuple[int, int, int] = (0, 1, 2)
    channel_polarities: tuple[int, int, int] = (1, 1, 1)

    def __post_init__(self) -> None:
        compensation_matrix(self.matrix_number)
        if not isinstance(self.matrix_number, int) or isinstance(self.matrix_number, bool):
            raise TypeError("matrix_number must be an integer")
        if not isinstance(self.kp, (int, float)) or not math.isfinite(self.kp) or self.kp <= 0:
            raise ValueError("winding kp must be positive")
        if (
            not isinstance(self.polarity, int)
            or isinstance(self.polarity, bool)
            or self.polarity not in (-1, 1)
        ):
            raise ValueError("winding polarity must be +1 or -1")
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in self.channel_order
        ):
            raise TypeError("channel_order entries must be integers")
        if tuple(sorted(self.channel_order)) != (0, 1, 2):
            raise ValueError("channel_order must be a permutation of (0, 1, 2)")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in (-1, 1)
            for value in self.channel_polarities
        ):
            raise ValueError("each channel polarity must be +1 or -1")

    def map_channels(self, injected: Sequence[complex]) -> Phasor3:
        """Map physical test-set outputs into the relay's logical A/B/C order.

        ``channel_order[p]`` is the physical input used as logical phase ``p``.
        This makes phase swapping (for example ABC/ACB wiring) and individual
        CT polarity errors explicit instead of burying them in a clock number.
        """

        raw = as_phasor3(injected)
        return as_phasor3(
            raw[source] * self.channel_polarities[logical]
            for logical, source in enumerate(self.channel_order)
        )

    def unmap_channels(self, logical: Sequence[complex]) -> Phasor3:
        """Inverse of :meth:`map_channels` for generated test currents."""

        mapped = as_phasor3(logical)
        physical = [0j, 0j, 0j]
        for logical_index, source in enumerate(self.channel_order):
            physical[source] = mapped[logical_index] / self.channel_polarities[logical_index]
        return as_phasor3(physical)

    def compensate(self, injected: Sequence[complex]) -> Phasor3:
        scaled = scale(self.map_channels(injected), self.polarity * self.kp)
        return apply_matrix(compensation_matrix(self.matrix_number), scaled)

    def decompensate(
        self,
        target: Sequence[complex],
        *,
        solution: Literal["minimum_norm", "sparse"] = "minimum_norm",
    ) -> Phasor3:
        """Generate physical currents whose forward result is ``target``."""

        matrix_input = inverse_compensation(
            self.matrix_number,
            target,
            solution=solution,
        )
        logical = scale(matrix_input, 1.0 / (self.polarity * self.kp))
        physical = self.unmap_channels(logical)
        residual = subtract(self.compensate(physical), as_phasor3(target))
        if max(abs(value) for value in residual) > 2e-8:
            raise AssertionError(f"winding inverse forward check failed: {residual}")
        return physical


@dataclass(frozen=True)
class DifferentialMeasurement:
    compensated1: Phasor3
    compensated2: Phasor3
    id_values: tuple[float, float, float]
    ir_values: tuple[float, float, float]

    @property
    def max_id(self) -> float:
        """Largest Id only; it must not be paired with ``max_ir`` as one phase."""

        return max(self.id_values)

    @property
    def max_ir(self) -> float:
        """Largest Ir only; it may come from a different phase than max Id."""

        return max(self.ir_values)

    @property
    def phase_pairs(self) -> tuple[tuple[float, float], ...]:
        """Per-element ``(Ir, Id)`` pairs, preserving the phase association."""

        return tuple(zip(self.ir_values, self.id_values, strict=True))


@dataclass(frozen=True)
class RelayModel:
    winding1: WindingModel
    winding2: WindingModel
    equations: EquationConfig
    name: str = "two-winding relay"

    def measure(
        self,
        injected1: Sequence[complex],
        injected2: Sequence[complex],
    ) -> DifferentialMeasurement:
        compensated1 = self.winding1.compensate(injected1)
        compensated2 = self.winding2.compensate(injected2)
        pairs = tuple(
            evaluate_pair(compensated1[index], compensated2[index], self.equations)
            for index in range(3)
        )
        return DifferentialMeasurement(
            compensated1=compensated1,
            compensated2=compensated2,
            id_values=tuple(item[0] for item in pairs),  # type: ignore[arg-type]
            ir_values=tuple(item[1] for item in pairs),  # type: ignore[arg-type]
        )

    def measure_increment(
        self,
        prefault1: Sequence[complex],
        prefault2: Sequence[complex],
        fault1: Sequence[complex],
        fault2: Sequence[complex],
    ) -> DifferentialMeasurement:
        """Apply the configured equations to fault-minus-prefault phasors.

        This is a transparent mathematical delta model.  A real relay may use
        sample-domain filters, memories and external-fault logic; those require
        the specific device algorithm and cannot be inferred from static JSON.
        """

        delta1 = subtract(as_phasor3(fault1), as_phasor3(prefault1))
        delta2 = subtract(as_phasor3(fault2), as_phasor3(prefault2))
        return self.measure(delta1, delta2)


@dataclass(frozen=True)
class ResidualWindingModel:
    """One residual-element input; no phase matrix is silently ignored."""

    kp: float
    polarity: int = 1
    label: str = ""
    channel_order: tuple[int, int, int] = (0, 1, 2)
    channel_polarities: tuple[int, int, int] = (1, 1, 1)

    def __post_init__(self) -> None:
        if not isinstance(self.kp, (int, float)) or not math.isfinite(self.kp) or self.kp <= 0:
            raise ValueError("residual winding kp must be positive")
        if (
            not isinstance(self.polarity, int)
            or isinstance(self.polarity, bool)
            or self.polarity not in (-1, 1)
        ):
            raise ValueError("residual winding polarity must be +1 or -1")
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in self.channel_order
        ):
            raise TypeError("channel_order entries must be integers")
        if tuple(sorted(self.channel_order)) != (0, 1, 2):
            raise ValueError("channel_order must be a permutation of (0, 1, 2)")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value not in (-1, 1)
            for value in self.channel_polarities
        ):
            raise ValueError("each channel polarity must be +1 or -1")

    def map_channels(self, injected: Sequence[complex]) -> Phasor3:
        raw = as_phasor3(injected)
        return as_phasor3(
            raw[source] * self.channel_polarities[logical]
            for logical, source in enumerate(self.channel_order)
        )

    def unmap_channels(self, logical: Sequence[complex]) -> Phasor3:
        mapped = as_phasor3(logical)
        physical = [0j, 0j, 0j]
        for logical_index, source in enumerate(self.channel_order):
            physical[source] = mapped[logical_index] / self.channel_polarities[logical_index]
        return as_phasor3(physical)

    def residual(self, injected: Sequence[complex], quantity: str) -> complex:
        value = self.polarity * self.kp * sum(self.map_channels(injected))
        if quantity == "I0":
            return value / 3.0
        if quantity == "3I0":
            return value
        raise ValueError(f"unknown residual quantity: {quantity}")

    def inject_for_residual(self, target: complex, quantity: str) -> Phasor3:
        if quantity not in ("I0", "3I0"):
            raise ValueError(f"unknown residual quantity: {quantity}")
        summed_target = target * (3.0 if quantity == "I0" else 1.0)
        per_phase = summed_target / (3.0 * self.polarity * self.kp)
        result = self.unmap_channels((per_phase, per_phase, per_phase))
        if abs(self.residual(result, quantity) - target) > 1e-9:
            raise AssertionError("residual winding inverse forward check failed")
        return result


@dataclass(frozen=True)
class ResidualRelayModel:
    """Explicit two-side residual/zero-sequence forward model.

    ``quantity='3I0'`` uses the phase-current sum.  ``quantity='I0'`` uses
    one third of that sum.  This is intentionally separate from phase
    compensation matrices, which normally eliminate zero sequence.
    """

    winding1: ResidualWindingModel
    winding2: ResidualWindingModel
    equations: EquationConfig
    quantity: str = "3I0"

    def __post_init__(self) -> None:
        if self.quantity not in ("I0", "3I0"):
            raise ValueError("residual quantity must be I0 or 3I0")

    def _residual(
        self, winding: ResidualWindingModel, injected: Sequence[complex]
    ) -> complex:
        return winding.residual(injected, self.quantity)

    def measure(
        self,
        injected1: Sequence[complex],
        injected2: Sequence[complex],
    ) -> tuple[float, float]:
        return evaluate_pair(
            self._residual(self.winding1, injected1),
            self._residual(self.winding2, injected2),
            self.equations,
        )
