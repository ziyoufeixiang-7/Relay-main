# 时间测量实验 API 代码撰写计划

> 目标模块：`time_measurement`
>
> 目标文件：`RelayProtection/api/ApiTimeMeas.py`
>
> 协议依据：项目根目录 `tm.json` 与 `docs/api/api_time_measurement.md`

## 1. 当前状态

旧 `ApiTimeMeas.py` 的输入结构、跳转目标和状态2 DO 解释均与权威 `tm.json` 不一致；
旧实现已被完整替换，没有在其基础上增量修补。

截至 2026-07-25，新实现已经完成并发布：

- 正式实现位于 `RelayProtection/api/ApiTimeMeas.py`；
- `MODULE_KEYS=["time_measurement"]` 已生效；
- `Registry.py` 已恢复 `ApiTimeMeas` 注册；
- 验证阶段使用的不以 `Api` 开头的草稿和临时测试文件均已删除；
- `BaseApi.create("time_measurement")` 已验证返回新的 `ApiTimeMeas`。

实现阶段采用非 `Api` 前缀草稿，是因为 `BaseApi.create()` 会先动态扫描所有
`api/Api*.py`；如果草稿提前使用正式文件名，即使尚未写入 Registry，也会被前端启动
路径发现。当前只有验收后的正式实现可被发现。

现有 `tests/test_migrated_engine_e2e.py` 中的时间测量片段仍使用旧协议并 import 已删除
模块，属于待重写的遗留测试，不能作为当前实现依据。

新实现从空文件开始，只复用了项目公共接口和协议枚举。

## 2. 不进一步修改的底层文件

在当前状态序列已经使用 `sendNodeUpdate()` 的代码基础上，本实验不要求进一步修改：

```text
RelayProtection/api/BaseApi.py
RelayProtection/logic/EngineCtrl.py
RelayProtection/logic/USEEngine.py
RelayProtection/logic/FPGACodec.py
```

现有接口已经足够：

```text
ctrl.upsertNodes()
ctrl.trigNode()
ctrl.stopTest()
ctrl.sendNodeUpdate()
ctrl.sendReport()
ctrl.sendError()
self.physDictToReg()
```

状态2计时器组合属于 API 软件判据，不能为了它改变所有实验共享的 FPGA DI 触发机制。

## 3. 文件变更范围

实现阶段：

| 文件 | 操作 |
|---|---|
| 非 `Api` 前缀的临时模块/测试 | 已用于隔离验证，验收后已删除 |
| `api/ApiTimeMeas.py` | 已发布完整新实现 |
| `api/Registry.py` | 已恢复 import/register |
| `docs/api/api_time_measurement.md` | 已与实现同步 |
| `docs/api/api_time_measurement_implementation_plan.md` | 已记录完成状态和验收结果 |

不要恢复旧代码中的：

```text
params.statics
params.limits
node.next_id
lastDiShadow = 0
状态2 do_action.mask
```

## 4. 建议的数据模型

新实现应把“参数定义”和“运行状态”分离。

### 4.1 解析后的节点

```python
@dataclass(frozen=True)
class _NodeSpec:
    role: int
    node_id: int
    static_phys: dict
    do_actions: tuple
    triggers: tuple
```

`role` 只允许：

```text
1 = nodes[0]
2 = nodes[1]
3 = nodes[2]
```

本实验的节点 ID 与角色固定一致：

```text
nodes[0].id = 1
nodes[1].id = 2
nodes[2].id = 3
```

不接受重新编号、不连续编号或交换顺序。

### 4.2 解析后的计时器

```python
@dataclass(frozen=True)
class _TimerSpec:
    index: int  # 内部固定为 0、1、2
    key: str
    start_mode: str
    stop_mode: str
    start_event: object
    stop_event: object
    cascade_source: Optional[int]
```

`start_event/stop_event` 应在参数解析阶段转换成结构化数据，运行时不要反复
`split("_")` 猜字符串。

`params.timers` 只识别固定键 timer1～timer3。键缺失表示未配置；键存在时必须是
object，并同时包含合法字符串 start_mode 和 stop_mode。`null`、缺字段、空字符串均
原子拒绝。

`_timer_specs` 与 `_timer_runtime` 都以 0-based index `0、1、2` 为内部键；
只有生成前端键名时才使用 `timer{index + 1}`。不得在运行判据中混用 0-based 与
1-based 索引。

DI 条件可表示为：

```python
@dataclass(frozen=True)
class _DiCondition:
    bit: int
    target: int  # OPEN=1, CLOSE=0
```

### 4.3 计时器运行态

```python
@dataclass
class _TimerRuntime:
    start_hw_ts: Optional[int] = None
    stop_hw_ts: Optional[int] = None
```

不保存 `IDLE/RUNNING/STOPPED` 字符串，也不在运行中保存 `elapsed_ms`。逻辑状态完全由
两个可空时间戳推导：

```text
start_hw_ts is None                       -> 尚未启动
start_hw_ts is not None, stop_hw_ts None  -> 已启动、尚未停时
start_hw_ts is not None, stop_hw_ts set   -> 已完成
```

`stop_hw_ts` 不允许在 `start_hw_ts` 之前单独出现。

### 4.4 状态2出口

```python
@dataclass(frozen=True)
class _State2Exit:
    selected_timers: tuple[int, ...]
    required_mask: int
    required_value: int
    time_ctrl_only: bool
```

运行时只做整数判断，不再解析 `TIMER_1_2` 字符串。

## 5. 运行字段

`__init__()` 中初始化所有回调可能访问的字段：

```text
_current_role
_current_node_id
_experiment_entry_hw_ts
_experiment_deadline_monotonic
_experiment_watchdog_task
_state2_entry_hw_ts
_timer_specs
_timer_runtime
_state2_exit
_state2_timeout_ms
_state2_timeout_task
_state2_jump_pending
_manual_jump_pending
_last_di
_report_sent
_stopping
```

`_onSetup()` 开始时必须再次调用 `_reset_runtime_state()`，防止未来 API 实例复用时残留。

`_last_di` 初始化为：

