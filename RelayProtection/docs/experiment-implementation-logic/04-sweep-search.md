# 通用递变搜索试验 — 实现逻辑与数学模型

> 模块标识：`ac_test`、`dc_test`、`harmonic_test`、`steps_gradient_test`、`acdc_test`（共用同一递变引擎）
> 被测对象：任意"幅值/相位/频率/谐波"型定值的**动作值、返回值、返回系数**测量
> 文档性质：以**继电保护测试原理**与**前端接口契约**为依据；不含代码实现细节。

---

## 1. 摘要

大量保护与测量元件的定值是一个**门槛量**：被测量缓慢递增（或递减）越过门槛时元件动作（"动作值/启动值"），
反向退回越过返回门槛时元件返回（"返回值"）。两者之比即**返回系数**
$K_{\text{re}}=I_{\text{return}}/I_{\text{action}}$，是衡量元件性能的重要指标。

通用递变搜索试验用一个**阶梯递变**（ramp）的输出量逼近门槛：从基准值开始，每隔 `stepTime`
施加一次增量 `step`，直到监视的开入（DI）变位 → 记录此刻输出值为**动作值**与**动作时间**；
若开启反向扫描，则保持/反向递变直至 DI 返回 → 记录**返回值**与**返回系数**。

五个模块（交流/直流/谐波/梯度/交直流）只是"被递变的物理量"不同，**搜索骨架完全一致**。

---

## 2. 原理背景

### 2.1 动作值与返回系数

设被递变量为 $u$（电压、电流、频率、相位或谐波含量），第 $n$ 步的输出

$$
u_n=\operatorname{clamp}\!\bigl(u_0+n\cdot\Delta u,\ \le u_{\max}\bigr),\qquad n=0,1,\dots,N,
$$

其中 $u_0$ = 基准（`statics`），$\Delta u$ = 步增量（`steps`），$u_{\max}$ = 上限（`limits`），$N$ = `count`。
当第 $n^\*$ 步触发 DI 时，动作值 $u_{\text{act}}=u_{n^\*}$。

反向扫描从动作点起以相反方向递变，触发 DI 返回于第 $m^\*$ 步，返回值 $u_{\text{ret}}=u_{m^\*}$，

$$
\boxed{\;K_{\text{re}}=\dfrac{u_{\text{ret}}}{u_{\text{act}}}\;}
$$

过电压/过流元件 $K_{\text{re}}<1$，欠压/欠频元件 $K_{\text{re}}>1$。

### 2.2 步进时间与动作时间

`stepTime` 为每步驻留时长。动作时间由 FPGA SOE 给出（DI 变位时间戳 $-$ 试验开始时间戳），
与"递变到动作值所经历的步数 × 步时"在量级上一致，但以硬件时间戳为准。

### 2.3 复归与磁链平滑（DC 补偿）

- **试验前复归**（`enablePreTestReset`）：先施加 `resetTableData` 一段 `preTestResetTime`，
  让元件处于确定的初始返回态，避免上次试验残留。
- **步间复归**（`enableStepReset`）：每步之间插入一段复归态（`stepResetTime`），
  使元件每步都"从返回态重新启动"——用于测量**纯动作值**而非累积热效应；
  `stepResetMode`：`0` 单向（仅正扫复归）/ `1` 全程（正反扫均复归）。
- **直流分量补偿**（`dcComp`）：对感性负载，故障突变会产生按时间常数 $\tau$ 衰减的非周期直流分量

  $$
  i_{\text{dc}}(t)=I_0\,e^{-t/\tau},
  $$

  注入该衰减直流可平滑磁链过渡、逼真重现暂态，避免理想正弦突变带来的失真。
  `tau` = 负载时间常数，`dt` = 衰减步长，配合 `gateChannel/gatePhase` 在指定相位点切入（硬件相位门控）。

---

## 3. 引擎实现映射

递变搜索映射到引擎的两种波形模式（见 `docs/engine_execution_model.md`）：

| 阶段 | 引擎模式 | 说明 |
|---|---|---|
| 正向/反向递变 | **Mode 2 Sweep** | `baseFrame` 基准生效 → `SYS_SYNC` 三区对齐 → 每步 `STEP_SHADOW` 预载、`SYS_UPDATE` 翻转 |
| 步间复归 | **Mode 3 Reset** | 故障基准与复归值在 Active/Shadow 间乒乓，Base 在幕后累加下一步 |
| 直流门控切入 | **Mode 4 DcComp** | 相位门控 `PHASE_GATE` 阻塞 FIFO，目标相位命中硬件级翻转 |

DI 触发评估由引擎按优先级（manualTrig > timeout > diMatch）完成；阶梯递变与跳转完全由预编译节点驱动。

> 幅值越界须裁剪：AC 层幅值在 FPGA 内以 U16 累加、I16 读取，跨 32767 会反相，故每步累加结果须
> 受 `limits` 钳位（见 `docs/fpga_protocol.md` §4）。

---

## 4. 前端控制变量

### 4.1 系统 `sys`

| 字段 | 物理意义 / 影响 |
|---|---|
| `mode` | `1` 自动（自动递变搜索）/ `0` 手动（仅静态输出，靠 `update_static` 改值） |
| `changeMode` | `0` 触发即终止 / `1` 触发后保持，等待反向扫描（测返回值） |
| `returnMode` | `0` 反扫遇 DI 返回即停 / `1` 反扫忽略 DI，跑满 `count` |
| `stepTime` | 每步驻留时间（µs） |
| `logicMask` | DI 匹配掩码：低 8 位通道掩码，bit8(0x100) 与/或逻辑 |
| `doMask` / `doCtrlMask` | 复归段 / 动作段的 DO 状态 |
| `debounce` | DI 防抖时间 |

