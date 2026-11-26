# 差动试验界面参数、前端 API 与数学公式映射

## 1. 文档目的

本文把差动试验界面中的参数逐项对应到：

1. 前端界面状态；
2. WebSocket 启动 JSON；
3. 当前后端实际读取位置；
4. 差动保护数学变量、方程或补偿矩阵；
5. 当前已经生效、仅由前端展开、尚未生效或存在歧义的行为。

本文以当前工作区代码为准：

- API：../RelayProtection/api/ApiDiffTest.py
- 前端参数桥接：../RelayProtection/calc/diff_bridge.py
- 当前生产计算：../RelayProtection/calc/diff.py
- FPGA 控制入口：../RelayProtection/logic/EngineCtrl.py
- 节点执行：../RelayProtection/logic/USEEngine.py
- 独立正确性模型：diffmath/

当前工作树已经不包含 Vue/TypeScript 前端源码。本文所说的“当前前端组包行为”来自
Git 历史中最后一个仍包含前端成品包的提交 2fa209c，其 web/index.html 是压缩后的
构建产物。因而本文同时记录：

- 最后可审计的前端实际组包行为；
- 当前 Python 后端实际读取行为；
- 两者已经确认存在的错配。

完整公式推导见 [差动保护详细计算过程与数学公式.md](差动保护详细计算过程与数学公式.md)。

> 重要边界：当前 ApiDiffTest 尚未接入 differential_math_lab/diffmath。在线运行仍然使用 calc/diff_bridge.py → calc/diff.py。因此本文必须区分“参数在专业数学模型中的含义”和“当前生产代码的实际行为”，二者不能混写。

### 1.1 状态标记

| 标记 | 含义 |
|---|---|
| 直接生效 | JSON 字段由当前后端读取，并直接影响波形、节点或判据 |
| 前端展开 | 界面值本身不下发，前端必须先把它转换成后端认识的字段 |
| 仅前端 | 只用于界面、曲线、导入导出或报告计算 |
| 已发送但未使用 | 可以出现在 JSON 中，但当前后端完全不读取 |
| 有条件生效 | 只在 6I、2I、比率或谐波中的某一条路径生效 |

### 1.2 参数处理总链路

~~~text
界面控件
  │
  ├─ 纯显示或曲线参数 ───────────────► 前端保存/绘图/生成点
  │
  └─ 实验参数
       │ 前端做枚举转换、秒→毫秒、组别展开、搜索区间展开
       ▼
WebSocket start JSON
       │
       ├─ params.debounce ───────────► EngineCtrl
       ├─ params.sys.logicMask ──────► ApiDiffTest → ApiNodeData
       ├─ params.statics ────────────► 物理通道固定量
       └─ params.payload
             ├─ equationSettings ────► calc.diff.solve
             ├─ protectionSettings ─► 6I 补偿/经验算法
             ├─ connectionSettings ─► 6I/2I 与通道分配
             ├─ projectSettings ────► 项目、相别、点和搜索
             └─ testParams ─────────► 节点时序中的部分字段
       ▼
ApiNodeData 节点 1/2/3
       ▼
EngineCtrl → USEEngine → FPGA
~~~

---

## 2. 三张截图配置的直接对应

截图所示主要设置为：

| 界面项 | 截图值 | 应生成的 API 值 | 当前状态 |
|---|---|---|---|
| 保护类型 | 变压器保护 | protectionSettings.protectionObject = transformer，不原样下发 | 前端展开；会影响 Kp 辅助计算分支 |
| 绕组数 | 双绕组 | protectionSettings.windingType = double，不原样下发 | 前端展开；会影响组别和 Kp 侧选择 |
| 接线组别 | Y/Y-12 | y、y、0、0 四个展开字段 | 前端展开 |
| 相位校正 | 无校正 | phaseCorrection = none | 比率 6I、2I 以不同方式生效；谐波路径忽略 |
| Y 侧零序校正 | 未选 | zeroSequenceCorrection = false | 仅比率 6I 有条件生效；2I/谐波忽略 |
| 参与绕组 | 高→低 | 目标语义是选定侧 1/侧 2 后展开；当前 bundle 的三绕组 letter/clock 展开有已知错误 | 前端展开 |
| 动作方程 | Id = \|I1′ + I2′\| | idEquation = id_sum | 直接生效 |
| 制动方程 | Ir = \|I1′ − I2′\| / K | irEquation = ir_diff_k | 直接生效 |
| K | 2.000 | kFactor = 2.0 | 直接生效 |
| Kp1、Kp2 | 1.000、1.000 | kp1 = 1.0，kp2 = 1.0 | 直接生效 |
| 标幺显示 | 未选 | isPerUnit = false | 直接生效 |
| 接线方式 | 扩展差动（6I） | type = ext-differential | 直接生效 |
| I1 接线 | Ia、Ib、Ic | i1Terminal = high-y | 直接生效 |
| I2 接线 | Ix、Iy、Iz | i2Terminal = low-y | 直接生效 |
| I1/I2 显示角 | 0°、180° | 当前无可生效的输入字段 | 派生显示 |
| 试验项目 | 比率差动—边界搜索 | activeProject = ratio-search | 直接生效 |
| 测试相别 | A 相差动 | testPhase = A | 6I 生效 |
| 动作门槛 | 0.500 A | 前端曲线/点生成参数 idMin | 仅前端 |
| 速断定值 | 5.000 A | 前端曲线参数 idInst | 仅前端 |
| Kzd 公式 | Kzd = Id / Ir | projectSettings.ratio.kzd = id/ir，不下发 | 仅前端 |
| 系统频率 | 50.000 Hz | ratio.frequency = 50.0 | 当前未读取 |
| 搜索起点 | 0.000 % | 前端展开成 points[].y[0] | 前端展开 |
| 搜索终点 | 120.000 % | 前端展开成 points[].y[1] | 前端展开 |
| 搜索精度 | 0.010 A | ratio.precision = 0.01 | 直接生效 |
| 搜索方式 | 二分法 | ratio.method = binary | 当前忽略，始终二分 |
| 差动类型 | 稳态差动 | diffMode = 0 | 当前未读取 |
| 模拟故障前 | 选中 | simulatePreFault = true | 直接生效 |
| 故障前时间 | 1.000 s | preFaultDuration = 1000 | 直接生效，单位必须是 ms |
| 故障限时 | 1.000 s | faultDuration = 1000 | 直接生效，单位必须是 ms |
| 负荷电流 | 0.000 A | loadCurrent = 0.0 | 当前未读取 |
| 防抖确认 | 15 ms | 后端要求 params.debounce = 15；最后一版前端却发 sys.debounce | 当前前后端错配 |
| 动作逻辑 | 逻辑与 | logicMask 增加 0x100 | 直接生效 |
| 开入翻转 | A、B、C | logicMask 低八位为 0x07 | 直接生效 |

### 2.1 按截图语义并兼容当前后端的推荐启动 JSON

下面加入了一个示例测试点。它是“按截图语义正确表达、并能被当前 Python 后端正确读取”的推荐包，不等同于最后一版前端当前实际发出的包；二者差异见下一节。

截图中的点表为空时，最后一版前端会阻止发送；即使绕过前端直接发送，当前 ApiDiffTest 也会报告没有测试点并停止。

假设添加一个点：

- 固定制动电流 Ir = 1.0 A；
- 该点整定动作边界 Id = 0.5 A；
- 搜索范围为整定值的 0%～120%；
- 所以实际发送的 Id 区间为 0.0～0.6 A。

对应 JSON：

~~~json
{
  "module": "differential_test",
  "cmd": "start",
  "params": {
    "debounce": 15,
    "sys": {
      "logicMask": 263
    },
    "statics": {},
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
            {
              "id": 1,
              "x": 1.0,
              "y": [0.0, 0.6]
            }
          ]
        }
      },
      "testParams": {
        "diffMode": 0,
        "simulatePreFault": true,
        "preFaultDuration": 1000,
        "faultDuration": 1000,
        "loadCurrent": 0.0
      }
    }
  }
}
~~~

其中：

$$
263=0x107=0x100+0x01+0x02+0x04
$$

即某次判定时，A、B、C 三路的当前电平必须同时相对参考电平发生变化，才判为动作。这不是“三路曾经分别翻转过”的历史锁存判据。

Ie 不显示在这三张截图中。最后一版前端实际取 kpCalcParams.sides[0].in2；示例中的
1.9682 只是当时界面状态的一个典型值。由于截图未勾选标幺显示，本例 Ie 不参与计算。

示例中 statics 为空仅表示可以借用保留节点 0 留下的 50 Hz 频率状态；它不会生成六路辅助电压幅值。若被测保护需要电压，必须在 params.statics 的 0～5 通道明确下发。

### 2.2 最后一版前端实际发送与推荐包的差异

最后一个可审计前端包实际把防抖放在 sys 内：

~~~json
"params": {
  "sys": {
    "debounce": 15,
    "logicMask": 263
  }
}
~~~

但当前 EngineCtrl 只读取：

~~~text
params.debounce
~~~

不读取：

~~~text
params.sys.debounce
~~~

所以按最后一版前端原样运行时，截图的 15 ms 实际不会生效，EngineCtrl 会使用默认
20 ms。推荐包把 debounce 移到 params 顶层，就是为了匹配当前后端。

此外，最后一版前端存在 logicMask 初始值问题：

- Store 默认界面显示“逻辑与”；
- 默认勾选 A、B、C；
- 但 logicMaskResult 初始值仍可能是按“逻辑或”编码的 7；
- Logic 组件没有在首次挂载时立即重算；
- 只有用户改变逻辑或重新勾选通道后，才可能刷新为 263。

因此仅看截图不能断定最后一版前端实际发送的是 7 还是 263。按截图文字语义，正确值
一定是 263；联调时必须抓取 WebSocket start 原文确认。

最后一版前端对其他截图字段的组包行为是：

| 前端界面状态 | 实际启动包 |
|---|---|
| protectionObject = transformer | 被组包器剔除 |
| windingType = double | 被组包器剔除 |
| vectorGroup = Y,y0 | 展开为 y/y、0/0 后，原字段被剔除 |
| involvedWinding | 被组包器剔除 |
| I1/I2 显示角 | 被组包器显式剔除 |
| Kp 辅助计算对象 | 被剔除，但最终 kp1/kp2 下发 |
| isPerUnit | 实际下发 |
| Ie | 实际下发，取 sides[0].in2 |
| UI 时间（秒） | 前端已乘 1000 并取整后下发 |
| 搜索 start/end 百分比 | 不单独下发，展开到每个 points[].y |

最后一版前端真正送入 sendInitParams 的字段树为：

~~~text
module = differential_test
cmd = start
params
├─ sys
│  ├─ debounce
│  └─ logicMask
├─ statics
└─ payload
   ├─ variant
   ├─ protectionSettings       仅 six_channel
   ├─ busbarSettings           仅 busbar
   ├─ equationSettings
   │  ├─ idEquation
   │  ├─ irEquation
   │  ├─ kFactor
   │  ├─ kp1
   │  ├─ kp2
   │  ├─ isPerUnit
   │  └─ Ie
   ├─ connectionSettings
   │  ├─ type
   │  ├─ i1Terminal
   │  └─ i2Terminal
   ├─ projectSettings
   │  ├─ activeProject
   │  ├─ testPhase
   │  ├─ ratio
   │  │  ├─ frequency
   │  │  ├─ precision
   │  │  ├─ method
   │  │  └─ points
   │  └─ harmonic              仅 six_channel 的 harmonic-* 项目
   └─ testParams
      ├─ diffMode
      ├─ faultDuration
      ├─ preFaultDuration
      ├─ simulatePreFault
      └─ loadCurrent
~~~

ratio 块始终存在；谐波项目中 ratio.points 为空，并另加 harmonic 块。比率项目不发送
harmonic 块。

这个字段树的直接来源是 Store 中的 baseSettings.testType：

| baseSettings.testType | 前端枚举 | payload.variant | 条件块 |
|---:|---|---|---|
| 0 | SIX_CHANNEL | six_channel | 发送 protectionSettings，不发送 busbarSettings |
| 1 | BUSBAR | busbar | 发送 busbarSettings，不发送 protectionSettings |

