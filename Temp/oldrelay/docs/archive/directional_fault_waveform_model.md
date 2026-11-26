# 方向性故障波形计算模型

> **适用模块**: InvTimeItHdl (反时限 I-t)  
> **核心模型**: 恒定故障电压 + 受控故障电流 + 负荷电流叠加

---

## 0. 符号定义

| 符号 | 含义 | 来源 |
|------|------|------|
| $U_n$ | 额定相电压 (如 57.735V) | `vBeforeFault` |
| $V_f$ | 故障电压 (接地=相电压, 相间=线电压) | `vFault` |
| $\theta_L$ | 线路阻抗角 | `lineAngle` |
| $I_{pre}$ | 故障前负荷电流幅值 | `iBeforeFault` |
| $\phi$ | 负荷功率因数角 | `preLoadAngle` (默认 0°) |
| $I_{set}$ | 设定电流值 (测试点 value) | testPoint.value |
| $I_\Delta$ | 纯故障电流幅值 = $I_{set} \times scale$ | 由 iDefinition + faultType 决定 |

---

## 1. 故障前状态 (三相对称负荷)

### 电压 (方向性启用时)

$$U_a = U_n \angle 0°, \quad U_b = U_n \angle -120°, \quad U_c = U_n \angle 120°$$

### 电流

$$I_{pre\_a} = I_{pre} \angle (-\phi)$$
$$I_{pre\_b} = I_{pre} \angle (-120° - \phi)$$
$$I_{pre\_c} = I_{pre} \angle (120° - \phi)$$

---

## 2. 故障态电压

### 2.1 接地故障 (A/B/C_GROUND)

故障相电压跌落到 $V_f$，与故障前**同相位**。非故障相不变。

| 故障类型 | Ua | Ub | Uc |
|---------|-----|-----|-----|
| A_GROUND | $V_f \angle 0°$ | $U_n \angle -120°$ | $U_n \angle 120°$ |
| B_GROUND | $U_n \angle 0°$ | $V_f \angle -120°$ | $U_n \angle 120°$ |
| C_GROUND | $U_n \angle 0°$ | $U_n \angle -120°$ | $V_f \angle 120°$ |

### 2.2 相间故障 (AB/BC/CA) — 线电压钳位

`vFault` 为**线电压**，钳位在故障前线电压同相位。

| 故障 | 线电压 $U_{xy}$ | 相位 | 非故障相 Z |
|------|----------------|------|-----------|
| AB | $V_f \angle 30°$ | 30° | C: $U_n \angle 120°$ |
| BC | $V_f \angle -90°$ | -90° | A: $U_n \angle 0°$ |
| CA | $V_f \angle 150°$ | 150° | B: $U_n \angle -120°$ |

求解故障相电压：

$$U_x = \frac{U_{xy} + (-U_z)}{2}, \quad U_y = \frac{(-U_z) - U_{xy}}{2}$$

其中 $-U_z = U_n \angle (\theta_z + 180°)$

### 2.3 三相短路 (ABC_PHASE)

$$U_a = V_f \angle 0°, \quad U_b = V_f \angle -120°, \quad U_c = V_f \angle 120°$$

---

## 3. 故障态电流 — 叠加定理

### 总公式

$$\dot{I}_{total} = \dot{I}_{pre} + \dot{I}_{\Delta}$$

其中 $\dot{I}_\Delta$ 由 `iDefinition` + `faultType` + `lineAngle` 决定。

---

## 4. iDefinition = FAULT_CURRENT ($I_f$)

设定值 $I_{set}$ 直接等于故障相电流幅值。Scale = 1.0。

### 4.1 接地故障

| 故障 | $I_{\Delta a}$ | $I_{\Delta b}$ | $I_{\Delta c}$ |
|------|-----------|-----------|-----------|
| A_GROUND | $I_{set} \angle (-\theta_L)$ | 0 | 0 |
| B_GROUND | 0 | $I_{set} \angle (-120° - \theta_L)$ | 0 |
| C_GROUND | 0 | 0 | $I_{set} \angle (120° - \theta_L)$ |

### 4.2 相间故障