```python
int(self.ctrl.di) & 0xFF
```

不能初始化为 0，因为协议中 `255` 才表示八路全部 OPEN。它只用于识别 DI 型
`start_mode` 和 `stop_mode` 的定向 OPEN/CLOSE 事件。

EngineCtrl 启动实验时会先触发保留节点 `0x0000`；FPGA 收到任何 start 指令都会回报
一次完整 DI 状态，所以节点0 start 回报会更新 `ctrl.di`。API setup 直接读取该值即可，
不需要 freshness 标志或额外 DI 查询接口。

若完整 DI 回报在 setup 后才进入 API，节点1有效 `tick=0` 以前的事件窗口只更新
`_last_di` 并返回，不启停 timer。实现和端到端测试必须锁定这两种允许的调度顺序。

## 6. 参数解析必须原子完成

推荐顺序：

```text
parse params
  -> recognize debounce（EngineCtrl 已先读取并设置）
  -> parse exactly three nodes
  -> require nodes[0..2].id exactly 1/2/3
  -> parse timers
  -> validate timer cascade matrix
  -> parse role-specific triggers
  -> compile state2 timer exit predicate
  -> compile physical values
  -> compile all DO actions
  -> build all ApiNodeData in a local dict
  -> final reserved-ID check
  -> upsertNodes once
  -> trigNode(1)
```

任何步骤失败时：

- 不调用 `upsertNodes()`；
- 不调用 `trigNode()`；
- 调用 `ctrl.stopTest(clear_error_message)`；
- 不留下部分节点。

只校验后端实际读取的字段。旧前端多传但后端完全不用的键直接忽略。

EngineCtrl 在 `api.setup()` 前已经读取 `params.debounce`，所以 `params` 非 object 或
debounce 无法转换时，错误可能先发生在框架层。ApiTimeMeas 只负责自己获得控制权后的
原子校验，不为此修改共享 EngineCtrl。

## 7. 三个状态角色

角色和节点 ID 固定：

```python
state1 = nodes[0]
state2 = nodes[1]
state3 = nodes[2]

assert state1["id"] == 1
assert state2["id"] == 2
assert state3["id"] == 3
```

目标固定：

```text
1 -> 2
2 -> 3
3 -> 0xFFFF
```

节点 ID 必须是非 bool 整数并严格等于对应角色的 `1、2、3`。入口固定为节点 1；
上传字典的 key 固定为 `{1, 2, 3}`，0 和 FFFF 永不出现在上传字典中。

不要读取 `node.next_id` 或 `trigger.next_id`。它们即使由旧前端附带也只作为未知附加字段
忽略。

## 8. 公共静态波形编译

三个状态均只接受：

```text
mode = STATIC
```

编译：

```python
base = self.physDictToReg(node["static"])
```

必须沿用开发指南中的物理量规则：

- 通道 0～11；
- layer 0～64；
- 每层两个有限数字；
- layer 0 第二项为频率 Hz；
- layer 1～64 第二项为相位 °；
- 第一项已经是所需幅值，不乘根号 2；
- 不使用 `is_delta=True`。

## 9. 状态1/3普通 DO 编译

输入项：

```json
{"delay": 0, "mask": 3}
```

纯函数：

```python
def _compile_absolute_do(raw_actions) -> list[int]:
    ...
```

规则：

1. `delay` 是 `0..65535` 的非 bool 整数；
2. `mask` 是 `0..255` 的非 bool 整数；
3. 输入顺序由前端按 delay 排好，后端保持；
4. 每项打包 `(delay << 8) | mask`；
5. 空数组返回 `[0]`，不能返回 `[]` 或 `None`。

空数组返回 `[0]` 有两个控制目的：

- 明确把八路 DO 归零；
- 让新节点的 `doActions` 为非空，使 USEEngine 取消旧节点未完成的 DO task。

状态3的这一行为会安全终止尚未执行完的状态2脉冲。

## 10. 状态2八路脉冲编译

### 10.1 输入校验

状态2 `do_actions` 必须恰好八项。数组下标就是 DO bit：

```text
index 0 -> 0x01
...
index 7 -> 0x80
```

每项只读取：

```text
delay
holdTime
```

二者必须是非 bool、非负整数，并满足：

```text
delay + holdTime <= 65535
```

### 10.2 事件生成

```python
events: dict[int, int] = {}

for channel, action in enumerate(raw_actions):
    bit = 1 << channel
    start = action["delay"]
    end = start + action["holdTime"]
    events[start] = events.get(start, 0) ^ bit
    events[end] = events.get(end, 0) ^ bit
```

必须使用 XOR：

- 每个通道在 start 翻转；
- 同一通道在 end 再翻转；
- 同一时刻的多个通道合并；
- `holdTime=0` 时同一 bit 的两次翻转互相抵消。

### 10.3 基线

底层只接受绝对 mask，因此在 setup 时必须确定：

```python
state2_baseline = state1 最后一条普通 DO 的 mask
```

状态1空数组会先归一化为 0，所以基线为 0。

这是协议声明值，不是运行时采样值。状态2必须生成相对该声明基线的确定绝对输出：

- 第一组翻转在 0 ms：首条直接输出 `baseline XOR toggle_at_0`；
- 第一组翻转晚于 0 ms：先在 0 ms 输出 baseline；
- 后续始终输出累计后的完整绝对 mask。

状态1若被提前触发，状态2仍按上述声明值执行。前端若要求状态1自身先实际到达最终
mask，应保证触发时刻晚于最后一条状态1 DO。权威 `tm.json` 在 0 ms 写 mask 0，是
无歧义路径。

不要直接读取 setup 时的 `ctrl.do` 作为状态2基线；那是进入状态1之前的值，不是未来
进入状态2时的值。

### 10.4 绝对时间线

```python
mask = state2_baseline
packed = []

for at_ms in sorted(events):
    toggle = events[at_ms]
    if toggle == 0:
        continue
    mask ^= toggle
    packed.append((at_ms << 8) | mask)
```

是否补基线必须检查第一条实际生成的 packed 动作，而不是 `min(events)`：

