# 线路保护统一短路算法计算推导 (大一统理论)

本文档旨在提供一个严格、完备的数学推导框架，用于在微机继电保护测试仪的**“恒流模型”**下，计算任意短路类型（单相接地、两相接地、相间短路、三相短路）的输出相量。该推导直接支撑底层的 Python/C++ 核心代码引擎，并修正了过往文档中非故障相处理的不严谨之处。

---

## 1. 算法核心设计原则与符号定义

### 1.1 核心设计原则
1. **纯短路电流分配与恒流控制**：任何故障类型下的“纯故障电流分量”，其幅值必须严格等于用户设定的 $I_f$。
2. **正向压降锚定法则**：无论设定的是正向还是反向故障，电压的计算公式永远固定为“**正向纯故障电流导致的压降 + 负荷压降**”。这保证了电压相角在改变故障方向时绝对不会出现 $180^\circ$ 的无厘头跳变。
3. **后处理反转与极性法则**：反向故障的处理，仅仅是在最后输出时，把相关的纯故障电流分量进行翻转 ($180^\circ$)。最后再加上负荷电流，并统一受 CT 极性（指向线路/母线）的集体翻转控制。
4. **非故障相绝对钳位 (相间/两相接地)**：在发生不对称故障（如相间短路或两相接地）时，系统视作无穷大电源，**非故障相的电压绝对等于系统的额定电压，不应叠加任何负荷压降**。

### 1.2 核心符号定义
*   $ft$：故障类型索引。
    *   `0/1/2`：单相接地（A/B/C）
    *   `3/4/5`：相间短路（AB/BC/CA）
    *   `6/7/8`：两相接地（AB/BC/CA）
    *   `9`：三相短路
*   $\delta_A=0^\circ, \delta_B=-120^\circ, \delta_C=+120^\circ$：三相系统基准相角。
*   $V_{nom}$：系统额定相电压幅值。三相电源电压 $\dot{E}_p = V_{nom} \angle \delta_p$。
*   $I_{load}, \theta_{load}$：负荷电流。三相负荷 $\dot{I}_{load\_p} = I_{load} \angle (\theta_{load} + \delta_p)$。
*   $I_f$：设定的短路电流幅值。
*   $\vec{Z}_{line}$：设定的线路阻抗 $\vec{Z}_{line} = R_{line} + jX_{line}$。
*   $\vec{K}_L$：零序补偿系数复数，根据补偿公式（直接、Kr/Kx、Z0/Z1）动态求出。

---

## 2. 单相接地 (LG) 计算推导
故障相：$\Phi$ (对应 `ft = 0,1,2`)

### 2.1 零序补偿与回路阻抗
计算真实的复数等效回路阻抗 $\vec{Z}_{loop}$。
*   **Formula 1 (分量法)**：$\vec{Z}_{loop} = (1 + K_r)R_{line} + j(1 + K_x)X_{line}$
*   **Formula 0/2 (复数法)**：$\vec{Z}_{loop} = \vec{Z}_{line} \cdot (1 + \vec{K}_L)$。其中 Formula 2 是通过 $\vec{K}_L = \frac{1}{3}(\frac{\vec{Z}_0}{\vec{Z}_1} - 1)$ 换算而来。

提取 $\vec{Z}_{loop}$ 的幅值 $Z_{loop}$ 和角度 $\phi_{loop}$。

### 2.2 生成纯短路电流
为让电压不漂移，必须使得电流的相角等于“故障相基准相角减去回路阻抗角”：
$$ \dot{I}_{f\_fwd} = I_f \angle (\delta_\Phi - \phi_{loop}) $$

### 2.3 生成硬件输出电压
*   故障相 $\Phi$ 叠加负荷：$\dot{V}_\Phi = \dot{I}_{load\_\Phi}\vec{Z}_{line} + \dot{I}_{f\_fwd}\vec{Z}_{loop}$
*   非故障相钳位额定值：$\dot{V}_k = \dot{E}_k \quad (k \neq \Phi)$

---

## 3. 两相接地 (LLG) 计算推导 (逆矩阵相角投影法)
故障相：$x, y$ (对应 `ft=6`为AB, `7`为BC, `8`为CA)，非故障相为 $z_{unf}$。

### 3.1 零序压降算子提取
对于分量法：$\vec{Z}_{0\_drop\_coeff} = K_r R_{line} + j K_x X_{line}$，此时折算的 $\vec{K}_L = \vec{Z}_{0\_drop\_coeff} / \vec{Z}_{line}$。
对于其他方法：$\vec{Z}_{0\_drop\_coeff} = \vec{K}_L \cdot \vec{Z}_{line}$。

