import math
import copy
import logging
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("SlipTestHdl")

SLIP_INTERVAL_MS = 10
SQRT3 = math.sqrt(3)


class SlipTestHdl:
    MODULE_KEYS = ["slip_test"]

    def __init__(self):
        self.isActive = False
        self.testType = 0
        self.doMask = 0
        self.doCtrlMask = 0
        self.logicMask = 0
        self.slipStart = 0.0
        self.slipDDt = 1.0
        self.delta_per_step = 0.0
        self.targetChannels: List[int] = []
        self.slipPhasor = 0
        self.slipVarType = "0_0"
        self.isLineVoltage = False
        self.statics = {}
        self.payload = {}

        # Search state
        self.searchValue = 0.0
        self.searchStep = 0.0
        self.searchEnd = 0.0
        self.searchStarted = False

        # Timing (testType=1)
        self.timerTick = -1
        self.timerStartHwTs = 0
        self.timerStartRecorded = False

        # Runtime
        self.actionDetected = False
        self.actionTimeMs: Optional[float] = None
        self.currentNodeId = -1
        self.lastKnownTick = 0
        self.lastKnownDi = -1
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastSentPayload = None

    def ResetState(self):
        self.isActive = False
        self.actionDetected = False
        self.actionTimeMs = None
        self.currentNodeId = -1
        self.lastKnownTick = 0
        self.lastKnownDi = -1
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastSentPayload = None
        self.timerStartRecorded = False
        self.searchStarted = False
        self._lastTelemetryTs = 0

    # ═══════════════════════════════════════════════════
    #  通道解析
    # ═══════════════════════════════════════════════════

    def _ParseChannels(self, payload: dict) -> List[int]:
        var_type = payload.get("slipVarType", "0_0")
        self.slipVarType = var_type
        self.isLineVoltage = (var_type == "2_0")

        if var_type == "5":
            return list(range(12))

        phase_str = payload.get("slipPhase", "0")
        return [int(x.strip()) for x in phase_str.split(",") if x.strip().isdigit()]

    # ═══════════════════════════════════════════════════
    #  物理值 → 寄存器值
    # ═══════════════════════════════════════════════════

    def _PhysToRegDict(self, phys_dict: dict) -> dict:
        """Delegates to self.mgr.PhysToReg"""
        return self.mgr.PhysToReg(phys_dict)

    def _BuildStepReg(self, delta: float, channels: List[int], phasor: int) -> dict:
        """Build sweep step dict in register values."""
        step = {}
        for ch in channels:
            ch_str = str(ch)
            is_cur = ch in ApiHdlMgr.API_I_CHS
            actual_delta = delta / SQRT3 if self.isLineVoltage else delta

            if phasor == 0:  # amplitude
                calib_delta = self.mgr.ApplyDelta(ch, actual_delta)
                if abs(calib_delta) < 1e-6:
                    calib_delta = 0.0
                amp_reg = HWConfig.ConvertAmpToReg(calib_delta, is_cur)
                step[ch_str] = {"1": [amp_reg, 0]}
            elif phasor == 1:  # phase
                phase_reg = HWConfig.ConvertPhaseToReg(actual_delta)
                step[ch_str] = {"1": [0, phase_reg]}
            elif phasor == 2:  # frequency
                freq_reg = HWConfig.ConvertFreqToReg(actual_delta)
                step[ch_str] = {"0": [0, freq_reg]}
        return step

    def _BuildResetStaticPhys(self, statics: dict, channels: List[int],
                                  phasor: int, value: float) -> dict:
        """Build reset static (physical) with slipStart overriding target channels."""
        result = copy.deepcopy(statics)
        for ch in channels:
            ch_str = str(ch)
            if ch_str not in result:
                if phasor == 2:
                    result[ch_str] = {"0": [0.0, 50.0]}
                else:
                    result[ch_str] = {"0": [0.0, 50.0], "1": [0.0, 0.0]}

            actual_val = value / SQRT3 if self.isLineVoltage else value

            if phasor == 0:
                if "1" not in result[ch_str]:
                    result[ch_str]["1"] = [0.0, 0.0]
                result[ch_str]["1"][0] = actual_val
            elif phasor == 1:
                if "1" not in result[ch_str]:
                    result[ch_str]["1"] = [0.0, 0.0]
                result[ch_str]["1"][1] = actual_val
            elif phasor == 2:
                result[ch_str]["0"][1] = actual_val
        return result

    def _BuildEndpointStaticPhys(self, statics: dict, channels: List[int],
                                     phasor: int, endpoint: float,
                                     lock_channels: List[int] = None,
                                     lock_value: float = 0.0) -> dict:
        """Build endpoint static (physical) for Node 2 wait phase."""
        result = self._BuildResetStaticPhys(statics, channels, phasor, endpoint)
        if lock_channels:
            for ch in lock_channels:
                ch_str = str(ch)
                if ch_str not in result:
                    result[ch_str] = {"0": [0.0, 50.0], "1": [0.0, 0.0]}
                if "1" not in result[ch_str]:
                    result[ch_str]["1"] = [0.0, 0.0]
                result[ch_str]["1"][0] = lock_value
        return result

    # ═══════════════════════════════════════════════════
    #  节点编译
    # ═══════════════════════════════════════════════════

    def _Compile3Nodes(self, endpoint: float, ddt: float,
                         is_search: bool, lock_channels: List[int] = None,
                         lock_value: float = 0.0) -> list:
        """Compile 3-node cycle: reset → sweep → wait."""
        delta = ddt * (SLIP_INTERVAL_MS / 1000.0)
        if abs(delta) < 1e-12:
            delta = 1e-6
        count = max(1, int(abs(endpoint - self.slipStart) / abs(delta)))

        # Node 0: Reset
        reset_phys = self._BuildResetStaticPhys(
            self.statics, self.targetChannels, self.slipPhasor, self.slipStart)
        if lock_channels:
            for ch in lock_channels:
                ch_str = str(ch)
                if ch_str not in reset_phys:
                    reset_phys[ch_str] = {"0": [0.0, 50.0], "1": [0.0, 0.0]}
                if "1" not in reset_phys[ch_str]:
                    reset_phys[ch_str]["1"] = [0.0, 0.0]
                reset_phys[ch_str]["1"][0] = lock_value
        reset_reg = self._PhysToRegDict(reset_phys)
        freq_reg = HWConfig.ConvertFreqToReg(50.0)
        ApiHdlMgr.FillMissingChannels(reset_reg, freq_reg)

        return_time = self.payload.get("returnTime", 1000)
        node0 = {
            "id": 0,
            "doActions": [{"delay": 0, "mask": self.doMask}] if self.doMask else [],
            "static": reset_reg,
            "triggers": [{"condition": "TIME_OUT", "timeout": int(return_time), "nextId": 1}]
        }

        # Node 1: Sweep
        sweep_static_reg = copy.deepcopy(reset_reg)
        step_reg = self._BuildStepReg(delta, self.targetChannels, self.slipPhasor)

        # Anti-tear
        for ch_str, layers in step_reg.items():
            if ch_str not in sweep_static_reg:
                sweep_static_reg[ch_str] = {}
            for l_str, sv in layers.items():
                if sv[0] != 0 or sv[1] != 0:
                    if l_str not in sweep_static_reg[ch_str]:
                        dp = freq_reg if l_str == "0" else 0
                        sweep_static_reg[ch_str][l_str] = [0, dp]
                    if sweep_static_reg[ch_str][l_str][0] == 0 and sv[0] != 0:
                        sweep_static_reg[ch_str][l_str][0] = 1 if sv[0] > 0 else -1

        do_act = self.doCtrlMask if self.doCtrlMask else self.doMask
        eff_logic_mask = self.logicMask if self.logicMask > 0 else 0xFF
        di_mask = eff_logic_mask | 0x200
        node1 = {
            "id": 1,
            "doActions": [{"delay": 0, "mask": do_act}] if do_act else [],
            "static": sweep_static_reg,
            "dynamic": {"interval": SLIP_INTERVAL_MS, "count": count, "step": step_reg},
            "triggers": [
                {"condition": "DI_MATCH", "diMask": di_mask, "nextId": -2},
                {"condition": "COUNT_OVER", "nextId": 2}
            ]
        }

        # Node 2: Wait at endpoint
        ep_phys = self._BuildEndpointStaticPhys(
            self.statics, self.targetChannels, self.slipPhasor, endpoint,
            lock_channels, lock_value)
        ep_reg = self._PhysToRegDict(ep_phys)
        ApiHdlMgr.FillMissingChannels(ep_reg, freq_reg)

        wait_time = self.payload.get("waitTime", 1000)
        # 搜索模式: TIME_OUT → 回到 Node 0 (循环)
        # 非搜索模式: TIME_OUT → -2 (终止)
        wait_next = 0 if is_search else -2

        node2_triggers = [
            {"condition": "DI_MATCH", "diMask": di_mask, "nextId": -2},
            {"condition": "TIME_OUT", "timeout": int(wait_time), "nextId": wait_next}
        ]

        node2 = {
            "id": 2,
            "doActions": [],
            "static": ep_reg,
            "triggers": node2_triggers
        }

        return [node0, node1, node2]

    # ═══════════════════════════════════════════════════
    #  编译入口
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        self.statics = params.get("statics") or {}
        self.payload = params.get("payload") or {}
        limits = params.get("limits") or {}

        self.doMask = sys_cfg.get("doMask", 0)
        self.doCtrlMask = sys_cfg.get("doCtrlMask", 0)
        self.logicMask = sys_cfg.get("logicMask", 0)
        debounce = sys_cfg.get("debounce", 15)

        self.testType = self.payload.get("testType", 0)
        self.slipPhasor = self.payload.get("slipPhasor", 0)
        
        # 增加后端保护：如果测的是系统频率 (5)，强行将 phasor 设置为 2 (频率)，无视前端错误传参
        if self.payload.get("slipVarType") == "5":
            self.slipPhasor = 2
            
        self.slipStart = self.payload.get("slipStart", 50.0)
        self.slipDDt = self.payload.get("slipDDt", 1.0)
        self.targetChannels = self._ParseChannels(self.payload)
        self.delta_per_step = self.slipDDt * (SLIP_INTERVAL_MS / 1000.0)

        # Limits
        properLimits = {}
        for ch_str, val in limits.items():
            ch = int(ch_str)
            if isinstance(val, list) and len(val) >= 2:
                properLimits[ch_str] = [
                    HWConfig.ConvertAmpToReg(float(val[0]), ch in ApiHdlMgr.API_I_CHS),
                    HWConfig.ConvertFreqToReg(float(val[1]))
                ]

        # Compile based on testType
        if self.testType == 0:
            self.searchValue = self.payload.get("actionSearchStart", self.slipStart)
            self.searchStep = self.payload.get("actionSearchStep", 1.0)
            self.searchEnd = self.payload.get("actionSearchEnd", self.slipStart)
            nodes = self._Compile3Nodes(self.searchValue, self.slipDDt, is_search=True)

        elif self.testType == 1:
            slip_end = self.payload.get("slipEnd", 45.0)
            timer_val = self.payload.get("timerStartValue", self.slipStart)
            if abs(self.delta_per_step) > 1e-12:
                self.timerTick = int(abs(timer_val - self.slipStart) / abs(self.delta_per_step))
            else:
                self.timerTick = 0
            nodes = self._Compile3Nodes(slip_end, self.slipDDt, is_search=False)

        elif self.testType == 2:
            self.searchValue = self.payload.get("ddtSearchStart", self.slipDDt)
            self.searchStep = self.payload.get("ddtSearchStep", 0.1)
            self.searchEnd = self.payload.get("ddtSearchEnd", 0.0)
            slip_end = self.payload.get("slipEnd", 45.0)
            nodes = self._Compile3Nodes(slip_end, self.searchValue, is_search=True)

        elif self.testType == 3:
            lock_phase_str = self.payload.get("lockPhase", "")
            lock_chs = [int(x.strip()) for x in lock_phase_str.split(",") if x.strip().isdigit()]
            self.lockChannels = lock_chs
            self.searchValue = self.payload.get("lockSearchStart", 0.0)
            self.searchStep = self.payload.get("lockSearchStep", 0.1)
            self.searchEnd = self.payload.get("lockSearchEnd", 10.0)
            slip_end = self.payload.get("slipEnd", 45.0)
            nodes = self._Compile3Nodes(slip_end, self.slipDDt, is_search=True,
                                          lock_channels=lock_chs, lock_value=self.searchValue)

        elif self.testType == 4:
            lock_phase_str = self.payload.get("lockPhase", "")
            lock_chs = [int(x.strip()) for x in lock_phase_str.split(",") if x.strip().isdigit()]
            self.lockChannels = lock_chs
            self.searchValue = self.payload.get("lockSearchStart", 0.0)
            self.searchStep = self.payload.get("lockSearchStep", 0.1)
            self.searchEnd = self.payload.get("lockSearchEnd", 100.0)
            slip_end = self.payload.get("slipEnd", 45.0)
            nodes = self._Compile3Nodes(slip_end, self.slipDDt, is_search=True,
                                          lock_channels=lock_chs, lock_value=self.searchValue)
        else:
            nodes = []

        self.searchStarted = True
        logger.info(f"Slip test compiled: testType={self.testType}, channels={self.targetChannels}, "
                    f"start={self.slipStart}, ddt={self.slipDDt}")

        return {
            "sys": {
                "vChannels": ApiHdlMgr.API_V_CHS,
                "iChannels": ApiHdlMgr.API_I_CHS,
                "debounce": debounce,
                "limit": properLimits
            },
            "nodes": nodes
        }

    # ═══════════════════════════════════════════════════
    #  物理值反算
    # ═══════════════════════════════════════════════════

    def _ComputeCurrentPhysics(self, tick: int) -> dict:
        """Reconstruct current physical output from tick."""
        result = copy.deepcopy(self.statics)
        actual_delta = self.delta_per_step / SQRT3 if self.isLineVoltage else self.delta_per_step
        current_val = self.slipStart + tick * actual_delta

        for ch in self.targetChannels:
            ch_str = str(ch)
            if ch_str not in result:
                result[ch_str] = {"0": [0.0, 50.0], "1": [0.0, 0.0]}
            if self.slipPhasor == 0:
                if "1" not in result[ch_str]:
                    result[ch_str]["1"] = [0.0, 0.0]
                base = self.statics.get(ch_str, {}).get("1", [0.0, 0.0])
                result[ch_str]["1"][0] = self.slipStart + tick * actual_delta
            elif self.slipPhasor == 1:
                if "1" not in result[ch_str]:
                    result[ch_str]["1"] = [0.0, 0.0]
                result[ch_str]["1"][1] = self.slipStart + tick * actual_delta
            elif self.slipPhasor == 2:
                result[ch_str]["0"][1] = self.slipStart + tick * actual_delta
        return result

    # ═══════════════════════════════════════════════════
    #  遥测拦截
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        if not self.isActive:
            return [data]

        t_type = data.get("type", "")
        msgs = []

        if t_type not in ("value_update", "trigger", "terminal", "di_change"):
            return [data]

        if t_type == "di_change":
            new_di = data.get("di", self.lastKnownDi)
            if new_di != self.lastKnownDi:
                self.lastKnownDi = new_di
                msgs.append({"module": self.MODULE_KEY, "type": "value_update", "di": new_di})
            return msgs

        if t_type == "value_update":
            if "tick" not in data and "di" in data:
                new_di = data["di"]
                if new_di != self.lastKnownDi:
                    self.lastKnownDi = new_di
                    msgs.append({"module": self.MODULE_KEY, "type": "value_update", "di": new_di})
                return msgs

            tick = data.get("tick", 0)
            nid = data.get("id", 0)
            hw_ts = data.get("hw_ts", 0)
            new_di = data.get("di", self.lastKnownDi)
            new_do = data.get("do", self.lastKnownDo)

            if tick >= 0:
                self.lastKnownTick = tick
                self.lastValueUpdateHwTs = hw_ts
            # Track current node from tick=0 entries
            if tick == 0:
                self.currentNodeId = nid

            # DI/DO change
            di_do = {}
            if new_di != self.lastKnownDi:
                self.lastKnownDi = new_di
                di_do["di"] = new_di
            if new_do != self.lastKnownDo:
                self.lastKnownDo = new_do
                di_do["do"] = new_do
            if di_do:
                di_do["module"] = self.MODULE_KEY
                di_do["type"] = "value_update"
                msgs.append(di_do)

            # Timer recording (testType=1)
            if self.testType == 1 and nid == 1 and tick >= 0:
                if tick == self.timerTick and not self.timerStartRecorded:
                    self.timerStartHwTs = hw_ts
                    self.timerStartRecorded = True
                    logger.info(f"Timer start recorded at tick={tick}, hw_ts={hw_ts}")

            # Physical value update
            if tick >= 0 and nid == 1:
                vals = self._ComputeCurrentPhysics(tick)
                msg = {"module": self.MODULE_KEY, "type": "value_update", "static": vals}
                if msg != self.lastSentPayload:
                    import time
                    if tick == 0 or time.monotonic() - getattr(self, "_lastTelemetryTs", 0) >= 0.05:
                        self._lastTelemetryTs = time.monotonic()
                        self.lastSentPayload = msg
                        msgs.append(msg)

        elif t_type == "trigger":
            cond = data.get("condition", "")
            hw_ts = data.get("hw_ts", 0)

            if cond == "DI_MATCH" and not self.actionDetected:
                self.actionDetected = True
                if self.testType == 1 and self.timerStartRecorded:
                    elapsed_us = (hw_ts - self.timerStartHwTs) & 0xFFFFFFFF
                    self.actionTimeMs = elapsed_us / 1000.0
                    logger.info(f"Action time: {self.actionTimeMs:.1f} ms")

            elif cond == "TIME_OUT":
                if self.currentNodeId == 2 and self.testType in (0, 2, 3, 4):
                    # Search iteration timeout - no action this round
                    if not self.actionDetected:
                        self.searchValue += self.searchStep
                        exceeded = False
                        if self.searchStep > 0 and self.searchValue > self.searchEnd:
                            exceeded = True
                        elif self.searchStep < 0 and self.searchValue < self.searchEnd:
                            exceeded = True

                        if exceeded:
                            logger.info("Search exhausted, stopping")
                            self.isActive = False
                            msgs.append(self._BuildReport(null_result=True))
                            msgs.append({"module": self.MODULE_KEY, "type": "stop"})
                        else:
                            logger.info(f"Search next: value={self.searchValue}")
                            # 重置本轮状态
                            self.actionDetected = False
                            self.currentNodeId = -1
                            # 引擎已经跳到 Node 0, 更新节点内容
                            new_nodes = self._RecompileSearchNodes()
                            msgs.append({
                                "internal_hot_update": True,
                                "nodes": new_nodes
                            })

        elif t_type == "terminal":
            self.isActive = False
            msgs.append(self._BuildReport(null_result=not self.actionDetected))
            msgs.append({"module": self.MODULE_KEY, "type": "stop"})
            logger.info(f"Slip test terminal. action={self.actionDetected}")

        return msgs

    def _RecompileSearchNodes(self) -> list:
        """Recompile nodes for next search iteration."""
        if self.testType == 0:
            return self._Compile3Nodes(self.searchValue, self.slipDDt, is_search=True)
        elif self.testType == 2:
            slip_end = self.payload.get("slipEnd", 45.0)
            return self._Compile3Nodes(slip_end, self.searchValue, is_search=True)
        elif self.testType in (3, 4):
            slip_end = self.payload.get("slipEnd", 45.0)
            return self._Compile3Nodes(slip_end, self.slipDDt, is_search=True,
                                         lock_channels=getattr(self, "lockChannels", []),
                                         lock_value=self.searchValue)
        return []

    def _BuildReport(self, null_result: bool = False) -> dict:
        report = {"testType": self.testType}

        if self.testType == 0:
            report["actionValue"] = None if null_result else self.searchValue
        elif self.testType == 1:
            report["timerStartValue"] = self.payload.get("timerStartValue")
            report["actionTime"] = None if null_result else round(self.actionTimeMs, 1) if self.actionTimeMs else None
        elif self.testType == 2:
            report["slipLockValue"] = None if null_result else self.searchValue
        elif self.testType == 3:
            report["currentLockValue"] = None if null_result else self.searchValue
        elif self.testType == 4:
            report["voltageLockValue"] = None if null_result else self.searchValue

        return {"module": self.MODULE_KEY, "type": "report", "data": report}
