"""Test-only eth0 profiles. Preparing never activates or edits an existing DHCP profile."""
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import uuid

NM = '/usr/bin/nmcli'
IP = '/usr/sbin/ip'
IFACE = 'eth0'
DIRECT_ADDRESS = '192.168.60.1/24'
DIRECT_NETWORK = ipaddress.ip_network(DIRECT_ADDRESS, strict=False)
CONFIG = Path('/var/lib/relay-network/wired-config.json')
SELECTION = Path('/var/lib/relay-network/wired-selection.json')
MARKER = 'relay-wired-direct-test-v1'
if os.environ.get('RELAY_TEST_DEVICE_CONFIG'):
    from test_device_config import load
    network = load()['network']
    IFACE = network['wired_interface']
    DIRECT_ADDRESS = network['wired_address']
    DIRECT_NETWORK = ipaddress.ip_network(DIRECT_ADDRESS, strict=False)
    CONFIG = Path('/var/lib/relay-mobile-test/wired-config.json')
    SELECTION = Path('/var/lib/relay-mobile-test/wired-selection.json')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            temporary.chmod(0o600)
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class WiredNetwork:
    def __init__(self, run):
        self.run = run

    async def prop(self, ident, key):
        return await self.run(NM, '-g', key, 'connection', 'show', 'uuid', ident)

    def config(self):
        result = json.loads(CONFIG.read_text())
        return {mode: str(uuid.UUID(result[mode])) for mode in ('direct', 'auto')}

    async def current(self):
        value = await self.run(NM, '-g', 'GENERAL.CON-UUID', 'device', 'show', IFACE)
        return '' if value in ('', '--') else str(uuid.UUID(value))

    async def device(self):
        rows = json.loads(await self.run(IP, '-j', 'address', 'show', 'dev', IFACE))
        if not rows:
            raise RuntimeError('wired_unavailable')
        return rows[0]

    async def status(self):
        device = await self.device()
        current = await self.current()
        config = self.config() if CONFIG.exists() else {}
        mode = next((mode for mode, ident in config.items() if ident == current), None)
        if mode is None:
            mode = 'auto' if current and await self.prop(current, 'ipv4.method') == 'auto' else 'other' if current else 'disconnected'
        return {'available': True, 'configured': bool(config), 'interface': IFACE,
                'mode': mode, 'linkUp': 'LOWER_UP' in device.get('flags', []),
                'addresses': [f"{a['local']}/{a['prefixlen']}" for a in device.get('addr_info', []) if a.get('family') == 'inet'],
                'directAddress': DIRECT_ADDRESS}

    async def check_conflicts(self, approved_direct=False):
        rows = json.loads(await self.run(IP, '-j', '-4', 'address', 'show'))
        for row in rows:
            for address in row.get('addr_info', []):
                network = ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}", strict=False)
                if network.overlaps(DIRECT_NETWORK):
                    if approved_direct and row.get('ifname') == IFACE and f"{address['local']}/{address['prefixlen']}" == DIRECT_ADDRESS:
                        continue
                    raise RuntimeError('wired_subnet_conflict')
        routes = json.loads(await self.run(IP, '-j', '-4', 'route', 'show', 'table', 'all'))
        for route in routes:
            destination = route.get('dst', 'default')
            if destination == 'default':
                continue
            network = ipaddress.ip_network(destination, strict=False)
            if network.overlaps(DIRECT_NETWORK):
                if approved_direct and route.get('dev') == IFACE and network.subnet_of(DIRECT_NETWORK):
                    continue
                raise RuntimeError('wired_subnet_conflict')

    async def verify(self, config):
        ident = config['direct']
        expected = {'connection.interface-name': IFACE, 'connection.stable-id': MARKER,
                    'ipv4.method': 'manual', 'ipv4.addresses': DIRECT_ADDRESS,
                    'ipv4.gateway': '', 'ipv4.dns': '', 'ipv4.routes': '',
                    'ipv4.never-default': 'yes', 'ipv6.method': 'disabled'}
        for key, value in expected.items():
            if await self.prop(ident, key) != value:
                raise RuntimeError('wired_profile_conflict')
        if await self.prop(config['auto'], 'ipv4.method') != 'auto':
            raise RuntimeError('wired_profile_conflict')
        for ident in config.values():
            if await self.prop(ident, 'connection.type') != '802-3-ethernet' or await self.prop(ident, 'connection.interface-name') not in ('', IFACE):
                raise RuntimeError('wired_profile_conflict')

    async def prepare(self):
        """Called by the privileged test installer, never by a browser request."""
        if CONFIG.exists():
            config = self.config()
            await self.verify(config)
            await self.check_conflicts(await self.current() == config['direct'])
            return await self.status()
        await self.check_conflicts()
        current = await self.current()
        auto = current if current and await self.prop(current, 'ipv4.method') == 'auto' else None
        rows = await self.run(NM, '-t', '-f', 'NAME', 'connection', 'show')
        if any(name in rows.splitlines() for name in ('relay-wired-direct', 'relay-wired-auto')):
            raise RuntimeError('wired_profile_conflict')
        if not auto:
            await self.run(NM, 'connection', 'add', 'type', 'ethernet', 'ifname', IFACE,
                           'con-name', 'relay-wired-auto', 'connection.autoconnect', 'no',
                           'connection.stable-id', MARKER + '-auto', 'ipv4.method', 'auto')
            auto = await self.run(NM, '-g', 'connection.uuid', 'connection', 'show', 'relay-wired-auto')
        await self.run(NM, 'connection', 'add', 'type', 'ethernet', 'ifname', IFACE,
                       'con-name', 'relay-wired-direct', 'connection.autoconnect', 'no',
                       'connection.autoconnect-priority', '999', 'connection.stable-id', MARKER,
                       'ipv4.method', 'manual', 'ipv4.addresses', DIRECT_ADDRESS,
                       'ipv4.never-default', 'yes', 'ipv4.ignore-auto-dns', 'yes', 'ipv6.method', 'disabled')
        direct = await self.run(NM, '-g', 'connection.uuid', 'connection', 'show', 'relay-wired-direct')
        config = {'direct': str(uuid.UUID(direct)), 'auto': str(uuid.UUID(auto))}
        await self.verify(config)
        save(CONFIG, config)
        return await self.status()

    async def switch(self, mode, persist=True):
        if mode not in ('direct', 'auto'):
            raise ValueError('invalid_network_input')
        config = self.config()
        await self.verify(config)
        previous = await self.current()
        if mode == 'direct':
            await self.check_conflicts(previous == config['direct'])
        status = await self.status()
        if not status['linkUp']:
            raise RuntimeError('wired_no_link')
        old_autoconnect = await self.prop(config['direct'], 'connection.autoconnect')
        previous_selection = json.loads(SELECTION.read_text()) if SELECTION.exists() else None
        managed_auto = await self.prop(config['auto'], 'connection.stable-id') == MARKER + '-auto'
        old_auto = await self.prop(config['auto'], 'connection.autoconnect') if managed_auto else None
        try:
            wanted = 'yes' if mode == 'direct' else 'no'
            if await self.prop(config['direct'], 'connection.autoconnect') != wanted:
                await self.run(NM, 'connection', 'modify', 'uuid', config['direct'], 'connection.autoconnect', wanted)
            if managed_auto:
                wanted_auto = 'yes' if mode == 'auto' else 'no'
                if await self.prop(config['auto'], 'connection.autoconnect') != wanted_auto:
                    await self.run(NM, 'connection', 'modify', 'uuid', config['auto'], 'connection.autoconnect', wanted_auto)
            await self.run(NM, '--wait', '25', 'connection', 'up', 'uuid', config[mode], 'ifname', IFACE)
            observed = await self.status()
            if observed['mode'] != mode or not observed['addresses'] or mode == 'direct' and observed['addresses'] != [DIRECT_ADDRESS]:
                raise RuntimeError('network_failed')
            if persist:
                save(SELECTION, {'mode': mode})
        except (RuntimeError, OSError):
            try:
                await self.run(NM, 'connection', 'modify', 'uuid', config['direct'], 'connection.autoconnect', old_autoconnect)
                if managed_auto:
                    await self.run(NM, 'connection', 'modify', 'uuid', config['auto'], 'connection.autoconnect', old_auto)
                if previous:
                    await self.run(NM, '--wait', '25', 'connection', 'up', 'uuid', previous, 'ifname', IFACE)
                else:
                    await self.run(NM, 'connection', 'down', 'uuid', config[mode])
                if persist:
                    if previous_selection is None:
                        SELECTION.unlink(missing_ok=True)
                    else:
                        save(SELECTION, previous_selection)
            except (RuntimeError, OSError):
                raise RuntimeError('wired_restore_failed')
            raise RuntimeError('wired_failed_restored')
        return observed

    async def restore(self):
        if not SELECTION.exists():
            return
        try:
            mode = json.loads(SELECTION.read_text())['mode']
            if mode not in ('direct', 'auto'):
                return
            config = self.config()
            await self.verify(config)
            # Direct wins on a later cable insertion even if booted without a cable.
            await self.run(NM, 'connection', 'modify', 'uuid', config['direct'], 'connection.autoconnect', 'yes' if mode == 'direct' else 'no')
            if await self.prop(config['auto'], 'connection.stable-id') == MARKER + '-auto':
                await self.run(NM, 'connection', 'modify', 'uuid', config['auto'], 'connection.autoconnect', 'yes' if mode == 'auto' else 'no')
            if (await self.status())['linkUp'] and await self.current() != config[mode]:
                await self.switch(mode, persist=False)
        except (RuntimeError, OSError, ValueError, KeyError):
            # Keep serving the local panel; a wired fault must not switch Wi-Fi.
            return
