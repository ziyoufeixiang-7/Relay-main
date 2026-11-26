# 状态序列实验 API

> 模块：`universal_sequence`
>
> 完整权威示例：项目根目录 `StatSequApi.json`
>
> 本文定义前端协议、字段含义、`ApiNodeData` 映射、内部节点生成、运行交互和报告。
> 实现应同时遵守项目根目录《API实验代码开发指南.md》。

## 1. 实验用途

状态序列实验允许前端提交一组有向状态节点。每个节点可以：

- 输出一组固定物理量；
- 在固定物理量上连续累加步进量；
- 按相对节点进入时刻执行 DO 动作；
- 通过 DI、超时、扫描完成或前端手动命令跳转；
- 在条件成立后继续保持一段时间，再进入目标节点；
- 将 `next_id=-2` 转换为实验终止。

API 的职责是：

```text
前端节点
  -> 参数校验
  -> 保留前端主节点 ID
  -> 物理量转换
  -> ApiNodeData 主节点
  -> 按需生成 post-condition 延时节点
  -> EngineCtrl.upsertNodes()
  -> EngineCtrl.trigNode()
  -> nodeId 更新与最终报告
```

API 不直接拼 FPGA 帧，也不重复发送 EngineCtrl 已经负责的 static、DI、DO 更新。

## 2. 权威示例包含两个独立消息

`StatSequApi.json` 依次展示了两个独立的前端消息：

1. `start`：启动四节点状态序列；
2. `next_node`：在 MANUAL_TRIG 节点手动推进。

它们应分别发送，不能作为一个 JSON 根对象一次发送。

## 3. 启动消息

外层格式：

```json
{
  "module": "universal_sequence",
  "cmd": "start",
  "params": {
    "debounce": 10,
    "nodes": []
  }
}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `module` | string | 是 | 固定为 `universal_sequence` |
| `cmd` | string | 是 | 启动时固定为 `start` |
| `params` | object | 是 | 状态序列参数 |
| `params.debounce` | number | 否 | DI 防抖时间，单位 ms |
| `params.nodes` | array | 是 | 非空的前端状态节点数组 |

### 3.1 debounce

权威示例使用：

```json
{
  "debounce": 10
}
```

含义是 10 ms。

该字段由 `EngineCtrl.startTest()` 在创建实验节点前读取并设置，状态序列 API 不应再次
转换或重复设置。字段省略时，当前 EngineCtrl 使用 20 ms。

## 4. nodes 总体结构

```text
Node
├─ id
├─ mode
├─ static
├─ dynamic              # 仅 SWEEP
├─ do_actions
└─ triggers
```

完整字段表：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `id` | int | 是 | `1..1000` 内的唯一前端节点 ID；可不连续 |
| `mode` | enum string | 是 | `STATIC` 或 `SWEEP` |
| `static` | object | 是 | 进入节点时生效的绝对物理量 |
| `dynamic` | object | SWEEP 是 | 扫描间隔、次数和单步增量 |
| `do_actions` | array | 否 | 节点内 DO 绝对定时动作；省略等价于空数组 |
| `triggers` | array | 否 | 节点出口；省略等价于空数组 |

节点级不存在默认 `next_id`。所有自动或手动出口必须在 `triggers[]` 中定义。

### 4.1 前端节点 ID

前端节点 ID 必须：

- 位于 `1..1000`；
- 是唯一整数；
- 不要求从 1 开始；
- 不要求连续或递增。

主节点的 Engine ID 与前端 ID 完全一致：

```text
engine_id = front_id
```

例如以下 ID 都合法：

```text
7, 20, 3, 1000
```

节点数组顺序只决定实验入口：

```python
start_engine_id = params["nodes"][0]["id"]
```

也就是说，上例从节点 7 开始。之后只按各 trigger 的 `next_id` 跳转；后端既不自动
加 1，也不把数组中的“下一个元素”当作隐式目标。

API 可以建立 `front_id -> ApiNodeData` 和反向查询字典，但这是为了校验和回调识别，
不是为了重新编号。

### 4.2 内部 ID 范围

所有传给 `upsertNodes()` 的 key 必须位于：

```text
0x0001 .. 0xFFFE
```

绝对禁止写入：

```text
0x0000
0xFFFF
```

`0x0000`、`0xFFFF` 是系统保留节点。`next_id=-2` 只会被转换为跳转目标
`0xFFFF`，不会生成或覆盖 `0xFFFF` 节点。

主节点使用 `1..1000`。API 生成的延时节点必须使用与主节点不重叠的内部 ID，且主节点
和全部延时节点的总 ID 都必须保持在 `0x0001..0xFFFE`。

## 5. static 物理量

格式：

```json
{
  "0": {
    "0": [0, 50],
    "1": [81.64962003, 0]
  },
  "1": {
    "0": [0, 50],
    "1": [81.64962003, -120]
  }
}
```

结构：

```text
前端通道字符串
  -> layer 字符串
       -> [幅值, 频率或相位]
