import logging
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig
from api.ApiHdlMgr import ApiHdlMgr

logger = logging.getLogger("TMTestHdl")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 宏定义: DI 断开时的电平 (True = 断开读到高电平 1, False = 断开读到低电平 0)
# 切换此值即可适配不同硬件接线逻辑
DI_OPEN_IS_HIGH = True
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 开入端口名称到 bit 位索引的映射 (硬件物理接线定义)
DI_PORT_MAP = {
    "A": 0, "B": 1, "C": 2, "R": 3,
    "a": 4, "b": 5, "c": 6, "r": 7
}

# 前端节点 ID 偏移量 (前端 1,2,3 → 内部 0,1,2)
_NODE_ID_OFFSET = 1


def _ParseDiCondition(mode_str: str):
    """
    解析 DI 启停条件字符串，返回 (bit_index, is_close_edge)。
    例: "DI_A_CLOSE_START" → (0, True)
        "DI_B_OPEN"        → (1, False)
    返回 None 表示不是 DI 类条件。
    """
    if not mode_str.startswith("DI_"):
        return None
    parts = mode_str.split("_")  # ["DI", "A", "CLOSE", "START"] or ["DI", "A", "CLOSE"]
    if len(parts) < 3:
        return None
    port_name = parts[1]
    action = parts[2]  # "CLOSE" or "OPEN"
    bit_idx = DI_PORT_MAP.get(port_name)
    if bit_idx is None:
        return None
    is_close = (action == "CLOSE")
    return (bit_idx, is_close)


def _CheckDiEdge(bit_idx: int, is_close_edge: bool, prev_di: int, curr_di: int) -> bool:
    """
    检测指定 bit 是否发生了目标边沿跳变。
    is_close_edge=True: 检测闭合边沿 (端子从断开→闭合)
    is_close_edge=False: 检测断开边沿 (端子从闭合→断开)
    """
    prev_bit = (prev_di >> bit_idx) & 1
    curr_bit = (curr_di >> bit_idx) & 1
    if prev_bit == curr_bit:
        return False  # 无变化

    if DI_OPEN_IS_HIGH:
        # 断开=1, 闭合=0
        if is_close_edge:
            return prev_bit == 1 and curr_bit == 0  # 1→0 = 闭合
        else:
            return prev_bit == 0 and curr_bit == 1  # 0→1 = 断开
    else:
        # 断开=0, 闭合=1
        if is_close_edge:
            return prev_bit == 0 and curr_bit == 1  # 0→1 = 闭合
        else:
            return prev_bit == 1 and curr_bit == 0  # 1→0 = 断开


class TimerState:
    """单个计时器的运行时状态"""
    def __init__(self, config: dict):
        self.start_mode: str = config.get("start_mode", "")
        self.stop_mode: str = config.get("stop_mode", "")
        self.state: str = "IDLE"          # IDLE / RUNNING / STOPPED
        self.start_hw_ts: Optional[int] = None
        self.stop_hw_ts: Optional[int] = None
        self.elapsed_ms: Optional[float] = None

        # 预解析 DI 条件，避免运行时重复解析
        self.start_di_cond = _ParseDiCondition(self.start_mode)
        self.stop_di_cond = _ParseDiCondition(self.stop_mode)

    def Reset(self):
        self.state = "IDLE"
        self.start_hw_ts = None
        self.stop_hw_ts = None
        self.elapsed_ms = None


