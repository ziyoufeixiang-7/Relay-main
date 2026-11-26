#!/usr/bin/env bash
set -euo pipefail

# Relay Protection System - Raspberry Pi Init Script
# Auto-escalate to root
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_FILE="$SCRIPT_DIR/init.conf"

if [ ! -f "$CONF_FILE" ]; then
    echo "[FAIL] Config file not found: $CONF_FILE"
    exit 1
fi
# shellcheck source=deploy/init.conf
source "$CONF_FILE"

SERVER_BASE="http://47.98.48.17/update"
PRODUCT_URL="${SERVER_BASE}/${PRODUCT}"
FPGA_URL="${SERVER_BASE}/fpga"

# --- Helper functions ---

log_ok()   { echo "[OK]   $1"; }
log_fail() { echo "[FAIL] $1"; }
log_info() { echo "[INFO] $1"; }
log_warn() { echo "[WARN] $1"; }

unit_exists() {
    systemctl list-unit-files --no-legend "$1" 2>/dev/null | grep -q . \
        || systemctl cat "$1" &>/dev/null
}

try_disable() {
    for svc in "$@"; do
        if unit_exists "$svc"; then
            systemctl disable --now "$svc" 2>/dev/null && log_ok "Disabled $svc" || log_fail "Disable $svc"
        fi
    done
}

try_mask() {
    for svc in "$@"; do
        if unit_exists "$svc"; then
            systemctl mask "$svc" 2>/dev/null && log_ok "Masked $svc" || log_fail "Mask $svc"
        fi
    done
}

# --- Module functions ---

module_1_hardware() {
    echo "========================================"
    echo "  Module 1: Hardware & System Config"
    echo "========================================"

    # Resolve SCREEN to SCREEN_OVERLAY
    if [ "$SCREEN" = "10_1" ]; then
        SCREEN_OVERLAY="vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a,dsi0"
    elif [ "$SCREEN" = "7_0" ]; then
        SCREEN_OVERLAY="vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0"
    else
        log_warn "Unknown SCREEN value '$SCREEN', defaulting to 7_0"
        SCREEN_OVERLAY="vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0"
    fi

    # Screen config (idempotent)
    if ! grep -q "RELAY-INIT" /boot/firmware/config.txt; then
        cat >> /boot/firmware/config.txt << EOF

# RELAY-INIT
[all]
dtoverlay=${SCREEN_OVERLAY}
dtparam=uart0=on
disable_splash=1
boot_delay=0
hdmi_ignore_hotplug=1
dtparam=watchdog=on
EOF
        log_ok "config.txt updated (overlay: ${SCREEN_OVERLAY})"
    else
        log_info "config.txt already configured, skipped"
    fi

    # Free UART0 for FPGA
    sed -i -E 's/console=(serial0|ttyAMA0),[0-9]+ *//g' /boot/firmware/cmdline.txt
    log_ok "Serial console removed from cmdline.txt"

    # Silent boot
    for opt in quiet loglevel=0 vt.global_cursor_default=0 logo.nologo; do
        grep -qF " $opt" /boot/firmware/cmdline.txt || sed -i "s/\$/ $opt/" /boot/firmware/cmdline.txt
    done
    log_ok "Silent boot parameters set"

    # APT mirror
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g;
                s|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g;
                s|http://archive.raspberrypi.com/debian|https://mirrors.tuna.tsinghua.edu.cn/raspberrypi|g;
                s|http://archive.raspberrypi.org/debian|https://mirrors.tuna.tsinghua.edu.cn/raspberrypi|g' "$f"
    done
    log_ok "APT mirror set to Tsinghua"

    # Install dependencies
    apt-get update -qq && log_ok "APT updated" || log_fail "APT update"
    apt-get install -y --no-install-recommends libftdi1-2 openocd gpiod && log_ok "Dependencies installed" || log_fail "Package install"

    # Watchdog
    mkdir -p /etc/systemd/system.conf.d
    cat > /etc/systemd/system.conf.d/watchdog.conf << 'WDT_EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
WDT_EOF
    log_ok "Watchdog configured (15s runtime, 2min reboot)"

    # Journald
    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/size.conf << 'JOURNALD_EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
JOURNALD_EOF
    log_ok "Journald configured (persistent, 200M max)"

    echo "-------- Module 1 complete --------"
}

