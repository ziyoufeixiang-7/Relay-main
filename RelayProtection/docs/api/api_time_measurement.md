# 时间测量实验 API

> 模块：`time_measurement`
>
> 权威示例：项目根目录 `tm.json`
>
> 实现依据：本文与《API实验代码开发指南.md》

## 1. 实验目标

时间测量实验使用三个固定角色的静态波形状态和最多三个软件计时器，测量继电器接点的
动作、返回及级联时间。

计时结果只由 FPGA 上报的硬件时间戳计算。Python 主机时间、WebSocket 到达时间、
状态跳转完成时间都不得参与计时结果。

实验固定流程为：

```text
状态1（准备态）
  -> 状态2（测试态）
  -> 状态3（测试后保持态）
  -> 0xFFFF（停止）
```

它与状态序列实验的主要区别是：

- 只有三个固定角色，不是任意节点图；
- 状态2可以由一个或多个计时器的停时条件控制；
- 状态2的八项 `do_actions` 是八个 DO 通道的脉冲定义，不是八个绝对 mask；
- 最终报告只报告计时器，不报告各状态持续时间。

## 2. `tm.json` 的使用方式

`tm.json` 连续展示了三个独立 WebSocket JSON 消息：

1. `start` 请求；
2. 实验中的 `next_node` 请求；
3. 后端最终 `report` 回复。

它们必须分别发送。整个 `tm.json` 不是一个可以一次 `json.loads()` 的单一 JSON
文档。

`tm.json` 是本协议的严格正确示例。旧 API 文档和已删除的旧实现只可用于确认历史枚举
名称，不能反向改变本协议。

## 3. 启动消息

外层结构：

```json
{
  "module": "time_measurement",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "timers": {},
    "nodes": []
  }
}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `module` | string | 是 | 固定为 `time_measurement` |
| `cmd` | string | 是 | 启动时固定为 `start` |
| `params` | object | 是 | 实验参数 |
| `params.debounce` | number | 否 | DI 防抖时间，单位 ms；缺省值由 EngineCtrl 决定 |
| `params.timers` | object | 是 | `timer1`～`timer3` 的定义 |
| `params.nodes` | array | 是 | 恰好三个状态节点 |

旧文档中的下列结构不是本协议：

```text
params.statics
params.limits
node.next_id
trigger.next_id
```

每个状态的波形放在自己的 `node.static` 中。三个状态的目标由固定角色决定，不从
`next_id` 读取。

为兼容前端版本差异，后端应忽略自己不读取的附加字段，但所有实际使用字段必须严格
校验。

## 4. 三个状态及节点 ID

`params.nodes` 必须恰好包含三个节点，并严格按以下 ID 和数组位置提供：

| 数组位置 | 固定 ID | 角色 | 固定目标 |
|---:|---:|---|---:|
| `nodes[0]` | `1` | 状态1 | `2` |
| `nodes[1]` | `2` | 状态2 | `3` |
| `nodes[2]` | `3` | 状态3 | `0xFFFF` |

固定常量为：

```python
STATE1_ID = 1
STATE2_ID = 2
STATE3_ID = 3
```

任何顺序变化、缺失、重复或其他 ID 都是参数错误。后端不重新编号，也不从
`next_id` 推断角色。

实验入口固定是节点 `1`。三个固定 ID 原样作为 Engine ID 和
`value_update.nodeId`。保留节点 `0x0000`、`0xFFFF` 绝对不能写入。

## 5. 节点公共字段

每个节点结构：

```json
{
  "id": 1,
  "mode": "STATIC",
  "static": {},
  "do_actions": [],
  "triggers": []
}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `id` | int | 是 | 按数组位置固定为 `1`、`2`、`3`，且不能是 bool |
| `mode` | string | 是 | 当前只允许 `STATIC` |
| `static` | object | 是 | 进入该状态时生效的绝对物理量 |
| `do_actions` | array | 是 | 状态1/3与状态2的结构不同 |
| `triggers` | array | 是 | 当前状态的退出规则 |

时间测量实验不支持 `SWEEP`，所以不会生成 `steps`、`interval` 或
`countOverId`。

## 6. `static` 物理量

格式：

```json
{
  "0": {
    "0": [0, 50],
    "1": [81.64962003, 0]
  }
}
```

结构为：

```text
前端通道字符串
  -> layer 字符串
       -> [幅值, 频率或相位]
```

### 6.1 通道与层

- 前端通道为 `"0"`～`"11"`；
- layer 为 `"0"`～`"64"`；
- 每个 layer 必须是两个有限数字。

| layer | 第一项 | 第二项 |
|---:|---|---|
| 0 | 直流分量幅值 | 公共基频，Hz |
| 1～64 | 对应次数波形的幅值 | 相位，° |

### 6.2 幅值口径

前端发送的 layer 0～64 第一项已经是后端需要的实际幅值，不是有效值。

