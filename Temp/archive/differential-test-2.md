# 差动试验 API 协议（6 路电流 / 母差保护）

> **模块标识**: 统一为 `"differential_test"`，**不**拆成两个 WebSocket `module`；6 路 / 母差通过 `payload.variant`（`six_channel` | `busbar`）区分。字段名与前端 `useDifferentialTestStore().state` 对齐（`camelCase`）。  
> **入口命令**: `"cmd": "start"`（启动）/ `"cmd": "update_static"`（运行中更新静态量）  
> **适用前端类型**: `sendInitParams(IWsParams)`（推荐：`sys + statics + payload`；**试验点仅放在** `payload.projectSettings.ratio.points` / `harmonic.points`，见 §2.1）

---

## 0. 文档目的与制定说明

### 0.1 计划（编写顺序）

1. 对照前端 Pinia 状态 [`differential-test-store`](../../src/views/general-test/store/differential-test-store.ts) 与配置页 [`DifferentialConfigTabs.vue`](../../src/views/general-test/module/differential-test/components/DifferentialConfigTabs.vue)，列出用户可输入项。
2. 区分 **需下发后端**（驱动仪器、执行试验序列、判据）与 **纯前端**（图表视图、报表辅助、本地推导）。
3. 约定 `IWsParams` 中 `sys`、`statics`、`payload` 的职责分工；电流与辅助电压经 **`statics` 矩阵** 下发（见 §2.4、§2.5）；**试验点列表不在顶层 `testPoints`**，仅在 **`payload.projectSettings.*.points`**。
4. 联调口径见 **§7（已决议）**。

### 0.2 当前代码现状（必读）

| 项目 | 说明 |
|------|------|
| WebSocket 模块枚举 | [`ENUM_WS_MODULE`](../../src/ws/enum.ts) 已包含 `DIFFERENTIAL_TEST = "differential_test"`。 |
| 路由 | `/differential-test-6ch` 与 `/differential-test-busbar` 共用同一 Store，通过当前路由区分 **6 路** / **母差**，并在下发 `payload.variant` 中显式标识。 |
| 下发链路 | `EventListBtn` 已接入差动 `start`；`differential-test-store` 已实现 `sendConfig()`（`start`）与 `sendStaticUpdate()`（`update_static`）。 |
| 本文档性质 | 作为 **目标协议**：字段来源于现有界面状态；试验点序列为 **`projectSettings.ratio.points` / `harmonic.points`**（§7）；`statics` 为仪器量下发载体。 |

---

## 1. 实验概述

差动试验用于比率差动、谐波制动（**仅六路电流界面**）等项目的边界搜索或定点测试：前端配置保护方程、补偿系数、制动曲线、**`projectSettings` 下 `ratio.points` / `harmonic.points` 试验点列表**、试验时序；仪器量经 **`statics` 通道矩阵** 下发；后端按点采样开入并回传报告。**母差界面无谐波试验项**，协议中与 `harmonic-*` 相关字段仅对 `variant: six_channel` 有效。

```text
开始(start) -> 运行(value_update，实时量) -> 报告(report，试验点结果) -> 结束(stop)
```

**两形态共性**：同一套状态结构，通过 `payload.variant` 区分（与单一 `module: differential_test` 配合使用）。

| 形态 | 前端路由 | 典型 `testType` | 额外 payload |
|------|-----------|-----------------|---------------|
| 差动 6 路 | `/differential-test-6ch` | `six_channel`（与枚举 `ENUM_DIFFERENTIAL_TEST_TYPE` 一致） | `protectionSettings`、`connectionSettings`（扩展/分相 6I/2I）等 |
| 母差保护 | `/differential-test-busbar` | `busbar` | `busbarSettings`、母差接线类型等（**不下发** `busbarKpParams`，见 §7） |

---

## 2. 前端 -> 后端（启动）

### 2.1 请求示例（推荐骨架）

说明：**试验点数据仅通过** `payload.projectSettings.ratio.points`（比率项目）及 **`projectSettings.harmonic.points`**（谐波项目）传递，**不在** `IWsParams.testPoints` 重复下发。下发时 points **仅包含已勾选行**，并移除纯前端字段 `selected` / `status`。电流与**辅助电压**均在 **`statics`** 中下发，**不要**在 `payload` 中带 `auxVoltageData` 作为下发音量载体（界面编辑量组包进 `statics`）。谐波相关 `projectSettings.harmonic` / `activeProject: harmonic-*` **仅** `six_channel` 使用。

**`params.sys`（`debounce`、`logicMask`）**：含义与赋值方式与全项目其它界面一致，见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.1 `sys`**；差动场景不传开出相关 `doMask` / `doCtrlMask`。

