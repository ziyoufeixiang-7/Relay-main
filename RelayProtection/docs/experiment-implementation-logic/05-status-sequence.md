# 状态序列试验 — 实现逻辑、代码映射与审计

> 模块标识：`universal_sequence`（状态序列器 / State Sequencer）
> 被测对象：任意"按一连串确定工况依次推进"的保护逻辑与时序（故障前→故障→故障后、
> 多次重合闸、跨态配合校验等）。线路保护 `lp_test` 即建在同一引擎之上。
>
> 本篇在 01–04 论文式模板基础上**额外扩展两节**：§8 引擎/代码实现映射、§9 协议与代码审计。
> 依据：继保测试原理 + `docs/api/api_status_sequence.md` 前端契约 + 实际代码
> （`api/ApiStatSequ.py`、`logic/EngineCtrl.py`、`logic/USEEngine.py`）+ 旧实现
> （`Temp/oldrelay/api/StatusSeqHdl.py`）。

---

## 1. 摘要

状态序列试验把测试仪当作一台**可编程工况发生器**：前端用一张**状态图**（若干 node）描述
"先输出什么、停多久、满足什么条件后跳到哪个状态"，测试仪据此依次施加各态的电压/电流，
并精确记录**每个状态停留多久、由什么原因退出、各路开入（DI）相对进入该态分别在何时变位**。

它是最通用的试验骨架：阻抗、反时限、整组等专用试验本质上都是"特化的状态序列"。专用 API
把继保参数算成相量后下发；状态序列则把"图"直接交给前端编排，因此最灵活、也最依赖引擎的
节点跳转语义正确。

---

## 2. 原理背景

继电保护的动作行为往往**依赖前序状态与时序**，而非单点稳态。例如：

- **带方向/记忆的距离元件**：需要"正常负荷 → 故障"这一过渡才能正确判别；
- **重合闸**：跳闸 → 去游离 → 重合 → 永跳，是一串状态；
- **后加速、振荡闭锁、TV 断线**：都要求按确定时序复现一连串工况。

状态序列试验的价值在于：用一张确定的图复现这些工况链，并对**每一段**给出客观度量
（停留时间、退出原因、各开入翻转时刻），从而验证保护的**时序与逻辑**是否符合定值。

---

## 3. 试验目的与判据

| 目的 | 度量 | 判据 |
|---|---|---|
| 验证状态按设定条件正确跳转 | 每态 `end_condition`（DI_MATCH/TIME_OUT/COUNT_OVER/MANUAL_TRIG/TERMINAL） | 跳转原因与设计一致 |
| 验证保护各级动作时间 | 每态 `duration_sec`（硬件 SOE 时间戳之差） | 与定值时间窗一致 |
| 验证开入响应时刻 | `diA..dir`（相对进态的首次变位时刻，秒） | 在允许范围内 |

判据由前端/上位机根据回报的 `rows` 评定；后端只负责**确定性复现 + 精确度量 + 如实上报**。

---

## 4. 前端控制变量（`params`）

### 4.1 `sys`
- `debounce`：DI 去抖窗口（ms），滤除开入抖动。**置于顶层 `params.debounce`**（见 §9.3）。

### 4.2 `nodes`（状态数组）
每个 node 是一段工况：

| 字段 | 含义 |
|---|---|
| `id` | 前端节点号（**1 起**，见 §8.1 为何不可用 0）。 |
| `mode` | `STATIC`（恒定输出）/ `SWEEP`（阶梯递变输出）。 |
| `static` | 进入该态的基准输出，`{通道:{层:[幅值, 频率或相位]}}`。 |
| `dynamic` | 仅 `SWEEP`：`stepTime`(ms)、`count`(步数)、`steps`(每步增量)。 |
| `do_actions` | 进态时的继电器输出动作 `[{delay(ms), mask}]`。 |
| `triggers` | 出态规则数组，**按序求值，首个命中者执行跳转**。 |

### 4.3 `triggers`（出态条件）

