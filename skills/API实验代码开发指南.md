# API 实验代码开发指南

> 整理日期：2026-07-25
>
> 适用目录：`RelayProtection/api/`
>
> 目标：只说明如何设计实验 API、如何使用 `EngineCtrl`、如何正确构造
> `ApiNodeData`，使开发者可据此独立完成新的实验 API。
>
> 口径：以当前 `BaseApi.py`、`EngineCtrl.py`、`USEEngine.py` 和 `ApiScanTest.py`
> 实际实现为准。
>
> 路径说明：本文代码路径均相对 `RelayProtection/`。

## 1. API 开发的最小心智模型

实验 API 位于“前端物理参数”和“通用波形执行引擎”之间：

```text
前端 start JSON
  -> BaseApi.create(module)
  -> 具体 API._onSetup(params)
  -> 实验数学计算
  -> physDictToReg()
  -> ApiNodeData 节点图
  -> EngineCtrl.upsertNodes()
  -> EngineCtrl 编译为 USENode/FPGA 帧
  -> USEEngine 执行
  -> onUpdate()/onDi()
  -> 实验结果
  -> sendReport()
```

API 应负责：

- 定义前端参数；
- 把参数转换为实验所需的物理量；
- 构造节点图和跳转条件；
- 根据 node、tick、DI 和硬件时间戳维护实验状态；
- 生成前端报告。

API 不应负责：

- 自己拼 FPGA 字节帧；
- 直接操作 UART；
- 直接控制功放；
- 自己等待 FPGA ACK；
- 绕过 `EngineCtrl` 建立另一套原始发送流程。

API 开发真正需要掌握的核心只有：

```text
BaseApi 生命周期
EngineCtrl 接口
ApiNodeData
节点 mode
DI/DO/timeout/countOver
onUpdate/onDi 回调
```

推荐按以下顺序实现一个新实验：

1. 定义前端 `module`、`params` 类型、默认值、单位和幅值口径；
2. 列出需要输出的通道、layer，以及每个阶段的绝对物理量；
3. 画节点图，为所有普通节点分配 `1..0xFFFE` 的唯一 ID；
4. 为每个节点选择 Mode1/2/3，并明确 DI、timeout、countOver、manual 出口；
5. 用 `physDictToReg()` 分别转换绝对 base/reset 和增量 steps；
6. 一次构造节点，检查 `upsertNodes()`，再触发入口节点；
7. 用有效 `onUpdate()` 驱动业务 phase，用 `onDi()` 记录实验事件；
8. 在 `_onStop()` 单点清理 task 并生成约定好的最终报告。

## 2. 新建和注册一个 API

### 2.1 文件和类

新 API 文件放在：

```text
RelayProtection/api/ApiMyTest.py
```

类必须继承 `BaseApi`：

```python
from api.BaseApi import BaseApi, ApiNodeData


class ApiMyTest(BaseApi):
    MODULE_KEYS = ["my_test"]
```

推荐始终使用 `MODULE_KEYS`，即使当前只有一个 module。需要兼容多个前端实验名称时：

```python
class ApiMyTest(BaseApi):
    MODULE_KEYS = [
        "my_test",
        "my_test_alias",
    ]
```

前端启动消息中的 module 必须完全匹配：

```json
{
  "cmd": "start",
  "module": "my_test",
  "params": {}
}
```

### 2.2 API 发现过程

`BaseApi.create(module)` 的当前顺序：

1. 扫描 `api/` 下所有 `Api*.py`；
2. import 模块；
3. 查找 `BaseApi` 子类；
4. 比较 `MODULE_KEYS` 或 `MODULE_KEY`；
5. 找到后创建一个新实例；
6. 扫描未找到时，再使用 `api/Registry.py` 的类列表。

因此新 API 应同时：

1. 使用 `ApiXxx.py` 命名；
2. 声明唯一的 `MODULE_KEYS`；
3. 在 `api/Registry.py` 中 import 并加入 `BaseApi._REGISTRY` 列表。

示例：

```python
from api.ApiMyTest import ApiMyTest

BaseApi._REGISTRY = [
    # existing APIs ...
    ApiMyTest,
]
```

模块 import 阶段不要连接硬件、创建任务或读取前端参数。所有运行态工作放在 API 实例和
`_onSetup()` 中。

## 3. `BaseApi` 生命周期

### 3.1 完整调用顺序

```text
EngineCtrl.startTest()
  -> BaseApi.create(module) -> ApiMyTest()
  -> 清空旧软件状态，设置 DI 防抖
  -> 请求系统 Node 0
  -> running=True，开启功放
  -> 固定等待 100 ms
  -> api.setup(ctrl, params)
       -> self.ctrl = ctrl
       -> self.isActive = True
       -> 同步调用 self._onSetup(params)

运行中
  -> 同步调用 onUpdate()/onDi()/onWebCommand()

EngineCtrl.stopTest()
  -> running=False，关闭功放，请求 FFFF
  -> 正常收到 FFFF terminal update
  -> 若 API 实现 onTerminal()，先调用 onTerminal(ffff_hw_ts)
  -> api.onStop()
       -> self.isActive = False
       -> 同步调用 self._onStop()
  -> 清理控制器状态并发送 stop
```

`BaseApi` 定义见 `api/BaseApi.py:34-96`。

这里的 100 ms 是固定启动延时，不是 Node 0 ACK。API 不能把这段延时当成硬件同步
条件；普通实验节点是否生效，仍以自己的有效 `onUpdate()` 为准。

### 3.2 必要方法

```python
class ApiMyTest(BaseApi):
    MODULE_KEYS = ["my_test"]

    def __init__(self):
        super().__init__()

    def _onSetup(self, params):
        pass

    def onUpdate(self, nodeId, tick, hw_ts):
        pass

    def onDi(self, di, hw_ts):
        pass

    def onWebCommand(self, msg):
        pass

    def _onStop(self):
        pass
```

| 方法 | 调用时机 | API 应完成的工作 |
|---|---|---|
| `__init__()` | API 实例刚创建 | 初始化所有回调可能访问的字段 |
| `_onSetup(params)` | start 流程中 | 解析参数、构造节点、启动实验 |
| `onUpdate(nodeId,tick,hw_ts)` | 波形节点或 tick 生效 | 记录阶段、值和硬件时刻 |
| `onDi(di,hw_ts)` | FPGA 上报 DI | 判断边沿、动作、返回或计时条件 |
| `onWebCommand(msg)` | 前端发送实验专属命令 | 手动推进、更新参数或结束 |
| `_onStop()` | 正常收到 FFFF terminal update | 取消任务、整理报告、释放 API 状态 |

### 3.3 回调必须是同步方法

当前 `EngineCtrl` 直接调用：

```python
a.onUpdate(...)
a.onDi(...)
a.onWebCommand(...)
a.onStop()
```

不会 `await` 回调。因此不要写成：

```python
async def onUpdate(...):   # 当前调用链不会 await
    ...
```

需要异步实验流程时，在同步方法中创建任务：

```python
def _onSetup(self, params):
    self._task = asyncio.create_task(self._run(params))
```

并在 `_onStop()` 取消：

```python
def _onStop(self):
    if self._task and not self._task.done():
        self._task.cancel()
    self._task = None
```

当前控制器不会隔离 API 回调异常。`_onSetup()`、`onUpdate()`、`onDi()`、
`onWebCommand()` 和 `_onStop()` 都应快速返回且不能把异常抛给框架；尤其 `_onStop()`
一旦抛异常，会阻断控制器后续清理和停止完成通知。参数解析、报告整理等可能失败的
逻辑应在 API 内捕获异常，并按需调用 `stopTest(reason)`。

### 3.4 `isActive` 的使用

每个异步等待之后检查：

```python
await asyncio.sleep(0.1)
if not self.isActive or not self.ctrl.running:
    return
```

回调也建议先检查：

```python
def onDi(self, di, hw_ts):
    if not self.isActive or not self.ctrl.running:
        return
```

`EngineCtrl` 在调用 API `setup()` 前已经创建了 API 对象并触发系统 Node 0，所以字段应
在 `__init__()` 初始化；`not self.isActive` 的短路判断可忽略 setup 前的系统事件。

`stopTest()` 会先把 `ctrl.running` 置为 False，API 要等到终止事件才会变成
`isActive=False`。因此只检查 `isActive` 不足以排除停止过程中的在途回调；调用
`stopTest()` 后，不应再修改实验结果、上传节点或触发节点。

## 4. 前端物理量字典

### 4.1 JSON 形式

前端 JSON 对象的 key 一定是字符串：

```json
{
  "6": {
    "0": [0.0, 50.0],
    "1": [1.4142, 0.0],
    "2": [0.1414, 30.0]
  }
}
```

结构为：

```text
API通道字符串
  -> 逻辑层字符串
       -> [幅值, 频率或相位]
```

### 4.2 API 通道

`physDictToReg()` 会把 API 通道映射为 FPGA 物理通道：

| API 通道 | FPGA 物理通道 | 类型 |
|---:|---:|---|
| 0 | 0 | 电压 |
| 1 | 2 | 电压 |
| 2 | 4 | 电压 |
| 3 | 6 | 电压 |
| 4 | 8 | 电压 |
| 5 | 10 | 电压 |
| 6 | 1 | 电流 |
| 7 | 3 | 电流 |
| 8 | 5 | 电流 |
| 9 | 7 | 电流 |
| 10 | 9 | 电流 |
| 11 | 11 | 电流 |

