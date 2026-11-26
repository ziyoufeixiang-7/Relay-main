"""Calibration mathematics and simulated RIGOL/34410A/34461A I/O; no hardware."""
import asyncio
import copy
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.BaseApi import BaseApi
import Updater
from logic.AutoCal import AutoCal, _Stopped
from logic.Calibration import Calibration
from logic.EngineCtrl import EngineCtrl
from logic.EngineBridge import EngineBridge
from logic.FPGACodec import HWConfig, HWCodec
from logic.USEEngine import USEEngine


class _Files:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="relay-calibration-test-")
        self.path = Path(self.directory.name) / "calibration.json"
        self.legacy = {str(ch): [0.001 * ch, -0.95, 0.95] for ch in range(16)}
        Calibration.AtomicWriteJson(str(self.path), self.legacy)
        self.calib = Calibration(str(self.path))

    def curve(self, channel=0, points=None):
        data = Calibration.EmptyFrequencyData()
        data["channels"][str(channel)] = {"points": points or [
            {"freqHz": 50.0, "factorAc": 1.0},
            {"freqHz": 150.0, "factorAc": 1.2},
            {"freqHz": 1000.0, "factorAc": 1.3},
        ]}
        Calibration.AtomicWriteJson(self.calib.frequencyPath, data)
        self.calib.LoadCalib()


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.files = _Files()
        self.addCleanup(self.files.directory.cleanup)
        self.calib = self.files.calib

    def test_legacy_encoding_is_unchanged_without_curves(self):
        for ch in range(12):
            for layer in (0, 1, 2, 64):
                for delta in (False, True):
                    for amplitude in (-10.0, 0.0, 0.02, 10.0):
                        second = 50.0 if layer == 0 else -120.0
                        expected_amp = amplitude * (-0.95 if layer == 0 else 0.95)
                        if layer == 0 and not delta:
                            expected_amp += self.files.legacy[str(ch)][0]
                        expected = (
                            HWConfig.ConvertAmpToReg(expected_amp, ch in HWConfig.I_CHANNELS),
                            HWConfig.ConvertFreqToReg(second) if layer == 0 else HWConfig.ConvertPhaseToReg(second + (0 if delta else 180)),
                        )
                        self.assertEqual(self.calib.PhysToReg(ch, layer, amplitude, second, delta), expected)

    def test_dc_bias_is_not_added_to_delta(self):
        absolute = self.calib.PhysToReg(1, 0, 0, 50)[0]
        delta = self.calib.PhysToReg(1, 0, 0, 0, True)[0]
        self.assertNotEqual(absolute, 0)
        self.assertEqual(delta, 0)

    def test_interpolation_and_endpoint_clamping(self):
        self.files.curve()
        self.assertAlmostEqual(self.calib.AcFactor(0, 100), 1.1)
        self.assertEqual(self.calib.AcFactor(0, 50), 1)
        self.assertEqual(self.calib.AcFactor(0, 150), 1.2)
        self.assertEqual(self.calib.AcFactor(0, 20), 1)
        self.assertEqual(self.calib.AcFactor(0, 3200), 1.3)
        self.assertEqual(self.calib.AcFactor(1, 150), 0.95)

    def test_harmonic_frequency_and_round_trip(self):
        self.files.curve()
        amp, phase = self.calib.PhysToReg(0, 3, 10, -30, fundamentalHz=50)
        self.assertEqual(amp, HWConfig.ConvertAmpToReg(12))
        physical = self.calib.RegToPhys(0, 3, amp, phase, fundamentalHz=50, rounded=False)
        self.assertAlmostEqual(physical[0], 10, places=6)
        self.assertAlmostEqual(physical[1], 330, delta=0.006)

    def test_delta_path_retains_legacy_sweep_coefficient(self):
        self.files.curve()
        value = self.calib.PhysToReg(0, 3, 1, 0, True, fundamentalHz=50)[0]
        self.assertEqual(value, HWConfig.ConvertAmpToReg(0.95))

    def test_zero_harmonic_does_not_require_positive_frequency(self):
        self.files.curve()
        self.assertEqual(self.calib.PhysToReg(0, 64, 0, 0, fundamentalHz=0)[0], 0)
        with self.assertRaises(ValueError):
            self.calib.PhysToReg(0, 1, 1, 0, fundamentalHz=0)

    def test_overflow_is_rejected_before_unsigned_mask(self):
        with self.assertRaises(ValueError):
            self.calib.PhysToReg(0, 1, 1000, 0)

    def test_invalid_load_retains_entire_snapshot(self):
        snapshot = self.calib.GetSnapshot()
        bad = copy.deepcopy(self.files.legacy)
        bad["10"][1] = 0
        Calibration.AtomicWriteJson(str(self.files.path), bad)
        self.assertFalse(self.calib.LoadCalib())
        self.assertIs(snapshot, self.calib.GetSnapshot())

    def test_invalid_curve_does_not_partially_publish(self):
        snapshot = self.calib.GetSnapshot()
        data = Calibration.EmptyFrequencyData()
        data["channels"]["0"] = {"points": [{"freqHz": 55, "factorAc": 1}, {"freqHz": 50, "factorAc": 1}]}
        Calibration.AtomicWriteJson(self.calib.frequencyPath, data)
        self.assertFalse(self.calib.LoadCalib())
        self.assertIs(snapshot, self.calib.GetSnapshot())

    def test_invalid_initial_calibration_cannot_output_default_coefficients(self):
        Calibration.AtomicWriteJson(str(self.files.path), {"0": [0, -1, 1]})
        invalid = Calibration(str(self.files.path))
        with self.assertRaisesRegex(ValueError, "No valid calibration"):
            invalid.PhysToReg(0, 1, 10, 0)

    def test_nan_write_leaves_original_file_and_no_temp(self):
        before = self.files.path.read_bytes()
        with self.assertRaises(ValueError):
            Calibration.AtomicWriteJson(str(self.files.path), {"bad": float("nan")})
        self.assertEqual(before, self.files.path.read_bytes())
        self.assertEqual(list(self.files.path.parent.glob(".calibration-*.tmp")), [])

    def test_updater_preserves_both_device_calibration_files(self):
        self.files.curve()
        destination = self.files.path.parent / "new-install"
        destination.mkdir()
        source = self.files.path.parent / "old-install"
        (source / "config").mkdir(parents=True)
        old_legacy = source / "config" / "calibration.json"
        old_curve = source / "config" / "calibration_ac_frequency.json"
        Calibration.AtomicWriteJson(str(old_legacy), self.files.legacy)
        Calibration.AtomicWriteJson(str(old_curve), self.calib.frequencyData)
        Updater._copyProtectedFiles(str(source), str(destination))
        self.assertEqual((destination / "config" / old_legacy.name).read_bytes(), old_legacy.read_bytes())
        self.assertEqual((destination / "config" / old_curve.name).read_bytes(), old_curve.read_bytes())

    def test_first_upgrade_keeps_old_coefficients_and_installs_empty_frequency_file(self):
        source = self.files.path.parent / "old-device"
        staged = self.files.path.parent / "unpacked-update"
        (source / "config").mkdir(parents=True)
        (staged / "config").mkdir(parents=True)
        Calibration.AtomicWriteJson(str(source / "config/calibration.json"), self.files.legacy)
        Calibration.AtomicWriteJson(str(staged / "config/calibration.json"), {"0": [0, -1, 1]})
        Calibration.AtomicWriteJson(str(staged / "config/calibration_ac_frequency.json"), Calibration.EmptyFrequencyData())
        # Also test the old installed updater: it only knows calibration.json.
        for protected in (["config/calibration.json"], Updater.protectedFiles):
            with patch.object(Updater, "protectedFiles", protected):
                Updater._copyProtectedFiles(str(source), str(staged))
            installed = Calibration(str(staged / "config/calibration.json"))
            self.assertEqual(installed.GetSnapshot(), self.calib.GetSnapshot())
            self.assertEqual(installed.frequencyData["channels"], {})

    def test_base_api_maps_channels_and_uses_each_channels_frequency(self):
        self.files.curve(channel=1)
        with patch("api.BaseApi.calib", self.calib):
            api = BaseApi()
            result = api.physDictToReg({"6": {"3": [10, 0], "0": [0, 50]}})
        self.assertIn(1, result)
        frequency = HWConfig.ConvertFreqToReg(50) / HWConfig.FREQ
        factor = self.calib.AcFactor(1, 3 * frequency)
        self.assertEqual(result[1][3][0], HWConfig.ConvertAmpToReg(10 * factor, True))

    def test_static_frequency_only_update_preserves_existing_harmonic(self):
        self.files.curve()
        with patch("api.BaseApi.calib", self.calib):
            api = BaseApi()
            state = {0: {0: list(self.calib.PhysToReg(0, 0, 0, 50)),
                         1: list(self.calib.PhysToReg(0, 1, 10, 30))}}
            api.ctrl = type("Ctrl", (), {"state": state})()
            result = api.physDictToReg({"0": {"0": [0, 150]}})
        self.assertIn(1, result[0])
        self.assertAlmostEqual(result[0][1][0] / HWConfig.VOLTAGE, 12, places=6)

    def test_frontend_reverse_conversion_uses_reset_frequency(self):
        self.files.curve()
        ctrl = object.__new__(EngineCtrl)
        ctrl.activeApi = None
        ctrl.state = {0: {0: list(self.calib.PhysToReg(0, 0, 0, 50))}}
        messages = []
        ctrl._send = messages.append
        reset = {0: {1: list(self.calib.PhysToReg(0, 1, 10, 0, fundamentalHz=150)),
                     0: list(self.calib.PhysToReg(0, 0, 0, 150))}}
        with patch("logic.EngineCtrl.calib", self.calib):
            ctrl.sendValueUpdate(reset)
        self.assertEqual(messages[0]["static"]["0"]["1"][0], 10)

    def test_api_snapshot_does_not_change_after_reload(self):
        with patch("api.BaseApi.calib", self.calib):
            api = BaseApi()
            before = api.physDictToReg({"0": {"0": [0, 150], "1": [10, 0]}})
            self.files.curve()
            after = api.physDictToReg({"0": {"0": [0, 150], "1": [10, 0]}})
        self.assertEqual(before, after)


class _Dmm:
    def __init__(self):
        self.commands = []
        self.range = 1.0
        self.errors = []
        self.nplc = 20

    def write(self, command):
        self.commands.append(command)
        if command.startswith(":CONF:"):
            self.range = float(command.split()[1])

    def query(self, command):
        if command == "*IDN?":
            return "RIGOL,DM858,MOCK,1.0"
        if command == ":TRIG:SOUR?":
            return "IMM"
        if command == ":SYST:ERR?":
            return self.errors.pop(0) if self.errors else '+0,"No error"'
        if command.endswith(":NPLC?"):
            return str(self.nplc)
        if command.endswith(":RANG?"):
            return str(self.range)
        raise AssertionError(f"Unexpected mock command {command}")


class AutoCalibrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.files = _Files()
        self.addCleanup(self.files.directory.cleanup)
        self.patch = patch("logic.AutoCal.calib", self.files.calib)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        settle = patch("logic.AutoCal.time.sleep")
        settle.start()
        self.addCleanup(settle.stop)
        self.messages, self.frames, self.amplifier = [], [], []

        async def transmit(frames):
            self.frames.extend(frames)
            return 123

        self.auto = AutoCal(self.messages.append, self.frames.append, self.amplifier.append, send_frames=transmit)
        self.auto._calib_path = str(self.files.path)
        self.auto._frequency_path = self.files.calib.frequencyPath
        self.auto._dmm = _Dmm()
        self.auto._dmmModel = "DM858"
        self.auto._dmmIdn = "RIGOL,DM858,MOCK,1.0"

    async def test_ac_output_explicitly_writes_frequency(self):
        await self.auto._outputAc(0, 100, 155)
        amp, freq = struct.unpack(">II", self.frames[0][5:13])
        self.assertEqual(self.frames[0][2], 0xFF)
        self.assertEqual(amp, 0)
        self.assertEqual(freq, HWConfig.ConvertFreqToReg(155))
        self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_UPDATE)

    async def test_calibrated_output_uses_peak_factor_and_phase(self):
        self.files.curve()
        target = await self.auto._outputCalib(0, 10, True, 150)
        amp, phase = struct.unpack(">II", self.frames[1][5:13])
        self.assertEqual(amp, HWConfig.ConvertAmpToReg(10 * math.sqrt(2) * 1.2) & 0xFFFF0000)
        self.assertEqual(phase, HWConfig.ConvertPhaseToReg(180))
        self.assertAlmostEqual(target, 10, delta=0.01)

    async def test_dm858_uses_20_plc_and_10a_instead_of_3a(self):
        self.auto._configureDmm([":CONF:CURR:DC 3"], False)
        self.assertIn(":CONF:CURR:DC 10", self.auto._dmm.commands)
        self.assertIn(":SENS:CURR:DC:NPLC 20", self.auto._dmm.commands)
        self.assertNotIn(":SENS:CURR:DC:NPLC 10", self.auto._dmm.commands)
        self.assertNotIn(":CALC:SCAL:STAT OFF", self.auto._dmm.commands)

    async def test_dm858e_retains_3a_range(self):
        self.auto._dmmModel = "DM858E"
        self.auto._configureDmm([":CONF:CURR:AC 3"], True)
        self.assertIn(":CONF:CURR:AC 3", self.auto._dmm.commands)

    async def test_legacy_direct_current_zero_uses_100ma_range(self):
        self.assertEqual(self.auto._commands(1, 1, False, 0), [":CONF:CURR:DC 0.1"])

    async def test_dm858_and_dm858e_generated_ranges_are_documented(self):
        # Explicit oracle from manual 3.10.3/4/11/12, not the implementation.
        for model, current_max in (("DM858", 10), ("DM858E", 3)):
            self.auto._dmmModel = model
            for ch in (0, 1):
                for curr_mode in (0, 1):
                    for is_ac in (False, True):
                        for amplitude in ((0, 0.2, 0.5, 1.1, 2) if ch == 1 else (0, 10, 60, 110)):
                            with self.subTest(model=model, ch=ch, curr_mode=curr_mode, ac=is_ac, value=amplitude):
                                command = self.auto._commands(ch, curr_mode, is_ac, amplitude)[0]
                                allowed = ((0.0001, 0.001, 0.01, 0.1, 1, current_max)
                                           if ":CURR:" in command else (0.1, 1, 10, 100, 750 if is_ac else 1000))
                                self.assertIn(float(command.split()[1]), allowed)

    async def test_configuration_uses_legacy_write_settle_flow_without_queries(self):
        self.auto._dmm.query = Mock(side_effect=AssertionError("No configuration queries in legacy flow"))
        for quantity in ("VOLT", "CURR"):
            for is_ac in (False, True):
                with self.subTest(quantity=quantity, ac=is_ac):
                    self.auto._dmm.commands.clear()
                    suffix = "AC" if is_ac else "DC"
                    commands = [f":CONF:{quantity}:{suffix} 0.1"]
                    with patch("logic.AutoCal.time.sleep") as settle:
                        self.auto._configureDmm(commands, is_ac)
                    expected = commands + ([] if is_ac else [f":SENS:{quantity}:DC:NPLC 20"])
                    self.assertEqual(self.auto._dmm.commands, expected)
                    settle.assert_called_once_with(0.5)
        self.auto._dmm.query.assert_not_called()

    async def test_coerced_1v_range_does_not_block_sampling_or_rescale_reading(self):
        original_write = self.auto._dmm.write

        def write(command):
            original_write(command)
            if command == ":CONF:VOLT:AC 0.1":
                self.auto._dmm.range = 1.0  # Reproduce the field observation.

        def query(command):
            if command == ":READ?":
                return "0.0375,0.0375,0.0375,0.0375,0.0375"
            raise AssertionError(f"Unexpected extra query: {command}")

        self.auto._dmm.write = write
        self.auto._dmm.query = query
        result = await self.auto._measurePoint(
            1, 0, True, 0.5, [":CONF:VOLT:AC 0.1"], settle_sec=0, sample_sec=0.001,
        )
        self.assertEqual(self.auto._dmm.range, 1.0)
        self.assertEqual(result[:3], (0.5, 0, 5))
        self.assertEqual(self.amplifier, [False, True, False])

    async def test_error_queue_does_not_add_a_new_gate_to_legacy_flow(self):
        self.auto._dmm.errors = ['-224,"Illegal parameter value"', '+0,"No error"']
        self.auto._configureDmm([":CONF:VOLT:AC 100"], True)
        self.assertEqual(len(self.auto._dmm.errors), 2)

    async def test_nplc_readback_does_not_add_a_new_gate_to_legacy_flow(self):
        self.auto._dmm.nplc = 5
        self.auto._configureDmm([":CONF:VOLT:DC 100"], False)
        self.assertIn(":SENS:VOLT:DC:NPLC 20", self.auto._dmm.commands)

    async def test_stop_disables_amplifier_before_query_finishes(self):
        self.auto._running = True
        self.auto._stop()
        self.assertEqual(self.amplifier, [False])
        with self.assertRaises(_Stopped):
            await self.auto._outputAc(0, 100, 55)
        self.assertEqual(self.frames, [])

    async def test_stop_and_failed_ack_cannot_enable_output(self):
        async def failed(_frames):
            return -1
        self.auto._sendFramesFunc = failed
        with self.assertRaises(RuntimeError):
            await self.auto._outputAc(0, 100, 55)
        self.assertNotIn(True, self.amplifier)

    async def test_reading_list_filters_invalid_and_overload(self):
        self.assertEqual(self.auto._parseReadings("1.2,NaN,9.9E37,inf,-0.1"), [1.2, -0.1])
        with self.assertRaises(ValueError):
            self.auto._parseReadings("NaN,9.9E37")

    async def test_wls_handles_nonzero_third_x(self):
        gain, offset = self.auto._wls_fit(1, -1, 0.2, 2.3, -1.7, 0.7, 0.1, 0.1, 0.1)
        self.assertAlmostEqual(gain, 2)
        self.assertAlmostEqual(offset, 0.3)

    async def test_legacy_save_preserves_other_channels_and_reloads(self):
        self.auto._saveCalib(1, 0.005, -1.02, 1.01)
        data = json.loads(self.files.path.read_text())
        self.assertEqual(data["1"], [0.005, -1.02, 1.01])
        self.assertEqual(data["0"], self.files.legacy["0"])
        self.assertEqual(self.files.calib.GetSnapshot().factorsAc[1], 1.01)
        self.assertTrue(Path(str(self.files.path) + ".bak").exists())

    async def test_corrupt_legacy_file_is_never_replaced_by_one_channel(self):
        Calibration.AtomicWriteJson(str(self.files.path), {"0": [0, -1, 1]})
        before = self.files.path.read_bytes()
        with self.assertRaises(ValueError):
            self.auto._saveCalib(1, 0, -1, 1)
        self.assertEqual(before, self.files.path.read_bytes())

    async def test_frequency_curve_is_saved_only_after_verification(self):
        self.auto._options = {"frequenciesHz": [55, 155], "referenceRms": 10, "unused": True}
        observations = []

        async def measure(_ch, _mode, _ac, amplitude, _commands, **kwargs):
            observations.append(Path(self.auto._frequency_path).exists())
            if kwargs.get("calibrated"):
                return amplitude, 0.0, 10, amplitude
            gain = 1.05 if kwargs["frequency_hz"] < 100 else 0.9
            return amplitude * gain, 1e-8, 10, amplitude

        self.auto._measurePoint = measure
        before = self.files.path.read_bytes()
        await self.auto._runFrequencyCalibration(0, 0)
        self.assertEqual(observations, [False] * 4)
        self.assertEqual(before, self.files.path.read_bytes())
        points = self.files.calib.frequencyData["channels"]["0"]["points"]
        self.assertAlmostEqual(points[0]["factorAc"], 1 / 1.05)
        self.assertAlmostEqual(points[1]["factorAc"], 1 / 0.9)
        self.assertEqual(self.messages[-1]["progress"], 100)

    async def test_failed_verification_keeps_previous_frequency_table(self):
        self.files.curve()
        before = Path(self.auto._frequency_path).read_bytes()
        self.auto._options = {"frequenciesHz": [55, 155], "referenceRms": 10}

        async def measure(_ch, _mode, _ac, amplitude, _commands, **kwargs):
            return amplitude * (1.1 if kwargs.get("calibrated") else 1.0), 0, 10, amplitude

        self.auto._measurePoint = measure
        with self.assertRaisesRegex(ValueError, "Verification failed"):
            await self.auto._runFrequencyCalibration(0, 0)
        self.assertEqual(before, Path(self.auto._frequency_path).read_bytes())

    async def test_default_verification_accepts_quantized_3khz_endpoint(self):
        self.auto._options = {}
        frequencies = self.auto._frequencies()
        self.assertEqual(frequencies, self.auto._frequencies(frequencies))

    async def test_shunt_value_changes_range_selection(self):
        self.auto._options = {"shuntOhms": 2.0}
        self.assertEqual(self.auto._commands(1, 0, True, 0.5), [":CONF:VOLT:AC 1"])

    async def test_dm858e_frequency_calibration_cannot_exceed_3a_input(self):
        self.auto._dmmModel = "DM858E"
        self.auto._options = {"referenceRms": 4}
        with self.assertRaisesRegex(ValueError, "3 A"):
            self.auto._frequencyOptions(1, 1)

    async def test_shunt_profile_accepts_dds_endpoint_rounding(self):
        self.auto._options = {"shuntProfile": {"dcOhms": 0.075, "points": [
            {"freqHz": 55, "ohmsMagnitude": 0.075},
            {"freqHz": 3000, "ohmsMagnitude": 0.076},
        ]}}
        frequency = HWConfig.ConvertFreqToReg(3000) / HWConfig.FREQ
        self.assertAlmostEqual(self.auto._shuntImpedance(frequency, True), 0.076)

    async def test_running_experiment_prevents_calibration(self):
        self.auto._canStart = lambda: False
        await self.auto._start({"ch": 0})
        self.assertFalse(self.auto.is_running())
        self.assertEqual(self.frames, [])
        self.assertEqual(self.messages[-1]["type"], "calError")

    async def test_real_measurement_flow_with_mock_instrument(self):
        original_query = self.auto._dmm.query
        self.auto._dmm.query = lambda command: "10,10,10,10,10" if command == ":READ?" else original_query(command)
        result = await self.auto._measurePoint(
            0, 0, True, 10, [":CONF:VOLT:AC 100"],
            frequency_hz=155, settle_sec=0, sample_sec=0.001,
        )
        self.assertEqual(result[:3], (10, 0, 5))
        self.assertAlmostEqual(result[3], 10, delta=0.01)
        self.assertEqual(self.amplifier, [False, True, False])

    async def test_slow_single_readings_complete_minimum_after_verify_window(self):
        elapsed = [0.0]
        count = [0]
        original_query = self.auto._dmm.query

        def query(command):
            if command == ":READ?":
                elapsed[0] += 0.9  # Instrument integration/auto-zero plus USB.
                count[0] += 1
                return "10"
            return original_query(command)

        async def sleep(seconds):
            elapsed[0] += seconds

        self.auto._dmm.query = query
        # Patch AutoCal's clock only, not asyncio's or unittest's monotonic clock.
        with patch("logic.AutoCal.time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)), \
                patch("logic.AutoCal.asyncio.sleep", sleep):
            result = await self.auto._measurePoint(
                0, 0, True, 10, [":CONF:VOLT:AC 100"], settle_sec=0, sample_sec=3,
            )
        self.assertEqual(count[0], 5)
        self.assertEqual(result[:3], (10, 0, 5))
        self.assertGreater(elapsed[0], 3)
        self.assertLess(elapsed[0], 8)

    async def test_no_valid_readings_has_bounded_sampling_time(self):
        elapsed = [0.0]
        original_query = self.auto._dmm.query

        def query(command):
            if command == ":READ?":
                elapsed[0] += 1
                return "9.9E37"
            return original_query(command)

        async def sleep(seconds):
            elapsed[0] += seconds

        self.auto._dmm.query = query
        with patch("logic.AutoCal.time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)), \
                patch("logic.AutoCal.asyncio.sleep", sleep):
            with self.assertRaisesRegex(ValueError, "Too few valid samples"):
                await self.auto._measurePoint(
                    0, 0, True, 10, [":CONF:VOLT:AC 100"], settle_sec=0, sample_sec=3,
                )
        self.assertLess(elapsed[0], 10)
        self.assertFalse(self.amplifier[-1])

    async def test_stop_during_blocked_visa_read_prevents_more_output(self):
        entered = threading.Event()
        original_query = self.auto._dmm.query

        def query(command):
            if command == ":READ?":
                entered.set()
                time.sleep(0.03)  # Blocks only the simulated instrument worker.
                return "10,10,10,10,10"
            return original_query(command)

        self.auto._dmm.query = query
        self.auto._running = True
        task = asyncio.create_task(self.auto._measurePoint(
            0, 0, True, 10, [":CONF:VOLT:AC 100"], settle_sec=0, sample_sec=0.1,
        ))
        while not entered.is_set():
            if task.done():
                await task
            await asyncio.sleep(0.001)
        frames_before = len(self.frames)
        self.auto._stop()
        self.assertEqual(self.amplifier[-1], False)
        with self.assertRaises(_Stopped):
            await task
        self.assertEqual(len(self.frames), frames_before)

    async def test_end_to_end_legacy_calibration_uses_emitted_registers(self):
        registers = {}

        async def transmit(frames):
            for frame in frames:
                self.frames.append(frame)
                if frame == HWCodec.FRAME_SYS_RESET:
                    registers.clear()
                elif len(frame) == 14:
                    registers[0 if frame[2] == 0xFF else frame[2] + 1] = struct.unpack(">II", frame[5:13])
            return 123

        original_query = self.auto._dmm.query

        def query(command):
            if command not in (":READ?", ":MEAS:VOLT:AC?", ":MEAS:VOLT:DC?"):
                return original_query(command)
            is_ac = (":AC " in next(cmd for cmd in reversed(self.auto._dmm.commands) if cmd.startswith(":CONF:"))
                     if command == ":READ?" else command.endswith(":AC?"))
            value = registers[1 if is_ac else 0][0]
            if value & 0x80000000:
                value -= 0x100000000
            physical = value / HWConfig.VOLTAGE
            measured = physical / math.sqrt(2) * 1.05 if is_ac else physical * -1.02 + 0.12
            return ",".join([str(measured)] * 5) if command == ":READ?" else str(measured)

        original_measure = self.auto._measurePoint

        async def fast_measure(*args, **kwargs):
            kwargs.update(settle_sec=0, sample_sec=0.001)
            return await original_measure(*args, **kwargs)

        self.auto._sendFramesFunc = transmit
        self.auto._dmm.query = query
        self.auto._measurePoint = fast_measure
        await self.auto._start({"ch": 0, "calibrationMode": "legacy", "extra": "ignored"})
        self.assertFalse(self.auto.is_running())
        self.assertEqual(self.amplifier[-1], False)
        self.assertFalse(any(msg.get("type") == "calError" for msg in self.messages), self.messages)
        bias, dc, ac = self.files.calib.calibData["0"]
        self.assertAlmostEqual(bias, 0.12 / 1.02, places=7)
        self.assertAlmostEqual(dc, -1 / 1.02, places=7)
        self.assertAlmostEqual(ac, 1 / 1.05, places=7)

    async def test_end_to_end_frequency_calibration_compensates_emitted_output(self):
        registers = {}

        async def transmit(frames):
            for frame in frames:
                self.frames.append(frame)
                if frame == HWCodec.FRAME_SYS_RESET:
                    registers.clear()
                elif len(frame) == 14:
                    registers[0 if frame[2] == 0xFF else frame[2] + 1] = struct.unpack(">II", frame[5:13])
            return 123

        original_query = self.auto._dmm.query

        def query(command):
            if command not in (":READ?", ":MEAS:VOLT:AC?"):
                return original_query(command)
            frequency = registers[0][1] / HWConfig.FREQ
            gain = 1.05 if frequency < 100 else 0.9
            measured = registers[1][0] / HWConfig.VOLTAGE / math.sqrt(2) * gain
            return ",".join([str(measured)] * 5) if command == ":READ?" else str(measured)

        original_measure = self.auto._measurePoint

        async def fast_measure(*args, **kwargs):
            kwargs.update(settle_sec=0, sample_sec=0.001)
            return await original_measure(*args, **kwargs)

        self.auto._sendFramesFunc = transmit
        self.auto._dmm.query = query
        self.auto._measurePoint = fast_measure
        before = self.files.path.read_bytes()
        await self.auto._start({"ch": 0, "frequenciesHz": [55, 155], "referenceRms": 10})
        self.assertEqual(before, self.files.path.read_bytes())
        self.assertFalse(any(msg.get("type") == "calError" for msg in self.messages), self.messages)
        points = self.files.calib.frequencyData["channels"]["0"]["points"]
        self.assertAlmostEqual(points[0]["factorAc"], 1 / 1.05, places=7)
        self.assertAlmostEqual(points[1]["factorAc"], 1 / 0.9, places=7)
        reports = [msg for msg in self.messages if msg.get("type") == "verifyPoint"]
        self.assertEqual(len(reports), 2)
        self.assertTrue(all(report["error"] < 0.01 for report in reports))


class _VisaInstrument(_Dmm):
    def __init__(self, idn="RIGOL TECHNOLOGIES,DM858,MOCK,1.0", *, idn_error=None, close_error=None):
        super().__init__()
        self.idn = idn
        self.idn_error = idn_error
        self.close_error = close_error
        self.closed = False

    def query(self, command):
        if command == "*IDN?":
            if self.idn_error:
                raise self.idn_error
            return self.idn
        return super().query(command)

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class _Dm3068(_VisaInstrument):
    """Independent command oracle from the supplied DM3068 CHM, not AutoCal.

    Native range indices and TRUE/FALSE readiness are from the CHM. READ?
    reproduces the field timeout even with valid Agilent trigger settings.
    Configuration queries are forbidden; reading readiness is not a gate.
    """
    def __init__(self):
        super().__init__(idn="Rigol Technologies,DM3068,MOCK,01.01.00")
        self.dialect = "RIGOL"
        self.trigger = "EXT"
        self.function = None
        self.manual = False
        self.hold = True
        self.ready_responses = []
        self.queries = []
        self.reading = "+1.00000000E+01\r\n"

    def write(self, command):
        if command.startswith("CMDSet "):
            self.dialect = command.split()[1]
        elif self.dialect == "AGILENT":
            if command not in ("SENS:DET:BAND 20", "SENS:CURR:DC:NPLC 10", "SENS:VOLT:DC:NPLC 10"):
                raise AssertionError(f"Unexpected Agilent setting {command}")
        elif self.dialect == "RIGOL":
            if command.startswith(":FUNC:"):
                self.function = command.removeprefix(":FUNC:")
            elif command == ":MEAS MANU":
                self.manual = True
            elif command.startswith(":MEAS:"):
                function, value = command.split()
                allowed = {
                    ":MEAS:VOLT:AC": (0.2, 2, 20, 200, 750),
                    ":MEAS:VOLT:DC": (0.2, 2, 20, 200, 1000),
                    ":MEAS:CURR:AC": (0.0002, 0.002, 0.02, 0.2, 2, 10),
                    ":MEAS:CURR:DC": (0.0002, 0.002, 0.02, 0.2, 2, 10),
                }
                self.range = allowed[function][int(value)]
            elif command == ":TRIG:SOUR AUTO":
                self.trigger = "AUTO"
            elif command == ":TRIG:AUTO:HOLD OFF":
                self.hold = False
            elif command not in (":CALC:FUNC NONE", ":TRIG:AUTO:INTE 0.2"):
                raise AssertionError(f"Unexpected DM3068 command: {command}")
        else:
            raise AssertionError(f"Native commands not allowed in {self.dialect}")
        super().write(command)

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return self.idn
        if command == ":READ?":
            raise TimeoutError("VI_ERROR_TMO: field Agilent READ? path")
        if self.dialect != "RIGOL" or self.trigger != "AUTO" or self.hold or not self.manual:
            raise AssertionError("Native continuous measurement not configured")
        if command == ":MEAS?":
            return self.ready_responses.pop(0) if self.ready_responses else "TRUE\r\n"
        if command != f":MEAS:{self.function}?":
            raise AssertionError(f"Configuration must not query/gate on {command}")
        return self.reading


class Dm3068Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reuse only the temporary files/frame capture, never a real resource.
        await AutoCalibrationTests.asyncSetUp(self)
        self.auto._dmm = _Dm3068()
        self.auto._acceptDmm(self.auto._dmm.idn)

    async def test_configuration_order_and_no_readback_gates(self):
        for dialect in ("RIGOL", "FLUKE", "AGILENT"):
            for ch in (0, 1):
                for is_ac in (False, True):
                    with self.subTest(dialect=dialect, ch=ch, ac=is_ac):
                        self.auto._dmm.dialect = dialect
                        self.auto._dmm.commands.clear()
                        command = self.auto._commands(ch, 1, is_ac, 0.5 if ch == 1 else 57.735)
                        self.auto._configureDmm(command, is_ac)
                        writes = self.auto._dmm.commands
                        self.assertEqual(writes[0], "CMDSet AGILENT")
                        self.assertEqual(writes[2], "CMDSet RIGOL")
                        self.assertEqual(writes[3:6], command)
                        self.assertEqual(self.auto._dmm.dialect, "RIGOL")
                        self.assertNotIn(":SENS:VOLT:DC:NPLC 20", writes)
                        self.assertNotIn(":SENS:CURR:DC:NPLC 20", writes)
                        read_command = command[0].replace(":FUNC:", ":MEAS:") + "?"
                        self.assertEqual(self.auto._parseReadings(self.auto._readDmm(read_command)), [10.0])
                        self.assertNotIn(":READ?", self.auto._dmm.queries)

    async def test_existing_calibration_and_verify_points_use_documented_ranges(self):
        cases = (
            (0, 0, True, 57.735, ":MEAS:VOLT:AC 3", 200),
            (0, 0, False, 0, ":MEAS:VOLT:DC 1", 2),
            (0, 0, False, 100, ":MEAS:VOLT:DC 3", 200),
            (0, 0, False, -100, ":MEAS:VOLT:DC 3", 200),
            (0, 0, True, 10, ":MEAS:VOLT:AC 2", 20),
            (0, 0, True, 60, ":MEAS:VOLT:AC 3", 200),
            (0, 0, True, 110, ":MEAS:VOLT:AC 3", 200),
            (1, 1, True, 0.5, ":MEAS:CURR:AC 4", 2),
            (1, 1, False, 0, ":MEAS:CURR:DC 4", 2),
            (1, 1, False, -0.5, ":MEAS:CURR:DC 4", 2),
            (1, 1, True, 0.2, ":MEAS:CURR:AC 4", 2),
            (1, 1, True, 1.1, ":MEAS:CURR:AC 4", 2),
            (1, 1, True, 2, ":MEAS:CURR:AC 4", 2),
            (1, 1, True, 3, ":MEAS:CURR:AC 5", 10),
            (1, 0, True, 0.5, ":MEAS:VOLT:AC 0", 0.2),
            (1, 0, True, 2, ":MEAS:VOLT:AC 0", 0.2),
            (1, 0, False, -0.5, ":MEAS:VOLT:DC 0", 0.2),
        )
        for ch, mode, ac, value, expected, physical_range in cases:
            with self.subTest(ch=ch, mode=mode, ac=ac, value=value):
                commands = self.auto._commands(ch, mode, ac, value)
                self.assertEqual(commands[-1], expected)
                self.assertEqual(commands[1], ":MEAS MANU")
                self.auto._configureDmm(commands, ac)
                self.assertEqual(self.auto._dmm.range, physical_range)

    async def test_shunt_profile_selects_range_using_actual_frequency(self):
        self.auto._options = {"shuntProfile": {"dcOhms": 0.075, "points": [
            {"freqHz": 55, "ohmsMagnitude": 0.075},
            {"freqHz": 3000, "ohmsMagnitude": 0.12},
        ]}}
        self.assertEqual(self.auto._commands(1, 0, True, 2, 55)[-1], ":MEAS:VOLT:AC 0")
        self.assertEqual(self.auto._commands(1, 0, True, 2, 3000)[-1], ":MEAS:VOLT:AC 1")

    async def test_current_readings_are_not_rescaled_by_meter_range(self):
        for mode, reading in ((0, "+3.75000000E-02"), (1, "+5.00000000E-01")):
            with self.subTest(mode=mode):
                self.auto._dmm.reading = reading + "\r\n"
                result = await self.auto._measurePoint(
                    1, mode, True, 0.5, self.auto._commands(1, mode, True, 0.5),
                    settle_sec=0, sample_sec=0.001,
                )
                self.assertEqual(result[:3], (0.5, 0, 5))
                self.assertAlmostEqual(result[3], 0.5, delta=0.001)
                self.assertFalse(self.amplifier[-1])

    async def test_dm3068_does_not_change_fpga_output_frames(self):
        for ch in (0, 1):
            for is_ac, amplitude in ((False, 0), (False, -0.5), (True, 0.5)):
                with self.subTest(ch=ch, ac=is_ac, value=amplitude):
                    emitted = []
                    for model in ("DM858", "DM3068"):
                        self.auto._dmmModel = model
                        self.frames.clear()
                        await self.auto._outputCalib(ch, amplitude, is_ac, 155)
                        emitted.append(list(self.frames))
                    self.assertEqual(emitted[0], emitted[1])

    async def test_legacy_calibration_fit_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_legacy_calibration_uses_emitted_registers(self)

    async def test_multifrequency_fit_verify_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_frequency_calibration_compensates_emitted_output(self)
        measurement = self.files.calib.frequencyData["channels"]["0"]["measurement"]
        self.assertEqual(measurement["instrumentModel"], "DM3068")

    async def test_stop_and_ack_failure_still_prevent_output(self):
        await AutoCalibrationTests.test_stop_disables_amplifier_before_query_finishes(self)
        self.auto._stop_flag = False
        await AutoCalibrationTests.test_stop_and_failed_ack_cannot_enable_output(self)

    async def test_zero_and_overload_have_distinct_diagnostics(self):
        for reading, expected in (("0", "AC zero/nonpositive readings="),
                                  ("9.9E37", "No finite, non-overload DMM readings")):
            with self.subTest(reading=reading):
                elapsed = [0.0]

                async def sleep(seconds):
                    elapsed[0] += seconds

                self.auto._dmm.reading = reading
                clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
                with patch("logic.AutoCal.time", clock), patch("logic.AutoCal.asyncio.sleep", sleep):
                    with self.assertRaises(ValueError) as error:
                        await self.auto._measurePoint(
                            1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                            settle_sec=0, sample_sec=0.001,
                        )
                message = str(error.exception)
                self.assertIn("Too few valid samples: 0 < 5", message)
                self.assertIn("mode=current_direct", message)
                self.assertIn(":MEAS:CURR:AC 4", message)
                self.assertIn(f"last_raw='{reading}'", message)
                self.assertIn(expected, message)
                self.assertFalse(self.amplifier[-1])

    async def test_shunt_mode_is_visible_in_no_samples_error(self):
        elapsed = [0.0]

        async def sleep(seconds):
            elapsed[0] += seconds

        self.auto._dmm.reading = "0"
        clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
        with patch("logic.AutoCal.time", clock), patch("logic.AutoCal.asyncio.sleep", sleep):
            with self.assertRaises(ValueError) as error:
                await self.auto._measurePoint(
                    1, 0, True, 0.5, self.auto._commands(1, 0, True, 0.5),
                    settle_sec=0, sample_sec=0.001,
                )
        self.assertIn("mode=current_shunt", str(error.exception))
        self.assertIn(":MEAS:VOLT:AC 0", str(error.exception))

    async def test_timeout_stops_output_without_repeating_pending_query(self):
        instrument = self.auto._dmm
        instrument.query = Mock(side_effect=TimeoutError("VI_ERROR_TMO"))
        with self.assertRaisesRegex(RuntimeError, "DM3068.*VI_ERROR_TMO"):
            await self.auto._measurePoint(
                1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                settle_sec=0, sample_sec=30,
            )
        instrument.query.assert_called_once_with(":MEAS?")
        self.assertEqual(self.amplifier, [False, True, False])
        self.assertTrue(instrument.closed)
        self.assertIsNone(self.auto._dmm)

    async def test_timeout_discards_point_even_after_five_good_readings(self):
        instrument = self.auto._dmm
        instrument.query = Mock(side_effect=["TRUE", "0.5"] * 5 + ["TRUE", TimeoutError("VI_ERROR_TMO")])
        with self.assertRaisesRegex(RuntimeError, "DMM query failed"):
            await self.auto._measurePoint(
                1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                settle_sec=0, sample_sec=30,
            )
        self.assertEqual(instrument.query.call_count, 12)
        self.assertTrue(instrument.closed)
        self.assertFalse(self.amplifier[-1])

    async def test_real_verify_entry_reports_current_query_failure_and_resets(self):
        instrument = self.auto._dmm
        original_query = instrument.query

        def query(command):
            if command == ":MEAS:CURR:AC?":
                raise TimeoutError("VI_ERROR_TMO")
            return original_query(command)

        original_measure = self.auto._measurePoint

        async def fast_measure(*args, **kwargs):
            kwargs.update(settle_sec=0)
            return await original_measure(*args, **kwargs)

        instrument.query = query
        self.auto._measurePoint = fast_measure
        before = self.files.path.read_bytes()
        await self.auto._startVerify({"ch": 6, "currMode": 1})
        error = self.messages[-1]
        self.assertEqual(error["type"], "calError")
        self.assertIn("mode=current_direct", error["msg"])
        self.assertIn("VI_ERROR_TMO", error["msg"])
        self.assertIn("query=:MEAS:CURR:AC?", error["msg"])
        self.assertFalse(self.auto.is_running())
        self.assertTrue(instrument.closed)
        self.assertFalse(self.amplifier[-1])
        self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_RESET)
        self.assertEqual(self.files.path.read_bytes(), before)

    async def test_native_ready_flag_is_not_parsed_as_amperes(self):
        self.auto._configureDmm(self.auto._commands(1, 1, True, 0.5), True)
        instrument = self.auto._dmm
        instrument.ready_responses = ["FALSE\r\n", "0", "TRUE", "1"]
        instrument.reading = "0.5"
        self.assertIsNone(self.auto._readDmm(":MEAS:CURR:AC?"))
        self.assertIsNone(self.auto._readDmm(":MEAS:CURR:AC?"))
        self.assertEqual(self.auto._readDmm(":MEAS:CURR:AC?"), "0.5")
        self.assertEqual(self.auto._readDmm(":MEAS:CURR:AC?"), "0.5")
        self.assertEqual(instrument.queries.count(":MEAS:CURR:AC?"), 2)
        self.assertEqual(instrument.queries.count(":MEAS?"), 4)

    async def test_no_new_readings_waits_bounded_time_and_does_not_read_cached_value(self):
        elapsed = [0.0]

        async def sleep(seconds):
            elapsed[0] += seconds

        instrument = self.auto._dmm
        instrument.ready_responses = ["FALSE"] * 100
        clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
        with patch("logic.AutoCal.time", clock), patch("logic.AutoCal.asyncio.sleep", sleep):
            with self.assertRaisesRegex(ValueError, "0 < 5.*no new reading"):
                await self.auto._measurePoint(
                    1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                    settle_sec=0, sample_sec=0.001,
                )
        self.assertNotIn(":MEAS:CURR:AC?", instrument.queries)
        self.assertFalse(instrument.closed)  # FALSE is not a transport error.
        self.assertLess(elapsed[0], 6)
        self.assertFalse(self.amplifier[-1])

    async def test_native_configuration_preserves_serial_crlf_and_uses_lf_for_usb(self):
        for resource, expected in (("ASRL/dev/ttyUSB0::INSTR", "\r\n"),
                                   ("USB0::0x1AB1::0x0C94::MOCK::INSTR", "\n"),
                                   ("TCPIP0::192.0.2.1::INSTR", "\n")):
            with self.subTest(resource=resource):
                self.auto._dmmResource = resource
                self.auto._configureDmm(self.auto._commands(0, 0, True, 57.735), True)
                self.assertEqual(self.auto._dmm.write_termination, expected)
                self.assertEqual(self.auto._dmm.read_termination, "\n")

    async def test_native_nine_point_verify_voltage_current_and_shunt_from_frames(self):
        # Independent plant: fixture DC=-0.95, AC=0.95. Invert those known
        # fixture constants, not Calibration.RegToPhys or the implementation.
        for api_ch, mode in ((0, 1), (6, 1), (6, 0)):
            with self.subTest(channel=api_ch, mode=mode):
                self.messages.clear()
                self.frames.clear()
                self.auto._dmm = _Dm3068()
                instrument = self.auto._dmm
                registers = {}
                elapsed = [0.0]
                hw_ch = 0 if api_ch == 0 else 1
                scale = HWConfig.VOLTAGE if api_ch == 0 else HWConfig.CURRENT
                before = self.files.path.read_bytes()

                async def transmit(frames):
                    for frame in frames:
                        self.frames.append(frame)
                        if frame == HWCodec.FRAME_SYS_RESET:
                            registers.clear()
                        elif len(frame) == 14:
                            registers[0 if frame[2] == 0xFF else frame[2] + 1] = struct.unpack(">II", frame[5:13])
                    return 123

                original_query = instrument.query

                def query(command):
                    response = original_query(command)  # Validates dialect/function.
                    if not command.startswith(":MEAS:"):
                        return response
                    ac = command.endswith(":AC?")
                    amplitude = registers[1 if ac else 0][0]
                    if amplitude & 0x80000000:
                        amplitude -= 0x100000000
                    measured = (amplitude / scale / math.sqrt(2) / 0.95 if ac else
                                (amplitude / scale - 0.001 * hw_ch) / -0.95)
                    if api_ch == 6 and mode == 0:
                        measured *= 0.075
                    return str(measured)

                async def sleep(seconds):
                    elapsed[0] += seconds

                instrument.query = query
                self.auto._sendFramesFunc = transmit
                clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
                with patch("logic.AutoCal.time", clock), patch("logic.AutoCal.asyncio.sleep", sleep):
                    await self.auto._startVerify({"ch": api_ch, "currMode": mode})
                reports = [msg for msg in self.messages if msg.get("type") == "verifyPoint"]
                self.assertEqual(len(reports), 9, self.messages)
                self.assertTrue(all(report["error"] < 0.01 for report in reports), reports)
                self.assertNotIn(":READ?", instrument.queries)
                self.assertEqual(self.files.path.read_bytes(), before)
                self.assertFalse(self.amplifier[-1])
                self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_RESET)


