"""
ApiLineTest.py — 线路保护定值实验 API (lp_test)

实现 line-protection-impl-logic.md 的固定 9 节点状态机：
  1 NORMAL  2 FAULT1  3 FAULT1_HOLD  4 CBOPEN  5 CBOPEN_HOLD
  6 RECLOSE 7 FAULT2  8 FAULT2_HOLD  9 PARK

三条链由 nature / after 决定（§6）：
  基础      : 1→2→3→4→5→6→9
  after==0  : 1→2→7→8→4→5→6→9   (第一次故障 t1 后演变为转换故障再跳闸)
  after==1  : 1→2→3→4→5→6→7→9   (重合后施加转换故障)

节点 6 RECLOSE 出口统一（§5）：无论瞬时/永久，均 timeout(point.time)→9 且 trip→9，
只有输出态随 nature 变（瞬时=NORMAL，永久=FAULT(p1)）。

时间记录（§7）：三个基准时刻
  跳A/B/C    → 相对节点 2 进入（跨节点保留，重合之前的跳闸都归此）
  重合       → 相对节点 4 进入
  后加速跳闸 → 相对节点 6 进入（永久故障 / after==1 转换故障）

12 通道交流量由 calc.calculation_engine 统一计算（NORMAL/FAULT/OPEN/ZERO），
经 pack_channels 打包为 12 通道字典后直接传给 physDictToReg。
"""

import math
import logging
import asyncio
from typing import Dict, Any, List, Optional, Set

from api.BaseApi import BaseApi, ApiNodeData
from calc.calculation_engine import (
    calculate_fault, calculate_normal, calculate_open, pack_channels
)

logger = logging.getLogger("ApiLineTest")

# ── 节点 ID ──
NORMAL, FAULT1, FAULT1_HOLD, CBOPEN, CBOPEN_HOLD, RECLOSE, FAULT2, FAULT2_HOLD, PARK = range(1, 10)

# ── diMatchMask 标志位（见 USEEngine.coreLoop）──
DI_REF_PREV = 0x200      # 参考取“进入本节点时”的 DI；配合 OR + XOR 实现变化检测

# ── DI 角色 ──
ROLE_OTHER, ROLE_THREE_TRIP, ROLE_TRIP_ABC, ROLE_RECLOSER = 0, 1, 2, 3

# ── 触发方式 ──
TRIG_KEY, TRIG_TIME, TRIG_DI = 0, 1, 2

# ── 通道映射（API 索引）：电压 0/1/2 = Ua/Ub/Uc，电流 6/7/8 = Ia/Ib/Ic ──
V_CH = {'A': 0, 'B': 1, 'C': 2}
I_CH = {'A': 6, 'B': 7, 'C': 8}
BASE_ANG = {'A': 0.0, 'B': -120.0, 'C': 120.0}
PHASES = ('A', 'B', 'C')

# ── 故障类型 → 故障相集合 ──
FT_PHASES: Dict[int, Set[str]] = {
    0: {'A'}, 1: {'B'}, 2: {'C'},
    3: {'A', 'B'}, 4: {'B', 'C'}, 5: {'C', 'A'},
    6: {'A', 'B'}, 7: {'B', 'C'}, 8: {'C', 'A'},
    9: {'A', 'B', 'C'},
}
SINGLE_PHASE_GROUND = {0, 1, 2}

# ── 常量 ──
SQRT2 = math.sqrt(2.0)        # RMS → 峰值（FPGA 要峰值，由节点构造层换算）
TICKS_PER_MS = 1000.0         # 硬件时间戳为 us 单位，每毫秒 1000 个 us tick
MAX_RECLOSE_WAIT = 10000      # ms，CBOPEN 等不到重合的上限（impl-logic §11）
POINT_TIMEOUT = 60.0          # s，单点二级兜底


