import asyncio
import copy
import math
import os
import sys
import unittest


sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from api.ApiSlipTest import (  # noqa: E402
    ACTION_LATCH_ID,
    MAX_NODE_TIMEOUT_MS,
    RESET_ID,
    ROUND_DONE_ID,
    SWEEP_ID,
    WAIT_ID,
    ApiSlipTest,
)


def _statics():
    return {
        str(channel): {
            "0": [0.0, 50.0],
            "1": [10.0 + channel, float(channel)],
        }
        for channel in range(12)
    }


def _base_params(payload, sys_cfg=None):
    return {
        "sys": (
            {"doMask": 3, "doCtrlMask": 0, "logicMask": 1}
            if sys_cfg is None
            else sys_cfg
        ),
        "statics": _statics(),
        "payload": payload,
        # Old frontend fields must be harmless when this API does not read
        # them.
        "limits": {"legacy": True},
        "unused": {"anything": "is ignored"},
    }


def _type0_payload(**overrides):
    payload = {
        "testType": 0,
        "slipVarType": "0_0",
        "slipPhasor": 2,
        "slipPhase": "0",
        "slipStart": 50.0,
        "slipDDt": -10.0,
        "actionSearchStart": 50.0,
        "actionSearchEnd": 49.0,
        "actionSearchStep": -1.0,
        "returnTime": 20,
        "waitTime": 30,
    }
    payload.update(overrides)
    return payload


class _FakeCtrl:
    def __init__(self):
        self.running = True
        self.di = 0
        self.nodes = {}
        self.upsert_batches = []
        self.triggers = []
        self.reports = []
        self.stop_count = 0
        self.stop_reason = None

    def upsertNodes(self, nodes):
        if not self.running:
            return False
        batch = copy.deepcopy(nodes)
        self.upsert_batches.append(batch)
        self.nodes.update(batch)
        return True

    def trigNode(self, node_id):
        if not self.running:
            return False
        self.triggers.append(node_id)
        return True

    def sendReport(self, report):
        self.reports.append(copy.deepcopy(report))

    def stopTest(self, reason=None):
        self.stop_count += 1
        self.stop_reason = reason
        self.running = False


def _setup(params):
    ctrl = _FakeCtrl()
    api = ApiSlipTest()
    api.physDictToReg = (
        lambda value, is_delta=False: copy.deepcopy(value)
    )
    api.setup(ctrl, params)
    return api, ctrl