class _Dm3058(_Dm3068):
    """DM3058/E manual oracle: share only the native reading-ready protocol.

    AC current indices differ from DC. Trigger interval is integer ms,
    slow rate is effective on AC, and scalar reads require single display.
    """
    def __init__(self, model="DM3058E"):
        super().__init__()
        self.idn = f"RIGOL TECHNOLOGIES,{model},MOCK,1.0"
        self.secondary = True
        self.slow = False

    def write(self, command):
        if command == "CMDSet RIGOL":
            self.dialect = "RIGOL"
        elif self.dialect != "RIGOL":
            raise AssertionError("DM3058 needs native RIGOL command set")
        elif command == ":FUNCtion2:CLEar":
            self.secondary = False
        elif command.startswith(":FUNC:"):
            self.function = command.removeprefix(":FUNC:")
            self.slow = False
        elif command == ":MEAS MANU":
            self.manual = True
        elif command.startswith(":MEAS:"):
            function, value = command.split()
            allowed = {
                ":MEAS:VOLT:AC": (0.2, 2, 20, 200, 750),
                ":MEAS:VOLT:DC": (0.2, 2, 20, 200, 1000),
                ":MEAS:CURR:AC": (0.02, 0.2, 2, 10),
                ":MEAS:CURR:DC": (0.0002, 0.002, 0.02, 0.2, 2, 10),
            }
            self.range = allowed[function][int(value)]
        elif command == f":RATE:{self.function} S":
            self.slow = True
        elif command == ":TRIG:SOUR AUTO":
            self.trigger = "AUTO"
        elif command == ":TRIG:AUTO:HOLD OFF":
            self.hold = False
        elif command not in (":CALC:FUNC NONE", ":TRIG:AUTO:INTE 400"):
            raise AssertionError(f"Unsupported DM3058 command: {command}")
        _VisaInstrument.write(self, command)

    def query(self, command):
        if command != "*IDN?" and (self.secondary or not self.slow):
            raise AssertionError("Configure single display and slow measurement first")
        return super().query(command)


