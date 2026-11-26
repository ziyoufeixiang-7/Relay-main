"""Release regressions: old formulas, compiled frames, and static harmonics.

Every instrument/control endpoint is mocked. No hardware is opened or driven.
Expected legacy values follow the pre-change formulas, not the new converter.
"""
import copy
import math
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ApiDiffTest import ApiDiffTest
from api.ApiBaseTest import ApiBaseTest
from api.ApiImpdTest import ApiImpdTest
from api.ApiInvsTime import ApiInvsTime
from api.ApiLineTest import ApiLineTest
from api.ApiScanTest import ApiScanTest
from api.ApiSlipTest import ApiSlipTest
from api.ApiStatSequ import ApiStatSequ
from api.ApiTimeMeas import ApiTimeMeas
from api.BaseApi import BaseApi
from logic.Calibration import Calibration
from logic.EngineCtrl import EngineCtrl
from logic.FPGACodec import HWConfig


class _Control:
    def __init__(self):
        self.state = {channel: {} for channel in HWConfig.ACTIVE_CHANNELS}
        self.nodes = {}
        self.di = 255
        self.running = True
        self.triggers = []
        self.errors = []
        self._module = None

    def upsertNodes(self, nodes):
        self.nodes.update(nodes)
        return True

    def trigNode(self, node):
        self.triggers.append(node)
        return True

    def stopTest(self, reason=None):
        self.errors.append(reason)


def _close_task(coroutine):
    coroutine.close()
    return None


class CalibrationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="relay-calibration-compatibility-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "calibration.json"
        # Distinct coefficients expose accidental channel/per-component mixing.
        self.legacy = {str(ch): [0.0001 * ch, -0.90 - ch * 0.001,
                                 0.91 + ch * 0.002] for ch in range(16)}
        Calibration.AtomicWriteJson(str(path), self.legacy)
        self.calib = Calibration(str(path))
        self.compiler = object.__new__(EngineCtrl)

    def _install_curves(self):
        data = Calibration.EmptyFrequencyData()
        for ch in range(12):
            data["channels"][str(ch)] = {"points": [
                {"freqHz": freq, "factorAc": self._factor(ch, freq)}
                for freq in (47, 55, 155, 1000, 3000)
            ]}
        Calibration.AtomicWriteJson(self.calib.frequencyPath, data)
        self.assertTrue(self.calib.LoadCalib())

    @staticmethod
    def _factor(ch, frequency):
        return 0.8 + ch * 0.003 + min(3000, max(47, frequency)) * 0.00005

    def _old_encode(self, ch, layer, amplitude, second, delta=False):
        bias, dc, ac = self.legacy[str(ch)]
        value = amplitude * (dc if layer == 0 else ac)
        if layer == 0 and not delta:
            value += bias
        return (HWConfig.ConvertAmpToReg(value, ch in HWConfig.I_CHANNELS),
                HWConfig.ConvertFreqToReg(second) if layer == 0 else
                HWConfig.ConvertPhaseToReg(second + (0 if delta else 180)))

    def _old_map(self, physical, is_delta=False):
        return {HWConfig.MapChannel(int(ch)): {
            int(layer): list(self._old_encode(HWConfig.MapChannel(int(ch)), int(layer),
                                             values[0], values[1], is_delta))
            for layer, values in layers.items()
        } for ch, layers in physical.items()}

    def _setup(self, cls, params, old=False):
        ctrl = _Control()
        with patch("api.BaseApi.calib", self.calib), patch("asyncio.create_task", _close_task):
            api = cls()
            if old:
                api.physDictToReg = self._old_map
                # The reference path must not reuse the new graph resolver.
                api.compileStaticNodes = lambda nodes, successors, entry: {
                    node: self._old_map(data) for node, data in nodes.items()
                }
            api.setup(ctrl, copy.deepcopy(params))
        self.assertEqual(ctrl.errors, [], ctrl.errors)
        return api, ctrl

    def _assert_nodes_equal(self, expected, actual):
        self.assertEqual(set(expected), set(actual))
        for node_id in expected:
            with self.subTest(node_id=node_id):
                self.assertEqual(asdict(expected[node_id]), asdict(actual[node_id]))
                self.assertEqual(asdict(self.compiler._compileNode(expected[node_id])),
                                 asdict(self.compiler._compileNode(actual[node_id])))

    @staticmethod
    def _statics(frequency=55):
        return {str(ch): {"0": [0.01 * ch, frequency],
                          "1": [10 + ch / 10, (ch % 3) * 120],
                          "3": [1 + ch / 100, -30]}
                for ch in range(12)}

    def test_all_channels_all_65_layers_match_old_encode_and_decode(self):
        for ch in range(12):
            for layer in range(65):
                maximum = 59 if ch in HWConfig.I_CHANNELS else 199
                for amplitude in (0, 1e-8, -1e-8, 0.02, -0.02, maximum, -maximum):
                    for delta in (False, True):
                        second = (-2.5 if delta else 52.0) if layer == 0 else layer * 37.25 - 720
                        expected = self._old_encode(ch, layer, amplitude, second, delta)
                        self.assertEqual(self.calib.PhysToReg(ch, layer, amplitude, second, delta), expected)
                        amp, phase = expected
                        signed = amp - 0x100000000 if amp & 0x80000000 else amp
                        physical = signed / (HWConfig.CURRENT if ch in HWConfig.I_CHANNELS else HWConfig.VOLTAGE)
                        bias, dc, ac = self.legacy[str(ch)]
                        old_amp = (physical - bias) / dc if layer == 0 else physical / ac
                        old_second = phase / HWConfig.FREQ if layer == 0 else (
                            (((phase >> 16) & 0xFFFF) ^ 0x8000) / HWConfig.PHASE)
                        self.assertEqual(self.calib.RegToPhys(ch, layer, amp, phase),
                                         (round(old_amp, 3), round(old_second, 2)))

    def test_empty_and_missing_frequency_file_are_identical(self):
        snapshot = self.calib.GetSnapshot()
        Calibration.AtomicWriteJson(self.calib.frequencyPath, Calibration.EmptyFrequencyData())
        self.assertTrue(self.calib.LoadCalib())
        self.assertEqual(snapshot.factorsAc, self.calib.GetSnapshot().factorsAc)
        self.assertEqual(snapshot.acCurves, self.calib.GetSnapshot().acCurves)

    def test_partial_update_without_curves_adds_no_harmonics(self):
        api, ctrl = self._setup(BaseApi, {})
        ctrl.state[0] = self._old_map(self._statics())[0]
        physical = {"0": {"0": [0, 155]}}
        self.assertEqual(api.physDictToReg(physical), self._old_map(physical))

    def test_raw_register_nodes_and_telemetry_remain_legacy_with_curves(self):
        self._install_curves()
        registers = self._old_map({"0": {"0": [0, 55], "3": [10, 30]}})
        params = {"startNode": 1, "nodes": {"1": {"mode": 1, "base": registers, "timeoutMs": 20,
                                                   "timeoutId": 0xFFFF, "doActions": [3]}}}
        api, ctrl = self._setup(ApiBaseTest, params)
        self.assertEqual(ctrl.nodes[1].base, registers)
        self.assertFalse(any(api._calibrationSnapshot.acCurves))
        view = object.__new__(EngineCtrl)
        view.activeApi, view.state = api, registers
        messages = []
        view._send = messages.append
        with patch("logic.EngineCtrl.calib", self.calib):
            view.sendValueUpdate(registers)
        self.assertEqual(messages[0]["static"]["0"]["3"], [10, 30])

    def test_sweep_base_reset_reverse_and_feedback_remain_legacy_with_curves(self):
        self._install_curves()
        for reset in (False, True):
            for reverse in (False, True):
                params = {"sys": {"mode": 1, "stepTime": 10, "changeMode": int(reverse),
                                   "returnMode": 1, "doMask": 3, "doCtrlMask": 1},
                          "statics": self._statics(), "count": 4,
                          "steps": {"0": {"0": [0, 1], "1": [0.2, 3]},
                                    "6": {"1": [-0.1, -3]}},
                          "payload": {"enablePreTestReset": True, "enableStepReset": reset,
                                      "stepResetMode": 1, "resetTableData": self._statics(47)}}
                expected, _ = self._setup(ApiScanTest, params, old=True)
                actual, _ = self._setup(ApiScanTest, params)
                self._assert_nodes_equal(expected._nodesMap, actual._nodesMap)
                self.assertFalse(any(actual._calibrationSnapshot.acCurves))
                registers = actual._nodesMap[2].base[0][1]
                self.assertEqual(self.calib.RegToPhys(0, 1, *registers,
                                                     fundamentalHz=55, snapshot=actual._calibrationSnapshot)[0], 10)
                ctrl = object.__new__(EngineCtrl)
                ctrl.activeApi, ctrl.state = actual, copy.deepcopy(actual._nodesMap[2].base)
                messages = []
                ctrl._send = messages.append
                with patch("logic.EngineCtrl.calib", self.calib):
                    ctrl.sendValueUpdate(actual._nodesMap[2].base)
                self.assertEqual(messages[0]["static"]["0"]["1"][0], 10)

    def test_slip_reset_sweep_wait_nodes_match_old_frames_with_curves(self):
        self._install_curves()
        for phasor, start, endpoint in ((0, 10, 11), (1, 0, 10), (2, 55, 56)):
            params = {"sys": {"doMask": 3, "doCtrlMask": 1, "logicMask": 7},
                      "statics": self._statics(), "payload": {
                          "testType": 0, "slipVarType": "0_0", "slipPhasor": phasor,
                          "slipPhase": "0", "slipStart": start, "slipDDt": 10,
                          "actionSearchStart": endpoint, "actionSearchEnd": endpoint + 1,
                          "actionSearchStep": 1, "returnTime": 20, "waitTime": 30}}
            _, expected = self._setup(ApiSlipTest, params, old=True)
            actual_api, actual = self._setup(ApiSlipTest, params)
            self._assert_nodes_equal(expected.nodes, actual.nodes)
            self.assertFalse(any(actual_api._calibrationSnapshot.acCurves))
            self.assertEqual(expected.triggers, actual.triggers)

    def test_mixed_sequence_all_phases_use_legacy_snapshot(self):
        self._install_curves()
        params = {"nodes": [
            {"id": 4, "mode": "STATIC", "static": self._statics(), "do_actions": [{"delay": 0, "mask": 3}],
             "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": 7, "post_condition_delay_ms": 5}]},
            {"id": 7, "mode": "SWEEP", "static": self._statics(),
             "dynamic": {"stepTime": 10, "count": 3, "steps": {"0": {"1": [0.1, 2]}}},
             "triggers": [{"condition": "COUNT_OVER", "next_id": 9, "post_condition_delay_ms": 0}]},
            {"id": 9, "mode": "STATIC", "static": self._statics(155),
             "triggers": [{"condition": "TIME_OUT", "timeout": 30, "next_id": -2, "post_condition_delay_ms": 0}]},
        ]}
        _, expected = self._setup(ApiStatSequ, params, old=True)
        api, actual = self._setup(ApiStatSequ, params)
        self._assert_nodes_equal(expected.nodes, actual.nodes)
        self.assertFalse(any(api._calibrationSnapshot.acCurves))

    def test_all_static_api_classes_keep_frequency_calibration_enabled(self):
        self._install_curves()
        params = {"sys": {"mode": 0}, "nodes": [{"mode": "SWEEP"}]}
        # The extra nodes field is deliberately irrelevant to these APIs.
        for cls in (ApiDiffTest, ApiImpdTest, ApiInvsTime, ApiLineTest, ApiTimeMeas, ApiScanTest):
            with self.subTest(api=cls.__name__), patch.object(cls, "_onSetup", lambda *args: None):
                api, _ = self._setup(cls, params)
                self.assertTrue(all(api._calibrationSnapshot.acCurves[:12]))

    def test_static_harmonic_scan_uses_actual_frequency_not_layer_or_rms(self):
        self._install_curves()
        physical = self._statics(52)
        params = {"sys": {"mode": 0}, "statics": physical, "steps": {}, "count": 1}
        api, _ = self._setup(ApiScanTest, params)
        self._assert_frequency_registers(physical, api._nodesMap[1].base)

    def _assert_frequency_registers(self, physical, registers):
        for api_ch, layers in physical.items():
            hw_ch = HWConfig.MapChannel(int(api_ch))
            frequency = HWConfig.ConvertFreqToReg(layers.get("0", [0, 50])[1]) / HWConfig.FREQ
            for layer, pair in layers.items():
                layer = int(layer)
                expected = self._old_encode(hw_ch, layer, *pair) if layer == 0 else (
                    HWConfig.ConvertAmpToReg(pair[0] * self._factor(hw_ch, layer * frequency), hw_ch in HWConfig.I_CHANNELS),
                    HWConfig.ConvertPhaseToReg(pair[1] + 180),
                )
                self.assertEqual(registers[hw_ch][layer], list(expected))
                converted = self.calib.RegToPhys(hw_ch, layer, *registers[hw_ch][layer],
                                                  fundamentalHz=frequency, rounded=False)
                self.assertAlmostEqual(converted[0], pair[0], delta=2e-7)

    def test_all_64_harmonics_multi_channel_frequency_and_phase(self):
        self._install_curves()
        physical = {str(ch): {str(layer): [1 + ch / 10, layer * 3.7 - 180] for layer in range(1, 65)}
                    for ch in range(12)}
        for ch, layers in physical.items():
            layers["0"] = [0.01, 47 + int(ch)]
        api, _ = self._setup(BaseApi, {})
        self._assert_frequency_registers(physical, api.physDictToReg(physical))

    def test_static_sequence_uses_curves_without_changing_routes_or_do(self):
        self._install_curves()
        params = {"nodes": [
            {"id": 4, "mode": "STATIC", "static": self._statics(55), "do_actions": [{"delay": 0, "mask": 3}],
             "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": 7, "post_condition_delay_ms": 5}]},
            {"id": 7, "mode": "STATIC", "static": self._statics(155),
             "triggers": [{"condition": "TIME_OUT", "timeout": 30, "next_id": -2, "post_condition_delay_ms": 0}]},
        ]}
        _, old = self._setup(ApiStatSequ, params, old=True)
        _, new = self._setup(ApiStatSequ, params)
        self.assertEqual(set(old.nodes), set(new.nodes))
        for node in params["nodes"]:
            self._assert_frequency_registers(node["static"], new.nodes[node["id"]].base)
        for node_id in old.nodes:
            before, after = asdict(old.nodes[node_id]), asdict(new.nodes[node_id])
            before.pop("base")
            after.pop("base")
            self.assertEqual(before, after)

    def test_differential_ratio_and_harmonic_nodes_preserve_peak_boundary(self):
        self.assertEqual(ApiDiffTest._polar(1 + 0j), [math.sqrt(2), 0])
        self._install_curves()
        for connection, first, second in (("ext-differential", "high-y", "low-y"),
                                           ("phase-differential", "ia", "ix")):
            for harmonic in (False, True):
                project = {"activeProject": "harmonic-fixed" if harmonic else "ratio-fixed", "testPhase": "A",
                           "ratio": {"points": [{"id": 1, "ir": 5.5, "idValue": 2}]},
                           "harmonic": {"order": 3, "points": [{"id": 1, "value": 20}], "applySide": "i1"}}
                params = {"statics": {"0": {"0": [0, 50], "1": [80, 0]}}, "payload": {
                    "projectSettings": project,
                    "connectionSettings": {"type": connection, "i1Terminal": first, "i2Terminal": second},
                    "equationSettings": {"idEquation": "id_sum", "irEquation": "ir_diff_k", "kFactor": 2},
                }}
                api, _ = self._setup(ApiDiffTest, params)
                physical = (api._prepare_harmonic_point(20, 2) if harmonic else
                            api._prepare_ratio_point(5.5, 2).fault_statics)
                self._assert_frequency_registers(physical, api._build_fault_node(physical).base)

    def test_differential_single_and_multiphase_fault_frames_match_old_without_curves(self):
        for phase in ("A", "B", "C", "AB", "BC", "CA", "ABC"):
            for low_letter, clock in (("y", "12"), ("d", "11")):
                params = {"statics": {"0": {"0": [0, 50], "1": [80, 0]}}, "payload": {
                    "projectSettings": {"activeProject": "ratio-fixed", "testPhase": phase,
                                        "ratio": {"points": [{"id": 1, "ir": 6.5, "idValue": 2.142}]}},
                    "protectionSettings": {"vectorGroupLetter1": "y", "vectorGroupLetter2": low_letter,
                                           "vectorGroupHighSideClock": "0", "vectorGroupLowSideClock": clock,
                                           "phaseCorrection": "y-side", "zeroSequenceCorrection": False},
                    "equationSettings": {"idEquation": "id_sum", "irEquation": "ir_diff_k", "kFactor": 2},
                }}
                old, _ = self._setup(ApiDiffTest, params, old=True)
                new, _ = self._setup(ApiDiffTest, params)
                old_point, new_point = old._prepare_ratio_point(6.5, 2.142), new._prepare_ratio_point(6.5, 2.142)
                self.assertEqual(old_point, new_point)
                self._assert_nodes_equal(
                    {1: old._build_prefault_node(old_point.prefault_statics),
                     2: old._build_fault_node(old_point.fault_statics), 3: old._build_idle_node()},
                    {1: new._build_prefault_node(new_point.prefault_statics),
                     2: new._build_fault_node(new_point.fault_statics), 3: new._build_idle_node()},
                )

    def test_inverse_time_static_nodes_match_old_without_curves(self):
        params = {"sys": {"logicMask": 1}, "statics": {},
                  "payload": {"ratedFrequency": 50, "iBeforeFault": 0, "tBeforeFault": 20,
                              "faultLimitTime": 300, "faultType": "A_GROUND", "iDefinition": "FAULT_CURRENT",
                              "outputMode": {"voltage": "GROUP1_UABC", "current": "GROUP1_IABC"},
                              "directionality": {"enabled": False}},
                  "testPoints": [{"id": 1, "value": 3}, {"id": 2, "value": 5}]}
        _, old = self._setup(ApiInvsTime, params, old=True)
        _, new = self._setup(ApiInvsTime, params)
        self._assert_nodes_equal(old.nodes, new.nodes)
        self.assertTrue(old.nodes)

    def test_time_measurement_static_nodes_keep_routes_and_use_new_curves(self):
        self._install_curves()
        params = {"timers": {"timer1": {"start_mode": "ENTER_STATE_2", "stop_mode": "DI_A_CLOSE"}},
                  "nodes": [{"id": node_id, "mode": "STATIC", "static": self._statics(55 * node_id),
                             "do_actions": [{"delay": 0, "holdTime": 10} for _ in range(8)] if node_id == 2 else [],
                             "triggers": [{"condition": "TIME_OUT", "timeout": 20,
                                           "next_id": node_id + 1 if node_id < 3 else -2,
                                           "post_condition_delay_ms": 0}]}
                            for node_id in (1, 2, 3)]}
        _, old = self._setup(ApiTimeMeas, params, old=True)
        _, new = self._setup(ApiTimeMeas, params)
        self.assertEqual(set(new.nodes), set(old.nodes))
        self.assertTrue(new.nodes)
        for node in params["nodes"]:
            self._assert_frequency_registers(node["static"], new.nodes[node["id"]].base)
            before, after = asdict(old.nodes[node["id"]]), asdict(new.nodes[node["id"]])
            before.pop("base")
            after.pop("base")
            self.assertEqual(before, after)

    def test_line_fault_nodes_unchanged_when_frequency_table_is_empty(self):
        params = {"tests": [{"type": 0, "dir": 0, "nature": 0,
                              "points": [{"id": 1, "ft": 0, "z": 2.85, "ang": 90, "time": 1000, "i": 5}]}],
                  "payload": {"trigger": {"type": 0},
                              "ctrl": {"closeAngle": {"mode": 1, "angle": 0},
                                       "attenuation": {"tauMs": 100, "superimposeDC": True},
                                       "pt": 0, "ct": 1, "cb": {"sim": True, "trip": 20, "close": 20}},
                              "di": {"A": 2, "B": 2, "C": 2, "R": 3}, "do": {"init": 0, "mode": 0},
                              "sys": {"v": 57.735, "f": 50, "iLoad": {"amp": 0, "ang": 0}},
                              "model": {"type": 0, "kl": {"formula": 0, "p1": 0.667, "p2": 0}},
                              "output": {"vMode": 0, "iMode": 0}}}
        old, _ = self._setup(ApiLineTest, params, old=True)
        new, _ = self._setup(ApiLineTest, params)
        for fault_type in (0, 1, 2, 3, 4, 5, 6):
            point = dict(params["tests"][0]["points"][0], ft=fault_type)
            self._assert_nodes_equal(old._build_nodes(old._tests[0], point),
                                     new._build_nodes(new._tests[0], point))

    def test_frequency_only_update_preserves_all_64_existing_harmonics_and_phase(self):
        self._install_curves()
        api, ctrl = self._setup(BaseApi, {})
        physical = {"6": {"0": [0, 47], **{str(layer): [0.01 * layer, layer * 5.5] for layer in range(1, 65)}}}
        ctrl.state.update(api.physDictToReg(physical))
        before = copy.deepcopy(ctrl.state)
        after = api.physDictToReg({"6": {"0": [0, 55]}})
        self.assertEqual(set(after[1]), set(range(65)))
        for layer in range(1, 65):
            self.assertEqual(after[1][layer][1], before[1][layer][1])
            amplitude, _ = self.calib.RegToPhys(1, layer, *after[1][layer],
                                               fundamentalHz=HWConfig.ConvertFreqToReg(55) / HWConfig.FREQ,
                                               snapshot=api._calibrationSnapshot, rounded=False)
            self.assertAlmostEqual(amplitude, 0.01 * layer, delta=2e-7)


if __name__ == "__main__":
    unittest.main()
