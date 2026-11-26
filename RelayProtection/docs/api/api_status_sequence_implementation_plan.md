# 状态序列实验代码实现计划

> 目标模块：`universal_sequence`
>
> 协议依据：`docs/api/api_status_sequence.md`
>
> 通用实现依据：项目根目录《API实验代码开发指南.md》
>
> 权威输入样例：项目根目录 `StatSequApi.json`

## 1. 本轮目标

全新实现：

```text
RelayProtection/api/ApiStatSequ.py
```

不复用、复制或修补旧实现。

最终需要实现：

- 完整参数校验；
- 前端 ID 与内部 ID 映射；
- STATIC/SWEEP 主节点；
- DI/TIME_OUT/COUNT_OVER/MANUAL_TRIG；
- DI_MATCH 安全 timeout；
- 仅为正 `post_condition_delay_ms` 生成的内部延时节点；
- post_condition_delay_ms；
- next_node 命令；
- nodeId 更新；
- DI 相对时间记录；
- 一次性最终报告；
- 正常停止、前端停止和错误停止；
- 延时节点继承源业务节点 DO task 的既有语义。

## 2. 实施状态

已完成：

- 根据权威 JSON 重写状态序列 API 文档；
- 明确所有输入字段和枚举；
- 明确 `ApiNodeData` 映射；
- 明确主节点 ID 恒等映射和按需延时节点设计；
- 全新实现 `ApiStatSequ.py`；
- 恢复 `api/Registry.py` 注册；
- 增加 nodeId 接口和状态序列专用 FFFF 硬件时间戳钩子，并使用 EngineCtrl.di 保存
  实验启动 DI；
- 兼容旧前端附加字段，只校验后端实际读取的字段；
- 完成 FakeCtrl 边界验证和真实 USEEngine + EngineCtrl 端到端验证；
- 确认纯延时节点保留源业务节点的 DO task；
- 明确旧实现和旧测试不能作为新逻辑来源。

`universal_sequence` 当前可由 `BaseApi.create()` 发现，也已在 Registry 中显式注册。

## 3. 实现涉及的文件

### 3.1 新建

```text
RelayProtection/api/ApiStatSequ.py
```

不要求保留 `RelayProtection/tests/test_api_status_sequence.py`。实现期间可以使用临时调试
脚本或临时测试，验收完成后删除，不把它作为最终交付文件。

### 3.2 修改

```text
RelayProtection/api/Registry.py
RelayProtection/logic/EngineCtrl.py
```

修改目的：

| 文件 | 修改 |
|---|---|
| `Registry.py` | 新实现完成后重新 import/register `ApiStatSequ` |
| `EngineCtrl.py` | 增加 `sendNodeUpdate()`；收到 FFFF 时仅向实现了 `onTerminal()` 的 API 传递时间戳 |

`BaseApi.py` 保持原有无参 `onStop()` 生命周期接口，不保存状态序列专用数据。

### 3.3 不作为实现依据

```text
旧 ApiStatSequ.py
旧状态序列测试断言
其他历史实现文档中的代码映射
```

旧测试可以在新协议稳定后删除或重写，但不能反向约束新设计。

## 4. 新 API 类结构

建议保持一个类：

```python
class ApiStatSequ(BaseApi):
    MODULE_KEYS = ["universal_sequence"]
```

内部逻辑拆成小函数，不在 `_onSetup()` 中堆积所有处理：

```text
_parse_and_validate()
_validate_physical_map()
_validate_trigger_set()
_build_front_id_map()
_get_or_allocate_delay_node()
_compile_main_node()
_compile_delay_node()
_resolve_target()
_pack_do_actions()
_finish_current_visit()
_infer_end_condition()
_build_report()
```

## 5. 建议的内部数据对象

可在 `ApiStatSequ.py` 内定义只服务本 API 的 dataclass。

### 5.1 FrontNodeSpec

```text
front_id
engine_id
mode
static_phys
dynamic
do_actions
triggers
manual_route_target
```

### 5.2 RouteEdgeSpec

```text
source_front_id
condition
post_delay_ms
real_target_engine_id
compiled_target_engine_id
is_terminal
has_safety_timeout
```

按来源保存：

```text
routes_by_source[source_front_id]
```

报告判断使用“当前源节点 + 实际到达的编译目标”查找边，不能只从延时节点 ID 推断原因。