| 字段 | 含义 |
|---|---|
| `condition` | `DI_MATCH` / `TIME_OUT` / `COUNT_OVER` / `MANUAL_TRIG`。 |
| `next_id` | 命中后跳往的 node `id`；`-2` 表示终止（→ 引擎 `0xFFFF`）。 |
| `di_mask` | （DI_MATCH）监视的开入通道位掩码。 |
| `timeout` | （TIME_OUT）超时 ms；与 DI_MATCH 并列时作为**安全超时**兜底。 |
| `post_condition_delay_ms` | 条件命中后、真正跳转前的保持时延（经"桩节点"实现，§8.4）。 |

---

## 5. 数学模型（控制变量 → 注入相量）

### 5.1 通道/层 → 物理输出

每个 DDS 通道分层叠加：

- **层 0（基波层）**：`[幅值, 频率Hz]` —— 决定该通道的基波正弦；
- **层 ≥1（附加层）**：`[幅值, 相位°]` —— 叠加的谐波/特定相位分量。

设通道 $c$ 第 $k$ 层为 $(A_{c,k}, \theta_{c,k}/f_{c,k})$，则该通道瞬时输出

$$
x_c(t)=A_{c,0}\sin(2\pi f_{c,0}t)+\sum_{k\ge1}A_{c,k}\sin\!\big(2\pi f_{c,0}\,h_k\,t+\theta_{c,k}\big).
$$

物理值经**标定**（`calib.PhysToReg`：层 0 绝对增益+偏置、层 ≥1 增量增益）转为寄存器值。
> 注意：状态序列前端送的就是**幅值**（非有效值×√2），故转换中不额外乘 √2（见 §8.2、§9）。

### 5.2 SWEEP 阶梯递变

`SWEEP` 态第 $n$ 步输出 $u_n=u_0+n\cdot\Delta u$（$u_0$=`static`，$\Delta u$=`steps`，
$n=0..\text{count}$），每步驻留 `stepTime`。其引擎实现与 04《递变搜索》完全同源（Mode 2）。

### 5.3 DI 变位判据（StatusSeq 口径）

引擎对每个节点用 `diMatchMask` 评估变位。状态序列下发**裸通道掩码**（不加 0x100/0x200/0x400
标志位），等价于：

$$
\text{match}=\big[(\,d_{\text{now}}\oplus d_{\text{start}})\ \&\ \text{mask}\big]>0,
$$

即"相对**序列起始态** $d_{\text{start}}$、被监视位发生任意（OR）变位即命中"。这与旧
`StatusSeqHdl` 一致（旧 `ApiHdlMgr` 打包 `union=diMask` 不加标志位）。
> 对比：反时限/滑差用 `mask|0x200`（以**进入故障节点**时的 DI 为基准），语义不同——见各自文档。

### 5.4 时间度量

- **停留时间**：$\text{duration}_i=(\text{ts}^{\text{entry}}_{i+1}-\text{ts}^{\text{entry}}_i)\ \&\ \mathtt{0xFFFFFFFF}$（µs），末态用最后一次遥测 ts。
- **DI 翻转时刻**：$\text{di}_{\text{ch}}=(\text{ts}^{\text{di}}-\text{ts}^{\text{entry}}_i)/10^6$（s），仅记每通道**首次**变位。
所有时间戳来自 FPGA SOE，硬件级精度。

---

## 6. 试验时序与状态图

```
0x0000(校准/待机) ──trigNode(1)──► [node1] ──trig──► [node2] ──...──► (-2 → 0xFFFF 终止)
                                       │ post_delay>0
                                       ▼
                                  [stub(1000+)]──TIME_OUT(delay)──► next
```

- 引擎从内置 `0x0000` 校准节点出发，`ApiStatSequ.setup` 上传节点后 `trigNode(1)` 启动；
- 每态按 triggers 求值跳转；`MANUAL_TRIG` 由上位机 `{"cmd":"next_node"}` 驱动；
- `post_condition_delay_ms>0` 时，条件命中先跳到一个**静态桩节点**保持 `delay` 后再跳真实目标；
- 跳到 `-2`（→`0xFFFF`）终止 → `EngineCtrl._doStop` → `onStop` 出报告。

---

## 7. 测量与上报（SOE）

### 7.1 过程遥测
进入每态时推送 `{type:value_update, nodeId, static}`（物理反算值）；DI/DO 变化由 `EngineCtrl`
分别以 `{value_update, di}` / `{value_update, do}` 单独推送（见 §9 文档勘误）。

