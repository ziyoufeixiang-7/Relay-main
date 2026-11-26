# 滑差实验实现逻辑与 `ApiSlipTest.py` 实现说明

> 模块：`slip_test`
>
> 前端协议：`RelayProtection/docs/api/api_slip_test.md`
>
> 请求字段示例：项目根目录 `slip.json`（type0 第一轮是合法零距离轮次）
>
> 目标代码：`RelayProtection/api/ApiSlipTest.py`

## 1. 文档结论

滑差实验应由 `ApiSlipTest.py` 把前端的业务参数编译成
`ApiNodeData`，再仅通过 `EngineCtrl` 已有的
`upsertNodes()`、`trigNode()`、`sendReport()` 和
`stopTest()` 完成实验。

这次实现不需要修改：

- `BaseApi.py`；
- `EngineCtrl.py`；
- `USEEngine.py`；
- FPGA 指令或公共节点执行逻辑。

`ApiSlipTest.py` 已按本说明整体重写，没有修改公共引擎。实现复用：

- `BaseApi.physDictToReg()` 的物理量转换入口；
- `ApiNodeData` 的 STATIC/SWEEP 节点模型；
- `onUpdate(nodeId, tick, hw_ts)` 提供的节点/tick硬件时间戳；
- `onDi(di, hw_ts)` 提供的 DI 硬件时间戳；
- 现有五类报告字段名。

## 2. 总体处理顺序

收到 `start` 后必须按以下顺序处理：

```text
1. 读取并校验 testType
2. 根据 testType 确定本类实验必需字段
3. 解析主滑差目标：
   slipVarType + slipPhasor + slipPhase
4. testType=3/4 时解析闭锁搜索目标
5. 解析搜索序列、复归时间、等待时间和 DI/DO 掩码
6. 生成本轮逻辑 SweepPlan
7. 把逻辑物理量编译为 ApiNodeData
8. 全部校验和编译成功后再调用 upsertNodes()
9. trigNode(1) 启动
```

前端可能携带旧版本遗留的额外字段。API 只校验和读取当前
`testType` 实际用到的字段，其他字段直接忽略，不能因为未使用的
多余字段拒绝实验。

## 3. 当前内部数据模型

`_parse_config()` 先把 JSON 归一化为只读对象，运行阶段不再重复
解释字符串枚举。当前关键对象是：

```python
@dataclass(frozen=True)
class _SlipTarget:
    var_type: str
    phasor: int
    channels: tuple[int, ...]
    layer: int
    component_index: int
    value_scale: float


@dataclass(frozen=True)
class _LockTarget:
    var_type: str
    channels: tuple[int, ...]
    value_scale: float


@dataclass(frozen=True)
class _SweepPlan:
    endpoint: float
    ddt: float
    step: float
    count: int


@dataclass(frozen=True)
class _RoundPlan:
    sweep: _SweepPlan
    report_value: float | None
    lock_value: float | None
    timer_tick: int | None
```

`_Config` 统一保存五类实验共用配置和预先展开的 `search_values`。
运行时状态与配置分开保存：

```text
配置：
  _config
  _current_round

运行时：
  _round_index
  _current_node_id
  _last_di / _recent_di_events
  _timer_start_hw_ts
  _monitor_arm_hw_ts / _monitor_entry_hw_ts
  _last_sweep_tick_hw_ts / _wait_entry_hw_ts
  _transition_action_hw_ts
  _action_task
  _report_sent / _finishing
```

这样可以避免 `isLineVoltage` 这一类全局布尔量误伤角度或频率转换。

## 4. 主滑差变量解析

### 4.1 `slipVarType`

`slipVarType` 有且只有四种：

| 值 | 含义 | `slipPhase` 的解释 |
|---|---|---|
| `"0_0"` | 相电压 | API 电压通道号，只允许 `0..5` |
| `"0_1"` | 相电流 | API 电流通道号，只允许 `6..11` |
| `"2_0"` | 线电压 | 线电压选择编号，只允许 `0..8` |
| `"5"` | 全通道频率 | 忽略 `slipPhase` 和 `slipPhasor`，固定作用于 `0..11` |

普通相电压/相电流模式允许：

```json
{"slipPhase": "0"}
```

或：

```json
{"slipPhase": "0,1,2"}
```

选择必须全部属于同一类。不能发送 `"1,2,6"` 这种混合电压和电流
通道的组合。

### 4.2 线电压选择编号

`slipVarType="2_0"` 时，`slipPhase` 不再是 API 通道号，必须先按
下表展开：

| 选择编号 | 物理量 | 展开的 API 相电压通道 |
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

线电压模式只允许一个编号 `0..8`，不允许逗号组合。编号 `6`、`7`
和 `8` 已经表达三相或两组三相组合。

不能把编号 `6、7、8` 直接传给 `HWConfig.MapChannel()`；它们是组合
编号，不是电流通道。

### 4.3 `slipPhasor`

除 `slipVarType="5"` 外，`slipPhasor` 决定真正变化的物理分量：

| 值 | 物理分量 | `statics`/`ApiNodeData` 位置 | 单位 |
|---:|---|---|---|
| `0` | 电压或电流幅值 | 层1 `[幅值, 相角]` 第0项 | V 或 A（幅度值） |
| `1` | 相角 | 层1 `[幅值, 相角]` 第1项 | ° |
| `2` | 频率 | 层0 `[直流量, 频率]` 第1项 | Hz |

