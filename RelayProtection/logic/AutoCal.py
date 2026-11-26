import asyncio
import copy
import datetime
import json
import math
import os
import statistics
import sys
import time

from utils.SysLogger import GetLogger
from logic.FPGACodec import HWCodec, HWConfig
from logic.Calibration import Calibration, calib

logger = GetLogger("AutoCal")


class _Stopped(Exception):
    pass


class AutoCal:
    # Verified native-command families only: DM30xx is not one SCPI dialect.
    _RIGOL_NATIVE_MODELS = frozenset({"DM3058", "DM3058E", "DM3068"})
    _KEYSIGHT_MODELS = frozenset({"34410A", "34461A"})
    DEFAULT_FREQUENCIES = (
        47, 55, 105, 155, 205, 255, 305, 405, 505, 755, 1000,
        1505, 2005, 2505, 3000,
    )

    def __init__(self, send_func, hwSendBytes, setAmplifier, *,
                 send_frames=None, can_start=None, state_update=None, on_saved=None):
        self._send = send_func
        self._sendBytesFunc = hwSendBytes
        self._sendFramesFunc = send_frames
        self._setAmplifier = setAmplifier
        self._canStart = can_start
        self._stateUpdate = state_update
        self._onSaved = on_saved
        self._running = False
        self._stop_flag = False
        self._authenticated = False
        self._dmm = None
        self._rm = None
        self._dmmModel = None
        self._dmmIdn = None
        self._dmmResource = None
        self._dmmLock = asyncio.Lock()
        self._options = {}

        if "__compiled__" in globals():
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
        else:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._calib_path = os.path.join(base, "config", "calibration.json")
        self._frequency_path = os.path.join(base, "config", "calibration_ac_frequency.json")

    def is_running(self) -> bool:
        return self._running

    def handle_command(self, msg: dict):
        action = msg.get("action")
        if action == "auth":
            self._authenticated = str(msg.get("password")) == "100200"
            self._send({"module": "autoCal", "type": "authResult", "success": int(self._authenticated)})
            return
        if not self._authenticated:
            self._sendError("Unauthorized, please enter the calibration password first")
            return
        if action in ("start", "verify"):
            # Reserve synchronously: stop/another start may arrive before the
            # scheduled coroutine gets its first turn on the event loop.
            ch = self._begin(msg)
            if ch is not None:
                job = self._start if action == "start" else self._startVerify
                asyncio.ensure_future(job(dict(msg), _reserved_ch=ch))
        elif action == "stop":
            self._stop()

    def _sendStatus(self, progress: int, value=None, **details):
        self._send({
            "module": "autoCal", "type": "calStatus",
            "progress": progress, "value": value, **details,
        })

    def _sendError(self, msg: str):
        self._send({"module": "autoCal", "type": "calError", "msg": msg})

    def _checkStop(self):
        if self._stop_flag:
            raise _Stopped()

    async def _io(self, func, *args):
        # No VISA call or time.sleep may block WebSocket/stop handling.
        async with self._dmmLock:
            return await asyncio.to_thread(func, *args)

    async def _sendFrames(self, frames, cleanup=False):
        if not cleanup:
            self._checkStop()
        if self._sendFramesFunc:
            timestamp = await self._sendFramesFunc(frames)
            if timestamp is not None and timestamp < 0:
                raise RuntimeError("FPGA did not acknowledge calibration output")
        else:
            # Backward-compatible injection for standalone/mock users.
            for frame in frames:
                if not cleanup:
                    self._checkStop()
                self._sendBytesFunc(frame)
        if not cleanup:
            self._checkStop()

    async def _resetFpga(self):
        await self._sendFrames([HWCodec.FRAME_SYS_RESET])
        if self._stateUpdate:
            self._stateUpdate()
        await asyncio.sleep(0.01)
        await self._sendFrames([HWCodec.FRAME_SYS_START])

    async def _writeOutput(self, ch, dc, ac=None):
        frames = [HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0xFF, 1 << ch, *dc)]
        if ac is not None:
            frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0x00, 1 << ch, *ac))
        await self._sendFrames(frames + [HWCodec.FRAME_SYS_UPDATE])
        if self._stateUpdate:
            self._stateUpdate(ch, 0, dc)
            if ac is not None:
                self._stateUpdate(ch, 1, ac)

    async def _outputDc(self, ch, reg16):
        if not -32768 <= reg16 <= 32767:
            raise ValueError("Raw DC amplitude exceeds DAC range")
        await self._writeOutput(ch, ((reg16 << 16) & 0xFFFFFFFF, HWConfig.ConvertFreqToReg(50.0)))

    async def _outputAc(self, ch, reg16, frequency_hz=50.0):
        if not 0 < reg16 <= 32767:
            raise ValueError("Raw AC amplitude exceeds DAC range")
        await self._writeOutput(
            ch, (0, HWConfig.ConvertFreqToReg(frequency_hz)), ((reg16 << 16) & 0xFFFFFFFF, 0)
        )

    async def _outputCalib(self, ch, amplitude, is_ac, frequency_hz=50.0, snapshot=None):
        frequency = HWConfig.ConvertFreqToReg(frequency_hz) / HWConfig.FREQ
        layer, scale = (1, math.sqrt(2.0)) if is_ac else (0, 1.0)
        amp, phase = calib.PhysToReg(
            ch, layer, amplitude * scale, 0.0 if is_ac else frequency,
            fundamentalHz=frequency, snapshot=snapshot,
        )
        amp &= 0xFFFF0000  # Match the old calibration's high-16-bit target.
        if is_ac:
            dc = calib.PhysToReg(ch, 0, 0.0, frequency, snapshot=snapshot)
            await self._writeOutput(ch, dc, (amp, phase))
        else:
            await self._writeOutput(ch, (amp, phase))
        target, _ = calib.RegToPhys(
            ch, layer, amp, phase, fundamentalHz=frequency,
            snapshot=snapshot, rounded=False,
        )
        return target / scale

    def _connectDmm(self) -> bool:
        """Run in the VISA worker; report errors only after returning to asyncio.

        Preserve the old connection criterion: a nonempty *IDN? response.
        Model-specific SCPI configuration is a separate measurement step.
        """
        resource = self._options.get("visaResource")
        failures = []
        if self._dmm and resource and resource != self._dmmResource:
            self._closeDmm()
        try:
            if self._dmm:
                identification = self._dmm.query("*IDN?").strip()
                self._acceptDmm(identification)
                return True
        except Exception as exc:
            failures.append(f"cached {self._dmmResource}: {exc}")
            self._closeDmm()
        try:
            import pyvisa
            self._rm = self._rm or pyvisa.ResourceManager("@py")
            resources = [resource] if resource else self._rm.list_resources()
            # Keep the previously working preference for the local USB/GPIB DMM.
            resources = sorted(resources, key=lambda name: not any(
                token in name.upper() for token in ("6833", "0X1AB1", "USB", "GPIB")
            ))
            logger.info("DMM VISA resources: %s", resources)
            if not resources:
                raise RuntimeError("No VISA DMM resources found; check USB connection/permissions and PyUSB/libusb")
            for resource in resources:
                self._checkStop()
                instrument = None
                try:
                    instrument = self._rm.open_resource(resource)
                    instrument.timeout = 5000
                    if resource.upper().endswith("::SOCKET"):
                        instrument.read_termination = "\n"
                        instrument.write_termination = "\n"
                    elif resource.upper().startswith("ASRL"):
                        # DM3068 programming guide: RS232 commands end in CRLF.
                        # Use the meter's default 9600 / 8-N-1 serial settings.
                        instrument.read_termination = "\n"
                        instrument.write_termination = "\r\n"
                    identification = instrument.query("*IDN?").strip()
                    logger.info("DMM %s *IDN?: %r", resource, identification)
                    self._acceptDmm(identification)
                    self._dmm = instrument
                    self._dmmResource = resource
                    logger.info("Connected DMM: %s", identification)
                    return True
                except Exception as exc:
                    failures.append(f"{resource}: {type(exc).__name__}: {exc}")
                    logger.warning("DMM probe failed: %s", failures[-1])
                    if instrument:
                        try:
                            instrument.close()
                        except Exception:
                            logger.warning("Failed to close VISA resource %s", resource, exc_info=True)
            raise RuntimeError("; ".join(failures))
        except _Stopped:
            raise
        except Exception as exc:
            # EngineCtrl._send schedules coroutines on the main event loop.
            # Calling it here would mask the VISA error with 'no current event loop'.
            raise RuntimeError(f"DMM connection failed: {exc}") from exc

    def _closeDmm(self):
        try:
            self._dmm.close()
        except Exception:
            logger.warning("Failed to close cached DMM", exc_info=True)
        finally:
            self._dmm = None
            self._dmmModel = None
            self._dmmIdn = None
            self._dmmResource = None

    def _acceptDmm(self, identification):
        if not identification:
            raise ValueError("Empty *IDN? response")
        fields = [field.strip().strip('\"').upper() for field in identification.split(",")]
        self._dmmIdn = identification
        model = fields[1] if len(fields) >= 2 else None
        self._dmmModel = model if "RIGOL" in fields[0] and model in self._RIGOL_NATIVE_MODELS | {"DM858", "DM858E"} else None
        if model in self._KEYSIGHT_MODELS and any(brand in fields[0] for brand in ("AGILENT", "KEYSIGHT")):
            self._dmmModel = model
        if self._dmmModel is None:
            logger.warning("DMM identification %r uses legacy SCPI configuration; frequency calibration requires DM858/DM858E/DM3058/DM3058E/DM3068/34410A/34461A",
                           identification)

    def _configureDmm(self, commands, is_ac):
        # Preserve the proven workflow: configure, settle, then READ?. No
        # additional range/NPLC/trigger readback or error-queue gate. Firmware
        # may round a requested range (e.g. 0.1 -> 1); readings remain in V/A.
        if self._dmmModel == "DM3068":
            self._configureDm3068(commands, is_ac)
            return
        if self._dmmModel in {"DM3058", "DM3058E"}:
            self._configureDm3058(commands)
            return
        if self._dmmModel in self._KEYSIGHT_MODELS:
            self._configure34410a(commands, is_ac)
            return
        for command in commands:
            if self._dmmModel == "DM858" and command.startswith(":CONF:CURR:") and command.endswith(" 3"):
                command = command[:-1] + "10"  # DM858E has 3 A; DM858 has 10 A.
            self._dmm.write(command)
        if not is_ac:
            function = commands[0].split()[0].replace(":CONF:", ":SENS:")
            nplc = 20 if self._dmmModel in {"DM858", "DM858E"} else 10
            self._dmm.write(f"{function}:NPLC {nplc}")
        time.sleep(0.5)  # VISA worker only; stop handling stays responsive.

    def _configure34410a(self, commands, is_ac):
        # Shared 34410A/34461A native SCPI; retain the 34410A's proven writes.
        # 34461A CONF:CURR with a range <= 3 A also selects its 3 A terminals.
        # Its separate 10 A input is deliberately not selected by this workflow.
        # Unlike DM3068, these meters use CONF + READ? without CMDSet.
        self._dmm.write_termination = "\n"
        self._dmm.read_termination = "\n"
        for command in commands:
            self._dmm.write(command)
        function = commands[0].split()[0].replace(":CONF:", ":SENS:")
        # Both support per-function AC bandwidth and 10, not 20 PLC.
        self._dmm.write(f"{function}:BAND 20" if is_ac else f"{function}:NPLC 10")
        for command in (":TRIG:SOUR IMM", ":TRIG:COUN 1", ":SAMP:COUN 1", ":TRIG:DEL:AUTO ON"):
            self._dmm.write(command)
        # No RANG?/SYST:ERR? gate: the 34410A may report 1000 for AC 750 V.
        # Readings are still physical V/A; do not rescale them by the range.
        time.sleep(0.5)

    def _configureDm3068(self, commands, is_ac):
        # Follow the DM3068 guide's native FUNC/MEAS example, not the
        # Agilent READ? trigger/buffer path that times out on the field unit.
        # USB/LAN examples terminate text with LF; RS232 requires CRLF.
        serial = (self._dmmResource or "").upper().startswith("ASRL")
        self._dmm.write_termination = "\r\n" if serial else "\n"
        self._dmm.read_termination = "\n"

        # Only the filter / 10 PLC setters need the Agilent dialect: native
        # RATE:...:AC is documented as a no-op, and native DC S means 100 PLC.
        # Keep these measurement settings unchanged; never query READ? here.
        function = commands[0].removeprefix(":FUNC:")
        self._dmm.write("CMDSet AGILENT")
        time.sleep(0.1)  # Allow command-set changes to finish before new syntax.
        self._dmm.write("SENS:DET:BAND 20" if is_ac else f"SENS:{function}:NPLC 10")
        self._dmm.write("CMDSet RIGOL")
        time.sleep(0.1)
        for command in (*commands, ":CALC:FUNC NONE", ":TRIG:SOUR AUTO",
                        ":TRIG:AUTO:HOLD OFF", ":TRIG:AUTO:INTE 0.2"):
            self._dmm.write(command)
        time.sleep(0.5)

    def _configureDm3058(self, commands):
        # DM3058/DM3058E guide §§3-24, 3-27, 3-46..48, 3-57..58.
        # Unlike DM3068, native AC RATE works and trigger interval is integer
        # milliseconds. Agilent SENS:DET:BAND is accepted but has no effect.
        serial = (self._dmmResource or "").upper().startswith("ASRL")
        self._dmm.write_termination = "\r\n" if serial else "\n"
        self._dmm.read_termination = "\n"
        self._dmm.write("CMDSet RIGOL")
        time.sleep(0.1)
        function = commands[0].removeprefix(":FUNC:")
        # Native scalar MEAS queries are unavailable in double-display mode.
        for command in (":FUNCtion2:CLEar", *commands, f":RATE:{function} S",
                        ":CALC:FUNC NONE", ":TRIG:SOUR AUTO",
                        ":TRIG:AUTO:HOLD OFF", ":TRIG:AUTO:INTE 400"):
            self._dmm.write(command)
        time.sleep(0.5)

    def _readDmm(self, read_command):
        if self._dmmModel in self._RIGOL_NATIVE_MODELS:
            # In native mode MEAS? returns TRUE/FALSE, NOT a measurement.
            # Poll readiness instead of counting the same cached sample again.
            try:
                ready = self._dmm.query(":MEAS?").strip().upper()
            except Exception as exc:
                raise RuntimeError(f"{self._dmmModel} :MEAS? data-ready query failed: {exc}") from exc
            if ready in {"FALSE", "0"}:
                return None
            if ready not in {"TRUE", "1"}:
                raise ValueError(f"Unexpected {self._dmmModel} :MEAS? data-ready response: {ready!r}")
        return self._dmm.query(read_command)

    @staticmethod
    def _parseReadings(response):
        readings = []
        for field in response.strip().split(","):
            try:
                value = float(field)
            except ValueError:
                continue
            # Instrument overload markers (e.g. 9.9E37) are not measurements.
            if math.isfinite(value) and abs(value) < 1e30:
                readings.append(value)
        if not readings:
            raise ValueError("No finite, non-overload DMM readings")
        return readings

    @staticmethod
    def _wls_fit(x_pos, x_neg, x_zero, y_pos, y_neg, y_zero, var_pos, var_neg, var_zero):
        points = ((x_pos, y_pos, var_pos), (x_neg, y_neg, var_neg), (x_zero, y_zero, var_zero))
        if any(not math.isfinite(v) for point in points for v in point) or any(point[2] < 0 for point in points):
            raise ValueError("Invalid DC fitting samples")
        weights = [1 / (point[2] + 1e-12) for point in points]
        maximum = max(weights)
        weights = [weight / maximum for weight in weights]
        total = sum(weights)
        mean_x = sum(w * p[0] for w, p in zip(weights, points)) / total
        mean_y = sum(w * p[1] for w, p in zip(weights, points)) / total
        denominator = sum(w * (p[0] - mean_x) ** 2 for w, p in zip(weights, points))
        if denominator <= 0:
            raise ValueError("Degenerate DC fitting points")
        gain = sum(w * (p[0] - mean_x) * (p[1] - mean_y) for w, p in zip(weights, points)) / denominator
        return gain, mean_y - gain * mean_x

    def _stop(self):
        if self._running:
            self._stop_flag = True
            # Query may take seconds; amplifier disable must not wait for it.
            self._setAmplifier(False)
            logger.info("AutoCal stop requested; amplifier disabled")

    def _begin(self, msg):
        if self._running:
            self._sendError("Calibration is already running")
            return None
        if self._canStart and not self._canStart():
            self._sendError("Please stop the running experiment before calibration")
            return None
        try:
            if isinstance(msg.get("ch"), bool):
                raise ValueError("boolean channel")
            ch = HWConfig.MapChannel(msg.get("ch", -1))
            if ch not in HWConfig.ACTIVE_CHANNELS:
                raise ValueError("inactive channel")
        except (IndexError, TypeError, ValueError):
            self._sendError(f"Invalid channel: {msg.get('ch')}")
            return None
        self._options = dict(msg)  # Ignore unrelated frontend fields.
        self._running = True
        self._stop_flag = False
        return ch

    async def _finish(self):
        try:
            self._setAmplifier(False)
            await self._sendFrames([HWCodec.FRAME_SYS_RESET], cleanup=True)
            if self._stateUpdate:
                self._stateUpdate()
        except Exception:
            logger.exception("Failed to reset FPGA after calibration")
        finally:
            self._running = False
            self._stop_flag = False

    async def _start(self, msg, *, _reserved_ch=None):
        ch = self._begin(msg) if _reserved_ch is None else _reserved_ch
        if ch is None:
            return
        try:
            self._checkStop()
            if not await self._io(self._connectDmm):
                return
            self._checkStop()
            mode = msg.get("calibrationMode", "ac_frequency" if "frequenciesHz" in msg else "legacy")
            if mode == "ac_frequency":
                await self._runFrequencyCalibration(ch, msg.get("currMode", 0))
            elif mode == "legacy":
                await self._runCalibration(ch, msg.get("currMode", 0))
            else:
                raise ValueError("calibrationMode must be legacy or ac_frequency")
        except _Stopped:
            self._sendStatus(0, stage="stopped")
        except Exception as exc:
            logger.error("AutoCal error: %s", exc, exc_info=True)
            self._sendError(f"Calibration exception: {exc}")
        finally:
            await self._finish()

    def _shuntImpedance(self, frequency_hz, is_ac):
        profile = self._options.get("shuntProfile") or {}
        resistance = Calibration._number(profile.get("dcOhms", self._options.get("shuntOhms", 0.075)), "shunt resistance")
        if resistance <= 0:
            raise ValueError("Shunt resistance must be positive")
        if not is_ac or not profile.get("points"):
            return resistance
        points = profile["points"]
        normalized = [{"freqHz": point["freqHz"], "factorAc": point["ohmsMagnitude"]} for point in points]
        snapshot = calib.WithAcCurve(0, normalized)
        lsb = 1 / HWConfig.FREQ
        if not normalized[0]["freqHz"] - lsb <= frequency_hz <= normalized[-1]["freqHz"] + lsb:
            raise ValueError("Frequency outside the supplied shunt impedance calibration")
        return calib.AcFactor(0, frequency_hz, snapshot)

    async def _measurePoint(self, ch, curr_mode, is_ac, amplitude, commands, *,
                            frequency_hz=50.0, calibrated=False, snapshot=None,
                            settle_sec=5.0, sample_sec=10.0, progress_start=0.0, progress_span=1.0):
        self._checkStop()
        minimum = self._options.get("minSamples", 5)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
            raise ValueError("minSamples must be an integer >= 2")
        measurement_mode = ("current_shunt" if curr_mode == 0 else "current_direct") if ch in HWConfig.I_CHANNELS else "voltage_direct"
        quantity = "CURR" if measurement_mode == "current_direct" else "VOLT"
        read_command = f":MEAS:{quantity}:{'AC' if is_ac else 'DC'}?" if self._dmmModel in self._RIGOL_NATIVE_MODELS else ":READ?"
        context = (f"model={self._dmmModel}, ch={HWConfig.UnmapChannel(ch)}, "
                   f"mode={measurement_mode}, function={'AC' if is_ac else 'DC'}, "
                   f"config={commands!r}, query={read_command}")
        logger.info("DMM measurement: %s, requested=%s, frequency=%s", context, amplitude, frequency_hz)
        self._setAmplifier(False)
        await self._resetFpga()
        await self._io(self._configureDmm, commands, is_ac)
        self._checkStop()
        frequency = HWConfig.ConvertFreqToReg(frequency_hz) / HWConfig.FREQ
        if calibrated:
            target = await self._outputCalib(ch, amplitude, is_ac, frequency, snapshot)
        else:
            scale = (HWConfig.CURRENT if ch in HWConfig.I_CHANNELS else HWConfig.VOLTAGE) / 65536
            reg16 = round(amplitude * (math.sqrt(2) if is_ac else 1) * scale)
            if is_ac:
                await self._outputAc(ch, reg16, frequency)
            else:
                await self._outputDc(ch, reg16)
            target = reg16 / scale / (math.sqrt(2) if is_ac else 1)
        self._checkStop()
        impedance = self._shuntImpedance(frequency, is_ac) if ch in HWConfig.I_CHANNELS and curr_mode == 0 else None
        self._setAmplifier(True)
        started = time.monotonic()
        details = {"ch": HWConfig.UnmapChannel(ch), "frequencyHz": frequency if is_ac else 0.0,
                   "stage": "verify" if calibrated else "measure"}
        while time.monotonic() - started < settle_sec:
            self._checkStop()
            self._sendStatus(min(99, int(progress_start)), **details)
            await asyncio.sleep(0.2)
        samples = []
        last_raw = None
        last_error = None
        nonpositive_count = 0
        started = time.monotonic()
        # 20 PLC/auto-zero and USB latency can yield fewer than five readings
        # during the legacy 3 s verify window. Allow bounded time to finish them.
        sample_limit = sample_sec + max(5.0, minimum * 1.0)
        while (time.monotonic() - started < sample_sec or
               (len(samples) < minimum and time.monotonic() - started < sample_limit)):
            self._checkStop()
            query_failed = False
            try:
                try:
                    raw = await self._io(self._readDmm, read_command)
                except Exception:
                    query_failed = True
                    raise
                self._checkStop()
                last_raw = str(raw)[:160] if raw is not None else ":MEAS?=FALSE (no new reading)"
                values = self._parseReadings(raw) if raw is not None else []
                if impedance is not None:
                    values = [value / impedance for value in values]
                if is_ac:
                    nonpositive_count += sum(value <= 0 for value in values)
                    values = [value for value in values if value > 0]
                samples.extend(values)
                value = round(values[-1], 6) if values else None
            except _Stopped:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("DMM read error: %s; raw=%r; %s", context, last_raw, last_error)
                if query_failed and self._dmmModel in self._RIGOL_NATIVE_MODELS | self._KEYSIGHT_MODELS:
                    # A timed-out query may still be pending in the instrument.
                    # Do not pile more queries onto it while current flows.
                    # Discard this point even if earlier readings were valid.
                    self._setAmplifier(False)
                    await self._io(self._closeDmm)
                    raise RuntimeError(f"DMM query failed; {context}; {last_error}; "
                                       "output disabled, VISA session closed") from exc
                value = None
            fraction = min(1.0, (time.monotonic() - started) / sample_sec)
            self._sendStatus(min(99, int(progress_start + progress_span * fraction)), value, **details)
            await asyncio.sleep(0.2)
        self._setAmplifier(False)
        self._checkStop()
        if len(samples) < minimum:
            raise ValueError(f"Too few valid samples: {len(samples)} < {minimum}; {context}; "
                             f"last_raw={last_raw!r}; AC zero/nonpositive readings={nonpositive_count}; "
                             f"last_error={last_error or 'none'}")
        mean = statistics.mean(samples)
        variance = statistics.pvariance(samples, mean)
        logger.info("Cal ch=%s f=%.6f target=%.8f mean=%.8f var=%.3e N=%s",
                    ch, frequency if is_ac else 0, target, mean, variance, len(samples))
        return mean, variance, len(samples), target

    def _commands(self, ch, curr_mode, is_ac, amplitude, frequency_hz=50.0):
        suffix = "AC" if is_ac else "DC"
        if self._dmmModel in self._KEYSIGHT_MODELS and ch in HWConfig.I_CHANNELS and curr_mode != 0 and abs(amplitude) > 3:
            raise ValueError(f"{self._dmmModel} direct-current measurement is limited to 3 A (RMS for AC)")
        if self._dmmModel in self._RIGOL_NATIVE_MODELS:
            # DM3058/E and DM3068 native MEAS parameters are indices, NOT V/A.
            # Keep direct current on the 2/10 A high-current ranges, including
            # DC zero (DM3068's lower ranges have a separate 250 mA fuse).
            direct_current = ch in HWConfig.I_CHANNELS and curr_mode != 0
            value = abs(amplitude)
            if ch in HWConfig.I_CHANNELS and not direct_current:
                value *= self._shuntImpedance(frequency_hz, is_ac)
            ranges = (2, 10) if direct_current else (0.2, 2, 20, 200, 750 if is_ac else 1000)
            selected = next((r for r in ranges if value <= r), None)
            if selected is None:
                raise ValueError(f"Measurement exceeds {self._dmmModel} range")
            if not is_ac and amplitude == 0 and ch not in HWConfig.I_CHANNELS:
                selected = 2  # DC offset headroom; direct current already uses 2 A.
            quantity = "CURR" if direct_current else "VOLT"
            range_index = (4 if selected == 2 else 5) if direct_current else ranges.index(selected)
            if direct_current and is_ac and self._dmmModel in {"DM3058", "DM3058E"}:
                range_index -= 2  # DM3058/E AC has no 200 uA / 2 mA ranges.
            return [f":FUNC:{quantity}:{suffix}", ":MEAS MANU",
                    f":MEAS:{quantity}:{suffix} {range_index}"]
        if ch in HWConfig.I_CHANNELS:
            if curr_mode == 0:
                voltage = abs(amplitude) * self._shuntImpedance(frequency_hz, is_ac)
                ranges = (0.1, 1, 10, 100, 750 if is_ac else 1000)
                selected = next((r for r in ranges if voltage * 1.05 <= r * (1.2 if r != ranges[-1] else 1)), None)
                if selected is None:
                    raise ValueError("Shunt voltage exceeds multimeter range")
                return [f":CONF:VOLT:{suffix} {selected}"]
            if not is_ac and amplitude == 0:
                return [":CONF:CURR:DC 0.1"]  # Retain legacy zero-offset resolution.
            high_range = 10 if self._dmmModel == "DM858" else 3
            return [f":CONF:CURR:{suffix} {1 if abs(amplitude) <= 1 else high_range}"]
        if is_ac:
            return [f":CONF:VOLT:AC {10 if abs(amplitude) <= 10 else 100 if abs(amplitude) <= 100 else 750}"]
        return [f":CONF:VOLT:DC {1 if amplitude == 0 else 10 if abs(amplitude) <= 10 else 100 if abs(amplitude) <= 100 else 1000}"]

    async def _runCalibration(self, ch, curr_mode):
        is_current = ch in HWConfig.I_CHANNELS
        ac = 0.5 if is_current else 57.735
        dc = 0.5 if is_current else 100.0
        results = []
        for index, (is_ac, amplitude) in enumerate(((True, ac), (False, 0.0), (False, dc), (False, -dc))):
            results.append(await self._measurePoint(
                ch, curr_mode, is_ac, amplitude, self._commands(ch, curr_mode, is_ac, amplitude),
                sample_sec=30 if is_current else 10, progress_start=index * 24, progress_span=24,
            ))
        ac_result, zero, positive, negative = results
        gain, offset = self._wls_fit(
            positive[3], negative[3], zero[3], positive[0], negative[0], zero[0],
            positive[1], negative[1], zero[1],
        )
        ac_gain = ac_result[0] / ac_result[3]
        if not 0.5 <= abs(gain) <= 1.5 or not 0.5 <= ac_gain <= 1.5:
            raise ValueError(f"Abnormal gains: DC={gain}, AC={ac_gain}; check amplifier/wiring")
        self._checkStop()
        self._saveCalib(ch, -offset / gain, 1 / gain, 1 / ac_gain)
        self._sendStatus(100, revision=calib.GetSnapshot().revision)

    def _loadFrequencyData(self):
        try:
            with open(self._frequency_path, encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            data = Calibration.EmptyFrequencyData()
        Calibration.ValidateFrequencyData(data)
        return data

    def _saved(self):
        if not calib.LoadCalib():
            raise RuntimeError("Saved calibration could not be reloaded")
        if self._onSaved:
            self._onSaved()

    def _saveCalib(self, ch, bias, f_dc, f_ac):
        # Never replace all other channels with {} on a read failure.
        with open(self._calib_path, encoding="utf-8") as stream:
            original = json.load(stream)
        Calibration.ValidateLegacy(original)
        self._loadFrequencyData()
        data = copy.deepcopy(original)
        data[str(ch)] = [round(bias, 8), round(f_dc, 8), round(f_ac, 8)]
        Calibration.ValidateLegacy(data)
        Calibration.AtomicWriteJson(self._calib_path + ".bak", original)
        Calibration.AtomicWriteJson(self._calib_path, data)
        self._saved()

    def _frequencies(self, default=None):
        values = self._options.get("frequenciesHz", default if default is not None else self.DEFAULT_FREQUENCIES)
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            raise ValueError("frequenciesHz needs at least two points")
        requested = sorted({Calibration._number(value, "frequency") for value in values})
        if len(requested) < 2 or not 0 < requested[0] or requested[-1] > Calibration.MAX_CALIBRATION_HZ + 1 / HWConfig.FREQ:
            raise ValueError("Frequency points must be distinct and in (0, 3000] Hz")
        quantized = [HWConfig.ConvertFreqToReg(value) / HWConfig.FREQ for value in requested]
        if quantized[0] < 20 - 1 / HWConfig.FREQ or any(a >= b for a, b in zip(quantized, quantized[1:])):
            raise ValueError("AC points must be >= 20 Hz and distinct after DDS quantization")
        return quantized

    def _frequencyOptions(self, ch, curr_mode):
        if self._dmmModel not in self._RIGOL_NATIVE_MODELS | self._KEYSIGHT_MODELS | {"DM858", "DM858E"}:
            raise ValueError(f"Frequency calibration requires RIGOL DM858/DM858E/DM3058/DM3058E/DM3068 or Agilent/Keysight 34410A/34461A; connected *IDN?={self._dmmIdn!r}")
        amplitude = Calibration._number(self._options.get("referenceRms", 0.5 if ch in HWConfig.I_CHANNELS else 57.735), "referenceRms")
        if amplitude <= 0:
            raise ValueError("referenceRms must be positive")
        if ch in HWConfig.I_CHANNELS and curr_mode != 0:
            maximum = 3 if self._dmmModel in self._KEYSIGHT_MODELS | {"DM858E"} else 7
            if amplitude > maximum:
                raise ValueError(f"Automatic direct-current measurement is limited to {maximum} A RMS")
        settle = Calibration._number(self._options.get("settleMs", 5000), "settleMs") / 1000
        sample = Calibration._number(self._options.get("sampleMs", 30000 if ch in HWConfig.I_CHANNELS else 10000), "sampleMs") / 1000
        tolerance = Calibration._number(self._options.get("maxErrorPct", 2.0), "maxErrorPct")
        if settle < 3.1 or sample <= 0 or tolerance <= 0:
            raise ValueError("Need settleMs >= 3100, sampleMs > 0, maxErrorPct > 0")
        return amplitude, settle, sample, tolerance

    async def _runFrequencyCalibration(self, ch, curr_mode):
        frequencies = self._frequencies()
        amplitude, settle, sample, tolerance = self._frequencyOptions(ch, curr_mode)
        points = []
        for index, frequency in enumerate(frequencies):
            mean, variance, count, target = await self._measurePoint(
                ch, curr_mode, True, amplitude,
                self._commands(ch, curr_mode, True, amplitude, frequency), frequency_hz=frequency,
                settle_sec=settle, sample_sec=sample,
                progress_start=index * 50 / len(frequencies), progress_span=50 / len(frequencies),
            )
            gain = mean / target
            if not math.isfinite(gain) or not 0.2 <= gain <= 2.0:
                raise ValueError(f"Abnormal AC gain {gain} at {frequency} Hz")
            points.append({"freqHz": frequency, "factorAc": 1 / gain,
                           "measuredRms": mean, "rawTargetRms": target,
                           "variance": variance, "sampleCount": count})
        candidate = calib.WithAcCurve(ch, points)
        for index, frequency in enumerate(frequencies):
            result = await self._measurePoint(
                ch, curr_mode, True, amplitude,
                self._commands(ch, curr_mode, True, amplitude, frequency), frequency_hz=frequency,
                calibrated=True, snapshot=candidate, settle_sec=settle, sample_sec=sample,
                progress_start=50 + index * 49 / len(frequencies), progress_span=49 / len(frequencies),
            )
            error = self._reportVerification(ch, True, amplitude, frequency, result)
            if error > tolerance:
                raise ValueError(f"Verification failed at {frequency} Hz: {error:.3f}% > {tolerance}%")
        self._checkStop()
        data = self._loadFrequencyData()
        original = copy.deepcopy(data)
        data["channels"][str(ch)] = {
            "points": points, "referenceRms": amplitude,
            "measurement": {
                "mode": "current_shunt" if ch in HWConfig.I_CHANNELS and curr_mode == 0 else "current_direct" if ch in HWConfig.I_CHANNELS else "voltage_direct",
                "instrumentModel": self._dmmModel, "instrumentIdn": self._dmmIdn,
                "shuntProfile": self._options.get("shuntProfile"),
                "shuntOhms": self._options.get("shuntOhms", 0.075),
                "shuntAcModel": "supplied_impedance" if (self._options.get("shuntProfile") or {}).get("points") else "assumed_resistive",
                "loadDescription": self._options.get("loadDescription"),
                "completedUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        }
        Calibration.ValidateFrequencyData(data)
        with open(self._calib_path, encoding="utf-8") as stream:
            Calibration.ValidateLegacy(json.load(stream))
        Calibration.AtomicWriteJson(self._frequency_path + ".bak", original)
        Calibration.AtomicWriteJson(self._frequency_path, data)
        self._saved()
        self._sendStatus(100, stage="saved", revision=calib.GetSnapshot().revision)

    def _reportVerification(self, ch, is_ac, requested, frequency, result):
        mean, variance, count, target = result
        error = abs(mean - target) / abs(target) * 100 if target else 0.0
        self._send({
            "module": "autoCal", "type": "verifyPoint",
            "target": round(target, 4), "measured": round(mean, 4), "error": round(error, 2),
            "requested": requested, "frequencyHz": frequency if is_ac else 0.0,
            "ch": HWConfig.UnmapChannel(ch), "unit": "A" if ch in HWConfig.I_CHANNELS else "V",
            "amplitudeConvention": "rms" if is_ac else "dc",
            "sampleCount": count, "variance": variance,
        })
        return error

    async def _startVerify(self, msg, *, _reserved_ch=None):
        ch = self._begin(msg) if _reserved_ch is None else _reserved_ch
        if ch is None:
            return
        try:
            self._checkStop()
            if not calib.LoadCalib():
                raise RuntimeError("Cannot verify invalid calibration files")
            if not await self._io(self._connectDmm):
                return
            self._checkStop()
            mode = msg.get("calibrationMode", "ac_frequency" if "frequenciesHz" in msg else "legacy")
            if mode == "legacy":
                await self._runVerify(ch, msg.get("currMode", 0))
            elif mode == "ac_frequency":
                snapshot = calib.GetSnapshot()
                curve = snapshot.acCurves[ch]
                if not curve:
                    raise ValueError("This channel has no AC frequency calibration")
                frequencies = self._frequencies([point[0] for point in curve])
                amplitude, settle, sample, _ = self._frequencyOptions(ch, msg.get("currMode", 0))
                for index, frequency in enumerate(frequencies):
                    if not curve[0][0] <= frequency <= curve[-1][0]:
                        raise ValueError("Verification frequency is outside the calibrated range")
                    result = await self._measurePoint(
                        ch, msg.get("currMode", 0), True, amplitude,
                        self._commands(ch, msg.get("currMode", 0), True, amplitude, frequency),
                        frequency_hz=frequency, calibrated=True, snapshot=snapshot,
                        settle_sec=settle, sample_sec=sample,
                        progress_start=index * 99 / len(frequencies), progress_span=99 / len(frequencies),
                    )
                    self._reportVerification(ch, True, amplitude, frequency, result)
                self._sendStatus(100, stage="verified", revision=snapshot.revision)
            else:
                raise ValueError("calibrationMode must be legacy or ac_frequency")
        except _Stopped:
            self._sendStatus(0, stage="stopped")
        except Exception as exc:
            logger.error("Verify error: %s", exc, exc_info=True)
            self._sendError(f"Verify exception: {exc}")
        finally:
            await self._finish()

    async def _runVerify(self, ch, curr_mode):
        levels = (0.2, 1.1, 2.0) if ch in HWConfig.I_CHANNELS else (10.0, 60.0, 110.0)
        points = [(True, value) for value in levels]
        points.extend((False, value * sign) for value in levels for sign in (1, -1))
        snapshot = calib.GetSnapshot()
        for index, (is_ac, amplitude) in enumerate(points):
            result = await self._measurePoint(
                ch, curr_mode, is_ac, amplitude, self._commands(ch, curr_mode, is_ac, amplitude),
                calibrated=True, snapshot=snapshot, sample_sec=3.0,
                progress_start=index * 99 / len(points), progress_span=99 / len(points),
            )
            self._reportVerification(ch, is_ac, amplitude, 50.0, result)
        self._sendStatus(100, stage="verified", revision=snapshot.revision)