后端直接调用：

```python
self.physDictToReg(node["static"])
```

不得再乘 `sqrt(2)`，也不得使用 `is_delta=True`。

映射结果写入：

```python
ApiNodeData.base
```

## 7. DI 固定定义

DI 是完整的 8 位当前状态：

| 名称 | bit | mask |
|---|---:|---:|
| `A` | 0 | `0x01` |
| `B` | 1 | `0x02` |
| `C` | 2 | `0x04` |
| `R` | 3 | `0x08` |
| `a` | 4 | `0x10` |
| `b` | 5 | `0x20` |
| `c` | 6 | `0x40` |
| `r` | 7 | `0x80` |

电平语义固定为：

```text
1 = OPEN  = 断开
0 = CLOSE = 闭合
```

因此：

```text
di = 255 = 0xFF = 八路全部断开
di = 0   = 0x00 = 八路全部闭合
```

本协议不提供 `DI_OPEN_IS_HIGH` 开关。

计时器启时和停时的 `OPEN/CLOSE` 都是本次真实 DI SOE 中的定向事件，不使用状态序列
的实验起点/节点起点 XOR 参考规则：

```python
matched = (
    previous_bit != current_bit
    and current_bit == target_level
)
```

其中 `OPEN` 的目标电平是 1，`CLOSE` 的目标电平是 0。停表还必须满足计时器已经拥有
`start_hw_ts` 且尚无 `stop_hw_ts`。因此：

- 计时器启动前发生的同类事件不参与本次计时；
- 某位一直保持在目标电平，不会因其他 DI 位变化而停表；
- 启动后第一次命中的目标定向事件写入 `stop_hw_ts`；
- 后续同类事件不能覆盖第一次停时；
- DI 型启时和所有 DI 停时都严格使用实际 `onDi(di, hw_ts)` 回报携带的硬件时间戳；
  `ENTER_STATE_2` 例外地使用节点2 ACK 硬件时间戳启时。

## 8. 计时器定义

`params.timers` 最多包含：

```text
timer1
timer2
timer3
```

`timer1`～`timer3` 都是可选固定键。键缺失表示该 timer 未配置；如果键存在，其值必须
是 object，并且必须同时包含合法字符串 `start_mode` 和 `stop_mode`。`null`、半配置
对象或空字符串都属于参数错误。其他未使用的附加键按兼容规则忽略。

单个计时器：

```json
{
  "start_mode": "ENTER_STATE_2",
  "stop_mode": "DI_A_CLOSE"
}
```

| 字段 | 含义 |
|---|---|
| `start_mode` | 计时器从哪个硬件事件开始 |
| `stop_mode` | 计时器由哪一路 DI 的 OPEN/CLOSE 事件停止 |

未配置的计时器最终报告 `null`。状态2的 `TIMER_*` 条件不得引用未配置的计时器。

### 8.1 `start_mode` 枚举

进入状态2启动：

```text
ENTER_STATE_2
```

DI 事件启动：

```text
DI_A_CLOSE_START   DI_A_OPEN_START
DI_B_CLOSE_START   DI_B_OPEN_START
DI_C_CLOSE_START   DI_C_OPEN_START
DI_R_CLOSE_START   DI_R_OPEN_START
DI_a_CLOSE_START   DI_a_OPEN_START
DI_b_CLOSE_START   DI_b_OPEN_START
DI_c_CLOSE_START   DI_c_OPEN_START
DI_r_CLOSE_START   DI_r_OPEN_START
```

历史枚举允许的计时器级联启动：

```text
timer2: TIMER1_STOP_START
timer3: TIMER1_STOP_START 或 TIMER2_STOP_START
```

含义是：指定的前置计时器停止时，使用同一个停止硬件时间戳启动当前计时器。

级联配置必须满足：

- 引用的前置计时器必须存在；
- 同一个计时器只启动一次。

timer1 不支持级联启动，协议也不定义 `TIMER3_STOP_START`。这样依赖方向始终从前序
timer 指向后序 timer，不会形成环。

### 8.2 `stop_mode` 枚举

```text
DI_A_CLOSE   DI_A_OPEN
DI_B_CLOSE   DI_B_OPEN
DI_C_CLOSE   DI_C_OPEN
DI_R_CLOSE   DI_R_OPEN
DI_a_CLOSE   DI_a_OPEN
DI_b_CLOSE   DI_b_OPEN
DI_c_CLOSE   DI_c_OPEN
DI_r_CLOSE   DI_r_OPEN
```

例如：

```text
DI_A_CLOSE -> A 在本次 DI SOE 中发生 1 -> 0 时停止
DI_A_OPEN  -> A 在本次 DI SOE 中发生 0 -> 1 时停止
```

只有该定向事件发生在计时器启动后，且这是第一次匹配，才记录该次 DI 回报携带的
`hw_ts`。不得使用：