class ApiLineTest(BaseApi):
    MODULE_KEY = "lp_test"

    def __init__(self):
        super().__init__()
        self._tests: List[Dict] = []
        self._payload: Dict = {}

        # sys 额定量
        self._v_rated = 57.735
        self._f = 50.0
        self._load_amp = 0.0
        self._load_ang = 0.0

        # DI 归一化（§3）
        self._trip_phase_bits = {'A': 0, 'B': 0, 'C': 0}
        self._all_trip_bits = 0
        self._reclose_mask = 0

        # trigger
        self._trig_type = TRIG_TIME
        self._trig_time = 20000.0
        self._trig_mask = 0

        # cb（断路器模拟）
        self._cb_sim = False
        self._cb_trip = 0
        self._cb_close = 0

        # DO（开出）
        self._do_enabled = False
        self._do_init = 0
        self._do_mode = 1
        self._do_flipall = {"delay": 0, "hold": 0}
        self._do_custom: Dict[str, Any] = {}

        # 计算引擎参数
        self._ct_val = 0
        self._pt_val = 0
        self._kl_formula = 0
        self._kl_p1 = 0.0
        self._kl_p2 = 0.0
        self._v_mode = 0
        self._i_mode = 0
        self._ux_cfg = {"en": False, "type": 0, "syncV": 0.0, "syncAng": 0.0}

        # 运行态（每点复位）
        self._cur_node = 0
        self._reclose_is_fault = False
        self._seen_reclose = False
        self._di_prev = 0
        self._t2 = 0          # 节点 2 进入时刻（跳A/B/C 基准）
        self._t4 = 0          # 节点 4 进入时刻（重合 基准）
        self._t6 = 0          # 节点 6 进入时刻（后加速 基准）
        self._rec: Dict[str, int] = {}
        self._node_done: Optional[asyncio.Event] = None

    # ══════════════════════════════════════════
    # Setup
    # ══════════════════════════════════════════

    def _onSetup(self, params: Dict[str, Any]):
        self._tests = params.get("tests", [])
        self._payload = params.get("payload", {})

        sys_cfg = self._payload.get("sys", {})
        self._v_rated = float(sys_cfg.get("v", 57.735))
        self._f = float(sys_cfg.get("f", 50.0))
        iload = sys_cfg.get("iLoad", {})
        self._load_amp = float(iload.get("amp", 0.0))
        self._load_ang = float(iload.get("ang", 0.0))

        trig = self._payload.get("trigger", {})
        self._trig_type = int(trig.get("type", TRIG_TIME))
        self._trig_time = float(trig.get("time", 20000.0))
        self._trig_mask = int(trig.get("mask", 0))

        ctrl = self._payload.get("ctrl", {})
        cb = ctrl.get("cb", {})
        self._cb_sim = bool(cb.get("sim", False))
        self._cb_trip = int(cb.get("trip", 0))
        self._cb_close = int(cb.get("close", 0))

        self._ct_val = int(ctrl.get("ct", 0))
        self._pt_val = int(ctrl.get("pt", 0))

        model = self._payload.get("model", {})
        kl = model.get("kl", {})
        self._kl_formula = int(kl.get("formula", 0))
        self._kl_p1 = float(kl.get("p1", 0.0))
        self._kl_p2 = float(kl.get("p2", 0.0))

        output = self._payload.get("output", {})
        self._v_mode = int(output.get("vMode", 0))
        self._i_mode = int(output.get("iMode", 0))
        ux = output.get("ux", {})
        self._ux_cfg = {
            "en": bool(ux.get("en", False)),
            "type": int(ux.get("type", 0)),
            "syncV": float(ux.get("syncV", 0.0)),
            "syncAng": float(ux.get("syncAng", 0.0)),
        }

        self._parse_di(self._payload.get("di", {}))
        self._parse_do(self._payload.get("do"))

        if not self._tests:
            logger.warning("No tests, stopping.")
            self.ctrl.stopTest("No tests provided")
            return

        logger.info(f"LP setup: tests={len(self._tests)}, trig={self._trig_type}, "
                    f"cb_sim={self._cb_sim}(trip={self._cb_trip},close={self._cb_close}), "
                    f"trip_bits={self._trip_phase_bits}, reclose_mask={self._reclose_mask:#x}")

        self._node_done = asyncio.Event()
        asyncio.create_task(self._run())

    def _parse_di(self, di_cfg: Dict[str, Any]):
        """把 payload.di 归一化为逐相跳闸位 + 重合位（§3.1），消除分组特判。"""
        name_ch_phase = [('A', 0, 'A'), ('B', 1, 'B'), ('C', 2, 'C'),
                         ('a', 4, 'A'), ('b', 5, 'B'), ('c', 6, 'C')]
        bits = {'A': 0, 'B': 0, 'C': 0}
        for name, ch, phase in name_ch_phase:
            role = int(di_cfg.get(name, 0))
            if role == ROLE_TRIP_ABC:
                bits[phase] |= (1 << ch)
            elif role == ROLE_THREE_TRIP:
                for ph in PHASES:           # 三跳：任一相故障都动作
                    bits[ph] |= (1 << ch)
        self._trip_phase_bits = bits
        self._all_trip_bits = bits['A'] | bits['B'] | bits['C']

        reclose = 0
        for name, ch in (('R', 3), ('r', 7)):
            if int(di_cfg.get(name, 0)) == ROLE_RECLOSER:
                reclose |= (1 << ch)
        self._reclose_mask = reclose

    def _trip_mask(self, ft: int) -> int:
        m = 0
        for ph in FT_PHASES.get(ft, set()):
            m |= self._trip_phase_bits[ph]
        return m

    # ══════════════════════════════════════════
    # DO（开出）解析与编排
    # ══════════════════════════════════════════

    def _parse_do(self, do_cfg: Optional[Dict[str, Any]]):
        """解析 payload.do。缺省（None）→ 不输出 DO（不触碰开出）。"""
        if not do_cfg:
            self._do_enabled = False
            return
        self._do_enabled = True
        self._do_init = int(do_cfg.get("init", 0)) & 0xFF
        self._do_mode = int(do_cfg.get("mode", 1))
        self._do_flipall = do_cfg.get("flipAll", {}) or {}
        self._do_custom = do_cfg.get("custom", {}) or {}

    @staticmethod
    def _do_pack(events: List) -> List[int]:
        """事件 [(delayMs, doByte), ...] → doActions [(delayMs<<8)|doByte, ...]（按时刻排序）。"""
        return [((int(t) & 0xFFFF) << 8) | (int(b) & 0xFF) for t, b in sorted(events, key=lambda e: e[0])]

    def _apply_do(self, nodes: Dict[int, ApiNodeData], has_tr: bool, non_full_phase: bool):
        """把 payload.do 编排进相关节点的 doActions（依赖 USEEngine DO 跨节点保持）。

        约定：带 doActions 的节点会改变 DO；不带的节点保持上一段 DO。
          - init：故障前开出初值，挂在节点 1（每点起始即复位为 init）。
          - mode=0 SYNC_WITH_RECLOSER：断路器辅助接点模拟——断路器断开（进入 CBOPEN）
              翻转全部 DO，重合（进入 RECLOSE）翻回。这是重合闸装置获取断路器位置回馈的关键。
          - mode=1 DELAY_FLIP_ALL：故障态内 delay 翻转全部、hold 后翻回（编入 FAULT1）。
          - mode=2 CUSTOM：逐通道 HOLD/FLIP（编入 FAULT1）。
        """
        if not self._do_enabled:
            return
        init = self._do_init
        flip_all = init ^ 0xFF

        # 节点 1：每点起始复位为 init
        if NORMAL in nodes:
            nodes[NORMAL].doActions = self._do_pack([(0, init)])

        if self._do_mode == 0:
            # 断路器辅助接点：断开→翻转，重合→翻回
            if CBOPEN in nodes:
                nodes[CBOPEN].doActions = self._do_pack([(0, flip_all)])
            if RECLOSE in nodes:
                nodes[RECLOSE].doActions = self._do_pack([(0, init)])

        elif self._do_mode == 1:
            # 故障态内延时统一翻转再翻回
            delay = int(self._do_flipall.get("delay", 0))
            hold = int(self._do_flipall.get("hold", 0))
            if FAULT1 in nodes:
                nodes[FAULT1].doActions = self._do_pack([
                    (delay, flip_all),
                    (delay + hold, init),
                ])

        elif self._do_mode == 2:
            # 逐通道自定义 FLIP/HOLD：累积成时间轴上的绝对 DO 字节
            events = []  # (time_ms, ch, target_bit)
            for ch_str, cfg in self._do_custom.items():
                try:
                    ch = int(ch_str)
                except (TypeError, ValueError):
                    continue
                if not (0 <= ch <= 7) or int(cfg.get("action", 0)) != 1:
                    continue
                d = int(cfg.get("delay", 0))
                h = int(cfg.get("hold", 0))
                init_bit = (init >> ch) & 1
                events.append((d, ch, 1 - init_bit))       # 翻转
                events.append((d + h, ch, init_bit))        # 翻回
            if events and FAULT1 in nodes:
                cur = init
                packed = []
                for t, ch, bit in sorted(events, key=lambda e: e[0]):
                    cur = (cur | (1 << ch)) if bit else (cur & ~(1 << ch))
                    cur &= 0xFF
                    packed.append((t, cur))
                nodes[FAULT1].doActions = self._do_pack(packed)

    # ══════════════════════════════════════════
    # 通道状态构建 — 使用 calc.calculation_engine
    # ══════════════════════════════════════════

    def _pack(self, V_abc, I_abc, is_open=False) -> Dict:
        """将复数元组打包为 12 通道字典（直接匹配 physDictToReg）。"""
        ux = dict(self._ux_cfg, is_open=is_open)
        return pack_channels(V_abc, I_abc,
                             vMode=self._v_mode, iMode=self._i_mode,
                             ux_cfg=ux, f=self._f)

    def _state_normal(self) -> Dict:
        """正常态：三相额定电压 + 三相负荷电流。"""
        V, I = calculate_normal(self._v_rated, self._load_amp, self._load_ang, self._ct_val)
        return self._pack(V, I, is_open=False)

    def _state_zero(self) -> Dict:
        """全电流归零态：电压保留额定，电流全 0。"""
        V, I = calculate_normal(self._v_rated, 0.0, 0.0, self._ct_val)
        return self._pack(V, I, is_open=False)

    def _state_open(self, p: Dict) -> Dict:
        """断路器断开特殊正常态（含 PT 安装侧影响）。"""
        ft = int(p.get("ft", 0))
        V, I = calculate_open(ft, self._v_rated, self._load_amp, self._load_ang,
                              self._ct_val, self._pt_val)
        return self._pack(V, I, is_open=True)

    def _state_fault(self, p: Dict, direction: int, open_phase: Optional[int] = None) -> Dict:
        """故障态 12 通道相量 — 使用统一计算引擎。

        open_phase: 非全相（单相已断开）场景下，维持已跳开相为开极
                    （该相电流=0；线路侧 PT 时该相电压塌陷）。用于 type=5
                    死区内转换故障：第一次单相跳开后，转换故障期间该极仍断开。
        """
        ft = int(p.get("ft", 0))
        i_f = float(p.get("i", 0.0) or 0.0)

        # 定制校验换算：如果前端传的是逻辑电流，需要换算回物理短路电流 I_f
        # 零序过流 (type=1)：传的是 3I0。单相接地时 3I0 = If，无需换算。
        # 负序过流 (type=3)：传的是 3I2。相间短路时 3I2 = √3 * If，需要除以 √3。单相接地时 3I2 = If，无需换算。
        if self._test_type == 3 and ft in (3, 4, 5):
            i_f = i_f / math.sqrt(3.0)

        z = float(p.get("z", 0.0) or 0.0)
        ang = float(p.get("ang", 0.0) or 0.0)

        m_val = float(p.get("m", 1.0))
        delta_formula = int(p.get("formula", 0))

        V, I = calculate_fault(
            ft, self._v_rated, self._load_amp, self._load_ang,
            direction, i_f, z, ang,
            self._kl_formula, self._kl_p1, self._kl_p2, self._ct_val,
            test_type=self._test_type, delta_m=m_val, delta_formula=delta_formula
        )

        if open_phase is not None and 0 <= open_phase <= 2:
            # 维持非全相：被跳开相电流置 0；线路侧 PT 时该相电压塌陷
            V = list(V)
            I = list(I)
            I[open_phase] = complex(0, 0)
            if self._pt_val == 1:
                V[open_phase] = complex(0, 0)
            V, I = tuple(V), tuple(I)

        return self._pack(V, I, is_open=False)

    # ══════════════════════════════════════════
    # 节点构建
    # ══════════════════════════════════════════

    def _node(self, base_dict: Dict, *, timeout: Optional[int] = None, timeout_id: Optional[int] = None,
              di_mask: Optional[int] = None, di_id: Optional[int] = None) -> ApiNodeData:
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(base_dict)
        if timeout is not None:
            n.timeoutMs = int(timeout)
            n.timeoutId = timeout_id
        if di_mask is not None:
            n.diMatchMask = di_mask
            n.diMatchId = di_id
        return n

    def _build_nodes(self, test: Dict, p: Dict) -> Dict[int, ApiNodeData]:
        """按 type/dir/nature + point 参数构建本测点的节点集（§5）。"""
        self._test_type = int(test.get("type", 0))
        direction = int(test.get("dir", 0))
        nature = int(test.get("nature", 0))

        ft1 = int(p.get("ft", 0))
        time1 = int(p.get("time", 1000))

        has_tr = "ft2" in p
        after = int(p.get("after", 0)) if has_tr else None
        delay = int(p.get("delay", 0)) if has_tr else None
        ft2 = int(p.get("ft2", 0)) if has_tr else None
        time2 = int(p.get("time2", 1000)) if has_tr else None
        p2 = {"ft": ft2, "z": p.get("z2"), "ang": p.get("ang2"), "i": p.get("i2")} if has_tr else None

        cb_trip = self._cb_sim and self._cb_trip > 0
        cb_close = self._cb_sim and self._cb_close > 0
        hold1 = FAULT1_HOLD if cb_trip else CBOPEN     # 第一次故障跳闸落点
        recl = CBOPEN_HOLD if cb_close else RECLOSE    # 重合落点

        # 非全相（不灵敏零序）链：第一次单相跳开后，死区内施加转换故障，不重合。
        non_full_phase = has_tr and after == 0
        open_ph = ft1 if (non_full_phase and ft1 in (0, 1, 2)) else None
        self._reclose_is_fault = (nature == 1)

        nodes: Dict[int, ApiNodeData] = {}

        # ── 1 NORMAL：出口取决于触发方式 ──
        n1 = self._node(self._state_normal())
        if self._trig_type == TRIG_TIME:
            n1.timeoutMs, n1.timeoutId = int(self._trig_time), FAULT1
        elif self._trig_type == TRIG_DI:
            n1.diMatchMask, n1.diMatchId = self._trig_mask | DI_REF_PREV, FAULT1
        # TRIG_KEY: 无自动出口，等前端 {"cmd":"next_node"} → trigNode(FAULT1)
        nodes[NORMAL] = n1

        # ── 2 FAULT1：第一次故障总是检测跳闸 ──
        n2 = self._node(self._state_fault(p, direction))
        n2.diMatchMask, n2.diMatchId = self._trip_mask(ft1) | DI_REF_PREV, hold1
        n2.timeoutMs, n2.timeoutId = time1, PARK
        nodes[FAULT1] = n2

        # ── 3 FAULT1_HOLD（cb_trip 时：断路器跳闸延时保持）──
        if cb_trip:
            nodes[FAULT1_HOLD] = self._node(self._state_fault(p, direction),
                                            timeout=self._cb_trip, timeout_id=CBOPEN)

        # ── 4 CBOPEN（断路器断开/单相断开特殊正常态）──
        n4 = self._node(self._state_open(p))
        if non_full_phase:
            # 非全相死区：经 delay 在开极状态下施加转换故障，不等重合
            n4.timeoutMs, n4.timeoutId = delay, FAULT2
        else:
            n4.diMatchMask, n4.diMatchId = self._reclose_mask | DI_REF_PREV, recl
            n4.timeoutMs, n4.timeoutId = MAX_RECLOSE_WAIT, PARK
        nodes[CBOPEN] = n4

        # ── 5 CBOPEN_HOLD（重合链 + cb_close）──
        if cb_close and not non_full_phase:
            nodes[CBOPEN_HOLD] = self._node(self._state_open(p),
                                            timeout=self._cb_close, timeout_id=RECLOSE)

        # ── 6 RECLOSE（重合链：输出按 nature；after==1 改写为转换）──
        if not non_full_phase:
            n6_state = self._state_fault(p, direction) if self._reclose_is_fault else self._state_normal()
            n6 = self._node(n6_state)
            if has_tr and after == 1:
                n6.timeoutMs, n6.timeoutId = delay, FAULT2          # 重合后转换：t1 → 7
            else:
                n6.timeoutMs, n6.timeoutId = time1, PARK            # 统一：point.time → 9
                n6.diMatchMask, n6.diMatchId = self._trip_mask(ft1) | DI_REF_PREV, PARK  # 跳闸 → 9
            nodes[RECLOSE] = n6

        # ── 7 FAULT2（故障转换：after==0 死区内转换 / after==1 重合后转换）──
        # 出口统一：跳闸 → 9，超时 → 9（转换故障跳闸即三跳终结，不再重合）。
        # after==0 时维持非全相开极。
        if has_tr:
            n7 = self._node(self._state_fault(p2, direction, open_phase=open_ph))
            n7.diMatchMask = self._trip_mask(ft2) | DI_REF_PREV
            n7.diMatchId = PARK
            n7.timeoutMs, n7.timeoutId = time2, PARK
            nodes[FAULT2] = n7

        self._apply_do(nodes, has_tr, non_full_phase)
        return nodes

    # ══════════════════════════════════════════
    # 主运行循环
    # ══════════════════════════════════════════

    async def _run(self):
        await asyncio.sleep(0.5)  # 预热
        if not self.isActive:
            return

        # 固定 PARK 节点（点间挂起，不经过 0x0000/0xFFFF）
        if not self.ctrl.upsertNodes({PARK: self._node(self._state_zero())}):
            return
        await asyncio.sleep(0.05)
        if not self.isActive:
            return

        for test in self._tests:
            for p in test.get("points", []):
                if not self.isActive:
                    return
                await self._run_point(test, p)
                if not self.isActive:        # 硬件错误 → 引擎已停 → 整体退出
                    return
                self._send_point_result(p)   # 每点测完即发一次（含未跳闸的空时间）

        if self.isActive:
            self.ctrl.stopTest()

    async def _run_point(self, test: Dict, p: Dict) -> bool:
        nodes = self._build_nodes(test, p)
        if not self.ctrl.upsertNodes(nodes):   # 编译失败 → 引擎转 0xFFFF
            return False
        await asyncio.sleep(0.05)
        if not self.isActive:
            return False

        self._reset_records()
        self._node_done.clear()
        self.ctrl.trigNode(NORMAL)             # 从 0x0000/PARK 触发进入本点

        try:
            await asyncio.wait_for(self._node_done.wait(), timeout=POINT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Point #{p.get('id')} timeout ({POINT_TIMEOUT}s)")
            return False
        return True

    def _reset_records(self):
        self._cur_node = 0
        self._seen_reclose = False
        self._di_prev = self.ctrl.di           # 以本点起始 DI 为归因基线
        self._t2 = 0
        self._t4 = 0
        self._t6 = 0
        self._rec = {}

    def _send_point_result(self, p: Dict):
        """每点测完上报：仅 id + 五个动作时间（毫秒），未发生留空 null（§7.1）。"""
        def ms(key: str) -> Optional[float]:
            v = self._rec.get(key)
            return round(v / TICKS_PER_MS, 1) if v is not None else None
        self.ctrl.sendReport({
            "id": p.get("id"),
            "tripA": ms("tripA"),
            "tripB": ms("tripB"),
            "tripC": ms("tripC"),
            "reclose": ms("reclose"),
            "postAccel": ms("postAccel"),
        })

    # ══════════════════════════════════════════
    # 硬件回调
    # ══════════════════════════════════════════

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        """节点进入回调：刷新三个基准时刻（跨节点保留），PARK 表示本点结束。"""
        self._cur_node = nodeId
        if nodeId == FAULT1:
            self._t2 = hw_ts               # 跳A/B/C 基准（仅节点 2，不在节点 7 刷新）
        elif nodeId == CBOPEN:
            self._t4 = hw_ts               # 重合 基准
        elif nodeId == RECLOSE:
            self._t6 = hw_ts               # 后加速 基准
            self._seen_reclose = True      # 已过重合：此后跳闸归“后加速”
        if nodeId == PARK and tick == 0 and self._node_done:
            self._node_done.set()

    def onDi(self, diMask: int, hw_ts: int):
        """开入变化回调：按当前节点段归因 跳A/B/C / 重合 / 后加速（均只记第一次）。"""
        if not self.isActive:
            return
        changed = diMask ^ self._di_prev
        self._di_prev = diMask
        node = self._cur_node

        # 重合（节点 4 期间，reclose_mask 跳变）
        if self._reclose_mask and (changed & self._reclose_mask) \
                and "reclose" not in self._rec and self._t4:
            self._rec["reclose"] = hw_ts - self._t4

        # 后加速跳闸（重合之后的终态故障：永久的节点 6，或 after==1 的节点 7）
        post = (node == RECLOSE and self._reclose_is_fault) or (node == FAULT2 and self._seen_reclose)
        if post:
            if (changed & self._all_trip_bits) and "postAccel" not in self._rec and self._t6:
                self._rec["postAccel"] = hw_ts - self._t6
            return

        # 跳A/B/C（节点 2 进入 ~ 重合之前：节点 2/3，及 after==0 链的 7/8），均以 t2 为基准
        if not self._seen_reclose and node in (FAULT1, FAULT1_HOLD, FAULT2, FAULT2_HOLD) and self._t2:
            for ph in PHASES:
                key = f"trip{ph}"
                if (changed & self._trip_phase_bits[ph]) and key not in self._rec:
                    self._rec[key] = hw_ts - self._t2

    def _onStop(self):
        if self._node_done:
            self._node_done.set()
        logger.info("LineProtectionTest stopped.")

    def onWebCommand(self, msg: Dict[str, Any]):
        cmd = msg.get("cmd")
        if cmd == "stop":
            self.ctrl.stopTest()
        elif cmd == "next_node" and self._cur_node == NORMAL:
            self.ctrl.trigNode(FAULT1)     # KEY 触发：前端按键推进 1 → 2