```json
{
  "module": "differential_test",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 7
    },
    "statics": {
      "0": { "0": [5.0, 0.0], "1": [57.735, 0.0] },
      "1": { "0": [5.0, 180.0] },
      "3": { "1": [57.735, -120.0] }
    },
    "payload": {
      "variant": "six_channel",
      "protectionSettings": {
        "vectorGroupLetter1": "y",
        "vectorGroupLetter2": "y",
        "vectorGroupHighSideClock": "0",
        "vectorGroupLowSideClock": "0",
        "phaseCorrection": "none",
        "zeroSequenceCorrection": false
      },
      "equationSettings": {
        "idEquation": "id_sum",
        "irEquation": "ir_diff_k",
        "kFactor": 2.0,
        "kp1": 1.0,
        "kp2": 1.0,
        "Ie": 1.9682,
        "isPerUnit": false
      },
      "connectionSettings": {
        "type": "ext-differential",
        "i1Terminal": "high-y",
        "i2Terminal": "low-y"
      },
      "projectSettings": {
        "activeProject": "ratio-search",
        "testPhase": "A",
        "ratio": {
          "frequency": 50.0,
          "precision": 0.01,
          "method": "binary",
          "points": [
            { "id": 1, "x": 1.0, "y": [0.0, 0.72] }
          ]
        }
      },
      "testParams": {
        "diffMode": 0,
        "faultDuration": 1.0,
        "preFaultDuration": 1.0,
        "simulatePreFault": true,
        "loadCurrent": 0.0
      }
    }
  }
}
```

### 2.2 母差形态示例（payload 差异片段）

将 `payload.variant` 设为 `"busbar"`（母差路由）。**不包含** `projectSettings.harmonic` / `activeProject: harmonic-*`（谐波仅六路界面）。`start` 包仍应带 **`statics`**（电流/电压）；试验点仅在 **`projectSettings.ratio.points`**（或母差实际使用的项目子对象下的 `points`）中传递。下方仅示意 payload 中与六路不同的片段：

```json
{
  "payload": {
    "variant": "busbar",
    "busbarSettings": {
      "model": "pcs_915",
      "subModel": "high_grand",
      "windingDir": "towards_i"
    },
    "connectionSettings": {
      "type": "ext-5i",
      "i1Terminal": "ia",
      "i2Terminal": "ix"
    },
    "equationSettings": {
      "idEquation": "id_sum",
      "irEquation": "ir_sum",
      "kFactor": 2.0,
      "kp1": 1.0,
      "kp2": 1.0,
      "isPerUnit": false
    }
  }
}
```

（具体枚举取值与前端 `ENUM_BUSBAR_*`、选项表一致，此处为示意。）

### 2.3 字段说明（核心）

| 路径 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `params.sys.*` | `object` | 是 | 差动目前仅使用 `debounce`、`logicMask`；**不传** `doMask` / `doCtrlMask`（开出相关）。含义与赋值见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.1**。 |
| `params.limits` | `object` | 按需 | 若需保护上限则下发；定义见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.2**；**启动示例可不出现**。 |
| `params.statics` | `object` | **是** | **电流与辅助电压的下发载体**（[`StaticsConfig`](../../src/ws/types.ts)）；不在 `payload` 中用 `auxVoltageData` 数组承载下发音量。 |
| `params.testPoints` | — | **不使用** | 差动试验**不下发**顶层 `testPoints`；试验点仅见 **`payload.projectSettings.ratio.points`** / **`harmonic.points`**。 |
| `params.steps` / `params.count` |  | 可选 | 递变/扫描时与全局约定一致。 |
| `params.payload.variant` | `string` | 是 | `six_channel` \| `busbar`。 |
| `params.payload.equationSettings` | `object` | 是 | 动作/制动方程、`K`、KP1/KP2；`Ie`=高压侧额定电流 In,2（见 §2.1 示例）。 |
| `params.payload.connectionSettings` | `object` | 是 | 接线类型；仅含 I1/I2 接测试仪端子选项（不下发角度）。 |
| `params.payload.projectSettings` | `object` | 是 | `activeProject`、`testPhase`、比率/谐波子对象；**试验点序列**：**`ratio.points`** / **`harmonic.points`**（仅已选中点，且不含 `selected`/`status`）。 |
| `params.payload.testParams` | `object` | 是 | 仅下发 5 个字段：`diffMode`、`faultDuration`、`preFaultDuration`、`simulatePreFault`、`loadCurrent`；**不含** `logic` / `logicMask` / `debounceTime`（该类由 **`params.sys`** 承担，见上表）。 |
| `params.payload.protectionSettings` | `object` | 六路常用 | 含接线组别三字段 + 高压侧角度预留 + 相位/零序校正（见 §6.1.10）；母差场景通常不下发。界面另有 `protectionObject`、`windingType`、`involvedWinding` 等仅本地/导入导出，**不写入启动包**。 |
| `params.payload.busbarSettings` | `object` | 母差常用 | 型号、绕组朝向等（枚举 slug）。 |
| `params.payload.kpCalcParams` | — | **不下发** | 仅前端辅助计算用途。 |
| `params.payload.auxVoltageData` | — | **不下发** | **禁止**作为启动包中的电压下发音量字段；电压写入 **`statics`**（§2.5）。Store 中同名数组仅 UI 编辑态，组包时换算进 `statics`。 |
| `params.payload.projectSettings.ratio.points` | `array` | 有选中点时 | 右图**线段/点**列表；边界搜索为 `{ id, x, y:[y0,y1] }`，定点为 `{ id, x, y }`；见 **§2.6**。 |
| `params.payload.projectSettings.harmonic.points` | `array` | `harmonic-*` 且有选中点时 | 谐波边界为 `{ id, y, x:[x0,x1] }`，定点为 `{ id, x, y }`。 |

### 2.4 结构约束

