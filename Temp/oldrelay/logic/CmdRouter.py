import asyncio
from logic.FPGACodec import HWCodec
from logic.USEEngine2 import USEEngine2
from api.ApiHdlMgr import ApiHdlMgr
from utils.SysLogger import GetLogger

logger = GetLogger("CmdRouter")

class CmdRouter:
    def __init__(self, hwGateway, hwProtect, wsSendCb):
        self.hwGateway = hwGateway
        self.hwProtect = hwProtect
        self.wsSendCb = wsSendCb
        self.registry = ApiHdlMgr()
        self.useEngine = USEEngine2(hwGateway=hwGateway, emitEvent=self.DispatchWsEvent)
        self.hwGateway.engine = self.useEngine
        self.hwProtect.engine = self.useEngine
        self.hwProtect.wsSendCb = wsSendCb
        self.bg_tasks = set()
        self.active_hdl = None
        self.loop = asyncio.get_running_loop()

    def SafeDiscard(self, t):
        self.bg_tasks.discard(t)
        if not t.cancelled() and t.exception():
            logger.error(f"Background task crashed: {t.exception()}")

    def SpawnBgTask(self, coro):
        task = asyncio.create_task(coro)
        self.bg_tasks.add(task)
        task.add_done_callback(self.SafeDiscard)
        return task

    async def StartEngine(self, data: dict) -> None:
        if self.useEngine.isRunning:
            await self.useEngine.Stop()
        try:
            flat_nodes = ApiHdlMgr.JsonNodesToFlat(data.get("nodes", []))
            self.useEngine.LoadConfig(data.get("sys", {}), flat_nodes)
            self.hwProtect.SetAmplifierEnable(True)
            self.SpawnBgTask(self.useEngine.Start())
        except Exception as e:
            logger.error(f"Failed to init USE: {e}")
            import traceback; traceback.print_exc()
            await self.wsSendCb({"type": "error", "msg": str(e)})

    async def _ApplyHotUpdate(self, data: dict, default_trig_id=None):
        flat_nodes = ApiHdlMgr.JsonNodesToFlat(data.get("nodes", []))
        self.useEngine.UpsertNodesFlat(flat_nodes)
        trig_id = data.get("trig_id", default_trig_id)
        if trig_id is not None:
            self.useEngine.FireManualTrigger(trig_id)

    async def HandleCommand(self, payload: dict) -> None:
        cmd = payload.get("cmd")
        if not cmd:
            return
        if cmd == "stop":
            await self.useEngine.Stop()
            if self.active_hdl:
                mod_key = self.active_hdl.MODULE_KEY
                self.active_hdl.ResetState()
                try:
                    await self.wsSendCb({"module": mod_key, "type": "stop"})
                except Exception:
                    pass
            self.hwProtect.SetAmplifierEnable(False)
            self.hwGateway.SendBytes(HWCodec.FRAME_SYS_RESET)
            return
        
        if cmd == "next_node":
            trig_id = None
            if self.active_hdl and hasattr(self.active_hdl, 'manualTrigTargets'):
                cur = self.useEngine.currentNodeId
                if cur is not None and cur in self.active_hdl.manualTrigTargets:
                    trig_id = self.active_hdl.manualTrigTargets[cur]
            if trig_id is None:
                cur = self.useEngine.currentNodeId
                if cur is not None and cur in self.useEngine.nodes:
                    trigs = self.useEngine.nodes[cur].triggers
                    if trigs:
                        trig_id = trigs[0][1]  # first trigger's nextId
            if trig_id is not None:
                self.useEngine.FireManualTrigger(trig_id)
            return
            
        if cmd == "update_static":
            if self.active_hdl and hasattr(self.active_hdl, 'ProcessStart'):
                try:
                    data = await self.active_hdl.ProcessStart({"statics": payload.get("static") or {}})
                    if data and "hot_update" in data:
                        await self._ApplyHotUpdate(data)
                except Exception as e:
                    logger.error(f"Error handling update_static: {e}")
            return
            
        module = payload.get("module")
        if cmd == "start":
            config_params = payload.get("params", {})

            if module == "useEngine":
                await self.StartEngine(config_params)
                return
            else:
                hdl = self.registry.get_handler(module)
                if not hdl:
                    logger.error(f"Module {module} not found in ApiHdlMgr.")
                    return
                data = await hdl.ProcessStart(config_params)
                if not data: return
                
                if "hot_update" in data:
                    await self._ApplyHotUpdate(data, default_trig_id=1)
                    return
                    
                self.active_hdl = hdl
                self.registry.ClearOtherHandlers(module)
                await self.StartEngine(data)
                return
        if module == "useEngine":
            pass  # engine-level commands handled above

    async def DispatchWsEvent(self, payload) -> None:
        # 协议事件 list → handler dict 适配
        if isinstance(payload, list) and len(payload) >= 2:
            evt_id = payload[0]
            if evt_id == 0x23:  # EvtDiChange: [0x23, di, hw_ts] — 独立 DI 事件
                di_dict = {"type": "di_change", "di": payload[1], "hw_ts": payload[2]}
                # 始终转发给前端 (value_update 格式)
                await self.wsSendCb({"type": "value_update", "di": payload[1]})
                # 同时传给 handler 做逻辑判断 (时间计算等)
                if self.active_hdl and self.active_hdl.isActive:
                    for m in self.active_hdl.InterceptWsEvent(di_dict):
                        if m.get("type") == "value_update" and "di" in m and "static" not in m:
                            continue  # 已经发过了，跳过 handler 返回的 DI 消息
                        if m.get("internal_hot_update"):
                            await self._ApplyHotUpdate(m)
                        elif m.get("internal_manual_trig"):
                            self.useEngine.FireManualTrigger(m.get("manual_trig_id"))
                        else:
                            await self.wsSendCb(m)
                return
            elif evt_id == 0x21:  # EvtValueUpdate: [0x21, id, tick, do, hw_ts]
                payload = {"type": "value_update", "id": payload[1], "tick": payload[2],
                           "do": payload[3], "hw_ts": payload[4]}
            elif evt_id == 0x22:  # EvtTrigger: [0x22, cond, next_id, hw_ts]
                _COND_NAMES = {0: "DI_MATCH", 1: "TIME_OUT", 2: "COUNT_OVER"}
                payload = {"type": "trigger", "condition": _COND_NAMES.get(payload[1], "UNKNOWN"),
                           "next_id": payload[2], "hw_ts": payload[3] if len(payload) > 3 else 0}
            else:
                return

        if self.active_hdl and self.active_hdl.isActive:
            for m in self.active_hdl.InterceptWsEvent(payload):
                if m.get("internal_hot_update"):
                    await self._ApplyHotUpdate(m)
                elif m.get("internal_manual_trig"):
                    self.useEngine.FireManualTrigger(m.get("manual_trig_id"))
                else:
                    await self.wsSendCb(m)
            return
        await self.wsSendCb(payload)