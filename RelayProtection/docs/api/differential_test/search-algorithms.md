# 搜索算法：二分法与遍历法

> **代码入口**: [`ApiDiffTest.py#_run_search`](../../api/ApiDiffTest.py)

---

## 1. 搜索方向自动判断

后端根据 `points[]` 元素中哪个轴是区间数组，自动判断搜索方向：

| 前端点格式 | 搜索方向 | 固定值 | 搜索范围 |
|:---|:---|:---|:---|
| `{ id, x: 1.0, y: [0, 0.72] }` | **搜 y 轴** (Id) | ir = x (固定) | Id ∈ [y[0], y[1]] |
| `{ id, y: 0.5, x: [1.0, 2.0] }` | **搜 x 轴** (Harm%) | Id = y (固定) | Harm% ∈ [x[0], x[1]] |

```python
if isinstance(y_val, list):   # y 是区间 → ratio-search
    axis = "y", fixed = x_val
elif isinstance(x_val, list):  # x 是区间 → harmonic-search
    axis = "x", fixed = y_val
```

---

## 2. 二分法 (binary)

### 2.1 算法流程

```mermaid
flowchart TD
    A["初始化<br/>lo = search_min<br/>hi = search_max"] --> B{"hi - lo > precision?"}
    B -->|是| C["mid = (lo + hi) / 2"]
    C --> D["执行单点测试<br/>_test_one_point(ir, mid)"]
    D --> E["发送 value_update<br/>(每步都发)"]
    E --> F{保护动作?}
    F -->|是 (tripped)| G["hi = mid<br/>记录动作边界"]
    F -->|否| H["lo = mid"]
    G --> B
    H --> B
    B -->|否| I{找到动作边界?}
    I -->|是| J["发送 report<br/>(最终边界值)"]
    I -->|否| K["不发 report"]
```

### 2.2 关键行为

| 特性 | 说明 |
|:---|:---|
| **收敛条件** | `hi - lo ≤ precision`（前端配置的搜索精度） |
| **搜索方向** | 始终从小到大（lo → hi），动作后缩小上界 |
| **中点计算** | `mid = (lo + hi) / 2`，标准二分 |
| **动作判定** | 保护 DI 在 Node 2 故障持续期间触发 → tripped = True |
| **value_update** | **每步**都发（无论动作与否），用于前端右图实时轨迹 |
| **report** | 仅搜索结束后且**找到动作边界**时发送一次 |
| **搜索步数** | ≈ log₂((hi - lo) / precision)，如区间 0.72、精度 0.01 → ≈ 7 步 |

### 2.3 伪代码

```python
lo, hi = search_min, search_max
found_trip = False

while (hi - lo) > precision:
    mid = (lo + hi) / 2
    
    # 确定 ir 和 id
    if axis == "y":
        ir, id_val = fixed_val, mid
    else:
        ir, id_val = mid, fixed_val
    
    tripped = await _test_one_point(ir, id_val)
    
    # 每步发送实时轨迹
    send_value_update(id=pt_id, x=ir, y=id_val)
    
    if tripped:
        hi = mid         # 缩小上界
        found_trip = True
        result = mid
    else:
        lo = mid         # 抬高下界

# 只有找到动作边界才报告
if found_trip:
    send_report(id=pt_id, x=..., y=result, i1=..., i2=...)
```

---

## 3. 遍历法 (linear)

> **注意**: 遍历法使用与二分法相同的 `_binary_search` 接口，通过 `method` 参数区分。当前代码默认使用二分法。遍历法的实现逻辑为：

### 3.1 算法流程

```
从 lo 开始，步长 = precision
依次测试 lo, lo+step, lo+2×step, ...
找到第一个动作点 → 记录为边界 → 停止搜索
```

### 3.2 与二分法的对比

| 特性 | 二分法 (binary) | 遍历法 (linear) |
|:---|:---|:---|
| **搜索步数** | O(log₂(N)) | O(N), N = range/precision |
| **精度** | 由 precision 保证 | 由步长 = precision 保证 |
| **速度** | 快（7步 vs 72步） | 慢但逻辑简单 |
| **适用场景** | 常规搜索（推荐） | 需要密集轨迹的调试场景 |
| **停止条件** | hi - lo ≤ precision | 找到第一个动作点 |

---

## 4. 定点测试 (ratio-fixed)

定点测试不进行搜索，直接逐点执行：

```python
for pt in points:
    ir, id_val = pt["x"], pt["y"]
    tripped = await _test_one_point(ir, id_val)
    
    send_value_update(...)    # 每个点都发轨迹
    if tripped:
        send_report(...)      # 只有动作才报告
```

### 定点 vs 搜索对比

| 特性 | 定点 (ratio-fixed) | 搜索 (ratio-search) |
|:---|:---|:---|
| **points 格式** | `{ id, x, y }` (标量) | `{ id, x, y: [lo, hi] }` (区间) |
| **每点测试次数** | 1 次 | log₂(range/precision) 次 |
| **report 发送条件** | 动作即报告 | 搜索结束+找到边界才报告 |
| **value_update** | 每点 1 次 | 每步 1 次 |

---

## 5. 单点测试流程 (_test_one_point)

无论搜索算法如何，每次单点测试的内部流程完全相同：

```
1. isPerUnit 校准：如果标幺值模式，ir/id 乘以 Ie
2. 计算故障电流：调用 compute_6ch (6I) 或 compute_2i (2I) + 分流
3. 更新 Node 2：写入故障电流到 FSM
4. 触发测试：trigNode(1→2→3)
5. 等待结果：await _node_done (DI 动作或超时)
6. 返回：(tripped, statics, i1_amp, i2_amp)
```

**FSM 时序**：

```
Node 1 (故障前)  →  Node 2 (故障态)  →  Node 3 (挂起)
零电流+额定电压      计算的故障电流        零电流+额定电压
preFaultMs           faultDurationMs       等待下一轮
                     等待 DI / 超时
```
