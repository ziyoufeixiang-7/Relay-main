# 反时限 I-t（电流-时间特性）API 协议

> **模块标识**: `"module": "inverse_time_it_test"`  
> **Handler 文件**: `api/InvTimeItHdl.py` (待实现)  
> **MODULE_KEYS**: `["inverse_time_it_test"]`

---

## 1. 实验概述

反时限 I-t 实验用于测试保护装置的**电流-时间特性曲线**。按测试点列表逐点执行：每个测试点输出指定故障电流，等待保护动作并记录实际动作时间。

```
[故障前驻留] ──→ [故障输出 (测试点 1)] ──→ [故障前驻留] ──→ [故障输出 (测试点 2)] ──→ ... ──→ 全部完成
```

- 使用 `IWsParams` 结构下发（非 `nodes[]` 模式）
- 参数主体为 `sys` + `statics` + `payload` + `testPoints`
- 测试点串行执行：输出故障电流 → 等待 DI 动作或超时 → 记录时间 → 下一点

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "inverse_time_it_test",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 5
    },
    "statics": {
      "0": { "0": [0, 50], "1": [0, 0] },
      "1": { "0": [0, 50], "1": [0, -120] },
      "2": { "0": [0, 50], "1": [0, 120] }
    },
    "payload": {
      "iDefinition": "FAULT_CURRENT",
      "faultType": "A_GROUND",
      "ratedFrequency": 50,
      "directionality": {
        "enabled": false,
        "vBeforeFault": 57.735,
        "vFault": 20,
        "lineAngle": 70,
        "ctPolarity": "TOWARD_COMPONENT"
      },
      "outputMode": {
        "voltage": "GROUP1_UABC",
        "current": "GROUP1_IABC"
      },
      "iBeforeFault": 0,
      "tBeforeFault": 1,
      "faultLimitTime": 60
    },
    "testPoints": [
      { "id": 1, "value": 2.0 },
      { "id": 2, "value": 3.0 }
    ]
  }
}
```

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.debounce` | INT | 开入防抖时间 (ms) |
| `sys.logicMask` | INT | 动作接点逻辑掩码（位掩码，判断保护动作的 DI 组合条件） |
| `statics` | Object | 辅助电压通道矩阵，`"通道ID": {"层ID": [幅值, 频率/相位]}` |

#### payload 子字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `iDefinition` | STRING | 是 | 电流定义。取值：`FAULT_CURRENT` / `NEGATIVE_SEQUENCE` / `ZERO_SEQUENCE` / `SINGLE_PATH` |
| `negativeSequenceChannel` | STRING | 条件 | 仅 `iDefinition = NEGATIVE_SEQUENCE` 时必传。取值：`I2` / `3I2` |
| `faultType` | STRING | 是 | 故障类型。取值：`A_GROUND` / `B_GROUND` / `C_GROUND` / `AB_PHASE` / `BC_PHASE` / `CA_PHASE` / `ABC_PHASE` |
| `ratedFrequency` | NUMBER | 是 | 额定频率 (Hz) |
| `directionality.enabled` | BOOL | 是 | 是否启用方向性 |
| `directionality.vBeforeFault` | NUMBER | 是 | 故障前相电压 (V) |
| `directionality.vFault` | NUMBER | 是 | 故障相电压 (V) |
| `directionality.lineAngle` | NUMBER | 是 | 线路阻抗角 (°) |
| `directionality.ctPolarity` | STRING | 是 | CT 极性。取值：`TOWARD_COMPONENT` / `TOWARD_BUSBAR` |
| `outputMode.voltage` | STRING | 是 | 电压输出组。取值：`GROUP1_UABC` / `GROUP2_UXYZ` |
| `outputMode.current` | STRING | 是 | 电流输出组。取值：`GROUP1_IABC` / `GROUP2_IXYZ` |
| `iBeforeFault` | NUMBER | 是 | 故障前电流 (A) |
| `tBeforeFault` | NUMBER | 是 | 故障前驻留时间 (s) |
| `faultLimitTime` | NUMBER | 是 | 故障最大限时 (s)，超时判定为保护未动作 |

#### testPoints 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `testPoints[].id` | INT | 测试点序号（从 1 递增） |
| `testPoints[].value` | NUMBER | 故障电流值 (A) |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 停止实验

```json
{
  "module": "inverse_time_it_test",
  "cmd": "stop"
}
```

> 本实验无 `next_node` 和 `update_static` 控制。全自动逐点执行。

---

## 4. 后端 → 前端：遥测事件

### 4.1 实时数据更新

每个测试点完成后发送：

```json
{
  "module": "inverse_time_it_test",
  "type": "value_update",
  "id": 1,
  "currentI": 2.0,
  "actualTime": 0.582,
  "di": 5,
  "do": 3
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 测试点序号（与 `testPoints[].id` 对齐） |
| `currentI` | NUMBER | 电流实测值 (A) |
| `actualTime` | NUMBER | 动作时间实测值 (s) |
| `di` | INT | 开入位图（可选，仅变化时） |
| `do` | INT | 开出位图（可选，仅变化时） |

### 4.2 测试报告

所有测试点完成后或停止时发送：

```json
{
  "module": "inverse_time_it_test",
  "type": "report",
  "data": {
    "rows": [
      {
        "id": 1,
        "currentI": 2.0,
        "actualTime": 0.582
      },
      {
        "id": 2,
        "currentI": 3.0,
        "actualTime": 0.291
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `data.rows[].id` | INT | 测试点序号 |
| `data.rows[].currentI` | NUMBER | 电流实测值 (A) |
| `data.rows[].actualTime` | NUMBER | 动作时间实测值 (s) |

> 派生字段（`iRatio`、`theoreticalTime`、`absoluteError`、`relativeError`）由前端根据 `id` 索引计算，不由后端返回。

### 4.3 实验结束

```json
{
  "module": "inverse_time_it_test",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "inverse_time_it_test",
  "type": "error",
  "msg": "错误描述"
}
```

---

## 5. 编译映射规则

### 5.1 节点生成策略

每个测试点编译为 2 个引擎节点循环：

| 引擎 Node | 对应阶段 | 触发条件 |
|-----------|---------|---------|
| Node A (故障前) | 输出故障前电压/电流，驻留 `tBeforeFault` 秒 | `TIME_OUT` = `tBeforeFault * 1000` ms |
| Node B (故障态) | 输出故障电流，等待保护动作 | `DI_MATCH` (logicMask) + `TIME_OUT` = `faultLimitTime * 1000` ms |

### 5.2 特殊编译逻辑

- **故障电流计算**: HDL 需根据 `iDefinition` + `faultType` + `directionality` 组合，计算各通道 (Ua/Ub/Uc/Ia/Ib/Ic) 的幅值和相位
- **outputMode 映射**: `GROUP1_UABC` → 通道 0~2, `GROUP2_UXYZ` → 通道 3~5; `GROUP1_IABC` → 通道 6~8, `GROUP2_IXYZ` → 通道 9~11
- **logicMask → diMask**: `sys.logicMask` 映射为引擎的 `diMask` 参数
- **串行执行**: 测试点 N 完成后，HDL 通过 Hot Update 或预编译切换到测试点 N+1
