"""Service installation and handover contracts; no real system services or NM."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import install_service as installer
import network_manager as network


class HandoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_handover_consumes_marker_without_selecting_network(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'preserve'
            marker.touch()
            manager = AsyncMock()
            with patch.object(network, 'PRESERVE_ONCE', marker):
                await network.restore_startup(manager)
            self.assertFalse(marker.exists())
            manager.restore.assert_not_called()
            manager.wired.restore.assert_not_called()

    async def test_normal_boot_restores_saved_selections(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = AsyncMock()
            with patch.object(network, 'PRESERVE_ONCE', Path(directory) / 'absent'):
                await network.restore_startup(manager)
            manager.restore.assert_awaited_once()
            manager.wired.restore.assert_awaited_once()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.helper = self.root / 'services/mobile_network'
        self.helper.mkdir(parents=True)
        for name in installer.FILES:
            (self.helper / name).touch()
        self.units = self.root / 'systemd'
        self.runtime = self.root / 'run'
        self.runtime.mkdir()
        self.calls = []
        self.patches = [
            patch.object(installer, 'SYSTEMD', self.units),
            patch.object(installer, 'RUN_DIR', self.runtime),
            patch.object(installer.shutil, 'which', return_value='/bin/fixture'),
            patch.object(installer.os, 'geteuid', return_value=0, create=True),
            patch.object(installer.grp, 'getgrnam'),
            patch.object(installer, 'run', side_effect=self.run_command),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_command(self, *args, **kwargs):
        self.calls.append(args)
        if args[1] in ('is-active', 'is-enabled'):
            return 'inactive' if args[1] == 'is-active' else 'disabled'
        return ''

    def install(self, *flags):
        with patch('sys.argv', ['install_service.py', '--install-dir', str(self.root), *flags]):
            installer.main()

    def test_preflight_does_not_write_or_start_services(self):
        self.install('--check')
        self.assertFalse(self.units.exists())
        self.assertEqual(self.calls, [])

    def test_installed_service_points_only_at_backend_and_preserves_current_network(self):
        self.install()
        unit = (self.units / 'relay-network-manager.service').read_text()
        backend = (self.units / 'relay-protection.service.d/mobile-network.conf').read_text()
        self.assertIn(str(self.helper / 'network_manager.py'), unit)
        self.assertNotIn('relay-control-gateway', unit)
        self.assertIn('RELAY_MIRROR_NETWORK=1', backend)
        self.assertTrue((self.runtime / 'relay-network-preserve-once').exists())
        stop = self.calls.index(('systemctl', 'stop', 'relay-network-manager.service'))
        start = self.calls.index(('systemctl', 'start', 'relay-network-manager.service'))
        self.assertLess(stop, start)
        self.assertTrue(all(call[0] == 'systemctl' for call in self.calls))

    def test_enable_only_never_starts_or_stops_network_service(self):
        self.install('--enable-only')
        self.assertFalse(any(call[1] in ('start', 'stop', 'restart') for call in self.calls))
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_missing_dependency_fails_before_any_system_change(self):
        with patch.object(installer.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'prerequisites'):
                self.install()
        self.assertFalse(self.units.exists())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
