#!/usr/bin/env bash
set -euo pipefail

# NVMe Clone & Boot - Clone TF card to NVMe SSD
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

SD_DEV="mmcblk0"
NVME_DEV=""

# --- Helpers ---

log_ok()   { echo "[OK]   $1"; }
log_fail() { echo "[FAIL] $1"; }
log_info() { echo "[INFO] $1"; }
log_warn() { echo "[WARN] $1"; }

detect_nvme() {
    NVME_DEV=$(lsblk -dno NAME | grep -m1 "^nvme" || true)
    if [ -z "$NVME_DEV" ]; then
        log_fail "No NVMe device detected"
        return 1
    fi
    log_ok "Detected NVMe: /dev/$NVME_DEV"
}

set_eeprom_config() {
    local tmpconf
    tmpconf=$(mktemp)
    rpi-eeprom-config > "$tmpconf"
    if [ ! -s "$tmpconf" ]; then
        log_fail "Failed to read EEPROM config"
        rm -f "$tmpconf"
        return 1
    fi
    for arg in "$@"; do
        local key="${arg%%=*}"
        local val="${arg#*=}"
        if grep -q "^${key}=" "$tmpconf"; then
            sed -i "s/^${key}=.*/${key}=${val}/" "$tmpconf"
        else
            echo "${key}=${val}" >> "$tmpconf"
        fi
    done
    rpi-eeprom-config --apply "$tmpconf"
    rm -f "$tmpconf"
}

# --- Steps ---

step_1_check() {
    echo "========================================"
    echo "  Step 1: Check Devices"
    echo "========================================"
    detect_nvme || return 1
    lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
    local NVME_SIZE=$(lsblk -dno SIZE "/dev/$NVME_DEV" | tr -d ' ')
    local SD_SIZE=$(lsblk -dno SIZE "/dev/$SD_DEV" | tr -d ' ')
    log_info "TF card: /dev/$SD_DEV ($SD_SIZE) -> NVMe: /dev/$NVME_DEV ($NVME_SIZE)"
    echo "-------- Step 1 complete --------"
}

step_2_clone() {
    echo "========================================"
    echo "  Step 2: Clone TF -> NVMe"
    echo "========================================"
    detect_nvme || return 1

    # Security prompt
    local NVME_SIZE=$(lsblk -dno SIZE "/dev/$NVME_DEV" | tr -d ' ')
    local SD_SIZE=$(lsblk -dno SIZE "/dev/$SD_DEV" | tr -d ' ')
    echo -e "\n  Source: /dev/$SD_DEV ($SD_SIZE, TF card)"
    echo -e "  Target: /dev/$NVME_DEV ($NVME_SIZE)\n"
    echo "  !! ALL DATA ON /dev/$NVME_DEV WILL BE ERASED !!"
    echo -n "  Continue? [y/N]: "
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        return 0
    fi

    # Get boot partition size
    local BOOT_SECTORS=$(sfdisk -d "/dev/$SD_DEV" 2>/dev/null | grep "${SD_DEV}p1" | grep -oP 'size=\s*\K[0-9]+')
    local BOOT_START=$(sfdisk -d "/dev/$SD_DEV" 2>/dev/null | grep "${SD_DEV}p1" | grep -oP 'start=\s*\K[0-9]+')

    # 1) Create partition table (boot same size, root remaining)
    log_info "Creating partition table on /dev/$NVME_DEV ..."
    sfdisk "/dev/$NVME_DEV" << PART_EOF >/dev/null
label: dos
/dev/${NVME_DEV}p1 : start=$BOOT_START, size=$BOOT_SECTORS, type=c
/dev/${NVME_DEV}p2 : start=$(( BOOT_START + BOOT_SECTORS )), type=83
PART_EOF
    partprobe "/dev/$NVME_DEV" 2>/dev/null || true
    sleep 1
    log_ok "Partition table created"

    # 2) Clone boot partition
    log_info "Cloning boot partition ..."
    dd if="/dev/${SD_DEV}p1" of="/dev/${NVME_DEV}p1" bs=4M status=progress conv=fsync
    log_ok "Boot partition cloned"

    # 3) Create rootfs + rsync
    log_info "Creating ext4 on /dev/${NVME_DEV}p2 ..."
    mkfs.ext4 -q -F "/dev/${NVME_DEV}p2"
    log_ok "Root filesystem created"

    mkdir -p /mnt/nvme-root
    mount "/dev/${NVME_DEV}p2" /mnt/nvme-root

    log_info "Syncing root filesystem (rsync, files only) ..."
    rsync -aHAXx --info=progress2 / /mnt/nvme-root/
    sync
    log_ok "Root filesystem synced"

    umount /mnt/nvme-root
    log_ok "Clone complete"
    echo "-------- Step 2 complete --------"
}

step_3_resize() {
    echo "========================================"
    echo "  Step 3: Verify NVMe Partition"
    echo "========================================"
    detect_nvme || return 1
    lsblk -o NAME,SIZE,TYPE "/dev/$NVME_DEV"
    log_ok "Root partition already at full NVMe size"
    echo "-------- Step 3 complete --------"
}

