import math
import logging
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("InvTimeItHdl")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 故障类型常量
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 相间故障: 电流等值反向 (相位差180°)
# 接地故障: 单相有电流
FAULT_PHASE_MAP = {
    #                   (Ia_active, Ia_angle), (Ib_active, Ib_angle), (Ic_active, Ic_angle)
    "A_GROUND":   ((True,   0), (False,    0), (False,    0)),
    "B_GROUND":   ((False,  0), (True,  -120), (False,    0)),
    "C_GROUND":   ((False,  0), (False,    0), (True,   120)),
    "AB_PHASE":   ((True,   0), (True,   180), (False,    0)),
    "BC_PHASE":   ((False,  0), (True,  -120), (True,    60)),
    "CA_PHASE":   ((True, -60), (False,    0), (True,   120)),
    "ABC_PHASE":  ((True,   0), (True,  -120), (True,   120)),
}

# 单路电流: 并联输出, 所有活跃通道同相 0°
SINGLE_PATH_MAP = {
    "A_GROUND":   (True,  False, False),
    "B_GROUND":   (False, True,  False),
    "C_GROUND":   (False, False, True),
    "AB_PHASE":   (True,  True,  False),
    "BC_PHASE":   (False, True,  True),
    "CA_PHASE":   (True,  False, True),
    "ABC_PHASE":  (True,  True,  True),
}

# 方向性模式: 纯故障电流附加角度表
# (has_delta, base_angle_offset)  实际角度 = base_angle_offset - lineAngle
# 参考: docs/directional_fault_waveform_model.md
DELTA_ANGLES = {
    "A_GROUND":  [(True, 0),    (False, 0),    (False, 0)],
    "B_GROUND":  [(False, 0),   (True, -120),  (False, 0)],
    "C_GROUND":  [(False, 0),   (False, 0),    (True, 120)],
    "AB_PHASE":  [(True, 30),   (True, 210),   (False, 0)],
    "BC_PHASE":  [(False, 0),   (True, -90),   (True, 90)],
    "CA_PHASE":  [(True, 330),  (False, 0),    (True, 150)],
    "ABC_PHASE": [(True, 0),    (True, -120),  (True, 120)],
}

# 相间故障线电压相位 / 故障对索引
LINE_V_ANGLE = {"AB_PHASE": 30, "BC_PHASE": -90, "CA_PHASE": 150}
FAULT_PAIR   = {"AB_PHASE": (0, 1, 2), "BC_PHASE": (1, 2, 0), "CA_PHASE": (2, 0, 1)}

