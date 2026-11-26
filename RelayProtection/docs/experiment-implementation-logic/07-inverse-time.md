# 反时限试验（I-t / V·F-t）— 实现逻辑、代码映射与审计

> 模块标识：`inverse_time_it_test`（过流反时限 I-t）、`inverse_time_vft_test`（过/欠压、过/欠频、V/Hz 反时限）
> 被测对象：动作时间随被测量大小变化的**反时限特性曲线** $t=f(I)$ 或 $t=f(V/f)$。
> 依据：继保测试原理 + `docs/api/api_inverse_time.md` + 代码（`api/ApiInvsTime.py`、`logic/EngineCtrl.py`、
> `logic/USEEngine.py`）+ 旧实现（`Temp/oldrelay/api/InvTimeItHdl.py`、`InvTimeVftHdl.py`）。

---

## 1. 摘要

反时限保护的动作时间是被测量的函数：被测量越大、动作越快（如反时限过流）。本试验**逐点**施加
不同大小的故障量，测出每点的**实际动作时间**，从而描绘并校验保护的 $t$-$I$（或 $t$-$V$、$t$-$f$、
$t$-$(V/f)$）特性曲线是否符合定值（IEC/IEEE 标准反时限曲线或自定义曲线）。

每个测试点 = 两个引擎节点：**故障前态**（保持 `tBeforeFault` 的正常/负荷工况）→ **故障态**
（施加该点故障量，等保护出口 DI 跳闸或 `faultLimitTime` 超时）。所有测试点连成一条节点链，
引擎一次跑完、逐点上报动作时间。

---

## 2. 原理背景

- **I-t（过流反时限）**：被测量为故障电流，动作时间随电流增大而减小。支持
  接地/相间/三相故障的相别与角度建模，以及方向性过流（带故障前负荷 + 故障分量的矢量叠加）。
- **V·F-t**：被测量为电压（过/欠压 `V_T`）、频率（过/欠频 `F_T`）或电压/频率比（过激磁 `VF_T`）。

反时限试验的关键是**动作时间的精确测量**——用 FPGA SOE 时间戳记录"进入故障态"到"DI 跳闸"的间隔。

---

## 3. 试验目的与判据

| 目的 | 度量 | 判据 |
|---|---|---|
| 校验反时限特性曲线 | 各点 `actualTime`（秒） | 与理论 $t=f(\cdot)$ 在允许误差内 |
| 校验最小动作量/门槛 | 是否在 `faultLimitTime` 内跳闸 | 门槛附近动作/不动作符合定值 |

未在 `faultLimitTime` 内跳闸的点 `actualTime` 上报 `null`（未跳）。

---

## 4. 前端控制变量（`params`）

### 4.1 公共
- `debounce`（顶层 `params.debounce`）/ `sys.logicMask`：DI 去抖 / 监视跳闸的开入通道掩码。
- `payload.tBeforeFault`：故障前态时长（**ms**）。
- `payload.faultLimitTime`：故障态等待跳闸上限（**ms**；0 = 不超时）。
- `testPoints`：`[{id, value}]`，`value` 含义随试验而定（A / V / Hz / V·Hz⁻¹）。
- `statics`：辅助通道（如极化电压、非故障组）物理值。

### 4.2 I-t 专有
| 字段 | 含义 |
|---|---|
| `ratedFrequency` | 系统频率 Hz。 |
| `iBeforeFault` | 故障前负荷电流 A。 |
| `faultType` | `A/B/C_GROUND`、`AB/BC/CA_PHASE`、`ABC_PHASE`。 |
| `iDefinition` | `value` 解释：`FAULT_CURRENT`/`NEGATIVE_SEQUENCE`/`ZERO_SEQUENCE`/`SINGLE_PATH`。 |
| `negativeSequenceChannel` | 负序定义 `I2` 或 `3I0`。 |
| `outputMode` | 逻辑 ABC → 物理通道组（电压 `GROUP1_UABC`(0,1,2)/`GROUP2_UXYZ`(3,4,5)；电流 `GROUP1_IABC`(6,7,8)/`GROUP2_IXYZ`(9,10,11)）。 |
| `directionality` | 方向性过流：`enabled`、`lineAngle`、`preLoadAngle`、`vBeforeFault`、`vFault`、`ctPolarity`(`TOWARD_COMPONENT`/`TOWARD_BUSBAR`)。 |

### 4.3 V·F-t 专有
| 字段 | 含义 |
|---|---|
| `testProject` | `V_T`（压）/ `F_T`（频）/ `VF_T`（V/Hz）。 |
| `vOutputMode` | 施压通道与角度：`UA/UB/UC/UAB/UBC/UCA/UABC_POS/UABC_NEG/UABC_ZERO`。 |
| `angle` | 基准相位偏置（度）。 |
| `vBeforeFault`/`fBeforeFault` | 故障前电压/频率基线。 |
| `vbValue`/`fbValue`、`vfVariable` | `VF_T` 时锚点与变化量：`VOLTAGE_AMPLITUDE_V`（变压）/`VOLTAGE_FREQUENCY_F`（变频）。 |

