"""Time-measurement experiment API."""

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from api.BaseApi import ApiNodeData, BaseApi


logger = logging.getLogger("ApiTimeMeas")


STATE1_ID = 1
STATE2_ID = 2
STATE3_ID = 3
TERMINAL_ID = 0xFFFF

U32_MASK = 0xFFFFFFFF
U32_HALF = 0x80000000
WATCHDOG_MARGIN_US = 1_000_000
MAX_EXPERIMENT_US = U32_HALF - WATCHDOG_MARGIN_US
MAX_TIMEOUT_MS = (MAX_EXPERIMENT_US - 1) // 1000

_DI_BITS = {
    "A": 0,
    "B": 1,
    "C": 2,
    "R": 3,
    "a": 4,
    "b": 5,
    "c": 6,
    "r": 7,
}

_DI_START_RE = re.compile(
    r"^DI_([ABCRabcr])_(OPEN|CLOSE)_START$"
)
_DI_STOP_RE = re.compile(
    r"^DI_([ABCRabcr])_(OPEN|CLOSE)$"
)

_TIMER_EXIT_MEMBERS = {
    "TIMER_1": (0,),
    "TIMER_2": (1,),
    "TIMER_3": (2,),
    "TIMER_1_2": (0, 1),
    "TIMER_2_3": (1, 2),
    "TIMER_3_1": (2, 0),
    "TIMER_1_2_3": (0, 1, 2),
}


class _ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class _DiCondition:
    bit: int
    target: int


@dataclass(frozen=True)
class _TimerSpec:
    index: int
    key: str
    start_mode: str
    stop_mode: str
    enter_state2: bool
    start_event: Optional[_DiCondition]
    stop_event: _DiCondition
    cascade_source: Optional[int]


@dataclass
class _TimerRuntime:
    start_hw_ts: Optional[int] = None
    stop_hw_ts: Optional[int] = None


@dataclass(frozen=True)
class _State2Exit:
    selected_timers: Tuple[int, ...]
    required_mask: int
    required_value: int
    time_ctrl_only: bool = False


@dataclass(frozen=True)
class _RoleTriggers:
    timeout_ms: Optional[int]
    di_mask: Optional[int]
    manual_enabled: bool


@dataclass(frozen=True)
class _NodeSpec:
    role: int
    node_id: int
    static_phys: Dict[str, Dict[str, List[float]]]
    do_actions: Tuple[int, ...]
    timeout_ms: Optional[int]
    di_mask: Optional[int]
    manual_enabled: bool


@dataclass(frozen=True)
class _CompiledSetup:
    api_nodes: Dict[int, ApiNodeData]
    timer_specs: Dict[int, _TimerSpec]
    cascade_dependents: Dict[int, Tuple[int, ...]]
    state2_exit: Optional[_State2Exit]
    state2_timeout_ms: Optional[int]
    manual_roles: Set[int]


