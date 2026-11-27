"""OTA network hook integration against temporary installs and fake systemctl."""
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_updater import Env, run_updater, sha256_of


class NetworkUpdateTests(unittest.TestCase):
    def scenario(self, fail_check=False, fail_install=False):
        env = Env()
        self.addCleanup(shutil.rmtree, env.root, True)
        env.make_install('1.0.0')
        previous_calibration = env.read_calib()
        usb, _ = env.make_package('1.0.1')
        archive = Path(usb) / 'backend.tar.gz'
        payload = Path(env.root) / 'payload'
        payload.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(payload, filter='data')
        helper = payload / 'services/mobile_network/install_service.py'
        helper.parent.mkdir(parents=True)
        log = Path(env.root) / 'network-installer.jsonl'
        helper.write_text(
            'import json,sys\n'
            f'with open({str(log)!r},"a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
            f'if "--check" in sys.argv and {fail_check!r}: sys.exit(21)\n'
            f'if "--check" not in sys.argv and {fail_install!r}: sys.exit(22)\n'
        )
        with tarfile.open(archive, 'w:gz') as stream:
            for path in payload.iterdir():
                stream.add(path, arcname=path.name)
        Path(str(archive) + '.sha256').write_text(sha256_of(archive) + '  backend.tar.gz\n')
        code, messages = run_updater(env, '--source', 'usb', '--usb-dir', usb)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertIn('--check', calls[0])
        self.assertIn(env.install + '.new', calls[0])
        self.assertEqual(env.read_calib(), previous_calibration)
        if fail_check or fail_install:
            self.assertNotEqual(code, 0)
            self.assertEqual(env.read_version(), '1.0.0')
            self.assertFalse(any(row.get('status') == 'success' for row in messages))
        else:
            self.assertEqual(code, 0)
            self.assertEqual(env.read_version(), '1.0.1')
            self.assertIn(env.install, calls[1])
            self.assertNotIn('--check', calls[1])
            self.assertTrue(any(row.get('status') == 'success' for row in messages))

    def test_network_service_installed_after_preflight_and_application_swap(self):
        self.scenario()

    def test_failed_prerequisite_check_leaves_existing_application_untouched(self):
        self.scenario(fail_check=True)

    def test_failed_service_install_restores_previous_application(self):
        self.scenario(fail_install=True)


if __name__ == '__main__':
    unittest.main()