class Dm3058Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await AutoCalibrationTests.asyncSetUp(self)
        self.auto._dmm = _Dm3058()
        self.auto._acceptDmm(self.auto._dmm.idn)

    async def test_documented_ranges_and_trigger_settings_for_both_models(self):
        cases = (
            (0, 1, False, 0, ":MEAS:VOLT:DC 1", 2),
            (0, 1, False, -100, ":MEAS:VOLT:DC 3", 200),
            (0, 1, True, 57.735, ":MEAS:VOLT:AC 3", 200),
            (1, 1, False, 0, ":MEAS:CURR:DC 4", 2),
            (1, 1, False, -0.5, ":MEAS:CURR:DC 4", 2),
            (1, 1, False, 3, ":MEAS:CURR:DC 5", 10),
            (1, 1, True, 0.5, ":MEAS:CURR:AC 2", 2),
            (1, 1, True, 2, ":MEAS:CURR:AC 2", 2),
            (1, 1, True, 3, ":MEAS:CURR:AC 3", 10),
            (1, 0, True, 0.5, ":MEAS:VOLT:AC 0", 0.2),
        )
        for model in ("DM3058", "DM3058E"):
            for ch, mode, ac, value, expected, physical_range in cases:
                with self.subTest(model=model, ch=ch, mode=mode, ac=ac, value=value):
                    instrument = self.auto._dmm = _Dm3058(model)
                    instrument.dialect = "AGILENT"  # Previously used by another application.
                    self.auto._acceptDmm(instrument.idn)
                    commands = self.auto._commands(ch, mode, ac, value)
                    self.assertEqual(commands[-1], expected)
                    self.auto._configureDmm(commands, ac)
                    self.assertEqual(instrument.range, physical_range)
                    self.assertEqual(instrument.commands[0], "CMDSet RIGOL")
                    self.assertIn(":TRIG:AUTO:INTE 400", instrument.commands)
                    self.assertEqual(instrument.queries, [])
                    read_command = commands[0].replace(":FUNC:", ":MEAS:") + "?"
                    self.assertEqual(self.auto._parseReadings(self.auto._readDmm(read_command)), [10.0])
                    self.assertEqual(self.auto._frequencyOptions(ch, mode)[0], 0.5 if ch else 57.735)

    async def test_readiness_scalar_samples_and_shunt_units(self):
        await Dm3068Tests.test_native_ready_flag_is_not_parsed_as_amperes(self)
        await Dm3068Tests.test_current_readings_are_not_rescaled_by_meter_range(self)
        await Dm3068Tests.test_shunt_profile_selects_range_using_actual_frequency(self)

    async def test_native_no_data_and_transport_endings(self):
        await Dm3068Tests.test_no_new_readings_waits_bounded_time_and_does_not_read_cached_value(self)
        await Dm3068Tests.test_native_configuration_preserves_serial_crlf_and_uses_lf_for_usb(self)

    async def test_timeout_disables_output_and_closes_session_once(self):
        instrument = self.auto._dmm
        instrument.query = Mock(side_effect=TimeoutError("VI_ERROR_TMO"))
        with self.assertRaisesRegex(RuntimeError, "DM3058E.*VI_ERROR_TMO"):
            await self.auto._measurePoint(1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                                          settle_sec=0, sample_sec=0.001)
        instrument.query.assert_called_once_with(":MEAS?")
        self.assertTrue(instrument.closed)
        self.assertFalse(self.amplifier[-1])

    async def test_legacy_fit_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_legacy_calibration_uses_emitted_registers(self)

    async def test_frequency_fit_verify_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_frequency_calibration_compensates_emitted_output(self)
        self.assertEqual(self.files.calib.frequencyData["channels"]["0"]["measurement"]["instrumentModel"], "DM3058E")

    async def test_nine_point_verify_voltage_direct_current_and_shunt(self):
        # Reuse the independent plant, replacing only its meter factory.
        with patch.object(sys.modules[__name__], "_Dm3068", _Dm3058):
            await Dm3068Tests.test_native_nine_point_verify_voltage_current_and_shunt_from_frames(self)


