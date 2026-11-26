# 滑差实验 API（`slip_test`）

> 模块名：`slip_test`
>
> 请求字段示例：项目根目录 `slip.json`
>
> 实现设计：`RelayProtection/docs/experiment-implementation-logic/08-slip.md`

## 1. 文档状态

本文定义滑差实验的目标前端协议，重点说明所有枚举、选择编号、物理
值口径和五类报告。

项目根目录的 `slip.json` 提供完整字段和五类 payload 示例。其中
type0 第一轮 `slipStart == actionSearchStart` 是合法且有意义的
零距离轮次：先保持复归时间，再以同一物理值进入等待阶段监听 DI。
项目根目录旧 `api_slip_test.md` 和其他历史文档只作为枚举来源，
不再作为协议依据。

`RelayProtection/api/ApiSlipTest.py` 已按本文规则实现线电压选择、
五类实验、零距离轮次、搜索状态机和硬件时间戳计时。

`slip.json` 的文件结构需要注意：

- 第一个对象是完整的 `start` 请求；
- 后面的五段 `"payload": {...}` 是相互独立的分型示例；
- 因此整个文件不是一个可一次性解析的单一 JSON 文档。

## 2. 消息格式

### 2.1 启动

```json
{
  "module": "slip_test",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "sys": {},
    "statics": {},
    "payload": {}
  }
}
```

### 2.2 停止

```json
{
  "cmd": "stop"
}
```

滑差实验不使用 `next_node`、`update_static` 或前端自定义 nodes。

### 2.3 额外字段

为了兼容前端旧版本，请求可以携带未定义的附加字段。后端只读取当前
`testType` 所需字段；未使用的额外字段直接忽略。

已被读取的字段必须满足本文的类型、枚举和范围要求。

## 3. `slip.json` 完整请求示例

下面是 `slip.json` 第一段完整请求的等价紧凑表示：

```json
{
  "module": "slip_test",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "sys": {
      "doMask": 0,
      "doCtrlMask": 0,
      "logicMask": 7
    },
    "statics": {
      "0":  {"0": [0, 50], "1": [81.64962003, 0]},
      "1":  {"0": [0, 50], "1": [81.64962003, -120]},
      "2":  {"0": [0, 50], "1": [81.64962003, 120]},
      "3":  {"0": [0, 50], "1": [81.64962003, 0]},
      "4":  {"0": [0, 50], "1": [81.64962003, -120]},
      "5":  {"0": [0, 50], "1": [81.64962003, 120]},
      "6":  {"0": [0, 50], "1": [0, 0]},
      "7":  {"0": [0, 50], "1": [0, -120]},
      "8":  {"0": [0, 50], "1": [0, 120]},
      "9":  {"0": [0, 50], "1": [0, 0]},
      "10": {"0": [0, 50], "1": [0, -120]},
      "11": {"0": [0, 50], "1": [0, 120]}
    },
    "payload": {
      "testType": 0,
      "slipVarType": "0_0",
      "slipPhasor": 2,
      "slipPhase": "0",
      "slipStart": 50,
      "actionSearchStart": 50,
      "actionSearchEnd": 40,
      "actionSearchStep": -0.1,
      "slipDDt": -1,
      "returnTime": 1000,
      "waitTime": 1000
    }
  }
}
```

该例表示：

- 执行 `testType=0` 动作值搜索；
- 主目标是通道0的频率；
- 每轮从50 Hz复归；
- 第一轮终点也是50 Hz；
- 未动作时终点每轮减少0.1 Hz，最终搜索到40 Hz；
- 非零距离轮次以 `-1 Hz/s` 扫描；
- `logicMask=7` 表示监视 DI0、DI1、DI2 的变位。

第一轮起点和终点相同，因此该轮不生成 Sweep step。后端执行：

```text
在50 Hz保持 returnTime
→ 直接进入50 Hz终点等待状态
→ 保持 waitTime 并监听DI
```

若第一轮未动作，第二轮从50 Hz扫描到49.9 Hz，后续继续按搜索序列
执行。

## 4. `params` 总体结构

