# 差动保护电流数学实验室

## 1. 本目录解决什么问题

本目录是一套与 FPGA、`EngineCtrl` 和实验时序完全分离的差动电流数学模型。它按下面的顺序工作：

```text
保护二次端子六路相量
    ↓ 通道排列、逐相极性、整侧极性
    ↓ TAP/Kp 幅值归一化
    ↓ 被测保护实际采用的相位/零序补偿矩阵
保护内部两侧三相比较量 J1、J2
    ↓ 被测保护实际采用的 Id/Ir 方程
每个差动元件的 (Ir, Id)
```

测试电流生成严格走相反方向：先在保护比较域构造目标，再反求六路端子电流，最后必须重新走一遍上述正向模型。正向回算不等于目标时，函数直接报错，不会用默认值继续输出。

本目录没有修改 `RelayProtection/api/ApiDiffTest.py`、`RelayProtection/calc/diff.py` 或任何硬件控制代码。它的用途是先把数学口径做对，并把现有 6I 单相和多相算法放到同一个模型下审查。

需要逐公式推导时，请直接阅读 [差动保护详细计算过程与数学公式.md](差动保护详细计算过程与数学公式.md)。

需要核对界面控件、前端启动 JSON、当前后端读取状态及其对应公式时，请阅读
[差动试验界面参数与API公式映射.md](差动试验界面参数与API公式映射.md)。

需要规划将差动数学模型编译为 Windows DLL、Linux `.so`、ARM/嵌入式静态库，并为后续硬件保留统一接口时，请阅读
[差动计算通用库接口与实施计划.md](差动计算通用库接口与实施计划.md)。

## 2. 结论先行

### 2.1 单相通过不能证明多相公式正确

现有程序有两条不同的 6I 路径：

- A、B、C 调用 `_empirical_6i_single_phase()`，这是按昂立显示值归纳的经验分支；
- AB、BC、CA、ABC 调用 `determine_theta()` 和 `solve_M_equation()`，不是把已经验证的单相模型自然扩展到多相。

以用户已经现场验证过单相的 Yd11、Y 侧校正为例，能解释该单相输出的保护正向模型是 W1 使用 M1、W2 使用 M0。现有多相路径却根据 W1 的 `highClock=0` 得到 0° 补偿，相当于使用 M12 型零序投影，不是 M1。因而现有单相与多相从根上就不属于同一套矩阵。

在本文明确写出的假设模型和测试点 `Id=0.5、Ir=1.0、K=2` 下：

| 现有算法 | Yd11 | Yy12 |
|---|---:|---:|
| Y 侧校正、无额外零序校正、A/B/C | 3/3 正向回算一致 | 3/3 正向回算一致 |
| Y 侧校正、AB/BC/CA/ABC | 0/4 一致 | 1/4 一致，仅 ABC 一致 |

这不是硬件试验结论，而是“现有输出送入明确的保护正向模型后，保护会算出什么”的确定性数学结果。完整数值见 [VALIDATION_REPORT.md](VALIDATION_REPORT.md)。

### 2.2 铭牌接线组别不能唯一确定六路测试电流

Yd11、Yy12 只能说明变压器绕组的名义相位关系。保护内部最终比较量还取决于：

- 被测保护厂家、型号、固件及实际启用的补偿算法；
- 两侧额定容量、电压、CT 一二次额定值和 CT 星形/三角形接法；
- CT 星点方向、极性、A/B/C 或 A/C/B 通道排列；
- 保护实际使用的矩阵号，以及矩阵放在哪一侧；
- 零序是在矩阵中已经消除，还是另有独立零序处理；
- Id、Ir 的精确公式、K 的位置和标幺基准。

因此代码优先提供 `explicit_profile()`。`profile_from_legacy_settings()` 只用于解释当前前端字段和复核现有经验算法，不能作为任意保护的生产默认值。

### 2.3 “特性点试验”和“物理故障仿真”必须分开

本目录生成的是保护特性试验点：指定保护内部希望看到的 Id/Ir 和相别模式，然后反求测试仪六路输出。