```

### 5.1 通道

状态序列实验使用前端通道 `"0"`～`"11"`。

API 调用 `physDictToReg()` 后，前端通道会映射为 FPGA 物理通道。`ApiNodeData` 内保存
映射后的物理通道号，不保存原始 JSON 字符串。

### 5.2 layer 0～64

| layer | 第一项 | 第二项 |
|---:|---|---|
| 0 | 直流/零层幅值 | 公共基频，Hz |
| 1 | 基波幅值 | 相位，° |
| 2 | 二次谐波幅值 | 相位，° |
| … | … | … |
| 64 | 64 次谐波幅值 | 相位，° |

必须满足：

- layer 范围为 `"0"`～`"64"`；
- 每个 layer 恰好包含两个有限数字；
- layer 0 不是基波；
- layer 1 才是基波；
- layer 1～64 的第二项都是角度值，单位为度。

### 5.3 幅值口径

前端发送的 layer 0～64 第一项全部是实际幅值/峰值，不是有效值。

后端绝对不能再乘：

```text
sqrt(2)
```

API 直接进行绝对量转换：

```python
base_reg = self.physDictToReg(front_node["static"])
```

它对应：

```python
ApiNodeData.base = base_reg
```

`static` 是绝对值，不能使用 `is_delta=True`。

## 6. mode

只支持两个大写枚举：

```text
STATIC
SWEEP
```

其他拼写、大小写或数值都属于参数错误。

### 6.1 STATIC

前端：

```json
{
  "id": 2,
  "mode": "STATIC",
  "static": {},
  "do_actions": [],
  "triggers": []
}
```

映射：

```python
ApiNodeData(
    mode=1,
    base=self.physDictToReg(node["static"]),
)
```

进入节点后：

1. static 绝对值生效；
2. 产生 `tick=0`；
3. 启动 do_actions；
4. 等待 manual、timeout 或 DI。

如果没有出口，节点持续保持，直到前端 stop 或其他有效软件终止。

为兼容旧前端，STATIC 节点即使携带未使用的 `dynamic` 或其他附加字段，后端也直接
忽略；只有 SWEEP 节点才会读取和校验 `dynamic`。

### 6.2 SWEEP

前端：

```json
{
  "id": 1,
  "mode": "SWEEP",
  "static": {},
  "dynamic": {
    "stepTime": 1,
    "count": 100,
    "steps": {}
  }
}
```

映射：

```python
step_reg = self.physDictToReg(
    node["dynamic"]["steps"],
    is_delta=True,
)