API 输入使用前端/API 通道号；`ApiNodeData` 内部字典使用转换后的物理通道号。

### 4.3 逻辑层 0～64

| API 逻辑层 | FPGA index | `[第0项, 第1项]` 的含义 |
|---:|---:|---|
| 0 | `0xFF` | `[直流分量, 公共基频 Hz]` |
| 1 | `0x00` | `[基波峰值幅值, 相位°]` |
| 2 | `0x01` | `[二次谐波峰值幅值, 相位°]` |
| … | … | … |
| 64 | `0x3F` | `[64 次谐波峰值幅值, 相位°]` |

必须牢记：

- layer 0 不是基波；
- layer 0 的第二项是频率；
- layer 1 才是基波；
- layer 1～64 的第二项是相位；
- 一个通道各交流层共享 layer 0 中的基频；
- 有效逻辑层范围是 `0..64`。

`physDictToReg()` 当前不会主动检查层号范围或 `[值1,值2]` 的长度；API 输入契约和参数
校验必须保证层号为 `0..64`，每层恰好有两个有限数值。

一个通道的理论输出为：

\[
x(t)=D+\sum_{k=1}^{64}A_k\sin(2\pi kft+\phi_k)
\]

### 4.4 峰值和有效值

`physDictToReg()` 不会自动执行 RMS → 峰值转换。

如果前端给的是有效值，API 需要明确转换：

```python
peak = rms * math.sqrt(2.0)
```

如果前端本来就给峰值，则不能再次乘 `sqrt(2)`。每个 API 的输入文档必须明确幅值口径。

## 5. `physDictToReg()`：从物理量到寄存器

### 5.1 绝对量

`base` 和 `reset` 是绝对物理量：

```python
base_reg = self.physDictToReg(base_phys)
reset_reg = self.physDictToReg(reset_phys)
```

转换包括：

- 字符串通道转整数；
- API 通道映射为 FPGA 物理通道；
- 字符串层号转整数；
- 应用通道 Calibration；
- 幅值、频率、相位转 U32 寄存器。

绝对转换规则：

| 层 | 第0项 | 第1项 |
|---:|---|---|
| 0 | `amplitude × factorDc + bias` | 频率寄存器 |
| 1..64 | `amplitude × factorAc` | `(phase + 180°)` 相位寄存器 |

额外 180° 是当前设备交流极性补偿，API 不要自己再加。

### 5.2 增量

`steps` 必须按增量转换：

```python
step_reg = self.physDictToReg(step_phys, is_delta=True)
```

增量转换：

| 层 | 第0项 | 第1项 |
|---:|---|---|
| 0 | `Δamplitude × factorDc`，不加 bias | Δ频率 |
| 1..64 | `Δamplitude × factorAc` | Δ相位，不加 180° |

错误写法：

```python
step_reg = self.physDictToReg(step_phys)  # 会把绝对校准偏置用于每一步
```

正确写法：

```python
step_reg = self.physDictToReg(step_phys, is_delta=True)
```

负增量会转换为 U32 补码，FPGA 对 Base 做 32 位加法。

### 5.3 转换后的数据结构

`physDictToReg()` 返回：

```python
Dict[int, Dict[int, List[int]]]
```

即：

```python
{
    physical_channel: {
        logical_layer: [p1_u32, p2_u32]
    }
}
```

示意：

```python
{
    1: {
        0: [0x00000000, 0x0053E2D6],
        1: [0x10000000, 0x80000000],
    }
}
```

`ApiNodeData.base/reset/steps` 保存的是这种寄存器字典，不能直接放前端浮点 JSON。

## 6. `ApiNodeData` 完整定义

源码定义见 `api/BaseApi.py:13-32`：

```python
@dataclass
class ApiNodeData:
    mode: int

    interval: Optional[int] = None
    resetTime: Optional[int] = None

    base: Optional[Dict[int, Dict[int, List[int]]]] = None
    reset: Optional[Dict[int, Dict[int, List[int]]]] = None
    steps: Optional[List[Dict[int, Dict[int, List[int]]]]] = None
    gate: Optional[List[int]] = None

    resetDo: Optional[int] = None
    doActions: Optional[List[int]] = None

    countOverId: Optional[int] = None
    diMatchMask: Optional[int] = None
    diMatchId: Optional[int] = None
    timeoutMs: Optional[int] = None
    timeoutId: Optional[int] = None
```

### 6.1 字段总表

| 字段 | 类型 | 单位/数据域 | 作用 |
|---|---|---|---|
| `mode` | `int` | 当前使用 1/2/3 | 节点执行模式 |
| `interval` | `int/None` | ms | Mode2/3 基准和故障步保持时间 |
| `resetTime` | `int/None` | ms | Mode3 每次复归保持时间 |
| `base` | `Dict[int,Dict[int,List[int]]]/None` | 两个 U32 | 绝对基准 |
| `reset` | `Dict[int,Dict[int,List[int]]]/None` | 两个 U32 | Mode3 绝对复归值 |
| `steps` | 上述字典的 `List`/None | 两个 U32 增量 | 每个 tick 的增量 |
| `gate` | `[physical_ch,phase_u32]`/None | 寄存器域 | Mode4 相位门控预留 |
| `resetDo` | `int/None` | 高/低各 8 位 | Mode3 动作/复归 DO |
| `doActions` | `List[int]/None` | 24-bit packed | Mode1/2 节点内定时 DO |
| `countOverId` | `int/None` | 节点 ID | steps 完成后的目标 |
| `diMatchMask` | `int/None` | 位域 | 硬件节点 DI 判据 |
| `diMatchId` | `int/None` | 节点 ID | DI 命中目标 |
| `timeoutMs` | `int/None` | ms | 节点超时 |
| `timeoutId` | `int/None` | 节点 ID | 超时目标 |

这些是 Python 类型标注，dataclass 本身不会检查类型、范围或字段组合。API 必须在构造
节点前完成参数校验；不要依赖 `ApiNodeData` 自动拒绝负时间、非法 ID 或缺失字段。

### 6.2 `base`

物理来源：

```python
node.base = self.physDictToReg(base_phys)
```

编译为：

```text
DDS_WR_SHADOW
```

作用是同时设置 FPGA Base 和 Shadow；进入 mode 后由 `SYS_UPDATE` 使其成为 Active。

`base=None` 或 `{}` 不产生参数帧，节点会继承进入节点前 `SYS_SYNC` 得到的当前值。动态
反向扫描可利用 `base={}` 从上一节点末值继续；普通独立节点建议显式给出 base。

### 6.3 `reset`

物理来源：

```python
node.reset = self.physDictToReg(reset_phys)
```

编译为：

```text
DDS_WR_STAGE
```

它只覆盖 Shadow，不修改 Base。Mode3 利用这一点在复归值和故障累加值之间切换。

如果复归要求某些通道/层真正归零，必须把它们写进 reset。稀疏 reset 不会自动清空没有
出现的通道和层。

### 6.4 `steps`

形式：

```python
node.steps = [
    step_tick_1_reg,
    step_tick_2_reg,
    ...,
]
```

每一项编译为一批：

```text
DDS_STEP_SHADOW
```

每个 step 可以不同：

```python
node.steps = [
    self.physDictToReg(step1_phys, is_delta=True),
    self.physDictToReg(step2_phys, is_delta=True),
    self.physDictToReg(step3_phys, is_delta=True),
]
```

等步长扫描可以：

```python
one_step = self.physDictToReg(step_phys, is_delta=True)
node.steps = [one_step] * count
```

step 是连续累加：

```text
tick1 = base + step[0]
tick2 = base + step[0] + step[1]
...
```

`steps=None` 或空列表时，Mode2/3 当前会退化为 Static；Mode3 的 `resetDo is None`
也会退化为 Static。API 设计时应把这些字段组合视为参数错误，而不是依赖退化行为。

### 6.5 `interval`

- 单位：毫秒；
- Mode2/3 有非空 steps 时必须提供；
- 实验 API 应转换为正整数并设置合理上限；
- Mode2：base 和每个 step 都保持一个 interval；
- Mode3：每个故障/扫描值保持一个 interval；
- 最后一个 step 也会保持完整 interval，之后才执行 countOver。

### 6.6 `resetTime`

- 单位：毫秒；
- Mode3 必须提供；
- 实验 API 应转换为非负整数并设置合理上限；
- 初始 reset 和每个 step 前 reset 都保持 resetTime；
- reset 窗口只响应 manual trigger，不评估节点 DI/timeout。

### 6.7 `gate`

预留格式：

```python
node.gate = [physical_channel, phase_u32]
```

它要求：

- `base` 中有该物理通道；
- 该通道有 layer 0 频率；
- 有 steps 和 interval。

当前 `USEEngine.coreLoop()` 没有启用 Mode4 分派，所以新实验 API 不应使用 `gate` 或
`mode=4`。

### 6.8 跳转字段

三种自动跳转：

```python
# 超时
node.timeoutMs = 1000
node.timeoutId = 2

# DI
node.diMatchMask = 0x01
node.diMatchId = 3

# step完成
node.countOverId = 0xFFFF
```

字段必须成对：

