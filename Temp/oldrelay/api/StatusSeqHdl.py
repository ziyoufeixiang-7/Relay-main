import logging
import time
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("StatusSeqHdl")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 宏定义: DI 断开时的电平 (True = 断开读到高电平 1)
DI_OPEN_IS_HIGH = True
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 前端节点 ID 偏移量 (前端 1,2,3... → 内部 0,1,2...)
_NODE_ID_OFFSET = 1

# 延时桩节点 ID 偏移量
_STUB_NODE_OFFSET = 1000

# 开入端口名称映射 (bit 索引 → 报告字段名)
DI_NAMES = ["diA", "diB", "diC", "diR", "dia", "dib", "dic", "dir"]


class NodeTracker:
    """单个前端状态节点的运行时跟踪数据"""

    def __init__(self, front_id: int):
        self.front_id = front_id
        self.entry_hw_ts: Optional[int] = None     # 进入该状态后首次 value_update(tick=0) 的 hw_ts
        self.entry_time_mono: float = 0.0           # Python monotonic 时间戳 (fallback)
        self.entry_di: int = 0                      # 进入状态时的 DI 快照
        self.di_times: Dict[str, Optional[float]] = {n: None for n in DI_NAMES}
        self.end_condition: str = ""
        self.entered: bool = False                   # 是否已收到 tick=0

    def ToReportRow(self, duration_sec: float) -> dict:
        row = {
            "id": self.front_id,
            "duration_sec": round(duration_sec, 6),
            "end_condition": self.end_condition
        }
        row.update(self.di_times)
        return row


