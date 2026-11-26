import asyncio
import time
from typing import List, Dict, Optional, Tuple

from logic.FPGACodec import HWCodec, HWConfig
from logic.FlatCodec import FlatCodec, NodeKey, TrigCond, EngineEvt
from utils.SysLogger import GetLogger

logger = GetLogger("USEEngine")

class WaveMode:
    Static = 0
    Sweep  = 1
    Reset  = 2
    DcComp = 3

_I_CH_SET = frozenset(HWConfig.I_CHANNELS)  # 电流通道集合，用于 currentState 过滤

def _U64ListToHwDict(words: List[int]) -> Dict[int, Dict[int, list]]:
    """[U64 DDS word, ...] → {hw_ch: {layer: [amp_i32, phase_freq]}}"""
    result = {}
    for w in words:
        ch, layer, amp, pf = FlatCodec.UnpackDdsWord(w)
        if ch not in result:
            result[ch] = {}
        result[ch][layer] = [amp, pf]
    return result




# ═══════════════════════════════════════════════════
#  数据模型 — 基于 Flat U64 协议 (ADR-001)
# ═══════════════════════════════════════════════════

class SequenceNode:
    """直接从 Flat U64 节点构建"""
    def __init__(self, flat: dict):
        self.id: int = flat[NodeKey.Id]

        # DoAction: [U32, ...] → [(delay, mask), ...]
        self.doActions: List[Tuple[int, int]] = [
            FlatCodec.UnpackDoAction(p) for p in flat.get(NodeKey.DoAction, [])
        ]

        # Static: [U64, ...] → {hw_ch: {layer: [amp, freq]}}
        self.static: Dict[int, Dict[int, list]] = (
            _U64ListToHwDict(flat[NodeKey.Static]) if NodeKey.Static in flat else {}
        )

        # Dynamic
        self.interval: Optional[float] = flat.get(NodeKey.Interval)
        raw_steps = flat.get(NodeKey.Steps)
        self.steps: Optional[List[Dict[int, Dict[int, list]]]] = None
        self.count: int = 0
        if raw_steps:
            self.count = len(raw_steps)
            self.steps = [_U64ListToHwDict(s) for s in raw_steps]

        # PhaseGate: (channel, phase) or None
        self.phaseGate: Optional[tuple] = None
        if NodeKey.PhaseGate in flat:
            self.phaseGate = FlatCodec.UnpackPhaseGate(flat[NodeKey.PhaseGate])

        # Reset
        self.resetTime: Optional[float] = flat.get(NodeKey.ResetTime)
        self.resetStatic: Dict[int, Dict[int, list]] = (
            _U64ListToHwDict(flat[NodeKey.ResetStatic]) if NodeKey.ResetStatic in flat else {}
        )
        self.resetDo: Optional[int] = None
        self.overDo: Optional[int] = None
        if NodeKey.ResetDo in flat:
            self.overDo, self.resetDo = FlatCodec.UnpackResetDo(flat[NodeKey.ResetDo])

        # Triggers: [U64, ...] → [(cond, nextId, data), ...]
        self.triggers: List[tuple] = []
        for packed in flat.get(NodeKey.Triggers, []):
            cond, nid, union = FlatCodec.UnpackTrigger(packed)
            self.triggers.append((cond, nid, union))

        # Mode classification: interval 决定是否动态
        if self.interval is not None:
            if self.resetTime is not None:
                self.mode = WaveMode.Reset
            elif self.phaseGate:
                self.mode = WaveMode.DcComp
            else:
                self.mode = WaveMode.Sweep
        else:
            self.mode = WaveMode.Static

def _TimeoutFuture(fut: asyncio.Future):
    if not fut.done():
        fut.set_result(-2)