前端幅值已经是幅度值，不是有效值。后端不乘 `√2`。
该句描述波形编译口径；type2 幅值报告临时 `/√2` 的前端兼容例外
见第12.2节。

### 4.4 `slipVarType="5"`

系统频率模式固定归一化为：

```text
phasor = 2
channels = (0, 1, ..., 11)
layer = 0
component_index = 1
value_scale = 1
```

即使 JSON 同时携带：

```json
{
  "slipPhasor": 0,
  "slipPhase": "0"
}
```

也必须忽略这两个值，全部12个通道的频率一起变化。

## 5. 物理量写入规则

### 5.1 扫描起点覆盖静态值

所有被主滑差选中的通道，其目标分量都必须由 `slipStart` 覆盖
`statics`：

```text
slipPhasor=0：层1幅值 = slipStart
slipPhasor=1：层1相角 = slipStart
slipPhasor=2：层0频率 = slipStart
slipVarType="5"：全部12通道层0频率 = slipStart
```

复归节点的输出就是扫描起点，不是原静态值。未被滑差选中的通道、
层和另一个分量继续保留 `statics`。

普通相电压、相电流不做 `√2` 或 `√3` 换算：

```text
通道实际起点 = slipStart
通道实际步增量 = 逻辑步增量
```

### 5.2 线电压幅值

只有同时满足：

```text
slipVarType == "2_0"
slipPhasor == 0
```

才执行：

```text
实际相通道幅值 = 逻辑线电压幅值 / √3
实际相通道增量 = 逻辑线电压增量 / √3
```

该换算必须同时作用于：

- 复归节点的 `slipStart`；
- Sweep 的每一个幅值增量；
- 终点等待节点的 endpoint；
- `testType=4` 中线电压闭锁搜索值。

搜索范围、`tick_values` 和 `timerStartValue` 始终保留前端逻辑
线电压值，不能使用除以 `√3` 后的相通道值。报告同样绝不做
`/√3`；唯一例外是当前前端兼容要求：type2 线电压幅值报告在逻辑
线电压值基础上再 `/√2` 返回有效值。

此换算成立的物理前提是对应相电压等幅、相差120°：

```text
|Up - Uq| = √3 × Uphase
```

### 5.3 线电压相角与频率

线电压的相角和频率绝不能除以 `√3`：

```text
slipPhasor=1：每个展开通道的相角起点写为slipStart，之后增加同一个角度增量
slipPhasor=2：每个展开通道的频率起点写为slipStart，之后增加同一个频率增量
```

这里不再把 `statics` 相角当作扫描基准；被展开通道的目标字段由
扫描起点直接覆盖。

## 6. 从逻辑值编译物理字典

建议把当前 `_BuildStepPhys()`、`_BuildResetStaticPhys()` 和
`_BuildEndpointStaticPhys()` 合并为三个职责明确的函数：

```python
def apply_main_value(
    statics: dict,
    target: SlipTarget,
    logical_value: float,
) -> dict:
    """返回某个逻辑绝对值对应的完整静态物理字典。"""


def build_main_delta(
    target: SlipTarget,
    logical_delta: float,
) -> dict:
    """返回一个 Sweep tick 的物理增量字典。"""


def apply_lock_value(
    statics: dict,
    target: LockTarget,
    logical_value: float,
) -> dict:
    """把本轮闭锁幅值叠加到完整静态物理字典。"""
```

组合顺序固定为：

```text
deepcopy(statics)
→ 用主变量绝对值覆盖所选通道的目标分量
→ testType=3/4 时应用本轮闭锁值
→ physDictToReg()
```

`testType=3/4` 的主变量层和闭锁变量层不冲突：

- type3：主变量为电压幅值/角度/频率，闭锁量是电流幅值；
- type4：主变量是全通道频率，闭锁量是电压幅值。

## 7. 10 ms 快速 Sweep 的数学模型

### 7.1 固定步长和步数

```text
SLIP_INTERVAL_MS = 10
dt = 0.010 s
distance = endpoint - slipStart
step = roundDDt × 0.010
rawCount = abs(distance / step)
```

非零距离 Sweep 必须满足：

```text
distance != 0
roundDDt != 0
distance × roundDDt > 0
```

步数按完整10 ms步计算：

```python
nearest = round(rawCount)
if is_close(rawCount, nearest):
    count = nearest
else:
    count = math.floor(rawCount)
```

容差吸附用于避免 `3.999999999` 被错误计算成3步。非零距离参数
必须产生至少一个完整 Sweep step。

所有 step 完全相同：

```python
step_reg = physDictToReg(step_phys, is_delta=True)
steps = [step_reg] * count
```

不生成最后一个不足步，也不做寄存器域终点校正。正弦计算和32位
寄存器精度已经满足实验要求。

### 7.2 Sweep tick 与 endpoint

概念上的 Sweep tick 值为：

```python
tick_values = [
    slipStart + tick * step
    for tick in range(count + 1)
]
```

USEEngine 回报：

```text
tick=0：Sweep base，即 slipStart
tick=1：执行第1个固定step后
...
tick=count：执行最后一个固定step后
```

