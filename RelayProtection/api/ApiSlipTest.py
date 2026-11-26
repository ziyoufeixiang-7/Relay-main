"""Slip-test experiment API.

The frontend describes a physical slip target and one of five experiment
types.  This module validates that description, expands it into a small
ApiNodeData graph, and keeps all search/report state in the API layer.

Reserved engine node IDs 0x0000 and 0xFFFF are never overwritten.
"""

import asyncio
import copy
import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from api.BaseApi import ApiNodeData, BaseApi
from logic.FPGACodec import HWConfig


logger = logging.getLogger("ApiSlipTest")

SLIP_INTERVAL_MS = 10
SQRT2 = math.sqrt(2.0)
SQRT3 = math.sqrt(3.0)

RESET_ID = 1
SWEEP_ID = 2
WAIT_ID = 3
ACTION_LATCH_ID = 4
ROUND_DONE_ID = 5

U32_MASK = 0xFFFFFFFF
U32_HALF = 0x80000000
TIMER_SAFETY_MARGIN_US = 1_000_000
MAX_NODE_TIMEOUT_MS = (
    U32_HALF - TIMER_SAFETY_MARGIN_US - 1
) // 1000

# These caps prevent a malformed request from allocating an unbounded number
# of compiled FPGA frames or search rounds.
MAX_SWEEP_STEPS = 100_000
MAX_SEARCH_ROUNDS = 10_000

_LINE_VOLTAGE_CHANNELS = {
    0: (0, 1),              # Uab
    1: (1, 2),              # Ubc
    2: (2, 0),              # Uca
    3: (3, 4),              # Uxy
    4: (4, 5),              # Uyz
    5: (5, 3),              # Uzx
    6: (0, 1, 2),           # Uabc
    7: (3, 4, 5),           # Uxyz
    8: (0, 1, 2, 3, 4, 5), # Uabc, Uxyz
}


class _ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class _SlipTarget:
    var_type: str
    phasor: int
    channels: Tuple[int, ...]
    layer: int
    component_index: int
    value_scale: float

    @property
    def is_amplitude(self) -> bool:
        return self.phasor == 0

    @property
    def is_frequency(self) -> bool:
        return self.phasor == 2


@dataclass(frozen=True)
class _LockTarget:
    var_type: str
    channels: Tuple[int, ...]
    value_scale: float


@dataclass(frozen=True)
class _SweepPlan:
    endpoint: float
    ddt: float
    step: float
    count: int

    @property
    def direct_wait(self) -> bool:
        return self.count == 0


@dataclass(frozen=True)
class _RoundPlan:
    sweep: _SweepPlan
    report_value: Optional[float]
    lock_value: Optional[float]
    timer_tick: Optional[int]


@dataclass(frozen=True)
class _Config:
    test_type: int
    statics: Dict[str, Dict[str, List[float]]]
    target: _SlipTarget
    lock_target: Optional[_LockTarget]
    slip_start: float
    fixed_endpoint: Optional[float]
    slip_ddt: float
    search_values: Tuple[float, ...]
    timer_start_value: Optional[float]
    return_time_ms: int
    wait_time_ms: int
    do_mask: int
    do_ctrl_mask: int
    logic_mask: int


