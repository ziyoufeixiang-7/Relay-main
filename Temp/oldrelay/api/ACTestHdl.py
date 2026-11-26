"""
Unified Sweep Test Handler (ACTestHdl)
Covers: ac_test, dc_test, harmonic_test, steps_gradient_test, acdc_test

Architecture (changeMode=1):
  Node P(optional pre-reset) → Node A(forward sweep) → Node H(empty hold)
  → HDL builds Node R(reverse sweep, hot update) → 0xFFFF terminal

Architecture (changeMode=0):
  Node P(optional) → Node A(forward sweep) → 0xFFFF terminal

Event types received from CmdRouter:
  di_change   → {type:"di_change", di:int, hw_ts:int}
  value_update → {type:"value_update", id:int, tick:int, do:int, hw_ts:int}
  trigger     → {type:"trigger", condition:str, next_id:int, hw_ts:int}
  terminal    → {type:"terminal"}
"""
import logging
from typing import Dict, Any, Optional

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("ACTestHdl")

# Terminal nextId: -1 → 0xFFFF after & 0xFFFF in CompileTrigger
_TERM = -1


class ACTestHdl:
    MODULE_KEYS = ["ac_test", "dc_test", "harmonic_test", "steps_gradient_test", "acdc_test"]

    def __init__(self):
        self.isActive = False
        self.mgr = None  # Set by ApiHdlMgr on discovery

    # ═══════════════════════════════════════════════════
    #  State
    # ═══════════════════════════════════════════════════

    def ResetState(self):
        self.isActive = False
        self._phase = "IDLE"
        self._mode = 1
        self._changeMode = 0
        self._returnMode = 0
        self._stepTime = 1000
        self._logicMask = 255
        self._statics = {}
        self._steps = {}
        self._count = 1

        # Reset
        self._enableStepReset = False
        self._stepResetMode = 0
        self._stepResetTime = 100
        self._resetTableData = {}
        self._regReset = {}

        # DO
        self._doMask = 0
        self._doCtrlMask = 0
        self._overMask = 0

        # Node IDs
        self._actionId = 0
        self._holdId = 1
        self._returnId = 2
        self._regStepsRev = {}

        # Telemetry
        self._tripTime = None
        self._tripVals = None
        self._returnTime = None
        self._returnVals = None
        self._lastTick = 0
        self._peakTick = 0
        self._lastValueTs = 0
        self._lastDiTs = 0
        self._lastDi = -1
        self._lastDo = -1
        self._lastSent = None

    # ═══════════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════════

    def _Reg(self, phys, factor=1.0, is_delta=False):
        return self.mgr.PhysToReg(phys, factor, is_delta)

    def _PhysicsAt(self, tick: int) -> dict:
        """Physical output values at given tick."""
        vals = {}
        for ch, layers in self._steps.items():
            vals[ch] = {}
            for l, step in layers.items():
                base = self._statics.get(ch, {}).get(l, [0.0, 0.0])
                if self._phase == "ACTION":
                    amp = base[0] + step[0] * tick
                else:
                    peak_amp = base[0] + step[0] * self._peakTick
                    amp = peak_amp - step[0] * tick
                vals[ch][l] = [round(amp, 4), base[1]]
        return vals

    def _ResetPhysics(self) -> dict:
        """Physical values during reset phase (tick=-1). Always uses resetTableData."""
        vals = {}
        for ch, layers in self._steps.items():
            vals[ch] = {}
            for l in layers:
                vals[ch][l] = self._resetTableData.get(ch, {}).get(l, [0.0, 50.0])
        return vals

    # ═══════════════════════════════════════════════════
    #  Compilation
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.isActive and self._mode == 0:
            nodes = self._CompileManualUpdate(params)
            return {"hot_update": True, "nodes": nodes, "trig_id": 0}
        return self._CompileStart(params)

    def _CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.ResetState()
        self.isActive = True
        self._phase = "ACTION"

        sys_cfg = params.get("sys") or {}
        self._mode = sys_cfg.get("mode", 1)
        self._changeMode = sys_cfg.get("changeMode", 0)
        self._returnMode = sys_cfg.get("returnMode", 0)
        self._stepTime = sys_cfg.get("stepTime", 1000)
        self._logicMask = sys_cfg.get("logicMask", 255)
        self._doMask = sys_cfg.get("doMask", 0)
        self._doCtrlMask = sys_cfg.get("doCtrlMask", 0)
        self._overMask = self._doMask ^ self._doCtrlMask
        debounce = sys_cfg.get("debounce", 61)

        self._statics = params.get("statics") or {}
        self._steps = params.get("steps") or {}
        self._count = params.get("count", 1)
        limits = params.get("limits") or {}

        payload = params.get("payload") or {}
        enablePreReset = payload.get("enablePreTestReset", False)
        preResetTime = payload.get("preTestResetTime", 1000)
        self._enableStepReset = payload.get("enableStepReset", False)
        self._stepResetMode = payload.get("stepResetMode", 0)
        self._stepResetTime = payload.get("stepResetTime", 100)
        self._resetTableData = payload.get("resetTableData", {})

        # Fill missing channels in resetTableData
        for ch in ApiHdlMgr.API_V_CHS + ApiHdlMgr.API_I_CHS:
            if str(ch) not in self._resetTableData:
                self._resetTableData[str(ch)] = {"0": [0.0, 50.0]}

        regStatics = self._Reg(self._statics)
        regStepsFwd = self._Reg(self._steps, 1.0, is_delta=True)
        self._regStepsRev = self._Reg(self._steps, -1.0, is_delta=True)
        self._regReset = self._Reg(self._resetTableData)

        # Anti-Tear: ensure stepped layers have ≥1 LSB
        for ch_str, layers in regStepsFwd.items():
            if ch_str not in regStatics:
                regStatics[ch_str] = {}
            for l_str, sv in layers.items():
                if sv[0] != 0:
                    if l_str not in regStatics[ch_str]:
                        dp = HWConfig.ConvertFreqToReg(50.0) if l_str == "0" else 0
                        regStatics[ch_str][l_str] = [0, dp]
                    if regStatics[ch_str][l_str][0] == 0:
                        regStatics[ch_str][l_str][0] = 1 if sv[0] > 0 else -1

        # Limits
        properLimits = {}
        for ch_str, val in limits.items():
            ch = int(ch_str)
            if isinstance(val, list) and len(val) >= 2:
                properLimits[ch_str] = [
                    HWConfig.ConvertAmpToReg(float(val[0]), ch in ApiHdlMgr.API_I_CHS),
                    HWConfig.ConvertFreqToReg(float(val[1]))
                ]

        sysOut = {
            "vChannels": ApiHdlMgr.API_V_CHS,
            "iChannels": ApiHdlMgr.API_I_CHS,
            "debounce": debounce,
            "limit": properLimits
        }

        nodes = []

        if self._mode == 1:
            return self._CompileAuto(nodes, sysOut, params, enablePreReset, preResetTime, regStatics, regStepsFwd)
        else:
            return self._CompileManual(nodes, sysOut, regStatics)

    def _CompileAuto(self, nodes, sysOut, params, enablePreReset, preResetTime, regStatics, regStepsFwd):
        # Node IDs
        self._actionId = 1 if enablePreReset else 0
        self._holdId = self._actionId + 1
        self._returnId = self._actionId + 2

        # ── Node P: Pre-test reset ──
        if enablePreReset:
            nodes.append({
                "id": 0,
                "doActions": [{"delay": 0, "mask": self._doMask}] if self._doMask else [],
                "static": self._regReset,
                "dynamic": {"interval": preResetTime, "count": 1, "step": {}},
                "triggers": [{"condition": "COUNT_OVER", "nextId": self._actionId}]
            })

        # ── Node A: Forward sweep ──
        n_a = {
            "id": self._actionId,
            "doActions": [] if self._enableStepReset else (
                [{"delay": 0, "mask": self._overMask}] if self._overMask else []),
            "static": regStatics,
            "dynamic": {"interval": self._stepTime, "count": self._count, "step": regStepsFwd},
            "triggers": []
        }

        if self._enableStepReset:
            n_a["dynamic"]["reset"] = {
                "time": self._stepResetTime,
                "static": self._regReset,  # Always resetTableData
                "resetDo": self._doMask,
                "overDo": self._overMask
            }

        # Trigger target: holdId if changeMode=1, terminal if changeMode=0
        tgt = self._holdId if self._changeMode in (1, 2) else _TERM

        if self._returnMode == 0:
            n_a["triggers"].append({"condition": "DI_MATCH", "diMask": self._logicMask, "nextId": tgt})
            n_a["triggers"].append({"condition": "COUNT_OVER", "nextId": tgt})
        else:
            n_a["triggers"].append({"condition": "COUNT_OVER", "nextId": tgt})

        nodes.append(n_a)

        # ── DC Compensation (optional) ──
        dcComp = params.get("dcComp")
        prevStatics = params.get("prevStatics")
        if dcComp and prevStatics:
            tau = dcComp.get("tau", 50.0)
            dt = dcComp.get("dt", 10.0)
            gateCh = dcComp.get("gateChannel", 0)
            gatePhase = dcComp.get("gatePhase", HWConfig.ConvertPhaseToReg(0))
            prevReg = self._Reg(prevStatics)
            modStatic, dynBlock = ApiHdlMgr.CompileDcComp(prevReg, regStatics, gateCh, gatePhase, tau, dt)
            dcId = 10
            for t in n_a["triggers"]:
                if t.get("nextId") not in (None, _TERM):
                    t["nextId"] = dcId
                elif t.get("condition") == "COUNT_OVER":
                    t["nextId"] = dcId
            nodes.append({
                "id": dcId,
                "doActions": [{"delay": 0, "mask": self._doCtrlMask}] if self._doCtrlMask else [],
                "static": modStatic,
                "dynamic": dynBlock,
                "triggers": [{"condition": "TIME_OUT", "timeout": (dynBlock["count"] + 5) * dt, "nextId": tgt}]
            })

        # ── Node H: Empty hold (only changeMode=1) ──
        if self._changeMode in (1, 2):
            nodes.append({"id": self._holdId, "doActions": [], "static": {}, "triggers": []})

        return {"sys": sysOut, "nodes": nodes}

    def _CompileManual(self, nodes, sysOut, regStatics):
        self._actionId = 0
        nodes.append({
            "id": 0,
            "doActions": [{"delay": 0, "mask": self._doMask}] if self._doMask else [],
            "static": regStatics,
            "triggers": [
                {"condition": "DI_MATCH", "diMask": self._logicMask | 0x200, "nextId": _TERM},
                {"condition": "TIME_OUT", "timeout": 150000, "nextId": _TERM}
            ]
        })
        return {"sys": sysOut, "nodes": nodes}

    def _CompileManualUpdate(self, new_params: Dict[str, Any]) -> list:
        self._statics = new_params.get("statics") or self._statics
        sys_cfg = new_params.get("sys") or {}
        self._logicMask = sys_cfg.get("logicMask", self._logicMask)
        return [{
            "id": 0,
            "doActions": [],
            "static": self._Reg(self._statics),
            "triggers": [
                {"condition": "DI_MATCH", "diMask": self._logicMask | 0x200, "nextId": _TERM},
                {"condition": "TIME_OUT", "timeout": 150000, "nextId": _TERM}
            ]
        }]

    # ═══════════════════════════════════════════════════
    #  Hot Update: Build Node R at trigger time
    # ═══════════════════════════════════════════════════

    def _BuildReturnNode(self) -> dict:
        actualCount = self._peakTick
        # Start with ALL original statics, then overlay peak values for stepped channels
        import copy
        fullPeak = copy.deepcopy(self._statics)
        peakStepped = self._tripVals or self._PhysicsAt(self._peakTick)
        for ch, layers in peakStepped.items():
            if ch not in fullPeak:
                fullPeak[ch] = {}
            for l, val in layers.items():
                fullPeak[ch][l] = val
        peakReg = self._Reg(fullPeak)

        useReturnReset = (self._stepResetMode == 1 and self._enableStepReset)

        n_r = {
            "id": self._returnId,
            "doActions": [] if useReturnReset else (
                [{"delay": 0, "mask": self._overMask}] if self._overMask else []),
            "static": peakReg,
            "dynamic": {"interval": self._stepTime, "count": actualCount, "step": self._regStepsRev},
            "triggers": []
        }

        if useReturnReset:
            n_r["dynamic"]["reset"] = {
                "time": self._stepResetTime,
                "static": self._regReset,
                "resetDo": self._doMask,
                "overDo": self._overMask
            }

        if self._returnMode == 0:
            n_r["triggers"].append({"condition": "DI_MATCH", "diMask": self._logicMask | 0x500, "nextId": _TERM})
            n_r["triggers"].append({"condition": "COUNT_OVER", "nextId": _TERM})
        else:
            n_r["triggers"].append({"condition": "COUNT_OVER", "nextId": _TERM})

        return n_r

    # ═══════════════════════════════════════════════════
    #  Report
    # ═══════════════════════════════════════════════════

    def _BuildReport(self) -> dict:
        r = {
            "tripTime": round(self._tripTime, 1) if self._tripTime is not None else None,
            "tripValues": self._tripVals
        }
        if self._changeMode in (1, 2) or self._mode == 0:
            r["returnTime"] = round(self._returnTime, 1) if self._returnTime is not None else None
            r["returnValues"] = self._returnVals
            if self._tripVals and self._returnVals:
                try:
                    pch = list(self._steps.keys())[0]
                    pl = list(self._steps[pch].keys())[0]
                    tv = self._tripVals[pch][pl][0]
                    rv = self._returnVals[pch][pl][0]
                    if abs(tv) > 0.0001:
                        r["returnRatio"] = round(rv / tv, 4)
                except Exception:
                    pass
        return r

    def _Emit(self, out: list):
        self.isActive = False
        mk = getattr(self, "MODULE_KEY", "ac_test")
        out.append({"module": mk, "type": "report", "data": self._BuildReport()})
        out.append({"module": mk, "type": "stop"})

    # ═══════════════════════════════════════════════════
    #  Telemetry Intercept
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        if not self.isActive:
            return [data]
        t = data.get("type", "")
        out = []
        mk = getattr(self, "MODULE_KEY", "ac_test")

        if t == "terminal":
            self._Emit(out)
            return out
        if t == "di_change":
            return self._OnDiChange(data, out)
        if t == "value_update":
            return self._OnValueUpdate(data, out, mk)
        if t == "trigger":
            return self._OnTrigger(data, out, mk)
        return [data]

    def _OnDiChange(self, data, out):
        new_di = data.get("di", -1)
        hw_ts = data.get("hw_ts", 0)
        old_di = self._lastDi

        if new_di == old_di:
            return out

        self._lastDi = new_di
        self._lastDiTs = hw_ts

        if self._lastValueTs > 0 and old_di != -1:
            changed = (new_di ^ old_di) & 0xFF
            mask = self._logicMask & 0xFF
            if changed & mask:
                dt_us = (hw_ts - self._lastValueTs) & 0xFFFFFFFF
                dt_ms = round(dt_us / 1000.0, 1)
                if self._phase == "ACTION" and self._tripTime is None:
                    self._tripTime = dt_ms
                    self._tripVals = self._PhysicsAt(self._lastTick)
                    self._peakTick = self._lastTick
                elif self._phase == "RETURN" and self._returnTime is None:
                    self._returnTime = dt_ms
                    self._returnVals = self._PhysicsAt(self._lastTick)

        out.append({"type": "value_update", "di": new_di})
        return out

    def _OnValueUpdate(self, data, out, mk):
        tick = data.get("tick", 0)
        nid = data.get("id", 0)
        new_do = data.get("do", self._lastDo)
        self._lastValueTs = data.get("hw_ts", self._lastValueTs)

        # Ignore hold node (empty placeholder, don't overwrite _lastTick)
        if nid == self._holdId:
            return out

        # Phase transition
        if self._phase == "ACTION" and nid == self._returnId:
            self._phase = "RETURN"
            self._peakTick = self._lastTick
        elif nid == self._actionId:
            self._phase = "ACTION"

        # DO change
        if new_do != self._lastDo:
            self._lastDo = new_do
            out.append({"module": mk, "type": "value_update", "do": new_do})

        # Reset phase (tick=-1)
        if tick == -1:
            vals = self._ResetPhysics()
            msg = {"module": mk, "type": "value_update", "static": vals}
            if msg != self._lastSent:
                self._lastSent = msg
                out.append(msg)
            return out

        # Normal tick
        if tick >= 0:
            self._lastTick = tick
        if tick >= 0 or tick == -2:
            vals = self._PhysicsAt(self._lastTick)
            if vals:
                msg = {"module": mk, "type": "value_update", "static": vals}
                if msg != self._lastSent:
                    self._lastSent = msg
                    out.append(msg)
        return out

    def _OnTrigger(self, data, out, mk):
        cond = data.get("condition")
        next_id = data.get("next_id")

        # ── Core decision: Node A ended, jumped to Node H ──
        if next_id == self._holdId and self._changeMode in (1, 2):
            # Fallback trip time
            if cond == "DI_MATCH" and self._phase == "ACTION" and self._tripTime is None:
                dt_us = (self._lastDiTs - self._lastValueTs) & 0xFFFFFFFF
                self._tripTime = round(dt_us / 1000.0, 1)
                self._tripVals = self._PhysicsAt(self._lastTick)
                self._peakTick = self._lastTick

            # peakTick = the actual last tick of forward sweep (for return count & starting point)
            # For returnMode=1 (full sweep), this is always the full count even if DI triggered mid-sweep
            self._peakTick = self._lastTick

            # Build and inject Node R
            n_r = self._BuildReturnNode()
            out.append({"internal_hot_update": True, "nodes": [n_r]})
            out.append({"internal_manual_trig": True, "manual_trig_id": self._returnId})
            return out

        # DI_MATCH fallback (for changeMode=0 scenarios)
        if cond == "DI_MATCH":
            if self._phase == "ACTION" and self._tripTime is None:
                dt_us = (self._lastDiTs - self._lastValueTs) & 0xFFFFFFFF
                self._tripTime = round(dt_us / 1000.0, 1)
                self._tripVals = self._PhysicsAt(self._lastTick)
                self._peakTick = self._lastTick
            elif self._phase == "RETURN" and self._returnTime is None:
                dt_us = (self._lastDiTs - self._lastValueTs) & 0xFFFFFFFF
                self._returnTime = round(dt_us / 1000.0, 1)
                self._returnVals = self._PhysicsAt(self._lastTick)

        elif cond == "TIME_OUT" and self._mode == 0:
            self.isActive = False
            out.append({"module": mk, "type": "error",
                         "msg": "Manual mode input timeout exceeded 150s. System stopped."})

        return out
