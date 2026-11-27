#!/bin/bash
# Run on the Raspberry Pi with sudo; password is read interactively, never logged.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
iface=${1:-wlan0}
ssid=${2:-RelayTest}
prepare=${3:-}
address=${RELAY_HOTSPOT_ADDRESS:-192.168.50.1/24}
dhcp_start=${RELAY_HOTSPOT_DHCP_START:-192.168.50.20}
dhcp_end=${RELAY_HOTSPOT_DHCP_END:-192.168.50.100}
config_dir=${RELAY_HOTSPOT_DIR:-/etc/relay-hotspot}
dhcp_unit=${RELAY_HOTSPOT_DHCP_UNIT:-relay-hotspot-dhcp.service}
isolation_unit=${RELAY_HOTSPOT_ISOLATION_UNIT:-relay-hotspot-isolation.service}
netmask=$(python3 -c 'import ipaddress,sys;print(ipaddress.IPv4Interface(sys.argv[1]).netmask)' "$address")
[[ "$iface" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 1
command -v nmcli >/dev/null
test -x /usr/sbin/nft
test -x /usr/sbin/dnsmasq || { echo 'Install dnsmasq-base before running (sudo apt install dnsmasq-base).' >&2; exit 1; }
[[ $(nmcli -g WIFI-PROPERTIES.AP device show "$iface") == yes ]] || { echo 'AP mode unsupported.' >&2; exit 1; }
if [[ -t 0 ]]; then
 read -r -s -p 'Hotspot password (8-63 ASCII characters): ' password
else
 IFS= read -r password
fi
echo
[[ ${#password} -ge 8 && ${#password} -le 63 && "$password" != *[^\ -~]* ]] || exit 1
# Fail rather than overwrite an existing profile or configuration.
if nmcli -g NAME connection show | grep -Fxq relay-hotspot; then
 echo 'relay-hotspot already exists; inspect it before reconfiguration.' >&2; exit 1
fi
nmcli connection add type wifi ifname "$iface" con-name relay-hotspot ssid "$ssid" \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.powersave 2 \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$password" \
  ipv4.method manual ipv4.addresses "$address" ipv4.never-default yes \
  ipv6.method disabled connection.autoconnect no connection.autoconnect-priority 0
unset password
install -d -m 755 "$config_dir"
cat > "$config_dir/dnsmasq.conf" <<EOF
# Local DHCP only: no DNS service, gateway or Internet sharing.
port=0
interface=$iface
bind-dynamic
dhcp-range=$dhcp_start,$dhcp_end,$netmask,12h
dhcp-option=3
dhcp-option=6
dhcp-authoritative
dhcp-leasefile=/var/lib/misc/relay-hotspot.leases
EOF
cat > "/etc/systemd/system/$dhcp_unit" <<EOF
[Unit]
Description=Relay tester isolated hotspot DHCP
After=NetworkManager.service
Requires=NetworkManager.service
[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=$config_dir/dnsmasq.conf
Restart=on-failure
RestartSec=2
[Install]
WantedBy=multi-user.target
EOF
/usr/sbin/dnsmasq --test --conf-file="$config_dir/dnsmasq.conf"
cat > "$config_dir/isolation.nft" <<EOF
table inet relay_hotspot_isolation {
 chain forward {
  type filter hook forward priority -10; policy accept;
  iifname "$iface" drop
  oifname "$iface" drop
 }
}
EOF
cat > "/etc/systemd/system/$isolation_unit" <<EOF
[Unit]
Description=Block relay hotspot forwarding to other networks
After=NetworkManager.service
Before=$dhcp_unit
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/nft delete table inet relay_hotspot_isolation
ExecStart=/usr/sbin/nft -f $config_dir/isolation.nft
ExecStop=/usr/sbin/nft delete table inet relay_hotspot_isolation
[Install]
WantedBy=multi-user.target
EOF
/usr/sbin/nft -c -f "$config_dir/isolation.nft"
systemctl daemon-reload
if [[ "$prepare" == --prepare ]]; then
 echo 'Hotspot prepared without activation.'
 exit 0
fi
systemctl start "$isolation_unit" "$dhcp_unit"
nmcli connection up relay-hotspot
install -d -m 755 /var/lib/relay-network
profile=$(nmcli -g connection.uuid connection show relay-hotspot)
printf '{"profile":"%s"}\n' "$profile" > /var/lib/relay-network/selection.json
chmod 600 /var/lib/relay-network/selection.json
printf 'Hotspot ready. Connect to %s; address %s\n' "$ssid" "${address%/*}"