| 字段 | 类型 | 是否必需 | 含义 |
|---|---:|---:|---|
| `debounce` | number | 建议显式发送 | DI 防抖时间，单位 ms |
| `sys` | object | 是 | DI/DO 位掩码 |
| `statics` | object | 是 | 12个通道的初始物理状态 |
| `payload` | object | 是 | 实验类型、扫描目标和搜索参数 |
| `limits` | object | 否 | 当前滑差协议不读取，存在时忽略 |

## 5. `statics`

结构：

```text
{
  "API通道号": {
    "层号": [物理量1, 物理量2]
  }
}
```

本实验使用：

| 层 | 数组 | 含义 |
|---:|---|---|
| `"0"` | `[直流幅值, 频率]` | `slipPhasor=2` 修改频率 |
| `"1"` | `[交流幅值, 相角]` | `slipPhasor=0` 修改幅值；`1` 修改相角 |

通道：

| API 通道 | 物理通道 |
|---:|---|
| `0` | UA |
| `1` | UB |
| `2` | UC |
| `3` | UX |
| `4` | UY |
| `5` | UZ |
| `6` | IA |
| `7` | IB |
| `8` | IC |
| `9` | IX |
| `10` | IY |
| `11` | IZ |

JSON 中始终发送 API 通道号，硬件通道映射由后端统一完成。

后端实际使用的通道必须具备对应层的两个有限数：

- 幅值/相角以及闭锁量需要层1 `[幅值, 相角]`；
- 频率需要层0 `[直流幅值, 频率]`；
- 关键层缺失时应报告参数错误，不自动补默认相量。

## 6. 物理值口径

### 6.1 扫描起点覆盖 `statics`

后端先复制 `statics`，然后用 `slipStart` 覆盖所有被滑差选中通道
的目标分量：

```text
幅值扫描：覆盖层1幅值
角度扫描：覆盖层1相角
频率扫描：覆盖层0频率
系统频率：覆盖全部12通道的层0频率
```

因此 RESET/复归状态输出的是扫描起点，不是目标分量原来的静态值。
未被滑差选择的通道、层和另一个分量继续使用 `statics`。

线电压幅值的起点按下一节先除以 `√3` 再写入展开通道。

### 6.2 幅值

所有电压和电流输入都是幅度值，不是有效值：

```text
后端不再乘 √2
```

这里描述的是实验输入和波形执行口径。`testType=2` 幅值报告临时
`/√2` 的前端兼容例外见第13节，不反向改变任何输入或输出波形。

只有线电压幅值需要转换为相通道幅值：

```text
实际相通道幅值 = 前端线电压幅值 / √3
```

该规则只用于幅值，不用于角度或频率。

### 6.3 角度

角度单位为度。

被滑差选中的通道以 `slipStart` 覆盖 `statics` 中原来的相角。
复归状态输出该扫描起点；未被选中的通道仍保持 `statics`。

### 6.4 频率

频率单位为 Hz，直接使用前端物理值。

## 7. `testType` 枚举

| 值 | 名称 | 实验目的 | 搜索量 | 报告字段 |
|---:|---|---|---|---|
| `0` | 动作值搜索 | 搜索会使保护动作的扫描终点 | `actionSearch*` | `actionValue` |
| `1` | 动作时间 | 单次扫描并测量硬件动作时间 | 无逐轮搜索 | `timerStartValue`, `actionTime` |
| `2` | 滑差闭锁值搜索 | 搜索变化率大小 | `ddtSearch*` | `slipLockValue` |
| `3` | 电流闭锁值搜索 | 搜索闭锁电流幅值 | `lockSearch*` | `currentLockValue` |
| `4` | 电压闭锁值搜索 | 搜索闭锁电压幅值 | `lockSearch*` | `voltageLockValue` |

只允许 `0..4`。

## 8. `slipVarType` 枚举

`slipVarType` 选择主滑差目标的通道类别：

| 值 | 含义 | 通道选择规则 |
|---|---|---|
| `"0_0"` | 相电压 | `slipPhase` 使用实际电压通道 `0..5` |
| `"0_1"` | 相电流 | `slipPhase` 使用实际电流通道 `6..11` |
| `"2_0"` | 线电压 | `slipPhase` 使用第10节的线电压编号 `0..8` |
| `"5"` | 系统频率 | 固定作用于全部通道 `0..11` |