真实的 A-G、AB、ABC 物理故障需要正序、负序、零序网络、故障侧、源阻抗、变压器漏抗、中性点接地方式、故障位置等参数。现有前端数据不包含这些量，所以本目录不会伪造一个所谓通用物理故障公式。

## 3. 保护装置正向计算模型

### 3.1 相量和单位边界

数学核心中的电流全部是复相量，并且必须统一使用同一种幅值口径。推荐在数学层一律使用 RMS：

$$
\dot I = I_{RMS} e^{j\varphi}
$$

如果 FPGA 层要求正弦峰值，只能在最终输出边界转换一次：

$$
I_{peak}=\sqrt{2}I_{RMS}
$$

本库不会暗中乘或除 $\sqrt{2}$。`rms_to_peak()` 和 `peak_to_rms()` 是显式的边界函数。当前 `ApiDiffTest.py` 会对点值乘 $\sqrt{2}$；是否保留该行为，必须先确认前端点值到底是 RMS 还是幅度值。

### 3.2 每侧完整变换

以直接接到保护二次端子的三相电流为：

$$
\mathbf X_s=[\dot I_{A,s},\dot I_{B,s},\dot I_{C,s}]^T
$$

本库的每侧正向模型为：

$$
\mathbf J_s=\mathbf M_s\left(p_s k_{p,s}\mathbf P_s\mathbf X_s\right)
$$

其中：

- $\mathbf P_s$ 是通道排列和逐相极性映射；
- $p_s$ 是整侧 CT/接线极性，取 `+1` 或 `-1`；
- $k_{p,s}$ 是本库明确定义的正向乘数；
- $\mathbf M_s$ 是保护实际使用的补偿矩阵；
- $\mathbf J_s$ 才是保护差动方程真正使用的三相量。

`WindingModel.channel_order=(0,2,1)` 可表达 A/C/B 接线；`channel_polarities=(1,-1,1)` 可表达逻辑 B 相反极性。这些影响不能塞进钟点号里猜测。

### 3.3 TAP、CT 和 Kp

SEL 公布的一种 TAP 定义为：

$$
TAP_s=\frac{MVA\times1000\times CTCON}{\sqrt{3}\,kV_s\,CTR_s}
$$

其中星形 CT 的 `CTCON=1`，三角形 CT 的 `CTCON=√3`，`CTR=CT一次额定值/CT二次额定值`。

测试仪直接注入保护二次端子时，保护归一化量是：

$$
\mathbf J_s=\mathbf M_s\frac{\mathbf I_{sec,s}}{TAP_s}
$$

此时本库设置 `kp=1/TAP`，不能再除一次 CTR。`ratings.py` 提供 `tap_secondary_amperes()` 和 `kp_from_tap()`。其他厂家的参考电流定义可能不同，必须按 DUT 手册建立等价的正向乘数。

### 3.4 SEL 风格 Matrix 0…12

`compensation_matrix()` 实现的是 SEL 公布的 Matrix 0…12 表，不宣称所有厂家的矩阵编号都相同。

- M0 是单位阵，保留零序；
- M1…M12 都消除零序；
- 对 ABC 正序，Mn 旋转 $n\times30°$；
- 对 ACB 负序，旋转方向相反；
- M12 是 0° 的零序投影，不等于保留零序的 M0。

例如：

$$
\mathbf M_1=\frac{1}{\sqrt3}
\begin{bmatrix}
1&-1&0\\
0&1&-1\\
-1&0&1
\end{bmatrix}
$$

$$
\mathbf M_{12}=\frac13
\begin{bmatrix}
2&-1&-1\\
-1&2&-1\\
-1&-1&2
\end{bmatrix}
$$

任何非零矩阵都满足：

$$
\mathbf M_n[1,1,1]^T=0
$$

所以它是秩 2 矩阵，纯零序信息已经丢失。

### 3.5 Id/Ir 必须按被测保护选择

完成两侧补偿后，保护逐个内部相元件计算 Id/Ir。一个官方 ABB 示例是：