ApiNodeData(
    mode=2,
    base=self.physDictToReg(node["static"]),
    interval=node["dynamic"]["stepTime"],
    steps=[step_reg for _ in range(node["dynamic"]["count"])],
)
```

`ApiNodeData` 没有单独的 count 字段。`count` 通过 steps 列表长度表达。

### 6.3 dynamic 字段

| 字段 | 类型 | 约束 | 对应 ApiNodeData |
|---|---|---|---|
| `stepTime` | int | 正整数，单位 ms | `interval` |
| `count` | int | 正整数 | `len(steps)` |
| `steps` | object | 单步增量字典 | `steps[i]` |

`dynamic.steps` 与 static 使用相同的通道/layer结构，但数值是单次增量：

| layer | 第一项增量 | 第二项增量 |
|---:|---|---|
| 0 | 幅值增量 | 频率增量，Hz |
| 1～64 | 幅值增量 | 相位增量，° |

必须使用：

```python
is_delta=True
```

不能对增量应用绝对量校准偏置。

steps 可以稀疏，只列出发生变化的通道和 layer。但每个 steps 中出现的通道/layer，
必须同时在本节点 static 中提供绝对基准，否则 EngineCtrl 无法可靠维护累计物理状态。

### 6.4 SWEEP tick 和总时间

`count=N` 时，完整 tick 为：

```text
0, 1, 2, ... , N
```

含义：

```text
tick0 = static
tick1 = static + step
tick2 = static + 2 × step
...
tickN = static + N × step
```

引擎会让 base 和最后一步都保持完整 interval，因此未被提前触发时：

```text
COUNT_OVER 时间 = (count + 1) × stepTime
```

权威示例节点 1：

```text
count=100
stepTime=1 ms
```

所以 COUNT_OVER 在约 101 ms 后发生，不是 100 ms。

没有 COUNT_OVER 规则时，SWEEP 完成最后一步后继续保持最终值，等待其他触发。

## 7. do_actions

前端格式：

```json
[
  {"delay": 0, "mask": 0},
  {"delay": 2, "mask": 1},
  {"delay": 5, "mask": 3}
]
```

### 7.1 delay

`delay` 是相对当前节点 base 生效时刻的绝对延时，单位 ms。

它不是与上一条 DO 动作之间的间隔。

示例：

```text
节点 base 生效时刻 + 0 ms -> DO=0
节点 base 生效时刻 + 2 ms -> DO=1
节点 base 生效时刻 + 5 ms -> DO=3
```

约束：

```text
0 <= delay <= 65535
```

前端保证动作已经按 `delay` 非递减排列。API 必须保持数组原顺序打包，不重新排序。
后端仍需校验每项的类型和数值范围。

### 7.2 mask

`mask` 是完整的 8 位 DO 锁存值，不是需要翻转的 bit。

| mask | 含义 |
|---:|---|
| 0 | DO 全部关闭 |
| 1 | DO0 打开 |
| 3 | DO0、DO1 打开 |
| 255 | DO0～DO7 全部打开 |

约束：

```text
0 <= mask <= 255
```

### 7.3 ApiNodeData 编码

每项转换为：

```python
packed = (
    ((delay_ms & 0xFFFF) << 8)
    | (mask & 0xFF)
)
```

权威示例节点 1：

| 前端动作 | packed |
|---|---:|
| `{delay:0,mask:0}` | `0x000000` |
| `{delay:2,mask:1}` | `0x000201` |
| `{delay:5,mask:3}` | `0x000503` |

最终：

```python
ApiNodeData.doActions = [
    0x000000,
    0x000201,
    0x000503,
]
```

### 7.4 DO 保持和节点退出

- DO 是锁存值；
- 进入新节点不会自动清零；
- 若节点必须清零，应显式提供 `{delay:0,mask:0}`；
- FFFF 系统终止节点会安排 DO=0；
- 纯延时节点是源业务节点的中转阶段，源节点尚未完成的 DO 定时动作应继续运行；
- DO 动作的 delay 仍然相对源业务节点 tick0，不会因进入延时节点重新计时；
- 进入下一个带 `doActions` 的业务节点时，旧 task 才被取消，并启动新节点自己的
  do_actions。

当前 `USEEngine.coreLoop()` 的条件正好符合这个语义：

```python
if self._doTask and n.doActions:
```

纯延时节点通常 `doActions=None`，所以源节点 task 保留；后续业务节点带有
`doActions` 时再取消旧 task 并启动新 task。状态序列实验不修改这段 USEEngine 代码。

## 8. triggers

支持四个枚举：

```text
DI_MATCH
TIME_OUT
COUNT_OVER
MANUAL_TRIG
```

通用字段：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `condition` | enum string | 是 | 触发类型 |
| `next_id` | int | 是 | 目标前端 ID，或 `-2` |
| `post_condition_delay_ms` | int | 是 | 条件成立后的保持时间，0 表示不额外保持 |
| `timeout` | int | 条件相关 | 超时时间，单位 ms |
| `di_mask` | int | DI_MATCH 是 | DI 判据位域 |

### 8.1 next_id

合法值：

```text
已有前端节点 ID
-2
```

转换：

```text
next_id > 0 -> next_id
next_id = -2 -> 0xFFFF
```

其他负数、0 或不存在的 ID 都是参数错误。

API 主动终止使用 `EngineCtrl.stopTest()`；自动节点字段可以把最终目标设为
`0xFFFF`。任何情况下都不能 upsert 自定义 FFFF。

### 8.2 TIME_OUT

示例：

```json
{
  "condition": "TIME_OUT",
  "next_id": 3,
  "timeout": 500,
  "post_condition_delay_ms": 0
}
```

约束：

- `timeout` 必须是正整数；
- 单位 ms；
- 映射到 `ApiNodeData.timeoutMs`；
- `post_condition_delay_ms=0` 时直接使用真实目标；
- `post_condition_delay_ms>0` 时先使用内部延时节点。

主节点映射：

```python
api_node.timeoutMs = 500
api_node.timeoutId = compiled_route_target
```

### 8.3 COUNT_OVER

示例：

```json
{
  "condition": "COUNT_OVER",
  "next_id": 2,
  "post_condition_delay_ms": 200
}
```

只允许用于 SWEEP。

映射：

```python
api_node.countOverId = compiled_route_target
```

`COUNT_OVER` 是 `ApiNodeData` 和 `USEEngine` 原生支持的触发，不需要 API 自己计数、
定时或在回调中模拟。API 只需设置 `countOverId`。引擎在所有增量执行完成且最后一个
interval 保持结束后，自然返回该目标 ID。

### 8.4 MANUAL_TRIG

示例：

```json
{
  "condition": "MANUAL_TRIG",
  "next_id": 4,
  "post_condition_delay_ms": 0
}
```

MANUAL_TRIG 不写入 `ApiNodeData` 的自动触发字段。API 保存：

```text
当前 front node -> compiled_route_target
```

当前节点进入并收到前端 `next_node` 后，API 调用：

```python
self.ctrl.trigNode(compiled_route_target)
```

当 `next_id=-2` 且 `post_condition_delay_ms=0` 时，API 直接调用 `stopTest()`，不把
`0xFFFF` 当作可写节点。

manual trigger 只有一个待处理槽。发送下一次 `next_node` 前，前端必须等待目标业务节点
的 nodeId 更新。

### 8.5 DI_MATCH

示例：

```json
{
  "condition": "DI_MATCH",
  "next_id": -2,
  "di_mask": 263,
  "timeout": 8000,
  "post_condition_delay_ms": 200
}
```

主映射：

```python
api_node.diMatchMask = 263
api_node.diMatchId = compiled_route_target
```

如果 DI_MATCH 同时带正整数 timeout，它表示安全超时：

```python
api_node.timeoutMs = 8000
api_node.timeoutId = compiled_route_target
```

DI 命中与该规则的安全超时具有相同的 `next_id` 和
`post_condition_delay_ms`，因此二者使用同一个编译目标；延时大于 0 时也共用同一个
延时节点。API 根据运行期间收到的实际 DI 回报判断报告原因：

```text
已观察到 DI 判据满足 -> DI_MATCH
未观察到 DI 判据满足，安全 timeout 到期 -> TIME_OUT
```

不能把安全超时也报告成 DI_MATCH。

## 9. di_mask 位域

`di_mask` 不是单纯的 DI 通道位掩码。

| 位 | 值 | 含义 |
|---:|---:|---|
| 0～7 | `0x001..0x080` | 选择 DI0～DI7 |
| 8 | `0x100` | 1=全部选中位满足；0=任一选中位满足 |
| 9 | `0x200` | 1=参考本节点入口 DI；0=参考实验起始 DI |
| 10 | `0x400` | 反转参考比较，使条件由“不同”变为“相同” |

要求：

- 低 8 位不能为 0；
- 不允许使用 bit 10 以上的未知标志；
- `0x400` 是电平比较，不会记忆先动作后返回的历史。

引擎比较逻辑：

```python
watch = di_mask & 0xFF
reference = di_at_node_entry \
    if di_mask & 0x200 \
    else di_at_experiment_start

