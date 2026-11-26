# 时间测量实验执行原理

> 模块：`time_measurement`
>
> 当前状态：旧 API 已删除，等待按新计划全新实现。

本文件只保留执行原理摘要，避免与详细协议重复。

完整开发依据：

- `docs/api/api_time_measurement.md`
- `docs/api/api_time_measurement_implementation_plan.md`
- 项目根目录 `tm.json`
- 项目根目录《API实验代码开发指南.md》

## 1. 三状态模型

实验固定为三个节点，前端 ID 必须严格为 `1、2、3`：

```text
节点1 状态1（准备）
  -> 节点2 状态2（测试）
  -> 节点3 状态3（测试后保持）
  -> 0xFFFF
```

`params.nodes` 必须恰好包含三个节点，并按 `1、2、3` 排列；任何缺失、重复、乱序或
其他 ID 都应在下发 FPGA 前拒绝。

三个状态都是静态波形。状态1和状态3支持按键、超时和普通 DI_MATCH；状态2只由
计时器停时组合或安全超时推进。

## 2. 计时原理

最多三个计时器，每个计时器按配置启动：

```text
进入状态2
指定 DI OPEN/CLOSE
另一个计时器停止
```

每个计时器运行时只保存两个可空硬件时间戳：

```text
start_hw_ts
stop_hw_ts
```

不需要额外维护 `IDLE/RUNNING/STOPPED` 状态；两个字段是否为 `None` 已完整表达计时
进度。计时器只由**启动以后第一次命中的指定方向 DI 事件**停止：

```python
previous_bit != current_bit and current_bit == target_level
```

其中 `OPEN` 的 `target_level=1`，`CLOSE` 的 `target_level=0`。计时器启动前已经发生
的事件和启动时已经存在的目标电平都不能停表；同一路 DI 后续必须先离开该电平、再产生
新的目标方向事件，才会记录停止时间。停止时间一经记录，不再被后续动作覆盖。

由于 DI SOE 时间戳会减去防抖时间，回调后到不等于物理事件后发生。事件相对实验入口
和 timer 启时都要使用 U32 半周期差判断：

```python
delta = (event_ts - reference_ts) & 0xFFFFFFFF
valid = delta < 0x80000000
```

`delta=0` 合法；本协议相应要求单次实验及计时区间小于约 35 分 47.484 秒。
节点1入口同时用主机单调时钟启动带 1 秒裕量的全局可取消 watchdog；约
35 分 46.484 秒仍未结束时明确报错停止，避免静默输出 U32 取模结果。watchdog 的
主机时间不参与计时报告。

固定 DI 语义：

```text
A/B/C/R/a/b/c/r -> bit0..7
1 -> OPEN
0 -> CLOSE
255 -> 全部 OPEN
```

实验启动先运行保留节点 `0x0000`。FPGA 收到该节点的 start 指令会返回完整 DI，
EngineCtrl 用它更新 `ctrl.di`，时间测量 API 再以该值初始化 `_last_di`。初始快照只
建立定向事件基线，不作为 timer 启停事件，也不需要新增 `diStart()` 接口。

所有计时只使用 FPGA 回报的 U32 微秒时间戳：

```python
elapsed_ms = (
    (stop_hw_ts - start_hw_ts) & 0xFFFFFFFF
) / 1000.0
```

离开状态2后，尚未停止的计时器仍在状态3继续监听 DI。实验到达 FFFF 后才生成报告。

## 3. 状态2出口

状态2的 `TIMER_*` 条件不交给 FPGA `diMatchMask`。

API 将所选计时器的 `stop_mode` 展开为当前 DI 的 OPEN/CLOSE 电平组合。每次 DI 回报
先识别本次真实方向事件，再依次处理 DI 启动、停表和同一硬件时间戳的级联启动，最后判断：

```text
所选计时器的 stop_hw_ts 均已记录
并且
当前完整 DI 满足所有 stop_mode 电平
```

第一项保证每个计时器确实在启动后见过停表事件，第二项保证多个接点当前仍同时处于要求
状态。仅仅在进入状态2前已经 OPEN，或进入状态2时当前电平恰好满足，都不能触发跳转。

命中后，API 只调用一次 `EngineCtrl.trigNode(3)`。

状态2的安全 TIME_OUT 也由 API 的可取消任务调用同一个一次性出口，不再同时配置
USEEngine timeout。这样 TIMER 和 TIME_OUT 只有一个跳转控制者，不会在节点3 ACK 前
留下第二次 `manualTrig(3)`。

Python 调度延迟不影响报告精度，因为计时器已经在 DI 回报时使用硬件时间戳停表。

## 4. DO 原理

状态1和状态3使用普通绝对动作：

```json
{"delay": 0, "mask": 0}
```

空数组也必须编译成 0 ms 输出 mask 0。

状态2的八项数组按位置对应 DO0～DO7，每项：

```json
{"delay": 2, "holdTime": 10}
```

表示该通道在 2 ms 翻转、保持 10 ms、在 12 ms 翻回。

底层只接受完整绝对 DO 字节，所以 API 必须把八路翻转按绝对时间合并。`tm.json` 从
mask 0 开始的结果固定为：

```text
0 ms   F3
2 ms   F7
6 ms   FF
12 ms  FB
36 ms  F3
100 ms 00
```

同一时刻的多个通道合并成一次硬件写入。

## 5. 报告

最终只报告计时器：

```json
{
  "type": "report",
  "module": "time_measurement",
  "data": {
    "timer1_elapsed_ms": 32.145,
    "timer2_elapsed_ms": 105.002,
    "timer3_elapsed_ms": null
  }
}
```

报告仅在 `_onStop()` 中生成。已完成计时器此时用两个 U32 时间戳计算并按
`round(value, 3)` 处理；未配置、未启动或未停止均为 `null`。