class _RunCtx:
    """Shared context for mode runner methods, replaces inner closures."""
    __slots__ = ('eng', 'node', 'f', 't0', 'entry_di', 'lastCond')
    def __init__(self, eng, node, f, entry_di):
        self.eng, self.node, self.f, self.t0, self.entry_di = eng, node, f, 0.0, entry_di
        self.lastCond = None

    def st(self, idx):
        a = self.f.get("St", [])
        return a[min(idx, len(a)-1)] if a else []

    def nst(self, idx):
        a = self.f.get("St", [])
        if not a: return []
        return [HWCodec.NegStepFrame(f) for f in a[min(idx, len(a)-1)]]

    def telemetry(self, tick, add_acc=True):
        if add_acc: self.eng._AccumulatorAdd(self.node, tick)
        self.eng.OnValueUpdate(
            self.node.id, tick, self.eng.doStatusShadow)

    async def exit(self, tgt, teardown):
        # 先取消所有后台 DO 任务，防止 teardown 后仍有 DO 帧泄漏
        for t in list(self.eng.bgTasks):
            t.cancel()
        self.eng.bgTasks.clear()
        if teardown and await self.eng.FlushFrames(teardown) < 0: return -2
        if tgt is None and self.node.mode != WaveMode.Static:
            for cond, nid, _ in self.node.triggers:
                if cond == TrigCond.ConCountOver:
                    self.lastCond = TrigCond.ConCountOver
                    tgt = nid
                    break
        if self.lastCond is not None:
            await self.eng.emitEvent([EngineEvt.EvtTrigger, self.lastCond, tgt, self.eng.lastUpHwTs])
            self.lastCond = None
        return tgt

    async def sleep(self, target_os):
        eng = self.eng
        while eng.isRunning:
            ms = (time.perf_counter() - self.t0) * 1000.0
            res = eng._EvaluateTriggers(self.node, ms, self.entry_di)
            if res is not None:
                cond, tgt = res
                self.lastCond = cond
                return tgt
            remain = target_os - time.perf_counter()
            if remain <= 0.005:
                await asyncio.sleep(0)
                while time.perf_counter() < target_os: pass
                break
            eng.diEvent.clear()
            try:
                await asyncio.wait_for(eng.diEvent.wait(), timeout=min(remain, 0.05))
            except asyncio.TimeoutError:
                pass
        return None