| 故障 | $I_{\Delta a}$ | $I_{\Delta b}$ | $I_{\Delta c}$ |
|------|-----------|-----------|-----------|
| AB | $I_{set} \angle (30° - \theta_L)$ | $I_{set} \angle (210° - \theta_L)$ | 0 |
| BC | 0 | $I_{set} \angle (-90° - \theta_L)$ | $I_{set} \angle (90° - \theta_L)$ |
| CA | $I_{set} \angle (330° - \theta_L)$ | 0 | $I_{set} \angle (150° - \theta_L)$ |

### 4.3 三相短路

$$I_{\Delta a} = I_{set} \angle (-\theta_L)$$
$$I_{\Delta b} = I_{set} \angle (-120° - \theta_L)$$
$$I_{\Delta c} = I_{set} \angle (120° - \theta_L)$$

---

## 5. iDefinition = NEGATIVE_SEQUENCE ($I_2$)

设定值 $I_{set}$ 等于负序电流幅值 $|I_2|$。需要根据对称分量法反算相电流。

### 缩放系数

| negativeSequenceChannel | 接地故障 | 相间故障 | ABC (纯负序) |
|------------------------|---------|---------|------------|
| `I2` (输入 = $I_2$) | ×3 | ×√3 | ×1 (特殊) |
| `3I2` (输入 = $3I_2$) | ×1 | ×(1/√3) | ×(1/3) (特殊) |

### 5.1 接地故障 (以 I2 为例, scale=3)

单相接地: $I_1 = I_2 = I_0$, $I_f = 3I_2$

| 故障 | $I_{\Delta a}$ | $I_{\Delta b}$ | $I_{\Delta c}$ |
|------|-----------|-----------|-----------|
| A_GROUND | $3 I_{set} \angle (-\theta_L)$ | 0 | 0 |
| B_GROUND | 0 | $3 I_{set} \angle (-120° - \theta_L)$ | 0 |
| C_GROUND | 0 | 0 | $3 I_{set} \angle (120° - \theta_L)$ |

### 5.2 相间故障 (以 I2 为例, scale=√3)

相间短路无零序, $|I_2| = \frac{1}{\sqrt{3}}|I_a|$, 故 $I_a = \sqrt{3} I_2$

| 故障 | $I_{\Delta a}$ | $I_{\Delta b}$ | $I_{\Delta c}$ |
|------|-----------|-----------|-----------|
| AB | $\sqrt{3} I_{set} \angle (30° - \theta_L)$ | $\sqrt{3} I_{set} \angle (210° - \theta_L)$ | 0 |
| BC | 0 | $\sqrt{3} I_{set} \angle (-90° - \theta_L)$ | $\sqrt{3} I_{set} \angle (90° - \theta_L)$ |
| CA | $\sqrt{3} I_{set} \angle (330° - \theta_L)$ | 0 | $\sqrt{3} I_{set} \angle (150° - \theta_L)$ |

### 5.3 三相对称 (纯负序, ABC_PHASE)

三相幅值均为 $I_{set}$, **相序反转 (A-C-B)**:

$$I_{\Delta a} = I_{set} \angle (-\theta_L)$$
$$I_{\Delta b} = I_{set} \angle (120° - \theta_L) \quad \text{(注意: +120°, 非 -120°)}$$
$$I_{\Delta c} = I_{set} \angle (-120° - \theta_L) \quad \text{(注意: -120°, 非 +120°)}$$

---

## 6. iDefinition = ZERO_SEQUENCE ($I_0$)

设定值 $I_{set}$ 等于零序电流幅值 $|I_0|$。

> 相间故障 (AB/BC/CA) 不存在零序分量, 此组合应被屏蔽或输出 0。

### 缩放系数

| 条件 | 接地故障 | ABC (纯零序) |
|------|---------|------------|
| 输入 = $I_0$ | ×3 | ×1 (特殊) |

### 6.1 接地故障 (scale=3)

单相接地: $I_f = 3I_0$

