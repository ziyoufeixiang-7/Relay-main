---
name: relay-test-api
description: >
  Guide for writing API-layer experiment code (api/Api*Test.py) for the RelayProtection
  relay protection tester. Use when implementing or modifying a test experiment — distance/impedance
  (距离/阻抗), differential (差动), overcurrent/inverse-time (过流/反时限), auto-reclose (重合闸),
  sweep/ramp search (递变搜索), slip (滑差), state-sequence (状态序列), time-measurement (时间测量) —
  or when working with EngineCtrl nodes, ApiNodeData, USEEngine waveform modes, DI/DO triggers,
  SOE action-time timing, fault phasor injection, or the channel/layer statics format.
---

# 编写继电保护测试仪 API 层实验代码

本 skill 教你如何在 `RelayProtection/api/` 下编写一个实验类（`Api*Test.py`），
**只通过 `EngineCtrl` 暴露的函数 + `calc/` 计算引擎**驱动硬件完成一项继电保护测试。

> 核心心智模型：**API 层只描述"状态机 + 每个状态输出什么相量 + 何时跳到哪个状态"，
> 然后把节点交给 EngineCtrl 编译下发。API 绝不直接碰 FPGA 帧或 USEEngine 内部。**

---

## 1. 三层架构与你的位置

```
前端(WebSocket JSON)
   │  cmd:start / stop / 自定义命令
   ▼
EngineCtrl ── 你调用它的函数 (upsertNodes/trigNode/sendReport/...)
   │  把 ApiNodeData(物理参数) 编译成 USENode(二进制帧) 下发子进程
   ▼
USEEngine(子进程, 零编译永久状态机) ── 四种波形模式, 节点跳转
   │  SYS/PARAM 帧 + ACK(含 SOE 硬件时间戳)
   ▼
FPGA(DDS 波形发生) ── 三级缓冲 Base→Shadow→Active 乒乓翻转 → DAC 输出
```

**你写的是 `BaseApi` 的子类**。它：
- 解析前端 `params`；
- 用 `calc/` 算出每个状态的 12 通道相量；
- 构造 `ApiNodeData` 节点字典，`ctrl.upsertNodes()` 下发，`ctrl.trigNode()` 启动；
- 在 `onUpdate/onDi` 回调里收集硬件 SOE 事件，算动作时间/边界；
- 用 `ctrl.sendReport()/_send()` 回传结果。

详细底层原理见仓库：`docs/fpga_protocol.md`、`docs/engine_execution_model.md`、`docs/architecture_overview.md`。

---

## 2. 你真正在控制什么：FPGA → DDS

FPGA 为 16 个物理通道各跑一个 DDS。每个通道支持 **64 个交流谐波层 + 1 个直流层**。
你给每个通道每个"层(layer)"指定 `[幅值, 频率/相位]`：

| layer 键 | 物理含义 | `[v0, v1]` |
|---|---|---|
| `"0"` | 直流层 | `[直流幅值, 基波频率Hz]` |
| `"1"` | 基波(1 次) | `[幅值, 相位°]` |
| `"2"` | 2 次谐波 | `[幅值, 相位°]` |
| `"N"` | N 次谐波 | `[幅值, 相位°]` |

- **频率**只在 layer `"0"` 的第二个元素给出；各交流谐波层频率 = 层号 × 基波频率（DDS 自动合成）。
  纯 50Hz 工频可省略 layer `"0"`，只给 layer `"1"`。
- **幅值**最终以"峰值"写入 FPGA（16 位有符号，上限 32767，越界会反相 → 必须钳位）。
  电压/电流校准、RMS↔峰值换算由 `calc/` 与 `Calibration` 处理。
- **三级缓冲**：所有写入先进 Base+Shadow，`SYS_UPDATE` 把 Shadow↔Active 乒乓翻转生效，
  保证多通道无毛刺切换。这层细节由 USEEngine 处理，你不用管。
- **SOE 时间戳**：FPGA 对每帧回 ACK 带硬件时间戳，DI 变位也带时间戳。
  **动作时间一律用 SOE，禁止用上位机软件计时。**

