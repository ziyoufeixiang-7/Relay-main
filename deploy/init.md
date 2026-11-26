# Relay Protection - Raspberry Pi Init

> [!WARNING]
> 以 root 运行 `deploy/init.sh`，脚本会自动提权。

运行 `./deploy/init.sh` 后选择对应模块，选 `0` 一键自动执行模块 1-6。

---

## 模块 1：硬件与系统配置

在 `deploy/init.conf` 中修改 `SCREEN` 变量选择屏幕型号后运行。

```bash
# 屏幕尺寸设置 (10_1 或 7_0)
# 10_1: vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a,dsi0
# 7_0 : vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0
SCREEN="10_1"

# config.txt 写入（自动映射 dtoverlay，幂等）
# 串口释放（UART0 给 FPGA）
# 静默启动参数与去除 Logo (logo.nologo)
# APT 清华镜像源
# watchdog 15s + journald 200M 持久化
```

---

## 模块 2：服务优化

```bash
# mask（D-Bus 激活/有 generator，disable 无效）
#   cloud-init 全家、udisks2、rp1-test、glamor-test、accounts-daemon、packagekit
# disable（普通服务）
#   NetworkManager-wait-online、rpi-eeprom-update、sshswitch、e2scrub_reap
#   rpcbind、rpc-statd-notify、nfs-blkmap、systemd-binfmt
# alsa udev 空规则覆盖
# !! rpi-resize-swap-file 不能禁用（管理 zram swap）
```

---

## 模块 3：桌面精简

```bash
# labwc autostart 精简为 kanshi + lxsession-xdg-autostart
# polkit GUI 弹窗禁用（polkitd 保留）
# --global mask（provision 时 pi 无会话，不能用 --user）
#   PipeWire 全家、xdg-desktop-portal-wlr、xdg-document-portal、gvfs 部分组件
# !! xdg-desktop-portal + portal-gtk 保留（浏览器文件选择器依赖）
```

---

## 模块 4：下载

```bash
# 后端：backend.tar.gz + SHA-256 校验 → /opt/RelayProtection/
# 前端：index.html + version.json
# FPGA：xc3sprog + flash + fpga.bit
# 启动画面：根据 SCREEN 自动下载对应的 splash_90.png 或 splash.png 并重构 initramfs
# 下载源：http://47.98.48.17/update/
```

---

## 模块 5：服务注册与 Kiosk

```bash
# relay-protection.service（root、OOM-888、自动重启）
# 桌面自动登录 + 屏幕常亮
# Chromium kiosk：--incognito --ozone-platform=wayland
#   --renderer-process-limit=1 --process-per-site（省内存）
#   file:///opt/RelayProtection/index.html
```

---

## 模块 6：屏幕旋转（自适应）

```bash
# 安装 wlr-randr
# 自动检测 DRM 接口 (初次无驱动时后备预判为 DSI-1)
# 依据 SCREEN 设置自动输出 kanshi 旋转配置（10_1 设为 90，7_0 设为 normal）
```

---

## 执行顺序

```text
一次成型：选 0（一键自动执行模块 1-6） → 选 8 重启生效
（由于加入了 DSI-1 预判，底层驱动与桌面旋转会在首次重启时同步生效，无需再重启第二次）
```
