import json
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import AsyncMock, patch

import wired_network as wired
from network_manager import NetworkManager

DIRECT = '11111111-1111-1111-1111-111111111111'
AUTO = '22222222-2222-2222-2222-222222222222'


class WiredTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'wired-config.json'
        self.selection = Path(self.temp.name) / 'wired-selection.json'
        self.config.write_text(json.dumps({'direct': DIRECT, 'auto': AUTO}))
        self.patches = [patch.object(wired, 'CONFIG', self.config), patch.object(wired, 'SELECTION', self.selection)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.run = AsyncMock(return_value='')
        self.manager = wired.WiredNetwork(self.run)
        self.manager.verify = AsyncMock()
        self.manager.check_conflicts = AsyncMock()
        self.manager.current = AsyncMock(return_value=AUTO)
        self.manager.prop = AsyncMock(side_effect=lambda ident, key: 'no' if key == 'connection.autoconnect' else '')
        self.manager.status = AsyncMock(side_effect=[{'linkUp': True, 'mode': 'auto', 'addresses': ['192.168.1.130/24']},
                                                   {'linkUp': True, 'mode': 'direct', 'addresses': [wired.DIRECT_ADDRESS]}])

    async def test_direct_saves_only_after_success_and_never_touches_wifi(self):
        await self.manager.switch('direct')
        self.assertEqual(json.loads(self.selection.read_text()), {'mode': 'direct'})
        self.assertTrue(any('up' in call.args and DIRECT in call.args for call in self.run.call_args_list))
        self.assertFalse(any('wlan0' in call.args for call in self.run.call_args_list))
        self.assertFalse(any(AUTO in call.args and 'modify' in call.args for call in self.run.call_args_list))

    async def test_invalid_mode_and_missing_cable_do_not_modify_profiles(self):
        with self.assertRaises(ValueError):
            await self.manager.switch('eth1; reboot')
        self.run.assert_not_called()
        self.manager.status = AsyncMock(return_value={'linkUp': False})
        with self.assertRaisesRegex(RuntimeError, 'wired_no_link'):
            await self.manager.switch('direct')
        self.run.assert_not_called()
        self.assertFalse(self.selection.exists())

    async def test_activation_failure_restores_previous_and_keeps_saved_choice(self):
        self.selection.write_text('{"mode":"auto"}')
        async def run(*args):
            if 'up' in args and DIRECT in args:
                raise RuntimeError('network_failed')
            return ''
        self.run.side_effect = run
        with self.assertRaisesRegex(RuntimeError, 'wired_failed_restored'):
            await self.manager.switch('direct')
        self.assertEqual(json.loads(self.selection.read_text()), {'mode': 'auto'})
        self.assertTrue(any('up' in call.args and AUTO in call.args for call in self.run.call_args_list))

    async def test_failed_rollback_reports_failure_truthfully(self):
        self.run.side_effect = lambda *args: (_ for _ in ()).throw(RuntimeError('network_failed')) if 'up' in args else ''
        with self.assertRaisesRegex(RuntimeError, 'wired_restore_failed'):
            await self.manager.switch('direct')
        self.assertFalse(self.selection.exists())

    async def test_auto_uses_original_dhcp_profile_without_editing_it(self):
        self.manager.current.return_value = DIRECT
        self.manager.status = AsyncMock(side_effect=[{'linkUp': True}, {'mode': 'auto', 'addresses': ['192.168.1.130/24']}])
        await self.manager.switch('auto')
        self.assertEqual(json.loads(self.selection.read_text()), {'mode': 'auto'})
        self.assertFalse(any('modify' in c.args and AUTO in c.args for c in self.run.call_args_list))

    async def test_conflicting_interface_and_route_are_rejected(self):
        manager = wired.WiredNetwork(self.run)
        for addresses, routes in [
            ([{'ifname': 'wlan0', 'addr_info': [{'local': '192.168.60.4', 'prefixlen': 24}]}], []),
            ([], [{'dst': '192.168.60.0/24', 'dev': 'tun0'}]),
        ]:
            self.run.side_effect = [json.dumps(addresses), json.dumps(routes)]
            with self.assertRaisesRegex(RuntimeError, 'wired_subnet_conflict'):
                await manager.check_conflicts()

    async def test_existing_managed_direct_routes_are_allowed_but_not_other_interfaces(self):
        manager = wired.WiredNetwork(self.run)
        addresses = [{'ifname': 'eth0', 'addr_info': [{'local': '192.168.60.1', 'prefixlen': 24}]}]
        self.run.side_effect = [json.dumps(addresses), json.dumps([{'dst': 'default', 'dev': 'wlan0'}, {'dst': '192.168.60.0/24', 'dev': 'eth0'}, {'dst': '192.168.60.1', 'dev': 'eth0'}])]
        await manager.check_conflicts(approved_direct=True)

    async def test_boot_without_saved_wired_selection_makes_no_changes(self):
        await self.manager.restore()
        self.run.assert_not_called()

    async def test_boot_without_cable_keeps_direct_autoactivation_for_later_insertion(self):
        self.selection.write_text('{"mode":"direct"}')
        self.manager.status = AsyncMock(return_value={'linkUp': False})
        await self.manager.restore()
        self.assertTrue(any(c.args[-2:] == ('connection.autoconnect', 'yes') for c in self.run.call_args_list))
        self.assertFalse(any('up' in c.args for c in self.run.call_args_list))

    async def test_preparation_validates_existing_configuration_without_activation(self):
        self.manager.status = AsyncMock(return_value={'configured': True})
        await self.manager.prepare()
        self.run.assert_not_called()
        self.assertFalse(self.selection.exists())

    async def test_preparation_without_manifest_has_no_activation_or_gateway(self):
        self.config.unlink()
        manager = wired.WiredNetwork(self.run)
        manager.check_conflicts = AsyncMock()
        manager.current = AsyncMock(return_value=AUTO)
        manager.prop = AsyncMock(return_value='auto')
        manager.verify = AsyncMock()
        manager.status = AsyncMock(return_value={'configured': True})
        self.run.side_effect = ['', '', DIRECT]
        await manager.prepare()
        calls = [c.args for c in self.run.call_args_list]
        add = next(c for c in calls if 'add' in c)
        self.assertIn(wired.DIRECT_ADDRESS, add)
        self.assertIn('ipv4.never-default', add)
        self.assertIn('ipv6.method', add)
        self.assertEqual(add[add.index('connection.autoconnect') + 1], 'no')
        self.assertFalse(any('up' in c for c in calls))
        self.assertFalse(self.selection.exists())

    async def test_root_wired_action_disabled_by_default(self):
        with patch.dict('os.environ', {}, clear=True):
            manager = NetworkManager()
        manager.run = AsyncMock()
        with self.assertRaises(ValueError):
            await manager.dispatch({'action': 'switch_wired', 'mode': 'direct'})
        manager.run.assert_not_called()

    async def test_gateway_wired_action_is_local_idle_and_test_only(self):
        from service import Gateway, Client
        from test_gateway import Socket
        for simulation, role, owner, active, mode, expected in [
            (False, 'local', None, False, 'direct', 'invalid_network_input'),
            (True, 'remote', None, False, 'direct', 'local_only'),
            (True, 'local', 'another', False, 'direct', 'device_not_idle'),
            (True, 'local', None, True, 'direct', 'device_not_idle'),
            (True, 'local', None, False, 'eth1', 'invalid_network_input'),
        ]:
            gateway = Gateway(ui_mirror=simulation)
            gateway.upstream = Socket()
            gateway.last_load = time.monotonic()
            gateway.phase, gateway.active, gateway.owner = 'local', active, owner
            gateway.network = {'available': True}
            gateway.network_rpc = AsyncMock()
            client = Client(Socket(), role)
            await gateway.handle(client, {'gateway': True, 'action': 'network', 'operation': 'switch_wired', 'mode': mode})
            self.assertEqual(client.ws.messages[-1]['code'], expected)
            gateway.network_rpc.assert_not_called()

    async def test_gateway_forwards_only_wired_mode_not_arbitrary_interface_or_commands(self):
        from service import Gateway, Client
        from test_gateway import Socket
        gateway = Gateway(ui_mirror=True)
        gateway.upstream = Socket()
        gateway.last_load = time.monotonic()
        gateway.phase, gateway.active = 'local', False
        gateway.network = {'available': True}
        gateway.network_rpc = AsyncMock(return_value={'ok': True, 'state': {'available': True}})
        client = Client(Socket(), 'local')
        await gateway.handle(client, {'gateway': True, 'action': 'network', 'operation': 'switch_wired',
                                     'mode': 'direct', 'interface': 'wlan0', 'command': 'reboot'})
        await gateway.network_task
        gateway.network_rpc.assert_awaited_once_with({'action': 'switch_wired', 'mode': 'direct'})
        self.assertFalse(gateway.network_busy)