相电压、相电流不会自动互相偏移：

- `"0_0"` 的 `"0"` 就是 UA；
- `"0_1"` 的 `"6"` 才是 IA；
- 后端不会把 `"0_1"` 的 `"0"` 自动转换成通道6。

当 `slipVarType="5"` 时：

- `slipPhasor` 被忽略并固定为频率；
- `slipPhase` 被忽略；
- 12个通道的频率同时变化。

## 9. `slipPhasor` 枚举

| 值 | 含义 | 写入位置 | `slipStart/slipEnd` 单位 | `slipDDt` 单位 |
|---:|---|---|---|---|
| `0` | 电压/电流幅值 | 层1第0项 | V 或 A（幅度） | V/s 或 A/s |
| `1` | 相角 | 层1第1项 | ° | °/s |
| `2` | 频率 | 层0第1项 | Hz | Hz/s |

只允许 `0、1、2`。

`slipVarType` 和 `slipPhasor` 是两个不同维度：

- `slipVarType` 选择目标通道类别；
- `slipPhasor` 选择这些通道上要变化的幅值、角度或频率。

例如：

```json
{
  "slipVarType": "0_0",
  "slipPhasor": 2,
  "slipPhase": "0,1,2"
}
```

表示只改变 UA、UB、UC 三个电压通道的频率。

## 10. `slipPhase`

### 10.1 相电压/相电流模式

`slipPhase` 是逗号分隔的实际 API 通道号：

```json
{"slipPhase": "0"}
```

```json
{"slipPhase": "0,1,2"}
```

约束：

- `slipVarType="0_0"` 时全部编号必须在 `0..5`；
- `slipVarType="0_1"` 时全部编号必须在 `6..11`；
- 可以同时选择多个同类通道；
- 不允许 `"1,2,6"` 这种电压/电流混合选择；
- 不允许空 token、非整数、重复或越界编号。

### 10.2 线电压模式

`slipVarType="2_0"` 时，`slipPhase` 改为线电压选择编号：

| 编号 | 物理量 | 后端展开通道 |
|---:|---|---|
| `0` | Uab | `0,1` |
| `1` | Ubc | `1,2` |
| `2` | Uca | `2,0` |
| `3` | Uxy | `3,4` |
| `4` | Uyz | `4,5` |
| `5` | Uzx | `5,3` |
| `6` | Uabc | `0,1,2` |
| `7` | Uxyz | `3,4,5` |
| `8` | Uabc、Uxyz | `0,1,2,3,4,5` |

编号 `6、7、8` 是组合选择，不是 API 电流通道。

线电压模式只允许发送一个编号 `0..8`，不能发送 `"0,1,2"` 这种
组合字符串。编号 `6`、`7`、`8` 已分别表示 Uabc、Uxyz 和两组三相。

### 10.3 系统频率模式

`slipVarType="5"` 时完全忽略 `slipPhase`。为了保持 JSON 结构，
前端仍可发送 `"0"`，但它不表示只选择通道0。

## 11. `testType=0`：动作值搜索

字段：

```json
{
  "testType": 0,
  "slipVarType": "0_0",
  "slipPhasor": 2,
  "slipPhase": "0,1,2",
  "slipStart": 40,
  "actionSearchStart": 40,
  "actionSearchEnd": 39.96,
  "actionSearchStep": -0.03999999999999915,
  "slipDDt": -1,
  "returnTime": 1000,
  "waitTime": 1000
}
```

每轮：

```text
slipStart 保持 returnTime
→ 以 slipDDt 扫描到当前 actionSearchValue
→ 终点保持 waitTime
```

第一轮终点为 `actionSearchStart`。未动作则终点增加
`actionSearchStep`，直到实际执行完 `actionSearchEnd`。

`slipStart` 和 `actionSearchStart` 含义不同：

- `slipStart` 是每轮复归值和扫描起点；
- `actionSearchStart` 是第一轮扫描终点；
- 两者可以相等；相等时该轮跳过 Sweep，只执行复归保持和终点等待。

动作时立即停止，报告当前轮终点：

```json
{
  "testType": 0,
  "actionValue": 39.98
}
```