if di_mask & 0x400:
    reference ^= 0xFF

value = (di_now ^ reference) & watch

matched = (
    value == watch
    if di_mask & 0x100
    else value != 0
)
```

### 9.1 权威示例 263

```text
263 = 0x107
```

分解：

```text
0x007 -> 选择 DI0、DI1、DI2
0x100 -> AND，全部选中位必须满足
无 0x200 -> 参考实验起始 DI
无 0x400 -> 判断相对参考发生变化
```

所以节点 4 的含义是：

```text
DI0、DI1、DI2 必须全部相对实验起始状态发生变化。
```

### 9.2 实验起始 DI 的采样时刻

`di_mask` 只规定使用哪个参考值，本身不保存参考 DI。状态序列 API 使用 EngineCtrl
已经维护并回传给 API 的 `ctrl.di`：

```python
if not self.ctrl.upsertNodes(all_nodes):
    return

experiment_start_di = int(self.ctrl.di) & 0xFF

if not self.ctrl.trigNode(entry_id):
    return
```

也就是在节点编译、上传完成后，触发入口节点前保存一次实验启动 DI。每个业务节点
`tick=0` 时再保存该节点自己的入口 DI。

判据选择：

- `di_mask & 0x200 == 0`：参考 `experiment_start_di`；
- `di_mask & 0x200 != 0`：参考当前业务节点的入口 DI；
- `di_mask & 0x400 != 0`：对上述已选择的参考值取反。

因此状态序列 API 不需要越过 EngineCtrl 读取 USEEngine 的内部 `diStart`。

这里的 `ctrl.di` 是 EngineCtrl 缓存的最新完整 DI；start 时不会额外伪造一次 DI
回调。设备流程要求入口触发前 DI 已稳定。若 DI 恰好在“API 保存快照”和“硬件线程消费
入口触发”之间变化，引擎跳转仍按自己的实时 DI 正常执行，但最终报告对
DI_MATCH/safety timeout 的归因可能存在极窄差异。

## 10. 多 trigger 组合和优先级

`triggers` 是数组，但数组顺序不是运行优先级。

新 API 允许一个节点同时配置不同类型的触发，但应遵守：

- 每种 condition 最多一条；
- DI_MATCH 自带 timeout 时，不能再配置独立 TIME_OUT；
- COUNT_OVER 只允许出现在 SWEEP；
- 当前版本的 MANUAL_TRIG 必须是该节点唯一 trigger；
- 同一源节点内，不同 condition 不能解析到同一个编译目标；
- 所有 next_id 必须在编译前解析成功。

上述同目标限制用于保证报告原因可确定。USEEngine 只回报到达的节点 ID，不回报本次命中的
触发字段；如果同一源节点的 TIME_OUT、DI_MATCH 或 COUNT_OVER 同时指向相同真实目标
且使用相同 post delay，API 无法可靠区分它们，参数校验会拒绝该图。

DI_MATCH 自带的安全 timeout 不属于两条 trigger：它们本来就是同一个
`_TriggerSpec`，仍按实际 DI 状态在 `DI_MATCH` 与 `TIME_OUT` 之间判定，且共用同一个
编译目标。

当前引擎固定优先级：

```text
MANUAL_TRIG
  > TIME_OUT
  > DI_MATCH
  > COUNT_OVER
```

其中 MANUAL_TRIG 由 API 收到 `next_node` 后主动触发；COUNT_OVER 只在完整扫描结束且
没有更早触发时发生。

MANUAL_TRIG 暂不与自动触发组合，是为了避免前端命令与已经发生、但尚未回调到主线程的
自动跳转竞争同一个 manual target 槽。

## 11. post_condition_delay_ms

含义：

```text
条件已经成立
  -> 继续保持当前波形和 DO
  -> 等待 post_condition_delay_ms
  -> 进入 next_id
