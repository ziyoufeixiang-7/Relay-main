# Sweep 扫频实验技术说明

> **Handler**: `api/ACTestHdl.py`
> **模块**: `ac_test` / `dc_test` / `harmonic_test` / `steps_gradient_test` / `acdc_test`

---

## 1. 波形输出模型

每个通道的波形由 DDS 多层叠加生成：

```
通道输出 = Σ Layer[i].amplitude × sin(Layer[i].freq × t + Layer[i].phase)
```

| 层级 | 用途 | 静态参数 [amp, phase/freq] |
|------|------|--------------------------|
| Layer 0 | 载波（DC 偏置 + 基波频率） | `[dc_offset, frequency]` |
| Layer 1 | 基波 AC 分量 | `[amplitude, phase]` |
| Layer 2+ | 谐波分量 | `[amplitude, phase]` |

扫频时，`steps` 定义每步增量，叠加在 `statics` 上：

```
tick N 的输出 = statics[ch][layer] + steps[ch][layer] × N
```

---

## 2. 十种场景完整波形

以过流保护测试为例：`statics = {ch0.L1: [5A, 0°]}`，`steps = {ch0.L1: [0.5A, 0°]}`，`count = 20`

### 场景 1：始→终 + 动作返回（无复归）

```
changeMode=0, returnMode=0, enableStepReset=false

电流
 ↑
 │               ╱ DI变位(动作!)
 │             ╱ │
 │           ╱   ↓ 停止
 │     ╱╱╱╱╱
 │───╱──────────────→ tick
   5A  每步+0.5A

Node A: statics=[5A,0°], step=[+0.5A,0°], count=20
Triggers: DI_MATCH → 0xFFFF, COUNT_OVER → 0xFFFF
DO: overMask (进入时设置)
```

引擎行为：正向阶梯扫频，DI 变位立即终止。

---

### 场景 2：始→终 + 全程变化（无复归）

```
changeMode=0, returnMode=1, enableStepReset=false

电流
 ↑
 │                         ╱ 峰顶(count步)
 │         DI变位────╱      │
 │     (记录动作值)╱         ↓ 停止
 │     ╱╱╱╱╱╱╱╱╱╱╱╱╱╱
 │───╱──────────────────────→ tick
   5A

Node A: statics=[5A,0°], step=[+0.5A,0°], count=20
Triggers: COUNT_OVER → 0xFFFF   (无 DI_MATCH 触发器)
DO: overMask
```

引擎行为：扫完全部 count 步才终止。DI 变位由 HDL 在 `di_change` 事件中记录 tripTime，但不停止扫频。

---

### 场景 3：始→终→始 + 动作返回（无复归）

```
changeMode=1, returnMode=0, enableStepReset=false

电流
 ↑
 │         ╱ DI变位(动作!)
 │       ╱ │
 │     ╱   │ [Hold] HDL 注入 Node R
 │   ╱     │                     DI恢复(返回!)
 │ ╱       │                 ╲ │
 │╱────────┤──────────────────╲┤──→ tick
 5A   Node A  Node H→Node R    ↓ 停止

Node A: step=[+0.5A,0°], count=20
  Triggers: DI_MATCH → holdId, COUNT_OVER → holdId
Node H: 空节点, static={}, triggers=[]
  → HDL 收到 trigger(next_id=holdId):
    1. peakTick = lastTick
    2. 构建 Node R: static=峰值全通道, step=[-0.5A,0°], count=peakTick
    3. hot_update 注入 Node R
    4. manual_trig 跳转到 Node R
Node R: step=[-0.5A,0°], count=peakTick
  Triggers: DI_MATCH(0x500) → 0xFFFF, COUNT_OVER → 0xFFFF
```

**Node R 的 static 计算**：
```python
fullPeak = copy.deepcopy(statics)              # 全部原始通道
peakStepped = PhysicsAt(peakTick)              # 步进通道的峰值
fullPeak[ch][l] = peakStepped[ch][l]           # 覆盖步进通道
peakReg = PhysToReg(fullPeak)                  # 转寄存器值
```

**DI_MATCH(0x500) 含义**：
- bit8 = AND 模式
- bit10 = 恢复极性（检测 DI 从动作态恢复到进入节点时的基准态）

---

### 场景 4：始→终→始 + 全程变化（无复归）

