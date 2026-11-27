"""Root-owned, narrow NetworkManager API over a permission-restricted Unix socket."""
import asyncio
import json
import os
from pathlib import Path
import uuid

SOCKET = '/run/relay-network/control.sock'
STATE = Path('/var/lib/relay-network/selection.json')
IFACE = 'wlan0'
TEST_CONFIG = None
if os.environ.get('RELAY_TEST_DEVICE_CONFIG'):
    from test_device_config import load, device_serial
    TEST_CONFIG = load()
    if TEST_CONFIG['device_serial'].lower() != device_serial():
        raise RuntimeError('Network test service is not enrolled for this device')
    IFACE = TEST_CONFIG['network']['wireless_interface']
    SOCKET = '/run/relay-mobile-network-test/control.sock'
    STATE = Path('/var/lib/relay-mobile-test/selection.json')

SOCKET = os.environ.get('RELAY_NETWORK_SOCKET', SOCKET)
PRESERVE_ONCE = Path(os.environ.get('RELAY_NETWORK_PRESERVE_FILE', '/run/relay-network-preserve-once'))
SETUP_SCRIPT = Path(__file__).resolve().with_name('setup-relay-hotspot.sh')

class NetworkManager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.wired = None
        if TEST_CONFIG or os.environ.get('RELAY_WIRED_DIRECT_TEST') == '1':
            from wired_network import WiredNetwork
            self.wired = WiredNetwork(self.run)

    async def run(self, *args, timeout=40, input_data=None):
        env = os.environ.copy()
        if TEST_CONFIG:
            network = TEST_CONFIG['network']
            env.update(RELAY_HOTSPOT_ADDRESS=network['hotspot_address'],
                       RELAY_HOTSPOT_DHCP_START=network['hotspot_dhcp_start'],
                       RELAY_HOTSPOT_DHCP_END=network['hotspot_dhcp_end'],
                       RELAY_HOTSPOT_DHCP_UNIT=network['hotspot_dhcp_unit'],
                       RELAY_HOTSPOT_ISOLATION_UNIT=network['hotspot_isolation_unit'],
                       RELAY_HOTSPOT_DIR='/etc/relay-mobile-test/hotspot')
        proc = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.PIPE if input_data else asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(input_data), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError('network_timeout')
        if proc.returncode:
            raise RuntimeError('network_failed')
        return output.decode().strip()

    async def profiles(self):
        rows = await self.run('/usr/bin/nmcli', '-t', '-f', 'UUID,TYPE', 'connection', 'show')
        result = []
        for row in rows.splitlines():
            ident, kind = row.split(':', 1)
            if kind != '802-11-wireless':
                continue
            # Only profiles compatible with this interface, including unbound ones.
            iface = await self.run('/usr/bin/nmcli', '-g', 'connection.interface-name', 'connection', 'show', 'uuid', ident)
            if iface and iface != IFACE:
                continue
            name = await self.run('/usr/bin/nmcli', '-g', 'connection.id', 'connection', 'show', 'uuid', ident)
            mode = await self.run('/usr/bin/nmcli', '-g', '802-11-wireless.mode', 'connection', 'show', 'uuid', ident)
            ssid = await self.run('/usr/bin/nmcli', '-g', '802-11-wireless.ssid', 'connection', 'show', 'uuid', ident)
            if mode == 'ap' and name != 'relay-hotspot':
                continue
            result.append({'id': ident, 'name': name, 'ssid': ssid, 'mode': 'hotspot' if name == 'relay-hotspot' else 'wifi'})
        return result

    async def status(self):
        profiles = await self.profiles()
        current = await self.run('/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', IFACE)
        active = next((p for p in profiles if p['id'] == current), None)
        addresses = await self.run('/usr/bin/nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', IFACE)
        state = {'available': True, 'mode': active['mode'] if active else 'disconnected',
                'ssid': active['ssid'] if active else '', 'addresses': addresses.splitlines(),
                'profiles': profiles, 'hotspotConfigured': any(p['mode'] == 'hotspot' for p in profiles)}
        if self.wired:
            try:
                state['wired'] = await self.wired.status()
            except (RuntimeError, OSError, ValueError, KeyError):
                state['wired'] = {'available': False, 'configured': False}
        return state

    async def remember(self, ident):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'profile': ident}))
        os.chmod(tmp, 0o600)
        tmp.replace(STATE)

    async def activate(self, profile):
        network = TEST_CONFIG['network'] if TEST_CONFIG else {}
        dhcp = network.get('hotspot_dhcp_unit', 'relay-hotspot-dhcp')
        isolation = network.get('hotspot_isolation_unit', 'relay-hotspot-isolation')
        if profile['mode'] == 'hotspot':
            await self.run('/usr/bin/systemctl', 'start', isolation, dhcp)
        await self.run('/usr/bin/nmcli', '--wait', '25', 'connection', 'up', 'uuid', profile['id'], 'ifname', IFACE)
        if profile['mode'] != 'hotspot':
            await self.run('/usr/bin/systemctl', 'stop', dhcp, isolation)
        await self.remember(profile['id'])

    async def dispatch(self, data):
        if not isinstance(data, dict):
            raise ValueError('invalid_network_input')
        async with self.lock:
            if data.get('action') == 'status':
                return await self.status()
            if data.get('action') == 'switch_wired':
                if not self.wired:
                    raise ValueError('invalid_network_action')
                await self.wired.switch(data.get('mode'))
                return await self.status()
            if data.get('action') == 'configure_hotspot':
                ssid, password = data.get('ssid', ''), data.get('password', '')
                if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
                    raise ValueError('invalid_network_input')
                if not isinstance(password, str) or not 8 <= len(password) <= 63 or any(ord(c) < 32 or ord(c) > 126 for c in password):
                    raise ValueError('invalid_network_input')
                existing = next((p for p in await self.profiles() if p['mode'] == 'hotspot'), None)
                if existing:
                    # Update only the saved profile; activation remains an explicit operation.
                    await self.run('/usr/bin/nmcli', 'connection', 'modify', 'uuid', existing['id'],
                        '802-11-wireless.ssid', ssid, '802-11-wireless-security.psk', password)
                else:
                    setup = str(SETUP_SCRIPT)
                    await self.run('/bin/bash', setup, IFACE, ssid, '--prepare',
                    input_data=(password + '\n').encode())
                return await self.status()
            if data.get('action') != 'switch':
                raise ValueError('invalid_network_action')
            ident = str(uuid.UUID(data.get('profile', '')))
            profiles = await self.profiles()
            target = next((p for p in profiles if p['id'] == ident), None)
            if target is None:
                raise ValueError('invalid_network_input')
            previous = await self.run('/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', IFACE)
            try:
                await self.activate(target)
            except RuntimeError:
                # Restore the previous working connection, otherwise fall back to the hotspot.
                fallback = next((p for p in profiles if p['id'] == previous), None)
                fallback = fallback or next((p for p in profiles if p['mode'] == 'hotspot'), None)
                if fallback:
                    await self.activate(fallback)
                raise RuntimeError('network_failed_restored')
            return await self.status()

    async def restore(self):
        if TEST_CONFIG:
            await self.restore_wifi_first()
            return
        if not STATE.exists():
            return
        try:
            ident = json.loads(STATE.read_text())['profile']
            current = await self.run('/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', IFACE)
            if ident == current:
                return
            await self.dispatch({'action': 'switch', 'profile': ident})
        except (RuntimeError, ValueError, KeyError, OSError):
            profiles = await self.profiles()
            fallback = next((p for p in profiles if p['mode'] == 'hotspot'), None)
            if fallback:
                await self.activate(fallback)

    async def restore_wifi_first(self):
        """Test-device startup prefers saved Wi-Fi over a remembered hotspot."""
        async with self.lock:
            profiles = await self.profiles()
            current = await self.run('/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', IFACE)
            wifi = [p for p in profiles if p['mode'] == 'wifi']
            if any(p['id'] == current for p in wifi):
                return
            preferred = None
            try:
                preferred = json.loads(STATE.read_text()).get('profile')
            except (ValueError, OSError):
                pass
            wifi.sort(key=lambda p: p['id'] != preferred)
            if wifi:
                for profile in wifi:
                    try:
                        await self.activate(profile)
                        return
                    except RuntimeError:
                        continue
                # Saved networks may be out of range; retain the current connection
                # and leave hotspot selection to the operator.
                previous = next((p for p in profiles if p['id'] == current), None)
                if previous:
                    await self.activate(previous)
                return
            hotspot = next((p for p in profiles if p['mode'] == 'hotspot'), None)
            if hotspot and hotspot['id'] != current:
                await self.activate(hotspot)