最后一个 Sweep tick 不要求精确等于 endpoint，误差小于一个10 ms
步长。`COUNT_OVER` 后进入节点3，由节点3的静态 base 直接输出
endpoint 并保持 `waitTime`。

应设置单轮最大 Sweep tick 数，避免极小 `d/dt` 生成过大的
`ApiNodeData.steps`。当前实现使用：

```text
MAX_SWEEP_STEPS = 100000
MAX_SEARCH_ROUNDS = 10000
```

## 8. 统一节点状态机

三个波形节点固定为：

| ID | 名称 | 模式 | 作用 |
|---:|---|---|---|
| `1` | RESET | STATIC | 用 `slipStart` 覆盖目标分量，保持 `returnTime` |
| `2` | SWEEP | SWEEP | 固定10 ms、固定 step 扫描 |
| `3` | WAIT | STATIC | 直接输出 endpoint，保持 `waitTime` |

为可靠区分 DI 动作和正常轮次结束，再使用两个不改波形的内部节点：

| ID | 名称 | 作用 |
|---:|---|---|
| `4` | ACTION_LATCH | DI 命中后的报告/停止过渡节点 |
| `5` | ROUND_DONE | 等待超时后的下一轮/搜索结束过渡节点 |

节点 `0x0000` 和 `0xFFFF` 是引擎保留节点，实验 API 绝不能覆盖。

### 8.1 正常时序

```text
节点1 RESET
    ├─ 零距离：TIME_OUT ────────────────► 节点3 WAIT
    │
    └─ 非零距离：TIME_OUT
                    ▼
节点2 SWEEP ── DI_MATCH ─────────────► 节点4 ACTION_LATCH
    │ COUNT_OVER                              │
    ▼                                         └─ 报告并 stopTest()
节点3 WAIT  ── DI_MATCH ─────────────► 节点4 ACTION_LATCH
    │ TIME_OUT
    ▼
节点5 ROUND_DONE
    ├─ 有下一轮：更新节点后 trigNode(1)
    └─ 已耗尽/单次结束：报告 null 并 stopTest()
```

节点2执行完 `count` 个固定 step 后，必须通过：

```text
countOverId = 3
```

直接进入节点3。节点3以静态 base 输出 endpoint，并完整保持
`waitTime`。不再使用节点2总 timeout。

零距离不能编译成 `mode=2, steps=[]`。`USEEngine.runSweep()` 遇到
空 steps 会退化为 STATIC，并且不会处理 `countOverId`。因此零距离
轮次不创建本轮节点2，节点1通过 `timeoutId=3` 直接进入节点3。

### 8.2 节点字段

有效 DI 掩码：

```text
effectiveLogicMask = logicMask if logicMask != 0 else 0xFF
diMatchMask = effectiveLogicMask | 0x200
```

`0x200` 表示相对进入当前节点时的 DI 状态检测变位。

节点1：

```text
mode = 1
base = 用slipStart覆盖主目标 + 本轮lockValue
timeoutMs = returnTime
timeoutId = 2（非零距离）或3（零距离）
doActions = [doMask]
不配置DI触发
```

节点2：

```text
mode = 2
base = {}（复用节点1已经建立的完整slipStart状态）
interval = 10
steps = [同一个固定step] × count
countOverId = 3
diMatchMask = effectiveLogicMask | 0x200
diMatchId = 4
doActions = [doMask XOR doCtrlMask]
```

节点3：

```text
mode = 1
base = 只包含主目标通道/层的稀疏endpoint绝对值
timeoutMs = waitTime
timeoutId = 5
diMatchMask = effectiveLogicMask | 0x200
diMatchId = 4
doActions = [doMask XOR doCtrlMask]
```

节点2、3进入时都以delay 0输出同一个绝对值
`doMask XOR doCtrlMask`。`doCtrlMask` 是相对复归状态的翻转掩码，
不是绝对 DO 值。零距离轮次虽然不进入节点2，节点3仍执行同样动作。

节点4、5使用空 base，不修改当前模拟量和 DO：

```text
mode = 1
base = {}
无timeout
无DI触发
```

DI 不直接跳 `0xFFFF`，而是先进入节点4。这样 API 可以使用
`onDi()` 保存真实 DI 硬件时间戳，再在节点4完成一次报告并调用
`stopTest()`，避免自动终止先清除 API 而丢报告。

进入节点5后：

```text
有下一轮：
  先完整编译下一轮节点1..5
  upsertNodes()
  成功后 trigNode(1)

搜索结束或type1未动作：
  发送对应null报告
  stopTest()
```

### 8.3 DO 语义

DO 计算规则：

```text
复归阶段：resetDo = doMask
控制阶段：controlDo = doMask XOR doCtrlMask
doCtrlMask 字段缺失：按0处理，即不翻转
```

`doActions` 应始终生成绝对值列表，包括：

```python
doActions=[0]
```

空列表表示“不执行 DO 动作”，不等于“输出清零”。节点2、3都明确
输出 `controlDo`，下一轮节点1重新输出 `doMask`。

## 9. DI 动作判定

### 9.1 初始 DI

EngineCtrl 启动实验前会触发 FPGA 保留节点0。FPGA 收到 start 后会
返回一次当前 DI 状态，因此 `_onSetup()` 直接使用：