```python
if not packed or (packed[0] >> 8) > 0:
    packed.insert(0, state2_baseline)
```

例如某通道 `delay=0, holdTime=0` 在 0 ms 净抵消，而第一条有效事件在 5 ms，
仍必须补 0 ms 基线。

### 10.5 权威断言

`tm.json` 必须得到：

```text
0 ms   F3
2 ms   F7
6 ms   FF
12 ms  FB
36 ms  F3
100 ms 00
```

对应：

```python
[
    0x0000F3,
    0x0002F7,
    0x0006FF,
    0x000CFB,
    0x0024F3,
    0x006400,
]
```

这一断言应作为编译器最重要的纯函数测试。

## 11. timer 枚举解析

### 11.1 DI 名称

固定映射：

```python
DI_BITS = {
    "A": 0, "B": 1, "C": 2, "R": 3,
    "a": 4, "b": 5, "c": 6, "r": 7,
}
```

固定电平：

```text
OPEN  = 1
CLOSE = 0
```

### 11.2 start_mode

支持：

```text
ENTER_STATE_2
DI_{port}_{OPEN|CLOSE}_START
```

历史级联枚举矩阵：

```text
timer1: 不支持级联 start_mode
timer2: TIMER1_STOP_START
timer3: TIMER1_STOP_START 或 TIMER2_STOP_START
```

解析结果分别是：

```text
state-entry event
DI directional event
cascade source index
```

### 11.3 stop_mode

只支持：

```text
DI_{port}_{OPEN|CLOSE}
```

解析成 `_DiCondition(bit, target)`。

### 11.4 级联校验

严格按上述矩阵校验。必须拒绝：

- 引用不存在 timer；
- timer1 使用任何 TIMER* 级联；
- timer2 引用 timer2/timer3；
- timer3 引用 timer3；
- 未定义的 `TIMER3_STOP_START`。

一个前置计时器停止时，所有依赖它且 `start_hw_ts is None` 的计时器都使用同一个
`hw_ts` 启动。

## 12. timer 启停函数

### 12.1 启动

```python
def _try_start_timer(index: int, hw_ts: int) -> bool:
```

只允许 `start_hw_ts is None`：

```python
start_hw_ts = hw_ts & 0xFFFFFFFF
```

重复启动返回 `False`，不覆盖第一次启动时间。启动本身不把当前 DI 电平当作事件；
只有当前 `onDi()` 回调实际识别出的定向事件才可用于停时。

### 12.2 停止

```python
def _try_stop_timer(index: int, hw_ts: int) -> bool:
```

只允许：

```text
start_hw_ts is not None
stop_hw_ts is None
_forward_delta_us(hw_ts, start_hw_ts) is not None
```

命中后只保存：

```python
stop_hw_ts = hw_ts & 0xFFFFFFFF
```

运行中不计算也不缓存 `elapsed_us/elapsed_ms`。重复停止返回 `False`，不覆盖第一次
停时时间。停止后在同一个 DI 回调的固定点循环中处理级联启动。

### 12.3 有效时间戳

必须按来源区分：

- `onUpdate()` 的节点 ACK 时间戳必须是非 bool、非负整数；`-1` 表示 ACK/通信失败；
- `onDi()` 的时间戳必须是非 bool 整数，但允许为负。

USEEngine 传给 `onDi()` 的值是 FPGA SOE 时间戳减去防抖估算时间。在 U32 回绕附近，
该 Python 整数可能为负但仍然有效。DI 回调入口必须立即归一化：

```python
di_hw_ts_u32 = hw_ts & 0xFFFFFFFF
```

随后启动、停时和级联全部使用该 U32 值。不能把合法的负 DI 时间戳误判为通信错误。

发生无效时间戳时应：

- 不伪造主机时间；
- 发送清晰错误并停止实验；
- 未完成 timer 最终保持 `null`。

### 12.4 U32 事件顺序

DI 时间戳已经减去防抖估算时间，因此 Python 回调先后不能单独证明物理事件发生在
timer 启动以后。统一使用半周期 U32 顺序函数：

```python
U32_MASK = 0xFFFFFFFF
U32_HALF = 0x80000000
WATCHDOG_MARGIN_US = 1_000_000

def _forward_delta_us(event_ts: int, reference_ts: int) -> Optional[int]:
    delta = (event_ts - reference_ts) & U32_MASK
    return delta if delta < U32_HALF else None
```

规则：

- 返回 0 合法，两个有序反馈可具有同一微秒值；
- 返回 `None` 时，事件位于参考点以前或超出可无歧义判断的窗口；
- DI 事件相对实验入口、停时事件相对 timer 启时都必须得到非 `None`；
- 不得使用普通有符号 `<`/`>` 比较 U32 时间戳。

因此单次实验及任一计时区间必须小于 `2^31 µs`，约 `35 min 47.484 s`。超过该范围
无法仅凭一个 U32 值区分事件先后，不属于本协议支持范围。

定义：

```python
MAX_EXPERIMENT_US = U32_HALF - WATCHDOG_MARGIN_US
MAX_TIMEOUT_MS = (MAX_EXPERIMENT_US - 1) // 1000  # 2_146_483
```

进入节点1时用 `loop.time() + MAX_EXPERIMENT_US / 1_000_000` 保存绝对
`_experiment_deadline_monotonic`，并创建全局可取消 watchdog。到达 deadline 仍未
结束时，它必须把 `_stopping` 前置为真并调用带明确原因的 `stopTest()`。每个运行
回调也必须在处理 timer 前检查相同 deadline；即使 `asyncio.sleep()` 因调度晚醒，
回调也不能继续接受超期事件。主机时钟只用于强制最长运行时限，不参与任何计时结果。

## 13. DI 事件处理

### 13.1 定向 DI 事件

`onDi()` 收到完整 DI 值和本次 SOE 时间戳。先用 `_last_di` 和当前值一次性识别本回调
实际发生的全部定向事件：

```python
events = set()

for bit in range(8):
    previous_bit = (previous_di >> bit) & 1
    current_bit = (current_di >> bit) & 1
    if previous_bit != current_bit:
        events.add(_DiCondition(bit, current_bit))
```