variant 不是由 connectionSettings.type 或 protectionObject 在组包时推断的。两个路由页面会在进入时修改
baseSettings.testType，然后由组包器直接依据这个数值选择上表分支。

---

## 3. 顶层命令与系统参数

### 3.1 module 和 cmd

| JSON 路径 | 取值 | 含义 |
|---|---|---|
| module | differential_test | 由 EngineCtrl 创建 ApiDiffTest |
| cmd | start | 启动新实验 |
| cmd | stop | 停止当前实验 |

模块名必须是 differential_test。当前 ApiDiffTest.MODULE_KEY 没有 lp_test、diff_test 等别名。

### 3.2 防抖 debounce

界面“开入防抖确认时间 15 ms”实际对应：

~~~json
"params": {
  "debounce": 15
}
~~~

当前 EngineCtrl 的读取路径是 params.debounce，单位为 ms。

最后一版前端的界面原始状态是 testParams.debounceTime，组包器把它转换成
params.sys.debounce；正是这个转换目标与当前 EngineCtrl 不一致。

下面这种写法当前无效：

~~~json
"params": {
  "sys": {
    "debounce": 15
  }
}
~~~

现有部分旧文档仍把 debounce 写进 params.sys。最后一个可审计前端构建包也确实这样
发送，所以这不只是文档遗留，而是当前前后端的实际协议错配。

### 3.3 statics 通道

API 通道与仪器输出对应如下：

| API 通道 | 物理量 |
|---:|---|
| 0 | Ua |
| 1 | Ub |
| 2 | Uc |
| 3 | Ux |
| 4 | Uy |
| 5 | Uz |
| 6 | Ia |
| 7 | Ib |
| 8 | Ic |
| 9 | Ix |
| 10 | Iy |
| 11 | Iz |

每个通道的层结构为：

| layer | 数组 | 含义 |
|---:|---|---|
| 0 | [直流分量, 频率] | 第一个值为 DC，第二个值为 Hz |
| 1 | [基波幅度, 相角] | 正弦基波 |
| 2～64 | [第 n 次谐波幅度, 相角] | n 次谐波 |

例如：

~~~json
"0": {
  "0": [0.0, 50.0],
  "1": [57.735, 0.0]
}
~~~

表示 Ua 无直流、基频 50 Hz、基波幅度 57.735、相角 0°。

最后一版前端会把辅助电压界面的 RMS 值转换为 statics 使用的正弦幅度值，核心口径为：

$$
U_{\mathrm{amp}}
=
\frac{\left\lceil\sqrt2U_{\mathrm{RMS}}\times10^4\right\rceil}{10^4}
$$

比率点的 Id/Ir 则仍以 RMS 发送，由 ApiDiffTest 在后端乘 $\sqrt2$。因此不能把
statics 中的幅度值与 points 中的 RMS 坐标当作同一数值口径。

辅助电压表的精确前端状态路径为：

| 界面列 | 前端状态路径 | 组包行为 |
|---|---|---|
| 名称 | auxVoltageData[i].name | Ua/Ub/Uc/Ux/Uy/Uz 映射为通道 0～5 |
| 电压电流/幅值 | auxVoltageData[i].amplitude | 交流时按上式从 RMS 转幅度 |
| 角度 | auxVoltageData[i].angle | 写入 layer 1 第 2 个值 |
| 频率 | auxVoltageData[i].frequency | 写入 layer 0 第 2 个值 |
| 直流 | auxVoltageData[i].isDc | 当前仅决定是否跳过 $\sqrt2$ 换算 |

### 3.3.1 最后一版前端的 isDc 组包缺陷

按 ApiNodeData 语义，直流应该放在 layer 0 的第 1 个值。但最后一版 bundle 的 B()/h()
在 isDc=true 时只是不再乘 $\sqrt2$，仍然构造：

~~~json
{
  "0": [0, 50],
  "1": [界面数值, 角度]
}
~~~

因此它仍把数值写到正弦基波层，layer 0 的直流槽仍为 0。这是已确认的前端实现缺陷，
不能把 isDc=true 理解为当前已正确生成 DC 节点。

启动组包前还会先把 currentTrackingData.i1/i2 的幅值和角度清零，再调用 B()。
因此实际 params.statics 除辅助电压外，还可能包含当前活动相/端子的零幅值电流项。
这些电流项会在 ApiDiffTest 构造故障节点时被重新生成。

当前 ApiDiffTest 的特殊行为：

1. params.statics 中的电压通道会被保留；
2. 电流通道 6～11 会被全部重新生成；
3. 原来放在电流通道中的 layer 0 或其他层会被整项覆盖；
4. 实验节点 0 把所有活动通道初始化为 50 Hz，所以截图中的 50 Hz 恰好与默认值一致；
5. ratio.frequency 当前未用于改写电流通道频率，因此把界面改为 60 Hz 并不能保证当前差动电流按 60 Hz 输出。

### 3.4 variant

最后一版前端不是根据 connectionSettings.type 或 protectionObject 推断 variant，而是根据：

~~~text
state.baseSettings.testType
~~~

当 testType=0（SIX_CHANNEL）时发送：

~~~json
"variant": "six_channel"
~~~

当 testType=1（BUSBAR）时发送：

~~~json
"variant": "busbar"
~~~

当前 ApiDiffTest 不读取 variant。真正决定 6I/2I 的字段是：

~~~text
payload.connectionSettings.type
~~~

所以 variant 目前只是前端路由或协议标识，不能代替 connectionSettings.type。反过来，
connectionSettings.type 也不会改写 variant。

---

## 4. “保护设置”页

## 4.1 保护类型

最后一版前端状态字段为 protectionSettings.protectionObject。界面通常提供：

| 界面 | 前端状态值 |
|---|---|
| 变压器保护 | transformer |
| 发变组保护 | gen-trans |
| 发电机保护 | generator |
| 母线保护 | busbar |

该字段不原样进入当前启动包。当前后端不会根据“变压器保护”自动选择矩阵，也不会根据“发电机保护”关闭变压器补偿。
但它不是完全无影响的“纯显示字段”：最后一版前端会用它选择 Kp 辅助计算的界面和算法，
算出的 equationSettings.kp1/kp2 随后会下发。

它的正确作用应当是：

1. 限制可选接线组别；
2. 限制可选试验项目；
3. 决定需要哪一类 DUT Profile；
4. 由前端或未来后端生成明确的保护计算参数。

不能只发送“保护类型”就期望后端知道保护内部算法。

## 4.2 双绕组、三绕组

界面的“双绕组/三绕组”是前端 protectionSettings.windingType：

| 界面 | 前端状态值 |
|---|---|
| 双绕组 | double |
| 三绕组 | triple |

组包时该字段不原样下发，当前后端不直接读取。但它会被前端传给
ITu(vectorGroup, windingType)，以决定按双绕组还是三绕组方式展开 letter/clock；
它还会决定 Kp 弹窗显示哪些侧和如何选出 kp1/kp2。因此按本文的状态标记，它应属于
“前端展开”，不应标成纯“仅前端”。

ApiDiffTest 的数学核心只有两个逻辑侧：

$$
\mathbf J_1,\qquad \mathbf J_2
$$

因此三绕组保护一次也只能选择其中一对做当前 6I 实验。目标设计中，前端必须根据“参与试验的绕组”把所选两侧展开成：

~~~text
vectorGroupLetter1
vectorGroupLetter2
vectorGroupHighSideClock
vectorGroupLowSideClock
i1Terminal
i2Terminal
~~~

但这是目标语义，不是对最后一版 bundle 的描述。当前 bundle 的 ITu 没有读取
involvedWinding，而且 i1Terminal/i2Terminal 仍是独立用户状态，不会随绕组对自动生成。
这两点的具体后果见 4.6.1。如果只把 involvedWinding 或 involvedPairIndex 放入 JSON，
而没有正确展开真正需要的字段，当前后端无法知道究竟测试高—中、高—低还是中—低。

## 4.3 接线组别

最后一版前端中，截图“Y/Y-12”的内部组合值为：

~~~text
protectionSettings.vectorGroup = Y,y0
~~~

组合值本身不由后端读取。前端必须拆为四个字段：

| 界面含义 | JSON 字段 |
|---|---|
| I1 侧联结字母 | vectorGroupLetter1 |
| I2 侧联结字母 | vectorGroupLetter2 |
| I1 侧钟点 | vectorGroupHighSideClock |
| I2 侧钟点 | vectorGroupLowSideClock |

常见例子：

| 界面组别 | letter1 | letter2 | highClock | lowClock |
|---|---|---|---:|---:|
| Y/Y-12 | y | y | 0 | 0 |
| Y/Y-0 | y | y | 0 | 0 |
| Y/Δ-11 | y | d | 0 | 11 |
| Y/Δ-1 | y | d | 0 | 1 |

12 点和 0 点电气等价：

$$
12\times30^\circ=360^\circ\equiv0^\circ
$$

协议建议统一发送字符串 "0"。当前代码虽然多数分支会让 12 和 0 得到相同结果，但没有合法范围校验，不应依赖这个偶然行为。

当前代码兼容一个旧字段 vectorGroupClock：

- 如果存在 vectorGroupHighSideClock，则采用新格式；
- 如果不存在 vectorGroupHighSideClock，则 highClock 固定为 0，并读取 vectorGroupClock 作为 lowClock；
- 只发送 vectorGroupLowSideClock 而不发送 highClock 时，lowClock 不会按预期生效。

### 4.3.1 前端组别组合值

最后一版前端使用逗号分隔的内部 slug，显示文字与内部值不是同一个字符串：

| 界面系列 | 前端内部形式 | 启动时展开 |
|---|---|---|
| Y/Y-N | Y,yN | y、y、0、N |
| Y/Δ-N | Y,dN | y、d、0、N |
| Δ/Y-N | d,yN | d、y、0、N |
| Δ/Δ-N | d,dN | d、d、0、N |

其中显示的 12 统一用内部数字 0，例如：

| 界面 | 内部 vectorGroup |
|---|---|
| Y/Y-12 | Y,y0 |
| Δ/Δ-12 | d,d0 |
| Y/Δ-11 | Y,d11 |
| Y/Δ-1 | Y,d1 |
| Δ/Y-11 | d,y11 |
| Δ/Y-1 | d,y1 |

最后一版三绕组选项包括：

| 界面 | 内部 vectorGroup |
|---|---|
| Y/Y/Y-12 | Y,y,y0 |
| Y/Y/Δ-11 | Y,yn,d11 |
| Y/Y/Δ-1 | Y,yn,d1 |
| Y/Δ/Δ-11 | Y,d11,d11 |
| Y/Δ/Δ-1 | Y,d1,d1 |

双绕组的 involvedWinding 值就是当前 vectorGroup 值。最后一版 bundle 可生成的
全部 30 个双绕组 value 如下：

| 组别系列 | 精确 value |
|---|---|
| Y/Y | Y,y0、Y,y1、Y,y2、Y,y3、Y,y4、Y,y5、Y,y6、Y,y7、Y,y8、Y,y9、Y,y10、Y,y11 |
| Y/Δ | Y,d1、Y,d3、Y,d5、Y,d7、Y,d9、Y,d11 |
| Δ/Y | d,y1、d,y3、d,y5、d,y7、d,y9、d,y11 |
| Δ/Δ | d,d0、d,d2、d,d4、d,d6、d,d8、d,d10 |

上表是允许集，不能把“Y/Δ-N”等模式理解为任意 N 都能由当前下拉框生成。

### 4.3.2 vectorGroupBalanceAngle 只是前端派生状态

最后一版前端还保存：

~~~text
protectionSettings.vectorGroupBalanceAngle
~~~

它由 vectorGroup 和 involvedWinding 查表派生。双绕组中，第二段钟点数字为 $n$ 时，当前表的角度为：

$$
\alpha_{balance}=(180^\circ+30^\circ n)\bmod 360^\circ
$$

例如 Y,y0 是 180°，Y,d11 是 150°，Y,d1 是 210°。三绕组则按所选 pair 的
balanceAngleByPair 查表。