- 状态2跳转到状态3的时间；
- `time.time()` 或 `time.perf_counter()`；
- WebSocket 消息到达时间。

### 8.3 计时器运行数据

每个已配置计时器运行时只保存：

```python
start_hw_ts: Optional[int] = None
stop_hw_ts: Optional[int] = None
```

不维护额外的字符串状态，也不在 DI 回调中预先计算耗时。两个时间戳本身就是唯一
运行依据：

| `start_hw_ts` | `stop_hw_ts` | 含义 |
|---|---|---|
| `None` | `None` | 尚未启动 |
| 非 `None` | `None` | 已启动，等待首次停时事件 |
| 非 `None` | 非 `None` | 已取得完整起止时间 |

`stop_hw_ts` 不允许在 `start_hw_ts` 之前写入。两者一旦写入都不能被后续事件覆盖。

“事件发生在启动以后”不能只用 Python 回调到达顺序判断。DI 回报时间戳已经减去防抖
估算时间，所以一个物理上发生在状态2入口之前的接点动作，可能在状态2 ACK 之后才回调。
必须用 U32 半周期规则判断：

```python
U32_MASK = 0xFFFFFFFF
U32_HALF = 0x80000000

def forward_delta_us(event_ts, reference_ts):
    delta = (event_ts - reference_ts) & U32_MASK
    return delta if delta < U32_HALF else None
```

`None` 表示该事件位于参考点之前，或已经超出本协议可无歧义表示的时间窗口，不能启停
计时器。`delta == 0` 合法，因此同一硬件微秒值仍允许得到 `0.000 ms`。

据此，本协议要求单个实验及任一计时区间小于 `2^31 µs`，约
`35 min 47.484 s`。超过该范围时，单个 U32 时间戳无法无歧义区分“之前”和“之后”，
不属于本协议支持范围。API 从节点1入口启动全局可取消 watchdog；接近该上限仍未结束
时必须以明确错误停止实验，不能继续运行后静默报告一个 U32 取模值。实现留出 1 秒
安全裕量，以主机单调时钟保存绝对 deadline，并在每个回调处理 timer 前也检查该
deadline；watchdog 调度晚醒时，回调门槛仍会先拒绝新的实验事件。

离开状态2不会清除时间戳。权威报告中，状态2由 `TIMER_1` 结束后，`timer2` 仍可在
状态3收到首次匹配的 DI 定向事件并得到 `105.002 ms`。因此 `onDi()` 必须在状态1、
状态2和状态3期间继续维护尚未取得 `stop_hw_ts` 的计时器；只有“是否触发状态2跳转”
的判断限定在状态2。

API 还单独保存：

```python
experiment_entry_hw_ts: Optional[int] = None
state2_enter_hw_ts: Optional[int] = None
```

进入节点1的第一次有效 `tick=0` 回调保存实验入口时间。DI 回报只有通过“相对实验入口
的 U32 半周期顺序判定”后，才可作为 timer 启停事件，避免防抖完成后迟到的实验前动作
混入本次实验。

进入状态2的 `tick=0` 回调中，用节点 ACK 的硬件时间戳设置该值，并用同一个值启动所有
`ENTER_STATE_2` 计时器。

### 8.4 报告阶段的时间计算

硬件时间戳是 U32 微秒计数。只有 `_onStop()` 生成报告时，才对起止时间都存在、且
停时阶段已通过上述半周期顺序判定的计时器计算：

```python
elapsed_us = (stop_hw_ts - start_hw_ts) & 0xFFFFFFFF
elapsed_ms = elapsed_us / 1000.0
value = round(elapsed_ms, 3)
```

任一时间戳为 `None` 时报告 `null`。`round(..., 3)` 保留到 1 µs 时间戳分辨率，
不表示整个系统的绝对测量误差必然为 1 µs。JSON number 不保证把整数值显示成固定
三个尾随零。

## 9. 状态1

状态1是准备态，默认目标为状态2。

### 9.1 触发枚举

支持：

```text
MANUAL_TRIG
TIME_OUT
DI_MATCH
```

#### MANUAL_TRIG

```json
{
  "condition": "MANUAL_TRIG"
}
```

只有当前处于状态1且配置了该规则时，前端 `next_node` 才会调用：

```python
self.ctrl.trigNode(2)
```

与状态序列相同，`MANUAL_TRIG` 必须是该状态唯一的 trigger；不能再与
`TIME_OUT` 或 `DI_MATCH` 混用，以免前端命令和已发生但尚未回调的软件/硬件自动
跳转竞争。

#### TIME_OUT

```json
{
  "condition": "TIME_OUT",
  "timeout": 1000
}
```

映射：

```text
ApiNodeData.timeoutMs = timeout
ApiNodeData.timeoutId = 2
```