- 使用 **`IWsParams`**：`sys` + **`limits`（按需）** + **`statics`** + `payload`，**不使用顶层 `testPoints`**；试验点仅存在于 **`payload.projectSettings.ratio.points`** / **`harmonic.points`**。不使用 `IWsParamsNew.nodes[]`，除非后端改为多节点状态机。
- **`payload` 命名**：与 Store **camelCase** 对齐。
- **`projectSettings.harmonic` / `activeProject` 为 `harmonic-*`**：**仅** `variant: six_channel` 有意义；母差不下发谐波块或不解析。
- **按界面条件收敛**：仅传当前项目界面显示且后端需要的字段。例如 `activeProject=ratio-fixed` 时，不传比率搜索相关控制量；`harmonic-*` 项目才传 `projectSettings.harmonic`。
- 参数收敛：`kpCalcParams`、`ratio.curveSegments`、`ratio.error` 为前端计算/展示参数，启动下发时不传。
- **`payload.terminals`**：**不下发**；前端已从 Store 与组包中移除该字段。

### 2.5 `statics` 与通道编号约定（交叉引用）

- **电流与辅助电压**均由 **`params.statics`**（及 `limits`）描述；结构为 [`StaticsConfig`](../../src/ws/types.ts)（通道索引 → `layer` → `[电流幅值, 相位]`），与同项目交流类试验一致。
- **差动试验数值口径**：界面、Store、启动包 **`points[]`** 与 **`value_update`** 的 `x`/`y` 均为**工程值**（A、Harm% 等），前端仅做精度 round，**不做** ÷In / ×In 标幺换算。**不做** `turnValidValToAmp` / `turnAmpValToVaildVal` 有效值↔峰值换算；`statics` 电流层本次仍按工程值下发。
- **通道编号**：与 **整组 / 状态序列** 下 **`static` 矩阵的通道索引约定相同**（`EndIdxList`，如 Ua、Ia 等），见 **`whole-group-test.md` §2.3** 中「`static` 的通道索引与状态序列相同」的说明。
- **阻抗试验**等对 `IWsParams` 与 `payload` 的分工，见 **`impedance-characteristic-test.md` §2.4**（差动**不使用**其顶层 `testPoints` 承载试验点的方式）。
- **`payload.connectionSettings`**（含 I1/I2 接测试仪端子选项、角度等）为 **保护/接线业务参数**，随启动包下发；**协议层面不规定**其与 `statics` 某通道键的一一对应，避免与整组通道枚举混为一谈。

### 2.6 试验点 `points[]`（右图线段 / 单点）

启动包中 **`projectSettings.ratio.points`** / **`harmonic.points`** 为**数组**，元素与右图 [`DifferentialChart.vue`](../../src/views/general-test/module/differential-test/components/DifferentialChart.vue) 中**已勾选**的虚线线段或实心点一一对应。编码见 [`differential-points-payload.ts`](../../src/views/general-test/module/differential-test/utils/differential-points-payload.ts)。

**边界搜索 = 线段**（一轴标量、另一轴为扫描区间 `[start, end]`）：

| `activeProject` | 元素形态 | 含义 |
|-----------------|----------|------|
| `ratio-search` | `{ id, x, y: [y0, y1] }` | 竖线：`x` = **Ir**（固定）；`y` = **Id** 扫描区间（`id_boundary × ratio.search.start/end / 100`，已排序） |
| `harmonic-search` | `{ id, y, x: [x0, x1] }` | 横线：`y` = **Id**（`id_val`，固定）；`x` = **Harm%** 扫描区间（`harmonic.search.start`～`end`，已排序） |

**定点 = 单点**（`x`、`y` 均为标量）：

| `activeProject` | 元素形态 | 含义 |
|-----------------|----------|------|
| `ratio-fixed` | `{ id, x, y }` | `x` = **Ir**，`y` = **Id**（`id_val`，无实测时用 `id_boundary`） |
| `harmonic-fixed` | `{ id, x, y }` | `x` = **Harm%**（`harm`），`y` = **Id**（`id_val`） |

**示例 — 比率边界搜索**：

```json
"points": [
  { "id": 1, "x": 0.5, "y": [0.0, 0.6] }
]
```

**示例 — 谐波边界搜索**：

```json
"points": [
  { "id": 1, "y": 0.5, "x": [1.0, 2.0] }
]
```

**示例 — 比率定点**：

```json
"points": [
  { "id": 1, "x": 0.5, "y": 1.0 },
  { "id": 2, "x": 1.2, "y": 0.72 }
]
```

下发的搜索控制字段中，`ratio` 仅携带 `precision`、`method`（直出字段，不再套 `search`）；线段几何以 `points[]` 元素内区间数组为准。`id` 与界面点表一致，供 **`report`** / **`value_update`** 回传时对齐行。

界面与右图为工程值；写入 WS 的 **`points[]`** 与界面一致（仅 round），见 §2.5、§6.1.12。

---

## 3. 仅前端 / 由运行推导的数据（一般不应作为启动唯一依据）

以下字段多为展示或运行期回填；**与 §7 已决议不冲突**处为准。

| 字段 | 说明 |
|------|------|
| `projectSettings.activePointIndex` | 当前选中行，仅 UI。 |
| `currentTrackingData` | 界面电流跟踪区；试验中由 `value_update` 的 `x`/`y` 实时刷新上方 Ir（或谐波 %）与 Id，并逆推 I1/I2 相量。 |
| `axisConfig` | 特性图坐标范围，仅视图。 |
| `searchSettings` | Store 预留，界面未绑定；可不下发。 |
| 点表 `id_boundary` | 用于计算 `ratio-search` 的 **`y: [y0,y1]`**；以前端值为准，**后端不强制按曲线重算**（除非后续协议变更）。 |
| 点表 `id_val` | **实测值**，来自 `report` 回填；定点时写入 `points[].y`，非启动必填。 |
| `payload` 内 **`auxVoltageData`** | 若存在，仅表示 Store/UI 状态；**下发音量以 `statics` 为准**（§2.3）。 |
| **`busbarKpParams`** | **仅界面**：母差支路 CT/KP 及 KP 计算弹窗；**不在启动 `payload` 中下发**（§7）。 |

