# USE FSM 引擎四大波形控制底层实现

> 本文档描述 FPGA 硬件管线的核心法则与四种波形模式的缓冲区交互逻辑。
> 数据源头为 `new_protocol.md §4` 中定义的 Flat Node 结构。

---

## 符号与数据源映射

下表列出本文档中使用的所有符号及其与 Flat Node 协议字段的对应关系。

| 符号 | 含义 | 数据源 (Flat Node Key) | 编译产物 |
|:-----|:-----|:----------------------|:---------|
| `Ss` | 稳态阴影帧集 | `Static` [U64] | `DDS_WR_SHADOW` 帧 × N |
| `Rs` | 复归态阴影帧集 | `ResetStatic` [U64] | `DDS_WR_SHADOW` 帧 × N |
| `SsRs` | Ss + Rs 合并帧 | — | `Ss` ∪ `Rs` |
| `Steps[N]` | 第 N 拍步进增量帧 | `Steps` [List[U64]] | `DDS_STEP_SHADOW` 帧 × N |
| `neg_Steps[N]` | Steps[N] 的反向帧 | — | 运行时取反：对帧 `[5:7]` amp 和 `[7:11]` freq/phase 做 I16/I32 补码取反 + 重算 XOR |
| `S0Ss` | 静态层 S0 同步锁帧 | 由 `Static`+`ResetStatic` 层级推导 | Δ=0 的 `DDS_STEP_SHADOW` |
| `S0St` | 步进层 S0 同步锁帧 | 由 `Steps` 层级推导 | Δ=0 的 `DDS_STEP_SHADOW` |
| `SsDc` | DcComp 洗底帧 | `Static` + Σ`Steps` | `DDS_WR_SHADOW` 帧 |
| `Up` | 系统更新指令 | — | `SYS_UPDATE` 单帧 |
| `Pg[N]` | 第 N 拍相位闸门帧 | `PhaseGate` [U64] | `PHASE_GATE` 帧，相位逐拍累加 |
| `ResetDo` | 进入复归态时的 DO 掩码 | `ResetDo` [U32] 低 16 位 | `SYS_SET_DO` 帧 |
| `OverDo` | 退出复归态时的 DO 掩码 | `ResetDo` [U32] 高 16 位 | `SYS_SET_DO` 帧 |
| `DoAction` | 延时 DO 输出任务 | `DoAction` [U32] | `SYS_SET_DO` 帧 (后台定时) |

---

## 模式分类规则

```
有 Interval？
├── 否 → Mode 1: 静态驻留 (Static)
└── 是 → 有 ResetTime？
    ├── 是 → Mode 3: 含复归脉冲扫频 (Reset)
    └── 否 → 有 PhaseGate？
        ├── 是 → Mode 4: 离散补偿阵列 (DcComp)
        └── 否 → Mode 2: 连续扫频 (Sweep)
```

---

## 核心硬件法则

### 1. 局部覆写 (Partial Override)
进入新状态节点时，FPGA 的三个缓冲区（Active、Shadow、Base）保留上一状态遗留的完整波形（`Prev`）。`Ss` 仅局部覆盖指定通道和层级，未覆盖的保留 `Prev`。

> ⚠️ **Ss 必须完全覆盖 Rs**：含复归模式中，`Ss` 的通道/层级掩码必须是 `Rs` 的超集。否则 Teardown 无法干净恢复。

### 2. 管线群发 (Pipeline Batching)
`Ss` 等符号代表的是**多帧集合**，在 UART 上一口气连贯压入。

### 3. S0 同步锁拆分
S0 = 步长 Δ=0 的 `DDS_STEP_SHADOW`，强制 `Shadow = Base` 对齐。拆分为：
- **S0Ss**: 被 Ss 覆盖过的层级
- **S0St**: 参与步进的动态层级

### 4. 状态数学
- **Ss_Base** = `Prev ⊕ Ss` (静态基底)
- **Out(N)** = `Ss_Base ⊕ Σ Steps[0..N-1]` (第 N 拍输出，均匀时退化为 `N × Steps[0]`)
- **Rst** = `Prev ⊕ Rs` (复归态)

### 5. Steps 统一模型
所有扫频模式共用 `Steps[]` 数组（源自 `Node.Steps` 二维矩阵）。每个元素 `Steps[N]` 是第 N 拍的增量帧集合：
- **均匀步进**：`Steps[0] == Steps[1] == ... == Steps[M-1]`（编译期去重，只生成一份帧）
- **非均匀步进**：每拍增量可不同（如 DcComp 离散补偿）
- **neg_Steps[N]**：`Steps[N]` 的反向帧，用于跳闸撤销

### 6. DO 控制
- **DoAction**：后台定时任务，按 `(delay_ms, mask)` 在进入节点后延时执行 `SYS_SET_DO`。DcComp 模式中延迟到 Pg[0] ACK 后才生成。
- **ResetDo**：复归态进入瞬间执行的 `SYS_SET_DO(EnterMask)`
- **OverDo**：复归态退出（即将输出）瞬间执行的 `SYS_SET_DO(ExitMask)`