```python
self.lastDi = self.ctrl.di & 0xFF
```

不需要给 `EngineCtrl` 或 `BaseApi` 新增 `diStart()` 接口。

### 9.2 节点2、3配置硬件 DI 条件

节点1复归阶段不配置 DI 条件。节点2 Sweep 和节点3 WAIT 都配置：

```text
diMatchMask = effectiveLogicMask | 0x200
diMatchId = 4
```

因此动作只在扫描与终点等待期间有效，命中后硬件状态机立即跳到
内部动作节点4。

API 的 `onDi()` 更新 DI 基线，并缓存当前轮受监视位的硬件时间戳：

```python
self.lastDi = di & 0xFF
self.recentDiEvents.append(hw_ts & 0xFFFFFFFF)
```

节点4的 `onUpdate()` 与 `onDi()` 是异步回调，不能依赖到达顺序。
节点4应让出事件循环，等待同一批 DI 回调完成后再生成报告；type1
只选择复归结束之后的第一次有效 DI 变位作为动作
时间戳。后续接点反跳或其他受监视位变化不能覆盖首次动作时刻。

引擎先安装 DI 判据，再下发节点 base 和 `SYS_UPDATE`，所以合法 DI
可能发生在这段下发窗口内，其硬件时间戳略早于节点 `tick=0` ACK。
为把窗口降到最小，节点2不重复下发完整 reset base，节点3只写主
目标的稀疏 endpoint 层。

节点1的 `tick=0` ACK 与 `returnTime` 都已知，因此先计算：

```text
monitorArmHwTs = resetTick0HwTs + returnTime × 1000
```

API 以 `monitorArmHwTs` 为本轮监视起点，把 SWEEP 和 WAIT 视为同一
条连续硬件时间轴。每次进入 SWEEP/WAIT 或收到 DI 时，都会检查
`monitorArmHwTs` 到当前节点入口之间是否已有受监视变位；若有，
直接走同一个动作报告流程。这样 `RESET → SWEEP` 和
`COUNT_OVER → WAIT` 建立新 `diPrev` 时都不会把节点切换窗口中的
合法动作吞成基线，复归阶段更早的旧 DI 也不会参与报告。

若硬件已经进入节点4却始终没有收到相匹配的 DI 硬件时间戳，应走
运行错误停止，不能伪造动作时间。

为兼容现有协议：

```text
logicMask=0 → effectiveLogicMask=0xFF
```

即0仍表示监视全部8路，而不是禁用动作检测。

## 10. `testType=0`：动作值搜索

### 10.1 搜索变量

```text
currentEndpoint =
    actionSearchStart
    + searchIndex × actionSearchStep
```

本轮：

```text
start = slipStart
end = currentEndpoint
ddt = slipDDt
```

每一个非零距离搜索终点都必须位于 `slipDDt` 指向的一侧：

```text
(currentEndpoint - slipStart) × slipDDt > 0
```

`currentEndpoint == slipStart` 合法。该轮在节点1保持
`returnTime` 后直接进入节点3，在相同物理值保持 `waitTime` 并
监听 DI，不执行节点2 Sweep。`slip.json` 的 type0 第一轮就是这一
语义。

如果搜索序列跨过 `slipStart` 到另一侧，应在启动前整体拒绝，不能
到运行中途才发现方向反转。

### 10.2 每轮流程

```text
在 slipStart 保持 returnTime
→ 以 slipDDt 扫描到 currentEndpoint
→ 在 currentEndpoint 保持 waitTime
```

SWEEP 或 WAIT 阶段发生有效 DI 变位：

```json
{
  "testType": 0,
  "actionValue": 48.5
}
```

`actionValue` 是命中轮次的搜索终点，不根据 DI 时间戳反算动作瞬时
值。报告后立即 `stopTest()`。

本轮未动作：

- 尚有下一搜索值：编译下一轮并重新从节点1复归；
- 已完成 `actionSearchEnd` 这一轮：报告 `actionValue: null` 后停止。

最终边界必须实际执行完整一轮，不能在准备进入边界时提前判定耗尽。

## 11. `testType=1`：动作时间

### 11.1 单轮流程

```text
在 slipStart 保持 returnTime
→ 以 slipDDt 扫描到 slipEnd
→ 在 slipEnd 保持 waitTime
→ 未动作则报告 null 并停止
```

### 11.2 最近 tick

实现不分配完整 `tick_values` 列表，而是从目标值在固定 step 上的
位置计算相邻两个候选 tick，并把 `0`、`count` 作为边界候选：

```python
position = (timerStartValue - slipStart) / step
candidates = {floor(position), ceil(position), 0, count}
timerTick = 距离 timerStartValue 最近的合法候选
```

浮点距离在容差内相等时保留较小 tick，即选择较早时刻。

`timerStartValue` 必须位于 `slipStart` 和 `slipEnd` 的闭区间内。

若 `slipStart == slipEnd`，没有节点2和 Sweep tick。
`timerStartValue` 必须等于这个共同值，并在收到：

```text
onUpdate(nodeId=3, tick=0, hw_ts)
```

时记录 `timerStartHwTs`。这样只计算 WAIT 测试阶段，不把节点1的
`returnTime` 计入动作时间。

收到：

```text
onUpdate(nodeId=2, tick=timerTick, hw_ts)
```