```
changeMode=1, returnMode=1, enableStepReset=false

电流
 ↑
 │                    ╱╲ 峰顶
 │    DI变位────╱      ╲
 │  (记录动作值)         ╲────DI恢复
 │           ╱              ╲  (记录返回值)
 │         ╱                  ╲
 │───────╱────────────────────╲──→ tick
   5A   正向全程              反向全程  → 停止

Node A: Triggers: COUNT_OVER → holdId  (无 DI_MATCH)
Node R: Triggers: COUNT_OVER → 0xFFFF  (无 DI_MATCH)
```

引擎行为：正向扫完全部 count 步 → hold → HDL 注入反向 Node R（count=peakTick 步）→ 反向扫完 → 终止。

DI 变位仅由 HDL 在 `di_change` 事件中记录，不影响引擎扫频进程：
- ACTION 阶段第一次 DI 变位 → tripTime / tripVals
- RETURN 阶段第一次 DI 变位 → returnTime / returnVals

---

### 场景 5-6：始→终 + 单程/全程复归

```
changeMode=0, returnMode=0, enableStepReset=true

电流
 ↑                    DI变位(动作!)
 │  ╱╲  ╱╲  ╱╲  ╱╲  ╱│
 │ ╱  ╲╱  ╲╱  ╲╱  ╲╱  ↓ 停止
 │╱─────────────────────→ tick
   复归 输出 复归 输出

每步时序:
  ┤ resetDo ├────┤ overDo ├
  ← resetTime →  ← stepTime →
  输出 resetData  输出 statics+N×step
```

Node A 使用引擎 Reset 模式：
```python
n_a["dynamic"]["reset"] = {
    "time": stepResetTime,
    "static": regReset,        # resetTableData 寄存器值
    "resetDo": doMask,         # 复归期 DO
    "overDo": overMask         # 输出期 DO = doMask ^ doCtrlMask
}
```

> Node A 在 Reset 模式下不设置 `doActions`（由 resetDo/overDo 控制 DO 切换）

---

### 场景 7：始→终→始 + 动作返回 + 单程复归

```
changeMode=1, returnMode=0, enableStepReset=true, stepResetMode=0

电流
 ↑               DI变位
 │  ╱╲ ╱╲ ╱╲ ╱╲ ╱│
 │ ╱ ╲╱ ╲╱ ╲╱ ╲╱  [Hold] → 注入 Node R
 │╱                │ 反向连续(无复归)
 │                 ╲─────────────→ tick
   正向(有复归)       反向(无复归)
```

**单程复归关键**：Node R **不带** reset 块，反向连续扫频。

---

### 场景 8：始→终→始 + 动作返回 + 全程复归

```
changeMode=1, returnMode=0, enableStepReset=true, stepResetMode=1

电流
 ↑               DI变位
 │  ╱╲ ╱╲ ╱╲ ╱╲ ╱│
 │ ╱ ╲╱ ╲╱ ╲╱ ╲╱  [Hold] → 注入 Node R
 │╱                │╲ ╱╲ ╱╲ ╱╲
 │                 ╲╱ ╲╱ ╲╱ ╲╱ ╲─→ tick
   正向(有复归)      反向(也有复归)
```

**全程复归关键**：Node R **带** reset 块（与 Node A 相同的 resetTime/resetData/resetDo/overDo）。

```python
# 仅当 stepResetMode=1 且 enableStepReset=true 时
n_r["dynamic"]["reset"] = {
    "time": stepResetTime,
    "static": regReset,
    "resetDo": doMask,
    "overDo": overMask
}
```

---

### 场景 9-10：全程变化 + 复归

与场景 7-8 相同的复归行为，但触发器改为纯 COUNT_OVER（无 DI_MATCH）。

---

## 3. 实验前复归

可选的 Node P，在正式扫频前输出复归波形：

```python
{
    "id": 0,
    "doActions": [{"delay": 0, "mask": doMask}],   # 复归期 DO
    "static": regReset,                              # resetTableData
    "dynamic": {"interval": preTestResetTime, "count": 1, "step": {}},
    "triggers": [{"condition": "COUNT_OVER", "nextId": actionId}]
}
```

纯静态节点（step 为空），持续 preTestResetTime 毫秒后自动跳转到 Node A。

---

## 4. 手动模式

```python
{
    "id": 0,
    "doActions": [{"delay": 0, "mask": doMask}],
    "static": regStatics,
    "triggers": [
        {"condition": "DI_MATCH", "diMask": logicMask | 0x200, "nextId": -1},
        {"condition": "TIME_OUT", "timeout": 150000, "nextId": -1}
    ]
}
```

- `0x200`（bit9）= 使用进入节点时的 DI 作为变位基准
- 前端发送 `update_static` → HDL 重编译 Node 0 → hot_update + manual_trig 跳回自身
- 150 秒超时安全保护

