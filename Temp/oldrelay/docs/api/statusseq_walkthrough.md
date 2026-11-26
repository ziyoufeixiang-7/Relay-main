# StatusSequence 状态序列试验技术说明

> **模块**: `universal_sequence`
> **Handler**: `api/StatusSeqHdl.py`
> **继保用途**: 自定义多状态波形序列，用于模拟复杂保护工况并采集继电器逐状态动作时序

---

## 1. 继电保护背景

### 1.1 什么是状态序列测试？

状态序列是继电保护测试中最**通用**的测试模式，允许操作员自由编排 N 个状态节点。每个节点可配置独立的：
- **波形输出**（电压/电流幅值、相位、频率）
- **DO 开出动作**（模拟断路器合闸/分闸）
- **触发条件**（超时、DI 变位、手动按键、步进完成）
- **扫频参数**（可选，每步递增/递减幅值）

### 1.2 典型应用场景

| 场景 | 状态序列编排 | 继保含义 |
|------|-------------|---------|
| **距离保护** | 故障前(额定)→故障态(短路)→跳闸后(零态) | 模拟电力系统短路→保护装置动作→断路器跳闸 |
| **重合闸** | 故障→跳闸→无压检测→合闸→重合→永久故障→最终跳闸 | 模拟自动重合闸完整时序 |
| **差动保护** | 稳态→内部故障(差流)→保护动作 | 测试差动保护灵敏度 |
| **频率保护** | 额定频率→频率缓降→欠频动作 | 测试低频减载装置 |

### 1.3 与 ACTest 的区别

| 特性 | ACTest | StatusSequence |
|------|--------|---------------|
| 用途 | 单项保护特性测量(动作值/返回值) | 多状态复杂工况仿真 |
| 节点数 | 固定 2-3 个(正向+反向) | 用户自定义 N 个 |
| 报告 | 动作值/返回值/返回系数 | 每个节点的持续时间 + 8路 DI 动作时间 |
| 扫频 | 所有步进通道统一 | 每个节点独立配置 |

---

## 2. 前端参数详解

### 2.1 系统参数 (`sys`)

| 参数 | 含义 | 继保意义 |
|------|------|----------|
| `debounce` | 硬件去抖动(ms) | FPGA 级 DI 滤波，消除机械触点抖动 |

### 2.2 节点参数 (`nodes[]`)

| 参数 | 类型 | 含义 | 继保意义 |
|------|------|------|----------|
| `id` | INT | 前端节点 ID (≥1) | 标识各状态，显示在前端 |
| `mode` | STRING | `"STATIC"` / `"SWEEP"` | 静态驻留或步进扫描 |
| `static` | Object | 各通道各层波形参数 | `"ch": {"layer": [amp, freq/phase]}` |
| `do_actions` | Array | DO 输出动作序列 | `{delay(ms), mask}` — 模拟断路器操作 |
| `triggers` | Array | 触发条件列表 | 控制状态间跳转逻辑 |
| `dynamic` | Object | Sweep 参数(可选) | stepTime/count/steps |

### 2.3 触发条件详解

| 条件 | 含义 | 典型使用 |
|------|------|---------|
| `TIME_OUT` | 定时触发 | 故障前额定态持续 N 秒后自动进入故障态 |
| `DI_MATCH` | DI 变位触发 | 等待保护装置出口动作(跳闸/合闸信号) |
| `MANUAL_TRIG` | 手动按键触发 | 操作员手动控制状态切换 |
| `COUNT_OVER` | 步进完成触发 | Sweep 到达目标值后跳转 |

### 2.4 触发参数

| 参数 | 含义 | 说明 |
|------|------|------|
| `timeout` | 超时时间(ms) | TIME_OUT 时为等待时间; DI_MATCH 时为最大限时(0=不限) |
| `di_mask` | DI 逻辑掩码 | 低8位=通道掩码, 高位=逻辑模式 |
| `next_id` | 跳转目标 | 前端节点 ID; `-2`=结束实验 |
| `post_condition_delay_ms` | 触发后延时(ms) | 条件满足后**延时 N ms** 再跳转，用于采集保护动作后的暂态 |

> [!IMPORTANT]
> **post_condition_delay_ms** 是继保测试中非常关键的参数。例如：距离保护动作后，需要延时 50ms 采集断路器分闸过程中的暂态波形。

---

## 3. 内部状态序列设计

### 3.1 ID 映射

前端节点 ID 从 **1** 开始，引擎节点 ID 从 **0** 开始：

```
前端 ID:  1,    2,    3    → 引擎 ID: 0, 1, 2
前端 ID: -2 (结束)          → 引擎 ID: -2 (不变)
```