class StatusSeqHdl:
    MODULE_KEYS = ["universal_sequence"]
    """
    状态序列 Handler
    将前端 N 个状态节点编译为 USEEngine 节点配置。
    支持 STATIC 和 SWEEP 模式，处理 post_condition_delay_ms 延时拆分。
    """

    def __init__(self):

        self.isActive = False
        self.trackers: List[NodeTracker] = []
        self.currentTracker: Optional[NodeTracker] = None
        self.lastKnownDi = 0
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono: float = 0.0

        # 编译时保存: 前端节点物理值 (用于遥测反算)
        self.nodePhysData: Dict[int, dict] = {}  # front_id → {"statics": {}, "steps": {}}

        # 编译时保存: 引擎内部 ID → 前端节点 ID 映射
        self.engineToFrontId: Dict[int, int] = {}
        self.stubNodeIds: set = set()
        self.stubOrigCondition: Dict[int, str] = {}  # 桩引擎ID → 原始 condition
        self.manualTrigTargets: Dict[int, int] = {}  # 引擎ID → 手动触发目标ID

    def ResetState(self):
        self.isActive = False
        self.trackers = []
        self.currentTracker = None
        self.lastKnownDi = 0
        self.lastKnownDo = -1
        self.lastValueUpdateHwTs = 0
        self.lastValueUpdateMono = 0.0
        self.nodePhysData = {}
        self.engineToFrontId = {}
        self.stubNodeIds = set()
        self.stubOrigCondition = {}
        self.manualTrigTargets = {}

    # ═══════════════════════════════════════════════════
    #  物理值 → 寄存器值 转换
    # ═══════════════════════════════════════════════════

    def _PhysToRegDict(self, phys_dict: dict, factor: float = 1.0, is_delta: bool = False) -> dict:
        """Delegates to self.mgr.PhysToReg"""
        return self.mgr.PhysToReg(phys_dict, factor, is_delta)

    # ═══════════════════════════════════════════════════
    #  编译入口
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将前端状态序列配置编译为 USEEngine 节点配置"""
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 61)
        front_nodes = params.get("nodes") or []

        engine_nodes = []
        freq_50_reg = HWConfig.ConvertFreqToReg(50.0)

        # 动态映射前端 ID 到内部连续 ID (首个节点必须为 0)
        self.frontToEngineId = {}
        for i, fn in enumerate(front_nodes):
            front_id = fn.get("id", i + 1)
            self.frontToEngineId[front_id] = i

        for fn in front_nodes:
            front_id = fn.get("id", 1)
            internal_id = self.frontToEngineId.get(front_id, 0)
            front_next = fn.get("next_id", -2)
            if front_next is None:
                front_next = -2
            internal_next = self.frontToEngineId.get(front_next, front_next)

            # 记录映射
            self.engineToFrontId[internal_id] = front_id

            # 创建跟踪器
            tracker = NodeTracker(front_id)
            self.trackers.append(tracker)

            # 编译 static
            node_statics_phys = fn.get("static") or {}
            node_steps_phys = {}
            node_statics_reg = self._PhysToRegDict(node_statics_phys) if node_statics_phys else {}

            # 保存物理值用于遥测反算
            self.nodePhysData[front_id] = {
                "statics": node_statics_phys,
                "steps": {}
            }

            # 强制补全未配置通道为 0V/50Hz
            ApiHdlMgr.FillMissingChannels(node_statics_reg, freq_50_reg)

            # 编译 triggers
            engine_triggers = []
            trigger_cond_for_delay = ""  # 用于记录桩节点的原始条件

            for trig in fn.get("triggers", []):
                cond = trig.get("condition", "")
                post_delay = trig.get("post_condition_delay_ms", 0)
                trig_timeout = trig.get("timeout", 0)
                di_mask = trig.get("di_mask", 0)
                trig_next = trig.get("next_id", -2)
                if trig_next is None:
                    trig_next = -2
                trig_internal_next = self.frontToEngineId.get(trig_next, trig_next)

                # 判断是否需要桩节点
                actual_next = trig_internal_next
                if post_delay > 0:
                    stub_id = _STUB_NODE_OFFSET + internal_id
                    actual_next = stub_id
                    trigger_cond_for_delay = cond

                if cond == "DI_MATCH":
                    engine_triggers.append({
                        "condition": "DI_MATCH",
                        "diMask": di_mask,
                        "nextId": actual_next
                    })
                    # 安全超时: 如果 timeout > 0，添加第二个 TIME_OUT
                    if trig_timeout > 0:
                        engine_triggers.append({
                            "condition": "TIME_OUT",
                            "timeout": trig_timeout,
                            "nextId": actual_next
                        })
                elif cond == "TIME_OUT":
                    engine_triggers.append({
                        "condition": "TIME_OUT",
                        "timeout": trig_timeout,
                        "nextId": actual_next
                    })
                elif cond == "MANUAL_TRIG":
                    self.manualTrigTargets[internal_id] = actual_next
                elif cond == "COUNT_OVER":
                    engine_triggers.append({
                        "condition": "COUNT_OVER",
                        "nextId": actual_next
                    })

            # 构建主节点
            node = {
                "id": internal_id,
                "doActions": fn.get("do_actions", []),
                "static": node_statics_reg,
                "triggers": engine_triggers
            }

            # 编译 dynamic (SWEEP 模式)
            mode = fn.get("mode", "STATIC")
            dynamic_cfg = fn.get("dynamic") or {}
            if mode == "SWEEP" and dynamic_cfg:
                node_steps_phys = dynamic_cfg.get("steps") or {}
                step_time = dynamic_cfg.get("stepTime", 100)
                count = dynamic_cfg.get("count", 1)

                reg_step = self._PhysToRegDict(node_steps_phys, 1.0, is_delta=True)

                # Anti-Tear: 确保步进层在 static 中存在且幅值不为 0
                for ch_str, layers in reg_step.items():
                    if ch_str not in node_statics_reg:
                        node_statics_reg[ch_str] = {}
                    for l_str, step_vals in layers.items():
                        if step_vals[0] != 0:
                            if l_str not in node_statics_reg[ch_str]:
                                def_p = freq_50_reg if l_str == "0" else 0
                                node_statics_reg[ch_str][l_str] = [0, def_p]
                            if node_statics_reg[ch_str][l_str][0] == 0:
                                node_statics_reg[ch_str][l_str][0] = 1 if step_vals[0] > 0 else -1

                node["dynamic"] = {
                    "interval": step_time,
                    "count": count,
                    "step": reg_step
                }

                # 保存步进物理值
                self.nodePhysData[front_id]["steps"] = node_steps_phys

            engine_nodes.append(node)

            # 生成延时桩节点
            for trig in fn.get("triggers", []):
                post_delay = trig.get("post_condition_delay_ms", 0)
                if post_delay > 0:
                    trig_next = trig.get("next_id", -2)
                    if trig_next is None:
                        trig_next = -2
                    trig_internal_next = self.frontToEngineId.get(trig_next, trig_next)
                    stub_id = _STUB_NODE_OFFSET + internal_id

                    if stub_id not in self.stubNodeIds:
                        self.stubNodeIds.add(stub_id)
                        self.stubOrigCondition[stub_id] = trigger_cond_for_delay
                        self.engineToFrontId[stub_id] = front_id  # 桩节点映射到同一前端 ID

                        stub_node = {
                            "id": stub_id,
                            "doActions": [],
                            "static": {},
                            "triggers": [{
                                "condition": "TIME_OUT",
                                "timeout": post_delay,
                                "nextId": trig_internal_next
                            }]
                        }
                        engine_nodes.append(stub_node)

        return {
            "sys": {
                "vChannels": ApiHdlMgr.API_V_CHS,
                "iChannels": ApiHdlMgr.API_I_CHS,
                "debounce": debounce
            },
            "nodes": engine_nodes
        }

    # ═══════════════════════════════════════════════════
    #  物理值反算 (用于遥测推送)
    # ═══════════════════════════════════════════════════

    def _ComputeCurrentPhysics(self, front_id: int, tick: int) -> dict:
        """根据前端节点 ID 和 tick 反算当前物理值"""
        phys = self.nodePhysData.get(front_id)
        if not phys:
            return {}

        base = phys["statics"]
        steps = phys["steps"]
        if not base:
            return {}

        result = {}
        for ch_str, layers in base.items():
            result[ch_str] = {}
            for l_str, vals in layers.items():
                base_amp = vals[0]
                base_freq = vals[1]
                step_amp = steps.get(ch_str, {}).get(l_str, [0, 0])[0] if tick > 0 else 0
                step_freq = steps.get(ch_str, {}).get(l_str, [0, 0])[1] if tick > 0 else 0
                result[ch_str][l_str] = [
                    round(base_amp + step_amp * tick, 6),
                    round(base_freq + step_freq * tick, 6)
                ]
        return result

    # ═══════════════════════════════════════════════════
    #  DI 边沿检测
    # ═══════════════════════════════════════════════════

    def _ProcessDiChange(self, di: int, hw_ts: int):
        """检测 DI 变化，记录首次动作时间到当前 tracker"""
        if self.currentTracker is None or not self.currentTracker.entered:
            return

        tracker = self.currentTracker
        prev_di = self.lastKnownDi

        for i, name in enumerate(DI_NAMES):
            prev_bit = (prev_di >> i) & 1
            curr_bit = (di >> i) & 1
            if prev_bit != curr_bit and tracker.di_times[name] is None:
                # 首次变化，计算相对进入状态的时间
                elapsed_us = (hw_ts - tracker.entry_hw_ts) & 0xFFFFFFFF
                tracker.di_times[name] = round(elapsed_us / 1_000_000.0, 6)

    # ═══════════════════════════════════════════════════
    #  遥测事件拦截
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        t_type = data.get("type", "")
        outMsgs = []

        if not self.isActive:
            return [data]

        if t_type == "di_change":
            # ── DI 变位事件: 来自 CmdRouter 的 EvtDiChange ──
            hw_ts = data.get("hw_ts", 0)
            di = data.get("di", self.lastKnownDi)

            self._ProcessDiChange(di, hw_ts)
            self.lastKnownDi = di

            outMsgs.append({
                "module": "universal_sequence",
                "type": "value_update",
                "di": di
            })
            return outMsgs

        if t_type == "value_update":
            if "tick" not in data and "di" in data:
                # ── 旧格式 DI 事件 (无 tick): 兼容保留 ──
                hw_ts = data.get("hw_ts", 0)
                di = data["di"]

                self._ProcessDiChange(di, hw_ts)
                self.lastKnownDi = di

                outMsgs.append({
                    "module": "universal_sequence",
                    "type": "value_update",
                    "di": di
                })
                return outMsgs
            else:
                # ── 引擎遥测 (有 tick) ──
                tick = data.get("tick", 0)
                nid = data.get("id", 0)
                new_di = data.get("di", self.lastKnownDi)
                new_do = data.get("do", self.lastKnownDo)
                hw_ts = data.get("hw_ts", 0)
                self.lastValueUpdateHwTs = hw_ts
                self.lastValueUpdateMono = time.monotonic()

                # 跳过桩节点的遥测
                if nid in self.stubNodeIds:
                    return outMsgs

                front_id = self.engineToFrontId.get(nid, nid + _NODE_ID_OFFSET)

                # tick=0 → 进入新状态的首次更新
                if tick == 0:
                    # tick=0 → 进入新状态的首次更新
                    for t in self.trackers:
                        if t.front_id == front_id and not t.entered:
                            self.currentTracker = t
                            t.entered = True
                            t.entry_hw_ts = hw_ts
                            t.entry_time_mono = time.monotonic()
                            t.entry_di = new_di
                            break

                # 更新 DI/DO
                self.lastKnownDi = new_di

                di_do_msg = {}
                if new_do != self.lastKnownDo:
                    self.lastKnownDo = new_do
                    di_do_msg["do"] = new_do

                if di_do_msg:
                    di_do_msg["module"] = "universal_sequence"
                    di_do_msg["type"] = "value_update"
                    outMsgs.append(di_do_msg)

                # 反算物理值
                phys_vals = self._ComputeCurrentPhysics(front_id, tick if tick >= 0 else 0)

                msg = {
                    "module": "universal_sequence",
                    "type": "value_update",
                    "nodeId": front_id
                }
                if phys_vals:
                    msg["static"] = phys_vals
                outMsgs.append(msg)
                return outMsgs

        elif t_type == "trigger":
            cond = data.get("condition", "")
            next_internal = data.get("next_id")
            hw_ts = data.get("hw_ts", 0)

            # 如果跳转到桩节点，记录原始条件但不转发给前端
            if next_internal is not None and next_internal in self.stubNodeIds:
                orig_cond = self.stubOrigCondition.get(next_internal, cond)
                if self.currentTracker:
                    self.currentTracker.end_condition = orig_cond
                return outMsgs  # 不转发

            # 真实节点跳转
            if self.currentTracker:
                if not self.currentTracker.end_condition:
                    self.currentTracker.end_condition = cond

            # 转换前端 ID
            if next_internal is not None and next_internal >= 0:
                front_next = self.engineToFrontId.get(next_internal, next_internal + _NODE_ID_OFFSET)
            else:
                front_next = next_internal

            outMsgs.append({
                "module": "universal_sequence",
                "type": "trigger",
                "condition": cond,
                "next_id": front_next
            })
            return outMsgs

        elif t_type == "terminal":
            self.isActive = False

            # 最后一个状态的退出条件
            if self.currentTracker:
                if not self.currentTracker.end_condition:
                    self.currentTracker.end_condition = "TERMINAL"

            # 计算 duration: 后一个节点 entry - 当前节点 entry
            entered_trackers = [t for t in self.trackers if t.entered]
            rows = []
            for i, t in enumerate(entered_trackers):
                if t.entry_hw_ts is None:
                    duration = 0.0
                elif i + 1 < len(entered_trackers) and entered_trackers[i + 1].entry_hw_ts is not None:
                    # 优先用 hw_ts，如果非零
                    hw_diff = ((entered_trackers[i + 1].entry_hw_ts - t.entry_hw_ts) & 0xFFFFFFFF)
                    if hw_diff > 0:
                        duration = hw_diff / 1_000_000.0
                    else:
                        # hw_ts 为 0 时回退到 Python 单调时间
                        duration = entered_trackers[i + 1].entry_time_mono - t.entry_time_mono
                else:
                    # 最后一个节点
                    hw_diff = ((self.lastValueUpdateHwTs - t.entry_hw_ts) & 0xFFFFFFFF)
                    if hw_diff > 0:
                        duration = hw_diff / 1_000_000.0
                    else:
                        duration = self.lastValueUpdateMono - t.entry_time_mono
                rows.append(t.ToReportRow(duration))

            outMsgs.append({
                "module": "universal_sequence",
                "type": "report",
                "data": {"rows": rows}
            })
            outMsgs.append({
                "module": "universal_sequence",
                "type": "stop"
            })
            return outMsgs

        return [data]
