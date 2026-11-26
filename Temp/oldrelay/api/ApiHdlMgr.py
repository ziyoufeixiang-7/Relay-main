import importlib
import inspect
import pkgutil
import math
from typing import Dict, Any, List, Tuple

from logic.FPGACodec import HWConfig
from logic.FlatCodec import FlatCodec, NodeKey, TrigCond

import api

class ApiHdlMgr:
    # API 级别逻辑通道索引 (前端/Handler 使用)
    API_V_CHS = [0, 1, 2, 3, 4, 5]
    API_I_CHS = [6, 7, 8, 9, 10, 11]

    def __init__(self):
        self.handlers: Dict[str, Any] = {}
        self._DiscoverHandlers()
        
        import os, json
        self.calib_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "calibration.json")
        self.calib_data = {}
        self.LoadCalib()

    def LoadCalib(self):
        import os, json
        try:
            if os.path.exists(self.calib_filepath):
                with open(self.calib_filepath, 'r', encoding='utf-8') as f:
                    self.calib_data = json.load(f)
            else:
                self.calib_data = {}
        except Exception as e:
            print(f"Failed to load calibration file: {e}")
            self.calib_data = {}

    def SaveCalib(self):
        import os, json
        os.makedirs(os.path.dirname(self.calib_filepath), exist_ok=True)
        with open(self.calib_filepath, 'w', encoding='utf-8') as f:
            json.dump(self.calib_data, f, indent=4)

    def ApplyAbsolute(self, ch_idx: int, value: float) -> float:
        """应用绝对值校准：Command = value * factor - bias (优先使用 factor_dc)"""
        ch_str = str(ch_idx)
        if ch_str in self.calib_data:
            factor = self.calib_data[ch_str].get("factor_dc", self.calib_data[ch_str].get("factor", 1.0))
            bias = self.calib_data[ch_str].get("bias", 0.0)
            return bias - value * factor
        return value

    def ApplyDelta(self, ch_idx: int, value: float, l_idx: int = 1) -> float:
        """应用增量校准（用于扫频 Step）：CommandDelta = Delta * factor (区分 DC/AC 层)"""
        ch_str = str(ch_idx)
        if ch_str in self.calib_data:
            if l_idx == 0:
                factor = self.calib_data[ch_str].get("factor_dc", self.calib_data[ch_str].get("factor", 1.0))
            else:
                factor = self.calib_data[ch_str].get("factor_ac", self.calib_data[ch_str].get("factor", 1.0))
            return - value * factor
        return value

    def UpdateCalib(self, ch_idx: int, factor: float, bias: float):
        """兼容原有单 factor 更新，同时写入 factor_dc 和 factor_ac"""
        ch_str = str(ch_idx)
        self.calib_data[ch_str] = {
            "factor_dc": round(factor, 8),
            "factor_ac": round(factor, 8),
            "bias": round(bias, 8)
        }
        self.SaveCalib()

    def UpdateCalibDual(self, ch_idx: int, factor_dc: float, factor_ac: float, bias: float):
        """双因子增益独立更新"""
        ch_str = str(ch_idx)
        self.calib_data[ch_str] = {
            "factor_dc": round(factor_dc, 8),
            "factor_ac": round(factor_ac, 8),
            "bias": round(bias, 8)
        }
        self.SaveCalib()

    # ═══════════════════════════════════════════════════
    #  共享物理值转换方法 (替代各 Handler 的私有副本)
    # ═══════════════════════════════════════════════════

    def PhysToReg(self, phys_dict: dict, factor: float = 1.0, is_delta: bool = False) -> dict:
        """
        将前端物理值字典转换为 FPGA 寄存器值字典。
        统一替代各 Handler 中的 _PhysToRegDict 方法。

        Args:
            phys_dict: {ch_str: {layer_str: [amplitude, freq_or_phase]}}
            factor:    幅值缩放因子 (如 -1.0 用于反向步进)
            is_delta:  False 时 Layer0 用 ApplyAbsolute，True 时全部用 ApplyDelta
        """
        reg_d = {}
        for ch_str, layers in phys_dict.items():
            ch_idx = int(ch_str)
            is_cur = ch_idx in self.API_I_CHS
            reg_d[ch_str] = {}
            for l_idx_str, vals in layers.items():
                l_idx = int(l_idx_str)
                v_phys = vals[0]

                if not is_delta and l_idx == 0:
                    v_calib = self.ApplyAbsolute(ch_idx, v_phys)
                else:
                    v_calib = self.ApplyDelta(ch_idx, v_phys, l_idx=l_idx)

                v = v_calib * factor
                if abs(v) < 0.000001:
                    v = 0.0
                a_reg = HWConfig.ConvertAmpToReg(v, is_cur)
                p_reg = HWConfig.ConvertFreqToReg(vals[1]) if l_idx == 0 else HWConfig.ConvertPhaseToReg(vals[1])
                reg_d[ch_str][l_idx_str] = [a_reg, p_reg]
        return reg_d

    def MakeChannelReg(self, ch_idx: int, amplitude: float, angle_deg: float,
                         freq_hz: float) -> dict:
        """
        为单个通道生成 Layer0 + Layer1 寄存器值。
        统一替代 InvTimeItHdl / InvTimeVftHdl 中的 _MakeChannelReg。

        Args:
            ch_idx:      通道索引
            amplitude:   AC 幅值 (物理值)
            angle_deg:   相位角 (度)
            freq_hz:     频率 (Hz)
        """
        is_cur = ch_idx in self.API_I_CHS
        # Layer 0: DC = 0V/0A at rated frequency
        dc_calib = self.ApplyAbsolute(ch_idx, 0.0)
        dc_amp_reg = HWConfig.ConvertAmpToReg(dc_calib, is_cur)
        freq_reg = HWConfig.ConvertFreqToReg(freq_hz)
        # Layer 1: AC at amplitude and angle (指定 l_idx=1)
        ac_calib = self.ApplyDelta(ch_idx, amplitude, l_idx=1)
        if abs(ac_calib) < 0.000001:
            ac_calib = 0.0
        ac_amp_reg = HWConfig.ConvertAmpToReg(ac_calib, is_cur)
        phase_reg = HWConfig.ConvertPhaseToReg(angle_deg)
        return {
            "0": [dc_amp_reg, freq_reg],
            "1": [ac_amp_reg, phase_reg]
        }

    @staticmethod
    def FillMissingChannels(static_dict: dict, freq_reg: int = None) -> dict:
        """
        补全 static_dict 中未配置的通道 (0~15) 为 0V / 默认频率。
        原地修改并返回 static_dict。

        Args:
            static_dict: 待补全的寄存器值字典
            freq_reg:    频率寄存器值 (None 时使用 50Hz)
        """
        if freq_reg is None:
            freq_reg = HWConfig.ConvertFreqToReg(50.0)
        for ch_idx in range(16):
            ch_s = str(ch_idx)
            if ch_s not in static_dict:
                static_dict[ch_s] = {"0": [0, freq_reg]}
        return static_dict

    # ═══════════════════════════════════════════════════
    #  旧协议 → 新协议 转译层 (过渡期使用)
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _remap_dds_dict(d: dict) -> dict:
        """将 DDS 字典中的 API 通道号键映射为 HW 物理通道号键"""
        return {str(HWConfig.MapChannel(int(k))): v for k, v in d.items()}

    @classmethod
    def _remap_channels_in_node(cls, node: dict) -> dict:
        """
        将 Handler 输出的 JSON 节点中的 API 通道号映射为 HW 物理通道号。
        修改: static, dynamic.step, dynamic.steps[], dynamic.reset.static,
              dynamic.phaseGate.channel
        """
        import copy
        n = copy.deepcopy(node)
        if "static" in n and n["static"]:
            n["static"] = cls._remap_dds_dict(n["static"])
        if "dynamic" in n and n["dynamic"]:
            dyn = n["dynamic"]
            if "step" in dyn and dyn["step"]:
                dyn["step"] = cls._remap_dds_dict(dyn["step"])
            if "steps" in dyn and dyn["steps"]:
                dyn["steps"] = [cls._remap_dds_dict(s) for s in dyn["steps"]]
            if "reset" in dyn and dyn["reset"] and "static" in dyn["reset"]:
                dyn["reset"]["static"] = cls._remap_dds_dict(dyn["reset"]["static"])
            if "phaseGate" in dyn and "channel" in dyn["phaseGate"]:
                dyn["phaseGate"]["channel"] = HWConfig.MapChannel(
                    int(dyn["phaseGate"]["channel"])
                )
        return n

    @classmethod
    def CompileDdsDict(cls, data_dict: dict) -> list:
        words = []
        for ch_str, layers in data_dict.items():
            ch = int(ch_str)
            for l_str, vals in layers.items():
                words.append(FlatCodec.PackDdsWord(ch, int(l_str), int(vals[0]), int(vals[1])))
        return words

    @classmethod
    def CompileTrigger(cls, trig: dict) -> int:
        _COND_MAP = {
            "DI_MATCH": TrigCond.ConDiMatch,
            "TIME_OUT": TrigCond.ConTimeout,
            "COUNT_OVER": TrigCond.ConCountOver,
        }
        cond = _COND_MAP.get(trig.get("condition", ""), TrigCond.ConDiMatch)
        next_id = trig.get("nextId", -1)
        if next_id < 0:
            next_id = next_id & 0xFFFF
        if cond == TrigCond.ConDiMatch:
            union = trig.get("diMask", 0) & 0xFFFFFFFF
        elif cond == TrigCond.ConTimeout:
            union = int(trig.get("timeout", 0)) & 0xFFFFFFFF
        else:
            union = 0
        return FlatCodec.PackTrigger(cond, next_id, union)

    @classmethod
    def CompileDoActions(cls, do_list: list) -> list:
        return [FlatCodec.PackDoAction(a.get("delay", 0), a.get("mask", 0)) for a in do_list]

    @classmethod
    def CompileNode(cls, json_node: dict) -> dict:
        flat = {NodeKey.Id: json_node["id"]}

        do_key = "doActions" if "doActions" in json_node else "do_actions"
        if do_key in json_node and json_node[do_key]:
            flat[NodeKey.DoAction] = cls.CompileDoActions(json_node[do_key])

        if json_node.get("static"):
            flat[NodeKey.Static] = cls.CompileDdsDict(json_node["static"])

        if "dynamic" in json_node and json_node["dynamic"]:
            dyn = json_node["dynamic"]
            flat[NodeKey.Interval] = dyn.get("interval", 10)
            count = dyn.get("count", 0)

            if "step" in dyn and dyn["step"] and count > 0:
                single_step = cls.CompileDdsDict(dyn["step"])
                flat[NodeKey.Steps] = [single_step] * count
            elif "steps" in dyn and dyn["steps"]:
                flat[NodeKey.Steps] = [cls.CompileDdsDict(s) for s in dyn["steps"]]

            if "phaseGate" in dyn:
                pg = dyn["phaseGate"]
                flat[NodeKey.PhaseGate] = FlatCodec.PackPhaseGate(
                    pg.get("channel", 0), pg.get("phase", 0)
                )

            if "reset" in dyn:
                rst = dyn["reset"]
                if rst.get("time"):
                    flat[NodeKey.ResetTime] = rst["time"]
                if rst.get("static"):
                    flat[NodeKey.ResetStatic] = cls.CompileDdsDict(rst["static"])
                enter_mask = rst.get("resetDo", 0)
                exit_mask = rst.get("overDo", 0)
                if enter_mask or exit_mask:
                    flat[NodeKey.ResetDo] = FlatCodec.PackResetDo(exit_mask, enter_mask)

        if json_node.get("triggers"):
            flat[NodeKey.Triggers] = [cls.CompileTrigger(t) for t in json_node["triggers"]]

        return flat

    @classmethod
    def JsonNodesToFlat(cls, json_nodes: list) -> list:
        return [
            cls.CompileNode(cls._remap_channels_in_node(n))
            for n in json_nodes
        ]

    def _DiscoverHandlers(self):
        for _, module_name, _ in pkgutil.iter_modules(api.__path__):
            if module_name.endswith("Hdl"):
                mod = importlib.import_module(f"api.{module_name}")
                for name, obj in inspect.getmembers(mod, inspect.isclass):
                    if name.endswith("Hdl"):
                        module_keys = getattr(obj, "MODULE_KEYS", None)
                        if module_keys:
                            for mk in module_keys:
                                handler_instance = obj()
                                handler_instance.mgr = self
                                handler_instance.MODULE_KEY = mk
                                self.handlers[mk] = handler_instance
                        else:
                            module_key = getattr(obj, "MODULE_KEY", None)
                            if module_key:
                                handler_instance = obj()
                                handler_instance.mgr = self
                                handler_instance.MODULE_KEY = module_key
                                self.handlers[module_key] = handler_instance

    def get_handler(self, module_name: str) -> Any:
        return self.handlers.get(module_name)

    def get_all_active(self) -> List[Any]:
        return [h for h in self.handlers.values() if getattr(h, "isActive", False)]

    def ClearOtherHandlers(self, exclude_module: str):
        for name, hdl in self.handlers.items():
            if name != exclude_module and getattr(hdl, "isActive", False):
                if hasattr(hdl, "ResetState"):
                    hdl.ResetState()
                else:
                    hdl.isActive = False

    @staticmethod
    def CompileDcComp(
        prev_static: Dict[str, Dict[str, List]],
        curr_static: Dict[str, Dict[str, List]],
        gate_channel: int,
        gate_phase: int,
        tau: float,
        dt: float = 10.0
    ) -> Tuple[dict, dict]:
        """
        Convert dcComp parameters into a modified static + dynamic block.
        """
        M = max(2, math.ceil(4 * tau / dt))  # 4τ covers 98% decay

        # Collect all channels that have DC layer (layer "0") in either state
        all_chs = set()
        for s in (prev_static, curr_static):
            for ch_str, layers in s.items():
                if "0" in layers:
                    all_chs.add(ch_str)

        if not all_chs:
            return curr_static, {"interval": dt, "count": 0, "steps": []}

        # Compute absolute DC compensation values for each channel at each step
        # DC_comp[i] = ΔV × e^(-i×dt/τ)  where ΔV = prev_dc - curr_dc
        abs_dc = {}  # ch -> [comp_0, comp_1, ..., comp_{M-1}]
        for ch in all_chs:
            prev_dc = int(prev_static.get(ch, {}).get("0", [0, 0])[0])
            curr_dc = int(curr_static.get(ch, {}).get("0", [0, 0])[0])
            delta_v = prev_dc - curr_dc  # Compensation = old - new

            ch_comp = []
            for i in range(M):
                comp = round(delta_v * math.exp(-i * dt / tau))
                ch_comp.append(comp)
            abs_dc[ch] = ch_comp

        # 1. Build modified static: curr_static with DC layer = B.DC + comp[0]
        modified_static = {}
        for ch_str, layers in curr_static.items():
            modified_static[ch_str] = {}
            for l_str, vals in layers.items():
                if l_str == "0" and ch_str in all_chs:
                    # DC layer: add comp[0] to the target amplitude
                    modified_static[ch_str][l_str] = [
                        int(vals[0]) + abs_dc[ch_str][0],
                        vals[1]
                    ]
                else:
                    modified_static[ch_str][l_str] = list(vals)

        # 2. Build steps: per-step increments starting from step 1
        #    St_dc[i] = comp[i] - comp[i-1]  (i >= 1)
        steps = []
        for i in range(1, M):
            step_dict = {}
            for ch in all_chs:
                delta = abs_dc[ch][i] - abs_dc[ch][i - 1]
                if delta != 0:
                    step_dict[ch] = {"0": [delta, 0]}  # DC layer increment, freq delta=0
            steps.append(step_dict)

        dynamic_block = {
            "interval": dt,
            "count": len(steps),
            "steps": steps,
            "phaseGate": {
                "channel": gate_channel,
                "phase": gate_phase
            }
        }

        return modified_static, dynamic_block