"""
端到端验证（真实 USEEngine.coreLoop）——锁定四个迁移实验 API 的关键修复：

  P0  节点 0 与引擎保留校准节点 0x0000 冲突 + nextId∈{0,0xFFFF} 剪枝
      → 实验节点必须从 1 开始编号，否则多节点序列在第一个节点后即被剪枝终止。
  P1  StatusSeq/TMTest 的 _FillMissingChannels 在 physDictToReg 之后插入 str 通道键，
      导致引擎 _compileNode 的 `1 << ch` 崩溃 → 改为用同一转换补齐(int 键)。
  P2  InvTime 末个测试点：故障态 DI_MATCH 直接跳 0xFFFF 终止，不触发 onUpdate 结算，
      动作时间会丢失 → _onStop 补算当前测试点。

本测试用忠实复刻 EngineCtrl(编译/派发/0xFFFF=停机) 的 RealCtrl 驱动真实引擎，
不依赖 oldrelay，可在任意环境运行：
    cd RelayProtection && python3 tests/test_migrated_engine_e2e.py
"""
import os
import sys
import time
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.USEEngine import USEEngine, USENode          # noqa: E402
from logic.FPGACodec import HWCodec                      # noqa: E402
from api.ApiStatSequ import ApiStatSequ                # noqa: E402
from api.ApiTimeMeas import ApiTimeMeas                      # noqa: E402
from api.ApiInvsTime import ApiInvsTime                    # noqa: E402
from api.ApiSlipTest import ApiSlipTest                  # noqa: E402

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✅ {name}")
    else:
        FAIL += 1; print(f"  ❌ {name}")


def _now_us():
    return int(time.perf_counter() * 1_000_000)


class _MockGw:
    """每发一帧立即回 ACK；hw_ts 用真实微秒，使动作时间相减为正。"""
    def __init__(self): self.eng = None
    def SendBytes(self, frame): self.eng.HandleHwFeedback(_now_us(), 0x0000)


class RealCtrl:
    """忠实复刻 EngineCtrl 中 API 依赖的那部分：节点编译、事件派发、0xFFFF=_doStop。"""
    def __init__(self):
        self.gw = _MockGw()
        self.engine = USEEngine(self.gw, self.onEvent)
        self.gw.eng = self.engine
        self.nodes = {}; self.di = 0; self.do = 0
        self.running = False; self.api = None; self._module = None
        self.reports = []; self.msgs = []; self.visited = []; self.stopped = False
        self.entry_ts = {}; self.terminal_ts = None
        self.on_enter = {}     # {engineNodeId: (delay_s, [di, ...])}

    # —— EngineCtrl 同款编译 ——
    def _frames(self, dDict, code):
        masks = {}
        for ch, layers in (dDict or {}).items():
            for l, (a, p) in layers.items():
                masks[(l, a, p)] = masks.get((l, a, p), 0) | (1 << ch)
        return [HWCodec.BuildParamFrame(code, (l - 1) & 0xFF, m, a, p) for (l, a, p), m in masks.items()]

    def _compile(self, n):
        return USENode(
            mode=n.mode, interval=n.interval, resetTime=n.resetTime,
            baseFrame=self._frames(n.base, HWCodec.DDS_WR_SHADOW),
            resetFrame=self._frames(n.reset, HWCodec.DDS_WR_STAGE),
            stepFrames=[self._frames(s, HWCodec.DDS_STEP_SHADOW) for s in n.steps] if n.steps else None,
            resetDo=n.resetDo, doActions=n.doActions, countOverId=n.countOverId,
            diMatchMask=n.diMatchMask, diMatchId=n.diMatchId,
            timeoutMs=n.timeoutMs, timeoutId=n.timeoutId)

    # —— API 依赖的 ctrl 接口 ——
    def upsertNodes(self, apiNodes):
        if not self.running: return False
        comp = {}
        for nid, an in apiNodes.items():
            self.nodes[nid] = an; comp[nid] = self._compile(an)
        self.engine.upsertNodes(comp); return True

    def trigNode(self, nid):
        if not self.running: return False
        self.engine.manualTrig(nid); return True

    def stopTest(self, reason=None):
        if not self.running: return
        self.running = False; self.engine.manualTrig(0xFFFF)

    def sendReport(self, d): self.reports.append(d)
    def sendStop(self): pass
    def _send(self, d): self.msgs.append(d)
    def sendDi(self): pass
    def sendDo(self): pass
    def sendNodeUpdate(self, node_id): self.msgs.append({"type": "value_update", "nodeId": node_id})
    def sendError(self, message): self.msgs.append({"type": "error", "msg": message})

    async def onEvent(self, evt):
        if evt[0] == 0:
            nodeId, tick, ts = evt[1], evt[2], evt[3]
            if nodeId == 0xFFFF:                 # 真实 EngineCtrl: 跳 0xFFFF == _doStop
                self.terminal_ts = ts
                if self.api:
                    terminal_cb = getattr(self.api, "onTerminal", None)
                    if callable(terminal_cb):
                        terminal_cb(ts)
                    self.api.onStop()
                self.stopped = True; self.running = False; return
            if tick == 0:
                self.visited.append(nodeId)
                self.entry_ts[nodeId] = ts
                if nodeId in self.on_enter:
                    delay, seq = self.on_enter.pop(nodeId)
                    asyncio.create_task(self._inject(delay, seq))
            if self.api and self.running: self.api.onUpdate(nodeId, tick, ts)
        elif evt[0] == 1:
            self.di = evt[1]
            if self.api: self.api.onDi(evt[1], evt[2])
        elif evt[0] == 2:
            self.do = evt[1]

    async def _inject(self, delay, seq):
        for di in seq:
            await asyncio.sleep(delay)
            # di 低 8 位为 DI 值；0x100 表示 di=0 又不会被当成 ACK(0x0000)
            self.engine.HandleHwFeedback(_now_us(), di)

    async def start(self, module, api, params, on_enter=None):
        self.api = api; self._module = module
        self.on_enter = on_enter or {}
        self.engine.start(); await asyncio.sleep(0.02)
        self.engine.dbncUs = 0          # mock 下去掉去抖偏移，避免 hw_ts 相减为负
        self.running = True
        self.engine.manualTrig(0x0000); await asyncio.sleep(0.05)
        api.setup(self, params)