class ApiSlipTest(BaseApi):
    MODULE_KEYS = ["slip_test"]

    def __init__(self):
        super().__init__()
        self._reset_runtime_state()

    # ------------------------------------------------------------------
    # Setup and validation
    # ------------------------------------------------------------------

    def _reset_runtime_state(self):
        old_task = getattr(self, "_action_task", None)
        if old_task is not None and not old_task.done():
            old_task.cancel()

        self._config: Optional[_Config] = None
        self._round_index = 0
        self._current_round: Optional[_RoundPlan] = None
        self._current_node_id: Optional[int] = None

        self._last_di = 0
        self._recent_di_events: Deque[int] = deque(maxlen=32)
        self._di_event = asyncio.Event()

        self._timer_start_hw_ts: Optional[int] = None
        self._timer_event = asyncio.Event()
        self._monitor_arm_hw_ts: Optional[int] = None
        self._monitor_entry_hw_ts: Optional[int] = None
        self._monitor_node_id: Optional[int] = None
        self._transition_action_hw_ts: Optional[int] = None
        self._action_entry_hw_ts: Optional[int] = None
        self._action_task: Optional[asyncio.Task] = None

        self._setup_complete = False
        self._finishing = False
        self._report_sent = False
        self._round_transitioning = False

    def _onSetup(self, params: Dict[str, Any]):
        self._reset_runtime_state()
        try:
            if self.ctrl is None or not getattr(self.ctrl, "running", False):
                self._finishing = True
                return

            self._config = self._parse_config(params)
            self._last_di = int(self.ctrl.di) & 0xFF
            self._round_index = 0

            nodes = self._prepare_round(self._round_index)
            if not self.ctrl.upsertNodes(nodes):
                self._finishing = True
                return
            if not self.ctrl.trigNode(RESET_ID):
                self._stop_with_error(
                    "slip_test: failed to enter reset node"
                )
                return
            self._setup_complete = True
        except _ValidationError as exc:
            self._stop_with_error(
                f"Invalid slip_test parameter: {exc}"
            )
        except Exception as exc:
            logger.exception("Failed to set up slip_test")
            self._stop_with_error(
                f"Failed to set up slip_test: {exc}"
            )

    def _parse_config(self, params: Any) -> _Config:
        if not isinstance(params, dict):
            raise _ValidationError("params must be an object")

        raw_sys = params.get("sys", {})
        if not isinstance(raw_sys, dict):
            raise _ValidationError("params.sys must be an object")

        do_mask = self._optional_mask(
            raw_sys, "doMask", "params.sys.doMask", 0
        )
        if "doCtrlMask" in raw_sys:
            do_ctrl_mask = self._require_int(
                raw_sys["doCtrlMask"],
                "params.sys.doCtrlMask",
                minimum=0,
                maximum=0xFF,
            )
        else:
            do_ctrl_mask = 0
        logic_mask = self._optional_mask(
            raw_sys, "logicMask", "params.sys.logicMask", 0
        )

        payload = params.get("payload")
        if not isinstance(payload, dict):
            raise _ValidationError("params.payload must be an object")

        test_type = self._require_int(
            payload.get("testType"),
            "params.payload.testType",
            minimum=0,
            maximum=4,
        )
        target = self._parse_main_target(payload, test_type)
        lock_target = self._parse_lock_target(payload, test_type)

        statics = self._validate_physical_map(
            params.get("statics"),
            "params.statics",
        )
        self._require_target_layers(
            statics, target.channels, target.layer, "main slip target"
        )
        if lock_target is not None:
            self._require_target_layers(
                statics,
                lock_target.channels,
                1,
                "lock target",
            )

        slip_start = self._require_number(
            payload.get("slipStart"),
            "params.payload.slipStart",
        )
        slip_ddt = self._require_number(
            payload.get("slipDDt"),
            "params.payload.slipDDt",
        )
        return_time_ms = self._require_int(
            payload.get("returnTime"),
            "params.payload.returnTime",
            minimum=1,
            maximum=MAX_NODE_TIMEOUT_MS,
        )
        wait_time_ms = self._require_int(
            payload.get("waitTime"),
            "params.payload.waitTime",
            minimum=1,
            maximum=MAX_NODE_TIMEOUT_MS,
        )

        if (target.is_amplitude or target.is_frequency) and slip_start < 0:
            raise _ValidationError(
                "params.payload.slipStart must be non-negative "
                "for amplitude/frequency slip"
            )

        fixed_endpoint: Optional[float] = None
        search_values: Tuple[float, ...]
        timer_start_value: Optional[float] = None

        if test_type == 0:
            search_values = self._build_search_values(
                payload,
                "actionSearchStart",
                "actionSearchEnd",
                "actionSearchStep",
            )
        else:
            fixed_endpoint = self._require_number(
                payload.get("slipEnd"),
                "params.payload.slipEnd",
            )
            if (
                (target.is_amplitude or target.is_frequency)
                and fixed_endpoint < 0
            ):
                raise _ValidationError(
                    "params.payload.slipEnd must be non-negative "
                    "for amplitude/frequency slip"
                )

            if test_type == 1:
                search_values = (0.0,)
                timer_start_value = self._require_number(
                    payload.get("timerStartValue"),
                    "params.payload.timerStartValue",
                )
                low = min(slip_start, fixed_endpoint)
                high = max(slip_start, fixed_endpoint)
                tolerance = self._value_tolerance(
                    slip_start, fixed_endpoint, timer_start_value
                )
                if not (
                    low - tolerance
                    <= timer_start_value
                    <= high + tolerance
                ):
                    raise _ValidationError(
                        "params.payload.timerStartValue must be "
                        "between slipStart and slipEnd"
                    )
            elif test_type == 2:
                search_values = self._build_search_values(
                    payload,
                    "ddtSearchStart",
                    "ddtSearchEnd",
                    "ddtSearchStep",
                )
                if any(value <= 0 for value in search_values):
                    raise _ValidationError(
                        "all ddtSearch values must be greater than 0"
                    )
            else:
                search_values = self._build_search_values(
                    payload,
                    "lockSearchStart",
                    "lockSearchEnd",
                    "lockSearchStep",
                )
                if any(value < 0 for value in search_values):
                    raise _ValidationError(
                        "all lockSearch values must be non-negative"
                    )

        config = _Config(
            test_type=test_type,
            statics=statics,
            target=target,
            lock_target=lock_target,
            slip_start=slip_start,
            fixed_endpoint=fixed_endpoint,
            slip_ddt=slip_ddt,
            search_values=search_values,
            timer_start_value=timer_start_value,
            return_time_ms=return_time_ms,
            wait_time_ms=wait_time_ms,
            do_mask=do_mask,
            do_ctrl_mask=do_ctrl_mask,
            logic_mask=logic_mask,
        )

        # Validate every search round before any waveform is sent.  This
        # includes physical output ranges and register-domain step
        # quantization, not only the first round.
        for index in range(len(search_values)):
            round_plan = self._build_round_plan(config, index)
            self._validate_round_outputs(
                config, round_plan, index
            )
        return config

    def _parse_main_target(
        self,
        payload: Dict[str, Any],
        test_type: int,
    ) -> _SlipTarget:
        var_type = payload.get("slipVarType")
        if var_type not in ("0_0", "0_1", "2_0", "5"):
            raise _ValidationError(
                "params.payload.slipVarType must be one of "
                "'0_0', '0_1', '2_0', '5'"
            )

        if test_type == 3 and var_type == "0_1":
            raise _ValidationError(
                "testType 3 does not allow phase-current slip"
            )
        if test_type == 4 and var_type != "5":
            raise _ValidationError(
                "testType 4 requires slipVarType '5'"
            )

        if var_type == "5":
            return _SlipTarget(
                var_type=var_type,
                phasor=2,
                channels=tuple(range(12)),
                layer=0,
                component_index=1,
                value_scale=1.0,
            )

        phasor = self._require_int(
            payload.get("slipPhasor"),
            "params.payload.slipPhasor",
            minimum=0,
            maximum=2,
        )

        if var_type == "0_0":
            channels = self._parse_channel_list(
                payload.get("slipPhase"),
                "params.payload.slipPhase",
                minimum=0,
                maximum=5,
            )
        elif var_type == "0_1":
            channels = self._parse_channel_list(
                payload.get("slipPhase"),
                "params.payload.slipPhase",
                minimum=6,
                maximum=11,
            )
        else:
            selector = self._parse_single_line_selector(
                payload.get("slipPhase"),
                "params.payload.slipPhase",
            )
            channels = _LINE_VOLTAGE_CHANNELS[selector]

        if phasor == 0:
            layer, component_index = 1, 0
        elif phasor == 1:
            layer, component_index = 1, 1
        else:
            layer, component_index = 0, 1

        return _SlipTarget(
            var_type=var_type,
            phasor=phasor,
            channels=channels,
            layer=layer,
            component_index=component_index,
            value_scale=(
                1.0 / SQRT3
                if var_type == "2_0" and phasor == 0
                else 1.0
            ),
        )

    def _parse_lock_target(
        self,
        payload: Dict[str, Any],
        test_type: int,
    ) -> Optional[_LockTarget]:
        if test_type == 3:
            channels = self._parse_channel_list(
                payload.get("lockPhase"),
                "params.payload.lockPhase",
                minimum=6,
                maximum=11,
            )
            return _LockTarget(
                var_type="0_1",
                channels=channels,
                value_scale=1.0,
            )

        if test_type != 4:
            return None

        var_type = payload.get("lockVarType")
        if var_type == "0_0":
            channels = self._parse_channel_list(
                payload.get("lockPhase"),
                "params.payload.lockPhase",
                minimum=0,
                maximum=5,
            )
            scale = 1.0
        elif var_type == "2_0":
            selector = self._parse_single_line_selector(
                payload.get("lockPhase"),
                "params.payload.lockPhase",
            )
            channels = _LINE_VOLTAGE_CHANNELS[selector]
            scale = 1.0 / SQRT3
        else:
            raise _ValidationError(
                "testType 4 lockVarType must be '0_0' or '2_0'"
            )

        return _LockTarget(
            var_type=var_type,
            channels=channels,
            value_scale=scale,
        )

    def _validate_physical_map(
        self,
        value: Any,
        path: str,
    ) -> Dict[str, Dict[str, List[float]]]:
        if not isinstance(value, dict):
            raise _ValidationError(f"{path} must be an object")

        result: Dict[str, Dict[str, List[float]]] = {}
        for raw_channel, raw_layers in value.items():
            channel = self._canonical_dict_index(
                raw_channel,
                f"{path} channel",
                minimum=0,
                maximum=11,
            )
            if not isinstance(raw_layers, dict):
                raise _ValidationError(
                    f"{path}.{channel} must be an object"
                )

            channel_key = str(channel)
            result[channel_key] = {}
            for raw_layer, raw_pair in raw_layers.items():
                layer = self._canonical_dict_index(
                    raw_layer,
                    f"{path}.{channel} layer",
                    minimum=0,
                    maximum=64,
                )
                if (
                    not isinstance(raw_pair, (list, tuple))
                    or len(raw_pair) != 2
                ):
                    raise _ValidationError(
                        f"{path}.{channel}.{layer} must be "
                        "[amplitude, frequency_or_phase]"
                    )

                pair = [
                    self._require_number(
                        raw_pair[index],
                        f"{path}.{channel}.{layer}[{index}]",
                    )
                    for index in range(2)
                ]
                result[channel_key][str(layer)] = pair
        self._validate_output_bounds(result, path)
        return result

    @staticmethod
    def _validate_output_bounds(
        physical_map: Dict[str, Dict[str, List[float]]],
        path: str,
        *,
        is_delta: bool = False,
    ):
        for channel_key, layers in physical_map.items():
            channel = int(channel_key)
            max_amplitude = (
                HWConfig.MAX_VOLTAGE
                if channel < 6
                else HWConfig.MAX_CURRENT
            )
            for layer_key, pair in layers.items():
                layer = int(layer_key)
                amplitude, frequency_or_phase = pair
                if abs(amplitude) > max_amplitude:
                    unit = "V" if channel < 6 else "A"
                    raise _ValidationError(
                        f"{path}.{channel}.{layer}[0] exceeds "
                        f"{max_amplitude:g} {unit}"
                    )
                if layer == 0:
                    if is_delta:
                        valid_frequency = (
                            abs(frequency_or_phase)
                            <= HWConfig.MAX_FREQ
                        )
                    else:
                        valid_frequency = (
                            0
                            <= frequency_or_phase
                            <= HWConfig.MAX_FREQ
                        )
                    if not valid_frequency:
                        qualifier = "magnitude " if is_delta else ""
                        raise _ValidationError(
                            f"{path}.{channel}.0[1] frequency "
                            f"{qualifier}exceeds hardware range"
                        )

    @staticmethod
    def _require_target_layers(
        statics: Dict[str, Dict[str, List[float]]],
        channels: Tuple[int, ...],
        layer: int,
        target_name: str,
    ):
        layer_key = str(layer)
        missing = [
            channel
            for channel in channels
            if (
                str(channel) not in statics
                or layer_key not in statics[str(channel)]
            )
        ]
        if missing:
            raise _ValidationError(
                f"{target_name} requires layer {layer} on channels "
                + ",".join(str(channel) for channel in missing)
            )

    def _build_search_values(
        self,
        payload: Dict[str, Any],
        start_key: str,
        end_key: str,
        step_key: str,
    ) -> Tuple[float, ...]:
        prefix = "params.payload."
        start = self._require_number(
            payload.get(start_key), prefix + start_key
        )
        end = self._require_number(
            payload.get(end_key), prefix + end_key
        )
        step = self._require_number(
            payload.get(step_key), prefix + step_key
        )
        if step == 0:
            raise _ValidationError(f"{prefix}{step_key} must not be 0")

        if self._values_close(start, end):
            return (end,)
        if (end > start) != (step > 0):
            raise _ValidationError(
                f"{prefix}{step_key} points away from {end_key}"
            )

        values: List[float] = []
        for index in range(MAX_SEARCH_ROUNDS):
            value = start + index * step
            reached = (
                value >= end
                if step > 0
                else value <= end
            )
            if reached or self._values_close(value, end):
                values.append(end)
                return tuple(values)
            values.append(value)

        raise _ValidationError(
            f"{prefix}{start_key}/{step_key}/{end_key} exceeds "
            f"{MAX_SEARCH_ROUNDS} search rounds"
        )

    # ------------------------------------------------------------------
    # Round and node compilation
    # ------------------------------------------------------------------

    def _build_round_plan(
        self,
        config: _Config,
        index: int,
    ) -> _RoundPlan:
        search_value = config.search_values[index]
        lock_value: Optional[float] = None

        if config.test_type == 0:
            endpoint = search_value
            round_ddt = config.slip_ddt
            report_value: Optional[float] = search_value
        elif config.test_type == 1:
            endpoint = self._fixed_endpoint(config)
            round_ddt = config.slip_ddt
            report_value = None
        elif config.test_type == 2:
            endpoint = self._fixed_endpoint(config)
            if (
                not self._values_close(
                    config.slip_start, endpoint
                )
                and config.slip_ddt == 0
            ):
                raise _ValidationError(
                    "slipDDt must provide a non-zero direction "
                    "for testType 2"
                )
            round_ddt = math.copysign(
                abs(search_value),
                config.slip_ddt,
            )
            report_value = search_value
        else:
            endpoint = self._fixed_endpoint(config)
            round_ddt = config.slip_ddt
            report_value = search_value
            lock_value = search_value

        if (
            (config.target.is_amplitude or config.target.is_frequency)
            and endpoint < 0
        ):
            raise _ValidationError(
                "a generated slip endpoint is negative"
            )

        sweep = self._build_sweep_plan(
            config.slip_start,
            endpoint,
            round_ddt,
        )

        timer_tick = None
        if config.test_type == 1:
            timer_value = config.timer_start_value
            if timer_value is None:
                raise _ValidationError(
                    "testType 1 requires timerStartValue"
                )
            if not sweep.direct_wait:
                timer_tick = self._nearest_tick(
                    config.slip_start,
                    sweep.step,
                    sweep.count,
                    timer_value,
                )
                remaining_ms = (
                    (sweep.count - timer_tick + 1)
                    * SLIP_INTERVAL_MS
                    + config.wait_time_ms
                )
            else:
                remaining_ms = config.wait_time_ms

            if (
                remaining_ms * 1000
                >= U32_HALF - TIMER_SAFETY_MARGIN_US
            ):
                raise _ValidationError(
                    "timerStartValue-to-action window is too long "
                    "for the 32-bit hardware timestamp"
                )

        return _RoundPlan(
            sweep=sweep,
            report_value=report_value,
            lock_value=lock_value,
            timer_tick=timer_tick,
        )

    def _validate_round_outputs(
        self,
        config: _Config,
        round_plan: _RoundPlan,
        index: int,
    ):
        reset_phys = self._apply_main_value(
            config.statics,
            config.target,
            config.slip_start,
        )
        endpoint_phys = self._apply_main_value(
            config.statics,
            config.target,
            round_plan.sweep.endpoint,
        )
        if (
            config.lock_target is not None
            and round_plan.lock_value is not None
        ):
            reset_phys = self._apply_lock_value(
                reset_phys,
                config.lock_target,
                round_plan.lock_value,
            )
            endpoint_phys = self._apply_lock_value(
                endpoint_phys,
                config.lock_target,
                round_plan.lock_value,
            )

        round_path = f"generated round {index + 1}"
        self._validate_output_bounds(
            reset_phys, f"{round_path} reset"
        )
        self._validate_output_bounds(
            endpoint_phys, f"{round_path} endpoint"
        )

        if round_plan.sweep.direct_wait:
            return

        step_phys = self._build_main_delta(
            config.target, round_plan.sweep.step
        )
        self._validate_output_bounds(
            step_phys,
            f"{round_path} step",
            is_delta=True,
        )
        step_reg = self.physDictToReg(
            step_phys, is_delta=True
        )
        if self._register_map_is_zero(step_reg):
            raise _ValidationError(
                f"{round_path} 10 ms slip step quantizes to zero"
            )

    def _build_sweep_plan(
        self,
        start: float,
        endpoint: float,
        ddt: float,
    ) -> _SweepPlan:
        if self._values_close(start, endpoint):
            return _SweepPlan(
                endpoint=endpoint,
                ddt=ddt,
                step=0.0,
                count=0,
            )

        if ddt == 0:
            raise _ValidationError(
                "slipDDt must not be 0 for a non-zero-distance sweep"
            )
        if (endpoint > start) != (ddt > 0):
            raise _ValidationError(
                "slipDDt direction does not point from slipStart "
                "to the round endpoint"
            )

        step = ddt * (SLIP_INTERVAL_MS / 1000.0)
        if not math.isfinite(step) or step == 0:
            raise _ValidationError(
                "slipDDt produces an invalid 10 ms step"
            )

        raw_count = abs((endpoint - start) / step)
        if not math.isfinite(raw_count):
            raise _ValidationError("sweep step count is not finite")

        nearest = round(raw_count)
        if math.isclose(
            raw_count,
            nearest,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            count = int(nearest)
        else:
            count = math.floor(raw_count)

        if count < 1:
            raise _ValidationError(
                "non-zero-distance sweep produces no complete 10 ms step"
            )
        if count > MAX_SWEEP_STEPS:
            raise _ValidationError(
                f"sweep exceeds {MAX_SWEEP_STEPS} steps"
            )

        return _SweepPlan(
            endpoint=endpoint,
            ddt=ddt,
            step=step,
            count=count,
        )

    @classmethod
    def _nearest_tick(
        cls,
        start: float,
        step: float,
        count: int,
        timer_value: float,
    ) -> int:
        position = (timer_value - start) / step
        lower = max(0, min(count, math.floor(position)))
        upper = max(0, min(count, math.ceil(position)))
        candidates = sorted({lower, upper, 0, count})
        tolerance = cls._value_tolerance(
            start,
            start + count * step,
            timer_value,
        )
        best_tick = candidates[0]
        best_distance = abs(
            (start + best_tick * step) - timer_value
        )
        for tick in candidates[1:]:
            distance = abs(
                (start + tick * step) - timer_value
            )
            if distance < best_distance - tolerance:
                best_tick = tick
                best_distance = distance
        return best_tick

    def _prepare_round(self, index: int) -> Dict[int, ApiNodeData]:
        if self._config is None:
            raise RuntimeError("slip_test config is not initialized")
        self._current_round = self._build_round_plan(
            self._config, index
        )
        self._current_node_id = None
        self._recent_di_events.clear()
        self._di_event = asyncio.Event()
        self._timer_start_hw_ts = None
        self._timer_event = asyncio.Event()
        self._monitor_arm_hw_ts = None
        self._monitor_entry_hw_ts = None
        self._monitor_node_id = None
        self._transition_action_hw_ts = None
        self._action_entry_hw_ts = None
        self._action_task = None
        return self._compile_round_nodes(
            self._config, self._current_round
        )

    def _compile_round_nodes(
        self,
        config: _Config,
        round_plan: _RoundPlan,
    ) -> Dict[int, ApiNodeData]:
        reset_phys = self._apply_main_value(
            config.statics,
            config.target,
            config.slip_start,
        )
        endpoint_phys = self._apply_main_value(
            config.statics,
            config.target,
            round_plan.sweep.endpoint,
        )

        if (
            config.lock_target is not None
            and round_plan.lock_value is not None
        ):
            reset_phys = self._apply_lock_value(
                reset_phys,
                config.lock_target,
                round_plan.lock_value,
            )
            endpoint_phys = self._apply_lock_value(
                endpoint_phys,
                config.lock_target,
                round_plan.lock_value,
            )

        reset_reg = self.physDictToReg(reset_phys)
        endpoint_target_phys = {
            str(channel): {
                str(config.target.layer): list(
                    endpoint_phys[str(channel)][
                        str(config.target.layer)
                    ]
                )
            }
            for channel in config.target.channels
        }
        endpoint_reg = self.physDictToReg(endpoint_target_phys)
        effective_logic_mask = (
            config.logic_mask if config.logic_mask else 0xFF
        )
        di_match_mask = effective_logic_mask | 0x200
        # doCtrlMask is a flip mask, not an absolute DO value.  RESET uses
        # doMask; both control nodes enter with the same XOR result at
        # delay=0 (the packed value therefore contains only the low 8 bits).
        control_do = config.do_mask ^ config.do_ctrl_mask

        nodes: Dict[int, ApiNodeData] = {
            RESET_ID: ApiNodeData(
                mode=1,
                base=reset_reg,
                timeoutMs=config.return_time_ms,
                timeoutId=(
                    WAIT_ID
                    if round_plan.sweep.direct_wait
                    else SWEEP_ID
                ),
                # [0] is intentional: zero means explicitly clear DO.
                doActions=[config.do_mask],
            ),
            WAIT_ID: ApiNodeData(
                mode=1,
                base=endpoint_reg,
                diMatchMask=di_match_mask,
                diMatchId=ACTION_LATCH_ID,
                timeoutMs=config.wait_time_ms,
                timeoutId=ROUND_DONE_ID,
                doActions=[control_do],
            ),
            ACTION_LATCH_ID: ApiNodeData(mode=1, base={}),
            ROUND_DONE_ID: ApiNodeData(mode=1, base={}),
        }

        if not round_plan.sweep.direct_wait:
            step_phys = self._build_main_delta(
                config.target,
                round_plan.sweep.step,
            )
            step_reg = self.physDictToReg(
                step_phys, is_delta=True
            )
            if self._register_map_is_zero(step_reg):
                raise _ValidationError(
                    "the 10 ms slip step quantizes to zero"
                )
            nodes[SWEEP_ID] = ApiNodeData(
                mode=2,
                interval=SLIP_INTERVAL_MS,
                # RESET already established the complete start state.  An
                # empty base keeps the DI-reference-to-tick0 window small
                # and EngineCtrl's telemetry state remains valid for steps.
                base={},
                steps=[step_reg] * round_plan.sweep.count,
                countOverId=WAIT_ID,
                diMatchMask=di_match_mask,
                diMatchId=ACTION_LATCH_ID,
                doActions=[control_do],
            )

        return nodes

    @staticmethod
    def _register_map_is_zero(
        register_map: Dict[int, Dict[int, List[int]]],
    ) -> bool:
        return not any(
            first != 0 or second != 0
            for layers in register_map.values()
            for first, second in layers.values()
        )

    @staticmethod
    def _apply_main_value(
        statics: Dict[str, Dict[str, List[float]]],
        target: _SlipTarget,
        logical_value: float,
    ) -> Dict[str, Dict[str, List[float]]]:
        result = copy.deepcopy(statics)
        physical_value = logical_value * target.value_scale
        layer_key = str(target.layer)
        for channel in target.channels:
            result[str(channel)][layer_key][
                target.component_index
            ] = physical_value
        return result

    @staticmethod
    def _build_main_delta(
        target: _SlipTarget,
        logical_delta: float,
    ) -> Dict[str, Dict[str, List[float]]]:
        physical_delta = logical_delta * target.value_scale
        pair = [0.0, 0.0]
        pair[target.component_index] = physical_delta
        layer_key = str(target.layer)
        return {
            str(channel): {layer_key: list(pair)}
            for channel in target.channels
        }

    @staticmethod
    def _apply_lock_value(
        statics: Dict[str, Dict[str, List[float]]],
        target: _LockTarget,
        logical_value: float,
    ) -> Dict[str, Dict[str, List[float]]]:
        result = copy.deepcopy(statics)
        physical_value = logical_value * target.value_scale
        for channel in target.channels:
            result[str(channel)]["1"][0] = physical_value
        return result

    # ------------------------------------------------------------------
    # Runtime callbacks
    # ------------------------------------------------------------------

    def onDi(self, di: int, hw_ts: int):
        if not self.isActive or self._finishing:
            return

        try:
            value = int(di) & 0xFF
            changed = value ^ self._last_di
            self._last_di = value

            config = self._config
            if config is None:
                return
            effective_mask = (
                config.logic_mask if config.logic_mask else 0xFF
            )
            if changed & effective_mask:
                self._recent_di_events.append(
                    int(hw_ts) & U32_MASK
                )
                self._di_event.set()
                self._try_schedule_transition_action()
        except Exception as exc:
            logger.exception("Failed to process slip_test DI")
            self._stop_with_error(
                f"slip_test DI processing failed: {exc}"
            )

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        if not self.isActive or self._finishing:
            return

        try:
            self._current_node_id = int(nodeId)
            raw_timestamp = int(hw_ts)
            if raw_timestamp < 0:
                # USEEngine returns -1 after an ACK timeout and has already
                # queued the reserved terminal node.  Never turn that error
                # marker into a valid u32 timestamp or advance the search.
                return
            timestamp = raw_timestamp & U32_MASK

            if nodeId == RESET_ID and tick == 0:
                self._round_transitioning = False
                if self._config is not None:
                    # USEEngine starts RESET's timeout immediately after
                    # this base+UPDATE ACK.  The derived hardware boundary
                    # separates RESET-only DI history from a legitimate
                    # action in the following node's pre-ACK window.
                    self._monitor_arm_hw_ts = (
                        timestamp
                        + self._config.return_time_ms * 1000
                    ) & U32_MASK

            if nodeId in (SWEEP_ID, WAIT_ID) and tick == 0:
                self._monitor_entry_hw_ts = timestamp
                self._monitor_node_id = int(nodeId)
                # A new Event prevents an earlier RESET callback from
                # satisfying a later action resolver.  Hardware timestamps
                # remain cached for continuous-phase ordering.
                self._di_event = asyncio.Event()

            if (
                self._config is not None
                and self._config.test_type == 1
                and self._current_round is not None
                and self._timer_start_hw_ts is None
            ):
                if (
                    not self._current_round.sweep.direct_wait
                    and nodeId == SWEEP_ID
                    and tick == self._current_round.timer_tick
                ):
                    self._record_timer_start(timestamp)
                elif (
                    self._current_round.sweep.direct_wait
                    and nodeId == WAIT_ID
                    and tick == 0
                ):
                    # With no Sweep ticks, the test interval starts when
                    # WAIT applies the same endpoint after returnTime.
                    self._record_timer_start(timestamp)

            # Retry after every relevant update.  DI and node callbacks are
            # independent tasks, so their arrival order is not assumed.
            self._try_schedule_transition_action()

            if nodeId == ACTION_LATCH_ID and tick == 0:
                self._schedule_action_resolution(timestamp)
            elif nodeId == ROUND_DONE_ID and tick == 0:
                self._advance_or_finish()
        except _ValidationError as exc:
            self._stop_with_error(
                f"Invalid slip_test runtime state: {exc}"
            )
        except Exception as exc:
            logger.exception("Failed to process slip_test update")
            self._stop_with_error(
                f"slip_test update processing failed: {exc}"
            )

    def _record_timer_start(self, timestamp: int):
        if self._timer_start_hw_ts is not None:
            return
        self._timer_start_hw_ts = timestamp & U32_MASK
        self._timer_event.set()

    def _schedule_action_resolution(self, entry_hw_ts: int):
        if self._action_task is not None:
            return
        self._action_entry_hw_ts = entry_hw_ts & U32_MASK
        self._action_task = asyncio.create_task(
            self._resolve_action()
        )

    async def _resolve_action(self):
        try:
            # DI and value-update callbacks are emitted as independent
            # asyncio tasks.  Yield twice so already queued callbacks can
            # publish their hardware timestamps before resolving the report.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            action_hw_ts = self._select_action_timestamp()
            if action_hw_ts is None:
                try:
                    await asyncio.wait_for(
                        self._di_event.wait(), timeout=0.05
                    )
                except asyncio.TimeoutError:
                    pass
                action_hw_ts = self._select_action_timestamp()

            config = self._config
            if (
                config is None
                or not self.isActive
                or self._finishing
            ):
                return

            if action_hw_ts is None:
                self._stop_with_error(
                    "slip_test action latch has no matching DI "
                    "hardware timestamp"
                )
                return

            action_time_ms: Optional[float] = None
            if config.test_type == 1:
                if self._timer_start_hw_ts is None:
                    try:
                        await asyncio.wait_for(
                            self._timer_event.wait(), timeout=0.05
                        )
                    except asyncio.TimeoutError:
                        pass

                if self._timer_start_hw_ts is not None:
                    elapsed_us = (
                        action_hw_ts - self._timer_start_hw_ts
                    ) & U32_MASK
                    if elapsed_us < U32_HALF:
                        action_time_ms = round(
                            elapsed_us / 1000.0, 1
                        )

            self._finish(
                action_detected=True,
                action_time_ms=action_time_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Failed to resolve slip_test action")
            self._stop_with_error(
                f"slip_test action processing failed: {exc}"
            )

    def _select_action_timestamp(self) -> Optional[int]:
        if (
            self._action_entry_hw_ts is None
            or self._monitor_entry_hw_ts is None
            or self._monitor_node_id not in (SWEEP_ID, WAIT_ID)
            or not self._recent_di_events
        ):
            return None

        if self._transition_action_hw_ts is not None:
            return self._transition_action_hw_ts

        monitor_start = (
            self._monitor_arm_hw_ts
            if self._monitor_arm_hw_ts is not None
            else self._monitor_entry_hw_ts
        )
        return self._find_action_between(
            monitor_start, self._action_entry_hw_ts
        )

    def _try_schedule_transition_action(self):
        if (
            self._finishing
            or self._current_round is None
            or self._monitor_entry_hw_ts is None
            or self._monitor_node_id not in (SWEEP_ID, WAIT_ID)
        ):
            return

        # Monitoring is one continuous physical-time interval beginning at
        # RESET timeout, including the short reference-capture interval
        # between adjacent engine nodes.
        start_hw_ts = (
            self._monitor_arm_hw_ts
            if self._monitor_arm_hw_ts is not None
            else self._monitor_entry_hw_ts
        )
        end_hw_ts = self._monitor_entry_hw_ts
        transition_ts = self._find_action_between(
            start_hw_ts, end_hw_ts
        )
        if transition_ts is None:
            return

        self._transition_action_hw_ts = transition_ts
        self._schedule_action_resolution(end_hw_ts)

    def _find_action_between(
        self, start_hw_ts: int, end_hw_ts: int
    ) -> Optional[int]:
        if not self._recent_di_events:
            return None

        candidates = []
        for timestamp in self._recent_di_events:
            after_start = (timestamp - start_hw_ts) & U32_MASK
            before_end = (end_hw_ts - timestamp) & U32_MASK
            if (
                after_start < U32_HALF
                and before_end < U32_HALF
            ):
                candidates.append(
                    (after_start, timestamp)
                )

        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])[1]

    def _advance_or_finish(self):
        if (
            self._finishing
            or self._config is None
            or self._round_transitioning
            or self._action_task is not None
        ):
            return
        self._round_transitioning = True

        next_index = self._round_index + 1
        if next_index >= len(self._config.search_values):
            self._finish(action_detected=False)
            return

        nodes = self._prepare_round(next_index)
        if not self.ctrl.upsertNodes(nodes):
            self._finishing = True
            return

        self._round_index = next_index
        if not self.ctrl.trigNode(RESET_ID):
            self._stop_with_error(
                "slip_test: failed to start the next search round"
            )

    # ------------------------------------------------------------------
    # Report and stop handling
    # ------------------------------------------------------------------

    def _finish(
        self,
        *,
        action_detected: bool,
        action_time_ms: Optional[float] = None,
    ):
        if self._finishing:
            return
        self._finishing = True

        report = self._build_report(
            action_detected=action_detected,
            action_time_ms=action_time_ms,
        )
        try:
            if not self._report_sent:
                self.ctrl.sendReport(report)
                self._report_sent = True
        finally:
            if getattr(self.ctrl, "running", False):
                self.ctrl.stopTest()

    def _build_report(
        self,
        *,
        action_detected: bool,
        action_time_ms: Optional[float],
    ) -> Dict[str, Any]:
        if self._config is None:
            raise RuntimeError("slip_test config is not initialized")
        test_type = self._config.test_type
        report: Dict[str, Any] = {"testType": test_type}

        result = (
            self._current_round.report_value
            if (
                action_detected
                and self._current_round is not None
            )
            else None
        )

        if test_type == 0:
            report["actionValue"] = result
        elif test_type == 1:
            report["timerStartValue"] = (
                self._config.timer_start_value
            )
            report["actionTime"] = (
                action_time_ms if action_detected else None
            )
        elif test_type == 2:
            # Temporary frontend compatibility: type2 amplitude searches
            # are configured in peak V/A units, but the current frontend
            # forgets to convert the report to RMS.  Keep all execution
            # values unchanged and convert only this report field.
            if (
                result is not None
                and self._config.target.is_amplitude
            ):
                result /= SQRT2
            report["slipLockValue"] = result
        elif test_type == 3:
            report["currentLockValue"] = result
        else:
            report["voltageLockValue"] = result
        return report

    def _stop_with_error(self, message: str):
        if self._finishing:
            return
        self._finishing = True
        logger.error(message)
        if self.ctrl is not None and getattr(
            self.ctrl, "running", False
        ):
            self.ctrl.stopTest(message)

    def _onStop(self):
        task = self._action_task
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()

        # Preserve the existing frontend contract for an explicit user stop:
        # an unfinished action-time test returns one null result.  Validation
        # and runtime-error stops set _finishing and do not fabricate a report.
        if (
            self._setup_complete
            and self._config is not None
            and self._config.test_type == 1
            and not self._report_sent
            and not self._finishing
        ):
            self.ctrl.sendReport(
                {
                    "testType": 1,
                    "timerStartValue": (
                        self._config.timer_start_value
                    ),
                    "actionTime": None,
                }
            )
            self._report_sent = True

    # ------------------------------------------------------------------
    # Small parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fixed_endpoint(config: _Config) -> float:
        if config.fixed_endpoint is None:
            raise _ValidationError(
                "this testType requires slipEnd"
            )
        return config.fixed_endpoint

    def _optional_mask(
        self,
        source: Dict[str, Any],
        key: str,
        path: str,
        default: int,
    ) -> int:
        if key not in source:
            return default
        return self._require_int(
            source[key], path, minimum=0, maximum=0xFF
        )

    @staticmethod
    def _require_number(value: Any, path: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise _ValidationError(
                f"{path} must be a finite number"
            )
        return float(value)

    @staticmethod
    def _require_int(
        value: Any,
        path: str,
        *,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _ValidationError(f"{path} must be an integer")
        if minimum is not None and value < minimum:
            raise _ValidationError(
                f"{path} must be >= {minimum}"
            )
        if maximum is not None and value > maximum:
            raise _ValidationError(
                f"{path} must be <= {maximum}"
            )
        return value

    def _parse_channel_list(
        self,
        value: Any,
        path: str,
        *,
        minimum: int,
        maximum: int,
    ) -> Tuple[int, ...]:
        if not isinstance(value, str) or not value:
            raise _ValidationError(
                f"{path} must be a comma-separated string"
            )

        result: List[int] = []
        seen = set()
        for raw_token in value.split(","):
            token = raw_token.strip()
            if not token or not token.isdigit():
                raise _ValidationError(
                    f"{path} contains a non-integer channel"
                )
            channel = int(token)
            if not minimum <= channel <= maximum:
                raise _ValidationError(
                    f"{path} channel {channel} is outside "
                    f"{minimum}..{maximum}"
                )
            if channel in seen:
                raise _ValidationError(
                    f"{path} contains duplicate channel {channel}"
                )
            seen.add(channel)
            result.append(channel)

        if not result:
            raise _ValidationError(f"{path} must not be empty")
        return tuple(result)

    def _parse_single_line_selector(
        self,
        value: Any,
        path: str,
    ) -> int:
        selectors = self._parse_channel_list(
            value, path, minimum=0, maximum=8
        )
        if len(selectors) != 1:
            raise _ValidationError(
                f"{path} must contain exactly one line-voltage "
                "selector"
            )
        return selectors[0]

    @staticmethod
    def _canonical_dict_index(
        value: Any,
        path: str,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        if not isinstance(value, str) or not value.isdigit():
            raise _ValidationError(
                f"{path} key must be a decimal string"
            )
        index = int(value)
        if str(index) != value:
            raise _ValidationError(
                f"{path} key must use canonical decimal form"
            )
        if not minimum <= index <= maximum:
            raise _ValidationError(
                f"{path} key must be in {minimum}..{maximum}"
            )
        return index

    @staticmethod
    def _value_tolerance(*values: float) -> float:
        return max(1.0, *(abs(value) for value in values)) * 1e-12

    @classmethod
    def _values_close(cls, left: float, right: float) -> bool:
        return math.isclose(
            left,
            right,
            rel_tol=1e-12,
            abs_tol=cls._value_tolerance(left, right),
        )