时只记录一次：

```python
timerStartHwTs = hw_ts & 0xFFFFFFFF
timerStartRecorded = True
```

### 11.3 硬件时间戳差

DI 动作时：

```python
elapsed_us = (
    (di_hw_ts & 0xFFFFFFFF)
    - timerStartHwTs
) & 0xFFFFFFFF
```

只有：

```text
elapsed_us < 0x80000000
```

才表示合法的向前时间差。半圈是：

```text
0x80000000 us ≈ 35分47.484秒
```

若动作发生在计时 tick 之前，模减结果会落在后半圈，必须报告
`null`，不能错误报告约4294秒。

启动前还必须保证最迟合法动作仍落在 U32 正向半圈内：

```text
remainingTimerWindowMs
  = (count - timerTick + 1) × 10 + waitTime

remainingTimerWindowMs
  < 0x80000000 / 1000 - safetyMarginMs
```

建议：

```text
safetyMarginMs = 1000
```

其中额外一个10 ms来自 USEEngine 在最后 tick 后、处理
`COUNT_OVER` 前的固定 interval。若窗口过长，应在启动时拒绝，
不能让一个合法的长动作被半圈规则误判为反向时间。

合法报告：

```json
{
  "testType": 1,
  "timerStartValue": 49.5,
  "actionTime": 150.2
}
```

```text
actionTime = round(elapsed_us / 1000.0, 1)
```

没有动作、计时起点未记录或时间戳方向非法：

```json
{
  "testType": 1,
  "timerStartValue": 49.5,
  "actionTime": null
}
```

### 11.4 tick 回调与 DI 回调的同事件循环竞态

USEEngine 的数值更新事件和 DI 事件是分别调度的异步任务。若 DI
恰好发生在 `timerTick` 附近，`onDi()` 可能先于对应的
`onUpdate()` 被调用。

因此 type1 的动作节点不能在 `timerStartRecorded=False` 时立即
断定计时失败，也不能用 Python 回调顺序猜测硬件事件先后。建议维护：

```text
pendingActionHwTs
pendingActionTask
```

处理规则：

```text
timerStartRecorded=True：
  立即按硬件时间戳计算

timerStartRecorded=False：
  暂存DI硬件时间戳并让出一个事件循环
  若对应onUpdate补记timerStartHwTs，则用两个硬件时间戳计算
  若让出事件循环后仍没有起点时间戳，则结果为null
```

节点2、3的硬件 `diMatchId=4`，不会直接终止。节点4可以用一个短
异步任务连续 `await asyncio.sleep(0)`，让同一批已排队的 DI 和
tick 事件先完成；`onUpdate()` 发现 pending action 时也应主动调用
同一个解析函数。最终仍以两个硬件时间戳的 U32 先后关系为准。

`USEEngine` 在 FPGA ACK 超时后会把 update 时间戳返回为 `-1`，
同时触发保留终止节点。API 必须忽略该 update，不能把 `-1` 转成
`0xFFFFFFFF` 参与计时、报告或搜索推进。

## 12. `testType=2`：滑差闭锁值搜索

### 12.1 `ddtSearch*` 是变化率大小

严格示例中：

```text
slipStart > slipEnd
slipDDt < 0
ddtSearchStart > 0
ddtSearchEnd > 0
```

因此 `ddtSearchStart/Step/End` 搜索的是变化率大小，主方向由
`slipDDt` 的符号给出。

本轮：

```python
searchMagnitude = currentDdtSearchValue
roundDDt = copysign(abs(searchMagnitude), slipDDt)
```

并校验：

```text
(slipEnd - slipStart) × slipDDt > 0
searchMagnitude > 0
```

当前代码直接把正的 `ddtSearchStart` 当作正向实际增量，会让严格
示例先朝错误方向扫描，必须修正。

统一零距离规则同样适用于 type2：若 `slipStart == slipEnd`，本轮
不执行 Sweep，只做 RESET→WAIT；`slipDDt` 可以为0。非零距离时
`slipDDt` 必须非0，因为它负责提供变化率方向。

### 12.2 流程与报告

每轮都固定：

```text
start = slipStart
end = slipEnd
```

只有 `roundDDt` 的大小随搜索值变化。

动作时：

```json
{
  "testType": 2,
  "slipLockValue": 5.5
}
```

执行阶段的 `currentDdtSearchValue` 始终按前端输入的幅度值使用。
但当前前端漏做了报告显示侧的有效值换算，因此临时兼容如下：

```text
主变量为电压/电流幅值：
  slipLockValue = currentDdtSearchValue / √2

主变量为相角/频率：
  slipLockValue = currentDdtSearchValue
```

这只影响 `testType=2` 的报告字段，不改变 Sweep step、搜索判断或
type0/type3/type4 的报告。代码中应保留“前端修复后删除”的备注。

未动作则搜索下一个变化率大小；完成 `ddtSearchEnd` 仍未动作时
报告 `slipLockValue: null`。

## 13. `testType=3`：电流闭锁值搜索

### 13.1 主滑差变量限制

主滑差变量只允许：

| `slipVarType` | 主变量 |
|---|---|
| `"0_0"` | 相电压相关幅值/相角/频率 |
| `"2_0"` | 线电压相关幅值/相角/频率 |
| `"5"` | 全12通道频率 |

