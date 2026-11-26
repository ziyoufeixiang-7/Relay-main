import os
import sys
import unittest


sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from logic.EngineCtrl import EngineCtrl  # noqa: E402


class _Engine:
    errorReason = None


class _Api:
    def __init__(self):
        self.terminal_ts = None

    def onTerminal(self, hw_ts):
        self.terminal_ts = hw_ts


class EngineTerminalTimestampTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_terminal_timestamp_precedes_stop_cleanup(self):
        ctrl = object.__new__(EngineCtrl)
        ctrl.engine = _Engine()
        ctrl.errorReason = None
        ctrl.activeApi = _Api()
        observations = []

        def stop():
            observations.append(ctrl.activeApi.terminal_ts)

        ctrl._doStop = stop
        await ctrl.onEvent([0, 0xFFFF, 0, 123_456])

        self.assertEqual(observations, [123_456])

    async def test_terminal_callback_failure_does_not_block_stop(self):
        class _BrokenApi:
            @staticmethod
            def onTerminal(_hw_ts):
                raise RuntimeError("expected test failure")

        ctrl = object.__new__(EngineCtrl)
        ctrl.engine = _Engine()
        ctrl.errorReason = None
        ctrl.activeApi = _BrokenApi()
        stopped = []
        ctrl._doStop = lambda: stopped.append(True)

        await ctrl.onEvent([0, 0xFFFF, 0, 123_456])

        self.assertEqual(stopped, [True])
