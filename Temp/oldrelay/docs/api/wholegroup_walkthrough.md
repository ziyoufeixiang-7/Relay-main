# WholeGroupTest 整组试验技术说明

> **模块**: `whole_group_test`
> **Handler**: `api/WholeGroupTestHdl.py`
> **继保用途**: 模拟完整保护动作流程，测量跳闸/重合闸时间

---

## 1. 继电保护背景

### 1.1 什么是整组试验？

整组试验是继电保护最完整的功能测试，模拟从故障发生到断路器动作的**完整闭环**。与状态序列不同，整组试验的节点由 Handler 自动生成，包含跳闸态/重合态的自动编排。

### 1.2 典型流程

```
故障前(额定电量) →[触发]→ 故障态(短路电流) →[跳闸DI]→ 跳闸态(电流清零)
    →[重合闸DI]→ 重合态(恢复/永久故障) →[二次跳闸DI]→ 终止
```

### 1.3 与 StatusSequence 的关系

| 特性 | StatusSequence | WholeGroupTest |
|------|---------------|----------------|
| 节点 | 用户自定义 N 个 | Handler 自动生成 6-9 个 |
| static | 用户自定义 | 前端计算相量，Handler 生成跳闸态 |
| 报告 | 通用 duration + DI SOE | 跳闸A/B/C + 重合闸时间 |
| DO | 用户配置 | 根据 controlMode 自动生成 |

---

## 2. 前端参数详解

### 2.1 `faultSettings` — 故障配置

| 参数 | 含义 |
|------|------|
| `firstFault.type` | 故障类型(0-9): A接地/B接地/.../三相短路 |
| `faultNature` | 0=瞬时性, 1=永久性 |
| `testLimitMs` | 试验限时(ms) |
| `transition.enabled` | 是否启用故障转换 |
| `transition.transitionMoment` | 0=故障后转换, 1=重合后转换 |
| `transition.transitionTimeMs` | 转换延时(ms) |

### 2.2 `triggerMode` — 触发方式

| 值 | 含义 | Handler 处理 |
|---|------|-------------|
| 0 | 按键触发 | `manualTrigTargets[0] = 1` |
| 1 | 时间触发 | `TIME_OUT` trigger |
| 2 | 开入触发 | `DI_MATCH` trigger |

### 2.3 `binaryInput` — 开入功能映射

| function | 继保含义 |
|----------|---------|
| 0 | 跳 A 接点 |
| 1 | 跳 B 接点 |
| 2 | 跳 C 接点 |
| 3 | 三跳接点 |
| 4 | 重合闸接点 |
| 5 | 其他信号 |

### 2.4 `binaryOutput` — 开出控制

| controlMode | 含义 | DO 行为 |
|-------------|------|--------|
| 0 | 跟随跳合闸 | 跳闸→mask=0, 重合→mask=initialMask |
| 1 | 延时翻转 | 故障后 delay ms → mask=0 → hold ms → mask=initialMask |
| 2 | 自定义 | 使用 doSequence 序列 |

---

## 3. 内部节点生成

### 3.1 节点映射

```
Engine ID    Front ID    角色              Static 来源
────────────────────────────────────────────────────
0            0           故障前             前端 node0.static
1            1           故障态             前端 node1.static (+ DC comp)
10           1           跳闸延时桩(20ms)   复用 node1.static
2            2           跳闸态             _GetTripStatic: 电流清零, Ux覆写
20           2           重合延时桩(20ms)   复用跳闸态 static
3            3           重合态             瞬时→node0.static / 永久→node1.static
30           3           二次跳闸延时桩     复用重合态 static
4            4           二次跳闸态         复用跳闸态 static
5 (可选)     5           故障转换           前端 node2.static
50 (可选)    5           转换延时桩(20ms)   复用转换 static
1001 (可选)  1           DC 分量衰减        CompileDcComp 生成
```

### 3.2 跳闸态生成 (`_GetTripStatic`)

```python
# 1. 电流清零: 所有电流通道 layer amp = 0
# 2. 电压复位: 保持故障态电压（PT位置由前端决定）
# 3. Ux 覆写: ch3.layer1 = uxAfterTrip.amplitude/angle
```

### 3.3 DI 掩码构建 (`_BuildDiMask`)

根据 `fault_type` 和 `binaryInput.settings[].function` 映射：

```python
# 故障态: 监听跳闸接点(0/1/2) + 三跳(3)
#   A_GROUND(0): 跳A + 三跳
#   AB_PHASE(3): 跳A + 跳B + 三跳
#   三相(9): 跳A + 跳B + 跳C + 三跳

# 跳闸态: 监听重合闸接点(4)
```

### 3.4 MANUAL_TRIG

```python
# prefault condition == "MANUAL_TRIG":
#   node0.triggers = []  (无引擎 trigger)
#   manualTrigTargets[0] = 1  (手动触发 → Node 1)
```

CmdRouter 的 `next_node` 命令检查 `manualTrigTargets` → `FireManualTrigger(1)` ✅

---

## 4. DI SOE 与报告

### 4.1 事件流

```
Engine → value_update(tick=0) → 记录 nodeEntryHwTs
Engine → di_change → _ProcessDiChange → 记录各路 DI 首次变位时间
Engine → terminal → _BuildReport → 聚合跳闸/重合时间
```

### 4.2 报告字段

```json
{
  "tripA": 120.5,      // A相跳闸时间(ms), null=未动作
  "tripB": 118.0,
  "tripC": 119.0,
  "recloseR": 450.0,   // 重合闸时间(ms), null=未重合
  "diA": 1, "diB": 1, "diC": 1, "diR": 1,
  "dia": 0, "dib": 0, "dic": 0, "dir": 0
}
```

**前端推导公式**:
- 重合延时 = recloseR − (max(tripA,B,C) + 断路器跳闸延时 + 去抖)
- 后加速 = min(tripA,B,C) − (上次recloseR + 合闸延时 + 去抖)

---

## 5. 代码审查发现

### 🔴 Bug #4: CompileStart 返回格式不兼容 (已修复)

**问题**: 返回 `{"cmd":"start", "module":"universal_sequence", "params":{...}}`，但 `CmdRouter.StartEngine` 期望 `{"sys":{}, "nodes":[]}`。

**修复**: 改为直接返回 `{"sys":{...}, "nodes":[...]}`

### 🔴 Bug #5: 缺少 InterceptWsEvent (已修复)

**问题**: CmdRouter L141 调用 `self.active_hdl.InterceptWsEvent(payload)` 但 WGT 没有此方法 → `AttributeError` 运行时崩溃。

**修复**: 添加完整的 `InterceptWsEvent` 实现，包含 `di_change`/`value_update`/`trigger`/`terminal` 四种事件处理，以及独立的 DI SOE 跟踪和报告生成。

### 🔴 Bug #6: 缺少 manualTrigTargets (已修复)

**问题**: 当 `prefault_trigger.condition == "MANUAL_TRIG"` 时，node0 没有 trigger，CmdRouter 的 `next_node` 路由找不到目标 → 按键无响应。

**修复**: 添加 `manualTrigTargets[0] = 1`，让 CmdRouter 正确路由手动触发。
