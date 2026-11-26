# Relay Protection - NVMe Clone & Boot

> [!WARNING]
> 以 root 运行 `deploy/nvme-clone.sh`，脚本会自动提权。

运行 `./deploy/nvme-clone.sh` 后选 `0`，一键自动将 TF 卡系统克隆至 NVMe SSD 并配置优先启动。

---

## 前提条件

- Pi 5 已通过 PCIe 连接 NVMe SSD
- TF 卡系统已部署完毕（init.sh 模块 1-6 + 重启验证通过）
- 系统已更新到最新（`sudo apt update && sudo apt full-upgrade`）

---

## 模块 1：检查设备 (Check Devices)

```bash
# 自动检测并确认 NVMe 设备（如 nvme0n1）
# 对比 TF 卡（mmcblk0）与 NVMe 的容量大小
```

---

## 模块 2：克隆系统 (Clone TF -> NVMe)

```bash
# 1. 在 NVMe 上创建分区表
#    - boot 分区 (p1)：与 TF 卡 boot 完全等大
#    - root 分区 (p2)：吃满 NVMe 剩余的所有空间
# 2. dd 块级克隆 boot 分区
# 3. 格式化 root 分区 (mkfs.ext4) 并通过 rsync 同步所有文件
```

---

## 模块 3：验证分区 (Verify NVMe Partition)

```bash
# 确认 root 分区 (p2) 已经成功扩展至 NVMe 的全盘容量
```

---

## 模块 4：修复 UUID (Fix PARTUUID)

```bash
# 为 NVMe 重新生成全新的磁盘 UUID，防止与 TF 卡冲突
# 挂载 NVMe 分区
# 将 cmdline.txt 里的 root=PARTUUID 替换为 NVMe 的新 UUID
# 将 /etc/fstab 里的启动挂载点替换为 NVMe 的新 UUID
```

---

## 模块 5：设置启动顺序 (Set Boot Order)

```bash
# 写入 EEPROM 配置 (rpi-eeprom-config)
# BOOT_ORDER=0xf16 (NVMe优先(6) -> TF卡保底(1) -> 重启(f))
# PCIE_PROBE=1 (开启 PCIe 探测)
```

---

## 模块 6：验证启动源 (Verify Boot Source)

```bash
# 在重启后执行，验证根目录 / 是否挂载在 nvme0n1p2
# 验证 findmnt -n -o SOURCE / 的输出结果
```

---

## 执行顺序

```text
一键克隆：选 0（自动执行模块 1-5） → 选 8 重启生效
```

---

## 应急回滚 (Rollback)

如果 NVMe 损坏需要临时通过 TF 卡启动：
```bash
# 在菜单中选择 7 (Rollback)
# 脚本会将 BOOT_ORDER 恢复为 0xf14（TF卡优先）并关闭 PCIE_PROBE
```