```

它不是触发检测前的延时。

`ApiNodeData` 没有 post_condition_delay_ms 字段。API 只在延时大于 0 时生成内部
延时节点；该节点只负责保持输出和计时，不负责标识触发原因。

### 11.1 delay = 0：直接跳转

`0 ms` 不生成内部节点：

```text
主节点 --条件成立--> 真实业务节点或 FFFF
```

各字段直接使用真实目标：

```python
api_node.timeoutId = real_target
api_node.diMatchId = real_target
api_node.countOverId = real_target
```

MANUAL_TRIG 的普通目标直接 `trigNode(real_target)`；终止目标由 API 调用
`stopTest()`。后端不能把 `timeoutMs=0` 当作立即超时，因为当前引擎中 0 表示不启用
该超时。

### 11.2 delay > 0：生成纯延时节点

```text
主节点 --条件成立--> 内部延时节点 --timeout--> 真实业务节点或 FFFF
```

普通目标的首选内部 ID 是：

```python
delay_node_id = 1000 + next_id
```

例如：

```json
{
  "condition": "TIME_OUT",
  "next_id": 3,
  "timeout": 500,
  "post_condition_delay_ms": 200
}
```

编译为：

```text
源节点 timeoutMs=500, timeoutId=1003

Node1003:
  mode=1
  base={}
  timeoutMs=200
  timeoutId=3
```

延时节点统一使用：

```python
ApiNodeData(
    mode=1,
    base={},
    timeoutMs=post_condition_delay_ms,
    timeoutId=real_target,
)
```

`base={}` 通过 SYS_SYNC 保持条件命中时的波形。延时节点不创建新的 DO task，源业务
节点尚未完成的 DO 定时动作继续按原 tick0 时间基准执行。

### 11.3 next_id=-2：真实目标始终是 0xFFFF

前端 `next_id=-2` 的唯一含义是停止：

```python
real_target = 0xFFFF
```

不得通过 `-2` 或 source ID 计算出另一个业务目标。

- `post_condition_delay_ms=0`：自动触发字段直接指向 `0xFFFF`；MANUAL_TRIG 调用
  `stopTest()`；
- `post_condition_delay_ms>0`：为了实现“条件成立后继续保持”的语义，触发先进入一个
  纯延时节点；该节点的 `timeoutId` 必须是 `0xFFFF`。

这里的延时节点 ID 只是 API 从空闲内部 ID 中分配的实现细节，与 `-2`、source ID
都没有数值映射关系。它不会改变最终目标是 `0xFFFF` 的协议含义。

同一节点的 DI_MATCH 与安全 TIME_OUT 使用相同延时和相同终点，所以共用这个纯延时
节点。

### 11.4 延时节点复用和冲突

同一内部延时节点只能表示同一组：

```text
(post_condition_delay_ms, real_target)
```

来自不同源节点的出口配置相同目标和相同延时时可以复用。同一源节点的不同 condition
若因此得到同一个编译目标，会因报告原因不可区分而在校验阶段被拒绝。若首选 ID 已被
不同延时配置占用，编译器从空闲内部 ID 中另行分配。分配前先保留所有主节点 ID和
普通目标的
`1000 + next_id` 首选 ID，再从 `1001..0xFFFE` 中选择未使用值。无论怎样分配，都
不能使用 0/FFFF、不能改变前端主节点 ID，也不能向前端发送延时节点 ID。

## 12. ApiNodeData 总映射

| 前端字段 | STATIC | SWEEP | ApiNodeData |
|---|---:|---:|---|
| `mode` | `STATIC` | `SWEEP` | 1 / 2 |
| `static` | 是 | 是 | `base`，绝对转换 |
| `dynamic.stepTime` | — | 是 | `interval` |
| `dynamic.count` | — | 是 | 生成 steps 列表长度 |
| `dynamic.steps` | — | 是 | `steps`，增量转换 |
| `do_actions` | 可选 | 可选 | `doActions` |
| `TIME_OUT.timeout` | 可选 | 可选 | `timeoutMs` |
| TIME_OUT 目标 | 可选 | 可选 | `timeoutId=编译目标` |
| `DI_MATCH.di_mask` | 可选 | 可选 | `diMatchMask` |
| DI_MATCH 目标 | 可选 | 可选 | `diMatchId=编译目标` |
| COUNT_OVER 目标 | 不允许 | 可选 | `countOverId=编译目标`，引擎原生触发 |
| MANUAL_TRIG | API 保存 | API 保存 | 不写自动触发字段 |
| `post_condition_delay_ms=0` | API 编译 | API 编译 | 直接使用真实目标 |
| `post_condition_delay_ms>0` | API 编译 | API 编译 | 内部延时节点 timeout |
| `next_id=-2` | API 编译 | API 编译 | 最终目标 FFFF |

状态序列不使用：

```text
mode=3
mode=4
reset
resetTime
resetDo
gate
```

## 13. 内部 nodes 生成

### 13.1 第一阶段：主节点

按 `nodes[]` 原顺序读取，每个主节点直接使用其前端 ID：

```python
engine_id = front_id
```

例如输入顺序：

```text
7, 20, 3
```

生成的主节点 ID 仍为 7、20、3，启动目标是数组第一项 7。trigger 的
`next_id=3` 就直接指向主节点 3。

### 13.2 第二阶段：按需生成延时节点

只遍历 `post_condition_delay_ms>0` 的 trigger：

- 普通目标优先使用 `1000 + next_id`；
- `next_id=-2` 先解析为真实目标 `0xFFFF`，延时节点 ID 从空闲内部 ID 分配；
- 来自不同源节点的相同延时和目标可复用；
- ID 冲突时安全分配其他空闲内部 ID；
- 延时为 0 时不生成任何内部节点；
- DI_MATCH 与它的安全 timeout 共用同一编译目标。

统一编译逻辑：

```python
real_target = (
    0xFFFF
    if trigger["next_id"] == -2
    else trigger["next_id"]
)