这个字段用于前端报告/导入导出，不进入 start payload。它也不是
connectionSettings.i1Angle/i2Angle；后两者由 idEquation 的 watcher 派生，并在启动组包时被显式剔除。

### 4.3.3 接线组别不等于保护矩阵号

Yd11、Yy12 描述变压器铭牌相移，但保护内部还可能：

- 在 Y 侧补偿；
- 在 Δ 侧补偿；
- 由外部 CT 接线补偿；
- 消除或保留零序；
- 交换相序；
- 使用不同厂家定义的矩阵号；
- 把 TAP、CT 变比或额定值包含在矩阵之前或之后。

所以不能仅由 Yd11 推断唯一的六路测试电流。专业模型应显式写成：

$$
\mathbf J_s=
\mathbf M_s
\left(
p_sK_{Ps}\mathbf P_s\mathbf X_s
\right)
$$

其中：

| 符号 | 界面/API 来源 |
|---|---|
| $\mathbf X_s$ | 测试仪实际三相输出 |
| $\mathbf P_s$ | 相序、通道排列和逐相极性；当前界面没有完整字段 |
| $p_s$ | 整侧 CT 极性；当前界面没有明确字段 |
| $K_{Ps}$ | kp1 或 kp2 |
| $\mathbf M_s$ | DUT 实际补偿矩阵；由组别、校正方式和装置定值共同确认 |
| $\mathbf J_s$ | 保护内部用于 Id/Ir 的比较量，即界面中的 $I_s'$ |

## 4.4 相位校正方式

界面与 JSON 的直接映射：

| 界面 | phaseCorrection |
|---|---|
| 无校正 | none |
| Y 侧校正 | y-side |
| Δ 侧校正 | delta-side |

它表达的是“保护在哪类绕组侧进行相位校正”，不是让用户任意增加一个输出角度。

在 differential_math_lab 的 legacy 适配假设下，典型矩阵对应如下。M0 表示单位阵，M12 表示零序投影，M1/M11 分别包含正、负 30° 序分量旋转。

### Yd11

| phaseCorrection | zeroSequenceCorrection | W1/W2 假设矩阵 |
|---|---:|---|
| none | false | M0 / M0 |
| none | true | M12 / M0 |
| y-side | false 或 true | M1 / M0 |
| delta-side | false | M0 / M11 |
| delta-side | true | M12 / M11 |

### Yy12

| phaseCorrection | zeroSequenceCorrection | W1/W2 假设矩阵 |
|---|---:|---|
| none | false | M0 / M0 |
| none | true | M12 / M12 |
| y-side | false 或 true | M11 / M11 |
| delta-side | false | M0 / M0 |
| delta-side | true | M12 / M12 |

以上表格只是对现有界面语义的明确数学化，仍必须用被测保护说明书或保护实际矩阵定值确认。

### 4.4.1 截图配置对应的矩阵

截图是：

~~~text
Y/Y-12
无校正
未勾选 Y 侧零序校正
~~~

因此在本文明确假设下：

$$
m_{W1}=0,\qquad \mathbf M_{W1}=\mathbf M(0)=\mathbf I
$$

$$
m_{W2}=0,\qquad \mathbf M_{W2}=\mathbf M(0)=\mathbf I
$$

它不是前一份公式文档中 Yy12 + Y 侧校正示例使用的 M11/M11。是否选择 Y 侧校正会改变矩阵，二者不能混用。

### 4.4.2 当前生产代码的实际差异

当前 calc/diff.py 没有让所有相别统一经过上述 Profile：

- A/B/C 使用 _empirical_6i_single_phase 经验分支；
- AB/BC/CA/ABC 使用 determine_theta + M 矩阵分支；
- 单相经验分支完全忽略 highClock；
- 多相分支分别使用两侧绝对 clock，而不是统一从相对钟点和 DUT Profile 推导。

因此当前单相通过并不能证明多相正确。已知例子：

| 设置 | 专业/legacy Profile | 当前多相路径实际近似 |
|---|---|---|
| Yd11 + y-side | M1 / M0 | M12 / M0 |
| Yy12 + y-side | M11 / M11 | M12 / M12 |

这正是现有 6I 单相能够复现昂立，而 AB、ABC 等多相可能失败的主要原因之一。

## 4.5 Y 侧零序校正

界面复选框对应：

~~~json
"zeroSequenceCorrection": true
~~~

相差动保护中，零序消除的标准投影形式为：

$$
\mathbf M_{12}
=
\mathbf I-\frac13
\begin{bmatrix}
1\\1\\1
\end{bmatrix}
\begin{bmatrix}
1&1&1
\end{bmatrix}
$$

即：

$$
\mathbf M_{12}\mathbf X
=
\mathbf X-\frac{I_A+I_B+I_C}{3}
\begin{bmatrix}
1\\1\\1
\end{bmatrix}
$$

它会消除三相公共零序分量。

必须区分：

- zeroSequenceCorrection：相差动比较前是否消除零序；
- diffMode = 1：要测试零序差动元件。

这两个参数含义完全不同。当前后端只部分使用前者，完全没有实现后者。

当前 A/B/C 单相经验分支还有一个已确认的适用侧错误：只要两侧中任一侧是 Y，
`_empirical_6i_single_phase` 就会对所有“未命中相位校正”的侧执行 `apply_zero_seq`，
没有再确认该侧自身是否为 Y。因此 Yd11 + y-side + zeroSequenceCorrection=true 时，
它会错误地在 d 侧增加对消电流；本文 legacy Profile 的正确矩阵对仍是 M1/M0。
这是当前生产实现缺陷，不是“Y 侧零序校正”的专业定义。

## 4.6 参与试验的绕组

最后一版前端状态字段为 protectionSettings.involvedWinding。界面可见值例如：

- 高→低；
- 高→中；
- 中→低。

旧版状态或导入数据中也可能带 involvedPairIndex。组包时它们都不会直接下发，当前
后端也不读取。

它不是简单固定为 high-low 字符串。正常下拉框会把“所选绕组对本身的组别 slug”
作为 value。

双绕组只有高—低一个选项，其 value 就是当前双绕组 vectorGroup，例如 Y,y0 或
Y,d11。

三绕组的实际 value 如下：

| 三绕组 vectorGroup | 高→低 | 高→中 | 中→低 |
|---|---|---|---|
| Y,y,y0 | Y,y0 | Y,y | y,y0 |
| Y,yn,d11 | Y,d11 | Y,yn | yn,d11 |
| Y,yn,d1 | Y,d1 | Y,yn | yn,d1 |
| Y,d11,d11 | Y,d11~0,2 | Y,d11~0,1 | d11,d11 |
| Y,d1,d1 | Y,d1~0,2 | Y,d1~0,1 | d1,d1 |

后缀 ~0,2 和 ~0,1 只是为了区分文字相同但绕组位置不同的 pair。导入兼容层还接受
high-low、high-mid、mid-low，但它们不是正常下拉框生成的主要 value。

从实验语义看，目标前端应完成以下工作：

1. 选出本轮的物理绕组 1 和绕组 2；
2. 将它们的 y/d 字母写入 letter1/letter2；
3. 将它们的钟点写入 highClock/lowClock；
4. 将它们的 Kp 写入 kp1/kp2；
5. 生成正确的 I1/I2 接线提示和端子映射；
6. 保持报告中的 I1/I2 与这两个绕组含义一致。

当前 API 不支持三个绕组同时进入同一制动公式。
最后一版 bundle 也没有完成上述全部目标：它不会根据 involvedWinding 自动改写
i1Terminal/i2Terminal，而 letter/clock 还存在下节所述的固定高—低侧错误。

### 4.6.1 当前三绕组组包错配

最后一版前端的三绕组组别编码器没有根据 involvedWinding 选择组别片段，而是固定取
第 1 段和第 3 段，也就是固定按高—低侧生成 letter/clock。

与此同时，Kp 辅助计算会根据参与绕组选择不同的两侧：

- 高—低：sides[0] 与 sides[2]；
- 高—中：sides[0] 与 sides[1]；
- 中—低：sides[1] 与 sides[2]。

因此高—中或中—低试验可能出现：

~~~text
kp1/kp2 属于所选绕组对
letter/clock 却仍属于高—低绕组对
~~~

这是确定性的参数错配。双绕组截图不受这个问题影响。
另外，当前组包器只是把独立的 connectionSettings.i1Terminal/i2Terminal 原样带入 payload，
没有根据 involvedWinding 重新生成端子。

此外，最后一版前端始终用 sides[0].in2 作为 Ie。三绕组选择中—低时，这个基准是否
仍符合保护的标幺定义，也必须另行确认。

---

## 5. 动作方程、制动方程和系数

## 5.1 符号

为了避免把测试仪输出和保护内部比较量混为一谈，本文使用：

$$
J_1=I_1',\qquad J_2=I_2'
$$

其中 $J_1$、$J_2$ 已经经过 Kp、极性、相序和矩阵补偿；界面 Id/Ir 方程应当作用于它们，而不是直接作用于某一路测试仪端子。

令：

$$
D=I_d,\qquad R=I_r
$$

## 5.2 动作方程 idEquation

| 界面公式 | API 枚举 |
|---|---|
| $D=\lvert J_1\rvert$ | id_i1 |
| $D=\lvert J_1+J_2\rvert$ | id_sum |
| $D=\lvert J_1-J_2\rvert$ | id_diff |
| $D=\lvert J_1+J_2\rvert^2$ | id_sum_sq |
| $D=\lvert J_1-J_2\rvert^2$ | id_diff_sq |

平方型方程的 Id 量纲是电流平方，不再是 A。它们的标幺换算也必须使用 $I_e^2$，不能仍乘一次 $I_e$。

## 5.3 制动方程 irEquation

令：

$$
a=|J_1|,\qquad b=|J_2|
$$

两侧夹角为：

$$
\theta=\arg J_1-\arg J_2
$$

各枚举的字面数学含义为：

| API 枚举 | 数学定义 |
|---|---|
| ir_i2 | $R=b$ |
| ir_diff_k | $R=\lvert J_1-J_2\rvert/K$ |
| ir_sum_k | $R=\lvert J_1+J_2\rvert/K$ |
| ir_max_k | $R=K\max(a,b)$ |
| ir_i2_k | $R=Kb$ |
| ir_id_abs_diff | $R=\lvert D-a-b\rvert$ |
| ir_sum_div_k | $R=(a+b)/K$ |
| ir_imax_sum_k | $R=\lvert I_{\max}-\sum I_{\text{other}}\rvert/K$ |
| ir_sqrt_cos | $R=\sqrt{-ab\cos\theta}$ |
| ir_sum_sq_cos | $R=-ab\cos\theta$ |
| ir_diff_sq_cos | $R=ab\cos\theta$ |

最后一版前端界面允许的组合不是任意笛卡尔积，而是：

| idEquation | 允许的 irEquation |
|---|---|
| id_i1 | ir_i2 |
| id_sum | ir_diff_k、ir_max_k、ir_i2_k、ir_id_abs_diff、ir_sum_div_k、ir_imax_sum_k、ir_sqrt_cos |
| id_diff | ir_sum_k、ir_max_k、ir_i2_k、ir_id_abs_diff、ir_sum_div_k、ir_imax_sum_k、ir_sqrt_cos |
| id_sum_sq | ir_sum_sq_cos |
| id_diff_sq | ir_diff_sq_cos |

对于只有两侧的 6I：

$$
I_{\max}-\sum I_{\text{other}}
=
\bigl||J_1|-|J_2|\bigr|
$$

### 5.3.1 当前生产求解器的限制

当前 calc/diff.py 并没有逐项严格反解上述所有公式：

1. ir_diff_k、ir_sum_k、ir_sum_div_k、ir_imax_sum_k 被合并为同一个和差求解器；
2. id_sum_sq、id_diff_sq 不按所选 irEquation 分流；
3. id_i1 直接令侧 1 等于 Id、侧 2 等于 Ir；
4. 未知枚举可能回退到另一种计算，而不是拒绝发波；
5. 求解完成后没有用所选原公式做正向闭环检查。