通道编号（API 层用 0-15 连续索引，`physDictToReg` 自动映射到物理通道）：
电压 `0,1,2`(Ua,Ub,Uc) / `3,4,5`(Ux,Uy,Uz)；电流 `6,7,8`(Ia,Ib,Ic) / `9,10,11`(Ix,Iy,Iz)。

---

## 3. USEEngine：节点状态机与四种波形

引擎是一个**永久状态机**，停在内置节点上等触发：
- `0x0000` 待机节点（开机零校准 + SYS_START）。
- `0xFFFF` 终止节点（SYS_RESET 切断输出）。

> ⚠️ **0xFFFF 语义（最重要的坑）**：任何节点的出口跳到 `0x0000`/`0xFFFF` 会触发
> **清空节点表(GC) + `_doStop()`**（结束整个实验、发 report+stop）。
> - **单次实验**（测一个值就结束）→ 出口指向 `0xFFFF` 收尾，正确。
> - **多点/多阶段实验**（连续测多个点）→ **绝不能**把"点间挂起"指向 `0xFFFF`，
>   否则第一个点后整个实验就结束。必须停在一个**真实 idle 节点**（见 §8 模式 B）。

### 四种波形模式（`ApiNodeData.mode`）

| mode | 名称 | 用途 | 关键字段 |
|---|---|---|---|
| `1` | Static 静态 | 恒定输出一段相量（正常态/故障态/挂起态） | `base` |
| `2` | Sweep 步进扫频 | 按 `interval` 每拍叠加 `steps[i]` 增量 | `base`,`steps`,`interval`,`countOverId` |
| `3` | Reset 故障-复归交替 | 故障↔复归乒乓（步间复归、多次重合） | `base`,`reset`,`steps`,`interval`,`resetTime`,`resetDo` |
| `4` | DcComp 门控直流 | 相位门控纳秒级切入衰减直流（当前禁用） | `gate`,`steps`,`gateFrames` |

### 触发与跳转（每个节点最多配这几种出口）

| 出口 | 字段 | 触发条件 |
|---|---|---|
| 超时 | `timeoutMs` → `timeoutId` | 停留超过 `timeoutMs` 毫秒 |
| DI 变位 | `diMatchMask` → `diMatchId` | 开入按掩码+边沿匹配 |
| 步进结束 | `countOverId` | Sweep/Reset 跑完所有 `steps` |
| 手动 | `ctrl.trigNode(id)` | 上位机强制跳转（优先级最高） |

引擎进入每个节点先做 `SYS_SYNC` 三区对齐，并记录 `diPrev`（本节点入口 DI）。

---

## 4. EngineCtrl：你能调用的函数

`self.ctrl` 是 `EngineCtrl` 实例。**API 层只用下面这些**：

| 调用 | 作用 |
|---|---|
| `self.ctrl.upsertNodes(nodes: Dict[int, ApiNodeData]) -> bool` | 编译并下发节点字典；返回 False 表示未运行/编译失败 |
| `self.ctrl.trigNode(nodeId: int) -> bool` | 强制跳转到某节点（启动/推进状态机） |
| `self.ctrl.stopTest(reason: str=None)` | 主动结束整个实验（→ `0xFFFF` → 发 stop；带 reason 则发 error） |
| `self.ctrl.sendReport(data: dict)` | 发送 `{"type":"report","data":data}` 结果报告 |
| `self.ctrl.sendValueUpdate(targetDict, src=None)` | 发送 value_update（自动 RegToPhys 反算物理值） |
| `self.ctrl._send(data: dict)` | 底层发送任意消息（自动补 `module` 字段） |
| `self.ctrl.di` / `self.ctrl.do` | 当前 DI / DO 字节（只读快照） |

> ⚠️ **没有 `sendMsg` 方法**！要发自定义 `value_update`/进度消息，用 `self.ctrl._send({...})`。
> （历史 bug：误用 `ctrl.sendMsg` 在真机崩溃。）

---

## 5. BaseApi：你要实现的接口

```python
from api.BaseApi import BaseApi, ApiNodeData

class ApiFooTest(BaseApi):
    MODULE_KEY = "foo_test"           # 或 MODULE_KEYS = ["a_test","b_test"] 多模块共用

    def _onSetup(self, params):       # 必须实现：解析 params，搭建并启动状态机
        ...
    def onUpdate(self, nodeId, tick, hw_ts):   # 可选：节点进入/步进回调
        ...
    def onDi(self, di, hw_ts):                  # 可选：开入变位回调（含 SOE 时间戳）
        ...
    def onWebCommand(self, msg):                # 可选：start/stop 之外的前端命令
        ...
    def _onStop(self):                          # 可选：清理（取消后台任务）
        ...
```