$$
I_{d,p}=|J_{1,p}+J_{2,p}|
$$

$$
I_{r,p}=\frac{|J_{1,p}-J_{2,p}|}{2}
$$

正常穿越时 $J_{2,p}=-J_{1,p}$，所以 Id 为 0。

SEL 资料列出的另一类制动量是：

$$
I_{r,p}=k\left(|J_{1,p}|+|J_{2,p}|\right)
$$

还有装置采用：

$$
I_{r,p}=\max\left(|J_{1,p}|,|J_{2,p}|\right)
$$

这些定义在内部故障区并不等价。因此“保护装置如何计算”不能写成项目级唯一公式，必须把方程作为 DUT Profile 的组成部分。

代码提供三个含义明确的构造器：

- `EquationConfig.abb_615()`；
- `EquationConfig.scaled_magnitude_sum(scale_factor=0.5)`；
- `EquationConfig.maximum_magnitude()`。

### 3.6 当前前端方程枚举的处理原则

为了能审查现有 JSON，本库还实现了前端枚举名称的字面公式。下表不是对任意保护的权威定义；正式使用前必须拿具体保护说明书逐项确认。

设 $A=|J_1|$、$B=|J_2|$、$\theta=\angle J_1-\angle J_2$：

| 枚举 | 本库严格按以下字面公式正算 |
|---|---|
| `id_i1` | $Id=A$ |
| `id_sum` | $Id=|J_1+J_2|$ |
| `id_diff` | $Id=|J_1-J_2|$ |
| `id_sum_sq` | $Id=|J_1+J_2|^2$ |
| `id_diff_sq` | $Id=|J_1-J_2|^2$ |
| `ir_i2` | $Ir=B$ |
| `ir_diff_k` | $Ir=|J_1-J_2|/K$ |
| `ir_sum_k` | $Ir=|J_1+J_2|/K$ |
| `ir_max_k` | $Ir=K\max(A,B)$ |
| `ir_i2_k` | $Ir=KB$ |
| `ir_id_abs_diff` | $Ir=|Id-A-B|$ |
| `ir_sum_div_k` | $Ir=(A+B)/K$ |
| `ir_imax_sum_k` | 两绕组字面退化为 $Ir=|A-B|/K$；多绕组必须提供全部输入 |
| `ir_sqrt_cos` | $Ir=\sqrt{-AB\cos\theta}$，根号内必须非负 |
| `ir_sum_sq_cos` | $Ir=-AB\cos\theta$，结果必须非负 |
| `ir_diff_sq_cos` | $Ir=AB\cos\theta$，结果必须非负 |

求解器覆盖同向和反向两个合法支路。例如 ABB 公式、`K=2、Id=3、Ir=1` 的合法解是 `J1=2.5∠0°、J2=0.5∠0°`。旧式“负幅值再翻相”本质上也在处理这个支路；新代码直接保留有符号相量并正向验算。

平方公式的 Id/Ir 单位是 $A^2$ 或 $pu^2$。标幺换算时必须乘 $Ie^2$，不能和线性公式一样只乘 `Ie`。

## 4. 从目标 Id/Ir 反求六路电流

完整步骤如下：

1. 根据 `isPerUnit` 和 `Ie` 把坐标换成方程使用的单位；
2. `solve_scalar()` 求出一对补偿域有符号相量 $j_1,j_2$；
3. 按 A/B/C/AB/BC/CA/ABC 选择保护比较域模式向量 $\mathbf q$；
4. 令 $\mathbf J_1=j_1\mathbf q$、$\mathbf J_2=j_2\mathbf q$；
5. 对每侧求解 $\mathbf M_s\mathbf Y_s=\mathbf J_s$；
6. 逆向恢复 Kp、整侧极性、逐相极性和通道排列；
7. 将得到的六路重新送入 `RelayModel.measure()`；
8. 逐相核对 Id 和 Ir，任何不一致都抛出异常。

### 4.1 为什么非零矩阵必须用伪逆