`timeout` 是 `1..2_146_483` 的非 bool 整数毫秒值。不能使用 0；当前 USEEngine
会把 0 当成未设置 timeout。上限已经包含全局 U32 半周期 watchdog 的 1 秒安全裕量。

#### DI_MATCH

```json
{
  "condition": "DI_MATCH",
  "di_mask": 513
}
```

状态1/3的 `DI_MATCH` 与状态序列相同：

- bit 0～7：参与判断的 DI；
- `0x100`：多位 AND；未设置时为任意位 OR；
- `0x200`：相对进入当前节点时的 DI；未设置时相对实验开始 DI；
- `0x400`：参考状态按 8 位取反。

`di_mask` 必须是非 bool 整数，只允许 bit 0～10，且低 8 位监视掩码不能为 0：

```python
(di_mask & ~0x7FF) == 0
and (di_mask & 0xFF) != 0
```

“与状态序列相同”在本方案中有意只复用三种 condition 和上述 `di_mask` 位语义。
本协议不读取 DI_MATCH 内嵌的 `timeout` 或 `post_condition_delay_ms`；安全超时
必须作为同一状态中独立的 `TIME_OUT` trigger 提供。

映射：

```text
ApiNodeData.diMatchMask = di_mask
ApiNodeData.diMatchId = 2
```

它与计时器的 `stop_mode` 是两套不同语义，不得混用。

### 9.2 状态1/3普通 DO

状态1和状态3的动作格式与状态序列一致：

```json
{
  "delay": 0,
  "mask": 3
}
```

| 字段 | 约束 | 含义 |
|---|---|---|
| `delay` | `0..65535` 整数 | 相对进入该状态的绝对毫秒时刻 |
| `mask` | `0..255` 整数 | 该时刻写入的完整 8 位 DO 值 |

打包：

```python
packed = (delay << 8) | mask
```

前端保证动作按 `delay` 升序排列，后端保持该顺序。

如果状态1或状态3收到：

```json
"do_actions": []
```

后端必须生成：

```python
ApiNodeData.doActions = [0x000000]
```

其含义是进入状态后在 0 ms 明确输出 DO mask 0。不能映射成 `None` 或空列表。

## 10. 状态2

状态2是测试态。波形仍为 `STATIC`，但 DO 与触发规则是本实验的专用格式。

### 10.1 八路 `do_actions`

状态2必须提供恰好八项：

```text
do_actions[0] -> DO0 -> mask 0x01
do_actions[1] -> DO1 -> mask 0x02
...
do_actions[7] -> DO7 -> mask 0x80
```

每项格式：

```json
{
  "delay": 2,
  "holdTime": 10
}
```

含义：

1. 进入状态2后 `delay` ms，DOi 相对基线翻转；
2. 保持 `holdTime` ms；
3. 在 `delay + holdTime` ms 再翻转一次，回到原状态。

它不是：

- 一个完整 DO mask；
- 按数组顺序执行的八条绝对动作；
- `delay` 后写 `1 << i` 并清除其他七路。

`delay`、`holdTime` 必须为非负整数，并满足：

```text
delay + holdTime <= 65535
```

### 10.2 编译为绝对 DO 时间线

`USEEngine.doActions` 只接受“绝对时刻 + 完整绝对 DO 字节”，所以 API 必须先把八路
翻转合并。

伪代码：

```python
events = {}

for channel, action in enumerate(do_actions):
    bit = 1 << channel
    start = action["delay"]
    end = start + action["holdTime"]
    events[start] = events.get(start, 0) ^ bit
    events[end] = events.get(end, 0) ^ bit

mask = state2_baseline_mask
packed_actions = []

for at_ms in sorted(events):
    toggle = events[at_ms]
    if toggle == 0:
        continue
    mask ^= toggle
    packed_actions.append((at_ms << 8) | mask)

if not packed_actions or (packed_actions[0] >> 8) > 0:
    packed_actions.insert(0, state2_baseline_mask)
```

同一时刻的所有通道必须先 XOR 合并，再写一次完整 mask。这样可以避免本应同时翻转的
通道被拆成多次串行硬件写入。

如果同一时刻的净 XOR 为 0，省略该次无变化动作。是否补 0 ms 基线必须检查第一条
实际生成的 `packed_actions`，不能只检查 `min(events)`。例如 0 ms 的
`holdTime=0` 净抵消、第一条有效事件在 5 ms 时，仍必须插入 0 ms 基线。

### 10.3 权威示例的编译结果

`tm.json` 的状态1在 0 ms 明确输出 `mask=0`，所以状态2基线为 `0x00`。

八路脉冲应编译为：

