"""
ApiImpdTest.py — 阻抗特性测试 API

实现基于 Async 的二分查找状态机：
  1. 解析扫描线段 (start -> end)。
  2. 动态生成节点链：RESET -> PRE_FAULT -> FAULT -> IDLE。
  3. 通过 `calculate_fault` 动态算出当前阻抗点的相量（支持 klEqualsZero）。
  4. 搜寻临界跳闸点，实时上报 `value_update`，终了上报 `report`。

节点流转约定（与 ApiDiffTest / ApiLineTest 一致）：
  - 测点之间停在真实的 IDLE 节点，**绝不**把 FAULT 的出口指向 0xFFFF。
    0xFFFF 是引擎保留的"停止/错误"态：跳到它会触发 EngineCtrl._doStop()
    （清空节点表 + onStop），导致整个实验在第一个点后即终止。
  - FAULT 的超时/跳闸出口都指向 IDLE_NODE；onUpdate 检测进入 IDLE 来置位完成事件。
  - 只有全部测点跑完才 stopTest() → 引擎转 0xFFFF。
"""

import math
import logging
import asyncio
from typing import Dict, Any, Optional, Tuple

from api.BaseApi import BaseApi, ApiNodeData
from calc.calculation_engine import (
    calculate_fault, calculate_normal, pack_channels
)
from utils.SearchAlgo import searchBoundary

logger = logging.getLogger("ApiImpdTest")

# ── 节点 ID（全部为真实节点，不使用 0xFFFF 作跳转目标）──
RESET_NODE, PRE_FAULT_NODE, FAULT_NODE, IDLE_NODE = 1, 2, 3, 4

# ── diMatchMask 标志位（见 USEEngine.coreLoop）──
DI_REF_PREV = 0x200      # 参考取"进入本节点时"的 DI；配合 OR + XOR 实现边沿检测

# ── DI 角色（与 ApiLineTest 统一）──
PHASES = ['A', 'B', 'C']
ROLE_THREE_TRIP = 1      # 三跳
ROLE_TRIP_ABC = 2        # 分相跳

# ── 时间换算 ──
TICKS_PER_MS = 1000.0    # 硬件时间戳为 us 单位，每毫秒 1000 个 tick