class TMTestHdl:
    MODULE_KEYS = ["time_measurement"]
    """
    Time Measurement Handler
    管理 3 个 FSM 状态节点和 3 个独立计时器。
    计时器使用 FPGA 硬件 SOE 时间戳实现精确计时。
    """

    def __init__(self):

        self.isActive = False
        self.currentNode = -1         # 当前内部节点 ID (0, 1, 2)
        self.timers: List[TimerState] = []
        self.state2EndCondition = ""  # 状态 2 的计时器结束条件
        self.lastDiShadow = 0
        self.lastKnownDo = -1
        self.triggerFiredForState2 = False  # 防止重复触发

    def ResetState(self):
        self.isActive = False
        self.currentNode = -1
        self.timers = []
        self.state2EndCondition = ""
        self.lastDiShadow = 0
        self.lastKnownDo = -1
        self.triggerFiredForState2 = False
        self.manualTrigNextId = -2

    def _PhysToRegDict(self, phys_dict: dict) -> dict:
        """Delegates to self.mgr.PhysToReg"""
        return self.mgr.PhysToReg(phys_dict)

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """统一入口：编译前端 start 配置为引擎配置"""
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将前端时间测量配置编译为 USEEngine 节点配置"""
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 61)
        timer_cfgs = params.get("timers") or {}
        front_nodes = params.get("nodes") or []
        statics_map = params.get("statics") or {}
        limits_cfg = params.get("limits") or {}

        # 初始化 3 个计时器
        self.timers = [
            TimerState(timer_cfgs.get("timer1", {})),
            TimerState(timer_cfgs.get("timer2", {})),
            TimerState(timer_cfgs.get("timer3", {}))
        ]

        # 编译 limits
        properLimits = {}
        for ch_str, val in limits_cfg.items():
            ch = int(ch_str)
            if isinstance(val, list) and len(val) >= 2:
                amp_lim, freq_lim = float(val[0]), float(val[1])
            else:
                amp_lim, freq_lim = float(val), 60.0
            properLimits[ch_str] = [
                HWConfig.ConvertAmpToReg(amp_lim, ch in ApiHdlMgr.API_I_CHS),
                HWConfig.ConvertFreqToReg(freq_lim)
            ]

        sysOut = {
            "vChannels": ApiHdlMgr.API_V_CHS,
            "iChannels": ApiHdlMgr.API_I_CHS,
            "debounce": debounce,
            "limit": properLimits
        }

        # 编译节点: 前端 id 1,2,3 → 内部 id 0,1,2
        engine_nodes = []
        for fn in front_nodes:
            front_id = fn.get("id", 1)
            internal_id = front_id - _NODE_ID_OFFSET
            front_next = fn.get("next_id", -2)
            internal_next = (front_next - _NODE_ID_OFFSET) if front_next > 0 else front_next

            # 取该节点对应的 statics (按前端 ID 索引)
            node_statics_phys = statics_map.get(str(front_id), {})
            node_statics_reg = self._PhysToRegDict(node_statics_phys) if node_statics_phys else {}

            # 强制补全未配置通道为 0V/50Hz
            freq_50_reg = HWConfig.ConvertFreqToReg(50.0)
            ApiHdlMgr.FillMissingChannels(node_statics_reg, freq_50_reg)

            # 编译 triggers
            engine_triggers = []
            for trig in fn.get("triggers", []):
                cond = trig.get("condition", "")

                # 检查是否是 TIMER 类触发条件 (只在状态 2 使用)
                if cond.startswith("TIMER_") or cond == "TIME_CTRL":
                    self.state2EndCondition = cond
                    self.manualTrigNextId = internal_next
                else:
                    engine_trig = {"condition": cond}
                    if cond == "TIME_OUT" and "timeout" in trig:
                        engine_trig["timeout"] = trig["timeout"]
                    if cond == "DI_MATCH" and "diMask" in trig:
                        engine_trig["diMask"] = trig["diMask"]
                    engine_trig["nextId"] = internal_next
                    engine_triggers.append(engine_trig)

            node = {
                "id": internal_id,
                "doActions": fn.get("do_actions", []),
                "static": node_statics_reg,
                "triggers": engine_triggers
            }
            engine_nodes.append(node)

        return {
            "sys": sysOut,
            "nodes": engine_nodes
        }

    def _TryStartTimer(self, timer_idx: int, hw_ts: int):
        """尝试启动一个计时器"""
        t = self.timers[timer_idx]
        if t.state == "IDLE":
            t.state = "RUNNING"
            t.start_hw_ts = hw_ts
            logger.info(f"Timer {timer_idx + 1} STARTED (hw_ts={hw_ts})")

    def _TryStopTimer(self, timer_idx: int, hw_ts: int):
        """尝试停止一个计时器，并检查级联启动"""
        t = self.timers[timer_idx]
        if t.state == "RUNNING":
            t.state = "STOPPED"
            t.stop_hw_ts = hw_ts
            t.elapsed_ms = ((hw_ts - t.start_hw_ts) & 0xFFFFFFFF) / 1000.0
            logger.info(f"Timer {timer_idx + 1} STOPPED (elapsed={t.elapsed_ms:.3f}ms)")

            # 级联启动检查
            stopped_key = f"TIMER{timer_idx + 1}_STOP_START"
            for cascade_idx, ct in enumerate(self.timers):
                if cascade_idx != timer_idx and ct.state == "IDLE":
                    if ct.start_mode == stopped_key:
                        self._TryStartTimer(cascade_idx, hw_ts)

    def _CheckState2EndCondition(self) -> bool:
        """检查状态 2 的计时器结束条件是否满足"""
        cond = self.state2EndCondition
        if not cond or cond == "TIME_CTRL":
            return False

        t1 = self.timers[0].state == "STOPPED" if len(self.timers) > 0 else True
        t2 = self.timers[1].state == "STOPPED" if len(self.timers) > 1 else True
        t3 = self.timers[2].state == "STOPPED" if len(self.timers) > 2 else True

        checks = {
            "TIMER_1": t1,
            "TIMER_2": t2,
            "TIMER_3": t3,
            "TIMER_1_2": t1 and t2,
            "TIMER_2_3": t2 and t3,
            "TIMER_3_1": t3 and t1,
            "TIMER_1_2_3": t1 and t2 and t3,
        }
        return checks.get(cond, False)

    def _ProcessDiSoe(self, di: int, hw_ts: int) -> list:
        """处理 DI 变化事件，检查计时器启停条件"""
        outMsgs = []
        prev_di = self.lastDiShadow

        for idx, timer in enumerate(self.timers):
            # 检查启时条件 (DI 类)
            if timer.state == "IDLE" and timer.start_di_cond:
                bit_idx, is_close = timer.start_di_cond
                if _CheckDiEdge(bit_idx, is_close, prev_di, di):
                    self._TryStartTimer(idx, hw_ts)

            # 检查停时条件
            if timer.state == "RUNNING" and timer.stop_di_cond:
                bit_idx, is_close = timer.stop_di_cond
                if _CheckDiEdge(bit_idx, is_close, prev_di, di):
                    self._TryStopTimer(idx, hw_ts)

        self.lastDiShadow = di

        # 检查状态 2 结束条件
        if self.currentNode == 1 and not self.triggerFiredForState2:
            if self._CheckState2EndCondition():
                self.triggerFiredForState2 = True
                logger.info(f"State 2 end condition [{self.state2EndCondition}] met! Firing manual_trig.")
                outMsgs.append({
                    "internal_manual_trig": True,
                    "manual_trig_id": self.manualTrigNextId
                })

        return outMsgs

    def InterceptWsEvent(self, data: dict) -> list:
        """
        拦截并转译 USEEngine 的原始遥测事件。
        返回需要发送给前端的消息列表。
        """
        t_type = data.get("type", "")
        outMsgs = []

        if not self.isActive:
            return [data]

        if t_type == "di_change":
            # DI 变位事件 - 来自 CmdRouter 的 EvtDiChange
            hw_ts = data.get("hw_ts", 0)
            di = data.get("di", 0)
            timer_results = self._ProcessDiSoe(di, hw_ts)
            outMsgs.extend(timer_results)
            outMsgs.append({
                "module": "time_measurement",
                "type": "value_update",
                "di": di
            })
            return outMsgs

        if t_type == "value_update":
            # 区分两种 value_update 来源:
            # A. DI SOE 事件 (仅有 di + hw_ts，无 tick): 来自旧格式兼容
            # B. 引擎遥测 (有 tick, id 等完整字段): 来自 USEEngine.OnValueUpdate
            if "tick" not in data and "di" in data:
                # DI SOE 事件 - 执行计时器逻辑
                hw_ts = data.get("hw_ts", 0)
                di = data["di"]
                timer_results = self._ProcessDiSoe(di, hw_ts)
                outMsgs.extend(timer_results)

                # 转发 DI 变化给前端
                outMsgs.append({
                    "module": "time_measurement",
                    "type": "value_update",
                    "di": di
                })
                return outMsgs
            else:
                # 引擎遥测事件
                node_id = data.get("id", 0)
                front_id = node_id + _NODE_ID_OFFSET
                new_do = data.get("do", -1)

                do_msg = {}
                if new_do != self.lastKnownDo:
                    self.lastKnownDo = new_do
                    do_msg["do"] = new_do

                if do_msg:
                    do_msg["module"] = "time_measurement"
                    do_msg["type"] = "value_update"
                    outMsgs.append(do_msg)

                outMsgs.append({
                    "module": "time_measurement",
                    "type": "value_update",
                    "nodeId": front_id
                })
                return outMsgs

        elif t_type == "trigger":
            next_internal = data.get("next_id")
            cond = data.get("condition", "")
            hw_ts = data.get("hw_ts", 0)

            # 记录当前节点
            if next_internal is not None and next_internal >= 0:
                self.currentNode = next_internal

            # 如果跳转到内部节点 1 (状态 2)，检查 ENTER_STATE_2 启时
            if next_internal == 1:
                for idx, timer in enumerate(self.timers):
                    if timer.state == "IDLE" and timer.start_mode == "ENTER_STATE_2":
                        self._TryStartTimer(idx, hw_ts)

            # 转发给前端 (ID 映射回前端)
            front_next = (next_internal + _NODE_ID_OFFSET) if (next_internal is not None and next_internal >= 0) else next_internal
            outMsgs.append({
                "module": "time_measurement",
                "type": "trigger",
                "condition": cond,
                "next_id": front_next
            })
            return outMsgs

        elif t_type == "terminal":
            self.isActive = False

            # 构建最终测量报告
            report_data = {}
            for idx, timer in enumerate(self.timers):
                key = f"timer{idx + 1}_elapsed_ms"
                if timer.state == "STOPPED" and timer.elapsed_ms is not None:
                    report_data[key] = round(timer.elapsed_ms, 1)
                else:
                    report_data[key] = None

            outMsgs.append({
                "module": "time_measurement",
                "type": "report",
                "data": report_data
            })
            outMsgs.append({
                "module": "time_measurement",
                "type": "stop"
            })
            return outMsgs

        # 其他事件类型直接透传
        return [data]