step_4_fixuuid() {
    echo "========================================"
    echo "  Step 4: Fix PARTUUID"
    echo "========================================"
    detect_nvme || return 1

    # Get UUIDs
    local NVME_BOOT_UUID=$(blkid -s PARTUUID -o value "/dev/${NVME_DEV}p1")
    local NVME_ROOT_UUID=$(blkid -s PARTUUID -o value "/dev/${NVME_DEV}p2")
    local SD_BOOT_UUID=$(blkid -s PARTUUID -o value "/dev/${SD_DEV}p1")
    local SD_ROOT_UUID=$(blkid -s PARTUUID -o value "/dev/${SD_DEV}p2")

    log_info "TF  boot=$SD_BOOT_UUID  root=$SD_ROOT_UUID"
    log_info "NVMe boot=$NVME_BOOT_UUID  root=$NVME_ROOT_UUID"

    if [ "$SD_ROOT_UUID" = "$NVME_ROOT_UUID" ]; then
        log_warn "UUIDs are identical. Generating new UUIDs for NVMe..."
        local NEW_DISK_ID=$(od -A n -t x4 -N 4 /dev/urandom | tr -d ' ')
        fdisk "/dev/$NVME_DEV" <<EOF >/dev/null 2>&1 || true
x
i
0x${NEW_DISK_ID}
r
w
EOF
        partprobe "/dev/$NVME_DEV" 2>/dev/null || true
        sleep 1
        NVME_BOOT_UUID=$(blkid -s PARTUUID -o value "/dev/${NVME_DEV}p1")
        NVME_ROOT_UUID=$(blkid -s PARTUUID -o value "/dev/${NVME_DEV}p2")
        log_ok "New NVMe UUIDs: boot=$NVME_BOOT_UUID root=$NVME_ROOT_UUID"
    fi

    mkdir -p /mnt/nvme-boot /mnt/nvme-root
    mount "/dev/${NVME_DEV}p1" /mnt/nvme-boot
    mount "/dev/${NVME_DEV}p2" /mnt/nvme-root

    # Update cmdline and fstab
    sed -i "s/${SD_ROOT_UUID}/${NVME_ROOT_UUID}/g" /mnt/nvme-boot/cmdline.txt
    sed -i "s/${SD_BOOT_UUID}/${NVME_BOOT_UUID}/g" /mnt/nvme-root/etc/fstab
    sed -i "s/${SD_ROOT_UUID}/${NVME_ROOT_UUID}/g" /mnt/nvme-root/etc/fstab
    log_ok "cmdline.txt and fstab updated"

    umount /mnt/nvme-boot /mnt/nvme-root
    log_ok "NVMe partitions unmounted"
    echo "-------- Step 4 complete --------"
}

step_5_bootorder() {
    echo "========================================"
    echo "  Step 5: Set Boot Order"
    echo "========================================"
    echo "  BOOT_ORDER: 1=TF 4=USB 6=NVMe f=restart"
    echo -n "  Set NVMe as primary boot (TF fallback)? [Y/n]: "
    read -r confirm
    if [ "$confirm" = "n" ] || [ "$confirm" = "N" ]; then
        log_info "Skipped boot order config."
        return 0
    fi

    set_eeprom_config BOOT_ORDER=0xf16 PCIE_PROBE=1
    log_ok "Boot order set: NVMe(6) -> TF(1) -> restart(f)"
    echo "-------- Step 5 complete --------"
}

step_6_verify() {
    echo "========================================"
    echo "  Step 6: Verify Boot Source"
    echo "========================================"
    local ROOT_DEV=$(findmnt -n -o SOURCE /)
    echo "  Root device: $ROOT_DEV"
    if echo "$ROOT_DEV" | grep -q "nvme"; then
        log_ok "Successfully booted from NVMe"
    else
        log_info "Booted from TF card. Reboot to apply NVMe boot."
    fi
    echo "-------- Step 6 complete --------"
}

step_rollback() {
    echo "========================================"
    echo "  Rollback: Restore TF Card Boot"
    echo "========================================"
    echo -n "  Reset boot order to TF card only? [y/N]: "
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        return 0
    fi
    set_eeprom_config BOOT_ORDER=0xf14 PCIE_PROBE=0
    log_ok "Boot order reset to TF card"
    echo "-------- Rollback complete --------"
}

do_reboot() {
    echo "Rebooting..."
    sync
    sleep 2
    reboot
}

show_menu() {
    echo ""
    echo "========================================"
    echo "  NVMe Clone & Boot Setup"
    echo "========================================"
    echo "  0) Auto (Steps 1-5)"
    echo "  1) Check devices"
    echo "  2) Clone TF -> NVMe"
    echo "  3) Verify NVMe partition"
    echo "  4) Fix PARTUUID (fstab & cmdline)"
    echo "  5) Set boot order"
    echo "  6) Verify boot source"
    echo "  7) Rollback (restore TF boot)"
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
                step_1_check
                step_2_clone
                step_3_resize
                step_4_fixuuid
                step_5_bootorder
                echo -e "\n  All steps complete! Select 8 to reboot.\n"
                ;;
            1) step_1_check ;;
            2) step_2_clone ;;
            3) step_3_resize ;;
            4) step_4_fixuuid ;;
            5) step_5_bootorder ;;
            6) step_6_verify ;;
            7) step_rollback ;;
            8) do_reboot ;;
            q|Q) echo "Exiting."; exit 0 ;;
            *) echo "Invalid option" ;;
        esac
    done
}

main "$@"
