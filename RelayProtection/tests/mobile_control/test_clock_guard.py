import json
import time
import unittest
from types import SimpleNamespace
from service import Gateway


class ClockGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.g = Gateway(ui_mirror=True)
        self.g.upstream = object()
        self.g.phase, self.g.active, self.g.last_load = 'local', False, time.monotonic()
        self.connection = SimpleNamespace(remote_address=('127.0.0.1', 1234),
                                          respond=lambda code, message: SimpleNamespace(status_code=code))

    async def test_guard_blocks_start_and_releases(self):
        reply = await self.g.http_request(self.connection, SimpleNamespace(path='/clock-guard/acquire', headers={}))
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(self.g.idle())
        token = json.loads(reply.body)['token']
        await self.g.http_request(self.connection, SimpleNamespace(path='/clock-guard/release', headers={'X-Clock-Token': token}))
        self.assertTrue(self.g.idle())

    async def test_running_or_remote_owned_cannot_acquire(self):
        for key, value in [('active', True), ('owner', 'phone'), ('pending_start', time.monotonic())]:
            original = getattr(self.g, key)
            setattr(self.g, key, value)
            reply = await self.g.http_request(self.connection, SimpleNamespace(path='/clock-guard/acquire', headers={}))
            self.assertEqual(reply.status_code, 409)
            setattr(self.g, key, original)

    async def test_network_caller_cannot_acquire(self):
        self.connection.remote_address = ('192.168.1.80', 1234)
        reply = await self.g.http_request(self.connection, SimpleNamespace(path='/clock-guard/acquire', headers={}))
        self.assertEqual(reply.status_code, 403)
