"""Device-specific settings for explicitly enabled test instances only."""
import ipaddress
import json
import os
from pathlib import Path
import re

CONFIG_ENV = 'RELAY_TEST_DEVICE_CONFIG'
DEFAULT_NETWORK = {
    'wireless_interface': 'wlan0', 'wired_interface': 'eth0',
    'hotspot_address': '192.168.50.1/24',
    'hotspot_dhcp_start': '192.168.50.20', 'hotspot_dhcp_end': '192.168.50.100',
    'hotspot_profile': 'relay-hotspot', 'hotspot_ssid': 'RelayTest',
    'wired_address': '192.168.60.1/24', 'wired_computer_address': '192.168.60.2',
    'hotspot_dhcp_unit': 'relay-mobile-hotspot-dhcp.service',
    'hotspot_isolation_unit': 'relay-mobile-hotspot-isolation.service',
}

def device_serial():
    for path in ['/sys/firmware/devicetree/base/serial-number', '/proc/device-tree/serial-number']:
        try:
            value = Path(path).read_text().strip('\0\n ')
            if value: return value.lower()
        except OSError:
            pass
    for line in Path('/proc/cpuinfo').read_text().splitlines():
        if line.startswith('Serial'):
            return line.split(':', 1)[1].strip().lower()
    raise RuntimeError('Device serial unavailable; explicit enrollment required')

def validate(config):
    if config.get('version') != 1 or config.get('test_enabled') is not True:
        raise ValueError('Not an explicitly enabled test device configuration')
    if not re.fullmatch(r'[0-9a-fA-F]{8,32}', config.get('device_serial', '')):
        raise ValueError('Invalid registered device serial')
    network = {**DEFAULT_NETWORK, **config.get('network', {})}
    for key in ['wireless_interface', 'wired_interface']:
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', network[key]):
            raise ValueError('Invalid network interface')
    if network['wireless_interface'] == network['wired_interface']:
        raise ValueError('Wireless and wired interfaces must differ')
    hotspot = ipaddress.IPv4Interface(network['hotspot_address'])
    direct = ipaddress.IPv4Interface(network['wired_address'])
    for address in [hotspot, direct]:
        if (not any(address.network.subnet_of(ipaddress.IPv4Network(block)) for block in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']) or address.network.prefixlen > 29
                or address.ip in (address.network.network_address, address.network.broadcast_address)):
            raise ValueError('Use a private usable IPv4 subnet')
    if hotspot.network.overlaps(direct.network):
        raise ValueError('Hotspot and wired subnets overlap')
    start, end = (ipaddress.IPv4Address(network[k]) for k in ['hotspot_dhcp_start', 'hotspot_dhcp_end'])
    pc = ipaddress.IPv4Address(network['wired_computer_address'])
    if not hotspot.network.network_address < start <= end < hotspot.network.broadcast_address or start <= hotspot.ip <= end:
        raise ValueError('Invalid hotspot DHCP range')
    if not direct.network.network_address < pc < direct.network.broadcast_address or pc == direct.ip:
        raise ValueError('Invalid wired computer address')
    for key in ['hotspot_dhcp_unit', 'hotspot_isolation_unit']:
        if not re.fullmatch(r'relay-[a-z0-9-]+\.service', network[key]):
            raise ValueError('Invalid test network unit')
    if network['hotspot_profile'] != 'relay-hotspot':
        raise ValueError('Unsupported managed hotspot profile')
    if not isinstance(network['hotspot_ssid'], str) or not 1 <= len(network['hotspot_ssid'].encode()) <= 32 or any(ord(c)<32 for c in network['hotspot_ssid']):
        raise ValueError('Invalid hotspot SSID')
    return {**config, 'network': network}

def load(path=None):
    path = path or os.environ.get(CONFIG_ENV)
    if not path:
        return None
    source = Path(path)
    if os.name != 'nt':
        stat = source.stat()
        if source.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
            raise RuntimeError('Test device config must be root-owned and not writable by other users')
    return validate(json.loads(source.read_text()))

def public_config(config):
    network = config['network']
    return {'version': 1, 'wirelessInterface': network['wireless_interface'],
            'wiredInterface': network['wired_interface'], 'hotspotAddress': network['hotspot_address'],
            'wiredAddress': network['wired_address'], 'wiredComputerAddress': network['wired_computer_address'],
            'wiredNetmask': str(ipaddress.IPv4Interface(network['wired_address']).netmask)}
