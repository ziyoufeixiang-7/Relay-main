# 线路保护故障量计算 — 完整数学推导

## 1. 系统定义与参考约定

### 1.1 正常态（故障前）

三相平衡额定电压（RMS），以 A 相为 0° 基准：

$$
\dot{V}_A = V \angle 0°, \quad
\dot{V}_B = V \angle -120°, \quad
\dot{V}_C = V \angle +120°
$$

三相平衡负荷电流（RMS，各相均等）：

$$
\dot{I}^{load}_p = I_L \angle (BASE_p + \varphi_L), \quad p \in \{A, B, C\}
$$

其中 $BASE_A=0°,\ BASE_B=-120°,\ BASE_C=+120°$；$I_L$ 为负荷电流幅值，$\varphi_L$ 为负荷角。  
平衡负荷的零序之和为零：$\dot{I}^{load}_A + \dot{I}^{load}_B + \dot{I}^{load}_C = 0$

---

## 2. 继电器测量方程

距离保护各元件的测量公式：

### 2.1 接地距离元件（单相接地、两相接地）

$$
\boxed{ Z_{meas} = \frac{\dot{U}_x}{\dot{I}_x + k_L \cdot 3\dot{I}_0} }
$$

其中：
- $\dot{I}_x$：故障相电流（总量，含负荷）
- $3\dot{I}_0 = \dot{I}_A + \dot{I}_B + \dot{I}_C$：残余电流（零序三倍值）
- $k_L$：零序补偿系数（复数）

### 2.2 相间距离元件（AB/BC/CA 相间，两相接地亦可用）

$$
\boxed{ Z_{meas} = \frac{\dot{U}_x - \dot{U}_y}{\dot{I}_x - \dot{I}_y} }
$$

### 2.3 三相元件（三相短路）

$$
\boxed{ Z_{meas} = \frac{\dot{U}_p}{\dot{I}_p} }, \quad p \in \{A, B, C\}
$$

---

## 3. 零序补偿系数 $k_L$ 的三种计算方法

### 方法 0：直接给定（复数幅值+角度）

$$
k_L = |k_L| \angle \theta_{kL}
$$

### 方法 1：分量法（kR + j·kX）

$$
k_R = \frac{R_0 - R_1}{3R_1}, \quad k_X = \frac{X_0 - X_1}{3X_1}, \quad k_L = k_R + j \cdot k_X
$$

> 注意：方法 1（kR/kX）与标准公式 $\frac{Z_0-Z_1}{3Z_1}$ 是**不同的两种补偿方案**，结果一般不同。

### 方法 2：阻抗比法

$$
k_L = \frac{\dot{Z}_0}{\dot{Z}_1}
$$

---

## 4. 故障量计算（IF 恒定模型）

**IF 恒定**：故障电流分量幅值固定为给定值 $i$（RMS）。

定义：
- $\dot{Z} = z \angle \varphi$：测试阻抗（继电器定值阻抗）
- $\dot{K} = k_L$：零序补偿（复数）
- $(1+\dot{K}) = (1+k_L)$，幅值 $|1+K|$，角度 $\arg(1+K)$

### 4.1 故障电流基准相量（Base Phasor，始终按"正向"计算）

故障电流基准相量使得电压恰好沿所在相轴方向（相位）推算。  
对故障相 $x$，接地回路的**基准故障电流**为：

$$
\boxed{ \dot{I}^{base}_{f,x} = i \angle \bigl(BASE_x - \varphi - \arg(1+K)\bigr) }
$$

**推导**：令接地元件测到 $Z_{meas} = z\angle\varphi$，则电压回路方程：

$$
\dot{U}_x = \dot{Z} \cdot \bigl( \dot{I}^{base}_{f,x} \cdot (1+K) + \dot{I}^{load}_x \bigr)
$$

需使 $\dot{U}_x$ 沿相 $x$ 的额定轴方向（即 $\angle BASE_x$）。当负荷电流为零时：

$$
\dot{U}_x = z \angle\varphi \cdot i \angle(BASE_x - \varphi - \arg(1+K)) \cdot |1+K|\angle\arg(1+K)
= z \cdot i \cdot |1+K| \angle BASE_x \quad \checkmark
$$

对相间元件（无 $K$ 项）：

$$
\boxed{ \dot{I}^{base}_{f} = i \angle \bigl(\arg(\dot{V}^{nom}_x - \dot{V}^{nom}_y) - \varphi\bigr) }
$$

---

## 5. 五种故障类型的完整公式

### 5.1 单相接地（ft = 0/1/2，故障相 $x \in \{A,B,C\}$）

**基准故障电流（正向）：**

$$
\dot{I}^{base}_{f,x} = i \angle \bigl(BASE_x - \varphi - \arg(1+K)\bigr)
$$