---

## 模式一：静态驻留 (Static)

**所需字段**：`Static`, `DoAction`(可选), `Triggers`

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 初始 | `Ss` + `Up` | **Ss_Base** ⚡ | Prev | Ss_Base |
| 跳闸收尾 | `S0Ss` | Ss_Base | Ss_Base | Ss_Base |

---

## 模式二：连续扫频 (Sweep)

**所需字段**：`Static`, `Interval`, `Steps`(可选), `DoAction`(可选), `Triggers`

**核心策略**：超前预装载，跳闸时用 `-Steps[N]` 撤销回退。

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| Tick 0 | `Ss` + `Up` | **Out(0)** ⚡ | Prev | Out(0) |
| 预载 T1 | `S0Ss` + `Steps[0]` | Out(0) | Out(1) | Out(1) |
| T0 跳闸收尾 | `-Steps[0]` | Out(0) | Out(0) | Out(0) |
| Tick 1 | `Up` | **Out(1)** ⚡ | Out(0) | Out(1) |
| 预载 T2 | `Steps[1]` | Out(1) | Out(2) | Out(2) |
| T1 跳闸收尾 | `-Steps[1]` | Out(1) | Out(1) | Out(1) |

---

## 模式三：含复归的脉冲扫频 (Reset)

**所需字段**：`Static`, `Interval`, `Steps`(可选), `ResetTime`, `ResetStatic`(可选), `ResetDo`(可选), `DoAction`(可选), `Triggers`

**DO 时序**：每个 Tick 含两个子阶段——复归期和输出期。DO 在阶段边界切换：

```
          ResetDo              OverDo            ResetDo
     ─────┤ 复归期 ├──────────┤ 输出期 ├──────────┤ 复归期 ├───
          ← ResetTime →      ← Interval →      ← ResetTime →
```

**Teardown 法则**：
1. **首拍输出期 (T0)**：未发生步进累加 → 仅发 `S0Ss`
2. **后续输出期**：已有步进累加 → 发 `S0Ss + S0St`
3. **复归期迟发跳闸**：真凶是上一拍 Out(N-1) → `-Steps[N]` → `Up` → `S0Ss + S0St`

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 初始 | `SsRs` + `Up` | **Rst** ⚡ | Prev | Rst |
| T0 复归期 | `ResetDo` + `Ss` | Rst | Out(0) | Out(0) |
| T0 复归跳闸 | `Up` + `S0Ss` | Out(0) ⚡ | Out(0) | Out(0) |
| T0 输出 | `OverDo` + `Up` | **Out(0)** ⚡ | Rst | Out(0) |
| T0 输出跳闸 | `S0Ss` | Out(0) | Out(0) | Out(0) |
| T1 复归期 | `ResetDo` + `Up` + `Steps[1]` | **Rst** ⚡ | Out(1) | Out(1) |
| T1 复归跳闸 | `-Steps[1]` + `Up` + `S0Ss + S0St` | **Out(0)** ⚡ | Out(0) | Out(0) |
| T1 输出 | `OverDo` + `Up` | **Out(1)** ⚡ | Rst | Out(1) |
| T1 输出跳闸 | `S0Ss + S0St` | Out(1) | Out(1) | Out(1) |

---

## 模式四：离散补偿阵列 (DcComp)

**所需字段**：`Static`, `Interval`, `Steps`, `PhaseGate`, `DoAction`(可选), `Triggers`

**管线交互**：发送帧 → 死等相角门控 Pg ACK → 响应跳闸 → 继续下一波。
DoAction 延迟到 Pg[0] ACK 时刻才开始计时。

**Teardown 法则**：
- **Pg[0] 前**：波形未输出，不响应跳闸
- **途中跳闸**：挂着直流偏置 → `SsDc` + `Up` + `S0St`
- **跑完跳闸**：DC 已归零 → 仅发 `S0St`

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 首批压入 | `Ss, Steps[0], Pg[0], S0Ss, Steps[1]` | Prev | DC(0) | DC(0) |
| Pg0 前跳闸 | (不响应，死等 ACK) | Prev | DC(0) | DC(0) |
| Pg0 触发 | 硬件翻转 + S0Ss + Steps[1] | **DC(0)** ⚡ | DC(1) | DC(1) |
| Pg0 后跳闸 | `SsDc` + `Up` + `S0St` | **Ss_Base** ⚡ | Ss_Base | Ss_Base |
| Pg1 触发 | 硬件翻转 + Steps[2] | **DC(1)** ⚡ | DC(2) | DC(2) |
| 途中跳闸 | `SsDc` + `Up` + `S0St` | **Ss_Base** ⚡ | Ss_Base | Ss_Base |
| 跑完跳闸 | `S0St` | Ss_Base | Ss_Base | Ss_Base |