### 5.3 DelayNodeSpec

```text
delay_node_id
post_delay_ms
target_engine_id
is_terminal
```

延时节点只描述“保持多久、然后去哪里”，不作为结束原因标记。DI_MATCH 与其安全
timeout 具有相同延时和目标时共用同一个 DelayNodeSpec。

### 5.4 VisitRecord

```text
front_id
entry_hw_ts
entry_host_ts
entry_di
last_di
di_first_change[8]
di_match_observed
pending_manual
```

同一个 front ID 每次重新进入都创建新 VisitRecord，允许报告中出现重复 ID。

## 6. 必要运行状态

`__init__()` 初始化所有回调字段：

```text
_main_by_engine
_engine_by_front
_delay_by_engine
_routes_by_source
_manual_target_by_front
_all_nodes
_entry_engine_id
_current_front_id
_current_engine_id
_current_visit
_rows
_phase
_manual_pending
_pending_end_condition
_stopping
```

建议 phase：

```text
CREATED
ACTIVE_MAIN
POST_DELAY
STOPPING
STOPPED
```

回调开始时统一检查：

```python
if (
    not self.isActive
    or not self.ctrl.running
    or self._stopping
):
    return
```

`_onStop()` 不能使用该检查，因为调用前 `isActive` 已经变成 False。

## 7. 编译流程

`_onSetup(params)` 保持同步，无固定 sleep，也不创建后台控制 task。

### 7.1 第一步：完整解析

先解析为内部 spec，不调用 EngineCtrl。

校验：

- params/nodes 类型；
- 每个 ID 是 `1..1000` 内的唯一整数；
- `nodes[0].id` 作为入口；
- mode；
- static/dynamic；
- 通道/layer/数值；
- do_actions；
- triggers；
- next_id；
- trigger 组合；
- 所有主节点和预计延时节点 ID 均避开 0/FFFF。

任何字段失败：

```python
self.ctrl.stopTest(reason)
return
```

此时不得已经上传部分节点。

### 7.2 第二步：主 ID 映射

```python
engine_by_front = {
    front_id: front_id
    for front_id in parsed_front_ids
}
```

前端 ID 不要求从 1 开始、连续或按数值递增。主节点恒等映射；字典只用于：

- target 解析；
- 重复和不存在引用校验；
- 主节点与内部延时节点区分。

入口必须单独保存：

```python
entry_engine_id = params["nodes"][0]["id"]
```

### 7.3 第三步：预分配正延时节点

只处理：

```text
post_condition_delay_ms > 0
```

首选 ID：

```text
普通目标: 1000 + next_id
```

同一 `(post_delay_ms, real_target)` 可以复用延时节点。DI_MATCH 与其安全 timeout
必须共用同一个编译目标。

来自不同源节点的相同延时和目标可以复用；同一源节点的不同 condition 不允许得到同一
compiled target，否则 USEEngine 只回报目标节点、API 无法可靠生成 end_condition。
DI_MATCH 自带的安全 timeout 仍是一条 trigger 内部的两个硬件出口，不受该限制。

`next_id=-2` 必须先解析为 `real_target=0xFFFF`，不能由 `-2` 或 source ID
计算出其他业务节点。若延时大于0，延时节点 ID 从
`1001..0xFFFE` 的空闲内部 ID 中确定性分配，其 `timeoutId=0xFFFF`。

分配采用两遍：

1. 先保留主节点 ID 和所有普通目标的 `1000+next_id` 首选 ID；
2. 终止延时节点及首选 ID 冲突项再从剩余空闲 ID 中分配。

任何内部节点都不能使用0或FFFF。

`post_condition_delay_ms=0` 不分配任何延时节点。

每条 route 统一解析：

```python
real_target = (
    0xFFFF
    if trigger.next_id == -2
    else trigger.next_id
)

compiled_target = (
    real_target
    if trigger.post_delay_ms == 0
    else get_or_allocate_delay_node(
        real_target,
        trigger.post_delay_ms,
    )
)
```

### 7.4 第四步：编译主节点

STATIC：

```python
ApiNodeData(
    mode=1,
    base=self.physDictToReg(static_phys),
    doActions=packed_do_actions or None,
)
```

SWEEP：