其中：

```text
_DiCondition(bit, 1) = 该位本次实际进入 OPEN
_DiCondition(bit, 0) = 该位本次实际进入 CLOSE
```

Timer 的 DI 启动和停时都只匹配这个 `events` 集合：

```python
timer_di_condition in events
```

不能仅因当前完整 DI 中某位已经是目标电平就停表。由其他无关 DI 位产生的 SOE 不得
使 timer 启动或停止。这与状态1/3硬件 `DI_MATCH` 的参考电平语义不同。

### 13.2 启动后的首个停时事件

Timer 只有在 `start_hw_ts` 已存在、`stop_hw_ts` 尚为空时，才消费其 `stop_mode`
对应的定向事件。因此：

- timer 启动以前发生的同类 DI 事件直接忽略，不能在以后补记；
- 启动后遇到的第一个目标定向事件写入 `stop_hw_ts`；
- 后续重复目标事件不覆盖第一次停时时间；
- 完整 DI 数值重复且没有位变化时，`events` 为空，不产生任何启停；
- 不用普通 U32 数值大小判断事件先后；先用回调顺序处理事件，再用
  `_forward_delta_us()` 排除防抖后到、但物理时间位于启动点以前的事件。

如果启动前目标事件已经发生，timer 启动后必须等待该位先离开目标状态、再重新进入
目标状态，才会得到新的有效停时事件。

不得用 `stop_event_hw_ts == start_hw_ts` 或 `!=` 判断事件是否发生在启动后。两个不同
硬件反馈可能恰好具有相同的微秒计数值；必须要求当前回调包含目标定向事件，且
`_forward_delta_us(stop_event_hw_ts, start_hw_ts)` 非 `None`。结果为 0 时仍可报告
`0.000 ms`。

### 13.3 单次回调固定点顺序

每次 `onDi()` 先校验生命周期、输入类型和全局 deadline，再把 `hw_ts` 归一化为 U32，
固定执行：

```text
1. 校验 API/ctrl 状态、di/hw_ts 类型和主机绝对 deadline
2. 归一化 hw_ts，snapshot previous_di/current_di
3. 校验实验事件窗口和 SOE 相对实验入口的 U32 顺序
4. 识别本回调全部 `_DiCondition(bit, OPEN/CLOSE)` 定向事件
5. 用这些事件启动匹配 DI_*_START 且尚未启动的 timer
6. 执行最多 3 轮固定点：
   a. 对所有已启动且未停时的 timer，用同一 events 集合匹配 stop_mode 并停表
   b. 对本轮刚停下的 timer，用同一个 hw_ts 启动其尚未启动的级联 timer
   c. 若本轮既无新停表也无新启动，提前结束
7. 保存 current_di 为 _last_di
8. 若当前是状态2，检查状态2出口
```

三轮上限来自最多三个 timer，足以完成：

```text
timer1 本回调停时
  -> timer2 同 timestamp 启动并在同一 events 中停时
  -> timer3 同 timestamp 启动并在同一 events 中停时
```

这种真实发生在同一个 DI SOE 回调中的启动与停时允许得到 `0.000 ms`。不能把启动前
旧事件重放成 0 ms。

必须先完成全部固定点启停，再判断状态2出口。即使当前是状态3，事件识别和 timer
固定点仍执行，使未完成 timer 能继续计时。

核心伪代码：

```python
if (
    not self.isActive
    or not self.ctrl.running
    or self._stopping
):
    return

if (
    isinstance(di, bool)
    or not isinstance(di, int)
    or not 0 <= di <= 0xFF
    or isinstance(hw_ts, bool)
    or not isinstance(hw_ts, int)
):
    self._stop_with_error("invalid DI feedback")
    return

if (
    self._experiment_deadline_monotonic is not None
    and asyncio.get_running_loop().time()
        >= self._experiment_deadline_monotonic
):
    self._stop_with_error(
        "experiment reached safe U32 runtime limit"
    )
    return

ts = hw_ts & 0xFFFFFFFF
previous_di = self._last_di
current_di = di

if (
    self._current_role not in (1, 2, 3)
    or self._experiment_entry_hw_ts is None
):
    self._last_di = current_di
    return

if _forward_delta_us(
    ts,
    self._experiment_entry_hw_ts,
) is None:
    # 防抖完成后迟到、但物理时间位于实验入口以前的事件。
    self._last_di = current_di
    return

events = {
    _DiCondition(bit, (current_di >> bit) & 1)
    for bit in range(8)
    if ((previous_di ^ current_di) & (1 << bit))
}

# DI 型启动先于本回调的停时固定点。
for index, spec in configured_timers:
    if (
        spec.start_event is not None
        and spec.start_event in events
        and runtime[index].start_hw_ts is None
    ):
        runtime[index].start_hw_ts = ts

for _ in range(3):
    newly_stopped = []

    for index, spec in configured_timers:
        rt = runtime[index]
        if (
            rt.start_hw_ts is not None
            and rt.stop_hw_ts is None
            and spec.stop_event in events
            and _forward_delta_us(
                ts,
                rt.start_hw_ts,
            ) is not None
        ):
            rt.stop_hw_ts = ts
            newly_stopped.append(index)

    newly_started = False
    for source_index in newly_stopped:
        for dependent_index in cascade_dependents[source_index]:
            dependent = runtime[dependent_index]
            if dependent.start_hw_ts is None:
                dependent.start_hw_ts = ts
                newly_started = True

    if not newly_stopped and not newly_started:
        break

self._last_di = current_di
self._maybe_exit_state2(current_di)
```

`configured_timers`、`cascade_dependents` 都在 setup 完成后固定。循环不能把前一次
`onDi()` 的 events 保存下来供以后使用。

### 13.4 DI 事件处理窗口

Timer 事件窗口从节点 1 的有效 `tick=0` 回调开始，到实验请求停止或收到 FFFF 为止：