框架提供：
- `self.ctrl`：EngineCtrl；`self.isActive`：实验是否在运行（每个 await 后检查它，失活就 return）。
- `self.physDictToReg(phys_dict, is_delta=False)`：把物理相量字典编译成 `ApiNodeData.base/reset/steps` 用的寄存器字典。
- 不要重写 `setup()`/`onStop()`（框架调用它们，再转调你的 `_onSetup`/`_onStop`）。

**回调语义**：
- `onUpdate(nodeId, tick, hw_ts)`：`tick=0` 节点入口(base 生效)；`tick=-1` 复归相(mode3)；`tick≥1` 第 N 步。`hw_ts`=硬件 SOE。
- `onDi(di, hw_ts)`：`di`=新的 8 位开入；`hw_ts`=变位的硬件 SOE（已做防抖补偿）。

**生命周期**：`startTest` → 引擎进 `0x0000` 待机、放大器开 → 调你的 `_onSetup(params)`。
此时引擎刚进待机，**必须先预热**（`await asyncio.sleep(0.3~0.5)`）再 `upsertNodes`+`trigNode`。
通常在 `_onSetup` 末尾 `asyncio.create_task(self._run())` 开一个协程做预热与逐点循环。

---

## 6. ApiNodeData：节点字段全表

```python
n = ApiNodeData(mode=1)
n.base       = self.physDictToReg(phys_dict)       # 该节点输出的相量（必填，除非纯挂起）
n.reset      = self.physDictToReg(reset_phys)      # mode3 复归相
n.steps      = [self.physDictToReg(d, is_delta=True) for d in deltas]  # mode2/3 每拍增量
n.interval   = 1000                                # mode2/3 步间隔(ms)
n.resetTime  = 100                                 # mode3 复归相时长(ms)
n.resetDo    = (exitDo << 8) | enterDo             # mode3 故障/复归 DO
n.doActions  = [(delayMs << 8) | doByte, ...]      # DO 时序(相对节点入口)
n.timeoutMs, n.timeoutId = 1000, 2                 # 超时 → 跳 2
n.diMatchMask, n.diMatchId = mask | 0x200, 3       # DI 边沿匹配 → 跳 3
n.countOverId = 0xFFFF                             # 步进跑完 → 结束
```

- `base/reset/steps` 必须是 `physDictToReg(...)` 的输出（寄存器字典），不是裸物理字典。
- 出口字段都可缺省（缺省即该出口不存在）。Static 节点若无任何出口 → 永久挂起等 `trigNode`。

---

## 7. 相量数据格式与计算引擎助手

**物理相量字典**（喂给 `physDictToReg`）：
```python
phys = {
  "0": {"1": [57.735, 0.0]},      # Ua 基波 57.735V ∠0°
  "6": {"1": [5.0, -75.0],        # Ia 基波 5A ∠-75°
        "2": [1.0, 0.0]},          # Ia 叠加 2 次谐波 1A
}
```

线路保护/阻抗类可直接用 `calc/calculation_engine.py`（已封装故障相量计算）：
```python
from calc.calculation_engine import calculate_fault, calculate_normal, calculate_open, pack_channels
V, I = calculate_fault(ft, sys_v, iload_amp, iload_ang, dir_val, i_f, z_mag, z_ang,
                       formula, p1, p2, ct_val)      # 10 种故障类型 → (Va,Vb,Vc),(Ia,Ib,Ic) RMS 复数
phys = pack_channels(V, I, vMode=0, iMode=0, ux_cfg={"en":False}, f=50.0)  # → 12 通道物理字典(含×√2峰值)
n.base = self.physDictToReg(phys)
```
差动类用 `calc/diff_bridge.py` 的 `compute_6ch/compute_2i`。

---

## 8. 五大经典模式（代码骨架）

