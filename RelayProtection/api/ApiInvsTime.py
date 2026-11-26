import math
import logging
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.BaseApi import BaseApi, ApiNodeData

logger = logging.getLogger("ApiInvsTime")

# --- I-t Constants ---
FAULT_PHASE_MAP = {
    "A_GROUND":   ((True,   0), (False,    0), (False,    0)),
    "B_GROUND":   ((False,  0), (True,  -120), (False,    0)),
    "C_GROUND":   ((False,  0), (False,    0), (True,   120)),
    "AB_PHASE":   ((True,   0), (True,   180), (False,    0)),
    "BC_PHASE":   ((False,  0), (True,  -120), (True,    60)),
    "CA_PHASE":   ((True, -60), (False,    0), (True,   120)),
    "ABC_PHASE":  ((True,   0), (True,  -120), (True,   120)),
}

SINGLE_PATH_MAP = {
    "A_GROUND":   (True,  False, False),
    "B_GROUND":   (False, True,  False),
    "C_GROUND":   (False, False, True),
    "AB_PHASE":   (True,  True,  False),
    "BC_PHASE":   (False, True,  True),
    "CA_PHASE":   (True,  False, True),
    "ABC_PHASE":  (True,  True,  True),
}

DELTA_ANGLES = {
    "A_GROUND":  [(True, 0),    (False, 0),    (False, 0)],
    "B_GROUND":  [(False, 0),   (True, -120),  (False, 0)],
    "C_GROUND":  [(False, 0),   (False, 0),    (True, 120)],
    "AB_PHASE":  [(True, 30),   (True, 210),   (False, 0)],
    "BC_PHASE":  [(False, 0),   (True, -90),   (True, 90)],
    "CA_PHASE":  [(True, 330),  (False, 0),    (True, 150)],
    "ABC_PHASE": [(True, 0),    (True, -120),  (True, 120)],
}

LINE_V_ANGLE = {"AB_PHASE": 30, "BC_PHASE": -90, "CA_PHASE": 150}
FAULT_PAIR   = {"AB_PHASE": (0, 1, 2), "BC_PHASE": (1, 2, 0), "CA_PHASE": (2, 0, 1)}

V_CH_MAP = {"GROUP1_UABC": (0, 1, 2), "GROUP2_UXYZ": (3, 4, 5)}
I_CH_MAP = {"GROUP1_IABC": (6, 7, 8), "GROUP2_IXYZ": (9, 10, 11)}

# --- VF-t Constants ---
V_OUTPUT_MAP = {
    "UA":        [(0, 0)],
    "UB":        [(1, -120)],
    "UC":        [(2, 120)],
    "UAB":       [(0, 0), (1, 180)],
    "UBC":       [(1, -120), (2, 60)],
    "UCA":       [(2, 120), (0, -60)],
    "UABC_POS":  [(0, 0), (1, -120), (2, 120)],
    "UABC_NEG":  [(0, 0), (1, 120), (2, -120)],
    "UABC_ZERO": [(0, 0), (1, 0), (2, 0)],
}

# --- Math Helpers ---
def _PolarToRect(mag, angle_deg):
    rad = math.radians(angle_deg)
    return (mag * math.cos(rad), mag * math.sin(rad))

def _RectToPolar(re, im):
    mag = math.sqrt(re * re + im * im)
    angle = math.degrees(math.atan2(im, re))
    return (mag, angle)

def _VectorAdd(mag1, ang1, mag2, ang2):
    r1, i1 = _PolarToRect(mag1, ang1)
    r2, i2 = _PolarToRect(mag2, ang2)
    return _RectToPolar(r1 + r2, i1 + i2)

class PointTracker:
    def __init__(self, test_id: int, fault_node_id: int, value: float):
        self.test_id = test_id
        self.fault_node_id = fault_node_id
        self.value = value
        self.entry_hw_ts: Optional[int] = None
        self.entry_time_mono: float = 0.0
        self.actual_time: Optional[float] = None
        self.tripped: bool = False

