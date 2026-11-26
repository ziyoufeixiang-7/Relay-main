import asyncio
from logic.Calibration import calib
from logic.FPGACodec import HWCodec, HWConfig
from utils.SysLogger import GetLogger

logger = GetLogger("EngineBridge")

class EngineBridge:
    def __init__(self, hw_loop: asyncio.AbstractEventLoop,
                 main_loop: asyncio.AbstractEventLoop,
                 emitEvent):
        self._hw_loop = hw_loop
        self._main_loop = main_loop
        self._emit_cb = emitEvent
        self._engine = None

    def bind(self, engine):
        self._engine = engine
        engine._emit = self._on_hw_event
        engine._spawn = self._spawn_on_hw
        logger.info("EngineBridge bound to USEEngine instance.")

    def _spawn_on_hw(self, coro):
        return asyncio.ensure_future(coro, loop=self._hw_loop)

    async def _on_hw_event(self, evt):
        if self._emit_cb:
            self._main_loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._emit_cb(evt),
            )

    @property
    def errorReason(self):
        return self._engine.errorReason

    @errorReason.setter
    def errorReason(self, val):
        self._engine.errorReason = val

    def start(self):
        self._hw_loop.call_soon_threadsafe(self._engine.start)

    def manualTrig(self, targetId: int):
        self._hw_loop.call_soon_threadsafe(self._engine.manualTrig, targetId)

    def setDebounce(self, dbnc: int):
        self._hw_loop.call_soon_threadsafe(self._engine.setDebounce, dbnc)

    def upsertNodes(self, newNodes: dict):
        self._hw_loop.call_soon_threadsafe(self._engine.upsertNodes, newNodes)

    def sendBytes(self, data: bytes):
        self._hw_loop.call_soon_threadsafe(self._engine._send, data)

    async def sendFrames(self, frames):
        """Calibration uses the existing lock/ACK accounting, not raw _send."""
        future = asyncio.run_coroutine_threadsafe(self._engine.flush(frames), self._hw_loop)
        return await asyncio.wrap_future(future)

    def refreshCalibration(self):
        # Only refresh the cached zero calibration; USEEngine execution is unchanged.
        self._hw_loop.call_soon_threadsafe(self._refreshZeroCalibration)

    def _refreshZeroCalibration(self):
        node = self._engine.nodes[0x0000]
        node.baseFrame = (
            [HWCodec.FRAME_SYS_RESET]
            + [HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0xFF, 1 << ch,
                                      *calib.PhysToReg(ch, 0, 0.0, 50.0))
               for ch in HWConfig.ACTIVE_CHANNELS]
            + node.baseFrame[-2:]  # Preserve START and current debounce configuration.
        )
