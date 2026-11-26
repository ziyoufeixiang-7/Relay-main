"""Independent physical-output and command-order regressions; no real hardware."""
import asyncio
import copy
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ApiScanTest import ApiScanTest
from api.ApiStatSequ import ApiStatSequ
from api.ApiTimeMeas import ApiTimeMeas
from api.BaseApi import BaseApi
from logic.Calibration import Calibration
from logic.EngineCtrl import EngineCtrl
from logic.FPGACodec import HWConfig


def close_task(coroutine):
    coroutine.close()


class Fixture:
    def install(self):
        directory = tempfile.TemporaryDirectory(prefix="calibration-safety-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "calibration.json"
        Calibration.AtomicWriteJson(str(path), {str(ch): [0, -1, 1] for ch in range(16)})
        self.calib = Calibration(str(path))
        data = Calibration.EmptyFrequencyData()
        data["channels"]["0"] = {"points": [
            {"freqHz": 50, "factorAc": 1}, {"freqHz": 150, "factorAc": 2},
        ]}
        Calibration.AtomicWriteJson(self.calib.frequencyPath, data)
        self.assertTrue(self.calib.LoadCalib())
        for module in ("api.BaseApi", "logic.EngineCtrl", "logic.AutoCal"):
            target = patch(module + ".calib", self.calib)
            target.start()
            self.addCleanup(target.stop)


class Control:
    def __init__(self):
        self.state, self.nodes, self.errors, self.triggers = {}, {}, [], []
        self.running, self.di = True, 255

    def upsertNodes(self, nodes):
        self.nodes.update(nodes)
        return True

    def trigNode(self, node):
        self.triggers.append(node)
        return True

    def stopTest(self, reason=None):
        self.errors.append(reason)
        self.running = False


class Plant:
    """Decode actual compiled frames and apply an independently defined gain.

    Channel 0 gain is 1 at 50 Hz, 1/2 at 150 Hz. Registers/voltage scaling and
    interpolation are decoded here, without calling Calibration.RegToPhys.
    """
    def __init__(self):
        self.registers = {0: (0, HWConfig.ConvertFreqToReg(50))}
        self.compiler = object.__new__(EngineCtrl)

    def enter(self, node):
        for frame in self.compiler._compileNode(node).baseFrame:
            if int.from_bytes(frame[3:5], "big") & 1:
                layer = 0 if frame[2] == 255 else frame[2] + 1
                self.registers[layer] = struct.unpack(">II", frame[5:13])

    def amplitude(self, layer=1):
        amp = self.registers.get(layer, (0, 0))[0]
        signed = amp - 2**32 if amp >= 2**31 else amp
        frequency = self.registers[0][1] / HWConfig.FREQ * layer
        factor = 1 + (min(150, max(50, frequency)) - 50) / 100
        return signed / HWConfig.VOLTAGE / factor


class StaticGraphTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.install()

    def compile(self, nodes, edges, entry=1):
        api = BaseApi()
        api.ctrl = Control()
        return api.compileStaticNodes(nodes, edges, entry)

    def test_frequency_only_change_adds_inherited_harmonics(self):
        result = self.compile({1: {"0": {"0": [0, 50], "1": [10, 0], "3": [2, 45]}},
                               2: {"0": {"0": [0, 150]}}}, {1: [2]})
        self.assertEqual(set(result[2][0]), {0, 1, 3})
        self.assertAlmostEqual(result[2][0][1][0] / HWConfig.VOLTAGE, 20, places=6)
        self.assertAlmostEqual(result[2][0][3][0] / HWConfig.VOLTAGE, 4, places=6)

    def test_inheritance_follows_edges_not_array_order(self):
        result = self.compile({9: {"0": {"0": [0, 150], "1": [10, 0]}},
                               2: {"0": {"1": [7, 20]}},
                               5: {}}, {9: [5], 5: [2]}, entry=9)
        self.assertAlmostEqual(result[2][0][1][0] / HWConfig.VOLTAGE, 14, places=6)
        self.assertNotIn(0, result[2][0])  # Do not overwrite inherited DC/frequency.

    def test_loop_inheritance_reaches_fixed_point(self):
        result = self.compile({1: {"0": {"0": [0, 150], "1": [10, 0]}},
                               2: {"0": {"1": [7, 20]}}}, {1: [2], 2: [1]})
        self.assertAlmostEqual(result[2][0][1][0] / HWConfig.VOLTAGE, 14, places=6)

    def test_branch_with_ambiguous_amplitude_cannot_change_frequency(self):
        nodes = {1: {}, 2: {"0": {"1": [10, 0]}}, 3: {"0": {"1": [20, 0]}},
                 4: {"0": {"0": [0, 150]}}}
        edges = {1: [2, 3], 2: [4], 3: [4]}
        with self.assertRaisesRegex(ValueError, "Node 4.*amplitude/phase is ambiguous"):
            self.compile(nodes, edges)
        nodes[4]["0"]["1"] = [15, 0]
        result = self.compile(nodes, edges)
        self.assertAlmostEqual(result[4][0][1][0] / HWConfig.VOLTAGE, 30, delta=1e-6)

    def test_branch_with_ambiguous_frequency_cannot_change_amplitude(self):
        nodes = {1: {}, 2: {"0": {"0": [0, 50]}}, 3: {"0": {"0": [0, 150]}},
                 4: {"0": {"1": [10, 0]}}}
        with self.assertRaisesRegex(ValueError, "inherited frequency is ambiguous"):
            self.compile(nodes, {1: [2, 3], 2: [4], 3: [4]})

    def test_branch_inheritance_without_reencoding_is_allowed(self):
        nodes = {1: {}, 2: {"0": {"1": [10, 0]}}, 3: {"0": {"1": [20, 0]}}, 4: {}}
        self.assertEqual(self.compile(nodes, {1: [2, 3], 2: [4], 3: [4]})[4], {})

    def test_frequency_inheritance_is_per_channel_with_frontend_current_mapping(self):
        data = copy.deepcopy(self.calib.frequencyData)
        data["channels"]["1"] = copy.deepcopy(data["channels"]["0"])
        Calibration.AtomicWriteJson(self.calib.frequencyPath, data)
        self.assertTrue(self.calib.LoadCalib())
        result = self.compile({1: {"0": {"0": [0, 50]}, "6": {"0": [0, 150]}},
                               2: {"0": {"1": [7, 0]}, "6": {"1": [7, 0]}}}, {1: [2]})
        self.assertEqual(set(result[2]), {0, 1})
        self.assertAlmostEqual(result[2][0][1][0] / HWConfig.VOLTAGE, 7, delta=1e-6)
        self.assertAlmostEqual(result[2][1][1][0] / HWConfig.CURRENT, 14, delta=1e-6)

    def test_empty_table_preserves_legacy_partial_nodes(self):
        Calibration.AtomicWriteJson(self.calib.frequencyPath, Calibration.EmptyFrequencyData())
        self.assertTrue(self.calib.LoadCalib())
        result = self.compile({1: {"0": {"0": [0, 50], "1": [10, 0]}},
                               2: {"0": {"0": [0, 150]}}}, {1: [2]})
        self.assertEqual(set(result[2][0]), {0})
        self.assertEqual(result[1][0][1][0], HWConfig.ConvertAmpToReg(10))

    @staticmethod
    def node(node_id, static, target, delay=0):
        return {"id": node_id, "mode": "STATIC", "static": static,
                "triggers": [{"condition": "TIME_OUT", "timeout": 100,
                              "next_id": target, "post_condition_delay_ms": delay}]}

    def test_actual_sequence_frames_preserve_output_through_delay_node(self):
        ctrl, api, plant = Control(), ApiStatSequ(), Plant()
        api.setup(ctrl, {"nodes": [
            self.node(9, {"0": {"0": [0, 50], "1": [10, 0]}}, 2, delay=20),
            self.node(2, {"0": {"0": [0, 150]}}, -2),
        ]})
        self.assertEqual(ctrl.errors, [])
        plant.enter(ctrl.nodes[9])
        self.assertAlmostEqual(plant.amplitude(), 10, places=6)
        hidden_id = ctrl.nodes[9].timeoutId
        self.assertNotEqual(hidden_id, 2)
        plant.enter(ctrl.nodes[hidden_id])
        self.assertAlmostEqual(plant.amplitude(), 10, places=6)
        plant.enter(ctrl.nodes[2])
        self.assertAlmostEqual(plant.amplitude(), 10, places=6)

    def test_actual_time_measurement_frames_preserve_inherited_output(self):
        ctrl, api, plant = Control(), ApiTimeMeas(), Plant()
        nodes = [self.node(1, {"0": {"0": [0, 150], "1": [10, 0]}}, 2),
                 self.node(2, {"0": {"1": [7, 0]}}, 3),
                 self.node(3, {"0": {"0": [0, 50]}}, -2)]
        for node in nodes:
            node["do_actions"] = [{"delay": 0, "holdTime": 10} for _ in range(8)] if node["id"] == 2 else []
        with patch("asyncio.create_task", close_task):
            api.setup(ctrl, {"nodes": nodes, "timers": {
                "timer1": {"start_mode": "ENTER_STATE_2", "stop_mode": "DI_A_CLOSE"}}})
        self.assertEqual(ctrl.errors, [])
        for node_id, expected in ((1, 10), (2, 7), (3, 7)):
            plant.enter(ctrl.nodes[node_id])
            self.assertAlmostEqual(plant.amplitude(), expected, places=6)

    def test_two_updates_before_ack_use_latest_requested_amplitude(self):
        ctrl, api, plant = Control(), ApiScanTest(), Plant()
        with patch("asyncio.create_task", close_task):
            api.setup(ctrl, {"sys": {"mode": 0}, "statics": {"0": {"0": [0, 50], "1": [10, 0]}}})
        ctrl.state = copy.deepcopy(api._nodesMap[1].base)
        api.onWebCommand({"cmd": "update_static", "static": {"0": {"1": [20, 0]}}})
        api.onWebCommand({"cmd": "update_static", "static": {"0": {"0": [0, 150]}}})
        # Simulate coalescing: only the final requested node reaches hardware.
        plant.enter(ctrl.nodes[ctrl.triggers[-1]])
        self.assertAlmostEqual(plant.amplitude(), 20, places=6)
        self.assertEqual(api.statics["0"]["1"], [20, 0])
        self.assertEqual(api._startNode, ctrl.triggers[-1])

    def test_rejected_update_does_not_change_desired_state(self):
        ctrl, api = Control(), ApiScanTest()
        with patch("asyncio.create_task", close_task):
            api.setup(ctrl, {"sys": {"mode": 0}, "statics": {"0": {"0": [0, 150], "1": [10, 0]}}})
        before = copy.deepcopy(api.statics)
        with self.assertRaises(ValueError):
            api.onWebCommand({"cmd": "update_static", "static": {"0": {"1": [150, 0]}}})
        self.assertEqual(api.statics, before)
        self.assertEqual(ctrl.triggers, [])


class CommandSafetyTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.install()
        self.messages = []
        self.engine = SimpleNamespace(sendBytes=Mock(), sendFrames=AsyncMock(return_value=1),
                                      manualTrig=Mock(), setDebounce=Mock(), upsertNodes=Mock(), errorReason=None)
        self.protect = SimpleNamespace(SetAmplifierEnable=Mock(), Beep=Mock())

        async def send(message):
            self.messages.append(message)

        self.ctrl = EngineCtrl(self.engine, self.protect, send)
        self.ctrl._createApi = lambda module: ApiScanTest()

    async def start(self, amplitude=10):
        with patch("asyncio.create_task", close_task):
            await self.ctrl.HandleCommand({"module": "harmonic_test", "cmd": "start", "params": {
                "sys": {"mode": 0}, "statics": {"0": {"0": [0, 150], "1": [amplitude, 0]}}}})
        await asyncio.sleep(0)

    def assert_safe_stop_requested(self):
        self.assertFalse(self.ctrl.running)
        self.protect.SetAmplifierEnable.assert_called_with(False)
        self.engine.manualTrig.assert_called_with(0xFFFF)
        self.assertFalse(self.ctrl._stopEvent.is_set())
        self.assertTrue(any(msg["type"] == "error" for msg in self.messages))

    async def test_conversion_failure_during_start_disables_output(self):
        await self.start(150)
        self.assert_safe_stop_requested()
        api = self.ctrl.activeApi
        callbacks = []
        api.onTerminal = lambda ts: callbacks.append(("terminal", ts))
        api._onStop = lambda: callbacks.append(("stop", None))
        await self.ctrl.onEvent([0, 0xFFFF, 0, 1234])
        await asyncio.sleep(0)
        self.assertTrue(self.ctrl._stopEvent.is_set())
        self.assertIsNone(self.ctrl.activeApi)
        self.assertEqual(sum(msg["type"] == "error" for msg in self.messages), 1)
        self.assertEqual(sum(msg["type"] == "stop" for msg in self.messages), 1)
        self.assertEqual(callbacks, [("terminal", 1234), ("stop", None)])
        self.assertFalse(api.isActive)

    async def test_conversion_failure_during_update_disables_output(self):
        await self.start()
        api = self.ctrl.activeApi
        await self.ctrl.HandleCommand({"cmd": "update_static", "static": {"0": {"1": [150, 0]}}})
        await asyncio.sleep(0)
        self.assert_safe_stop_requested()
        self.assertEqual(api.statics["0"]["1"], [10, 0])

    async def test_stop_during_warmup_does_not_setup_api(self):
        api = BaseApi()
        api.setup = Mock()
        self.ctrl._createApi = lambda module: api
        task = asyncio.create_task(self.ctrl.startTest({"module": "harmonic_test"}))
        await asyncio.sleep(0)
        self.assertTrue(self.ctrl.running)
        self.ctrl.stopTest()
        await self.ctrl.onEvent([0, 0xFFFF, 0, 1234])
        await task
        api.setup.assert_not_called()
        self.protect.SetAmplifierEnable.assert_called_with(False)

    async def test_stop_cleanup_exception_cannot_block_terminal_handshake(self):
        await self.start()
        self.ctrl.activeApi._onStop = Mock(side_effect=RuntimeError("cleanup failed"))
        await self.ctrl.onEvent([0, 0xFFFF, 0, 1234])
        self.assertTrue(self.ctrl._stopEvent.is_set())
        self.assertFalse(self.ctrl.running)
        self.assertIsNone(self.ctrl.activeApi)

    async def test_compile_failure_disables_amplifier(self):
        await self.start()
        with patch.object(self.ctrl, "_compileNode", side_effect=ValueError("bad frame")):
            self.assertFalse(self.ctrl.upsertNodes({2: object()}))
        await asyncio.sleep(0)
        self.assert_safe_stop_requested()
        self.assertNotIn(2, self.ctrl.nodes)

    async def test_queued_calibration_start_and_verify_can_be_stopped(self):
        for action in ("start", "verify"):
            with self.subTest(action=action):
                auto = self.ctrl.autoCal
                auto._authenticated = True
                auto._io = AsyncMock(return_value=True)
                auto._runCalibration = AsyncMock()
                auto._runVerify = AsyncMock()
                auto.handle_command({"action": action, "ch": 0})
                self.assertTrue(auto.is_running())
                auto.handle_command({"action": "stop"})
                await asyncio.sleep(0)
                self.assertFalse(auto.is_running())
                auto._io.assert_not_awaited()
                auto._runCalibration.assert_not_awaited()
                auto._runVerify.assert_not_awaited()
                self.assertNotIn(True, [call.args[0] for call in self.protect.SetAmplifierEnable.call_args_list])

    async def test_queued_calibration_reserves_exclusive_access(self):
        auto = self.ctrl.autoCal
        auto._authenticated = True
        auto._io = AsyncMock(return_value=True)
        auto.handle_command({"action": "start", "ch": 0})
        auto.handle_command({"action": "verify", "ch": 6})
        await self.ctrl.HandleCommand({"cmd": "start", "module": "harmonic_test"})
        auto.handle_command({"action": "stop"})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(auto._options["ch"], 0)
        self.assertTrue(any("already running" in msg.get("msg", "") for msg in self.messages))
        self.assertTrue(any("Calibration is running" in msg.get("msg", "") for msg in self.messages))
        self.engine.manualTrig.assert_not_called()


if __name__ == "__main__":
    unittest.main()