class _Agilent34410a(_VisaInstrument):
    """Command oracle from the manual and USB probe; no configuration queries.

    READ? returns one fresh V/A value, not the DM3068 TRUE/FALSE readiness.
    The connected firmware reports 1000 for an AC 750 V range request.
    """
    def __init__(self):
        super().__init__(idn="Agilent Technologies,34410A,MOCK,2.35-2.35-0.09-46-09")
        self.function = None
        self.settings = set()
        self.queries = []
        self.reading = "+5.00000000E-01\n"

    def write(self, command):
        super().write(command)
        if command.startswith(":CONF:"):
            function, value = command.split()
            self.function = function.removeprefix(":CONF:")
            allowed = {
                "VOLT:AC": (0.1, 1, 10, 100, 750),
                "VOLT:DC": (0.1, 1, 10, 100, 1000),
                "CURR:AC": (0.0001, 0.001, 0.01, 0.1, 1, 3),
                "CURR:DC": (0.0001, 0.001, 0.01, 0.1, 1, 3),
            }
            if float(value) not in allowed[self.function]:
                raise AssertionError(f"Unsupported range: {command}")
            if self.function == "VOLT:AC" and self.range == 750:
                self.range = 1000
            self.settings.clear()
        elif command in (
            ":SENS:VOLT:AC:BAND 20", ":SENS:CURR:AC:BAND 20",
            ":SENS:VOLT:DC:NPLC 10", ":SENS:CURR:DC:NPLC 10",
            ":TRIG:SOUR IMM", ":TRIG:COUN 1", ":SAMP:COUN 1", ":TRIG:DEL:AUTO ON",
        ):
            self.settings.add(command)
        else:
            raise AssertionError(f"Unexpected 34410A command: {command}")

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return self.idn
        if command != ":READ?":
            raise AssertionError(f"Do not gate configuration on {command}")
        setting = "BAND 20" if self.function.endswith("AC") else "NPLC 10"
        expected = {
            f":SENS:{self.function}:{setting}", ":TRIG:SOUR IMM",
            ":TRIG:COUN 1", ":SAMP:COUN 1", ":TRIG:DEL:AUTO ON",
        }
        if self.settings != expected:
            raise AssertionError("Single immediate measurement not configured")
        return self.reading