```python
step_reg = self.physDictToReg(
    dynamic["steps"],
    is_delta=True,
)

ApiNodeData(
    mode=2,
    base=self.physDictToReg(static_phys),
    interval=dynamic["stepTime"],
    steps=[
        step_reg
        for _ in range(dynamic["count"])
    ],
    doActions=packed_do_actions or None,
)
```

再连接：

```text
timeoutMs/timeoutId
diMatchMask/diMatchId
countOverId
manual route metadata
```

`_pack_do_actions()` 按前端数组原顺序逐项打包，不调用 `sort()` 或 `sorted()`；相同
delay 的动作也不能重排。前端负责保证 delay 顺序，API 只检查字段类型和数值范围。

### 7.5 第五步：编译延时节点

共同字段：

```python
delay_node = ApiNodeData(
    mode=1,
    base={},
    timeoutMs=spec.post_delay_ms,
    timeoutId=spec.target_engine_id,
)
```

终止目标：

```python
delay_node.timeoutId = 0xFFFF
```

这里不会出现 0 ms 延时节点。普通 0 ms 路由直接把真实目标写入主节点字段；
MANUAL_TRIG 的 0 ms 终止由 API 调用 `stopTest()`。

### 7.6 第六步：一次上传和启动

```python
if not self.ctrl.upsertNodes(all_nodes):
    return

if not self.ctrl.trigNode(
    self._entry_engine_id
):
    return
```

不增加 50 ms 魔法延时。

`nodes[0]` 对应主节点的有效 tick0 才表示实验业务开始。

## 8. Trigger 编译

### 8.1 TIME_OUT

```text
timeoutMs = trigger.timeout
timeoutId = compiled_route_target
```

`compiled_route_target` 在 delay=0 时是真实目标，在 delay>0 时是延时节点。

### 8.2 DI_MATCH

```text
diMatchMask = trigger.di_mask
diMatchId = compiled_route_target
```

若带 timeout：

```text
timeoutMs = trigger.timeout
timeoutId = compiled_route_target
```

DI 和安全 timeout 共用目标。报告原因由 API 在当前业务节点收到的实际 DI 回报是否满足
判据决定。

### 8.3 COUNT_OVER

```text
countOverId = compiled_route_target
```

只允许 SWEEP。`ApiNodeData.countOverId` 和 `USEEngine.runSweep()` 原生实现
COUNT_OVER；API 不创建软件计数器，也不根据 tick 主动触发。

### 8.4 MANUAL_TRIG

不写入 ApiNodeData：

```text
manual_target_by_front[front_id] = compiled_route_target
```

delay=0 的普通目标就是原始 `next_id`；权威样例节点3保存目标4，不生成中间节点。
delay=0 且 `next_id=-2` 时保存终止标记，由 `next_node` 调用 `stopTest()`。

第一版要求 MANUAL_TRIG 是所在节点唯一 trigger，避免前端命令与在途自动跳转竞争单槽
manual target。

### 8.5 固定触发优先级

API 文档和测试都按：

```text
manual > timeout > DI > countOver
```

不得根据 triggers 数组顺序推断运行优先级。

## 9. 权威样例编译结果

### 9.1 主节点

| engine ID | front ID | mode |
|---:|---:|---|
| 1 | 1 | SWEEP |
| 2 | 2 | STATIC |
| 3 | 3 | STATIC |
| 4 | 4 | STATIC |

### 9.2 延时节点

| internal ID | 来源/用途 | delay | target |
|---:|---|---:|---:|
| 1002 | 指向业务节点2的正延时路由 | 200 ms | 2 |
| `stop_delay_id` | 节点4指向停止的纯延时路由；ID由空闲分配器决定 | 200 ms | FFFF |

节点4的 DI_MATCH 与安全 TIME_OUT 共用 `stop_delay_id`。节点2的 0 ms TIME_OUT 和节点3的
0 ms MANUAL_TRIG 直接指向真实业务节点，不生成延时节点。

### 9.3 关键 ApiNodeData

```text
Node1:
  mode=2
  interval=1
  len(steps)=100
  doActions=[
    0x000000,
    0x000201,
    0x000503
  ]
  countOverId=1002

Node1002:
  mode=1
  base={}
  timeoutMs=200
  timeoutId=2

Node2:
  mode=1
  timeoutMs=500
  timeoutId=3

Node3:
  mode=1
  next_node -> API trigNode(4)

Node4:
  mode=1
  diMatchMask=0x107
  diMatchId=stop_delay_id
  timeoutMs=8000
  timeoutId=stop_delay_id

Node(stop_delay_id):
  mode=1
  base={}
  timeoutMs=200
  timeoutId=0xFFFF
```

