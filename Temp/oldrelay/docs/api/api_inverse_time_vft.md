# 反时限 VF-t（电压/频率-时间特性）API 协议

> **模块标识**: `"module": "inverse_time_vft_test"`  
> **Handler 文件**: `api/InvTimeVftHdl.py` (待实现)  
> **MODULE_KEYS**: `["inverse_time_vft_test"]`

---

## 1. 实验概述

反时限 VF-t 实验用于测试保护装置的**电压/频率-时间特性曲线**。按测试点列表逐点执行：每个测试点输出指定电压或频率变化量，等待保护动作并记录实际动作时间。

```
[故障前驻留] ──→ [故障输出 (测试点 1)] ──→ [故障前驻留] ──→ [故障输出 (测试点 2)] ──→ ... ──→ 全部完成
```

- 使用 `IWsParams` 结构下发（非 `nodes[]` 模式）
- 参数主体为 `sys` + `statics` + `payload` + `testPoints`
- 支持三种测试项目：`V_T` (电压-时间) / `F_T` (频率-时间) / `VF_T` (电压频率-时间)
- 测试点串行执行：输出故障量 → 等待 DI 动作或超时 → 记录时间 → 下一点

---

## 2. 前端 → 后端：启动命令

```json
{
  "module": "inverse_time_vft_test",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 7
    },
    "statics": {
      "3": { "0": [0, 50], "1": [0, 0] },
      "4": { "0": [0, 50], "1": [0, -120] },
      "5": { "0": [0, 50], "1": [0, 120] },
      "6": { "0": [0, 50], "1": [0, 0] },
      "7": { "0": [0, 50], "1": [0, -120] },
      "8": { "0": [0, 50], "1": [0, 120] }
    },
    "payload": {
      "testProject": "VF_T",
      "vOutputMode": "UA",
      "angle": 0,
      "vbValue": 57.735,
      "fbValue": 50,
      "vfVariable": "VOLTAGE_AMPLITUDE_V",
      "vBeforeFault": 57.735,
      "fBeforeFault": 50,
      "tBeforeFault": 1,
      "faultLimitTime": 60
    },
    "testPoints": [
      { "id": 1, "value": 1.1 },
      { "id": 2, "value": 1.2 }
    ]
  }
}
```

### 2.1 params 字段详解

| 字段 | 类型 | 说明 |
|------|------|------|
| `sys.debounce` | INT | 开入防抖时间 (ms) |
| `sys.logicMask` | INT | 动作接点逻辑掩码（位掩码，判断保护动作的 DI 组合条件） |
| `statics` | Object | 辅助通道矩阵，`"通道ID": {"层ID": [幅值, 频率/相位]}` |

#### payload 子字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `testProject` | STRING | 是 | 测试项目。取值：`V_T` (电压-时间) / `F_T` (频率-时间) / `VF_T` (电压频率-时间) |
| `vOutputMode` | STRING | 是 | 电压输出方式。取值：`UA` / `UB` / `UC` / `UAB` / `UBC` / `UCA` / `UABC_POS` / `UABC_NEG` / `UABC_ZERO` |
| `angle` | NUMBER | 是 | 输出角度 (°) |
| `vbValue` | NUMBER | 是 | 基准电压 VB (V) |
| `fbValue` | NUMBER | 是 | 基准频率 FB (Hz) |
| `vfVariable` | STRING | 条件 | 仅 `testProject = VF_T` 时必传。取值：`VOLTAGE_AMPLITUDE_V` (变化量为电压) / `VOLTAGE_FREQUENCY_F` (变化量为频率) |
| `vBeforeFault` | NUMBER | 是 | 故障前电压 (V) |
| `fBeforeFault` | NUMBER | 是 | 故障前频率 (Hz) |
| `tBeforeFault` | NUMBER | 是 | 故障前驻留时间 (s) |
| `faultLimitTime` | NUMBER | 是 | 故障最大限时 (s)，超时判定为保护未动作 |

#### testPoints 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `testPoints[].id` | INT | 测试点序号（从 1 递增） |
| `testPoints[].value` | NUMBER | 变化量值。单位由 `testProject` 决定：`V_T` → V, `F_T` → Hz, `VF_T` → V/Hz |

---

## 3. 前端 → 后端：运行中控制命令

### 3.1 停止实验

```json
{
  "module": "inverse_time_vft_test",
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
  "module": "inverse_time_vft_test",
  "type": "value_update",
  "id": 1,
  "vOrFValue": 63.5,
  "actualTime": 0.582,
  "di": 5,
  "do": 3
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT | 测试点序号（与 `testPoints[].id` 对齐） |
| `vOrFValue` | NUMBER | 变化量实测值 (V / Hz / V/Hz) |
| `actualTime` | NUMBER | 动作时间实测值 (s) |
| `di` | INT | 开入位图（可选，仅变化时） |
| `do` | INT | 开出位图（可选，仅变化时） |

### 4.2 测试报告

所有测试点完成后或停止时发送：

```json
{
  "module": "inverse_time_vft_test",
  "type": "report",
  "data": {
    "rows": [
      {
        "id": 1,
        "vOrFValue": 63.5,
        "actualTime": 0.582
      },
      {
        "id": 2,
        "vOrFValue": 69.3,
        "actualTime": 0.418
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `data.rows[].id` | INT | 测试点序号 |
| `data.rows[].vOrFValue` | NUMBER | 变化量实测值 |
| `data.rows[].actualTime` | NUMBER | 动作时间实测值 (s) |

> 派生字段（`m`、`theoreticalTime`、`absoluteError`、`relativeError`）由前端按 `id` 索引计算，不由后端返回。

### 4.3 实验结束

```json
{
  "module": "inverse_time_vft_test",
  "type": "stop"
}
```

### 4.4 错误通知

```json
{
  "module": "inverse_time_vft_test",
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
| Node A (故障前) | 输出故障前电压/频率，驻留 `tBeforeFault` 秒 | `TIME_OUT` = `tBeforeFault * 1000` ms |
| Node B (故障态) | 输出变化量 (V/F)，等待保护动作 | `DI_MATCH` (logicMask) + `TIME_OUT` = `faultLimitTime * 1000` ms |

### 5.2 输出通道映射

HDL 需根据 `vOutputMode` 确定实际输出通道：

| vOutputMode | 输出通道 |
|-------------|---------|
| `UA` | 通道 0 |
| `UB` | 通道 1 |
| `UC` | 通道 2 |
| `UAB` | 通道 0, 1 (线电压) |
| `UBC` | 通道 1, 2 (线电压) |
| `UCA` | 通道 2, 0 (线电压) |
| `UABC_POS` | 通道 0, 1, 2 (正序分量) |
| `UABC_NEG` | 通道 0, 1, 2 (负序分量) |
| `UABC_ZERO` | 通道 0, 1, 2 (零序分量) |

### 5.3 特殊编译逻辑

- **testProject 决定变化量类型**:
  - `V_T`: `testPoints[].value` 为电压值，故障态改变电压幅值，频率不变
  - `F_T`: `testPoints[].value` 为频率值，故障态改变频率，电压不变
  - `VF_T`: `testPoints[].value` 为比值 V/Hz，根据 `vfVariable` 决定变化的是电压还是频率
- **logicMask → diMask**: `sys.logicMask` 映射为引擎的 `diMask` 参数
- **串行执行**: 测试点 N 完成后，HDL 通过 Hot Update 或预编译切换到测试点 N+1
- **基准值**: `vbValue` 和 `fbValue` 用于 `VF_T` 模式下计算实际输出值
