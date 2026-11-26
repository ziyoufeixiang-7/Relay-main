# 线路保护 API V2 规范

本文档定义了线路保护实验（`lp_test`）的完整通信协议。

---

## 1. 完整请求结构

```json
{
  "module": "lp_test",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "tests": [ ... ],
    "payload": { ... }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `debounce` | `number` | 开入防抖时间（ms），默认 20。 |
| `tests` | `array` | 测试分组列表，见第 2 节。 |
| `payload` | `object` | 实验全局控制参数，见第 3 节。 |

**执行语义**：`tests` 数组**按顺序依次执行**。
- 若某组发生**错误失败**（硬件异常、ACK 超时等），整个实验**停止**。
- 若某组只是**未测到保护动作**（在限时内无跳闸），视为该组测试完成，**继续执行下一组**。

---

## 2. tests 测试分组

每个 `tests` 元素包含公共字段 + points 列表。

### 2.1 公共字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `number` | 测试类型，见下表。 |
| `dir` | `number` | `0`=正向故障，`1`=反向故障。 |
| `nature` | `number` | `0`=瞬时性故障，`1`=永久性故障。 |

| `type` | 说明 |
|--------|------|
| `0` | 距离保护（原 DISTANCE） |
| `1` | 零序过流保护（原 ZERO_SEQ） |
| `2` | 过流保护（原 OC） |
| `3` | 负序过流保护（原 NEG_SEQ） |
| `4` | 工频变化量阻抗保护（原 DELTA_Z） |
| `5` | 不灵敏零序（非全相零序）保护（原 INSENS_ZS） |
| `6` | 自动重合闸及后加速（原 AUTO_RECLOSE） |

---

### 2.2 type=0 距离保护

无独立 settings 块，所有参数由前端计算好后直接放入每个 point。

```json
{
  "type": 0,
  "dir": 0,
  "nature": 0,
  "points": [
    { "id": 1, "ft": 0, "z": 1.9, "ang": 75.0, "time": 100.0, "i": 5.0 },
    { "id": 2, "ft": 3, "z": 4.2, "ang": 75.0, "time": 600.0, "i": 5.0 }
  ]
}
```

#### points

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 行序号。 |
| `ft` | `number` | `0`A接地 `1`B接地 `2`C接地 `3`AB相间 `4`BC相间 `5`CA相间 `6`AB接地 `7`BC接地 `8`CA接地 `9`三相短路 |
| `z` | `number` | 实际测试阻抗（Ω），前端已计算完毕。 |
| `ang` | `number` | 阻抗角（度）。 |
| `time` | `number` | 故障时间限制（ms），前端已计算完毕。 |
| `i` | `number` | 短路电流（A）。 |

---

### 2.3 type=1/2/3 过流类保护（ZERO_SEQ / OC / NEG_SEQ）

无独立 settings 块，参数全部由前端计算好后放入每个 point。三种类型结构完全一致，仅 `ft` 允许值不同。

```json
{
  "type": 1,
  "dir": 0,
  "nature": 0,
  "points": [
    { "id": 1, "ft": 0, "z": 6.0, "ang": 75.0, "time": 100.0, "i": 4.75 },
    { "id": 2, "ft": 1, "z": 6.0, "ang": 75.0, "time": 600.0, "i": 5.25 }
  ]
}
```

#### points

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 行序号。 |
| `ft` | `number` | 允许值因 `type` 而异，见下表。 |
| `z` | `number` | 实际测试阻抗（Ω），前端已计算完毕。 |
| `ang` | `number` | 阻抗角（度）。 |
| `time` | `number` | 故障时间限制（ms），前端已计算完毕。 |
| `i` | `number` | 实际测试电流（A），前端已计算完毕。 |

#### 各类型允许的 ft

| `ft` | 故障类型 | type=1 (ZERO_SEQ) | type=2 (OC) | type=3 (NEG_SEQ) |
|------|---------|:-----------------:|:-----------:|:----------------:|
| `0` | A相接地 | ✓ | ✓ | ✓ |
| `1` | B相接地 | ✓ | ✓ | ✓ |
| `2` | C相接地 | ✓ | ✓ | ✓ |
| `3` | AB相间 | — | ✓ | ✓ |
| `4` | BC相间 | — | ✓ | ✓ |
| `5` | CA相间 | — | ✓ | ✓ |
| `9` | 三相短路 | — | ✓ | — |

> **零序（type=1）**：仅单相接地故障产生 3I₀ 分量。  
> **负序（type=3）**：三相对称短路无负序分量（I₂=0），不适用。

---

### 2.4 type=4 工频变化量阻抗保护

> ℹ️ **说明**：支持南瑞（`formula=0`）和许继（`formula=1`）工频变化量阻抗公式，正方向会叠加偏移项，反方向故障电压直接置 0。

与其他类型一致，仅 points 内多 `m`（校验点系数）和 `formula`（计算公式）参数。

```json
{
  "type": 4,
  "dir": 0,
  "nature": 0,
  "points": [
    { "id": 1, "ft": 0, "z": 5.7, "ang": 75.0, "time": 60.0, "i": 5.0, "m": 0.95, "formula": 0 },
    { "id": 2, "ft": 0, "z": 6.3, "ang": 75.0, "time": 60.0, "i": 5.0, "m": 1.05, "formula": 0 }
  ]
}
```

#### points

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 行序号。 |
| `ft` | `number` | `0`A接地 `1`B接地 `2`C接地 `3`AB相间 `4`BC相间 `5`CA相间 `6`AB接地 `7`BC接地 `8`CA接地 `9`三相短路 |
| `z` | `number` | 实际测试阻抗（Ω），前端已计算完毕。 |
| `ang` | `number` | 阻抗角（度）。 |
| `time` | `number` | 故障总持续时间（ms），前端已计算完毕。 |
| `i` | `number` | 短路电流（A）。 |
| `m` | `number` | 校验点系数 m。 |
| `formula` | `number` | 计算公式：`0`=南瑞继保，`1`=许继电气。 |

---

### 2.5 type=5 不灵敏零序（非全相零序）保护

所有参数平铺在 points 内。第一次故障用无后缀字段，转换故障用 `2` 后缀字段。

> 非全相零序本质上**必然包含故障转换**，故 `ft2`/`z2`/`ang2`/`time2`/`i2`/`after`/`delay` 字段**始终存在**。

```json
{
  "type": 5,
  "dir": 0,
  "nature": 0,
  "points": [
    {
      "id": 1,
      "ft": 0, "z": 6.0, "ang": 75.0, "time": 100.0,  "i": 4.75,
      "ft2": 2, "z2": 3.0, "ang2": 75.0, "time2": 500.0, "i2": 8.0,
      "after": 0, "delay": 100.0
    },
    {
      "id": 2,
      "ft": 0, "z": 6.0, "ang": 75.0, "time": 1100.0, "i": 5.25,
      "ft2": 2, "z2": 3.0, "ang2": 75.0, "time2": 500.0, "i2": 8.0,
      "after": 0, "delay": 100.0
    }
  ]
}
```

#### points

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 行序号。 |
| `ft` | `number` | 第一次故障类型：`0`A接地，`1`B接地，`2`C接地。 |
| `z` | `number` | 第一次故障阻抗值（Ω）。 |
| `ang` | `number` | 第一次故障阻抗角（度）。 |
| `time` | `number` | 第一次故障时间限制（ms）。 |
| `i` | `number` | 第一次故障短路电流（A）。 |
| `ft2` | `number` | 转换故障类型：`0`A接地，`1`B接地，`2`C接地。 |
| `z2` | `number` | 转换故障阻抗值（Ω）。 |
| `ang2` | `number` | 转换故障阻抗角（度）。 |
| `time2` | `number` | 转换故障时间限制（ms）。 |
| `i2` | `number` | 转换故障短路电流（A）。 |
| `after` | `number` | 触发时刻：`0`=第一次故障跳闸后，`1`=重合闸后。 |
| `delay` | `number` | 触发时刻之后延迟多少 ms 施加转换故障。 |

---

### 2.6 type=6 自动重合闸及后加速

与 type=5 结构一致，固定单点。是否发生故障转换由 `ft2` 字段**是否存在**判断：无转换故障时省略 `2` 后缀字段及 `after`/`delay`。

**无故障转换：**

```json
{
  "type": 6,
  "dir": 0,
  "nature": 0,
  "points": [
    { "id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000.0, "i": 10.0 }
  ]
}
```

**有故障转换：**

```json
{
  "type": 6,
  "dir": 0,
  "nature": 0,
  "points": [
    {
      "id": 1,
      "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000.0, "i": 10.0,
      "ft2": 3, "z2": 4.0, "ang2": 75.0, "time2": 500.0, "i2": 8.0,
      "after": 1, "delay": 100.0
    }
  ]
}
```

#### points（固定单点）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 固定为 1。 |
| `ft` | `number` | 第一次故障类型（10 种）。 |
| `z` | `number` | 第一次故障阻抗值（Ω）。 |
| `ang` | `number` | 第一次故障阻抗角（度）。 |
| `time` | `number` | 第一次故障时间限制（ms）。 |
| `i` | `number` | 第一次故障短路电流（A）。 |
| `ft2` | `number` | 转换故障类型（10 种），有转换时填写。 |
| `z2` | `number` | 转换故障阻抗值（Ω）。 |
| `ang2` | `number` | 转换故障阻抗角（度）。 |
| `time2` | `number` | 转换故障时间限制（ms）。 |
| `i2` | `number` | 转换故障短路电流（A）。 |
| `after` | `number` | 触发时刻：`0`=第一次故障跳闸后，`1`=重合闸后。 |
| `delay` | `number` | 触发时刻之后延迟多少 ms 施加转换故障。 |

---

## 3. Payload 实验全局控制参数

```json
"payload": {
  "trigger": {
    "type": 1,
    "time": 20000.0,
    "mask": 7
  },
  "ctrl": {
    "closeAngle": { "mode": 1, "angle": 0.0 },
    "attenuation": { "tauMs": 100.0, "superimposeDC": true },
    "pt": 0,
    "ct": 0,
    "cb": {
      "sim": true,
      "trip": 20.0,
      "close": 20.0
    }
  },
  "di": {
    "A": 2, "B": 2, "C": 2,
    "R": 3,
    "a": 0, "b": 0, "c": 0,
    "r": 0
  },
  "do": {
    "init": 0,
    "mode": 1,
    "flipAll": { "delay": 50, "hold": 100 },
    "custom": {
      "1": { "action": 1, "delay": 20, "hold": 50 },
      "2": { "action": 0 }
    }
  },
  "sys": {
    "v": 57.735,
    "f": 50.0,
    "iLoad": { "amp": 0.0, "ang": 0.0 }
  },
  "model": {
    "type": 0,
    "kl": { "formula": 0, "p1": 0.667, "p2": 0.0 }
  },
  "output": {
    "vMode": 0,
    "iMode": 0,
    "ux": {
      "en": true,
      "type": 4,
      "syncV": 57.735,
      "syncAng": 0.0
    }
  }
}
```

### 3.1 Trigger（触发方式）

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `number` | `0`=KEY（按键）/ `1`=TIME（定时）/ `2`=DI（开入） |
| `time` | `number` | **type=1**：故障前时间（ms）。 |
| `mask` | `number` | **type=2**：开入信号掩码。 |

> **KEY 触发机制**（`type=0`）：前端发 `{"cmd":"next_node"}` 经 WebSocket → 后端 `onWebCommand` → `trigNode(FAULT1)` 推进到故障态。节点 1 无自动出口，仅靠前端按键驱动。

### 3.2 Ctrl（控制参数）

| 字段 | 类型 | 说明 |
|------|------|------|
| `pt` | `number` | PT 安装侧：`0`=母线侧，`1`=线路侧。 |
| `ct` | `number` | CT 极性：`0`=指向线路，`1`=指向母线。 |
| `cb.sim` | `boolean` | 是否模拟断路器跳合闸延时。 |
| `cb.trip` | `number` | 断路器跳闸延时（ms）。 |
| `cb.close` | `number` | 断路器合闸延时（ms）。 |
| `closeAngle`, `attenuation` | `object` | 预留参数，当前硬件不处理。 |

### 3.3 DI（开入定义）

物理开入通道：`A/B/C`（0/1/2），`R`（3），`a/b/c`（4/5/6），`r`（7）。

| 字段 | 允许值 | 说明 |
|------|--------|------|
| `A/B/C/a/b/c` | `0` / `1` / `2` | `0`=OTHER，`1`=THREE_TRIP，`2`=TRIP_ABC（A跳a，B跳b，C跳c） |
| `R/r` | `3` / `0` | `3`=RECLOSER（重合闸接点），`0`=OTHER |

### 3.4 DO（故障后开出控制）

| 字段 | 类型 | 说明 |
|------|------|------|
| `init` | `number` | 故障前开出初始状态（8bit 掩码），默认 0。 |
| `mode` | `number` | `0`=SYNC_WITH_RECLOSER（随跳合闸变化）/ `1`=DELAY_FLIP_ALL（延时统一翻转）/ `2`=CUSTOM（自定义） |
| `flipAll` | `object` | **mode=1**：延时 `delay` ms 后翻转所有 DO，保持 `hold` ms 后翻转回。 |
| `custom` | `object` | **mode=2**：键为 DO 通道号，`action` 为 `0`(HOLD) 或 `1`(FLIP)（FLIP 需提供 `delay`/`hold`）。 |

### 3.5 Sys（额定系统参数）

| 字段 | 类型 | 说明 |
|------|------|------|
| `v` | `number` | 额定电压（V）。 |
| `f` | `number` | 额定频率（Hz）。 |
| `iLoad.amp` | `number` | 负荷电流幅值（A）。 |
| `iLoad.ang` | `number` | 负荷电流角度（度）。 |

### 3.6 Model（计算模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `number` | `0`=IF_CONSTANT（If 恒定）/ `1`=UF_CONSTANT（Uf 恒定）/ `2`=ZS_CONSTANT（Zs 恒定）。**此实验固定为 `0`**。 |
| `kl.formula` | `number` | kL 计算公式：`0`=直接给定，`1`=分量法(kR+jkX)，`2`=阻抗比法(Z0/Z1) |
| `kl.p1` | `number` | **formula=0**：kL 幅值。**formula=1**：kR。**formula=2**：Z0/Z1 幅值。 |
| `kl.p2` | `number` | **formula=0**：kL 角度（°）。**formula=1**：kX。**formula=2**：Z0/Z1 角度（°）。 |

**三种 formula 示例：**
```json
// formula=0：直接给定 kL（p1=幅值, p2=角度°）
"kl": { "formula": 0, "p1": 0.667, "p2": 0.0 }

// formula=1：分量法（p1=kR, p2=kX）
"kl": { "formula": 1, "p1": 0.2, "p2": 0.8 }

// formula=2：阻抗比法（p1=Z0/Z1 幅值, p2=Z0/Z1 角度°）
"kl": { "formula": 2, "p1": 3.0, "p2": 0.0 }
```

### 3.7 Output（输出配置）

| 字段 | 类型 | 说明 |
|------|------|------|
| `vMode` | `number` | `0`=ABC / `1`=XYZ |
| `iMode` | `number` | `0`=ABC / `1`=XYZ / `2`=ABC_XYZ_PARALLEL（并联输出） |
| `ux.en` | `boolean` | 是否启用 Ux 输出。 |
| `ux.type` | `number` | `0`=3U0 `1`=-3U0 `2`=3√3U0 `3`=-3√3U0 `4`=检同期A `5`=检同期B `6`=检同期C `7`=检同期AB `8`=检同期BC `9`=检同期CA |
| `ux.syncV` | `number` | **仅检同期有效**。跳闸后线路抽取电压（V）。 |
| `ux.syncAng` | `number` | **仅检同期有效**。跳闸后线路抽取电压角度（度）。 |

---

每测完一个 point 立即发一次 `sendReport`，`data` 只含该点的 `id` 与所记录的时间（未发生的留 `null`，单位秒）：

```json
{
  "type": "report",
  "module": "lp_test",
  "data": {
    "id": 12,
    "tripA": 0.0231,
    "tripB": null,
    "tripC": null,
    "reclose": 0.5012,
    "postAccel": 0.0185
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 前端给定的测点编号（原样回传）。 |
| `tripA/B/C` | `number \| null` | 相对故障开始的跳闸时间（秒）。 |
| `reclose` | `number \| null` | 相对断路器断开的重合闸时间（秒）。 |
| `postAccel` | `number \| null` | 相对重合闸的后加速跳闸时间（秒），仅永久故障。 |