| 故障 | $I_{\Delta a}$ | $I_{\Delta b}$ | $I_{\Delta c}$ |
|------|-----------|-----------|-----------|
| A_GROUND | $3 I_{set} \angle (-\theta_L)$ | 0 | 0 |
| B_GROUND | 0 | $3 I_{set} \angle (-120° - \theta_L)$ | 0 |
| C_GROUND | 0 | 0 | $3 I_{set} \angle (120° - \theta_L)$ |

### 6.2 三相对称 (纯零序, ABC_PHASE)

三相**大小相等、相位完全相同** (同相):

$$I_{\Delta a} = I_{\Delta b} = I_{\Delta c} = I_{set} \angle (-\theta_L)$$

---

## 7. iDefinition = SINGLE_PATH (单路)

所有活跃通道并联输出, 同相 0°, 不受 lineAngle 影响:

| 故障 | 活跃通道 |
|------|---------|
| A_GROUND | Ia = $I_{set}$ ∠0° |
| AB_PHASE | Ia = Ib = $I_{set}$ ∠0° |
| ABC_PHASE | Ia = Ib = Ic = $I_{set}$ ∠0° |

> SINGLE_PATH 不参与负荷电流叠加和 CT 极性翻转。

---

## 8. CT 极性逻辑

| ctPolarity | 处理 |
|-----------|------|
| `TOWARD_COMPONENT` (指向元件) | 不变 |
| `TOWARD_BUSBAR` (指向母线) | 所有电流相位 +180° |

---

## 9. 统一算法流程

```
1. 确定 scale_factor:
   - FAULT_CURRENT: 1.0
   - NEGATIVE_SEQUENCE (I2): ground=3, inter-phase=√3
   - NEGATIVE_SEQUENCE (3I2): ground=1, inter-phase=1/√3
   - ZERO_SEQUENCE: ground=3

2. 计算 I_delta = I_set × scale_factor

3. 查 DELTA_ANGLES 表获取各相的 (has_delta, angle):
   - 标准模式: 7种故障类型 × 3相
   - ABC 特殊模式: 纯负序(ACB) / 纯零序(同相)

4. 矢量叠加: I_total = I_pre + I_delta (per phase)

5. CT 极性: TOWARD_BUSBAR → 全体 +180°

6. 输出 (mag, angle) → 寄存器值
```

---

## 10. 矢量运算辅助函数

```python
import math

def polar_to_rect(mag, angle_deg):
    rad = math.radians(angle_deg)
    return (mag * math.cos(rad), mag * math.sin(rad))

def rect_to_polar(re, im):
    mag = math.sqrt(re**2 + im**2)
    angle = math.degrees(math.atan2(im, re))
    return (mag, angle)

def vector_add(mag1, ang1, mag2, ang2):
    r1, i1 = polar_to_rect(mag1, ang1)
    r2, i2 = polar_to_rect(mag2, ang2)
    return rect_to_polar(r1 + r2, i1 + i2)
```

---

## 11. 数值验证

### AB 故障, lineAngle=70°, vFault=20V, vBefore=57.735V

**电压:**

| 通道 | 幅值 | 相位 |
|------|------|------|
| Ua | 30.55V | -40.89° |
| Ub | 30.55V | -79.11° |
| Uc | 57.735V | 120° |

**电流 (FAULT_CURRENT, I_set=10A, I_pre=0):**

| 通道 | 幅值 | 相位 |
|------|------|------|
| Ia | 10A | -40° |
| Ib | 10A | 140° |
| Ic | 0A | — |

**电流 (I2, I_set=5A, I_pre=0):** scale=√3

| 通道 | 幅值 | 相位 |
|------|------|------|
| Ia | 8.66A | -40° |
| Ib | 8.66A | 140° |
| Ic | 0A | — |

**电流 (FAULT_CURRENT, I_set=10A, I_pre=2A, φ=30°):** 叠加验证

| 通道 | $I_{pre}$ | $I_\Delta$ | $I_{total}$ |
|------|-----------|-----------|-------------|
| Ia | 2∠-30° | 10∠-40° | vector_add(2,-30, 10,-40) |
| Ib | 2∠-150° | 10∠140° | vector_add(2,-150, 10,140) |
| Ic | 2∠90° | 0 | 2∠90° |