---

## 5. 数学模型（控制变量 → 注入相量）

### 5.1 节点链
对第 $k$ 个测试点：故障前节点输出 `iBeforeFault`/`vBeforeFault`（正常/负荷态），保持 `tBeforeFault`；
故障节点输出该点故障量，等 DI 跳闸或 `faultLimitTime` 超时后进入下一点的故障前节点。

### 5.2 I-t 故障电流
**非方向**：按 `faultType` 取相别有效位与角度（`FAULT_PHASE_MAP`），幅值 $I_\varphi = \text{value}\times\text{scale}$，
其中 scale 由 `iDefinition` 决定（故障电流 1；负序 `I2`：接地 3、相间 √3；零序：接地 3、相间 0；`SINGLE_PATH` 并联同相）。

**方向性**（叠加模型）：故障前三相对称负荷 $\vec I_{\text{pre}}=(i_{pre}\angle{-\phi_{load}},\,i_{pre}\angle{-120-\phi_{load}},\,i_{pre}\angle{120-\phi_{load}})$；
故障分量 $\vec I_\Delta$ 由 `DELTA_ANGLES[faultType]` 给角度、幅值 $=\text{value}\times\text{scale}$、再整体 $-\,\text{lineAngle}$。
各相

$$
\vec I_{\text{total}}=\vec I_{\text{pre}}+\vec I_\Delta,\qquad
\text{若 }ctPolarity=\text{TOWARD\_BUSBAR}\Rightarrow \angle\!+\!180^\circ.
$$

方向性下故障电压跌落 `_ComputeDirVoltages`：接地故障——故障相降到 `vFault`、其余 `vBeforeFault`；
相间故障——线电压钳位 $U_{xy}=\text{vFault}\angle\theta$，解出 $U_x,U_y$，非故障相保持；三相——全部降到 `vFault`。

### 5.3 V·F-t
`_ResolveFaultVf`：
$$
V\_T:(V,f)=(\text{value},\,f_{before});\quad F\_T:(V,f)=(V_{before},\,\text{value});
$$
$$
VF\_T:\ \text{变压}\ (V,f)=(\text{value}\cdot f_b,\,f_{before});\ \ \text{变频}\ (V,f)=(V_{before},\,v_b/\text{value}).
$$
按 `vOutputMode` 取通道与角度偏置（`V_OUTPUT_MAP`），各通道输出 $(V,\ \text{angle}+\text{offset},\ f)$。

### 5.4 层语义与标定
计算量放在**层 1**（AC：幅值 + 相位），层 0 给频率（`_MakeChannelReg → {"0":[0,freq], "1":[amp,angle]}`，
返回的是**物理值**）；随后经 `BaseApi.physDictToReg`（`calib` 标定 + 通道映射 + 幅值寄存器换算）下发。

### 5.5 动作时间
$$
t_{\text{act}}=\big[(\text{ts}_{\text{DI 跳闸}}-\text{ts}_{\text{故障入口}})\ \&\ \mathtt{0xFFFFFFFF}\big]/10^6\ \text{s}.
$$
取**首次**满足掩码变位的 DI 时间戳（SOE µs）。

---

## 6. 试验时序

```
0x0000 ─trigNode(1)─► [点1 故障前(tBeforeFault)] ─TIME_OUT─► [点1 故障(DI_MATCH/faultLimit)]
        ─► [点2 故障前] ─► [点2 故障] ─► ... ─► (末点故障 DI_MATCH/超时 → 0xFFFF 终止)
```
故障节点出口：`DI_MATCH`（掩码 `logicMask|0x200`，以**进入故障态**的 DI 为基准做变位）或 `faultLimitTime` 超时，
二者都跳向下一点的故障前节点；末点跳 `0xFFFF` 终止。

---

## 7. 测量与上报

- **跳闸即推**：故障态因 DI/超时退出时推送 `{value_update, id, actualTime, di, currentI|vOrFValue}`。
- **最终报告**：
```json
{"type":"report","module":"inverse_time_it_test","data":{"rows":[
  {"id":1,"currentI":5.0,"actualTime":1.2345}, {"id":2,"currentI":10.0,"actualTime":0.4501}]}}
```
`actualTime` 单位**秒**；V·F-t 用 `vOrFValue` 字段。未跳点 `actualTime=null`。

---

## 8. 引擎/代码实现映射

### 8.1 节点编号
每点 2 节点，从 1 开始：`before_node_id = 2·idx+1`、`fault_node_id = 2·idx+2`，
`next_before`（下一点故障前）= `2·idx+3` 或末点 `0xFFFF`；`trigNode(1)` 启动。
（`0x0000` 校准 / `0xFFFF` 终止保留——节点 0 不可用，见 05 §8.1。）

