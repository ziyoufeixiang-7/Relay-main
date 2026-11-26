# 阻抗特性试验 API

`module`: `impedance_characteristic_test`  
`cmd`: `start`

---

## 1. 请求结构

```json
{
  "module": "impedance_characteristic_test",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "tests": [
      {
        "points": [
          { "id": 1, "x": [0.5, 2.0], "y": [1.0, 3.0], "ft": 0, "i": 5.0, "time": 1000.0 }
        ]
      }
    ],
    "payload": {
      "ctrl": {
        "closeAngle": { "mode": 1, "angle": 0.0 },
        "attenuation": { "tauMs": 100.0, "superimposeDC": true },
        "pt": 0,
        "ct": 0,
        "cb": { "sim": true, "trip": 20.0, "close": 20.0 },
        "impedance": { "resetBeforeTest": false, "resetTimeMs": 0, "preFaultTimeMs": 0 }
      },
      "di": { "A": 2, "B": 2, "C": 2, "R": 3, "a": 0, "b": 0, "c": 0, "r": 0 },
      "do": { "init": 0, "mode": 0 },
      "sys": { "v": 57.735, "f": 50.0, "iLoad": { "amp": 0.0, "ang": 0.0 } },
      "model": {
        "type": 0,
        "kl": { "formula": 0, "p1": 0.667, "p2": 0.0 },
        "klEqualsZero": false,
        "precision": 0.01
      }
    }
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `debounce` | `number` | 开入防抖（ms） |
| `tests` | `array` | 单组，`points` 为勾选扫描线 |
| `payload` | `object` | 全局参数，枚举同 [线路保护](line-protection-verification-test.md) §3 |

**不下发**：`trigger`、`output`、`tests[].type` / `dir` / `nature`

---

## 2. tests.points

仅下发已勾选、且具备 `start`+`end` 的辐射式/平行式扫描线。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `number` | 行序号 |
| `x` | `[number, number]` | 电阻 R：起点、终点（Ω） |
| `y` | `[number, number]` | 电抗 X：起点、终点（Ω） |
| `ft` | `number` | 故障类型，同线路保护 `ft` |
| `i` | `number` | 短路电流（A） |
| `time` | `number` | 故障限时（ms） |

### ft

| 值 | 故障 |
| --- | --- |
| `0` | A 接地 |
| `1` | B 接地 |
| `2` | C 接地 |
| `3` | AB 相间 |
| `4` | BC 相间 |
| `5` | CA 相间 |
| `6` | AB 接地 |
| `7` | BC 接地 |
| `8` | CA 接地 |
| `9` | 三相短路 |

---

## 3. payload

### 3.1 ctrl

| 字段 | 类型 | 枚举 |
| --- | --- | --- |
| `pt` | `number` | `0` 母线侧 / `1` 线路侧 |
| `ct` | `number` | `0` 指向线路 / `1` 指向母线 |
| `closeAngle.mode` | `number` | `0` 固定 / `1` 随机 |
| `cb.sim` | `boolean` | 模拟断路器延时 |
| `cb.trip` / `cb.close` | `number` | 跳闸/合闸延时（ms） |
| `impedance.resetBeforeTest` | `boolean` | 试验前复归 |
| `impedance.resetTimeMs` | `number` | 复归时间（ms） |
| `impedance.preFaultTimeMs` | `number` | 故障前时间（ms） |

`attenuation` 字段同线路保护，当前硬件不处理。

### 3.2 di / do

枚举同 [线路保护 §3.3–3.4](line-protection-verification-test.md)。

### 3.3 sys

| 字段 | 类型 |
| --- | --- |
| `v` | 额定电压（V） |
| `f` | 频率（Hz） |
| `iLoad.amp` | 负荷电流（A） |
| `iLoad.ang` | 负荷电流角（°） |

### 3.4 model

| 字段 | 类型 | 枚举 |
| --- | --- | --- |
| `type` | `number` | `0` If 恒定（界面固定） |
| `kl.formula` | `number` | `0` (Z0-Z1)/3Z1 / `1` KR·KX / `2` Z0/Z1 |
| `kl.p1` | `number` | formula=0/2：KL 幅值；formula=1：KR |
| `kl.p2` | `number` | formula=0/2：KL 角度（°）；formula=1：KX |
| `klEqualsZero` | `boolean` | 指向元件时接地阻抗 KL=0 |
| `precision` | `number` | 搜索精度（Ω） |

---

## 4. 事件

### 4.1 `value_update` (二分搜索过程动态更新)

每次探测（包括线段端点及中间点）完成时，都会发送 `value_update` 事件。该事件与**差动实验**结构对齐，并带有本次探测的实测结果。

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
  "actionTimeMs": 30.0          // 动作时间 (没动作则为 null)
}
```

### 4.2 `report` (单条线段测试结束报告)

二分收敛结束后，上报该线段最终寻找出的临界跳闸点（动作边界）。若起点终点无跨越（两端都跳闸或都不跳），则相关字段回传 `null`。

```json
{
  "data": {
    "rows": [
      {
        "id": 1,
        "boundaryZValue": 0.984,  // 二分收敛后的最终边界阻抗幅值
        "boundaryZAngle": 0.0,    // 二分收敛后的最终边界阻抗角
        "actionTimeMs": 30.0      // 动作时间
      }
    ]
  }
}
```

### 4.3 `stop` / `error`
与其它全站模块通用的停止和错误下发结构一致。
