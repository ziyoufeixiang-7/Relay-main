import asyncio
import time
from typing import Dict
from utils.SysLogger import GetLogger
from api.BaseApi import BaseApi, ApiNodeData
from logic.Calibration import calib
from logic.USEEngine import USENode
from logic.FPGACodec import HWCodec, HWConfig
from logic.SysConfig import SysConfig
from logic.AutoCal import AutoCal
logger = GetLogger("EngineCtrl")

ampBeepTime = 0.5
diBeepTime = 0.1

class EngineCtrl:
    def __init__(self, engine, hwProtect, wsSend):
        self.engine = engine
        self.sysConfig = SysConfig(self._send)
        self.autoCal = AutoCal(
            self._send, self.engine.sendBytes, self.setAmplifier,
            send_frames=getattr(self.engine, "sendFrames", None) or getattr(self.engine, "flush", None),
            can_start=lambda: self._stopEvent.is_set() and not self.running,
            state_update=self._calibrationState,
            on_saved=getattr(self.engine, "refreshCalibration", None),
        )
        self._hwProtect = hwProtect
        self._wsSend = wsSend

        self.running = False
        self._module = None
        self.activeApi = None
        self.di = 0
        self.do = 0
        self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
        self.nodes = {}
        self.errorReason = None
        self._lastTelemetryTs = 0.0
        self._stopEvent = asyncio.Event()
        self._stopEvent.set()
        self._startLock = asyncio.Lock()

    async def onEvent(self, evt):
        match evt[0]:
            case 0:
                nodeId, tick, ts = evt[1], evt[2], evt[3]
                if nodeId == 0xFFFF:
                    # The terminal node's ACK is the only hardware timestamp
                    # available for closing the final state-sequence row.
                    # Keep this callback optional so other APIs are
                    # unaffected.
                    if a := self.activeApi:
                        terminal_cb = getattr(a, "onTerminal", None)
                        if callable(terminal_cb):
                            try:
                                terminal_cb(ts)
                            except Exception:
                                logger.exception(
                                    "API terminal timestamp callback failed"
                                )
                    self.errorReason = (
                        self.errorReason
                        or self.engine.errorReason
                    )
                    self.engine.errorReason = None
                    self._doStop()
                    return
                self._handleValueUpdate(nodeId, tick)
                if a := self.activeApi:
                    a.onUpdate(nodeId, tick, ts)
            case 1:
                self._hwProtect.Beep(diBeepTime)
                self.di = evt[1]
                self.sendDi()
                if a := self.activeApi:
                    a.onDi(evt[1], evt[2])
            case 2:
                self.do = evt[1]
                self.sendDo()

    async def startTest(self, msg):
        if self.autoCal.is_running():
            self.sendError("Calibration is running, please stop it first")
            return
        async with self._startLock:
            if not self._stopEvent.is_set():
                self.engine.manualTrig(0xFFFF)
                await self._stopEvent.wait()
            # Calibration can start while a queued test awaits terminal ACK.
            if self.autoCal.is_running():
                self.sendError("Calibration is running, please stop it first")
                return
            self._stopEvent.clear()
            try:
                self._module = msg.get("module")
                self.activeApi = self._createApi(self._module) if self._module else None
                if not self.activeApi:
                    self.errorReason = f"API module '{self._module or 'None'}' not found."
                    self._doStop()
                    return

                self.do = 0
                self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
                self.nodes = {}
                self.engine.setDebounce(HWConfig.ConvertDbncToReg(msg.get("params", {}).get("debounce", 20)))
                self.engine.manualTrig(0x0000)
                self.running = True
                self.setAmplifier(True)

                api = self.activeApi
                await asyncio.sleep(0.1)
                # Stop / terminal ACK can arrive while the amplifier warms up.
                if self.running and self.activeApi is api:
                    api.setup(self, msg.get("params", {}))
            except asyncio.CancelledError:
                if not self._stopEvent.is_set():
                    self._failTest("Test start cancelled")
                raise
            except Exception as exc:
                logger.exception("API start failed")
                self._failTest(f"API start failed: {exc}")

    def _failTest(self, reason):
        # Do not wait for an FPGA ACK to disable unsafe output or report error.
        # Keep the terminal handshake so the next test cannot race the reset.
        self.running = False
        self.setAmplifier(False)
        # onStop still follows the terminal timestamp (state-sequence reports).
        # running=False already blocks every API node write / manual trigger.
        self.sendError(reason)
        self.engine.manualTrig(0xFFFF)

    def stopTest(self, reason: str = None):
        if self.autoCal.is_running():
            self.autoCal._stop()
        if not self.running:
            return
        self.running = False
        if reason:
            self.errorReason = reason
        self.setAmplifier(False)
        self.engine.manualTrig(0xFFFF)

    def _doStop(self):
        self.setAmplifier(False)
        if reason := self.errorReason:
            self.errorReason = None
            logger.error(f"Engine forced terminal: {reason}")
            self.sendError(reason)
        if a := self.activeApi:
            try:
                a.onStop()
            except Exception:
                logger.exception("API stop callback failed")
            self.activeApi = None
        self.sendStop()
        self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
        self.nodes = {}
        self.running = False
        self._module = None
        self._stopEvent.set()

    def upsertNodes(self, apiNodes: Dict[int, ApiNodeData]) -> bool:
        if not self.running:
            return False
        compiledNodes = {}
        for nodeId, apiNode in apiNodes.items():
            try:
                compiledNodes[nodeId] = self._compileNode(apiNode)
                logger.debug(f"Compiled Node {nodeId}")
            except Exception as e:
                logger.error(f"Failed to compile Node {nodeId}: {e}", exc_info=True)
                self._failTest(f"Node {nodeId:04X} compile error: {e}")
                return False
        self.nodes.update(apiNodes)
        self.engine.upsertNodes(compiledNodes)
        return True

    def trigNode(self, nodeId: int) -> bool:
        if not self.running:
            return False
        self.engine.manualTrig(nodeId)
        return True

    def sendDi(self):
        self._send({"type": "value_update", "di": self.di})

    def sendDo(self):
        self._send({"type": "value_update", "do": self.do})

    def sendNodeUpdate(self, nodeId: int):
        self._send({"type": "value_update", "nodeId": nodeId})

    def sendReport(self, data: dict):
        self._send({"type": "report", "data": data})

    def sendError(self, msg: str):
        self._send({"type": "error", "msg": msg})

    def sendStop(self):
        self._send({"type": "stop"})

    def sendLoad(self, thermal: float, active: bool):
        self._send({"type": "load", "thermal": thermal, "active": active})

    def sendValueUpdate(self, targetDict: dict, src: dict = None):
        snapshot = getattr(self.activeApi, "_calibrationSnapshot", None)
        physical = {}
        for ch, layers in targetDict.items():
            view = dict(self.state.get(ch, {}))
            view.update(src[ch] if src is not None else layers)
            freq = view.get(0, [0, HWConfig.ConvertFreqToReg(50.0)])[1] / HWConfig.FREQ
            physical[str(HWConfig.UnmapChannel(ch))] = {
                str(layer): list(calib.RegToPhys(
                    ch, layer, *(src[ch][layer] if src is not None else values),
                    fundamentalHz=freq, snapshot=snapshot,
                )) for layer, values in layers.items()
            }
        self._send({
            "type": "value_update",
            "static": physical,
        })

    def _calibrationState(self, ch=None, layer=None, registers=None):
        if ch is None:
            self.state = {channel: {} for channel in HWConfig.ACTIVE_CHANNELS}
        else:
            self.state[ch][layer] = list(registers)

    def setAmplifier(self, active: bool):
        self._hwProtect.SetAmplifierEnable(active)
        self._hwProtect.Beep(ampBeepTime)

    def _send(self, data: dict):
        if self._module:
            data["module"] = self._module
        asyncio.ensure_future(self._wsSend(data))

    def _createApi(self, module: str):
        api_instance = BaseApi.create(module)
        if not api_instance:
            logger.error(f"API module '{module}' not found.")
        return api_instance

    def _compileDictToFrames(self, dDict: dict, cmdCode: int) -> list:
        masks = {}
        for ch, layers in (dDict or {}).items():
            for l, (a, p) in layers.items():
                masks[(l, a, p)] = masks.get((l, a, p), 0) | (1 << ch)
        return [
            HWCodec.BuildParamFrame(cmdCode, (l - 1) & 0xFF, m, a, p)
            for (l, a, p), m in masks.items()
        ]

    def _compileNode(self, n: ApiNodeData) -> USENode:
        gateFrames = None
        if n.gate and n.steps and n.interval is not None and n.base:
            ch, phase = n.gate[0], n.gate[1]
            delta = int(n.base[ch][0][1] * HWConfig.PHASE_PER_FREQ_MS * n.interval)
            gateFrames = [[HWCodec.BuildPhaseGateFrame(ch, (phase + i * delta) & 0xFFFFFFFF)] for i in range(len(n.steps))]
            
        return USENode(
            mode=n.mode,
            interval=n.interval,
            resetTime=n.resetTime,
            baseFrame=self._compileDictToFrames(n.base, HWCodec.DDS_WR_SHADOW),
            resetFrame=self._compileDictToFrames(n.reset, HWCodec.DDS_WR_STAGE),
            stepFrames=[self._compileDictToFrames(s, HWCodec.DDS_STEP_SHADOW) for s in n.steps] if n.steps else None,
            gateFrames=gateFrames,
            resetDo=n.resetDo,
            doActions=n.doActions,
            countOverId=n.countOverId,
            diMatchMask=n.diMatchMask,
            diMatchId=n.diMatchId,
            timeoutMs=n.timeoutMs,
            timeoutId=n.timeoutId,
        )

    def _handleValueUpdate(self, nodeId: int, tick: int):
        if not (n := self.nodes.get(nodeId)):
            return
        match tick:
            case 0:
                if target := n.base:
                    for ch, layers in target.items():
                        self.state[ch].update({l: list(v) for l, v in layers.items()})
                    self.sendValueUpdate(target)
            case -1:
                if target := n.reset:
                    self.sendValueUpdate(target)
            case _:
                if target := n.steps[tick - 1]:
                    for ch, layers in target.items():
                        for l, (a, p) in layers.items():
                            self.state[ch][l][0] = (self.state[ch][l][0] + a) & 0xFFFFFFFF
                            self.state[ch][l][1] = (self.state[ch][l][1] + p) & 0xFFFFFFFF
                    if tick == len(n.steps) or time.monotonic() - self._lastTelemetryTs >= 0.05:
                        self._lastTelemetryTs = time.monotonic()
                        self.sendValueUpdate(target, self.state)

    async def HandleCommand(self, msg: dict):
        match msg.get("cmd"):
            case "start":
                await self.startTest(msg)
            case "stop":
                self.stopTest()
            case "sysConfig":
                self.sysConfig.handle_command(msg)
            case "autoCal":
                self.autoCal.handle_command(msg)
            case _:
                if self.running and (a := self.activeApi):
                    try:
                        a.onWebCommand(msg)
                    except Exception as exc:
                        logger.exception("API command failed")
                        self._failTest(f"API command failed: {exc}")