| 时间 | 翻转 | 绝对 mask |
|---:|---|---:|
| 0 ms | DO0、DO1、DO4、DO5、DO6、DO7 | `0xF3` |
| 2 ms | DO2 | `0xF7` |
| 6 ms | DO3 | `0xFF` |
| 12 ms | DO2 回转 | `0xFB` |
| 36 ms | DO3 回转 | `0xF3` |
| 100 ms | DO0、DO1、DO4～DO7 回转 | `0x00` |

最终：

```python
ApiNodeData.doActions = [
    (0 << 8) | 0xF3,
    (2 << 8) | 0xF7,
    (6 << 8) | 0xFF,
    (12 << 8) | 0xFB,
    (36 << 8) | 0xF3,
    (100 << 8) | 0x00,
]
```

### 10.4 状态2 DO 基线约束

状态2动作必须预编译成绝对 mask，底层没有“运行时相对翻转”指令。因此协议将
`state2_baseline_mask` 明确定义为状态1最后一条声明的绝对 `mask`；状态1空动作按本
协议归一化为 0。

状态2生成的是相对这个“协议声明基线”的确定绝对时间线，不在进入状态2时重新读取
`ctrl.do` 猜测基线。若第一组翻转晚于 0 ms，状态2必须在 0 ms 先写一次基线；若第一组
翻转就在 0 ms，则直接写 `baseline XOR t0_toggle_bits`。

如果状态1在自己的延迟 DO 尚未全部执行时被 MANUAL_TRIG 或 DI_MATCH 提前结束，状态2
仍会按声明基线输出确定的绝对结果。前端若要求状态1结束前的物理 DO 也必须到达该基线，
应保证其触发时序晚于状态1最后一条 DO。权威 `tm.json` 使用 0 ms 的 `mask=0`，不存在
该时序歧义。

### 10.5 状态2触发枚举

计时器条件：

```text
TIMER_1
TIMER_2
TIMER_3
TIMER_1_2
TIMER_2_3
TIMER_3_1
TIMER_1_2_3
```

超时条件：

```text
TIME_OUT
```

兼容枚举：

```text
TIME_CTRL
```

`TIME_CTRL` 表示不使用计时器组合自动推进；状态2只由 `TIME_OUT` 或外部停止结束。
它本身不生成硬件触发，因此配置 `TIME_CTRL` 时必须同时提供一个有效
`TIME_OUT`。

“TIME_CTRL 必须带 TIME_OUT”是本次新协议为避免状态2无出口而规定的校验约束，不是
`tm.json` 已覆盖的示例分支。

状态2最多配置一个计时器组合或 `TIME_CTRL`，并可再配置一个 `TIME_OUT` 作为安全
超时。状态2不支持 `MANUAL_TRIG` 或 `DI_MATCH`。

### 10.6 TIMER 组合映射

例如：

```text
timer1.stop_mode = DI_A_CLOSE
timer2.stop_mode = DI_B_OPEN
condition = TIMER_1_2
```

API 将它编译为：

```text
A 必须为 0（CLOSE）
B 必须为 1（OPEN）
timer1.stop_hw_ts 和 timer2.stop_hw_ts 都必须非 None
```

可表示为：

```python
required_mask = 0x03
required_value = 0x02
```

每次收到完整 DI 后，在完成本次停表和级联启动后检查：

```python
all(timer.stop_hw_ts is not None for timer in selected_timers) and (
    (di & required_mask) == required_value
)
```

如果同一 DI 位被组合中的不同计时器同时要求 OPEN 和 CLOSE，参数无效，必须在上传
节点前拒绝。

状态2的 TIMER 组合不能直接写入 `ApiNodeData.diMatchMask`。它由 API 的
`onDi()` 判断，命中后只调用一次：

```python
self.ctrl.trigNode(3)
```

API 调度产生的少量软件延迟不影响计时精度，因为每个计时器早已使用本次 DI 回报的
硬件时间戳完成停表。

状态2出口中的电平组合使用当前完整 DI，但计时器停时本身仍要求目标位在本次真实
DI 回报中发生定向变化。另一位的无关变化不能让目标位未变化的计时器停表。

同一真实 DI 回报可以同时包含多位变化。如果某个计时器在这次回报中由 DI 启动或由
前置计时器停时级联启动，并且这同一份回报也包含其 `stop_mode` 所要求的定向事件，
该计时器允许使用同一个 `hw_ts` 启停并最终报告 `0.000 ms`。每次回调只使用当次
`previous_di -> di` 生成的事件集合，不缓存旧事件，因此启动前的历史动作和稳定目标
电平都不能制造停时。

### 10.7 状态2 TIME_OUT

```json
{
  "condition": "TIME_OUT",
  "timeout": 5000
}
```

状态2的 TIMER 判据和安全超时必须由同一个 API 仲裁器请求跳转。实现保存：

```text
state2_timeout_ms = timeout
```

`timeout` 同样必须是 `1..2_146_483` 的非 bool 整数毫秒值。