**电压（始终从正向基准计算，与方向无关）：**

$$
\boxed{ \dot{U}_x = \dot{Z} \cdot \bigl( \dot{I}^{base}_{f,x} \cdot (1+K) + \dot{I}^{load}_x \bigr) }
$$

$$
\dot{U}_{y \ne x} = \dot{V}^{nom}_y \quad \text{（非故障相维持额定）}
$$

**输出电流（方向依赖）：**

$$
\dot{I}^{out}_{f,x} =
\begin{cases}
+\dot{I}^{base}_{f,x} & \text{正向 (direction=0)} \\
-\dot{I}^{base}_{f,x} & \text{反向 (direction=1)}
\end{cases}
$$

$$
\dot{I}_x = \dot{I}^{out}_{f,x} + \dot{I}^{load}_x, \qquad
\dot{I}_{y \ne x} = \dot{I}^{load}_y
$$

**验证（无负荷）：**

| 方向 | 3I₀ | 继电器测量回路 $\dot{I}_x + K \cdot 3\dot{I}_0$ | $Z_{meas}$ |
|------|-----|------------------------------------------------|------------|
| 正向 | $+\dot{I}^{base}_{f,x}$ | $\dot{I}^{base}_{f,x}(1+K)$ | $z\angle\varphi$ ✓ |
| 反向 | $-\dot{I}^{base}_{f,x}$ | $-\dot{I}^{base}_{f,x}(1+K)$ | $z\angle(\varphi+180°)$ ✓ |

---

### 5.2 相间故障（ft = 3/4/5，故障相对 $x$-$y$，$y$ 不接地）

设 $\theta_{xy} = \arg(\dot{V}^{nom}_x - \dot{V}^{nom}_y)$（额定线电压方向角）。

**基准故障电流（正向，相间无 $K$）：**

$$
\dot{I}^{base}_f = i \angle (\theta_{xy} - \varphi)
$$

**差分循环电流（正向）：**

$$
\Delta \dot{I}_{fwd} = (\dot{I}^{base}_f + \dot{I}^{load}_x) - (-\dot{I}^{base}_f + \dot{I}^{load}_y)
= 2\dot{I}^{base}_f + \dot{I}^{load}_x - \dot{I}^{load}_y
$$

**电压（始终从正向基准计算）：**

$$
\dot{U}_{mid} = \tfrac{1}{2}(\dot{V}^{nom}_x + \dot{V}^{nom}_y)
$$

$$
\boxed{
\dot{U}_x = \dot{U}_{mid} + \frac{\dot{Z} \cdot \Delta\dot{I}_{fwd}}{2}, \quad
\dot{U}_y = \dot{U}_{mid} - \frac{\dot{Z} \cdot \Delta\dot{I}_{fwd}}{2}
}
$$

$$
\dot{U}_{z} = \dot{V}^{nom}_z \quad \text{（第三相额定）}
$$

**输出电流：**

$$
\dot{I}^{out}_f = \pm \dot{I}^{base}_f, \qquad
\dot{I}_x = \dot{I}^{out}_f + \dot{I}^{load}_x, \quad
\dot{I}_y = -\dot{I}^{out}_f + \dot{I}^{load}_y, \quad
\dot{I}_z = \dot{I}^{load}_z
$$

**验证（无负荷）：**

$$
Z_{meas} = \frac{\dot{U}_x - \dot{U}_y}{\dot{I}_x - \dot{I}_y}
= \frac{\dot{Z} \cdot 2\dot{I}^{base}_f}{\pm 2\dot{I}^{base}_f}
= \begin{cases} z\angle\varphi & \text{正向} \\ z\angle(\varphi+180°) & \text{反向} \end{cases} \checkmark
$$

---

### 5.3 两相接地（ft = 6/7/8，故障相对 $x$-$y$，均接地）

两相均经接地短路，各相独立使用接地元件测量公式，含 $k_L$。

**基准故障电流（各相独立，正向）：**

$$
\dot{I}^{base}_{f,x} = i \angle (BASE_x - \varphi - \arg(1+K))
$$
$$
\dot{I}^{base}_{f,y} = i \angle (BASE_y - \varphi - \arg(1+K))
$$

**正向零序电流（平衡负荷，$3\dot{I}^{load}_0 = 0$）：**

$$
3\dot{I}^{base}_{0} = \dot{I}^{base}_{f,x} + \dot{I}^{base}_{f,y}
$$

**电压（始终从正向基准计算）：**

