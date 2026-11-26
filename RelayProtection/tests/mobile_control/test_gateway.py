import asyncio
import json
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

from service import Client, Gateway
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

class Socket:
    def __init__(self):
        self.messages = []
        self.closed = False
    async def send(self, raw):
        self.messages.append(json.loads(raw))
    async def close(self):
        self.closed = True

class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_mode_stop_ack_after_idle_load_completes_stop(self):
        self.g.ui_mirror = True
        self.g.active = True
        await self.g.begin_stop('remote_disconnected')
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertEqual(self.g.phase, 'stopping')
        await self.g.receive_upstream({'type': 'stop'})
        self.assertEqual(self.g.phase, 'local')
        self.assertTrue(self.g.state(self.local)['canStart'])

    async def asyncSetUp(self):
        self.g = Gateway(lease_timeout=.15, stop_timeout=.2, state_timeout=.4)
        self.up = Socket()
        self.g.upstream = self.up
        self.g.phase, self.g.active, self.g.last_load = 'local', False, time.monotonic()
        self.local = Client(Socket(), 'local')
        self.remote = Client(Socket(), 'remote')
        self.other = Client(Socket(), 'remote')
        self.g.clients = {c.id: c for c in [self.local, self.remote, self.other]}

    async def test_state_reports_mobile_connections_separately_from_control(self):
        state = self.g.state(self.local)
        self.assertEqual(state['owner'], 'local')
        self.assertEqual(state['remoteClients'], 2)
        self.assertIsNone(self.remote.lease)

    async def test_slow_mobile_does_not_block_heartbeat_or_stop_confirmation(self):
        gate = asyncio.Event()
        entered = asyncio.Event()
        original_send = self.remote.ws.send
        async def stalled_send(raw):
            entered.set()
            await gate.wait()
            await original_send(raw)
        self.remote.ws.send = stalled_send
        self.remote.sender = asyncio.create_task(self.g.client_sender(self.remote))
        try:
            await self.claim()
            await asyncio.wait_for(entered.wait(), .2)
            await asyncio.wait_for(self.g.receive_upstream({'type': 'load', 'active': True}), .2)
            await asyncio.wait_for(self.command(self.remote, cmd='stop'), .2)
            await asyncio.wait_for(self.g.handle(self.remote, {'gateway': True, 'action': 'heartbeat', 'lease': self.remote.lease}), .2)
            await asyncio.wait_for(self.g.receive_upstream({'type': 'stop'}), .2)
            await asyncio.wait_for(self.g.receive_upstream({'type': 'load', 'active': False}), .2)
            self.assertTrue(self.g.state(self.remote)['canStart'])
            self.assertEqual(self.g.owner, self.remote.id)
            self.assertFalse(self.remote.ws.closed)
            gate.set()
            await asyncio.wait_for(self.remote.outbox.join(), .3)
            self.assertTrue(any(m.get('type') == 'stop' for m in self.remote.ws.messages))
        finally:
            self.remote.sender.cancel()
            await asyncio.gather(self.remote.sender, return_exceptions=True)

    async def test_stop_then_restart_keeps_same_client_and_lease(self):
        await self.claim()
        lease = self.remote.lease
        for cycle in range(3):
            await self.command(self.remote, request=f'start-{cycle}')
            await self.g.receive_upstream({'type': 'load', 'active': True})
            await self.command(self.remote, cmd='stop', request=f'stop-{cycle}')
            await self.g.receive_upstream({'type': 'stop'})
            await self.g.receive_upstream({'type': 'load', 'active': False})
            self.assertEqual(self.remote.lease, lease)
            self.assertTrue(self.g.state(self.remote)['canStart'])
            self.assertFalse(self.remote.ws.closed)
        self.assertEqual(len(self.up.messages), 6)

    async def test_remote_cannot_change_network(self):
        self.g.network_rpc = AsyncMock()
        await self.g.handle(self.remote, {'gateway': True, 'action': 'network', 'operation': 'switch'})
        self.assertIn('local_only', self.rejected(self.remote))
        self.g.network_rpc.assert_not_called()

    async def test_running_or_owned_device_cannot_change_network(self):
        self.g.network_rpc = AsyncMock()
        self.g.active = True
        await self.g.handle(self.local, {'gateway': True, 'action': 'network', 'operation': 'switch'})
        self.g.active = False
        await self.claim()
        await self.g.handle(self.local, {'gateway': True, 'action': 'network', 'operation': 'switch'})
        self.assertEqual(self.rejected(self.local).count('device_not_idle'), 2)
        self.g.network_rpc.assert_not_called()

    async def test_switch_blocks_start_and_releases_gate_after_result(self):
        gate = asyncio.Event()
        async def rpc(data):
            await gate.wait()
            return {'ok': True, 'state': {'available': True, 'mode': 'wifi'}}
        self.g.network_rpc = rpc
        self.g.refresh_addresses = AsyncMock()
        await self.g.handle(self.local, {'gateway': True, 'action': 'network', 'operation': 'switch', 'profile': 'x'})
        self.assertFalse(self.g.idle())
        await self.command(self.local)
        self.assertIn('network_busy', self.rejected(self.local))
        self.assertFalse(self.up.messages)
        gate.set()
        await self.g.network_task
        self.assertFalse(self.g.network_busy)
        self.assertTrue(self.g.idle())
        self.assertNotIn('network', self.g.state(self.remote))

    async def test_pps_requires_changes_and_expires(self):
        samples = iter([(True, {'pps0': '1'}), (True, {'pps0': '2'}),
                        (False, {'pps0': '2'}), (False, {})])
        timestamps = iter([10, 11, 14, 15])
        captured = []
        def read():
            sample = next(samples)
            if not sample[1]:
                self.g.closing = True
            return sample
        async def states():
            captured.append(dict(self.g.hardware))
        with patch.object(self.g, 'read_hardware', read), \
             patch.object(self.g, 'states', states), \
             patch('service.time.monotonic', side_effect=lambda: next(timestamps)), \
             patch('service.asyncio.to_thread', side_effect=lambda fn: fn()), \
             patch('service.asyncio.sleep', new=AsyncMock()):
            await self.g.hardware_loop()
        self.assertFalse(captured[0]['ppsFresh'])
        self.assertTrue(captured[1]['ppsFresh'])
        self.assertEqual(captured[1]['ppsSequence'], 'pps0:2')
        self.assertFalse(captured[2]['ppsFresh'])
        self.assertFalse(captured[2]['usb'])

    async def test_network_distinguishes_wired_carrier_from_wifi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, carrier in [('eth0', '0'), ('wlan0', '1'), ('lo', '1'), ('bridge0', '1')]:
                interface = root / name
                interface.mkdir()
                (interface / 'carrier').write_text(carrier)
                if name != 'bridge0':
                    (interface / 'device').mkdir()
                if name == 'wlan0':
                    (interface / 'wireless').mkdir()
                    (interface / 'operstate').write_text('up')
            with patch('service.Path', side_effect=lambda path: root if path == '/sys/class/net' else Path(path)):
                self.assertEqual(self.g.read_network(), {'net': False, 'wifi': True})
                (root / 'eth0' / 'carrier').write_text('1')
                (root / 'wlan0' / 'carrier').write_text('0')
                self.assertEqual(self.g.read_network(), {'net': True, 'wifi': False})

    async def claim(self):
        await self.g.handle(self.remote, {'gateway': True, 'action': 'claim'})
        self.assertEqual(self.g.owner, self.remote.id)

    async def command(self, client, cmd='start', request='one', lease=None):
        await self.g.handle(client, {'gateway': True, 'action': 'command',
            'requestId': request, 'lease': lease if lease is not None else client.lease,
            'command': {'cmd': cmd, 'module': 'ac_test', 'params': {}}})

    def rejected(self, client):
        return [m['code'] for m in client.ws.messages if m.get('event') == 'rejected']

    async def test_claim_and_conflicting_start(self):
        await self.claim()
        await self.g.handle(self.other, {'gateway': True, 'action': 'claim'})
        await self.command(self.local)
        self.assertIn('control_occupied', self.rejected(self.other))
        self.assertIn('not_controller', self.rejected(self.local))
        self.assertFalse(self.up.messages)

    async def test_running_local_cannot_be_taken_over(self):
        self.g.active = True
        await self.g.handle(self.remote, {'gateway': True, 'action': 'claim'})
        self.assertIn('device_not_idle', self.rejected(self.remote))

    async def test_raw_protocol_is_rejected(self):
        await self.g.handle(self.local, {'cmd': 'start'})
        self.assertIn('gateway_protocol_required', self.rejected(self.local))

    async def test_duplicate_is_forwarded_once(self):
        await self.claim()
        await self.command(self.remote)
        await self.command(self.remote)
        self.assertEqual(len(self.up.messages), 1)
        self.assertNotIn('gateway', self.up.messages[0])

    async def test_expired_lease_is_rejected(self):
        await self.claim()
        await self.command(self.remote, lease='old')
        self.assertIn('session_expired', self.rejected(self.remote))
        self.assertFalse(self.up.messages)

    async def test_remote_configuration_is_rejected(self):
        await self.claim()
        await self.command(self.remote, cmd='sysConfig')
        self.assertIn('local_only', self.rejected(self.remote))

    async def test_owner_stop_keeps_control_after_confirmation(self):
        await self.claim()
        self.g.active = True
        await self.command(self.remote, cmd='stop')
        await self.g.receive_upstream({'type': 'stop'})
        self.assertFalse(self.g.state(self.remote)['canStart'])
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertEqual(self.g.owner, self.remote.id)
        self.assertTrue(self.g.state(self.remote)['canStart'])

    async def test_local_stop_is_allowed_during_remote_control(self):
        await self.claim()
        self.g.active = True
        await self.command(self.local, cmd='stop')
        self.assertEqual(self.up.messages[-1], {'cmd': 'stop'})

    async def test_release_requires_stop_then_fresh_idle(self):
        await self.claim()
        self.g.active = True
        await self.g.handle(self.remote, {'gateway': True, 'action': 'release'})
        self.assertIsNone(self.g.owner)
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertFalse(self.g.state(self.local)['canStart'])
        await self.g.receive_upstream({'type': 'stop'})
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertTrue(self.g.state(self.local)['canStart'])

    async def test_idle_release_does_not_stop(self):
        await self.claim()
        await self.g.handle(self.remote, {'gateway': True, 'action': 'release'})
        self.assertEqual(self.g.phase, 'local')
        self.assertFalse(self.up.messages)

    async def test_status_stale_blocks_start(self):
        self.g.last_load -= 1
        await self.command(self.local)
        self.assertIn('device_unavailable', self.rejected(self.local))

    async def test_runtime_update_requires_running_owner(self):
        await self.claim()
        await self.command(self.remote, cmd='update_static')
        self.assertIn('device_not_running', self.rejected(self.remote))

    async def test_start_pending_does_not_accept_old_idle(self):
        await self.command(self.local)
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertFalse(self.g.state(self.local)['canStart'])
        await self.g.receive_upstream({'type': 'load', 'active': True})
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertTrue(self.g.state(self.local)['canStart'])

    async def test_missing_stop_confirmation_faults_and_late_confirmation_recovers(self):
        self.g.active = True
        await self.g.begin_stop('test')
        self.g.stop_started -= 1
        task = asyncio.create_task(self.g.watchdog())
        try:
            await asyncio.sleep(.08)
            self.assertEqual(self.g.phase, 'fault')
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.g.receive_upstream({'type': 'stop'})
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertTrue(self.g.state(self.local)['canStart'])

    async def test_stopped_trial_leaves_fault_so_remote_can_claim(self):
        await self.claim()
        self.g.active = False
        self.remote.heartbeat -= 1
        task = asyncio.create_task(self.g.watchdog())
        try:
            async with asyncio.timeout(.35):
                while self.g.phase != 'local' or self.g.owner is not None:
                    await asyncio.sleep(.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.g.phase, 'local')
        self.assertIsNone(self.g.owner)
        self.assertFalse(self.up.messages)
        self.assertTrue(self.g.idle())

    async def test_heartbeat_timeout_revokes_and_stops(self):
        await self.claim()
        self.g.active = True
        self.remote.heartbeat -= 1
        task = asyncio.create_task(self.g.watchdog())
        try:
            async with asyncio.timeout(.35):
                while not self.up.messages:
                    await asyncio.sleep(.01)
            self.assertIsNone(self.g.owner)
            self.assertEqual(self.up.messages[-1], {'cmd': 'stop'})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_non_controller_remote_cannot_stop(self):
        await self.claim()
        await self.command(self.other, cmd='stop')
        self.assertFalse(self.up.messages)

    async def test_heartbeat_with_old_lease_does_not_renew(self):
        await self.claim()
        self.remote.heartbeat = 1
        await self.g.handle(self.remote, {'gateway': True, 'action': 'heartbeat', 'lease': 'old'})
        self.assertEqual(self.remote.heartbeat, 1)

    async def test_events_are_delivered_to_both_clients(self):
        await self.g.receive_upstream({'type': 'stop', 'module': 'ac_test'})
        for c in [self.local, self.remote]:
            self.assertTrue(any(m.get('type') == 'stop' for m in c.ws.messages))

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_clients_use_one_upstream(self):
        connected = 0
        async def upstream(ws):
            nonlocal connected
            connected += 1
            await ws.send(json.dumps({'type': 'load', 'active': False}))
            async for raw in ws:
                pass
        async with serve(upstream, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            g = Gateway(f'ws://127.0.0.1:{port}')
            await g.start()
            try:
                async with serve(g.handler, '127.0.0.1', 0) as frontend:
                    p = frontend.sockets[0].getsockname()[1]
                    await asyncio.sleep(.05)
                    async with connect(f'ws://127.0.0.1:{p}') as local, connect(f'ws://127.0.0.1:{p}/remote', origin='http://47.98.48.17') as mobile:
                        self.assertEqual(json.loads(await local.recv())['role'], 'local')
                        self.assertEqual(json.loads(await mobile.recv())['role'], 'remote')
                        self.assertEqual(connected, 1)
            finally:
                await g.shutdown()

if __name__ == '__main__':
    unittest.main()
