# SlipTest 滑差试验技术说明

> **模块**: `slip_test`
> **Handler**: `api/SlipTestHdl.py`
> **继保用途**: 同期/解列/频率滑差保护测试

---

## 1. 继电保护背景

### 1.1 什么是滑差试验？

滑差试验用于测试同期装置、解列装置和频率保护装置。核心原理：从一个频率开始,以固定速率**变化频率/幅值/相位**,检测保护装置在什么值动作。

典型测试对象：
- **同期装置**: 发电机并网时,检测频差/压差/角差是否在允许范围
- **解列装置**: 系统振荡时,检测滑差频率超限后的解列动作
- **低频减载**: 频率下降到设定值时的减载动作

### 1.2 五种测试类型

| testType | 名称 | 搜索变量 | 说明 |
|----------|------|---------|------|
| 0 | 动作值搜索 | 幅值/相位/频率终点 | 逐次逼近找动作临界值 |
| 1 | 动作时间测量 | — | 固定参数,测量 DI_MATCH 时间 |
| 2 | 滑差锁定搜索 | 变化速率(ddt) | 搜索临界变化速率 |
| 3 | 电流锁定搜索 | 锁定电流值 | 搜索电流锁定阈值 |
| 4 | 电压锁定搜索 | 锁定电压值 | 搜索电压锁定阈值 |

---

## 2. 前端参数详解

### 2.1 核心参数

| 参数 | 含义 | 继保意义 |
|------|------|----------|
| `slipPhasor` | 变化量类型 | 0=幅值, 1=相位, 2=频率 |
| `slipStart` | 起始值 | 开始扫描的起点 |
| `slipDDt` | 变化速率(/s) | 每秒变化量 |
| `slipEnd` | 终止值(testType=1,2) | 扫描的终点 |
| `slipVarType` | 变量类型 | `"0_0"`=相电压/电流, `"2_0"`=线电压, `"5"`=全通道 |
| `slipPhase` | 目标通道 | 逗号分隔的通道索引 |

### 2.2 搜索参数 (testType=0/2/3/4)

| 参数 | 含义 |
|------|------|
| `actionSearchStart` / `ddtSearchStart` / `lockSearchStart` | 搜索起始值 |
| `actionSearchStep` / `ddtSearchStep` / `lockSearchStep` | 搜索步长 |
| `actionSearchEnd` / `ddtSearchEnd` / `lockSearchEnd` | 搜索终止值 |

### 2.3 Timer 参数 (testType=1)

| 参数 | 含义 |
|------|------|
| `timerStartValue` | 计时起点值 |

---

## 3. 三节点循环设计

每轮搜索使用固定的 3 个引擎节点：

```
┌─────────────────────────────────────────────────────┐
│  Node 0 (Reset)                                      │
│  输出起始值, 等待 returnTime ms                       │
│  → TIME_OUT → Node 1                                 │
├─────────────────────────────────────────────────────┤
│  Node 1 (Sweep)                                      │
│  从起始值以 delta/step 步进                           │
│  → DI_MATCH → -2 (动作,终止)                         │
│  → COUNT_OVER → Node 2 (扫完未动作)                  │
├─────────────────────────────────────────────────────┤
│  Node 2 (Wait at endpoint)                           │
│  驻留在终点值, 等待 waitTime ms                       │
│  → DI_MATCH → -2 (动作,终止)                         │
│  → TIME_OUT → Node 0 (搜索模式循环)                  │
│             → -2     (非搜索模式终止)                  │
└─────────────────────────────────────────────────────┘
```

### 3.1 步进计算

```python
SLIP_INTERVAL_MS = 10  # 每步 10ms
delta_per_step = slipDDt × (10 / 1000)  # 每步变化量
count = |endpoint - slipStart| / |delta_per_step|  # 总步数
```

### 3.2 diMask bit9

与 InvTimeIt 相同: `di_mask = logicMask | 0x200`，使用进入节点时的 DI 作为变位参考。

---

## 4. 搜索迭代机制

### 4.1 流程 (testType=0 动作值搜索)

```
Round 1: sweep to searchStart   →[no DI]→ 
Round 2: sweep to searchStart + step → [no DI]→
...
Round N: sweep to searchStart + N×step → [DI_MATCH!]→ 动作值 = searchValue
```

### 4.2 internal_hot_update

每轮搜索完成（Node 2 TIME_OUT → Node 0）后，Handler 通过 `internal_hot_update` 重编译节点：

```python
# InterceptWsEvent 返回:
{
    "internal_hot_update": True,
    "nodes": self._RecompileSearchNodes()
}
```

CmdRouter 接收后调用 `_ApplyHotUpdate` 更新引擎节点，使下一轮使用新的 searchValue。

> [!IMPORTANT]
> 搜索模式下引擎不停止——Node 2 → Node 0 循环继续，但 Handler 在每次循环时更新搜索参数。

---

## 5. 遥测处理

### 5.1 物理值反算

```python
# Node 1 Sweep 期间:
current_val = slipStart + tick × delta_per_step

# 频率模式(phasor=2): 更新 layer["0"][1]
# 幅值模式(phasor=0): 更新 layer["1"][0]
# 相位模式(phasor=1): 更新 layer["1"][1]
```

### 5.2 Timer 测量 (testType=1)

```python
# timerTick = |timerStartValue - slipStart| / |delta_per_step|
# 当 tick == timerTick 时记录 hw_ts
# DI_MATCH 时计算: actionTime = (trigger_hw_ts - timerStartHwTs) / 1000
```

---

## 6. 报告结构

根据 testType 不同，报告字段不同：

| testType | 报告字段 | 含义 |
|----------|---------|------|
| 0 | `actionValue` | 动作临界值 |
| 1 | `actionTime` | 动作时间(ms) |
| 2 | `slipLockValue` | 滑差锁定临界速率 |
| 3 | `currentLockValue` | 电流锁定阈值 |
| 4 | `voltageLockValue` | 电压锁定阈值 |

---

## 7. 代码审查

### ✅ 正确实现
- 5种测试类型的搜索逻辑
- 3节点循环设计(reset→sweep→wait)
- diMask bit9 进入参考
- 搜索迭代 + hot_update
- Timer 计时精度(hw_ts)
- 线电压 √3 修正

### ⚠️ 已修复
- `di_change` 事件处理(Bug #3)