- 节点1生效前若收到在途 DI，只更新 `_last_di`，不启动或停止 timer；
- 节点1生效后才回调、但修正 SOE 时间仍早于实验入口的事件，也只更新 `_last_di`；
- 状态1、状态2、状态3中的有效 DI 回调都执行第 13.3 节流水线；
- 离开状态2不清空 timer 时间戳，状态3仍可完成未停 timer；
- `ctrl.running` 已为 False、API 已停止或正在停止时，不再接受新 timer 事件；
- `onUpdate()` 的节点 ACK 和 `ctrl.di` 当前快照都不是 DI 定向事件。

这样既不会把 setup 到节点1真正生效之间的 DI 误记为实验事件，也不会漏掉状态1中的
DI 启动或状态3中的返回停时。

## 14. 状态2 TIMER 出口编译

### 14.1 枚举映射

```python
TIMER_EXIT_MEMBERS = {
    "TIMER_1": (0,),
    "TIMER_2": (1,),
    "TIMER_3": (2,),
    "TIMER_1_2": (0, 1),
    "TIMER_2_3": (1, 2),
    "TIMER_3_1": (2, 0),
    "TIMER_1_2_3": (0, 1, 2),
}
```

`TIME_CTRL` 映射为空组合，并标记只使用 TIME_OUT。

### 14.2 电平组合

对每个选中 timer 的 `stop_mode`：

```python
bit_mask = 1 << stop.bit

if required_mask & bit_mask:
    existing = 1 if required_value & bit_mask else 0
    if existing != stop.target:
        raise ValidationError("conflicting timer stop levels")

required_mask |= bit_mask
if stop.target == 1:
    required_value |= bit_mask
```

例如：

```text
timer1 DI_A_CLOSE -> A=0
timer2 DI_B_OPEN  -> B=1
```

结果：

```text
required_mask  = 0x03
required_value = 0x02
```

### 14.3 运行判据

```python
exit_spec = self._state2_exit
if (
    exit_spec is None
    or exit_spec.time_ctrl_only
    or not exit_spec.selected_timers
):
    return

all_have_stop_ts = all(
    runtime[i].stop_hw_ts is not None
    for i in exit_spec.selected_timers
)
level_match = (
    (current_di & exit_spec.required_mask)
    == exit_spec.required_value
)

if (
    current_role == 2
    and not _state2_jump_pending
    and all_have_stop_ts
    and level_match
):
    _request_state2_exit("TIMER")
```

要求所选 timer 已有停时时间戳，并且当前完整 DI 电平组合同时满足：

- 不能只看历史 `stop_hw_ts`，忽略接点已经返回；
- 不能只看当前电平，让尚无停时事件的 timer 触发跳转。

这里的当前电平组合只决定状态2是否可以退出，不参与 timer 停时时间的记录。Timer
停时仍只能来自第 13 节识别出的启动后首个目标定向事件。

`TIME_CTRL` 或仅配置 `TIME_OUT` 时不得进入空集合判断；否则 `all([]) == True` 且零
mask 恒匹配，会错误地在进入状态2后立即跳到节点3。

`_request_state2_exit()` 是 TIMER 和状态2 TIME_OUT 的唯一跳转入口。它必须：

1. 再次确认当前角色为2且没有 pending；
2. 先设置 `_state2_jump_pending=True`；
3. 取消另一条尚未到期的状态2超时任务（从超时任务自身调用时只清空引用）；
4. 调用 `ctrl.trigNode(3)`；
5. 若调用明确返回 `False`，恢复标记或进入统一停止处理。

这样两个 API 事件即使在同一事件循环轮次就绪，也只能提交一次状态2跳转。

## 15. 各状态 trigger 编译

### 15.1 状态1

目标固定为节点 2：

| condition | 实现 |
|---|---|
| `TIME_OUT` | `timeoutMs/timeoutId=2` |
| `DI_MATCH` | `diMatchMask/diMatchId=2` |
| `MANUAL_TRIG` | API 保存允许标记 |

状态1/3 `DI_MATCH.di_mask` 必须是非 bool 整数，只允许 `0x000..0x7FF` 已知位，并且
`di_mask & 0xFF != 0`。本方案有意只复用状态序列的三种 condition 和 di_mask 位
语义；不读取 DI_MATCH 内嵌 timeout 或 post_condition_delay_ms，安全超时使用独立
TIME_OUT。

### 15.2 状态2

目标固定为节点 3：

| condition | 实现 |
|---|---|
| `TIMER_*` | API 编译 selected timers 和 DI 电平组合 |
| `TIME_CTRL` | 不生成 timer 判据 |
| `TIME_OUT` | API 保存 `state2_timeout_ms`，进入状态2时创建可取消任务 |

状态2不得配置 MANUAL_TRIG/DI_MATCH。

TIME_CTRL 必须同时带 TIME_OUT 是本次新协议为避免无出口状态规定的校验约束。

状态2的 TIMER 与 TIME_OUT 必须统一由 API 的 `_request_state2_exit()` 仲裁，不能再把
TIME_OUT 同时写入 USEEngine。否则 USEEngine timeout 已选中节点3、但节点3 ACK 尚未
到达 API 时，API 仍可能留下第二个 `manualTrig(3)`，使节点3重复进入。

### 15.3 状态3

目标固定为 FFFF：

| condition | 实现 |
|---|---|
| `TIME_OUT` | `timeoutMs/timeoutId=0xFFFF` |
| `DI_MATCH` | `diMatchMask/diMatchId=0xFFFF` |
| `MANUAL_TRIG` | API 保存允许标记，命中后 `stopTest()` |

### 15.4 冲突限制

每个 `ApiNodeData` 只有一个 timeout 字段和一个 DI 字段，因此必须拒绝：

- 同一节点两个不同 TIME_OUT；
- 同一节点两个不同 DI_MATCH；
- 状态1或状态3把 MANUAL_TRIG 与其他 trigger 混用；
- 状态2两个 TIMER 组合；
- TIMER 组合和 TIME_CTRL 同时存在；
- 不属于该角色的条件。

未知附加字段忽略；未知 condition 枚举拒绝。