class ApiTimeMeas(BaseApi):
    MODULE_KEYS = ["time_measurement"]

    def __init__(self):
        super().__init__()
        self._reset_runtime_state()

    # ------------------------------------------------------------------
    # Setup and parsing
    # ------------------------------------------------------------------

    def _reset_runtime_state(self):
        for field_name in (
            "_state2_timeout_task",
            "_experiment_watchdog_task",
        ):
            task = getattr(self, field_name, None)
            if task is not None and not task.done():
                task.cancel()

        self._api_nodes: Dict[int, ApiNodeData] = {}
        self._timer_specs: Dict[int, _TimerSpec] = {}
        self._timer_runtime: Dict[int, _TimerRuntime] = {}
        self._cascade_dependents: Dict[int, Tuple[int, ...]] = {}
        self._state2_exit: Optional[_State2Exit] = None
        self._state2_timeout_ms: Optional[int] = None
        self._manual_roles: Set[int] = set()

        self._current_role: Optional[int] = None
        self._current_node_id: Optional[int] = None
        self._experiment_entry_hw_ts: Optional[int] = None
        self._state2_entry_hw_ts: Optional[int] = None
        self._experiment_deadline_monotonic: Optional[float] = None
        self._last_di = 0

        self._state2_timeout_task: Optional[asyncio.Task] = None
        self._experiment_watchdog_task: Optional[asyncio.Task] = None
        self._state2_jump_pending = False
        self._manual_jump_pending = False
        self._manual_target_role: Optional[int] = None
        self._stopping = False
        self._report_sent = False

    def _onSetup(self, params: Dict[str, Any]):
        self._reset_runtime_state()
        try:
            if self.ctrl is None or not self.ctrl.running:
                self._stopping = True
                return

            compiled = self._parse_and_compile(params)

            self._api_nodes = compiled.api_nodes
            self._timer_specs = compiled.timer_specs
            self._timer_runtime = {
                index: _TimerRuntime()
                for index in compiled.timer_specs
            }
            self._cascade_dependents = compiled.cascade_dependents
            self._state2_exit = compiled.state2_exit
            self._state2_timeout_ms = compiled.state2_timeout_ms
            self._manual_roles = compiled.manual_roles

            if not self.ctrl.upsertNodes(self._api_nodes):
                self._stopping = True
                return

            self._last_di = int(self.ctrl.di) & 0xFF
            if not self.ctrl.trigNode(STATE1_ID):
                self._stopping = not self.ctrl.running
                if self.ctrl.running:
                    self._stop_with_error(
                        "time_measurement: failed to enter state 1"
                    )
        except _ValidationError as exc:
            self._stop_with_error(
                f"Invalid time_measurement parameter: {exc}"
            )
        except Exception as exc:
            logger.exception("Failed to set up time_measurement")
            self._stop_with_error(
                f"Failed to set up time_measurement: {exc}"
            )

    def _parse_and_compile(
        self,
        params: Dict[str, Any],
    ) -> _CompiledSetup:
        if not isinstance(params, dict):
            raise _ValidationError("params must be an object")

        if "debounce" in params:
            debounce = params["debounce"]
            if (
                isinstance(debounce, bool)
                or not isinstance(debounce, (int, float))
                or not math.isfinite(float(debounce))
                or debounce < 0
            ):
                raise _ValidationError(
                    "params.debounce must be a finite "
                    "non-negative number"
                )

        timer_specs = self._parse_timers(params.get("timers"))

        raw_nodes = params.get("nodes")
        if not isinstance(raw_nodes, list) or len(raw_nodes) != 3:
            raise _ValidationError(
                "params.nodes must contain exactly three nodes"
            )

        for index, expected_id in enumerate(
            (STATE1_ID, STATE2_ID, STATE3_ID)
        ):
            raw_node = raw_nodes[index]
            path = f"nodes[{index}]"
            if not isinstance(raw_node, dict):
                raise _ValidationError(f"{path} must be an object")
            node_id = self._require_int(
                raw_node.get("id"),
                f"{path}.id",
            )
            if node_id != expected_id:
                raise _ValidationError(
                    f"{path}.id must be {expected_id}"
                )
            if raw_node.get("mode") != "STATIC":
                raise _ValidationError(
                    f"{path}.mode must be STATIC"
                )

        statics = [
            self._validate_physical_map(
                raw_node.get("static"),
                f"nodes[{index}].static",
            )
            for index, raw_node in enumerate(raw_nodes)
        ]

        state1_do = self._compile_absolute_do(
            self._required_array(
                raw_nodes[0],
                "do_actions",
                "nodes[0].do_actions",
            ),
            "nodes[0].do_actions",
        )
        state3_do = self._compile_absolute_do(
            self._required_array(
                raw_nodes[2],
                "do_actions",
                "nodes[2].do_actions",
            ),
            "nodes[2].do_actions",
        )
        state2_do = self._compile_state2_pulse_do(
            self._required_array(
                raw_nodes[1],
                "do_actions",
                "nodes[1].do_actions",
            ),
            state1_do[-1] & 0xFF,
            "nodes[1].do_actions",
        )

        state1_triggers = self._parse_role_triggers(
            self._required_array(
                raw_nodes[0],
                "triggers",
                "nodes[0].triggers",
            ),
            STATE1_ID,
            "nodes[0].triggers",
        )
        state3_triggers = self._parse_role_triggers(
            self._required_array(
                raw_nodes[2],
                "triggers",
                "nodes[2].triggers",
            ),
            STATE3_ID,
            "nodes[2].triggers",
        )
        state2_exit, state2_timeout = self._parse_state2_triggers(
            self._required_array(
                raw_nodes[1],
                "triggers",
                "nodes[1].triggers",
            ),
            timer_specs,
        )

        node_specs = (
            _NodeSpec(
                role=STATE1_ID,
                node_id=STATE1_ID,
                static_phys=statics[0],
                do_actions=tuple(state1_do),
                timeout_ms=state1_triggers.timeout_ms,
                di_mask=state1_triggers.di_mask,
                manual_enabled=state1_triggers.manual_enabled,
            ),
            _NodeSpec(
                role=STATE2_ID,
                node_id=STATE2_ID,
                static_phys=statics[1],
                do_actions=tuple(state2_do),
                timeout_ms=None,
                di_mask=None,
                manual_enabled=False,
            ),
            _NodeSpec(
                role=STATE3_ID,
                node_id=STATE3_ID,
                static_phys=statics[2],
                do_actions=tuple(state3_do),
                timeout_ms=state3_triggers.timeout_ms,
                di_mask=state3_triggers.di_mask,
                manual_enabled=state3_triggers.manual_enabled,
            ),
        )

        api_nodes = self._build_api_nodes(node_specs)
        cascade_dependents = self._build_cascade_dependents(
            timer_specs
        )
        manual_roles = {
            spec.role
            for spec in node_specs
            if spec.manual_enabled
        }

        return _CompiledSetup(
            api_nodes=api_nodes,
            timer_specs=timer_specs,
            cascade_dependents=cascade_dependents,
            state2_exit=state2_exit,
            state2_timeout_ms=state2_timeout,
            manual_roles=manual_roles,
        )

    def _parse_timers(
        self,
        raw_timers: Any,
    ) -> Dict[int, _TimerSpec]:
        if not isinstance(raw_timers, dict):
            raise _ValidationError(
                "params.timers must be an object"
            )

        timer_specs: Dict[int, _TimerSpec] = {}
        for index in range(3):
            key = f"timer{index + 1}"
            if key not in raw_timers:
                continue

            raw_timer = raw_timers[key]
            path = f"params.timers.{key}"
            if not isinstance(raw_timer, dict):
                raise _ValidationError(f"{path} must be an object")

            start_mode = raw_timer.get("start_mode")
            stop_mode = raw_timer.get("stop_mode")
            if not isinstance(start_mode, str) or not start_mode:
                raise _ValidationError(
                    f"{path}.start_mode must be a non-empty string"
                )
            if not isinstance(stop_mode, str) or not stop_mode:
                raise _ValidationError(
                    f"{path}.stop_mode must be a non-empty string"
                )

            (
                enter_state2,
                start_event,
                cascade_source,
            ) = self._parse_start_mode(
                index,
                start_mode,
                f"{path}.start_mode",
            )
            stop_event = self._parse_stop_mode(
                stop_mode,
                f"{path}.stop_mode",
            )

            timer_specs[index] = _TimerSpec(
                index=index,
                key=key,
                start_mode=start_mode,
                stop_mode=stop_mode,
                enter_state2=enter_state2,
                start_event=start_event,
                stop_event=stop_event,
                cascade_source=cascade_source,
            )

        for index, spec in timer_specs.items():
            source = spec.cascade_source
            if source is not None and source not in timer_specs:
                raise _ValidationError(
                    f"params.timers.{spec.key}.start_mode "
                    f"references unconfigured timer{source + 1}"
                )
            if source is not None and source >= index:
                raise _ValidationError(
                    f"params.timers.{spec.key}.start_mode "
                    "must reference a preceding timer"
                )
        return timer_specs

    def _parse_start_mode(
        self,
        index: int,
        start_mode: str,
        path: str,
    ) -> Tuple[
        bool,
        Optional[_DiCondition],
        Optional[int],
    ]:
        if start_mode == "ENTER_STATE_2":
            return True, None, None

        match = _DI_START_RE.fullmatch(start_mode)
        if match:
            return (
                False,
                self._make_di_condition(
                    match.group(1),
                    match.group(2),
                ),
                None,
            )

        cascade_source = None
        if index == 1 and start_mode == "TIMER1_STOP_START":
            cascade_source = 0
        elif index == 2 and start_mode == "TIMER1_STOP_START":
            cascade_source = 0
        elif index == 2 and start_mode == "TIMER2_STOP_START":
            cascade_source = 1

        if cascade_source is not None:
            return False, None, cascade_source

        raise _ValidationError(f"{path} has an unknown value")

    def _parse_stop_mode(
        self,
        stop_mode: str,
        path: str,
    ) -> _DiCondition:
        match = _DI_STOP_RE.fullmatch(stop_mode)
        if not match:
            raise _ValidationError(f"{path} has an unknown value")
        return self._make_di_condition(
            match.group(1),
            match.group(2),
        )

    @staticmethod
    def _make_di_condition(
        port: str,
        state: str,
    ) -> _DiCondition:
        return _DiCondition(
            bit=_DI_BITS[port],
            target=1 if state == "OPEN" else 0,
        )

    def _parse_role_triggers(
        self,
        raw_triggers: List[Any],
        role: int,
        path: str,
    ) -> _RoleTriggers:
        if not raw_triggers:
            raise _ValidationError(
                f"{path} must contain at least one trigger"
            )

        timeout_ms = None
        di_mask = None
        manual_enabled = False
        seen = set()

        for index, raw_trigger in enumerate(raw_triggers):
            trigger_path = f"{path}[{index}]"
            if not isinstance(raw_trigger, dict):
                raise _ValidationError(
                    f"{trigger_path} must be an object"
                )
            condition = raw_trigger.get("condition")
            if condition not in (
                "MANUAL_TRIG",
                "TIME_OUT",
                "DI_MATCH",
            ):
                raise _ValidationError(
                    f"{trigger_path}.condition is invalid "
                    f"for state {role}"
                )
            if condition in seen:
                raise _ValidationError(
                    f"{path} contains duplicate {condition}"
                )
            seen.add(condition)

            if condition == "MANUAL_TRIG":
                manual_enabled = True
            elif condition == "TIME_OUT":
                timeout_ms = self._require_int(
                    raw_trigger.get("timeout"),
                    f"{trigger_path}.timeout",
                    minimum=1,
                    maximum=MAX_TIMEOUT_MS,
                )
            else:
                di_mask = self._require_int(
                    raw_trigger.get("di_mask"),
                    f"{trigger_path}.di_mask",
                    minimum=1,
                    maximum=0x7FF,
                )
                if not (di_mask & 0xFF):
                    raise _ValidationError(
                        f"{trigger_path}.di_mask must select "
                        "at least one DI"
                    )

        if manual_enabled and len(raw_triggers) != 1:
            raise _ValidationError(
                f"{path}.MANUAL_TRIG must be the only trigger"
            )

        return _RoleTriggers(
            timeout_ms=timeout_ms,
            di_mask=di_mask,
            manual_enabled=manual_enabled,
        )

    def _parse_state2_triggers(
        self,
        raw_triggers: List[Any],
        timer_specs: Dict[int, _TimerSpec],
    ) -> Tuple[Optional[_State2Exit], Optional[int]]:
        path = "nodes[1].triggers"
        if not raw_triggers:
            raise _ValidationError(
                f"{path} must contain at least one trigger"
            )

        timer_condition = None
        time_ctrl = False
        timeout_ms = None

        for index, raw_trigger in enumerate(raw_triggers):
            trigger_path = f"{path}[{index}]"
            if not isinstance(raw_trigger, dict):
                raise _ValidationError(
                    f"{trigger_path} must be an object"
                )
            condition = raw_trigger.get("condition")

            if condition == "TIME_OUT":
                if timeout_ms is not None:
                    raise _ValidationError(
                        f"{path} contains duplicate TIME_OUT"
                    )
                timeout_ms = self._require_int(
                    raw_trigger.get("timeout"),
                    f"{trigger_path}.timeout",
                    minimum=1,
                    maximum=MAX_TIMEOUT_MS,
                )
                continue

            if condition == "TIME_CTRL":
                if time_ctrl or timer_condition is not None:
                    raise _ValidationError(
                        f"{path} contains conflicting "
                        "state 2 control conditions"
                    )
                time_ctrl = True
                continue

            if condition in _TIMER_EXIT_MEMBERS:
                if timer_condition is not None or time_ctrl:
                    raise _ValidationError(
                        f"{path} contains conflicting "
                        "state 2 control conditions"
                    )
                timer_condition = condition
                continue

            raise _ValidationError(
                f"{trigger_path}.condition is invalid for state 2"
            )

        if time_ctrl and timeout_ms is None:
            raise _ValidationError(
                f"{path}.TIME_CTRL requires TIME_OUT"
            )
        if timer_condition is None and timeout_ms is None:
            raise _ValidationError(
                f"{path} has no effective exit"
            )

        if timer_condition is None:
            return (
                _State2Exit(
                    selected_timers=(),
                    required_mask=0,
                    required_value=0,
                    time_ctrl_only=time_ctrl,
                )
                if time_ctrl
                else None,
                timeout_ms,
            )

        selected = _TIMER_EXIT_MEMBERS[timer_condition]
        for timer_index in selected:
            if timer_index not in timer_specs:
                raise _ValidationError(
                    f"{path}.{timer_condition} references "
                    f"unconfigured timer{timer_index + 1}"
                )

        required_mask = 0
        required_value = 0
        for timer_index in selected:
            stop_event = timer_specs[timer_index].stop_event
            bit_mask = 1 << stop_event.bit
            if required_mask & bit_mask:
                existing = (
                    1 if required_value & bit_mask else 0
                )
                if existing != stop_event.target:
                    raise _ValidationError(
                        f"{path}.{timer_condition} requires "
                        "conflicting levels on the same DI"
                    )
            required_mask |= bit_mask
            if stop_event.target:
                required_value |= bit_mask

        return (
            _State2Exit(
                selected_timers=tuple(selected),
                required_mask=required_mask,
                required_value=required_value,
            ),
            timeout_ms,
        )

    def _validate_physical_map(
        self,
        value: Any,
        path: str,
    ) -> Dict[str, Dict[str, List[float]]]:
        if not isinstance(value, dict):
            raise _ValidationError(f"{path} must be an object")

        result: Dict[str, Dict[str, List[float]]] = {}
        for channel_key, raw_layers in value.items():
            channel = self._canonical_index(
                channel_key,
                f"{path} channel",
                minimum=0,
                maximum=11,
            )
            if not isinstance(raw_layers, dict):
                raise _ValidationError(
                    f"{path}.{channel} must be an object"
                )

            result[channel] = {}
            for layer_key, raw_pair in raw_layers.items():
                layer = self._canonical_index(
                    layer_key,
                    f"{path}.{channel} layer",
                    minimum=0,
                    maximum=64,
                )
                if (
                    not isinstance(raw_pair, (list, tuple))
                    or len(raw_pair) != 2
                ):
                    raise _ValidationError(
                        f"{path}.{channel}.{layer} must contain "
                        "exactly [amplitude, frequency_or_phase]"
                    )

                pair: List[float] = []
                for pair_index, number in enumerate(raw_pair):
                    if (
                        isinstance(number, bool)
                        or not isinstance(number, (int, float))
                        or not math.isfinite(float(number))
                    ):
                        raise _ValidationError(
                            f"{path}.{channel}.{layer}"
                            f"[{pair_index}] must be a finite number"
                        )
                    pair.append(float(number))
                result[channel][layer] = pair
        return result

    def _compile_absolute_do(
        self,
        raw_actions: List[Any],
        path: str,
    ) -> List[int]:
        packed: List[int] = []
        for index, raw_action in enumerate(raw_actions):
            action_path = f"{path}[{index}]"
            if not isinstance(raw_action, dict):
                raise _ValidationError(
                    f"{action_path} must be an object"
                )
            delay = self._require_int(
                raw_action.get("delay"),
                f"{action_path}.delay",
                minimum=0,
                maximum=0xFFFF,
            )
            mask = self._require_int(
                raw_action.get("mask"),
                f"{action_path}.mask",
                minimum=0,
                maximum=0xFF,
            )
            packed.append((delay << 8) | mask)
        return packed or [0]

    def _compile_state2_pulse_do(
        self,
        raw_actions: List[Any],
        baseline_mask: int,
        path: str,
    ) -> List[int]:
        if len(raw_actions) != 8:
            raise _ValidationError(
                f"{path} must contain exactly eight channel actions"
            )

        events: Dict[int, int] = {}
        for channel, raw_action in enumerate(raw_actions):
            action_path = f"{path}[{channel}]"
            if not isinstance(raw_action, dict):
                raise _ValidationError(
                    f"{action_path} must be an object"
                )
            delay = self._require_int(
                raw_action.get("delay"),
                f"{action_path}.delay",
                minimum=0,
                maximum=0xFFFF,
            )
            hold_time = self._require_int(
                raw_action.get("holdTime"),
                f"{action_path}.holdTime",
                minimum=0,
                maximum=0xFFFF,
            )
            end = delay + hold_time
            if end > 0xFFFF:
                raise _ValidationError(
                    f"{action_path}.delay + holdTime "
                    "must be <= 65535"
                )

            bit = 1 << channel
            events[delay] = events.get(delay, 0) ^ bit
            events[end] = events.get(end, 0) ^ bit

        mask = baseline_mask & 0xFF
        packed: List[int] = []
        for at_ms in sorted(events):
            toggle = events[at_ms]
            if not toggle:
                continue
            mask ^= toggle
            packed.append((at_ms << 8) | mask)

        if not packed or (packed[0] >> 8) > 0:
            packed.insert(0, baseline_mask & 0xFF)
        return packed

    def _build_api_nodes(
        self,
        node_specs: Tuple[_NodeSpec, ...],
    ) -> Dict[int, ApiNodeData]:
        api_nodes: Dict[int, ApiNodeData] = {}
        bases = self.compileStaticNodes(
            {spec.node_id: spec.static_phys for spec in node_specs},
            {STATE1_ID: [STATE2_ID], STATE2_ID: [STATE3_ID]},
            STATE1_ID,
        )
        for spec in node_specs:
            timeout_target = None
            di_target = None
            if spec.role == STATE1_ID:
                timeout_target = STATE2_ID
                di_target = STATE2_ID
            elif spec.role == STATE3_ID:
                timeout_target = TERMINAL_ID
                di_target = TERMINAL_ID

            api_nodes[spec.node_id] = ApiNodeData(
                mode=1,
                base=bases[spec.node_id],
                doActions=list(spec.do_actions),
                timeoutMs=spec.timeout_ms,
                timeoutId=(
                    timeout_target
                    if spec.timeout_ms is not None
                    else None
                ),
                diMatchMask=spec.di_mask,
                diMatchId=(
                    di_target
                    if spec.di_mask is not None
                    else None
                ),
            )

        if set(api_nodes) != {
            STATE1_ID,
            STATE2_ID,
            STATE3_ID,
        }:
            raise _ValidationError(
                "compiled node IDs must be exactly 1, 2 and 3"
            )
        if 0x0000 in api_nodes or TERMINAL_ID in api_nodes:
            raise _ValidationError(
                "compiled nodes must not overwrite 0 or FFFF"
            )

        state2 = api_nodes[STATE2_ID]
        if any(
            value is not None
            for value in (
                state2.timeoutMs,
                state2.timeoutId,
                state2.diMatchMask,
                state2.diMatchId,
            )
        ):
            raise _ValidationError(
                "state 2 automatic fields must stay empty"
            )
        return api_nodes

    @staticmethod
    def _build_cascade_dependents(
        timer_specs: Dict[int, _TimerSpec],
    ) -> Dict[int, Tuple[int, ...]]:
        mutable: Dict[int, List[int]] = {
            index: []
            for index in timer_specs
        }
        for index, spec in timer_specs.items():
            if spec.cascade_source is not None:
                mutable[spec.cascade_source].append(index)
        return {
            index: tuple(dependents)
            for index, dependents in mutable.items()
        }

    @staticmethod
    def _required_array(
        owner: Dict[str, Any],
        key: str,
        path: str,
    ) -> List[Any]:
        if key not in owner:
            raise _ValidationError(f"{path} is required")
        value = owner[key]
        if not isinstance(value, list):
            raise _ValidationError(f"{path} must be an array")
        return value

    @staticmethod
    def _canonical_index(
        value: Any,
        path: str,
        *,
        minimum: int,
        maximum: int,
    ) -> str:
        if not isinstance(value, str):
            raise _ValidationError(
                f"{path} key must be a string"
            )
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise _ValidationError(
                f"{path} key must be an integer string"
            ) from None
        if str(index) != value:
            raise _ValidationError(
                f"{path} key must use canonical decimal form"
            )
        if index < minimum or index > maximum:
            raise _ValidationError(
                f"{path} key must be in {minimum}..{maximum}"
            )
        return value

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

    # ------------------------------------------------------------------
    # Engine callbacks and timer processing
    # ------------------------------------------------------------------

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
        ):
            return
        try:
            if (
                isinstance(nodeId, bool)
                or not isinstance(nodeId, int)
                or isinstance(tick, bool)
                or not isinstance(tick, int)
                or isinstance(hw_ts, bool)
                or not isinstance(hw_ts, int)
                or hw_ts < 0
            ):
                raise RuntimeError("invalid node update feedback")
            if tick != 0:
                return
            if nodeId not in (
                STATE1_ID,
                STATE2_ID,
                STATE3_ID,
            ):
                raise RuntimeError(
                    f"unexpected node update {nodeId}"
                )
            if self._deadline_expired():
                self._stop_with_error(
                    "time_measurement: experiment reached "
                    "safe U32 runtime limit"
                )
                return

            previous_role = self._current_role
            if previous_role is None:
                if nodeId != STATE1_ID:
                    raise RuntimeError(
                        f"expected state 1, received state {nodeId}"
                    )
            elif (
                nodeId != previous_role
                and nodeId != previous_role + 1
            ):
                raise RuntimeError(
                    f"unexpected transition from state "
                    f"{previous_role} to state {nodeId}"
                )

            self._current_role = nodeId
            self._current_node_id = nodeId
            if (
                self._manual_jump_pending
                and nodeId == self._manual_target_role
            ):
                self._manual_jump_pending = False
                self._manual_target_role = None

            if nodeId == STATE1_ID:
                self._enter_state1(hw_ts)
            elif nodeId == STATE2_ID:
                self._enter_state2(hw_ts)
            else:
                self._enter_state3()

            self.ctrl.sendNodeUpdate(nodeId)

            if nodeId == STATE2_ID:
                self._maybe_exit_state2(
                    int(self.ctrl.di) & 0xFF
                )
        except Exception as exc:
            logger.exception("time_measurement onUpdate failed")
            self._stop_with_error(
                f"time_measurement update error: {exc}"
            )

    def _enter_state1(self, hw_ts: int):
        if self._experiment_entry_hw_ts is not None:
            return
        self._experiment_entry_hw_ts = hw_ts & U32_MASK
        self._experiment_deadline_monotonic = (
            time.monotonic()
            + MAX_EXPERIMENT_US / 1_000_000.0
        )
        self._start_experiment_watchdog()

    def _enter_state2(self, hw_ts: int):
        if self._state2_entry_hw_ts is None:
            self._state2_entry_hw_ts = hw_ts & U32_MASK
            for index, spec in self._timer_specs.items():
                if spec.enter_state2:
                    self._try_start_timer(
                        index,
                        self._state2_entry_hw_ts,
                    )

        if (
            self._state2_timeout_ms is not None
            and not self._state2_jump_pending
            and self._state2_timeout_task is None
        ):
            self._start_state2_timeout()

    def _enter_state3(self):
        self._cancel_task("_state2_timeout_task")

    def onDi(self, di: int, hw_ts: int):
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
        ):
            return
        try:
            if (
                isinstance(di, bool)
                or not isinstance(di, int)
                or not 0 <= di <= 0xFF
                or isinstance(hw_ts, bool)
                or not isinstance(hw_ts, int)
            ):
                raise RuntimeError("invalid DI feedback")
            if self._deadline_expired():
                self._stop_with_error(
                    "time_measurement: experiment reached "
                    "safe U32 runtime limit"
                )
                return

            current_di = di
            previous_di = self._last_di
            timestamp = hw_ts & U32_MASK

            if (
                self._current_role not in (
                    STATE1_ID,
                    STATE2_ID,
                    STATE3_ID,
                )
                or self._experiment_entry_hw_ts is None
            ):
                self._last_di = current_di
                return

            if self._forward_delta_us(
                timestamp,
                self._experiment_entry_hw_ts,
            ) is None:
                self._last_di = current_di
                return

            events = {
                _DiCondition(
                    bit=bit,
                    target=(current_di >> bit) & 1,
                )
                for bit in range(8)
                if (previous_di ^ current_di) & (1 << bit)
            }

            for index, spec in self._timer_specs.items():
                if (
                    spec.start_event is not None
                    and spec.start_event in events
                ):
                    self._try_start_timer(index, timestamp)

            for _ in range(3):
                newly_stopped: List[int] = []
                for index, spec in self._timer_specs.items():
                    runtime = self._timer_runtime[index]
                    if (
                        runtime.start_hw_ts is not None
                        and runtime.stop_hw_ts is None
                        and spec.stop_event in events
                        and self._forward_delta_us(
                            timestamp,
                            runtime.start_hw_ts,
                        )
                        is not None
                    ):
                        runtime.stop_hw_ts = timestamp
                        newly_stopped.append(index)

                newly_started = False
                for source_index in newly_stopped:
                    for dependent_index in (
                        self._cascade_dependents.get(
                            source_index,
                            (),
                        )
                    ):
                        if self._try_start_timer(
                            dependent_index,
                            timestamp,
                        ):
                            newly_started = True

                if not newly_stopped and not newly_started:
                    break

            self._last_di = current_di
            if self._current_role == STATE2_ID:
                self._maybe_exit_state2(current_di)
        except Exception as exc:
            logger.exception("time_measurement onDi failed")
            self._stop_with_error(
                f"time_measurement DI error: {exc}"
            )

    @staticmethod
    def _forward_delta_us(
        event_ts: int,
        reference_ts: int,
    ) -> Optional[int]:
        delta = (event_ts - reference_ts) & U32_MASK
        return delta if delta < U32_HALF else None

    def _try_start_timer(
        self,
        index: int,
        hw_ts: int,
    ) -> bool:
        runtime = self._timer_runtime.get(index)
        if runtime is None or runtime.start_hw_ts is not None:
            return False
        runtime.start_hw_ts = hw_ts & U32_MASK
        return True

    def _maybe_exit_state2(self, current_di: int):
        exit_spec = self._state2_exit
        if (
            exit_spec is None
            or exit_spec.time_ctrl_only
            or not exit_spec.selected_timers
            or self._current_role != STATE2_ID
            or self._state2_jump_pending
        ):
            return

        if not all(
            self._timer_runtime[index].stop_hw_ts is not None
            for index in exit_spec.selected_timers
        ):
            return
        if (
            current_di & exit_spec.required_mask
        ) != exit_spec.required_value:
            return

        self._request_state2_exit("TIMER")

    def _request_state2_exit(self, _source: str) -> bool:
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
            or self._current_role != STATE2_ID
            or self._state2_jump_pending
        ):
            return False

        self._state2_jump_pending = True
        self._cancel_task("_state2_timeout_task")
        if self.ctrl.trigNode(STATE3_ID):
            return True

        self._state2_jump_pending = False
        if self.ctrl.running and not self._stopping:
            self._stop_with_error(
                "time_measurement: failed to enter state 3"
            )
        return False

    # ------------------------------------------------------------------
    # Async timeouts
    # ------------------------------------------------------------------

    def _start_state2_timeout(self):
        if self._state2_timeout_task is not None:
            return
        self._state2_timeout_task = self._create_task(
            self._state2_timeout_loop()
        )

    async def _state2_timeout_loop(self):
        current = asyncio.current_task()
        try:
            await asyncio.sleep(
                self._state2_timeout_ms / 1000.0
            )
            if (
                not self.isActive
                or self.ctrl is None
                or not self.ctrl.running
                or self._stopping
                or self._current_role != STATE2_ID
                or self._state2_jump_pending
            ):
                return
            if self._deadline_expired():
                if self._state2_timeout_task is current:
                    self._state2_timeout_task = None
                self._stop_with_error(
                    "time_measurement: experiment reached "
                    "safe U32 runtime limit"
                )
                return

            if self._state2_timeout_task is current:
                self._state2_timeout_task = None
            self._request_state2_exit("TIME_OUT")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception(
                "time_measurement state 2 timeout failed"
            )
            self._stop_with_error(
                f"time_measurement timeout error: {exc}"
            )
        finally:
            if self._state2_timeout_task is current:
                self._state2_timeout_task = None

    def _start_experiment_watchdog(self):
        if self._experiment_watchdog_task is not None:
            return
        self._experiment_watchdog_task = self._create_task(
            self._experiment_watchdog_loop()
        )

    async def _experiment_watchdog_loop(self):
        current = asyncio.current_task()
        try:
            while self._experiment_deadline_monotonic is not None:
                remaining = (
                    self._experiment_deadline_monotonic
                    - time.monotonic()
                )
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)

            if (
                not self.isActive
                or self.ctrl is None
                or not self.ctrl.running
                or self._stopping
            ):
                return
            if self._experiment_watchdog_task is current:
                self._experiment_watchdog_task = None
            self._stop_with_error(
                "time_measurement: experiment reached "
                "safe U32 runtime limit"
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception(
                "time_measurement watchdog failed"
            )
            self._stop_with_error(
                f"time_measurement watchdog error: {exc}"
            )
        finally:
            if self._experiment_watchdog_task is current:
                self._experiment_watchdog_task = None

    @staticmethod
    def _create_task(coroutine) -> asyncio.Task:
        try:
            return asyncio.get_running_loop().create_task(coroutine)
        except Exception:
            coroutine.close()
            raise

    def _cancel_task(self, field_name: str):
        task = getattr(self, field_name, None)
        if task is None:
            return
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is current:
            if getattr(self, field_name, None) is task:
                setattr(self, field_name, None)
            return
        if getattr(self, field_name, None) is task:
            setattr(self, field_name, None)
        if not task.done():
            task.cancel()

    def _deadline_expired(self) -> bool:
        return (
            self._experiment_deadline_monotonic is not None
            and time.monotonic()
            >= self._experiment_deadline_monotonic
        )

    # ------------------------------------------------------------------
    # Frontend commands and report
    # ------------------------------------------------------------------

    def onWebCommand(self, msg: Dict[str, Any]):
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
        ):
            return
        try:
            if not isinstance(msg, dict):
                self.ctrl.sendError(
                    "time_measurement command must be an object"
                )
                return
            if msg.get("module") != "time_measurement":
                self.ctrl.sendError(
                    "Command module must be time_measurement"
                )
                return
            if msg.get("cmd") != "next_node":
                self.ctrl.sendError(
                    f"Unknown time_measurement command: "
                    f"{msg.get('cmd')}"
                )
                return
            if self._manual_jump_pending:
                self.ctrl.sendError(
                    "A next_node transition is already pending"
                )
                return
            if self._current_role == STATE2_ID:
                self.ctrl.sendError(
                    "State 2 does not accept next_node"
                )
                return
            if self._current_role not in self._manual_roles:
                self.ctrl.sendError(
                    "Current state does not accept next_node"
                )
                return

            if self._current_role == STATE1_ID:
                self._manual_jump_pending = True
                self._manual_target_role = STATE2_ID
                if not self.ctrl.trigNode(STATE2_ID):
                    self._manual_jump_pending = False
                    self._manual_target_role = None
                return

            if self._current_role == STATE3_ID:
                self._manual_jump_pending = True
                self._stopping = True
                try:
                    self.ctrl.stopTest()
                except Exception:
                    self._stopping = False
                    self._manual_jump_pending = False
                    raise
        except Exception as exc:
            logger.exception(
                "time_measurement command failed"
            )
            self._stop_with_error(
                f"time_measurement command error: {exc}"
            )

    def _onStop(self):
        self._cancel_task("_state2_timeout_task")
        self._cancel_task("_experiment_watchdog_task")
        if self._report_sent:
            return

        self._stopping = True
        try:
            report: Dict[str, Optional[float]] = {}
            for index in range(3):
                runtime = self._timer_runtime.get(index)
                key = f"timer{index + 1}_elapsed_ms"
                if (
                    runtime is None
                    or runtime.start_hw_ts is None
                    or runtime.stop_hw_ts is None
                ):
                    report[key] = None
                    continue

                elapsed_us = (
                    runtime.stop_hw_ts
                    - runtime.start_hw_ts
                ) & U32_MASK
                report[key] = (
                    round(elapsed_us / 1000.0, 3)
                    if elapsed_us < U32_HALF
                    else None
                )

            self.ctrl.sendReport(report)
            self._report_sent = True
        except Exception:
            logger.exception(
                "Failed to build time_measurement report"
            )
        finally:
            self._current_role = None
            self._current_node_id = None
            self._manual_jump_pending = False
            self._manual_target_role = None

    def _stop_with_error(self, reason: str):
        if self._stopping:
            return
        self._stopping = True
        try:
            if self.ctrl is not None and self.ctrl.running:
                self.ctrl.stopTest(reason)
        except Exception:
            logger.exception(
                "Failed to stop time_measurement after error"
            )
