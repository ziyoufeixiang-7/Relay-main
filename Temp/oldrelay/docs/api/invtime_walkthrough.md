# InvTime 反时限试验技术说明 (I-t / VF-t)

> **模块 I-t**: `inverse_time_it_test` — Handler: `api/InvTimeItHdl.py`
> **模块 VF-t**: `inverse_time_vft_test` — Handler: `api/InvTimeVftHdl.py`
> **继保用途**: 测试反时限保护装置的动作特性曲线

---

## 1. 继电保护背景

### 1.1 什么是反时限保护？

反时限保护是电力系统中最常用的过流保护形式。其核心特性：**故障电流越大，保护动作越快**。

```
动作时间
    ↑
    │  ╲
    │    ╲
    │      ╲
    │        ╲_______________
    │
    └────────────────────→ 故障电流倍数
```

### 1.2 I-t vs VF-t

| 特性 | I-t 试验 | VF-t 试验 |
|------|---------|----------|
| 测试对象 | 过流反时限保护 | 低压/低频反时限保护 |
| 变量 | 电流倍数 (1.2In~10In) | 电压/频率/V/Hz 比值 |
| 典型曲线 | IEC 标准反时限/非常反时限 | V/Hz 特性曲线 |
| 应用 | 线路/变压器过流保护 | 发电机/变压器励磁保护 |

### 1.3 测试流程

每个测试点的执行流程：
```
故障前(额定电量,1s) →[TIME_OUT]→ 故障态(N倍电流) →[DI_MATCH]→ 下一测试点
                                                 →[TIME_OUT]→ 超时跳过
```

---

## 2. I-t 试验参数详解

### 2.1 系统参数

| 参数 | 含义 |
|------|------|
| `debounce` | DI 去抖(ms) |
| `logicMask` | DI 逻辑掩码(低8位通道选择) |

### 2.2 payload 参数

| 参数 | 含义 | 继保意义 |
|------|------|----------|
| `iDefinition` | 电流定义 | `FAULT_CURRENT`/`NEGATIVE_SEQUENCE`/`ZERO_SEQUENCE` |
| `faultType` | 故障类型 | `A_GROUND`/`AB_PHASE`/`ABC_PHASE` 等 |
| `ratedFrequency` | 额定频率(Hz) | 50/60Hz |
| `iBeforeFault` | 故障前电流(A) | 通常为负荷电流或0 |
| `tBeforeFault` | 故障前持续(s) | 建立稳态时间 |
| `faultLimitTime` | 故障限时(s) | 最大等待时间,超时判为不动作 |
| `outputMode.voltage` | 电压通道组 | `GROUP1_UABC`/`GROUP2_UXYZ` |
| `outputMode.current` | 电流通道组 | `GROUP1_IABC`/`GROUP2_IXYZ` |

### 2.3 方向性参数 (`directionality`)

| 参数 | 含义 |
|------|------|
| `enabled` | 是否启用方向性 |
| `vBeforeFault` | 故障前相电压(V) |
| `vFault` | 故障态相电压/线电压(V) |
| `lineAngle` | 线路阻抗角(°) |
| `ctPolarity` | CT 极性(`TOWARD_COMPONENT`/`AWAY_FROM_COMPONENT`) |

### 2.4 testPoints

```json
[
  {"id": 1, "value": 3.0},   // 3倍额定电流
  {"id": 2, "value": 5.0},   // 5倍
  {"id": 3, "value": 10.0}   // 10倍
]
```

---

## 3. 故障电流计算模型

### 3.1 非方向性模式 (`directionality.enabled = false`)

根据 `faultType` 和 `iDefinition` 确定三相电流分配：

**接地故障 (A_GROUND/B_GROUND/C_GROUND)**:
- 故障相: `I_fault ∠ phase_angle`
- 非故障相: 0A

**相间故障 (AB_PHASE/BC_PHASE/CA_PHASE)**:
- 故障相对: 等值反向 (180°相位差)
- 非故障相: 0A

**三相故障 (ABC_PHASE)**:
- 三相: `I_fault ∠ 0°/-120°/+120°`

### 3.2 方向性模式

使用矢量叠加：`I_total = I_pre + I_delta`

```python
# I_delta = value × scale ∠ (base_angle - lineAngle)
# scale 取决于 iDefinition:
#   FAULT_CURRENT: scale = 1.0
#   NEGATIVE_SEQUENCE (I2): scale = 3.0(接地) 或 √3(相间)
#   ZERO_SEQUENCE: scale = 3.0(接地) 或 0(相间无零序)
```

### 3.3 diMask bit9 (0x200)

```python
di_mask = logic_mask | 0x200
```

**bit9 = 进入节点时的 DI 快照作为变位参考**。这确保故障态节点的 DI_MATCH 是和故障前正常态对比,而不是和系统初始化时的 DI 对比。

---

## 4. VF-t 试验参数

### 4.1 testProject 模式

| 值 | 含义 | value 解释 |
|---|------|-----------|
| `V_T` | 电压-时间 | value = 电压(V) |
| `F_T` | 频率-时间 | value = 频率(Hz) |
| `VF_T` | V/Hz-时间 | value = V/Hz 比值 |

### 4.2 vOutputMode

```python
V_OUTPUT_MAP = {
    "UA": [(0, 0)],              # 单相 A
    "UAB": [(0, 0), (1, 180)],   # 线电压 AB
    "UABC_POS": [(0,0),(1,-120),(2,120)],  # 正序三相
}
```

---

## 5. 节点编译

```python
# 每个测试点 → 2 个引擎节点
before_node_id = 2 × idx      # 故障前节点
fault_node_id  = 2 × idx + 1  # 故障态节点

# 链式连接:
#   before[0] →TIME_OUT→ fault[0] →DI_MATCH→ before[1] →→→ ... →→→ -2(结束)
```

---

## 6. 遥测与报告

### 6.1 trigger 事件处理

```python
if tracker.entry_hw_ts and hw_ts > 0:
    actual_time = (hw_ts - entry_hw_ts) & 0xFFFFFFFF / 1_000_000  # µs→s
else:
    actual_time = monotonic() - entry_time_mono  # fallback
```

### 6.2 最终报告

```json
{
  "type": "report",
  "data": {
    "rows": [
      {"id": 1, "currentI": 3.0, "actualTime": 2.5432},
      {"id": 2, "currentI": 5.0, "actualTime": 1.2100},
      {"id": 3, "currentI": 10.0, "actualTime": null}
    ]
  }
}
```

`null` = 超时未动作

---

## 7. 代码审查

### ✅ 正确实现
- 7种故障类型的电流相位计算
- 方向性矢量叠加模型
- diMask bit9 进入参考
- 多测试点链式执行
- hw_ts µs 精度计时

### ⚠️ 已修复
- `di_change` 事件处理(Bug #3)