### 8.2 `ApiNodeData` 与跳转
故障前节点：`mode=1`、`timeoutMs=tBeforeFault`、`timeoutId=fault_node`。
故障节点：`mode=1`、`diMatchMask=logicMask|0x200`、`diMatchId=next_before`、
`timeoutMs=faultLimitTime`、`timeoutId=next_before`。
> **DI 基准差异**：反时限用 `0x200`（以**进入故障节点**时的 DI 为基准），与状态序列的"裸掩码
> /diStart 基准"不同——因为反时限要的是"故障态内相对故障前正常态的变位"。`_ComputeCurrents/_ComputeVoltages`
> 产出物理值 → `physDictToReg` 标定。`_MakeChannelReg` 命名含 "Reg" 但返回物理值（历史命名，注意）。

### 8.3 API 驱动 EngineCtrl ↔ USEEngine
- `setup → upsertNodes(全部点的节点) + trigNode(1)`；
- `onDi`：在故障节点内按 `(di ^ faultEntryDi) & mask` 检测首次变位，置 `diMatchedThisNode`、锁存 `tripHwTs`；
- `onUpdate`：进入故障节点(tick0)记录 `entry_hw_ts`、快照 `faultEntryDi`、清 `tripHwTs`；
  离开故障节点（转移到下一点）时结算 `actual_time` 并推 `value_update`；
- `onStop`：**补算末点**（末点 DI_MATCH 直接跳 `0xFFFF`，不触发离开结算）后组 `rows` 报告。

---

## 9. 协议与代码审计

### 9.1 已修复（PR #4，佐证）
- 节点编号从 1（原 `2·idx` 含节点 0，与 `0x0000` 冲突被剪枝 → 实验跑不过第一点）；
- 末点动作时间结算（末点跳 `0xFFFF` 不触发 `onUpdate` 离开结算，`_onStop` 补算）；
- 动作时间锁存**首次**跳闸时刻 + 以故障入口 DI 为基准做边沿检测（对齐旧"清零 `lastDiChangeHwTs`"意图）。

### 9.2 与旧实现一致性
`FAULT_PHASE_MAP`/`SINGLE_PATH_MAP`/`DELTA_ANGLES`/`_ComputeCurrents`/`_ComputeDirVoltages`/
`V_OUTPUT_MAP`/`_ComputeVoltages`/`_ResolveFaultVf` 与旧 `InvTimeItHdl`/`InvTimeVftHdl` **逐行等价**；
`di_mask=logicMask|0x200` 亦与旧一致。标定从旧 `ApiHdlMgr.PhysToReg` 迁到 `calib.PhysToReg`（同义）。

### 9.3 文档勘误（`docs/api/api_inverse_time.md`）
- value_update 示例含 `do`，实际代码该消息不带 `do`（`di` 由 EngineCtrl 另推）→ 示例可澄清。
- 单位已正确（`tBeforeFault`/`faultLimitTime` 毫秒；`actualTime` 秒）——前序已修毫秒。

### 9.4 设计性/细节
- 故障态因**超时未跳**退出时也会推一条 `actualTime=null` 的 value_update（文档仅述"跳闸时推"）——无害。
- `_MakeChannelReg` 返回物理值而非寄存器值（命名历史遗留）。
- `limits`：后端不消费，由前端限幅 + DAC 硬件上限兜底（见 06 §9.3）。
- `debounce` 口径：已统一为顶层 `params.debounce`（`EngineCtrl` 即读此处）；文档已移位。

---

## 10. 算例（I-t，A 相接地过流，非方向）

`faultType=A_GROUND`、`iDefinition=FAULT_CURRENT`、`iBeforeFault=0`、`tBeforeFault=1000ms`、
`faultLimitTime=60000ms`、`testPoints=[{1,5.0},{2,10.0}]`：

1. 点1 故障前：I 全 0、1s；故障态：Ia=5A∠0°（层1），等 DI；relay 1.2345s 跳闸 → `actualTime=1.2345`；
2. 点2 故障前 1s；故障态 Ia=10A，0.4501s 跳闸 → `actualTime=0.4501`；末点跳闸 → 终止。
   报告 `rows=[{1,5.0,1.2345},{2,10.0,0.4501}]`。电流越大动作越快，符合反时限。

---

## 11. 边界情形与工程注意
- `faultLimitTime=0` 表示不超时——故障态将无限等待 DI（仅靠跳闸或停机退出）。
- 方向性叠加结果幅值/相位由矢量和决定，注意 `ctPolarity` 取向。
- `VF_T` 变频模式 `value=0` 会被保护为不改频率（避免除零）。
- DI 基准为**进入故障态**：故障前残留的 DI 状态不会误触发（与状态序列口径不同）。

---

## 12. 参考
- `docs/api/api_inverse_time.md`（前端契约）
- `docs/engine_execution_model.md`、`docs/architecture_overview.md`、`docs/fpga_protocol.md`
- 代码：`api/ApiInvsTime.py`、`api/BaseApi.py`、`logic/EngineCtrl.py`、`logic/USEEngine.py`、`logic/Calibration.py`
- 旧实现：`Temp/oldrelay/api/InvTimeItHdl.py`、`InvTimeVftHdl.py`、`api/ApiHdlMgr.py`
- 验证：`tests/test_migrated_engine_e2e.py`（真实引擎，含 I-t 与 V·F-t 多点）
