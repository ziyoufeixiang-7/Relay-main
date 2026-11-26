import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from logic.Calibration import calib
from logic.FPGACodec import HWConfig
import pkgutil
import importlib
import inspect
import api

logger = logging.getLogger("BaseApi")

@dataclass
class ApiNodeData:
    mode: int
    
    interval: Optional[int] = None
    resetTime: Optional[int] = None

    base: Optional[Dict[int, Dict[int, List[int]]]] = None
    reset: Optional[Dict[int, Dict[int, List[int]]]] = None
    steps: Optional[List[Dict[int, Dict[int, List[int]]]]] = None
    gate: Optional[List[int]] = None

    resetDo: Optional[int] = None
    doActions: Optional[List[int]] = None

    countOverId: Optional[int] = None
    diMatchMask: Optional[int] = None
    diMatchId: Optional[int] = None
    timeoutMs: Optional[int] = None
    timeoutId: Optional[int] = None
    
class BaseApi:
    _REGISTRY = []

    @staticmethod
    def create(module_name: str) -> 'BaseApi':
        for _, name, _ in pkgutil.iter_modules(api.__path__):
            if not name.startswith("Api") or name == "ApiNodeData":
                continue
            try:
                mod = importlib.import_module(f"api.{name}")
                for _, cls in inspect.getmembers(mod, lambda c: inspect.isclass(c) and issubclass(c, BaseApi) and c is not BaseApi):
                    if module_name in (getattr(cls, "MODULE_KEYS", None) or [getattr(cls, "MODULE_KEY", None)]):
                        return cls()
            except Exception as e:
                logger.warning(f"Failed to load API module {name}: {e}")
        if not BaseApi._REGISTRY:
            try:
                importlib.import_module("api.Registry")
            except Exception as e:
                logger.warning(f"Failed to load api.Registry: {e}")
        for cls in BaseApi._REGISTRY:
            if module_name in (getattr(cls, "MODULE_KEYS", None) or [getattr(cls, "MODULE_KEY", None)]):
                return cls()
        return None

    def __init__(self):
        self.ctrl = None
        self.isActive = False
        self._calibrationSnapshot = calib.GetSnapshot()

    def setup(self, ctrl, params: Dict[str, Any]):
        self.ctrl = ctrl
        self.isActive = True
        self._calibrationSnapshot = calib.GetSnapshot()
        if self._usesLegacyCalibration(params):
            self._calibrationSnapshot = calib.LegacySnapshot(self._calibrationSnapshot)
        self._onSetup(params)

    def _usesLegacyCalibration(self, params: Dict[str, Any]) -> bool:
        # This release calibrates static experiments only. Sweep APIs use one
        # legacy snapshot for base/reset/wait/deltas, not mixed coefficients.
        keys = getattr(self, "MODULE_KEYS", None) or [getattr(self, "MODULE_KEY", None)]
        if "slip_test" in keys or "raw_test" in keys:
            return True
        if not isinstance(params, dict):
            return False  # Leave input validation to the experiment API.
        if "steps_gradient_test" in keys:
            config = params.get("sys", {})
            return not isinstance(config, dict) or config.get("mode", 1) != 0
        if "universal_sequence" in keys:
            nodes = params.get("nodes", [])
            return isinstance(nodes, list) and any(
                isinstance(node, dict) and node.get("mode") == "SWEEP" for node in nodes
            )
        return False

    def onStop(self):
        if not self.isActive:
            return
        self.isActive = False
        self._onStop()

    def compileStaticNodes(self, nodes, successors, entry):
        channels = {HWConfig.UnmapChannel(ch) for ch in HWConfig.ACTIVE_CHANNELS
                    if self._calibrationSnapshot.acCurves[ch]}
        if not channels:
            return {node: self.physDictToReg(data) for node, data in nodes.items()}
        from logic.StaticCalibration import resolve_static_nodes
        physical, frequencies = resolve_static_nodes(nodes, successors, entry, channels)
        return {node: self.physDictToReg(data, previous_state={}, fundamental_hz=frequencies[node])
                for node, data in physical.items()}

    def physDictToReg(self, phys_dict: Dict[str, Dict[str, List[float]]], is_delta: bool = False,
                      *, previous_state=None, fundamental_hz=None) -> Dict[int, Dict[int, List[int]]]:
        result = {}
        state = getattr(self.ctrl, "state", {}) if previous_state is None else previous_state
        for ch_str, layers in phys_dict.items():
            hw_ch = HWConfig.MapChannel(int(ch_str))
            layers = {int(layer): values for layer, values in layers.items()}
            previous = state.get(hw_ch, {})
            old_freq = previous.get(0, [0, HWConfig.ConvertFreqToReg(50.0)])[1] / HWConfig.FREQ
            freq = (fundamental_hz or {}).get(int(ch_str), old_freq)
            if not is_delta and 0 in layers:
                freq = HWConfig.ConvertFreqToReg(layers[0][1]) / HWConfig.FREQ
            converted = {
                layer: list(calib.PhysToReg(hw_ch, layer, values[0], values[1], is_delta,
                                           fundamentalHz=freq, snapshot=self._calibrationSnapshot))
                for layer, values in layers.items()
            }
            # A static frequency-only update must preserve the physical amplitude
            # of already active harmonics, not their old compensated registers.
            if not is_delta and freq != old_freq and self._calibrationSnapshot.acCurves[hw_ch]:
                for layer, registers in previous.items():
                    if layer and layer not in converted and registers[0]:
                        amplitude, phase = calib.RegToPhys(
                            hw_ch, layer, *registers, fundamentalHz=old_freq,
                            snapshot=self._calibrationSnapshot, rounded=False,
                        )
                        converted[layer] = list(calib.PhysToReg(
                            hw_ch, layer, amplitude, phase, fundamentalHz=freq,
                            snapshot=self._calibrationSnapshot,
                        ))
            result[hw_ch] = converted
        return result

    def _onSetup(self, params: Dict[str, Any]):
        pass

    def _onStop(self):
        pass

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        pass

    def onDi(self, di: int, hw_ts: int):
        pass

    def onWebCommand(self, msg: Dict[str, Any]):
        pass

