# init.sh 执行命令清单

本文件用「实际命令 + 注释」逐模块列出 `deploy/init.sh` 做的事，便于审阅与手动复现。
`$REAL_USER`=运行 sudo 的用户（默认 pi），`$CONFIG_FILE=/boot/firmware/config.txt`，
`$CMDLINE_FILE=/boot/firmware/cmdline.txt`，`$INSTALL_DIR=/opt/RelayProtection`。

> 模块 2–8、5+ 均有 `[Y/n]` 确认；模块 1 与模块 5 的「装服务/USB 挂载」无条件执行。

---

## 模块 1：屏幕选择（无条件）

```bash
cp /boot/firmware/config.txt /boot/firmware/config.txt.bak-init   # 首次备份
sed -i '/dtoverlay=vc4-kms-dsi-waveshare-panel/d' "$CONFIG_FILE"   # 清旧屏 overlay
grep -q '^dtoverlay=vc4-kms-v3d' "$CONFIG_FILE" || echo 'dtoverlay=vc4-kms-v3d' >> "$CONFIG_FILE"
# 按所选屏写入，例（7寸H）：
echo 'dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0' >> "$CONFIG_FILE"
```

## 模块 2：串口（FPGA 通信）

```bash
echo 'dtparam=uart0=on' >> "$CONFIG_FILE"                          # 启用硬件串口 uart0
cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak-init  # 首次备份
sed -i -E 's/console=(serial0|ttyAMA0),[0-9]+ *//g' "$CMDLINE_FILE" # 移除串口控制台
```

## 模块 3：静默启动 + 清华 APT 源

```bash
# config.txt：去彩虹屏 + 减延迟 + 关 HDMI 热插拔（用 DSI 屏）
printf 'disable_splash=1\nboot_delay=0\nhdmi_ignore_hotplug=1\n' >> "$CONFIG_FILE"
# cmdline.txt：静默 + 隐光标 + 屏常亮（console 必须保留 tty1）
#   追加 quiet loglevel=0 vt.global_cursor_default=0 consoleblank=0
# APT 换清华镜像（备份到 /etc/apt/backup-init/，避免 sources.list.d 噪声）
sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list*
sed -i 's|http://archive.raspberrypi.com/debian|https://mirrors.tuna.tsinghua.edu.cn/raspberrypi|g' /etc/apt/sources.list.d/*
apt-get update -qq || true        # 失败非致命
```

## 模块 4：WiFi 管理权限

```bash
systemctl enable NetworkManager
usermod -aG netdev "$REAL_USER"
# polkit 放行：$REAL_USER 免授权管理 NetworkManager
cat > /etc/polkit-1/rules.d/10-network-manager.rules <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.")===0 && subject.user==="pi")
        return polkit.Result.YES;
});
EOF
```

## 模块 5：下载部署后端（下载可选；装服务无条件）

```bash
# 下载（可选）：从更新服务器取版本→下载→SHA256 校验→解压
curl -fsSL "$SERVER_URL/version.json"            # 取 url/sha256/version
curl -f -o /tmp/relay.tar.gz "<DL_URL>"
sha256sum /tmp/relay.tar.gz                       # 比对服务器 sha256
tar -xzf /tmp/relay.tar.gz -C "$INSTALL_DIR/"
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"

# 安装 systemd 服务（无条件）
cp relay-protection.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable relay-protection

# sudoers 白名单（写后 visudo -c 校验，损坏即删）
cat > /etc/sudoers.d/relay-protection <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart relay-protection, .../stop ..., .../start ...
$REAL_USER ALL=(ALL) NOPASSWD: /usr/bin/python3 /opt/RelayProtection/updater.py *
$REAL_USER ALL=(ALL) NOPASSWD: /usr/bin/mount, /usr/bin/umount
EOF
visudo -cf /etc/sudoers.d/relay-protection

# USB 自动挂载（udev 触发 systemd 服务，挂到主命名空间）
#   /usr/local/bin/usb-automount.sh + usb-autoumount.sh
#   /etc/systemd/system/usb-mount@.service
#   /etc/udev/rules.d/99-usb-automount.rules
udevadm control --reload-rules && udevadm trigger --subsystem-match=block
```

## 模块 5+：FPGA 开机自动烧录（可选）

```bash
# 仅在开启时下载固件+工具；不开启则不下载任何本地文件
curl -fsSL -u "fpga:<密码>" "$SERVER_URL/../fpga/fpga-pack.tar.gz" -o fpga-pack.tar.gz
sha256sum fpga-pack.tar.gz            # 比对服务器 .sha256
tar -xzf fpga-pack.tar.gz && bash fpga-deploy.sh
#   → 装 xc3sprog + bit 到 /opt/FPGADownload，建 fpga-download.service（开机 GPIO-JTAG 烧录）
```

## 模块 6：Kiosk 浏览器（sway + Chromium）

```bash
apt-get install -y --no-install-recommends sway chromium fonts-noto-cjk
# 字体（默认 MiSans，从更新服务器下载到 /usr/share/fonts/truetype/relay-custom）
curl -fsSL "$SERVER_URL/../fonts/MiSans/MiSans-Regular.ttf" -o .../MiSans-Regular.ttf
# /etc/fonts/local.conf 把 sans-serif 首选设为 MiSans；fc-cache -f

# tty1 自动登录（免密、静默）
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $REAL_USER --noclear --noissue %I \$TERM
EOF
touch "/home/$REAL_USER/.hushlogin"

# sway 配置 ~/.config/sway/config：隐光标/无边框/禁 XWayland/可选旋转
#   exec 'chromium <kiosk参数> file:///opt/RelayProtection/index.html; sleep 5; swaymsg exit'
# ~/.bash_profile：tty1 时 exec sway（浏览器退出→重登重拉=崩溃自恢复）
```

## 模块 7：系统精简 + 可靠性加固

```bash
# 禁用无用服务（加快开机）
systemctl disable --now systemd-networkd-wait-online.service ModemManager.service triggerhappy.service
systemctl mask cloud-init.service cloud-init-local.service cloud-config.service cloud-final.service

# 硬件看门狗（卡死自动重启）
grep -q '^dtparam=watchdog=on' "$CONFIG_FILE" || echo 'dtparam=watchdog=on' >> "$CONFIG_FILE"
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOF

# 日志写 RAM（护 SD/SSD）
cat > /etc/systemd/journald.conf.d/volatile.conf <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=32M
EOF

# zram 压缩交换（1GB 内存关键）+ 停 SD swapfile
apt-get install -y --no-install-recommends zram-tools
printf 'ALGO=zstd\nPERCENT=60\n' > /etc/default/zramswap
systemctl enable --now zramswap
systemctl disable --now dphys-swapfile
echo 'vm.swappiness=100' > /etc/sysctl.d/99-relay-zram.conf
```

## 模块 8：用户权限

```bash
usermod -aG gpio dialout plugdev video render input "$REAL_USER"
```

## 收尾

```bash
sync          # 落盘所有配置，避免重启前掉电丢失
sudo reboot   # 重启生效
```