M1…M12 消零序，因而没有普通逆矩阵。目标必须满足：

$$
J_A+J_B+J_C=0
$$

在这个可观测子空间内，SEL 风格矩阵的 Moore–Penrose 逆是转置：

$$
\mathbf Y=\mathbf M^T\mathbf J
$$

但任意公共模 $z[1,1,1]^T$ 都不会改变保护看到的结果：

$$
\mathbf M(\mathbf Y+z[1,1,1]^T)=\mathbf J
$$

所以六路输出并不唯一。本库提供：

- `minimum_norm`：零公共模，电流平方和最小，适合多相；
- `sparse`：加入公共模让尽量多的物理通道为 0，用于复现昂立式单端单相注入。

`sparse` 不是最小峰值电流方案；接入硬件前仍须做每通道电流和功率限制。

## 5. 七种 6I 相别如何定义

本库将 AB/BC/CA/ABC 定义为“保护补偿域的代数特性模式”：

| `test_phase` | 保护比较域模式 $\mathbf q$ |
|---|---|
| AB | $[1,-1,0]^T$ |
| BC | $[0,1,-1]^T$ |
| CA | $[-1,0,1]^T$ |
| ABC | $[1,e^{-j120°},e^{j120°}]^T$ |

A/B/C 要分两种情况：

- 两侧都是 M0 时，可以分别用纯单位向量；
- 任一侧消零序时，纯 `[1,0,0]` 不在矩阵值域内。本库使用与现有昂立单端经验输出相容的可观测双元件模式，并在结果的 `target_basis` 中明确返回真实模式。

因此，在 M1 下所谓“A 相单端试验”可能使保护内部 A、C 两个元件同时出现同幅 Id/Ir；在 M11 下可能使 A、B 两个元件同时出现。它是保护元件特性试验方法，不应被描述成通用 A-G 物理故障。

昂立官方手册把 A/B/C、AB/BC/CA、ABC 列成七个 6I 相别，但没有公开一套跨厂家通用的六相量公式。正式对齐昂立时，最有价值的输入不是界面截图，而是每个相别实际显示的六路幅相或可导出的测试配置。

## 6. 为什么现有多相算法失败

### 6.1 Yd11、Y 侧校正

能解释已验证单相经验输出的假设是：

```text
W1(Y): M1
W2(d): M0
```

现有单相经验分支只使用 `lowClock=11` 选通道和符号，调用时完全没有把 `highClock` 传进去。得到的电流经 M1/M0 正向回算后，A/B/C 的目标 Id/Ir 能闭合。因此本报告中的单相结论严格限于已验证的 `highClock=0`；对于非零 high clock，现有单相算法没有实现相对钟点关系，不能沿用该结论。

现有多相分支却分别把 `highClock` 交给 W1、`lowClock` 交给 W2。W1 是 Y 侧且 `highClock=0`，`determine_theta()` 得到 0°；W2 是 d 侧又不匹配 Y 侧校正，于是无矩阵。结果实际上按 M12/M0 型路径反求，不是 M1/M0。AB、BC、CA 和 ABC 全部偏离目标。

### 6.2 Yy12、Y 侧校正

用于解释现有单相的兼容假设是 M11/M11。现有多相分支对 `clock=0/12` 都走 0° 路径，没有真正逆向 M11。平衡正序 ABC 因共同旋转和幅值不变而偶然闭合，但 AB/BC/CA 的逐元件幅值被矩阵重新分配，所以不闭合。

### 6.3 现有 Yd11 的附加零序缺陷

Y 侧已经使用 M1 时，M1 本身已经消除零序，不应再对未匹配的 d 侧追加零序对消。现有经验分支在 `y-side + zeroSequenceCorrection=true` 时会对 d 侧再写一个错误通道，A/B/C 三种情况均出现 `Id` 最大误差 0.75、`Ir` 最大误差 0.375。

本次只记录并验证问题，没有修改生产代码。

## 7. 已实现的其他计算模式

### 7.1 6I 稳态定点及搜索点