## 10. onUpdate 控制逻辑

签名：

```python
def onUpdate(
    self,
    nodeId: int,
    tick: int,
    hw_ts: int,
):
```

入口：

```python
if (
    not self.isActive
    or not self.ctrl.running
    or self._stopping
    or hw_ts < 0
):
    return
```

### 10.1 可见主节点 tick0

当：

```text
nodeId in _main_by_engine
tick == 0
```

执行：

1. WAIT_ENTRY 只接受入口 ID，POST_DELAY 只接受延时节点声明的真实目标；
2. nodes 上传成功、触发入口前保存一次 `ctrl.di` 作为实验启动 DI；首节点 tick0
   使用该值评估未设置 `0x200` 的 DI_MATCH；
3. 若存在未关闭 visit，说明上一业务节点通过 0 ms 路由直接到达这里；根据上一节点
   route metadata、manual pending 和实际 DI 回报推断结束原因，并用当前 `hw_ts` 结算；
4. 设置 current engine/front ID；
5. phase=ACTIVE_MAIN；
6. manual_pending=False；
7. 新 VisitRecord 的 `di_match_observed=False`；
8. 记录 entry_hw_ts；
9. 记录 entry_host_ts；
10. 记录 entry_di/last_di；
11. 创建 VisitRecord；
12. 调用 `ctrl.sendNodeUpdate(front_id)`。

EngineCtrl 已在 API 回调前发送本节点 static，API 不重复发送。

### 10.2 SWEEP 正 tick

```text
tick > 0
```

API 不发送 nodeId，不修改业务 visit 入口。

EngineCtrl 自动累计并发送 static。

### 10.3 正延时节点 tick0

当：

```text
nodeId in _delay_by_engine
tick == 0
```

执行：

1. 验证该延时节点可由 current_front_id 的已配置 route 到达；
2. 根据源节点 trigger、manual pending 和实际 DI 回报确定 end_condition；
3. 使用延时节点 `hw_ts` 关闭当前 visit；
4. phase=POST_DELAY；
5. 清除 manual_pending；
6. 不发送延时节点 nodeId；
7. 等待延时节点自身 timeout 自动进入真实目标或 FFFF。

延时节点 ID 不直接表示触发原因。特别是节点4的 DI_MATCH 与安全 TIME_OUT 都会进入
同一个 `stop_delay_id`；`_infer_end_condition()` 按
`current_visit.di_match_observed` 选择：

```text
True  -> DI_MATCH
False -> TIME_OUT
```

### 10.4 0 ms 路由

自动条件直接把真实目标写入主节点：

```text
TIME_OUT   -> timeoutId=real_target
DI_MATCH   -> diMatchId=real_target
COUNT_OVER -> countOverId=real_target
```

因此不会收到中间节点回调。普通业务目标在下一主节点 `tick=0` 时结算上一 visit。
自动终止目标可以直接写 `0xFFFF`；MANUAL_TRIG 的 0 ms 终止由 API 执行：

```python
self._stopping = True
self.ctrl.stopTest()
```

一次 manual trigger 后不能再发送另一个 trigger，必须等待目标主节点 tick0。

## 11. onDi 报告逻辑

EngineCtrl 已经发送：

```json
{"type":"value_update","di":0}
```

API 不重复发送。

只在：

```text
phase=ACTIVE_MAIN
current_visit 存在
```

时记录。

算法：

```python
changed = (
    di ^ current_visit.last_di
) & 0xFF

for bit in range(8):
    if (
        changed & (1 << bit)
        and first_change[bit] is None
    ):
        elapsed_us = (
            hw_ts - entry_hw_ts
        ) & 0xFFFFFFFF

        if elapsed_us <= max_visit_us:
            first_change[bit] = (
                elapsed_us / 1_000_000.0
            )

current_visit.last_di = di
```

`max_visit_us` 根据节点 timeout、扫描最大时间和允许的运行上限计算，防止防抖回溯形成
接近 `2^32` 的错误时长。

