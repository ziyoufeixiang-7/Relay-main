# FPGA 自动化双增益校准系统 (`autocal.py`) 技术文档

> **版本**: 旧架构 (迁移参考用)
> **源文件**: `calibration/autocal.py`
> **用途**: 为新代码迁移提供旧校准逻辑的完整实现细节参考

---

## 目录

1. [系统概述](#1-系统概述)
2. [通道映射与硬件拓扑](#2-通道映射与硬件拓扑)
3. [FPGA 寄存器量化与高 16 位截断反算](#3-fpga-寄存器量化与高-16-位截断反算)
4. [校准数学模型 — 直流三点 WLS 拟合公式推导（核心）](#4-校准数学模型--直流三点-wls-拟合公式推导核心)
5. [交流增益独立校准模型](#5-交流增益独立校准模型)
6. [校准系数的应用公式 — ApplyAbsolute 与 ApplyDelta](#6-校准系数的应用公式--applyabsolute-与-applydelta)
7. [自动化校准流程时序](#7-自动化校准流程时序)
8. [万用表档位配置策略](#8-万用表档位配置策略)
9. [75mΩ 采样电阻电流折算逻辑](#9-75mω-采样电阻电流折算逻辑)
10. [硬件通信协议 — DDS 帧格式与 Layer0/Layer1](#10-硬件通信协议--dds-帧格式与-layer0layer1)
11. [校准后验证流程](#11-校准后验证流程)
12. [校准数据持久化格式](#12-校准数据持久化格式)
13. [迁移注意事项](#13-迁移注意事项)

---

## 1. 系统概述

`autocal.py` 是一个基于 Gradio WebUI 的 FPGA 自动化校准仪表盘，用于对 12 路电压/电流输出通道进行高精度增益与零偏校准。系统通过以下硬件闭环工作：

```
┌────────────────┐     串口 (230400 baud)     ┌──────────┐     功率放大器     ┌──────────────┐
│  校准 WebUI    │ ──────────────────────────→ │   FPGA   │ ─────────────────→ │  被校准通道  │
│  (Gradio)      │                             │  DDS 合成 │                    │  (V/I 输出)  │
└────────────────┘                             └──────────┘                    └──────┬───────┘
        ↑                                                                             │
        │                    VISA / USB-TMC                                           │ 物理连线
        │              ┌──────────────────┐                                           │
        └──────────────│  Rigol 万用表     │ ←─────────────────────────────────────────┘
                       │  (DM3068 等)      │          电压/电流测量
                       └──────────────────┘
```

**核心设计思想**：

- 通过 FPGA DDS 合成器输出**已知的原始目标信号**（不经过校准因子）
- 用高精度万用表测量功放实际输出的物理值
- 对比「期望值」与「实测值」，用最小二乘法拟合出增益 (factor) 和零偏 (bias)
- 校准后，实际使用时将这些系数反向应用到 FPGA 输入命令上，补偿系统增益和零偏误差

---

## 2. 通道映射与硬件拓扑

### 2.1 API 通道号 vs 物理通道号

系统使用两套通道编号：

| API 通道号 | 物理名称 | 物理通道号 | 类型 |
|:----------:|:--------:|:----------:|:----:|
| 0 | UA | 0 | 电压 |
| 1 | UB | 2 | 电压 |
| 2 | UC | 4 | 电压 |
| 3 | UX | 6 | 电压 |
| 4 | UY | 8 | 电压 |
| 5 | UZ | 10 | 电压 |
| 6 | IA | 1 | 电流 |
| 7 | IB | 3 | 电流 |
| 8 | IC | 5 | 电流 |
| 9 | IX | 7 | 电流 |
| 10 | IY | 9 | 电流 |
| 11 | IZ | 11 | 电流 |

映射关系定义在 `HWConfig._CH_MAPPING`：

```python
_CH_MAPPING = (0, 2, 4, 6, 8, 10, 1, 3, 5, 7, 9, 11, 12, 13, 14, 15)
# 索引即 API 通道号，值为物理通道号
```

**设计逻辑**：物理硬件上偶数通道 (0,2,4,6,8,10) 为电压输出，奇数通道 (1,3,5,7,9,11) 为电流输出。但 API 层面按功能分组：0-5 为电压，6-11 为电流，通过映射表实现解耦。

### 2.2 电压/电流通道判定

代码中统一使用 `ch_api >= 6` 来判断是否为电流通道：

```python
is_cur = ch_api >= 6
```

电流通道使用 `HWConfig.CURRENT` 缩放因子，电压通道使用 `HWConfig.VOLTAGE`。

---

## 3. FPGA 寄存器量化与高 16 位截断反算

### 3.1 问题背景

FPGA 的 DDS 合成器使用 32 位寄存器存储幅值，但由于 DAC 精度限制和硬件 pipeline 设计，实际**只使用高 16 位** (即 bit[31:16])，低 16 位被截断丢弃。这意味着写入寄存器的值和实际生效的值之间存在量化误差。

**校准拟合时，如果使用理论目标值而非实际量化截断后的等效值来做回归，会引入系统性拟合偏差**。因此需要函数 `get_quantized_phys_target()` 来反算截断后的真实等效物理值。

### 3.2 反算流程

`get_quantized_phys_target(value, is_ac, is_cur)` 的计算步骤：

#### 直流 (is_ac=False)

```
输入目标值 value (物理值, V 或 A)
    │
    ▼
reg_val = round(value × scale)              ← 物理值 → 32 位寄存器值
    │
    ▼
high_16 = (reg_val >> 16) & 0xFFFF          ← 提取高 16 位 (无符号)
    │
    ▼
high_16_signed = I16(high_16)               ← 转为有符号 16 位整型
    │                                          if high_16 < 32768: 保持
    │                                          else: high_16 - 65536
    ▼
actual_reg = high_16_signed × 65536         ← 还原为实际生效的 32 位寄存器值
    │
    ▼
actual_phys = actual_reg / scale            ← 寄存器值 → 实际等效物理值
```

**数学表达**：

$$x_{actual} = \frac{\text{I16}\!\left(\left\lfloor \frac{x_{target} \cdot S}{2^{16}} \right\rceil\right) \cdot 2^{16}}{S}$$

其中 $S$ 为缩放因子 (`HWConfig.VOLTAGE` 或 `HWConfig.CURRENT`)，$\lfloor \cdot \rceil$ 表示四舍五入取整。

#### 交流 (is_ac=True)

交流输出使用峰值进入寄存器，因此多一步 RMS ↔ 峰值 转换：

```
输入 RMS value
    │
    ▼
peak_val = value × √2                      ← RMS → 峰值
    │
    ▼
(同直流截断流程) → actual_peak
    │
    ▼
actual_rms = actual_peak / √2              ← 峰值 → 回到 RMS
```

### 3.3 缩放因子

```python
MAX_VOLTAGE = 200          # 电压通道最大量程 200V
MAX_CURRENT = 60           # 电流通道最大量程 60A
VOLTAGE = 0x7FFFFFFF / 200 # = 10,737,418.235 (每伏特对应的寄存器步长)
CURRENT = 0x7FFFFFFF / 60  # = 35,791,394.117 (每安培对应的寄存器步长)
```

---

## 4. 校准数学模型 — 直流三点 WLS 拟合公式推导（核心）

### 4.1 物理模型假设

假设 FPGA-功放系统是一个线性通道，对于直流输出存在如下关系：

$$y_{measured} = G_{dc} \cdot x_{target} + O_{dc}$$

其中：
- $x_{target}$ — FPGA 写入的目标物理值（经过高 16 位截断反算后的等效值）
- $y_{measured}$ — 万用表实测的功放输出物理值
- $G_{dc}$ — 直流系统增益（含 DAC、功放、变压器等全链路增益）
- $O_{dc}$ — 系统直流零偏（含运放失调、DAC 零点偏移等）

### 4.2 三点测量策略

系统在三个直流工作点上采样：

| 测量点 | 电压通道目标值 | 电流通道目标值 | 变量名 |
|:------:|:-------------:|:-------------:|:------:|
| 正向满量程 | +100V | +0.5A | `measured_p100` |
| 反向满量程 | -100V | -0.5A | `measured_n100` |
| 零点 | 0V | 0A | `measured_zero` |

每个测量点都经过长时间连续采样求平均以抑制低频抖动与温漂，同时记录采样方差。

**目标值修正**：实际拟合时，$x_1, x_2, x_3$ 并非直接使用 100V / -100V / 0V，而是先通过 `get_quantized_phys_target()` 计算 FPGA 高 16 位截断后的真实等效物理值：

```python
target_p = get_quantized_phys_target(100.0, False, is_cur)    # x₁
target_n = get_quantized_phys_target(-100.0, False, is_cur)   # x₂
target_z = get_quantized_phys_target(0.0, False, is_cur)      # x₃
```

### 4.3 加权最小二乘法 (WLS) 拟合推导

#### 为什么使用 WLS 而非 OLS？

三个测量点的采样方差不同（例如零点方差远小于满量程方差），普通最小二乘法 (OLS) 对所有点赋予等权重，而 WLS 可以让方差小（即置信度高）的点在拟合中贡献更大权重。

#### 权重定义

每个测量点的权重取方差的倒数：

$$w_i = \frac{1}{\sigma_i^2 + \epsilon}, \quad \epsilon = 10^{-12} \text{ (防止除零稳定项)}$$

其中 $\sigma_i^2$ 是第 $i$ 个测量点的采样方差 (`variance_p100`, `variance_n100`, `variance_zero`)。

#### WLS 正规方程推导

对于线性模型 $y = G \cdot x + O$，加权误差平方和为：

$$Q(G, O) = \sum_{i=1}^{3} w_i \left(y_i - G x_i - O\right)^2$$

对 $G$ 和 $O$ 分别求偏导并令其为零：

$$\frac{\partial Q}{\partial O} = -2 \sum_{i=1}^{3} w_i(y_i - G x_i - O) = 0$$

$$\frac{\partial Q}{\partial G} = -2 \sum_{i=1}^{3} w_i x_i (y_i - G x_i - O) = 0$$

化简得到正规方程组：

$$\begin{cases} W \cdot O + S_{wx} \cdot G = S_{wy} \\ S_{wx} \cdot O + S_{wxx} \cdot G = S_{wxy} \end{cases}$$

其中定义加权累计和：

| 符号 | 公式 | 代码变量 |
|:----:|:----:|:--------:|
| $W$ | $\sum w_i$ | `sum_w` |
| $S_{wx}$ | $\sum w_i x_i$ | `sum_wx` |
| $S_{wy}$ | $\sum w_i y_i$ | `sum_wy` |
| $S_{wxx}$ | $\sum w_i x_i^2$ | `sum_wxx` |
| $S_{wxy}$ | $\sum w_i x_i y_i$ | `sum_wxy` |

#### 求解 Cramer 法则

行列式：

$$D = W \cdot S_{wxx} - S_{wx}^2$$

若 $|D| > 10^{-12}$，则：

$$G_{dc} = \frac{W \cdot S_{wxy} - S_{wx} \cdot S_{wy}}{D}$$

$$O_{dc} = \frac{S_{wxx} \cdot S_{wy} - S_{wx} \cdot S_{wxy}}{D}$$

若 $|D| < 10^{-12}$（三个目标点近乎共线导致行列式退化），退回到简单 OLS 两点法：

$$G_{dc} = \frac{y_1 - y_2}{x_1 - x_2}, \quad O_{dc} = y_3 - x_3$$

### 4.4 校准系数推导

拟合得到 $G_{dc}$ 和 $O_{dc}$ 后，需要反转关系以得到**校准补偿系数**。

**目标**：给定用户期望的物理输出值 $v_{desired}$，计算需要写入 FPGA 的命令值 $v_{command}$，使得功放实际输出 $y = v_{desired}$。

由物理模型：

$$v_{desired} = G_{dc} \cdot v_{command} + O_{dc}$$

解出：

$$v_{command} = \frac{v_{desired} - O_{dc}}{G_{dc}}$$

定义校准系数：

$$\text{factor\_dc} = -\frac{1}{G_{dc}}$$

$$\text{bias} = O_{dc} \cdot \text{factor\_dc} = -\frac{O_{dc}}{G_{dc}}$$

则 FPGA 命令值为：

$$v_{command} = \text{bias} - v_{desired} \cdot \text{factor\_dc}$$

**注意负号**：`factor_dc` 的定义中包含一个负号，这是因为旧代码中 `ApplyAbsolute` 的公式采用了 `bias - value * factor` 的形式（即 $v_{command} = \text{bias} - v_{desired} \times \text{factor\_dc}$），将减号嵌入了 factor 的定义中。

### 4.5 验证：代入校准前后对比

假设测得 $G_{dc} = -1.05$, $O_{dc} = 0.02V$：

- $\text{factor\_dc} = -1/(-1.05) = 0.95238...$
- $\text{bias} = 0.02 \times 0.95238 = 0.01905...$

要输出 100V：

$$v_{command} = 0.01905 - 100 \times 0.95238 = -95.219V$$

代入物理模型验证：

$$y = (-1.05) \times (-95.219) + 0.02 = 99.98V + 0.02V = 100.0V \quad \checkmark$$

---

## 5. 交流增益独立校准模型

### 5.1 为什么交流需要独立校准？

直流和交流信号经过功放后的增益通常不同，原因包括：
- 变压器在 50Hz 下的传输比与直流截止特性不同
- 功放的频响曲线在 DC 和 50Hz 处增益不一致
- DDS 合成器 Layer0 (DC) 和 Layer1 (AC) 的数据通路可能存在差异

### 5.2 交流校准测量

交流校准只测一个点（50Hz 正弦波的 RMS 值）：

| 通道类型 | 测量点 | 万用表测量模式 |
|:--------:|:------:|:-------------:|
| 电压 | 57.735V AC RMS (即 100V/√3) | AC 电压 100V 量程 |
| 电流 | 0.5A AC RMS | AC 电流 / Shunt 电压 |

> **注意**：实际代码中电压通道 AC 测试值为 57.735V，而非 100V。目标值同样需要经过 `get_quantized_phys_target()` 截断反算。

### 5.3 交流增益计算

$$G_{ac} = \frac{y_{measured,ac}}{x_{target,ac}}$$

其中 $x_{target,ac}$ 是经过 FPGA 高 16 位截断反算后的 RMS 等效值。

交流增益需要继承直流的极性（反相标志），以确保相位一致性：

```python
G_unified_ac = math.copysign(G_ac, G_dc)   # 取 G_ac 的绝对值，符号跟随 G_dc
factor_ac = -1.0 / G_unified_ac
```

### 5.4 交流校准系数的应用

交流信号的校准**不含零偏修正**（交流信号本身是零均值的），仅进行增益补偿：

$$v_{command,ac} = -v_{desired,ac} \cdot \text{factor\_ac}$$

对应代码中 `ApplyDelta` (l_idx=1)：

```python
def ApplyDelta(self, ch_idx, value, l_idx=1):
    factor = calib_data[ch_str].get("factor_ac", ...)
    return -value * factor
```

---

## 6. 校准系数的应用公式 — ApplyAbsolute 与 ApplyDelta

### 6.1 ApplyAbsolute — 直流绝对值校准

用于 Layer0 (直流分量)，同时补偿增益和零偏：

$$v_{command} = \text{bias} - v_{desired} \times \text{factor\_dc}$$

```python
def ApplyAbsolute(self, ch_idx, value):
    factor = calib_data[ch_str].get("factor_dc", ...)
    bias   = calib_data[ch_str].get("bias", 0.0)
    return bias - value * factor
```

### 6.2 ApplyDelta — 增量/交流校准

用于 Layer1 (交流分量) 和步进增量（如扫频），仅补偿增益不含零偏：

$$v_{command,\delta} = -\Delta v \times \text{factor}$$

- 当 `l_idx=0` 时使用 `factor_dc`（直流步进增量）
- 当 `l_idx=1` 时使用 `factor_ac`（交流幅值或交流步进）

```python
def ApplyDelta(self, ch_idx, value, l_idx=1):
    if l_idx == 0:
        factor = calib_data[ch_str].get("factor_dc", ...)
    else:
        factor = calib_data[ch_str].get("factor_ac", ...)
    return -value * factor
```

### 6.3 为什么增量不需要零偏？

零偏 (bias) 是一个常数偏移，只在绝对值定位时需要。当计算增量 $\Delta v$ 时，零偏在差分中被消除：

$$v_{cmd}(v_1) - v_{cmd}(v_2) = (\text{bias} - v_1 \cdot f) - (\text{bias} - v_2 \cdot f) = -(v_1 - v_2) \cdot f$$

---

## 7. 自动化校准流程时序

### 7.1 一键全自动校准执行顺序

`run_auto_calibration_flow()` 按以下固定顺序执行四步测量：

```
步骤 1/4: 交流校准测量 (step_index=3)
    │  ← 先测交流，因为交流测量不依赖直流零偏结果
    ▼
步骤 2/4: 零偏直流测量 (step_index=2, 0V/0A)
    │  ← 零点测量最敏感，放在中间以减少热漂移影响
    ▼
步骤 3/4: 正向直流测量 (step_index=0, +100V/+0.5A)
    │
    ▼
步骤 4/4: 反向直流测量 (step_index=1, -100V/-0.5A)
    │
    ▼
WLS 拟合计算 + JSON 保存
```

### 7.2 单步测量流程 (`output_and_auto_sample`)

每一步测量的内部时序：

```
1. FPGA 系统复位 + 全通道清零     ← 确保功放处于安全静默状态
   │
2. 万用表预配置                    ← 根据信号类型提前切换档位和量程
   │  (等待 0.5s 让仪表模式切换稳定)
   │
3. FPGA DDS 输出原始信号           ← set_fpga_output_raw(), 不经过校准因子
   │
4. 功放稳定等待                    ← 可配置 1~60 秒 (默认 5s)
   │  (功放从零输出跳变到目标值后存在过冲与稳态振荡)
   │
5. 万用表连续采样                  ← 可配置 1~60 秒 (默认 5s)
   │  (独立采样线程, 约 150ms/次)
   │  (计算全周期均值以过滤 50Hz 残余纹波和低频抖动)
   │
6. 停止采样, 计算均值与方差
   │
7. FPGA 复位 + 全通道归零          ← 测完立即关闭输出，保护设备
```

### 7.3 采样线程机制

采样使用独立的 daemon 线程 `multimeter_sampling_loop()`：

- **采样间隔**: 每次读取后 `sleep(0.15)` (约 150ms)
- **线程安全**: 使用 `threading.Lock` 保护 `sample_list` 和 `sample_times`
- **均值计算**: 停止采样后对全部采样点取算术平均
- **方差计算**: $\sigma^2 = \frac{1}{N}\sum_{i=1}^{N}(x_i - \bar{x})^2$（总体方差，非样本方差）

---

## 8. 万用表档位配置策略

`configure_multimeter(is_ac, is_cur, target_val)` 根据测量场景自动选择最优档位：

### 8.1 电压通道

| 测量点 | AC/DC | SCPI 命令 | 量程 | 说明 |
|:------:|:-----:|:---------:|:----:|:----:|
| ±100V DC | DC | `:CONF:VOLT:DC 100` | 100V | 手动设定避免自动挡误判 |
| 0V DC | DC | `:CONF:VOLT:DC 0.001` | 1mV (MIN) | 零点测量使用最小量程提升分辨率 |
| 57.735V AC | AC | `:CONF:VOLT:AC 100` | 100V | 防止自动挡卡在低量程导致超量程 |

### 8.2 电流通道 — Shunt 模式 (采样电阻)

电流通道实际测量的是 75mΩ 采样电阻两端的毫伏级电压：

| 测量点 | AC/DC | SCPI 命令 | 量程 | 说明 |
|:------:|:-----:|:---------:|:----:|:----:|
| ±0.5A DC → 37.5mV | DC | `:CONF:VOLT:DC 0.1` | 100mV | Shunt 电压恒定使用 100mV 挡 |
| 0A DC → 0mV | DC | `:CONF:VOLT:DC 0.1` | 100mV | 同上，无更小的 mV 挡 |
| 0.5A AC → 37.5mV | AC | `:CONF:VOLT:AC 0.1` | 100mV | AC 电压挡测 Shunt |

### 8.3 电流通道 — 直接测量模式

| 测量点 | AC/DC | SCPI 命令 | 量程 | 说明 |
|:------:|:-----:|:---------:|:----:|:----:|
| ±0.5A DC | DC | `:CONF:CURR:DC 1` | 1A | 0.5A 在 1A 挡测量 |
| 0A DC | DC | `:CONF:CURR:DC 0.1` | 100mA | 零输出用 100mA 挡提升分辨率 |
| 0.5A AC | AC | `:CONF:CURR:AC 1` | 1A | AC 电流挡 |

### 8.4 NPLC 设置

直流测量统一设置 NPLC=10（积分时间 = 10 个工频周期 = 200ms @50Hz），以获得最高精度和最大工频噪声抑制。交流测量不需要手动设 NPLC。

---

## 9. 75mΩ 采样电阻电流折算逻辑

### 9.1 原理

电流通道的 Shunt 测量模式下，万用表测的是采样电阻两端的电压降，需要折算为电流：

$$I_{physical} = \frac{V_{DMM}}{R_{shunt}} = \frac{V_{DMM}}{0.075 \Omega}$$

### 9.2 代码实现位置

折算发生在两个地方：

**采样线程中** (`multimeter_sampling_loop`)：
```python
if global_state.is_current and curr_meas_mode == 'shunt':
    phys_val = raw_v / 0.075
```

**验证循环中** (`run_calibrated_verification_loop`)：
```python
if is_cur and curr_meas_mode == 'shunt':
    phys_val = raw_v / 0.075
```

### 9.3 两种电流测量模式的区别

| 特性 | Shunt 模式 (`shunt`) | 直接测量模式 (`direct`) |
|:----:|:-------------------:|:---------------------:|
| 万用表测量的物理量 | 电压 (mV) | 电流 (A) |
| 需要折算 | 是 (÷0.075) | 否 |
| 精度 | 较高 (mV 挡分辨率高) | 取决于电流挡精度 |
| 适用场景 | 有外部采样电阻的功放 | 万用表串入回路直接测 |

---

## 10. 硬件通信协议 — DDS 帧格式与 Layer0/Layer1

### 10.1 帧类型

系统使用两种帧类型通过串口与 FPGA 通信：

**系统帧** (4 字节，帧头 `0x5A`)：

```
[0x5A] [CmdCode] [Param_U8] [XOR校验]
```

常用系统命令：
- `SYS_RESET (0x04)`: 硬件全局复位
- `SYS_START (0x00)`: 启动 DDS 波形生成
- `SYS_UPDATE (0x05)`: 触发 Ping-Pong 缓冲区翻转（使 Shadow 生效）

**参数帧** (12 字节，帧头 `0xA5`)：

```
[0xA5] [CmdCode] [RegIndex] [ChMask_H] [ChMask_L] [P1_H] [P1_L] [P2_3] [P2_2] [P2_1] [P2_0] [XOR校验]
```

- `RegIndex`: 寄存器索引，255 = Layer0，0 = Layer1
- `ChMask`: 16 位通道掩码，bit N 表示物理通道 N
- `P1_u32` 高 16 位: 幅值的高 16 位
- `P2_u32`: Layer0 时为频率寄存器值，Layer1 时为相位寄存器值

### 10.2 Layer0 / Layer1 的物理含义

FPGA DDS 输出 = Layer0 (直流偏置) + Layer1 (交流正弦波)

| Layer | 含义 | RegIndex | P1 参数 | P2 参数 |
|:-----:|:----:|:--------:|:-------:|:-------:|
| Layer 0 | 直流分量 (DC offset) | 255 | 直流幅值 | 频率 |
| Layer 1 | 交流分量 (AC sine) | 0 | 峰值幅值 | 相位 |

### 10.3 校准期间的 DDS 控制示例

**输出 +100V 直流** (API 通道 0 → 物理通道 0)：
```python
# Layer0: DC = +100V, Freq = 50Hz
frame_l0 = BuildParamFrame(DDS_WR_SHADOW, 255, 0x0001, amp_100V_reg, freq_50Hz_reg)
# Layer1: AC = 0V, Phase = 0°
frame_l1 = BuildParamFrame(DDS_WR_SHADOW, 0, 0x0001, 0, 0)
# 触发生效
FRAME_SYS_UPDATE
```

**输出 57.735V AC** (API 通道 0 → 物理通道 0)：
```python
# Layer0: DC = 0V (无直流偏置)
frame_l0 = BuildParamFrame(DDS_WR_SHADOW, 255, 0x0001, 0, freq_50Hz_reg)
# Layer1: AC 峰值 = 57.735 × √2 = 81.65V
frame_l1 = BuildParamFrame(DDS_WR_SHADOW, 0, 0x0001, amp_peak_reg, phase_0_reg)
# 触发生效
FRAME_SYS_UPDATE
```

### 10.4 Ping-Pong 双缓冲

`DDS_WR_SHADOW (0x10)` 写入 Shadow 缓冲区（不立即生效），`SYS_UPDATE` 触发 Ping-Pong 翻转使 Shadow 变为 Active。这保证了多通道参数的原子性更新——所有通道在同一个 Update 脉冲后同时生效。

---

## 11. 校准后验证流程

### 11.1 验证原理

校准完成后，用户可以在「校准效果手动校验」面板中输入任意目标物理值，系统会：

1. 读取最新的 `calibration.json` 校准系数
2. 通过 `ApplyAbsolute` / `ApplyDelta` 计算校准后的 FPGA 命令值
3. 发送命令让功放输出
4. 连续读取万用表测值
5. 计算相对误差

### 11.2 实际等效物理值反算

验证时，需要反算出经过校准和 FPGA 高 16 位截断后的**真正实际等效物理输出值** `actual_target`，以此作为误差计算的分母。

**直流反算**：

```python
v_calib = ApplyAbsolute(ch_api, value)          # bias - value * factor_dc
amp_reg = ConvertAmpToReg(v_calib, is_cur)      # 转寄存器值
# 高 16 位截断
high_16_signed = I16((amp_reg >> 16) & 0xFFFF)
actual_reg = high_16_signed * 65536
v_calib_actual = actual_reg / scale             # 截断后的实际校准命令值
# 反解出实际物理输出：v_calib_actual = bias - actual_target * factor_dc
actual_target = (bias - v_calib_actual) / factor_dc
```

**交流反算**：

```python
peak_val = value * √2
calibrated_peak = ApplyDelta(ch_api, peak_val, l_idx=1)   # -peak * factor_ac
amp_reg = ConvertAmpToReg(calibrated_peak, is_cur)
# 高 16 位截断
calib_peak_actual = I16截断(amp_reg) / scale
# 反解：calib_peak_actual = -peak_actual * factor_ac
peak_actual = -calib_peak_actual / factor_ac
actual_target = |peak_actual| / √2
```

### 11.3 相对误差计算

$$\text{Rel. Error} = \frac{y_{DMM} - x_{actual}}{x_{actual}} \times 100\%$$

当 $|x_{actual}| < 10^{-6}$ 时（零点附近），改用绝对误差显示：

$$\text{Abs. Error} = y_{DMM} - x_{actual}$$

---

## 12. 校准数据持久化格式

### 12.1 旧格式 (当前代码使用)

文件位置: `config/calibration.json`

```json
{
    "0": {
        "factor_dc": 0.9558393,
        "factor_ac": 0.95609362,
        "bias": 0.01904483
    },
    "6": {
        "factor_dc": 1.21536231,
        "factor_ac": 1.21407421,
        "bias": 0.00642705
    }
}
```

- Key: API 通道号 (字符串)
- `factor_dc`: 直流增益校准系数 ($-1/G_{dc}$)
- `factor_ac`: 交流增益校准系数 ($-1/G_{unified,ac}$)
- `bias`: 直流零偏校准系数 ($-O_{dc}/G_{dc}$)

### 12.2 新代码格式对比 (迁移参考)

新代码 (`newrelay/.../calibration.json`) 使用数组格式：

```json
{
    "0": [0.01904483, -0.9558393, 0.95609362]
}
```

数组元素顺序: `[bias, -factor_dc, factor_ac]`（注意 factor_dc 前面多了一个负号）

> **迁移要点**: 新旧格式的 factor_dc 符号约定不同，迁移时需注意 `ApplyAbsolute` 和 `ApplyDelta` 公式中的正负号是否需要调整。

### 12.3 典型校准系数范围

从实际数据观察：

| 通道类型 | factor_dc 典型范围 | factor_ac 典型范围 | bias 典型范围 |
|:--------:|:-----------------:|:-----------------:|:------------:|
| 电压 (0-5) | 0.953 ~ 0.957 | 0.954 ~ 0.957 | -0.048 ~ +0.046 V |
| 电流 (6-11) | 1.20 ~ 1.30 | 1.20 ~ 1.60 | -0.033 ~ +0.006 A |

电流通道的 factor 大于 1 是因为功放在电流模式下增益小于 1（即系统增益 $|G_{dc}| < 1$）。

---

## 13. 迁移注意事项

### 13.1 核心数学必须保留的部分

1. **高 16 位截断反算** (`get_quantized_phys_target`): 若新代码的 FPGA 仍使用高 16 位截断，则此函数必须保留，否则拟合会有系统性偏差。
2. **WLS 加权拟合**: 方差倒数权重机制对于提高校准精度有意义，建议保留。
3. **AC/DC 独立因子**: `factor_dc` 和 `factor_ac` 分开校准是必要的，不能合并为单一 factor。
4. **极性继承**: 交流 factor 的符号必须从直流 factor 继承 (`copysign`)，否则可能出现反相输出。

### 13.2 可改进的部分

1. **采样方差使用总体方差**而非样本方差 ($N$ vs $N-1$)，当采样点数较大时差异可忽略，但严格数学上应使用样本方差。
2. **零点测量使用固定量程** (1mV 挡)，若新硬件零偏更大可能需要调整。
3. **交流只测一个点**（单点校准），无法检测 AC 增益的非线性；若需要更高精度，可增加多个 AC 测试点。
4. **全局状态管理**使用了全局单例 `CalibrationState`，迁移时建议考虑更模块化的设计。

### 13.3 关键公式速查表

| 场景 | 公式 |
|:----:|:----:|
| 直流校准输出 | $v_{cmd} = \text{bias} - v_{desired} \times \text{factor\_dc}$ |
| 交流校准输出 | $v_{cmd,ac} = -v_{desired,ac} \times \text{factor\_ac}$ |
| 直流增益拟合 | $G_{dc} = \frac{W \cdot S_{wxy} - S_{wx} \cdot S_{wy}}{W \cdot S_{wxx} - S_{wx}^2}$ |
| factor_dc | $\text{factor\_dc} = -1/G_{dc}$ |
| factor_ac | $\text{factor\_ac} = -1/\text{copysign}(G_{ac}, G_{dc})$ |
| bias | $\text{bias} = O_{dc} \times \text{factor\_dc}$ |
| 电流折算 (Shunt) | $I = V_{DMM} / 0.075$ |
| 截断反算 (DC) | $x_{actual} = \text{I16}(\lfloor x \cdot S / 2^{16} \rceil) \cdot 2^{16} / S$ |

---

*文档结束。本文档基于 `calibration/autocal.py` (1130 行) 源码分析生成，供新代码迁移参考。*
