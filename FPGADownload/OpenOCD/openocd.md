# OpenOCD Usage (Raspberry Pi 5)

Usage guide for OpenOCD SPI Flash programming on XC6SLX25 + M25P16.

---

## 1. Install

```bash
sudo apt update && sudo apt install -y openocd gpiod
```

## 2. Program SPI Flash

```bash
sudo FPGADownload/OpenOCD/flash /path/to/fpga.bit
```

or with Python source:

```bash
sudo python3 FPGADownload/OpenOCD/flash.py /path/to/fpga.bit
```

## 3. Detect JTAG Chain

```bash
sudo FPGADownload/OpenOCD/flash --detect
```