`generate_ratio_point()` 负责一个数学点。固定试验和搜索试验的电流计算相同，区别只在外层如何选择下一个 Id/Ir，不应再写另一套相量公式。

### 7.2 穿越负荷和故障前状态

`generate_through_current()` 在保护补偿域构造平衡正序穿越电流，并按动作公式选择两侧相对极性，保证正向回算 Id 为 0。

不能固定认为侧 2 永远反相：

- `id_sum`/`id_sum_sq` 的穿越量两侧反相；
- `id_diff`/`id_diff_sq` 的穿越量两侧同相；
- `id_i1` 没有两侧相消的穿越状态，函数明确拒绝。

### 7.3 理想工频变化量

`generate_state_pair(diff_mode=2)` 实现透明的相量增量：

$$
\Delta\mathbf I=\mathbf I_{fault}-\mathbf I_{prefault}
$$

故障前输出穿越负荷，故障后输出“穿越负荷 + 目标增量”，再对增量做正向回算。

这不是任意真实保护的完整 DPFC 算法。真实装置还会包含采样滤波、记忆窗、启动门槛、外部故障识别和复位时间；缺少 DUT 算法时只能验证理想相量关系，不能声称验证动作时间。

### 7.4 零序量

`ResidualRelayModel` 与相差动矩阵完全分开，可显式选择：

$$
3I_0=I_A+I_B+I_C
$$

或：

$$
I_0=\frac{I_A+I_B+I_C}{3}
$$

它只表示两组三相残流的数学比较。真正 REF/限制接地故障通常还需要中性点 CT、其变比和极性；只有六路相电流时不能冒充完整 REF。

### 7.5 谐波

`generate_harmonic_point()` 将谐波比定义为补偿后电流谱幅值比，而不是借用可能平方的 Id 方程：

$$
h_n=\frac{|J_n|}{|J_1|}\times100\%
$$

支持两个空间模式：

- `compensated`：每个活动保护元件得到相同的目标谐波百分比；
- `physical_waveform`：物理通道第 n 次谐波角为 `n × 基波角 + relative_angle_deg`，用来描述同形三相波形，此时经过补偿后各元件百分比可能不同。

代码还允许指定 `order`、`apply_side`、相对/绝对幅值和 `relative_angle_deg`。二次/五次是独立闭锁、交叉闭锁还是制动、百分比分母和高定值旁路仍必须按 DUT 手册；本库不会替保护决定这些逻辑。

### 7.6 2I 和并联输出

`TwoCurrentModel` 实现两标量端子的正向比较；`generate_2i_point()` 反求后可把一个端子总电流平均分给多个物理并联源，并在合流后重新正算。

`legacy_2i_model()` 中的 $\sqrt3$ 只用于复现当前接口的隐含增益，不是任意 Y/D 保护的通用定律。准确 2I 模型仍应由实际接线和保护手册给出复数增益与极性。

## 8. 前端参数应如何进入数学模型

### 8.1 会改变六路幅相的字段

| 当前字段 | 应进入的位置 | 当前可确定程度 |
|---|---|---|
| `vectorGroupLetter1/2` | 仅作为绕组类型提示 | 不能单独决定矩阵 |
| `vectorGroupHighSideClock/LowSideClock` | 用于核对相对钟点 | 不能机械等同矩阵号 |
| `phaseCorrection` | legacy profile 的校正侧提示 | 必须最终映射为两侧实际矩阵 |
| `zeroSequenceCorrection` | 相差动比较前零序策略 | 要确认在哪侧、是否已包含在矩阵中 |
| `idEquation/irEquation` | `EquationConfig` | 必须按 DUT 手册确认精确公式 |
| `kFactor` | 方程中的 K | 要确认是乘数还是除数 |
| `kp1/kp2` | 两侧正向幅值乘数或 TAP 适配 | 要确认方向及是否已含 CT/√3 |
| `Ie/isPerUnit` | 坐标单位转换 | 平方量必须使用 $Ie^2$ |
| `connectionSettings.type` | 6I 或 2I 生成器 | 可直接映射 |
| `i1Terminal/i2Terminal` | 六路物理通道映射 | 两侧不得重叠 |
| `testPhase` | 保护域相别模式 | 必须确认是特性 shot 还是物理故障 |
| `diffMode` | 稳态、残流或理想增量模型 | 三类必须走不同模型 |
| `loadCurrent` | 故障前穿越量 | 当前仅有幅值，默认正序、参考角 0° |
| `harmonic.order` | 谐波次数 | 可直接映射 |
| `harmonic.applySide` | 谐波施加侧 | 可直接映射 |
| `harmonicErrorType` | 相对百分比或绝对安培 | 可直接映射，但需统一 RMS/峰值 |
| `points[].x/y` | Ir/Id 或谐波坐标 | 先按项目类型解释，再做单位转换 |