### 7.2 最终报告
```json
{"type":"report","module":"universal_sequence","data":{"rows":[
  {"id":1,"duration_sec":2.000123,"end_condition":"TIME_OUT","diA":null,...,"dir":null},
  {"id":2,"duration_sec":0.453000,"end_condition":"DI_MATCH","diA":0.451000,...}
]}}
```
- `duration_sec`：该态停留时间；
- `end_condition`：退出原因（**推断**所得，见 §8.3 与 §9 局限）；
- `diA..dir`：8 路开入相对进态的首次变位时刻，`null`=该态内未变位。

---

## 8. 引擎/代码实现映射

### 8.1 节点编号约定（关键）
引擎保留 `0x0000`=校准/待机、`0xFFFF`=终止；且 `coreLoop` 在 `nextId∈{0x0000,0xFFFF}` 时
**把节点表剪枝**为仅剩这两个（`engine_execution_model.md` §4.2）。因此：

- 实验节点**必须从 1 开始**编号，`trigNode(1)` 启动（`ApiStatSequ._onSetup` 末尾）。
- 任何"中途跳到 0"的设计（如旧滑差回环到 node0）在新引擎都会误触发剪枝。

映射代码：`frontToEngineId[front_id]=i+1`、`engineToFrontId[i+1]=front_id`。

### 8.2 `ApiNodeData` 字段 → 物理输出
`base`/`steps`（物理值）经 `BaseApi.physDictToReg`（标定 + `MapChannel` 逻辑通道→硬件通道，
**幅值不乘 √2**）→ `EngineCtrl._compileDictToFrames` → DDS 帧：

| 字段 | 帧类型 | 物理含义 |
|---|---|---|
| `base`(=`static`) | `DDS_WR_SHADOW` | 进态稳态波形（写 Base+Shadow） |
| `steps` | `DDS_STEP_SHADOW` 二维 | 每步增量（SWEEP） |

`_compileDictToFrames` 以 `1<<ch` 组帧——**要求通道键为 int**（见 §9 P1 修复）。

### 8.3 跳转逻辑
`ApiNodeData` 的 `timeoutId`/`diMatchId`/`countOverId` 即三类自动出口目标；`diMatchMask`
为裸掩码（§5.3）；`MANUAL_TRIG` 不进引擎触发器，由 `onWebCommand → trigNode(target)` 驱动。

新引擎**不回报"哪个触发器命中"**（仅有 value_update/di_change/do_change 三种事件），故
`ApiStatSequ` 用**推断**重建 `end_condition`：`onUpdate` 检测到节点切换时，依
`manualTrigFired`→`diMatchedThisNode`→(`countOverId` 启发式)→`TIME_OUT` 顺序判定离开前一态的原因。
（这是新架构相对旧架构的本质差异：旧引擎 `EvtTrigger` 直接带 cond，新引擎须推断。）

### 8.4 API 如何驱动 EngineCtrl ↔ USEEngine
- `setup()` → `upsertNodes(engine_nodes)`（含 stub）+ `trigNode(1)`；
- `onUpdate(nodeId,tick,ts)`：转移检测/`end_condition` 推断/`currentTracker` 切换/记录进态
  DI 基准/推送遥测；遇 stub 节点直接 `return`（跳过遥测与推断，避免被 stub 覆盖）；
- `onDi(di,ts)`：累计各通道首次变位时刻 `di_times`，并按进态基准置位 `diMatchedThisNode`；
- `onWebCommand({cmd:next_node})`：若当前态有 MANUAL_TRIG 目标则 `trigNode`；
- `onStop()`：算 `duration_sec`、组 `rows`、`sendReport`。
- EngineCtrl 侧：`manualTrig` 驱动跳转；节点跳 `0xFFFF` → `_doStop` → `onStop`；value_update
  按 50ms 节流推送。

### 8.5 桩节点（post_condition_delay_ms）
`post_delay>0` 时为该触发器生成唯一桩 `1000+stub_seq`（静态、`TIME_OUT(delay)→真实 next`），
原触发器目标改指向桩。桩 id ≥1000 不与实验节点（1..N）或保留节点冲突。

---

## 9. 协议与代码审计（bug 清单与结论）

