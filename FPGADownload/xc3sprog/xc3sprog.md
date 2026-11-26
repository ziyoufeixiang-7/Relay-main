# xc3sprog Usage & Build (Raspberry Pi 5)

Usage and build guide for `xc3sprog`.

---

## 1. Usage

### Install Runtime Dependency

```bash
# sudo apt-get update && sudo apt-get install -y libftdi-dev
sudo apt-get update && sudo apt-get install -y libftdi1-2
```

### Set Execute Permission

```bash
chmod +x ./xc3sprog
```

### Detect JTAG Chain

```bash
sudo ./xc3sprog -c sysfsgpio_rpi
```

### Program Bitstream (SRAM, volatile)

```bash
sudo ./xc3sprog -c sysfsgpio_rpi -v -p 0 fpga.bit
```

### Program Bitstream (SPI Flash, persistent)

---

## 2. Build from Source

### 2.1 Install Dependencies

```bash
sudo apt-get update
sudo apt-get install build-essential cmake libusb-dev libftdi-dev git -y
```

### 2.2 Clone Source

```bash
git clone https://github.com/matrix-io/xc3sprog.git
cd xc3sprog
```

### 2.3 Source Modifications

#### Add `sysfsrpi.h`

```cpp
#ifndef __IO_SYSFS_RPI__
#define __IO_SYSFS_RPI__
#include "sysfs.h"
class IOSysFsRPi : public IOSysFsGPIO
{
 public:
  IOSysFsRPi();
};
#endif
```

#### Add `sysfsrpi.cpp`

```cpp
#include "sysfsrpi.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
static int get_rp1_gpio_base() {
    DIR *dir = opendir("/sys/class/gpio");
    if (!dir) return 571;
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
                        base = atoi(ent->d_name + 8);
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

// JTAG: TMS=GPIO24, TCK=GPIO25, TDI=GPIO23, TDO=GPIO22
IOSysFsRPi::IOSysFsRPi()
 : IOSysFsGPIO(get_rp1_gpio_base() + 24, 
               get_rp1_gpio_base() + 25, 
               get_rp1_gpio_base() + 23, 
               get_rp1_gpio_base() + 22)
{}
```

#### Modify `CMakeLists.txt`

```diff
-add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp ioftdi.cpp 
+add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp sysfsrpi.cpp ioftdi.cpp 
```

#### Modify `cabledb.h`

```diff
 // Append to CABLES_TYPES enum:
+  CABLE_SYSFS_GPIO_RPI,
```

#### Modify `cabledb.cpp`

```diff
 // Add to getCableType():
+    if (strcasecmp(given_name, "sysfsgpio_rpi") == 0)
+      return CABLE_SYSFS_GPIO_RPI;

 // Add to getCableName():
+    case CABLE_SYSFS_GPIO_RPI: return "sysfsgpio_rpi";
```

#### Modify `cablelist.txt`

```diff
 // Append to end of file:
+sysfsgpio_rpi  sysfsgpio_rpi 0     NULL
```

#### Modify `utilities.cpp`

```diff
 // Add header:
+#include "sysfsrpi.h"

 // Add branch in getIO():
+  else if(cable->cabletype == CABLE_SYSFS_GPIO_RPI)  
+  {
+      io->reset(new IOSysFsRPi());
+      io->get()->setVerbose(verbose);
+      res = io->get()->Init(cable, serial, use_freq);
+  }

 // Add to getCableName():
+  case CABLE_SYSFS_GPIO_RPI: return "sysfsgpio_rpi"; break;
```

### 2.4 Build

```bash
mkdir build && cd build
cmake ..
make -j4
```
