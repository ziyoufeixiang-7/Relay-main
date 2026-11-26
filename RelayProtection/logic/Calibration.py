import hashlib
import json
import logging
import math
import os
import stat
import sys
import tempfile
from bisect import bisect_left
from dataclasses import dataclass, replace
from typing import Tuple

from logic.FPGACodec import HWConfig

logger = logging.getLogger("Calibration")


@dataclass(frozen=True)
class CalibrationSnapshot:
    biases: tuple
    factorsDc: tuple
    factorsAc: tuple
    acCurves: tuple
    revision: str


class Calibration:
    """Convert DC values / AC peaks, not RMS. Curves use absolute factors.

    The legacy file stays [bias, factorDc, factorAc] per FPGA channel.
    Frequency/waveform context belongs to the caller, never global state.
    """

    MAX_CALIBRATION_HZ = 3000.0

    def __init__(self, calib_path=None):
        if "__compiled__" in globals():
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
        else:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.calibPath = calib_path or os.path.join(base, "config", "calibration.json")
        self.frequencyPath = os.path.join(os.path.dirname(self.calibPath), "calibration_ac_frequency.json")
        self.calibData = {}
        self.frequencyData = self.EmptyFrequencyData()
        self._warnedRanges = set()
        self._snapshot = None
        self.LoadCalib()

    @staticmethod
    def EmptyFrequencyData():
        return {
            "schemaVersion": 1,
            "channelNumbering": "fpga",
            "amplitudeConvention": "peak",
            "interpolation": "linear_factor",
            "outOfRangePolicy": "clamp_warn",
            "channels": {},
        }

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        return float(value)

    @staticmethod
    def _channel(key):
        channel = int(key)
        if str(channel) != str(key) or not 0 <= channel <= 15:
            raise ValueError(f"Invalid FPGA calibration channel: {key}")
        return channel

    @classmethod
    def ValidateLegacy(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Legacy calibration must be an object")
        for key, entry in data.items():
            cls._channel(key)
            if not isinstance(entry, list) or len(entry) != 3:
                raise ValueError(f"Channel {key} needs [bias, factorDc, factorAc]")
            for value in entry:
                cls._number(value, f"Channel {key} coefficient")
            if entry[1] == 0 or entry[2] <= 0:
                raise ValueError(f"Channel {key}: DC must be nonzero and AC positive")
        if not set(map(str, range(12))).issubset(data):
            raise ValueError("Legacy calibration is missing active FPGA channels")

    @classmethod
    def ValidateFrequencyData(cls, data):
        if not isinstance(data, dict) or data.get("schemaVersion") != 1:
            raise ValueError("Unsupported AC frequency calibration schema")
        for name, expected in (
            ("channelNumbering", "fpga"), ("amplitudeConvention", "peak"),
            ("interpolation", "linear_factor"), ("outOfRangePolicy", "clamp_warn"),
        ):
            if data.get(name, expected) != expected:
                raise ValueError(f"Unsupported {name}")
        channels = data.get("channels")
        if not isinstance(channels, dict):
            raise ValueError("AC frequency channels must be an object")
        curves = [()] * 16
        for key, entry in channels.items():
            channel = cls._channel(key)
            if not isinstance(entry, dict) or not isinstance(entry.get("points"), list):
                raise ValueError(f"Channel {key} needs AC frequency points")
            points = []
            for point in entry["points"]:
                if not isinstance(point, dict):
                    raise ValueError(f"Invalid frequency point in channel {key}")
                freq = cls._number(point.get("freqHz"), "freqHz")
                factor = cls._number(point.get("factorAc"), "factorAc")
                # A quantized 3 kHz endpoint can lie a DDS LSB above 3000.
                if not 0 < freq <= cls.MAX_CALIBRATION_HZ + 1 / HWConfig.FREQ:
                    raise ValueError("AC calibration frequency must be in (0, 3000] Hz")
                if factor <= 0 or (points and freq <= points[-1][0]):
                    raise ValueError("AC factors must be positive; frequencies strictly increasing")
                points.append((freq, factor))
            if len(points) < 2:
                raise ValueError(f"Channel {key} needs at least two frequency points")
            curves[channel] = tuple(points)
        return tuple(curves)

    def LoadCalib(self):
        """Publish a fully validated snapshot; retain previous values on error."""
        try:
            with open(self.calibPath, encoding="utf-8") as stream:
                legacy = json.load(stream)
            self.ValidateLegacy(legacy)
            frequency = self.EmptyFrequencyData()
            try:
                with open(self.frequencyPath, encoding="utf-8") as stream:
                    frequency = json.load(stream)
            except FileNotFoundError:
                pass
            curves = self.ValidateFrequencyData(frequency)
            biases, dc, ac = [0.0] * 16, [-0.95] * 16, [0.95] * 16
            for key, entry in legacy.items():
                biases[int(key)], dc[int(key)], ac[int(key)] = map(float, entry)
            revision = hashlib.sha256(json.dumps(
                [legacy, frequency], sort_keys=True, allow_nan=False
            ).encode()).hexdigest()[:16]
            snapshot = CalibrationSnapshot(tuple(biases), tuple(dc), tuple(ac), curves, revision)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            logger.error("Failed to load calibration; retaining previous parameters: %s", exc)
            return False
        self.calibData, self.frequencyData = legacy, frequency
        self._snapshot = snapshot
        return True

    def GetSnapshot(self):
        if self._snapshot is None:
            raise ValueError("No valid calibration has been loaded; check calibration files")
        return self._snapshot

    def LegacySnapshot(self, snapshot=None):
        """Keep raw diagnostics/fixed-register sweeps on the old coefficients."""
        snapshot = snapshot or self.GetSnapshot()
        if not any(snapshot.acCurves):
            return snapshot
        return replace(snapshot, acCurves=((),) * 16, revision=snapshot.revision + ":legacy")

    def AcFactor(self, chIdx, frequencyHz=50.0, snapshot=None):
        snapshot = snapshot or self.GetSnapshot()
        points = snapshot.acCurves[chIdx]
        if not points:
            return snapshot.factorsAc[chIdx]
        if not math.isfinite(frequencyHz) or frequencyHz <= 0:
            raise ValueError("Nonzero AC output needs a positive fundamental frequency")
        index = bisect_left(points, (frequencyHz, -math.inf))
        if index == 0 or index == len(points):
            endpoint = points[0] if index == 0 else points[-1]
            if abs(frequencyHz - endpoint[0]) > 1 / HWConfig.FREQ:
                warning = (snapshot.revision, chIdx, "low" if index == 0 else "high")
                if warning not in self._warnedRanges:
                    self._warnedRanges.add(warning)
                    logger.warning("Channel %s: %.6f Hz outside calibration %.6f..%.6f Hz; clamping factor",
                                   chIdx, frequencyHz, points[0][0], points[-1][0])
            return endpoint[1]
        f0, a0 = points[index - 1]
        f1, a1 = points[index]
        return a0 + (a1 - a0) * (frequencyHz - f0) / (f1 - f0)

    def WithAcCurve(self, chIdx, points, snapshot=None):
        data = self.EmptyFrequencyData()
        data["channels"][str(chIdx)] = {"points": points}
        curve = self.ValidateFrequencyData(data)[chIdx]
        snapshot = snapshot or self.GetSnapshot()
        curves = list(snapshot.acCurves)
        curves[chIdx] = curve
        return replace(snapshot, acCurves=tuple(curves))

    @staticmethod
    def AtomicWriteJson(path, data):
        """An interrupted write must not truncate device calibration data."""
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".calibration-", suffix=".tmp", dir=directory)
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o644
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=4, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _ampToReg(amplitude, chIdx):
        if not math.isfinite(amplitude):
            raise ValueError("Calibrated amplitude must be finite")
        scale = HWConfig.CURRENT if chIdx in HWConfig.I_CHANNELS else HWConfig.VOLTAGE
        value = round(amplitude * scale)
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError(f"Channel {chIdx}: calibrated amplitude exceeds signed 32-bit range")
        return value & 0xFFFFFFFF

    def PhysToReg(self, chIdx: int, layerIdx: int, amplitude: float,
                  freqOrPhase: float, isDelta: bool = False,
                  fundamentalHz: float = 50.0, snapshot=None) -> Tuple[int, int]:
        snapshot = snapshot or self.GetSnapshot()
        if layerIdx == 0:
            amplitude = amplitude * snapshot.factorsDc[chIdx]
            if not isDelta:
                amplitude += snapshot.biases[chIdx]
            return self._ampToReg(amplitude, chIdx), HWConfig.ConvertFreqToReg(freqOrPhase)
        # Existing sweep APIs repeat a fixed delta. Do not retrofit frequency
        # compensation into that path: this change is for absolute static values.
        factor = (snapshot.factorsAc[chIdx] if isDelta else
                  self.AcFactor(chIdx, layerIdx * fundamentalHz, snapshot)) if amplitude else 1.0
        return (
            self._ampToReg(amplitude * factor, chIdx),
            HWConfig.ConvertPhaseToReg(freqOrPhase + (0.0 if isDelta else 180.0)),
        )

    def RegToPhys(self, chIdx: int, layerIdx: int, aReg32: int, pRegU32: int,
                  fundamentalHz: float = 50.0, snapshot=None, rounded=True) -> Tuple[float, float]:
        snapshot = snapshot or self.GetSnapshot()
        if aReg32 & 0x80000000:
            aReg32 -= 0x100000000
        amplitude = aReg32 / (HWConfig.CURRENT if chIdx in HWConfig.I_CHANNELS else HWConfig.VOLTAGE)
        if layerIdx == 0:
            amplitude = (amplitude - snapshot.biases[chIdx]) / snapshot.factorsDc[chIdx]
            second = pRegU32 / HWConfig.FREQ
        else:
            if amplitude:
                amplitude /= self.AcFactor(chIdx, layerIdx * fundamentalHz, snapshot)
            second = (((pRegU32 >> 16) & 0xFFFF) ^ 0x8000) / HWConfig.PHASE
        return (round(amplitude, 3), round(second, 2)) if rounded else (amplitude, second)


calib = Calibration()
