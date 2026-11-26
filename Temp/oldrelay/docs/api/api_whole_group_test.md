# 整组试验 (Whole Group Test) API 协议

> **模块标识**: `"module": "whole_group_test"`  
> **Handler 文件**: `api/WholeGroupTestHdl.py`  
> **MODULE_KEYS**: `["whole_group_test"]`

---

## 1. 实验概述

整组试验模拟完整的继电保护动作流程，通过多节点状态序列实现「故障前 → 第一次故障 → 故障转换（可选）→ 结束」的完整闭环。前端负责整组相量计算并写入 `nodes[].static`，后端负责状态机执行、时间测量和开入/开出控制。

```
故障前 (Node 0) ──触发──→ 第一次故障 (Node 1) ──[转换]──→ 转换后故障 (Node 2) ──→ 结束 (-2)
```

**关键特征**：
- 节点 `static` 由前端计算写入（含电压、电流、频率），后端**不做相量计算**
- 故障前触发方式支持：按键触发 (`MANUAL_TRIG`)、时间触发 (`TIME_OUT`)、开入触发 (`DI_MATCH`)
- 开出控制支持 3 种模式：跟随跳合闸、延时翻转、自定义控制
- 报告返回原始时间 (ms)，**后加速/重合延时由前端推导**

---

## 2. 前端 → 后端：启动命令

### 2.1 请求示例

```json
{
  "module": "whole_group_test",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15,
      "doMask": 0,
      "logicMask": 7,
      "doCtrlMask": 0
    },
    "nodes": [
      {
        "id": 0,
        "mode": "STATIC",
        "static": { "0": { "0": [0, 50], "1": [57.735, 0] } },
        "do_actions": [{ "delay": 0, "mask": 0 }],
        "triggers": [{ "condition": "MANUAL_TRIG", "next_id": 1, "post_condition_delay_ms": 0 }],
        "limits": {}
      },
      {
        "id": 1,
        "mode": "STATIC",
        "static": { "0": { "0": [0, 50], "1": [30.0, -40.89] } },
        "do_actions": [{ "delay": 0, "mask": 3 }],
        "triggers": [
          { "condition": "DI_MATCH", "diMask": 7, "next_id": -2 },
          { "condition": "TIME_OUT", "timeout": 5000, "next_id": -2 }
        ],
        "limits": {}
      }
    ],
    "payload": {
      "faultSettings": {
        "firstFault": {},
        "faultNature": 0,
        "useRX": false,
        "transition": {
          "enabled": false,
          "transitionMoment": 0,
          "transitionTimeMs": 100,
          "afterTransition": {}
        },
        "testLimitMs": 5000
      },
      "triggerMode": {
        "firstFault": 0,
        "prefaultTrigger": {
          "condition": "MANUAL_TRIG",
          "next_id": 1,
          "post_condition_delay_ms": 0
        }
      },
      "testControl": {},
      "binaryInput": {
        "settings": [
          { "id": "A", "name": "开入 A", "function": 0 },
          { "id": "B", "name": "开入 B", "function": 1 },
          { "id": "C", "name": "开入 C", "function": 2 },
          { "id": "R", "name": "开入 R", "function": 4 },
          { "id": "a", "name": "开入 a", "function": 5 },
          { "id": "b", "name": "开入 b", "function": 5 },
          { "id": "c", "name": "开入 c", "function": 5 },
          { "id": "r", "name": "开入 r", "function": 5 }
        ]
      },
      "binaryOutput": {
        "controlMode": 0,
        "initialMask": 3
      },
      "uiOutput": {
        "voltageMode": 0,
        "currentMode": 0,
        "uxSetting": 0,
        "needUx": false,
        "uxAfterTrip": { "amplitude": 0.0, "angle": 0.0 }
      }
    }
  }
}
```

### 2.2 `params` 顶层字段