class Agilent34410aTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await AutoCalibrationTests.asyncSetUp(self)
        self.auto._dmm = _Agilent34410a()
        self.auto._acceptDmm(self.auto._dmm.idn)

    async def test_all_legacy_points_configure_without_readback_gates(self):
        for ch, mode, levels in ((0, 1, (0, 10, 57.735, 100, 110)),
                                 (1, 1, (0, 0.2, 0.5, 1.1, 2)),
                                 (1, 0, (0, 0.2, 0.5, 1.1, 2))):
            for ac in (False, True):
                for value in levels:
                    for sign in (1, -1):
                        with self.subTest(ch=ch, mode=mode, ac=ac, amplitude=value * sign):
                            commands = self.auto._commands(ch, mode, ac, value * sign)
                            self.auto._configureDmm(commands, ac)
                            self.assertEqual(self.auto._readDmm(":READ?").strip(), "+5.00000000E-01")
        self.assertEqual(set(self.auto._dmm.queries), {":READ?"})
        self.assertEqual(self.auto._dmm.write_termination, "\n")
        self.assertEqual(self.auto._dmm.read_termination, "\n")

    async def test_ac_voltage_range_coercion_does_not_change_reading(self):
        self.auto._configureDmm(self.auto._commands(0, 1, True, 110), True)
        self.assertEqual(self.auto._dmm.range, 1000)
        self.auto._dmm.reading = "1.10000000E+02"
        self.assertEqual(self.auto._parseReadings(self.auto._readDmm(":READ?")), [110])

    async def test_three_amp_limit_applies_only_to_direct_current(self):
        self.auto._options = {"referenceRms": 3}
        self.assertEqual(self.auto._frequencyOptions(1, 1)[0], 3)
        self.assertEqual(self.auto._commands(1, 1, True, 3), [":CONF:CURR:AC 3"])
        self.auto._options = {"referenceRms": 3.01}
        with self.assertRaisesRegex(ValueError, "3 A"):
            self.auto._frequencyOptions(1, 1)
        for ac in (False, True):
            for amplitude in (-3.01, 3.01):
                with self.assertRaisesRegex(ValueError, "3 A"):
                    self.auto._commands(1, 1, ac, amplitude)
        self.assertEqual(self.auto._frequencyOptions(0, 1)[0], 3.01)
        self.assertEqual(self.auto._frequencyOptions(1, 0)[0], 3.01)
        self.assertEqual(self.auto._commands(1, 0, True, 3.01), [":CONF:VOLT:AC 1"])

    async def test_scalar_readings_are_averaged_in_physical_units(self):
        for mode, reading in ((0, "0.0375"), (1, "0.5")):
            with self.subTest(mode=mode):
                self.auto._dmm.reading = reading
                result = await self.auto._measurePoint(
                    1, mode, True, 0.5, self.auto._commands(1, mode, True, 0.5),
                    settle_sec=0, sample_sec=0.001,
                )
                self.assertEqual(result[:3], (0.5, 0, 5))
                self.assertFalse(self.amplifier[-1])

    async def test_legacy_calibration_fit_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_legacy_calibration_uses_emitted_registers(self)

    async def test_multifrequency_fit_verify_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_frequency_calibration_compensates_emitted_output(self)
        self.assertEqual(self.files.calib.frequencyData["channels"]["0"]["measurement"]["instrumentModel"], "34410A")

    async def test_timeout_discards_point_without_repeated_queries(self):
        for good_count in (0, 5):
            with self.subTest(good_count=good_count):
                instrument = _Agilent34410a()
                self.auto._dmm = instrument
                self.auto._acceptDmm(instrument.idn)
                instrument.query = Mock(side_effect=["0.5"] * good_count + [TimeoutError("VI_ERROR_TMO")])
                with self.assertRaisesRegex(RuntimeError, "34410A.*VI_ERROR_TMO"):
                    await self.auto._measurePoint(
                        1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                        settle_sec=0, sample_sec=30,
                    )
                self.assertEqual(instrument.query.call_count, good_count + 1)
                self.assertTrue(all(call.args == (":READ?",) for call in instrument.query.call_args_list))
                self.assertTrue(instrument.closed)
                self.assertIsNone(self.auto._dmm)
                self.assertFalse(self.amplifier[-1])

    async def test_nine_point_verify_voltage_current_and_shunt_from_frames(self):
        for api_ch, mode in ((0, 1), (6, 1), (6, 0)):
            with self.subTest(channel=api_ch, mode=mode):
                self.messages.clear()
                instrument = _Agilent34410a()
                self.auto._dmm = instrument
                registers, elapsed = {}, [0.0]
                hw_ch = 0 if api_ch == 0 else 1
                scale = HWConfig.VOLTAGE if api_ch == 0 else HWConfig.CURRENT
                before = self.files.path.read_bytes()

                async def transmit(frames):
                    self.frames.extend(frames)
                    for frame in frames:
                        if frame == HWCodec.FRAME_SYS_RESET:
                            registers.clear()
                        elif len(frame) == 14:
                            registers[0 if frame[2] == 0xFF else frame[2] + 1] = struct.unpack(">II", frame[5:13])
                    return 123

                original_query = instrument.query

                def query(command):
                    response = original_query(command)
                    if command != ":READ?":
                        return response
                    ac = instrument.function.endswith("AC")
                    amplitude = registers[1 if ac else 0][0]
                    if amplitude & 0x80000000:
                        amplitude -= 0x100000000
                    # Invert known fixture gains, not the code under test.
                    measured = (amplitude / scale / math.sqrt(2) / 0.95 if ac else
                                (amplitude / scale - 0.001 * hw_ch) / -0.95)
                    return str(measured * 0.075 if api_ch == 6 and mode == 0 else measured)

                async def sleep(seconds):
                    elapsed[0] += seconds

                instrument.query = query
                self.auto._sendFramesFunc = transmit
                clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
                with patch("logic.AutoCal.time", clock), patch("logic.AutoCal.asyncio.sleep", sleep):
                    await self.auto._startVerify({"ch": api_ch, "currMode": mode})
                reports = [msg for msg in self.messages if msg.get("type") == "verifyPoint"]
                self.assertEqual(len(reports), 9, self.messages)
                self.assertTrue(all(report["error"] < 0.01 for report in reports), reports)
                self.assertEqual(set(instrument.queries), {"*IDN?", ":READ?"})
                self.assertEqual(self.files.path.read_bytes(), before)
                self.assertFalse(self.amplifier[-1])
                self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_RESET)

    async def _frontend_current_workflow(self, frequency_mode, expected_model="34410A"):
        """Exercise the production command route; only VISA and hardware are fake."""
        ctrl = object.__new__(EngineCtrl)
        ctrl.autoCal = self.auto
        instrument = self.auto._dmm
        self.auto._dmm = None  # Exercise discovery, not an injected connected model.
        self.auto._dmmModel = None
        rm = Mock()
        rm.list_resources.return_value = ("USB0::0x0957::0x0607::MOCK::INSTR",)
        rm.open_resource.return_value = instrument
        visa = SimpleNamespace(ResourceManager=Mock(return_value=rm))
        registers, elapsed = {}, [0.0]
        real_sleep = asyncio.sleep

        async def transmit(frames):
            self.frames.extend(frames)
            for frame in frames:
                if frame == HWCodec.FRAME_SYS_RESET:
                    registers.clear()
                elif len(frame) == 14:
                    self.assertEqual(int.from_bytes(frame[3:5], "big"), 1 << 1)
                    registers[0 if frame[2] == 255 else frame[2] + 1] = struct.unpack(">II", frame[5:13])
            return 123

        original_query = instrument.query

        def query(command):
            response = original_query(command)
            if command != ":READ?":
                return response
            ac = instrument.function.endswith("AC")
            value = registers[1 if ac else 0][0]
            if value & 0x80000000:
                value -= 0x100000000
            raw_current = value / HWConfig.CURRENT
            frequency = registers[0][1] / HWConfig.FREQ
            # Independent amplifier model, including negative DC polarity.
            gain = 1.05 if frequency < 100 else 0.9
            return str(raw_current / math.sqrt(2) * gain if ac else raw_current * -1.02 + 0.01)

        async def sleep(seconds):
            elapsed[0] += seconds
            await real_sleep(0)

        async def dispatch(**fields):
            jobs = []

            def schedule(coroutine):
                task = asyncio.create_task(coroutine)
                jobs.append(task)
                return task

            with patch("logic.AutoCal.asyncio.ensure_future", schedule):
                await ctrl.HandleCommand({"cmd": "autoCal", **fields})
            if jobs:
                await asyncio.gather(*jobs)

        self.auto._sendFramesFunc = transmit
        self.auto._onSaved = Mock()
        instrument.query = query
        clock = SimpleNamespace(monotonic=lambda: elapsed[0], sleep=lambda seconds: None)
        options = {"ch": 6, "currMode": 1, "unusedFrontendField": "ignored"}
        if frequency_mode:
            options.update(calibrationMode="ac_frequency", frequenciesHz=[55, 155, 3000], referenceRms=0.5)
        legacy_before = self.files.path.read_bytes()
        with patch.dict(sys.modules, {"pyvisa": visa}), patch("logic.AutoCal.time", clock), \
                patch("logic.AutoCal.asyncio.sleep", sleep):
            await dispatch(action="auth", password="100200")
            self.assertEqual(self.messages[-1]["success"], 1)
            await dispatch(action="start", **options)
            self.assertFalse(self.auto.is_running())
            self.assertFalse(any(msg.get("type") == "calError" for msg in self.messages), self.messages)
            self.auto._onSaved.assert_called_once_with()
            self.assertFalse(self.amplifier[-1])
            self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_RESET)
            if frequency_mode:
                self.assertEqual(self.files.path.read_bytes(), legacy_before)
                curve = self.files.calib.frequencyData["channels"]["1"]
                self.assertEqual(curve["measurement"]["instrumentModel"], expected_model)
                self.assertEqual(curve["measurement"]["mode"], "current_direct")
                for point, gain in zip(curve["points"], (1.05, 0.9, 0.9)):
                    self.assertAlmostEqual(point["factorAc"], 1 / gain)
            else:
                bias, dc, ac = self.files.calib.calibData["1"]
                self.assertAlmostEqual(bias, 0.01 / 1.02, places=7)
                self.assertAlmostEqual(dc, -1 / 1.02, places=7)
                self.assertAlmostEqual(ac, 1 / 1.05, places=7)
            paths = list(self.files.path.parent.glob("calibration*.json"))
            saved = {path: path.read_bytes() for path in paths}
            self.messages.clear()
            await dispatch(action="verify", **options)
        self.assertFalse(any(msg.get("type") == "calError" for msg in self.messages), self.messages)
        reports = [msg for msg in self.messages if msg.get("type") == "verifyPoint"]
        self.assertEqual(len(reports), 3 if frequency_mode else 9, self.messages)
        self.assertTrue(all(report["ch"] == 6 and report["unit"] == "A" and report["error"] < 0.01
                            for report in reports), reports)
        self.assertTrue(any(msg.get("progress") == 100 for msg in self.messages))
        self.assertEqual(saved, {path: path.read_bytes() for path in paths})
        self.assertFalse(self.auto.is_running())
        self.assertFalse(self.amplifier[-1])
        self.assertEqual(self.frames[-1], HWCodec.FRAME_SYS_RESET)
        rm.open_resource.assert_called_once()  # Verify reuses the identified session.

    async def test_frontend_current_calibration_then_nine_point_verify(self):
        await self._frontend_current_workflow(frequency_mode=False)

    async def test_frontend_current_frequency_calibration_then_verify(self):
        await self._frontend_current_workflow(frequency_mode=True)