- 有 `timeoutMs` 就要有 `timeoutId`；
- 有有效 `diMatchMask` 就要有 `diMatchId`；
- `countOverId` 只在有 steps 的 Mode2/3 走完后使用。

`timeoutMs=0` 在当前实现中等价于未启用 timeout；需要立即跳转时使用明确的软件触发，
不要依赖 0 ms timeout。

`diMatchMask` 的低 8 位为 0 时同样不会触发 DI 跳转。

## 7. 节点 ID 规则

### 7.1 可写范围

实验 API 可以定义：

```text
0x0001 .. 0xFFFE
```

节点 ID 是 `upsertNodes()` 字典的 key：

```python
self.ctrl.upsertNodes({
    1: node_prepare,
    2: node_test,
    3: node_idle,
})
```

### 7.2 两个保留节点

> **API 绝对不能通过 `upsertNodes()` 写入或覆盖节点 `0x0000` 和 `0xFFFF`。**

这是 API 编写红线，不是当前控制器已经实现的保护。`EngineCtrl.upsertNodes()` 不会
拒绝这两个 key，也不能期待它返回 False；错误写入会真实覆盖系统节点，必须由 API
参数校验、代码评审和测试保证所有 upsert key 位于 `1..0xFFFE`。

| ID | 引擎含义 | API 是否可定义 |
|---:|---|---|
| `0x0000` | FPGA 初始化、0输出、50Hz、DDS启动、防抖 | 禁止 |
| `0xFFFF` | FPGA reset、DO=0、实验终止 | 禁止 |

允许：

```python
node.timeoutId = 0xFFFF
node.diMatchId = 0xFFFF
node.countOverId = 0xFFFF
```

API 主动结束实验时使用：

```python
self.ctrl.stopTest()
```

不要用 `trigNode(0xFFFF)` 代替 `stopTest()`：前者不会立即设置 `running=False`，也
不会立即关闭功放。

禁止：

```python
self.ctrl.upsertNodes({0: custom_node})
self.ctrl.upsertNodes({0x0000: custom_node})
self.ctrl.upsertNodes({65535: custom_node})
self.ctrl.upsertNodes({0xFFFF: custom_node})
```

### 7.3 不要把 Node 0 当普通复归节点

状态机跳到 0 或 FFFF 时会清除本轮普通节点，只保留两个系统节点。因此循环、逐点搜索
和点间复归必须创建自己的普通节点：

```python
NODE_RESET = 1
NODE_TEST = 2
NODE_IDLE = 3
```

不要写：

```python
node.timeoutId = 0  # 会进入系统节点并清掉普通实验节点
```

### 7.4 前端 ID 与内部 ID

如果前端节点 ID 不受 API 控制，建议建立映射：

```python
front_to_engine = {
    front_node["id"]: index + 1
    for index, front_node in enumerate(front_nodes)
}
```

这样可以保证内部 ID：

- 不使用 0/FFFF；
- 不重复；
- 可为 stub/IDLE 节点预留区间；
- 不受前端显示编号影响。

所有 `timeoutId`、`diMatchId`、`countOverId` 和手动触发目标，都必须是已定义的普通
节点或 `0xFFFF`。目标节点不存在时，引擎会回落到 `0xFFFF` 并终止；不要把“缺失
目标”当作正常结束方法。

## 8. `EngineCtrl` 如何编译节点

### 8.1 字段到 FPGA 操作

| `ApiNodeData` | 编译操作 |
|---|---|
| `base` | `DDS_WR_SHADOW (0x10)` |
| `reset` | `DDS_WR_STAGE (0x11)` |
| `steps[i]` | `DDS_STEP_SHADOW (0x14)` |
| `gate` | `DDS_PHASE_GATE (0x1F)` |

同一 layer 且两个寄存器值相同的多个物理通道会合并成一个 channel mask，API 不需要
自己合并通道。

### 8.2 稀疏数据和继承

进入每个节点前，`USEEngine` 先发送 `SYS_SYNC`，把当前 Active 同步到 Base 和 Shadow。
因此：

- 新节点未写的通道/层保持上一节点当前值；
- `base={}` 可完整继承上一节点；
- sparse base 只修改列出的项；
- sparse step 只累加列出的项；
- sparse reset 只复归列出的项。

如果实验要求“完整清零/复归”，API 必须自己生成覆盖所有相关通道和层的 reset 字典。

上述继承首先是 FPGA 缓冲语义。控制层维护的 `EngineCtrl.state` 不是 FPGA 回读值；
处理正 tick 时会直接访问 `state[ch][layer]`。因此每个 step 中出现的通道/层，都必须
已由当前节点的 base 或已确认的前驱 base 建立绝对状态。否则 FPGA 可能已经执行 step，
但控制层遥测会因缺少基准项而异常，API 也收不到该次正常 `onUpdate()`。独立扫描节点
最稳妥的写法，是在同一节点 base 中补齐所有 step 涉及的通道和层。

## 9. 三种可用 mode

### 9.1 Mode1：Static

构造：

```python
node = ApiNodeData(
    mode=1,
    base=base_reg,
)
```

可选：

```text
doActions
timeoutMs/timeoutId
diMatchMask/diMatchId
```

精确时序：

```text
进入节点
  -> SYS_SYNC
  -> 写 base
  -> SYS_UPDATE
  -> emit tick=0
  -> 启动 doActions
  -> 启动 timeout
  -> 等待 manual / timeout / DI
```

没有出口的 Static 会保持当前值并等待软件或 DI；当前 `sleepForever()` 的实际上限约为
100000 秒，之后进入 FFFF。

适合：

- 固定波形；
- 预故障；
- 复归/IDLE；
- 定时状态序列；
- 等待前端手动推进。

### 9.2 Mode2：Sweep

构造：

```python
node = ApiNodeData(
    mode=2,
    base=base_reg,
    steps=step_regs,
    interval=100,
)
```

设有 N 个 step：

```text
base + UPDATE -> tick 0
预载 step[0]
等待 interval

UPDATE -> tick 1
预载 step[1]
等待 interval

...

UPDATE -> tick N
等待最后一个 interval
-> countOverId 或继续等待触发
```

tick 与输出：

| tick | 当前输出 |
|---:|---|
| 0 | base |
| 1 | base + step[0] |
| 2 | base + step[0] + step[1] |
| N | base + Σstep[0..N-1] |

从 tick0 生效到 countOver 共经历 `N+1` 个 interval。

这是未被 manual、timeout 或 DI 提前中断时的完整过程。发生提前跳转时，后续 tick
不再产生，`countOverId` 也不会执行；API 不能把“等满固定 tick 数”作为唯一完成条件。

适合：

- 幅值阶梯；
- 频率阶梯；
- 相位阶梯；
- 谐波比例阶梯；
- 连续动作值搜索。

### 9.3 Mode3：Reset

构造：

```python
node = ApiNodeData(
    mode=3,
    base=base_reg,
    reset=reset_reg,
    steps=step_regs,
    interval=500,
    resetTime=100,
    resetDo=(fault_do << 8) | reset_do,
)
```

缓冲含义：

- Base 保存故障/扫描值；
- reset 只覆盖 Shadow；
- step 在复归期间累加 Base；
- Active 在 reset 和故障值之间切换。

精确 tick：

```text
reset生效 -> tick -1 -> resetDo低8位
等待 resetTime

base生效  -> tick 0  -> resetDo高8位
等待 interval

reset生效 -> tick -1 -> resetDo低8位
复归期间预载 step[0]
等待 resetTime

step1生效 -> tick 1 -> resetDo高8位
等待 interval

...
```

Mode3 的 tick 序列：

```text
-1, 0, -1, 1, -1, 2, ... , -1, N
```

注意：

- 真正进入 Mode3 需要非空 steps、`resetDo is not None`，以及数值型
  `interval/resetTime`；
- `base/reset` 在代码层可以为空以继承缓冲状态，但普通独立复归实验应显式提供；
- `resetDo=0` 是合法值，不能用 truthiness 判断是否配置；
- 缺少 steps 或 `resetDo` 时会静默退化为 Static，而不是报参数错误；
- 只有真正进入 Mode3 时才不执行普通 `doActions`；退化为 Static 后会按 Mode1 处理；
- reset 区间不进行 FSM DI/timeout 跳转，只响应 manual trigger；
- API 的 `onDi()` 仍可能收到 reset 区间发生的 DI 上报，业务记录时要自行识别阶段。

每个 Mode3 阶段先执行波形 `SYS_UPDATE`，再写对应的阶段 DO；两者分别等待 ACK，不是
同一条原子硬件指令。`onUpdate()` 表示波形 Update 已确认，不表示对应 DO 已经生效。

### 9.4 Mode4

`runDcComp()` 和 gate 编译代码存在，但 `coreLoop` 没有启用 `case 4`。

结论：

```text
新 API 当前只能使用 mode 1、2、3。
```

## 10. tick 和硬件时间戳

### 10.1 `onUpdate`

签名：

```python
def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
    ...
```

| mode | tick 序列 |
|---:|---|
| 1 | `0` |
| 2 | `0,1,2,...,N` |
| 3 | `-1,0,-1,1,-1,2,...,-1,N` |

表中是节点未被提前触发时的完整序列。manual、timeout 或 DI 跳转后，API 实际只会
收到该序列的一个前缀。

通用含义：