differential_math_lab/diffmath/equations.py 已经逐公式实现并做正向校验，但目前尚未接入 ApiDiffTest。

## 5.4 方程中的 K 与曲线中的 Kzd

截图中有两个容易混淆的“制动系数”。

### 方程参数 K

JSON：

~~~json
"equationSettings": {
  "kFactor": 2.0
}
~~~

它直接进入所选 Ir 方程，例如：

$$
R=\frac{|J_1-J_2|}{K}
$$

### 曲线/报告参数 Kzd

截图中“Kzd = Id/Ir”是保护特性曲线或结果表的计算方式。最后一版前端状态值与公式为：

| 前端值 | 公式 |
|---|---|
| delta_id/delta_ir | $K_{zd}=\Delta I_d/\Delta I_r$ |
| id/ir | $K_{zd}=I_d/I_r$ |
| ir/id | $K_{zd}=I_r/I_d$ |

该选择目前只在前端用于曲线或报告，不进入 ApiDiffTest 的电流反解。

所以：

~~~text
kFactor ≠ kzd
~~~

即使截图中二者数值偶然相同，也不能互相替代。

## 5.5 Kp1、Kp2

界面：

~~~text
保护内部：电流平衡系数
Kp1
Kp2
~~~

JSON：

~~~json
"equationSettings": {
  "kp1": 1.0,
  "kp2": 1.0
}
~~~

本文和 diffmath 明确定义保护正向方向为：

$$
\mathbf J_s
=
\mathbf M_s
\left(
p_sK_{Ps}\mathbf P_s\mathbf X_s
\right)
$$

因此测试仪反求方向包含：

$$
\mathbf X_s
=
\frac{1}{p_sK_{Ps}}
\mathbf P_s^T
\mathbf M_s^+
\mathbf J_s
$$

$\mathbf M_s^+$ 表示逆或 Moore-Penrose 伪逆。这里还有一个不能省略的可解条件：M0 是单位矩阵，可反解任意三相目标；M1～M12 都会滤除零序量，只能精确反解位于矩阵值域内的目标，即：

$$
\mathbf 1^T\mathbf J_s=0
$$

此时才有：

$$
\mathbf M_s\mathbf M_s^+\mathbf J_s=\mathbf J_s
$$

若 $\mathbf 1^T\mathbf J_s\ne0$，伪逆只能给出投影后的最小二乘结果，不能生成与原目标完全相同的保护比较域电流。这也是第 6.4 节为非零矩阵单独构造零和模板的原因。

当前 calc.diff 的实现也会在最后执行：