class ApiImpdTest(BaseApi):
    MODULE_KEYS = ["impedance_characteristic_test"]

    def __init__(self):
        super().__init__()
        self._tests = []
        self._payload = {}
        self._debounce = 15

        self._f = 50.0
        self._v_rated = 57.735
        self._load_amp = 0.0
        self._load_ang = 0.0

        self._reset_before_test = False
        self._reset_time_ms = 0
        self._pre_fault_time_ms = 0

        self._model_type = 0
        self._kl_formula = 0
        self._kl_p1 = 0.0
        self._kl_p2 = 0.0
        self._kl_equals_zero = False
        self._precision = 0.01

        self._trip_mask = 0
        self._node_done = asyncio.Event()
        self._search_task = None

        # 运行态（每点复位）
        self._cur_node = 0
        self._fault_entry_ts: Optional[int] = None   # 进入 FAULT 节点的硬件时间戳
        self._di_at_fault = 0                         # 进入 FAULT 时的 DI 基线
        self._point_trip_ms: Optional[float] = None   # 本点相对故障施加的跳闸时间 (ms)

    def _onSetup(self, params: Dict[str, Any]):
        self._tests = params.get("tests", [])
        self._debounce = int(params.get("debounce", 15))
        self._payload = params.get("payload", {})

        # sys
        sys_cfg = self._payload.get("sys", {})
        self._v_rated = float(sys_cfg.get("v", 57.735))
        self._f = float(sys_cfg.get("f", 50.0))
        i_load = sys_cfg.get("iLoad", {})
        self._load_amp = float(i_load.get("amp", 0.0))
        self._load_ang = float(i_load.get("ang", 0.0))

        # ctrl
        ctrl = self._payload.get("ctrl", {})
        imp = ctrl.get("impedance", {})
        self._reset_before_test = bool(imp.get("resetBeforeTest", False))
        self._reset_time_ms = int(imp.get("resetTimeMs", 0))
        self._pre_fault_time_ms = int(imp.get("preFaultTimeMs", 0))

        self._pt_side = int(ctrl.get("pt", 0))
        self._ct_val = int(ctrl.get("ct", 0))

        # model
        model = self._payload.get("model", {})
        self._model_type = int(model.get("type", 0))
        kl = model.get("kl", {})
        self._kl_formula = int(kl.get("formula", 0))
        self._kl_p1 = float(kl.get("p1", 0.0))
        self._kl_p2 = float(kl.get("p2", 0.0))
        self._kl_equals_zero = bool(model.get("klEqualsZero", False))
        self._precision = float(model.get("precision", 0.01))
        if self._precision <= 0.001:
            self._precision = 0.01  # 防呆

        # di
        self._parse_di(self._payload.get("di", {}))

        if not self._tests:
            logger.warning("No tests provided.")
            self.ctrl.stopTest("No tests")
            return

        logger.info(f"Impedance Setup: tests={len(self._tests)}, precision={self._precision}, "
                    f"reset={self._reset_before_test}({self._reset_time_ms}ms), "
                    f"preFault={self._pre_fault_time_ms}ms, klEqualsZero={self._kl_equals_zero}, "
                    f"trip_mask={self._trip_mask:#x}")

        self._search_task = asyncio.create_task(self._run_tests())

    def _parse_di(self, di_cfg: Dict[str, Any]):
        """解析跳闸 Mask。分相跳(2)与三跳(1)都计入'任一跳闸'掩码。"""
        mask = 0
        name_ch = [('A', 0), ('B', 1), ('C', 2), ('a', 4), ('b', 5), ('c', 6)]
        for name, ch in name_ch:
            role = int(di_cfg.get(name, 0))
            if role in (ROLE_TRIP_ABC, ROLE_THREE_TRIP):
                mask |= (1 << ch)
        self._trip_mask = mask
        if self._trip_mask == 0:
            logger.warning("No trip DI configured! Test might timeout.")

    def _onStop(self):
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

    # ══════════════════════════════════════════
    # 硬件回调
    # ══════════════════════════════════════════

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        self._cur_node = nodeId
        if nodeId == FAULT_NODE and tick == 0:
            # 记录进入故障态的时刻 + DI 基线，用于真实动作时间与边沿检测
            self._fault_entry_ts = hw_ts
            self._di_at_fault = self.ctrl.di
        elif nodeId == IDLE_NODE and tick == 0:
            self._node_done.set()

    def onDi(self, di: int, hw_ts: int):
        # 仅在故障态、且尚未记录时，按边沿（相对进入故障时的 DI）判定跳闸
        if self._cur_node != FAULT_NODE or self._point_trip_ms is not None:
            return
        if self._fault_entry_ts is None:
            return
        changed = (di ^ self._di_at_fault) & self._trip_mask
        if changed:
            self._point_trip_ms = round((hw_ts - self._fault_entry_ts) / TICKS_PER_MS, 1)

    # ══════════════════════════════════════════
    # 主运行循环
    # ══════════════════════════════════════════

    async def _run_tests(self):
        try:
            await asyncio.sleep(0.3)  # 预热
            if not self.isActive:
                return

            # 预编译固定的 IDLE 节点（点间挂起，绝不经过 0x0000/0xFFFF）
            if not self.ctrl.upsertNodes({IDLE_NODE: self._build_idle_node()}):
                return
            await asyncio.sleep(0.05)
            if not self.isActive:
                return

            for test_group in self._tests:
                points = test_group.get("points", [])
                for p in points:
                    if not self.isActive:
                        return
                    await self._search_boundary(p)
                    if not self.isActive:
                        return

            if self.isActive:
                self.ctrl.stopTest()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("Error in impedance search loop")
            if self.isActive:
                self.ctrl.stopTest(f"Error: {e}")

    def _build_idle_node(self) -> ApiNodeData:
        """点间挂起态：额定电压 + 负荷电流，无自动出口（等 trigNode）。"""
        V, I = calculate_normal(self._v_rated, self._load_amp, self._load_ang, self._ct_val)
        pack = pack_channels(V, I, vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f)
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(pack)
        return n

    async def _search_boundary(self, p: Dict):
        p_id = p.get("id", 1)
        ft = int(p.get("ft", 0))
        i_f = float(p.get("i", 5.0))
        fault_time = int(p.get("time", 1000))

        x_range = p.get("x", [0.0, 0.0])
        y_range = p.get("y", [0.0, 0.0])

        start_R, end_R = x_range[0], x_range[-1]
        start_X, end_X = y_range[0], y_range[-1]

        last_trip_time = None

        async def checkTrip(pt: Tuple[float, float]) -> bool:
            nonlocal last_trip_time
            cur_R, cur_X = pt
            hit, time = await self._test_point(cur_R, cur_X, ft, i_f, fault_time)
            
            if self.isActive:
                self._send_value_update(p_id, cur_R, cur_X, hit, time)
                
            if hit:
                last_trip_time = time
                
            return hit

        boundary_pt = await searchBoundary(
            (start_R, start_X), (end_R, end_X), 
            self._precision, checkTrip, 0
        )
        
        if boundary_pt is None:
            logger.warning(f"Point {p_id} segment [{start_R},{start_X}] -> [{end_R},{end_X}] "
                           f"has no boundary (both states matched)")
            self._send_report_row(p_id, None, None, None)
        else:
            boundary_R, boundary_X = boundary_pt
            self._send_report_row(p_id, boundary_R, boundary_X, last_trip_time)

    async def _test_point(self, R: float, X: float, ft: int, i_f: float,
                          fault_time: int) -> Tuple[bool, Optional[float]]:
        """对单个阻抗点施加故障，返回 (是否跳闸, 动作时间ms或None)。"""
        nodes: Dict[int, ApiNodeData] = {}

        Z_mag = math.hypot(R, X)
        Z_ang = math.degrees(math.atan2(X, R))

        # 正常态数据
        V_norm, I_norm = calculate_normal(self._v_rated, self._load_amp, self._load_ang, self._ct_val)
        pack_norm = pack_channels(V_norm, I_norm, vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f)

        # 故障态数据：klEqualsZero（1、2 象限即 X>=0）需真正清零 KL。
        # 注意：仅把 p1/p2 置 0 不够——formula=2(Z0/Z1) 下 KL=(0-1)/3=-1/3 并非 0；
        #       必须同时把公式强制成 formula=0（直接给定 KL=0∠0）。
        if self._kl_equals_zero and X >= 0.0:
            use_formula, kl_p1_val, kl_p2_val = 0, 0.0, 0.0
        else:
            use_formula, kl_p1_val, kl_p2_val = self._kl_formula, self._kl_p1, self._kl_p2

        V_fault, I_fault = calculate_fault(
            ft, self._v_rated, self._load_amp, self._load_ang,
            dir_val=0, i_f_amp=i_f, z_mag=Z_mag, z_ang=Z_ang,
            formula=use_formula, p1=kl_p1_val, p2=kl_p2_val,
            ct_val=self._ct_val
        )
        pack_fault = pack_channels(V_fault, I_fault, vMode=0, iMode=0, ux_cfg={"en": False}, f=self._f)

        entry_node = FAULT_NODE

        # FAULT 节点：超时/跳闸均回到 IDLE（真实节点），并用 DI_REF_PREV 做边沿检测
        n_fault = ApiNodeData(mode=1)
        n_fault.base = self.physDictToReg(pack_fault)
        n_fault.timeoutMs = fault_time
        n_fault.timeoutId = IDLE_NODE
        n_fault.diMatchMask = self._trip_mask | DI_REF_PREV
        n_fault.diMatchId = IDLE_NODE
        nodes[FAULT_NODE] = n_fault

        if self._pre_fault_time_ms > 0:
            n_pre = ApiNodeData(mode=1)
            n_pre.base = self.physDictToReg(pack_norm)
            n_pre.timeoutMs = self._pre_fault_time_ms
            n_pre.timeoutId = FAULT_NODE
            nodes[PRE_FAULT_NODE] = n_pre
            entry_node = PRE_FAULT_NODE

        if self._reset_before_test and self._reset_time_ms > 0:
            n_res = ApiNodeData(mode=1)
            n_res.base = self.physDictToReg(pack_norm)
            n_res.timeoutMs = self._reset_time_ms
            n_res.timeoutId = entry_node
            nodes[RESET_NODE] = n_res
            entry_node = RESET_NODE

        if not self.ctrl.upsertNodes(nodes):
            return False, None

        # 复位本点记录
        self._cur_node = 0
        self._fault_entry_ts = None
        self._point_trip_ms = None
        self._node_done.clear()

        self.ctrl.trigNode(entry_node)

        try:
            timeout_s = (self._reset_time_ms + self._pre_fault_time_ms + fault_time) / 1000.0 + 2.0
            await asyncio.wait_for(self._node_done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error("Node timeout waiting for IDLE in USEEngine!")

        is_hit = self._point_trip_ms is not None
        return is_hit, self._point_trip_ms

    def _send_value_update(self, p_id: int, R: float, X: float, hit: bool, time: Optional[float]):
        Z_mag = math.hypot(R, X)
        Z_ang = math.degrees(math.atan2(X, R))
        msg = {
            "type": "value_update",
            "module": "impedance_characteristic_test",
            "id": p_id,
            "x": round(R, 3),
            "y": round(X, 3),
            "boundaryZValue": round(Z_mag, 3),
            "boundaryZAngle": round(Z_ang, 3),
            "hit": hit,
            "actionTimeMs": round(time, 1) if time is not None else None
        }
        self.ctrl._send(msg)

    def _send_report_row(self, p_id: int, R: Optional[float], X: Optional[float], time: Optional[float]):
        if R is not None and X is not None:
            Z_mag = math.hypot(R, X)
            Z_ang = math.degrees(math.atan2(X, R))
            row = {
                "id": p_id,
                "boundaryZValue": round(Z_mag, 3),
                "boundaryZAngle": round(Z_ang, 3),
                "actionTimeMs": round(time, 1) if time is not None else None
            }
        else:
            row = {
                "id": p_id,
                "boundaryZValue": None,
                "boundaryZAngle": None,
                "actionTimeMs": None
            }

        self.ctrl.sendReport({
            "rows": [row]
        })
