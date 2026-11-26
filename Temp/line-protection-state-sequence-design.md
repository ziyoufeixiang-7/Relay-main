# 线路保护定值实验 — 状态序列实现设计

本文档定义 `lp_test`（线路保护定值校验）的**后端实现模型**：把 [line-protection-api-v2.md](line-protection-api-v2.md) 中的每一项设置，翻译成 FPGA 上实际执行的**电压/电流/开出状态序列**。

> 配套协议见 `line-protection-api-v2.md`（输入什么）；本文档说明（如何执行）。代码实现（`calc/line_fault.py` + `api/ApiLineProtectionTest.py`）为后续阶段，本文档先定模型。

---

## 1. 概述与底层机制

### 1.1 节点 = 状态
执行引擎以"节点"(`ApiNodeData`，见 [BaseApi.py:13](Relay/RelayProtection/api/BaseApi.py#L13)) 为单位。**一个节点 = 一个固定的电压/电流输出态 + 可选的开出(DO)动作序列**。

本实验**只使用静态节点 `mode=1`**：不使用步进/复位/扫描（`steps`/`reset`/`gate`/`interval`/`countOverId` 均不用）。每个测试点是前端已算好的离散故障量，整个实验就是**一串静态节点之间按条件跳转**——这就是"状态序列"。

### 1.2 节点跳转条件（本实验只用三类）
| 条件 | 字段 | 含义 |
|------|------|------|
| 超时 | `timeoutMs` → `timeoutId` | 停留 `timeoutMs` 毫秒后跳到 `timeoutId` |
| 开入匹配 | `diMatchMask` → `diMatchId` | DI 满足掩码逻辑时跳到 `diMatchId` |
| 手动 | API `trigNode(id)` | 后端主动触发跳转 |

特殊节点：`0x0000`=初始/复位态，`0xFFFF`=停止/错误。

### 1.3 DO 跨节点持续
DO 动作 `doActions` 为打包列表 `[(delayMs<<8)|doValue, ...]`，相对节点起点 t0 定时翻转开出。

**关键语义（依赖 USEEngine 改造，见 §7）**：DO 输出**跨节点持续**，不随节点切换自动清除。规则：进入新节点时，**仅当新节点自带 `doActions`（哪怕空列表）才终止上一节点的 DO 任务**；新节点无 `doActions` 则上一节点 DO 输出继续保持。这样跨状态的 DO 时序（如断路器辅助接点在 故障→断开→重合 间保持）才可实现。

### 1.4 物理量 → 寄存器
`BaseApi.physDictToReg()`（[BaseApi.py:64](Relay/RelayProtection/api/BaseApi.py#L64)）将物理字典 `{ch:{layer:[幅值,相位]}}` 转为寄存器。
- **通道**：API 0-5 = 电压 (Ua,Ub,Uc,Ux,Uy,Uz)；6-11 = 电流 (Ia,Ib,Ic,Ix,Iy,Iz)。本实验用 Ua=0,Ub=1,Uc=2,Ux=3,Ia=6,Ib=7,Ic=8。
- **层(layer)**：0 = 直流+频率（写额定频率 `sys.f`）；1 = 基波，值为 `[幅值, 相位°]`。
- **RMS→峰值**：FPGA 接收峰值，前端/计算用 RMS，写入前需 **×√2**（见 `ApiDifferentialTest._compute_and_build_fault`）。

### 1.5 实现模板
[ApiDifferentialTest.py](Relay/RelayProtection/api/ApiDifferentialTest.py) 是最接近的范例：故障前→故障→挂起 三节点 + 逐点 `trigNode` + `asyncio.Event`（在 `onUpdate` 回到挂起态时置位）。线路保护在此基础上扩展更多状态。

---

## 2. 输出通道与正常态模型

### 2.1 通道映射与输出配置
| 物理量 | API 通道 | layer | 写入 |
|--------|---------|-------|------|
| Ua/Ub/Uc | 0/1/2 | 1 | [幅值V_RMS, 相位°] |
| Ux | 3 | 1 | 见 `output.ux` |
| Ia/Ib/Ic | 6/7/8 | 1 | [幅值A_RMS, 相位°] |
| 频率 | 各通道 | 0 | [0, `sys.f`] |

- `output.vMode`（0=ABC / 1=XYZ）、`iMode`（0=ABC / 1=XYZ / 2=ABC_XYZ并联）决定三相量映射到哪组物理通道；并联模式下电流幅值在并联通道间均分（参考 `ApiDifferentialTest._build_fault_statics_2i`）。
- `output.ux`：`en` 决定是否输出 Ux；`type` 决定 Ux 取值（3U0 / −3U0 / 3√3U0 / 检同期 A/B/C/AB/BC/CA）。检同期类型下，跳闸后线路侧抽取电压用 `syncV`/`syncAng`（见 §4 CBOPEN 态、§6 ptLocation）。

### 2.2 正常态 S_NORMAL（故障前 / 额定）
- 电压：Ua=`sys.v`∠0°，Ub=`sys.v`∠−120°，Uc=`sys.v`∠+120°。
- 电流：负荷电流 `sys.iLoad`。Ia=`iLoad.amp`∠(0°+`iLoad.ang`)，Ib=…∠(−120°+ang)，Ic=…∠(+120°+ang)。`iLoad.amp=0` 时三相电流为 0。
- 频率：layer 0 写 `sys.f`。
- Ux：按 `output.ux` 计算（正常态 3U0≈0）。

---

## 3. 故障计算模型（全部 10 种故障类型）

**计算模型固定为 `IF_CONSTANT`**（`model.type=0`，协议已注明）：每个测试点给定故障电流 `i`(RMS)、测试阻抗 `z`(Ω)、阻抗角 `ang`(°)、故障类型 `ft`；后端据此与 `kL` 补偿算出各相 U/I 相量。

统一入口（拟在 `calc/line_fault.py` 实现）：
```
fault_phasors(ft, z, ang, i, kL, V_rated, dir, ctPolarity)
    -> { "U": {A,B,C 幅值∠相位}, "I": {A,B,C 幅值∠相位} }
```

### 3.1 符号与角度约定
- `V` = `sys.v` 额定相电压(RMS)；基准相角 baseA=0°、baseB=−120°、baseC=+120°。
- `φ` = `ang` 阻抗角；`Z` = `z`∠φ；`K` = `kL`（复数，`kl.amp`∠`kl.ang`）。
- `I` = `i`，故障电流幅值(RMS)。

> **角度约定**：相量图绝对参考可任意旋转。本文锚定**故障相电压沿其额定相轴**（故障相电压幅值减小、方向不变），故障相电流相应滞后。此约定满足"继电器测得 Z = U/(I+K·3I0)"的幅值与相角关系。**贵方如有特定继保厂家/规程的相量约定，可在评审时替换本节公式，状态序列结构不受影响。**

### 3.2 单相接地 ft=0/1/2（故障相 X = A/B/C）
接地距离回路：`U_X = (I_X + K·3I0)·Z`，单相接地 3I0 = I_X，故 `U_X = I_X·Z·(1+K)`。
- 故障相电压：`U_X = I·z·|1+K| ∠ baseX`（沿相轴，幅值减小）
- 故障相电流：`I_X = I ∠ (baseX − φ − arg(1+K))`
- 非故障两相：电压保持额定 `V∠base`，电流 = 0（或负荷电流）。
- 3I0 = I_X（供零序保护 type=1 检测）。

### 3.3 相间 ft=3/4/5（故障两相 X-Y = AB/BC/CA，不接地）
相间距离回路：`U_X − U_Y = (I_X − I_Y)·Z`，相间 I_Y=−I_X，故 `ΔU_XY = 2·I_X·Z`。
- 故障两相电流：`I_X = I ∠ θ`，`I_Y = I ∠ (θ+180°)`；θ 取使 ΔU 对称的参考角（实现时锚定于 X-Y 线电压轴）。第三相电流 = 0。
- 线电压差：`ΔU_XY = 2·I·z ∠ (θ+φ)`。
- 两相电压围绕故障前中点对称分裂：`U_common =（U_X额定 + U_Y额定)/2`，`U_X = U_common + ΔU_XY/2`，`U_Y = U_common − ΔU_XY/2`。
- 第三相电压保持额定。无零序，**K 不参与**。

### 3.4 两相接地 ft=6/7/8（故障两相 X-Y 接地）
阻抗由**相间回路**测量（距离保护相元件实际测量量），`(U_X−U_Y)/(I_X−I_Y)=Z`，**不含 K**。两故障相各自沿相轴塌陷（如三相故障中的两相），因两相电流不互相抵消而存在零序（3I0≠0），由此区别于纯相间故障。
- 两故障相电压：`U_X = I·z∠baseX`、`U_Y = I·z∠baseY`。
- 两故障相电流：`I_X = I∠(baseX−φ)`、`I_Y = I∠(baseY−φ)`；接地回流 3I0 = I_X + I_Y ≠ 0。
- 第三相：电压额定，电流 = 负荷/0。
- 验证：相间回路反算 Z=z∠φ ✓；3I0、I2 均非零（见 `calc/line_fault.py` 自测）。
- > 注：此为相元件锚定模型，地元件在两相接地下会看到不同的视在阻抗（物理正确）。若贵方需按地元件/序网严格建模，可在评审时调整。

### 3.5 三相短路 ft=9
对称三相，无零序/负序。
- `U_A=I·z∠φ`、`U_B=I·z∠(−120°+φ)`、`U_C=I·z∠(120°+φ)`（或沿相轴锚定，幅值=I·z）。
- `I_A=I∠0°`、`I_B=I∠−120°`、`I_C=I∠120°`。**K 不参与**。

### 3.6 方向与极性
- `dir`：0=正向（如上）；1=反向 → **所有故障电流相量 +180°**（故障在保护背侧）。
- `ctPolarity`：0=指向线路、1=指向母线 → 电流参考方向取反（+180°）。`dir` 与 `ctPolarity` 的相位翻转叠加。

### 3.7 各类型输出速查（幅值 / 是否含 K / 零序）
| ft | 名称 | 测量回路 | 故障相 U 幅值 | 故障相 I | 含 K | 3I0 | I2 |
|----|------|---------|--------------|---------|:---:|:---:|:--:|
| 0/1/2 | 单相接地 | 接地回路 | I·z·\|1+K\| | I | 是 | 有 | 有 |
| 3/4/5 | 相间 | 相间回路 | ΔU=2·I·z | I（两相反向） | 否 | 无 | 有 |
| 6/7/8 | 两相接地 | 相间回路 | I·z（两相） | I（两相） | 否 | 有 | 有 |
| 9 | 三相 | 任一回路 | I·z | I（对称） | 否 | 无 | 无 |

---

## 4. 状态目录（State Catalog）

每个状态 = 一个静态节点(mode=1)。下表为状态原型，具体节点 ID 与退出条件在 §5 各序列中实例化。

| 状态 | 输出（U / I / DO） | 典型退出条件 |
|------|-------------------|--------------|
| `S_NORMAL` 正常/故障前 | 额定电压 + 负荷电流（§2.2）；DO=`do.init` | 触发（超时/手动/DI，按 `trigger`） |
| `S_FAULT1` 第一次故障 | 按 `ft` 计算的故障 U/I（§3）；DO 按 `do.mode` | 跳闸 DI 匹配 / `point.time` 超时 |
| `S_TRIPDELAY` 跳闸延时 | 维持 `S_FAULT1` 输出 | `timeoutMs=cb.trip` 超时 → CBOPEN |
| `S_CBOPEN` 断路器断开 | 电流=0；电压按 PT 位置（§6 ptLocation）；DO 翻转（aux 接点） | 重合 DI(R/r) / 超时（不重合则结束） |
| `S_CLOSEDELAY` 合闸延时 | 维持 `S_CBOPEN` 输出 | `timeoutMs=cb.close` 超时 → 再带电 |
| `S_FAULT2` 转换故障 | 按 `ft2` 计算的故障 U/I（type 5/6） | 跳闸 DI / `time2` 超时 |
| `S_RECLOSE_FAULT` 重合到故障/后加速 | 重新施加故障 U/I（永久故障） | 后加速跳闸 DI / 超时 |
| `S_END` 结束/挂起 | 额定电压 + 零电流（恢复态） | 无（等待下一点 `trigNode` 或停止） |

> `S_TRIPDELAY`/`S_CLOSEDELAY` 仅当 `cb.sim=true` 时插入；否则跳闸/合闸后立即进入下一态（见 §6 断路器）。

---

## 5. 各测试类型的状态序列

约定：节点用 `ID:状态` 表示；箭头标注跳转条件。

### 5.1 基础定值点（type 0-4：距离/零序/过流/负序/工频变化量）
最简序列：正常 → 故障 → 结束。

```
1:S_NORMAL ──触发──▶ 2:S_FAULT1 ──跳闸DI──▶ 3:S_END   (记录动作时间=故障起点→DI)
                          └──point.time 超时(未动作)──▶ 3:S_END  (记录"未动作")
```

| 节点 | 输出 | 退出条件 → 目标 |
|------|------|----------------|
| 1 `S_NORMAL` | 额定+负荷 | 触发（§6 trigger）→ 2 |
| 2 `S_FAULT1` | ft 故障量 | `diMatchMask`=跳闸掩码 → 3；`timeoutMs`=`point.time` → 3 |
| 3 `S_END` | 额定+零流 | 等待下一点 |

逐点：后端为每个 point 重写节点 2 的故障量，`trigNode(1)` 启动，等待回到节点 3，记录是否跳闸及动作时间，下发 `value_update`/`report`，再测下一点。

### 5.2 故障转换（type 5：不灵敏零序，必含转换）
第一次故障后（或重合后）经 `delay` 注入转换故障 `ft2`。

```
1:S_NORMAL ──触发──▶ 2:S_FAULT1 ──(after=0:首次故障后+delay)──▶ 4:S_FAULT2 ──跳闸/超时──▶ 3:S_END
                          └─(after=1 需先走重合流程, 见 5.3 结构)
```
- `after=0`：节点 2 停留 `delay`（`timeoutMs=delay,timeoutId=4`）后进入节点 4 转换故障。
- 节点 4 退出：跳闸 DI → 3；`time2` 超时 → 3。
- type 5 的 `after` 通常为 0；若为 1，复用 5.3 的重合结构，在重合后注入 `ft2`。

### 5.3 自动重合闸及后加速（type 6）
完整序列（永久故障 + 后加速）：

```
1:S_NORMAL ─触发→ 2:S_FAULT1 ─跳闸DI→ 5:S_TRIPDELAY ─cb.trip→ 6:S_CBOPEN
   ─重合DI(R/r)→ 7:S_CLOSEDELAY ─cb.close→ 分支:
      nature=0 瞬时  →  3:S_END         (重合成功, 恢复)
      nature=1 永久  →  8:S_RECLOSE_FAULT ─后加速跳闸DI→ 9:S_CBOPEN(二次断开) → 3:S_END
```

| 节点 | 输出 | 退出条件 → 目标 |
|------|------|----------------|
| 1 `S_NORMAL` | 额定+负荷 | 触发 → 2 |
| 2 `S_FAULT1` | ft 故障量 | 跳闸DI → 5；`point.time` 超时(未跳)→ 3 |
| 5 `S_TRIPDELAY` | 维持故障 | `cb.trip` → 6 |
| 6 `S_CBOPEN` | I=0；U按PT | 重合DI(R/r) → 7；超时(不重合)→ 3 |
| 7 `S_CLOSEDELAY` | 维持断开 | `cb.close` → (8 或 3，按 nature) |
| 8 `S_RECLOSE_FAULT` | 重施故障量 | 后加速跳闸DI → 9；超时 → 3 |
| 9 `S_CBOPEN` | I=0；U按PT | 直接/超时 → 3 |

**叠加故障转换（type 6 带 ft2）**：`after` 决定 `ft2` 注入时机——`after=0` 在 2 之后 `delay` 注入 `S_FAULT2`（替换 2→5 的去向）；`after=1` 在重合合闸（节点 7 之后）`delay` 注入 `S_FAULT2`（替换永久故障的 8）。

**无故障转换 + 瞬时**：序列在重合成功后即结束（2→5→6→7→3）。

---

## 6. 跳转条件与 payload 参数映射

### 6.1 触发方式 `trigger` —— 决定 S_NORMAL 如何退出
| `trigger.type` | S_NORMAL 退出 |
|---------------|--------------|
| 1 TIME | `timeoutMs=trigger.time`，超时进入故障态 |
| 0 KEY | 后端等待按键后 `trigNode(故障态)`（按键机制见协议 §3.1 TODO） |
| 2 DI | `diMatchMask=trigger.mask`，开入满足后进入故障态 |

### 6.2 故障持续 `point.time`
故障态最大持续时间 → 故障节点 `timeoutMs`。在 `time` 内检测到跳闸 DI 即记动作；否则超时判"未动作"，转下一态/下一点（与 §1 执行语义一致：未动作继续下一组）。

### 6.3 开入 `di` —— 构造两类掩码
物理开入：A/B/C(0/1/2)、R(3)、a/b/c(4/5/6)、r(7)。由各通道角色构造：
- **跳闸掩码**：角色为 `TRIP_ABC`(2) 或 `THREE_TRIP`(1) 的相通道位（A/B/C，可含 a/b/c）。用于故障态检测跳闸。
- **重合掩码**：角色为 `RECLOSER`(3) 的位（R/r）。用于 CBOPEN 态检测重合。
- 逻辑位（参考 [USEEngine.py](Relay/RelayProtection/logic/USEEngine.py) `_evalTrig`/`coreLoop` 的 diMatch 语义）：`0x100`=AND(全匹配，否则 OR/任一)、`0x200`=以进节点时的 DI 为基准、`0x400`=电平取反。检测"任一相跳闸"用 OR（不置 0x100）。

### 6.4 断路器 `cb`
- `cb.sim=true`：插入延时态 `S_TRIPDELAY`(=`cb.trip`)、`S_CLOSEDELAY`(=`cb.close`)，模拟断路器机械动作时间。
- `cb.sim=false`：跳过延时态，跳闸/重合后立即切换输出。
- 实现取舍：**采用独立延时节点**（维持上一态输出 + `timeoutMs`），而非节点内定时——因为输出值的改变必须发生在节点边界，独立延时节点最清晰且复用现有跳转机制。

### 6.5 PT 位置 `ptLocation` —— 决定 S_CBOPEN 电压
- `0` 母线侧：断路器断开后 PT 仍接母线 → **电压保持额定**，电流=0。
- `1` 线路侧：断路器断开后线路失电 → **电压塌陷为 0**；若启用检同期 `ux`，线路侧 Ux 输出抽取电压 `syncV`∠`syncAng`（供重合检同期判据）。

### 6.6 方向/极性 `dir` / `ctPolarity`
见 §3.6：影响故障电流相量方向（±180°），不改变状态序列结构。

### 6.7 开出 `do` —— 利用 DO 跨节点持续（§1.3）
DO 时序可跨状态延续；需在某段 DO 终止处给该节点挂一个 `doActions`（空列表即终止）。
- `do.init`：实验起始开出 → 首节点(S_NORMAL) `doActions` 起始值。
- `mode=0` SYNC_WITH_RECLOSER：DO 随跳闸翻转、随重合翻回 → 在 `S_TRIPDELAY/S_CBOPEN` 起点用 `doActions` 设新值，跨节点保持；在重合相关节点再用 `doActions` 翻回。
- `mode=1` DELAY_FLIP_ALL：故障态内延时 `delay` 翻转全部、保持 `hold` 后翻回 → 编为 `S_FAULT1` 的 `doActions`（可跨入后续态保持，直到带 `doActions` 的节点终止）。
- `mode=2` CUSTOM：逐通道 HOLD/FLIP(+`delay`/`hold`) → 编为 `doActions`。
- 文档/实现需明确：每个状态是"携带 `doActions`（含空）以终止上一段 DO"还是"留空以延续"。

### 6.8 系统/模型/补偿
- `sys`（额定 V/f、负荷电流）→ S_NORMAL 与各态背景量（§2.2）。
- `model` 固定 IF_CONSTANT；`kL` 用于接地类故障电压计算（§3.2/3.4）。

---

## 7. 实现落地（后续阶段）

### 7.1 前置改造 `logic/USEEngine.py`
当前 `coreLoop` 在每次切换节点时无条件取消 DO 任务（[USEEngine.py:296-298](Relay/RelayProtection/logic/USEEngine.py#L296)）：
```python
if self._doTask:
    self._doTask.cancel()
    self._doTask = None
```
改为：**仅当新节点带有 `doActions` 时才取消上一节点 `_doTask`**；否则保留，使 DO 输出跨节点持续。§1.3/§6.7 的跨节点 DO 时序依赖此改造。

### 7.2 新增 `calc/line_fault.py`
实现 §3 故障模型：`fault_phasors(ft, z, ang, i, kL, V, dir, ctPolarity) → {U,I 相量}`，覆盖全部 10 种 `ft`。镜像 `calc/diff.py` 风格。

### 7.3 新增 `api/ApiLineProtectionTest.py`
- `MODULE_KEY = "lp_test"`。
- `_onSetup`：解析 `debounce`/`tests`/`payload`，构建固定节点(S_NORMAL/S_END/延时态)，逐 test、逐 point 构建故障节点序列（按 type 选 §5 的拓扑）。
- 逐点执行：复用 `ApiDifferentialTest._test_one_point` 模式（`upsertNodes` 写故障节点 → `trigNode` → `asyncio.Event` 等待回到 S_END）。
- `onDi`：区分跳闸/重合（按 §6.3 掩码），记录动作时间。
- `onUpdate`：推进状态、置位完成事件。
- `sendReport`：上报动作时间/合格判定（结构见协议 §4 TODO）。
- 复用：`physDictToReg`、节点 FSM、消息通道（`value_update`/`report`/`stop`）。

---

## 8. 待定项（TODO）
- **Report 回传结构**：动作时间、跳闸/重合时刻、各点合格判定、错误原因等（协议 §4 TODO）。
- **KEY 触发机制**：按键事件如何送达后端（协议 §3.1 TODO）。
- **故障计算专用公式**：§3 采用标准对称分量约定；若贵方提供厂家/规程专用公式，替换 §3，状态序列结构不变。
- **两相接地(ft=6/7/8) 相量模型**：§3.4 为简化模型，评审重点确认。
