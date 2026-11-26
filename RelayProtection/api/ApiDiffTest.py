"""Differential-protection experiment API.

The established hardware sequence is retained: optional pre-fault Node 1,
fault Node 2, and zero-current waiting Node 3. Differential coordinates and
mathematical currents use RMS values; current phasors are converted to peak
amplitudes only when they are serialized into FPGA ``statics``.
"""

from __future__ import annotations

import asyncio
import cmath
from dataclasses import dataclass
import logging
import math
from typing import Any, Awaitable, Callable, Dict, List, Optional

from api.BaseApi import ApiNodeData, BaseApi
from calc.diff_test_math import (
    DifferentialMathError,
    calculate_harmonic_2i,
    calculate_harmonic_6i,
    calculate_ratio_2i,
    calculate_ratio_6i,
    phase_target,
)


logger = logging.getLogger("ApiDiffTest")


# Front-end API channel indices. BaseApi performs the final MapChannel step.
ALL_CURRENT_CHANNELS = ["6", "7", "8", "9", "10", "11"]
TERMINAL_6I = {
    "high-y": ["6", "7", "8"],
    "low-y": ["9", "10", "11"],
}
TERMINAL_2I = {
    "ia": ["6"], "ib": ["7"], "ic": ["8"],
    "ix": ["9"], "iy": ["10"], "iz": ["11"],
    "iab": ["6", "7"], "ibc": ["7", "8"], "ica": ["8", "6"],
    "ixy": ["9", "10"], "iyz": ["10", "11"], "izx": ["11", "9"],
    "iabc": ["6", "7", "8"], "ixyz": ["9", "10", "11"],
}


class _ExperimentStopped(Exception):
    """Internal control-flow marker; never reported as a test error."""


@dataclass(frozen=True)
class _PreparedRatioPoint:
    prefault_statics: Dict[str, Dict[str, List[float]]]
    fault_statics: Dict[str, Dict[str, List[float]]]
    report_i1: float
    report_i2: float


@dataclass(frozen=True)
class _PointExecution:
    tripped: bool
    fault_statics: Dict[str, Dict[str, List[float]]]
    report_i1: float = 0.0
    report_i2: float = 0.0


