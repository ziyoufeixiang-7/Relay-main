# FPGA 固件远程部署指南

FPGA 固件随后端程序一起通过云服务器分发到目标树莓派。

---

## 整体流程

```
初始化（TF 卡）                        固件更新（后端 OTA）
┌──────────────────┐                  ┌──────────────────────────────────┐
│ init.sh:         │                  │ SysEngine.bin:                   │
│  安装 xc3sprog   │                  │  下载更新包（含 fpga.bit）        │
│  安装 libftdi    │                  │  → xc3sprog 烧 SPI Flash         │
│  → /opt/FPGA.../ │                  │  → 删除 fpga.bit                 │
└──────────────────┘                  └──────────────────────────────────┘

发布（开发端）
┌──────────────────────────────────┐
│ deploy/publish.sh:               │
│  检测 deploy/fpga.bit            │
│  → 后端 + 前端 + fpga.bit 上传   │
│  → 服务器合包为设备更新包         │
└──────────────────────────────────┘
```

---

## 发布固件（开发端）

```bash
# 将最新的 fpga.bit 放到 deploy/ 目录
cp /path/to/your/fpga.bit ~/Desktop/Relay-protection/newrelay/deploy/fpga.bit

# 发布（自动检测 fpga.bit 是否存在，有则一并上传）
./deploy/publish.sh
```

---

## 初始化目标树莓派

init.sh 自动安装 xc3sprog + libftdi 到 `/opt/FPGADownload/`，不烧录 FPGA。

---

## FPGA 固件更新

由后端程序（SysEngine.bin）处理：
1. 从云端下载更新包（包含 fpga.bit）
2. 调用 `xc3sprog` 烧录到 SPI Flash（W25Q64）
3. 烧录完成后立即删除本地 fpga.bit

```bash
# 手动烧录（调试用）
sudo /opt/FPGADownload/xc3sprog -c sysfsgpio_rpi -v -I W25Q64 fpga.bit
```

---

## 目录结构

```
FPGADownload/
├── xc3sprog                       # 预编译 FPGA 下载工具（树莓派5）
├── xc3sprog.md                    # 编译与使用指南
├── fpga_remote_deploy.md          # 本文档
└── .fpga_auth_pass                # 下载密码（不进 git）

deploy/
├── fpga.bit                       # 最新 FPGA 固件（手动放入，可选）
├── publish.sh                     # 发布脚本（后端+前端+FPGA）
└── init.sh                        # 初始化脚本（安装 xc3sprog）
```

---

## 故障排查

```bash
# JTAG 未检测到 → 检查连线+共地:
sudo /opt/FPGADownload/xc3sprog -c sysfsgpio_rpi
# 预期: JTAG loc.: 0  IDCODE: 0x24004093  Desc: XC6SLX25

# libftdi.so.1 缺失:
sudo apt-get install -y libftdi-dev
```
