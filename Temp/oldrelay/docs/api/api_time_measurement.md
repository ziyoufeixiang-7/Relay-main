# 时间测量 (Time Measurement) API 协议

> **模块标识**: `"module": "time_measurement"`  
> **Handler 文件**: `api/TMTestHdl.py`  
> **MODULE_KEYS**: `["time_measurement"]`

---

## 1. 实验概述

时间测量实验由 **3 个状态节点** 和 **3 个独立计时器** 构成。

```
状态 1 (准备态)  ──→  状态 2 (动作态)  ──→  状态 3 (结束态)  ──→  实验结束 (next_id = -2)
```

- **状态 1**：按键触发 (`MANUAL_TRIG`) / 超时触发 (`TIME_OUT`) / 开入触发 (`DI_MATCH`)
- **状态 2**：超时触发 (`TIME_OUT`) / 计时器停时触发 (`TIMER_1` / `TIMER_2` / `TIMER_3` / 组合)
- **状态 3**：超时触发 (`TIME_OUT`) / 按键触发 (`MANUAL_TRIG`)，触发后结束实验

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "time_measurement",
  "cmd": "start",
  "params": {
    "sys": { "debounce": 15 },
    "timers": {
      "timer1": {
        "start_mode": "ENTER_STATE_2",
        "stop_mode": "DI_A_CLOSE"
      },
      "timer2": {
        "start_mode": "TIMER1_STOP_START",
        "stop_mode": "DI_B_OPEN"
      },
      "timer3": {
        "start_mode": "TIMER2_STOP_START",
        "stop_mode": "DI_C_CLOSE"
      }
    },
    "nodes": [
      {
        "id": 1,
        "do_actions": [{ "delay": 0, "mask": 3 }],
        "static": {},
        "limits": {},
        "next_id": 2,
        "triggers": [{ "condition": "TIME_OUT", "timeout": 20000 }]
      },
      {
        "id": 2,
        "do_actions": [],
        "static": {},
        "limits": {},
        "next_id": 3,
        "triggers": [{ "condition": "TIMER_1" }]
      },
      {
        "id": 3,
        "do_actions": [],
        "static": {},
        "limits": {},
        "next_id": -2,
        "triggers": [{ "condition": "MANUAL_TRIG" }]
      }
    ]
  }
}
```

> **备注**：每个节点的 `triggers` 可包含多个触发条件，满足任一即跳转至 `next_id`。

**各状态节点可用的 `condition` 触发方式：**

| 状态 | 可用触发条件 | 说明 |
|------|-------------|------|
| 状态 1 (准备态) | `MANUAL_TRIG`, `TIME_OUT`, `DI_MATCH` | 按键/超时/开入 三选 |
| 状态 2 (动作态) | `TIME_OUT`, `TIMER_1`, `TIMER_2`, `TIMER_3`, `TIMER_1_2`, `TIMER_2_3`, `TIMER_3_1`, `TIMER_1_2_3`, `TIME_CTRL` | 超时 或 计时器触发 |
| 状态 3 (结束态) | `MANUAL_TRIG`, `TIME_OUT` | 按键/超时 二选 |

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.debounce` | INT | 开入防抖时间 (ms) |
| `timers` | Object | 3 个计时器配置 (见 §2.2) |
| `statics` | Object | 各状态节点的静态输出参数，key=前端节点 ID |
| `limits` | Object | 通道限值，`"通道ID": [幅值上限, 频率上限]` |
| `nodes` | Array | 状态节点配置列表 |
| `nodes[].id` | INT | 前端节点 ID (1, 2, 3) |
| `nodes[].do_actions` | Array | DO 输出动作，`{delay, mask}` |
| `nodes[].next_id` | INT | 跳转目标，-2=结束实验 |
| `nodes[].triggers` | Array | 触发条件列表 |

### 2.2 计时器配置

3 个计时器与状态机并行运行，各有独立的启时/停时条件。计时器使用 FPGA 硬件 SOE 时间戳实现精确计时。

#### 启时条件 `start_mode`

