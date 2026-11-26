"""
ApiExampleTest.py — 实验 API 骨架模板（复制改名后使用）

演示最常用的"多点连续 + 二分边界搜索 + SOE 动作时间"范式（skill §8 模式 B/C/D）。
把 TODO 处替换为你的实验逻辑即可。删掉用不到的部分。

要点回顾（详见 SKILL.md）：
  - 点间停在真实 IDLE 节点，绝不跳 0xFFFF（否则第一个点后整实验结束）。
  - 动作时间用硬件 SOE 时间戳。
  - DI 边沿检测 OR 上 0x200，并以故障入口 DI 为基线。
  - 每个 await 后检查 self.isActive。
"""

import math
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple

from api.BaseApi import BaseApi, ApiNodeData
from calc.calculation_engine import calculate_fault, calculate_normal, pack_channels

logger = logging.getLogger("ApiExampleTest")

# ── 节点 ID（全部真实节点；IDLE 用于点间挂起，绝不用 0xFFFF 作点间目标）──
PREFAULT, FAULT, IDLE = 1, 2, 3

DI_REF_PREV = 0x200          # diMatchMask 边沿检测标志：以"本节点入口"为基准
TICKS_PER_MS = 327.68        # 硬件时间戳 → 毫秒


class ApiExampleTest(BaseApi):
    MODULE_KEY = "example_test"          # TODO: 改为你的前端模块标识

    def __init__(self):
        super().__init__()
        self._points = []
        self._trip_mask = 0
        self._precision = 0.01
        self._done = asyncio.Event()
        self._task = None
        # 每点运行态
        self._cur_node = 0
        self._fault_ts: Optional[int] = None
        self._di_base = 0
        self._action_ms: Optional[float] = None

    # ── 1. 解析参数并启动 ──
    def _onSetup(self, params: Dict[str, Any]):
        payload = params.get("payload", {})
        self._points = payload.get("points", [])
        self._precision = float(payload.get("precision", 0.01))
        # TODO: 解析 sys / di / model 等
        sys_cfg = payload.get("sys", {})
        self._v = float(sys_cfg.get("v", 57.735))
        self._f = float(sys_cfg.get("f", 50.0))
        # 跳闸 DI 掩码（示例：A/B/C 三相跳，bit0/1/2）
        self._trip_mask = int(payload.get("di", {}).get("mask", 0x07))

        if not self._points:
            self.ctrl.stopTest("No test points")
            return
        self._task = asyncio.create_task(self._run())

    def _onStop(self):
        if self._task and not self._task.done():
            self._task.cancel()

    # ── 2. 硬件回调 ──
    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        self._cur_node = nodeId
        if nodeId == FAULT and tick == 0:
            self._fault_ts = hw_ts            # 记录故障施加时刻（SOE 基准）
            self._di_base = self.ctrl.di      # 记录 DI 基线（边沿检测用）
        elif nodeId == IDLE and tick == 0:
            self._done.set()                  # 回到 IDLE = 本点结束

    def onDi(self, di: int, hw_ts: int):
        if self._cur_node != FAULT or self._action_ms is not None or self._fault_ts is None:
            return
        if (di ^ self._di_base) & self._trip_mask:                  # 边沿：相对故障入口
            self._action_ms = round((hw_ts - self._fault_ts) / TICKS_PER_MS, 1)

    # ── 3. 主循环：预热 → 建 IDLE → 逐点 ──
    async def _run(self):
        try:
            await asyncio.sleep(0.3)                                # 预热
            if not self.isActive:
                return
            if not self.ctrl.upsertNodes({IDLE: self._build_idle()}):
                return
            for pt in self._points:
                if not self.isActive:
                    return
                await self._search_point(pt)
            if self.isActive:
                self.ctrl.stopTest()                               # 全部测完才结束
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("example test error")
            if self.isActive:
                self.ctrl.stopTest(f"Error: {e}")

    # ── 4. 二分边界搜索（沿某物理量区间）──
    async def _search_point(self, pt: Dict):
        pid = pt.get("id", 0)
        lo, hi = pt["range"][0], pt["range"][1]                     # TODO: 你的搜索区间
        boundary, btime = None, None
        # 端点先判定，确认区间内有翻转（此处略，可参考 ApiImpedanceTest）
        while (hi - lo) > self._precision:
            if not self.isActive:
                return
            mid = (lo + hi) / 2.0
            hit, t = await self._test_one(pt, mid)
            if not self.isActive:
                return
            self.ctrl._send({"type": "value_update", "id": pid,
                             "value": round(mid, 4), "hit": hit, "actionTimeMs": t})
            if hit:                                                 # 过量类：越大越动作 → 收上界
                hi = mid; boundary, btime = mid, t
            else:
                lo = mid
        self.ctrl.sendReport({"rows": [{"id": pid, "boundary": boundary, "actionTimeMs": btime}]})

    # ── 5. 单点施加：建故障节点 → 触发 → 等回 IDLE ──
    async def _test_one(self, pt: Dict, value: float) -> Tuple[bool, Optional[float]]:
        # TODO: 用 value 算本次注入相量。示例：A 相接地，short-circuit current = value
        V, I = calculate_fault(ft=0, sys_v=self._v, i_load_amp=0, i_load_ang=0,
                               dir_val=0, i_f_amp=value, z_mag=pt.get("z", 1.0),
                               z_ang=pt.get("ang", 75.0), formula=0, p1=0.667, p2=0.0, ct_val=0)
        phys = pack_channels(V, I, vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f)

        fault = ApiNodeData(mode=1)
        fault.base = self.physDictToReg(phys)
        fault.timeoutMs, fault.timeoutId = int(pt.get("time", 1000)), IDLE      # 超时 → IDLE
        fault.diMatchMask, fault.diMatchId = self._trip_mask | DI_REF_PREV, IDLE  # 跳闸 → IDLE

        # 可选故障前态
        pre = ApiNodeData(mode=1)
        pre.base = self.physDictToReg(pack_channels(*calculate_normal(self._v, 0, 0, 0),
                                                    vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f))
        pre.timeoutMs, pre.timeoutId = int(pt.get("preTime", 100)), FAULT

        if not self.ctrl.upsertNodes({PREFAULT: pre, FAULT: fault}):
            return False, None

        # 复位本点状态并触发
        self._cur_node = 0
        self._fault_ts = None
        self._action_ms = None
        self._done.clear()
        self.ctrl.trigNode(PREFAULT)

        try:
            await asyncio.wait_for(self._done.wait(),
                                   timeout=(pt.get("preTime", 100) + pt.get("time", 1000)) / 1000 + 2)
        except asyncio.TimeoutError:
            logger.error("point timeout waiting IDLE")
        return self._action_ms is not None, self._action_ms

    # ── 6. IDLE 节点：输出正常量、无出口（等 trigNode）──
    def _build_idle(self) -> ApiNodeData:
        phys = pack_channels(*calculate_normal(self._v, 0, 0, 0),
                             vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f)
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(phys)
        return n
