"""Install the backend-owned NetworkManager helper without selecting a network."""
import argparse
import grp
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

MARKER = '# Managed backend mobile network service'
SYSTEMD = Path('/etc/systemd/system')
RUN_DIR = Path('/run')
FILES = ('network_manager.py', 'wired_network.py', 'test_device_config.py',
         'setup-relay-hotspot.sh', 'install_service.py')


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True).stdout.strip()


def service_name(value):
    if not re.fullmatch(r'relay-[a-z0-9-]+\.service', value):
        raise argparse.ArgumentTypeError('Invalid relay service name')
    return value


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.mobile-next')
    temporary.write_text(text, encoding='utf-8')
    temporary.chmod(0o644)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', default='/opt/RelayProtection')
    parser.add_argument('--unit', type=service_name, default='relay-network-manager.service')
    parser.add_argument('--backend-unit', type=service_name, default='relay-protection.service')
    parser.add_argument('--test-config')
    parser.add_argument('--test-user')
    parser.add_argument('--enable-only', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--ensure', action='store_true', help='Keep an already-correct active instance unchanged')
    args = parser.parse_args()
    root = Path(args.install_dir).resolve()
    # Paths are written into systemd directives, not interpreted as shell commands.
    if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(root)):
        parser.error('Installation directory must be an absolute service-safe path')
    helper = root / 'services/mobile_network'
    for name in FILES:
        if not (helper / name).is_file():
            raise RuntimeError('Missing backend network file: ' + name)
    missing = [name for name in ('python3', 'nmcli', 'nft', 'dnsmasq', 'ip', 'systemctl')
               if not shutil.which(name)]
    if missing:
        raise RuntimeError('Install network prerequisites before updating: ' + ', '.join(missing))
    config = None
    if args.test_config:
        config = Path(args.test_config).resolve()
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(config)):
            parser.error('Invalid test configuration path')
        from test_device_config import load, device_serial
        enrolled = load(config)
        if enrolled['device_serial'].lower() != device_serial():
            raise RuntimeError('Network service is not enrolled for this device')
        if not args.test_user:
            parser.error('--test-user is required for an enrolled test instance')
        pwd.getpwnam(args.test_user)
    elif args.test_user:
        parser.error('--test-user requires --test-config')
    if args.check:
        print('Backend network installation prerequisites OK')
        return
    if os.geteuid() != 0:
        raise RuntimeError('Run the network service installer as root')
    if not config:
        try:
            grp.getgrnam('relay-control')
        except KeyError:
            run('groupadd', '--system', 'relay-control')
    runtime = args.unit.removesuffix('.service') if config else 'relay-network'
    socket = '/run/' + runtime + '/control.sock'
    preserve = RUN_DIR / (runtime + '-preserve-once')
    environment = f'Environment=RELAY_NETWORK_SOCKET={socket}\nEnvironment=RELAY_NETWORK_PRESERVE_FILE={preserve}\n'
    if config:
        environment += f'Environment=RELAY_TEST_DEVICE_CONFIG={config}\nEnvironment=RELAY_NETWORK_TEST_USER={args.test_user}\n'
    command = '/usr/bin/python3 ' + str(helper / 'network_manager.py')
    unit = f'''{MARKER}
[Unit]
Description=Backend-owned relay mobile network manager
After=NetworkManager.service
Requires=NetworkManager.service
Before={args.backend_unit}

[Service]
Type=simple
User=root
RuntimeDirectory={runtime}
RuntimeDirectoryMode=0755
StateDirectory={'relay-mobile-test' if config else 'relay-network'}
{environment}ExecStart={command}
Restart=on-failure
RestartSec=3
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
'''
    # Override prior frontend deployment drop-ins as well as the base unit.
    override = f'{MARKER}\n[Service]\nExecStart=\nExecStart={command}\n{environment}'
    backend = f'''{MARKER}
[Unit]
Wants={args.unit}
After={args.unit}
[Service]
Environment=RELAY_MIRROR_NETWORK=1
Environment=RELAY_NETWORK_SOCKET={socket}
'''
    outputs = {
        SYSTEMD / args.unit: unit,
        SYSTEMD / (args.unit + '.d') / '99-backend-mobile-network.conf': override,
        SYSTEMD / (args.backend_unit + '.d') / 'mobile-network.conf': backend,
    }
    previous = {path: path.read_bytes() if path.exists() else None for path in outputs}
    active = run('systemctl', 'is-active', args.unit, check=False) == 'active'
    enabled = run('systemctl', 'is-enabled', args.unit, check=False) == 'enabled'
    if args.ensure and active and enabled and all(previous[path] == contents.encode('utf-8') for path, contents in outputs.items()) and Path(socket).exists():
        print(f'{args.unit} already configured and active')
        return
    try:
        for path, contents in outputs.items():
            atomic_write(path, contents)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', args.unit)
        if not args.enable_only:
            # Stop first: RuntimeDirectory is removed by systemd on stop.
            run('systemctl', 'stop', args.unit)
            preserve.write_text('preserve live connections during installation\n')
            preserve.chmod(0o600)
            run('systemctl', 'start', args.unit)
    except BaseException:
        run('systemctl', 'stop', args.unit, check=False)
        for path, contents in previous.items():
            if contents is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(contents)
        run('systemctl', 'daemon-reload', check=False)
        if not enabled:
            run('systemctl', 'disable', args.unit, check=False)
        if active:
            preserve.write_text('preserve live connections during rollback\n')
            run('systemctl', 'start', args.unit, check=False)
        raise
    print(f'Installed {args.unit} from {helper}; live network selection retained')


if __name__ == '__main__':
    main()