### A. 单次值测量（递变搜索，出口可直接 0xFFFF）
正向递变 → DI 跳闸即结束。见 `api/ApiSweepTest.py`。
```python
n = ApiNodeData(mode=2)
n.base = self.physDictToReg(statics); n.steps=[step]*count; n.interval=stepTime
n.diMatchMask, n.diMatchId = logicMask, 0xFFFF   # DI 跳闸 → 结束(单次)
n.countOverId = 0xFFFF                            # 跑完未跳 → 结束
```

### B. 多点连续测试（idle 节点 + asyncio.Event）★最常用
点间停在**真实 idle 节点**，用事件等待每点结束。见 `api/ApiImpedanceTest.py`、`ApiDifferentialTest.py`。
```python
IDLE = 4
async def _run(self):
    await asyncio.sleep(0.3)                       # 预热
    self.ctrl.upsertNodes({IDLE: self._build_idle()})   # 常驻挂起节点(无出口)
    for pt in self._points:
        if not self.isActive: return
        await self._test_point(pt)
    if self.isActive: self.ctrl.stopTest()        # 全部测完才结束

async def _test_point(self, pt):
    fault = ApiNodeData(mode=1); fault.base = ...
    fault.timeoutMs, fault.timeoutId = pt["time"], IDLE   # 超时回 IDLE(不是 0xFFFF!)
    fault.diMatchMask, fault.diMatchId = self._trip|0x200, IDLE  # 跳闸回 IDLE
    self.ctrl.upsertNodes({FAULT: fault})
    self._done.clear()
    self.ctrl.trigNode(PREFAULT or FAULT)
    await asyncio.wait_for(self._done.wait(), timeout=...)

def onUpdate(self, nodeId, tick, hw_ts):
    if nodeId == IDLE and tick == 0:
        self._done.set()                          # 回到 IDLE = 本点结束
```

### C. 二分边界搜索
沿一条线段二分逼近"动作/不动作"翻转点。注意**单调方向**：
过量类（过流/差动）越大越动作 → `tripped: hi=mid`；
谐波闭锁/欠量类越大越闭锁/越不动作 → 方向相反 `tripped: lo=mid`。
```python
while (hi - lo) > precision:
    mid = (lo+hi)/2
    tripped = await self._test_point(mid)
    if tripped: hi = mid          # 找最小动作值
    else:       lo = mid
```

### D. 故障前 → 故障 → 测动作时间（SOE）
```python
def onUpdate(self, nodeId, tick, hw_ts):
    if nodeId == FAULT and tick == 0:
        self._t_fault = hw_ts; self._di_base = self.ctrl.di   # 记录故障施加时刻+DI基线
def onDi(self, di, hw_ts):
    if self._cur == FAULT and (di ^ self._di_base) & self._trip:
        self._action_ms = (hw_ts - self._t_fault) / 327.68    # SOE → ms
```

### E. 分支状态机（线路保护/重合闸）
NORMAL→FAULT→(CB跳闸延时)→CBOPEN→(重合)→RECLOSE→... 多节点、DO 模拟断路器辅助接点。
见 `api/ApiLineProtectionTest.py`。

---

## 9. DI 触发与边沿检测

`diMatchMask` = 通道掩码(低 8 位) **|** 行为标志位：

| 标志 | 含义 |
|---|---|
| `0x100` | AND 逻辑（所有掩码位都变位才触发）；缺省=OR（任一变位） |
| `0x200` | 参考基准取**本节点入口** DI（即"本节点内的变位"，边沿检测）；缺省=取**实验开始**时 DI |
| `0x400` | 极性反转（适配常闭接点） |

引擎判据：`(diNow ^ diRef) & mask`，边沿匹配。
**几乎所有"检测本状态内是否跳闸"都应 OR 上 `0x200`**，否则会拿全程基准误判。

在 `onDi` 里自己算动作相别时，同样要用"故障入口的 DI 基线"做异或，见 §8-D。

---

## 10. DO 输出（开出）

- `n.doActions = [(delayMs<<8)|doByte, ...]`：进入节点后第 `delayMs` ms 把整字节 DO 设为 `doByte`（绝对值）。
- **跨节点保持**：只有带 `doActions` 的节点才改 DO，不带的节点维持上一段。
  → 想"断路器断开后保持到重合"，在 CBOPEN 节点设断开值、在 RECLOSE 节点设闭合值，中间节点不设。