---

## 4. 前端 -> 后端（运行中控制：第二个下发事件）

与其它模块一致：

### 4.1 停止

```json
{
  "module": "differential_test",
  "cmd": "stop"
}
```

### 4.2 `update_static`（运行中调值）

差动试验支持运行中通过 `update_static` 更新 `statics`（电流/辅助电压矩阵）；该事件不要求重复下发 `payload`。

```json
{
  "cmd": "update_static",
  "static": {
    "6": { "0": [0, 50], "1": [5.0, 0.0] },
    "9": { "0": [0, 50], "1": [4.8, 180.0] }
  }
}
```

> 与其它交流类界面一致：`static` 的通道索引与 `EndIdxList` 对齐（见 §2.5 引用）。

---

## 5. 后端 -> 前端（事件）

### 5.1 `value_update`（右图实测轨迹 + 开入开出）

前端路由为差动页且 `module === differential_test` 时，`ws.ts` 将 **整条 JSON 消息对象** 传给 `upsertResultFromValueUpdate`；**开入/开出**与滑差、阻抗等试验相同，在 [`ws.ts`](../../src/ws/ws.ts) `VALUE_UPDATE` 分支末尾统一刷新底部 Footer（`diRaw = ~di`，`doRaw = do`）。

差动专用字段（与 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 中 `DifferentialValueUpdatePayload` 一致）：

| 路径 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `module` | `string` | 是 | 固定 `differential_test` |
| `type` | `string` | 是 | 固定 `value_update` |
| `id` | `number` | 轨迹时必填 | 试验点 id，与启动包 `points[].id` 相同 |
| `x` | `number` | 轨迹时必填 | 右图横轴（比率=Ir，谐波=Harm%）；工程值 |
| `y` | `number` | 轨迹时必填 | 右图纵轴（Id，A）；工程值 |
| `static` | `object` | 否 | `StaticsConfig`；用于回填 I1/I2 电流跟踪区（通道 6–8 / 9–11） |
| `di` | `number` | 否 | 开入 raw 位图（与其它试验一致） |
| `do` | `number` | 否 | 开出 raw 位图（与其它试验一致） |

**差动不再解析** `value_update` 根级的 legacy 字段 `ir` / `id`（差流）/ `i1` / `i2`；电流以 `static` 或 `x`/`y` 为准。

根级示例（含轨迹 + 开入开出，推荐）：

```json
{
  "module": "differential_test",
  "type": "value_update",
  "id": 1,
  "x": 1.0,
  "y": 0.612,
  "static": {
    "6": { "0": [1.2, 0.0] },
    "9": { "0": [1.15, 180.0] }
  },
  "di": 255,
  "do": 15
}
```

仅刷新开入开出（无 `id`/`x`/`y` 时可省略轨迹更新）：

```json
{
  "module": "differential_test",
  "type": "value_update",
  "di": 255,
  "do": 15
}
```

说明：

- **开入/开出**：与 [`slip-test.md`](./slip-test.md) §4.1 相同语义；前端底部「开入」「开出」指示灯随每条 `value_update` 更新（不要求同时携带 `x`/`y`）。
- **右图青色实测轨迹**：当 `id`、`x`、`y` 均有效时，在图内追加一点 **`(x, y)`**（与 WS 工程坐标一致，仅 round）。**实心圆**（`#00d9ff`），与预设试验点区分；数量有上限滚动丢弃旧点。
- **电流跟踪区**：同步刷新 `currentTrackingData`——`x` 写入制动电流 Ir 或谐波含量 Harm%（界面同一输入，`x` 语义相同）；`y` 写入差流 Id；并调用方程逆推更新下方 I1/I2 相量幅值。
- **不**写入试验点表、**不**更新结果表（结果仍以 `report` 为准）。
- **清除实测轨迹的推荐时机**（前端已实现或可依赖）：
  1. **下一次「开始试验」**：下发 `start` 前清空（便于新一次试验从零轨迹开始）。
  2. **离开差动试验页**：组件卸载时清空（避免回来时误读上一班次轨迹）。
  3. **`stop`（试验结束）**：仅 **差动**：收到 `stop` 且当前为差动路由时清空 **`value_update` 流轨迹**（反时限不在此清除图上实测点，便于结束后对照曲线）。

  4. **重置界面 / 导入方案**：重置或导入后清空或重建状态。

---

### 5.2 `report`（试验点结果 → 结果视图表格）

WebSocket 上报时 **`report` 事件的载荷为 `message.data`**（即 `handleEventReport(receiveData.module, data.data)`）。前端 [`setReportFromEvent`](../../src/views/general-test/store/differential-test-store.ts) 解析 **`data.points[]`**，按元素 **`id`** 匹配点表行并回填；若根级无 `points` 数组，则将 **`data` 根级** 当作单条结果解析（逐点推送时的兼容路径）。

**`id` 语义**：与启动包 **`points[].id`** 一致；前端 `points.find(p => p.id === raw.id)` 定位行后，读取该行已有 **`id_boundary`（整定）** 等本地字段，并合并 report 实测值。

