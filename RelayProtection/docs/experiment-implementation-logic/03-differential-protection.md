# 差动保护试验 — 实现逻辑与数学模型

> 模块标识：`differential_test`（前端路由 `/differential-test-6ch`，含母差变体）
> 被测对象：比率制动差动、谐波制动差动（变压器/母线/线路差动，6 路或分相 2 路）
> 文档性质：以**继电保护测试原理**与**前端接口契约**为依据；不含代码实现细节。

---

## 1. 摘要

差动保护基于**基尔霍夫电流定律**：正常或区外故障时，流入与流出被保护设备的电流之和（差动电流）
理论上为零；区内故障时差动电流等于故障电流。为躲过 CT 变比误差、变压器励磁涌流、有载调压等，
实际采用**比率制动**（差动电流随穿越电流按比例抬高门槛）与**谐波制动**（用涌流中的二次谐波闭锁）。

差动试验在 $I_r$（制动电流）–$I_d$（差动电流）平面上**搜索动作边界**或**定点校验**，
并可用谐波含量搜索涌流闭锁门槛。其"逆问题"是：给定目标工作点 $(I_r,I_d)$ 与所选动作/制动方程，
反算出应注入测试仪两侧的电流相量 $\dot I_1,\dot I_2$（经接线组别补偿后使保护恰好测得该 $(I_r,I_d)$）。

---

## 2. 保护原理背景

### 2.1 比率制动特性

差动电流 $I_d$ 与制动电流 $I_r$ 由两侧电流构造（见 §3）。比率制动动作判据通常是一条分段折线：

$$
I_d>\;
\begin{cases}
I_{d,\min}, & I_r\le I_{r1}\ (\text{最小动作电流，门槛段})\\
I_{d,\min}+k_1\,(I_r-I_{r1}), & I_{r1}<I_r\le I_{r2}\ (\text{比率段，斜率}\,k_1)\\
\cdots, & (\text{多段斜率})\\
I_{d,\text{inst}}, & (\text{差动速断，瞬时段})
\end{cases}
$$

- $I_{d,\min}$：最小动作电流（门槛），躲过正常不平衡电流。
- 斜率 $k$（制动系数）：穿越电流越大、门槛越高，躲过区外故障时的 CT 饱和误差。
- $I_{d,\text{inst}}$：差动速断，大电流区内故障快速切除，不受谐波闭锁。

### 2.2 谐波制动

变压器空载合闸的**励磁涌流**含大量二次谐波，其差动电流虽大却非内部故障。
谐波制动用二次谐波与基波之比闭锁差动：

$$
\frac{I_{2\text{nd}}}{I_{1\text{st}}}>\eta_{\text{set}}\;\Rightarrow\;\text{闭锁}.
$$

谐波制动试验在固定 $I_d$ 下，搜索使保护**由动作转为闭锁**的临界谐波百分比 $\eta_{\text{bd}}$。

### 2.3 接线组别补偿

变压器两侧电流因联结组别（如 Yd11）存在固有相移与零序差异，差动前须补偿。
记两侧实际注入电流向量 $\dot{\mathbf I}_1,\dot{\mathbf I}_2$，补偿后量

$$
\dot{\mathbf I}_1'=\mathbf M_1\,\dot{\mathbf I}_1,\qquad
\dot{\mathbf I}_2'=\mathbf M_2\,\dot{\mathbf I}_2,
$$

$\mathbf M$ 为由**钟点数**（`vectorGroupHighSideClock/LowSideClock`，$0\!-\!11$，每点 $30^\circ$）、
**相位校正侧**（`phaseCorrection`：none / y-side / delta-side）与**零序校正**
（`zeroSequenceCorrection`：Y 侧扣除零序 $\dot I_0$）决定的相量变换。
保护的 $I_d,I_r$ 均以补偿后量 $\dot I_1',\dot I_2'$ 计算（下文方程中带 $'$ 者均指补偿后）。

> 试验中前端通常已选定**测试相** `testPhase`（A/B/C 或 AB/…/ABC），后端只需针对该相元件，
> 把补偿关系简化为标量/二维相量求解（见 §4）。