### 8.2 不改变数学公式、只改变实验流程的字段

以下字段可以保留在 API 层，不应进入六路相量公式：

- `activeProject=ratio-fixed/ratio-search/harmonic-fixed/harmonic-search`；
- `precision`、`method` 和搜索上下限；
- `preFaultDuration`、`faultDuration`；
- `simulatePreFault`；
- `logicMask`、DI 防抖和报告字段；
- `statics` 中的固定电压；
- 多余且后端未读取的兼容字段。

固定和搜索只应复用同一个点生成器。时间、DI 和节点跳转不能反过来改变差动电流公式。

### 8.3 当前协议仍缺少的 DUT 必要量

要把本目录安全接入实际 API，至少还要从保护定值或一次确认表中得到：

1. 保护厂家、型号、固件及差动功能版本；
2. 两侧 MVA、kV、CT 一次/二次额定值和 CT 接法；
3. 保护实际显示的 TAP/参考电流或 Kp 定值；
4. 每侧实际矩阵号，不能只给变压器铭牌钟点；
5. CT 星点方向、整侧极性、逐相极性和 ABC/ACB 排列；
6. Id/Ir 精确公式、K 的位置、每相还是三相聚合；
7. A/AB/ABC 的测试语义；
8. 零序消除与 REF 是否启用、中性点 CT 信息；
9. 谐波闭锁/制动、独立/交叉闭锁及百分比定义；
10. 点值和 FPGA 静态层各自是 RMS 还是峰值。

没有这些量时，可以复现昂立某一个模板的输出，但不能证明它适用于另一台保护。

## 9. 代码结构

| 文件 | 作用 |
|---|---|
| `diffmath/matrices.py` | SEL 风格 M0…M12、序分量、伪逆和相量工具 |
| `diffmath/equations.py` | Id/Ir 正向公式、合法域反解、标幺换算 |
| `diffmath/model.py` | 6I 保护正向模型、通道/极性、理想增量和残流模型 |
| `diffmath/ratings.py` | CT ratio、SEL 风格 TAP 和 `kp=1/TAP` |
| `diffmath/profiles.py` | 显式 DUT profile 及严格标注的 legacy 假设适配 |
| `diffmath/generator.py` | A/B/C/AB/BC/CA/ABC、穿越、增量、零序和谐波反求 |
| `diffmath/two_current.py` | 2I 正向/反求和并联电流分配 |
| `diffmath/output.py` | 极坐标序列化及显式 RMS/峰值转换 |
| `diffmath/legacy_validation.py` | 只读调用现有 `calc.diff` 并送入新正向模型审查 |
| `run_validation.py` | 输出可复现的现有/新算法对照 |
| `tests/` | 确定性数学测试，不访问硬件 |

## 10. 使用方式

### 10.1 显式建立 DUT 正向模型

```python
from diffmath import EquationConfig, explicit_profile

relay = explicit_profile(
    matrix1=1,
    matrix2=0,
    kp1=1.0,
    kp2=1.0,
    polarity1=1,
    polarity2=1,
    channel_order1=(0, 1, 2),
    channel_order2=(0, 1, 2),
    channel_polarities1=(1, 1, 1),
    channel_polarities2=(1, 1, 1),
    equations=EquationConfig.abb_615(),
)
```