**`x` / `y` 与点表字段**（字段名不变，含义随 `activeProject` 变化，与启动包 [`differential-points-payload.ts`](../../src/views/general-test/module/differential-test/utils/differential-points-payload.ts) 一致）：

| `activeProject` | `x`（report → 点表） | `y`（report → 点表） | 点表整定（不由 report 覆盖） |
|-----------------|----------------------|----------------------|------------------------------|
| `ratio-search` | 制动电流 **Ir** → `ir` | 实测动作边界 **Id** → `id_val` | `id_boundary`（曲线计算） |
| `ratio-fixed` | **Ir** → `ir` | **Id** → `id_val` | — |
| `harmonic-fixed` | 谐波含量 **Harm%** → `harm` | **Id** → `id_val` | — |
| `harmonic-search` | 实测 **Harm%** → `harm` | 固定扫描 **Id**（一般与点表 `id_val` 一致；`y` 可回传同值） | `harm_boundary` |

**分项目结果视图列**（[`DifferentialResultModal.vue`](../../src/views/general-test/module/differential-test/DifferentialResultModal.vue)）：

| 项目 | 表头列 | 数据来源 |
|------|--------|----------|
| `ratio-search` | 序号；制动电流 Ir；动作边界(整定)；**动作边界**；相对误差；Kzd；[备注:I1侧电流]；[I2侧电流] | `ir`←report.x；`id_boundary`←点表；`id_val`←report.y；误差/Kzd 前端算；I1/I2←report |
| `ratio-fixed` | 序号；制动电流 Ir；动作电流 Id；动作时间；[I1]；[I2] | `ir`/`id_val`←report；`tripTimeMs`←report；I1/I2 可选列 |
| `harmonic-fixed` | 序号；动作电流 Id；谐波 Harm；动作时间 | `id_val`/`harm`←report；`tripTimeMs`←report |
| `harmonic-search` | 序号；动作电流 Id；制动边界(整定)；制动边界 Harm；相对误差 | `id_val`←点表（扫描固定 Id）；`harm_boundary`←点表；`harm`←report.x；误差相对 `harm_boundary` |

界面：顶部只读展示「测试项目」「测试相别」；**仅比率项目**显示「比率差动：显示I1,I2两侧电流」复选框。

#### `points[]` 元素字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | `number` | 是 | 与启动包 `points[].id` 一致，用于匹配点表行 |
| `x` | `number` | 是 | 见上表项目语义（比率=Ir，谐波=Harm%） |
| `y` | `number` | 是 | 见上表（一般为实测 Id → `id_val`） |
| `i1` | `number` | 比率项目需要时 | I1 侧电流（实数）；谐波项目可不返 |
| `i2` | `number` | 比率项目需要时 | I2 侧电流 |
| `tripTimeMs` | `number` | 定点项目需要时 | 动作时间（ms）；`ratio-fixed` / `harmonic-fixed` |

**不由后端返回**：`id_boundary` / `harm_boundary`（整定）、相对误差、Kzd、序号——前端按同 `id` 点表行与界面参数计算。

#### `report` 载荷示例

比率边界搜索：

```json
{
  "points": [
    { "id": 1, "x": 1.0, "y": 0.612, "i1": 1.2, "i2": 1.15 }
  ]
}
```

比率定点（含动作时间）：

```json
{
  "points": [
    { "id": 1, "x": 0.5, "y": 0.48, "i1": 1.0, "i2": 0.95, "tripTimeMs": 32 }
  ]
}
```

谐波边界搜索（`x` 为实测 Harm%）：

```json
{
  "points": [
    { "id": 1, "x": 12.5, "y": 0.6 }
  ]
}
```

TypeScript 类型见 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 中 `DifferentialReportPoint`。

### 5.3 `error` / `stop` / `load`

与其它模块一致；`stop` 表示试验正常结束。

---

## 6. 兼容与版本说明

- **`module` 字符串**：[`ENUM_WS_MODULE`](../../src/ws/enum.ts) 已定义 **`DIFFERENTIAL_TEST = "differential_test"`**（单一模块名，与 §7 一致）。
- **枚举值**：与 [`differential-test-enum.ts`](../../src/views/general-test/enums/differential-test-enum.ts) 一致，完整取值见 **§6.1**；字符串 slug 建议后端按字面量解析，数字枚举见各小节 `ENUM_*` 定义。
- **`value_update` / `report` 字段**：可与 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 对齐做 TypeScript 校验（§5）。

---

## 6.1 枚举定义（与前端一致）

以下枚举与选项表均来自 [`differential-test-enum.ts`](../../src/views/general-test/enums/differential-test-enum.ts)。**启动包 `payload` 中字符串字段**（如 `idEquation`、`activeProject`）取下表 **value**；**数字字段**（如 `testParams.diffMode`）取下表 **数值**。母差形态（`variant: busbar`）**不含** `harmonic-*` 项目与 `projectSettings.harmonic` 块。

#### 6.1.1 形态（`payload.variant`）

组包时由 `ENUM_DIFFERENTIAL_TEST_TYPE` 映射为字符串（非直接下发数字）：

- 路由 / Store `baseSettings.testType`（`ENUM_DIFFERENTIAL_TEST_TYPE`）
  - `0` → 下发 `six_channel`：六路电流差动（`/differential-test-6ch`）
  - `1` → 下发 `busbar`：母差保护（`/differential-test-busbar`）

