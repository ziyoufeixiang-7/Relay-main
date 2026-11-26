# 滑差试验 (Slip Test) API 协议

> **模块标识**: `"module": "slip_test"`  
> **Handler 文件**: `api/SlipTestHdl.py`  
> **MODULE_KEYS**: `["slip_test"]`

---

## 1. 实验概述

滑差试验用于测试继电保护装置对**频率/电压/电流滑变**（d/dt 变化率）的响应特性。支持 5 种测试子类型（testType 0~4），分别针对动作值搜索、动作时间测试和三种闭锁值搜索。

```
下发启动参数(sys + statics + limits + payload)
    → 运行中 value_update 同步输出
    → DI 动作 / 搜索完成 → report (滑差根级结果字段)
    → stop
```

**关键特征**：
- 与阶梯递变共用左侧模拟量通道表格，但使用独立模块名 `slip_test`
- **不使用** `steps` / `count` 字段；输出变化由后端按 `payload` 执行
- `payload` 按 `testType` 子集下发，非全量

---

## 2. 前端 → 后端：启动命令

### 2.1 请求示例（testType=1 动作时间测试）

```json
{
  "module": "slip_test",
  "cmd": "start",
  "params": {   
    "debounce": 15,
    "sys": {
      "doMask": 3,
      "doCtrlMask": 5,
      "logicMask": 255
    },
    "statics": {
      "0": { "0": [0, 50], "1": [57.735, 0] }
    },
    "payload": {
      "testType": 1,
      "slipVarType": "5",
      "slipPhasor": 2,
      "slipPhase": "0",
      "slipStart": 50,
      "slipEnd": 45,
      "slipDDt": 1,
      "timerStartValue": 49,
      "returnTime": 1000,
      "waitTime": 1000
    }
  }
}
```

### 2.2 params 字段详解

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `debounce` | INT | 是 | 开入防抖时间 (ms) |
| `sys.doMask` | INT | 是 | 开出初始状态位掩码 |
| `sys.doCtrlMask` | INT | 是 | 开出控制位掩码 |
| `sys.logicMask` | INT | 是 | 开入逻辑掩码 |
| `statics` | Object | 是 | 各模拟通道起始静态值，`"通道ID": {"层ID": [幅值, 频率/相位]}` |
| `payload` | Object | 是 | 滑差业务参数，按 `testType` 子集下发 (见 §2.4) |

> **不下发**: `stepTime` / `changeMode` / `returnMode` / `steps` / `count`

### 2.3 testType 枚举

| 取值 | 含义 |
|------|------|
| `0` | 动作值搜索 (`ACTION_VALUE`) |
| `1` | 动作时间测试 (`ACTION_TIME`) |
| `2` | 滑差闭锁值 d/dt 搜索 (`SLIP_LOCK_VALUE`) |
| `3` | 电流闭锁值搜索 (`CURRENT_LOCK_VALUE`) |
| `4` | 电压闭锁值搜索 (`VOLTAGE_LOCK_VALUE`) |

### 2.4 payload 按 testType 的必填字段

| testType | 必填 payload 键 |
|----------|-----------------|
| `0` 动作值搜索 | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `actionSearchStart`, `actionSearchEnd`, `actionSearchStep`, `slipDDt`, `returnTime`, `waitTime` |
| `1` 动作时间测试 | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `timerStartValue`, `returnTime`, `waitTime` |
| `2` 滑差闭锁值搜索 | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `ddtSearchStart`, `ddtSearchEnd`, `ddtSearchStep`, `returnTime`, `waitTime` |
| `3` 电流闭锁值搜索 | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `lockPhase`, `lockSearchStart`, `lockSearchEnd`, `lockSearchStep`, `returnTime`, `waitTime` |
| `4` 电压闭锁值搜索 | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `lockVarType`, `lockPhase`, `lockSearchStart`, `lockSearchEnd`, `lockSearchStep`, `returnTime`, `waitTime` |

### 2.5 变量类型与相量枚举

#### slipVarType (变量大类)

| 取值 | 含义 |
|------|------|
| `0_0` | 相电压 |
| `0_1` | 相电流 |
| `2_0` | 线电压 |
| `5` | 系统频率 |

#### slipPhasor (相量类型)

| 取值 | 含义 |
|------|------|
| `0` | 幅值 |
| `1` | 角度 |
| `2` | 频率 |