$$
\boxed{
\dot{U}_x = \dot{Z} \cdot \bigl(\dot{I}^{base}_{f,x} + \dot{I}^{load}_x + \dot{K} \cdot 3\dot{I}^{base}_0\bigr)
}
$$
$$
\boxed{
\dot{U}_y = \dot{Z} \cdot \bigl(\dot{I}^{base}_{f,y} + \dot{I}^{load}_y + \dot{K} \cdot 3\dot{I}^{base}_0\bigr)
}
$$
$$
\dot{U}_z = \dot{V}^{nom}_z
$$

展开：$\dot{U}_x = \dot{Z}\bigl(\dot{I}^{base}_{f,x}(1+K) + K\dot{I}^{base}_{f,y} + \dot{I}^{load}_x\bigr)$

**输出电流：**

$$
\dot{I}^{out}_{f,x} = \pm\dot{I}^{base}_{f,x}, \quad
\dot{I}^{out}_{f,y} = \pm\dot{I}^{base}_{f,y}, \quad
\dot{I}_z = \dot{I}^{load}_z
$$
$$
\dot{I}_x = \dot{I}^{out}_{f,x} + \dot{I}^{load}_x, \quad
\dot{I}_y = \dot{I}^{out}_{f,y} + \dot{I}^{load}_y
$$

**验证（无负荷，以相 $x$ 为例）：**

实际 $3\dot{I}_0 = \pm(\dot{I}^{base}_{f,x} + \dot{I}^{base}_{f,y})$（方向随 direction 变化）

$$
Z_{meas,x} = \frac{\dot{Z}\bigl(\dot{I}^{base}_{f,x}(1+K)+K\dot{I}^{base}_{f,y}\bigr)}
{\pm\bigl(\dot{I}^{base}_{f,x}(1+K)+K\dot{I}^{base}_{f,y}\bigr)}
= \begin{cases} z\angle\varphi & \text{正向} \\ z\angle(\varphi+180°) & \text{反向} \end{cases} \checkmark
$$

---

### 5.4 三相短路（ft = 9，对称，无零序）

**基准故障电流（正向）：**

$$
\dot{I}^{base}_{f,p} = i \angle (BASE_p - \varphi), \quad p \in \{A,B,C\}
$$

（三相对称，$k_L$ 不参与，因无零序分量）

**电压（始终从正向基准计算）：**

$$
\boxed{ \dot{U}_p = \dot{Z} \cdot (\dot{I}^{base}_{f,p} + \dot{I}^{load}_p) }
$$

**输出电流：**

$$
\dot{I}^{out}_{f,p} = \pm\dot{I}^{base}_{f,p}, \quad
\dot{I}_p = \dot{I}^{out}_{f,p} + \dot{I}^{load}_p
$$

**验证（无负荷）：**

$$
Z_{meas} = \frac{\dot{Z}\cdot\dot{I}^{base}_{f,p}}{\pm\dot{I}^{base}_{f,p}} = \pm z\angle\varphi \checkmark
$$

---

## 6. CT 极性变换

CT 极性仅影响输出电流，不影响电压（PT 独立测量）：

$$
\text{ct\_polarity}=1 \Rightarrow \dot{I}_{p,out} \leftarrow -\dot{I}_{p,out}, \quad \forall p \in \{A,B,C\}
$$

---

## 7. 正/反向故障的关键结论

| 量 | 正向 (dir=0) | 反向 (dir=1) |
|----|-------------|-------------|
| 基准故障电流 $\dot{I}^{base}_{f}$ | $i\angle\theta_{base}$ | **相同**（不变） |
| **电压** | $\dot{Z}\cdot\bigl(\dot{I}^{base}_{f}(1+K)+\dot{I}^{load}\bigr)$ | **完全相同** |
| 输出故障电流 | $+\dot{I}^{base}_{f}$ | $-\dot{I}^{base}_{f}$ |
| 非故障相电流 | $\dot{I}^{load}$ | $\dot{I}^{load}$（相同） |
| 继电器测得 $Z$ | $z\angle\varphi$ | $z\angle(\varphi+180°)$ |

> **反向故障的识别机制**：继电器测到 $Z_{meas}=z\angle(\varphi+180°)$（第三象限阻抗），方向元件判定反向并闭锁。

---

## 8. 代码实现要点对照

| 数学定义 | 代码实现 |
|---------|---------|
| $\dot{I}^{base}_{f,x}$ | 不含 `dir_flip` 的 `_polar(i, BASE[x]-ang-arg_1pK)` |
| $\dot{U}_x$（电压始终从基准算）| `Uc[x] = Z * (If_base * onepK + Il[x])` |
| 输出电流方向 | `If_out = -If_base if direction==1 else If_base` |
| 非故障相电流不变 | `Ic[p] = Il[p]`（不加 `dir_flip`） |
| CT 极性最后统一翻转 | `if ct_polarity==1: Ic = {p: -Ic[p]}` |