#### 6.1.2 试验项目（`payload.projectSettings.activeProject`）

- `payload.projectSettings.activeProject`（`differentialTestProjectOptions`）
  - `ratio-search`: 比率差动--边界搜索
  - `ratio-fixed`: 比率差动--定点测试
  - `harmonic-fixed`: 谐波制动--定点测试（**仅** `six_channel`）
  - `harmonic-search`: 谐波制动--边界搜索（**仅** `six_channel`）

#### 6.1.3 测试相别（`payload.projectSettings.testPhase`）

- `payload.projectSettings.testPhase`（六路：`differentialTestPhaseOptions`；母差界面：`single`）
  - `A`: A 相差动
  - `B`: B 相差动
  - `C`: C 相差动
  - `AB`: AB 相差动
  - `BC`: BC 相差动
  - `CA`: CA 相差动
  - `ABC`: ABC 相差动
  - `single`: 单相差动（**仅母差**界面选项）

#### 6.1.4 动作方程（`payload.equationSettings.idEquation`）

- `payload.equationSettings.idEquation`（`idEquationOptions`）
  - `id_i1`: Id = I1
  - `id_sum`: Id = |I1' + I2'|
  - `id_diff`: Id = |I1' - I2'|
  - `id_sum_sq`: Id = |I1' + I2'|^2
  - `id_diff_sq`: Id = |I1' - I2'|^2

#### 6.1.5 制动方程（`payload.equationSettings.irEquation`）

- `payload.equationSettings.irEquation`（`irOptionsForIdI1`，仅当 `idEquation=id_i1`）
  - `ir_i2`: Ir = I2
- `payload.equationSettings.irEquation`（`irOptionsGeneral`，六路常用）
  - `ir_diff_k`: Ir = |I1' - I2'|/K
  - `ir_sum_k`: Ir = |I1' + I2'|/K
  - `ir_max_k`: Ir = Max(|I1'|, |I2'|) * K
  - `ir_i2_k`: Ir = |I2'| * K
  - `ir_id_abs_diff`: Ir = |Id - |I1'| - |I2'||
  - `ir_sum_div_k`: Ir = (|I1'| + |I2'|) / K
  - `ir_imax_sum_k`: Ir = |Imax' - ΣIi'|/K, Ii' ≠ Imax'
  - `ir_sqrt_cos`: Ir = √(-|I1'×I2'| * cosθ)
  - `ir_sum_sq_cos`: Ir = -|I1' * I2'| * cosθ（配合 `id_sum_sq`）
  - `ir_diff_sq_cos`: Ir = |I1' * I2'| * cosθ（配合 `id_diff_sq`）
- `payload.equationSettings.irEquation`（`busbarIrEquationOptions`，母差扩展）
  - `ir_bp2`: Ir = |Id-|I1'|-|I2'||
  - `ir_sum`: Ir = |I1'|+|I2'|

#### 6.1.6 搜索方式

- `payload.projectSettings.ratio.method` / `payload.projectSettings.harmonic.search.method`（`searchMethodOptions`）
  - `binary`: 二分法搜索
  - `linear`: 线性搜索

#### 6.1.7 接线（`payload.connectionSettings`）

- `payload.connectionSettings.type`（六路：`connectionTypeOptions`）
  - `ext-differential`: 扩展差动 (6I)
  - `phase-differential`: 分相差动 (2I)
- `payload.connectionSettings.type`（母差：`busbarConnectionTypeOptions`）
  - `ext-5i`: 扩展接线 (5I)
  - `simple-3i`: 简化接线 (3I)
- `payload.connectionSettings.i1Terminal` / `i2Terminal`（六路扩展：`terminalOptions6I`）
  - `high-y`: 接测试仪 Ia,Ib,Ic
  - `low-y`: 接测试仪 Ix,Iy,Iz
- `payload.connectionSettings.i1Terminal` / `i2Terminal`（六路分相 / 母差：`terminalOptions2I`）
  - `ia`: 接测试仪 Ia
  - `ib`: 接测试仪 Ib
  - `ic`: 接测试仪 Ic
  - `ix`: 接测试仪 Ix
  - `iy`: 接测试仪 Iy
  - `iz`: 接测试仪 Iz
  - `iab`: 接测试仪 Iab两并
  - `ibc`: 接测试仪 Ibc两并
  - `ica`: 接测试仪 Ica两并
  - `iabc`: 接测试仪 Iabc三并
  - `ixy`: 接测试仪 Ixy两并
  - `iyz`: 接测试仪 Iyz两并
  - `izx`: 接测试仪 Izx两并
  - `ixyz`: 接测试仪 Ixyz三并

#### 6.1.8 母差设置（`payload.busbarSettings`，`variant: busbar`）

- `payload.busbarSettings.model`（`ENUM_BUSBAR_MODEL` / `busbarModelOptions`）
  - `pcs_915`: PCS-915 系列
  - `bp_2`: BP-2 系列
  - `rcs_915`: rcs-915 系列
  - `other`: 其他型号
- `payload.busbarSettings.subModel`（`ENUM_BUSBAR_SUB_MODEL` / `busbarSubModelOptions`）
  - `high_grand`: 大差高值
  - `low_grand`: 大差低值
  - `high_i`: I 母小差高值
  - `low_i`: I 母小差低值
  - `high_ii`: II 母小差高值
  - `low_ii`: II 母小差低值