if trigger["post_condition_delay_ms"] == 0:
    compiled_route_target = real_target
else:
    compiled_route_target = get_or_create_delay_node(
        real_target=real_target,
        delay_ms=trigger["post_condition_delay_ms"],
    )
```

随后 TIME_OUT、DI_MATCH、COUNT_OVER 分别把 `compiled_route_target` 写入自己的
`ApiNodeData` 目标字段；MANUAL_TRIG 将它保存在 API 的手动路由表。

### 13.3 一次性上传

所有主节点和按需延时节点编译完成后，一次调用：

```python
if not self.ctrl.upsertNodes(all_nodes):
    return

if not self.ctrl.trigNode(params["nodes"][0]["id"]):
    return
```

不写固定 sleep。目标节点是否真正生效，以 `hw_ts>=0` 的 `onUpdate()` 为准。

## 14. 权威四节点示例的执行流程

### 14.1 前端业务图

```text
Node1 SWEEP
  -- COUNT_OVER
  -- 保持 200 ms
  -> Node2

Node2 STATIC
  -- TIME_OUT 500 ms
  -> Node3

Node3 STATIC
  -- MANUAL_TRIG
  -- 收到 next_node
  -> Node4

Node4 STATIC
  -- DI_MATCH 0x107
  -- 或安全 TIME_OUT 8000 ms
  -- 保持 200 ms
  -> -2 / FFFF
```

### 14.2 内部节点图

```text
主节点1 --COUNT_OVER--> 延时节点1002 --200ms--> 主节点2
主节点2 --TIME_OUT 500ms---------------------> 主节点3
主节点3 --next_node--------------------------> 主节点4
主节点4 --DI_MATCH------\
                         > stop_delay_id --200ms--> FFFF
