import unittest
from unittest.mock import AsyncMock, patch
from network_manager import NetworkManager

class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_startup_prefers_wifi_over_remembered_hotspot(self):
        manager = NetworkManager()
        wifi = {'id': 'wifi', 'mode': 'wifi'}
        manager.profiles = AsyncMock(return_value=[{'id': 'hotspot', 'mode': 'hotspot'}, wifi])
        manager.run = AsyncMock(return_value='hotspot')
        manager.activate = AsyncMock()
        with patch('network_manager.TEST_CONFIG', {'test': True}), patch('network_manager.STATE') as state:
            state.read_text.return_value = '{"profile":"hotspot"}'
            await manager.restore()
        manager.activate.assert_awaited_once_with(wifi)

    async def test_test_startup_keeps_connected_wifi(self):
        manager = NetworkManager()
        manager.profiles = AsyncMock(return_value=[{'id': 'wifi', 'mode': 'wifi'}])
        manager.run = AsyncMock(return_value='wifi')
        manager.activate = AsyncMock()
        await manager.restore_wifi_first()
        manager.activate.assert_not_called()

    async def test_test_startup_uses_hotspot_only_without_saved_wifi(self):
        manager = NetworkManager()
        hotspot = {'id': 'hotspot', 'mode': 'hotspot'}
        manager.profiles = AsyncMock(return_value=[hotspot])
        manager.run = AsyncMock(return_value='')
        manager.activate = AsyncMock()
        with patch('network_manager.STATE') as state:
            state.read_text.side_effect = OSError()
            await manager.restore_wifi_first()
        manager.activate.assert_awaited_once_with(hotspot)

    async def test_test_startup_tries_next_wifi_before_retaining_existing_hotspot(self):
        manager = NetworkManager()
        hotspot = {'id': 'hotspot', 'mode': 'hotspot'}
        wifi1, wifi2 = {'id': 'wifi1', 'mode': 'wifi'}, {'id': 'wifi2', 'mode': 'wifi'}
        manager.profiles = AsyncMock(return_value=[hotspot, wifi1, wifi2])
        manager.run = AsyncMock(return_value='hotspot')
        manager.activate = AsyncMock(side_effect=[RuntimeError('network_failed'), None])
        with patch('network_manager.STATE') as state:
            state.read_text.return_value = '{"profile":"wifi2"}'
            await manager.restore_wifi_first()
        self.assertEqual([c.args[0] for c in manager.activate.call_args_list], [wifi2, wifi1])

    async def test_test_startup_does_not_start_hotspot_when_saved_wifi_is_unreachable(self):
        manager = NetworkManager()
        wifi = {'id': 'wifi', 'mode': 'wifi'}
        manager.profiles = AsyncMock(return_value=[wifi, {'id': 'hotspot', 'mode': 'hotspot'}])
        manager.run = AsyncMock(return_value='')
        manager.activate = AsyncMock(side_effect=RuntimeError('network_failed'))
        with patch('network_manager.STATE') as state:
            state.read_text.return_value = '{}'
            await manager.restore_wifi_first()
        manager.activate.assert_awaited_once_with(wifi)

    async def test_restore_keeps_an_already_selected_connection(self):
        manager = NetworkManager()
        manager.run = AsyncMock(return_value='current')
        manager.dispatch = AsyncMock()
        with patch('network_manager.STATE') as state:
            state.exists.return_value = True
            state.read_text.return_value = '{"profile":"current"}'
            await manager.restore()
        manager.dispatch.assert_not_called()

    async def test_restore_still_applies_a_different_saved_selection(self):
        manager = NetworkManager()
        manager.run = AsyncMock(return_value='current')
        manager.dispatch = AsyncMock()
        with patch('network_manager.STATE') as state:
            state.exists.return_value = True
            state.read_text.return_value = '{"profile":"saved"}'
            await manager.restore()
        manager.dispatch.assert_awaited_once_with({'action':'switch','profile':'saved'})

    async def test_test_mode_edits_saved_hotspot_without_switching(self):
        manager = NetworkManager()
        manager.profiles = AsyncMock(return_value=[{'id': 'saved', 'mode': 'hotspot'}])
        manager.status = AsyncMock(return_value={'available': True})
        manager.run = AsyncMock()
        manager.activate = AsyncMock()
        await manager.dispatch({'action': 'configure_hotspot', 'ssid': 'Test', 'password': '12345678'})
        self.assertEqual(manager.run.call_args.args[:5], ('/usr/bin/nmcli', 'connection', 'modify', 'uuid', 'saved'))
        manager.activate.assert_not_called()

    async def test_test_mode_initial_preparation_never_activates(self):
        manager = NetworkManager()
        manager.profiles = AsyncMock(return_value=[])
        manager.status = AsyncMock(return_value={'available': True})
        manager.run = AsyncMock()
        manager.activate = AsyncMock()
        await manager.dispatch({'action': 'configure_hotspot', 'ssid': 'Test', 'password': '12345678'})
        self.assertEqual(manager.run.call_args.args, ('/bin/bash', str(__import__('network_manager').SETUP_SCRIPT), 'wlan0', 'Test', '--prepare'))
        manager.activate.assert_not_called()

    async def test_rejects_invalid_hotspot_password_without_command(self):
        manager = NetworkManager()
        manager.run = AsyncMock()
        with self.assertRaises(ValueError):
            await manager.dispatch({'action': 'configure_hotspot', 'ssid': 'Test', 'password': 'short'})
        manager.run.assert_not_called()

    async def test_failed_switch_restores_previous_profile(self):
        manager = NetworkManager()
        old = {'id': '11111111-1111-1111-1111-111111111111', 'mode': 'hotspot'}
        new = {'id': '22222222-2222-2222-2222-222222222222', 'mode': 'wifi'}
        manager.profiles = AsyncMock(return_value=[old, new])
        manager.run = AsyncMock(return_value=old['id'])
        manager.activate = AsyncMock(side_effect=[RuntimeError('network_failed'), None])
        with self.assertRaisesRegex(RuntimeError, 'network_failed_restored'):
            await manager.dispatch({'action': 'switch', 'profile': new['id']})
        self.assertEqual([c.args[0] for c in manager.activate.call_args_list], [new, old])

    async def test_unknown_profile_never_activates(self):
        manager = NetworkManager()
        manager.profiles = AsyncMock(return_value=[])
        manager.activate = AsyncMock()
        with self.assertRaises(ValueError):
            await manager.dispatch({'action': 'switch', 'profile': '11111111-1111-1111-1111-111111111111'})
        manager.activate.assert_not_called()

    async def test_success_persists_only_after_connection_up(self):
        manager = NetworkManager()
        manager.run = AsyncMock(side_effect=RuntimeError('network_failed'))
        manager.remember = AsyncMock()
        with self.assertRaises(RuntimeError):
            await manager.activate({'id': 'profile', 'mode': 'wifi'})
        manager.remember.assert_not_called()

if __name__ == '__main__':
    unittest.main()
