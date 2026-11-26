# 树莓派 5 自动下载 FPGA 固件与 xc3sprog 适配说明文档

本文档详细记录了本项目中 Spartan-6 FPGA 的开机自动烧录配置、针对树莓派 5 硬件所做出的 `xc3sprog` 源码定制修改、以及手动烧录和链路测试的完整指令。

---

## 一、 开机自动下载配置

为了在系统上电开机时自动将指定的 `.bit` 固件加载到 Spartan-6 FPGA，我们配置了专用的 `systemd` 服务。

### 1.1 服务配置文件
- **文件路径**: `/etc/systemd/system/fpga-program.service`
- **当前配置的固件路径**: `/home/pi/Desktop/core/new/fpgabit/0512/0512.bit`

### 1.2 服务内容详情
```ini
[Unit]
Description=Program FPGA Bitstream on Boot
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/xc3sprog -c sysfsgpio_rpi -p 0 /home/pi/Desktop/core/new/fpgabit/0512/0512.bit
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

### 1.3 服务维护与更新指令
* **查看服务运行状态**:
  ```bash
  sudo systemctl status fpga-program.service
  ```
* **手动重新触发一次烧录**:
  ```bash
  sudo systemctl start fpga-program.service
  ```
* **更新开机固件文件**:
  若要更换新的固件（例如换成新的 `.bit` 文件），只需使用管理员权限编辑 `/etc/systemd/system/fpga-program.service` 文件中的 `ExecStart` 路径，然后执行以下命令重载并使之生效：
  ```bash
  sudo systemctl daemon-reload
  ```

---

## 二、 对比 GitHub 原始 `xc3sprog` 的源码修改

由于 **树莓派 5 (Raspberry Pi 5) 引入了全新的 RP1 I/O 控制芯片**，传统的 BCM GPIO 寄存器在 Linux 系统的 `sysfs` 接口下的基础偏移量（GPIO Base）不再为 `0`，而是变为了动态变化的数值（例如 `571` 或 `569`）。
官方原版的 `xc3sprog` 固定的 GPIO 编号无法兼容这种动态偏移，导致在树莓派 5 上无法工作。

为了解决这个问题，我们在项目中对 `xc3sprog` 源码进行了二次开发，新增并注册了专为树莓派 5 定制的 `sysfsgpio_rpi` 下载缆线类型。

### 2.1 修改文件清单与具体实现

在子目录 `xc3sprog/` 下，我们主要添加并修改了以下文件：

#### 1. 【新增】 [sysfsrpi.h](file:///home/pi/Desktop/core/new/xc3sprog/sysfsrpi.h) 与 [sysfsrpi.cpp](file:///home/pi/Desktop/core/new/xc3sprog/sysfsrpi.cpp)
我们定义并实现了 `IOSysFsRPi` 类（继承自 `IOSysFsGPIO`），其中包含一个**动态探测 GPIO 基础偏移量**的核心函数：
```cpp
static int get_rp1_gpio_base() {
    DIR *dir = opendir("/sys/class/gpio");
    if (!dir) return 571; // 默认回退值
    
    struct dirent *ent;
    int base = 571;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "gpiochip", 8) == 0) {
            char path[256];
            snprintf(path, sizeof(path), "/sys/class/gpio/%s/label", ent->d_name);
            FILE *f = fopen(path, "r");
            if (f) {
                char label[256];
                if (fgets(label, sizeof(label), f)) {
                    if (strstr(label, "pinctrl-rp1") != NULL) {
                        base = atoi(ent->d_name + 8); // 解析出真实的 RP1 基础偏移量
                        fclose(f);
                        break;
                    }
                }
                fclose(f);
            }
        }
    }
    closedir(dir);
    return base;
}
```
通过该基准偏移量，我们动态绑定了物理引脚的 JTAG 信号线（TMS=24, TCK=25, TDI=23, TDO=22）：
```cpp
IOSysFsRPi::IOSysFsRPi()
 : IOSysFsGPIO(get_rp1_gpio_base() + 24,  // TMS
               get_rp1_gpio_base() + 25,  // TCK
               get_rp1_gpio_base() + 23,  // TDI
               get_rp1_gpio_base() + 22)  // TDO
{}
```

#### 2. 【修改】 [CMakeLists.txt](file:///home/pi/Desktop/core/new/xc3sprog/CMakeLists.txt)
在构建目标 `xc3sproglib` 的 `add_library` 声明中，加入了新增的 `sysfsrpi.cpp` 文件，使其参与编译：
```diff
-add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp ioftdi.cpp 
+add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp sysfsrpi.cpp ioftdi.cpp 
```

#### 3. 【修改】 [cabledb.h](file:///home/pi/Desktop/core/new/xc3sprog/cabledb.h) 与 [cabledb.cpp](file:///home/pi/Desktop/core/new/xc3sprog/cabledb.cpp)
- 在 `CABLES_TYPES` 枚举中添加了 `CABLE_SYSFS_GPIO_RPI` 缆线类型。
- 在名称映射表中注册了缆线名称字符串 `"sysfsgpio_rpi"`。

#### 4. 【修改】 [cablelist.txt](file:///home/pi/Desktop/core/new/xc3sprog/cablelist.txt)
向适配器列表中注册了 `sysfsgpio_rpi` 的类型配置：
```text
sysfsgpio_rpi  sysfsgpio_rpi 0     NULL
```

#### 5. 【修改】 [utilities.cpp](file:///home/pi/Desktop/core/new/xc3sprog/utilities.cpp)
引入了 `sysfsrpi.h` 头文件，并在 `getIO()` 解析适配器时进行了工厂类实例化分发：
```cpp
else if(cable->cabletype == CABLE_SYSFS_GPIO_RPI)  
{
    io->reset(new IOSysFsRPi());
    io->get()->setVerbose(verbose);
    res = io->get()->Init(cable, serial, use_freq);
}
```

---

## 三、 FPGA 烧录与测试命令

编译安装好的 `xc3sprog` 支持通过自定义的 `sysfsgpio_rpi` 缆线直接对板载 Spartan-6 芯片进行调试和编程。

### 3.1 JTAG 链路检测
执行以下命令来扫描并确认 JTAG 链上的 FPGA 芯片状态：
```bash
sudo xc3sprog -c sysfsgpio_rpi
```
**预期输出示例**：
```text
JTAG loc.:   0  IDCODE: 0x24004093  Desc: XC6SLX25 Rev: C  IR length:  6
```
> ⚠️ **注意**：如果未能识别到 IDCODE 或芯片型号，说明硬件物理接线（尤其是 TMS/TCK/TDI/TDO 或共地线 GND）存在松动或参考电平不一致，请立即断电检查连线。

### 3.2 手动烧录固件命令
执行以下命令将指定的 `.bit` 固件手动写入 FPGA 芯片：
```bash
sudo xc3sprog -c sysfsgpio_rpi -v -p 0 /home/pi/Desktop/core/new/fpgabit/0512/0512.bit
```
- `-c sysfsgpio_rpi`：指定使用我们为树莓派 5 编写的 `sysfsgpio_rpi` JTAG 驱动。
- `-v`：输出详细的烧录日志（Verbose Mode）。
- `-p 0`：烧录 JTAG 链路上的第 0 号设备（即当前识别到的 XC6SLX25 芯片）。

正常烧录大约需要 **28 秒** 左右，烧录完成后即可立即运行 FPGA 侧的逻辑。