不允许：

```text
slipVarType="0_1"
```

即电流闭锁搜索不能同时把相电流作为主滑差变量。

### 13.2 闭锁目标

`lockPhase` 是一个或多个真实电流通道：

```text
6..11
```

例如：

```json
{"lockPhase": "6"}
```

表示 Ia；`"6,7,8"` 表示三相电流。当前轮把所选通道的层1幅值
设置为：

```text
currentLockValue = currentLockSearchValue
```

该值已经是电流幅度值，不乘 `√2`，也不除以 `√3`。

### 13.3 流程与报告

每轮：

```text
在 slipStart + currentLockValue 保持 returnTime
→ 执行主变量 slipStart → slipEnd Sweep
→ 在 slipEnd + currentLockValue 保持 waitTime
```

动作时：

```json
{
  "testType": 3,
  "currentLockValue": 0.8
}
```

未动作则增加 `lockSearchStep`；完成 `lockSearchEnd` 仍未动作时
报告 `currentLockValue: null`。

## 14. `testType=4`：电压闭锁值搜索

### 14.1 主滑差固定为系统频率

type4 必须满足：

```text
slipVarType = "5"
```

主 Sweep 固定为全部12个通道的频率。`slipPhasor` 和 `slipPhase`
即使存在也忽略。

### 14.2 `lockVarType`

`lockVarType` 在 type4 中必须真正参与解析：

| `lockVarType` | 闭锁变量 | `lockPhase` |
|---|---|---|
| `"0_0"` | 相电压幅值 | 实际电压通道 `0..5` |
| `"2_0"` | 线电压幅值 | 第4.2节的线电压选择编号 `0..8` |

相电压闭锁：

```text
实际通道幅值 = currentSearchValue
```

线电压闭锁：

```text
lockPhase只能是单个编号0..8
按编号展开对应相通道
实际相通道幅值 = currentSearchValue / √3
```

### 14.3 流程与报告

每轮：

```text
施加当前电压闭锁值
→ 全12通道频率从 slipStart 扫描到 slipEnd
→ 等待 waitTime 检查动作
```

动作时：

```json
{
  "testType": 4,
  "voltageLockValue": 12.0
}
```

即使 `lockVarType="2_0"`，报告也返回前端逻辑线电压值，不返回
`/√3` 后的相电压值。

完成 `lockSearchEnd` 仍未动作时报告
`voltageLockValue: null`。

## 15. 搜索序列的统一实现

四种搜索类型都使用：

```text
SearchStart
SearchStep
SearchEnd
```

不要反复执行：

```python
current += step
```

否则长序列会积累浮点误差。使用：

```python
raw_value = start + index * step
```

并在最后一步跨过 end 时钳到精确边界：

```python
def value_at(index):
    value = start + index * step
    if step > 0:
        return min(value, end)
    return max(value, end)
```

需要防止连续两个 index 都被钳到 end。记录当前轮是否已经执行
`end`：

```text
end尚未执行：
  下一轮可以钳到end

end已经执行且未动作：
  搜索耗尽，报告null
```

搜索参数必须满足：

```text
start、step、end 都是有限数
step != 0
start != end 时：(end - start) × step > 0
```

`start == end` 合法，表示只执行一轮。

还应设置最大搜索轮数，防止极小搜索步长造成无限期输出。

## 16. 报告与停止幂等

API 至少维护：

```python
self.reportSent = False
self.finishing = False
```

统一出口：

```python
def _finish(self, report: dict):
    if self.finishing:
        return
    self.finishing = True

    if not self.reportSent:
        self.ctrl.sendReport(report)
        self.reportSent = True

    self.ctrl.stopTest()
```

这样可以防止：

- DI 连续变位导致重复报告；
- DI 进入节点4的动作处理与用户 stop 同时发生；
- 节点3超时进入节点5的轮次结束处理与用户 stop 同时发生；
- `onStop()` 再次补发相同报告。

外部主动 stop 时，可兼容当前行为：

- type1 尚未报告：补发 `actionTime: null`；
- 搜索型尚未报告：不伪造搜索耗尽报告；
- 已报告：`onStop()` 不再发送任何报告。

## 17. 输入校验和错误处理

所有真正使用的字段至少做以下校验：

1. `testType` 是非布尔整数，且为 `0..4`。
2. 只校验当前分型真正读取的枚举：
   - 普通主变量校验 `slipVarType`、`slipPhasor`；
   - `slipVarType="5"` 不读取 `slipPhasor/slipPhase`；
   - type4 不读取主变量的 `slipPhasor/slipPhase`，但校验
     `lockVarType`；
   - 被忽略的旧字段即使缺失或带遗留值也不拒绝。
3. 数值均为有限数，拒绝 `NaN` 和 `Infinity`。
4. `returnTime`、`waitTime` 是 `1..2146483` 的毫秒整数；底层的0
   不是立即跳转，而是没有 timeout。显式上限还避免超大 Python
   整数在引擎除以1000时触发浮点溢出。
5. `doMask`、`doCtrlMask`、`logicMask` 在 `0..255`。
6. 普通相电压/相电流实际读取的 `slipPhase` 可用逗号多选，但必须
   非空、没有空 token、非整数和重复项；未读取的选择字段不校验。
