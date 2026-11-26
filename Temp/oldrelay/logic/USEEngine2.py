"""
USEEngine2 — Phase 1 适配器 (纯 FSM 包装)

USEEngine2 包装旧 USEEngine，对外只暴露新 API。
Engine 不知道功放/生命周期，这些由调用层 (ApiHdlMgr/CmdRouter) 控制。

新 API:
    engine.debounce = 61
    engine.UpsertNodes([flat_node1, ...])
    engine.ManualTrig(target_id)

哨兵节点:
    0x0000 (IDLE)     → 旧 LoadConfig + Start
    0xFFFF (TERMINAL) → 旧 Stop
    其他              → 旧 FireManualTrigger
"""

import asyncio
from logic.USEEngine import USEEngine
from logic.FlatCodec import NodeKey
from utils.SysLogger import GetLogger

logger = GetLogger("USEEngine2")


class USEEngine2:
    IDLE_ID     = 0x0000
    TERMINAL_ID = 0xFFFF

    def __init__(self, hwGateway, emitEvent):
        self._eng = USEEngine(hwGateway=hwGateway, emitEvent=emitEvent)

        # 直写属性
        self.debounce = 61

        # 节点缓冲区（ManualTrig(0) 时传给 LoadConfig）
        self._buffered_nodes = []

        # 后台任务
        self._bg_tasks = set()

    # ═══════════════════════════════════════════════════
    #  __getattr__: 透传未定义属性到内部 _eng
    #  (isRunning, currentState, nodes, HandleHwFeedback,
    #   hwLock, ackFuture, diEvent, hwGateway, etc.)
    # ═══════════════════════════════════════════════════

    def __getattr__(self, name):
        if name == '_eng':
            raise AttributeError(name)
        return getattr(self._eng, name)

    # ═══════════════════════════════════════════════════
    #  新 API: 仅 2 个方法
    # ═══════════════════════════════════════════════════

    def UpsertNodes(self, flat_nodes):
        """
        写入/覆盖节点（禁止写入 IDLE 和 TERMINAL）。
        - 引擎未运行: 缓冲, 等 ManualTrig(0) 时一起加载
        - 引擎运行中: 直接热更新
        """
        filtered = []
        for fn in flat_nodes:
            nid = fn.get(NodeKey.Id)
            if nid == self.IDLE_ID or nid == self.TERMINAL_ID:
                logger.warning(f"UpsertNodes: 禁止写入哨兵节点 0x{nid:04X}, 已跳过")
                continue
            filtered.append(fn)

        self._buffered_nodes = filtered
        if self._eng.isRunning:
            self._eng.UpsertNodesFlat(filtered)

    def ManualTrig(self, target_id):
        """
        跳转到任意节点 (0x0000 ~ 0xFFFF)。
        Engine 不做任何功放控制，调用者自行处理。

        - 0x0000 (IDLE):     LoadConfig + Start (硬件初始化)
        - 0xFFFF (TERMINAL): Stop (硬件复位)
        - 其他:              FireManualTrigger (FSM 跳转)
        """
        if target_id == self.IDLE_ID:
            self._SpawnTask(self._DoIdle())
        elif target_id == self.TERMINAL_ID:
            self._SpawnTask(self._DoTerminal())
        else:
            if self._eng.isRunning:
                self._eng.FireManualTrigger(target_id)
            else:
                logger.warning(f"ManualTrig({target_id}): 引擎未运行, 忽略")

    # ═══════════════════════════════════════════════════
    #  内部异步实现 (包装旧 API)
    # ═══════════════════════════════════════════════════

    async def _DoIdle(self):
        """ManualTrig(0) → 旧 LoadConfig + Start"""
        if self._eng.isRunning:
            await self._eng.Stop()
        sys_config = {"debounce": self.debounce}
        self._eng.LoadConfig(sys_config, self._buffered_nodes)
        await self._eng.Start()
        logger.info(f"IDLE: HW reset + debounce={self.debounce} + start")

    async def _DoTerminal(self):
        """ManualTrig(0xFFFF) → 旧 Stop"""
        if self._eng.isRunning:
            await self._eng.Stop()
        logger.info("TERMINAL: 引擎已停止")

    # ═══════════════════════════════════════════════════
    #  旧 API 兼容层 (过渡期, CmdRouter 仍调用这些)
    # ═══════════════════════════════════════════════════

    async def Stop(self):
        """旧 Stop → ManualTrig(0xFFFF)"""
        await self._DoTerminal()

    def ForceStopLocal(self):
        """旧 ForceStopLocal (HWProtect 同步调用)"""
        self._eng.ForceStopLocal()

    def FireManualTrigger(self, target_id=None):
        """旧 FireManualTrigger"""
        self._eng.FireManualTrigger(target_id)

    def LoadConfig(self, sys_config, flat_nodes):
        """旧 LoadConfig → 缓冲到属性"""
        self.debounce = sys_config.get("debounce", self.debounce)
        self._buffered_nodes = flat_nodes
        self._eng.LoadConfig(sys_config, flat_nodes)

    async def Start(self):
        """旧 Start"""
        await self._eng.Start()

    def UpsertNodesFlat(self, flat_nodes):
        """旧 UpsertNodesFlat → UpsertNodes"""
        self._eng.UpsertNodesFlat(flat_nodes)

    # ═══════════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════════

    def _SpawnTask(self, coro):
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._OnTaskDone)
        return task

    def _OnTaskDone(self, t):
        self._bg_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error(f"后台任务异常: {t.exception()}")
