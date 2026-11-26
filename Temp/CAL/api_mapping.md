# 线路保护 API 参数映射指南

在 `line-protection-api-v2.md` 中定义的完整请求结构中，数据分为 `payload` (全局配置) 和 `tests -> points` (局部故障点) 两级。

本文档说明如何将前端发来的 JSON 数据，解构并映射到底层大一统函数 `calculate_fault` 的输入参数中。

---

## 1. 底层函数签名
```python
def calculate_fault(
    ft, sys_v, i_load_amp, i_load_ang, 
    dir_val, i_f_amp, z_mag, z_ang, 
    formula, kl_params, ct_val
)
```

## 2. 映射关系表

### 2.1 局部故障点参数 (来自 `point`)
在 `tests[i].points[j]` 中提取：

| 函数参数 | 对应 JSON 字段 | 映射规则 / 说明 |
|----------|----------------|-----------------|
| `ft` | `point.ft` | 故障类型枚举。0-9。若是非全相/重合闸的第二次故障，则使用 `point.ft2`。 |
| `i_f_amp`| `point.i` | 设定的短路电流 (A)。若是第二次故障，则使用 `point.i2`。 |
| `z_mag` | `point.z` | 实际测试阻抗幅值 (Ω)。若是第二次故障，则使用 `point.z2`。 |
| `z_ang` | `point.ang` | 阻抗角 (度)。若是第二次故障，则使用 `point.ang2`。 |
| `dir_val`| `tests[i].dir` | 0=正向，1=反向。来自外层的 test 结构。 |

### 2.2 全局系统参数 (来自 `payload`)

| 函数参数 | 对应 JSON 字段 | 映射规则 / 说明 |
|----------|----------------|-----------------|
| `sys_v` | `payload.sys.v` | 额定相电压，例如 57.735 (V)。 |
| `i_load_amp` | `payload.sys.iLoad.amp` | 负荷电流幅值 (A)。 |
| `i_load_ang` | `payload.sys.iLoad.ang` | 负荷电流角度 (度)。 |
| `ct_val` | `payload.ctrl.ct` | 0=指向线路，1=指向母线。 |
| `formula` | `payload.model.kl.formula`| 0=直接给定, 1=分量法(kR/kX), 2=阻抗比法(Z0/Z1)。 |

### 2.3 `kl_params` 字典打包规则
根据 `payload.model.kl.formula` 的不同，`payload.model.kl` 下包含不同的子字段。直接打包为 `kl_params` 字典传入即可。

*   **当 `formula = 0` 时**：
    需要提取：`kl.amp` 和 `kl.ang`
    转换为字典：`{'kl_amp': payload.model.kl.amp, 'kl_ang': payload.model.kl.ang}`
    
*   **当 `formula = 1` 时**：
    前端 JSON 给出的是 `R0, X0, R1, X1`，但底层公式期望的是 $K_r$ 和 $K_x$。
    因此需要在中间件进行计算：
    $K_r = (R_0 - R_1) / (3 R_1)$
    $K_x = (X_0 - X_1) / (3 X_1)$
    转换为字典：`{'kr': Kr, 'kx': Kx}`
    
*   **当 `formula = 2` 时**：
    需要提取 `Z0amp, Z0ang, Z1amp, Z1ang`，在中间件计算阻抗比：
    $\vec{Z}_0 = Z0amp \angle Z0ang$
    $\vec{Z}_1 = Z1amp \angle Z1ang$
    $\vec{Z}_{ratio} = \vec{Z}_0 / \vec{Z}_1$
    将比例的幅值和角度打包：
    转换为字典：`{'z0z1_amp': abs(Z_ratio), 'z0z1_ang': angle(Z_ratio)}`

---

## 3. 非全相 (type=5) 与 自动重合闸 (type=6) 的处理
对于涉及到故障转换的实验，底层函数 **不需要** 接收 `delay`、`after`、`time` 等时间参数。底层引擎仅仅是一个**稳态数学算子**。

**中间件执行逻辑：**
1. 读取 `point.ft, point.z, point.i` 等参数。
2. 传入底层 `calculate_fault` 获得一组 `(V_abc, I_abc)`，在硬件上输出这个稳态，并维持 `point.time` 毫秒（或直到保护动作）。
3. 如果保护跳闸，且设置了 `after=0` 或 `after=1`，根据 `delay` 延时后，再读取 `point.ft2, point.z2, point.i2`。
4. 再次传入底层 `calculate_fault` 获得第二组 `(V_abc, I_abc)`。
5. 将新的硬件状态下发。

总结：无论多么复杂的实验流程，到底层永远是**提取一组参数，调用一次 `calculate_fault`，获取一次最终的三相电压电流。**