它不是根据 DI 时间戳反算的动作瞬时物理值。

搜索耗尽：

```json
{
  "testType": 0,
  "actionValue": null
}
```

## 12. `testType=1`：动作时间

字段：

```json
{
  "testType": 1,
  "slipVarType": "0_1",
  "slipPhasor": 0,
  "slipPhase": "6",
  "slipStart": 56.5685425,
  "slipEnd": 56.51197396,
  "slipDDt": -1.41421356,
  "timerStartValue": 56.5685425,
  "returnTime": 1000,
  "waitTime": 1000
}
```

只执行一轮：

```text
slipStart 保持 returnTime
→ 以 slipDDt 扫描到 slipEnd
→ 终点保持 waitTime
```

Sweep 间隔固定10 ms。后端先生成真实离散 tick，再选择与
`timerStartValue` 最接近的 tick；距离相同时选择较早 tick。

若 `slipStart == slipEnd`，不生成 Sweep tick。复归完成后直接进入
WAIT，`timerStartValue` 必须等于这个共同值，计时起点使用 WAIT
节点进入时的 FPGA 硬件时间戳；复归时间不计入动作时间。

后端记录该 tick 的 FPGA 硬件时间戳。动作时间为：

```text
DI动作硬件时间戳 - 计时tick硬件时间戳
```

单位为 ms，报告保留1位小数：

```json
{
  "testType": 1,
  "timerStartValue": 56.5685425,
  "actionTime": 12.3
}
```

`timerStartValue` 必须位于 `slipStart` 和 `slipEnd` 的闭区间。

未动作、动作早于计时点或没有得到合法计时起点时：

```json
{
  "testType": 1,
  "timerStartValue": 56.5685425,
  "actionTime": null
}
```

从计时 tick 到最迟动作窗口结束的时间必须小于 U32 微秒时间戳的
正向半圈（约35分47.484秒），后端还会预留安全余量；超过时启动
请求会被拒绝。

## 13. `testType=2`：滑差闭锁值搜索

字段：

```json
{
  "testType": 2,
  "slipVarType": "0_0",
  "slipPhasor": 0,
  "slipPhase": "0",
  "slipStart": 70.71067812,
  "slipEnd": 56.5685425,
  "slipDDt": -1.41421356,
  "ddtSearchStart": 7.7781746,
  "ddtSearchEnd": 3.53553391,
  "ddtSearchStep": -0.14142135,
  "returnTime": 1000,
  "waitTime": 1000
}
```

`ddtSearchStart/End` 表示变化率大小；`ddtSearchStep` 表示搜索值
每轮的增量，因此可以为负。

非零距离 Sweep 中，每一轮实际使用的变化率大小必须大于0，因此
`ddtSearchStart` 到 `ddtSearchEnd` 的搜索序列不能包含0。

实际 Sweep 方向取 `slipDDt` 的符号：

```text
本轮实际 d/dt
  = sign(slipDDt) × abs(当前 ddtSearchValue)
```

上例的主变量从高值向低值扫描，所以：

```text
slipDDt < 0
当前 ddtSearchValue > 0
本轮实际 d/dt < 0
```

这一区分非常重要。不能把正的 `ddtSearchStart` 直接作为正向 Sweep
增量。

若 `slipStart == slipEnd`，仍按统一零距离规则执行
RESET→WAIT，不生成实际 d/dt step；此时 `slipDDt` 只保留为协议
字段，可以为0。只有非零距离的 type2 才要求 `slipDDt` 提供明确的
非零方向。

动作时：

```json
{
  "testType": 2,
  "slipLockValue": 5.5
}
```

临时前端兼容规则：

- 当主变量是电压或电流幅值（`slipPhasor=0`）时，实验执行和
  `ddtSearch*` 输入仍使用幅度值，但报告暂时返回有效值：
  `slipLockValue = 当前ddtSearchValue / √2`；
- 当主变量是相角或频率时，报告不做 `√2` 换算；
- 该兼容只用于 `testType=2` 的 `slipLockValue`。type0、type3、
  type4 报告口径不变。

这是针对当前前端遗漏幅度值到有效值换算的临时后端处理，修复前端
后应同步移除。

搜索耗尽：