---

## 3. 动作与制动方程（前端可选）

差动电流方程 `idEquation`（$'$ = 补偿后）：

| 取值 | 公式 |
|---|---|
| `id_i1` | $I_d=\lvert\dot I_1'\rvert$ |
| `id_sum` | $I_d=\lvert\dot I_1'+\dot I_2'\rvert$ |
| `id_diff` | $I_d=\lvert\dot I_1'-\dot I_2'\rvert$ |
| `id_sum_sq` | $I_d=\lvert\dot I_1'+\dot I_2'\rvert^2$ |
| `id_diff_sq` | $I_d=\lvert\dot I_1'-\dot I_2'\rvert^2$ |

制动电流方程 `irEquation`（部分）：

| 取值 | 公式 |
|---|---|
| `ir_i2` | $I_r=\lvert\dot I_2'\rvert$（配合 `id_i1`） |
| `ir_diff_k` | $I_r=\lvert\dot I_1'-\dot I_2'\rvert/K$ |
| `ir_sum_k` | $I_r=\lvert\dot I_1'+\dot I_2'\rvert/K$ |
| `ir_max_k` | $I_r=\max(\lvert\dot I_1'\rvert,\lvert\dot I_2'\rvert)\cdot K$ |
| `ir_i2_k` | $I_r=\lvert\dot I_2'\rvert\cdot K$ |
| `ir_sum_div_k` | $I_r=(\lvert\dot I_1'\rvert+\lvert\dot I_2'\rvert)/K$ |
| `ir_sqrt_cos` | $I_r=\sqrt{-\lvert\dot I_1'\dot I_2'\rvert\cos\theta}$ |
| `ir_sum_sq_cos` | $I_r=-\lvert\dot I_1'\dot I_2'\rvert\cos\theta$（配 `id_sum_sq`） |
| `ir_diff_sq_cos` | $I_r=\lvert\dot I_1'\dot I_2'\rvert\cos\theta$（配 `id_diff_sq`） |

其中 $K$ = `kFactor`，$\theta$ 为两侧电流夹角。$(idEquation,irEquation)$ 成对决定了**逆问题的解法**。

---

## 4. 数学模型：工作点 $(I_r,I_d)$ → 注入电流

试验给定目标点 $(I_r,I_d)$（以及测试相、接线、方程），需反算两侧注入电流。
关键在于：两个未知相量（$\dot I_1,\dot I_2$，共 4 个自由度：两幅值、两相角）由两个标量方程
（$I_d,I_r$）约束，**欠定**，故须补充**约束约定**：

1. **相位约定**：穿越电流（区外/负荷）下两侧电流相位相反（夹角 $180^\circ$），区内故障同相。
   测试时固定一侧相角为参考（如 $\dot I_2'=I_2'\angle180^\circ$），令两侧反相以构造"穿越制动"。
2. **求解**：在该相位约定下，$I_d,I_r$ 退化为关于两个幅值 $I_1',I_2'$ 的二元方程组，解出后
   反乘补偿逆矩阵 $\mathbf M^{-1}$ 还原为实际注入相量。

### 4.1 代表性闭式解（`id_diff`/`id_sum` 配 `ir_sum_k`/`ir_diff_k`）

为使**穿越电流→低差流**（区外/制动工况的标准测试几何），相位约定取：

- `id_diff`（$I_d=\lvert\dot I_1'-\dot I_2'\rvert$）→ 两侧**同相** $\dot I_2'=I_2'\angle0^\circ$；
- `id_sum`（$I_d=\lvert\dot I_1'+\dot I_2'\rvert$）→ 两侧**反相** $\dot I_2'=I_2'\angle180^\circ$。

两种约定下方程组均退化为

$$
I_1'-I_2'=I_d,\qquad I_1'+I_2'=K\,I_r,
$$

由此解出