进入状态2的有效 `tick=0` 后，API 创建一个可取消的 `asyncio` 超时任务；到期时与
TIMER 判据共同调用一次 `_request_state2_exit()`。该函数必须先设置
`_state2_jump_pending`，再调用 `trigNode(3)`。

状态2的 `ApiNodeData.timeoutMs/timeoutId` 必须保持 `None`。如果同时让 USEEngine
管理 TIME_OUT、让 API 管理 TIMER，就存在双控制者竞态：USEEngine 可能已经选中节点3，
API 在节点3 ACK 回来前又留下一个 `manualTrig(3)`，随后使节点3重复进入并重启其
DO/timeout。

进入节点3或实验停止时必须取消并清理状态2超时任务。这样 TIMER 与 TIME_OUT 即使
同时就绪，也只会由 API 的一次性标记提交一次跳转。该安全超时使用主机调度时间控制
状态转换，不参与任何计时器报告；报告仍只使用 FPGA 硬件时间戳。

## 11. 状态3

状态3是测试后保持态，默认目标为 `0xFFFF`。

它支持与状态1相同的：

```text
MANUAL_TRIG
TIME_OUT
DI_MATCH
```

映射与状态1相同，只是目标改为停止：

```text
TIME_OUT -> timeoutId = 0xFFFF
DI_MATCH -> diMatchId = 0xFFFF
MANUAL_TRIG -> ctrl.stopTest()
```

状态3仍必须处理 DI 回报，使尚未停止的计时器可以完成。只有到达 `0xFFFF` 并执行
`_onStop()` 后才生成最终报告。

状态3 `do_actions=[]` 必须编译为 0 ms 输出 mask 0。因为新节点的 `doActions` 非空，
USEEngine 会在进入状态3时取消状态2尚未执行的脉冲任务，随后由状态3的 0 ms DO
动作归零；不需要修改 USEEngine。

## 12. `next_node` 命令

权威消息：

```json
{
  "module": "time_measurement",
  "cmd": "next_node"
}
```

处理规则：

- 当前在状态1且状态1配置了 `MANUAL_TRIG`：跳到状态2；
- 当前在状态3且状态3配置了 `MANUAL_TRIG`：调用 `stopTest()`；
- 当前在状态2：拒绝，状态2只由 TIMER 条件或 TIME_OUT 控制；
- 当前状态没有配置 `MANUAL_TRIG`：返回错误，不得无条件推进；
- 已有一次手动跳转待生效：不得重复触发。

`next_node` 不携带目标 ID，前端也看不到内部跳转细节。

## 13. `ApiNodeData` 映射

### 13.1 状态1

```python
ApiNodeData(
    mode=1,
    base=state1_base,
    doActions=state1_absolute_do,
    timeoutMs=state1_timeout,
    timeoutId=2,
    diMatchMask=state1_di_mask,
    diMatchId=2,
)
```

没有配置的触发字段保持 `None`。

### 13.2 状态2

```python
ApiNodeData(
    mode=1,
    base=state2_base,
    doActions=state2_compiled_pulses,
)
```

TIMER 组合和状态2 TIME_OUT 都不写入 `ApiNodeData` 自动触发字段，由 API 统一仲裁。

### 13.3 状态3

```python
ApiNodeData(
    mode=1,
    base=state3_base,
    doActions=state3_absolute_do,
    timeoutMs=state3_timeout,
    timeoutId=0xFFFF,
    diMatchMask=state3_di_mask,
    diMatchId=0xFFFF,
)
```

三个实例必须以固定字典键上传：

```python
api_nodes = {
    1: state1_node,
    2: state2_node,
    3: state3_node,
}
```

绝对禁止上传：

```text
0x0000
0xFFFF
```

`0xFFFF` 只能作为目标。

## 14. 回调与运行顺序

### 14.1 `_onSetup()`

必须按以下顺序：

1. 重置全部运行状态；
2. 解析并校验完整参数，不边解析边上传；
3. 校验节点严格为 `nodes[0].id=1`、`nodes[1].id=2`、`nodes[2].id=3`，并解析最多
   三个计时器；
4. 编译普通 DO 和状态2脉冲 DO；
5. 编译三个 `ApiNodeData`；
6. 一次性调用 `upsertNodes()`；
7. 执行 `self._last_di = int(self.ctrl.di) & 0xFF`，保存 EngineCtrl 已维护并回传给
   API 的当前完整 DI，作为第一次定向事件判断的上一份快照；
8. 调用 `trigNode(1)`。

任一参数错误都必须原子失败，不能留下部分节点。

本实验不新增也不调用 `diStart()`，不越过 EngineCtrl 读取 USEEngine 内部状态。

启动实验时 EngineCtrl 会先触发保留节点 `0x0000`。FPGA 收到任何 start 指令都会返回
一次完整 DI 状态，因此节点0的 start 回报会先更新 `EngineCtrl.di`。随后 API setup
直接用 `ctrl.di & 0xFF` 初始化 `_last_di`，不需要 freshness 标志或额外 DI 查询接口。