class USEEngine:
    def __init__(self, hwGateway, emitEvent):
        self.hwGateway = hwGateway
        self.emitEvent = emitEvent

        # ACK 机制
        self.hwLock = asyncio.Lock()
        self.ackFuture = None
        self.loop = asyncio.get_running_loop()

        self.isRunning = False
        self.runTask = None
        self._lock = asyncio.Lock()
        self._term_sent = False
        self._suppress_hw_teardown = False

        # 运行时状态 (每次 LoadConfig 重置)
        self.debounce: int = 61
        self.debounceUs: int = int(61 / HWConfig.DBNC * 1000)
        self.nodes = {}
        self.currentNodeId: Optional[int] = None
        self.manualTrigTarget: Optional[int] = None
        self.currentState: Dict[int, Dict[int, int]] = {}  # {ch: {layer: amp}} 仅电流通道，供 HWProtect 计算虚拟温度

        self.diEvent = asyncio.Event()
        self.diStatusShadow: int = 0
        self.doStatusShadow: int = 0
        self.initDiShadow: int = 0
        self.lastUpHwTs: int = 0
        self.frames_sent_count: int = 0
        self.ack_counter: int = 0

        self.bgTasks = set()


    # ═══════════════════════════════════════════════════
    #  第1区: 控制 API (外部调用入口)
    # ═══════════════════════════════════════════════════

    def LoadConfig(self, sysConfig: dict, flatNodes: list):
        """每次测试前加载新配置，编译节点"""
        self.debounce = sysConfig.get("debounce", 61)
        self.nodes = {n.id: n for n in [SequenceNode(fn) for fn in flatNodes]}

        self.currentState = {}
        self.frames_sent_count = 0
        self.ack_counter = 0
        self._term_sent = False
        self._suppress_hw_teardown = False
        self._aotCompileAllNodes()


    def UpsertNodesFlat(self, flat_nodes: list):
        """接受 Flat Node list, 热更新"""
        new_nodes = [SequenceNode(fn) for fn in flat_nodes]
        for n in new_nodes:
            self.nodes[n.id] = n
            self._compileNode(n)


    async def Start(self):
        async with self._lock:
            if self.isRunning:
                return
            self.isRunning = True
            
            logger.info("USE Engine Full Monolithic started.")
            
            await self.EmergencyReset()
            
            self.diEvent.clear()
            
            init_frms = [
                HWCodec.FRAME_SYS_RESET,
                HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, self.debounce)
            ]
            await self.FlushFrames(init_frms)
            await self._SendWithAck(HWCodec.FRAME_SYS_START)
            self.debounceUs = int(self.debounce / HWConfig.DBNC * 1000)
            
            try:
                await asyncio.wait_for(self.diEvent.wait(), timeout=0.15)
            except asyncio.TimeoutError:
                pass
            
            self.runTask = asyncio.create_task(self._StartCoreLoop())
            self.runTask.add_done_callback(self._OnCoreFinished)


    async def Stop(self):
        async with self._lock: 
            self.isRunning = False
            if self.runTask and not self.runTask.done():
                self.runTask.cancel()
            logger.warning("Emergency teardown hooked! Resetting HW...")
            await self.EmergencyReset()


    def ForceStopLocal(self):
        self.isRunning = False
        self.currentState = None
        # 释放静默旗帜，让 `_OnCoreFinished` 回调看到后主动绕开所有发包和通讯动作
        self._suppress_hw_teardown = True 
        if self.runTask and not self.runTask.done():
            self.runTask.cancel()


    def FireManualTrigger(self, forced_target_id: Optional[int] = None) -> None:
        self.manualTrigTarget = forced_target_id
        self.diEvent.set()


    # ═══════════════════════════════════════════════════
    #  第2区: AOT 预编译
    # ═══════════════════════════════════════════════════

    def _aotCompileAllNodes(self):
        for n_id, node in self.nodes.items():
            self._compileNode(node)


    def _compileNode(self, n: SequenceNode) -> None:
        def build_shadow(l_idx, mask, a, p):
            return HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, (l_idx - 1) & 0xFF, mask, a, p)
        def build_step_shadow(l_idx, mask, da, dp):
            return HWCodec.BuildParamFrame(HWCodec.DDS_STEP_SHADOW, (l_idx - 1) & 0xFF, mask, da, dp)
            
        
        def build_masks_from_dict(data_dict: dict) -> dict:
            masks = {}
            for hw_ch, layers in data_dict.items():
                ch_bit = 1 << hw_ch
                for l_idx, (a_reg, p_reg) in layers.items():
                    key = (l_idx, a_reg, p_reg)
                    masks[key] = masks.get(key, 0) | ch_bit
            return masks

        static_masks = build_masks_from_dict(n.static)
        reset_masks = build_masks_from_dict(n.resetStatic)
        step_masks = [build_masks_from_dict(s) for s in (n.steps or [])]

        Ss = [build_shadow(l_idx, mask, a, p) for (l_idx, a, p), mask in static_masks.items()]
        Rs = [build_shadow(l_idx, mask, a, p) for (l_idx, a, p), mask in reset_masks.items()]
        Up = [HWCodec.FRAME_SYS_UPDATE]
        S0_Ss = [build_step_shadow(l_idx, 0xFFFF, 0, 0) for l_idx in {k[0] for k in static_masks}]
        St = [[build_step_shadow(l, m, da, dp) for (l, da, dp), m in mi.items()] for mi in step_masks]
        S0_St = [build_step_shadow(l, 0xFFFF, 0, 0) for l in {k[0] for mi in step_masks for k in mi}]
        Ss_Dc = [build_shadow(l_idx, mask, a, p) for (l_idx, a, p), mask in static_masks.items() if l_idx == 0] if n.mode == WaveMode.DcComp else []

        compiled = {
            "Ss": Ss, "Rs": Rs, "Up": Up,
            "S0_Ss": S0_Ss, "S0_St": S0_St, "Ss_Dc": Ss_Dc,
            "Ss_Rs": Ss + Rs,
            "St": St
        }

        if n.phaseGate:
            hw_ch, base_phase = n.phaseGate
            f_reg = n.static[hw_ch][0][1]
            delta_P = f_reg * HWConfig.PHASE_PER_FREQ_MS * n.interval
            compiled["Pg"] = [HWCodec.BuildPhaseGateFrame(hw_ch, (base_phase + int(i * delta_P)) & 0xFFFFFFFF) for i in range(len(St) + 1)]

        n.frms = compiled


    # ═══════════════════════════════════════════════════
    #  第3区: 波形运行 (FSM + 4种模式)
    # ═══════════════════════════════════════════════════

    async def _StartCoreLoop(self) -> None:
        """FSM 主循环入口 — 从节点 0 开始，按触发链依次执行"""
        self.currentNodeId = 0              # 从第一个节点开始
        self.manualTrigTarget = None        # 清除手动触发残留
        self.currentState = {}              # 清空波形累加器状态
        self.initDiShadow = self.diStatusShadow  # 记录 DI 初始状态作为变位比较基准

        try:
            # FSM 主循环: 逐节点执行直到序列终止(None)或引擎停止
            while self.isRunning and self.currentNodeId is not None:
                if self.currentNodeId not in self.nodes:
                    self.currentNodeId = None  # 节点不存在 = FSM 终止
                    break
                node = self.nodes[self.currentNodeId]
                node_entry_di = self.diStatusShadow   # 本节点进入时的 DI 快照
                # 将节点静态参数写入波形累加器(用于前端遥测)
                if node.static:
                    for ch, layers in node.static.items():
                        if ch in _I_CH_SET:
                            self.currentState[ch] = {l_idx: a for l_idx, (a, p) in layers.items()}
                # 执行节点波形，返回下一个节点 ID(None=序列终止)
                self.currentNodeId = await self._RunNodeTicks(node, node_entry_di)

            # FSM 自然结束(currentNodeId=None): 通知 Handler 生成最终报告
            if self.currentNodeId is None and self.isRunning:
                await self.emitEvent({"type": "terminal"})
        except Exception as e:
            # 异常退出也需要通知 Handler 生成报告
            logger.error(f"Core loop exception: {e}")
            try:
                await self.emitEvent({"type": "terminal"})
            except Exception:
                pass
        finally:
            self.isRunning = False
            for t in self.bgTasks:
                t.cancel()
            self.bgTasks.clear()


    async def _RunNodeTicks(self, node: SequenceNode, node_entry_di: int) -> Optional[int]:
        """根据节点模式分发到对应的波形运行器"""
        f = node.frms                                  # 预编译帧集合
        ctx = _RunCtx(self, node, f, node_entry_di)    # 构建运行上下文
        return await {
            WaveMode.Static: self._RunStatic,
            WaveMode.Sweep:  self._RunSweep,
            WaveMode.Reset:  self._RunReset,
            WaveMode.DcComp: self._RunDcComp,
        }[node.mode](ctx)


    async def _InitCommon(self, c: '_RunCtx') -> bool:
        """Mode 1/2/3 共用初始化: 写入阴影寄存器 → Update 切换 → 记录时间"""
        # Reset 模式需要同时写入 Static+Reset 帧，其他模式只写 Static
        init = c.f["Ss_Rs"] if c.node.mode == WaveMode.Reset else c.f["Ss"]
        if await self.FlushFrames(init) < 0: return False
        # 发送 UPDATE 指令触发 Ping-Pong 缓冲区切换，使阴影值生效
        ts = await self._SendWithAck(HWCodec.FRAME_SYS_UPDATE)
        if ts < 0: return False
        self.lastUpHwTs = ts              # 记录硬件返回的 Update 时间戳
        self.frames_sent_count += 1
        c.t0 = time.perf_counter()        # 波形实际生效时间
        self._SpawnDoTasks(c.node, c.t0)  # 启动 DO 延时输出任务
        self.OnValueUpdate(c.node.id, 0, self.doStatusShadow)  # 推送初始值更新 (确保 lastUpHwTs 已是最新的)
        return True

    # ─── Mode 1: 静态保持 ───
    async def _RunStatic(self, c: '_RunCtx') -> Optional[int]:
        if not await self._InitCommon(c): return -2
        # 无限等待触发(DI变位/超时/手动)
        tgt = await c.sleep(float('inf'))
        # 退出时归零所有静态层的步进寄存器
        return await c.exit(tgt, c.f.get("S0_Ss", []))

    # ─── Mode 2: 连续扫频 ───
    async def _RunSweep(self, c: '_RunCtx') -> Optional[int]:
        if not await self._InitCommon(c): return -2
        iv = c.node.interval / 1000.0     # 步进间隔(ms→s)
        # 写入步进归零帧 + 第 0 步增量，准备首次 tick
        if await self.FlushFrames(c.f.get("S0_Ss", []) + c.st(0)) < 0: return -2
        # 等待第一个 interval 到期，期间轮询触发条件
        tgt = await c.sleep(c.t0 + iv)
        if tgt is not None: return await c.exit(tgt, c.nst(0))  # 提前触发，反向补偿步进
        # 逐 tick 执行: Update→遥测→写下一步→等待
        for tick in range(1, c.node.count + 1):
            if not self.isRunning: break
            # 发 Update 使上一步增量生效
            ts = await self._SendWithAck(HWCodec.FRAME_SYS_UPDATE)
            if ts < 0: return -2
            self.lastUpHwTs = ts
            self.frames_sent_count += 1
            c.telemetry(tick)             # 推送遥测(累加器 + 值更新)
            # 写入下一步增量到阴影
            if await self.FlushFrames(c.st(tick)) < 0: return -2
            # 等待下一个 interval
            tgt = await c.sleep(c.t0 + (tick + 1) * iv)
            if tgt is not None: return await c.exit(tgt, c.nst(tick))
        # 所有 tick 完成，反向最后一步并等待终止触发
        return await c.exit(None, c.nst(c.node.count))

    # ─── Mode 3: 复位扫频 ───
    async def _RunReset(self, c: '_RunCtx') -> Optional[int]:
        if not await self._InitCommon(c): return -2
        rst = c.node
        iv = c.node.interval / 1000.0     # 输出相位时长(ms→s)
        rs = (rst.resetTime or 0) / 1000.0  # 复位相位时长(ms→s)
        cy = rs + iv                      # 完整周期 = 复位 + 输出
        rDo, oDo = rst.resetDo, rst.overDo  # 复位/输出阶段的 DO 掩码
        S0s, S0t = c.f.get("S0_Ss", []), c.f.get("S0_St", [])
        # 复归期 teardown 需先恢复 DO 到输出态
        oDo_frms = [HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, oDo)] if oDo is not None else []

        async def do_frm(m):
            """发送 DO 输出帧(如果有配置)"""
            if m is not None:
                return await self.FlushFrames([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, m)])
            return 0

        # ── T0 复位阶段: 设置复位 DO → 写入 Static → 等待复位时间 ──
        if await do_frm(rDo) < 0: return -2
        if await self.FlushFrames(c.f["Ss"]) < 0: return -2
        c.telemetry(-1, add_acc=False)    # tick=-1 表示复位阶段遥测
        tgt = await c.sleep(c.t0 + rs)
        if tgt is not None: return await c.exit(tgt, oDo_frms + c.f["Up"] + S0s)

        # ── T0 输出阶段: 切换输出 DO → Update 生效 → 等待输出时间 ──
        if await do_frm(oDo) < 0: return -2
        ts = await self._SendWithAck(HWCodec.FRAME_SYS_UPDATE)
        if ts < 0: return -2
        self.lastUpHwTs = ts
        self.frames_sent_count += 1
        c.telemetry(0, add_acc=False)     # tick=0 输出阶段遥测
        tgt = await c.sleep(c.t0 + cy)
        if tgt is not None: return await c.exit(tgt, S0s)

        # ── 后续 tick 循环: 每个周期 = 复位阶段 + 输出阶段 ──
        for tick in range(1, c.node.count + 1):
            if not self.isRunning: break
            # 复位阶段: 切DO → Update → 写步进增量 → 等复位时间
            if await do_frm(rDo) < 0: return -2
            ts = await self._SendWithAck(HWCodec.FRAME_SYS_UPDATE)
            if ts < 0: return -2
            self.lastUpHwTs = ts
            self.frames_sent_count += 1
            if await self.FlushFrames(c.st(tick)) < 0: return -2
            c.telemetry(-1, add_acc=False)
            tgt = await c.sleep(c.t0 + tick * cy + rs)
            if tgt is not None: return await c.exit(tgt, oDo_frms + c.nst(tick) + c.f["Up"] + S0s + S0t)
            # 输出阶段: 切DO → Update 使步进生效 → 等输出时间
            if await do_frm(oDo) < 0: return -2
            ts = await self._SendWithAck(HWCodec.FRAME_SYS_UPDATE)
            if ts < 0: return -2
            self.lastUpHwTs = ts
            self.frames_sent_count += 1
            c.telemetry(tick)
            tgt = await c.sleep(c.t0 + (tick + 1) * cy)
            if tgt is not None: return await c.exit(tgt, S0s + S0t)
        return await c.exit(None, S0s + S0t)

    # ─── Mode 4: 直流补偿突发 ───
    async def _RunDcComp(self, c: '_RunCtx') -> Optional[int]:
        M, Pg, Ss = c.node.count, c.f["Pg"], c.f["Ss"]  # M=步数, Pg=相位闸门帧, Ss=静态帧
        S0s, S0t = c.f.get("S0_Ss", []), c.f.get("S0_St", [])
        Sd, Up, St = c.f.get("Ss_Dc", []), c.f["Up"], c.f.get("St", [])  # Sd=洗底帧

        # 首帧突发: Static + Step[0] + PhaseGate[0](含Update) + 归零 + Step[1]
        burst = Ss + St[0] + [Pg[0]] + S0s + (St[1] if M > 1 else [])
        tgt_ack = self.frames_sent_count + len(Ss + St[0]) + 1  # Pg[0]的ACK序号
        await self.FlushFramesNoAck(burst)   # 全部发出，不等单帧ACK
        ts = await self.WaitForAck(tgt_ack)  # 等 PhaseGate ACK(硬件执行Update)
        if ts < 0: return -2
        self.lastUpHwTs = ts

        # PhaseGate ACK 后才开始计时和 DO 任务
        c.t0 = time.perf_counter()
        self._SpawnDoTasks(c.node, c.t0)
        # 首步后立即检查触发(DI 可能已变位)
        if (r := self.CheckTriggersImmediate(c.node, c.t0, c.entry_di)) is not None:
            c.lastCond = r[0]
            return await c.exit(r[1], Sd + Up + S0t)  # 洗底 + Update + 归零步进

        # 后续步: PhaseGate[N] + Step[N+1] → 等ACK → 检查触发
        for N in range(1, M):
            if not self.isRunning: break
            b = [Pg[N]] + (St[N+1] if N+1 < M else [])
            ta = self.frames_sent_count + 1
            await self.FlushFramesNoAck(b)
            ts = await self.WaitForAck(ta)
            if ts < 0: return -2
            self.lastUpHwTs = ts
            if (r := self.CheckTriggersImmediate(c.node, c.t0, c.entry_di)) is not None:
                c.lastCond = r[0]
                return await c.exit(r[1], Sd + Up + S0t)
            c.telemetry(N, add_acc=False)

        # 最后一步 PhaseGate 生效后，无限等待触发
        await self.FlushFramesNoAck([Pg[M]])
        ts = await self.WaitForAck(self.frames_sent_count)
        self.lastUpHwTs = ts
        tgt = await c.sleep(float('inf'))
        return await c.exit(tgt, S0t)
        


    def _SpawnDoTasks(self, node: SequenceNode, tZeroOs: float):
        """为节点的每个 DO 动作创建延时后台任务"""
        for delay_ms, mask in node.doActions:
            async def delayed_do(d_ms: float, m: int):
                # 计算目标执行时间(节点起始 + 延迟)
                target_os = tZeroOs + (d_ms / 1000.0)
                remain = target_os - time.perf_counter()
                if remain > 0:
                    await asyncio.sleep(remain)  # 等到目标时刻
                # 构建并发送 DO 输出帧
                frms = [HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, m & 0xFF)]
                self.doStatusShadow = m & 0xFF   # 更新 DO 影库
                await self.FlushFrames(frms)
                # tick=-2 标识 DO 变更事件(区别于波形 tick)
                self.OnValueUpdate(node.id, -2, self.doStatusShadow)
            task = asyncio.create_task(delayed_do(delay_ms, mask))
            self.bgTasks.add(task)                # 加入后台任务集合
            task.add_done_callback(self.bgTasks.discard)  # 完成后自动移除


    def _AccumulatorAdd(self, node: SequenceNode, tick: int = 0):
        """将指定 tick 的步进增量累加到电流通道幅值(供 HWProtect 虚拟温度计算)"""
        if not node.steps: return
        step_dict = node.steps[tick] if tick < len(node.steps) else node.steps[-1]
        for ch, layers in step_dict.items():
            if ch not in _I_CH_SET: continue
            ch_state = self.currentState.get(ch)
            if ch_state is None: continue
            for l_idx, (da, dp) in layers.items():
                if l_idx in ch_state:
                    ch_state[l_idx] += da


    # ═══════════════════════════════════════════════════
    #  第4区: 触发逻辑 + 遥测推送
    # ═══════════════════════════════════════════════════

    def _EvaluateTriggers(self, node: SequenceNode, action_time_ms: float, entry_di: int) -> Optional[Tuple[int, int]]:
        if self.manualTrigTarget is not None:
            tgt = self.manualTrigTarget
            self.manualTrigTarget = None
            return (None, tgt)
        for cond, nid, data in node.triggers:
            if cond == TrigCond.ConTimeout:
                if action_time_ms >= data:
                    return (cond, nid)
            elif cond == TrigCond.ConDiMatch:
                mask = data & 0xFF
                is_and = (data & 0x100) != 0
                is_recover = (data & 0x400) != 0
                curr = self.diStatusShadow
                base_di = entry_di if (data & 0x200) != 0 else self.initDiShadow
                if is_recover:
                    val = ~(curr ^ base_di) & mask
                else:
                    val = (curr ^ base_di) & mask
                matched = (val == mask and mask != 0) if is_and else (val > 0)
                if matched:
                    return (cond, nid)
        return None


    def CheckTriggersImmediate(self, node: SequenceNode, tZeroOs: float, entry_di: int) -> Optional[Tuple[int, int]]:
        action_time_ms = (time.perf_counter() - tZeroOs) * 1000.0
        return self._EvaluateTriggers(node, action_time_ms, entry_di)


    def OnValueUpdate(self, nodeId: int, tick: int, do: int):
        asyncio.create_task(self.emitEvent([EngineEvt.EvtValueUpdate, nodeId, tick, do, self.lastUpHwTs]))




    # ═══════════════════════════════════════════════════
    #  第5区: 硬件 I/O
    # ═══════════════════════════════════════════════════

    def HandleHwFeedback(self, timestamp: int, statusCode: int) -> None:
        logger.debug(f"RX ts={timestamp} status=0x{statusCode:04X}")
        fut = self.ackFuture
        if statusCode == 0x0000:
            if fut and not fut.done(): fut.set_result(timestamp)
            self.ack_counter += 1
        elif statusCode == 0xFFFF:
            if fut and not fut.done(): fut.set_result(-1)
        else:
            di = statusCode & 0xFF
            self.diStatusShadow = di
            self.diEvent.set()
            asyncio.create_task(self.emitEvent([EngineEvt.EvtDiChange, di, timestamp - self.debounceUs]))

    async def _SendWithAck(self, frame: bytes, max_retries: int = 3) -> int:
        async with self.hwLock:
            for attempt in range(max_retries):
                fut = self.loop.create_future()
                self.ackFuture = fut
                logger.debug(f"TX {frame.hex().upper()}")
                self.hwGateway.SendBytes(frame)
                timer = self.loop.call_later(0.1, _TimeoutFuture, fut)
                try:
                    ts = await fut
                    if ts >= 0:
                        return ts
                    elif ts == -2:
                        logger.error("ACK timeout, dropping packet.")
                        break
                    else:
                        logger.warning(f"HW ERR_ILLEGAL, retry {attempt + 1}/{max_retries}")
                finally:
                    timer.cancel()
                    self.ackFuture = None
            logger.error("CRITICAL: HW unresponsive, triggering hard reset")
            self.hwGateway.SendBytes(HWCodec.FRAME_SYS_RESET)
            self.ForceStopLocal()
            await self.emitEvent({"type": "error", "msg": "Hardware connection lost!"})
            return -1

    async def _SendNoAck(self, frame: bytes) -> None:
        async with self.hwLock:
            logger.debug(f"TX {frame.hex().upper()}")
            self.hwGateway.SendBytes(frame)

    async def FlushFrames(self, frames: List[bytes]) -> int:
        t = 0
        for f in frames:
            t = await self._SendWithAck(f)
            if t < 0: return -1
            self.frames_sent_count += 1
        return t

    async def FlushFramesNoAck(self, frames: List[bytes]) -> None:
        for f in frames:
            await self._SendNoAck(f)
            self.frames_sent_count += 1

    async def WaitForAck(self, target_ack: int, timeout: float = 1.0) -> int:
        deadline = time.perf_counter() + timeout
        while self.ack_counter < target_ack:
            remain = deadline - time.perf_counter()
            if remain <= 0: return -1
            fut = self.loop.create_future()
            self.ackFuture = fut
            timer = self.loop.call_later(remain, _TimeoutFuture, fut)
            try:
                ts = await fut
                if ts < 0: return -1
            finally:
                timer.cancel()
                self.ackFuture = None
        return ts

    async def EmergencyReset(self) -> None:
        await self._SendNoAck(HWCodec.FRAME_SYS_RESET)

    def _OnCoreFinished(self, fut):
        self.isRunning = False
        logger.info("USE Engine runloop terminated.")
        
        # 1. 基础防重放标记（防抖，确保回调内部只跑一次）
        if self._term_sent:
            return
        self._term_sent = True
        
        is_cancelled = fut.cancelled()
        if is_cancelled:
            logger.warning("Core runloop cancelled. External failsafe should handle HW reset.")
            return
            
        try:
            loop = asyncio.get_running_loop()
            
            def _safe_discard(t):
                try:
                    t.exception()
                except asyncio.CancelledError:
                    pass
            
            # 2. 判断死因：若是正常结束或UI点击停止，去砸底座保平安；若是被路由外置接管而静默吊销的，则绝不发包防死锁
            # terminal 事件已在 _StartCoreLoop 循环退出时发送，这里只做硬件复位
            if not self._suppress_hw_teardown:
                t1 = loop.create_task(self.Stop())
                t1.add_done_callback(_safe_discard)
        except RuntimeError:
            pass

