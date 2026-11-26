# deploy/ — 编译 / 发布 / 部署 / 初始化

所有与构建、发布、设备部署、初始化相关的脚本、产物、前端页面和文档都集中在本目录。

## 目录结构

| 路径 | 说明 |
|------|------|
| `build.sh` | Nuitka 编译后端 + 打包 `output/backend.tar.gz`（`--pack-only` 跳过编译） |
| `publish.sh` | 发布 后端+前端 到阿里云（交互选版本号，服务器端校验合包） |
| `publish-web.sh` | 仅发布前端（= `publish.sh --web-only`） |
| `publish_init.sh` | 打包上传初始化安装包（init.sh + service） |
| `init.sh` | 全新树莓派一键初始化（屏幕/串口/静默启动/WiFi权限/下载部署/Kiosk/精简/权限） |
| `deploy.sh` | 本机 output/dist/ 手动部署到 /opt/RelayProtection/（开发用） |
| `version.json` | 本地版本号（publish.sh 发布时自动维护） |
| `web/index.html` | 前端页面源文件（发布时上传，本地打包进 output/dist/） |
| `publish-web.ps1` | Windows 上仅发布前端（脚本+ssh.pem 拷到 Windows 用，见 docs/Windows前端发布.md） |
| `docs/` | 发布指南 / 部署指南 / Windows前端发布 / 部署与OTA更新手册 |
| `build/` | Nuitka 编译产物 SysEngine.dist + 缓存 SysEngine.build（不进 git） |
| `output/` | 发布产物：dist/（本地完整运行目录）+ backend.tar.gz(.sha256)（不进 git） |
| `relay-protection.service` | 后端 systemd 服务文件 |
| `ssh.pem` | 阿里云 SSH 密钥（不进 git，.gitignore 的 *.pem 规则） |

不在本目录的相关文件：

- `RelayProtection/utils/updater.py` — OTA 更新脚本源码；打包时复制到设备安装根目录 `/opt/RelayProtection/updater.py`（**设备端路径不能变**：sudoers 白名单固定为该路径。它必须是独立的纯标准库脚本：更新时要替换并重启 SysEngine 自身，不能合并进主程序）
- `RelayProtection/docs/api/OTA更新通讯协议.md` — 前后端 WebSocket OTA 协议

## 常用命令（在项目根目录执行）

```bash
./deploy/build.sh               # 编译 + 打包后端（15~30 分钟）
./deploy/build.sh --pack-only   # 只打包不编译（秒级）
./deploy/publish.sh             # 发布 后端+前端
./deploy/publish-web.sh         # 仅发布前端（无需编译打包）
./deploy/publish_init.sh        # 上传初始化安装包
./deploy/deploy.sh              # output/dist/ 部署到本机 /opt/（本地测试）
```

## 全新设备初始化

在全新烧录的 Raspberry Pi OS (Bookworm Lite) 上：

```bash
curl -sL http://47.98.48.17/update/relay12/init-pack.tar.gz | tar xz && sudo bash deploy/init.sh
```

init.sh 交互式引导完成 8 项配置：

| 步骤 | 内容 | 说明 |
|------|------|------|
| 1 | 屏幕选择 | 7.0寸H / 10.1寸 / 10.1寸V2（自动旋转90°） |
| 2 | 串口配置 | uart0=on + 关闭 console 串口输出（FPGA 通信） |
| 3 | 静默启动 | 隐藏启动日志/光标、屏常亮、清华 APT 镜像（无开机动画，黑屏直达界面） |
| 4 | WiFi 权限 | NetworkManager + polkit 规则（后端程序可免授权控制 WiFi） |
| 5 | 软件部署 | 从阿里云 relay12 目录下载最新版 → /opt/RelayProtection/；并安装 systemd 服务、sudoers、**USB 自动挂载**（udev + systemd，必装） |
| 6 | Kiosk 浏览器 | sway(Wayland) + Chromium kiosk，与桌面版同路径（触摸拖动一致），光标空闲自动隐藏 |
| 7 | 精简+加固 | 禁服务、看门狗、日志入RAM、zram压缩交换（1GB内存关键） |
| 8 | 权限配置 | gpio/dialout/plugdev/video/render/input 组 |

完成后 `sudo reboot`。

## 屏幕 dtoverlay 对照表

| 屏幕 | dtoverlay |
|------|----------|
| 7.0寸 H型 | `dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0` |
| 10.1寸 | `dtoverlay=vc4-kms-dsi-waveshare-panel,10_1_inch,dsi0` |
| 10.1寸 V2 | `dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a,dsi0` + 旋转90° |

## USB 自动挂载说明（U 盘离线更新依赖）

- udev 规则 `99-usb-automount.rules` 检测到 U 盘后**触发 systemd 服务** `usb-mount@.service` 挂载到 `/media/pi/<卷标>`
- 不能在 udev RUN 里直接 mount：systemd-udevd 在私有挂载命名空间运行，挂载对系统不可见（这就是之前"不手动挂载就检测不到 U 盘"的原因）
- 后端 `usbScan` 还有兜底：扫描时对未挂载的 USB 分区主动 `sudo mount`

## 文档

- [docs/发布指南.md](docs/发布指南.md) — 怎么发版（后端/前端/Windows）
- [docs/部署指南.md](docs/部署指南.md) — 新设备初始化、本地部署、服务管理
- [docs/初始化命令说明.md](docs/初始化命令说明.md) — init.sh 每条命令干了什么、旧设备手动补丁
- [docs/Windows前端发布.md](docs/Windows前端发布.md) — 给只管网页的同事看
- [docs/部署与OTA更新手册.md](docs/部署与OTA更新手册.md) — 完整原理手册