class SlipCompilationTests(unittest.TestCase):
    def test_zero_distance_bypasses_sweep(self):
        _, ctrl = _setup(_base_params(_type0_payload()))

        batch = ctrl.upsert_batches[0]
        self.assertEqual(batch[RESET_ID].timeoutId, WAIT_ID)
        self.assertNotIn(SWEEP_ID, batch)
        self.assertEqual(
            batch[WAIT_ID].base["0"]["0"][1], 50.0
        )
        self.assertEqual(batch[WAIT_ID].diMatchMask, 0x201)
        self.assertEqual(
            batch[WAIT_ID].diMatchId, ACTION_LATCH_ID
        )
        self.assertEqual(
            batch[WAIT_ID].timeoutId, ROUND_DONE_ID
        )
        self.assertEqual(batch[WAIT_ID].doActions, [3])
        self.assertEqual(ctrl.triggers, [RESET_ID])

    def test_do_ctrl_mask_flips_do_mask_in_both_control_nodes(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl = _setup(
            _base_params(
                payload,
                {
                    "doMask": 0b1010,
                    "doCtrlMask": 0b0110,
                    "logicMask": 1,
                },
            )
        )
        batch = ctrl.upsert_batches[0]

        self.assertEqual(batch[RESET_ID].doActions, [0b1010])
        self.assertEqual(batch[SWEEP_ID].doActions, [0b1100])
        self.assertEqual(batch[WAIT_ID].doActions, [0b1100])

        _, default_ctrl = _setup(
            _base_params(
                payload,
                {"doMask": 0b1010, "logicMask": 1},
            )
        )
        default_batch = default_ctrl.upsert_batches[0]
        self.assertEqual(default_batch[RESET_ID].doActions, [0b1010])
        self.assertEqual(default_batch[SWEEP_ID].doActions, [0b1010])
        self.assertEqual(default_batch[WAIT_ID].doActions, [0b1010])

    def test_line_voltage_mapping_and_fixed_steps(self):
        payload = {
            "testType": 1,
            "slipVarType": "2_0",
            "slipPhasor": 0,
            "slipPhase": "0",
            "slipStart": 100.0,
            "slipEnd": 90.0,
            "slipDDt": -100.0,
            "timerStartValue": 95.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl = _setup(_base_params(payload))
        batch = ctrl.upsert_batches[0]

        expected_start = 100.0 / math.sqrt(3.0)
        expected_end = 90.0 / math.sqrt(3.0)
        self.assertAlmostEqual(
            batch[RESET_ID].base["0"]["1"][0],
            expected_start,
        )
        self.assertAlmostEqual(
            batch[RESET_ID].base["1"]["1"][0],
            expected_start,
        )
        self.assertEqual(
            batch[RESET_ID].base["2"]["1"][0], 12.0
        )

        sweep = batch[SWEEP_ID]
        self.assertEqual(sweep.interval, 10)
        self.assertEqual(len(sweep.steps), 10)
        self.assertAlmostEqual(
            sweep.steps[0]["0"]["1"][0],
            -1.0 / math.sqrt(3.0),
        )
        self.assertAlmostEqual(
            batch[WAIT_ID].base["1"]["1"][0],
            expected_end,
        )
        self.assertEqual(sweep.countOverId, WAIT_ID)

    def test_non_integral_distance_has_no_tail_step(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.05,
            "slipDDt": -10.0,
            "timerStartValue": 49.5,
            "returnTime": 20,
            "waitTime": 30,
        }
        api, ctrl = _setup(_base_params(payload))
        batch = ctrl.upsert_batches[0]

        self.assertEqual(len(batch[SWEEP_ID].steps), 9)
        self.assertTrue(
            all(
                step["0"]["0"][1] == -0.1
                for step in batch[SWEEP_ID].steps
            )
        )
        self.assertEqual(
            batch[WAIT_ID].base["0"]["0"][1], 49.05
        )
        self.assertEqual(api._current_round.timer_tick, 5)
        self.assertEqual(
            ApiSlipTest._nearest_tick(
                50.0, -0.1, 10, 49.75
            ),
            2,
        )

    def test_type2_uses_search_magnitude_with_main_direction(self):
        payload = {
            "testType": 2,
            "slipVarType": "0_0",
            "slipPhasor": 0,
            "slipPhase": "0",
            "slipStart": 10.0,
            "slipEnd": 9.0,
            "slipDDt": -1.0,
            "ddtSearchStart": 20.0,
            "ddtSearchEnd": 10.0,
            "ddtSearchStep": -10.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl = _setup(_base_params(payload))
        step = ctrl.upsert_batches[0][SWEEP_ID].steps[0]
        self.assertEqual(step["0"]["1"][0], -0.2)

    def test_type3_and_type4_lock_mapping(self):
        type3 = {
            "testType": 3,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "lockPhase": "6,7",
            "lockSearchStart": 2.0,
            "lockSearchEnd": 2.0,
            "lockSearchStep": 1.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl3 = _setup(_base_params(type3))
        reset3 = ctrl3.upsert_batches[0][RESET_ID].base
        self.assertEqual(reset3["6"]["1"][0], 2.0)
        self.assertEqual(reset3["7"]["1"][0], 2.0)

        type4 = {
            "testType": 4,
            "slipVarType": "5",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "lockVarType": "2_0",
            "lockPhase": "0",
            "lockSearchStart": 30.0,
            "lockSearchEnd": 30.0,
            "lockSearchStep": 1.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl4 = _setup(_base_params(type4))
        reset4 = ctrl4.upsert_batches[0][RESET_ID].base
        for channel in range(12):
            self.assertEqual(
                reset4[str(channel)]["0"][1], 50.0
            )
        self.assertAlmostEqual(
            reset4["0"]["1"][0], 30.0 / math.sqrt(3.0)
        )
        self.assertAlmostEqual(
            reset4["1"]["1"][0], 30.0 / math.sqrt(3.0)
        )
        self.assertEqual(reset4["2"]["1"][0], 12.0)

    def test_line_voltage_rejects_comma_combination(self):
        payload = {
            "testType": 1,
            "slipVarType": "2_0",
            "slipPhasor": 0,
            "slipPhase": "0,1",
            "slipStart": 100.0,
            "slipEnd": 90.0,
            "slipDDt": -100.0,
            "timerStartValue": 95.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl = _setup(_base_params(payload))
        self.assertEqual(ctrl.stop_count, 1)
        self.assertIn(
            "exactly one line-voltage selector",
            ctrl.stop_reason,
        )
        self.assertFalse(ctrl.upsert_batches)

    def test_type2_requires_nonzero_direction_for_real_sweep(self):
        payload = {
            "testType": 2,
            "slipVarType": "0_0",
            "slipPhasor": 0,
            "slipPhase": "0",
            "slipStart": 10.0,
            "slipEnd": 11.0,
            "slipDDt": 0.0,
            "ddtSearchStart": 20.0,
            "ddtSearchEnd": 20.0,
            "ddtSearchStep": 1.0,
            "returnTime": 20,
            "waitTime": 30,
        }
        _, ctrl = _setup(_base_params(payload))
        self.assertEqual(ctrl.stop_count, 1)
        self.assertIn("non-zero direction", ctrl.stop_reason)

    def test_physical_bounds_and_timeout_upper_bound(self):
        over_limit = _base_params(
            _type0_payload(),
        )
        over_limit["statics"]["6"]["1"][0] = 61.0
        _, ctrl_limit = _setup(over_limit)
        self.assertEqual(ctrl_limit.stop_count, 1)
        self.assertIn("exceeds 60 A", ctrl_limit.stop_reason)

        timeout = _base_params(
            _type0_payload(
                returnTime=MAX_NODE_TIMEOUT_MS + 1,
            ),
        )
        _, ctrl_timeout = _setup(timeout)
        self.assertEqual(ctrl_timeout.stop_count, 1)
        self.assertIn("must be <=", ctrl_timeout.stop_reason)

    def test_later_round_register_quantization_is_prevalidated(self):
        payload = {
            "testType": 2,
            "slipVarType": "0_0",
            "slipPhasor": 0,
            "slipPhase": "0",
            "slipStart": 10.0,
            "slipEnd": 10.001,
            "slipDDt": 1.0,
            "ddtSearchStart": 0.1,
            "ddtSearchEnd": 0.000001,
            "ddtSearchStep": -0.099999,
            "returnTime": 20,
            "waitTime": 30,
        }
        ctrl = _FakeCtrl()
        api = ApiSlipTest()
        # Keep the real BaseApi calibration conversion for this test.
        api.setup(ctrl, _base_params(payload))

        self.assertEqual(ctrl.stop_count, 1)
        self.assertIn("round 2", ctrl.stop_reason)
        self.assertIn("quantizes to zero", ctrl.stop_reason)
        self.assertFalse(ctrl.upsert_batches)


class SlipRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_distance_type1_uses_wait_entry_timestamp(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 50.0,
            "slipDDt": 0.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(WAIT_ID, 0, 100_000)
        api.onDi(1, 135_000)
        api.onUpdate(ACTION_LATCH_ID, 0, 140_000)
        await asyncio.sleep(0.01)

        self.assertEqual(
            ctrl.reports,
            [{
                "testType": 1,
                "timerStartValue": 50.0,
                "actionTime": 35.0,
            }],
        )
        self.assertEqual(ctrl.stop_count, 1)

    async def test_type1_hardware_timestamp_wrap_and_nearest_tick(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 49.79,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))
        self.assertEqual(api._current_round.timer_tick, 2)

        api.onUpdate(SWEEP_ID, 0, 0xFFFF0000)
        api.onUpdate(SWEEP_ID, 2, 0xFFFFFFF0)
        api.onDi(1, 0x00002700)
        api.onUpdate(ACTION_LATCH_ID, 0, 0x00002800)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 10.0)
        self.assertEqual(ctrl.stop_count, 1)

    async def test_search_advances_at_round_done_then_reports_once(self):
        api, ctrl = _setup(_base_params(_type0_payload()))

        api.onUpdate(ROUND_DONE_ID, 0, 1_000)
        api.onUpdate(ROUND_DONE_ID, 0, 1_000)
        self.assertEqual(ctrl.triggers, [RESET_ID, RESET_ID])
        self.assertEqual(
            ctrl.upsert_batches[-1][RESET_ID].timeoutId,
            SWEEP_ID,
        )
        self.assertEqual(
            ctrl.upsert_batches[-1][WAIT_ID].base["0"]["0"][1],
            49.0,
        )

        api.onUpdate(SWEEP_ID, 0, 10_000)
        api.onDi(1, 20_000)
        api.onUpdate(ACTION_LATCH_ID, 0, 21_000)
        api.onUpdate(ACTION_LATCH_ID, 0, 21_000)
        await asyncio.sleep(0.01)

        self.assertEqual(
            ctrl.reports,
            [{"testType": 0, "actionValue": 49.0}],
        )
        self.assertEqual(ctrl.stop_count, 1)

    async def test_search_exhaustion_reports_null(self):
        payload = _type0_payload(
            actionSearchEnd=50.0,
            actionSearchStep=-1.0,
        )
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(ROUND_DONE_ID, 0, 1_000)
        self.assertEqual(
            ctrl.reports,
            [{"testType": 0, "actionValue": None}],
        )
        self.assertEqual(ctrl.stop_count, 1)

    async def test_search_action_report_units_and_compatibility(self):
        cases = [
            (
                {
                    "testType": 2,
                    "slipVarType": "0_0",
                    "slipPhasor": 0,
                    "slipPhase": "0",
                    "slipStart": 10.0,
                    "slipEnd": 9.0,
                    "slipDDt": -1.0,
                    "ddtSearchStart": 20.0,
                    "ddtSearchEnd": 10.0,
                    "ddtSearchStep": -10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {
                    "testType": 2,
                    "slipLockValue": 20.0 / math.sqrt(2.0),
                },
            ),
            (
                {
                    "testType": 2,
                    "slipVarType": "0_1",
                    "slipPhasor": 0,
                    "slipPhase": "6",
                    "slipStart": 10.0,
                    "slipEnd": 9.0,
                    "slipDDt": -1.0,
                    "ddtSearchStart": 20.0,
                    "ddtSearchEnd": 10.0,
                    "ddtSearchStep": -10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {
                    "testType": 2,
                    "slipLockValue": 20.0 / math.sqrt(2.0),
                },
            ),
            (
                {
                    "testType": 2,
                    "slipVarType": "0_0",
                    "slipPhasor": 2,
                    "slipPhase": "0",
                    "slipStart": 50.0,
                    "slipEnd": 49.0,
                    "slipDDt": -1.0,
                    "ddtSearchStart": 20.0,
                    "ddtSearchEnd": 10.0,
                    "ddtSearchStep": -10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {"testType": 2, "slipLockValue": 20.0},
            ),
            (
                {
                    "testType": 2,
                    "slipVarType": "2_0",
                    "slipPhasor": 0,
                    "slipPhase": "0",
                    "slipStart": 100.0,
                    "slipEnd": 90.0,
                    "slipDDt": -1.0,
                    "ddtSearchStart": 20.0,
                    "ddtSearchEnd": 10.0,
                    "ddtSearchStep": -10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {
                    "testType": 2,
                    "slipLockValue": 20.0 / math.sqrt(2.0),
                },
            ),
            (
                {
                    "testType": 2,
                    "slipVarType": "0_0",
                    "slipPhasor": 1,
                    "slipPhase": "0",
                    "slipStart": 0.0,
                    "slipEnd": 10.0,
                    "slipDDt": 1.0,
                    "ddtSearchStart": 20.0,
                    "ddtSearchEnd": 10.0,
                    "ddtSearchStep": -10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {"testType": 2, "slipLockValue": 20.0},
            ),
            (
                {
                    "testType": 3,
                    "slipVarType": "0_0",
                    "slipPhasor": 2,
                    "slipPhase": "0",
                    "slipStart": 50.0,
                    "slipEnd": 49.0,
                    "slipDDt": -10.0,
                    "lockPhase": "6",
                    "lockSearchStart": 2.0,
                    "lockSearchEnd": 3.0,
                    "lockSearchStep": 1.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {"testType": 3, "currentLockValue": 2.0},
            ),
            (
                {
                    "testType": 4,
                    "slipVarType": "5",
                    "slipStart": 50.0,
                    "slipEnd": 49.0,
                    "slipDDt": -10.0,
                    "lockVarType": "2_0",
                    "lockPhase": "0",
                    "lockSearchStart": 30.0,
                    "lockSearchEnd": 40.0,
                    "lockSearchStep": 10.0,
                    "returnTime": 20,
                    "waitTime": 30,
                },
                {"testType": 4, "voltageLockValue": 30.0},
            ),
        ]

        for payload, expected_report in cases:
            with self.subTest(test_type=payload["testType"]):
                api, ctrl = _setup(_base_params(payload))
                api.onUpdate(SWEEP_ID, 0, 5_000)
                api.onDi(1, 10_000)
                api.onUpdate(ACTION_LATCH_ID, 0, 11_000)
                await asyncio.sleep(0.01)
                self.assertEqual(ctrl.reports, [expected_report])
                self.assertEqual(ctrl.stop_count, 1)

    async def test_action_time_uses_first_di_not_contact_bounce(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(SWEEP_ID, 0, 5_000)
        api.onDi(1, 10_000)
        api.onDi(0, 12_000)
        api.onUpdate(ACTION_LATCH_ID, 0, 13_000)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 5.0)

    async def test_negative_ack_timestamp_does_not_advance(self):
        api, ctrl = _setup(_base_params(_type0_payload()))
        api.onUpdate(ROUND_DONE_ID, 0, -1)

        self.assertEqual(len(ctrl.upsert_batches), 1)
        self.assertEqual(ctrl.triggers, [RESET_ID])
        self.assertFalse(ctrl.reports)

    async def test_di_during_base_flush_is_not_discarded(self):
        payload = _type0_payload(
            actionSearchEnd=50.0,
            actionSearchStep=-1.0,
        )
        api, ctrl = _setup(_base_params(payload))

        # The engine installs its DI reference before base+UPDATE finishes,
        # so this valid action timestamp can precede WAIT's tick=0 ACK even
        # when its independently scheduled callback arrives afterward.
        api.onUpdate(RESET_ID, 0, 1_000)
        api.onUpdate(WAIT_ID, 0, 22_000)
        api.onUpdate(ACTION_LATCH_ID, 0, 23_000)
        api.onDi(1, 21_500)
        await asyncio.sleep(0.01)

        self.assertEqual(
            ctrl.reports,
            [{"testType": 0, "actionValue": 50.0}],
        )
        self.assertIsNone(ctrl.stop_reason)

    async def test_late_action_callback_beats_reset_di_history(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))
        api.onUpdate(RESET_ID, 0, 1_000)
        api.onDi(1, 100)
        api.onDi(0, 200)
        api.onUpdate(SWEEP_ID, 0, 30_000)

        async def publish_causal_di():
            await asyncio.sleep(0)
            api.onDi(1, 30_500)

        asyncio.create_task(publish_causal_di())
        api.onUpdate(ACTION_LATCH_ID, 0, 31_000)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 0.5)

    async def test_pre_entry_action_beats_post_entry_bounce(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(RESET_ID, 0, 1_000)
        api.onDi(1, 29_000)
        api.onUpdate(SWEEP_ID, 0, 30_000)
        api.onDi(0, 30_500)
        api.onUpdate(ACTION_LATCH_ID, 0, 31_000)
        await asyncio.sleep(0.01)

        # The first action preceded timer tick0; the later bounce must not
        # replace it with a false +0.5 ms measurement.
        self.assertIsNone(ctrl.reports[0]["actionTime"])

    async def test_reset_history_inside_pre_entry_window_is_rejected(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(RESET_ID, 0, 0)
        api.onDi(1, 19_000)
        api.onUpdate(SWEEP_ID, 0, 21_000)
        api.onDi(0, 21_500)
        api.onUpdate(ACTION_LATCH_ID, 0, 22_000)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 0.5)

    async def test_sweep_to_wait_di_window_is_recovered(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(RESET_ID, 0, 1_000)
        api.onUpdate(SWEEP_ID, 0, 100_000)
        api.onUpdate(SWEEP_ID, 10, 200_000)
        # Simulate WAIT's callback winning the race with the independently
        # queued DI callback.  The late callback still carries the earlier
        # FPGA timestamp.
        api.onUpdate(WAIT_ID, 0, 201_000)
        api.onDi(1, 200_500)
        # Even an exceptionally short WAIT must not let ROUND_DONE win the
        # race against the already scheduled action report.
        api.onUpdate(ROUND_DONE_ID, 0, 201_500)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 100.5)
        self.assertEqual(ctrl.stop_count, 1)

    async def test_sweep_to_wait_recovery_does_not_need_final_tick(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 50.0,
            "returnTime": 20,
            "waitTime": 100,
        }
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(RESET_ID, 0, 1_000)
        api.onUpdate(SWEEP_ID, 0, 100_000)
        api.onDi(1, 200_500)
        api.onUpdate(WAIT_ID, 0, 201_000)
        # The final-tick callback is delivered last but retains its original
        # hardware timestamp.
        api.onUpdate(SWEEP_ID, 10, 200_000)
        await asyncio.sleep(0.01)

        self.assertEqual(ctrl.reports[0]["actionTime"], 100.5)
        self.assertEqual(ctrl.stop_count, 1)

    async def test_reset_to_wait_reference_window_is_recovered(self):
        payload = _type0_payload(
            actionSearchEnd=50.0,
            actionSearchStep=-1.0,
        )
        api, ctrl = _setup(_base_params(payload))

        api.onUpdate(RESET_ID, 0, 0)
        api.onDi(1, 20_500)
        # WAIT captures DI=1 as its reference and therefore emits no
        # ACTION_LATCH update.  The API boundary starts at returnTime.
        api.onUpdate(WAIT_ID, 0, 21_000)
        await asyncio.sleep(0.01)

        self.assertEqual(
            ctrl.reports,
            [{"testType": 0, "actionValue": 50.0}],
        )
        self.assertEqual(ctrl.stop_count, 1)

    async def test_type1_no_action_and_external_stop_are_idempotent(self):
        payload = {
            "testType": 1,
            "slipVarType": "0_0",
            "slipPhasor": 2,
            "slipPhase": "0",
            "slipStart": 50.0,
            "slipEnd": 49.0,
            "slipDDt": -10.0,
            "timerStartValue": 49.5,
            "returnTime": 20,
            "waitTime": 100,
        }

        api_done, ctrl_done = _setup(_base_params(payload))
        api_done.onUpdate(ROUND_DONE_ID, 0, 10_000)
        self.assertEqual(
            ctrl_done.reports,
            [{
                "testType": 1,
                "timerStartValue": 49.5,
                "actionTime": None,
            }],
        )

        api_stop, ctrl_stop = _setup(_base_params(payload))
        api_stop.onStop()
        api_stop.onStop()
        self.assertEqual(
            ctrl_stop.reports,
            [{
                "testType": 1,
                "timerStartValue": 49.5,
                "actionTime": None,
            }],
        )


if __name__ == "__main__":
    unittest.main()