- `tick=-1`：Mode3 reset 波形生效；
- `tick=0`：base 生效；
- `tick>0`：对应数量的 step 已累计并生效；
- `hw_ts>=0`：该次 Update 最后一帧 ACK 的 FPGA U32 微秒时间戳；
- `hw_ts==-1`：ACK 超时或硬件通信错误产生的无效时间戳，不能作为波形生效确认，也
  不能参与实验计时；等待控制器随后终止并报告 error。

### 10.2 时间差

`onUpdate` 时间戳来自 FPGA U32 微秒计数；`onDi` 时间戳是在该计数上减去防抖估算
时间后的 Python 整数。跨两类时间戳计算差值时，必须统一处理 U32 回绕：

```python
elapsed_us = (end_ts - start_ts) & 0xFFFFFFFF
elapsed_ms = elapsed_us / 1000.0
```

不要直接：

```python
elapsed_ms = (end_ts - start_ts) / 1000.0
```

### 10.3 FFFF 和 Node0 回调

- Node FFFF 的 update 被 `EngineCtrl` 截获，API 不会收到 `onUpdate(0xFFFF,...)`；
- 若某个 API 自己实现了可选的 `onTerminal(hw_ts)`，EngineCtrl 会在无参
  `onStop()` 前把该 update 的硬件时间戳传给它；
- 正常收到 FFFF terminal update 后，控制器调用 API `_onStop()`；
- 系统 Node0 可能在 API setup 前产生事件；
- 回调字段应在 `__init__()` 初始化，并使用 `isActive/ctrl.running` 保护；
- 普通实验不要依赖收到 FFFF tick 来生成报告，应在 `_onStop()` 中完成最终结算。

`_onStop()` 只是控制层收到终止 update 的时点，不表示 FFFF 节点安排的 DO=0 已经收到
独立 ACK，也不能作为所有硬件安全动作均已完成的确认。

## 11. DI 触发详细定义

### 11.1 `onDi()` 收到什么

```python
def onDi(self, di: int, hw_ts: int):
    ...
```

- `di` 是完整的当前 8 位 DI 状态，不是 changed mask；
- FPGA 原始 DI 时间戳是 U32 微秒计数；当前引擎传给 `onDi()` 的 `hw_ts` 已减去配置的
  防抖估算时间 `dbncUs`，代码中没有先做 U32 截断；
- `EngineCtrl` 会把每次 DI 上报都交给 API；
- API 回调与节点 FSM 的 `diMatchMask` 是两套用途：
  - FSM 决定是否跳节点；
  - API 回调用于记录动作、返回和报告。

API 自己判断变化：

```python
changed = (di ^ self._last_di) & interested_mask
self._last_di = di
```

因此时间差仍统一按 32 位计数处理：

```python
elapsed_us = (hw_ts - entry_ts) & 0xFFFFFFFF
```

### 11.2 `diMatchMask` 位域

| 位 | 值 | 含义 |
|---:|---:|---|
| 0..7 | `0x001..0x080` | 选择 DI0..DI7 |
| 8 | `0x100` | 1=所有选中位满足；0=任一位满足 |
| 9 | `0x200` | 1=参考当前节点入口 DI；0=参考实验起始 DI |
| 10 | `0x400` | 反转比较参考，使条件从“不同”变为“相同” |

参考值：

```python
reference = diPrev if mask & 0x200 else diStart
if mask & 0x400:
    reference ^= 0xFF
```

匹配：

```python
watch = mask & 0xFF
value = (diNow ^ reference) & watch

OR模式:  value != 0
AND模式: value == watch
```

其中：

- `diStart`：Node0 收到跳转并退出、即将进入首个实验节点时的 DI；不是 Node0
  `tick=0` 时采样；
- `diPrev`：进入当前节点、完成 SYS_SYNC 后的 DI。

`0x400` 只是当前电平比较，不会记忆“先动作、再返回”的历史。带 `0x400` 时：

- OR：任一选中位当前等于原参考值即命中；
- AND：所有选中位当前等于原参考值才命中。

因此它可能在节点进入后第一次评估时立即成立。尤其
`0x200 | 0x400` 同时使用时，当前 DI 通常本来就等于本节点入口参考值，不应把这种组合
当成通用“返回沿”写法。

### 11.3 常用写法

```python
DI_AND = 0x100
DI_REF_ENTRY = 0x200
DI_INVERT_REFERENCE = 0x400
```

任一选中 DI 相对实验起点发生变化：

```python
node.diMatchMask = logic_mask
```

所有选中 DI 相对实验起点发生变化：

```python
node.diMatchMask = logic_mask | DI_AND
```

任一选中 DI 相对当前节点入口发生变化：

```python
node.diMatchMask = logic_mask | DI_REF_ENTRY
```

所有选中 DI 相对当前节点入口发生变化：

```python
node.diMatchMask = logic_mask | DI_AND | DI_REF_ENTRY
```

所有选中 DI 恢复到实验起点状态：

```python
node.diMatchMask = (
    logic_mask | DI_AND | DI_INVERT_REFERENCE
)
```

这只是“当前全部等于实验起点参考”的电平条件。API 仍需用自己的 phase 状态确认此前
确实发生过动作，才能把它解释为返回事件。

### 11.4 触发优先级

同一次 FSM 评估：

```text
manual trigNode
  > timeout
  > DI match
  > 最后一个 interval 结束后的 countOver
```

Mode3 resetTime 窗口只检查 manual trigger；DI 和 timeout 会等到故障 interval 再评估。

## 12. timeout、countOver 和 manual

### 12.1 timeout

```python
node.timeoutMs = 1000
node.timeoutId = 2
```

时间起点：

- Mode1：base tick0 生效后；
- Mode2：base tick0 生效后；
- Mode3：初始 reset tick-1 生效后。

timeout 到期后返回 timeoutId。若 Mode3 在 resetTime 内到期，会在进入下一次正常
interval 时被识别。

### 12.2 countOver

```python
node.countOverId = 0xFFFF
```

仅用于有 steps 的 Mode2/3：

1. 所有 step 已生效；
2. 最后一个 step 保持完整 interval；
3. 返回 countOverId。

未设置 `countOverId` 时，最后一个 interval 后节点继续保持，并等待 manual、timeout
或 DI，不会自动结束。

### 12.3 manual

API 调用：

```python
self.ctrl.trigNode(target_id)
```

用于：

- 启动首节点；
- 前端 next 命令；
- 动态搜索进入下一点；
- Python 侧计时条件完成；
- 进入普通中间/结果节点。

`trigNode()` 是请求跳转，不代表目标节点已经生效。真正生效以
`onUpdate(target_id, tick, hw_ts>=0)` 为准。

引擎只保存一个待处理的 manual target。上一个请求尚未被引擎消费时再次调用
`trigNode()`，后一个目标会覆盖前一个目标。因此不要连续快速触发多个节点；每次
trigger 后先等待目标节点的有效 `onUpdate()`，再发下一次 trigger。

主动结束实验应调用 `stopTest()`，而不是手动触发 FFFF。

## 13. DO 数据定义

### 13.1 `doActions`

每项编码：

```python
packed = ((delay_ms & 0xFFFF) << 8) | (do_value & 0xFF)
```

解码：

```python
delay_ms = (packed >> 8) & 0xFFFF
do_value = packed & 0xFF
```

示例：

```python
node.doActions = [
    (0 << 8) | 0x01,
    (100 << 8) | 0x03,
    (500 << 8) | 0x00,
]
```

时间原点是 base Update 收到 ACK 的时刻。列表应按 delay 升序。

API 应保证 `delay_ms` 是 `0..65535` 的整数、`do_value` 是 `0..255` 的整数；当前编码
会用位掩码截断越界值，不会替 API 报错。

当前行为：

- Mode1/2 会启动 doActions；
- 真正进入 Mode3 后不执行普通 doActions；
- DO 是锁存值，节点切换不会自动归零；
- Mode1/2 的 delay=0 动作也在 base tick0 之后异步启动；
- FFFF 会安排一个 delay=0 的 DO0，但它有独立的命令和 ACK；
- 当前引擎只有在新节点 `doActions` 非空时才取消旧节点延时 DO。

所以 API 不应让一个节点的长延时 DO 跨越未知节点边界。需要明确 DO 状态时，在目标
Mode1/2 节点提供从 0 ms 开始的动作；Mode3 使用 resetDo。

### 13.2 `resetDo`

```python
node.resetDo = ((fault_do & 0xFF) << 8) | (reset_do & 0xFF)
```

| 阶段 | 使用位 |
|---|---|
| Mode3 tick=-1 reset | 低 8 位 `reset_do` |
| Mode3 tick>=0 fault | 高 8 位 `fault_do` |

`resetDo=0` 表示两个阶段 DO 都为 0，是有效配置。

API 应把 `resetDo` 限制在 `0..0xFFFF`。

## 14. `EngineCtrl` 接口

### 14.1 API 应使用的接口

| 接口 | 返回 | 用途 |
|---|---|---|
| `upsertNodes(nodes)` | `bool` | 编译并新增/覆盖普通节点 |
| `trigNode(nodeId)` | `bool` | 请求跳转 |
| `stopTest(reason=None)` | 无 | 关功放并请求 FFFF |
| `sendNodeUpdate(nodeId)` | 无 | 发送当前可见业务节点 ID |
| `sendReport(data)` | 无 | 发送报告 |
| `sendError(msg)` | 无 | 发送错误但不自动停止 |
| `sendValueUpdate(targetDict,src=None)` | 无 | 把寄存器状态反转换后发 static |
| `sendDi()` | 无 | 重新发送当前 DI |
| `sendDo()` | 无 | 重新发送当前 DO |

