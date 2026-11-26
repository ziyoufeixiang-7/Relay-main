# 统一滑差类测试 (Unified Sweep Tests) API 协议

> **模块标识**: `"module": "ac_test"` / `"dc_test"` / `"harmonic_test"` / `"steps_gradient_test"` / `"acdc_test"`  
> **Handler 文件**: `api/ACTestHdl.py`  
> **MODULE_KEYS**: `["ac_test", "dc_test", "harmonic_test", "steps_gradient_test", "acdc_test"]`

---

## 1. 实验概述

四大滑差类测试（交流、直流、谐波、阶梯递变）共享统一的数据下发接口和底层 FSM 调度流程。

```
[Node 0] 实验前复归 (可选) ──→ [Node 1] 正向扫描 ──→ [Node 2] 反向返回 (Hot Update) ──→ 实验结束
```

- 支持手动模式 (mode=0) 和自动模式 (mode=1)
- 支持仅测动作 (changeMode=0) 和测动作+返回 (changeMode=1)
- 支持动作即停 (returnMode=0) 和测全程 (returnMode=1)

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "ac_test",
  "cmd": "start",
  "params": {
    "sys": {
      "mode": 1,
      "stepTime": 1000,
      "changeMode": 0,
      "returnMode": 1,
      "debounce": 15,
      "doMask": 3,
      "doCtrlMask": 5,
      "logicMask": 255
    },
    "limits": {
      "0": [100, 60],
      "6": [20, 60]
    },
    "statics": {
      "0": { "0": [0, 50], "1": [57.735, 0] }
    },
    "steps": {
      "0": { "1": [1, 0] }
    },
    "count": 50,
    "payload": {
      "enablePreTestReset": true,
      "preTestResetTime": 1000,
      "enableStepReset": true,
      "stepResetMode": 1,
      "stepResetTime": 100,
      "resetTableData": {
        "0": { "0": [0, 50] }
      }
    }
  }
}
```

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.mode` | INT | 0=手动模式, 1=自动模式 |
| `sys.stepTime` | INT | 每步时间 (ms) |
| `sys.changeMode` | INT | 0=仅测动作, 1=测动作和返回 |
| `sys.returnMode` | INT | 0=动作即停并返回, 1=测全程 |
| `sys.debounce` | INT | 去抖动时间 (ms) |
| `sys.doMask` | INT | 复归期间的 DO 掩码 |
| `sys.doCtrlMask` | INT | 正常输出期间（动作期）的 DO 掩码 |
| `sys.logicMask` | INT | DI 触发逻辑掩码（通道掩码+与或逻辑） |
| `limits` | Object | `"通道ID": [幅值上限, 频率上限]` |
| `statics` | Object | 扫描初始静态参数，`"通道ID": {"层ID": [幅值, 频率/相位]}` |
| `steps` | Object | 每步增量变化，格式同 `statics` |
| `count` | INT | 扫描步数上限 |

#### payload 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `enablePreTestReset` | BOOL | 是否启用实验前复归 |
| `preTestResetTime` | INT | 实验前复归持续时间 (ms) |
| `enableStepReset` | BOOL | 是否启用步进间复归 |
| `stepResetMode` | INT | 0=单程复归 (仅正向), 1=全程复归 |
| `stepResetTime` | INT | 步进间复归持续时间 (ms) |
| `resetTableData` | Object | 复归期间的输出参数，格式同 `statics` |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 手动更新静态值 (手动模式)

```json
{
  "module": "ac_test",
  "cmd": "update_static",
  "static": {
    "0": { "0": [0, 50], "1": [59.0, 0] },
    "6": { "0": [0, 50], "1": [5.4, -120] }
  }
}
```

### 3.2 停止实验

```json
{
  "module": "ac_test",
  "cmd": "stop"
}
```

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "ac_test",
  "type": "value_update",
  "static": {
    "0": { "0": [0, 50], "1": [57.735, 0] },
    "6": { "0": [0, 50], "1": [5, 0] }
  },
  "di": 5,
  "do": 5
}
```

### 4.2 测试报告

```json
{
  "module": "ac_test",
  "type": "report",
  "data": {
    "tripTime": 12.3,
    "tripValues": {
      "0": { "0": [0, 50], "1": [58.2, 0] }
    },
    "returnTime": 15.7,
    "returnValues": {
      "0": { "0": [0, 50], "1": [56.9, 0] }
    },
    "returnRatio": 0.9777
  }
}
```

### 4.3 实验结束

```json
{
  "module": "ac_test",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "ac_test",
  "type": "error",
  "msg": "Manual mode input timeout exceeded 150s. System stopped."
}
```

---

## 5. 编译映射规则

### 5.1 节点生成

| 引擎 Node | 对应前端状态 | 生成条件 |
|-----------|-------------|---------|
| Node 0 | 实验前复归 | `enablePreTestReset == true` |
| Node 1 | 正向扫描动作 | 必须包含 |
| Node 2 | 反向返回扫描 | Hot Update 动态生成 (`changeMode == 1`) |

### 5.2 复归参数补全规则

对于 `resetTableData`，未配置的通道强制补全 `[0V, 50Hz]` 作为 DC 层（Layer 0）的复归参数。这个补全的 0V 将通过校准流程输出真实的偏置从而使通道彻底闭环清零。

### 5.3 DO 切换控制

| 阶段 | DO 输出 |
|------|--------|
| 实验前复归 (Node 0) | `doMask` |
| 正向/反向扫描 (无复归) | `doCtrlMask` |
| 步进间复归期 | `resetDo` = `doMask` |
| 步进间输出期 | `overDo` = `doCtrlMask` |

### 5.4 反向返回 Node 2 的 Hot Update 约束

由于 Shadow 缓冲区在步进复归时会被撕裂，正向扫描可能在未知的第 N 步跳闸。因此：

- **带复归 (Rs) 时**：Node 2 绝对不可预先生成！HDL 在 Node 1 跳闸时捕获 tick，计算真实峰值，临时组装 Node 2 通过 Hot Update 热接入。
- **不带复归时**：可以留空 `static = {}` 预先下发，引擎自动继承反向步进。

### 5.5 DI_RECOVER 编译

前端 `DI_RECOVER` → 引擎 `DI_MATCH` + `diMask |= 0x500` (AND + recover 模式)