module_2_services() {
    echo "========================================"
    echo "  Module 2: Service Optimization"
    echo "========================================"

    # cloud-init (must mask)
    mkdir -p /etc/cloud && touch /etc/cloud/cloud-init.disabled
    try_mask cloud-init.service cloud-init-main.service cloud-init-local.service cloud-init-network.service cloud-config.service cloud-final.service

    # Standard disable
    try_disable NetworkManager-wait-online.service
    try_disable rpi-eeprom-update.service
    try_disable sshswitch.service
    try_disable e2scrub_reap.service

    # D-Bus activated / static (must mask)
    try_mask rp1-test.service glamor-test.service
    try_mask accounts-daemon.service packagekit.service

    # NFS
    try_disable rpcbind.service rpc-statd-notify.service nfs-blkmap.service
    try_mask run-rpc_pipefs.mount

    # Misc
    try_disable systemd-binfmt.service
    try_mask proc-sys-fs-binfmt_misc.mount proc-sys-fs-binfmt_misc.automount
    try_mask modprobe@efi_pstore.service
    try_mask alsa-restore.service alsa-state.service
    touch /etc/udev/rules.d/90-alsa-restore.rules
    log_ok "ALSA udev rules overridden"

    # Check for failures
    local failed
    failed=$(systemctl --failed --no-legend 2>/dev/null | wc -l || true)
    if [ "$failed" -eq 0 ]; then
        log_ok "No failed units"
    else
        log_warn "$failed failed unit(s) detected"
    fi

    echo "-------- Module 2 complete --------"
}

module_3_desktop() {
    echo "========================================"
    echo "  Module 3: Desktop Optimization"
    echo "========================================"

    # Slim down labwc autostart
    [ -f /etc/xdg/labwc/autostart.bak ] || cp /etc/xdg/labwc/autostart /etc/xdg/labwc/autostart.bak
    cat > /etc/xdg/labwc/autostart << 'LABWC_EOF'
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
LABWC_EOF
    log_ok "labwc autostart trimmed (panel/desktop removed)"

    # Disable polkit GUI agents
    mv /etc/xdg/autostart/polkit-mate-authentication-agent-1.desktop /etc/xdg/autostart/polkit-mate-authentication-agent-1.desktop.disabled 2>/dev/null && log_ok "polkit-mate agent disabled" || true
    mv /etc/xdg/autostart/lxpolkit.desktop /etc/xdg/autostart/lxpolkit.desktop.disabled 2>/dev/null && log_ok "lxpolkit agent disabled" || true

    # PipeWire / portals / gvfs (--global, no user session needed)
    systemctl --global mask pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket wireplumber.service xdg-desktop-portal-wlr.service xdg-document-portal.service xdg-document-portal.socket gvfs-mtp-volume-monitor.service gvfs-gphoto2-volume-monitor.service gvfs-afc-volume-monitor.service gvfs-goa-volume-monitor.service 2>/dev/null && log_ok "PipeWire/portals/gvfs masked (--global)" || log_fail "Global mask"

    echo "-------- Module 3 complete --------"
}