**通用选项 (3 个计时器均可用)：**

| 值 | 含义 |
|----|------|
| `ENTER_STATE_2` | 进入状态 2 时启时 |
| `DI_X_CLOSE_START` | 开入 X 由断开→闭合 启时 |
| `DI_X_OPEN_START` | 开入 X 由闭合→断开 启时 |

> X 可选: `A`, `B`, `C`, `R`, `a`, `b`, `c`, `r` (共 8 个开入端口)

**计时器 2 额外选项：**

| 值 | 含义 |
|----|------|
| `TIMER1_STOP_START` | 计时器 1 停时 → 自动启动计时器 2 |

**计时器 3 额外选项：**

| 值 | 含义 |
|----|------|
| `TIMER1_STOP_START` | 计时器 1 停时 → 自动启动计时器 3 |
| `TIMER2_STOP_START` | 计时器 2 停时 → 自动启动计时器 3 |

#### 停时条件 `stop_mode` (三个计时器通用)

| 值 | 含义 |
|----|------|
| `DI_X_CLOSE` | 开入 X 由断开→闭合 停时 |
| `DI_X_OPEN` | 开入 X 由闭合→断开 停时 |

> X 可选: `A`, `B`, `C`, `R`, `a`, `b`, `c`, `r`

#### 状态 2 计时器结束触发条件

状态 2 的 `triggers` 中 `condition` 可选以下计时器触发值：

| 值 | 含义 |
|----|------|
| `TIMER_1` | 计时器 1 停时后触发 |
| `TIMER_2` | 计时器 2 停时后触发 |
| `TIMER_3` | 计时器 3 停时后触发 |
| `TIMER_1_2` | 计时器 1 和 2 都停时后触发 |
| `TIMER_2_3` | 计时器 2 和 3 都停时后触发 |
| `TIMER_3_1` | 计时器 3 和 1 都停时后触发 |
| `TIMER_1_2_3` | 计时器 1、2、3 全部停时后触发 |
| `TIME_CTRL` | 时间控制 (预留) |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 按键触发（跳转下一状态）

```json
{
  "module": "time_measurement",
  "cmd": "next_node"
}
```

> 后端 Handler 转换为引擎命令 `{"cmd": "manual_trig", "id": 1}`

### 3.2 停止实验

```json
{
  "module": "time_measurement",
  "cmd": "stop"
}
```

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "time_measurement",
  "type": "value_update",
  "di": 255,         // DI 状态 (仅 DI 变化时)
  "do": 3,           // DO 状态 (仅 DO 变化时)
  "nodeId": 2        // 当前状态节点 ID (前端 ID)
}
```

> 字段为增量推送，仅变化的字段会被包含。

### 4.2 测试报告

```json
{
  "module": "time_measurement",
  "type": "report",
  "data": {
    "timer1_elapsed_ms": 125.4,
    "timer2_elapsed_ms": 300.0,
    "timer3_elapsed_ms": null
  }
}
```

> `null` 表示计时器未完成停时。

### 4.3 实验结束

```json
{
  "module": "time_measurement",
  "type": "stop"
}
```

---

## 5. 编译映射规则

### 5.1 节点生成

| 前端节点 | 引擎 Node ID | 说明 |
|---------|-------------|------|
| 状态 1 (id=1) | Node 0 | 前端 ID - 1 |
| 状态 2 (id=2) | Node 1 | 前端 ID - 1 |
| 状态 3 (id=3) | Node 2 | 前端 ID - 1 |

### 5.2 特殊编译逻辑

- **TIMER 类触发条件**：`TIMER_1` / `TIMER_1_2` 等在引擎中注册为 `MANUAL_TRIG`，由 HDL 接管计时器判断，满足条件后注入 `internal_manual_trig`
- **未配置通道**：强制补全为 `[0, freq_50_reg]` (DC 层 0V/50Hz)
- **计时器时间计算**：使用 DI SOE 事件中的 `hw_ts` 差值，`(stop_ts - start_ts) & 0xFFFFFFFF / 1000.0`