所有 `TIME_OUT.timeout` 必须是 `1..MAX_TIMEOUT_MS` 的非 bool 整数。值 0 必须拒绝；
状态1/3中当前 USEEngine 会把 0 解释为未配置 timeout，状态2也沿用同一协议约束。

还必须保证：

- 状态1至少有一个有效出口；
- 状态2至少有 TIMER 组合或 TIME_OUT；
- TIME_CTRL 必须与 TIME_OUT 同时出现；
- 状态3至少有一个有效出口。

MANUAL_TRIG 的互斥规则与状态序列一致：手动触发必须是该状态唯一的 trigger，避免
`next_node` 与已经发生、但尚未回调到 API 的 TIME_OUT/DI_MATCH 竞争。

## 16. `ApiNodeData` 构建

```python
api_nodes = {
    1: ApiNodeData(
        mode=1,
        base=state1_base,
        doActions=state1_do,
        timeoutMs=...,
        timeoutId=2,
        diMatchMask=...,
        diMatchId=2,
    ),
    2: ApiNodeData(
        mode=1,
        base=state2_base,
        doActions=state2_do,
    ),
    3: ApiNodeData(
        mode=1,
        base=state3_base,
        doActions=state3_do,
        timeoutMs=...,
        timeoutId=0xFFFF,
        diMatchMask=...,
        diMatchId=0xFFFF,
    ),
}
```

上传前最终断言：

```python
assert 0x0000 not in api_nodes
assert 0xFFFF not in api_nodes
assert set(api_nodes) == {1, 2, 3}
```

状态2实例的 `timeoutMs/timeoutId/diMatchMask/diMatchId` 都保持 `None`；它的 TIMER
和 TIME_OUT 全部由 API 处理。

## 17. 生命周期回调

### 17.1 `_onSetup()`

完成纯解析后：

```python
if not self.ctrl.upsertNodes(api_nodes):
    return

self._last_di = int(self.ctrl.di) & 0xFF
self.ctrl.trigNode(1)
```

`upsertNodes()` 失败后不再触发入口。

这里直接使用 EngineCtrl 已维护的 `ctrl.di` 初始化事件比较基线；不需要也不得为了本
实验给 BaseApi、EngineCtrl 或 EngineBridge 新增 `diStart` 接口。

### 17.2 `onUpdate()`

只处理：

```text
有效 API
非 bool 且非负的节点 ACK hw_ts
已知 nodeId
tick == 0
```

类型校验必须发生在 `hw_ts & 0xFFFFFFFF` 以前。已有
`_experiment_deadline_monotonic` 且主机单调时钟已经到期时，调用统一的安全 U32
时限错误停止并返回，不再接受该节点回调。

节点 ID 固定为 1、2、3，因此每个有效节点的 `tick=0` 回调都必须先统一更新：

```python
previous_role = _current_role
_current_role = nodeId
_current_node_id = nodeId
```

不能只在节点2赋值：否则节点1期间的 DI 计时事件会被事件窗口错误忽略，进入节点3后也
可能因角色仍残留为2而重复请求 `trigNode(3)`。

随后按角色执行：

- 第一次进入节点1时保存
  `_experiment_entry_hw_ts = hw_ts & 0xFFFFFFFF`；
- 第一次进入节点1时保存带 1 秒安全裕量的
  `_experiment_deadline_monotonic`，并创建唯一的 `_experiment_watchdog_task`；
- `previous_role == 2 and nodeId != 2` 时取消并清理状态2超时任务；
- 进入节点3时也必须确保状态2超时任务已经取消。

仅在进入节点 2 时执行：

```python
if _state2_entry_hw_ts is None:
    _state2_entry_hw_ts = hw_ts & 0xFFFFFFFF

for timer whose start_mode == ENTER_STATE_2:
    _try_start_timer(timer, _state2_entry_hw_ts)

if (
    _state2_timeout_ms is not None
    and not _state2_jump_pending
    and _state2_timeout_task is None
):
    _start_state2_timeout_task(_state2_timeout_ms)

_maybe_exit_state2(int(self.ctrl.di) & 0xFF)
```

`onUpdate(2, 0, hw_ts)` 中的当前 DI 电平绝对不能伪装成一条 DI 事件，不能据此启动
DI 型 timer 或停表。上面的 `_maybe_exit_state2()` 只允许检查此前已经具有
`stop_hw_ts` 的所选 timer 与当前完整 DI 组合；它不写任何 timer 时间戳。这样此前确实
完成的 timer 可在进入状态2时决定出口，但启动前已经存在的目标电平不会被误记为停时。

固定三态流程只会进入节点2一次；防御性实现仍应只保存第一次有效的
`_state2_entry_hw_ts`，重复回调不得覆盖 timer 启动基准。

`_state2_jump_pending` 只在 `_reset_runtime_state()` 中初始化为 `False`，不能在
节点2回调中重置。否则跳转请求已经发出、节点3 ACK 尚未到达时若出现重复节点2回调，
会重新打开仲裁并提交第二次 `trigNode(3)`。

状态2超时协程只负责：

```text
await asyncio.sleep(state2_timeout_ms / 1000)
确认 API/ctrl 仍活动且当前仍为状态2
若主机绝对 deadline 已到达，转入统一超时限错误停止
清空自身 task 引用
调用 _request_state2_exit("TIME_OUT")
```

重复节点2回调或已经 pending 后的回调不得创建第二个任务。取消任务时要安全处理
`CancelledError`，不得从旧状态残留到节点3。安全超时只决定状态跳转，不参与 timer
报告。

全局 watchdog 只负责：

```text
按 _experiment_deadline_monotonic 计算剩余时间并 await
确认 API/ctrl 仍活动且尚未 stopping
清空自身 task 引用并把 _stopping 前置为 True
stopTest("time_measurement: experiment reached safe U32 runtime limit")
```

它不读写 timer 时间戳。正常进入停止流程和 `_onStop()` 都必须取消并清理它；从任务
自身停止时只清空引用，不能取消当前 task。所有异步任务的清理都必须 identity-safe：
任务完成或捕获 `CancelledError` 时，只有
`self._stored_task is asyncio.current_task()` 才能把对应字段置为 `None`；旧任务的
`finally` 不能覆盖后来创建的新任务引用。

