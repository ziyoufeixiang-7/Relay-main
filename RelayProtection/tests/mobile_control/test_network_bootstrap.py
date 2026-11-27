"""No system service, network, serial or GPIO calls escape these mocks."""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from comms.mobile_control import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_first_start_installs_and_enables_network_in_this_process(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'services/mobile_network/install_service.py'
            path.parent.mkdir(parents=True)
            path.touch()
            with patch.dict(os.environ, {'RELAY_INSTALL_DIR': folder}, clear=True), \
                    patch.object(bootstrap, 'os', SimpleNamespace(name='posix', geteuid=lambda:0, environ=os.environ)), \
                    patch.object(bootstrap.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                self.assertTrue(bootstrap.ensure_network_service())
                self.assertIn('--ensure', run.call_args.args[0])
                self.assertEqual(os.environ['RELAY_MIRROR_NETWORK'], '1')
                self.assertEqual(os.environ['RELAY_NETWORK_SOCKET'], '/run/relay-network/control.sock')

    def test_explicit_opt_out_does_not_install(self):
        with patch.dict(os.environ, {'RELAY_MIRROR_NETWORK': '0'}, clear=True), \
                patch.object(bootstrap.subprocess, 'run') as run:
            self.assertFalse(bootstrap.ensure_network_service())
            run.assert_not_called()

    def test_enrolled_test_instance_keeps_its_isolated_socket(self):
        with patch.dict(os.environ, {'RELAY_TEST_DEVICE_CONFIG': '/fixture/device.json',
                                    'RELAY_MIRROR_NETWORK': '1'}, clear=True), \
                patch.object(bootstrap.subprocess, 'run') as run:
            self.assertTrue(bootstrap.ensure_network_service())
            run.assert_not_called()

    def test_failed_install_does_not_claim_network_is_enabled(self):
        with patch.dict(os.environ, {'RELAY_INSTALL_DIR': '/fixture'}, clear=True), \
                patch.object(bootstrap, 'os', SimpleNamespace(name='posix', geteuid=lambda:0, environ=os.environ)), \
                patch.object(bootstrap.Path, 'is_file', return_value=True), \
                patch.object(bootstrap.subprocess, 'run', return_value=Mock(returncode=1, stderr='missing dependency')):
            with self.assertRaisesRegex(RuntimeError, 'missing dependency'):
                bootstrap.ensure_network_service()
            self.assertNotIn('RELAY_MIRROR_NETWORK', os.environ)
