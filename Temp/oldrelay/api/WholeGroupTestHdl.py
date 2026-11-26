
import logging
import copy
import math
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("WholeGroupTestHdl")

# 开入端口名称映射 (bit 索引 → 报告字段名)
DI_NAMES = ["diA", "diB", "diC", "diR", "dia", "dib", "dic", "dir"]

class WholeGroupTestHdl:
    MODULE_KEYS = ["whole_group_test"]

    def __init__(self):

        self.isActive = False
        self.engineToFrontId: Dict[int, int] = {}
        self.nodePhysData: Dict[int, dict] = {}
        self.mgr = None

        # Runtime tracking
        self.currentNodeId = -1
        self.lastKnownDi = 0
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono: float = 0.0

        # DI SOE per engine node
        self.diSoeByNode: Dict[int, Dict[str, Optional[float]]] = {}
        self.nodeEntryHwTs: Dict[int, int] = {}
        self.nodeEntryMono: Dict[int, float] = {}
        self.enteredNodes: List[int] = []
        self.manualTrigTargets: Dict[int, int] = {}

    def ResetState(self):
        self.isActive = False
        self.engineToFrontId = {}
        self.nodePhysData = {}
        self.currentNodeId = -1
        self.lastKnownDi = 0
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono = 0.0
        self.diSoeByNode = {}
        self.nodeEntryHwTs = {}
        self.nodeEntryMono = {}
        self.enteredNodes = []
        self.manualTrigTargets = {}

    def _PhysToRegDict(self, phys_dict: dict) -> dict:
        """Delegates to self.mgr.PhysToReg"""
        return self.mgr.PhysToReg(phys_dict) if self.mgr else {}

    def _BuildDiMask(self, bi_settings: list, fault_type: int, is_reclose: bool) -> int:
        """
        故障态判断跳闸: 根据 fault_type (0=A接地, 3=AB相间...) 判断要监听哪个跳接点 (跳A/跳B/跳C/三跳)
        跳闸态判断重合: 监听 '重合闸接点'
        """
        mask = 0
        
        # 故障类型 0=A接地, 1=B接地, 2=C接地
        # 3=AB相间, 4=BC相间, 5=CA相间
        # 6=AB接地, 7=BC接地, 8=CA接地, 9=三相
        
        funcs = []
        if is_reclose:
            funcs = [4] # 4=重合闸接点
        else:
            funcs = [3] # 默认一定监听三跳
            if fault_type in [0, 3, 5, 6, 8]: # 涉及A相
                funcs.append(0) # 跳A
            if fault_type in [1, 3, 4, 6, 7]: # 涉及B相
                funcs.append(1) # 跳B
            if fault_type in [2, 4, 5, 7, 8]: # 涉及C相
                funcs.append(2) # 跳C
            if fault_type == 9: # 三相短路
                funcs = [3] # 仅三跳？通常也可以监听跳A/B/C，全加上吧
                funcs.extend([0, 1, 2])

        for i, setting in enumerate(bi_settings):
            if i >= 8: break
            func = setting.get("function", 5)
            if func in funcs:
                mask |= (1 << i)
                
        return mask

    def _GetTripStatic(self, node_static: dict, pt_is_line: bool, ux_setting: dict) -> dict:
        """构建跳闸态 static (电流清零，PT复位，Ux覆写)"""
        static = copy.deepcopy(node_static)
        
        # 电流清零
        for ch in ApiHdlMgr.API_I_CHS:
            ch_str = str(ch)
            if ch_str in static:
                for layer in static[ch_str]:
                    static[ch_str][layer][0] = 0.0
                    
        # 电压复位 (PT位置) 
        # TODO: 前端其实已经处理好了 Node 0 的额定电压，这里可以直接用 Node 0 的电压，但是简化起见，我们假设前端的跳闸态模板如果发了，我们就可以用，没发就用 Node 0 的电压。
        # 实际上用户文档说："跳闸后电压由 PT 位置决定...前端已修正"。等等，文档 8.2 节说：“跳闸态与重合态生成：后端基于模板和时序参数，动态复制并修改生成... Ux抽取电压覆写”。
        # 文档又说："PT/CT 极性及变比修正：前端已在下发前完成修正" 但 "跳闸态生成：后端动态复制并修改生成". 
        
        # 覆写 Ux (ch3)
        if "3" in static and "0" in static["3"]:
            ux_amp = ux_setting.get("amplitude", 0.0)
            ux_angle = ux_setting.get("angle", 0.0)
            static["3"]["0"][0] = ux_amp
            static["3"]["1"] = [ux_amp, ux_angle] # layer 1 is phase
            
        return static

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 61)
        
        nodes_in = params.get("nodes") or []
        payload = params.get("payload") or {}

        template_nodes = {n["id"]: n for n in nodes_in}
        node0 = template_nodes.get(0, {"static": {}})
        node1 = template_nodes.get(1, {"static": {}})
        node2 = template_nodes.get(2, {"static": {}})

        fault_settings = payload.get("faultSettings", {})
        test_limit_ms = fault_settings.get("testLimitMs", 5000)
        fault_nature = fault_settings.get("faultNature", 0) # 0=瞬时, 1=永久
        transition = fault_settings.get("transition", {})
        is_transition = transition.get("enabled", False)
        trans_moment = transition.get("transitionMoment", 0) # 0=故障后, 1=重合后
        trans_time_ms = transition.get("transitionTimeMs", 100)
        
        first_fault_type = fault_settings.get("firstFault", {}).get("type", 0)
        
        trigger_mode = payload.get("triggerMode", {})
        prefault_trig = trigger_mode.get("prefaultTrigger", {"condition": "MANUAL_TRIG"})
        
        bi_settings = payload.get("binaryInput", {}).get("settings", [])
        bo_settings = payload.get("binaryOutput", {})
        do_mode = bo_settings.get("controlMode", 0)
        
        ui_output = payload.get("uiOutput", {})
        ux_after_trip = ui_output.get("uxAfterTrip", {})

        engine_nodes = []
        
        # Helper to add node
        def add_node(front_id: int, engine_id: int, static_phys: dict, triggers: list, do_actions: list = None):
            self.engineToFrontId[engine_id] = front_id
            self.nodePhysData[front_id] = {"statics": static_phys, "steps": {}}
            
            # 补全 (物理值, 在 _PhysToRegDict 之前)
            for ch in range(16):
                ch_s = str(ch)
                if ch_s not in static_phys:
                    static_phys[ch_s] = {"0": [0, 50.0]}
                    
            reg_static = self._PhysToRegDict(static_phys)
            node = {
                "id": engine_id,
                "mode": "STATIC",
                "static": reg_static,
                "do_actions": do_actions or [],
                "triggers": triggers
            }
            engine_nodes.append(node)
            return node

        # Build DI Masks
        di_trip_mask = self._BuildDiMask(bi_settings, first_fault_type, False)
        di_reclose_mask = self._BuildDiMask(bi_settings, first_fault_type, True)
        
        # DO Actions
        do_trip_action = [{"delay": 0, "mask": 0}] # Default DO
        do_reclose_action = [{"delay": 0, "mask": bo_settings.get("initialMask", 3)}]
        
        if do_mode == 0:
            # Mode 0: DO changes on trip/reclose.
            # Trip -> mask=0 (open). Reclose -> mask=initialMask (close)
            pass
        elif do_mode == 1:
            delay_rev = bo_settings.get("delayReversal", {})
            d1 = delay_rev.get("delay", 100)
            h1 = delay_rev.get("hold", 100)
            do_trip_action = [] # Not driven by trip
            do_reclose_action = []
        elif do_mode == 2:
            do_trip_action = []
            do_reclose_action = []
            
        # 1. Node 0 (Pre-fault)
        trig0 = prefault_trig.copy()
        trig0["nextId"] = 1 # next internal id
        if trig0.get("condition") == "MANUAL_TRIG":
            node0_triggers = []
            self.manualTrigTargets[0] = 1  # Engine node 0 → manual trig to node 1
        else:
            node0_triggers = [trig0]
        add_node(0, 0, node0.get("static", {}), node0_triggers, [{"delay": 0, "mask": bo_settings.get("initialMask", 3)}])

        # 2. Node 1 (Fault)
        dc_comp = payload.get("testControl", {}).get("dcComponent", {})
        
        node1_static = node1.get("static", {})
        if dc_comp.get("enabled", False):
            gate_ch = 0 # 永远绑定电压参考相 Ua(ch0)
            # Frontend has computed the offset and put it in dcComponent.angle
            gate_phase = HWConfig.ConvertPhaseToReg(dc_comp.get("angle", 0.0))
            tau = dc_comp.get("tau", 0.04)
            
            node0_phys = node0.get("static", {})
            node1_phys = node1.get("static", {})
            
            # Since ApiHdlMgr doesn't exactly calculate the DC component for the specific formula Idc(t)
            # wait, the document says the Handler calculates Idc(t) steps.
            # We'll use ApiHdlMgr.CompileDcComp which uses prev_dc and curr_dc layer "0".
            # This implies frontend should inject the DC layer amplitude into node1_phys layer "0"
            # prior to this step, and CompileDcComp will decay it.
            mod_static, dyn_block = self.mgr.CompileDcComp(
                node0_phys, node1_phys, gate_ch, gate_phase, tau, dt=10.0
            )
            
            # Insert Node 1001 (DC_COMP)
            # Before going to Node 1, trigger goes to 1001 first.
            trig0["nextId"] = 1001
            
            # Create Node 1001
            node1001 = {
                "id": 1001,
                "mode": "STATIC", # With dynamic block
                "static": self._PhysToRegDict(mod_static),
                "dynamic": dyn_block,
                "do_actions": [],
                "triggers": [{"condition": "COUNT_OVER", "nextId": 1}]
            }
            self.engineToFrontId[1001] = 1 # Map back to 1
            engine_nodes.append(node1001)
            
            node1_static = mod_static # Update static for node 1

        node1_triggers = [
            {"condition": "DI_MATCH", "diMask": di_trip_mask, "nextId": 10},
            {"condition": "TIME_OUT", "timeout": test_limit_ms, "nextId": -2}
        ]
        
        node1_do_actions = []
        if do_mode == 1:
            delay_rev = bo_settings.get("delayReversal", {})
            d1 = delay_rev.get("delay", 100)
            h1 = delay_rev.get("hold", 100)
            node1_do_actions = [{"delay": d1, "mask": 0}, {"delay": d1+h1, "mask": bo_settings.get("initialMask", 3)}]
        elif do_mode == 2:
            node1_do_actions = bo_settings.get("doSequence", [])
            
        # Conversion fault A
        if is_transition and trans_moment == 0:
            node1_triggers.append({"condition": "TIME_OUT", "timeout": trans_time_ms, "nextId": 5})

        add_node(1, 1, node1_static, node1_triggers, node1_do_actions)

        # 3. Node 10 (Trip Delay)
        # Use node 1 static
        add_node(1, 10, node1.get("static", {}), [{"condition": "TIME_OUT", "timeout": 20, "nextId": 2}])

        # 4. Node 2 (Trip State)
        trip_static = self._GetTripStatic(node1.get("static", {}), False, ux_after_trip)
        node2_triggers = [
            {"condition": "DI_MATCH", "diMask": di_reclose_mask, "nextId": 20},
            {"condition": "TIME_OUT", "timeout": test_limit_ms, "nextId": -2}
        ]
        add_node(2, 2, trip_static, node2_triggers, do_trip_action if do_mode == 0 else [])

        # 5. Node 20 (Reclose Delay)
        add_node(2, 20, trip_static, [{"condition": "TIME_OUT", "timeout": 20, "nextId": 3}])

        # 6. Node 3 (Reclose State)
        if fault_nature == 0: # Transient
            reclose_static = node0.get("static", {})
        else: # Permanent
            reclose_static = node1.get("static", {})
            
        if is_transition and trans_moment == 1:
            reclose_static = node1.get("static", {})
            
        node3_triggers = [
            {"condition": "DI_MATCH", "diMask": di_trip_mask, "nextId": 30},
            {"condition": "TIME_OUT", "timeout": test_limit_ms, "nextId": -2}
        ]
        if is_transition and trans_moment == 1:
            node3_triggers.append({"condition": "TIME_OUT", "timeout": trans_time_ms, "nextId": 5})

        add_node(3, 3, reclose_static, node3_triggers, do_reclose_action if do_mode == 0 else [])

        # 7. Node 30 (Second Trip Delay)
        add_node(3, 30, reclose_static, [{"condition": "TIME_OUT", "timeout": 20, "nextId": 4}])

        # 8. Node 4 (Second Trip State)
        add_node(4, 4, trip_static, [{"condition": "TIME_OUT", "timeout": test_limit_ms, "nextId": -2}], do_trip_action if do_mode == 0 else [])

        # 9. Node 5 (Conversion Fault)
        if is_transition:
            trans_static = node2.get("static", {})
            node5_triggers = [
                {"condition": "DI_MATCH", "diMask": di_trip_mask, "nextId": 50},
                {"condition": "TIME_OUT", "timeout": test_limit_ms, "nextId": -2}
            ]
            add_node(5, 5, trans_static, node5_triggers)
            add_node(5, 50, trans_static, [{"condition": "TIME_OUT", "timeout": 20, "nextId": 2}])

        return {
            "sys": {
                "vChannels": ApiHdlMgr.API_V_CHS,
                "iChannels": ApiHdlMgr.API_I_CHS,
                "debounce": debounce
            },
            "nodes": engine_nodes
        }

    # ═══════════════════════════════════════════════════
    #  DI SOE 检测
    # ═══════════════════════════════════════════════════

    def _ProcessDiChange(self, di: int, hw_ts: int):
        """检测 DI 变化, 记录当前节点的首次动作时间"""
        if self.currentNodeId < 0:
            return
        nid = self.currentNodeId
        prev_di = self.lastKnownDi
        soe = self.diSoeByNode.get(nid)
        entry_ts = self.nodeEntryHwTs.get(nid)
        if soe is None or entry_ts is None:
            return

        for i, name in enumerate(DI_NAMES):
            prev_bit = (prev_di >> i) & 1
            curr_bit = (di >> i) & 1
            if prev_bit != curr_bit and soe[name] is None:
                elapsed_us = (hw_ts - entry_ts) & 0xFFFFFFFF
                soe[name] = round(elapsed_us / 1_000_000.0, 6)

    # ═══════════════════════════════════════════════════
    #  遥测拦截 (同 StatusSeqHdl 模式)
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        if not self.isActive:
            return [data]

        t_type = data.get("type", "")
        outMsgs = []

        if t_type == "di_change":
            hw_ts = data.get("hw_ts", 0)
            di = data.get("di", self.lastKnownDi)
            self._ProcessDiChange(di, hw_ts)
            self.lastKnownDi = di
            outMsgs.append({"module": "whole_group_test", "type": "value_update", "di": di})
            return outMsgs

        if t_type == "value_update":
            tick = data.get("tick", None)
            nid = data.get("id", 0)
            hw_ts = data.get("hw_ts", 0)
            new_di = data.get("di", self.lastKnownDi)
            new_do = data.get("do", self.lastKnownDo)

            if tick is not None:
                self.lastValueUpdateHwTs = hw_ts
                self.lastValueUpdateMono = time.monotonic()

                if tick == 0:
                    # 进入新节点
                    self.currentNodeId = nid
                    self.nodeEntryHwTs[nid] = hw_ts
                    self.nodeEntryMono[nid] = time.monotonic()
                    self.diSoeByNode[nid] = {n: None for n in DI_NAMES}
                    self.enteredNodes.append(nid)

                self.lastKnownDi = new_di

                di_do_msg = {}
                if new_do != self.lastKnownDo:
                    self.lastKnownDo = new_do
                    di_do_msg["do"] = new_do
                if di_do_msg:
                    di_do_msg["module"] = "whole_group_test"
                    di_do_msg["type"] = "value_update"
                    outMsgs.append(di_do_msg)

                front_id = self.engineToFrontId.get(nid, nid)
                outMsgs.append({
                    "module": "whole_group_test",
                    "type": "value_update",
                    "nodeId": front_id
                })
            return outMsgs

        elif t_type == "trigger":
            cond = data.get("condition", "")
            next_internal = data.get("next_id")
            if next_internal is not None and next_internal >= 0:
                front_next = self.engineToFrontId.get(next_internal, next_internal)
            else:
                front_next = next_internal
            outMsgs.append({
                "module": "whole_group_test",
                "type": "trigger",
                "condition": cond,
                "next_id": front_next
            })
            return outMsgs

        elif t_type == "terminal":
            self.isActive = False
            report = self._BuildReport()
            outMsgs.append(report)
            outMsgs.append({"module": "whole_group_test", "type": "stop"})
            return outMsgs

        return [data]

    # ═══════════════════════════════════════════════════
    #  报告生成
    # ═══════════════════════════════════════════════════

    def _BuildReport(self) -> dict:
        """从 DI SOE 数据构建整组试验报告"""
        # 从故障态节点 (front_id=1) 提取跳闸时间
        # 从跳闸态节点 (front_id=2) 提取重合闸时间
        tripA = tripB = tripC = recloseR = None

        for nid in self.enteredNodes:
            front_id = self.engineToFrontId.get(nid, nid)
            soe = self.diSoeByNode.get(nid, {})

            if front_id == 1:
                # 故障态: 提取跳闸时间 (秒→毫秒)
                if soe.get("diA") is not None:
                    tripA = round(soe["diA"] * 1000.0, 1)
                if soe.get("diB") is not None:
                    tripB = round(soe["diB"] * 1000.0, 1)
                if soe.get("diC") is not None:
                    tripC = round(soe["diC"] * 1000.0, 1)
            elif front_id == 2:
                # 跳闸态: 提取重合闸时间
                if soe.get("diR") is not None:
                    recloseR = round(soe["diR"] * 1000.0, 1)

        agg_row = {
            "tripA": tripA,
            "tripB": tripB,
            "tripC": tripC,
            "recloseR": recloseR,
            "diA": 1 if tripA is not None else 0,
            "diB": 1 if tripB is not None else 0,
            "diC": 1 if tripC is not None else 0,
            "diR": 1 if recloseR is not None else 0,
            "dia": 0, "dib": 0, "dic": 0, "dir": 0
        }

        return {
            "module": "whole_group_test",
            "type": "report",
            "data": {"rows": [agg_row]}
        }
