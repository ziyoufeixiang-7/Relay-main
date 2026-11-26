import asyncio
import time
from typing import Dict
from utils.SysLogger import GetLogger
from logic.FPGACodec import HWConfig, HWCodec
from gpiozero import DigitalOutputDevice, DigitalInputDevice

logger = GetLogger("HWProtect")

DISABLE_GPIO_HARDWARE = True
GPIO_AMP_ENABLE = 26
GPIO_HW_FAULT = 20

MAX_VIRTUAL_TEMP = 100.0
COEF_HEATING = 0.008 * (60.0 / 32767.0)**2
COEF_COOLING = 0.008

class HWProtect:
    def __init__(self):
        self.engine = None
        self.wsSendCb = None
        self.pinAmpEnable = None
        self.ampEnabled = False
        self.monitorTask = asyncio.create_task(self._MonitorLoop())
        
        if not DISABLE_GPIO_HARDWARE:
            try:
                self.pinAmpEnable = DigitalOutputDevice(GPIO_AMP_ENABLE, active_high=False, initial_value=False)
                self.pinHwFault = DigitalInputDevice(GPIO_HW_FAULT, pull_up=True)
                self.pinHwFault.when_activated = lambda: self._TriggerProtection("HARDWARE_OVERHEAT_PROTECTION (GPIO)")
            except Exception as e:
                logger.warning(f"Failed to initialize GPIO: {e}")
        else:
            logger.warning("Hardware GPIO protection is BYPASSED (DISABLE_GPIO_HARDWARE = True).")

    def SetAmplifierEnable(self, active: bool):
        self.ampEnabled = active
        if self.pinAmpEnable:
            if active:
                self.pinAmpEnable.on()
                logger.info("Amplifier Enabled")
            else:
                self.pinAmpEnable.off()
                logger.info("Amplifier Disabled")

    def _TriggerProtection(self, reason: str):
        self.SetAmplifierEnable(False)
        logger.error(reason)
        self.engine.hwGateway.SendBytes(HWCodec.FRAME_SYS_RESET)
        self.engine.ForceStopLocal()
        asyncio.ensure_future(self.wsSendCb({"type": "error", "msg": f"Emergency Halt: {reason}"}))


    async def _MonitorLoop(self):
        I_CH = HWConfig.I_CHANNELS
        temps = {ch: 0.0 for ch in I_CH}
        monotonic = time.monotonic
        
        while not self.engine:
            await asyncio.sleep(0.1)
        engine = self.engine
        wsSend = self.wsSendCb

        lastTs = monotonic()
        while False:
            await asyncio.sleep(0.2)
            now = monotonic()
            dt = now - lastTs
            lastTs = now
            coolMult = 1.0 - (COEF_COOLING * dt)
            maxT = 0.0

            if self.ampEnabled:
                state = engine.currentState
                heatFactor = COEF_HEATING * dt
                if state:
                    for ch in I_CH:
                        layers = state.get(ch)
                        power = 0.0
                        if layers:
                            for lIdx, amp in layers.items():
                                v = amp >> 16
                                power += v * v if lIdx == 0 else v * v * 0.5
                        T = temps[ch] * coolMult + power * heatFactor
                        temps[ch] = T
                        if T > maxT: maxT = T
                if maxT >= MAX_VIRTUAL_TEMP:
                    self._TriggerProtection("SOFTWARE_OVERHEAT_PROTECTION")
            else:
                for ch in I_CH:
                    T = temps[ch] * coolMult
                    temps[ch] = T
                    if T > maxT: maxT = T

            await wsSend({
                "type": "load",
                "thermal": round(maxT, 2),
                "active": self.ampEnabled
            })