class ApiDiffTest(BaseApi):
    MODULE_KEY = "differential_test"

    def __init__(self):
        super().__init__()
        self._payload: Dict[str, Any] = {}
        self._statics_base: Dict[str, Dict[str, List[float]]] = {}
        self._points: List[Dict[str, Any]] = []
        self._active_project = "ratio-fixed"
        self._project_data: Dict[str, Any] = {}
        self._harmonic_cfg: Dict[str, Any] = {}
        self._test_phase = "A"

        self._logic_mask = 7
        self._fault_duration_ms = 1000
        self._pre_fault_duration_ms = 1000
        self._simulate_prefault = True
        self._diff_mode = 0
        self._load_current = 0.0

        self._connection_type = "ext-differential"
        self._i1_terminal = "high-y"
        self._i2_terminal = "low-y"
        self._i1_channels: List[str] = []
        self._i2_channels: List[str] = []

        self._harmonic_order = 2
        self._current_layers = {1}

        self._fsm_state = "IDLE"
        self._current_node = 0
        self._di_tripped = False
        self._di_reference = 0
        self._di_reference_latched = False
        self._node_entry_di = 0
        self._node_done: Optional[asyncio.Event] = None
        self._run_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _as_dict(value: Any, name: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise DifferentialMathError(f"{name} must be an object")
        return value

    @staticmethod
    def _as_bool(value: Any, name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        raise DifferentialMathError(f"{name} must be a boolean")

    @staticmethod
    def _as_number(value: Any, name: str, *, nonnegative: bool = False) -> float:
        if isinstance(value, bool):
            raise DifferentialMathError(f"{name} must be a finite number")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DifferentialMathError(f"{name} must be a finite number") from exc
        if not math.isfinite(result):
            raise DifferentialMathError(f"{name} must be a finite number")
        if nonnegative and result < 0.0:
            raise DifferentialMathError(f"{name} must not be negative")
        return result

    @classmethod
    def _duration(cls, value: Any, name: str) -> int:
        duration = cls._as_number(value, name, nonnegative=True)
        # USEEngine interprets timeoutMs=0 as no timeout.
        return max(1, int(round(duration)))

    def _onSetup(self, params: Dict[str, Any]):
        try:
            params = self._as_dict(params, "params")
            sys_cfg = self._as_dict(params.get("sys", {}), "params.sys")
            payload = self._as_dict(params.get("payload", {}), "params.payload")
            project = self._as_dict(
                payload.get("projectSettings", {}), "payload.projectSettings"
            )
            test_params = self._as_dict(
                payload.get("testParams", {}), "payload.testParams"
            )
            connection = self._as_dict(
                payload.get("connectionSettings", {}),
                "payload.connectionSettings",
            )

            self._payload = payload
            self._statics_base = self._copy_voltage_statics(params.get("statics", {}))
            self._logic_mask = int(sys_cfg.get("logicMask", 7))
            self._active_project = str(project.get("activeProject", "ratio-fixed"))
            if self._active_project not in {
                "ratio-fixed", "ratio-search", "harmonic-fixed", "harmonic-search"
            }:
                raise DifferentialMathError(
                    f"unsupported activeProject: {self._active_project}"
                )

            project_key = (
                "harmonic" if self._active_project.startswith("harmonic") else "ratio"
            )
            self._project_data = self._as_dict(
                project.get(project_key, project), f"projectSettings.{project_key}"
            )
            raw_points = self._project_data.get("points", [])
            if not isinstance(raw_points, list):
                raise DifferentialMathError("project points must be an array")
            self._points = [self._as_dict(point, "test point") for point in raw_points]
            if not self._points:
                raise DifferentialMathError("no differential test points were provided")

            self._test_phase = str(project.get("testPhase", "A")).upper()
            self._fault_duration_ms = self._duration(
                test_params.get("faultDuration", 1000), "faultDuration"
            )
            self._pre_fault_duration_ms = self._duration(
                test_params.get("preFaultDuration", 1000), "preFaultDuration"
            )
            self._simulate_prefault = self._as_bool(
                test_params.get("simulatePreFault", True), "simulatePreFault"
            )
            if self._active_project.startswith("ratio"):
                mode_value = self._as_number(
                    test_params.get("diffMode", 0), "diffMode", nonnegative=True
                )
                self._diff_mode = int(mode_value)
                if self._diff_mode != mode_value:
                    raise DifferentialMathError("diffMode must be an integer")
                self._load_current = self._as_number(
                    test_params.get("loadCurrent", 0.0),
                    "loadCurrent",
                    nonnegative=True,
                )
            else:
                # Unused ratio-only fields from an older front end must not
                # influence or invalidate a harmonic experiment.
                self._diff_mode = 0
                self._load_current = 0.0

            self._connection_type = str(connection.get("type", "ext-differential"))
            if self._connection_type == "ext-differential":
                self._i1_terminal = str(connection.get("i1Terminal", "high-y"))
                self._i2_terminal = str(connection.get("i2Terminal", "low-y"))
                phase_target(self._test_phase)
                terminal_table = TERMINAL_6I
            elif self._connection_type == "phase-differential":
                self._i1_terminal = str(connection.get("i1Terminal", "ia"))
                self._i2_terminal = str(connection.get("i2Terminal", "ix"))
                terminal_table = TERMINAL_2I
            else:
                raise DifferentialMathError(
                    f"unsupported connection type: {self._connection_type}"
                )
            try:
                self._i1_channels = list(terminal_table[self._i1_terminal])
                self._i2_channels = list(terminal_table[self._i2_terminal])
            except KeyError as exc:
                raise DifferentialMathError(
                    "invalid current terminal selection: "
                    f"{self._i1_terminal}/{self._i2_terminal}"
                ) from exc
            overlap = set(self._i1_channels) & set(self._i2_channels)
            if overlap:
                raise DifferentialMathError(
                    f"I1 and I2 terminals overlap on channels {sorted(overlap)}"
                )

            self._harmonic_cfg = {}
            self._current_layers = {1}
            if self._active_project.startswith("harmonic"):
                # _project_data is either projectSettings.harmonic (current
                # protocol) or the flat projectSettings object (legacy).
                self._harmonic_cfg = self._project_data
                order_value = self._as_number(
                    self._harmonic_cfg.get("order", 2), "harmonic.order"
                )
                self._harmonic_order = int(order_value)
                if (
                    self._harmonic_order != order_value
                    or not 2 <= self._harmonic_order <= 64
                ):
                    raise DifferentialMathError(
                        "harmonic.order must be an integer from 2 to 64"
                    )
                self._current_layers.add(self._harmonic_order)

            self._di_reference = int(getattr(self.ctrl, "di", 0)) & 0xFF
            self._node_entry_di = self._di_reference
            self._node_done = asyncio.Event()
            self._fsm_state = "PREHEAT"
            self._run_task = asyncio.create_task(self._run_guarded())
            logger.info(
                "Differential test: project=%s, connection=%s, phase=%s, "
                "points=%d, preFault=%s/%dms, fault=%dms",
                self._active_project,
                self._connection_type,
                self._test_phase,
                len(self._points),
                self._simulate_prefault,
                self._pre_fault_duration_ms,
                self._fault_duration_ms,
            )
        except (DifferentialMathError, TypeError, ValueError, OverflowError) as exc:
            logger.error("Differential test setup rejected: %s", exc)
            self.ctrl.stopTest(f"Differential test configuration error: {exc}")

    # -------------------------------------------------------------- statics I/O

    @staticmethod
    def _copy_voltage_statics(value: Any) -> Dict[str, Dict[str, List[float]]]:
        """Copy Ua..Uz only; unrelated/legacy JSON members are ignored."""

        if not isinstance(value, dict):
            raise DifferentialMathError("params.statics must be an object")
        result: Dict[str, Dict[str, List[float]]] = {}
        for raw_channel, raw_layers in value.items():
            try:
                channel = int(raw_channel)
            except (TypeError, ValueError):
                continue
            if not 0 <= channel <= 5:
                continue
            if not isinstance(raw_layers, dict):
                raise DifferentialMathError(
                    f"statics[{raw_channel}] must be an object"
                )
            copied: Dict[str, List[float]] = {}
            for layer, values in raw_layers.items():
                try:
                    layer_number = int(layer)
                except (TypeError, ValueError):
                    continue
                if not 0 <= layer_number <= 64:
                    continue
                if not isinstance(values, (list, tuple)) or len(values) < 2:
                    raise DifferentialMathError(
                        f"statics[{raw_channel}][{layer}] must contain two values"
                    )
                copied[str(layer_number)] = [values[0], values[1]]
            result[str(channel)] = copied
        return result

    def _blank_current_statics(self) -> Dict[str, Dict[str, List[float]]]:
        statics = {
            channel: {layer: list(values) for layer, values in layers.items()}
            for channel, layers in self._statics_base.items()
        }
        zero_layers = {
            str(layer): [0.0, 0.0] for layer in sorted(self._current_layers)
        }
        for channel in ALL_CURRENT_CHANNELS:
            statics[channel] = {
                layer: list(values) for layer, values in zero_layers.items()
            }
        return statics

    @staticmethod
    def _polar(value: complex) -> List[float]:
        """Serialize an RMS current phasor as FPGA ``[peak, phase]``."""

        rms_amplitude = abs(value)
        if rms_amplitude <= 1e-12:
            return [0.0, 0.0]
        angle = ((math.degrees(cmath.phase(value)) + 180.0) % 360.0) - 180.0
        if abs(angle) <= 1e-12:
            angle = 0.0
        return [rms_amplitude * math.sqrt(2.0), angle]

    def _build_6i_statics(
        self, fundamental1, fundamental2, harmonic1=None, harmonic2=None
    ) -> Dict[str, Dict[str, List[float]]]:
        statics = self._blank_current_statics()
        for index, channel in enumerate(self._i1_channels):
            statics[channel]["1"] = self._polar(fundamental1[index])
            if harmonic1 is not None:
                statics[channel][str(self._harmonic_order)] = self._polar(harmonic1[index])
        for index, channel in enumerate(self._i2_channels):
            statics[channel]["1"] = self._polar(fundamental2[index])
            if harmonic2 is not None:
                statics[channel][str(self._harmonic_order)] = self._polar(harmonic2[index])
        return statics

    def _build_2i_statics(
        self,
        fundamental1: complex,
        fundamental2: complex,
        harmonic1: Optional[complex] = None,
        harmonic2: Optional[complex] = None,
    ) -> Dict[str, Dict[str, List[float]]]:
        statics = self._blank_current_statics()
        for channel in self._i1_channels:
            statics[channel]["1"] = self._polar(
                fundamental1 / len(self._i1_channels)
            )
            if harmonic1 is not None:
                statics[channel][str(self._harmonic_order)] = self._polar(
                    harmonic1 / len(self._i1_channels)
                )
        for channel in self._i2_channels:
            statics[channel]["1"] = self._polar(
                fundamental2 / len(self._i2_channels)
            )
            if harmonic2 is not None:
                statics[channel][str(self._harmonic_order)] = self._polar(
                    harmonic2 / len(self._i2_channels)
                )
        return statics

    def _build_idle_statics(self) -> Dict[str, Dict[str, List[float]]]:
        return self._blank_current_statics()

    # -------------------------------------------------------------- node layout

    def _node_from_statics(self, statics) -> ApiNodeData:
        node = ApiNodeData(mode=1)
        node.base = self.physDictToReg(statics)
        return node

    def _build_prefault_node(self, statics) -> ApiNodeData:
        node = self._node_from_statics(statics)
        node.timeoutMs = self._pre_fault_duration_ms
        node.timeoutId = 2
        return node

    def _build_fault_node(self, statics) -> ApiNodeData:
        node = self._node_from_statics(statics)
        if self._logic_mask & 0xFF:
            node.diMatchMask = self._logic_mask
            node.diMatchId = 3
        node.timeoutMs = self._fault_duration_ms
        node.timeoutId = 3
        return node

    def _build_idle_node(self) -> ApiNodeData:
        return self._node_from_statics(self._build_idle_statics())

    # -------------------------------------------------------------- calculation

    def _prepare_ratio_point(self, ir_value: Any, id_value: Any) -> _PreparedRatioPoint:
        if self._connection_type == "ext-differential":
            solution = calculate_ratio_6i(
                self._payload,
                ir_value=ir_value,
                id_value=id_value,
                test_phase=self._test_phase,
                diff_mode=self._diff_mode,
                load_current=self._load_current,
                simulate_prefault=self._simulate_prefault,
            )
            prefault = self._build_6i_statics(solution.prefault1, solution.prefault2)
            fault = self._build_6i_statics(solution.fault1, solution.fault2)
            logger.debug(
                "Differential profile: M%d/M%d, Kp=%g/%g",
                solution.profile.matrix1,
                solution.profile.matrix2,
                solution.profile.kp1,
                solution.profile.kp2,
            )
        else:
            solution = calculate_ratio_2i(
                self._payload,
                ir_value=ir_value,
                id_value=id_value,
                diff_mode=self._diff_mode,
                load_current=self._load_current,
                simulate_prefault=self._simulate_prefault,
            )
            prefault = self._build_2i_statics(solution.prefault1, solution.prefault2)
            fault = self._build_2i_statics(solution.fault1, solution.fault2)

        # These are the compensated-domain scalars used by the Id/Ir formula,
        # not fixed Ia/Ix channels (which are meaningless for B/C/multi-phase).
        return _PreparedRatioPoint(
            prefault,
            fault,
            abs(solution.scalar.winding1),
            abs(solution.scalar.winding2),
        )

    def _prepare_harmonic_point(self, harmonic_value: Any, fundamental_id: Any):
        apply_side = str(self._harmonic_cfg.get("applySide", "i1"))
        if self._connection_type == "ext-differential":
            solution = calculate_harmonic_6i(
                self._payload,
                fundamental_id=fundamental_id,
                harmonic_value=harmonic_value,
                test_phase=self._test_phase,
                apply_side=apply_side,
            )
            return self._build_6i_statics(
                solution.fundamental1,
                solution.fundamental2,
                solution.harmonic1,
                solution.harmonic2,
            )
        solution = calculate_harmonic_2i(
            self._payload,
            fundamental_id=fundamental_id,
            harmonic_value=harmonic_value,
            apply_side=apply_side,
        )
        return self._build_2i_statics(
            solution.fundamental1,
            solution.fundamental2,
            solution.harmonic1,
            solution.harmonic2,
        )

    # --------------------------------------------------------------- point FSM

    async def _execute_statics(self, prefault_statics, fault_statics) -> bool:
        if not self.isActive:
            raise _ExperimentStopped
        nodes = {2: self._build_fault_node(fault_statics)}
        if self._simulate_prefault:
            nodes[1] = self._build_prefault_node(prefault_statics)
        if self.ctrl.upsertNodes(nodes) is False:
            raise DifferentialMathError("failed to upload differential nodes")

        await asyncio.sleep(0.05)
        if not self.isActive:
            raise _ExperimentStopped
        self._di_tripped = False
        self._current_node = 0
        self._node_entry_di = int(getattr(self.ctrl, "di", 0)) & 0xFF
        if not self._di_reference_latched:
            # USEEngine captures diStart when the first manual trigger leaves
            # reserved Node 0.  Latch the same value once, immediately before
            # that trigger, then retain it for all subsequent points.
            self._di_reference = self._node_entry_di
            self._di_reference_latched = True
        assert self._node_done is not None
        self._node_done.clear()
        start_node = 1 if self._simulate_prefault else 2
        if self.ctrl.trigNode(start_node) is False:
            raise DifferentialMathError("failed to trigger differential node")

        total_ms = self._fault_duration_ms
        if self._simulate_prefault:
            total_ms += self._pre_fault_duration_ms
        wait_seconds = max(30.0, total_ms / 1000.0 + 5.0)
        try:
            await asyncio.wait_for(self._node_done.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError as exc:
            raise DifferentialMathError(
                f"node sequence did not reach Node 3 within {wait_seconds:g}s"
            ) from exc
        if not self.isActive:
            raise _ExperimentStopped
        return self._di_tripped

    async def _test_ratio_point(self, ir_value: Any, id_value: Any) -> _PointExecution:
        prepared = self._prepare_ratio_point(ir_value, id_value)
        tripped = await self._execute_statics(
            prepared.prefault_statics, prepared.fault_statics
        )
        return _PointExecution(
            tripped,
            prepared.fault_statics,
            prepared.report_i1,
            prepared.report_i2,
        )

    async def _test_harmonic_point(
        self, harmonic_value: Any, fundamental_id: Any
    ) -> _PointExecution:
        fault_statics = self._prepare_harmonic_point(harmonic_value, fundamental_id)
        tripped = await self._execute_statics(self._build_idle_statics(), fault_statics)
        return _PointExecution(tripped, fault_statics)

    # --------------------------------------------------------------- run modes

    async def _run_guarded(self):
        try:
            await self._run()
        except (_ExperimentStopped, asyncio.CancelledError):
            return
        except (DifferentialMathError, TypeError, ValueError) as exc:
            logger.error("Differential experiment rejected: %s", exc)
            if self.isActive:
                self.ctrl.stopTest(f"Differential test error: {exc}")
        except Exception as exc:
            logger.exception("Unexpected differential experiment error")
            if self.isActive:
                self.ctrl.stopTest(f"Differential test internal error: {exc}")

    async def _run(self):
        await asyncio.sleep(0.5)
        if not self.isActive:
            raise _ExperimentStopped
        self._fsm_state = "RUNNING"
        if self.ctrl.upsertNodes({3: self._build_idle_node()}) is False:
            raise DifferentialMathError("failed to upload differential idle node")
        await asyncio.sleep(0.05)
        if not self.isActive:
            raise _ExperimentStopped

        if self._active_project == "ratio-fixed":
            await self._run_ratio_fixed()
        elif self._active_project == "ratio-search":
            await self._run_ratio_search()
        elif self._active_project == "harmonic-fixed":
            await self._run_harmonic_fixed()
        else:
            await self._run_harmonic_search()
        if self.isActive:
            self._fsm_state = "DONE"
            self.ctrl.stopTest()

    @staticmethod
    def _point_id(point: Dict[str, Any]) -> Any:
        return point.get("id", 0)

    def _send_value_update(
        self, point_id: Any, x_value: float, y_value: float,
        execution: _PointExecution,
    ) -> None:
        self.ctrl._send({
            "type": "value_update",
            "id": point_id,
            "x": round(x_value, 6),
            "y": round(y_value, 6),
            "static": execution.fault_statics,
            "di": self.ctrl.di,
            "do": self.ctrl.do,
        })

    def _send_ratio_report(
        self, point_id: Any, ir_value: float, id_value: float,
        execution: _PointExecution,
    ) -> None:
        self.ctrl.sendReport({
            "points": [{
                "id": point_id,
                "x": round(ir_value, 6),
                "y": round(id_value, 6),
                "i1": round(execution.report_i1, 6),
                "i2": round(execution.report_i2, 6),
            }]
        })

    async def _run_ratio_fixed(self):
        for point in self._points:
            if not self.isActive:
                raise _ExperimentStopped
            point_id = self._point_id(point)
            ir_value = self._as_number(
                point.get("x", 0.0), "point.x", nonnegative=True
            )
            id_value = self._as_number(
                point.get("y", 0.0), "point.y", nonnegative=True
            )
            logger.info(
                "Differential fixed point %s: Ir=%g, Id=%g",
                point_id, ir_value, id_value,
            )
            execution = await self._test_ratio_point(ir_value, id_value)
            self._send_value_update(point_id, ir_value, id_value, execution)
            if execution.tripped:
                self._send_ratio_report(point_id, ir_value, id_value, execution)

    def _search_settings(self) -> tuple[float, str]:
        nested = self._project_data.get("search", {})
        if not isinstance(nested, dict):
            nested = {}
        precision = self._as_number(
            self._project_data.get("precision", nested.get("precision", 0.01)),
            "search precision",
        )
        if precision <= 0.0:
            raise DifferentialMathError("search precision must be greater than zero")
        method = str(
            self._project_data.get("method", nested.get("method", "binary"))
        )
        if method not in {"binary", "linear"}:
            raise DifferentialMathError(f"unsupported search method: {method}")
        return precision, method

    async def _search_boundary(
        self,
        start: float,
        end: float,
        precision: float,
        method: str,
        test: Callable[[float], Awaitable[_PointExecution]],
    ) -> Optional[tuple[float, _PointExecution]]:
        if start == end:
            raise DifferentialMathError("search interval start and end must differ")
        start_result = await test(start)
        end_result = await test(end)
        if start_result.tripped == end_result.tripped:
            return None

        if method == "linear":
            distance = abs(end - start)
            steps = max(1, math.ceil(distance / precision))
            for index in range(1, steps + 1):
                value = start + (end - start) * index / steps
                result = end_result if index == steps else await test(value)
                if result.tripped != start_result.tripped:
                    return value, result
            return None

        low_value, high_value = start, end
        low_result, high_result = start_result, end_result
        while abs(high_value - low_value) > precision:
            middle = (low_value + high_value) / 2.0
            middle_result = await test(middle)
            if middle_result.tripped == low_result.tripped:
                low_value, low_result = middle, middle_result
            else:
                high_value, high_result = middle, middle_result
        return high_value, high_result

    async def _run_ratio_search(self):
        precision, method = self._search_settings()
        for point in self._points:
            if not self.isActive:
                raise _ExperimentStopped
            point_id = self._point_id(point)
            x_value, y_value = point.get("x"), point.get("y")
            if isinstance(y_value, list) and len(y_value) >= 2:
                fixed_ir = self._as_number(x_value, "point.x", nonnegative=True)
                start = self._as_number(y_value[0], "point.y[0]", nonnegative=True)
                end = self._as_number(y_value[1], "point.y[1]", nonnegative=True)

                async def test(value, ir=fixed_ir, pid=point_id):
                    result = await self._test_ratio_point(ir, value)
                    self._send_value_update(pid, ir, value, result)
                    return result

                boundary = await self._search_boundary(
                    start, end, precision, method, test
                )
                if boundary is not None:
                    result_value, execution = boundary
                    self._send_ratio_report(
                        point_id, fixed_ir, result_value, execution
                    )
                else:
                    logger.warning(
                        "Differential search %s has equal endpoint states", point_id
                    )
            elif isinstance(x_value, list) and len(x_value) >= 2:
                fixed_id = self._as_number(y_value, "point.y", nonnegative=True)
                start = self._as_number(x_value[0], "point.x[0]", nonnegative=True)
                end = self._as_number(x_value[1], "point.x[1]", nonnegative=True)

                async def test(value, id_target=fixed_id, pid=point_id):
                    result = await self._test_ratio_point(value, id_target)
                    self._send_value_update(pid, value, id_target, result)
                    return result

                boundary = await self._search_boundary(
                    start, end, precision, method, test
                )
                if boundary is not None:
                    result_value, execution = boundary
                    self._send_ratio_report(
                        point_id, result_value, fixed_id, execution
                    )
                else:
                    logger.warning(
                        "Differential search %s has equal endpoint states", point_id
                    )
            else:
                raise DifferentialMathError(
                    f"search point {point_id} has no two-value interval"
                )

    async def _run_harmonic_fixed(self):
        for point in self._points:
            if not self.isActive:
                raise _ExperimentStopped
            point_id = self._point_id(point)
            harmonic = self._as_number(
                point.get("x", 0.0), "point.x", nonnegative=True
            )
            fundamental = self._as_number(
                point.get("y", 0.0), "point.y", nonnegative=True
            )
            execution = await self._test_harmonic_point(harmonic, fundamental)
            self._send_value_update(point_id, harmonic, fundamental, execution)
            self.ctrl.sendReport({
                "points": [{
                    "id": point_id,
                    "x": round(harmonic, 6),
                    "y": round(fundamental, 6),
                    "blocked": not execution.tripped,
                }]
            })

    async def _run_harmonic_search(self):
        precision, method = self._search_settings()
        for point in self._points:
            if not self.isActive:
                raise _ExperimentStopped
            point_id = self._point_id(point)
            fundamental = self._as_number(
                point.get("y"), "point.y", nonnegative=True
            )
            interval = point.get("x")
            if not isinstance(interval, list) or len(interval) < 2:
                raise DifferentialMathError(
                    f"harmonic search point {point_id} has no x interval"
                )
            start = self._as_number(interval[0], "point.x[0]", nonnegative=True)
            end = self._as_number(interval[1], "point.x[1]", nonnegative=True)

            async def test(value, fundamental_id=fundamental, pid=point_id):
                result = await self._test_harmonic_point(value, fundamental_id)
                self._send_value_update(pid, value, fundamental_id, result)
                return result

            boundary = await self._search_boundary(
                start, end, precision, method, test
            )
            if boundary is None:
                logger.warning(
                    "Harmonic search %s has equal endpoint states", point_id
                )
                continue
            threshold, execution = boundary
            self.ctrl.sendReport({
                "points": [{
                    "id": point_id,
                    "x": round(threshold, 6),
                    "y": round(fundamental, 6),
                    "blocked": not execution.tripped,
                }]
            })

    # ---------------------------------------------------------- engine callbacks

    def _di_condition_matches(self, di_value: int) -> bool:
        mask = self._logic_mask & 0xFF
        if not mask:
            return False
        reference = (
            self._node_entry_di
            if self._logic_mask & 0x200
            else self._di_reference
        )
        if self._logic_mask & 0x400:
            reference ^= 0xFF
        changed = (di_value ^ reference) & mask
        return changed == mask if self._logic_mask & 0x100 else bool(changed)

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        del hw_ts
        self._current_node = nodeId
        if nodeId == 2 and tick == 0:
            self._node_entry_di = int(getattr(self.ctrl, "di", 0)) & 0xFF
        if nodeId == 3 and tick == 0 and self._node_done is not None:
            # A condition already true at Node-2 entry need not emit a new DI
            # callback, but it still causes the hardware transition to Node 3.
            if self._di_condition_matches(int(getattr(self.ctrl, "di", 0)) & 0xFF):
                self._di_tripped = True
            self._node_done.set()

    def onDi(self, di: int, hw_ts: int):
        del hw_ts
        if self._fsm_state != "RUNNING" or self._current_node != 2:
            return
        if self._di_condition_matches(di & 0xFF):
            self._di_tripped = True

    def _onStop(self):
        self._fsm_state = "IDLE"
        if self._node_done is not None:
            self._node_done.set()
        logger.info("Differential test stopped")

    def onWebCommand(self, msg: Dict[str, Any]):
        cmd = msg.get("cmd")
        if cmd == "stop":
            self.ctrl.stopTest()
        elif cmd == "update_static":
            update = self._copy_voltage_statics(msg.get("static", {}))
            for channel, layers in update.items():
                self._statics_base.setdefault(channel, {}).update(layers)


__all__ = (
    "ApiDiffTest",
    "ALL_CURRENT_CHANNELS",
    "TERMINAL_6I",
    "TERMINAL_2I",
)