# 通道组映射
V_CH_MAP = {"GROUP1_UABC": (0, 1, 2), "GROUP2_UXYZ": (3, 4, 5)}
I_CH_MAP = {"GROUP1_IABC": (6, 7, 8), "GROUP2_IXYZ": (9, 10, 11)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 矢量运算辅助
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
    """单个测试点的运行时跟踪"""

    def __init__(self, test_id: int, fault_node_id: int, current_value: float):
        self.test_id = test_id
        self.fault_node_id = fault_node_id
        self.current_value = current_value
        self.entry_hw_ts: Optional[int] = None
        self.entry_time_mono: float = 0.0
        self.actual_time: Optional[float] = None
        self.tripped: bool = False


class InvTimeItHdl:
    MODULE_KEYS = ["inverse_time_it_test"]
    """
    反时限 I-t 实验 Handler
    将前端 iDefinition × faultType 参数编译为引擎节点链。
    每个测试点 = 2个引擎节点 (故障前 + 故障态)，一次性下发。
    """

    def __init__(self):

        self.isActive = False
        self.trackers: List[PointTracker] = []
        self.currentTracker: Optional[PointTracker] = None
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono: float = 0.0
        self.lastKnownDi = -1
        self.lastKnownDo = -1
        self.lastDiChangeHwTs: int = 0
        self.lastDiChangeMono: float = 0.0

        # 编译时保存
        self.faultNodeIds: set = set()
        self.beforeNodeIds: set = set()
        self.trackerByFaultNode: Dict[int, PointTracker] = {}

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
        self.beforeNodeIds = set()
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
    #  故障电流计算
    # ═══════════════════════════════════════════════════

    def _GetDirScale(self, i_def: str, payload: dict,
                       fault_type: str, is_ground: bool) -> float:
        """方向性模式下的 I_set → I_delta 缩放系数"""
        if i_def == "FAULT_CURRENT":
            return 1.0
        elif i_def == "NEGATIVE_SEQUENCE":
            neg_ch = payload.get("negativeSequenceChannel", "I2")
            if neg_ch == "I2":
                return 3.0 if is_ground else math.sqrt(3)
            else:  # 3I2
                return 1.0 if is_ground else 1.0 / math.sqrt(3)
        elif i_def == "ZERO_SEQUENCE":
            return 3.0 if is_ground else 0.0  # 相间故障不存在零序
        return 1.0

    def _GetAbcSpecialDeltas(self, i_def: str, payload: dict,
                                value: float, line_angle: float) -> list:
        """
        ABC_PHASE 特殊模式: 纯负序 (ACB) / 纯零序 (同相)
        返回 [(mag, angle), (mag, angle), (mag, angle)] for A, B, C
        """
        if i_def == "NEGATIVE_SEQUENCE":
            neg_ch = payload.get("negativeSequenceChannel", "I2")
            amp = value if neg_ch == "I2" else value / 3.0
            # 纯负序: ACB 相序 (B=+120°, C=-120°)
            return [
                (amp, -line_angle),
                (amp, 120 - line_angle),
                (amp, -120 - line_angle)
            ]
        elif i_def == "ZERO_SEQUENCE":
            # 纯零序: 三相同相
            return [
                (value, -line_angle),
                (value, -line_angle),
                (value, -line_angle)
            ]
        return [(0, 0), (0, 0), (0, 0)]

    def _ComputeCurrents(self, value: float, payload: dict,
                          i_channels: tuple, freq_hz: float) -> dict:
        """
        根据 iDefinition + faultType 计算三相电流通道的寄存器值。
        方向性模式: I_total = I_pre + I_delta (矢量叠加)
        非方向性模式: FAULT_PHASE_MAP + 缩放系数
        参考: docs/directional_fault_waveform_model.md
        """
        i_def = payload.get("iDefinition", "FAULT_CURRENT")
        fault_type = payload.get("faultType", "A_GROUND")
        dir_cfg = payload.get("directionality", {})
        dir_enabled = dir_cfg.get("enabled", False)
        ct_polarity = dir_cfg.get("ctPolarity", "TOWARD_COMPONENT")

        ch_a, ch_b, ch_c = i_channels
        result = {}

        # ─── SINGLE_PATH: 不参与方向性 ───
        if i_def == "SINGLE_PATH":
            active = SINGLE_PATH_MAP.get(fault_type, (True, False, False))
            for ch_idx, is_active in zip(i_channels, active):
                amp = value if is_active else 0.0
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, amp, 0.0, freq_hz)
            return result

        # ─── 方向性模式: 叠加模型 ───
        if dir_enabled:
            line_angle = dir_cfg.get("lineAngle", 70.0)
            i_pre = payload.get("iBeforeFault", 0.0)
            pre_load_angle = dir_cfg.get("preLoadAngle", 0.0)
            is_ground = fault_type in ("A_GROUND", "B_GROUND", "C_GROUND")

            # 故障前三相负荷电流 (对称)
            pre_phases = [
                (i_pre, -pre_load_angle),
                (i_pre, -120 - pre_load_angle),
                (i_pre, 120 - pre_load_angle)
            ]

            # ABC 特殊模式 (纯负序/纯零序)
            if fault_type == "ABC_PHASE" and i_def in ("NEGATIVE_SEQUENCE", "ZERO_SEQUENCE"):
                delta_list = self._GetAbcSpecialDeltas(
                    i_def, payload, value, line_angle)
            else:
                # 标准故障: 缩放 + DELTA_ANGLES 查表
                scale = self._GetDirScale(i_def, payload, fault_type, is_ground)
                i_delta = value * scale
                angle_table = DELTA_ANGLES.get(fault_type, DELTA_ANGLES["A_GROUND"])
                delta_list = []
                for has_d, base_ang in angle_table:
                    if has_d:
                        delta_list.append((i_delta, base_ang - line_angle))
                    else:
                        delta_list.append((0.0, 0.0))

            # 矢量叠加 + CT 极性
            for ch_idx, (pre_mag, pre_ang), (d_mag, d_ang) in \
                    zip(i_channels, pre_phases, delta_list):
                if d_mag > 1e-9:
                    total_mag, total_ang = _VectorAdd(pre_mag, pre_ang, d_mag, d_ang)
                else:
                    total_mag, total_ang = pre_mag, pre_ang
                if ct_polarity == "TOWARD_BUSBAR":
                    total_ang += 180.0
                result[str(ch_idx)] = self._MakeChannelReg(
                    ch_idx, total_mag, total_ang, freq_hz)
            return result

        # ─── 非方向性模式: 保持原有逻辑 ───
        phase_map = FAULT_PHASE_MAP.get(fault_type, FAULT_PHASE_MAP["A_GROUND"])
        is_ground = fault_type in ("A_GROUND", "B_GROUND", "C_GROUND")

        # ABC 特殊模式 (非方向性)
        if fault_type == "ABC_PHASE" and i_def in ("NEGATIVE_SEQUENCE", "ZERO_SEQUENCE"):
            if i_def == "NEGATIVE_SEQUENCE":
                neg_ch = payload.get("negativeSequenceChannel", "I2")
                amp = value if neg_ch == "I2" else value / 3.0
                angles = [0, 120, -120]  # ACB 负序
            else:  # ZERO_SEQUENCE
                amp = value
                angles = [0, 0, 0]  # 同相
            for ch_idx, angle in zip(i_channels, angles):
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, amp, angle, freq_hz)
            return result

        # 标准缩放
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

    # ═══════════════════════════════════════════════════
    #  方向性电压计算
    # ═══════════════════════════════════════════════════

    def _ComputeDirVoltages(self, payload: dict, is_fault: bool,
                              v_channels: tuple, freq_hz: float) -> dict:
        """
        方向性启用时, 计算电压通道寄存器值。
        故障前: 三相对称 vBeforeFault
        故障态:
          - 接地: 故障相降到 vFault, 保持原相位
          - 相间: 线电压钳位 (Uab=vFault∠30°), 解出 Ua/Ub
          - 三相: 三相均降到 vFault, 保持各自相位
        参考: docs/directional_fault_waveform_model.md
        """
        dir_cfg = payload.get("directionality", {})
        v_before = dir_cfg.get("vBeforeFault", 57.735)
        v_fault = dir_cfg.get("vFault", 20.0)
        fault_type = payload.get("faultType", "A_GROUND")

        ch_a, ch_b, ch_c = v_channels
        normal = {ch_a: 0, ch_b: -120, ch_c: 120}
        result = {}

        if not is_fault:
            # 故障前: 三相对称
            for ch_idx, angle in normal.items():
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v_before, angle, freq_hz)
            return result

        # ─── 接地故障: 故障相降到 vFault, 保持原相位 ───
        if fault_type in ("A_GROUND", "B_GROUND", "C_GROUND"):
            ground_map = {"A_GROUND": ch_a, "B_GROUND": ch_b, "C_GROUND": ch_c}
            fault_ch = ground_map[fault_type]
            for ch_idx, ang in normal.items():
                v = v_fault if ch_idx == fault_ch else v_before
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v, ang, freq_hz)

        # ─── 相间故障: 线电压钳位模型 ───
        elif fault_type in ("AB_PHASE", "BC_PHASE", "CA_PHASE"):
            uv_angle = LINE_V_ANGLE[fault_type]
            idx_x, idx_y, idx_z = FAULT_PAIR[fault_type]
            ch_x, ch_y, ch_z = v_channels[idx_x], v_channels[idx_y], v_channels[idx_z]

            # Uxy = vFault ∠ uv_angle
            uxy_r, uxy_i = _PolarToRect(v_fault, uv_angle)
            # -Uz = vBefore ∠ (normal_z + 180°)
            nz_r, nz_i = _PolarToRect(v_before, normal[ch_z] + 180)
            # Ux = (Uxy + (-Uz)) / 2
            ux_mag, ux_ang = _RectToPolar((uxy_r + nz_r) / 2, (uxy_i + nz_i) / 2)
            # Uy = ((-Uz) - Uxy) / 2
            uy_mag, uy_ang = _RectToPolar((nz_r - uxy_r) / 2, (nz_i - uxy_i) / 2)

            result[str(ch_x)] = self._MakeChannelReg(ch_x, ux_mag, ux_ang, freq_hz)
            result[str(ch_y)] = self._MakeChannelReg(ch_y, uy_mag, uy_ang, freq_hz)
            result[str(ch_z)] = self._MakeChannelReg(ch_z, v_before, normal[ch_z], freq_hz)

        # ─── 三相故障: 三相均降压, 保持各自相位 ───
        elif fault_type == "ABC_PHASE":
            for ch_idx, ang in normal.items():
                result[str(ch_idx)] = self._MakeChannelReg(ch_idx, v_fault, ang, freq_hz)

        return result

    # ═══════════════════════════════════════════════════
    #  编译入口
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将前端反时限 I-t 参数编译为引擎节点链"""
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 15)
        logic_mask = sys_cfg.get("logicMask", 0)
        statics = params.get("statics") or {}
        payload = params.get("payload") or {}
        test_points = params.get("testPoints") or []

        freq_hz = payload.get("ratedFrequency", 50.0)
        i_before = payload.get("iBeforeFault", 0.0)
        t_before = payload.get("tBeforeFault", 1000)
        fault_limit = payload.get("faultLimitTime", 60000)
        dir_enabled = payload.get("directionality", {}).get("enabled", False)

        output_mode = payload.get("outputMode", {})
        v_chs = V_CH_MAP.get(output_mode.get("voltage", "GROUP1_UABC"), (0, 1, 2))
        i_chs = I_CH_MAP.get(output_mode.get("current", "GROUP1_IABC"), (6, 7, 8))

        freq_50_reg = HWConfig.ConvertFreqToReg(freq_hz)
        engine_nodes = []

        # 编译辅助通道 (statics) 为寄存器值
        statics_reg = self._PhysToRegDict(statics)

        # 计算故障前电流
        before_i_reg = self._ComputeCurrents(i_before, payload, i_chs, freq_hz)

        # 计算故障前电压 (方向性启用时)
        before_v_reg = {}
        if dir_enabled:
            before_v_reg = self._ComputeDirVoltages(payload, False, v_chs, freq_hz)

        for idx, tp in enumerate(test_points):
            tp_id = tp.get("id", idx + 1)
            tp_value = tp.get("value", 0.0)

            before_node_id = 2 * idx
            fault_node_id = 2 * idx + 1
            next_before = fault_node_id
            next_fault = (2 * idx + 2) if idx < len(test_points) - 1 else -2

            # ─── 故障前节点 ───
            before_static = dict(statics_reg)
            before_static.update(before_i_reg)
            if dir_enabled:
                before_static.update(before_v_reg)

            # 补全未配置通道
            ApiHdlMgr.FillMissingChannels(before_static, freq_50_reg)

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
            self.beforeNodeIds.add(before_node_id)

            # ─── 故障态节点 ───
            fault_i_reg = self._ComputeCurrents(tp_value, payload, i_chs, freq_hz)
            fault_static = dict(statics_reg)
            fault_static.update(fault_i_reg)
            if dir_enabled:
                fault_v_reg = self._ComputeDirVoltages(payload, True, v_chs, freq_hz)
                fault_static.update(fault_v_reg)

            # 补全未配置通道
            ApiHdlMgr.FillMissingChannels(fault_static, freq_50_reg)

            # bit9 (0x200) = 以进入节点时的 DI 状态为参考基准
            # 这样故障态的 DI 翻转是和故障前正常态对比
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
            tracker = PointTracker(tp_id, fault_node_id, tp_value)
            self.trackers.append(tracker)
            self.trackerByFaultNode[fault_node_id] = tracker

        logger.info(f"Compiled {len(test_points)} test points → {len(engine_nodes)} engine nodes")
        return {
            "sys": {
                "vChannels": list(v_chs) + [c for c in ApiHdlMgr.API_V_CHS if c not in v_chs],
                "iChannels": list(i_chs) + [c for c in ApiHdlMgr.API_I_CHS if c not in i_chs],
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

            # 更新 DI/DO
            if "di" in data:
                self.lastKnownDi = data["di"]
            if "do" in data:
                self.lastKnownDo = data["do"]

            if tick is not None:
                # 引擎遥测
                node_id = data.get("id", -1)
                self.lastValueUpdateHwTs = hw_ts
                self.lastValueUpdateMono = time.monotonic()

                if tick == 0 and node_id in self.faultNodeIds:
                    # 进入故障态节点
                    tracker = self.trackerByFaultNode.get(node_id)
                    if tracker:
                        tracker.entry_hw_ts = hw_ts
                        tracker.entry_time_mono = time.monotonic()
                        self.lastDiChangeHwTs = 0  # 清零，防止残留上一个测试点的 DI 时间戳
                        self.currentTracker = tracker
                        logger.info(f"Test point {tracker.test_id} fault phase entered, I={tracker.current_value}")

        elif evt_type == "trigger":
            hw_ts = data.get("hw_ts", 0)
            condition = data.get("condition", "")
            next_id = data.get("next_id", -2)

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

                logger.info(f"Test point {tracker.test_id}: {condition}, "
                            f"time={tracker.actual_time:.4f}s, tripped={tracker.tripped}")

                # 发送 value_update 给前端
                msgs.append({
                    "module": self.MODULE_KEY,
                    "type": "value_update",
                    "id": tracker.test_id,
                    "currentI": tracker.current_value,
                    "actualTime": round(tracker.actual_time, 4),
                    "di": self.lastKnownDi,
                    "do": self.lastKnownDo
                })

                self.currentTracker = None

        elif evt_type == "terminal":
            # 实验自然结束
            self.isActive = False

            # 生成报告
            rows = []
            for tracker in self.trackers:
                rows.append({
                    "id": tracker.test_id,
                    "currentI": tracker.current_value,
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
            logger.info(f"I-t test complete. {len(rows)} points reported.")

        return msgs