module_4_download() {
    echo "========================================"
    echo "  Module 4: Downloads"
    echo "========================================"

    # Backend
    mkdir -p /opt/RelayProtection
    log_info "Downloading backend..."
    curl -f --progress-bar -o /tmp/relay-backend.tar.gz "${PRODUCT_URL}/backend.tar.gz" && log_ok "Backend downloaded" || { log_fail "Backend download"; }
    curl -fsSL -o /tmp/relay-backend.sha256 "${PRODUCT_URL}/backend.tar.gz.sha256" || true

    if [ -f /tmp/relay-backend.tar.gz ] && [ -f /tmp/relay-backend.sha256 ] && \
       [ "$(sha256sum /tmp/relay-backend.tar.gz | cut -d' ' -f1)" = "$(cut -d' ' -f1 /tmp/relay-backend.sha256)" ]; then
        tar -xzf /tmp/relay-backend.tar.gz -C /opt/RelayProtection/
        chmod +x /opt/RelayProtection/SysEngine.bin
        log_ok "Backend extracted and verified"
    else
        log_fail "Backend SHA-256 validation failed"
    fi

    # Frontend
    curl -sf -o /opt/RelayProtection/index.html "${PRODUCT_URL}/index.html" && log_ok "Frontend downloaded" || log_fail "Frontend download"
    curl -sf -o /opt/RelayProtection/version.json "${PRODUCT_URL}/version.json" && log_ok "Version metadata downloaded" || log_fail "Version download"
    [ -s /opt/RelayProtection/index.html ] || log_warn "Frontend missing, kiosk will show blank page"

    # FPGA
    mkdir -p /opt/FPGADownload
    log_info "Downloading FPGA tools..."

    curl -fsSL -o /opt/FPGADownload/xc3sprog "${FPGA_URL}/xc3sprog" && log_ok "xc3sprog downloaded" || log_fail "xc3sprog download"
    curl -fsSL -o /tmp/xc3sprog.sha256 "${FPGA_URL}/xc3sprog.sha256" || true
    if [ -f /tmp/xc3sprog.sha256 ] && [ "$(sha256sum /opt/FPGADownload/xc3sprog | cut -d' ' -f1)" = "$(cut -d' ' -f1 /tmp/xc3sprog.sha256)" ]; then
        chmod +x /opt/FPGADownload/xc3sprog
        log_ok "xc3sprog verified"
    else
        log_fail "xc3sprog validation"; rm -f /opt/FPGADownload/xc3sprog
    fi

    curl -fsSL -o /opt/FPGADownload/flash "${FPGA_URL}/flash" && log_ok "flash downloaded" || log_fail "flash download"
    curl -fsSL -o /tmp/flash.sha256 "${FPGA_URL}/flash.sha256" || true
    if [ -f /tmp/flash.sha256 ] && [ "$(sha256sum /opt/FPGADownload/flash | cut -d' ' -f1)" = "$(cut -d' ' -f1 /tmp/flash.sha256)" ]; then
        chmod +x /opt/FPGADownload/flash
        log_ok "flash verified"
    else
        log_fail "flash validation"; rm -f /opt/FPGADownload/flash
    fi

    curl -fsSL -o /opt/FPGADownload/fpga.bit "${FPGA_URL}/fpga.bit" && log_ok "fpga.bit downloaded" || log_fail "fpga.bit download"
    curl -fsSL -o /tmp/fpga.bit.sha256 "${FPGA_URL}/fpga.bit.sha256" || true
    if [ ! -f /tmp/fpga.bit.sha256 ] || \
       [ "$(sha256sum /opt/FPGADownload/fpga.bit 2>/dev/null | cut -d' ' -f1)" != "$(cut -d' ' -f1 /tmp/fpga.bit.sha256)" ]; then
        log_fail "fpga.bit validation"; rm -f /opt/FPGADownload/fpga.bit
    else
        log_ok "fpga.bit verified"
    fi

    # Splash Screen
    log_info "Downloading and configuring splash screen..."
    if [ "$SCREEN" = "10_1" ]; then
        SPLASH_URL="${SERVER_BASE}/splash_90.png"
    else
        SPLASH_URL="${SERVER_BASE}/splash.png"
    fi
    curl -sf -o /usr/share/plymouth/themes/pix/splash.png "$SPLASH_URL" && {
        log_ok "Splash image downloaded"
        if plymouth-set-default-theme -R pix; then
            log_ok "Plymouth theme updated (initramfs rebuilt)"
        else
            log_fail "Plymouth theme update"
        fi
    } || log_fail "Splash image download"

    echo "-------- Module 4 complete --------"
}

module_5_register() {
    echo "========================================"
    echo "  Module 5: Service Register & Kiosk"
    echo "========================================"

    # Backend systemd service
    cat > /etc/systemd/system/relay-protection.service << 'SERVICE_EOF'
[Unit]
Description=Relay Protection System
After=network.target
ConditionPathExists=/opt/RelayProtection/SysEngine.bin
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=/opt/RelayProtection
ExecStart=/opt/RelayProtection/SysEngine.bin
Restart=always
RestartSec=3
User=root
OOMScoreAdjust=-888

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    systemctl daemon-reload || true
    systemctl enable relay-protection && log_ok "relay-protection.service registered and enabled" || log_fail "Service enable"

    # Chromium kiosk autostart
    raspi-config nonint do_boot_behaviour B4 && log_ok "Desktop autologin enabled" || log_fail "Autologin"
    raspi-config nonint do_blanking 1 && log_ok "Screen blanking disabled" || log_fail "Blanking"

    mkdir -p /home/pi/.config/autostart
    cat > /home/pi/.config/autostart/kiosk.desktop << 'AUTOSTART_EOF'
[Desktop Entry]
Type=Application
Name=Kiosk
Comment=Kiosk
Exec=chromium --kiosk --incognito --ozone-platform=wayland --disable-pinch --noerrdialogs --disable-session-crashed-bubble --password-store=basic --overscroll-history-navigation=0 --renderer-process-limit=1 --process-per-site --disable-background-networking --disable-component-update --disable-sync --disable-extensions --disable-hang-monitor file:///opt/RelayProtection/index.html
AUTOSTART_EOF
    chown -R pi:pi /home/pi/.config
    mkdir -p /etc/chromium/policies/managed
    cat > /etc/chromium/policies/managed/kiosk.json << 'POLICY_EOF'
{
    "TranslateEnabled": false,
    "PasswordManagerEnabled": false,
    "AutofillAddressEnabled": false,
    "AutofillCreditCardEnabled": false,
    "DefaultNotificationsSetting": 2,
    "BookmarkBarEnabled": false,
    "DeveloperToolsAvailability": 2,
    "BackgroundModeEnabled": false,
    "PrintingEnabled": false,
    "MetricsReportingEnabled": false
}
POLICY_EOF
    log_ok "Chromium kiosk autostart + policy configured"

    echo "-------- Module 5 complete --------"
}

