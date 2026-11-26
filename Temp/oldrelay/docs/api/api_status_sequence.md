# 状态序列 (Universal Sequence) API 协议

> **模块标识**: `"module": "universal_sequence"`  
> **Handler 文件**: `api/StatusSeqHdl.py` (待实现)  
> **MODULE_KEYS**: `["universal_sequence"]`

---

## 1. 实验概述

状态序列实验支持用户自由编排 **N 个状态节点**（N ≥ 1），每个节点可配置独立的波形输出、DO 动作、触发条件和扫频动态参数。节点按 `next_id` 链式跳转，末状态 `next_id = -2` 表示实验结束。

```
状态 1 ──→ 状态 2 ──→ ... ──→ 状态 N ──→ 实验结束 (next_id = -2)
```

- 每个节点可选 **STATIC** (静态驻留) 或 **SWEEP** (扫频) 模式
- 每个节点独立配置 `static`、`limits`、`triggers`、`do_actions`、`dynamic`
- 通道索引 `"0"` ~ `"11"` 对应 Ua…Uz (0~5), Ia…Iz (6~11)

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "universal_sequence",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 10
    },
    "nodes": [
      {
        "id": 1,
        "mode": "SWEEP",
        "do_actions": [
          { "delay": 0, "mask": 16 },
          { "delay": 15, "mask": 16 }
        ],
        "static": {
          "0": { "0": [0, 50], "1": [81.65, 0] },
          "1": { "0": [0, 50], "1": [81.65, -120] },
          "2": { "0": [0, 50], "1": [81.65, 120] },
          "6": { "0": [0, 50], "1": [0, 0] }
        },
        "limits": {
          "0": [282.84, 1000],
          "6": [56.57, 1000]
        },
        "triggers": [
          {
            "condition": "TIME_OUT",
            "timeout": 5000,
            "next_id": 2,
            "post_condition_delay_ms": 14
          }
        ],
        "dynamic": {
          "stepTime": 1,
          "count": 100,
          "steps": {
            "0": { "0": [0, 0], "1": [16.97, 0] }
          }
        }
      },
      {
        "id": 2,
        "mode": "STATIC",
        "do_actions": [{ "delay": 0, "mask": 0 }],
        "static": {
          "0": { "0": [0, 50], "1": [81.65, 0] },
          "6": { "0": [0, 50], "1": [0, 0] }
        },
        "limits": {
          "0": [282.84, 1000],
          "6": [56.57, 1000]
        },
        "triggers": [
          {
            "condition": "DI_MATCH",
            "di_mask": 112,
            "timeout": 1000,
            "next_id": -2,
            "post_condition_delay_ms": 14
          }
        ]
      }
    ]
  }
}
```

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.debounce` | INT | 开入防抖时间 (ms) |
| `nodes` | Array | 状态节点列表，N ≥ 1 |

### 2.2 Node 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 前端节点 ID (从 1 开始) |
| `mode` | STRING | `"STATIC"` 或 `"SWEEP"` |
| `do_actions` | Array | DO 输出动作，`{delay(ms), mask(U8)}` |
| `static` | Object | 各通道各层的波形参数，`"通道ID": {"层ID": [幅值, 频率/相位]}` |
| `limits` | Object | 通道限值，`"通道ID": [幅值上限, 频率上限]` |
| `triggers` | Array | 触发条件列表 |
| `dynamic` | Object | (可选) 扫频参数，`mode = "SWEEP"` 时使用 |

### 2.3 Trigger 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `condition` | STRING | 触发条件 (见下表) |
| `timeout` | INT | 超时时间 (ms)。`TIME_OUT` 时为最大持续时间；`DI_MATCH` 时为最大输出限时 (0=不限时) |
| `di_mask` | INT | DI 逻辑组合掩码，`DI_MATCH` 时使用 |
| `next_id` | INT | 跳转目标节点 ID，`-2` 表示结束实验 |
| `post_condition_delay_ms` | INT | 触发条件满足后延时结束 (ms)。`MANUAL_TRIG` 时固定为 `0` |

**condition 可选值：**