$$
\boxed{\;I_1'=\frac{I_d+K\,I_r}{2},\qquad I_2'=\frac{K\,I_r-I_d}{2}\;}
\quad(\text{要求 }K\,I_r\ge I_d\ \text{以保证 }I_2'\ge0).
$$

> 注意相位约定的选择决定可实现性：上式在 $K I_r\ge I_d$（即工作点位于制动区一侧）时
> $I_2'\ge0$，对应真实可发的测试电流。若取相反约定会得到 $I_2'=(I_d-KI_r)/2<0$，
> 表示该约定下不可实现——故须按上述（id_diff 同相 / id_sum 反相）取相位。

再经接线补偿逆变换得实际注入 $\dot I_1,\dot I_2$。其他方程对 $(I_d,I_r)$ 各有对应闭式解
（"方程路由表"：每种 $(idEquation,irEquation)$ 组合一条求解公式）。

### 4.2 谐波制动点的构造

谐波试验在测试电流上叠加指定次（`harmonic.order`，常用 2 次）谐波，叠加侧由 `applySide` 指定，
谐波百分比为相对基波的幅值比。注入

$$
i(t)=I_{1}\sin(\omega t+\varphi_1)+\eta\,I_{1}\sin(n\omega t+\varphi_n),
$$

$\eta$ 即谐波含量。搜索 $\eta$ 使保护由动作转闭锁，得制动门槛 $\eta_{\text{bd}}$。

### 4.3 差动类型 `diffMode`

| 值 | 含义 | 注入差异 |
|---|---|---|
| `0` 稳态差动 | 故障电流不叠加负荷 | 直接注入 §4 解出的电流 |
| `1` 零序差动 | 零序差动元件 | 构造含零序的不平衡注入 |
| `2` 工频变化量差动 | 突变量差动 | 故障前后电流之差对应目标 $(I_r,I_d)$ |

---

## 5. 前端控制变量

### 5.1 保护与接线

| 字段 | 物理意义 |
|---|---|
| `protectionSettings.vectorGroupLetter1/2` | 两侧联结字母（y/d） |
| `protectionSettings.vectorGroupHighSideClock/LowSideClock` | 两侧钟点数（0–11，每点 30°） |
| `protectionSettings.phaseCorrection` | 相位校正侧（none/y-side/delta-side） |
| `protectionSettings.zeroSequenceCorrection` | Y 侧零序校正 |
| `connectionSettings.type` | `ext-differential`(6I 扩展差动) / `phase-differential`(2I 分相差动) |
| `connectionSettings.i1Terminal/i2Terminal` | I1/I2 接测试仪的端子（决定物理通道与角度） |

### 5.2 方程与系数

| 字段 | 物理意义 |
|---|---|
| `equationSettings.idEquation / irEquation` | 动作/制动方程（§3） |
| `equationSettings.kFactor` (K)、`kp1`、`kp2` | 制动系数与补偿系数 |
| `equationSettings.Ie` | 额定/基准电流（标幺基值） |

### 5.3 试验项目与试验点

| 字段 | 物理意义 |
|---|---|
| `projectSettings.activeProject` | `ratio-search`/`ratio-fixed`/`harmonic-search`/`harmonic-fixed` |
| `projectSettings.testPhase` | 测试相 A/B/C/AB/BC/CA/ABC |
| `projectSettings.search.method/precision` | 搜索策略（binary/linear）与精度 |
| `projectSettings.harmonic.order/applySide` | 谐波次数与叠加侧 |
| `projectSettings.points[]` | 试验点（见下） |

试验点 `points[]` 形态：

| 项目 | 元素 | 含义 |
|---|---|---|
| `ratio-search` | `{id, x, y:[y0,y1]}` | 竖线：$x=I_r$ 固定，$y=I_d$ 扫描区间 |
| `harmonic-search` | `{id, y, x:[x0,x1]}` | 横线：$y=I_d$ 固定，$x=$ 谐波% 扫描区间 |
| `ratio-fixed` / `harmonic-fixed` | `{id, x, y}` | 单点（$I_r,I_d$ 或 谐波%,$I_d$） |

### 5.4 时序

| 字段 | 物理意义 |
|---|---|
| `testParams.diffMode` | 差动类型（§4.3） |
| `testParams.preFaultDuration / faultDuration` | 预故障/故障持续时间（ms） |
| `testParams.simulatePreFault` | 是否施加故障前态 |
| `testParams.loadCurrent` | 稳定负荷电流幅值 |

电流与辅助电压经 `params.statics` 通道矩阵下发；试验点仅在 `payload.projectSettings.points`。

---

## 6. 试验时序与搜索

```
[故障前态 preFaultDuration] → 故障态(注入目标点电流, faultDuration) → 判跳闸 → 取下一点/二分
```

- **ratio-search**：固定 $I_r$，沿 $I_d\in[y_0,y_1]$ 二分，找动作/不动作翻转的边界 $I_{d,\text{bd}}$。
- **harmonic-search**：固定 $I_d$，沿谐波 $\%\in[x_0,x_1]$ 二分，找闭锁门槛 $\eta_{\text{bd}}$。
- **定点**：单点施加，记录是否动作及（如有）动作时间。
- 收敛阈值由 `search.precision` 控制；每点推送 `value_update`（右图 $(x,y)$ 实测轨迹），
  收敛后 `report` 回写点行（含 $i_1,i_2$ 侧电流实测值）。

---

## 7. 算例

**例：比率边界搜索，`id_diff`+`ir_sum_k`，$K=2$，测试相 A，固定 $I_r=1.0$ A，搜索 $I_d\in[0,2]$。**

中点 $I_d=1.0$ A：由 §4.1（id_diff 取两侧同相），
$I_1'=(I_d+K I_r)/2=(1.0+2.0)/2=1.5$ A，$I_2'=(K I_r-I_d)/2=(2.0-1.0)/2=0.5$ A，
均 $\ge0$ → 可实现。保护测得 $I_d=\lvert I_1'-I_2'\rvert=1.0$、$I_r=\lvert I_1'+I_2'\rvert/K=1.0$，
恰为目标工作点。二分逼近实际动作门槛 $I_{d,\text{bd}}$，即该 $I_r$ 下的比率特性纵坐标
（动作→减小 $I_d$ 收缩上界，不动作→增大下界）。

**例：谐波制动门槛搜索，2 次谐波，$I_1$ 侧叠加，测试相 A，固定 $I_d=5$ A，搜索谐波 $\%\in[0,40]$。**

注入侧（i1=high-y, A 相→Ia/ch6）：基波 $5\sqrt2$ A 峰值，叠加 layer "2"（2 次谐波）
幅值 $=\eta\%\times5\sqrt2$，另一侧 0。差流 $=5$ A，2 次谐波含量 $=\eta\%$。
保护当 $\eta\%>\eta_{\text{set}}$ 闭锁（不动作）。二分：动作→提高下界，闭锁→降低上界，
收敛得闭锁门槛 $\eta_{\text{bd}}\approx\eta_{\text{set}}$。

---

## 8. 边界情形与工程注意

1. **方程成对**：$idEquation$ 与 $irEquation$ 须匹配（如 `id_sum_sq` 配 `ir_sum_sq_cos`），逆解公式随之而定。
2. **可达性**：解出的 $I_2'<0$ 表示该 $(I_r,I_d)$ 在所选相位约定下不可达，应判为深制动区（不动作）。
3. **接线补偿一致**：测试仪补偿必须与保护整定的联结组别/零序校正一致，否则 $(I_r,I_d)$ 系统偏移。
4. **谐波速断不闭锁**：差动速断段不受谐波制动，谐波搜索仅对比率段有效。
5. **数值口径**：右图坐标、`points`、`statics` 电流层、回传 `value_update/report` 用**同一套工程值**，不做有效值↔峰值换算。
6. **2I vs 6I**：分相差动仅两个单电流，扩展差动为两组三相电流，补偿矩阵维度不同。

---

## 参考

- IEC 60255-187（变压器差动保护）、GB/T 14285（继电保护和安全自动装置技术规程）
- 《电力系统继电保护原理》— 变压器差动、比率制动、谐波制动章节
- 本仓库：`docs/api/differential_test/`（前端接口与方程枚举）、`docs/architecture_overview.md`