如果该 DI 回报在 API setup 后才被分发，节点1有效 `tick=0` 以前的 `onDi()` 仍只更新
`_last_di` 而不启停 timer；因此两种调度顺序都不会把初始完整 DI 快照当成实验动作。

### 14.2 `onUpdate(nodeId, tick, hw_ts)`

只在有效 `tick=0` 处理节点进入：

- 在任何 U32 归一化前校验 `nodeId/tick/hw_ts` 类型；已有全局 deadline 且已经到期时
  转入统一错误停止，不再处理节点；
- 按固定 ID 识别节点1、2、3，并先统一设置
  `_current_role = nodeId`、`_current_node_id = nodeId`；
- 到达手动请求的目标状态时清除对应 `_manual_jump_pending`；不得在节点2的重复
  `tick=0` 回调中把 `_state2_jump_pending` 清回 `False`，该标记只在整次实验运行态
  初始化时置空；
- 第一次进入节点1时，将 `hw_ts & 0xFFFFFFFF` 保存为 `experiment_entry_hw_ts`；
- 第一次进入节点1时同时保存带 1 秒裕量的主机单调时钟绝对 deadline，并创建全局
  watchdog；到达约 35 分 46.484 秒实验仍未终止时，watchdog 设置 stopping 并调用
  `stopTest()` 返回“达到安全 U32 运行时限”的明确错误；
- 离开节点2时取消并清理尚未到期的状态2超时任务；
- 第一次进入节点2时，将该 `hw_ts & 0xFFFFFFFF` 保存为
  `state2_enter_hw_ts`，并用同一个时间戳设置所有 `ENTER_STATE_2` 计时器尚为空的
  `start_hw_ts`；
- 状态2配置 TIME_OUT、当前没有 timeout task 且尚未 pending 时，从该有效入口回调
  创建一个可取消超时任务；任务到期与 TIMER 判据共用
  `_request_state2_exit()`；
- 进入节点2后立即用 `ctrl.di & 0xFF` 检查一次 TIMER 出口，但不在这里生成任何
  DI 事件、不更新 `_last_di`、也不写入 `stop_hw_ts`；当前电平只能用于出口组合，
  不能被伪造成 OPEN/CLOSE 定向事件。这样在状态1已经停下的 timer 不会因为后续没有
  新 DI 而卡住；
- 通过 `EngineCtrl.sendNodeUpdate(nodeId)` 发送固定节点 ID。

API 只负责：

```json
{"type": "value_update", "nodeId": 2}
```

`static`、`di`、`do` 更新由 EngineCtrl 已有逻辑发送，API 不重复发送。

### 14.3 `onDi(di, hw_ts)`

DI 回调时间戳是 FPGA SOE 值减去防抖估算时间，在 U32 回绕附近可能表现为负 Python
整数。完成生命周期和输入类型校验后，只要它是非 bool 整数就立即执行：

```python
di_hw_ts_u32 = hw_ts & 0xFFFFFFFF
```

不能把合法的负 DI 时间戳当成 ACK 错误。

先从同一份 `previous_di -> di` 计算本次真实定向事件集合。整个回调固定使用归一化
后的同一个 `di_hw_ts_u32`，处理顺序为：

1. 在任何 `int()` 或 U32 归一化前，校验 API 仍活动、`ctrl.running` 为真、没有进入
   停止流程，且 `di` 是 `0..255` 的非 bool 整数、时间戳是非 bool 整数；随后检查
   主机绝对 deadline，过期则报错停止且不处理本次事件；
2. 若节点1尚未生效，只更新 `_last_di` 后返回；若本次 SOE 时间戳按 U32 半周期规则
   位于 `experiment_entry_hw_ts` 之前，也只消费该电平变化并返回；
3. 对 `start_hw_ts is None` 的 DI 启动型计时器，若本次定向事件匹配
   `DI_*_START`，写入 `start_hw_ts=di_hw_ts_u32`；
4. 进入最多三轮的有界传播：
   - 对 `start_hw_ts is not None and stop_hw_ts is None` 的计时器，只在本次真实
     定向事件匹配 `stop_mode`，且
     `forward_delta_us(di_hw_ts_u32, start_hw_ts)` 非 `None` 时停表；
   - 满足上述条件后写入 `stop_hw_ts=di_hw_ts_u32`；不得做普通有符号大小比较，只
     使用 `forward_delta_us()`；
   - 对本轮刚获得 `stop_hw_ts` 的前置计时器，用同一个 `di_hw_ts_u32` 设置其尚未
     启动的级联计时器 `start_hw_ts`；
   - 本轮没有新停时或新级联启动时提前结束；