- `payload.busbarSettings.windingDir`（`ENUM_BUSBAR_WINDING_DIR` / `busbarWindingDirOptions`）
  - `towards_i`: 朝向 I 母
  - `towards_ii`: 朝向 II 母

#### 6.1.9 试验参数（`payload.testParams`）

启动包中仅下发 5 个字段：

- `payload.testParams.diffMode`（`ENUM_DIFFERENTIAL_MODE` / `differentialModeOptions`）
  - `0`: 稳态差动 (故障电流不叠加负荷)
  - `1`: 零序差动
  - `2`: 工频变化量/突变量差动
- `payload.testParams.faultDuration`
- `payload.testParams.preFaultDuration`
- `payload.testParams.simulatePreFault`
- `payload.testParams.loadCurrent`

#### 6.1.10 六路保护设置（`payload.protectionSettings`，`variant: six_channel`）

启动包**仅下发**下列 6 个字段（与界面「保护设置」红框一致）。编码函数：`encodeVectorGroupTransmission`（[`differential-vector-group.ts`](../../src/views/general-test/module/differential-test/utils/differential-vector-group.ts)）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `vectorGroupLetter1` | `string` | 铭牌第 1 段联结字母，小写（如 `Y` → `y`） |
| `vectorGroupLetter2` | `string` | 另一侧联结字母，小写；三绕组取**第 3 段**字母（跳过第 2 段） |
| `vectorGroupHighSideClock` | `string` | 高压侧钟点数字；取铭牌**第 1 段**尾部数字，无则 `"0"` |
| `vectorGroupLowSideClock` | `string` | 低压侧钟点数字；双绕组取第 2 段、三绕组取第 3 段尾部数字，无则 `"0"` |
| `phaseCorrection` | `string` | `phaseCorrectionOptions`：`none` \| `y-side` \| `delta-side` |
| `zeroSequenceCorrection` | `boolean` | Y 侧零序校正 |

**接线组别 slug → 字母与钟点**（界面 `vectorGroup` value + `windingType` 仅用于编码，不下发 slug）：

| 绕组 | 界面 slug（示例） | `letter1` | `letter2` | `highClock` | `lowClock` |
|------|-------------------|-----------|-----------|-------------|------------|
| 三绕组 | `Y,y,y0` | `y` | `y` | `0` | `0` |
| 三绕组 | `Y,yn,d1` | `y` | `d` | `0` | `1` |
| 双绕组 | `Y,d11` | `y` | `d` | `0` | `11` |
| 双绕组 | `Y,y0` | `y` | `y` | `0` | `0` |

**仅前端 Store / 导入导出、不下发启动包**：`protectionObject`、`windingType`、`vectorGroup`（逗号 slug）、`involvedWinding`、`involvedPairIndex`、`vectorGroupBalanceAngle`（仍用于计算 `connectionSettings.i2Angle` 等）。

**legacy 导入映射**（slug 规范化，仍用于 XML/界面）：`Y/Δ-11` → `Y,d11`；`high-low` + `Y,yn,d11` → `Y,d11`；`high-mid` → `Y,yn`；`mid-low` → `yn,d11`

#### 6.1.11 谐波项目（`payload.projectSettings.harmonic`，仅 `six_channel`）

- `payload.projectSettings.harmonic.order`（`harmonicOrderOptions`）
  - `2`～`20`: 2 次谐波 … 20 次谐波（数值为次数）
- `payload.projectSettings.harmonic.applySide`（`harmonicApplySideOptions`）
  - `i1`: I1 侧
  - `i2`: I2 侧
- `payload.projectSettings.harmonic.harmonicErrorType`（Store 字符串，界面相对/绝对误差）
  - `relative`: 相对误差（%）
  - `absolute`: 绝对误差

#### 6.1.12 试验点 `points[]`（§2.6）

- `ratio-search`：`{ id, x:number, y:[number,number] }`
- `harmonic-search`：`{ id, y:number, x:[number,number] }`
- `ratio-fixed` / `harmonic-fixed`：`{ id, x:number, y:number }`

**数值口径**：启动包 `points[]` 与 `value_update` 的 `x`/`y`（及区间端点）均为工程值，经组包/归一化后**仅做精度 round**，**不做** ÷In / ×In 标幺换算。

**仅前端、不下发启动包**（供对照）：`ratio.curveSegments[].type` 为 `threshold` | `slope` | `instant`；`kpCalcParams`；点表 `selected` / `status`；`protectionSettings` 中除 §6.1.10 五字段外的保护类型/参与绕组等。

---

## 7. 已决议口径（联调用）