可读取的当前状态：

```python
self.ctrl.di       # 当前完整 8 位 DI
self.ctrl.do       # 当前完整 8 位 DO
self.ctrl.running  # 控制器运行标志
```

### 14.2 `upsertNodes()`

```python
ok = self.ctrl.upsertNodes({
    1: node1,
    2: node2,
})
```

返回 False：

- 当前实验不在 running；
- 任一节点编译异常。

编译失败时控制器已经请求进入 FFFF。API 必须立即停止后续 trigger：

```python
if not self.ctrl.upsertNodes(nodes):
    return
```

返回 True 表示：

- `ApiNodeData` 已在主线程成功编译；
- 节点更新已经排队投递到硬件线程。

它不表示 FPGA 已经执行节点，也不返回硬件 ACK。

### 14.3 `trigNode()`

```python
if not self.ctrl.trigNode(first_node):
    return
```

返回 False 表示控制器已不在 running。返回 True 只表示跳转请求已投递。

`upsertNodes()` 和 `trigNode()` 都是同步、跨线程 fire-and-forget 接口。初始化时连续调用
会按调用顺序排队：

```python
if not self.ctrl.upsertNodes({NODE_TEST: new_node}):
    return

if not self.ctrl.trigNode(NODE_TEST):
    return
```

固定 `sleep(0.05)` 不是 ACK、上传完成通知或同步屏障，不应作为正确性的依据。当前
接口没有 upsert ACK；真正的执行确认只能等待目标节点
`onUpdate(..., tick=0, hw_ts>=0)`。如果业务必须获得节点安装确认，应先扩展正式控制器
接口。

manual trigger 是单槽、后写覆盖前写。一次 `trigNode()` 后应等待目标节点确认，不能
连续快速发送多个 trigger。

### 14.4 `stopTest()`

```python
self.ctrl.stopTest()
self.ctrl.stopTest("Invalid test parameter")
```

行为：

1. `running=False`；
2. 有 reason 时保存为最终 error；
3. 关闭功放；
4. 请求进入 FFFF；
5. 正常收到 FFFF terminal update 后调用 API `_onStop()`；
6. 发送 stop。

`stopTest()` 不是立即销毁 API。调用后不要再上传或触发节点。

带 reason 的正常终止消息顺序是：

```text
error -> API _onStop() 中的 report -> stop
```

如果 `running` 已经是 False，再次调用 `stopTest()` 不执行任何操作。

### 14.5 报告和更新

最终或分段报告：

```python
self.ctrl.sendReport({
    "rows": self._rows,
})
```

错误但继续运行：

```python
self.ctrl.sendError("Point 3 has no valid result")
```

`sendValueUpdate()` 的参数是寄存器域字典：

```python
self.ctrl.sendValueUpdate(node.base)
```

它会执行：

- 物理通道反映射为 API 通道字符串；
- 寄存器反转换为物理量；
- 发送 `{"type":"value_update","static":...}`。

不传 `src` 时，直接转换 `targetDict` 中的值。传入 `src` 时，`targetDict` 只负责选择
要发送的通道/层，实际值取自 `src[ch][layer]`；`src` 必须包含所有对应 key，否则会
抛 `KeyError`。steps 是增量寄存器，不能脱离累计后的绝对 `state` 直接作为
`sendValueUpdate()` 的物理值发送。

如果 API 要发送自定义进度或字段，当前一些旧 API 使用私有 `_send()`。新 API 不应依赖
`_compileNode`、`engine`、`setAmplifier` 等内部对象；自定义消息最好先在 EngineCtrl
增加明确公共接口，或统一通过 report/value_update 约定表达。

### 14.6 内部属性

现有旧 API 偶尔读取：

```text
ctrl.nodes
ctrl.state
ctrl._module
ctrl._send
```

它们属于控制器内部实现，不建议新 API 以此作为核心状态。API 应自己保存：

- 节点用途映射；
- 原始物理参数；
- 当前业务阶段；
- 报告数据；
- 当前测试点和硬件时间戳。

## 15. 回调的正确用法

### 15.1 `onUpdate()`

控制器在调用 API `onUpdate()` 前，会先按 node/tick 更新自己的软件 `state`，并按节流
规则向前端发送 `value_update`。因此回调看到的是本次更新后的软件状态；它仍是根据
base/step 推导的状态，不是 FPGA 模拟量回读。

推荐模式：

```python
def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
    if not self.isActive or not self.ctrl.running:
        return
    if hw_ts < 0:
        return

    self._current_node = nodeId

    if nodeId == NODE_TEST and tick == 0:
        self._test_entry_ts = hw_ts
        self._di_at_entry = self.ctrl.di

    if nodeId == NODE_IDLE and tick == 0:
        self._point_done.set()
```

不要根据 `trigNode()` 调用时刻认定输出已经生效；以 `hw_ts>=0` 的 onUpdate 为准。

### 15.2 `onDi()`

控制器在调用 API `onDi()` 前，已经把 `ctrl.di` 更新为本次完整 DI，并向前端发送 DI
value_update。

推荐同时保存：

- 上一次 DI；
- 当前节点入口 DI；
- 实验起点 DI；
- 当前 node/tick/phase；
- 需要监视的 mask。

```python
def onDi(self, di: int, hw_ts: int):
    if not self.isActive or not self.ctrl.running:
        return

    changed = (di ^ self._last_di) & self._trip_mask
    self._last_di = di

    if self._current_node != NODE_TEST:
        return

    if changed and self._test_entry_ts is not None:
        elapsed_ms = (
            (hw_ts - self._test_entry_ts) & 0xFFFFFFFF
        ) / 1000.0
```

`onDi()` 会收到完整 DI 上报，不等于节点 FSM 一定已经命中。多位 AND、参考点和 Mode3
reset 窗口都应按实验语义处理。

### 15.3 `onWebCommand()`

`start`、`stop` 等框架公共命令由控制器处理；实验专属命令交给当前 API：

```python
def onWebCommand(self, msg):
    if not self.isActive or not self.ctrl.running:
        return

    match msg.get("cmd"):
        case "next_node":
            self.ctrl.trigNode(self._next_node)
        case "finish":
            self.ctrl.stopTest()
        case _:
            self.ctrl.sendError(
                f"Unknown command: {msg.get('cmd')}"
            )
```

### 15.4 `_onStop()`

`BaseApi.onStop()` 会先设置：

```python
self.isActive = False
```

再调用 `_onStop()`。所以 `_onStop()` 中不要用：

```python
if not self.isActive:
    return
```

否则清理和报告永远不会执行。

正确：

```python
def _onStop(self):
    if self._task and not self._task.done():
        self._task.cancel()
    self._task = None

    self.ctrl.sendReport({
        "rows": self._rows,
    })
```

若某个实验必须用真实 FFFF 时间戳结算最后一个业务节点，可在该 API 内实现可选钩子，
不要修改所有 API 的 `onStop()` 签名：

```python
def __init__(self):
    super().__init__()
    self._terminal_hw_ts = None

def onTerminal(self, hw_ts):
    self._terminal_hw_ts = (
        hw_ts & 0xFFFFFFFF
        if isinstance(hw_ts, int)
        and not isinstance(hw_ts, bool)
        and hw_ts >= 0
        else None
    )
```

`_onStop()` 再用有效的 `_terminal_hw_ts` 与节点入口硬件时间戳按 U32 回绕计算最后一段
持续时间。时间戳为 `None` 时不要用主机时间伪造。

## 16. 常用节点图设计

### 16.1 单静态节点

```text
[TEST]
  ├─ DI -> FFFF
  └─ timeout -> FFFF
```

```python
node = ApiNodeData(
    mode=1,
    base=base_reg,
    diMatchMask=trip_mask | 0x200,
    diMatchId=0xFFFF,
    timeoutMs=5000,
    timeoutId=0xFFFF,
)
```

这种最小图只适合不需要通过目标节点区分结束原因的实验。报告必须严格区分 DI 和 timeout
时，让两个出口分别进入不同的普通结果节点，再由 API 调用 `stopTest()`。

### 16.2 定时状态序列

```text
[RESET] --timeout--> [PRE_FAULT] --timeout--> [FAULT]
                                               ├─ DI -> FFFF
                                               └─ timeout -> FFFF
```

每个状态使用 Mode1：

```python
nodes = {
    NODE_RESET: ApiNodeData(
        mode=1,
        base=reset_reg,
        timeoutMs=reset_ms,
        timeoutId=NODE_PREFAULT,
    ),
    NODE_PREFAULT: ApiNodeData(
        mode=1,
        base=prefault_reg,
        timeoutMs=prefault_ms,
        timeoutId=NODE_FAULT,
    ),
    NODE_FAULT: ApiNodeData(
        mode=1,
        base=fault_reg,
        diMatchMask=trip_mask | 0x200,
        diMatchId=0xFFFF,
        timeoutMs=fault_ms,
        timeoutId=0xFFFF,
    ),
}
```

### 16.3 连续扫描

