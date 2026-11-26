import asyncio
import time
from utils.SysLogger import GetLogger
from logic.FPGACodec import HWConfig
from gpiozero import DigitalOutputDevice, DigitalInputDevice

logger = GetLogger("HWProtect")

GPIO_AMP_ENABLE = 21
GPIO_BEEP = 26
GPIO_HW_FAULT = 20

MAX_VIRTUAL_TEMP = 100.0
COEF_HEATING = 0.008 * (60.0 / 32767.0)**2
COEF_COOLING = 0.008

class HWProtect:
    def __init__(self, enable_protection: bool = False):
        self._enabled = enable_protection
        self.engineCtrl = None
        self.pinAmpEnable = None
        self.pinBeep = None
        self.ampEnabled = False
        self._beepTask = None
        self.loop = asyncio.get_running_loop()
        self.monitorTask = asyncio.create_task(self._MonitorLoop())

        if enable_protection:
            try:
                self.pinAmpEnable = DigitalOutputDevice(GPIO_AMP_ENABLE, active_high=True, initial_value=False)
                self.pinBeep = DigitalOutputDevice(GPIO_BEEP, active_high=True, initial_value=False)
                self.pinHwFault = DigitalInputDevice(GPIO_HW_FAULT, pull_up=True, bounce_time=0.1)
                self.pinHwFault.when_activated = lambda: self._TriggerProtection("HARDWARE_OVERHEAT_PROTECTION (GPIO)")
                logger.info("Hardware GPIO protection enabled.")
            except Exception as e:
                logger.warning(f"Failed to initialize GPIO: {e}")
        else:
            logger.warning("Hardware protection DISABLED (debug mode).")

    def SetAmplifierEnable(self, active: bool):
        self.ampEnabled = active
        if self.pinAmpEnable:
            if active:
                self.pinAmpEnable.on()
                logger.info("Amplifier Enabled")
            else:
                self.pinAmpEnable.off()
                logger.info("Amplifier Disabled")

    def Beep(self, duration_s: float):
        def _start_beep():
            if self._beepTask and not self._beepTask.done():
                self._beepTask.cancel()
            self._beepTask = self.loop.create_task(self._Beeping(duration_s))
        self.loop.call_soon_threadsafe(_start_beep)

    async def _Beeping(self, duration_s: float):
        try:
            self.pinBeep.on()
            await asyncio.sleep(duration_s)
        finally:
            self.pinBeep.off()

    def _TriggerProtection(self, reason: str):
        self.SetAmplifierEnable(False)
        logger.error(reason)
        if self.engineCtrl:
            self.engineCtrl.stopTest(reason)


    async def _MonitorLoop(self):
        I_CH = HWConfig.I_CHANNELS
        temps = {ch: 0.0 for ch in I_CH}
        monotonic = time.monotonic
        
        while not self.engineCtrl:
            await asyncio.sleep(0.1)
        ctrl = self.engineCtrl

        lastTs = monotonic()
        while self._enabled:
            await asyncio.sleep(0.2)
            now = monotonic()
            dt = now - lastTs
            lastTs = now
            coolMult = 1.0 - (COEF_COOLING * dt)
            maxT = 0.0

            if self.ampEnabled:
                state = ctrl.state
                heatFactor = COEF_HEATING * dt
                if state:
                    for ch in I_CH:
                        layers = state.get(ch)
                        power = 0.0
                        if layers:
                            for layer, vals in layers.items():
                                v = vals[0] >> 16
                                if v >= 32768:
                                    v -= 65536
                                power += v * v if layer == 0 else v * v * 0.5
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

            self.engineCtrl.sendLoad(round(maxT, 2), self.ampEnabled)
