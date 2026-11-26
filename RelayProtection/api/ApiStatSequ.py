"""State-sequence experiment API.

The frontend supplies a graph of visible business nodes.  Visible node IDs are
kept unchanged (1..1000); positive post-condition delays are compiled into
internal static nodes that inherit the current waveform.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from api.BaseApi import ApiNodeData, BaseApi


logger = logging.getLogger("ApiStatSequ")


_DI_REPORT_FIELDS = (
    "diA",
    "diB",
    "diC",
    "diR",
    "dia",
    "dib",
    "dic",
    "dir",
)

_CONDITIONS = {
    "DI_MATCH",
    "TIME_OUT",
    "COUNT_OVER",
    "MANUAL_TRIG",
}


class _ValidationError(ValueError):
    pass


@dataclass
class _TriggerSpec:
    condition: str
    next_front_id: int
    real_target: int
    post_delay_ms: int
    timeout_ms: Optional[int] = None
    di_mask: Optional[int] = None
    compiled_target: Optional[int] = None

    @property
    def has_safety_timeout(self) -> bool:
        return self.condition == "DI_MATCH" and self.timeout_ms is not None


@dataclass
class _FrontNodeSpec:
    front_id: int
    mode_name: str
    static_phys: Dict[str, Dict[str, List[float]]]
    packed_do_actions: List[int]
    triggers: List[_TriggerSpec]
    dynamic: Optional[Dict[str, Any]] = None


@dataclass
class _DelayNodeSpec:
    node_id: int
    post_delay_ms: int
    target_engine_id: int


@dataclass
class _VisitRecord:
    front_id: int
    entry_hw_ts: int
    entry_host_ts: float
    entry_di: int
    last_di: int
    first_change_sec: List[Optional[float]] = field(
        default_factory=lambda: [None] * 8
    )
    di_match_observed: bool = False
    pending_manual: bool = False


class ApiStatSequ(BaseApi):
    MODULE_KEYS = ["universal_sequence"]

    _PHASE_CREATED = "CREATED"
    _PHASE_WAIT_ENTRY = "WAIT_ENTRY"
    _PHASE_ACTIVE_MAIN = "ACTIVE_MAIN"
    _PHASE_POST_DELAY = "POST_DELAY"
    _PHASE_STOPPED = "STOPPED"

    def __init__(self):
        super().__init__()
        self._reset_runtime_state()

    # ------------------------------------------------------------------
    # Setup and compilation
    # ------------------------------------------------------------------

    def _reset_runtime_state(self):
        self._main_by_engine: Dict[int, _FrontNodeSpec] = {}
        self._delay_by_engine: Dict[int, _DelayNodeSpec] = {}
        self._routes_by_source: Dict[int, List[_TriggerSpec]] = {}
        self._manual_by_front: Dict[int, _TriggerSpec] = {}
        self._all_nodes: Dict[int, ApiNodeData] = {}
        self._entry_engine_id: Optional[int] = None

        self._current_front_id: Optional[int] = None
        self._current_engine_id: Optional[int] = None
        self._current_visit: Optional[_VisitRecord] = None
        self._experiment_start_di: Optional[int] = None
        self._terminal_hw_ts: Optional[int] = None

        self._rows: List[Dict[str, Any]] = []
        self._phase = self._PHASE_CREATED
        self._manual_pending = False
        self._pending_end_condition: Optional[str] = None
        self._stopping = False
        self._report_sent = False

    def _onSetup(self, params: Dict[str, Any]):
        self._reset_runtime_state()
        try:
            specs = self._parse_and_validate(params)
            self._entry_engine_id = specs[0].front_id

            self._allocate_route_targets(specs)
            self._compile_nodes(specs)
            self._phase = self._PHASE_WAIT_ENTRY

            if not self.ctrl.upsertNodes(self._all_nodes):
                return
            self._experiment_start_di = int(self.ctrl.di) & 0xFF
            if not self.ctrl.trigNode(self._entry_engine_id):
                return
        except _ValidationError as exc:
            self._stop_with_error(
                f"Invalid universal_sequence parameter: {exc}"
            )
        except Exception as exc:
            logger.exception("Failed to set up universal_sequence")
            self._stop_with_error(
                f"Failed to set up universal_sequence: {exc}"
            )

    def _parse_and_validate(
        self,
        params: Dict[str, Any],
    ) -> List[_FrontNodeSpec]:
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
                    "params.debounce must be a finite non-negative number"
                )

        raw_nodes = params.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise _ValidationError("params.nodes must be a non-empty array")

        front_ids: List[int] = []
        seen_ids = set()
        for index, raw_node in enumerate(raw_nodes):
            path = f"nodes[{index}]"
            if not isinstance(raw_node, dict):
                raise _ValidationError(f"{path} must be an object")
            node_id = self._require_int(
                raw_node.get("id"),
                f"{path}.id",
                minimum=1,
                maximum=1000,
            )
            if node_id in seen_ids:
                raise _ValidationError(
                    f"{path}.id duplicates node ID {node_id}"
                )
            seen_ids.add(node_id)
            front_ids.append(node_id)

        id_set = set(front_ids)
        specs: List[_FrontNodeSpec] = []
        for index, raw_node in enumerate(raw_nodes):
            specs.append(
                self._parse_front_node(
                    raw_node,
                    index,
                    id_set,
                )
            )
        return specs

    def _parse_front_node(
        self,
        raw_node: Dict[str, Any],
        index: int,
        id_set: set,
    ) -> _FrontNodeSpec:
        path = f"nodes[{index}]"

        front_id = self._require_int(
            raw_node.get("id"),
            f"{path}.id",
            minimum=1,
            maximum=1000,
        )

        mode_name = raw_node.get("mode")
        if mode_name not in ("STATIC", "SWEEP"):
            raise _ValidationError(
                f"{path}.mode must be STATIC or SWEEP"
            )

        if "static" not in raw_node:
            raise _ValidationError(f"{path}.static is required")
        static_phys = self._validate_physical_map(
            raw_node["static"],
            f"{path}.static",
            allow_empty=True,
        )

        packed_do_actions = self._pack_do_actions(
            raw_node.get("do_actions", []),
            f"{path}.do_actions",
        )

        dynamic = None
        if mode_name == "SWEEP":
            dynamic = self._parse_dynamic(
                raw_node.get("dynamic"),
                static_phys,
                f"{path}.dynamic",
            )

        raw_triggers = raw_node.get("triggers", [])
        if not isinstance(raw_triggers, list):
            raise _ValidationError(f"{path}.triggers must be an array")

        triggers: List[_TriggerSpec] = []
        seen_conditions = set()
        for trigger_index, raw_trigger in enumerate(raw_triggers):
            trigger = self._parse_trigger(
                raw_trigger,
                f"{path}.triggers[{trigger_index}]",
                id_set,
            )
            if trigger.condition in seen_conditions:
                raise _ValidationError(
                    f"{path} contains duplicate "
                    f"{trigger.condition} triggers"
                )
            seen_conditions.add(trigger.condition)
            triggers.append(trigger)

        if "MANUAL_TRIG" in seen_conditions and len(triggers) != 1:
            raise _ValidationError(
                f"{path}.MANUAL_TRIG must be the node's only trigger"
            )
        if mode_name != "SWEEP" and "COUNT_OVER" in seen_conditions:
            raise _ValidationError(
                f"{path}.COUNT_OVER is only valid for SWEEP"
            )

        di_trigger = next(
            (t for t in triggers if t.condition == "DI_MATCH"),
            None,
        )
        if (
            di_trigger is not None
            and di_trigger.has_safety_timeout
            and "TIME_OUT" in seen_conditions
        ):
            raise _ValidationError(
                f"{path} cannot combine DI_MATCH.timeout "
                "with a TIME_OUT trigger"
            )

        return _FrontNodeSpec(
            front_id=front_id,
            mode_name=mode_name,
            static_phys=static_phys,
            packed_do_actions=packed_do_actions,
            triggers=triggers,
            dynamic=dynamic,
        )

    def _parse_dynamic(
        self,
        raw_dynamic: Any,
        static_phys: Dict[str, Dict[str, List[float]]],
        path: str,
    ) -> Dict[str, Any]:
        if not isinstance(raw_dynamic, dict):
            raise _ValidationError(f"{path} must be an object")

        step_time = self._require_int(
            raw_dynamic.get("stepTime"),
            f"{path}.stepTime",
            minimum=1,
        )
        count = self._require_int(
            raw_dynamic.get("count"),
            f"{path}.count",
            minimum=1,
        )
        steps = self._validate_physical_map(
            raw_dynamic.get("steps"),
            f"{path}.steps",
            allow_empty=False,
        )

        for channel, layers in steps.items():
            if channel not in static_phys:
                raise _ValidationError(
                    f"{path}.steps channel {channel} has no static base"
                )
            for layer in layers:
                if layer not in static_phys[channel]:
                    raise _ValidationError(
                        f"{path}.steps {channel}/{layer} "
                        "has no static base"
                    )

        return {
            "stepTime": step_time,
            "count": count,
            "steps": steps,
        }

    def _parse_trigger(
        self,
        raw_trigger: Any,
        path: str,
        id_set: set,
    ) -> _TriggerSpec:
        if not isinstance(raw_trigger, dict):
            raise _ValidationError(f"{path} must be an object")

        condition = raw_trigger.get("condition")
        if condition not in _CONDITIONS:
            raise _ValidationError(
                f"{path}.condition must be one of "
                f"{', '.join(sorted(_CONDITIONS))}"
            )

        next_id = self._require_int(
            raw_trigger.get("next_id"),
            f"{path}.next_id",
        )
        if next_id != -2 and next_id not in id_set:
            raise _ValidationError(
                f"{path}.next_id references unknown node {next_id}"
            )

        if "post_condition_delay_ms" not in raw_trigger:
            raise _ValidationError(
                f"{path}.post_condition_delay_ms is required"
            )
        post_delay = self._require_int(
            raw_trigger["post_condition_delay_ms"],
            f"{path}.post_condition_delay_ms",
            minimum=0,
        )

        timeout_ms = None
        di_mask = None
        if condition == "TIME_OUT":
            timeout_ms = self._require_int(
                raw_trigger.get("timeout"),
                f"{path}.timeout",
                minimum=1,
            )
        elif condition == "DI_MATCH":
            di_mask = self._require_int(
                raw_trigger.get("di_mask"),
                f"{path}.di_mask",
                minimum=1,
                maximum=0x7FF,
            )
            if not (di_mask & 0xFF):
                raise _ValidationError(
                    f"{path}.di_mask must select at least one DI"
                )
            if "timeout" in raw_trigger:
                timeout_ms = self._require_int(
                    raw_trigger["timeout"],
                    f"{path}.timeout",
                    minimum=1,
                )

        return _TriggerSpec(
            condition=condition,
            next_front_id=next_id,
            real_target=0xFFFF if next_id == -2 else next_id,
            post_delay_ms=post_delay,
            timeout_ms=timeout_ms,
            di_mask=di_mask,
        )

    def _validate_physical_map(
        self,
        value: Any,
        path: str,
        *,
        allow_empty: bool,
    ) -> Dict[str, Dict[str, List[float]]]:
        if not isinstance(value, dict):
            raise _ValidationError(f"{path} must be an object")
        if not allow_empty and not value:
            raise _ValidationError(f"{path} must not be empty")

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
                        f"{path}.{channel}.{layer} "
                        "must be [amplitude, frequency_or_phase]"
                    )

                pair: List[float] = []
                for pair_index, number in enumerate(raw_pair):
                    if (
                        isinstance(number, bool)
                        or not isinstance(number, (int, float))
                        or not math.isfinite(float(number))
                    ):
                        raise _ValidationError(
                            f"{path}.{channel}.{layer}[{pair_index}] "
                            "must be a finite number"
                        )
                    pair.append(float(number))
                result[channel][layer] = pair
        return result

    def _pack_do_actions(self, value: Any, path: str) -> List[int]:
        if not isinstance(value, list):
            raise _ValidationError(f"{path} must be an array")

        packed: List[int] = []
        for index, action in enumerate(value):
            action_path = f"{path}[{index}]"
            if not isinstance(action, dict):
                raise _ValidationError(f"{action_path} must be an object")
            delay = self._require_int(
                action.get("delay"),
                f"{action_path}.delay",
                minimum=0,
                maximum=0xFFFF,
            )
            mask = self._require_int(
                action.get("mask"),
                f"{action_path}.mask",
                minimum=0,
                maximum=0xFF,
            )
            packed.append((delay << 8) | mask)
        return packed

    @staticmethod
    def _canonical_index(
        value: Any,
        path: str,
        *,
        minimum: int,
        maximum: int,
    ) -> str:
        if not isinstance(value, str):
            raise _ValidationError(f"{path} key must be a string")
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
            raise _ValidationError(f"{path} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise _ValidationError(f"{path} must be <= {maximum}")
        return value

    def _allocate_route_targets(
        self,
        specs: List[_FrontNodeSpec],
    ):
        used_ids = {spec.front_id for spec in specs}
        preferred_ids = {
            1000 + trigger.real_target
            for spec in specs
            for trigger in spec.triggers
            if (
                trigger.post_delay_ms > 0
                and trigger.real_target != 0xFFFF
            )
        }
        delay_by_key: Dict[Tuple[int, int], int] = {}
        delay_specs: Dict[int, _DelayNodeSpec] = {}

        def allocate_fallback() -> int:
            for candidate in range(1001, 0xFFFF):
                if (
                    candidate not in used_ids
                    and candidate not in preferred_ids
                ):
                    return candidate
            raise _ValidationError(
                "no internal node ID is available for post delay"
            )

        for spec in specs:
            for trigger in spec.triggers:
                if trigger.post_delay_ms == 0:
                    trigger.compiled_target = trigger.real_target
                    continue

                key = (
                    trigger.post_delay_ms,
                    trigger.real_target,
                )
                if key in delay_by_key:
                    trigger.compiled_target = delay_by_key[key]
                    continue

                candidate = None
                if trigger.real_target != 0xFFFF:
                    preferred = 1000 + trigger.real_target
                    if preferred not in used_ids:
                        candidate = preferred
                if candidate is None:
                    candidate = allocate_fallback()

                if candidate in (0x0000, 0xFFFF):
                    raise _ValidationError(
                        "internal post-delay ID is reserved"
                    )
                used_ids.add(candidate)
                delay_by_key[key] = candidate
                delay_specs[candidate] = _DelayNodeSpec(
                    node_id=candidate,
                    post_delay_ms=trigger.post_delay_ms,
                    target_engine_id=trigger.real_target,
                )
                trigger.compiled_target = candidate

        for spec in specs:
            route_by_target: Dict[int, _TriggerSpec] = {}
            for trigger in spec.triggers:
                target = trigger.compiled_target
                if target is None:
                    raise _ValidationError(
                        f"node {spec.front_id} has an unresolved target"
                    )
                previous = route_by_target.get(target)
                if previous is not None:
                    raise _ValidationError(
                        f"node {spec.front_id} routes "
                        f"{previous.condition} and {trigger.condition} "
                        "to the same compiled target; their actual "
                        "end condition cannot be distinguished"
                    )
                route_by_target[target] = trigger

        self._delay_by_engine = delay_specs

    def _compile_nodes(self, specs: List[_FrontNodeSpec]):
        all_nodes: Dict[int, ApiNodeData] = {}
        main_by_engine: Dict[int, _FrontNodeSpec] = {}
        routes_by_source: Dict[int, List[_TriggerSpec]] = {}
        manual_by_front: Dict[int, _TriggerSpec] = {}

        bases = self.compileStaticNodes(
            {spec.front_id: spec.static_phys for spec in specs},
            {spec.front_id: [trigger.real_target for trigger in spec.triggers] for spec in specs},
            specs[0].front_id,
        )

        for spec in specs:
            api_node = ApiNodeData(
                mode=1 if spec.mode_name == "STATIC" else 2,
                base=bases[spec.front_id],
                doActions=spec.packed_do_actions or None,
            )

            if spec.mode_name == "SWEEP":
                dynamic = spec.dynamic
                if dynamic is None:
                    raise _ValidationError(
                        f"node {spec.front_id} has no SWEEP data"
                    )
                one_step = self.physDictToReg(
                    dynamic["steps"],
                    is_delta=True,
                )
                api_node.interval = dynamic["stepTime"]
                api_node.steps = [
                    one_step
                    for _ in range(dynamic["count"])
                ]

            for trigger in spec.triggers:
                target = trigger.compiled_target
                if target is None:
                    raise _ValidationError(
                        f"node {spec.front_id} has an unresolved target"
                    )
                if trigger.condition == "TIME_OUT":
                    api_node.timeoutMs = trigger.timeout_ms
                    api_node.timeoutId = target
                elif trigger.condition == "DI_MATCH":
                    api_node.diMatchMask = trigger.di_mask
                    api_node.diMatchId = target
                    if trigger.timeout_ms is not None:
                        api_node.timeoutMs = trigger.timeout_ms
                        api_node.timeoutId = target
                elif trigger.condition == "COUNT_OVER":
                    api_node.countOverId = target
                elif trigger.condition == "MANUAL_TRIG":
                    manual_by_front[spec.front_id] = trigger

            all_nodes[spec.front_id] = api_node
            main_by_engine[spec.front_id] = spec
            routes_by_source[spec.front_id] = spec.triggers

        for delay_id, delay_spec in self._delay_by_engine.items():
            all_nodes[delay_id] = ApiNodeData(
                mode=1,
                base={},
                timeoutMs=delay_spec.post_delay_ms,
                timeoutId=delay_spec.target_engine_id,
            )

        if any(node_id in (0x0000, 0xFFFF) for node_id in all_nodes):
            raise _ValidationError(
                "compiled nodes must not overwrite 0 or FFFF"
            )

        self._all_nodes = all_nodes
        self._main_by_engine = main_by_engine
        self._routes_by_source = routes_by_source
        self._manual_by_front = manual_by_front

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
            or hw_ts < 0
        ):
            return
        try:
            if nodeId in self._main_by_engine:
                if tick == 0:
                    self._enter_main_node(nodeId, hw_ts)
                return

            if nodeId in self._delay_by_engine and tick == 0:
                self._enter_delay_node(nodeId, hw_ts)
        except Exception as exc:
            logger.exception("universal_sequence onUpdate failed")
            self._stop_with_error(
                f"universal_sequence update error: {exc}"
            )

    def _enter_main_node(self, node_id: int, hw_ts: int):
        if self._current_visit is not None:
            condition = self._infer_end_condition(
                node_id,
                hw_ts,
            )
            if condition is None:
                raise RuntimeError(
                    f"unexpected transition from "
                    f"{self._current_front_id} to {node_id}"
                )
            self._finish_current_visit(condition, hw_ts)
        elif self._phase == self._PHASE_WAIT_ENTRY:
            if node_id != self._entry_engine_id:
                raise RuntimeError(
                    f"expected entry node {self._entry_engine_id}, "
                    f"received {node_id}"
                )
            if self._experiment_start_di is None:
                self._experiment_start_di = int(self.ctrl.di) & 0xFF
        elif self._phase == self._PHASE_POST_DELAY:
            delay_id = self._current_engine_id
            delay = (
                self._delay_by_engine.get(delay_id)
                if delay_id is not None
                else None
            )
            expected = (
                delay.target_engine_id
                if delay is not None
                else None
            )
            if node_id != expected:
                raise RuntimeError(
                    f"delay node {self._current_engine_id} expected "
                    f"target {expected}, received {node_id}"
                )
        else:
            raise RuntimeError(
                f"node {node_id} arrived in phase {self._phase}"
            )

        spec = self._main_by_engine[node_id]
        entry_di = int(self.ctrl.di) & 0xFF
        self._current_front_id = spec.front_id
        self._current_engine_id = node_id
        self._phase = self._PHASE_ACTIVE_MAIN
        self._manual_pending = False
        self._pending_end_condition = None
        self._current_visit = _VisitRecord(
            front_id=spec.front_id,
            entry_hw_ts=hw_ts & 0xFFFFFFFF,
            entry_host_ts=time.perf_counter(),
            entry_di=entry_di,
            last_di=entry_di,
        )
        self._evaluate_current_di_match(entry_di)
        self.ctrl.sendNodeUpdate(spec.front_id)

    def _enter_delay_node(self, node_id: int, hw_ts: int):
        if self._current_visit is None:
            raise RuntimeError(
                f"delay node {node_id} has no active source visit"
            )
        condition = self._infer_end_condition(node_id, hw_ts)
        if condition is None:
            raise RuntimeError(
                f"delay node {node_id} is not reachable from "
                f"{self._current_front_id}"
            )
        self._finish_current_visit(condition, hw_ts)
        self._current_engine_id = node_id
        self._current_front_id = None
        self._phase = self._PHASE_POST_DELAY
        self._manual_pending = False
        self._pending_end_condition = None

    def onDi(self, di: int, hw_ts: int):
        if (
            not self.isActive
            or self.ctrl is None
            or not self.ctrl.running
            or self._stopping
            or self._phase != self._PHASE_ACTIVE_MAIN
            or self._current_visit is None
        ):
            return
        try:
            visit = self._current_visit
            di_value = int(di) & 0xFF
            changed = (di_value ^ visit.last_di) & 0xFF
            if changed:
                elapsed_us = self._validated_elapsed_us(
                    visit,
                    hw_ts,
                )
                if elapsed_us is not None:
                    elapsed_sec = round(
                        elapsed_us / 1_000_000.0,
                        6,
                    )
                    for bit in range(8):
                        if (
                            changed & (1 << bit)
                            and visit.first_change_sec[bit] is None
                        ):
                            visit.first_change_sec[bit] = elapsed_sec
            visit.last_di = di_value
            self._evaluate_current_di_match(di_value)
        except Exception as exc:
            logger.exception("universal_sequence onDi failed")
            self._stop_with_error(
                f"universal_sequence DI error: {exc}"
            )

    def onTerminal(self, hw_ts: int):
        self._terminal_hw_ts = (
            hw_ts & 0xFFFFFFFF
            if (
                isinstance(hw_ts, int)
                and not isinstance(hw_ts, bool)
                and hw_ts >= 0
            )
            else None
        )

    def _evaluate_current_di_match(
        self,
        di_value: int,
    ):
        visit = self._current_visit
        if visit is None or visit.di_match_observed:
            return
        trigger = self._find_trigger(
            self._current_front_id,
            "DI_MATCH",
        )
        if trigger is None or trigger.di_mask is None:
            return

        mask = trigger.di_mask
        watch = mask & 0xFF
        reference = (
            visit.entry_di
            if mask & 0x200
            else (
                self._experiment_start_di
                if self._experiment_start_di is not None
                else visit.entry_di
            )
        )
        if mask & 0x400:
            reference ^= 0xFF
        value = (int(di_value) ^ reference) & watch
        matched = value == watch if mask & 0x100 else value != 0
        if matched:
            visit.di_match_observed = True

    # ------------------------------------------------------------------
    # Manual command
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
                    "universal_sequence command must be an object"
                )
                return
            if msg.get("module") != "universal_sequence":
                self.ctrl.sendError(
                    "Command module must be universal_sequence"
                )
                return
            if msg.get("cmd") != "next_node":
                self.ctrl.sendError(
                    f"Unknown universal_sequence command: "
                    f"{msg.get('cmd')}"
                )
                return
            if (
                self._phase != self._PHASE_ACTIVE_MAIN
                or self._current_front_id is None
                or self._current_visit is None
            ):
                self.ctrl.sendError(
                    "universal_sequence is not in a business node"
                )
                return

            trigger = self._manual_by_front.get(
                self._current_front_id
            )
            if trigger is None:
                self.ctrl.sendError(
                    "Current node does not accept next_node"
                )
                return
            if self._manual_pending:
                self.ctrl.sendError(
                    "A next_node transition is already pending"
                )
                return

            target = trigger.compiled_target
            if target is None:
                raise RuntimeError("manual target is unresolved")

            self._manual_pending = True
            self._current_visit.pending_manual = True
            if target == 0xFFFF:
                self._pending_end_condition = "MANUAL_TRIG"
                self._stopping = True
                try:
                    self.ctrl.stopTest()
                except Exception:
                    self._stopping = False
                    raise
                return

            if not self.ctrl.trigNode(target):
                self._manual_pending = False
                self._current_visit.pending_manual = False
        except Exception as exc:
            logger.exception("universal_sequence command failed")
            self._stop_with_error(
                f"universal_sequence command error: {exc}"
            )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _infer_end_condition(
        self,
        compiled_target: int,
        _exit_hw_ts: Optional[int],
    ) -> Optional[str]:
        source_id = self._current_front_id
        visit = self._current_visit
        if source_id is None or visit is None:
            return None

        candidates = [
            trigger
            for trigger in self._routes_by_source.get(source_id, [])
            if trigger.compiled_target == compiled_target
        ]
        if not candidates:
            return None
        if len(candidates) != 1:
            raise RuntimeError(
                f"node {source_id} has ambiguous routes to "
                f"{compiled_target}"
            )

        trigger = candidates[0]
        if trigger.condition == "MANUAL_TRIG":
            return (
                "MANUAL_TRIG"
                if self._manual_pending or visit.pending_manual
                else None
            )
        if trigger.condition == "DI_MATCH":
            if visit.di_match_observed:
                return "DI_MATCH"
            return (
                "TIME_OUT"
                if trigger.has_safety_timeout
                else "DI_MATCH"
            )
        return trigger.condition

    def _finish_current_visit(
        self,
        condition: str,
        exit_hw_ts: Optional[int],
    ):
        visit = self._current_visit
        if visit is None:
            return

        duration_sec = None
        if exit_hw_ts is not None:
            elapsed_us = self._validated_elapsed_us(
                visit,
                exit_hw_ts,
            )
            if elapsed_us is not None:
                duration_sec = round(
                    elapsed_us / 1_000_000.0,
                    6,
                )

        row: Dict[str, Any] = {
            "id": visit.front_id,
            "duration_sec": duration_sec,
            "end_condition": condition,
        }
        for index, field_name in enumerate(_DI_REPORT_FIELDS):
            row[field_name] = visit.first_change_sec[index]
        self._rows.append(row)
        self._current_visit = None

    @staticmethod
    def _validated_elapsed_us(
        visit: _VisitRecord,
        end_hw_ts: int,
    ) -> Optional[int]:
        elapsed_us = (
            (end_hw_ts & 0xFFFFFFFF)
            - visit.entry_hw_ts
        ) & 0xFFFFFFFF
        host_elapsed_us = max(
            0,
            int(
                (time.perf_counter() - visit.entry_host_ts)
                * 1_000_000
            ),
        )
        reasonable_limit = max(
            10_000_000,
            host_elapsed_us * 2 + 5_000_000,
        )
        if elapsed_us > reasonable_limit:
            return None
        return elapsed_us

    def _onStop(self):
        if self._report_sent:
            return
        was_ctrl_running = bool(
            self.ctrl is not None and self.ctrl.running
        )
        self._stopping = True
        try:
            if self._current_visit is not None:
                exit_hw_ts = self._terminal_hw_ts
                if self._pending_end_condition:
                    condition = self._pending_end_condition
                elif was_ctrl_running:
                    condition = self._infer_end_condition(
                        0xFFFF,
                        exit_hw_ts,
                    ) or "TERMINAL"
                else:
                    condition = "TERMINAL"
                self._finish_current_visit(
                    condition,
                    exit_hw_ts,
                )

            self.ctrl.sendReport({"rows": self._rows})
            self._report_sent = True
        except Exception:
            logger.exception(
                "Failed to build universal_sequence report"
            )
        finally:
            self._phase = self._PHASE_STOPPED
            self._current_front_id = None
            self._current_engine_id = None
            self._current_visit = None
            self._manual_pending = False

    def _find_trigger(
        self,
        front_id: Optional[int],
        condition: str,
    ) -> Optional[_TriggerSpec]:
        if front_id is None:
            return None
        return next(
            (
                trigger
                for trigger in self._routes_by_source.get(front_id, [])
                if trigger.condition == condition
            ),
            None,
        )

    def _stop_with_error(self, reason: str):
        if self._stopping:
            return
        self._pending_end_condition = "ERROR"
        self._stopping = True
        try:
            self.ctrl.stopTest(reason)
        except Exception:
            logger.exception(
                "Failed to stop universal_sequence after error"
            )