```python
node = ApiNodeData(
    mode=2,
    base=base_reg,
    steps=[step_reg] * count,
    interval=step_time_ms,
    diMatchMask=trip_mask,
    diMatchId=0xFFFF,
    countOverId=0xFFFF,
)
```

### 16.4 步间复归扫描

```python
node = ApiNodeData(
    mode=3,
    base=base_reg,
    reset=reset_reg,
    steps=[step_reg] * count,
    interval=fault_hold_ms,
    resetTime=reset_hold_ms,
    resetDo=((fault_do & 0xFF) << 8) | (reset_do & 0xFF),
    diMatchMask=trip_mask,
    diMatchId=0xFFFF,
    countOverId=0xFFFF,
)
```

### 16.5 多测试点：IDLE/TEST 循环

适合阻抗搜索、差动搜索、多个定点：

```text
[IDLE] --API trig--> [TEST]
   ^                   ├─ DI ------> [TRIP]
   |                   └─ timeout -> [TIMEOUT]
   |                                  |
   └──────── API记录结果并trig ────────┘
```

IDLE、TRIP 和 TIMEOUT 都是普通节点，不是 Node0/FFFF。每个测试点：

1. 动态 upsert 本点 TEST；
2. 清理本点 Event、时间戳和 phase；
3. trigger TEST，并等待有效 TEST tick0；
4. 由不同结果节点确认 DI 或 timeout；
5. API trigger IDLE；
6. 收到本代 IDLE tick0 后结算本点；
7. 软件 watchdog 超时则停止整轮，不能在未知输出状态继续。

必须用 phase 或 generation 防止旧回调提前释放本点 Event。第 18 节给出完整模板。

### 16.6 前端手动推进

```text
[STATE1] --next_node--> [STATE2] --next_node--> [STATE3] --next_node--> FFFF
```

```python
def onWebCommand(self, msg):
    if not self.isActive or not self.ctrl.running:
        return
    if msg.get("cmd") != "next_node":
        return

    if self._current_node == NODE_1:
        target = NODE_2
    elif self._current_node == NODE_2:
        target = NODE_3
    elif self._current_node == NODE_3:
        self.ctrl.stopTest()
        return
    else:
        return

    if not self.ctrl.trigNode(target):
        return
```

前端必须等待目标节点的有效 `onUpdate()` 后才能再次发送 `next_node`。

### 16.7 动态静态值：双节点乒乓

前端连续更新静态波形时，可交替使用两个普通节点：

```text
Node1 active -> 更新并触发 Node2
Node2 active -> 更新并触发 Node1
```

避免业务层把“正在执行的节点对象”和“下一次要替换的节点”混为一个阶段。

### 16.8 触发后延时 stub

节点字段只能表达 DI 命中后立即跳转。如果业务需要：

```text
DI命中 -> 保持当前输出 200ms -> 下一状态
```

可创建普通 stub：

```python
stub = ApiNodeData(
    mode=1,
    base={},          # 继承当前输出
    timeoutMs=200,
    timeoutId=next_id,
)

test.diMatchId = STUB_ID
```

stub ID 也必须位于 1..FFFE，并与业务节点不冲突。

### 16.9 用 `ApiScanTest` 理解一个完整 API

`api/ApiScanTest.py` 把多个前端 module 映射到同一个 API 类：

```python
MODULE_KEYS = [
    "ac_test",
    "dc_test",
    "harmonic_test",
    "steps_gradient_test",
    "acdc_test",
]
```

它们不需要不同的底层波形函数；实际波形由 `statics` 和 `steps` 中选择的通道、层及数值
决定。这体现了实验 API 的核心做法：把“实验名称和参数算法”留在 API，把“执行绝对
波形、增量、复归和跳转”统一表达成节点。

其数据处理顺序是：

```text
params.statics
  -> physDictToReg(..., is_delta=False)
  -> 正向扫描 base

params.steps
  -> physDictToReg(..., is_delta=True)
  -> 正向 step

对每个物理 step 的两个分量取负
  -> physDictToReg(..., is_delta=True)
  -> 反向 step

resetTableData + 所有参与扫描的通道/层补零
  -> physDictToReg(..., is_delta=False)
  -> 完整 reset
```

自动模式的节点骨架：

```text
[可选 Node1：试验前复归]
          |
          v
[Node2：正向 Mode2/Mode3]
   | DI动作             | countOver
   v                    v
[Node3：反向]         FFFF
   | 返回电平/countOver
   v
 FFFF
```

关键设计点：

- 无逐步复归时 Node2/3 用 Mode2，有逐步复归时用 Mode3；
- Node2 正扫使用正增量，Node3 反扫使用负增量；
- Node3 的 `base={}` 有意继承 Node2 结束输出；
- 动作-返回模式中，Node2 的 DI 目标是 Node3；
- Node3 用 `logicMask | 0x100 | 0x400` 比较“所有选中位回到实验起点电平”；
- `onUpdate()` 用 node/tick 区分正扫、反扫和复归阶段；
- `onDi()` 记录 DI 时间戳与最近有效 tick，再由 `_onStop()` 生成报告；
- 动作时间取“DI 补偿时间戳 - 最近一次有效 tick ACK 时间戳”并按 U32 回绕；
- 报告物理量由 `statics + steps × tick` 推导，不是 FPGA 模拟量回读；
- 手动模式交替更新 Node1/Node2，形成静态值 ping-pong。

新 API 可以复用这个“输入解析 → 绝对/增量转换 → 一次构造节点图 → 回调记录 →
停止报告”的结构，但应采用本指南的改进约束：保存并取消后台 task、检查
`upsertNodes()/trigNode()` 返回值、校验时间和数量范围，不把固定 sleep 当成同步确认。

## 17. 完整可复制模板：静态动作实验

```python
from typing import Any, Dict, Optional

from api.BaseApi import BaseApi, ApiNodeData


NODE_TEST = 1
NODE_TRIP = 2
NODE_TIMEOUT = 3

DI_REF_ENTRY = 0x200


class ApiMyTripTest(BaseApi):
    MODULE_KEYS = ["my_trip_test"]

    def __init__(self):
        super().__init__()

        self._current_node = -1
        self._entry_ts: Optional[int] = None
        self._di_at_entry = 0
        self._trip_mask = 0
        self._trip_candidate_ts: Optional[int] = None
        self._trip_candidate_di: Optional[int] = None
        self._exit: Optional[str] = None
        self._timeout_ms = 0

    def _onSetup(self, params: Dict[str, Any]):
        try:
            base_phys = params.get("base")
            if not isinstance(base_phys, dict) or not base_phys:
                raise ValueError("params.base is required")

            self._timeout_ms = int(
                params.get("timeoutMs", 5000)
            )
            if not 1 <= self._timeout_ms <= 3_600_000:
                raise ValueError(
                    "timeoutMs must be in 1..3600000"
                )

            self._trip_mask = int(
                params.get("tripMask", 0)
            ) & 0xFF

            base_reg = self.physDictToReg(base_phys)

            test_node = ApiNodeData(
                mode=1,
                base=base_reg,
                timeoutMs=self._timeout_ms,
                timeoutId=NODE_TIMEOUT,
            )

            if self._trip_mask:
                test_node.diMatchMask = (
                    self._trip_mask | DI_REF_ENTRY
                )
                test_node.diMatchId = NODE_TRIP

            if not self.ctrl.upsertNodes({
                NODE_TEST: test_node,
                NODE_TRIP: ApiNodeData(
                    mode=1,
                    base={},
                ),
                NODE_TIMEOUT: ApiNodeData(
                    mode=1,
                    base={},
                ),
            }):
                return

            if not self.ctrl.trigNode(NODE_TEST):
                return

        except Exception as exc:
            self.ctrl.stopTest(
                f"Invalid my_trip_test parameters: {exc}"
            )

    def onUpdate(
        self,
        nodeId: int,
        tick: int,
        hw_ts: int,
    ):
        if not self.isActive or not self.ctrl.running:
            return
        if hw_ts < 0:
            return

        self._current_node = nodeId

        if nodeId == NODE_TEST and tick == 0:
            self._entry_ts = hw_ts
            self._di_at_entry = self.ctrl.di

        elif nodeId == NODE_TRIP and tick == 0:
            self._exit = "trip"
            self.ctrl.stopTest()

        elif nodeId == NODE_TIMEOUT and tick == 0:
            self._exit = "timeout"
            self.ctrl.stopTest()

    def onDi(self, di: int, hw_ts: int):
        if not self.isActive or not self.ctrl.running:
            return

        if (
            self._current_node == NODE_TEST
            and self._entry_ts is not None
            and self._trip_mask
            and self._trip_candidate_ts is None
            and (
                (di ^ self._di_at_entry)
                & self._trip_mask
            )
        ):
            self._trip_candidate_ts = hw_ts
            self._trip_candidate_di = di

    def onWebCommand(self, msg: Dict[str, Any]):
        if not self.isActive or not self.ctrl.running:
            return

        match msg.get("cmd"):
            case "finish":
                self.ctrl.stopTest()
            case _:
                self.ctrl.sendError(
                    f"Unknown my_trip_test command: "
                    f"{msg.get('cmd')}"
                )

    def _onStop(self):
        trip_time_ms = None

        if (
            self._exit == "trip"
            and self._entry_ts is not None
            and self._trip_candidate_ts is not None
        ):
            elapsed_us = (
                self._trip_candidate_ts
                - self._entry_ts
            ) & 0xFFFFFFFF

            # 排除防抖回溯跨过 entry 后形成的近 2^32 伪时长。
            max_valid_us = (
                self._timeout_ms + 1000
            ) * 1000
            if elapsed_us <= max_valid_us:
                trip_time_ms = elapsed_us / 1000.0

        self.ctrl.sendReport({
            "tripTimeMs": (
                round(trip_time_ms, 3)
                if trip_time_ms is not None
                else None
            ),
            "tripDi": (
                self._trip_candidate_di
                if self._exit == "trip"
                else None
            ),
            "result": self._exit or "stopped",
        })
```