### 4.2 递变定义

| 字段 | 物理意义 |
|---|---|
| `statics` | 基准输出 $u_0$（通道→层→[幅值, 频率/相位]） |
| `steps` | 每步增量 $\Delta u$ |
| `count` | 总步数 $N$ |
| `limits` | 各通道幅值/频率上限。**注：当前后端不按 `limits` 做软件钳位**——幅值上限由前端限幅 + DAC 硬件上限兜底；本文下文涉及"钳位于 limits"为协议设计意图，非现行后端实现 |

### 4.3 复归与补偿 `payload` / `dcComp`

| 字段 | 物理意义 |
|---|---|
| `enablePreTestReset` / `preTestResetTime` | 试验前复归及时长 |
| `enableStepReset` / `stepResetMode` / `stepResetTime` | 步间复归、单向/全程、时长 |
| `resetTableData` | 复归态输出值 |
| `dcComp.tau / dt / gateChannel / gatePhase` | 衰减直流时间常数、步长、门控通道与相位 |
| `prevStatics` | 故障前状态（配合 dcComp 计算直流初值） |

### 4.4 五个模块的"递变量"差异

| 模块 | 递变量 | 典型用途 |
|---|---|---|
| `ac_test` | 交流幅值 / 相位 / 频率 | 过/欠压、过流、低频、相位元件动作值 |
| `dc_test` | 直流幅值 | 直流量元件 |
| `harmonic_test` | 谐波含量 | 谐波告警/制动门槛 |
| `steps_gradient_test` | 多通道梯度递变 | 复合判据扫描 |
| `acdc_test` | 交流叠加直流 | 含直流分量的暂态考核 |

---

## 5. 试验流程

```
[试验前复归]
   → 正向递变(Node 2): 每 stepTime 加 step, 钳位于 limits
        ├─ DI 变位  → 记 动作值 u_act + 动作时间 (SOE)
        │     ├─ changeMode=0 → 终止
        │     └─ changeMode=1 → 进入反向扫描(Node 3)
        └─ count 跑完且未触发 → 按 changeMode 终止或反扫
   → 反向递变(Node 3, 仅 changeMode=1): 从 u_act 反向递变
        ├─ returnMode=0: DI 返回 → 记 返回值 u_ret → 终止
        └─ returnMode=1: 跑满 count → 终止
   → report: tripTime, tripValues, returnTime, returnValues, returnRatio
```

- 自动模式（`mode=1`）：上述递变由后端自动执行。
- 手动模式（`mode=0`）：仅输出静态量，前端用 `update_static` 手动调值，用于人工找门槛。
- 反向扫描的自动触发（`changeMode=1`）由后端**热更新**机制在 DI 跳闸瞬间切换，前端无需 `next_node`。

---

## 6. 测量与上报

| 量 | 定义 |
|---|---|
| `tripTime` | 试验开始 → DI 触发的时间（ms，SOE） |
| `tripValues` | 触发瞬间各通道输出值（动作值） |
| `returnTime` | 反扫开始 → DI 返回的时间（ms） |
| `returnValues` | 返回瞬间各通道输出值（返回值） |
| `returnRatio` | $u_{\text{ret}}/u_{\text{act}}$ |

实时 `value_update` 推送当前 `static` 输出值，供前端绘制递变曲线。

---

## 7. 算例

**例：过流元件动作值与返回系数（`ac_test`，电流通道）。**

基准 $I_0=0$ A，步增 $\Delta I=0.5$ A，`stepTime`=1 ms，`count`=20，`limits`=10 A，
`changeMode=1`，`returnMode=0`。

- 正扫：$I_n=0.5n$。设第 $n^\*=11$ 步（$I=5.5$ A）DI 跳闸 → 动作值 $u_{\text{act}}=5.5$ A。
- 反扫：从 5.5 A 反向递减，设第 $m^\*$ 步 $I=5.2$ A 时 DI 返回 → 返回值 $u_{\text{ret}}=5.2$ A。
- 返回系数 $K_{\text{re}}=5.2/5.5=0.945$（过流元件 $<1$，合理）。

---

## 8. 边界情形与工程注意

1. **幅值钳位**：每步累加须受 `limits` 约束，防止 AC 幅值越 32767 反相。
2. **步间复归**：测纯动作值时启用 `enableStepReset`，避免热累积/记忆影响。
3. **返回系数仅在 `changeMode=1`**：需保持动作态并反扫才能测返回值。
4. **SOE 计时**：动作/返回时间以硬件时间戳为准。
5. **手动模式**：`mode=0` 时不自动递变，依赖 `update_static`。
6. **直流补偿**：仅感性负载/暂态考核需要；纯稳态动作值测量可不启用。

---

## 参考

- GB/T 14285、DL/T 478（继电保护及安全自动装置通用技术条件）
- 《电力系统继电保护原理》— 过流/低压/低频元件、返回系数章节
- 本仓库：`docs/architecture_overview.md`（Sweep/Reset/DcComp 模式寄存器级推导）、
  `docs/engine_execution_model.md`、`docs/fpga_protocol.md`