```json
{
  "testType": 2,
  "slipLockValue": null
}
```

## 14. `testType=3`：电流闭锁值搜索

字段：

```json
{
  "testType": 3,
  "slipVarType": "0_0",
  "slipPhasor": 0,
  "slipPhase": "0,1,2,3,4,5",
  "slipStart": 70.71067812,
  "slipEnd": 56.5685425,
  "slipDDt": -1.41421356,
  "lockPhase": "6",
  "lockSearchStart": 0.14142136,
  "lockSearchEnd": 1.41421357,
  "lockSearchStep": 0.14142136,
  "returnTime": 1000,
  "waitTime": 1000
}
```

主滑差变量只允许：

- 相电压 `"0_0"`；
- 线电压 `"2_0"`；
- 全通道频率 `"5"`。

不允许把相电流 `"0_1"` 作为主滑差变量。

`lockPhase` 是实际电流通道号，允许一个或多个 `6..11`：

```text
"6"     → IA
"6,7,8" → IA、IB、IC
```

进入本轮复归状态时，所选电流通道幅值设置为当前
`lockSearchValue`。该值已经是幅度值，不做 `√2` 或 `√3` 换算。

动作时：

```json
{
  "testType": 3,
  "currentLockValue": 0.8
}
```

搜索耗尽：

```json
{
  "testType": 3,
  "currentLockValue": null
}
```

## 15. `testType=4`：电压闭锁值搜索

字段：

```json
{
  "testType": 4,
  "slipVarType": "5",
  "slipPhasor": 2,
  "slipPhase": "0",
  "slipStart": 50,
  "slipEnd": 40,
  "slipDDt": -1,
  "lockVarType": "0_0",
  "lockPhase": "0",
  "lockSearchStart": 0.14142136,
  "lockSearchEnd": 1.41421357,
  "lockSearchStep": 0.14142136,
  "returnTime": 1000,
  "waitTime": 1000
}
```

主滑差固定为：

```text
slipVarType="5"
全部12通道频率一起变化
```

### 15.1 `lockVarType`

| 值 | 含义 | `lockPhase` |
|---|---|---|
| `"0_0"` | 相电压闭锁幅值 | 实际电压通道 `0..5` |
| `"2_0"` | 线电压闭锁幅值 | 第10.2节线电压编号 `0..8` |

相电压：

```text
实际通道幅值 = lockSearchValue
```

线电压：

```text
lockPhase 只能是单个线电压编号0..8
按编号展开对应相通道
实际相通道幅值 = lockSearchValue / √3
```

动作时都报告前端逻辑电压值：

```json
{
  "testType": 4,
  "voltageLockValue": 12.0
}
```

线电压模式也不会报告 `/√3` 后的相电压值。

搜索耗尽：

```json
{
  "testType": 4,
  "voltageLockValue": null
}
```

## 16. 公共数值字段

| 字段 | 单位 | 含义 |
|---|---|---|
| `slipStart` | 由 `slipPhasor` 决定 | 每轮复归值和主 Sweep 起点 |
| `slipEnd` | 同上 | type1..4 的主 Sweep 终点 |
| `slipDDt` | 对应单位/s | 主 Sweep 方向；type0/1/3/4 同时表示实际变化率 |
| `returnTime` | ms | 在起点保持的复归时间 |
| `waitTime` | ms | 到达终点后的等待时间 |
| `timerStartValue` | 主变量单位 | type1 的计时起算物理值 |

`returnTime` 和 `waitTime` 必须是大于0的整数。0不能表示立即跳转。
单节点时间上限为 `2146483 ms`；该上限同时避免主机 timeout 浮点
溢出，并给32位微秒硬件时间戳保留1秒安全余量。

非零距离 Sweep 中，`slipDDt` 的符号必须能从起点走向终点：

```text
(终点 - slipStart) × 实际d/dt > 0
```

所有会写入 FPGA 的静态值、复归值、终点和闭锁值还必须处于硬件
范围内：

| 物理量 | 后端范围 |
|---|---:|
| 相电压/电压通道幅值 | 绝对值不超过 `200 V` |
| 相电流/电流通道幅值 | 绝对值不超过 `60 A` |
| 线电压逻辑幅值 | 展开并 `/√3` 后每相不超过 `200 V` |
| 频率 | `0 .. 19531.24999 Hz` |

