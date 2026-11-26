import asyncio
import unittest
from unittest.mock import AsyncMock
from comms.WSGateway import WSGateway

class LegacySocket:
    remote_address=('127.0.0.1',1234)
    def __init__(self, messages):self.messages=iter(messages)
    def __aiter__(self):return self
    async def __anext__(self):
        try:return next(self.messages)
        except StopIteration:raise StopAsyncIteration
    async def close(self):pass

class LegacyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_direct_start_and_disconnect_stop_are_preserved(self):
        gateway=WSGateway()
        gateway.dispatcher=type('Engine',(),{'HandleCommand':AsyncMock()})()
        await gateway.Handler(LegacySocket(['{"cmd":"start","module":"ac_test"}']))
        commands=[call.args[0] for call in gateway.dispatcher.HandleCommand.await_args_list]
        self.assertEqual(commands,[{'cmd':'start','module':'ac_test'},{'cmd':'stop'}])
        self.assertIsNone(gateway.activeClient)

    async def test_malformed_and_non_object_messages_do_not_reach_engine(self):
        gateway=WSGateway()
        gateway.dispatcher=type('Engine',(),{'HandleCommand':AsyncMock()})()
        await gateway.Handler(LegacySocket(['[]','null','not json']))
        gateway.dispatcher.HandleCommand.assert_awaited_once_with({'cmd':'stop'})