$$
i_{1,out}=\frac{i_1'}{K_{P1}}
$$

$$
i_{2,out}=\frac{i_2'}{K_{P2}}
$$

所以在其他参数不变时，Kp 越大，测试仪所需输出越小。

“Kp1、Kp2 辅助计算”弹窗中的 CT、容量、电压等原始输入当前不会作为独立参数发给后端。
但它们的计算结果会通过两条路径影响启动包：

1. 所选两侧的 KP 写入 equationSettings.kp1/kp2；
2. kpCalcParams.sides[0].in2 始终被组包为 equationSettings.Ie，isPerUnit=true 时会参与实验计算。

最后一版前端辅助计算包含下列思路。

弹窗状态路径：

| 界面字段 | 前端状态路径 |
|---|---|
| 计算方法 | kpCalcParams.method |
| 额定容量 Sn（MVA） | kpCalcParams.sn |
| 高/中/低侧 | kpCalcParams.sides[0] / [1] / [2] |
| 某侧额定电压 Un（kV） | kpCalcParams.sides[i].un |
| 某侧 CT 变比 | kpCalcParams.sides[i].ctRatio |
| 某侧保护端额定二次电流 In,2（A） | kpCalcParams.sides[i].in2 |
| 某侧补偿系数 KP | kpCalcParams.sides[i].kp |
| Y 侧 KP 除以 $\sqrt3$ | kpCalcParams.ySideSqrt3Div |
| Y 侧 KP 乘以 $\sqrt3$ | kpCalcParams.ySideSqrt3Mul |
| 弹窗内参与绕组 | 弹窗临时 involvedWinding；确认后写入 protectionSettings.involvedWinding，并一并留在 kpCalcParams.involvedWinding |

两个 Y 侧 $\sqrt3$ 选项由前端保持互斥。

最后一版前端的默认辅助计算数据为：

| 侧 | Un（kV） | CT 变比 | In,2（A） | KP |
|---|---:|---:|---:|---:|
| 高 H | 220 | 240 | 1.9682 | 1 |
| 中 M | 115 | 400 | 2.2592 | 1 |
| 低 L | 10.5 | 5000 | 1.9795 | 1 |

默认 Sn=180 MVA，method=manual。

前端实现的精确条件是 method !== current-in。也就是说，不仅 voltage-ct 和
per-unit，就连 manual 也会在监听器触发时先按三相额定电流更新 In,2。只要 Un 和 CT 变比为正，就按：

$$
I_{n,i,2}
=
\frac{S_n\times10^6}
{\sqrt3\,U_{n,i}\times10^3\,CTR_i}
$$

### 手动

method=manual。

主页的 equationSettings.kp1/kp2 输入框是禁用状态。用户实际在弹窗中填写各物理侧的：

$$
kpCalcParams.sides[i].kp
$$

点击确认后，前端根据 involvedWinding 选出两个值，再写入真正下发的
equationSettings.kp1/kp2。

### 按额定电压和 CT 变比

method=voltage-ct。

以高压侧为参考，某侧 i 的相对补偿可写成：

$$
K_{Pi}
=
\frac{U_{n,i}\,CTR_i}
{U_{n,H}\,CTR_H}
$$

其中 $CTR$ 必须统一定义为一次额定电流与二次额定电流之比，且各侧使用相同方向。

### 按保护额定二次电流

method=current-in。

若 $I'_{n,H}$ 和 $I'_{n,i}$ 是换算到保护二次侧的额定穿越电流：

$$
K_{Pi}
=
\frac{I'_{n,H}}{I'_{n,i}}
$$

### 按每侧标幺基准

method=per-unit。

若保护以各侧二次基准 $I_{n,i,2}$ 做归一化：

$$
K_{Pi}
=
\frac{1}{I_{n,i,2}}
$$

前端还存在对 Y 侧乘或除 $\sqrt3$ 的选项。这只能在确认保护内部 Kp 定义、CT 接法和
相位补偿位置后使用，不能只看绕组字母机械套用。最终仍应通过正常穿越工况验证。
对 id_sum 或 id_sum_sq：

$$
\mathbf J_1+\mathbf J_2\approx\mathbf0
$$

对 id_diff 或 id_diff_sq：

$$
\mathbf J_1-\mathbf J_2\approx\mathbf0
$$

id_i1 只用侧 1 定义动作量，没有上述两侧穿越抵消关系。

以及各侧保护显示的标幺电流是否一致。

最后一版 bundle 识别 Y 侧的精确规则是：对 vectorGroup 的各段做 Y/yn 字母判断，
只对判定为 Y 侧的 sides[i] 应用乘/除 $\sqrt3$。双绕组在 Kp 数组中使用
sides[0] 表示高压侧、sides[2] 表示低压侧；组别的第二段因此对应 sides[2]，
不是 sides[1]。

点击确认后，前端按 involvedWinding 选出两个 kp 值，写入真正下发的
equationSettings.kp1/kp2。弹窗临时 involvedWinding 同时会写回
protectionSettings.involvedWinding；因为确认时复制了整个弹窗对象，kpCalcParams 中也会留下
involvedWinding 属性。整个 kpCalcParams 对象仍不会下发，但 Ie 是它的一个派生例外：

$$
Ie=\operatorname{round}(kpCalcParams.sides[0].in2,6)
$$

即 Ie 永远取高压侧 sides[0]，不随参与绕组对变化。

保护对象为 generator 时，前端显示 manual 和 ct-ratio 两个选项。CT 比值重算使用：

$$
K_{P,H}=1
$$

$$
K_{P,M}=\frac{CTR_M}{CTR_H}
$$

$$
K_{P,L}=\frac{CTR_L}{CTR_H}
$$

实现细节要特别注意：当 protectionObject=generator 时，弹窗的“重新计算”函数并不再按
method 分支，而是总先按上述 CT 比值生成三个 KP。manual 模式下 KP 单元格随后仍可手工编辑，
所以不应把当前代码描述成两套完全独立、互不影响的计算分支。

Kp 必须为有限正数。当前生产代码对 Kp=0 会静默跳过除法，这不是合理的专业定义，前端必须禁止。

## 5.6 标幺值、Ie 与 RMS/峰值

界面“Id、Ir 以标幺值方式显示”对应：

~~~json
"equationSettings": {
  "isPerUnit": true,
  "Ie": 1.9682
}
~~~

线性电流公式应转换为：

$$
D_A=D_{pu}I_e
$$

$$
R_A=R_{pu}I_e
$$

平方量应转换为：

$$
D_{A^2}=D_{pu}I_e^2
$$

对应的平方型 Ir 同样使用 $I_e^2$。

当前 ApiDiffTest 的实际行为是：

1. isPerUnit 为 true 时，无论方程是否平方，都把 Id、Ir 各乘一次 Ie；
2. 随后无条件把 Id、Ir 乘 $\sqrt2$；
3. calc.diff 根据线性反解生成硬件幅度；
4. report 再把部分电流除以 $\sqrt2$。

因此当前行为隐含：

~~~text
界面点值 = RMS
FPGA statics 的交流幅度 = 峰值
~~~

在线性方程下：

$$
I_{peak}=\sqrt2 I_{RMS}
$$

但平方型方程不能简单把平方坐标也整体乘 $\sqrt2$。这部分必须在正式接入新数学模型时修正。

isPerUnit 必须发送 JSON 布尔值 true/false，不能发送字符串 "false"；Python 中非空字符串会被 bool() 判为 true。

## 5.7 导入和导出

“导入”“导出”按钮用于前端方案文件和界面状态，不对应运行中的 WebSocket 控制命令。
导入后的 protectionObject、windingType、vectorGroup、involvedWinding 等仍必须经过正常
组包展开；后端不会直接解析前端方案文件。

---

## 6. 接线方式、端子和相角

## 6.1 扩展差动 6I

界面“扩展差动（6I）”对应：

~~~json
"connectionSettings": {
  "type": "ext-differential"
}
~~~

端子枚举：

| API 值 | 测试仪通道 |
|---|---|
| high-y | Ia、Ib、Ic，即 6、7、8 |
| low-y | Ix、Iy、Iz，即 9、10、11 |

截图对应：

~~~json
"i1Terminal": "high-y",
"i2Terminal": "low-y"
~~~

high-y 和 low-y 只是当前代码中的两组三相通道名称，并不能证明保护绕组一定是 Y 接线。真正的绕组字母来自 protectionSettings。

两侧不得选择重叠的物理通道。最后一版前端有两个监听器，可以防止
i1Terminal === i2Terminal 这种“枚举字符串完全相同”的情况。但它不会展开复合端子后检查物理通道集合交集，
例如 ia 与 iab 是不同字符串，却共用 Ia。当前后端也没有做通道集合重叠校验；
如果真正重叠，I2 后写值会覆盖 I1。对 ext-differential 的 high-y/low-y 两个选项，
前端的字符串不相等约束恰好能保证两组不重叠；对 2I 复合端子则不够。

## 6.2 分相差动 2I

界面“分相差动（2I）”对应：

~~~json
"type": "phase-differential"
~~~

当前后端只显式判断 type == "ext-differential"；其他任何字符串都会落入 2I 分支，而不是报错。因此 2I 必须精确发送 phase-differential，调用端不应依赖这个宽松回退。

端子枚举：

| API 值 | 通道 |
|---|---|
| ia / ib / ic | Ia / Ib / Ic |
| ix / iy / iz | Ix / Iy / Iz |
| iab / ibc / ica | 对应两个源并联 |
| ixy / iyz / izx | 对应两个源并联 |
| iabc / ixyz | 对应三个源并联 |

若 2I 省略两个端子，setup 先给它们填入 6I 默认值 high-y/low-y，但这两个值不在 TERMINAL_2I 中，之后两侧都会静默回退到 Ia（通道 6），侧 2 再覆盖侧 1。所以 2I 不能省略 i1Terminal/i2Terminal。

当前 ApiDiffTest 将一个端子总电流平均分给所选并联源：

$$
I_{\text{each}}=\frac{I_{\text{terminal}}}{N}
$$

然后 calc.diff_bridge 还会按 phaseCorrection 与 y/d 字母对匹配侧做经验性 $\sqrt3$ 修正。

2I 当前忽略：

- vectorGroup clock；
- zeroSequenceCorrection；
- testPhase。

## 6.3 I1、I2 显示角

截图显示：

~~~text
I1 相位 = 0°
I2 相位 = 180°
~~~

当前后端没有读取 i1Angle、i2Angle 或 vectorGroupBalanceAngle 的代码。即使前端保留或额外发送这些字段，也不会直接控制波形。

当前实际角度来自：

1. 动作方程决定 J2 初始同相还是反相；
2. 标量解为负时再翻转 180°；
3. testPhase 生成 A/B/C、线间或正序模板；
4. 相位补偿矩阵反求实际端子相角。

对于 id_sum，当前代码初始令 J2 为 180°；对于 id_diff，初始令 J2 为 0°。

所以截图中的 0°/180°应理解为由当前方程推导出的接线提示，不是独立控制参数。

上述角度结论直接对应 6I 路径。2I 的当前代码还会在 solve() 返回的 theta2 上再加一个固定 180°，然后归一化到 [-180°, 180°)：

$$
\theta_{2,\mathrm{2I}}=\operatorname{normalize}(180^\circ+\theta_2)
$$

因此在 2I 中，id_sum 默认的 theta2=180° 最终会输出 0°，id_diff 默认的 theta2=0° 反而会输出 -180°。不能把 6I 截图的角度规则直接套到 2I。

## 6.4 测试相别 testPhase

6I 支持：

| 界面 | API |
|---|---|
| A 相差动 | A |
| B 相差动 | B |
| C 相差动 | C |
| AB 相差动 | AB |
| BC 相差动 | BC |
| CA 相差动 | CA |
| ABC 相差动 | ABC |

testPhase 首先选择“测哪一种差动元件/相别”，但 A、B、C 的保护比较域模板还取决于两侧补偿矩阵，不能在所有接线组别下都固定为单位向量。

先从两侧矩阵号中选第一个非零值作为参考矩阵号；若两侧均为 0，则取 $m_{ref}=0$：

$$
m_{ref}=\operatorname{firstNonZero}(m_{W1},m_{W2}),
\qquad
\text{若不存在非零值，则 }m_{ref}=0
$$

当 $m_{ref}=0$ 时，A、B、C 模板才分别是：

$$
\mathbf q_A=
\begin{bmatrix}
1\\0\\0
\end{bmatrix},
\qquad
\mathbf q_B=
\begin{bmatrix}
0\\1\\0
\end{bmatrix},
\qquad
\mathbf q_C=
\begin{bmatrix}
0\\0\\1
\end{bmatrix}
$$

任何非零补偿矩阵都会滤除零序量，即 $\mathbf M(m)\,[1,1,1]^T=\mathbf 0$。因此纯单位向量不在它的输出空间内，不能作为可逆求解目标。对于奇数矩阵，沿用“单端子稀疏注入”的试验约定，先由 A 端子物理注入形成保护比较域基向量，再按最大分量归一化：

$$
\widetilde{\mathbf q}_A
=
\mathbf M(m_{ref})
\begin{bmatrix}
\sqrt 3\\0\\0
\end{bmatrix},
\qquad
\mathbf q_A
=
\frac{\widetilde{\mathbf q}_A}
{\max_k\left|\widetilde q_{A,k}\right|}
$$

B、C 模板由 $\mathbf q_A$ 的通道循环移位得到。例如：

$$
m_{ref}=1:\quad
\mathbf q_A=
\begin{bmatrix}
1\\0\\-1
\end{bmatrix};
\qquad
m_{ref}=11:\quad
\mathbf q_A=
\begin{bmatrix}
1\\-1\\0
\end{bmatrix}
$$

对于非零偶数矩阵，单个物理相不能形成合适的单元素单位目标，使用可观测的相间环流作为 A 相试验模板，并循环移位得到 B、C：

$$
\mathbf q_A=
\begin{bmatrix}
1\\-1\\0
\end{bmatrix}
$$

这是一种可计算、可前向验证的试验仪注入约定；“A 相差动”在非零矩阵下可能同时激励两个保护比较元件，不能把它误解为电力系统中只有 A 相电流的通用故障模型。

AB、BC、CA 和 ABC 模板与上述单相分支不同，分别使用相间环流和正序三相模板：

$$
\mathbf q_{AB}=
\begin{bmatrix}
1\\-1\\0
\end{bmatrix},
\qquad
\mathbf q_{BC}=
\begin{bmatrix}
0\\1\\-1
\end{bmatrix},
\qquad
\mathbf q_{CA}=
\begin{bmatrix}
-1\\0\\1
\end{bmatrix}
$$

$$
\mathbf q_{ABC}=
\begin{bmatrix}
1\\e^{-j120^\circ}\\e^{j120^\circ}
\end{bmatrix}
$$

然后：

$$
\mathbf J_1=J_1\mathbf q
$$

$$
\mathbf J_2=J_2\mathbf q
$$

再通过每侧矩阵反求 $\mathbf X_1$、$\mathbf X_2$。

当前生产代码的路由必须特别注意：

- A/B/C 进入昂立经验分支；
- AB/BC/CA/ABC 进入矩阵分支；
- 这两条路径不是同一个统一模型；
- 2I 模式完全不使用 testPhase。

---

## 7. “测试项目”页

## 7.1 activeProject

| 界面 | API 值 | 当前行为 |
|---|---|---|
| 比率差动—边界搜索 | ratio-search | 固定 Ir，二分搜索 Id |
| 比率差动—定点测试 | ratio-fixed | 逐个输出给定 Ir/Id |
| 谐波制动—定点测试 | harmonic-fixed | 输出给定基波 Id 与谐波 |
| 谐波制动—边界搜索 | harmonic-search | 搜索谐波闭锁边界 |

当前后端只接受这四个字符串。未知项目最终会停止，不会自动回退到比率搜索。

## 7.2 点表通用字段

| 字段 | 含义 |
|---|---|
| id | 前端点行标识；报告按它匹配 |
| x | 横坐标；比率项目为 Ir，谐波项目为谐波量 |
| y | 纵坐标；比率项目为 Id，谐波项目为基波 Id |

前端只应发送已经勾选的行。当前后端不会检查 selected 字段，而是执行 points 数组内的全部元素。

最后一版前端的精确序列化规则：

| 项目 | Store 行字段与全局参数 | 发送元素 |
|---|---|---|
| ratio-search | row.ir、row.id_boundary、ratio.search.start/end | {id, x=round(ir,3), y=[round(min(id_boundary×start/100, id_boundary×end/100),3), round(max(...),3)]} |
| ratio-fixed | row.ir、row.id_val、row.id_boundary | {id, x=round(ir,3), y=round(id_val ?? id_boundary,3)} |
| harmonic-search | row.id_val、harmonic.search.start/end | {id, y=round(id_val,3), x=[round(min(start,end),4), round(max(start,end),4)]} |
| harmonic-fixed | row.harm、row.id_val | {id, x=round(harm,4), y=round(id_val,3)} |

这里的 ?? 是 JavaScript 空值合并，不是逻辑或。因此 ratio-fixed 的 id_val=0 是有效值，
不会回退到 id_boundary。harmonic-search 的 x 边界直接取全局 search.start/end，
不使用行上的 harm_boundary。

每行的 id 被编码为 Number(row.id ?? 已编码行数+1)。前端“添加”通常用当前数组长度+1
生成 id，删除当前行后会把剩余行重排为 1～N。组包器本身没有额外验证 id 为有限正整数。

只有 row.selected 为真的行才进入编码器。selected、status、isPerUnit 以及上表中未映射的
前端中间字段都不会进入发送元素。对必需数值做 Number() 后不是有限数的行，
编码器会静默跳过。

点表按钮全部属于前端编辑行为：

| 界面按钮 | 作用 | 是否单独发送命令 |
|---|---|---|
| 添加 | 在前端 points 中增加一行 | 否 |
| 删除当前行 | 删除当前选中行 | 否 |
| 清空列表 | 清空前端 points | 否 |
| 全选 | 把各行 selected 设为 true | 否 |
| 全部不选 | 把各行 selected 设为 false | 否 |

只有最终点击开始时，selected=true 的行才被编码进 start JSON。

截图点表为空时：

~~~text
points = []
~~~

最后一版前端会在组包前检查：

- points 数组非空；
- 至少有一行 selected=true。

这两项检查发生在 YM() 编码前，它们不检查“编码后是否还有合法行”。因此源数组虽然有选中行，
但所有选中行都缺少必需数值时，前端仍可能最终发出 points=[]。检查不通过时不会发送 start。
若其他客户端绕过该检查直接发送空点，或前端编码后得到空点，当前 ApiDiffTest
会调用 stopTest("No test points provided")。

## 7.3 比率边界搜索

结构：

~~~json
"ratio": {
  "precision": 0.01,
  "method": "binary",
  "points": [
    {
      "id": 1,
      "x": 1.0,
      "y": [0.0, 0.6]
    }
  ]
}
~~~

含义：

$$
x=R=I_r
$$

$$
y=[D_{\min},D_{\max}]
$$

后端只直接读取：

- ratio.points；
- ratio.precision。

截图中的百分比搜索范围由前端展开。若某行理论动作边界为 $D_b$：

$$
D_{\min}
=
D_b\frac{p_{\min}}{100}
$$

$$
D_{\max}
=
D_b\frac{p_{\max}}{100}
$$

截图为 0%～120%，所以：

$$
[D_{\min},D_{\max}]=[0,1.2D_b]
$$

若 $D_b=0.5$ A：

$$
[D_{\min},D_{\max}]=[0,0.6]\ \mathrm A
$$

当前搜索先测试两个端点。只有端点的动作状态不同，才继续二分；若两端都动作或都不动作，返回“未找到边界”。

## 7.4 搜索精度

ratio.precision 表示 Id 轴的收敛宽度：

$$
|D_{\max}^{(n)}-D_{\min}^{(n)}|\le\varepsilon
$$

截图：

$$
\varepsilon=0.01\ \mathrm A
$$

precision 必须大于 0。当前后端没有校验 0 或负数，错误值可能导致搜索不收敛。

## 7.5 搜索方式

界面可显示：

- 二分法；
- 线性搜索。

通常对应：

~~~json
"method": "binary"
~~~

或：

~~~json
"method": "linear"
~~~

但当前 ApiDiffTest 不读取 method，并始终调用：

~~~text
searchBoundary(..., mode=0)
~~~

所以当前无论发送 binary 还是 linear，实际都是二分法。

## 7.6 动作门槛和速断定值

截图：

~~~text
动作门槛 = 0.500 A
速断定值 = 5.000 A
~~~

它们的精确前端路径是 projectSettings.ratio.idMin 和 projectSettings.ratio.idInst，用于：

- 绘制理论特性；
- 生成某个 Ir 点的理论动作边界；
- 生成 points[].y 搜索区间；
- 报告中显示整定值和误差。

其中“某个 Ir 点的理论动作边界”不是只用 idMin/idInst 两个数就能完整定义。
最后一版 bundle 的 calculateRatioBoundary() 还读取：

~~~text
projectSettings.ratio.curveSegments[]
~~~

每段的前端字段是：

| 字段 | 含义 |
|---|---|
| name | 显示名称 |
| active | 是否参与边界计算 |
| type | threshold、slope 或 instant |
| irStart / irEnd | 该段 Ir 区间 |
| k | slope 段斜率 |

前端先用 idMin、idInst 和激活的 curveSegments 算出行上的 id_boundary，再按
ratio.search.start/end 百分比把它展开成启动包 points[].y。
projectSettings.ratio.error 也是前端曲线/误差状态，它和 curveSegments 都不原样进入 start payload。

当前后端不直接读取 idMin、idInst，也不会依据它们重新计算 points。

这意味着启动时真正决定扫描范围的是 points[].y，而不是界面下方仍然保留的门槛或速断字段。

## 7.7 系统频率

截图 ratio.frequency = 50.0。

当前 ApiDiffTest 不读取该字段。USEEngine 的保留节点 0 会把活动通道初始化为 50 Hz，所以 50 Hz 看起来能够工作；但这不是 ratio.frequency 生效。

如果需要 60 Hz，正确设计应把频率明确写到每个需要输出的通道 layer 0，并确保 ApiDiffTest 构造节点时保留 layer 0。当前代码会覆盖电流通道字典，因此尚不能仅靠这个界面字段保证 60 Hz。

## 7.8 定点测试

ratio-fixed 的点为：

~~~json
{
  "id": 1,
  "x": 1.0,
  "y": 0.5
}
~~~

即：

$$
R=1.0,\qquad D=0.5
$$

固定点和边界搜索应使用同一个电流生成器；差别只在于谁决定下一个 D。

## 7.9 谐波项目补充

虽然截图当前选择比率搜索，项目下拉框还可能包含谐波项目。

| JSON 路径 | 含义 |
|---|---|
| harmonic.order | 谐波次数；前端界面限制为 2～20，当前后端只做 int() 转换而不校验范围 |
| harmonic.angle | 谐波相角；当前后端未读取 |
| harmonic.applySide | i1 或 i2；当前后端把任何非 i1 值都当成 i2 |
| harmonic.frequency | 基频；当前后端未读取 |
| harmonic.harmonicConstant | 理论闭锁定值；当前后端未读取 |
| harmonic.harmonicError | 定值误差；当前后端未读取 |
| harmonic.harmonicErrorType | relative 或 absolute；当前后端用它决定 points[].x 的单位，任何其他值按 relative |
| harmonic.points[].x | relative 时为谐波百分比，absolute 时为谐波电流 A RMS |
| harmonic.points[].y | 基波 Id |
| harmonic.precision | 当前 ApiDiffTest 实际读取的谐波边界搜索精度；缺省 0.01 |
| harmonic.search.precision | 最后一版前端实际放置 UI 精度的位置；当前 ApiDiffTest 不读取，存在路径错配 |
| harmonic.search.start/end | 前端生成 points[].x 区间，同时也会发送；后端使用展开后的点 |
| harmonic.search.method | binary/linear；当前后端仍始终二分 |

当前谐波实现直接构造某侧通道的基波和谐波层，没有经过 compute_6ch。因此当前谐波路径会忽略：

- Id/Ir 方程；
- K；
- Kp1/Kp2；
- clock；
- phaseCorrection；
- zeroSequenceCorrection；
- harmonic.angle；
- harmonic.frequency。

relative 时：

$$
I_n=\frac{h}{100}I_1
$$

absolute 时，x 直接解释为第 n 次谐波电流。

这里有两个必须明确的当前行为：

1. absolute 的 x 是整个逻辑端子的 A RMS；2I 多通道并联时，后端再除以通道数。即使 isPerUnit=true，absolute x 也不会再乘 Ie；只有基波 points[].y 会乘 Ie。
2. 最后一版前端把 harmonicErrorType 当作特性曲线的“相对误差/绝对误差”选项，但 points[].x 仍由谐波含量/搜索百分比生成。当选择 absolute 误差时，当前后端却会把该 x 改解释为安培值，存在确定的语义/单位错配。

此外，harmonic.order 没有后端范围校验。order=1 会让谐波项覆盖基波 layer 1，order=0 会把幅值/角度误写到“直流/频率”layer 0；因此 2～20 必须由调用端保证。

---

## 8. “试验控制”页

## 8.1 差动类型 diffMode

| 界面 | diffMode | 专业含义 | 当前行为 |
|---|---:|---|---|
| 稳态差动 | 0 | 故障态直接满足目标 Id/Ir | 当前实际近似这一模式 |
| 零序差动 | 1 | 测试 I0 或 3I0 差动元件 | 字段未读取 |
| 工频变化量/突变量差动 | 2 | 对故障态减故障前态的增量计算 | 字段未读取 |

零序差动理论量可能是：

$$
3I_0=I_A+I_B+I_C
$$

或：

$$
I_0=\frac{I_A+I_B+I_C}{3}
$$

变化量差动应先得到：

$$
\Delta\mathbf X_s
=
\mathbf X_{s,\mathrm{fault}}
-\mathbf X_{s,\mathrm{prefault}}
$$

再让保护对 $\Delta\mathbf X_1$、$\Delta\mathbf X_2$ 计算 Id/Ir。

当前代码对 diffMode=0、1、2 生成相同的故障电流，没有实现后二者。

## 8.2 模拟故障前

JSON：

~~~json
"simulatePreFault": true
~~~

当前有效：

- true：从节点 1 开始；
- false：直接从节点 2 开始。

当前后端没有对 simulatePreFault 做 bool() 或严格类型校验，而是直接在 if 中使用。必须发送 JSON 布尔值 false；字符串 "false" 在 Python 中是真值，仍会从节点 1 开始。

但节点 1 当前不是由 loadCurrent 计算出的负荷态，而是：

~~~text
保留 statics 电压
六路电流全部置零
~~~

因此“模拟故障前”目前更准确的含义是“先输出一段零电流复归状态”。

## 8.3 故障前时间和故障限时

界面单位为秒，后端字段单位为毫秒：

$$
t_{\mathrm{API,ms}}
=
1000t_{\mathrm{UI,s}}
$$

截图：

$$
1.000\ \mathrm s
\longrightarrow
1000\ \mathrm{ms}
$$

必须发送：

~~~json
"preFaultDuration": 1000,
"faultDuration": 1000
~~~

不能发送 1.0。当前 ApiDiffTest 直接 int() 后写入 timeoutMs，1.0 会被解释为 1 ms。

另外，timeoutMs=0 在 USEEngine 中不会形成“立即超时”，而会禁用定时出口，所以前端也不应发送 0。

## 8.4 负荷电流

截图 loadCurrent = 0.0 A。

专业上，稳态穿越负荷应在两侧形成经过保护补偿后互相制衡的量。例如：

$$
\mathbf J_{1,L}=\mathbf Q_L
$$

$$
\mathbf J_{2,L}=-\mathbf Q_L
$$

当前 ApiDiffTest 完全不读取 loadCurrent：

- 故障前六路电流恒为 0；
- 故障态不会叠加负荷；
- diffMode=2 也不会用它生成故障前基准。

截图为 0 A 时这个缺陷暂时不影响结果；一旦界面填非零值，当前输出仍不会变化。

## 8.5 实际三节点流程

~~~text
simulatePreFault=true

Node 1  故障前：固定电压 + 六路零电流
   │ preFaultDuration
   ▼
Node 2  故障态：固定电压 + 计算所得差动电流
   │ DI 匹配 或 faultDuration
   ▼
Node 3  挂起态：固定电压 + 六路零电流
   │ API 开始下一测试点
   └──────────────────────────────► Node 1 或 Node 2
~~~

除节点内的两个 Duration 外，当前软件还有固定等待：

- EngineCtrl 启动保留节点 0 后等待 100 ms，才调用 API setup；
- ApiDiffTest setup 后预热 500 ms；
- 首次上传节点 1/3 后等待 50 ms；
- 每个测试点更新节点 2 后再等待 50 ms，然后手动触发起始节点。

更重要的是，比率和谐波的每个点都用同一个 30 s 软件超时等待节点 3。这 30 s 包含节点 1 和节点 2 的总执行时间，不是分别给两个节点 30 s。simulatePreFault=true 时应保证：

$$
t_{preFault}+t_{fault}<30\ \mathrm{s}
$$

并留出通信与调度余量。若软件 30 s 先到，_test_one_point() 只会返回“未动作+空 statics”，不会在该分支中主动将正在执行的硬件节点切到 3，后续点可能在前一点尚未自然结束时开始。

---

## 9. 动作接点、逻辑与/或及翻转

## 9.1 八路开入位

| 界面 | 位 | 十六进制 |
|---|---:|---:|
| A | bit 0 | 0x01 |
| B | bit 1 | 0x02 |
| C | bit 2 | 0x04 |
| R | bit 3 | 0x08 |
| a | bit 4 | 0x10 |
| b | bit 5 | 0x20 |
| c | bit 6 | 0x40 |
| r | bit 7 | 0x80 |

高位控制：

| 位 | 值 | 含义 |
|---|---:|---|
| bit 8 | 0x100 | 1=逻辑与；0=逻辑或 |
| bit 9 | 0x200 | 1=以当前节点入口 DI 为参考；0=以整场实验起始 DI 为参考 |
| bit 10 | 0x400 | 参考状态按 8 位取反 |

最后一版前端的界面状态中，logic=1 表示 AND，logic=0 表示 OR；logic 和八个复选框
本身不分别下发，组包时合成为一个 logicMask 整数。

对应的前端状态链为：

~~~text
testParams.logic
testParams.logicMask[]
        ↓
testParams.logicMaskResult
        ↓
params.sys.logicMask
~~~

这里 testParams.logicMask[] 保存的是通道索引，不是已展开的位值：

| 开入 | logicMask[] 元素 | 最终位值 |
|---|---:|---:|
| A / B / C / R | 0 / 1 / 2 / 3 | 1 / 2 / 4 / 8 |
| a / b / c / r | 4 / 5 / 6 / 7 | 16 / 32 / 64 / 128 |

例如默认勾选 A/B/C 时，Store 中的数组是 [0,1,2]，不是 [1,2,4]。Logic 组件再将它转为
低八位 0x07，并按 logic 决定是否加 0x100，结果写入 logicMaskResult。

当前差动 API 直接把 params.sys.logicMask 原样写到节点 2，没有自动增加 0x200 或 0x400。

## 9.2 “翻转”的准确数学含义

令：

$$
m=logicMask\land0xFF
$$

先根据 bit 9 选取基准状态：

$$
r_0=
\begin{cases}
DI_{entry}, & logicMask\land0x200\ne0\\
DI_{start}, & logicMask\land0x200=0
\end{cases}
$$

再根据 bit 10 决定是否将 8 位基准状态取反：

$$
DI_{ref}=
\begin{cases}
r_0\oplus0xFF, & logicMask\land0x400\ne0\\
r_0, & logicMask\land0x400=0
\end{cases}
$$

计算：

$$
v=(DI_{now}\oplus DI_{ref})\land m
$$

逻辑或：

$$
v\ne0
$$

逻辑与：

$$
v=m
$$

该比较每次只看 DI_now 的当前快照，没有“某位一旦翻转就永久记住”的逐位锁存。因此 AND 要求所有选中位在同一判定时刻都满足与参考值不同；某位先翻转又恢复后，它就不再计入。

截图的推荐值是 $logicMask=0x107$：选择 A/B/C、使用逻辑与，且 bit 9、bit 10 都未置位，所以这个特例才可简化为 $DI_{ref}=DI_{start}$。ApiDiffTest 对 logicMask 原样透传，因此不能把该特例写成所有调用方都成立的规则。

所以界面的“开入 A 翻转”不是要求 A 为 1，也不是控制 DO，而是要求 A 相对整场实验开始时的电平发生变化。

## 9.3 截图的 logicMask

截图勾选 A、B、C：

$$
m=0x01+0x02+0x04=0x07
$$

又选择逻辑与：

$$
logicMask=0x100+0x07=0x107=263
$$

如果发送 7，则含义变成 A/B/C 任意一路翻转就动作。

logicMask=0 会彻底禁用 DI 判据，不表示“全部开入”。

最后一版前端的初始状态还存在一处错配：界面默认显示 AND 并勾选 A/B/C，但缓存的
logicMaskResult 可能仍是 7。只有用户实际操作过逻辑或勾选框才触发重算。因此调试
截图所示状态时，不能只看界面，必须检查发出的 JSON 是否真的是 263。

## 9.4 当前动作报告风险

USEEngine 会按 logicMask 决定是否从节点 2 跳到节点 3；但 ApiDiffTest.onDi() 的软件标记逻辑更宽：

~~~text
只要节点 2 期间收到任何 DI 回报，就把 _di_tripped 设为 true
~~~

它没有再次判断：

- 变化的是不是 mask 选中的通道；
- AND 条件是否已经全部满足；
- 最终节点跳转是 DI 触发还是超时。

因此可能出现：

1. 未选中的 DI 发生变化；
2. 软件先把 tripped 记为 true；
3. 硬件仍等待所选条件；
4. 最终故障限时到达；
5. API 却把该点当作动作。

这是当前实现缺陷，不是 logicMask 参数本身的定义。

反向漏判也可能发生：USEEngine 会在节点执行循环中直接用已有 DI 电平评估匹配；若进入节点 2 时条件已满足，可能不需要新的 DI_CHANGE 回报就跳到节点 3。此时 onDi() 没有机会把 _di_tripped 设为 true，API 可能把真正的 DI 跳转当成未动作。

节点 2 又把 diMatchId 和 timeoutId 都设为 3，所以单看 onUpdate(nodeId=3) 无法辨别出口原因。USEEngine 的同一次软件判定顺序为“手动触发 → 超时 → DI”；若超时与 DI 在同一次判定时都成立，会先返回超时出口。

## 9.5 “辅助电压”和“电流跟踪”页

辅助电压页最终应编码到 params.statics 的电压通道 0～5。对于每一路：

~~~json
"channel": {
  "0": [dcAmplitude, frequencyHz],
  "1": [fundamentalAmplitude, phaseDeg]
}
~~~

最后一版前端会把界面 RMS 电压转换为交流幅度后再写 statics，具体换算见第 3.3 节。
辅助电压不会进入 Id/Ir 方程或电流补偿矩阵，只会与故障前、故障态和挂起态一起保持
输出。

“电流跟踪”页是根据界面公式或 value_update 展示 Ia～Iz 的结果视图，不是独立输入
参数，也没有对应的 start payload 对象。它适合用来检查：

- 六路活动通道是否正确；
- 幅度是 RMS 还是峰值；
- I2 的同相/反相是否正确；
- 单相和多相是否使用一致的补偿模型。

---

## 10. 截图配置对应的数学计算

截图核心参数为：

~~~text
Y/Y-12
无相位校正
无 Y 侧零序校正
Id = |J1 + J2|
Ir = |J1 - J2| / K
K = 2
Kp1 = Kp2 = 1
6I
A 相
~~~

在截图条件下采用 M0/M0，即两侧矩阵均为单位阵。

令一个测试点为：

$$
R=I_r,\qquad D=I_d
$$

若 $D\le KR$，选择两侧反相：

$$
J_1=a
$$

$$
J_2=-b
$$

于是：

$$
D=|a-b|
$$

$$
R=\frac{a+b}{K}
$$

解得：

$$
a=\frac{KR+D}{2}
$$

$$
b=\frac{KR-D}{2}
$$

等价的有符号写法为：

$$
J_1=\frac{KR+D}{2}
$$

$$
J_2=\frac{D-KR}{2}
$$

当 $D<KR$ 时，$J_2<0$，应序列化成正幅值加 180°。

K=2 时：

$$
J_1=R+\frac D2
$$

$$
J_2=\frac D2-R
$$

再考虑 Kp：

$$
X_1=\frac{J_1}{K_{P1}}
$$

$$
X_2=\frac{J_2}{K_{P2}}
$$

对于 A 相、6I、默认端子：

$$
\mathbf X_1=
\begin{bmatrix}
X_1\\0\\0
\end{bmatrix}
$$

$$
\mathbf X_2=
\begin{bmatrix}
X_2\\0\\0
\end{bmatrix}
$$

即输出到：

~~~text
Ia = |X1|，角度由 X1 符号决定
Ix = |X2|，角度由 X2 符号决定
Ib、Ic、Iy、Iz = 0
~~~

当前 API 还会在线性计算前把 RMS 坐标整体乘 $\sqrt2$，所以最终 FPGA 幅度为：

$$
X_{s,peak}=\sqrt2X_{s,RMS}
$$

### 10.1 数值例

取：

$$
R=1.0\ \mathrm A
$$

$$
D=0.5\ \mathrm A
$$

$$
K=2,\qquad K_{P1}=K_{P2}=1
$$

则 RMS 保护域电流：

$$
J_1=\frac{2\times1.0+0.5}{2}=1.25\ \mathrm A
$$

$$
J_2=\frac{0.5-2\times1.0}{2}=-0.75\ \mathrm A
$$

因此 RMS 端子量：

~~~text
Ia = 1.25 A ∠ 0°
Ix = 0.75 A ∠ 180°
其他四路 = 0 A
~~~

当前 API 写入 FPGA 前转换为峰值：

$$
I_{a,peak}=1.25\sqrt2=1.767767\ \mathrm A
$$

$$
I_{x,peak}=0.75\sqrt2=1.060660\ \mathrm A
$$

这就是截图中 I1 显示 0°、I2 显示 180°的数学来源；当前并不是由两个角度输入框直接控制。

---

## 11. 参数总表：当前代码是否真正使用

| 界面/参数 | JSON 路径 | 数学或流程作用 | 当前状态 |
|---|---|---|---|
| 页面试验类型 | baseSettings.testType（前端状态） | 0=6I/2I，1=母线 | 展开为 variant，并决定 protectionSettings/busbarSettings 二选一 |
| 保护类型 | protectionSettings.protectionObject（前端状态） | 选择 DUT 模型和 Kp 辅助计算分支 | 不原样下发，但可间接改变 kp1/kp2 |
| 双/三绕组 | protectionSettings.windingType（前端状态） | 限制参与侧并决定组别/Kp 展开 | 不原样下发，但会改变 letter/clock 和 kp1/kp2 |
| 接线组别 | 展开为四个 letter/clock | 提示补偿 Profile | 仅比率 6I 使用展开值；2I 只读 letter；谐波忽略 |
| 相位校正 | protectionSettings.phaseCorrection | 选择补偿侧/矩阵 | 6I、2I 以不同方式使用 |
| Y 侧零序校正 | protectionSettings.zeroSequenceCorrection | M12 零序投影 | 仅当前比率 6I 路径 |
| 参与绕组 | protectionSettings.involvedWinding（前端状态） | 选择两侧 | 不原样下发；当前 Kp 侧选择生效，三绕组 letter/clock 展开存在错配 |
| Id 方程 | equationSettings.idEquation | 动作量定义 | 比率 6I/2I 使用 |
| Ir 方程 | equationSettings.irEquation | 制动量定义 | 比率 6I/2I 使用，但部分路由不准确 |
| K | equationSettings.kFactor | Ir 公式参数 | 仅比率 6I/2I 使用；谐波忽略 |
| Kp1/Kp2 | equationSettings.kp1/kp2 | 保护正向增益，反求时除 | 仅比率 6I/2I 使用；谐波忽略 |
| 标幺复选框 | equationSettings.isPerUnit | 单位转换 | 比率时 Id/Ir 乘 Ie；谐波时仅基波 y 乘 Ie |
| Ie | equationSettings.Ie | 标幺基准 | 后端默认 1；谐波 absolute x 不乘 Ie；前端实际取高压侧 in2 |
| Kp 辅助计算输入 | kpCalcParams 等 | 算出 Kp 和 Ie | 对象不原样下发；派生的 kp1/kp2 和 sides[0].in2→Ie 会下发 |
| 6I/2I | connectionSettings.type | 选择生成器 | 使用 |
| I1/I2 端子 | connectionSettings.i1Terminal/i2Terminal | 映射物理通道 | 使用 |
| I1/I2 显示角 | i1Angle/i2Angle 等 | 接线提示 | 后端不读 |
| 项目 | projectSettings.activeProject | 选择固定/搜索/谐波 | 使用 |
| 测试相别 | projectSettings.testPhase | 保护域模板 | 6I 使用，2I 忽略 |
| 点表 | ratio.points/harmonic.points | 测试坐标 | 使用 |
| 动作门槛 | projectSettings.ratio.idMin（前端状态） | 曲线/整定边界 | 不原样下发，用于生成 row.id_boundary |
| 速断值 | projectSettings.ratio.idInst（前端状态） | 曲线/整定边界 | 不原样下发，用于生成 row.id_boundary |
| 曲线分段/误差 | projectSettings.ratio.curveSegments/error（前端状态） | 理论边界和报告误差 | 不原样下发；curveSegments 参与 row.id_boundary 生成 |
| Kzd 公式 | projectSettings.ratio.kzd（前端状态） | 报告/曲线 | 不下发 |
| 系统频率 | ratio.frequency / harmonic.frequency | 发波频率 | 当前均不读 |
| 比率搜索百分比 | projectSettings.ratio.search.start/end（前端状态） | 生成 points[].y 区间 | 不原样下发，只展开到点 |
| 谐波搜索区间 | projectSettings.harmonic.search.start/end | 生成 points[].x 区间 | search 对象与展开点都下发，当前后端只用展开点 |
| 搜索精度 | ratio.precision / harmonic.precision | 收敛条件 | 后端使用这两个平铺路径；前端谐波却发 harmonic.search.precision |
| 搜索方法 | ratio.method / harmonic.search.method | binary/linear | 当前不读，始终二分 |
| 差动类型 | testParams.diffMode | 稳态/零序/变化量 | 当前不读 |
| 模拟故障前 | testParams.simulatePreFault | 是否使用节点 1 | 使用 |
| 故障前时间 | testParams.preFaultDuration | 节点 1 超时 | 使用，必须 ms |
| 故障限时 | testParams.faultDuration | 节点 2 超时 | 使用，必须 ms |
| 负荷电流 | testParams.loadCurrent | 穿越负荷 | 当前不读 |
| 防抖 | 后端 params.debounce；前端实际 params.sys.debounce | DI 硬件防抖 | 当前路径错配 |
| 动作逻辑/开入 | params.sys.logicMask | DI 匹配 | 使用 |
| 辅助电压 | auxVoltageData[]→params.statics 的 0～5 通道 | 固定电压输出 | 使用；isDc 的当前 layer 组包有缺陷 |
| 电流跟踪 | currentTrackingData（前端状态） | 展示当前计算/回报 | 不作为差动反解输入；启动前会先清零再参与 statics 构造 |

### 11.1 当前后端的缺省值和类型处理

下表是 Python 代码在字段缺失时的实际回退，不表示这些默认组合一定具有专业意义。表中以 payload 开头的路径均位于 params.payload 下；未写 payload 前缀的 equationSettings、protectionSettings 和 projectSettings 行也同样是 params.payload 内的相对路径：

| JSON 路径 | 缺省值 | 当前类型处理/风险 |
|---|---:|---|
| params.debounce | 20 | 直接参与浮点乘法，应为数值 ms |
| params.sys.logicMask | 7 | 不转 int，应为整数位域；7 是 A/B/C 逻辑或 |
| params.statics | {} | 应为通道字典；其中每个键都会尝试转成通道号，不会忽略任意成员 |
| payload.projectSettings.activeProject | ratio-fixed | 只有四个已支持字符串会执行测试 |
| 当前项目子对象.points | [] | 应为列表；空列表会 stopTest |
| payload.projectSettings.testPhase | A | 不做枚举校验；比率 6I 非法值生成全零目标，谐波 6I 非法值回退为 A |
| payload.equationSettings.Ie | 1.0 | 用 float() 转换，合法数字字符串也能通过 |
| payload.equationSettings.isPerUnit | false | 用 bool() 转换，所以非空字符串 "false" 会被当成 true |
| payload.testParams.faultDuration | 1000 | 用 int() 转换，单位 ms；浮点会截断小数部分 |
| payload.testParams.preFaultDuration | 1000 | 同上 |
| payload.testParams.simulatePreFault | true | 不转 bool，非空字符串 "false" 仍为真 |
| payload.connectionSettings.type | ext-differential | 只精确匹配该值时为 6I，其他值全部进入 2I |
| payload.connectionSettings.i1Terminal | high-y | 6I 可用；2I 中是非法默认，会再回退到 Ia |
| payload.connectionSettings.i2Terminal | low-y | 6I 可用；2I 中是非法默认，也会再回退到 Ia |
| equationSettings.idEquation | id_sum | 在比率路径由 diff_bridge 回退 |
| equationSettings.irEquation | ir_diff_k | 在比率路径由 diff_bridge 回退 |
| equationSettings.kFactor | 2.0 | 不做 float() 转换，应发 JSON 数字 |
| equationSettings.kp1/kp2 | 1.0/1.0 | 不做 float() 转换，应发有限正数 |
| protectionSettings.vectorGroupLetter1/2 | y/y | 当前不做枚举校验 |
| protectionSettings.vectorGroupHighSideClock/LowSideClock | 0/0 | 用 int() 转换；只有 highSide 字段存在时才进入新格式分支 |
| protectionSettings.phaseCorrection | none | 非法值通常等价于未匹配任何校正侧 |
| protectionSettings.zeroSequenceCorrection | false | 不做严格布尔校验 |
| projectSettings.ratio.precision | 0.01 | 不做数值转换或正数校验 |
| projectSettings.harmonic.precision | 0.01 | 同上；不读 harmonic.search.precision |
| projectSettings.harmonic.order | 2 | 用 int() 转换，不做 2～20 范围校验 |
| projectSettings.harmonic.harmonicErrorType | relative | 只有精确等于 absolute 时按绝对 A RMS，其他值都按百分比 |
| projectSettings.harmonic.applySide | i1 | 只有精确等于 i1 时选侧 1，其他值都选侧 2 |

比率定点中单点 id/x/y 的缺省值都是 0；搜索项则要求被搜索轴是至少两元素的列表。当前后端对字典、列表和枚举的类型校验很少；额外的 JSON 字段可以保留且通常会被忽略，但已读字段仍必须保持上述类型。

---

## 12. 推荐/目标前端组包转换

本节是推荐的目标设计，不是对最后一版 bundle 已经全部做对的声明。建议前端严格按以下顺序组包：

1. 根据保护类型限制可选项，但不把它当成后端计算参数。
2. 根据双/三绕组及“参与绕组”选出逻辑侧 1、侧 2。
3. 将组别拆成 y/d 字母和两个 clock；12 统一归一化为 0。
4. 发送实际 phaseCorrection 和 zeroSequenceCorrection。
5. 发送最终 kp1/kp2；辅助计算过程不必发送。
6. 根据标幺复选框发送真正的 JSON 布尔值，并在启用时发送正确 Ie。
7. 根据接线模式生成合法且不重叠的 i1Terminal/i2Terminal。
8. 只把已选择点写入对应 ratio.points 或 harmonic.points。
9. 在前端把动作边界和百分比展开为每一点的实际搜索区间。
10. 把界面秒值乘 1000 后写入两个 Duration 字段。
11. 把防抖写到顶层 params.debounce。
12. 按 A/B/C/R/a/b/c/r 生成低八位，AND 时再加 0x100。
13. 不依赖多余字段影响后端；当前后端未读取的字段即使发送也不会改变实验。

最后一版 bundle 已实现的数值转换为：

| 目标字段 | 当前前端转换 |
|---|---|
| params.sys.debounce | Math.round(Number(testParams.debounceTime ?? 0)) |
| payload.testParams.faultDuration | max(0, round(Number(界面秒值) × 1000)) |
| payload.testParams.preFaultDuration | max(0, round(Number(界面秒值) × 1000)) |
| payload.testParams.loadCurrent | Number 后保留 6 位小数 |
| payload.equationSettings.Ie | Number(kpCalcParams.sides[0].in2 ?? 0) 后保留 6 位小数 |
| payload.projectSettings.ratio.precision | Number(ratio.search.precision ?? 0)；空值回退为 0 |
| payload.projectSettings.harmonic.search.precision | Number(harmonic.search.precision ?? 0)；空值回退为 0 |

已知未达到目标的地方包括：debounce 仍放在 params.sys，三绕组编码器忽略
involvedWinding，involvedWinding 不会生成端子，harmonic.search.precision 与当前后端读取路径不一致，
以及初始 logicMaskResult 可能与界面的 AND 状态不一致。

目标设计示意伪代码：

~~~text
clock = displayedClock % 12

durationMs = round(durationSeconds * 1000)

diBits =
    (A ? 0x01 : 0) |
    (B ? 0x02 : 0) |
    (C ? 0x04 : 0) |
    (R ? 0x08 : 0) |
    (a ? 0x10 : 0) |
    (b ? 0x20 : 0) |
    (c ? 0x40 : 0) |
    (r ? 0x80 : 0)

logicMask = diBits | (logicIsAnd ? 0x100 : 0)

point.y = [
    boundaryId * searchStartPercent / 100,
    boundaryId * searchEndPercent / 100
]
~~~

---

## 13. 当前联调中必须注意的问题

### 13.1 会直接造成参数无效

1. debounce 放到 params.sys；
2. 界面秒值不乘 1000；
3. 只发送组合组别字符串，不发送 letter/clock；
4. 三绕组只发送参与绕组名称，不展开成两侧参数；
5. 期望 ratio.frequency 改变当前输出频率；
6. 期望 method=linear 让当前后端执行线性搜索；
7. 期望 diffMode 或 loadCurrent 改变波形；
8. 把 I1/I2 显示角当成当前可控制输入。

### 13.2 会造成错误波形或报告

1. K 与 Kzd 混用；
2. Kp=0、负值或字符串；
3. 两侧端子选择重叠；
4. 非法端子值触发静默默认；
5. 2I 未显式选择合法 ia～ixyz 端子；
6. precision 小于等于 0；
7. isPerUnit 使用字符串布尔值；
8. 平方量仍按线性 Ie 和 RMS/峰值规则缩放；
9. 认为单相经验路径通过就能证明多相矩阵路径正确；
10. Node 2 中未选 DI 的变化被软件误记为动作。

### 13.3 报告电流的现状

所有 report 都由 EngineCtrl 包成下列外层，并自动附加 module：

~~~json
{
  "type": "report",
  "data": {
    "points": []
  },
  "module": "differential_test"
}
~~~

四种项目的当前上报条件和点字段不相同：

| activeProject | 何时发 report | data.points[] 字段 |
|---|---|---|
| ratio-fixed | 仅 _di_tripped=true | id, x, y, i1, i2 |
| ratio-search | 仅 searchBoundary() 找到边界 | id, x, y, i1, i2 |
| harmonic-fixed | 每个点都发 | id, x, y, blocked |
| harmonic-search | 仅找到边界 | id, x, y, blocked=true |

因此 ratio-fixed 的未动作点、以及两端状态相同而找不到边界的搜索点，当前都不发 report，只有过程 value_update 或日志。报告中也没有 DI 硬件时间戳、动作时间或节点结束原因。

比率 6I report 中的 i1、i2 固定取计算数组第 0 路和第 3 路的幅值标量，并由峰值除回 RMS：

~~~text
i1 = channels[0][0] / sqrt(2)
i2 = channels[3][0] / sqrt(2)
~~~

这只天然对应逻辑侧的第一个分量。B、C、BC 等相别下，它可能为 0 或不能代表所有实际活动分量。合法且互不重叠地交换物理端子映射不会改变这里的两个逻辑侧数值，因为 report 在端子映射之前就从 channels 中取值；问题是“固定取第一个分量”，不是“交换端子”。

2I report 的 i1/i2 是分摊之前的整个逻辑端子 RMS 幅值；实际每个并联输出通道的幅值还要除以该端子的通道数。

启用 isPerUnit 时，比率 report 的 x/y 仍是前端点表坐标（pu），i1/i2 却已经是乘 Ie 后的物理 A RMS；同一报告内存在混合单位。

ratio-search 还有一处值对齐风险：x/y 来自 searchBoundary() 返回的区间第二端状态一侧，而i1/i2 只在某次探测被 _di_tripped 标记为 true 时更新。当第一端动作、第二端不动作时，最终 x/y 位于不动作侧，i1/i2 却来自上一个被标记为动作的探测点，并不严格对应同一次输出。

harmonic-search 找到两端状态不同的边界后，无论搜索方向如何都固定写 blocked=true；该字段不记录返回边界一侧的真实动作方向。

ApiDiffTest 还会在每次探测后主动发送：

~~~json
{
  "type": "value_update",
  "id": 1,
  "x": 1.0,
  "y": 0.5,
  "static": {},
  "di": 0,
  "do": 0,
  "module": "differential_test"
}
~~~

其中 static 是 ApiDiffTest 构造的请求值，交流幅值为峰值，尚未反映寄存器量化。EngineCtrl 在节点 base 生效时另会发送只带 static 的 value_update，它是寄存器反算的物理值；DI/DO 变化又分别作为只带 di 或 do 的 value_update 发送。因此前端不能假设每个测试点只收到一条 value_update，也不能把上述所有 static 回报都当成 RMS。

### 13.4 现有 API 文档与实际代码的已确认差异

RelayProtection/docs/api/differential_test/differential-test.md 可用于了解字段背景，但下列
内容不能再作为联调事实：

| 旧文档说法 | 当前实际 |
|---|---|
| 防抖位于 params.sys.debounce | 前端确实这样发，但 EngineCtrl 只读 params.debounce |
| points/statics 不做 RMS/峰值换算 | 比率 Id/Ir 在 ApiDiffTest 中会乘 $\sqrt2$；辅助电压也由前端换为幅度值 |
| isPerUnit 不下发 | 最后一版前端实际下发 isPerUnit 和 Ie |
| connectionSettings 含 I1/I2 角度 | 组包器显式去掉角度，后端也不读 |
| testParams 中的差动模型会控制算法 | diffMode、loadCurrent 当前未读取 |
| frequency 控制实验频率 | ratio/harmonic frequency 当前未接入差动电流节点 |

因此抓包、当前 Python 源码和实际六路回算应优先于旧文档描述。

---

## 14. 推荐的后续统一方向

本节不是当前 API 行为，而是让界面参数与专业公式真正一致所需要的目标。

1. 用显式 RelayModel/DUT Profile 代替“单相经验分支 + 多相另一套矩阵分支”。
2. 每一侧明确 matrix、Kp、极性、相序和通道排列。
3. 任何 Id/Ir 反解完成后，用同一保护正向模型闭环验证。
4. 按方程量纲分别处理线性量、平方量和标幺值。
5. 让 diffMode=0/1/2 分别进入稳态、残流和变化量模型。
6. 用 loadCurrent 生成故障前穿越量，并在需要时叠加到故障态。
7. 让 frequency 真正进入所有输出通道的 layer 0。
8. 让 method 明确选择二分或线性搜索。
9. 使用实际 DI 判据结果，而不是“Node 2 收到任意 DI 回报”判断动作。
10. 报告按实际活动相和端子汇总，不固定取 Ia/Ix。

---

## 15. 与代码和公式文档的对应

| 内容 | 文件 |
|---|---|
| 当前 API 参数读取和节点 | ../RelayProtection/api/ApiDiffTest.py |
| 当前 JSON 到计算参数桥接 | ../RelayProtection/calc/diff_bridge.py |
| 当前 Id/Ir 与 6I 经验算法 | ../RelayProtection/calc/diff.py |
| DI 位域执行 | ../RelayProtection/logic/USEEngine.py |
| 防抖实际读取位置 | ../RelayProtection/logic/EngineCtrl.py |
| 严格 Id/Ir 定义与反解 | diffmath/equations.py |
| M0～M12 与伪逆 | diffmath/matrices.py |
| 保护正向模型 | diffmath/model.py |
| legacy 界面到矩阵 Profile | diffmath/profiles.py |
| 六路生成与相别模板 | diffmath/generator.py |
| 全部数学推导 | [差动保护详细计算过程与数学公式.md](差动保护详细计算过程与数学公式.md) |

最终联调时应同时核对：

~~~text
界面显示值
前端实际发出的完整 start JSON
ApiDiffTest 日志
六路输出的 RMS/峰值口径
保护实际显示的每相 Id/Ir
昂立同设置下的六路幅值与相角
~~~

只有这六项闭合，才能确认参数映射、数学模型和硬件输出属于同一套定义。