5. 更新上一份 DI 快照；
6. 当前节点为 2 时，只按“所选计时器 `stop_hw_ts` 均非 `None`”以及当前完整 DI
   电平组合检查出口；
7. 首次满足时通过 `_request_state2_exit()` 调用 `trigNode(3)`。

最多三轮对应最多三个计时器的最长合法级联链，可避免无界循环。当前事件集合只在本次
回调使用；一个刚启动或刚被级联启动的计时器，只有当本次回报确实也包含其停时方向事件
时才会立即停表。不能仅用 `stop_hw_ts == start_hw_ts` 判断事件先后：两个有序硬件反馈
可能落在同一微秒计数值；应使用半周期 U32 差值，0 合法而大于等于半周期无效。计时器
处理不应因已经进入状态3而停止。

### 14.4 `_onStop()`

先取消并清理状态2超时任务和全局 watchdog，再只在这里计算时间并发送一次最终报告，
不再触发节点：

```python
def report_value(timer):
    if timer.start_hw_ts is None or timer.stop_hw_ts is None:
        return None
    elapsed_us = (
        timer.stop_hw_ts - timer.start_hw_ts
    ) & 0xFFFFFFFF
    return round(elapsed_us / 1000.0, 3)

self.ctrl.sendReport(
    {
        "timer1_elapsed_ms": report_value(timer1),
        "timer2_elapsed_ms": report_value(timer2),
        "timer3_elapsed_ms": report_value(timer3),
    }
)
```

`onDi()` 只记录首次启停时间戳，不能提前缓存 `elapsed_ms`。

## 15. 报告

权威格式：

```json
{
  "type": "report",
  "module": "time_measurement",
  "data": {
    "timer1_elapsed_ms": 32.145,
    "timer2_elapsed_ms": 105.002,
    "timer3_elapsed_ms": null
  }
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `timer1_elapsed_ms` | number/null | timer1 完整起止时间 |
| `timer2_elapsed_ms` | number/null | timer2 完整起止时间 |
| `timer3_elapsed_ms` | number/null | timer3 完整起止时间 |

报告始终保留三个键：

- 已完成：U32 硬件时间差，单位 ms，并执行 `round(value, 3)`；
- 未配置、未启动或未停止：`null`。

## 16. 参数校验摘要

进入 API `_onSetup()` 后，实现至少必须拒绝：

- `params` 不是对象；
- `nodes` 不是恰好三个对象；
- 三个节点的顺序或非 bool 整数 ID 不是严格的 `[1, 2, 3]`；
- `mode` 不是 `STATIC`；
- 实际使用的通道、layer 或物理二元数组非法；
- 状态1/3普通 DO 的 delay/mask 越界；
- 状态2不是恰好八个通道动作；
- 状态2 delay/holdTime 非整数、为负或和大于 65535；
- 未知的 timer start/stop 枚举；
- 已配置 timer 不是完整 object，或缺少 start_mode/stop_mode；
- 非法的后向级联或未定义的 TIMER3_STOP_START；
- 计时器级联引用缺失；
- 状态2 TIMER 条件引用未配置计时器；
- DI_MATCH 的 di_mask 类型、已知位或低 8 位监视掩码非法；
- TIMER 组合对同一 DI 位要求相反电平；
- 某状态配置了不属于该角色的 trigger；
- 状态1或状态3把 `MANUAL_TRIG` 与其他 trigger 混用；
- 同一底层字段存在互相冲突的重复规则。
- TIME_OUT 的 timeout 不是 `1..2_146_483` 的非 bool 整数；
- 状态1或状态3没有任何有效出口；
- 状态2既没有 TIMER 组合也没有 TIME_OUT；
- TIME_CTRL 没有同时配置 TIME_OUT。

框架边界：EngineCtrl 在调用 API 前已经读取 `params.debounce`。因此外层 `params`
必须首先是 JSON object，且 debounce 必须能被 EngineCtrl 转换；这两类错误可能在
API 自身校验前被框架拒绝。新 API 不应为此修改 EngineCtrl。

以下内容可以忽略：

- 后端完全不读取的附加字段；
- STATIC 节点携带的旧版无关元数据；
- trigger 中遗留但本协议不读取的 `next_id`。

忽略附加字段不等于放松实际控制字段的校验。

## 17. 底层接口边界

本实验只需要当前代码已经提供的接口：

```text
EngineCtrl.upsertNodes()
EngineCtrl.trigNode()
EngineCtrl.stopTest()
EngineCtrl.sendNodeUpdate()
EngineCtrl.sendReport()
BaseApi.physDictToReg()
```

不需要为时间测量进一步修改：

```text
BaseApi
EngineCtrl
USEEngine
FPGACodec
```

尤其不要为了状态2 TIMER 组合修改 FPGA DI 触发语义。组合判断属于本 API，计时精度
来自 DI SOE 的硬件时间戳，而不是来自 Python 跳转速度。