7. 普通电压选择只含 `0..5`，普通电流选择只含 `6..11`。
8. 主线电压的 `slipPhase` 必须是单个整数 `0..8`；type4
   `lockVarType="2_0"` 时 `lockPhase` 同样必须是单个整数 `0..8`。
9. type3 的 `lockPhase` 只含 `6..11`。
10. type4 的 `lockVarType` 只为 `"0_0"` 或 `"2_0"`，并按各自规则
    解释 `lockPhase`。
11. 非零距离 Sweep 的 `d/dt` 非0且方向正确，计算出的
    `count >= 1`；起终点相等时编译为 RESET 直接进入 WAIT。
12. type2 的 `ddtSearchStart/End` 都是大于0的变化率大小，
    `ddtSearchStep` 可正可负；实际执行轮次的变化率大小不得为0。
13. `timerStartValue` 位于逻辑起终点闭区间内；零距离时等于共同值
    并使用 WAIT 入口硬件时间戳。
14. 主扫描幅值和频率不能为负；整份 `statics` 及每轮覆盖后的
    reset/endpoint/lock 必须满足：电压通道幅值绝对值不超过
    `200 V`，电流通道不超过 `60 A`，频率为
    `0..19531.24999 Hz`。在线电压 `/√3` 展开后检查实际相通道值。
    必须在寄存器转换前拒绝越界，不能依赖32位取模。
15. 单轮 Sweep tick 数和搜索轮数不能超过 API 常量上限。
16. 转换后的固定 step 不能在全部目标通道上量化为0。
17. 业务节点 ID 只使用 `1..0xFFFE`，绝不写 `0x0000`、`0xFFFF`。
18. `statics` 中每个实际目标通道必须包含所需层及两个有限数：
    - frequency 目标必须有层0 `[dc, frequency]`；
    - amplitude/phase 目标和 lock 目标必须有层1
      `[amplitude, phase]`；
    - 不为缺失的关键层静默制造默认值。
19. type1 的“剩余 Sweep 时间 + waitTime”必须小于 U32 正向半圈并
    保留安全余量；零距离时只计算 `waitTime`。

解析、所有搜索轮的物理范围、step量化校验和首轮编译应在第一次
控制调用前完成。只有全部成功才写入实例运行状态并调用
`upsertNodes()`；不能等到后续轮次才发现 step 量化为0。

这里的“原子”只表示首次控制调用之前完成全部业务校验，不表示
`EngineCtrl.upsertNodes()` 是数据库事务。必须检查 `upsertNodes()`
和 `trigNode()` 的返回值；下发失败时立即走错误停止流程。

若校验失败：

```python
self.ctrl.stopTest("slip_test parameter error: ...")
```

由 EngineCtrl 现有错误/停止流程通知前端；不需要修改公共控制层。

`EngineCtrl` 当前不会替 API 捕获 `onUpdate()`、`onDi()` 内部异常。
因此两个回调及 pending timer 异步任务都要有顶层
`try/except`，异常统一调用 `_stop_with_error()`。特别是节点4和
节点5没有自动 timeout，若其回调抛异常而未停止，设备会持续保持
输出。

## 18. 当前方法拆分

`ApiSlipTest` 当前按以下结构组织：

```text
_onSetup()
  ├─ _parse_config()
  ├─ _parse_main_target()
  ├─ _parse_lock_target()
  ├─ _build_search_values()
  ├─ _prepare_round()
  ├─ ctrl.upsertNodes()
  └─ ctrl.trigNode(1)

_parse_channel_list()
_parse_single_line_selector()
_parse_main_target()
_parse_lock_target()

_apply_main_value()
_build_main_delta()
_apply_lock_value()

_build_sweep_plan()
_build_round_plan()
_compile_round_nodes()

onUpdate()
  ├─ 记录节点/tick硬件时间戳
  ├─ type1记录计时tick
  ├─ _try_schedule_transition_action()
  ├─ node4调度动作报告
  └─ node5调用 _advance_or_finish()

onDi()
  ├─ 更新_last_di
  ├─ 保存受掩码DI硬件时间戳
  └─ _try_schedule_transition_action()

_resolve_action()
_select_action_timestamp()
_build_report()
_finish()
_onStop()
```

## 19. 重写中已修复的旧代码问题

