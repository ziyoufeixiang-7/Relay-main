# 继电保护测试 API 响应格式 (前端对接文档)

本文档详细列出了**阻抗特性试验**（`impedance_characteristic_test`）和**线路保护校验**（`line_protection_verification_test`）后端回复给前端 WebSocket 的具体 JSON 消息结构。

所有由后端通过 `sendReport` 和 `sendMsg` 发出的消息，都会在最外层被包装上 `"type"` 和 `"module"`。

---

## 1. 阻抗特性试验 (Impedance Characteristic Test)

阻抗特性包含两种消息：
1. **`value_update`**：在二分查找**过程**中，每测试完一个点（包括中间点）就发送一次。用于前端实时刷新游标动画。
2. **`report`**：单条线段寻找完毕后，发送最终的收敛边界结果。

### 1.1 动态游标更新 (`value_update`)

```json
{
  "type": "value_update",
  "module": "impedance_characteristic_test",
  "id": 1,
  "x": 0.969,                   // 当前探测阻抗的 R (电阻)
  "y": 0.0,                     // 当前探测阻抗的 X (电抗)
  "boundaryZValue": 0.969,      // 当前探测阻抗幅值 |Z|
  "boundaryZAngle": 0.0,        // 当前探测阻抗角
  "hit": true,                  // 本次探测是否命中（跳闸）
  "actionTimeMs": 30.0          // 动作时间，单位毫秒 (没动作则为 null)
}
```

### 1.2 最终结果报告 (`report`)

```json
{
  "type": "report",
  "module": "impedance_characteristic_test",
  "data": {
    "rows": [
      {
        "id": 1,
        "boundaryZValue": 0.984,  // 二分收敛后的最终边界阻抗幅值
        "boundaryZAngle": 0.0,    // 二分收敛后的最终边界阻抗角
        "actionTimeMs": 30.0      // 动作时间 (如果整条线全不跳闸或全跳，上述字段回传 null)
      }
    ]
  }
}
```

---

## 2. 线路保护校验 (Line Protection Verification)

线路保护采用点对点测试机制，没有类似于阻抗寻找的过程动画。因此，每一个点测试（含各种时序转换完成后），**只发送一次 `report` 消息**。

### 2.1 综合跳闸结果报告 (`report`)

```json
{
  "type": "report",
  "module": "line_protection_verification_test",
  "data": {
    "id": 1,
    "tripA": 30.5,      // A相第一次跳闸时间 (毫秒)
    "tripB": null,      // B相第一次跳闸时间 (未跳则为 null)
    "tripC": null,      // C相第一次跳闸时间 
    "reclose": 1050.0,  // 重合闸动作时间 (毫秒)
    "postAccel": null   // 转换故障/永久故障后加速跳闸时间 (毫秒)
  }
}
```

**时间字段说明**：
- 线路保护里的所有时间（`tripA/B/C`, `reclose`, `postAccel`）单位均为**毫秒 (ms)**，保留 1 位小数。
- 所有时间均以当前序列段的发生起点为相对 0 时刻（例如：`tripA` 从下发短路瞬间起算；`reclose` 从跳闸后断路器断开瞬间起算）。
- 如果该动作没有发生，字段返回 `null`。

---

## 3. 全局通用消息

### 3.1 测试结束 (`stop`)
不管是什么模块，当所有点测完，或用户中途主动终止时：
```json
{
  "type": "stop",
  "module": "impedance_characteristic_test" // 或 line_protection_verification_test
}
```

### 3.2 错误下发 (`error`)
如果底层硬件卡死，或配置校验错误：
```json
{
  "type": "error",
  "module": "impedance_characteristic_test",
  "msg": "Node timeout waiting for IDLE in USEEngine!"
}
```
