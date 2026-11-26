# 树莓派5 初始配置说明

## 一键初始化

在全新烧录的 Raspberry Pi OS (Bookworm Lite) 上，通过 SSH 登录后执行：

```bash
cd ~/Desktop/Relay-protection/newrelay
sudo bash InitCofig/init.sh
```

脚本会交互式引导完成以下 8 项配置：

| 步骤 | 内容 | 说明 |
|------|------|------|
| 1 | 屏幕选择 | 7.0寸H / 10.1寸 / 10.1寸V2（自动旋转） |
| 2 | 串口配置 | uart0=on + 关闭 console 串口输出 |
| 3 | 开机动画 | splash.png + 隐藏 Linux 启动日志 |
| 4 | WiFi 配置 | 可选输入 WiFi SSID/密码连接网络 |
| 5 | 软件部署 | 从阿里云下载最新版本 → /opt/RelayProtection/ |
| 6 | Kiosk 浏览器 | Cage + Chromium 全屏显示 index.html |
| 7 | 系统精简 | 禁用无用服务加快开机 |
| 8 | 权限配置 | gpio/dialout/plugdev/sudoers |

完成后运行 `sudo reboot` 重启即可。

---

## 配置文件清单

| 文件 | 说明 |
|------|------|
| `init.sh` | 一键初始化主脚本 |
| `deploy.sh` | 后续手动重新部署（不含硬件配置） |
| `relay-protection.service` | 后端 systemd 服务 |
| `kiosk.service` | Chromium kiosk 服务（标准） |
| `kiosk-rotated.service` | Chromium kiosk 服务（旋转90°） |
| `splash.png` | 开机动画图片 |

---

## 手动配置参考

### 屏幕 dtoverlay 对照表

| 屏幕 | dtoverlay |
|------|----------|
| 7.0寸 H型 | `dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0` |
| 10.1寸 | `dtoverlay=vc4-kms-dsi-waveshare-panel,10_1_inch,dsi0` |
| 10.1寸 V2 | `dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a,dsi0` + 旋转90° |

### 串口配置

```ini
# /boot/firmware/config.txt
dtparam=uart0=on
```

```bash
# 从 cmdline.txt 删除
console=serial0,115200
```