### 3.2 节点编译流程

```mermaid
graph TD
    FN["前端节点<br/>id=1, mode=SWEEP"] -->|CompileStart| EN["引擎节点<br/>id=0, dynamic={...}"]
    FN -->|post_delay>0| STUB["延时桩节点<br/>id=1000, TIME_OUT"]
    EN -->|DI_MATCH| STUB
    STUB -->|TIME_OUT(delay)| NEXT["下一节点"]
```

**延时桩节点 (Stub Node)**:
- 当 `post_condition_delay_ms > 0` 时，Handler 生成一个 ID=1000+原始ID 的桩节点
- 桩节点 `static={}` (继承前一节点输出)，只有一个 `TIME_OUT` 触发
- 主节点的跳转目标改为桩节点，桩节点超时后再跳转到真实目标

### 3.3 MANUAL_TRIG 实现

手动触发**不生成引擎 trigger**。而是：
1. 编译时记录 `manualTrigTargets[engine_id] = target_id`
2. 前端发送 `next_node` 命令
3. CmdRouter 调用 `useEngine.FireManualTrigger(target_id)`
4. 引擎内部通过 `manualTrigTarget` 属性中断当前等待

### 3.4 Static 补全

所有未配置的通道自动补全为 `[0, freq_50_reg]`（DC 层 0V/50Hz），确保 FPGA 全通道输出一致。

---

## 4. 遥测推送

### 4.1 value_update 翻译

每个 tick 的引擎事件被翻译为前端语义消息：

```python
# tick → 物理值
amp = base_amp + step_amp × tick
freq = base_freq + step_freq × tick
```

**特殊处理**:
- **桩节点的遥测被跳过**（`if nid in self.stubNodeIds: return`）
- **tick=0 进入新状态**: 更新 `currentTracker`，记录 `entry_hw_ts`
- **DI/DO 变化**: 独立推送 DI/DO 消息

### 4.2 DI SOE（Sequence of Events）

每次 DI 变位时，Handler 检测所有 8 路 DI 的边沿变化：

```python
for i in range(8):  # diA, diB, diC, diR, dia, dib, dic, dir
    if bit[i] 首次翻转:
        di_times[DI_NAMES[i]] = (hw_ts - entry_hw_ts) / 1_000_000  # 秒
```

> [!NOTE]
> 只记录**首次**翻转时间。如果 DI 多次变化，只保留第一次。这是因为继保测试关注的是保护装置的**首次动作时间**。

### 4.3 trigger 翻译

- 跳转到桩节点: 记录 `end_condition` 但**不转发前端**
- 跳转到真实节点: 翻译 engine ID → 前端 ID 并转发

---

## 5. 报告生成机制

### 5.1 触发条件

`terminal` 事件 → 遍历所有已进入的 tracker → 生成报告

### 5.2 持续时间计算

```
duration = 下一个节点的 entry_hw_ts - 当前节点的 entry_hw_ts
# 最后一个节点:
duration = lastValueUpdate_hw_ts - entry_hw_ts
```

优先使用 **hw_ts**（µs 精度），如果 hw_ts 无效则回退到 Python `time.monotonic()`。

### 5.3 报告字段

```json
{
  "rows": [
    {
      "id": 1,                     // 前端节点 ID
      "duration_sec": 1.25,        // 持续时间(秒)
      "diA": 0.012,                // DI-A 动作时间(秒)，null=未动作
      "diB": null, "diC": 0.045, "diR": null,
      "dia": null, "dib": 0.033, "dic": null, "dir": null,
      "end_condition": "TIME_OUT"  // 该节点退出原因
    }
  ]
}
```

---

## 6. 关键代码路径

```
前端 start → CmdRouter.HandleCommand
  → StatusSeqHdl.CompileStart
    → 遍历 nodes[]
      → _PhysToRegDict          # 物理值 → 寄存器
      → FillMissingChannels     # 补全未配置通道
      → Anti-Tear 处理          # 步进层基值非零
      → 延时桩节点生成          # post_condition_delay_ms
    → ApiHdlMgr.JsonNodesToFlat # JSON → FlatCodec

引擎运行中:
  USEEngine → EvtValueUpdate → CmdRouter → StatusSeqHdl.InterceptWsEvent
    → tick=0: 更新 NodeTracker
    → tick>0: 反算物理值推送
    → DI 变位: 记录 SOE 时间

手动触发:
  前端 next_node → CmdRouter → useEngine.FireManualTrigger(target_id)

terminal:
  → 计算每个节点 duration
  → 收集 DI SOE 数据
  → 生成 rows[] 报告
```