若当前主节点存在 DI_MATCH，还必须使用与 USEEngine 相同的 `di_mask` 位域和参考 DI
计算匹配状态。实际回报一旦满足判据：

```python
self._current_visit.di_match_observed = True
```

该标记用于在 DI_MATCH 与其安全 TIME_OUT 共用目标时生成 `end_condition`，不依赖
延时节点 ID。

## 12. next_node 控制逻辑

```python
def onWebCommand(self, msg):
```

先验证：

- `module == "universal_sequence"`；
- `cmd == "next_node"`；
- phase=ACTIVE_MAIN；
- 当前节点有 manual route；
- manual_pending=False；
- `isActive` 且 `ctrl.running`。

成功：

```python
self._manual_pending = True

if compiled_manual_target == 0xFFFF:
    self._pending_end_condition = "MANUAL_TRIG"
    self._stopping = True
    self.ctrl.stopTest()
elif not self.ctrl.trigNode(compiled_manual_target):
    self._manual_pending = False
    return
```

这里必须判断编译后的目标。`next_id=-2` 但 `post_condition_delay_ms>0` 时，编译目标是
内部延时节点，应先 `trigNode(delay_node_id)`；只有0延时终止的编译目标才直接是
`0xFFFF`。

错误：

```python
self.ctrl.sendError(
    "Current node does not accept next_node"
)
```

不自动选择当前 ID+1，不使用节点数组顺序作为隐式出口。

## 13. nodeId 更新接口

在 EngineCtrl 增加：

```python
def sendNodeUpdate(
    self,
    nodeId: int,
):
    self._send({
        "type": "value_update",
        "nodeId": nodeId,
    })
```

状态序列 API 只在可见主节点 tick0 调用：

```python
self.ctrl.sendNodeUpdate(front_id)
```

禁止：

- 调用私有 `ctrl._send()`；
- 发送内部延时节点 ID；
- 重复发送 static/di/do。

## 14. DO task 中转语义：不修改 USEEngine

保留当前条件：

```python
if self._doTask and n.doActions:
    self._doTask.cancel()
    self._doTask = None
```

它实现的正是状态序列需要的语义：

- 进入 `doActions=None` 的纯延时节点时，不取消源业务节点 DO task；
- 源节点尚未到时的动作在中转延时内继续按源节点 tick0 执行；
- 进入带有 do_actions 的下一业务节点时，取消旧 task，再启动新节点 task；
- FFFF 最终归零。

不修改 `USEEngine.py`。临时调试只需确认：

```text
源节点有 +150 ms DO 动作
+100 ms 时进入一个 +200 ms 纯延时节点
+150 ms 时源节点 DO 动作仍正常执行
+300 ms 时进入带 do_actions 的下一业务节点并由新 task 接管
```

## 15. 报告生成

### 15.1 正常 route

`post_condition_delay_ms>0` 时，在延时节点 tick0 关闭当前 visit：

```python
duration_sec = (
    (delay_node_hw_ts - entry_hw_ts)
    & 0xFFFFFFFF
) / 1_000_000.0
```

`post_condition_delay_ms` 不计入源节点 duration。

`post_condition_delay_ms=0` 且目标是普通主节点时，在目标主节点 tick0 用同一公式关闭
上一 visit。结束原因由源节点配置及运行标记推断：

- manual pending -> `MANUAL_TRIG`；
- DI_MATCH 含安全 timeout：`di_match_observed` 为真写 `DI_MATCH`，否则
  `TIME_OUT`；
- 只有一个自动出口时直接使用该 condition；
- COUNT_OVER 由 `countOverId` 原生出口识别。

延时节点不携带 condition，也不作为报告行。

### 15.2 外部 stop

若 `_onStop()` 时仍有未关闭 visit：

```text
有 pending end condition -> 使用该 condition
无可归属条件 -> TERMINAL
有效 FFFF hw_ts -> 用 FFFF hw_ts - entry_hw_ts 结算 duration_sec
无可靠 FFFF hw_ts -> duration_sec=null
```

不伪造精确硬件退出时间。

### 15.3 错误

参数错误发生在主节点启动前：

```text
rows=[]
```

运行中异常：

```text
当前 visit.end_condition=ERROR
有效 FFFF hw_ts 时按实际时间结算，否则 duration_sec=null
```

ApiStatSequ 自己实现可选钩子：