- mode3 用 `resetDo=(exitDo<<8)|enterDo` 在故障/复归相自动切换 DO。

---

## 11. 写一个新实验的步骤清单

1. **读懂实验**：它测什么物理量（动作值/时间/边界/返回系数）？前端给哪些控制变量？
   先看 `docs/api/` 对应接口文档与 `docs/experiment-implementation-logic/` 理论文档。
2. **建类**：`api/ApiXxxTest.py`，`class ApiXxxTest(BaseApi)`，设 `MODULE_KEY`/`MODULE_KEYS`。
3. **`_onSetup`**：解析 `params`（sys/payload/points...）；存配置；`asyncio.create_task(self._run())`。
4. **算相量**：每个状态用 `calc/` 或自写公式 → 物理字典 → `physDictToReg`。
5. **搭节点**：选 mode；配 `base/steps/...` 与出口（timeout/diMatch/countOver）；
   选模式 A（单次→0xFFFF）或 B（多点→IDLE+Event）。
6. **回调**：`onUpdate` 记录入口/完成；`onDi` 判跳闸/算 SOE 动作时间。
7. **上报**：逐点 `ctrl._send({"type":"value_update",...})`，结束 `ctrl.sendReport({...})`。
8. **收尾**：单次→出口 0xFFFF；多点→全部跑完 `ctrl.stopTest()`。`_onStop` 取消后台任务。
9. **写测试**：用 mock ctrl（§13）跑通端到端。

---

## 12. 黄金法则与常见坑

1. 🔴 **0xFFFF 只用于结束整个实验**。多点测试点间停 IDLE 真实节点，否则第一个点后全停。
2. 🔴 **动作时间用硬件 SOE**（`hw_ts`），不用 `time`/软件计时。
3. 🔴 **边沿检测 OR `0x200`**，并在 `onDi` 用故障入口 DI 基线异或。
4. 🔴 **没有 `ctrl.sendMsg`**：自定义消息用 `ctrl._send`。
5. 🔴 **每个 `await` 后检查 `self.isActive`**，失活立即 return（实验可能被中途停止）。
6. ⚠️ **预热**：`_onSetup` 后先 `sleep(0.3~0.5)` 再下发节点+触发。
7. ⚠️ **幅值钳位**：递变/步进累加结果要 ≤ `limits`，防 AC 幅值越 32767 反相。
8. ⚠️ **base 必须是 `physDictToReg` 输出**，不是裸物理字典。
9. ⚠️ **谐波层**：layer `"N"` = N 次谐波（layer "2" 即 2 次）；频率由 layer "0" 的基频派生。
10. ⚠️ **RMS↔峰值**：注意你的物理量是 RMS 还是峰值；`pack_channels` 已做 ×√2，自写注入要自行处理并与上报口径一致。

---

## 13. 单元测试模式（mock EngineCtrl）

用一个假的 ctrl 复刻引擎语义（**含真实 0xFFFF=停止**），按节点图自走、注入 DI、断言报告：
见 `templates/test_example.py` 与现有 `tests/test_diff_harmonic.py`、`tests/test_ApiImpedanceTest.py`。
要点：mock 的 `_walk` 沿 `timeout/diMatch` 出口走图；遇到不存在的节点或 0xFFFF 视为 `_doStop`；
`onUpdate(IDLE,0)` 置位完成事件；用硬件时间戳推进算动作时间。

---

## 14. 参考文件

- 骨架模板：`templates/ApiExampleTest.py`（多点+二分+SOE 的完整可改骨架）、`templates/test_example.py`。
- 底层文档：`docs/fpga_protocol.md`、`docs/engine_execution_model.md`、`docs/architecture_overview.md`。
- 实验理论：`docs/experiment-implementation-logic/`（阻抗/线路保护/差动/递变）。
- 现成范例：`api/ApiImpedanceTest.py`(多点二分+SOE)、`api/ApiLineProtectionTest.py`(分支状态机+DO)、
  `api/ApiDifferentialTest.py`(6I/2I+谐波)、`api/ApiSweepTest.py`(递变+返回系数)。
- 计算引擎：`calc/calculation_engine.py`、`calc/diff_bridge.py`。