对应前端 start：

```json
{
  "cmd": "start",
  "module": "my_trip_test",
  "params": {
    "debounce": 20,
    "timeoutMs": 5000,
    "tripMask": 1,
    "base": {
      "6": {
        "0": [0.0, 50.0],
        "1": [1.4142, 0.0]
      }
    }
  }
}
```

## 18. 完整可复制模板：异步多点实验

```python
import asyncio
from typing import Any, Dict, List, Optional

from api.BaseApi import BaseApi, ApiNodeData


NODE_IDLE = 1
NODE_TEST = 2
NODE_TRIP = 3
NODE_TIMEOUT = 4

DI_REF_ENTRY = 0x200


class ApiMyPointTest(BaseApi):
    MODULE_KEYS = ["my_point_test"]

    def __init__(self):
        super().__init__()

        self._task: Optional[asyncio.Task] = None
        self._idle_ready = asyncio.Event()
        self._point_done = asyncio.Event()
        self._points: List[dict] = []
        self._rows: List[dict] = []
        self._phase = "CREATED"
        self._stopping = False
        self._current_node = -1
        self._entry_ts: Optional[int] = None
        self._trip_ts: Optional[int] = None
        self._trip_mask = 0
        self._di_at_entry = 0
        self._point_exit: Optional[str] = None
        self._hold_ms = 0

    def _can_run(self) -> bool:
        return bool(
            self.isActive
            and self.ctrl is not None
            and self.ctrl.running
            and not self._stopping
        )

    def _onSetup(self, params: Dict[str, Any]):
        try:
            self._points = params.get("points") or []
            self._trip_mask = int(
                params.get("tripMask", 0)
            ) & 0xFF

            if not isinstance(self._points, list):
                raise ValueError("params.points must be a list")
            if not self._points:
                raise ValueError("params.points is empty")

            self._task = asyncio.create_task(
                self._run(params)
            )
        except Exception as exc:
            self.ctrl.stopTest(
                f"Invalid point-test parameters: {exc}"
            )

    async def _run(self, params: Dict[str, Any]):
        try:
            idle_phys = params.get("idle")
            if not isinstance(idle_phys, dict) or not idle_phys:
                raise ValueError("params.idle is required")

            idle_node = ApiNodeData(
                mode=1,
                base=self.physDictToReg(idle_phys),
            )

            if not self.ctrl.upsertNodes({
                NODE_IDLE: idle_node,
                NODE_TRIP: ApiNodeData(
                    mode=1,
                    base={},
                ),
                NODE_TIMEOUT: ApiNodeData(
                    mode=1,
                    base={},
                ),
            }):
                return

            # 首点也先真正进入 IDLE，不能只上传节点后直接进 TEST。
            self._phase = "WAIT_INITIAL_IDLE"
            self._idle_ready.clear()
            if not self.ctrl.trigNode(NODE_IDLE):
                return

            try:
                await asyncio.wait_for(
                    self._idle_ready.wait(),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                if self._can_run():
                    self.ctrl.stopTest(
                        "Initial IDLE confirmation timeout"
                    )
                return

            for point in self._points:
                if not self._can_run():
                    return

                row = await self._run_point(point)
                if row is None or not self._can_run():
                    return
                self._rows.append(row)

            if self._can_run():
                self.ctrl.stopTest()

        except asyncio.CancelledError:
            return
        except Exception as exc:
            if self._can_run():
                self.ctrl.stopTest(
                    f"Point test failed: {exc}"
                )

    async def _run_point(self, point: dict):
        test_phys = point.get("static")
        if not isinstance(test_phys, dict) or not test_phys:
            raise ValueError("point.static is required")

        self._hold_ms = int(
            point.get("holdMs", 1000)
        )
        if not 1 <= self._hold_ms <= 3_600_000:
            raise ValueError(
                "point.holdMs must be in 1..3600000"
            )

        test_node = ApiNodeData(
            mode=1,
            base=self.physDictToReg(test_phys),
            timeoutMs=self._hold_ms,
            timeoutId=NODE_TIMEOUT,
        )

        if self._trip_mask:
            test_node.diMatchMask = (
                self._trip_mask | DI_REF_ENTRY
            )
            test_node.diMatchId = NODE_TRIP

        self._phase = "WAIT_TEST"
        self._entry_ts = None
        self._trip_ts = None
        self._di_at_entry = self.ctrl.di
        self._point_exit = None
        self._point_done.clear()

        if not self.ctrl.upsertNodes({
            NODE_TEST: test_node,
        }):
            return None

        if not self.ctrl.trigNode(NODE_TEST):
            return None

        try:
            await asyncio.wait_for(
                self._point_done.wait(),
                timeout=self._hold_ms / 1000.0 + 2.0,
            )
        except asyncio.TimeoutError:
            # 此时无法确认 FPGA 位于哪个节点，不能继续下一点。
            if self._can_run():
                self.ctrl.stopTest(
                    "Point completion watchdog timeout"
                )
            return None

        action_ms = None
        if (
            self._point_exit == "trip"
            and self._entry_ts is not None
            and self._trip_ts is not None
        ):
            elapsed_us = (
                (self._trip_ts - self._entry_ts)
                & 0xFFFFFFFF
            )
            max_valid_us = (
                self._hold_ms + 1000
            ) * 1000
            if elapsed_us <= max_valid_us:
                action_ms = elapsed_us / 1000.0

        return {
            "id": point.get("id"),
            "result": self._point_exit,
            "actionTimeMs": (
                round(action_ms, 3)
                if action_ms is not None
                else None
            ),
        }

    def onUpdate(self, nodeId, tick, hw_ts):
        if not self._can_run() or hw_ts < 0:
            return

        self._current_node = nodeId

        if (
            nodeId == NODE_IDLE
            and tick == 0
            and self._phase == "WAIT_INITIAL_IDLE"
        ):
            self._phase = "IDLE"
            self._idle_ready.set()

        elif (
            nodeId == NODE_TEST
            and tick == 0
            and self._phase == "WAIT_TEST"
        ):
            self._phase = "TEST"
            self._entry_ts = hw_ts
            self._di_at_entry = self.ctrl.di

        elif (
            nodeId in (NODE_TRIP, NODE_TIMEOUT)
            and tick == 0
            and self._phase == "TEST"
        ):
            self._point_exit = (
                "trip"
                if nodeId == NODE_TRIP
                else "no_trip"
            )
            self._phase = "RETURNING_IDLE"
            if not self.ctrl.trigNode(NODE_IDLE):
                return

        elif (
            nodeId == NODE_IDLE
            and tick == 0
            and self._phase == "RETURNING_IDLE"
        ):
            self._phase = "IDLE"
            self._point_done.set()

    def onDi(self, di, hw_ts):
        if (
            self._can_run()
            and self._phase == "TEST"
            and self._current_node == NODE_TEST
            and self._entry_ts is not None
            and self._trip_mask
            and self._trip_ts is None
            and (
                (di ^ self._di_at_entry)
                & self._trip_mask
            )
        ):
            self._trip_ts = hw_ts

    def _onStop(self):
        self._stopping = True
        self._idle_ready.set()
        self._point_done.set()

        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

        self.ctrl.sendReport({
            "rows": self._rows,
            "summary": {
                "count": len(self._rows),
            },
        })
```

这个模板刻意使用不同的 `NODE_TRIP` 和 `NODE_TIMEOUT`，由目标节点的有效
`onUpdate()` 确认真实出口；不能让 DI 和 timeout 都直接进入 FFFF，再根据某个恰好到达
的 `onDi()` 猜测结束原因。模板只在 `_onStop()` 发送一次全量报告，避免增量 rows 与
最终 rows 的重复语义。

## 19. 前端消息和报告

### 19.1 start 消息

统一外层：

```json
{
  "cmd": "start",
  "module": "my_test",
  "params": {
    "debounce": 20
  }
}
```

`params.debounce` 由 `EngineCtrl` 读取，其余 params 由 API 定义。

`module` 用于创建 API 实例；API 的 `_onSetup()` 只收到 `params`，不收到外层 start
消息。`sendReport()/sendError()/sendValueUpdate()` 发出的消息会由控制器自动补上当前
`module`。

### 19.2 实验专属命令

```json
{
  "cmd": "next_node",
  "module": "my_test"
}
```

公共命令以外的消息会进入当前 API 的 `onWebCommand()`。

当前控制器把未识别命令直接交给“当前 active API”，不会使用消息中的 `module` 再做
一次路由过滤。因此 API 必须校验 `cmd`、参数和允许执行的 phase；不要只依赖前端携带
正确 module。

### 19.3 report

API 相关的前端消息对应关系：