这里的 M1/M0 是示例，正式值必须来自被测保护设置。`explicit_profile()` 强制调用者明确提供 `matrix1/matrix2`、`kp1/kp2` 和 `equations`，不会把缺失 DUT 参数静默替换成默认公式。

### 10.2 先正算一组捕获的六路电流

```python
measurement = relay.measure(
    injected1=(1+0j, 0j, 0j),
    injected2=(-0.5+0j, 0j, 0.5+0j),
)

print(measurement.compensated1)
print(measurement.compensated2)
print(measurement.phase_pairs)  # 每个元件自己的 (Ir, Id)
```

不要把 `max_ir` 和 `max_id` 拼成一个点，它们可能来自不同元件。

### 10.3 生成并验证多相 6I 点

```python
from diffmath import generate_ratio_point
from diffmath.output import six_channel_polar

point = generate_ratio_point(
    relay,
    id_value=0.5,
    ir_value=1.0,
    test_phase="AB",
)

channels = six_channel_polar(point)
print(channels)  # [Ia, Ib, Ic, Ix, Iy, Iz]，未做 RMS/峰值转换
print(point.measurement.phase_pairs)
```

可直接运行 `example_usage.py` 查看完整示例。

### 10.4 运行验证

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=differential_math_lab \
python3 -m unittest discover -s differential_math_lab/tests -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=differential_math_lab \
python3 differential_math_lab/run_validation.py
```

`run_validation.py` 中的 PASS 只表示与文中明确假设的数学 profile 一致；已知 FAIL 是审查发现，不会让脚本返回失败。它不经过 `ApiDiffTest` 的 $\sqrt2$ 边界，也不代表 DUT、FPGA 或现场硬件验证。

## 11. 接入生产 API 前的规则

1. 先录入并锁定具体 DUT Profile，再允许生成电流；
2. 优先使用实际矩阵号，不从 Yd11/Yy12 自动猜矩阵；
3. 六路生成结果必须再次经过独立正向模型；
4. 检查每通道最大电流、总功率和端子是否重叠；
5. RMS/峰值只在硬件边界转换一次；
6. A/B/C 报告必须说明实际激活了哪些内部元件；
7. AB/ABC 必须明确是保护特性模式还是物理故障；
8. 零序差动、REF、变化量、谐波各用独立模型，不共用稳态相差动的捷径；
9. 用昂立或保护抓取的六路幅相作为外部 golden vectors，不能只比较最终“动作/不动作”；
10. 未确认的方程或设置应报错，不得回退到默认 `I1=Id、I2=Ir`。

## 12. 本阶段做到和没有做到的边界

已经做到：

- 官方资料支持的保护正向处理链；
- SEL 风格 M0…M12 的完整实现和 0 序/正序/负序验证；
- 逐相 Id/Ir 正算及多个典型厂家公式 profile；
- 同向、反向及平方坐标的严格反解；
- 6I 七相别特性模式、2I、穿越负荷、理想增量、残流和谐波数学生成；
- 现有单相/多相代码的只读对照；
- 主要生成路径及关键分支的正向闭环测试。

尚不能仅凭现有 JSON 做到：

- 确认某一具体保护实际使用哪两个矩阵；
- 把 legacy `Kp`、clock、zero-sequence 布尔量自动解释成任意厂家的真实内部算法；
- 生成唯一的物理 A-G/AB/ABC 故障电流；
- 模拟真实 DPFC 滤波和记忆；
- 模拟具体保护的谐波交叉闭锁和 REF 中性点电流；
- 宣称多相新结果已经通过昂立或现场硬件。

这些边界是为了防止“数学闭环正确”再次被误认为“现场配置一定正确”。下一步最有价值的是取得一台实际被测保护的型号、差动定值页、矩阵/TAP 显示值，以及昂立 A、AB、ABC 三组完整六路幅相，然后把它们加入独立 golden-vector 测试。
