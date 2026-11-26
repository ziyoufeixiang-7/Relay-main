"""No serial, GPIO, FPGA or real network services are imported by these tests."""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from comms.mobile_control.integrated import IntegratedGateway, _EngineEndpoint
from comms.mobile_control.service import Client
from test_gateway import Socket
from websockets.asyncio.client import connect


class IntegratedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = IntegratedGateway(require_enrollment=False,
                                         host='127.0.0.1', port=0)
        self.gateway.dispatcher = type('Engine', (), {})()
        self.gateway.dispatcher.HandleCommand = AsyncMock()
        self.gateway.upstream = _EngineEndpoint(self.gateway)
        self.gateway.phase = 'local'
        self.gateway.active = False
        self.gateway.last_load = time.monotonic()
        self.local = Client(Socket(), 'local')
        self.phone = Client(Socket(), 'remote')
        self.gateway.clients = {c.id: c for c in (self.local, self.phone)}

    async def action(self, client, action, **fields):
        await self.gateway.handle(client, {'gateway': True, 'action': action, **fields})

    async def test_claim_deduplicate_reclaim_and_hardware_confirmation(self):
        await self.action(self.local, 'remote_enable')
        await self.action(self.phone, 'claim')
        lease = self.phone.lease
        command = {'cmd': 'start', 'module': 'ac_test'}
        await self.action(self.phone, 'command', requestId='one', lease=lease, command=command)
        await self.action(self.phone, 'command', requestId='one', lease=lease, command=command)
        self.gateway.dispatcher.HandleCommand.assert_awaited_once_with(command)
        await self.gateway.receive_upstream({'type': 'load', 'active': True})
        await self.action(self.local, 'reclaim')
        await self.gateway.receive_upstream({'type': 'load', 'active': False})
        self.assertFalse(self.gateway.state(self.local)['canStart'])
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertTrue(self.gateway.state(self.local)['canStart'])
        self.assertIsNone(self.phone.lease)

    async def test_admin_auth_and_maintenance_cannot_be_forwarded_by_phone(self):
        await self.action(self.local, 'remote_enable')
        await self.action(self.phone, 'claim')
        for command in ({'cmd': 'adminAuth'}, {'cmd': 'autoCal'}, {'cmd': 'sysConfig'}):
            await self.action(self.phone, 'command', requestId=command['cmd'],
                              lease=self.phone.lease, command=command)
        self.gateway.dispatcher.HandleCommand.assert_not_awaited()

    async def test_start_waits_for_actual_terminal_ack(self):
        terminal = asyncio.Event()
        self.gateway.dispatcher._stopEvent = terminal
        with self.assertRaisesRegex(RuntimeError, 'terminal'):
            await self.gateway.send_upstream({'cmd': 'start'})
        self.gateway.dispatcher.HandleCommand.assert_not_awaited()

    async def test_stale_feed_stops_without_manufacturing_idle(self):
        self.gateway.active = True
        await self.gateway.upstream.close()
        self.assertIsNone(self.gateway.upstream)
        self.assertFalse(self.gateway.fresh())
        self.gateway.dispatcher.HandleCommand.assert_awaited_once_with({'cmd': 'stop'})

    async def test_engine_events_keep_report_before_stop(self):
        worker = asyncio.create_task(self.gateway._events())
        try:
            await self.gateway.SendToClient({'type': 'report', 'data': {'value': 1}})
            await self.gateway.SendToClient({'type': 'stop'})
            await asyncio.wait_for(self.gateway.events.join(), 1)
            kinds = [m.get('type') for m in self.local.ws.messages]
            self.assertLess(kinds.index('report'), kinds.index('stop'))
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_actual_socket_serves_state_without_upstream_connection(self):
        async def monitor():
            await asyncio.Future()
        self.gateway.clients.clear()
        with patch.object(self.gateway, 'address_loop', monitor), \
             patch.object(self.gateway, 'hardware_loop', monitor):
            async with self.gateway.StartServer() as server:
                await self.gateway.SendToClient({'type': 'load', 'active': False})
                await self.gateway.events.join()
                port = server.sockets[0].getsockname()[1]
                async with connect(f'ws://127.0.0.1:{port}', origin='null') as ws:
                    state = json.loads(await ws.recv())
                    self.assertTrue(state['integratedBackend'])
                    self.assertFalse(state['simulation'])
                    self.assertEqual(state['role'], 'local')
                    await ws.send(json.dumps({'cmd': 'start'}))
                    async with asyncio.timeout(2):
                        while True:
                            reply = json.loads(await ws.recv())
                            if reply.get('event') == 'rejected':
                                self.assertEqual(reply['code'], 'gateway_protocol_required')
                                break
                self.gateway.dispatcher.HandleCommand.assert_not_awaited()

    async def test_test_device_enrollment_is_required(self):
        with patch('comms.mobile_control.integrated.load', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'enrolled'):
                IntegratedGateway(require_enrollment=True)
    async def test_remote_system_configuration_actions_allowed_but_updates_blocked(self):
        await self.action(self.local, 'remote_enable')
        await self.action(self.phone, 'claim')
        for action in ['wifiScan', 'wifiStatus', 'wifiSavedList', 'wifiConnect', 'wifiConnectSaved', 'wifiForget']:
            await self.action(self.phone, 'command', requestId=action, lease=self.phone.lease,
                              command={'cmd': 'sysConfig', 'action': action})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        calls=[c.args[0]['action'] for c in self.gateway.dispatcher.HandleCommand.await_args_list]
        self.assertEqual(calls, ['wifiScan', 'wifiStatus', 'wifiSavedList', 'wifiConnect', 'wifiConnectSaved', 'wifiForget'])
        self.gateway.dispatcher.HandleCommand.reset_mock()
        for action in ['checkUpdate', 'startUpdate', 'rollback', 'usbScan']:
            await self.action(self.phone, 'command', requestId=action, lease=self.phone.lease,
                              command={'cmd': 'sysConfig', 'action': action})
        await self.action(self.phone, 'command', requestId='cal', lease=self.phone.lease,
                          command={'cmd': 'autoCal', 'action': 'auth'})
        self.gateway.dispatcher.HandleCommand.assert_not_awaited()

    async def test_network_write_blocks_start_and_idle_disconnect_does_not_need_new_stop(self):
        terminal=asyncio.Event();terminal.set()
        self.gateway.dispatcher._stopEvent=terminal
        pending=asyncio.get_running_loop().create_future()
        self.gateway.dispatcher.HandleCommand.return_value=pending
        await self.action(self.local, 'remote_enable')
        await self.action(self.phone, 'claim')
        await self.action(self.phone, 'command', requestId='wifi', lease=self.phone.lease,
                          command={'cmd': 'sysConfig', 'action': 'wifiConnect'})
        self.assertTrue(self.gateway.network_busy)
        self.assertFalse(self.gateway.state(self.phone)['canStart'])
        await self.action(self.local, 'reclaim')
        self.assertIsNone(self.phone.lease)
        self.assertEqual(self.gateway.phase, 'local')
        self.gateway.dispatcher.HandleCommand.assert_awaited_once()
        pending.set_result(None)
        await asyncio.gather(*self.gateway.tasks)
        self.assertFalse(self.gateway.network_busy)
        self.assertTrue(self.gateway.state(self.local)['canStart'])

    async def test_production_entry_does_not_require_a_test_enrollment_file(self):
        import os
        with patch.dict(os.environ, {}, clear=True):
            gateway=IntegratedGateway()
        self.assertIsNone(gateway.config)
        self.assertEqual(gateway.port, 8080)