然后：

```python
self.ctrl.sendNodeUpdate(nodeId)
```

不要调用私有 `ctrl._send()`，不要重复发送 static。

### 17.3 `onDi()`

执行第 13 节固定流水线。

不要因为：

```text
current_role != state2
```

而提前 return；只有 state2出口判断受该条件限制。

### 17.4 `onWebCommand()`

只接受：

```json
{
  "module": "time_measurement",
  "cmd": "next_node"
}
```

规则：

```text
state1 + MANUAL_TRIG -> trigNode(2)
state3 + MANUAL_TRIG -> stopTest()
state2               -> error
未配置 MANUAL_TRIG  -> error
重复 pending         -> error
```

跳转 pending 在目标状态的 tick0 回调中清除。

### 17.5 `_onStop()`

先取消并清理状态2超时任务和全局 watchdog，再只发送一次：

```python
report = {}

for index in range(3):
    runtime = timer_runtime.get(index)
    key = f"timer{index + 1}_elapsed_ms"
    if (
        runtime is None
        or runtime.start_hw_ts is None
        or runtime.stop_hw_ts is None
    ):
        report[key] = None
        continue

    elapsed_us = (
        runtime.stop_hw_ts - runtime.start_hw_ts
    ) & 0xFFFFFFFF
    report[key] = round(
        elapsed_us / 1000.0,
        3,
    )

self.ctrl.sendReport(report)
```

只有 `_onStop()` 计算 U32 时间差；运行过程中不缓存 elapsed。未配置、未启动或未停时
均报告 `null`。不需要 FFFF 硬件时间戳，因为报告不是状态持续时间。

## 18. 错误处理

建议统一：

```python
def _stop_with_error(self, message: str):
    if self._stopping:
        return
    self._stopping = True
    self.ctrl.stopTest(
        f"time_measurement: {message}"
    )
```

要求：

- 参数错误信息带 JSON 路径；
- 运行回调异常不能传播到 EngineCtrl 事件循环；
- 停止请求只发一次；
- 不能把错误 timer 结果伪造为 0；
- `_onStop()` 仍发送三个键的报告，未完成项为 null。

## 19. 验证计划

实现阶段可使用临时测试脚本，验收后按用户要求决定是否保留；不能用旧
`ApiTimeMeas.py` 的输出反向约束新协议。

### 19.1 权威 JSON

必须验证：

- 三个 `node.static` 被实际读取；
- 只接受 `nodes[0..2].id == 1/2/3`，入口固定为节点 1；
- `next_node` 只让状态1进入状态2；
- timer1/2/3 在 `onUpdate(2, 0, hw_ts)` 使用同一个 U32 入口硬件时间戳启动；
- 进入状态2时当前 DI 已处于 stop 目标电平，不会被当成定向事件停表；
- 状态2 TIMER_1 由 A CLOSE 后推进状态3；
- timer2/3 离开状态2后仍可继续停表；
- 报告精确为三个键。

### 19.2 DO 编译

纯函数断言：

```text
tm.json -> F3,F7,FF,FB,F3,00
```

另测：

- 同时刻多 bit 合并；
- holdTime=0 净翻转抵消；
- 0 ms 净抵消且首个有效事件晚于0时仍补0 ms基线；
- delay+holdTime=65535；
- 超出 65535 拒绝；
- 状态1/3空数组得到 `[0]`；
- 状态3进入时取消状态2剩余 DO task 并归零。

### 19.3 timer

至少覆盖：

- ENTER_STATE_2 启动；
- 八路 OPEN/CLOSE DI 启动；
- 八路 OPEN/CLOSE 定向事件停止；
- 节点1 ACK 后才到达、但修正 SOE 时间早于 `experiment_entry_hw_ts` 的 DI 只更新
  `_last_di`，不产生 timer 事件；
- timer 启动前的目标事件被忽略，启动后必须重新离开并进入目标才停表；
- 物理事件发生在状态2入口以前、但防抖完成后的回调在入口以后到达时，半周期判定仍
  拒绝该停时；
- 无关 DI 位变化不会使目标位已经处于目标电平的 timer 停表；
- 完整 DI 重复回报不形成事件；
- timer2 由 TIMER1 级联启动；
- timer3 由 TIMER1/TIMER2 级联启动；
- 单次 DI 回调通过最多三轮固定点完成 timer1→timer2→timer3 级联；
- 同一回调中真实发生的 DI 启动/停时或级联启动/停时允许报告 0.000 ms；
- 两个不同 DI 回调恰好携带相同 U32 微秒值时，后一回调的首次合法 stop 事件仍被接受；
- TIMER3_STOP_START 和后向级联拒绝；
- U32 回绕；
- `delta == 0`、`delta == 0x7FFFFFFF` 合法，`delta >= 0x80000000` 拒绝；
- TIME_OUT 接受 1 和 2_146_483 ms，拒绝 0、bool 和大于 2_146_483 ms；
- 实验未结束达到带裕量的全局 watchdog deadline 时主动报错停止，所有任务引用被清理；
- 非整数/bool DI 或时间戳在任何 `int()`/位运算前被拒绝；
- 旧 timeout/watchdog task 的 `finally` 不能清空后来创建的新 task 引用；
- 回绕前启动、回绕后停时只在 `_onStop()` 计算 U32 差值；
- 第一次停时后再次发生相同定向事件不覆盖 `stop_hw_ts`；
- 未启动/未停止报告 null；
- 运行中不保存 state 字符串或 elapsed 值；
- state3 中继续停表；
- 已配置 timer 为 null、缺 start_mode 或缺 stop_mode 时拒绝。

### 19.4 状态2出口

覆盖所有枚举：

```text
TIMER_1
TIMER_2
TIMER_3
TIMER_1_2
TIMER_2_3
TIMER_3_1
TIMER_1_2_3
TIME_CTRL
TIME_OUT
```

特别验证：