1. **`module`**：**合并**为单一 `"differential_test"`，**不**拆成两个 WebSocket `module`；6 路 / 母差用 **`payload.variant`**（`six_channel` | `busbar`）区分。  
2. **试验点列表**：**不下发** `IWsParams.testPoints`；试验点**仅**通过 **`payload.projectSettings.ratio.points`** / **`harmonic.points`** **数组**传递（线段或单点，§2.6），后端按数组顺序与 **`id`** 执行。  
3. **电流下发**：使用 **`params.statics`**（及按需 `limits` / `steps` / `count`）。**`statics` 通道索引约定**与整组/状态序列一致：**`statics` 的通道索引与状态序列相同（`EndIdxList`，如 Ua/Ia 等）**，见 [`whole-group-test.md`](./whole-group-test.md) **§2.3**；同顶层 `IWsParams` 形态可对照 [`impedance-characteristic-test.md`](./impedance-characteristic-test.md) **§2.4**。**不再**约定「`connectionSettings.i1Terminal` / `i2Terminal` = 某一仪器通道下标」；I1/I2 接线选项仍只在 **`payload.connectionSettings`** 中作为业务字段下发。
4. **`id_boundary`**：**以前端给出的值为准**；`ratio-search` 时用于计算各元素 **`y: [y0,y1]`**；后端默认**不**用 `curveSegments` 覆盖重算。  
5. **母差 `connectionSettings`**：**需要**端子级字段（如 `type` 及 **`i1Terminal` / `i2Terminal` / 角度**）；若将来协议简化再删。  
6. **辅助电压**：**必须经仪器表达时**，在 **`statics` 中定义**；**不在 `payload` 中**用 `auxVoltageData` 作为下发音量载体（组包时把界面量写入 `statics`）。  
7. **谐波试验**：**仅在六路电流试验界面**（`/differential-test-6ch`）；**母差界面无谐波项目**。协议中 **`projectSettings.harmonic`、`activeProject` 为 `harmonic-*` 仅适用于 `six_channel`**；`report` 中谐波相关字段亦仅在此形态约定。
8. **参数收敛**：`kpCalcParams`、`curveSegments`、`error` 不下发；启动包 `points[]` 仅含已选线段/点（§2.6，无 `selected`/`status`）。`report` 按 **`id`** 回写点行实测字段。  
9. **`payload.terminals`**：**不约定、不下发**（各侧 CT 等若需应由既有字段或后续扩展另行约定）。  
10. **`payload.busbarKpParams`**：**不下发**。母差支路 CT/KP 仅在前端 Store / KP 计算弹窗维护；启动包只需 **`busbarSettings`**、**`equationSettings`**、**`connectionSettings`**、**`projectSettings`** 等（§2）。
11. **边界搜索**：`ratio-search` 为 **`{ id, x, y:[y0,y1] }`**；`harmonic-search` 为 **`{ id, y, x:[x0,x1] }`**（§2.6），并配合搜索控制字段（`ratio.method/precision`、`harmonic.search`）。**不**再下发嵌套 `searchLine` 对象。

---

## 8. 右图绘制规则（现状）

本节用于说明“添加测试点后，差动试验右侧图形如何绘制”的前端现行规则，便于联调与回归核对。实现主入口见 [`DifferentialChart.vue`](../../src/views/general-test/module/differential-test/components/DifferentialChart.vue) 与 [`DifferentialConfigTabs.vue`](../../src/views/general-test/module/differential-test/components/DifferentialConfigTabs.vue)。

### 8.1 数据来源优先级

1. 右图测试点仅取当前项目点集：
   - `ratio-*`：`projectSettings.ratio.points`
   - `harmonic-*`：`projectSettings.harmonic.points`
2. “添加”按钮生成测试点时，数据取自“添加测试点弹窗”（`addPointForm`），不是下方搜索参数输入框。
3. 下方搜索参数（`search.start/end/precision/method`）当前主要用于启动包 `payload`，不会在“点击添加”时直接生成或重排右图静态点。

### 8.2 四种项目的点位映射与搜索线

| `activeProject` | 右图坐标 | 搜索线（界面） | 启动包 `points[]` 元素 |
|---|---|---|---|
| `ratio-search` | 竖线 | `x=ir`，`y` 从 `id_boundary×search.start%` 到 `id_boundary×search.end%` | `{ id, x: ir, y: [y0, y1] }` |
| `ratio-fixed` | 单点 | 无 | `{ id, x: ir, y: id_val }` |
| `harmonic-fixed` | 单点 | 无 | `{ id, x: harm, y: id_val }` |
| `harmonic-search` | 横线 | `y=id_val`，`x` 从 `harmonic.search.start` 到 `end` | `{ id, y: id_val, x: [x0, x1] }` |

其中，测试点只有 `selected=true` 时才绘制；当前选中行（`activePointIndex`）会额外绘制十字光标与外圈高亮。组包时仅已选线段/点进入 **`points[]`**（见 §2.6）。

### 8.3 比率边界 `id_boundary` 的生成规则

- `ratio-search` 添加点时，`id_boundary` 即时由 `calculateRatioBoundary(ir)` 计算。
- 计算逻辑按 `curveSegments` 顺序分段：
  - `threshold`：`Id = idMin`
  - `slope`：`Id = startId + (Ir - irStart) * k`
  - `instant`：当 `Ir >= irStart` 时，`Id = idInst`
- 曲线定义确认后，`refreshRatioBoundaryPoints()` 会重算已有点的 `id_boundary`。

### 8.4 曲线/区域与参数的对应关系

- 比率类曲线（含误差带）由以下参数直接决定：`idMin`、`idInst`、`curveSegments`、`error`。
- 谐波类曲线（含误差带）由以下参数直接决定：`harmonicConstant`、`idMin`、`idInst`、`harmonicError`。
- 图中青色实心点（`value_update` 轨迹）来自运行时下行事件，不属于“添加测试点”的静态绘制结果。

### 8.5 视角自动重置（auto-fit）规则

- 触发时机：测试点数量变化、项目切换、首次挂载（含容器首帧可见）。
- 取值策略：综合“曲线范围 + 当前点集最大 x/y”，再统一乘 `1.2` 留白。
- 刻度步长：按 `1/2/5/10 × 10^n` 取 `nice step`，用于主刻度与网格计算。

---