```python
def onTerminal(self, hw_ts):
    self._terminal_hw_ts = valid_u32_or_none(hw_ts)
```

EngineCtrl 收到 FFFF update 后先调用该钩子，再走原有无参 `BaseApi.onStop()`。
其他 API 不实现钩子，行为不变。状态序列自身捕获的运行异常在
`_stop_with_error()` 内保存 `ERROR`；EngineCtrl/硬件错误由独立的前端 `error`
回复表达，不向 API 传递 stop reason。

### 15.4 最终发送

只在 `_onStop()`：

```python
self.ctrl.sendReport({
    "rows": self._rows,
})
```

不发送分段 rows，避免前端重复合并。

## 16. 停止和异常

### 16.1 参数错误

```python
self.ctrl.stopTest(
    f"Invalid universal_sequence: {reason}"
)
```

### 16.2 编译失败

`upsertNodes()` 返回 False 后立即 return，不触发入口。

### 16.3 trigger 失败

`trigNode()` 返回 False 后立即 return，不继续推进。

### 16.4 用户 stop

EngineCtrl 处理公共 stop，API 不在 `onWebCommand()` 重复处理。

### 16.5 _onStop

```text
_stopping=True
phase=STOPPED
关闭未完成 visit
清除当前节点和 pending
发送一次报告
```

`_onStop()` 不能再次 trigger/stop。

## 17. 临时调试与验收清单

不要求新建或保留 `RelayProtection/tests/test_api_status_sequence.py`。下面的断言可以通过
临时脚本、交互调试或端到端运行完成；若实现期间创建临时测试文件，验收后删除。

### 17.1 权威样例编译

断言：

- 4 个主节点；
- 2 个正延时节点；
- upsert 总计 6 个节点；
- 主节点 IDs 1、2、3、4；
- 普通延时节点 ID 为1002；
- 终止延时节点使用空闲内部 ID，且其 timeoutId 为FFFF；
- Node1 mode/interval/100 steps；
- DO packed 值；
- Node4 diMask/timeout；
- Node4 DI 与安全 timeout 共用同一个终止延时节点；
- Node2 和 Node3 之间没有 0 ms 中间节点。

### 17.2 物理量

- layer 0/1/64；
- 峰值不乘 sqrt(2)；
- static 绝对转换；
- steps 增量转换；
- sparse step 缺 base 被拒绝；
- 非有限数被拒绝。

### 17.3 ID

- ID 重复；
- ID=0、1001、负数被拒绝；
- 非连续 ID 被接受；
- 不包含 1 被接受；
- `nodes[0]` 被用作入口；
- 主节点 engine ID 与前端 ID 相同；
- next_id 不存在；
- next_id=0；
- next_id=-2；
- 前端存在主节点998时，`next_id=-2` 的正延时绝不能生成节点998；
- 总内部节点超过 0xFFFE；
- 确认 upsert 中没有 0/FFFF。

### 17.4 mode/dynamic

- STATIC；
- SWEEP；
- 小写 mode 被拒绝；
- STATIC 携带 dynamic 或其他旧版附加字段时忽略未读取内容；
- SWEEP 缺 dynamic；
- stepTime=0；
- count=0；
- COUNT_OVER 用于 STATIC 被拒绝。

### 17.5 do_actions

- 0/1/3/255；
- delay 0/65535；
- 越界；
- 保持前端数组原顺序，不调用 sort；
- 相同 delay 的动作不重排；
- do_actions 元素的附加字段被忽略；
- 空动作不改 DO；
- 进入纯延时节点时源节点 DO task 继续运行；
- 进入带 do_actions 的下一业务节点时新 task 接管。

### 17.6 trigger

- 四个 condition；
- node/dynamic/trigger 中后端未读取的附加字段被忽略；
- 重复同类型；
- trigger 数组顺序不改变固定优先级；
- DI_MATCH 低8位为0；
- di_mask 未知高位；
- DI_MATCH safety timeout；
- safety timeout 与独立 TIME_OUT 冲突；
- MANUAL_TRIG 与自动 trigger 冲突；
- COUNT_OVER 完整 N+1 interval。

### 17.7 post delay