async def t_status():
    print("StatusSeq —— 3 状态定时序列")
    c = RealCtrl()
    params = {"sys": {}, "nodes": [
        {"id": 1, "mode": "STATIC", "static": {"0": {"0": [10.0, 50.0]}}, "triggers": [{"condition": "TIME_OUT", "timeout": 20, "post_condition_delay_ms": 0, "next_id": 2}]},
        {"id": 2, "mode": "STATIC", "static": {"0": {"0": [20.0, 50.0]}}, "triggers": [{"condition": "TIME_OUT", "timeout": 20, "post_condition_delay_ms": 0, "next_id": 3}]},
        {"id": 3, "mode": "STATIC", "static": {"0": {"0": [30.0, 50.0]}}, "triggers": [{"condition": "TIME_OUT", "timeout": 20, "post_condition_delay_ms": 0, "next_id": -2}]},
    ]}
    await c.start("universal_sequence", ApiStatSequ(), params)
    await asyncio.sleep(0.4)
    check("引擎节点 1/2/3 全部进入(未被剪枝)", all(n in c.visited for n in (1, 2, 3)))
    rows = c.reports[-1]["rows"] if c.reports else []
    check("报告含 3 行", len(rows) == 3)
    check("end_condition = TIME_OUT,TIME_OUT,TIME_OUT",
          [r["end_condition"] for r in rows] == ["TIME_OUT", "TIME_OUT", "TIME_OUT"])
    expected_last_duration = (
        round(
            (
                (c.terminal_ts - c.entry_ts[3])
                & 0xFFFFFFFF
            )
            / 1_000_000.0,
            6,
        )
        if c.terminal_ts is not None and 3 in c.entry_ts
        else None
    )
    check("最后状态持续时间等于0xFFFF-entry硬件时间差",
          bool(rows) and rows[-1]["duration_sec"] == expected_last_duration)