- 单 timer；
- 多 timer 在同一 DI SOE 停止；
- 多 timer 分别停止但当前电平最终同时满足；
- 已有 `stop_hw_ts` 但当前电平已返回时不跳；
- 当前电平满足但所选 timer 尚无 `stop_hw_ts` 时不跳；
- 同 bit OPEN/CLOSE 冲突在 setup 拒绝；
- TIMER 与 TIME_OUT 接近同时只进入一次状态3；
- 节点2 `ApiNodeData` 不配置底层 timeout，TIME_OUT 由 API 可取消任务触发；
- TIMER 先命中时取消状态2 timeout task，TIME_OUT 先命中时同样只提交一次跳转；
- 进入节点3或停止实验后不存在遗留状态2 timeout task；
- `TIME_CTRL` 和仅有 `TIME_OUT` 不会因空 timer 集合、零 mask 在进入状态2时立即跳转；
- timer 已在状态1取得 `stop_hw_ts`，进入状态2且当前完整 DI 组合满足时可立即推进，
  但 `onUpdate(2,0,ts)` 本身不生成任何 DI 事件或 timer 时间戳；
- timer 已停时但当前组合不满足时，等后续 DI 恢复组合后再推进，且不覆盖原停时时间。

### 19.5 状态1/3出口

分别验证：

```text
MANUAL_TRIG
TIME_OUT
DI_MATCH
```

并确认 MANUAL_TRIG 与其他 trigger 混用时原子拒绝，状态3自动或手动结束后报告只
发送一次。

### 19.6 兼容性

验证：

- 顶层、node、timer、trigger、do item 的未知附加字段不影响解析；
- 后端实际读取字段类型错误仍原子拒绝；
- 节点 ID 不是严格的 1、2、3，或交换节点位置时原子拒绝；
- 不为时间测量进一步修改 BaseApi/EngineCtrl/USEEngine；
- `_last_di` 直接取 `ctrl.di & 0xFF`，不新增 diStart 接口；
- 节点0 start 的完整 DI 回报在 setup 前到达时由 `ctrl.di` 初始化基线；
- 节点0 start 的完整 DI 回报在 setup 后、节点1入口前到达时只更新 `_last_di`，
  不产生 timer 事件；
- 全局 watchdog 的主机时间只用于运行上限，不进入任何 elapsed 计算；
- API 只发送 nodeId 和 report，不重复发送 static/di/do。

## 20. 已执行的实施顺序

本次实现按以下顺序完成：

1. 在不会被 `BaseApi.create()` 动态发现的位置创建纯枚举解析器和数据类；
2. 完成 params/nodes/timers 原子校验；
3. 完成状态1/3普通 DO 编译器；
4. 完成状态2八路 pulse 编译器并用 `tm.json` 锁定结果；
5. 完成定向 DI 事件、timer 双时间戳和三轮级联固定点纯逻辑；
6. 完成 U32 半周期事件顺序判定；
7. 完成 TIMER 组合到 DI 电平谓词的编译；
8. 完成状态2 API timeout task、全局半周期 watchdog 与统一出口仲裁；
9. 构建三个 `ApiNodeData`；
10. 实现 `_onSetup()`；
11. 实现 `onUpdate()` 与 nodeId 回复；
12. 实现 `onDi()` 和状态2 API跳转；
13. 实现 `next_node`；
14. 实现 `_onStop()` 中的 U32 timer 报告计算；
15. 使用 FakeCtrl 做边界验证；
16. 使用真实 USEEngine + EngineCtrl 做端到端验证；
17. 验收通过后发布 `ApiTimeMeas.py`、声明模块键并恢复 Registry 注册；
18. 删除临时测试文件并同步文档实施状态。

## 21. 完成定义

只有同时满足以下条件才算完成：

- `tm.json` 可按三个独立消息完成全流程；
- 三个状态的 static 和 DO 都按新协议生效；
- 状态2八路 pulse 时间线完全正确；
- timer 只用硬件时间戳计算；
- 防抖迟到事件通过 U32 半周期规则排除，单次实验/计时区间限制小于 `2^31 µs`；
- 接近半周期时由带安全裕量的全局 watchdog 明确报错停止，不静默产生 U32 取模报告；
- TIMER 组合由 API 根据所选 `stop_hw_ts` 是否存在和实际 DI 电平推进；
- 状态2 TIMER 与 TIME_OUT 统一由 API 仲裁，不留下底层/API 双跳转竞态；
- 状态3仍能完成尚未停止的 timer；
- 报告三个键、按 `round(value, 3)` 处理、未完成 null；
- 上传节点严格只有 1、2、3，0/FFFF 未被上传覆盖；
- 未为时间测量进一步修改 BaseApi/EngineCtrl/USEEngine；
- 旧 API 代码未恢复；
- Registry 只在新实现验收后重新注册。

## 22. 实施验收结果

已完成的自动验证：

- 权威 `tm.json` 可编译为且仅编译为节点 `1、2、3`，不会覆盖 `0` 或 `0xFFFF`；
- 状态2 DO 精确编译为
  `0x0000F3、0x0002F7、0x0006FF、0x000CFB、0x0024F3、0x006400`；
- 状态2的 `timeoutMs/timeoutId/diMatchMask/diMatchId` 均保持 `None`；
- 19 组测试函数全部通过，其中参数化覆盖 16 种 DI 启时枚举和 16 种 DI 停时枚举；
- 已覆盖稳定目标电平不触发、启动前迟到事件、U32 回绕、同时间戳零时长、三级级联、
  旧事件不重放、多计时器当前电平组合、TIMER/TIME_OUT 单次仲裁和任务清理；
- 已使用真实 `USEEngine.coreLoop` 与等价 EngineCtrl 编译/派发路径完成
  `1 -> 2 -> 3 -> 0xFFFF` 端到端验证；
- `compileall`、`git diff --check`、Registry 导入和 `BaseApi.create()` 动态发现均通过。

仍需在设备联调时确认：保留节点0的 start 回报在 API setup 前已经把完整 DI 快照写入
`EngineCtrl.di`。当前实现直接以 `ctrl.di & 0xFF` 初始化 `_last_di`，不增加
`diStart` 接口，也不会把首次稳定电平误当成动作边沿。