| 值 | 含义 |
|----|------|
| `TIME_OUT` | 时间触发 |
| `MANUAL_TRIG` | 按键触发 |
| `DI_MATCH` | 开入接点翻转触发 |
| `COUNT_OVER` | 步进到最终值 |

### 2.4 Dynamic 字段详解 (SWEEP 模式)

| 字段 | 类型 | 说明 |
|------|------|------|
| `stepTime` | INT | 每步时间 (ms) |
| `count` | INT | 最大步数 |
| `steps` | Object | 每步增量，`"通道ID": {"层ID": [幅值增量, 频率/相位增量]}` |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 按键触发（跳转下一状态）

```json
{
  "module": "universal_sequence",
  "cmd": "next_node"
}
```

> 仅当当前节点的触发条件为 `MANUAL_TRIG` 时，前端才会发送此命令。

### 3.2 停止实验

```json
{
  "module": "universal_sequence",
  "cmd": "stop"
}
```

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "universal_sequence",
  "type": "value_update",
  "nodeId": 2,
  "static": {
    "0": { "0": [0, 50], "1": [81.65, 0] },
    "6": { "0": [0, 50], "1": [0, 0] }
  },
  "di": 5,
  "do": 5
}
```

| 字段 | 说明 |
|------|------|
| `nodeId` | 当前执行的前端节点 ID，前端用于选中对应状态页并更新显示 |
| `static` | 各通道瞬时物理值 (非寄存器值) |
| `di` | DI 状态掩码 (仅变化时包含) |
| `do` | DO 状态掩码 (仅变化时包含) |

### 4.2 测试报告

```json
{
  "module": "universal_sequence",
  "type": "report",
  "data": {
    "rows": [
      {
        "id": 1,
        "duration_sec": 1.25,
        "diA": 0.012, "diB": null, "diC": 0.045, "diR": null,
        "dia": null, "dib": 0.033, "dic": null, "dir": null,
        "end_condition": "TIME_OUT"
      },
      {
        "id": 2,
        "duration_sec": 0.80,
        "diA": 0.015, "diB": 0.022, "diC": null, "diR": null,
        "dia": 0.018, "dib": null, "dic": null, "dir": null,
        "end_condition": "DI_MATCH"
      }
    ]
  }
}
```

**report.data.rows[] 字段定义：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 状态节点 ID |
| `duration_sec` | FLOAT | 该状态实际持续时间 (秒) |
| `diA` ~ `diR` | FLOAT/null | 大写开入 A~R 动作相对时间 (秒)，`null` = 未动作 |
| `dia` ~ `dir` | FLOAT/null | 小写开入 a~r 动作相对时间 (秒)，`null` = 未动作 |
| `end_condition` | STRING | 结束条件描述 |

> 各开入时间为进入该状态后的相对时间 (秒)。

### 4.3 实验结束

```json
{
  "module": "universal_sequence",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "universal_sequence",
  "type": "error",
  "msg": "错误描述"
}
```

---

## 5. 编译映射规则

### 5.1 节点生成

前端节点 ID 从 1 开始，引擎节点 ID 从 0 开始。HDL 编译时将前端 `id` 映射为引擎内部 ID (通常 `id - 1`)。

每个前端节点编译为一个引擎 Node Object：
- `static`: 物理值 → 寄存器值 (含 DC 层 0V/50Hz 补全)
- `limits`: 由 HDL 层自行判断，引擎不处理
- `triggers`: `DI_RECOVER` → `DI_MATCH` + `diMask |= 0x400`
- `dynamic.stepTime` → 引擎 `dynamic.interval`
- `dynamic.steps` (前端增量) → 引擎 `dynamic.step` (寄存器增量)

### 5.2 特殊编译逻辑

- **`post_condition_delay_ms`**: 前端的"触发后延时"，需要 HDL 自行在 InterceptWsEvent 中实现延时逻辑
- **`mode = "SWEEP"` + 有 `steps`**: 编译为引擎 `dynamic.step` (均匀增量模式)
- **未配置的通道**: 强制补全为 `[0, freq_50_reg]` (DC 层 0V/50Hz)
- **`di_mask`**: 前端的 `di_mask` 直接映射到引擎的 `diMask` 低 8 位
