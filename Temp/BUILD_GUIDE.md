# RelayProtection 编译部署指南

本文档描述如何将 RelayProtection 系统编译为一个**无需 Python 环境**即可运行的独立可执行程序。

## 1. 环境要求

| 项目 | 要求 |
|------|------|
| 硬件 | Raspberry Pi (aarch64) |
| 操作系统 | Debian 13 (trixie) 或兼容系统 |
| Python | 3.13+ (虚拟环境) |
| GCC | 14.x |
| Nuitka | 4.0+ |
| 磁盘空间 | ≥ 2GB（编译过程中临时文件较大） |

### 1.1 检查环境

```bash
# 检查架构
uname -m
# 期望输出: aarch64

# 检查 GCC
gcc --version
# 期望输出: gcc (Debian 14.2.0-19) 14.2.0

# 检查 Python（使用虚拟环境）
~/Desktop/Relay-protection/newrelay/.venv/bin/python --version
# 期望输出: Python 3.13.x

# 检查 Nuitka
~/Desktop/Relay-protection/newrelay/.venv/bin/python -m nuitka --version
# 期望输出: 4.0.x
```

## 2. 编译

### 2.1 一键编译

```bash
cd ~/Desktop/Relay-protection/newrelay
./deploy/build.sh
```

### 2.2 编译耗时

| 阶段 | 预计时间 |
|------|---------|
| Nuitka 分析依赖 | 2-5 分钟 |
| C 代码生成 | 5-10 分钟 |
| GCC 编译 | 20-40 分钟 |
| **总计** | **30-60 分钟** |

> **注意：** 首次编译耗时最长。如果只修改了少量 Python 文件后重新编译，Nuitka 会利用缓存，编译时间会大幅缩短（通常 5-10 分钟）。

### 2.3 编译过程中的正常输出

编译过程中会看到大量 GCC 编译输出，这是正常的。只要最终看到以下内容即表示成功：

```
========================================
  Build Complete!
========================================
Output directory: .../SysEngine.dist/
Run with:         .../SysEngine.dist/SysEngine.bin.bin
```

## 3. 编译产物

编译完成后，`SysEngine.dist/` 目录结构如下：

```
SysEngine.dist/
├── SysEngine.bin          ← 主可执行文件（原生 aarch64 ELF）
├── config/
│   └── calibration.json   ← 校准配置文件（可读写）
├── *.so                   ← 编译后的 Python 模块和 C 扩展库
└── ...                    ← Nuitka 运行时依赖
```

### 3.1 验证编译产物

```bash
# 确认是 aarch64 可执行文件
file SysEngine.dist/SysEngine.bin.bin
# 期望输出包含: ELF 64-bit LSB pie executable, ARM aarch64

# 确认 config 文件存在
ls -la SysEngine.dist/config/calibration.json

# 查看总大小
du -sh SysEngine.dist/
```

## 4. 运行

### 4.1 直接运行

```bash
cd /path/to/SysEngine.dist
./SysEngine.bin
```

### 4.2 后台运行

```bash
cd /path/to/SysEngine.dist
nohup ./SysEngine.bin > /tmp/relay.log 2>&1 &
```

### 4.3 部署到其他树莓派

将整个 `SysEngine.dist/` 目录复制到目标树莓派即可运行，**无需安装 Python 或任何依赖**：

```bash
# 在源机器上打包
tar czf relay-protection.tar.gz SysEngine.dist/

# 复制到目标机器
scp relay-protection.tar.gz pi@<目标IP>:/home/pi/

# 在目标机器上解压并运行
ssh pi@<目标IP>
tar xzf relay-protection.tar.gz
cd SysEngine.dist
./SysEngine.bin
```

## 5. 校准配置文件

`calibration.json` 位于 `SysEngine.dist/config/` 目录下。程序运行时会自动从可执行文件同级的 `config/calibration.json` 路径读写此文件。

- **更新校准数据：** 通过前端界面运行 AutoCal 后，程序会自动更新此文件。
- **手动替换：** 也可以直接替换 `config/calibration.json` 文件，重启程序后生效。
- **备份：** 建议在每次校准后备份此文件。

## 6. 常见问题

### Q: 编译报错 `gcc: error: ...`

确认 GCC 已安装：
```bash
sudo apt install gcc g++ build-essential
```

### Q: 编译报错 `No module named 'xxx'`

确认虚拟环境中安装了所有依赖：
```bash
~/Desktop/Relay-protection/newrelay/.venv/bin/pip install websockets orjson pyserial pyserial-asyncio gpiozero lgpio uvloop pyvisa pyvisa-py pyusb
```

### Q: 运行时报 `Permission denied` (串口/GPIO)

需要确保运行用户有串口和 GPIO 权限：
```bash
sudo usermod -aG dialout,gpio pi
# 重新登录后生效
```

### Q: 运行时 calibration.json 找不到

确认 `config/` 目录与 `SysEngine` 可执行文件在同一目录下：
```
SysEngine.dist/
├── SysEngine.bin
└── config/
    └── calibration.json
```

### Q: 重新编译后 calibration.json 被覆盖

`build.sh` 每次编译都会从源码的 `config/` 目录复制 `calibration.json`。如果你在运行时修改了校准数据，建议在重新编译前**先备份** `SysEngine.dist/config/calibration.json`。

## 7. 重新编译

修改代码后只需重新运行：

```bash
cd ~/Desktop/Relay-protection/newrelay
./deploy/build.sh
```

Nuitka 会自动利用上次编译的缓存（`SysEngine.build/` 目录），只重新编译修改过的文件。