### 2.6 payload 字段语义

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| `slipPhase` | STRING | — | 归一化通道索引串 (逗号分隔)，电流通道索引 +6 |
| `slipStart` | NUMBER | V/A/Hz/° | 滑差起点 |
| `slipEnd` | NUMBER | V/A/Hz/° | 滑差终点 |
| `slipDDt` | NUMBER | 单位/s | 滑差变化率 d/dt |
| `returnTime` | NUMBER | ms | 复归时间 |
| `waitTime` | NUMBER | ms | 终点等待时间 |
| `timerStartValue` | NUMBER | V/A/Hz/° | 计时启动值 (testType=1) |
| `actionSearchStart/End/Step` | NUMBER | V/A/Hz/° | 动作值搜索范围和步长 (testType=0) |
| `ddtSearchStart/End/Step` | NUMBER | 单位/s | d/dt 搜索范围和步长 (testType=2) |
| `lockPhase` | STRING | — | 闭锁通道索引串 (testType=3,4) |
| `lockVarType` | STRING | — | 闭锁侧变量大类 (testType=4) |
| `lockSearchStart/End/Step` | NUMBER | A 或 V | 闭锁量搜索范围和步长 (testType=3,4) |

> **幅值换算**: 电压/电流幅值类标量经 `turnValidValToAmp` (×√2) 与装置幅值口径对齐；角度/频率/时间不换算。

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 停止实验

```json
{
  "cmd": "stop"
}
```

> 不带 `module` 字段。

### 3.2 其他控制

滑差试验不使用 `update_static` / `next_node` 命令。运行中通道刷新依赖后端 `value_update`。

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

```json
{
  "module": "slip_test",
  "type": "value_update",
  "id": 0,
  "static": {
    "0": { "0": [0, 50], "1": [57.735, 0] }
  },
  "di": 255,
  "do": 15
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `module` | STRING | 必须为 `slip_test` |
| `id` | INT | 更新 ID |
| `static` | Object | 当前各通道输出值，结构同 `statics` |
| `di` | INT | 开入端口原始值 |
| `do` | INT | 开出端口原始值 |

### 4.2 测试报告

```json
{
  "module": "slip_test",
  "type": "report",
  "data": {
    "testType": 1,
    "actionValue": null,
    "timerStartValue": 49.0,
    "actionTime": 15.0,
    "slipLockValue": null,
    "currentLockValue": null,
    "voltageLockValue": null
  }
}
```

#### report.data 字段

| 字段 | 类型 | 条件 | 说明 |
|------|------|------|------|
| `testType` | INT | 必填 | 与启动时一致 |
| `actionValue` | NUMBER\|null | testType=0 | 动作值搜索结果 |
| `timerStartValue` | NUMBER\|null | testType=1 | 计时启动值 |
| `actionTime` | NUMBER\|null | testType=1 | 动作时间 (ms) |
| `slipLockValue` | NUMBER\|null | testType=2 | 滑差闭锁值 d/dt 搜索结果 |
| `currentLockValue` | NUMBER\|null | testType=3 | 电流闭锁值 (A) |
| `voltageLockValue` | NUMBER\|null | testType=4 | 电压闭锁值 (V) |

> 与当前 testType 无关的结果键可省略或置 `null`。

### 4.3 实验结束

```json
{
  "module": "slip_test",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "slip_test",
  "type": "error",
  "msg": "错误描述"
}
```

---

## 5. 编译映射规则

### 5.1 核心逻辑

滑差试验不使用固定节点链。后端根据 `testType` 执行不同算法：

| testType | 后端执行逻辑 |
|----------|-------------|
| 0 (动作值搜索) | 从 `actionSearchStart` 到 `actionSearchEnd`，按 `actionSearchStep` 步进，每步以 `slipDDt` 速率滑变，检测 DI 动作 |
| 1 (动作时间) | 以 `slipDDt` 速率从 `slipStart` 滑变到 `slipEnd`，经过 `timerStartValue` 时开始计时，检测 DI 动作时间 |
| 2 (滑差闭锁值搜索) | 在 `ddtSearchStart` 到 `ddtSearchEnd` 范围搜索临界 d/dt 值 |
| 3 (电流闭锁值搜索) | 在 `lockSearchStart` 到 `lockSearchEnd` 范围搜索临界电流闭锁值 |
| 4 (电压闭锁值搜索) | 在 `lockSearchStart` 到 `lockSearchEnd` 范围搜索临界电压闭锁值 |

### 5.2 通道映射

- `slipPhase`: 逗号分隔的通道索引，电流类已偏移 +6
- `slipVarType` + `slipPhasor`: 决定滑变的物理量和通道层级
- `statics`: 提供所有通道的初始输出值
- 运行期间通过 `value_update` 将当前通道状态同步给前端

### 5.3 特殊处理

- 幅值类 V/A 标量：前端已经过 ×√2 换算为幅值口径
- `returnTime` / `waitTime`: 后端用于搜索复归和步间等待
- `limits`: 后端需校验输出不超限