class _Keysight34461a(_VisaInstrument):
    """Truevolt manual oracle: CONF <= 3 A selects the 3 A input terminals.

    Keep this independent of the 34410A's observed 750 -> 1000 V readback.
    Reject native RIGOL commands and configuration-query gates.
    """
    def __init__(self):
        super().__init__(idn="Keysight Technologies,34461A,MOCK,A.03.03")
        self.function = None
        self.settings, self.queries = set(), []
        self.terminals = {"CURR:AC": 10, "CURR:DC": 10}
        self.reading = "+5.00000000E-01\n"

    def write(self, command):
        super().write(command)
        if command.startswith(":CONF:"):
            function, raw_range = command.split()
            self.function = function.removeprefix(":CONF:")
            selected = float(raw_range)
            ranges = {
                "VOLT:AC": (0.1, 1, 10, 100, 750),
                "VOLT:DC": (0.1, 1, 10, 100, 1000),
                "CURR:AC": (0.0001, 0.001, 0.01, 0.1, 1, 3, 10),
                "CURR:DC": (0.0001, 0.001, 0.01, 0.1, 1, 3, 10),
            }
            if selected not in ranges[self.function]:
                raise AssertionError(f"Invalid 34461A range: {command}")
            if self.function.startswith("CURR:"):
                self.terminals[self.function] = 10 if selected == 10 else 3
            self.settings.clear()
        elif command in {
            ":SENS:VOLT:AC:BAND 20", ":SENS:CURR:AC:BAND 20",
            ":SENS:VOLT:DC:NPLC 10", ":SENS:CURR:DC:NPLC 10",
            ":TRIG:SOUR IMM", ":TRIG:COUN 1", ":SAMP:COUN 1", ":TRIG:DEL:AUTO ON",
        }:
            self.settings.add(command)
        else:
            raise AssertionError(f"Unexpected 34461A command: {command}")

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return self.idn
        if command != ":READ?":
            raise AssertionError(f"Unexpected 34461A query: {command}")
        setting = "BAND 20" if self.function.endswith("AC") else "NPLC 10"
        expected = {f":SENS:{self.function}:{setting}", ":TRIG:SOUR IMM",
                    ":TRIG:COUN 1", ":SAMP:COUN 1", ":TRIG:DEL:AUTO ON"}
        if self.settings != expected:
            raise AssertionError("34461A needs a single immediate measurement")
        if self.function.startswith("CURR:") and self.terminals[self.function] != 3:
            raise AssertionError("Calibration must use the 3 A terminals")
        return self.reading


class Keysight34461aTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await AutoCalibrationTests.asyncSetUp(self)
        self.auto._dmm = _Keysight34461a()
        self.auto._acceptDmm(self.auto._dmm.idn)

    async def test_all_points_use_documented_settings_and_select_three_amp_terminals(self):
        await Agilent34410aTests.test_all_legacy_points_configure_without_readback_gates(self)
        self.assertEqual(self.auto._dmm.terminals, {"CURR:AC": 3, "CURR:DC": 3})
        self.assertFalse(any(c.startswith(":CONF:CURR:") and c.endswith(" 10")
                             for c in self.auto._dmm.commands))

    async def test_voltage_ranges_and_readings_remain_unscaled(self):
        for ac, amplitude, expected_range in ((False, 0, 1), (False, 100, 100), (True, 110, 750)):
            with self.subTest(ac=ac, amplitude=amplitude):
                self.auto._configureDmm(self.auto._commands(0, 1, ac, amplitude), ac)
                self.assertEqual(self.auto._dmm.range, expected_range)
                self.auto._dmm.reading = str(amplitude)
                self.assertEqual(self.auto._parseReadings(self.auto._readDmm(":READ?")), [amplitude])

    async def test_three_amp_limit_and_shunt_exclusion(self):
        await Agilent34410aTests.test_three_amp_limit_applies_only_to_direct_current(self)

    async def test_scalar_current_readings_and_shunt_units(self):
        await Agilent34410aTests.test_scalar_readings_are_averaged_in_physical_units(self)

    async def test_legacy_voltage_fit_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_legacy_calibration_uses_emitted_registers(self)

    async def test_multifrequency_voltage_fit_verify_save_and_cleanup(self):
        await AutoCalibrationTests.test_end_to_end_frequency_calibration_compensates_emitted_output(self)
        self.assertEqual(self.files.calib.frequencyData["channels"]["0"]["measurement"]["instrumentModel"], "34461A")

    async def test_nine_point_verify_voltage_current_and_shunt_from_frames(self):
        with patch.object(sys.modules[__name__], "_Agilent34410a", _Keysight34461a):
            await Agilent34410aTests.test_nine_point_verify_voltage_current_and_shunt_from_frames(self)

    async def test_timeout_discards_point_without_repeated_queries(self):
        for good_count in (0, 5):
            with self.subTest(good_count=good_count):
                instrument = self.auto._dmm = _Keysight34461a()
                self.auto._acceptDmm(instrument.idn)
                instrument.query = Mock(side_effect=["0.5"] * good_count + [TimeoutError("VI_ERROR_TMO")])
                with self.assertRaisesRegex(RuntimeError, "34461A.*VI_ERROR_TMO"):
                    await self.auto._measurePoint(1, 1, True, 0.5, self.auto._commands(1, 1, True, 0.5),
                                                  settle_sec=0, sample_sec=30)
                self.assertEqual(instrument.query.call_count, good_count + 1)
                self.assertTrue(instrument.closed)
                self.assertFalse(self.amplifier[-1])

    async def test_frontend_current_calibration_then_nine_point_verify(self):
        await Agilent34410aTests._frontend_current_workflow(self, frequency_mode=False, expected_model="34461A")

    async def test_frontend_current_frequency_calibration_then_verify(self):
        await Agilent34410aTests._frontend_current_workflow(self, frequency_mode=True, expected_model="34461A")


class DmmConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.messages, self.amplifier, self.frames = [], [], []
        self.auto = AutoCal(self.messages.append, self.frames.append, self.amplifier.append)
        self.rm = Mock()
        self.rm.list_resources.return_value = ("USB0::0x1AB1::0x0C94::MOCK::INSTR",)
        self.instrument = _VisaInstrument()
        self.rm.open_resource.return_value = self.instrument
        self.visa = SimpleNamespace(ResourceManager=Mock(return_value=self.rm))
        patcher = patch.dict(sys.modules, {"pyvisa": self.visa})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_first_connection_enumerates_opens_and_identifies(self):
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertIs(self.auto._dmm, self.instrument)
        self.assertEqual(self.auto._dmmModel, "DM858")
        self.assertEqual(self.instrument.timeout, 5000)
        self.visa.ResourceManager.assert_called_once_with("@py")
        self.assertEqual(self.instrument.commands, [])  # Connection needs no configuration writes.

    async def test_legacy_idn_is_not_rejected_by_new_model_whitelist(self):
        self.instrument.idn = "RIGOL TECHNOLOGIES,DM3099,MOCK,1.0"  # No DM30* wildcard.
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertIsNone(self.auto._dmmModel)
        with patch("logic.AutoCal.time.sleep"):
            self.auto._configureDmm([":CONF:CURR:DC 3"], False)
        self.assertEqual(self.instrument.commands, [":CONF:CURR:DC 3", ":SENS:CURR:DC:NPLC 10"])
        with self.assertRaisesRegex(ValueError, "Frequency calibration requires"):
            self.auto._frequencyOptions(0, 0)

    async def test_dm3068_manual_idn_is_recognized_without_connection_writes(self):
        self.instrument.idn = "Rigol Technologies,DM3068,DM3A020100823,01.01.00.01.01.00"
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertEqual(self.auto._dmmModel, "DM3068")
        self.assertEqual(self.instrument.commands, [])
        self.assertEqual(self.auto._frequencyOptions(1, 1)[0], 0.5)

    async def test_dm3058_and_dm3058e_are_recognized_without_connection_writes(self):
        for model in ("DM3058", "DM3058E"):
            self.instrument.idn = f"Rigol Technologies,{model},MOCK,1.0"
            self.assertTrue(await self.auto._io(self.auto._connectDmm))
            self.assertEqual(self.auto._dmmModel, model)
            self.assertEqual(self.instrument.commands, [])

    async def test_agilent_and_keysight_34410a_are_recognized_without_connection_writes(self):
        for manufacturer in ("Agilent Technologies", "Keysight Technologies"):
            with self.subTest(manufacturer=manufacturer):
                self.instrument.idn = f"{manufacturer},34410A,MOCK,2.35-2.35-0.09-46-09"
                self.assertTrue(await self.auto._io(self.auto._connectDmm))
                self.assertEqual(self.auto._dmmModel, "34410A")
                self.assertEqual(self.instrument.commands, [])
                self.assertEqual(self.auto._frequencyOptions(1, 1)[0], 0.5)
        self.instrument.idn = "Agilent Technologies,34401A,MOCK,1.0"
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertIsNone(self.auto._dmmModel)

    async def test_keysight_and_agilent_34461a_are_recognized_without_connection_writes(self):
        for manufacturer in ("Keysight Technologies", "Agilent Technologies"):
            with self.subTest(manufacturer=manufacturer):
                self.instrument.idn = f'"{manufacturer}","34461A",MOCK,A.03.03\r\n'
                self.assertTrue(await self.auto._io(self.auto._connectDmm))
                self.assertEqual(self.auto._dmmModel, "34461A")
                self.assertEqual(self.instrument.commands, [])
                self.assertEqual(self.auto._frequencyOptions(1, 1)[0], 0.5)
        for model in ("34465A", "34401A", "DM34461A"):
            self.instrument.idn = f"Keysight Technologies,{model},MOCK,1.0"
            self.assertTrue(await self.auto._io(self.auto._connectDmm))
            self.assertIsNone(self.auto._dmmModel)  # Do not infer support for other products.

    async def test_dm3068_serial_idn_uses_crlf_before_identification(self):
        self.auto._options = {"visaResource": "ASRL/dev/ttyUSB0::INSTR"}

        def query(command):
            self.assertEqual(command, "*IDN?")
            self.assertEqual(self.instrument.write_termination, "\r\n")
            self.assertEqual(self.instrument.read_termination, "\n")
            return "Rigol Technologies,DM3068,MOCK,1.0\r\n"

        self.instrument.query = query
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertEqual(self.auto._dmmModel, "DM3068")

    async def test_quoted_idn_fields_and_dm858e_are_recognized(self):
        self.instrument.idn = '"RIGOL TECHNOLOGIES","DM858E",MOCK,1.0\r\n'
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertEqual(self.auto._dmmModel, "DM858E")

    async def test_cached_session_does_not_require_cls(self):
        self.auto._dmm = self.instrument
        self.instrument.write = Mock(side_effect=RuntimeError("configuration not supported"))
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.instrument.write.assert_not_called()
        self.rm.open_resource.assert_not_called()

    async def test_usb_is_selected_before_network_resources_as_in_old_code(self):
        self.rm.list_resources.return_value = ("TCPIP0::192.0.2.1::INSTR", "USB0::6833::MOCK::INSTR")
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.rm.open_resource.assert_called_once_with("USB0::6833::MOCK::INSTR")

    async def test_failed_resource_and_failed_close_do_not_skip_remaining_devices(self):
        broken = _VisaInstrument(idn_error=TimeoutError("IDN timed out"), close_error=OSError("USB removed"))
        self.rm.list_resources.return_value = ("USB0::BROKEN::INSTR", "USB0::OK::INSTR")
        self.rm.open_resource.side_effect = [broken, self.instrument]
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertTrue(broken.closed)
        self.assertIs(self.auto._dmm, self.instrument)

    async def test_explicit_resource_switch_closes_old_session(self):
        previous = _VisaInstrument()
        self.auto._dmm = previous
        self.auto._dmmResource = "USB0::OLD::INSTR"
        self.auto._options = {"visaResource": "USB0::NEW::INSTR"}
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertTrue(previous.closed)
        self.rm.list_resources.assert_not_called()
        self.rm.open_resource.assert_called_once_with("USB0::NEW::INSTR")

    async def test_cached_disconnected_device_is_reopened(self):
        previous = _VisaInstrument(idn_error=OSError("disconnected"))
        self.auto._dmm = previous
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertTrue(previous.closed)
        self.assertIs(self.auto._dmm, self.instrument)

    async def test_socket_resource_uses_newline_termination(self):
        self.auto._options = {"visaResource": "TCPIP0::192.0.2.1::5555::SOCKET"}
        self.assertTrue(await self.auto._io(self.auto._connectDmm))
        self.assertEqual(self.instrument.read_termination, "\n")
        self.assertEqual(self.instrument.write_termination, "\n")

    async def test_timeout_is_preserved_in_connection_error(self):
        self.instrument.idn_error = TimeoutError("VI_ERROR_TMO")
        with self.assertRaisesRegex(RuntimeError, "USB0::.*VI_ERROR_TMO"):
            await self.auto._io(self.auto._connectDmm)
        self.assertIsNone(self.auto._dmm)
        self.assertTrue(self.instrument.closed)
        self.assertEqual(self.messages, [])  # The worker must never call the WebSocket sender.

    async def test_connection_error_is_sent_by_real_enginectrl_on_main_loop(self):
        self.rm.list_resources.side_effect = OSError("USB enumeration failed")
        ctrl = object.__new__(EngineCtrl)
        ctrl._module = None
        called_threads = []

        async def ws_send(message):
            called_threads.append(threading.get_ident())
            self.messages.append(message)

        ctrl._wsSend = ws_send
        self.auto._send = ctrl._send
        for method in (self.auto._start, self.auto._startVerify):
            with self.subTest(method=method.__name__):
                self.messages.clear()
                await method({"ch": 0})
                await asyncio.sleep(0)
                self.assertEqual(len(self.messages), 1)
                self.assertEqual(self.messages[0]["type"], "calError")
                self.assertIn("USB enumeration failed", self.messages[0]["msg"])
                self.assertNotIn("event loop", self.messages[0]["msg"])
                self.assertFalse(self.auto.is_running())
        self.assertEqual(called_threads, [threading.get_ident()] * 2)
        self.assertNotIn(True, self.amplifier)

    async def test_empty_enumeration_is_reported_without_output(self):
        self.rm.list_resources.return_value = ()
        await self.auto._start({"ch": 0})
        self.assertIn("No VISA DMM resources", self.messages[-1]["msg"])
        self.assertNotIn(True, self.amplifier)

    async def test_stop_during_connection_never_starts_output(self):
        entered, release = threading.Event(), threading.Event()
        original = self.instrument.query

        def query(command):
            if command == "*IDN?":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test worker not released")
            return original(command)

        self.instrument.query = query
        task = asyncio.create_task(self.auto._start({"ch": 0}))
        try:
            while not entered.is_set() and not task.done():
                await asyncio.sleep(0.001)
            self.assertTrue(entered.is_set())
            self.auto._stop()
        finally:
            release.set()
            await task
        self.assertNotIn(True, self.amplifier)
        self.assertEqual(self.frames, [HWCodec.FRAME_SYS_RESET])


class CalibrationBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_experiment_rechecks_calibration_after_stop_ack(self):
        ctrl = object.__new__(EngineCtrl)
        ctrl.autoCal = type("CalibrationState", (), {"running": False, "is_running": lambda self: self.running})()
        ctrl._stopEvent = asyncio.Event()
        ctrl._startLock = asyncio.Lock()
        errors = []
        ctrl.sendError = errors.append

        def terminate(_target):
            ctrl.autoCal.running = True
            ctrl._stopEvent.set()

        ctrl.engine = type("Engine", (), {"manualTrig": staticmethod(terminate)})()
        await ctrl.startTest({"module": "harmonic_test"})
        self.assertEqual(len(errors), 1)
        self.assertTrue(ctrl._stopEvent.is_set())

    async def test_calibration_frames_use_existing_ack_accounting(self):
        loop = asyncio.get_running_loop()
        bridge = EngineBridge(loop, loop, None)

        class Gateway:
            engine = None

            def SendBytes(self, _frame):
                self.engine.HandleHwFeedback(123, 0)

        gateway = Gateway()
        engine = USEEngine(gateway, None)
        gateway.engine = engine
        bridge.bind(engine)
        result = await bridge.sendFrames([HWCodec.FRAME_SYS_RESET, HWCodec.FRAME_SYS_START])
        self.assertEqual(result, 123)
        self.assertEqual(engine.sentCount, 2)
        self.assertEqual(engine.ackCounter, 2)

    async def test_saved_calibration_refreshes_zero_without_changing_debounce(self):
        files = _Files()
        self.addCleanup(files.directory.cleanup)
        bridge = EngineBridge(asyncio.get_running_loop(), asyncio.get_running_loop(), None)
        engine = USEEngine(type("Gateway", (), {"SendBytes": lambda _self, _frame: None})(), None)
        bridge.bind(engine)
        engine.setDebounce(73)
        retained = engine.nodes[0].baseFrame[-2:]
        terminal = engine.nodes[0xFFFF].baseFrame
        with patch("logic.EngineBridge.calib", files.calib):
            bridge._refreshZeroCalibration()
        self.assertEqual(engine.nodes[0].baseFrame[-2:], retained)
        self.assertIs(engine.nodes[0xFFFF].baseFrame, terminal)
        channel_one_frame = engine.nodes[0].baseFrame[HWConfig.ACTIVE_CHANNELS.index(1) + 1]
        self.assertEqual(struct.unpack(">II", channel_one_frame[5:13]), files.calib.PhysToReg(1, 0, 0, 50))


if __name__ == "__main__":
    unittest.main()
