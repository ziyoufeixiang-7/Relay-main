import time
import unittest
import asyncio
from unittest.mock import patch
from service import Client, Gateway
from test_gateway import Socket


class UiMirrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_claim_reclaim_and_automatic_variable_feedback(self):
        gateway = Gateway(ui_mirror=True, independent_remote=True)
        gateway.upstream = Socket()
        gateway.phase, gateway.active, gateway.last_load = 'local', False, time.monotonic()
        gateway.clients = {client.id: client for client in (self.local, self.phone, self.other)}
        self.phone.lease = None

        async def action(client, name, **fields):
            await gateway.handle(client, {'gateway': True, 'action': name, **fields})

        await action(self.phone, 'claim')
        self.assertIn('remote_not_enabled', self.rejected(self.phone))
        await action(self.phone, 'remote_enable')
        self.assertFalse(gateway.state(self.phone)['remoteEnabled'])
        await action(self.local, 'remote_enable')
        self.assertFalse(gateway.state(self.local)['canStart'])
        await action(self.phone, 'claim')
        lease = self.phone.lease
        self.assertTrue(gateway.state(self.phone)['canStart'])
        self.assertIsNone(gateway.latest_ui)
        self.assertFalse(gateway.state(self.phone)['uiMirror'])
        self.assertNotIn('uiReady', gateway.state(self.phone))
        self.assertNotIn('mirrorEpoch', gateway.state(self.phone))

        await action(self.phone, 'command', requestId='start-ac', lease=lease,
                     command={'cmd': 'start', 'module': 'ac_test'})
        await gateway.receive_upstream({'type': 'load', 'active': True, 'module': 'ac_test'})
        update = {'type': 'value_update', 'module': 'ac_test', 'static': {'0': 20}, 'di': 0, 'do': 0}
        await gateway.receive_upstream(update)
        self.assertIn(update, self.phone.ws.messages)
        self.assertNotIn(update, self.local.ws.messages)
        self.assertNotIn(update, self.other.ws.messages)

        await action(self.local, 'reclaim')
        await gateway.receive_upstream({'type': 'load', 'active': False})
        await action(self.local, 'reclaim')
        self.assertTrue(gateway.state(self.local)['remoteStopPending'])
        self.assertFalse(gateway.state(self.local)['canStart'])
        self.assertEqual(gateway.upstream.messages.count({'cmd': 'stop'}), 1)
        await action(self.phone, 'command', requestId='late', lease=lease,
                     command={'cmd': 'update_static'})
        self.assertIn('session_expired', self.rejected(self.phone))
        await gateway.receive_upstream({'type': 'stop'})
        self.assertTrue(gateway.state(self.local)['canStart'])
        self.assertFalse(gateway.state(self.local)['remoteEnabled'])
        self.assertFalse(gateway.state(self.local)['remoteStopPending'])

    async def test_network_loop_refreshes_interface_addresses_before_broadcast(self):
        from unittest.mock import AsyncMock
        self.g.addresses = [{'host': '192.168.50.1', 'iface': 'wlan0'}]
        async def rpc(_):
            self.g.closing = True
            return {'ok': True, 'state': {'mode': 'wifi', 'addresses': ['192.168.1.43/24']}}
        async def refresh():
            self.g.addresses = [{'host': '192.168.1.43', 'iface': 'wlan0'}]
        self.g.network_rpc = AsyncMock(side_effect=rpc)
        self.g.refresh_addresses = AsyncMock(side_effect=refresh)
        with patch('service.asyncio.sleep', new=AsyncMock()):
            await self.g.network_loop()
        self.g.refresh_addresses.assert_awaited_once()
        states = [m for m in self.local.ws.messages if m.get('event') == 'state']
        self.assertEqual(states[-1]['addresses'][0]['host'], '192.168.1.43')
        self.assertEqual(states[-1]['network']['mode'], 'wifi')

    async def test_current_interface_origin_works_before_address_monitor_refresh(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        import json
        gateway = Gateway(ui_mirror=True)
        self.assertEqual(gateway.addresses, [])
        async with serve(gateway.handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f'ws://127.0.0.1:{port}/remote', origin=f'http://127.0.0.1:{port}') as ws:
                self.assertEqual(json.loads(await ws.recv())['role'], 'remote')
            async with connect(f'ws://127.0.0.1:{port}/remote', origin=f'http://192.0.2.1:{port}') as ws:
                with self.assertRaises(ConnectionClosed) as closed:
                    await ws.recv()
                self.assertEqual(closed.exception.rcvd.code, 1008)

    async def test_missing_network_service_does_not_wedge_test_controls(self):
        await self.g.handle(self.local, {'gateway': True, 'action': 'network', 'operation': 'configure_hotspot', 'ssid': 'Draft', 'password': '12345678'})
        self.assertIn('network_unavailable', self.rejected(self.local))
        self.assertFalse(self.g.network_busy)
        self.assertFalse(self.g.upstream.messages)

    async def asyncSetUp(self):
        self.g = Gateway(ui_mirror=True)
        self.g.upstream = Socket()
        self.g.phase, self.g.active, self.g.last_load = 'local', False, time.monotonic()
        self.local, self.phone, self.other = [Client(Socket(), role) for role in ['local', 'remote', 'remote']]
        self.g.clients = {c.id: c for c in [self.local, self.phone]}
        await self.g.handle(self.local, {'gateway': True, 'action': 'ui_local_snapshot', 'snapshot': {'route': '/home'}})
        await self.g.handle(self.phone, {'gateway': True, 'action': 'claim'})
        await self.g.handle(self.phone, {'gateway': True, 'action': 'ui_ready', 'lease': self.phone.lease, 'epoch': self.g.mirror_epoch})
        # Direct authorization tests inject an observer; network admission is
        # covered separately and rejects observers while an owner exists.
        self.g.clients[self.other.id] = self.other

    async def snapshot(self, client=None, seq=2, route='/ac', **extra):
        client = client or self.phone
        await self.g.handle(client, {'gateway': True, 'action': 'ui_snapshot',
            'lease': client.lease, 'epoch': self.g.mirror_epoch, 'seq': seq,
            'snapshot': {'route': route, 'tab': '1', 'module': {'vcData': []}}, **extra})

    async def command(self, cmd, module='ac_test'):
        await self.g.handle(self.phone, {'gateway': True, 'action': 'command',
            'requestId': cmd, 'lease': self.phone.lease, 'command': {'cmd': cmd, 'module': module}})

    def rejected(self, client):
        return [m.get('code') for m in client.ws.messages if m.get('event') == 'rejected']

    async def test_snapshot_broadcast_and_refresh_never_send_engine_commands(self):
        await self.snapshot()
        cached = self.g.latest_ui
        self.assertIn(cached, self.local.ws.messages)
        self.assertIn(cached, self.other.ws.messages)
        await self.g.handle(self.local, {'gateway': True, 'action': 'ui_snapshot_get'})
        self.assertEqual(self.local.ws.messages[-1], cached)
        self.assertFalse(self.g.upstream.messages)

    async def test_dc_requires_matching_display_context_and_preserves_run_route_lock(self):
        await self.snapshot(route='/dc')
        await self.command('start', 'ac_test')
        self.assertIn('ui_context_required', self.rejected(self.phone))
        self.assertFalse(self.g.upstream.messages)
        await self.command('start', 'dc_test')
        self.assertEqual(self.g.upstream.messages[-1]['module'], 'dc_test')
        await self.snapshot(seq=3, route='/home')
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/dc')
        self.assertIn('ui_route_locked', self.rejected(self.phone))

    async def test_deferred_modules_remain_unavailable_for_mirror(self):
        for path in ['/sys', '/update-config', '/auto-cal-config/main', '/iec103-auto-test']:
            await self.snapshot(route=path)
            self.assertEqual(self.g.latest_ui['snapshot']['route'], '/home')
        self.assertIn('ui_snapshot_invalid', self.rejected(self.phone))

    async def test_all_eighteen_modules_start_only_in_matching_context(self):
        from service import MIRROR_MODULES, MIRROR_DEFINITIONS
        self.assertEqual(len(MIRROR_MODULES), 19)
        for seq, (path, module) in enumerate(MIRROR_MODULES.items(), 2):
            if MIRROR_DEFINITIONS[path].get('canControlTrial') is False:
                continue
            self.g.active=False; self.g.pending_start=None; self.phone.requests.clear()
            await self.snapshot(seq=seq,route=path)
            self.assertEqual(self.g.latest_ui['snapshot']['route'],path)
            await self.command('start','forbidden_module')
            self.assertEqual(self.rejected(self.phone)[-1],'ui_context_required')
            await self.command('start',module)
            self.assertEqual(self.g.upstream.messages[-1]['module'],module)

    async def test_local_system_tool_prevents_remote_handover(self):
        await self.g.revoke()
        await self.g.handle(self.local, {'gateway':True,'action':'ui_local_snapshot',
            'snapshot':{'route':'/home','localToolOpen':True}})
        await self.g.handle(self.phone,{'gateway':True,'action':'claim'})
        self.assertIn('local_tool_open',self.rejected(self.phone))
        self.assertIsNone(self.g.owner)

    async def test_shared_modules_require_their_own_backend_context(self):
        for seq, path, module in [(2, '/dac', 'acdc_test'), (3, '/harmonic', 'harmonic_test')]:
            await self.snapshot(seq=seq, route=path)
            await self.command('start', 'ac_test')
            self.assertEqual(self.rejected(self.phone)[-1], 'ui_context_required')
            await self.command('start', module)
            self.assertEqual(self.g.upstream.messages[-1]['module'], module)
            self.g.pending_start = None
            self.g.active = False
            self.phone.requests.clear()

    async def test_non_owner_old_sequence_and_old_lease_cannot_override(self):
        await self.snapshot(seq=2)
        cached = self.g.latest_ui
        await self.snapshot(self.local, seq=3)
        await self.snapshot(self.other, seq=3)
        await self.snapshot(seq=1)
        await self.snapshot(seq=3, lease='expired')
        self.assertEqual(self.g.latest_ui, cached)
        self.assertIn('session_expired', self.rejected(self.local))
        self.assertIn('ui_sequence_invalid', self.rejected(self.phone))

    async def test_route_locked_during_run_and_stop_confirmation(self):
        await self.snapshot()
        await self.command('start')
        await self.snapshot(seq=2, route='/status-simple')
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/ac')
        await self.snapshot(seq=3)  # same-module tab/parameter display still allowed
        await self.command('stop')
        await self.snapshot(seq=4, route='/home')
        self.assertEqual(self.g.phase, 'stopping')
        await self.g.receive_upstream({'type': 'stop'})
        self.assertEqual(self.g.phase, 'stopping')
        await self.g.receive_upstream({'type': 'load', 'active': False})
        await self.snapshot(seq=5, route='/home')
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/home')
        self.assertEqual([c['cmd'] for c in self.g.upstream.messages], ['start', 'stop'])

    async def test_reclaim_revokes_snapshot_sender_but_retains_display(self):
        await self.snapshot()
        cached = self.g.latest_ui
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        await self.snapshot(seq=2)
        self.assertEqual(self.g.latest_ui, cached)
        self.assertIsNone(self.phone.lease)
        await self.g.handle(self.other, {'gateway': True, 'action': 'claim'})
        self.assertNotEqual(self.g.mirror_epoch, cached['epoch'])
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/home')

    async def test_reclaim_during_stop_revokes_immediately_without_restarting_stop(self):
        self.g.active = True
        await self.g.begin_stop('user_stop', release=False)
        first_started = self.g.stop_started
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        self.assertIsNone(self.g.owner)
        self.assertIsNone(self.phone.lease)
        self.assertEqual(self.g.phase, 'stopping')
        self.assertEqual(self.g.stop_started, first_started)
        self.assertEqual([m['cmd'] for m in self.g.upstream.messages], ['stop'])

    async def test_disconnect_during_stop_revokes_immediately(self):
        self.g.active = True
        await self.g.begin_stop('user_stop', release=False)
        self.g.clients.pop(self.phone.id)
        await self.g.begin_stop('remote_disconnected')
        self.assertIsNone(self.g.owner)
        self.assertEqual(self.g.phase, 'stopping')

    async def test_only_idle_local_can_reset_failed_display_cache(self):
        await self.g.handle(self.phone, {'gateway': True, 'action': 'ui_reset'})
        self.assertIn('local_only', self.rejected(self.phone))
        await self.g.handle(self.local, {'gateway': True, 'action': 'ui_reset'})
        self.assertIn('device_not_idle', self.rejected(self.local))
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        await self.g.handle(self.local, {'gateway': True, 'action': 'ui_reset'})
        self.assertIsNone(self.g.latest_ui)
        self.assertIsNone(self.g.mirror_epoch)
        self.assertEqual(self.g.ui_seq, 0)

    async def test_state_probe_replies_even_when_status_is_unchanged(self):
        before = len(self.other.ws.messages)
        await self.g.handle(self.other, {'gateway': True, 'action': 'state_get'})
        self.assertEqual(len(self.other.ws.messages), before + 1)
        self.assertEqual(self.other.ws.messages[-1]['event'], 'state')

    async def test_second_remote_cannot_take_control(self):
        await self.g.handle(self.other, {'gateway': True, 'action': 'claim'})
        self.assertIn('control_occupied', self.rejected(self.other))
        self.assertEqual(self.g.owner, self.phone.id)
        self.assertEqual(self.phone.lease, self.g.clients[self.phone.id].lease)

    async def test_second_browser_is_closed_as_busy_without_affecting_owner(self):
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        import json
        owner, lease = self.g.owner, self.phone.lease
        async with serve(self.g.handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f'ws://127.0.0.1:{port}/remote', origin=f'http://127.0.0.1:{port}') as ws:
                packet = json.loads(await ws.recv())
                self.assertEqual(packet['event'], 'occupied')
                with self.assertRaises(ConnectionClosed) as closed:
                    await ws.recv()
                self.assertEqual(closed.exception.rcvd.code, 4009)
        self.assertEqual((self.g.owner, self.phone.lease), (owner, lease))
        self.assertEqual(self.g.phase, 'remote')

    async def test_claim_blocks_existing_observer_and_old_session_cannot_retry(self):
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        await self.g.handle(self.phone, {'gateway': True, 'action': 'claim'})
        self.assertTrue(self.other.blocked)
        self.assertEqual(self.other.ws.messages[-1]['event'], 'occupied')
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        await self.g.handle(self.other, {'gateway': True, 'action': 'claim'})
        self.assertIsNone(self.g.owner)

    async def test_simultaneous_claims_have_exactly_one_winner(self):
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        await asyncio.gather(*(self.g.handle(client, {'gateway': True, 'action': 'claim'})
                               for client in [self.phone, self.other]))
        self.assertEqual(sum(bool(client.lease) for client in [self.phone, self.other]), 1)
        self.assertIn(self.g.owner, {self.phone.id, self.other.id})
        loser = self.other if self.g.owner == self.phone.id else self.phone
        self.assertTrue(loser.blocked)

    async def test_latest_snapshot_replaces_queued_display_without_losing_seed(self):
        import json
        self.phone.sender = object()
        seed = {'event': 'ui_seed', 'epoch': 'e', 'seq': 1}
        await self.g.deliver(self.phone, seed)
        for seq in range(2, 15):
            await self.g.deliver(self.phone, {'event': 'ui_snapshot', 'epoch': 'e', 'seq': seq})
        self.assertEqual(self.phone.outbox.qsize(), 2)
        first, first_size = self.phone.outbox.get_nowait()
        last, last_size = self.phone.outbox.get_nowait()
        self.phone.outbox.task_done(); self.phone.outbox.task_done()
        self.assertEqual(json.loads(first), seed)
        self.assertEqual(json.loads(last)['seq'], 14)
        self.assertEqual(self.phone.queued_bytes, first_size + last_size)

    async def test_probe_response_echoes_current_request_id(self):
        await self.g.handle(self.phone, {'gateway': True, 'action': 'state_get', 'probeId': 'current-probe'})
        self.assertEqual(self.phone.ws.messages[-1]['probeId'], 'current-probe')

    async def test_coalescing_never_moves_a_snapshot_across_stop(self):
        import json
        self.phone.sender = object()
        for packet in [{'event': 'ui_snapshot', 'epoch': 'e', 'seq': 2}, {'type': 'stop'},
                       {'event': 'ui_snapshot', 'epoch': 'e', 'seq': 3}]:
            await self.g.deliver(self.phone, packet)
        packets = []
        while not self.phone.outbox.empty():
            raw, _ = self.phone.outbox.get_nowait()
            self.phone.outbox.task_done()
            packets.append(json.loads(raw))
        self.assertEqual([packet.get('seq', packet.get('type')) for packet in packets], [2, 'stop', 3])

    async def test_non_owner_remote_does_not_get_value_updates(self):
        packet = {'type': 'value_update', 'module': 'ac_test', 'static': {'1': 1}, 'di': 0, 'do': 0}
        self.phone.ws.messages.clear()
        self.other.ws.messages.clear()
        self.local.ws.messages.clear()
        await self.g.receive_upstream(packet)
        self.assertIn(packet, self.phone.ws.messages)
        self.assertIn(packet, self.local.ws.messages)
        self.assertNotIn(packet, self.other.ws.messages)

    async def test_slow_remote_drops_only_noncritical_diagnostic_packet(self):
        self.phone.outbox = __import__('asyncio').Queue(maxsize=1)
        self.phone.sender = object()
        await self.phone.outbox.put(('{"event":"state"}', 1))
        self.phone.queued_bytes = 1
        await self.g.deliver(self.phone, {'type': 'ui_metrics'})
        self.assertFalse(self.phone.closing)
        self.assertEqual(self.phone.outbox.qsize(), 1)

    async def test_report_and_stop_keep_order_and_report_is_never_silently_dropped(self):
        report = {'type': 'report', 'module': 'ac_test', 'data': {'tripTime': 31}}
        self.phone.ws.messages.clear()
        await self.g.receive_upstream(report)
        await self.g.receive_upstream({'type': 'stop'})
        self.assertEqual([m['type'] for m in self.phone.ws.messages[:2]], ['report', 'stop'])
        self.phone.outbox = asyncio.Queue(maxsize=1)
        self.phone.sender = object()
        await self.phone.outbox.put(('queued', 1))
        self.phone.queued_bytes = 1
        await self.g.deliver(self.phone, report)
        self.assertTrue(self.phone.closing)
        await asyncio.gather(*list(self.g.close_tasks))

    async def test_hotspot_send_grace_does_not_extend_control_lease(self):
        self.assertEqual(self.g.send_timeout, 8)
        self.assertEqual(self.g.lease_timeout, 6)
        self.assertEqual(Gateway(ui_mirror=False).send_timeout, 2)

    async def test_missing_or_mismatched_context_blocks_remote_start(self):
        await self.command('start')
        self.assertFalse(self.g.upstream.messages)
        await self.snapshot(route='/status-simple')
        await self.command('start')
        self.assertFalse(self.g.upstream.messages)
        await self.command('start', 'universal_sequence')
        self.assertEqual(len(self.g.upstream.messages), 1)

    async def test_unsupported_and_oversized_snapshots_rejected(self):
        await self.snapshot(route='/waveform-playback')
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/waveform-playback')
        await self.snapshot(snapshot={'route': '/ac', 'module': 'x' * (256*1024)})
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/waveform-playback')
        self.assertFalse(self.g.upstream.messages)

    async def test_waveform_display_does_not_allow_output(self):
        await self.snapshot(route='/waveform-playback')
        await self.command('start', 'waveform_playback')
        self.assertIn('display_only', self.rejected(self.phone))
        self.assertFalse(self.g.upstream.messages)

    async def test_production_gateway_does_not_enable_mirror(self):
        self.g.ui_mirror = False
        await self.snapshot()
        self.assertNotIn('uiMirror', self.g.state(self.local))
        self.assertEqual(self.g.latest_ui['snapshot']['route'], '/home')
        self.assertIn('unsupported_action', self.rejected(self.phone))

    async def test_unchanged_states_keepalive_once_per_second_changes_are_immediate(self):
        # SetUp adds an observer after the last state, changing remoteClients.
        # Establish the actual baseline before testing identical-state coalescing.
        await self.g.states(force=True)
        baseline = time.monotonic()
        self.phone.last_state_sent = baseline
        self.phone.ws.messages.clear()
        with patch('service.time.monotonic', return_value=baseline + .2):
            await self.g.states()
            self.assertFalse(self.phone.ws.messages)
            self.g.active = True
            await self.g.states()
            self.assertTrue(self.phone.ws.messages[-1]['active'])
            self.g.active = False
            await self.g.states()
            self.assertFalse(self.phone.ws.messages[-1]['active'])
            await self.g.states()
            self.assertEqual(len(self.phone.ws.messages), 2)
        with patch('service.time.monotonic', return_value=baseline + 1.21):
            await self.g.states()
            self.assertEqual(len(self.phone.ws.messages), 3)

    async def test_stop_error_and_reclaim_are_not_delayed_by_state_coalescing(self):
        self.phone.ws.messages.clear()
        await self.g.receive_upstream({'type': 'error', 'code': 'simulated'})
        self.assertEqual([m.get('type') for m in self.phone.ws.messages], ['error', 'gateway'])
        await self.g.receive_upstream({'type': 'stop'})
        self.assertEqual([m.get('type') for m in self.phone.ws.messages[-2:]], ['stop', 'gateway'])
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        self.assertEqual(self.phone.ws.messages[-1]['owner'], 'local')
        self.assertIsNone(self.phone.ws.messages[-1]['lease'])

    async def test_legacy_gateway_keeps_repeated_status_notifications(self):
        self.g.ui_mirror = False
        self.phone.ws.messages.clear()
        await self.g.states()
        await self.g.states()
        self.assertEqual(len(self.phone.ws.messages), 2)

    async def test_watchdog_keeps_frontend_status_alive_without_upstream(self):
        self.g.upstream = None
        await self.g.states()
        self.phone.ws.messages.clear()
        task = asyncio.create_task(self.g.watchdog())
        try:
            await asyncio.sleep(1.3)
            self.assertEqual(len(self.phone.ws.messages), 1)
            self.assertFalse(self.phone.ws.messages[0]['upstreamOnline'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_handover_imports_local_baseline_and_blocks_commands_until_ready(self):
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        baseline = {'route': '/ac', 'module': {'voltage': 77}, 'tab': '1'}
        await self.g.handle(self.local, {'gateway': True, 'action': 'ui_local_snapshot', 'snapshot': baseline})
        await self.g.handle(self.phone, {'gateway': True, 'action': 'claim'})
        self.assertEqual(self.phone.ws.messages[-1]['event'], 'ui_seed')
        self.assertEqual(self.phone.ws.messages[-1]['snapshot'], baseline)
        self.assertFalse(self.g.state(self.phone)['canControl'])
        await self.command('start')
        self.assertFalse(self.g.upstream.messages)
        await self.g.handle(self.phone, {'gateway': True, 'action': 'ui_ready', 'lease': self.phone.lease, 'epoch': self.g.mirror_epoch})
        await self.command('start')
        self.assertEqual(len(self.g.upstream.messages), 1)

    async def test_no_local_page_or_stale_baseline_rejects_claim(self):
        await self.g.handle(self.local, {'gateway': True, 'action': 'reclaim'})
        self.g.local_ui_seen = time.monotonic() - 4
        await self.g.handle(self.other, {'gateway': True, 'action': 'claim'})
        self.assertIn('ui_local_required', self.rejected(self.other))
        self.assertIsNone(self.g.owner)

    async def test_release_running_trial_automatically_restores_local_after_stop(self):
        await self.snapshot(route='/ac')
        await self.command('start')
        await self.g.receive_upstream({'type': 'load', 'active': True, 'module': 'ac_test'})
        await self.g.handle(self.phone, {'gateway': True, 'action': 'release'})
        self.assertIsNone(self.g.owner)
        self.assertIsNone(self.phone.lease)
        self.assertEqual(self.g.phase, 'stopping')
        self.assertFalse(self.g.state(self.local)['canStart'])
        await self.g.receive_upstream({'type': 'stop'})
        await self.g.receive_upstream({'type': 'load', 'active': False})
        self.assertTrue(self.g.state(self.local)['canControl'])
        self.assertTrue(self.g.state(self.local)['canStart'])
        self.assertEqual([m['cmd'] for m in self.g.upstream.messages], ['start', 'stop'])


class IndependentStopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = Gateway(ui_mirror=True, independent_remote=True)
        self.gateway.upstream = Socket()
        self.gateway.phase = 'remote'
        self.gateway.active = True
        self.gateway.last_load = time.monotonic()
        self.local = Client(Socket(), 'local')
        self.phone = Client(Socket(), 'remote')
        self.gateway.clients = {c.id: c for c in (self.local, self.phone)}
        self.gateway.owner = self.phone.id
        self.phone.lease = 'lease'
        self.gateway.remote_enabled_by = self.local.id
        await self.gateway.begin_stop('local_reclaimed')

    async def status(self, active, elapsed):
        with patch('service.time', wraps=time) as clock:
            clock.monotonic.return_value = self.gateway.stop_sent_at + elapsed
            await self.gateway.receive_upstream({'type': 'load', 'active': active})

    async def test_idle_loads_do_not_replace_stop_confirmation(self):
        self.assertFalse(self.gateway.state(self.local)['remoteEnabled'])
        self.assertTrue(self.gateway.state(self.local)['remoteStopPending'])
        await self.status(False, .05)
        self.assertFalse(self.gateway.state(self.local)['canStart'])
        await self.status(False, .25)
        self.assertEqual(self.gateway.phase, 'stopping')
        self.assertTrue(self.gateway.remote_stop_pending)
        self.assertFalse(self.gateway.stop_seen)
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertEqual(self.gateway.phase, 'local')
        self.assertTrue(self.gateway.state(self.local)['canStart'])
        self.assertFalse(self.gateway.remote_stop_pending)

    async def test_burst_or_intervening_running_status_cannot_confirm_stop(self):
        await self.status(False, .01)
        await self.status(False, .02)
        self.assertTrue(self.gateway.remote_stop_pending)
        await self.status(True, .2)
        await self.status(False, .3)
        self.assertTrue(self.gateway.remote_stop_pending)
        await self.status(False, .5)
        self.assertTrue(self.gateway.remote_stop_pending)
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertFalse(self.gateway.remote_stop_pending)

    async def test_repeated_reclaim_preserves_confirmation_and_single_stop(self):
        await self.status(False, .05)
        started = self.gateway.stop_started
        await self.gateway.begin_stop('local_reclaimed')
        self.assertEqual(self.gateway.stop_started, started)
        self.assertEqual(self.gateway.upstream.messages, [{'cmd': 'stop'}])
        await self.status(False, .25)
        self.assertFalse(self.gateway.state(self.local)['canStart'])
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertTrue(self.gateway.state(self.local)['canStart'])

    async def test_only_late_stop_recovers_stop_timeout_fault(self):
        self.gateway.phase, self.gateway.reason = 'fault', 'stop_unconfirmed'
        await self.status(False, .05)
        self.assertTrue(self.gateway.remote_stop_pending)
        await self.status(False, .25)
        self.assertEqual(self.gateway.phase, 'fault')
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertEqual(self.gateway.phase, 'local')
        self.assertTrue(self.gateway.state(self.local)['canStart'])


class IndependentStartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gateway = Gateway(ui_mirror=True, independent_remote=True)
        self.gateway.upstream = Socket()
        self.gateway.phase, self.gateway.active, self.gateway.last_load = 'local', False, time.monotonic()
        self.local, self.phone = Client(Socket(), 'local'), Client(Socket(), 'remote')
        self.gateway.clients = {c.id: c for c in (self.local, self.phone)}
        await self.gateway.handle(self.local, {'gateway': True, 'action': 'remote_enable'})
        await self.gateway.handle(self.phone, {'gateway': True, 'action': 'claim'})
        await self.gateway.handle(self.phone, {'gateway': True, 'action': 'command',
            'requestId': 'start', 'lease': self.phone.lease,
            'command': {'cmd': 'start', 'module': 'ac_test', 'params': {}}})

    async def test_start_requires_real_running_confirmation(self):
        state = self.gateway.state(self.phone)
        self.assertTrue(state['startPending'])
        self.assertIsNone(state['active'])
        self.assertFalse(state['canStart'])
        await self.gateway.receive_upstream({'type': 'load', 'active': False})
        self.assertTrue(self.gateway.state(self.phone)['startPending'])
        await self.gateway.receive_upstream({'type': 'load', 'active': True})
        self.assertFalse(self.gateway.state(self.phone)['startPending'])
        self.assertTrue(self.gateway.state(self.phone)['active'])

    async def test_start_timeout_stops_trial_but_keeps_online_phone_control(self):
        lease = self.phone.lease
        self.gateway.pending_start -= self.gateway.stop_timeout
        task = asyncio.create_task(self.gateway.watchdog())
        try:
            async with asyncio.timeout(1):
                while self.gateway.phase != 'stopping' or not self.gateway.stop_sent_at:
                    await asyncio.sleep(.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.gateway.owner, self.phone.id)
        self.assertEqual(self.phone.lease, lease)
        self.assertTrue(self.gateway.state(self.local)['remoteEnabled'])
        self.assertEqual(self.gateway.upstream.messages[-1], {'cmd': 'stop'})
        for elapsed in (.05, .25):
            with patch('service.time', wraps=time) as clock:
                clock.monotonic.return_value = self.gateway.stop_sent_at + elapsed
                await self.gateway.receive_upstream({'type': 'load', 'active': False})
        self.assertEqual(self.gateway.phase, 'stopping')
        await self.gateway.receive_upstream({'type': 'stop'})
        self.assertEqual(self.gateway.phase, 'remote')
        self.assertEqual(self.gateway.reason, 'start_unconfirmed')
        self.assertTrue(self.gateway.state(self.phone)['canStart'])
        self.assertEqual(self.gateway.owner, self.phone.id)

    async def test_heartbeat_loss_during_start_still_revokes_control(self):
        self.phone.heartbeat -= self.gateway.lease_timeout
        task = asyncio.create_task(self.gateway.watchdog())
        try:
            async with asyncio.timeout(1):
                while self.gateway.owner is not None:
                    await asyncio.sleep(.01)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(self.phone.lease)
        self.assertFalse(self.gateway.state(self.local)['remoteEnabled'])