class ApiInvsTime(BaseApi):
    MODULE_KEYS = ["inverse_time_it_test", "inverse_time_vft_test"]

    def __init__(self):
        super().__init__()
        self.trackers: List[PointTracker] = []
        self.currentTracker: Optional[PointTracker] = None
        self.lastKnownDi = -1
        self.lastDiChangeHwTs = 0
        self.diMatchedThisNode = False

        self.faultNodeIds = set()
        self.trackerByFaultNode: Dict[int, PointTracker] = {}
        self.currentNodeId = -1
        self.faultEntryDi = 0
        self.tripHwTs: Optional[int] = None



    def _MakeChannelReg(self, ch_idx: int, amplitude: float, angle_deg: float, freq_hz: float) -> dict:
        return {
            "0": [0.0, freq_hz],
            "1": [amplitude, angle_deg]
        }

    # --- I-t Compute Methods ---
    def _GetDirScale(self, i_def: str, payload: dict, fault_type: str, is_ground: bool) -> float:
        if i_def == "FAULT_CURRENT":
            return 1.0
        elif i_def == "NEGATIVE_SEQUENCE":
            neg_ch = payload.get("negativeSequenceChannel", "I2")
            if neg_ch == "I2":
                return 3.0 if is_ground else math.sqrt(3)
            else:
                return 1.0 if is_ground else 1.0 / math.sqrt(3)
        elif i_def == "ZERO_SEQUENCE":
            return 3.0 if is_ground else 0.0
        return 1.0

    def _GetAbcSpecialDeltas(self, i_def: str, payload: dict, value: float, line_angle: float) -> list:
        if i_def == "NEGATIVE_SEQUENCE":
            neg_ch = payload.get("negativeSequenceChannel", "I2")
            amp = value if neg_ch == "I2" else value / 3.0
            return [(amp, -line_angle), (amp, 120 - line_angle), (amp, -120 - line_angle)]
        elif i_def == "ZERO_SEQUENCE":
            return [(value, -line_angle), (value, -line_angle), (value, -line_angle)]
        return [(0, 0), (0, 0), (0, 0)]

    def _ComputeCurrents(self, value: float, payload: dict, i_channels: tuple, freq_hz: float) -> dict:
        i_def = payload.get("iDefinition", "FAULT_CURRENT")
        fault_type = payload.get("faultType", "A_GROUND")
        dir_cfg = payload.get("directionality", {})
        dir_enabled = dir_cfg.get("enabled", False)
        ct_polarity = dir_cfg.get("ctPolarity", "TOWARD_COMPONENT")

        result = {}
        if i_def == "SINGLE_PATH":
            active = SINGLE_PATH_MAP.get(fault_type, (True, False, False))
            for ch_idx, is_active in zip(i_channels, active):
                amp = value if is_active else 0.0
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, amp, 0.0, freq_hz)
            return result

        if dir_enabled:
            line_angle = dir_cfg.get("lineAngle", 70.0)
            i_pre = payload.get("iBeforeFault", 0.0)
            pre_load_angle = dir_cfg.get("preLoadAngle", 0.0)
            is_ground = fault_type in ("A_GROUND", "B_GROUND", "C_GROUND")

            pre_phases = [(i_pre, -pre_load_angle), (i_pre, -120 - pre_load_angle), (i_pre, 120 - pre_load_angle)]

            if fault_type == "ABC_PHASE" and i_def in ("NEGATIVE_SEQUENCE", "ZERO_SEQUENCE"):
                delta_list = self._GetAbcSpecialDeltas(i_def, payload, value, line_angle)
            else:
                scale = self._GetDirScale(i_def, payload, fault_type, is_ground)
                i_delta = value * scale
                angle_table = DELTA_ANGLES.get(fault_type, DELTA_ANGLES["A_GROUND"])
                delta_list = []
                for has_d, base_ang in angle_table:
                    if has_d:
                        delta_list.append((i_delta, base_ang - line_angle))
                    else:
                        delta_list.append((0.0, 0.0))

            for ch_idx, (pre_mag, pre_ang), (d_mag, d_ang) in zip(i_channels, pre_phases, delta_list):
                if d_mag > 1e-9:
                    total_mag, total_ang = _VectorAdd(pre_mag, pre_ang, d_mag, d_ang)
                else:
                    total_mag, total_ang = pre_mag, pre_ang
                if ct_polarity == "TOWARD_BUSBAR":
                    total_ang += 180.0
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, total_mag, total_ang, freq_hz)
            return result

        # Non-directional
        phase_map = FAULT_PHASE_MAP.get(fault_type, FAULT_PHASE_MAP["A_GROUND"])
        is_ground = fault_type in ("A_GROUND", "B_GROUND", "C_GROUND")

        if fault_type == "ABC_PHASE" and i_def in ("NEGATIVE_SEQUENCE", "ZERO_SEQUENCE"):
            if i_def == "NEGATIVE_SEQUENCE":
                neg_ch = payload.get("negativeSequenceChannel", "I2")
                amp = value if neg_ch == "I2" else value / 3.0
                angles = [0, 120, -120]
            else:
                amp = value
                angles = [0, 0, 0]
            for ch_idx, angle in zip(i_channels, angles):
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, amp, angle, freq_hz)
            return result

        if i_def == "FAULT_CURRENT":
            scale = 1.0
        elif i_def == "NEGATIVE_SEQUENCE":
            neg_ch = payload.get("negativeSequenceChannel", "I2")
            if neg_ch == "I2":
                scale = 3.0 if is_ground else math.sqrt(3)
            else:
                scale = 1.0 if is_ground else 1.0 / math.sqrt(3)
        elif i_def == "ZERO_SEQUENCE":
            scale = 3.0 if is_ground else 0.0
        else:
            scale = 1.0

        iph = value * scale
        for ch_idx, (active, angle) in zip(i_channels, phase_map):
            amp = iph if active else 0.0
            result[str(ch_idx)] = self._MakeChannelReg(ch_idx, amp, angle, freq_hz)
        return result

    def _ComputeDirVoltages(self, payload: dict, is_fault: bool, v_channels: tuple, freq_hz: float) -> dict:
        dir_cfg = payload.get("directionality", {})
        v_before = dir_cfg.get("vBeforeFault", 57.735)
        v_fault = dir_cfg.get("vFault", 20.0)
        fault_type = payload.get("faultType", "A_GROUND")

        ch_a, ch_b, ch_c = v_channels
        normal = {ch_a: 0, ch_b: -120, ch_c: 120}
        result = {}

        if not is_fault:
            for ch_idx, angle in normal.items():
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v_before, angle, freq_hz)
            return result

        if fault_type in ("A_GROUND", "B_GROUND", "C_GROUND"):
            ground_map = {"A_GROUND": ch_a, "B_GROUND": ch_b, "C_GROUND": ch_c}
            fault_ch = ground_map[fault_type]
            for ch_idx, ang in normal.items():
                v = v_fault if ch_idx == fault_ch else v_before
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v, ang, freq_hz)

        elif fault_type in ("AB_PHASE", "BC_PHASE", "CA_PHASE"):
            uv_angle = LINE_V_ANGLE[fault_type]
            idx_x, idx_y, idx_z = FAULT_PAIR[fault_type]
            ch_x, ch_y, ch_z = v_channels[idx_x], v_channels[idx_y], v_channels[idx_z]

            uxy_r, uxy_i = _PolarToRect(v_fault, uv_angle)
            nz_r, nz_i = _PolarToRect(v_before, normal[ch_z] + 180)
            ux_mag, ux_ang = _RectToPolar((uxy_r + nz_r) / 2, (uxy_i + nz_i) / 2)
            uy_mag, uy_ang = _RectToPolar((nz_r - uxy_r) / 2, (nz_i - uxy_i) / 2)

            result[str(ch_x)] = self._MakeChannelReg(ch_x, ux_mag, ux_ang, freq_hz)
            result[str(ch_y)] = self._MakeChannelReg(ch_y, uy_mag, uy_ang, freq_hz)
            result[str(ch_z)] = self._MakeChannelReg(ch_z, v_before, normal[ch_z], freq_hz)

        elif fault_type == "ABC_PHASE":
            for ch_idx, ang in normal.items():
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v_fault, ang, freq_hz)

        return result

    # --- VF-t Compute Methods ---
    def _ComputeVoltages(self, voltage: float, freq_hz: float, payload: dict) -> dict:
        v_output_mode = payload.get("vOutputMode", "UA")
        angle = payload.get("angle", 0)
        channels = V_OUTPUT_MAP.get(v_output_mode, [(0, 0)])

        result = {}
        for ch_idx, angle_offset in channels:
            ch_angle = angle + angle_offset
            result[str(ch_idx)] = self._MakeChannelReg(ch_idx, voltage, ch_angle, freq_hz)
        return result

    def _ResolveFaultVf(self, value: float, payload: dict):
        test_project = payload.get("testProject", "V_T")
        v_before = payload.get("vBeforeFault", 57.735)
        f_before = payload.get("fBeforeFault", 50)
        vb = payload.get("vbValue", 57.735)
        fb = payload.get("fbValue", 50)
        vf_variable = payload.get("vfVariable", "VOLTAGE_AMPLITUDE_V")

        if test_project == "V_T":
            return value, f_before
        elif test_project == "F_T":
            return v_before, value
        elif test_project == "VF_T":
            if vf_variable == "VOLTAGE_AMPLITUDE_V":
                return value * fb, f_before
            else:
                return v_before, vb / value if value != 0 else f_before
        return v_before, f_before

    # --- Core Setup ---
    def _onSetup(self, params: Dict[str, Any]):
        sys_cfg = params.get("sys") or {}
        logic_mask = sys_cfg.get("logicMask", 0)
        statics = params.get("statics") or {}
        payload = params.get("payload") or {}
        test_points = params.get("testPoints") or []

        freq_hz = payload.get("ratedFrequency", 50.0)
        t_before = payload.get("tBeforeFault", 1000)
        fault_limit = payload.get("faultLimitTime", 60000)

        statics_reg = statics
        freq_50_hz = 50.0

        engine_nodes = {}
        self.faultNodeIds.clear()
        self.trackerByFaultNode.clear()
        self.trackers.clear()
        self.currentNodeId = -1

        is_vft = (self.ctrl._module == "inverse_time_vft_test")

        if not is_vft:
            # I-t Setup
            i_before = payload.get("iBeforeFault", 0.0)
            dir_enabled = payload.get("directionality", {}).get("enabled", False)
            output_mode = payload.get("outputMode", {})
            v_chs = V_CH_MAP.get(output_mode.get("voltage", "GROUP1_UABC"), (0, 1, 2))
            i_chs = I_CH_MAP.get(output_mode.get("current", "GROUP1_IABC"), (6, 7, 8))

            before_i_reg = self._ComputeCurrents(i_before, payload, i_chs, freq_hz)
            before_v_reg = self._ComputeDirVoltages(payload, False, v_chs, freq_hz) if dir_enabled else {}
            
            for idx, tp in enumerate(test_points):
                tp_id = tp.get("id", idx + 1)
                tp_value = tp.get("value", 0.0)

                before_node_id = 2 * idx + 1
                fault_node_id = 2 * idx + 2
                next_before = (2 * idx + 3) if idx < len(test_points) - 1 else 0xFFFF

                # Before Fault
                before_static = dict(statics_reg)
                before_static.update(before_i_reg)
                if dir_enabled: before_static.update(before_v_reg)

                
                # Apply calibration and HW conversion
                before_hw_static = self.physDictToReg(before_static)

                engine_nodes[before_node_id] = ApiNodeData(
                    mode=1, base=before_hw_static, timeoutMs=int(t_before), timeoutId=fault_node_id
                )

                # Fault
                fault_i_reg = self._ComputeCurrents(tp_value, payload, i_chs, freq_hz)
                fault_static = dict(statics_reg)
                fault_static.update(fault_i_reg)
                if dir_enabled: fault_static.update(self._ComputeDirVoltages(payload, True, v_chs, freq_hz))

                
                # Apply calibration and HW conversion
                fault_hw_static = self.physDictToReg(fault_static)

                di_mask = logic_mask | 0x200
                engine_nodes[fault_node_id] = ApiNodeData(
                    mode=1, base=fault_hw_static,
                    diMatchMask=di_mask, diMatchId=next_before,
                    timeoutMs=int(fault_limit) if fault_limit > 0 else None, timeoutId=next_before
                )

                self.faultNodeIds.add(fault_node_id)
                tracker = PointTracker(tp_id, fault_node_id, tp_value)
                self.trackers.append(tracker)
                self.trackerByFaultNode[fault_node_id] = tracker
        else:
            # VF-t Setup
            v_before = payload.get("vBeforeFault", 57.735)
            f_before = payload.get("fBeforeFault", 50)
            before_v_reg = self._ComputeVoltages(v_before, f_before, payload)

            for idx, tp in enumerate(test_points):
                tp_id = tp.get("id", idx + 1)
                tp_value = tp.get("value", 0.0)

                before_node_id = 2 * idx + 1
                fault_node_id = 2 * idx + 2
                next_before = (2 * idx + 3) if idx < len(test_points) - 1 else 0xFFFF

                # Before Fault
                before_static = dict(statics_reg)
                before_static.update(before_v_reg)

                
                # Apply calibration and HW conversion
                before_hw_static = self.physDictToReg(before_static)

                engine_nodes[before_node_id] = ApiNodeData(
                    mode=1, base=before_hw_static, timeoutMs=int(t_before), timeoutId=fault_node_id
                )

                # Fault
                fault_v, fault_f = self._ResolveFaultVf(tp_value, payload)
                fault_v_reg = self._ComputeVoltages(fault_v, fault_f, payload)
                
                fault_static = dict(statics_reg)
                fault_static.update(fault_v_reg)

                
                # Apply calibration and HW conversion
                fault_hw_static = self.physDictToReg(fault_static)

                di_mask = logic_mask | 0x200
                engine_nodes[fault_node_id] = ApiNodeData(
                    mode=1, base=fault_hw_static,
                    diMatchMask=di_mask, diMatchId=next_before,
                    timeoutMs=int(fault_limit) if fault_limit > 0 else None, timeoutId=next_before
                )

                self.faultNodeIds.add(fault_node_id)
                tracker = PointTracker(tp_id, fault_node_id, tp_value)
                self.trackers.append(tracker)
                self.trackerByFaultNode[fault_node_id] = tracker

        self.ctrl.upsertNodes(engine_nodes)
        # 实验节点从 1 开始；0(=0x0000) 是引擎保留的校准节点，不能写入/跳入
        self.ctrl.trigNode(1)

    # --- Telemetry & Core Logic ---
    def onDi(self, di: int, hw_ts: int):
        if not self.isActive: return

        if self.currentNodeId in self.faultNodeIds:
            api_node = self.ctrl.nodes.get(self.currentNodeId)
            if api_node and api_node.diMatchMask:
                mask = api_node.diMatchMask & 0xFF
                # 相对“进入故障节点时”的 DI 做边沿检测，并锁存首次跳闸时刻
                # （引擎用 0x200，同此口径），避免用最后一次 DI 变化导致动作时间偏大。
                if ((di ^ self.faultEntryDi) & mask) and not self.diMatchedThisNode:
                    self.diMatchedThisNode = True
                    self.tripHwTs = hw_ts

        self.lastKnownDi = di
        self.lastDiChangeHwTs = hw_ts

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        if not self.isActive: return

        if self.currentNodeId in self.faultNodeIds and self.currentNodeId != nodeId:
            tracker = self.trackerByFaultNode.get(self.currentNodeId)
            if tracker and tracker.entry_hw_ts is not None:
                if self.diMatchedThisNode and self.tripHwTs is not None:
                    elapsed_us = (self.tripHwTs - tracker.entry_hw_ts) & 0xFFFFFFFF
                    tracker.actual_time = elapsed_us / 1_000_000.0
                    tracker.tripped = True
                else:
                    tracker.actual_time = None
                    tracker.tripped = False

                msg = {
                    "type": "value_update",
                    "id": tracker.test_id,
                    "actualTime": round(tracker.actual_time, 4) if tracker.actual_time is not None else None,
                    "di": self.lastKnownDi
                }
                is_vft = (self.ctrl._module == "inverse_time_vft_test")
                if is_vft:
                    msg["vOrFValue"] = tracker.value
                else:
                    msg["currentI"] = tracker.value
                
                self.ctrl._send(msg)

            self.diMatchedThisNode = False

        self.currentNodeId = nodeId

        if tick == 0 and nodeId in self.faultNodeIds:
            tracker = self.trackerByFaultNode.get(nodeId)
            if tracker:
                tracker.entry_hw_ts = hw_ts
                tracker.entry_time_mono = time.monotonic()
                self.currentTracker = tracker
                self.diMatchedThisNode = False
                self.faultEntryDi = self.ctrl.di & 0xFF   # 故障节点入口 DI 基准（实时快照）
                self.tripHwTs = None

    def _onStop(self):
        # 末点结算：最后一个故障节点 DI_MATCH 会直接跳 0xFFFF 终止，
        # 不触发 onUpdate 的“离开故障节点”结算，这里补算当前测试点动作时间，
        # 否则最后一个测试点即便跳闸 actualTime 也永远为 None。
        tr = self.currentTracker
        if tr is not None and tr.actual_time is None and self.diMatchedThisNode \
                and self.tripHwTs is not None and tr.entry_hw_ts is not None:
            elapsed_us = (self.tripHwTs - tr.entry_hw_ts) & 0xFFFFFFFF
            tr.actual_time = elapsed_us / 1_000_000.0
            tr.tripped = True

        rows = []
        is_vft = (self.ctrl._module == "inverse_time_vft_test")

        for tracker in self.trackers:
            row = {
                "id": tracker.test_id,
                "actualTime": round(tracker.actual_time, 4) if tracker.actual_time is not None else None
            }
            if is_vft:
                row["vOrFValue"] = tracker.value
            else:
                row["currentI"] = tracker.value
            rows.append(row)

        self.ctrl.sendReport({"rows": rows})