主节点4 --安全TIME_OUT--/
```

样例只生成两个延时节点：

- `1002 = 1000 + 目标节点2`；
- `stop_delay_id` 是从空闲内部 ID 中分配的纯延时节点，`timeoutId=0xFFFF`。

节点4的 DI_MATCH 和安全 TIME_OUT 具有相同延时及终点，因此共用
`stop_delay_id`。延时节点 ID
不能发送给前端。

### 14.3 Node1 DO

```text
tick0/base 时刻 + 0 ms -> DO=0
tick0/base 时刻 + 2 ms -> DO=1
tick0/base 时刻 + 5 ms -> DO=3
```

Node2、Node3、Node4 都在各自进入时通过：

```json
{"delay": 0, "mask": 0}
```

把 DO 清零。

## 15. next_node 命令

完整消息：

```json
{
  "module": "universal_sequence",
  "cmd": "next_node"
}
```

处理条件：

1. 当前 active API 的 module 是 `universal_sequence`；
2. 实验仍处于 `isActive=True` 且 `ctrl.running=True`；
3. 当前业务节点存在 MANUAL_TRIG；
4. 当前没有尚未确认的 manual 跳转。

满足后：

```python
self.ctrl.trigNode(manual_route_target)
```

权威样例的 `post_condition_delay_ms=0`，所以 `manual_route_target=4`，收到命令后直接
触发主节点4，不生成中间节点。

不满足时：

- 不改变节点；
- 不触发默认 next；
- 通过 `sendError()` 返回明确错误。

前端必须等待目标业务节点的 nodeId 更新后，才能再次发送 `next_node`。

## 16. 回调与运行状态

### 16.1 onUpdate

只处理：

```text
hw_ts >= 0
```

业务主节点 `tick=0`：

1. `WAIT_ENTRY` 只接受 `nodes[0].id`；`POST_DELAY` 只接受当前延时节点声明的真实目标；
2. 第一个业务节点读取 USEEngine 在离开 Node0 时保存的实验起始 DI；
3. 若上一业务节点仍有未关闭 visit，说明它通过 0 延时路由直接到达这里；先按上一节点
   的已配置 trigger、manual pending 和实际 DI 回报结算该 visit；
4. 更新当前 front node；
5. 初始化本次访问的报告行；
6. 记录节点入口 DI；
7. 发送原始 front nodeId；
8. 清除 manual pending 状态。

SWEEP 的 `tick>0`：

- 不重复发送 nodeId；
- EngineCtrl 自动更新并发送 static。

延时节点 `tick=0`：

1. 按源业务节点的 trigger 状态结算当前 visit；
2. DI_MATCH 带安全 timeout 时，根据实际 DI 回报是否已满足判据写入
   `DI_MATCH` 或 `TIME_OUT`；
3. 进入 TRANSITION 状态并等待延时节点 timeout 自动推进；
4. 不发送延时节点 ID。

`hw_ts=-1`：

- 不作为节点生效确认；
- 不参与计时；
- 等待 EngineCtrl 随后终止并报告错误。

### 16.2 onDi

API 只负责报告所需的 DI 相对时间，不重复发送 DI value_update。

进入每个业务节点时保存：

```text
entry_hw_ts
entry_di
last_di
```

每次 DI 回调：

1. 计算 `changed = di ^ last_di`；
2. 对每个首次变化的 bit 记录时间；
3. 若当前节点有 DI_MATCH，按同一 `di_mask` 和参考 DI 计算判据；满足时记录
   `di_match_observed=True` 和该次硬件时间戳；
4. 更新时间使用 U32 回绕；
5. 更新 last_di。

报告区分 DI 命中与安全超时依赖这个实际 DI 状态，不依赖延时节点 ID。

### 16.3 onWebCommand

状态序列只处理：

```text
next_node
```

其他实验专属命令返回错误或忽略，但不能改变状态机。

### 16.4 _onStop

`_onStop()` 被调用时 `isActive` 已经是 False。

必须：

- 取消并释放后台 task；
- 释放所有 Event；
- 结算仍打开的业务节点；
- 使用状态序列专用 `onTerminal(hw_ts)` 从 EngineCtrl 接收的实际 FFFF 时间戳结算
  最后一个业务节点；
- 只发送一次最终全量报告；
- 不再触发任何节点。

## 17. value_update 职责

运行过程中可能出现四类更新：

```json
{"type": "value_update", "nodeId": 1}
```

```json
{"type": "value_update", "static": {"0": {"0": [10.0, 50.0]}}}
```

```json
{"type": "value_update", "di": 0}
```

```json
{"type": "value_update", "do": 3}
```

职责必须严格区分：

| 更新 | 发送方 |
|---|---|
| `nodeId` | 状态序列 API |
| `static` | EngineCtrl |
| `di` | EngineCtrl |
| `do` | EngineCtrl |

状态序列 API 绝对不能重复发送 static、di、do。

主节点 Engine ID 与前端 ID 恒等。`nodeId` 必须使用该主节点 ID，不能发送内部延时
节点 ID。

建议为 EngineCtrl 增加公共接口：

```python
sendNodeUpdate(front_node_id)
```

状态序列 API 不应直接依赖私有 `_send()`。

四类 value_update 是独立消息，不应依赖它们之间的固定到达顺序。

## 18. 最终报告

最终报告由状态序列 API 在 `_onStop()` 中通过 `sendReport()` 发送，不是 EngineCtrl
自动生成。

只发送一次全量 rows：

```json
{
  "type": "report",
  "module": "universal_sequence",
  "data": {
    "rows": [
      {
        "id": 1,
        "duration_sec": 0.101,
        "end_condition": "COUNT_OVER",
        "diA": null,
        "diB": null,
        "diC": null,
        "diR": null,
        "dia": null,
        "dib": null,
        "dic": null,
        "dir": null
      }
    ]
  }
}
```

### 18.1 row 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int | 前端业务节点 ID |
| `duration_sec` | number/null | 从业务节点 tick0 到下一主节点、延时节点或 FFFF 实际生效时间 |
| `end_condition` | string | 本次访问的结束原因 |
| `diA` | number/null | DI0 首次变化相对时间，秒 |
| `diB` | number/null | DI1 首次变化相对时间，秒 |
| `diC` | number/null | DI2 首次变化相对时间，秒 |
| `diR` | number/null | DI3 首次变化相对时间，秒 |
| `dia` | number/null | DI4 首次变化相对时间，秒 |
| `dib` | number/null | DI5 首次变化相对时间，秒 |
| `dic` | number/null | DI6 首次变化相对时间，秒 |
| `dir` | number/null | DI7 首次变化相对时间，秒 |

`post_condition_delay_ms>0` 从延时节点开始，因此不计入源业务节点 duration_sec。
`post_condition_delay_ms=0` 时没有延时节点：普通跳转由下一主节点 tick0 结算，直接
终止由实际 `nodeId=0xFFFF` update 的硬件时间戳结算。只有该时间戳无效（例如 `-1`）
时，duration_sec 才为 null。

节点图允许循环，所以同一个 id 可以在 rows 中出现多次，每次访问各占一行。

### 18.2 end_condition

自动或手动路由使用原始 trigger 枚举：

```text
DI_MATCH
TIME_OUT
COUNT_OVER
MANUAL_TRIG
```

额外终止原因：

```text
TERMINAL
ERROR
```

| 值 | 含义 |
|---|---|
| `TERMINAL` | 前端 stop 或没有可归属 trigger 的正常终止 |
| `ERROR` | 状态序列 API 自己捕获的运行异常 |

DI_MATCH 带安全 timeout 且两者共用编译目标时：

- API 在当前节点实际收到并确认 DI 判据满足，写 `DI_MATCH`；
- 未确认 DI 判据满足而由安全 timeout 推进，写 `TIME_OUT`。

共享延时节点本身不携带结束原因。

为结算最后节点，EngineCtrl 在收到 FFFF update 后检查当前 API 是否实现
`onTerminal(hw_ts)`。状态序列实现该可选钩子并只保存有效的 U32 时间戳，随后
EngineCtrl 仍调用原有无参 `BaseApi.onStop()`。其他 API 没有该钩子，停止流程不变。

EngineCtrl 或硬件层的错误仍由独立的 `error` 回复通知前端，不向 API 传递
`stopReason`。因此这类外部错误不保证在报告末行重复标成 `ERROR`。

### 18.3 时间计算

正常节点路由使用硬件时间戳：

```python
elapsed_us = (
    exit_hw_ts - entry_hw_ts
) & 0xFFFFFFFF
```

```python
duration_sec = elapsed_us / 1_000_000.0
```

直接进入 FFFF 时：

```python
duration_sec = (
    (ffff_hw_ts - last_business_entry_hw_ts)
    & 0xFFFFFFFF
) / 1_000_000.0
```

DI 回调时间戳已经减去防抖估算值，DI 相对时间同样必须使用 U32 回绕，并设置合理上限，
防止回绕附近形成接近 `2^32` 的伪时间。

自然终止、MANUAL_TRIG 直接终止、外部 stop 或错误停止，只要收到有效的 FFFF 硬件
时间戳，都应计算最后业务节点 duration。没有有效退出硬件时间戳时才保留 null，不得
伪造时间。

## 19. 参数校验规则

开始任何节点上传前，API 必须一次性校验完整请求。

### 19.1 顶层

协议外层必须满足：

- module 是 `universal_sequence`；
- cmd 是 `start`。

这两个字段由 EngineCtrl 用于路由；`BaseApi._onSetup()` 实际只收到 params。状态序列
API 在 `_onSetup()` 中校验：

- params 必须是 object；
- nodes 必须是非空 array。

### 19.2 ID 和图

- 每个 ID 必须是 `1..1000` 内的唯一整数；
- ID 不要求连续、递增或包含 1；
- `nodes[0].id` 是实验入口；
- 所有 next_id 必须是现有 ID 或 -2；
- 每个主节点和内部延时节点 ID 都必须位于 `1..0xFFFE`；
- 不允许写入 0/FFFF；
- 主节点 ID 保持原值，不重新编号。

### 19.3 物理量

- 通道 key 必须为 `"0"`～`"11"`；
- layer key 必须为 `"0"`～`"64"`；
- 每层必须恰好两个有限数字；
- 不执行 RMS 到峰值转换；
- SWEEP 的每个 step 通道/layer 必须在 static 中有绝对基准。

### 19.4 mode 和 dynamic

- mode 只能是 STATIC/SWEEP；
- STATIC 不读取 dynamic；即使旧前端携带该字段也直接忽略；
- SWEEP 必须有 dynamic；
- stepTime/count 必须是正整数；
- steps 必须是非空增量 object。

### 19.5 DO

- do_actions 必须是 array；
- delay 必须为 0..65535 整数；
- mask 必须为 0..255 整数；
- 前端保证 delay 非递减，API 保持原顺序打包，不排序。

### 19.6 trigger

- condition 必须是四个枚举之一；
- 每种 condition 每节点最多一个；
- next_id 必须可解析；
- post_condition_delay_ms 必须是非负整数；
- TIME_OUT.timeout 必须是正整数；
- DI_MATCH.di_mask 低8位必须非0且无未知高位；
- DI_MATCH.timeout 若提供必须是正整数；
- DI_MATCH 安全 timeout 与独立 TIME_OUT 不可同时存在；
- COUNT_OVER 只能用于 SWEEP。
- MANUAL_TRIG 必须是所在节点的唯一 trigger。
- 同一节点的不同 trigger 不能解析到同一个 compiled target。

### 19.7 旧版本附加字段兼容

状态序列 API 采用“只校验实际读取字段”的规则。以下对象可以携带旧版本字段、前端展示
字段或其他附加元数据，后端不读取的 key 会直接忽略：

- `params`；
- 每个 node；
- SWEEP 的 `dynamic`；
- 每个 trigger；
- 每个 `do_actions` 元素。

例如 node 中额外的名称、描述、界面坐标或旧版 `next_id`，以及 trigger 中与当前
condition 无关的旧字段，都不会导致实验失败。

后端实际读取的字段仍必须严格校验。`static`/`steps` 内的通道、layer 和物理值都是需要
转换并下发的业务数据，不属于可忽略元数据；错误通道、错误层号或错误数值仍会拒绝。

任何校验失败都必须：

1. 不上传任何实验节点；
2. 调用 `stopTest(reason)`；
3. 不尝试继续执行剩余节点。

## 20. 权威示例验收结果

使用 `StatSequApi.json` 启动后，应观察到：

1. EngineCtrl 先设置 10 ms DI 防抖并执行系统 Node0；
2. API 一次生成并上传主节点1～4、延时节点1002和一个指向FFFF的纯延时节点；
3. API 触发前端 Node1；
4. Node1 从 DO0 开始，在 2 ms、5 ms 更新为 1、3；
5. Node1 执行 base 加 100 次增量；
6. 约 101 ms 后 COUNT_OVER；
7. 继续保持 200 ms 后进入 Node2；
8. Node2 进入时 DO 清零，500 ms 后直接进入 Node3；
9. Node3 保持，等待完整 `next_node` 消息；
10. 收到 next_node 后直接进入 Node4；
11. Node4 等待 DI0～2 全部相对实验起始状态变化；
12. 若 DI 未满足，8000 ms 安全超时按 TIME_OUT 结束；
13. DI 或安全超时都进入同一个纯延时节点并继续保持200 ms，随后跳到FFFF；报告原因由实际 DI 回报判定；
14. 最后进入 FFFF，API 发送一次最终报告；
15. 前端只看到业务 nodeId 1、2、3、4，看不到内部延时节点。

以上行为构成新状态序列 API 的接口验收基线。
