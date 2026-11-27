"""First-start repair for native bundles installed by an older OTA updater."""
import os
from pathlib import Path
import subprocess
import sys


def ensure_network_service():
    if os.environ.get('RELAY_MIRROR_NETWORK') == '0':
        return False
    # Explicitly enrolled source-test services have their own installer and socket.
    if os.environ.get('RELAY_TEST_DEVICE_CONFIG'):
        return os.environ.get('RELAY_MIRROR_NETWORK') == '1'
    if os.name != 'posix' or os.geteuid() != 0:
        return False
    root = Path(os.environ.get('RELAY_INSTALL_DIR') or Path(sys.argv[0]).resolve().parent)
    installer = root / 'services/mobile_network/install_service.py'
    if not installer.is_file():
        raise RuntimeError('Backend bundle is missing its mobile network installer')
    result = subprocess.run(['/usr/bin/python3', str(installer), '--install-dir', str(root), '--ensure'],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Cannot enable backend network service: ' + result.stderr.strip())
    # A drop-in affects the next process. Enable this first start in the current
    # process as well, so upgrading with an old Updater does not need a second OTA.
    os.environ.setdefault('RELAY_MIRROR_NETWORK', '1')
    os.environ.setdefault('RELAY_NETWORK_SOCKET', '/run/relay-network/control.sock')
    return True