- delay=0 不生成内部节点，直接使用真实目标；
- delay>0 延时节点 timeout；
- 普通目标首选 ID 为 `1000+next_id`；
- `next_id=-2` 始终解析为真实目标FFFF；
- `next_id=-2` 且 delay=0 时直接停止；
- `next_id=-2` 且 delay>0 时使用空闲内部延时节点，节点 timeoutId=FFFF；
- MANUAL_TRIG 的 `next_id=-2, delay>0` 必须先触发延时节点，不能提前 stop；
- 终止延时节点 ID 不由 `-2` 或 source ID 计算；
- 同一 `(delay, FFFF)` 可以复用；
- 分配器跳过主节点、普通目标首选ID、0和FFFF；
- 普通目标；
- terminal 目标；
- DI_MATCH 与安全 TIME_OUT 共用延时节点；
- 实际 DI 回报区分 `DI_MATCH`/`TIME_OUT`；
- 延时节点 ID 不发送前端。

### 17.8 next_node

- 正确完整命令；
- module 错误；
- 非 manual 节点；
- 重复命令；
- 停止过程中命令；
- 等到目标 nodeId 后允许下一次。

### 17.9 value_update

- API 只发送 nodeId；
- nodeId 为 front ID；
- sweep tick>0 不重复 nodeId；
- 延时节点不发送 nodeId；
- static/di/do 没有被 API 重复发送。

### 17.10 report

- 正常四种 end_condition；
- DI safety timeout 为 TIME_OUT；
- DI0..7 字段名；
- U32 回绕；
- 防抖回溯伪时长过滤；
- 循环访问同 ID 生成多行；
- 外部 stop 为 TERMINAL；
- 运行错误为 ERROR；
- 直接 FFFF 的最后一行使用 FFFF hw_ts 计算 duration；
- FFFF hw_ts=-1 时最后一行 duration 为 null；
- FFFF 时间戳 U32 回绕；
- `_onStop()` 只发一次全量 rows。

### 17.11 权威流程 E2E

使用 `StatSequApi.json` 验证：

```text
1 SWEEP
  -> COUNT_OVER
  -> 200 ms
2 STATIC
  -> 500 ms TIME_OUT
3 STATIC
  -> next_node
4 STATIC
  -> DI0..2 全部变化
     或 8000 ms TIME_OUT
  -> 200 ms
FFFF
```

最终断言：

- 前端 nodeId 只有 1、2、3、4；
- DO 顺序正确；
- 报告 rows 原因正确；
- 无内部 ID；
- 无重复 static/di/do；
- FFFF 没有被 upsert。

## 18. 实际实施顺序

本轮按以下顺序完成：

1. 增加 EngineCtrl.sendNodeUpdate 及单元测试；
2. 让 EngineCtrl 在 API 停止前通过状态序列可选钩子提供 FFFF 硬件时间戳；
3. 新建 ApiStatSequ 的纯解析和校验层；
4. 实现主节点恒等 ID、按需延时节点分配和纯节点编译；
5. 实现 onUpdate 主节点/延时节点状态机；
6. 实现 next_node；
7. 实现 onDi 与报告；
8. 恢复 Registry 注册；
9. 使用临时调试脚本完成验收，调试文件随后删除；
10. 使用权威 JSON 做端到端验收；
11. 删除或重写仍依赖旧行为的状态序列测试。

## 19. 完成定义

只有同时满足以下条件才算完成：

- 权威 JSON 可启动；
- 所有字段校验符合 API 文档；
- 主节点和按需延时节点一次性编译；
- 入口始终为 `nodes[0].id`；
- 非连续的 `1..1000` 前端 ID 保持不变；
- 无 0/FFFF 覆盖；
- STATIC/SWEEP 正确；
- count 语义为 N 次增量；
- 四种触发正确；
- COUNT_OVER 使用 `ApiNodeData.countOverId` 原生执行；
- MANUAL_TRIG 无竞态；
- 0 ms 直接路由、正 post delay 节点正确；
- DI_MATCH 与安全 timeout 共用目标并由实际 DI 回报区分报告原因；
- 源节点 DO task 在纯延时节点中继续运行，并在下一业务节点 do_actions 启动时被接管；
- nodeId 只由 API 发送且只包含前端 ID；
- static/di/do 只由 EngineCtrl 发送；
- 最终报告只发送一次；
- 临时调试与端到端验收通过，未遗留 `test_api_status_sequence.py`；
- Registry 无悬空 import；
- 旧状态序列逻辑不再被引用。