async def restore_startup(manager):
    # Installation/restart handover must retain the live Wi-Fi and wired connection.
    # /run is cleared at boot, so normal saved-selection restoration still runs then.
    preserve = PRESERVE_ONCE.exists()
    PRESERVE_ONCE.unlink(missing_ok=True)
    if not preserve:
        await manager.restore()
        if manager.wired:
            await manager.wired.restore()

async def main():
    import grp
    manager = NetworkManager()
    await restore_startup(manager)
    async def handle(reader, writer):
        try:
            if TEST_CONFIG:
                import pwd, socket, struct
                peer = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
                _, uid, _ = struct.unpack('3i', peer)
                if uid not in (0, pwd.getpwnam(os.environ['RELAY_NETWORK_TEST_USER']).pw_uid):
                    raise RuntimeError('network_access_denied')
            data = json.loads(await asyncio.wait_for(reader.readline(), 5))
            result = {'ok': True, 'state': await manager.dispatch(data)}
        except (ValueError, TypeError, RuntimeError, OSError, asyncio.TimeoutError) as exc:
            code = str(exc) if isinstance(exc, RuntimeError) else 'invalid_network_input'
            result = {'ok': False, 'code': code}
        writer.write((json.dumps(result) + '\n').encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    Path(SOCKET).unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, SOCKET, limit=4096)
    if TEST_CONFIG:
        import pwd
        os.chown(SOCKET, 0, pwd.getpwnam(os.environ['RELAY_NETWORK_TEST_USER']).pw_gid)
    else:
        os.chown(SOCKET, 0, grp.getgrnam('relay-control').gr_gid)
    os.chmod(SOCKET, 0o660)
    test_user = os.environ.get('RELAY_NETWORK_TEST_USER')
    if test_user and not TEST_CONFIG:
        # Root-owned service configuration can grant the test UI user access.
        await manager.run('/usr/bin/setfacl', '-m', f'u:{test_user}:rw', SOCKET)
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