---

## 5. 动作/返回时间测量

### 5.1 DI 变位检测（`_OnDiChange`）

```
时间线:
  ──┤ tick N: Update ACK (hw_ts₁) ├──────┤ DI 变位 (hw_ts₂) ├──
                                          tripTime = (hw_ts₂ - hw_ts₁) / 1000 ms
```

HDL 在 `di_change` 事件中计算：
- `lastValueTs` = 最后一次 SYS_UPDATE ACK 的 FPGA 时间戳
- `lastDiTs` = DI 变位时的 FPGA 时间戳（已扣除去抖延迟）
- `tripTime = (lastDiTs - lastValueTs) / 1000.0` ms

### 5.2 相位守卫

| 条件 | 行为 |
|------|------|
| `_lastDi == -1` (初始态) | 忽略（初始 DI 读取，不算动作） |
| `_lastDi == new_di` | 忽略（重复 DI，无变位） |
| `_lastValueTs == 0` | 忽略（还没有收到过 value_update） |
| `changed & logicMask == 0` | 忽略（变位的通道不在监控范围） |
| ACTION 阶段 + `tripTime is None` | 记录为**动作** |
| RETURN 阶段 + `returnTime is None` | 记录为**返回** |

### 5.3 Trigger 兜底

当 DI 变位恰好与 trigger 同时到达时，`_OnTrigger` 中有兜底逻辑：

```python
if cond == "DI_MATCH" and self._phase == "ACTION" and self._tripTime is None:
    self._tripTime = (lastDiTs - lastValueTs) / 1000.0
```

---

## 6. Node R 注入流程

当 `_OnTrigger` 收到 `next_id == holdId`（Node A 结束跳到 Node H）：

```
1. peakTick = lastTick                           # 正向最后一步
2. fullPeak = deepcopy(statics)                  # 全通道基底
3. peakStepped = PhysicsAt(peakTick)             # 步进通道峰值
4. fullPeak 覆盖步进通道                          # 完整峰值静态
5. peakReg = PhysToReg(fullPeak)                 # 转寄存器
6. Node R = {static: peakReg, step: -regSteps, count: peakTick}
7. 如果全程复归: Node R 加 reset 块
8. 发送 internal_hot_update(nodes=[Node R])       # 注入到引擎
9. 发送 internal_manual_trig(returnId)             # 从 H 跳转到 R
```

---

## 7. DO 掩码控制

```python
overMask = doMask ^ doCtrlMask
```

| 场景 | DO 值 |
|------|-------|
| 实验前复归 Node P | doMask |
| 正向/反向无复归扫频 | overMask（进入节点时设置） |
| 步进复归 - 复归期 | resetDo = doMask |
| 步进复归 - 输出期 | overDo = overMask |

> **Teardown 补偿**：如果引擎在复归期被打断（DI 触发），`_RunReset` 会在 teardown 前先发送 OverDo 帧恢复 DO 到输出态。

---

## 8. 遥测推送

HDL 在 `_OnValueUpdate` 中根据 tick 和 phase 计算物理值：

```python
# ACTION 阶段
amp = statics[ch][l][0] + steps[ch][l][0] * tick

# RETURN 阶段
amp = (statics[ch][l][0] + steps[ch][l][0] * peakTick) - steps[ch][l][0] * tick

# 复归阶段 (tick=-1)
amp = resetTableData[ch][l][0]
```

| tick 值 | 含义 | 推送内容 |
|---------|------|---------|
| ≥ 0 | 正常输出步 | 计算后的物理值 |
| -1 | 复归期 | resetTableData 物理值 |
| -2 | 静态更新 | 上一步物理值 |

---

## 9. 报告

```python
{
    "tripTime":     float,  # 动作时间 (ms)
    "tripValues":   dict,   # 动作时物理值 {ch: {l: [amp, phase]}}
    "returnTime":   float,  # 返回时间 (ms)，仅 changeMode=1
    "returnValues": dict,   # 返回时物理值
    "returnRatio":  float   # 返回系数 = 返回值主通道/动作值主通道
}
```

报告在 `terminal` 事件到达时生成（引擎进入 0xFFFF 节点后触发）。

---

## 10. 节点 ID 分配

| 有实验前复归 | P | A | H | R |
|:---:|:---:|:---:|:---:|:---:|
| 否 | — | 0 | 1 | 2 |
| 是 | 0 | 1 | 2 | 3 |

终止节点 ID = 0xFFFF（`nextId: -1`，编译后 `-1 & 0xFFFF = 0xFFFF`）。
