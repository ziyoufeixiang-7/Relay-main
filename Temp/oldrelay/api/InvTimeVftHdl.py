import math
import logging
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("InvTimeVftHdl")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# vOutputMode → 通道映射
# 每项: [(ch_index, angle_offset)] — angle_offset 相对于 payload.angle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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


class VftPointTracker:
    """单个测试点的运行时跟踪"""

    def __init__(self, test_id: int, fault_node_id: int, value: float):
        self.test_id = test_id
        self.fault_node_id = fault_node_id
        self.value = value  # testPoints[].value (V / Hz / V/Hz)
        self.entry_hw_ts: Optional[int] = None
        self.entry_time_mono: float = 0.0
        self.actual_time: Optional[float] = None
        self.tripped: bool = False


class InvTimeVftHdl:
    MODULE_KEYS = ["inverse_time_vft_test"]
    """
    反时限 VF-t 实验 Handler
    将前端 testProject + vOutputMode 参数编译为引擎节点链。
    每个测试点 = 2个引擎节点 (故障前 + 故障态)，一次性下发。
    """

    def __init__(self):

        self.isActive = False
        self.trackers: List[VftPointTracker] = []
        self.currentTracker: Optional[VftPointTracker] = None
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono: float = 0.0
        self.lastKnownDi = -1
        self.lastKnownDo = -1
        self.lastDiChangeHwTs: int = 0
        self.lastDiChangeMono: float = 0.0

        self.faultNodeIds: set = set()
        self.trackerByFaultNode: Dict[int, VftPointTracker] = {}

    def ResetState(self):
        self.isActive = False
        self.trackers = []
        self.currentTracker = None
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono = 0.0
        self.lastKnownDi = -1
        self.lastKnownDo = -1
        self.lastDiChangeHwTs = 0
        self.lastDiChangeMono = 0.0
        self.faultNodeIds = set()
        self.trackerByFaultNode = {}

    # ═══════════════════════════════════════════════════
    #  物理值 → 寄存器值
    # ═══════════════════════════════════════════════════

    def _PhysToRegDict(self, phys_dict: dict) -> dict:
        """Delegates to self.mgr.PhysToReg"""
        return self.mgr.PhysToReg(phys_dict)

    def _MakeChannelReg(self, ch_idx: int, amplitude: float, angle_deg: float,
                          freq_hz: float) -> dict:
        """Delegates to self.mgr.MakeChannelReg"""
        return self.mgr.MakeChannelReg(ch_idx, amplitude, angle_deg, freq_hz)

    # ═══════════════════════════════════════════════════
    #  电压通道计算
    # ═══════════════════════════════════════════════════

    def _ComputeVoltages(self, voltage: float, freq_hz: float, payload: dict) -> dict:
        """
        根据 vOutputMode 计算电压通道寄存器值。
        voltage: 输出电压幅值 (V)
        freq_hz: 输出频率 (Hz)
        """
        v_output_mode = payload.get("vOutputMode", "UA")
        angle = payload.get("angle", 0)
        channels = V_OUTPUT_MAP.get(v_output_mode, [(0, 0)])

        result = {}
        for ch_idx, angle_offset in channels:
            ch_angle = angle + angle_offset
            result[str(ch_idx)] = self._MakeChannelReg(ch_idx, voltage, ch_angle, freq_hz)
        return result

    def _ResolveFaultVf(self, value: float, payload: dict):
        """
        根据 testProject 和 value 计算故障态的电压和频率。
        返回 (fault_voltage, fault_frequency)
        """
        test_project = payload.get("testProject", "V_T")
        v_before = payload.get("vBeforeFault", 57.735)
        f_before = payload.get("fBeforeFault", 50)
        vb = payload.get("vbValue", 57.735)
        fb = payload.get("fbValue", 50)
        vf_variable = payload.get("vfVariable", "VOLTAGE_AMPLITUDE_V")

        if test_project == "V_T":
            # value = 电压 (V)
            return value, f_before
        elif test_project == "F_T":
            # value = 频率 (Hz)
            return v_before, value
        elif test_project == "VF_T":
            # value = V/Hz 比值
            if vf_variable == "VOLTAGE_AMPLITUDE_V":
                # 变电压: V_fault = value × fbValue
                return value * fb, f_before
            else:  # VOLTAGE_FREQUENCY_F
                # 变频率: F_fault = vbValue / value
                return v_before, vb / value if value != 0 else f_before
        return v_before, f_before

    # ═══════════════════════════════════════════════════
    #  编译入口
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将前端 VF-t 参数编译为引擎节点链"""
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 15)
        logic_mask = sys_cfg.get("logicMask", 0)
        statics = params.get("statics") or {}
        payload = params.get("payload") or {}
        test_points = params.get("testPoints") or []

        v_before = payload.get("vBeforeFault", 57.735)
        f_before = payload.get("fBeforeFault", 50)
        t_before = payload.get("tBeforeFault", 1000)
        fault_limit = payload.get("faultLimitTime", 60000)

        freq_reg_default = HWConfig.ConvertFreqToReg(f_before)
        engine_nodes = []

        # 编译辅助通道 (statics, 通常是电流通道 6~11)
        statics_reg = self._PhysToRegDict(statics)

        # 故障前电压通道
        before_v_reg = self._ComputeVoltages(v_before, f_before, payload)

        for idx, tp in enumerate(test_points):
            tp_id = tp.get("id", idx + 1)
            tp_value = tp.get("value", 0.0)

            before_node_id = 2 * idx
            fault_node_id = 2 * idx + 1
            next_before = fault_node_id
            next_fault = (2 * idx + 2) if idx < len(test_points) - 1 else -2

            # ─── 故障前节点 ───
            before_static = dict(statics_reg)
            before_static.update(before_v_reg)

            # 补全未配置通道
            ApiHdlMgr.FillMissingChannels(before_static, freq_reg_default)

            engine_nodes.append({
                "id": before_node_id,
                "doActions": [],
                "static": before_static,
                "triggers": [{
                    "condition": "TIME_OUT",
                    "timeout": int(t_before),
                    "nextId": next_before
                }]
            })

            # ─── 故障态节点 ───
            fault_v, fault_f = self._ResolveFaultVf(tp_value, payload)
            fault_v_reg = self._ComputeVoltages(fault_v, fault_f, payload)

            fault_static = dict(statics_reg)
            fault_static.update(fault_v_reg)

            # 补全未配置通道
            ApiHdlMgr.FillMissingChannels(fault_static, freq_reg_default)

            # bit9 (0x200) = 以进入节点时的 DI 状态为参考基准
            di_mask = logic_mask | 0x200
            triggers = [
                {"condition": "DI_MATCH", "diMask": di_mask, "nextId": next_fault},
            ]
            if fault_limit > 0:
                triggers.append({
                    "condition": "TIME_OUT",
                    "timeout": int(fault_limit),
                    "nextId": next_fault
                })

            engine_nodes.append({
                "id": fault_node_id,
                "doActions": [],
                "static": fault_static,
                "triggers": triggers
            })
            self.faultNodeIds.add(fault_node_id)

            # 创建跟踪器
            tracker = VftPointTracker(tp_id, fault_node_id, tp_value)
            self.trackers.append(tracker)
            self.trackerByFaultNode[fault_node_id] = tracker

        logger.info(f"Compiled {len(test_points)} VF-t test points → {len(engine_nodes)} engine nodes")
        return {
            "sys": {
                "vChannels": ApiHdlMgr.API_V_CHS,
                "iChannels": ApiHdlMgr.API_I_CHS,
                "debounce": debounce
            },
            "nodes": engine_nodes
        }

    # ═══════════════════════════════════════════════════
    #  遥测拦截
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        if not self.isActive:
            return []

        evt_type = data.get("type", "")
        msgs = []

        if evt_type == "di_change":
            self.lastKnownDi = data.get("di", self.lastKnownDi)
            self.lastDiChangeHwTs = data.get("hw_ts", 0)
            self.lastDiChangeMono = time.monotonic()
            return msgs

        if evt_type == "value_update":
            hw_ts = data.get("hw_ts", 0)
            tick = data.get("tick", None)

            if "di" in data:
                self.lastKnownDi = data["di"]
            if "do" in data:
                self.lastKnownDo = data["do"]

            if tick is not None:
                node_id = data.get("id", -1)
                self.lastValueUpdateHwTs = hw_ts
                self.lastValueUpdateMono = time.monotonic()

                if tick == 0 and node_id in self.faultNodeIds:
                    tracker = self.trackerByFaultNode.get(node_id)
                    if tracker:
                        tracker.entry_hw_ts = hw_ts
                        tracker.entry_time_mono = time.monotonic()
                        self.lastDiChangeHwTs = 0  # 清零，防止残留上一个测试点的 DI 时间戳
                        self.currentTracker = tracker
                        logger.info(f"VF-t point {tracker.test_id} fault phase entered, value={tracker.value}")

        elif evt_type == "trigger":
            hw_ts = data.get("hw_ts", 0)
            condition = data.get("condition", "")

            if self.currentTracker and not self.currentTracker.tripped:
                tracker = self.currentTracker

                # 计算动作时间
                # DI_MATCH: 用 DI 变位的硬件时间戳 (最精确)
                # TIME_OUT/其他: 用 monotonic 时间
                if condition == "DI_MATCH" and self.lastDiChangeHwTs > 0 and tracker.entry_hw_ts is not None:
                    elapsed_us = (self.lastDiChangeHwTs - tracker.entry_hw_ts) & 0xFFFFFFFF
                    tracker.actual_time = elapsed_us / 1_000_000.0
                elif tracker.entry_time_mono > 0:
                    tracker.actual_time = time.monotonic() - tracker.entry_time_mono
                else:
                    tracker.actual_time = 0.0

                tracker.tripped = (condition == "DI_MATCH")

                logger.info(f"VF-t point {tracker.test_id}: {condition}, "
                            f"time={tracker.actual_time:.4f}s, tripped={tracker.tripped}")

                msgs.append({
                    "module": self.MODULE_KEY,
                    "type": "value_update",
                    "id": tracker.test_id,
                    "vOrFValue": tracker.value,
                    "actualTime": round(tracker.actual_time, 4),
                    "di": self.lastKnownDi,
                    "do": self.lastKnownDo
                })

                self.currentTracker = None

        elif evt_type == "terminal":
            self.isActive = False

            rows = []
            for tracker in self.trackers:
                rows.append({
                    "id": tracker.test_id,
                    "vOrFValue": tracker.value,
                    "actualTime": round(tracker.actual_time, 4) if tracker.actual_time is not None else None
                })

            msgs.append({
                "module": self.MODULE_KEY,
                "type": "report",
                "data": {"rows": rows}
            })
            msgs.append({
                "module": self.MODULE_KEY,
                "type": "stop"
            })
            logger.info(f"VF-t test complete. {len(rows)} points reported.")

        return msgs
