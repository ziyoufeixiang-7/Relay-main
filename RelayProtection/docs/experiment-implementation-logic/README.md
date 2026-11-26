# 实验实现逻辑 — 论文式理论与数学文档

本目录收录各继电保护试验的**论文式理论文档**：从**继电保护测试专业原理**与**前端接口契约**
（即界面上有哪些控制变量、如何影响试验）出发，推导每个试验"控制变量 → 注入相量 → 判据 → 上报"
的完整数学模型。

> **编写口径**：以专业继保知识 + 各实验 API 文档（前端页面）为依据；**不依赖后端代码实现**
> （仅借用接口字段名作为"前端有什么"的事实），以免被尚未验证的实现细节带偏。

## 文档列表

| 文档 | 实验 | 模块标识 |
|---|---|---|
| [01](01-impedance-distance-protection.md) | 阻抗特性（距离保护） | `impedance_characteristic_test` |
| [02](02-line-protection.md) | 线路保护（距离/零序/相/负序过流/工频变化量/非全相零序/重合闸） | `lp_test` |
| [03](03-differential-protection.md) | 差动保护（比率/谐波制动，6I/2I） | `differential_test` |
| [04](04-sweep-search.md) | 通用递变搜索（动作值/返回系数） | `ac/dc/harmonic/steps/acdc_test` |
| [05](05-status-sequence.md) | 状态序列（含代码映射与协议/代码审计） | `universal_sequence` |
| [06](06-time-measurement.md) | 时间测量（新协议执行原理；API 等待重写） | `time_measurement` |
| [07](07-inverse-time.md) | 反时限 I-t / V·F-t（含代码映射与协议/代码审计） | `inverse_time_it_test` / `inverse_time_vft_test` |
| [08](08-slip.md) | 滑差 df/dt·dv/dt（目标执行逻辑与 API 重写方案） | `slip_test` |

## 统一结构

每篇均含：摘要 → 保护原理背景 → 试验目的与判据 → 前端控制变量 → **数学模型（控制变量→注入相量）**
→ 试验时序与搜索 → 测量与上报（SOE） → 算例 → 边界情形与工程注意 → 参考。

## 三层基础（所有试验共用）

- **FPGA**（`docs/fpga_protocol.md`）：DDS 波形发生，Base→Shadow→Active 三级缓冲乒乓翻转，SOE 时间戳。
- **USEEngine**（`docs/engine_execution_model.md`）：零编译永久 FSM，节点跳转由 DI/超时/计数驱动，四种波形模式。
- **EngineCtrl**（`docs/architecture_overview.md`）：物理参数→帧编译下发、遥测回传、0x0000 待机/0xFFFF 终止。

## 待补充（后续可扩展）

整组试验等——在 `docs/api/` 已有前端接口规范，可按相同模板续写。
迁移实验文档已覆盖状态序列 [05](05-status-sequence.md)、时间测量
[06](06-time-measurement.md)、反时限 [07](07-inverse-time.md) 和滑差
[08](08-slip.md)；其中时间测量旧 API 已删除，等待按新计划实现。