module_6_rotation() {
    echo "========================================"
    echo "  Module 6: Screen Rotation (V2 only)"
    echo "========================================"

    apt-get install -y --no-install-recommends wlr-randr && log_ok "wlr-randr installed" || log_fail "wlr-randr install"

    # Auto-detect DSI output from DRM subsystem
    DSI_OUTPUT=$(ls /sys/class/drm/ | grep -oP '(?<=card\d-)DSI-\d+' | head -1 || true)
    if [ -z "$DSI_OUTPUT" ]; then
        log_warn "No DSI output detected dynamically. Assuming DSI-1 for first install."
        DSI_OUTPUT="DSI-1"
    else
        log_ok "Detected DSI output: $DSI_OUTPUT"
    fi

    if [ "$SCREEN" = "10_1" ]; then
        TRANSFORM_VAL="90"
    else
        TRANSFORM_VAL="normal"
    fi
    mkdir -p /home/pi/.config/kanshi
    cat > /home/pi/.config/kanshi/config << KANSHI_EOF
profile {
    output $DSI_OUTPUT transform $TRANSFORM_VAL enable
}
KANSHI_EOF
    chown -R pi:pi /home/pi/.config/kanshi
    log_ok "kanshi rotation configured for $DSI_OUTPUT"
    log_warn "After reboot, verify BOTH display and touch orientation"

    echo "-------- Module 6 complete --------"
}

module_7_fpga_flash() {
    echo "========================================"
    echo "  Module 7: FPGA Flash (OpenOCD)"
    echo "========================================"

    local BIT_FILE="/opt/FPGADownload/fpga.bit"
    local FLASH_TOOL="/opt/FPGADownload/flash"

    if [ ! -f "$FLASH_TOOL" ]; then
        log_fail "Flash tool not found: $FLASH_TOOL (run module 4 first)"
        return 1
    fi
    if [ ! -f "$BIT_FILE" ]; then
        log_fail "Bitstream not found: $BIT_FILE (run module 4 first)"
        return 1
    fi

    log_info "Flashing $BIT_FILE ..."
    if "$FLASH_TOOL" "$BIT_FILE"; then
        log_ok "FPGA flash complete"
    else
        log_fail "FPGA flash failed"
        return 1
    fi

    echo -n "Delete local bit file? [y/N]: "
    read -r del_choice
    if [ "$del_choice" = "y" ] || [ "$del_choice" = "Y" ]; then
        rm -f "$BIT_FILE"
        log_ok "Deleted $BIT_FILE"
    else
        log_info "Kept $BIT_FILE"
    fi

    echo "-------- Module 7 complete --------"
}

do_reboot() {
    echo "Syncing filesystem..."
    sync
    echo "Rebooting in 3 seconds..."
    sleep 3
    reboot
}

# --- Main menu ---

show_menu() {
    echo ""
    echo "========================================"
    echo "  Relay Protection - System Init"
    echo "  Product: ${PRODUCT}  Screen: ${SCREEN}"
    echo "========================================"
    echo "  0) Auto install (modules 1-6)"
    echo "  1) Hardware & system config"
    echo "  2) Service optimization"
    echo "  3) Desktop optimization"
    echo "  4) Downloads (backend/frontend/FPGA)"
    echo "  5) Service register & kiosk autostart"
    echo "  6) Screen rotation (post-reboot)"
    echo "  7) FPGA flash (OpenOCD)"
    echo "  8) Reboot"
    echo "  q) Quit"
    echo "========================================"
    echo -n "  Select [0-8/q]: "
}

main() {
    while true; do
        show_menu
        read -r choice
        case "$choice" in
            0)
                module_1_hardware
                module_2_services
                module_3_desktop
                module_4_download
                module_5_register
                module_6_rotation
                echo ""
                echo "========================================"
                echo "  All modules (1-6) complete!"
                echo "  Select 8 to reboot."
                echo "========================================"
                ;;
            1) module_1_hardware ;;
            2) module_2_services ;;
            3) module_3_desktop ;;
            4) module_4_download ;;
            5) module_5_register ;;
            6) module_6_rotation ;;
            7) module_7_fpga_flash ;;
            8) do_reboot ;;
            q|Q) echo "Exiting."; exit 0 ;;
            *) echo "Invalid option: $choice" ;;
        esac
    done
}

main "$@"
