# USE 波形引擎 V1 — 旧架构原理 (Legacy)

> ⚠️ **此文档记录 SYS_SYNC 指令引入前的旧架构。新架构已彻底消除 Teardown 逻辑，请参阅 `engine_waveform_v2.md`。**

---

## 核心难题：预装载与跳闸冲突

旧架构中，所有步进帧使用 `DDS_STEP_SHADOW (0x14)` 指令：
```
Base += Step → 结果写入 Shadow
```

**问题**：该指令同时修改了 Base 和 Shadow。一旦步进帧写入，Base 已被永久修改。
如果此时发生跳闸（DI 触发），Shadow 中预装载的下一拍值必须被撤销，否则下一个节点会继承错误的缓冲区状态。

这就是整个 **Teardown（跳闸收尾）** 体系存在的根本原因。

---

## Teardown 工具箱

为了解决预装载冲突，旧架构需要以下编译产物：

| 符号 | 作用 | 底层指令 |
|:-----|:-----|:---------|
| `neg_Steps[N]` | Steps[N] 的反向帧，撤销已写入的步进增量 | 运行时对帧做 I16/I32 补码取反 |
| `S0Ss` | Δ=0 的步进帧，覆盖 Ss 涉及的层级，强制 Shadow = Base | `DDS_STEP_SHADOW(Δ=0)` |
| `S0St` | Δ=0 的步进帧，覆盖步进涉及的层级，强制 Shadow = Base | `DDS_STEP_SHADOW(Δ=0)` |
| `SsDc` | DcComp 专用洗底帧，将 DC 偏置归零到 Ss_Base | `DDS_WR_SHADOW` |

### 为什么需要 S0（Δ=0 步进帧）？

`DDS_STEP_SHADOW` 执行 `Base += Step`，然后将结果写入 Shadow。当 Step=0 时：
- Base 不变（加了 0）
- Shadow = Base（结果写入 Shadow）

这相当于 **强制 Shadow 对齐到 Base**。因为 Ping-Pong Update 后 Shadow 保留了旧值（老 Active 侧的数据），需要 S0 来"洗掉"Shadow 中的脏数据。

---

## 模式一：静态驻留 (Static)

```
Ss + Update → Active = Ss_Base
等待触发...
跳闸 → S0Ss（对齐 Shadow）
```

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 初始 | Ss + Up | **Ss_Base** ⚡ | Prev | Ss_Base |
| 跳闸 | S0Ss | Ss_Base | **Ss_Base** | Ss_Base |

**Teardown 代价**：需要 S0Ss 帧来对齐 Shadow。

---

## 模式二：连续扫频 (Sweep)

**核心策略**：超前预装载 + 跳闸反向撤销

```
Tick 0: Ss + Up → Active = Out(0)
预载:   S0Ss + Steps[0] → Shadow = Out(1), Base = Out(1)
等 Interval...
  跳闸? → neg_Steps[0] 撤销 → Shadow/Base 回退到 Out(0)
正常:   Up → Active = Out(1)
预载:   Steps[1] → Shadow = Out(2), Base = Out(2)
```

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| Tick 0 | Ss + Up | **Out(0)** ⚡ | Prev | Out(0) |
| 预载 T1 | S0Ss + Steps[0] | Out(0) | Out(1) | Out(1) |
| T0 跳闸 | **-Steps[0]** | Out(0) | Out(0) | Out(0) |
| Tick 1 | Up | **Out(1)** ⚡ | Out(0) | Out(1) |
| 预载 T2 | Steps[1] | Out(1) | Out(2) | Out(2) |
| T1 跳闸 | **-Steps[1]** | Out(1) | Out(1) | Out(1) |

**Teardown 代价**：
- 每拍需要预编译 `neg_Steps[N]`（反向帧）
- 首拍需要 `S0Ss` 清洗 Ping-Pong 残留
- `NegStepFrame()` 运行时做补码取反 + 重算 XOR 校验

---

## 模式三：含复归脉冲扫频 (Reset)

每个 Tick 包含两个子阶段，利用 Ping-Pong 自然交替 Rst/Out 状态：

```
          ResetDo              OverDo            ResetDo
     ─────┤ 复归期 ├──────────┤ 输出期 ├──────────┤ 复归期 ├───
```

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 初始 | SsRs + Up | **Rst** ⚡ | Prev | Rst |
| T0 复归 | ResetDo + Ss | Rst | Out(0) | Out(0) |
| T0 复归跳闸 | Up + **S0Ss** | Out(0) | Out(0) | Out(0) |
| T0 输出 | OverDo + Up | **Out(0)** ⚡ | Rst | Out(0) |
| T0 输出跳闸 | **S0Ss** | Out(0) | Out(0) | Out(0) |
| T1 复归 | ResetDo + Up + Steps[1] | **Rst** ⚡ | Out(1) | Out(1) |
| T1 复归跳闸 | **-Steps[1] + Up + S0Ss + S0St** | Out(0) | Out(0) | Out(0) |
| T1 输出 | OverDo + Up | **Out(1)** ⚡ | Rst | Out(1) |
| T1 输出跳闸 | **S0Ss + S0St** | Out(1) | Out(1) | Out(1) |

**Teardown 代价（最复杂的模式）**：
- 复归期跳闸：需 neg_Steps 撤销 + Update + S0Ss + S0St（4 步操作）
- 输出期跳闸：需 S0Ss + S0St
- 首拍 vs 后续拍的 Teardown 规则不同（有无 S0St 取决于是否已步进）
- Teardown 逻辑必须判断"跳闸发生在复归期还是输出期"

---

## 模式四：离散补偿阵列 (DcComp)

```
首批突发: Ss + Steps[0] + Pg[0] + S0Ss + Steps[1]
死等 Pg[0] ACK → 硬件相角触发 Update
后续: Pg[N] + Steps[N+1] → 死等 ACK
跑完: 等待触发
```

| 阶段 | 指令 | Active | Shadow | Base |
|:-----|:-----|:-------|:-------|:-----|
| 首批压入 | Ss+Steps[0]+Pg[0]+S0Ss+Steps[1] | Prev | DC(0) | DC(0) |
| Pg0 触发 | 硬件 Update | **DC(0)** ⚡ | DC(1) | DC(1) |
| Pg0 后跳闸 | **SsDc + Up + S0St** | Ss_Base | Ss_Base | Ss_Base |
| Pg1 触发 | 硬件 Update | **DC(1)** ⚡ | DC(2) | DC(2) |
| 途中跳闸 | **SsDc + Up + S0St** | Ss_Base | Ss_Base | Ss_Base |
| 跑完跳闸 | **S0St** | Ss_Base | Ss_Base | Ss_Base |

**Teardown 代价**：
- 需要预编译 `SsDc` 洗底帧（Static + Σ Steps 的累加归零值）
- 途中跳闸 vs 跑完跳闸的 Teardown 规则不同
- Pg[0] 前跳闸不响应（死等 ACK）

---

## 旧架构的根本缺陷

1. **编译产物膨胀**：每个节点需要 neg_Steps、S0Ss、S0St、SsDc 等额外帧集合
2. **Teardown 分支爆炸**：每种模式的每个阶段有不同的跳闸收尾规则
3. **Base 被污染**：`DDS_STEP_SHADOW` 永久修改 Base，跳闸时必须反向修复
4. **运行时计算**：`NegStepFrame()` 需要运行时补码取反 + 重算校验和
5. **难以维护**：Reset 模式的复归期/输出期跳闸规则极其容易出错