后端在寄存器转换前检查这些范围，防止32位寄存器取模回绕成另一
个危险输出值。

## 17. 搜索三元组

| `testType` | 字段 | 搜索量 |
|---:|---|---|
| `0` | `actionSearchStart/End/Step` | 每轮主 Sweep 终点 |
| `2` | `ddtSearchStart/End/Step` | 变化率大小 |
| `3` | `lockSearchStart/End/Step` | 电流闭锁幅值 |
| `4` | `lockSearchStart/End/Step` | 电压闭锁幅值 |

统一语义：

```text
Start = 第一轮值
Step  = 每轮未动作后的增量
End   = 最终必须实际执行的一轮
```

要求：

- `Step != 0`；
- `Start != End` 时，Step 必须能从 Start 走到 End；
- End 这一轮完成后仍未动作，才报告 `null`；
- Start 等于 End 时只执行一轮。

## 18. 固定10 ms Sweep

每个动态 tick 的名义逻辑增量：

```text
nominalStep = 本轮实际d/dt × 0.010
```

所有 Sweep tick 使用相同增量：

```text
step = 本轮实际d/dt × 0.010
count = floor(abs((endpoint - slipStart) / step))
```

若浮点比值非常接近整数，后端先按容差吸附到该整数，避免数学上
应为4步却因浮点误差得到3步。

对于非零距离轮次，参数必须至少产生1个完整10 ms step。

最后一个 Sweep tick 不要求精确等于 endpoint，也不生成尾步或
寄存器校正。10 ms 的步长精度已经满足实验要求。Sweep 计数结束后
通过 `COUNT_OVER` 进入终点等待状态，由该状态直接输出 endpoint 并
保持 `waitTime`。

若 `endpoint == slipStart`，该轮合法，但不创建空 Sweep。RESET
保持 `returnTime` 后直接跳到 WAIT；WAIT 以同一物理值保持
`waitTime` 并监听 DI。这样避免底层把空 `steps` 的 SWEEP 退化为
不会执行 `COUNT_OVER` 的永久 STATIC。

## 19. `sys`

### 19.1 `logicMask`

`logicMask` 是8位 DI 监视掩码：

| 位 | 值 | DI |
|---:|---:|---|
| 0 | `0x01` | DI0 |
| 1 | `0x02` | DI1 |
| 2 | `0x04` | DI2 |
| 3 | `0x08` | DI3 |
| 4 | `0x10` | DI4 |
| 5 | `0x20` | DI5 |
| 6 | `0x40` | DI6 |
| 7 | `0x80` | DI7 |

任一选中位发生变位即可判为动作，不区分 OPEN/CLOSE 方向。

为兼容现有协议：

```text
logicMask=0 → 监视全部8路
```

API 只在主 Sweep 和终点等待阶段判动作；复归阶段的 DI 变化只更新
基线。

后端节点条件为：

```text
节点2 SWEEP：
  diMatchMask = effectiveLogicMask | 0x200

节点3 WAIT：
  diMatchMask = effectiveLogicMask | 0x200
```

两者命中后都先跳内部动作节点，由 API 使用实际 DI 硬件时间戳生成
报告并停止；不会把复归节点配置为动作触发状态。

节点1的 `tick=0` 硬件时间戳加 `returnTime` 构成本轮动作监视
边界。API 把后续 SWEEP、节点切换和 WAIT 视为一条连续硬件时间轴：
不早于该边界的第一次受监视 DI 变位用于报告；更早的复归阶段旧
变位不参与。这样节点切换时重新捕获 DI 基线也不会吞掉窗口内动作。

### 19.2 `doMask`、`doCtrlMask`

两个字段都是8位值，但语义不同：

| 字段 | 阶段 |
|---|---|
| `doMask` | 复归阶段的绝对 DO 输出 |
| `doCtrlMask` | 进入控制阶段时需要相对 `doMask` 翻转的位掩码 |

控制阶段的绝对输出为：

```text
controlDo = doMask XOR doCtrlMask
```

例如 `doMask=0b1010`、`doCtrlMask=0b0110`，则节点2、3进入时都
立即输出 `0b1100`。