async def t_status_stub():
    print("StatusSeq —— DI_MATCH + 后延时桩(stub) 中转")
    c = RealCtrl()
    params = {"sys": {}, "nodes": [
        {"id": 1, "mode": "STATIC", "static": {"0": {"0": [10.0, 50.0]}},
         "triggers": [{"condition": "DI_MATCH", "di_mask": 1, "post_condition_delay_ms": 30, "next_id": 2}]},
        {"id": 2, "mode": "STATIC", "static": {"0": {"0": [20.0, 50.0]}},
         "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": -2}]},
    ]}
    # 进入节点1 后注入 di=1（相对 diStart 变位）触发 DI_MATCH，经 stub 延时 30ms 后到节点2
    await c.start("universal_sequence", ApiStatSequ(), params, on_enter={1: (0.02, [0x01])})
    await asyncio.sleep(0.4)
    rows = c.reports[-1]["rows"] if c.reports else []
    check("两个状态都进入并产出 2 行", len(rows) == 2)
    check("节点1 end_condition=DI_MATCH(未被 stub 的 TIME_OUT 覆盖)",
          bool(rows) and rows[0]["end_condition"] == "DI_MATCH")


async def t_status_sweep_countover():
    print("StatusSeq —— SWEEP 走完 count → COUNT_OVER")
    c = RealCtrl()
    params = {"sys": {}, "nodes": [
        {"id": 1, "mode": "SWEEP", "static": {"0": {"0": [10.0, 50.0]}},
         "dynamic": {"stepTime": 8, "count": 5, "steps": {"0": {"0": [0.5, 0.0]}}},
         "triggers": [{"condition": "COUNT_OVER", "next_id": 2}]},
        {"id": 2, "mode": "STATIC", "static": {"0": {"0": [20.0, 50.0]}},
         "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": -2}]},
    ]}
    await c.start("universal_sequence", ApiStatSequ(), params)
    await asyncio.sleep(0.4)
    rows = c.reports[-1]["rows"] if c.reports else []
    check("SWEEP 节点(1)与下一态(2)都进入", all(n in c.visited for n in (1, 2)))
    check("节点1 end_condition=COUNT_OVER", bool(rows) and rows[0]["end_condition"] == "COUNT_OVER")


async def t_status_manual():
    print("StatusSeq —— MANUAL_TRIG (next_node)")
    c = RealCtrl()
    params = {"sys": {}, "nodes": [
        {"id": 1, "mode": "STATIC", "static": {"0": {"0": [10.0, 50.0]}},
         "triggers": [{"condition": "MANUAL_TRIG", "next_id": 2}]},
        {"id": 2, "mode": "STATIC", "static": {"0": {"0": [20.0, 50.0]}},
         "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": -2}]},
    ]}
    api = ApiStatSequ()
    await c.start("universal_sequence", api, params)
    await asyncio.sleep(0.08)
    api.onWebCommand({"cmd": "next_node"})    # 上位机手动推进
    await asyncio.sleep(0.2)
    rows = c.reports[-1]["rows"] if c.reports else []
    check("手动推进后进入节点2", 2 in c.visited)
    check("节点1 end_condition=MANUAL_TRIG", bool(rows) and rows[0]["end_condition"] == "MANUAL_TRIG")


async def t_status_ditimes():
    print("StatusSeq —— 多路 DI 首次变位时刻")
    c = RealCtrl()
    params = {"sys": {}, "nodes": [
        {"id": 1, "mode": "STATIC", "static": {"0": {"0": [10.0, 50.0]}},
         "triggers": [{"condition": "TIME_OUT", "timeout": 150, "next_id": 2}]},
        {"id": 2, "mode": "STATIC", "static": {"0": {"0": [20.0, 50.0]}},
         "triggers": [{"condition": "TIME_OUT", "timeout": 20, "next_id": -2}]},
    ]}
    # 节点1 内：+20ms bit0(A) 变位，+40ms 追加 bit1(B) 变位
    await c.start("universal_sequence", ApiStatSequ(), params,
                  on_enter={1: (0.02, [0x01, 0x03])})
    await asyncio.sleep(0.4)
    rows = c.reports[-1]["rows"] if c.reports else []
    r0 = rows[0] if rows else {}
    check("diA / diB 均记录到首次变位", r0.get("diA") is not None and r0.get("diB") is not None)
    check("未变位通道 diC 为 null", "diC" in r0 and r0.get("diC") is None)