| 产生位置 | 前端 `type` | 主要字段 |
|---|---|---|
| `sendReport(data)` | `report` | `data` |
| `sendError(msg)` | `error` | `msg` |
| `sendValueUpdate(...)` | `value_update` | `static` |
| DI/DO 自动上报 | `value_update` | `di` 或 `do` |
| 正常终止清理 | `stop` | 无实验数据 |

控制器会为这些消息补上当前 `module`。

推荐：

```json
{
  "type": "report",
  "module": "my_test",
  "data": {
    "rows": [
      {
        "id": 1,
        "result": "trip",
        "actionTimeMs": 32.418
      }
    ],
    "summary": {
      "count": 1
    }
  }
}
```

规则：

- 只使用 JSON 可序列化类型；
- 无结果使用 `None`；
- 不发送 NaN/Infinity；
- 内部计算保留精度，在报告边界 round；
- 字段单位写进 API 文档；
- `partial`、`final` 等不是 EngineCtrl 内建协议字段；
- 多点实验必须在“只发最终全量”“增量追加”“最终全量替换”中明确选择一种，并与
  前端约定，不能同时发送重复 rows 却不定义合并规则；
- `sendReport()` 不会自动停止实验；
- `sendReport()` 只把发送任务排入事件循环，没有前端投递确认返回值；
- `stopTest()` 正常收到 FFFF terminal update 后才调用 `_onStop()`。

## 20. API 常见错误

### 20.1 把前端浮点字典直接放入 node

错误：

```python
node.base = params["statics"]
```

正确：

```python
node.base = self.physDictToReg(
    params["statics"]
)
```

### 20.2 step 没用 `is_delta=True`

错误：

```python
node.steps = [
    self.physDictToReg(step_phys)
]
```

正确：

```python
node.steps = [
    self.physDictToReg(
        step_phys,
        is_delta=True,
    )
]
```

### 20.3 把 layer0 当基波

错误：

```text
layer0 = [基波幅值, 相位]
```

正确：

```text
layer0 = [DC, 基频]
layer1 = [基波峰值, 相位]
```

### 20.4 写入节点 0/FFFF

错误：

```python
self.ctrl.upsertNodes({
    0: node,
    0xFFFF: end_node,
})
```

正确：

```python
self.ctrl.upsertNodes({
    1: node,
})
node.timeoutId = 0xFFFF
```

### 20.5 用 Node0 做循环复归

Node0 会清理普通节点。循环实验应创建自己的 RESET/IDLE 节点。

### 20.6 不检查返回值

错误：

```python
self.ctrl.upsertNodes(nodes)
self.ctrl.trigNode(1)
```

推荐：

```python
if not self.ctrl.upsertNodes(nodes):
    return
if not self.ctrl.trigNode(1):
    return
```

### 20.7 在回调里阻塞

不要：

```python
time.sleep(1)
```

需要等待时创建异步任务并使用：

```python
await asyncio.sleep(1)
```

### 20.8 把回调定义为 async

当前 EngineCtrl 不 await API 回调。保持回调为普通 `def`。

### 20.9 在 trigNode 后立即假定输出生效

以目标节点 `hw_ts>=0` 的 `onUpdate()` 为实际生效点。`hw_ts=-1` 是通信错误，不可
用于计时。

### 20.10 `_onStop()` 因 isActive=False 直接返回

`_onStop()` 被调用前 `isActive` 已经置 False。它仍需取消任务和发送最终报告。

### 20.11 Mode3 使用 doActions

真正进入 Mode3 后不执行普通 doActions，应使用 resetDo。若缺少非空 steps 或
`resetDo`，节点会退化为 Static，此时 doActions 又会执行；API 应在构造前拒绝这种
字段组合。

### 20.12 忘记硬件时间戳回绕

时间差始终使用：

```python
(end - start) & 0xFFFFFFFF
```

### 20.13 只检查 `isActive`

`stopTest()` 到 terminal update 之间，`isActive` 仍可能为 True，但 `ctrl.running`
已经是 False。运行回调和 await 后通常同时检查两者。

### 20.14 连续快速 `trigNode()`

manual target 只有一个槽，后写会覆盖尚未消费的前写。每次 trigger 后等待目标节点的
有效 `onUpdate()`，再推进下一节点。

### 20.15 用固定 sleep 当同步

`await asyncio.sleep(0.05)` 既不是 upsert ACK，也不是波形生效确认。节点执行确认只能
来自目标 `onUpdate()`；软件 watchdog 超时后应停止实验，不能在未知输出状态继续下一点。

### 20.16 DI 和 timeout 共用同一结果节点

如果报告必须区分动作和超时，应让两个出口进入不同普通结果节点，再根据结果节点
`onUpdate()` 确认原因。不要让两者都直接进入 FFFF 后仅凭 `onDi()` 的到达时机猜测。

## 21. 每个 API 应配套的设计说明

建议每个新 API 在 `docs/api/` 下提供：

```markdown
# ApiMyTest

## 1. 模块
- Python 类：
- MODULE_KEYS：
- 前端实验名称：

## 2. 输入 JSON
- 完整 start 示例：
- 字段类型：
- 默认值：
- 单位：
- 幅值是 RMS 还是峰值：

## 3. 物理量
- 使用通道：
- 使用 layer：
- layer0 的 DC/频率：
- layer1..64 的幅值/相位：
- base/reset 绝对量：
- steps 增量：

## 4. 节点表
| 内部ID | 名称 | mode | 输出 | DI | timeout | countOver |

明确声明不定义 0 和 FFFF。

## 5. 节点图
列出所有 timeout、DI、countOver、manual 跳转。

## 6. DI
- bit 定义：
- OR/AND：
- 实验起点参考或节点入口参考：
- 动作/返回判据：

## 7. DO
- doActions：
- resetDo：
- DO 时间原点：

## 8. 回调
- onUpdate 状态：
- onDi 状态：
- 硬件时间戳：
- 异步 Event：

## 9. 前端运行命令
| cmd | 参数 | 允许阶段 | 行为 |

## 10. 停止
- 正常完成：
- DI：
- timeout：
- 用户停止：
- 参数错误：

## 11. 报告
- JSON：
- 字段单位：
- None 含义：
- round 精度：

## 12. 异步任务
- 创建位置：
- isActive/ctrl.running 检查：
- _onStop 取消：
```

## 22. 新 API 开发检查表

### 类和生命周期

- [ ] 文件名为 `ApiXxx.py`
- [ ] 继承 `BaseApi`
- [ ] `MODULE_KEYS` 唯一
- [ ] 加入 `Registry.py`
- [ ] `__init__()` 初始化所有回调字段
- [ ] 回调是同步 `def`
- [ ] 回调自行捕获异常且不阻塞
- [ ] 异步 task 在 `_onStop()` 取消
- [ ] 每次 await 后检查 `isActive` 和 `ctrl.running`
- [ ] 取消后的 task 不再修改 rows 或发送报告

### 数据

- [ ] 前端 key 按字符串处理
- [ ] 幅值口径明确为 RMS 或峰值
- [ ] layer0 为 DC+频率
- [ ] layer1 为基波
- [ ] layer2..64 为谐波
- [ ] base/reset 使用绝对转换
- [ ] steps 使用 `is_delta=True`
- [ ] 每个 step 通道/层已有明确的绝对 base 状态
- [ ] `ApiNodeData` 中是寄存器字典

### 节点

- [ ] 所有 upsert key 位于 1..FFFE
- [ ] 没有写入 0
- [ ] 没有写入 FFFF
- [ ] 普通循环不跳 Node0
- [ ] 主动结束使用 stopTest，不用 trigNode(FFFF)
- [ ] 所有目标 ID 存在或为 FFFF
- [ ] stub/IDLE ID 不冲突
- [ ] upsert 返回值已检查
- [ ] trig 返回值已检查
- [ ] 每次 manual trigger 后等待目标节点确认

### Mode

- [ ] Mode1 有预期出口或明确手动控制
- [ ] Mode2 有非空 steps 和 interval
- [ ] Mode2/3 的 interval 是正整数并已限制范围
- [ ] Mode3 有非空 steps、数值型 interval/resetTime 和非 None resetDo
- [ ] 独立 Mode3 已显式提供所需 base/reset
- [ ] Mode3 不依赖 doActions
- [ ] 未使用 Mode4
- [ ] 最后一个 interval 已计入总时长

### DI/DO/时间

- [ ] diMatchMask 低8位正确
- [ ] OR/AND 正确
- [ ] diStart/diPrev 参考正确
- [ ] 0x400 被当作反转参考的电平条件，而不是返回沿记忆
- [ ] onDi 自己过滤 node/phase/mask
- [ ] DO 延时按升序
- [ ] Mode3 的 resetDo 高低位正确
- [ ] 时间差使用 U32 回绕
- [ ] 只把 hw_ts>=0 的 onUpdate 作为输出生效点
- [ ] watchdog 超时后停止或确认安全节点，不继续下一点

### 报告

- [ ] `_onStop()` 能生成最终报告
- [ ] 无结果使用 None
- [ ] 不含 NaN/Infinity
- [ ] 字段单位明确
- [ ] round 只发生在报告边界
- [ ] 分段 report 的追加/覆盖语义已与前端约定
- [ ] 最终报告只由一个明确位置发送

完成以上检查后，一个新实验通常不需要再理解 UART 或 FPGA 字节帧，即可通过
`ApiNodeData + EngineCtrl` 实现完整控制流程。