`doCtrlMask=0` 表示不翻转任何位，控制阶段继续输出 `doMask`。
如果字段缺失也按0处理。

节点2 SWEEP 和节点3 WAIT 都配置相同的
`doActions=[controlDo]`，delay为0。零距离轮次虽然跳过节点2，
节点3仍会得到完全相同的控制阶段 DO 输出。

## 20. 后端运行流程

前端不需要理解内部节点 ID，但整体流程为：

```text
复归
  输出 slipStart 和本轮闭锁值
  保持 returnTime
        ├─ endpoint == slipStart → 直接进入终点等待
        ↓  endpoint != slipStart
快速 Sweep
  每10 ms执行一个增量
  DI动作 → 报告并停止
  COUNT_OVER → 终点等待
        ↓
终点等待
  直接输出 endpoint，保持 waitTime
  DI动作 → 报告并停止
        ↓
搜索型：下一搜索轮
动作时间型：报告null并停止
```

内部实验节点只能使用 `1..0xFFFE`，不覆盖保留节点 `0x0000` 和
`0xFFFF`。

## 21. 报告外层格式

`EngineCtrl` 统一包装：

```json
{
  "type": "report",
  "module": "slip_test",
  "data": {}
}
```

`data` 只包含当前 `testType` 的字段。

### 21.1 动作值

```json
{
  "testType": 0,
  "actionValue": 48.5
}
```

### 21.2 动作时间

```json
{
  "testType": 1,
  "timerStartValue": 49.5,
  "actionTime": 150.2
}
```

`actionTime` 单位为 ms，来自 FPGA 硬件时间戳。

### 21.3 滑差闭锁值

```json
{
  "testType": 2,
  "slipLockValue": 2.5
}
```

### 21.4 电流闭锁值

```json
{
  "testType": 3,
  "currentLockValue": 0.8
}
```

### 21.5 电压闭锁值

```json
{
  "testType": 4,
  "voltageLockValue": 12.0
}
```

搜索耗尽或单次动作时间实验未动作时，对应结果字段为 `null`。

## 22. 其他消息

模拟量、DI 和 DO 更新分别发送：

```json
{
  "type": "value_update",
  "module": "slip_test",
  "static": {
    "0": {
      "0": [0, 49.99]
    }
  }
}
```

```json
{
  "type": "value_update",
  "module": "slip_test",
  "di": 7
}
```

```json
{
  "type": "value_update",
  "module": "slip_test",
  "do": 3
}
```

停止：

```json
{
  "type": "stop",
  "module": "slip_test"
}
```

错误：

```json
{
  "type": "error",
  "module": "slip_test",
  "msg": "错误描述"
}
```

## 23. 五类字段清单

| `testType` | 必须读取的 `payload` 字段 |
|---:|---|
| `0` | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `actionSearchStart`, `actionSearchEnd`, `actionSearchStep`, `slipDDt`, `returnTime`, `waitTime` |
| `1` | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `timerStartValue`, `returnTime`, `waitTime` |
| `2` | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `ddtSearchStart`, `ddtSearchEnd`, `ddtSearchStep`, `returnTime`, `waitTime` |
| `3` | `testType`, `slipVarType`, `slipPhasor`, `slipPhase`, `slipStart`, `slipEnd`, `slipDDt`, `lockPhase`, `lockSearchStart`, `lockSearchEnd`, `lockSearchStep`, `returnTime`, `waitTime` |
| `4` | `testType`, `slipVarType`, `slipStart`, `slipEnd`, `slipDDt`, `lockVarType`, `lockPhase`, `lockSearchStart`, `lockSearchEnd`, `lockSearchStep`, `returnTime`, `waitTime` |

type4 中 `slipPhasor`、`slipPhase` 可以为保持前端结构而发送，但后端
不读取其值。

## 24. 实现边界

本文协议由以下文件实现：

```text
RelayProtection/api/ApiSlipTest.py
```

不需要修改 `BaseApi.py`、`EngineCtrl.py` 或 `USEEngine.py`。现有公共
接口已经能提供节点下发、tick 硬件时间戳、DI 硬件时间戳、报告和
停止能力。