| 旧代码位置 | 原问题 | 当前实现 |
|---|---|---|
| `_ParseChannels()` | 把线电压编号直接当 API 通道，`6..8` 会误写电流通道 | 分开普通通道解析和线电压选择展开 |
| `_BuildStepPhys()` | 所有线电压角度/频率也 `/√3` | 只在线电压幅值时缩放 |
| `_BuildResetStaticPhys()` | 当前已有绝对值覆盖思路 | 保留该行为：所有被滑差选中的目标分量用 `slipStart` 覆盖 `statics` |
| `_BuildEndpointStaticPhys()` | 闭锁值只按直接通道幅值写入 | type3/4 分开解析；type4 使用 `lockVarType` |
| `_Compile3Nodes()` | `int()` 可能把接近整数的浮点比值少算一步 | 先做近整数容差吸附，再向下取完整10 ms步数 |
| `_Compile3Nodes()` | 起终点相等时强造一个伪 step | 零距离由节点1直接跳节点3，不创建空 Sweep |
| `_Compile3Nodes()` | DI 直接跳 `0xFFFF` 存在报告竞态 | 节点2/3配置 DI 条件并跳内部 ACTION_LATCH 节点4 |
| 搜索回环 | 节点3自动回旧节点1 | 节点3超时进节点5，由 API 更新后显式触发节点1 |
| `_onSetup()` | 枚举、方向、时间和搜索参数几乎不校验 | 先完整解析校验，再原子下发 |
| type2 编译 | 正的 `ddtSearch*` 被直接当正向变化率 | 搜索大小，执行方向取 `slipDDt` 符号 |
| type4 编译 | 完全忽略 `lockVarType` | 支持相电压/线电压两类闭锁映射 |
| timer tick | 用绝对距离向下取整 | 从固定 step 的相邻候选中选择最近 tick，等距取较早者 |
| `onDi()` | 复归阶段也判动作 | 仅 SWEEP/WAIT 逻辑阶段判动作，其他阶段只更新基线 |
| 节点切换 | `COUNT_OVER → WAIT` 同步窗口内可能漏掉 DI | 用最后 Sweep tick 与 WAIT tick0 的硬件时间戳恢复窗口内动作 |
| `onDi()` | U32模减无半圈校验 | 拒绝动作早于计时起点的伪大时间 |
| 搜索值 | 反复浮点累加产生漂移 | 按 `start + index × step` 计算 |
| 报告 | 缺少统一幂等出口 | `reportSent/finishing` 保证只报告一次 |

## 20. 已采用的实现顺序

实现按以下次序完成：

1. 先实现纯函数式枚举/选择解析，不连接 EngineCtrl。
2. 实现线电压展开和三种物理分量转换。
3. 实现固定10 ms step/count，覆盖近整数容差和正负方向。
4. 实现 type0/1 的单轮节点编译。
5. 加入节点4 ACTION_LATCH、节点5 ROUND_DONE 和统一搜索迭代器。
6. 接入 type2 的变化率大小/方向规则。
7. 接入 type3/4 的闭锁目标。
8. 实现阶段化 DI 判定和 type1 硬件计时。
9. 加入报告幂等、参数错误和边界上限。
10. 最后再替换现有 API 文件并做引擎联调。

公共引擎代码保持不变，可把影响范围严格限制在 `slip_test`。

## 21. 最低验证矩阵

### 21.1 枚举与通道

- 相电压 `"0"`、`"0,1,2"`；
- 相电流 `"6"`、`"6,7,8"`；
- 混合 `"1,2,6"` 被拒绝；
- 线电压编号 `0..8` 分别展开正确；
- 线电压 `"0,1,2"` 组合字符串被拒绝；
- `"5"` 无条件得到 `0..11` 且 phasor=2。

### 21.2 换算

- 普通电压/电流幅值不乘 `√2`；
- 线电压幅值的 start、step、endpoint 都 `/√3`；
- 线电压角度、频率不 `/√3`；
- 所有被扫描目标分量在复归状态由 `slipStart` 覆盖静态值；
- type4 线电压闭锁值 `/√3`，报告不缩放。

### 21.3 Sweep

- 正向整除；
- 负向整除；
- 非整除时所有 step 仍相同，不生成尾步；
- 最后 Sweep tick 可与 endpoint 存在小于一个 step 的差值；
- 起点等于终点时节点1直接跳节点3，不进入节点2；
- `d/dt` 符号错误被拒绝；
- 过小 step 量化为0时被拒绝；
- 超大 count 被拒绝。

### 21.4 五类实验

- type0 命中、未命中、边界轮命中；
- type1 最近 tick、等距取较早 tick、U32回绕、动作早于计时点；
- type2 严格 `slip.json` 正幅值搜索但负方向 Sweep；
- type3 单/多电流通道和非法主相电流；
- type4 相电压闭锁和线电压闭锁。

### 21.5 状态机与报告

- 复归阶段 DI 只更新基线；
- 节点2和节点3都配置 DI 条件并跳节点4；
- 节点2 `COUNT_OVER` 直接进入节点3；
- 最后 Sweep tick 到节点3入口之间的 DI 不会因重建基线而丢失；
- RESET 超时边界到首个监视节点入口之间的 DI 也不会被新基线吞掉；
- DI、最后 Sweep tick、WAIT tick0 回调乱序不会漏报或重复报告；
- 零距离轮次节点1直接进入节点3，type1以节点3入口时间戳计时；
- 节点3静态输出 endpoint 并保持 `waitTime`；
- 节点4只完成一次动作报告，节点5只处理轮次结束；
- 下一轮不会短暂输出上一轮闭锁值；
- 搜索边界实际执行后才报告 null；
- 用户 stop 与 DI 同时发生不会重复报告；
- 实验节点从不写入 `0x0000`、`0xFFFF`。

## 22. 最终实现边界

本方案刻意把修改限制在：

```text
RelayProtection/api/ApiSlipTest.py
```

API 已能通过现有接口完成：

```text
节点下发 → 波形输出 → tick硬件时间戳 → DI硬件时间戳
→ 搜索迭代 → 报告 → 安全停止
```

因此不应为了滑差实验给 `BaseApi`、`EngineCtrl` 或 `USEEngine`
增加专用字段、专用回调或停止原因。这样不会影响其他实验 API。