### 9.1 已修复（PR #4，作为佐证）
| 编号 | 问题 | 根因 | 修复 |
|---|---|---|---|
| P0 | 多状态序列只跑第一个节点即终止 | 节点 0 与 `0x0000` 冲突 + `trigNode(0)` 触发剪枝 | 实验节点从 1 编号、`trigNode(1)` |
| P1 | 引擎编译节点即崩溃 | `_FillMissingChannels` 在 `physDictToReg` 后插 **str** 通道键，`1<<ch` 报错 | 改用同一 `physDictToReg` 补齐（int 键） |
| P2 | DI_MATCH 进门瞬间误触发/语义偏移 | 误用 `\|0x400`（电平取反 vs diStart） | 改回**裸掩码**（diStart 基准），与旧实现一致 |

### 9.2 文档勘误（`docs/api/api_status_sequence.md`）
- **`next_id` 节点级 fallback**：文档称"无 triggers 时的默认跳转目标"，但新旧实现都**只读
  trigger 级 `next_id`**，节点级不生效。→ 建议文档删除该承诺，或后端补一条"无触发器时按
  节点 `next_id` 兜底（否则该态将无限挂起）"。本轮已在文档中标注。
- **value_update 含 di/do**：示例把 `di`/`do` 与 `nodeId`/`static` 放在同一条消息；实际
  `nodeId/static` 由 API 推送，`di`/`do` 由 `EngineCtrl.sendDi/sendDo` **分条**推送。属示意性，已注明。

### 9.3 跨实验观察（已统一）
- **`debounce` 口径**：约定 canonical 为**顶层 `params.debounce`**——`EngineCtrl.startTest` 即读
  `params.debounce`（缺省 20）。各 API 文档已把 `debounce` 从 `sys` 移到顶层 `params`；前端去抖
  请置于 `params.debounce`（`sys` 内的旧位置不再生效）。

### 9.4 设计性局限（如实记录，不算缺陷）
- `end_condition` 为推断值：当某态同时挂 DI_MATCH 与安全 `timeout`、或 DI 在前序态已变位并保持
  时，COUNT_OVER/TIME_OUT/DI_MATCH 的标注在极端时序下可能不精确（新引擎不回报命中触发器）。
  度量值（duration、di_times）仍以硬件 SOE 为准、不受影响。

---

## 10. 算例

3 态：node1 故障前(STATIC, 1s 超时→2) → node2 故障(STATIC, DI_MATCH+安全超时 5s→3) →
node3 故障后(STATIC, 20ms→-2)。继电器在故障态 451ms 跳闸：

| id | duration_sec | end_condition | diA |
|---|---|---|---|
| 1 | 1.000 | TIME_OUT | null |
| 2 | 0.453 | DI_MATCH | 0.451 |
| 3 | 0.020 | TERMINAL | null |

（若 node2 配 `post_condition_delay_ms=30`，则 DI 命中后多保持 30ms 再进 node3，
node2 的 duration 含该 30ms，`end_condition` 仍为 DI_MATCH——经桩节点中转，不被覆盖。）

---

## 11. 边界情形与工程注意

- 节点 `id` 必须 ≥1 且与 `next_id` 引用一致；终止用 `-2`。
- 无任何 trigger 的节点会在引擎 `sleepForever` 永久挂起（须由 `next_node` 或外部停机打断）。
- `SWEEP` 态须保证步进层在 `static` 中存在（anti-tear），否则乒乓翻转会撕裂未初始化层。
- DI 基准为**序列起始态**：若希望"仅本态内变位才算"，需另行约定（当前不支持节点级基准）。

---

## 12. 参考
- `docs/api/api_status_sequence.md`（前端契约）
- `docs/engine_execution_model.md`（节点跳转、剪枝、DI 标志、四模式）
- `docs/architecture_overview.md`、`docs/fpga_protocol.md`
- 代码：`api/ApiStatSequ.py`、`api/BaseApi.py`、`logic/EngineCtrl.py`、`logic/USEEngine.py`
- 旧实现：`Temp/oldrelay/api/StatusSeqHdl.py`、`api/ApiHdlMgr.py`
- 同源：`docs/line-protection-state-sequence-design.md`
- 验证：`tests/test_migrated_engine_e2e.py`（真实引擎驱动，含 StatusSeq 多场景）