### 3.2 矩阵逆解算与降维
设理想电压 $\dot{U}_{x\_target} = 1 \angle \delta_x$ 和 $\dot{U}_{y\_target} = 1 \angle \delta_y$。
构建逆解方程的复数分子向量：
$$ \vec{N}_x = \dot{U}_{x\_target}(1+\vec{K}_L) - \dot{U}_{y\_target}\vec{K}_L $$
$$ \vec{N}_y = \dot{U}_{y\_target}(1+\vec{K}_L) - \dot{U}_{x\_target}\vec{K}_L $$

计算公共相角后滞效应：
$$ \theta_{corr} = \arg(1 + 2\vec{K}_L) $$

### 3.3 提取电流并进行正向压降推导
提取理想相角减去阻抗角和公共修正角：
$$ \alpha = \arg(\vec{N}_x) - \arg(\vec{Z}_{line}) - \theta_{corr} $$
$$ \beta = \arg(\vec{N}_y) - \arg(\vec{Z}_{line}) - \theta_{corr} $$
强制赋予恒流幅值：
$$ \dot{I}_{x\_fwd} = I_f \angle \alpha $$
$$ \dot{I}_{y\_fwd} = I_f \angle \beta $$
公共零序压降：
$$ \dot{V}_{0\_drop} = (\dot{I}_{x\_fwd} + \dot{I}_{y\_fwd}) \cdot \vec{Z}_{0\_drop\_coeff} $$
合成最终电压：
$$ \dot{V}_x = \dot{I}_{x\_fwd}\vec{Z}_{line} + \dot{V}_{0\_drop} + \dot{I}_{load\_x}\vec{Z}_{line} $$
$$ \dot{V}_y = \dot{I}_{y\_fwd}\vec{Z}_{line} + \dot{V}_{0\_drop} + \dot{I}_{load\_y}\vec{Z}_{line} $$
非故障相钳位：
$$ \dot{V}_{z_{unf}} = \dot{E}_{z_{unf}} $$

---

## 4. 相间短路 (LL) 计算推导
故障相：$x, y$，非故障相：$z_{unf}$。无零序参与。

### 4.1 提取线电压基准相角与计算电流
线电压基准相角 $\delta_{xy}$ （AB 为 $30^\circ$，BC 为 $-90^\circ$，CA 为 $150^\circ$）。
故障电流：
$$ \dot{I}_{x\_fwd} = I_f \angle (\delta_{xy} - \arg(\vec{Z}_{line})) $$
$$ \dot{I}_{y\_fwd} = -\dot{I}_{x\_fwd} $$

### 4.2 电压对称跌落与合成
线电压压降：$\dot{V}_{xy\_drop} = (\dot{I}_{x\_fwd} - \dot{I}_{y\_fwd}) \vec{Z}_{line}$
几何中点电压：$\dot{V}_{center} = (\dot{E}_x + \dot{E}_y) / 2$
合成电压：
$$ \dot{V}_x = \dot{V}_{center} + \frac{\dot{V}_{xy\_drop}}{2} + \dot{I}_{load\_x}\vec{Z}_{line} $$
$$ \dot{V}_y = \dot{V}_{center} - \frac{\dot{V}_{xy\_drop}}{2} + \dot{I}_{load\_y}\vec{Z}_{line} $$
非故障相钳位：
$$ \dot{V}_{z_{unf}} = \dot{E}_{z_{unf}} $$

---

## 5. 三相短路 (LLL) 计算推导
三相故障，独立计算，无零序。

$$ \dot{I}_{p\_fwd} = I_f \angle (\delta_p - \arg(\vec{Z}_{line})) \quad (p \in \{A, B, C\}) $$
$$ \dot{V}_p = \dot{I}_{p\_fwd}\vec{Z}_{line} + \dot{I}_{load\_p}\vec{Z}_{line} $$

---

## 6. 后处理流水线 (统一适用于所有故障)

### 6.1 故障方向翻转
计算“实际注入的纯短路电流”：
$$
\dot{I}_{p\_actual} = \begin{cases}
\dot{I}_{p\_fwd}, & \text{当 dir = 0 (正向故障)} \\
-\dot{I}_{p\_fwd}, & \text{当 dir = 1 (反向故障)}
\end{cases}
$$

### 6.2 负荷叠加与 CT 极性
最终输出的三相电流：
$$ \dot{I}_{out\_p} = \dot{I}_{p\_actual} + \dot{I}_{load\_p} $$

最后，若硬件 CT 指向母线 (`ct = 1`)，则：
$$ \dot{I}_{out\_p} \leftarrow -\dot{I}_{out\_p} \quad (对所有相) $$