async def t_tmtest():
    print("TMTest —— 3 节点 + ENTER_STATE_2 计时器(DI 闭合停)")
    c = RealCtrl()
    params = {"sys": {}, "statics": {"1": {"6": {"0": [1.0, 50.0]}}, "2": {"6": {"0": [5.0, 50.0]}}, "3": {"6": {"0": [1.0, 50.0]}}},
              "timers": {"timer1": {"start_mode": "ENTER_STATE_2", "stop_mode": "DI_A_CLOSE"}},
              "nodes": [
                  {"id": 1, "next_id": 2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
                  {"id": 2, "next_id": 3, "triggers": [{"condition": "TIME_OUT", "timeout": 80}]},
                  {"id": 3, "next_id": -2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
              ]}
    # DI_OPEN_IS_HIGH=True → 闭合沿=1→0：进入节点2 后注入 di=1(断开) 再注入 di=0(闭合)→停表
    await c.start("time_measurement", ApiTimeMeas(), params, on_enter={2: (0.012, [0x01, 0x0100])})
    await asyncio.sleep(0.4)
    check("引擎节点 1/2/3 全部进入(未被剪枝)", all(n in c.visited for n in (1, 2, 3)))
    rep = c.reports[-1] if c.reports else {}
    check("timer1 计时已停且 ≥0", rep.get("timer1_elapsed_ms") is not None and rep.get("timer1_elapsed_ms") >= 0)


async def t_tmtest_cascade():
    print("TMTest —— 级联(TIMER1_STOP_START) + State-2 TIMER_1_2 自动结束")
    c = RealCtrl()
    params = {"sys": {}, "statics": {"1": {"6": {"0": [1.0, 50.0]}}, "2": {"6": {"0": [5.0, 50.0]}}, "3": {"6": {"0": [1.0, 50.0]}}},
              "timers": {"timer1": {"start_mode": "ENTER_STATE_2", "stop_mode": "DI_A_CLOSE"},
                         "timer2": {"start_mode": "TIMER1_STOP_START", "stop_mode": "DI_A_OPEN"}},
              "nodes": [
                  {"id": 1, "next_id": 2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
                  {"id": 2, "next_id": 3, "triggers": [
                      {"condition": "TIMER_1_2", "next_id": 3},
                      {"condition": "TIME_OUT", "timeout": 300}]},
                  {"id": 3, "next_id": -2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
              ]}
    # node2: DI_A open(0x01)→ close(0x0100=di0, 1→0 停 timer1 并级联启 timer2)→ open(0x01, 停 timer2)
    api = ApiTimeMeas()
    await c.start("time_measurement", api, params, on_enter={2: (0.015, [0x01, 0x0100, 0x01])})
    await asyncio.sleep(0.4)
    rep = c.reports[-1] if c.reports else {}
    check("State-2 经 TIMER_1_2 自动结束 → 进入节点3", 3 in c.visited)
    check("timer1(动作)与 timer2(返回/级联)均计得 >0",
          (rep.get("timer1_elapsed_ms") or 0) > 0 and (rep.get("timer2_elapsed_ms") or 0) > 0)
    check("timer3 未配置 → null", rep.get("timer3_elapsed_ms") is None)


async def t_tmtest_di_start():
    print("TMTest —— DI 边沿启动计时器(DI_B_CLOSE_START → DI_C_OPEN)")
    c = RealCtrl()
    params = {"sys": {}, "statics": {"1": {"6": {"0": [1.0, 50.0]}}, "2": {"6": {"0": [5.0, 50.0]}}, "3": {"6": {"0": [1.0, 50.0]}}},
              "timers": {"timer3": {"start_mode": "DI_B_CLOSE_START", "stop_mode": "DI_C_OPEN"}},
              "nodes": [
                  {"id": 1, "next_id": 2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
                  {"id": 2, "next_id": 3, "triggers": [{"condition": "TIME_OUT", "timeout": 200}]},
                  {"id": 3, "next_id": -2, "triggers": [{"condition": "TIME_OUT", "timeout": 20}]},
              ]}
    # DI_B open(0x02)→ close(0x0100=di0, bit1 1→0 启 timer3)→ DI_C open(0x04, bit2 0→1 停 timer3)
    api = ApiTimeMeas()
    await c.start("time_measurement", api, params, on_enter={2: (0.015, [0x02, 0x0100, 0x04])})
    await asyncio.sleep(0.4)
    rep = c.reports[-1] if c.reports else {}
    check("timer3 由 DI_B 闭合启动、DI_C 断开停止，计得 >0", (rep.get("timer3_elapsed_ms") or 0) > 0)
    check("timer1/timer2 未配置 → null",
          rep.get("timer1_elapsed_ms") is None and rep.get("timer2_elapsed_ms") is None)


async def t_invtime():
    print("InvTime —— 2 测点 I-t，故障态注入跳闸 DI")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {},
              "payload": {"ratedFrequency": 50.0, "iBeforeFault": 0.0, "tBeforeFault": 20, "faultLimitTime": 300,
                          "faultType": "A_GROUND", "iDefinition": "FAULT_CURRENT",
                          "outputMode": {"voltage": "GROUP1_UABC", "current": "GROUP1_IABC"},
                          "directionality": {"enabled": False}},
              "testPoints": [{"id": 1, "value": 3.0}, {"id": 2, "value": 5.0}]}
    # 故障节点 2/4 注入跳闸 di=1；点2 故障前节点 3 先把 di 复位 0，保证 node4 有 0->1 边沿
    await c.start("inverse_time_it_test", ApiInvsTime(), params,
                  on_enter={2: (0.012, [0x01]), 3: (0.004, [0x0100]), 4: (0.012, [0x01])})
    await asyncio.sleep(0.6)
    check("故障节点 2 与 4 都进入(末点未被剪枝)", 2 in c.visited and 4 in c.visited)
    rep = c.reports[-1] if c.reports else {}
    rows = rep.get("rows", rep.get("data", {}).get("rows", []))
    check("报告含 2 行", len(rows) == 2)
    check("两测点动作时间均非空(含末点结算)", all(r.get("actualTime") is not None for r in rows))


async def t_invtime_vft():
    print("InvTime —— V·F-t (V_T) 2 测点，故障态注入跳闸")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {},
              "payload": {"vBeforeFault": 57.735, "fBeforeFault": 50.0, "tBeforeFault": 20,
                          "faultLimitTime": 300, "testProject": "V_T", "vOutputMode": "UABC_POS",
                          "angle": 0.0, "vbValue": 57.735, "fbValue": 50.0,
                          "vfVariable": "VOLTAGE_AMPLITUDE_V"},
              "testPoints": [{"id": 1, "value": 65.0}, {"id": 2, "value": 70.0}]}
    await c.start("inverse_time_vft_test", ApiInvsTime(), params,
                  on_enter={2: (0.012, [0x01]), 3: (0.004, [0x0100]), 4: (0.012, [0x01])})
    await asyncio.sleep(0.6)
    rep = c.reports[-1] if c.reports else {}
    rows = rep.get("rows", rep.get("data", {}).get("rows", []))
    check("故障节点 2 与 4 都进入(末点未被剪枝)", 2 in c.visited and 4 in c.visited)
    check("报告 2 行且用 vOrFValue 字段", len(rows) == 2 and all("vOrFValue" in r for r in rows))
    check("两点动作时间均非空", all(r.get("actualTime") is not None for r in rows))


async def t_invtime_notrip():
    print("InvTime —— 不跳闸(faultLimitTime 超时) → actualTime=null")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {},
              "payload": {"ratedFrequency": 50.0, "iBeforeFault": 0.0, "tBeforeFault": 20,
                          "faultLimitTime": 60, "faultType": "A_GROUND", "iDefinition": "FAULT_CURRENT",
                          "outputMode": {"voltage": "GROUP1_UABC", "current": "GROUP1_IABC"},
                          "directionality": {"enabled": False}},
              "testPoints": [{"id": 1, "value": 3.0}]}
    await c.start("inverse_time_it_test", ApiInvsTime(), params)   # 不注入 DI → 超时
    await asyncio.sleep(0.4)
    rep = c.reports[-1] if c.reports else {}
    rows = rep.get("rows", rep.get("data", {}).get("rows", []))
    check("故障节点(2)进入后超时终止", 2 in c.visited and c.stopped)
    check("未跳闸点 actualTime=null", len(rows) == 1 and rows[0].get("actualTime") is None)


async def t_slip():
    print("Slip —— testType0 零距离首轮 + 多轮搜索")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {"0": {"0": [0.0, 50.0], "1": [50.0, 0.0]}},
              "payload": {"testType": 0, "slipVarType": "0_0", "slipPhase": "0", "slipPhasor": 0,
                          "slipStart": 50.0, "slipDDt": -50.0, "returnTime": 15, "waitTime": 15,
                          "actionSearchStart": 50.0, "actionSearchStep": -1.0, "actionSearchEnd": 48.0}}
    await c.start("slip_test", ApiSlipTest(), params)
    # 第一轮零距离走 1→3；第二轮开始才有节点2。等至少2次复归后注入跳闸。
    tripped = False
    for _ in range(40):
        await asyncio.sleep(0.02)
        if not tripped and c.visited.count(1) >= 2:
            c.engine.HandleHwFeedback(_now_us(), 0x01); tripped = True
    await asyncio.sleep(0.2)
    check("扫描节点(2)被进入(未被剪枝)", 2 in c.visited)
    check("复归节点(1)进入多次(搜索循环工作)", c.visited.count(1) >= 2)
    check("产出报告", len(c.reports) >= 1)


async def t_slip_zero_distance_action():
    print("Slip —— testType0 零距离轮次在 WAIT 阶段动作")
    c = RealCtrl()
    params = {
        "sys": {"logicMask": 1},
        "statics": {"0": {"0": [0.0, 50.0]}},
        "payload": {
            "testType": 0,
            "slipVarType": "0_0",
            "slipPhase": "0",
            "slipPhasor": 2,
            "slipStart": 50.0,
            "slipDDt": 0.0,
            "returnTime": 15,
            "waitTime": 100,
            "actionSearchStart": 50.0,
            "actionSearchStep": -1.0,
            "actionSearchEnd": 50.0,
        },
    }
    await c.start(
        "slip_test",
        ApiSlipTest(),
        params,
        on_enter={3: (0.01, [0x01])},
    )
    await asyncio.sleep(0.3)
    check(
        "零距离路径只进入1/3/4而不进入2",
        all(node in c.visited for node in (1, 3, 4))
        and 2 not in c.visited,
    )
    check(
        "WAIT阶段动作报告起点值",
        bool(c.reports)
        and c.reports[-1].get("actionValue") == 50.0,
    )


async def t_slip_actiontime():
    print("Slip —— testType1 动作时间(注入跳闸算 actionTime)")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {"0": {"0": [0.0, 50.0]}},
              "payload": {"testType": 1, "slipVarType": "0_0", "slipPhase": "0", "slipPhasor": 2,
                          "slipStart": 50.0, "slipEnd": 45.0, "slipDDt": -50.0,
                          "returnTime": 15, "waitTime": 200, "timerStartValue": 49.0}}
    # 扫描节点(2)进入后 ~40ms 注入跳闸（此时已过 timerTick）
    await c.start("slip_test", ApiSlipTest(), params, on_enter={2: (0.04, [0x01])})
    await asyncio.sleep(0.3)
    rep = c.reports[-1] if c.reports else {}
    check("testType1 报告 actionTime 非空", rep.get("testType") == 1 and rep.get("actionTime") is not None)
    check("报告带 timerStartValue", "timerStartValue" in rep)