| 路径 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sys` | Object | 是 | 系统配置（防抖、开出掩码、逻辑掩码） |
| `nodes` | NodeItem[] | 是 | 节点列表，`id` 从 0 递增，与状态序列同构 |
| `payload` | Object | 推荐 | 业务参数全集，见 §2.4 |

### 2.3 `sys` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `debounce` | INT | 开入防抖确认时间 (ms) |
| `doMask` | INT | 开出初始状态位掩码 |
| `doCtrlMask` | INT | 开出动作控制位掩码 |
| `logicMask` | INT | DI 逻辑掩码 (用于 DI_MATCH 条件) |

### 2.4 `nodes` 节点约定

| 节点 ID | 角色 | static | do_actions | triggers |
|---------|------|--------|------------|----------|
| `0` | 故障前 | 故障前相量 | `delay:0, mask: initialMask` | 由 `triggerMode` 决定 |
| `1` | 第一次故障 | 故障相量 | 完整开出时序 | DI_MATCH / TIME_OUT |
| `2` (可选) | 故障转换后 | 转换后相量 | 转换后开出 | `next_id: -2` 结束 |

**节点字段格式** (与状态序列/引擎协议完全相同)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 节点唯一 ID |
| `mode` | STRING | `"STATIC"` |
| `static` | Object | 通道输出值 `{ch: {layer: [amp, phase/freq]}}` |
| `do_actions` | DoItem[] | `[{delay, mask, holdTime?}]` 开出动作序列 |
| `triggers` | TriggerItem[] | 触发条件列表 |
| `limits` | Object | 通道限幅 |

### 2.5 `payload` 字段详解

#### 2.5.1 `faultSettings` — 故障参数

| 字段 | 类型 | 说明 |
|------|------|------|
| `firstFault` | Object | 第一次故障参数（类型、方向、电流、阻抗等） |
| `faultNature` | INT | 故障性质：`0`=瞬时性, `1`=永久性 |
| `useRX` | BOOL | 是否采用 R/X 界面选项 |
| `transition.enabled` | BOOL | 是否启用故障转换 |
| `transition.transitionMoment` | INT | 转换时刻：`0`=第一次故障后, `1`=重合闸后 |
| `transition.transitionTimeMs` | INT | 转换时间 (ms) |
| `transition.afterTransition` | Object | 转换后故障参数 |
| `testLimitMs` | INT | 试验限时 (ms) |

#### 2.5.2 `triggerMode` — 触发配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `firstFault` | INT | 触发方式：`0`=按键, `1`=时间, `2`=开入 |
| `prefaultTrigger.condition` | STRING | `"MANUAL_TRIG"` / `"TIME_OUT"` / `"DI_MATCH"` |
| `prefaultTrigger.next_id` | INT | 触发后跳转节点 (通常为 `1`) |
| `prefaultTrigger.post_condition_delay_ms` | INT | 触发后延时 (ms) |
| `prefaultTrigger.timeout` | INT | 仅 TIME_OUT：故障前时间 (ms) |
| `prefaultTrigger.di_mask` | INT | 仅 DI_MATCH：逻辑掩码 |

> `prefaultTrigger` 与 `nodes[0].triggers[0]` 同源，后端以 `condition` 字段为准。

#### 2.5.3 `binaryInput` — 开入配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `settings` | Array[8] | 8路开入有序列表 (下标 0-3=A/B/C/R, 4-7=a/b/c/r) |
| `settings[].id` | STRING | 开入标识 (`"A"`, `"B"`, ..., `"r"`) |
| `settings[].name` | STRING | 显示名称 |
| `settings[].function` | INT | 开入功能枚举 (见 §2.6.3) |

#### 2.5.4 `binaryOutput` — 开出配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `controlMode` | INT | 控制方式：`0`=跟随跳合闸, `1`=延时翻转, `2`=自定义 |
| `initialMask` | INT | 起始开出位图 (bit0→开出1, bit1→开出2) |
| `delayReversal` | Object | 仅 `controlMode=1`：`{delay, hold}` (ms) |
| `doSequence` | DoItem[] | 仅 `controlMode=1/2`：开出时序 `[{delay, mask, holdTime?}]` |

#### 2.5.5 `uiOutput` — 输出方式

| 字段 | 类型 | 说明 |
|------|------|------|
| `voltageMode` | INT | 电压输出组：`0`=第1组abc, `1`=第2组xyz |
| `currentMode` | INT | 电流输出组：`0`=第1组abc, `1`=第2组xyz, `2`=并联 |
| `uxSetting` | INT | Ux 设置 (见 §2.6.5) |
| `needUx` | BOOL | 是否输出 Ux |
| `uxAfterTrip` | Object | 跳闸后 Ux：`{amplitude, angle}` |

### 2.6 枚举值对照

#### 2.6.1 故障类型 (`faultSettings.firstFault.type`)

| 值 | 含义 |
|----|------|
| `0` | A 相接地 |
| `1` | B 相接地 |
| `2` | C 相接地 |
| `3` | AB 相间 |
| `4` | BC 相间 |
| `5` | CA 相间 |
| `6` | AB 两相接地 |
| `7` | BC 两相接地 |
| `8` | CA 两相接地 |
| `9` | 三相短路 |

#### 2.6.2 故障方向

| 值 | 含义 |
|----|------|
| `0` | 正向故障 |
| `1` | 反向故障 |

#### 2.6.3 开入功能 (`binaryInput.settings[].function`)

| 值 | 含义 |
|----|------|
| `0` | 跳 A 接点 |
| `1` | 跳 B 接点 |
| `2` | 跳 C 接点 |
| `3` | 三跳接点 |
| `4` | 重合闸接点 |
| `5` | 其他信号 |

#### 2.6.4 开出控制方式 (`binaryOutput.controlMode`)

| 值 | 含义 |
|----|------|
| `0` | 开出跟随跳合闸变化 |
| `1` | 故障触发后延时翻转 |
| `2` | 自定义控制 |

#### 2.6.5 Ux 设置 (`uiOutput.uxSetting`)

| 值 | 含义 |
|----|------|
| `0` | +3U0 |
| `1` | -3U0 |
| `2` | +√3×3U0 |
| `3` | -√3×3U0 |
| `4`-`6` | 检同期 A / B / C |
| `7`-`9` | 检同期 AB / BC / CA |

### 2.7 前端仅用，不下发的字段

| 字段 | 说明 |
|------|------|
| `ctPolarity` | CT 极性正方向 (前端计算用) |
| `calcModel` | If/KL/Zs/Ue/负荷电流/频率等 (前端相量计算用，结果写入 `static`) |
| 后加速/重合延时 | 由前端根据 §4.2 公式推导 |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 停止实验

```json
{
  "module": "whole_group_test",
  "cmd": "stop"
}
```

### 3.2 按键触发 (故障前 → 第一次故障)

```json
{
  "module": "whole_group_test",
  "cmd": "next_node"
}
```

> 仅在 `triggerMode.firstFault = 0` (按键触发) 时有效。

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "whole_group_test",
  "type": "value_update",
  "nodeId": 1,
  "static": { "0": { "1": [30.0, -40.89] } },
  "di": 5,
  "do": 3
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `nodeId` | INT | 当前节点 ID |
| `static` | Object | 当前通道输出 (可选) |
| `di` | INT | DI 位图 (可选, 变化时发送) |
| `do` | INT | DO 位图 (可选, 变化时发送) |

### 4.2 测试报告

```json
{
  "module": "whole_group_test",
  "type": "report",
  "data": {
    "rows": [
      {
        "index": 1,
        "tripA": 120.5,
        "tripB": 118.0,
        "tripC": 119.0,
        "recloseR": 450.0,
        "diA": 1, "diB": 0, "diC": 0, "diR": 1,
        "dia": 0, "dib": 0, "dic": 0, "dir": 0
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rows[]` | Array | 多行结果 |
| `tripA` / `tripB` / `tripC` | FLOAT \| null | 跳闸时间 (ms) |
| `recloseR` | FLOAT \| null | 重合闸时间 (ms) |
| `diA` ... `dir` | INT | 开入最终状态 (0/1) |

**前端推导公式** (单位均为 ms)：

- **重合延时** = `recloseR − (max(tripA, tripB, tripC) + 断路器跳闸延时 + 开入防抖)`
- **后加速** = `min(tripA, tripB, tripC) − (上一次recloseR + 断路器合闸延时 + 开入防抖)`
  - 首行「上一次 recloseR」取 `0`

> 断路器延时、开入防抖取自界面当前配置（与下发 `start` 时一致）。

### 4.3 实验结束

```json
{
  "module": "whole_group_test",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "whole_group_test",
  "type": "error",
  "msg": "错误描述"
}
```

---

## 5. 编译映射规则

整组试验的 `nodes` 由前端直接构建，后端 Handler 透传或做最小化转换。

### 5.1 节点生成

| 引擎 Node | 对应前端状态 | 触发条件 |
|-----------|-------------|----------|
| Node 0 | 故障前 | `MANUAL_TRIG` / `TIME_OUT` / `DI_MATCH` → Node 1 |
| Node 1 | 第一次故障 | `DI_MATCH` → Node 2 或 -2; `TIME_OUT(testLimitMs)` → -2 |
| Node 2 (可选) | 转换后故障 | `DI_MATCH` → -2; `TIME_OUT(testLimitMs)` → -2 |

### 5.2 特殊处理

- **`static` 透传**: 前端已将相量计算结果写入 `nodes[].static`，后端仅做物理值→寄存器值转换
- **开出时序**: `do_actions` 由前端根据 `binaryOutput.controlMode` 生成完整时序
- **故障转换**: 当 `transition.enabled=true` 时，Node 1 的 `TIME_OUT` 为转换延时，否则为试验限时
- **`triggerMode` 与 `nodes[0].triggers[0]` 同源**: 后端以 `nodes[0].triggers[0].condition` 为准