async def t_slip_exhaust():
    print("Slip —— testType0 搜索耗尽(无跳闸 → actionValue=null)")
    c = RealCtrl()
    params = {"sys": {"logicMask": 1}, "statics": {"0": {"0": [0.0, 50.0], "1": [50.0, 0.0]}},
              "payload": {"testType": 0, "slipVarType": "0_0", "slipPhase": "0", "slipPhasor": 0,
                          "slipStart": 50.0, "slipDDt": -50.0, "returnTime": 12, "waitTime": 12,
                          "actionSearchStart": 50.0, "actionSearchStep": -1.0, "actionSearchEnd": 48.0}}
    await c.start("slip_test", ApiSlipTest(), params)   # 不注入 DI → 搜索耗尽
    await asyncio.sleep(0.6)
    rep = c.reports[-1] if c.reports else {}
    check("搜索耗尽后停机", c.stopped)
    check("testType0 actionValue=null", rep.get("testType") == 0 and rep.get("actionValue") is None)


async def main():
    for t in (t_status, t_status_stub, t_status_sweep_countover, t_status_manual,
              t_status_ditimes, t_tmtest, t_tmtest_cascade, t_tmtest_di_start,
              t_invtime, t_invtime_vft, t_invtime_notrip,
              t_slip, t_slip_zero_distance_action,
              t_slip_actiontime, t_slip_exhaust):
        try:
            await t()
        except Exception:
            import traceback; traceback.print_exc()
            global FAIL; FAIL += 1
        print()
    print(f"RESULT: {PASS} passed, {FAIL} failed")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(1 if FAIL else 0)
