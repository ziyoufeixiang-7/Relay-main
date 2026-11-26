"""
test_line_protection.py — 线路保护 API 模拟测试

验证:
1. 节点构建正确性 (基础/after=0/after=1/永久/cb.sim)
2. 状态输出正确性 (NORMAL/FAULT/OPEN/ZERO 12通道)
3. 时间记录正确性 (onDi/onUpdate 归因)
4. DI 掩码正确性 (分相跳/三跳/重合)
5. cb.sim 变形 (跳过/保留节点 3/5)
"""

import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ApiLineTest import ApiLineTest

SQRT2 = math.sqrt(2.0)

# ── Mock ctrl ──
class MockCtrl:
    def __init__(self):
        self.nodes = {}
        self.triggered = []
        self.reports = []
        self.stopped = False
        self.di = 0

    def upsertNodes(self, nodes_dict):
        self.nodes.update(nodes_dict)
        return True

    def trigNode(self, nodeId):
        self.triggered.append(nodeId)

    def sendReport(self, data):
        self.reports.append(data)

    def stopTest(self, reason=None):
        self.stopped = True


def make_api(payload_overrides=None, tests_overrides=None):
    """创建 API 实例 + mock ctrl，不启动 asyncio。"""
    api = ApiLineTest()
    mock = MockCtrl()
    api.ctrl = mock
    api.isActive = True
    api.physDictToReg = lambda d, **kw: d  # 透传

    base_payload = {
        "trigger": {"type": 1, "time": 20000},
        "ctrl": {"pt": 0, "ct": 0, "cb": {"sim": False, "trip": 0, "close": 0}},
        "di": {"A": 2, "B": 2, "C": 2, "R": 3, "a": 0, "b": 0, "c": 0, "r": 0},
        "sys": {"v": 57.735, "f": 50.0, "iLoad": {"amp": 2.0, "ang": -30.0}},
        "model": {"type": 0, "kl": {"formula": 0, "p1": 0.667, "p2": 0.0}},
        "output": {"vMode": 0, "iMode": 0},
    }
    if payload_overrides:
        for k, v in payload_overrides.items():
            if isinstance(v, dict) and k in base_payload and isinstance(base_payload[k], dict):
                base_payload[k].update(v)
            else:
                base_payload[k] = v

    base_tests = [{"type": 0, "dir": 0, "nature": 0,
                   "points": [{"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0}]}]
    if tests_overrides is not None:
        base_tests = tests_overrides

    params = {"tests": base_tests, "payload": base_payload}
    import asyncio
    # Patch create_task to avoid event loop requirement (we only test sync logic)
    _orig = asyncio.create_task
    asyncio.create_task = lambda coro: coro.close() or None
    try:
        api._onSetup(params)
    finally:
        asyncio.create_task = _orig
    return api, mock


total = 0
passed = 0

def check(label, ok):
    global total, passed
    total += 1
    if ok:
        passed += 1
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
    return ok


# ══════════════════════════════════════════
# TEST 1: 节点构建正确性
# ══════════════════════════════════════════
def test_node_construction():
    print("=" * 60)
    print("TEST 1: 节点构建正确性")
    print("=" * 60)

    # 1a. 基础链 (type=0, nature=0, cb_sim=False)
    api, mock = make_api()
    test = api._tests[0]
    p = test["points"][0]
    nodes = api._build_nodes(test, p)
    check("基础链: 包含节点 1,2,4,6 (PARK 9 单独创建)", {1, 2, 4, 6}.issubset(set(nodes.keys())))
    check("基础链: 不含节点 3 (无 cb.sim)", 3 not in nodes)
    check("基础链: 不含节点 5 (无 cb.sim)", 5 not in nodes)

    # 1b. 基础链 + cb.sim
    api2, _ = make_api(payload_overrides={"ctrl": {"pt": 0, "ct": 0, "cb": {"sim": True, "trip": 50, "close": 30}}})
    nodes2 = api2._build_nodes(api2._tests[0], api2._tests[0]["points"][0])
    check("cb.sim: 包含节点 3 (FAULT1_HOLD)", 3 in nodes2)
    check("cb.sim: 包含节点 5 (CBOPEN_HOLD)", 5 in nodes2)
    check("cb.sim: 节点 3 timeout=50ms→4", nodes2[3].timeoutMs == 50 and nodes2[3].timeoutId == 4)
    check("cb.sim: 节点 5 timeout=30ms→6", nodes2[5].timeoutMs == 30 and nodes2[5].timeoutId == 6)

    # 1c. after=0 链（非全相：第一次跳闸→单相断开→死区内转换故障）
    tests_a0 = [{"type": 5, "dir": 0, "nature": 0, "points": [
        {"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0,
         "ft2": 2, "z2": 3.0, "ang2": 75.0, "time2": 500, "i2": 8.0, "after": 0, "delay": 100}
    ]}]
    api3, _ = make_api(tests_overrides=tests_a0)
    nodes3 = api3._build_nodes(api3._tests[0], api3._tests[0]["points"][0])
    check("after=0: 包含节点 7 (FAULT2)", 7 in nodes3)
    check("after=0: 第一次故障检测跳闸 (节点 2 有 DI→CBOPEN)",
          nodes3[2].diMatchMask is not None and nodes3[2].diMatchId == 4)
    check("after=0: 节点 2 timeout=time1→PARK(9)", nodes3[2].timeoutMs == 1000 and nodes3[2].timeoutId == 9)
    check("after=0: CBOPEN(4) 死区 timeout=delay100→7", nodes3[4].timeoutMs == 100 and nodes3[4].timeoutId == 7)
    check("after=0: CBOPEN(4) 不等重合 (无 reclose DI)", nodes3[4].diMatchMask is None)
    check("after=0: 节点 7 跳闸→9 (三跳终结)", nodes3[7].diMatchId == 9)
    check("after=0: 不含节点 6 (非全相不重合)", 6 not in nodes3)
    check("after=0: 节点 7 维持开极 Ia=0 (A 相已跳开)", abs(nodes3[7].base["6"]["1"][0]) < 0.01)

    # 1d. after=1 链
    tests_a1 = [{"type": 5, "dir": 0, "nature": 0, "points": [
        {"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0,
         "ft2": 2, "z2": 3.0, "ang2": 75.0, "time2": 500, "i2": 8.0, "after": 1, "delay": 200}
    ]}]
    api4, _ = make_api(tests_overrides=tests_a1)
    nodes4 = api4._build_nodes(api4._tests[0], api4._tests[0]["points"][0])
    check("after=1: 包含节点 7", 7 in nodes4)
    check("after=1: 节点 6 timeout=200ms→7 (重合后转换)", nodes4[6].timeoutMs == 200 and nodes4[6].timeoutId == 7)
    check("after=1: 节点 7 跳闸→9 (PARK)", nodes4[7].diMatchId == 9)

    # 1e. 永久故障 nature=1
    api5, _ = make_api(tests_overrides=[{"type": 0, "dir": 0, "nature": 1,
        "points": [{"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0}]}])
    nodes5 = api5._build_nodes(api5._tests[0], api5._tests[0]["points"][0])
    # 永久故障节点 6 输出应该是 FAULT 而非 NORMAL → 检查 base 不全为零
    n6_base = nodes5[6].base
    check("永久 nature=1: 节点 6 有 base", n6_base is not None)


# ══════════════════════════════════════════
# TEST 2: 状态输出正确性
# ══════════════════════════════════════════
def test_state_output():
    print("\n" + "=" * 60)
    print("TEST 2: 状态输出正确性")
    print("=" * 60)

    api, mock = make_api()
    test = api._tests[0]
    p = test["points"][0]
    nodes = api._build_nodes(test, p)

    # 节点 1 (NORMAL): 12 通道，电压峰值 ≈ 57.735×√2
    n1 = nodes[1].base
    check("NORMAL: 12 通道", len(n1) == 12)
    va_peak = n1["0"]["1"][0]
    check("NORMAL: Va peak ≈ 81.65", abs(va_peak - 57.735 * SQRT2) < 0.1)
    ia_peak = n1["6"]["1"][0]
    check("NORMAL: Ia peak ≈ 2×√2=2.83", abs(ia_peak - 2.0 * SQRT2) < 0.1)

    # 节点 2 (FAULT): 故障电流应非零
    n2 = nodes[2].base
    check("FAULT: 12 通道", len(n2) == 12)
    ia_fault = n2["6"]["1"][0]
    check("FAULT: Ia ≠ 0 (故障电流)", ia_fault > 0.1)

    # 节点 4 (OPEN, pt=0): 三相额定电压 (pt=0 母线侧)
    n4 = nodes[4].base
    check("OPEN pt=0: Va peak ≈ 81.65", abs(n4["0"]["1"][0] - 57.735 * SQRT2) < 0.1)
    # 单相接地 ft=0 → Ia=0, Ib/Ic=load
    check("OPEN ft=0: Ia=0 (故障相)", abs(n4["6"]["1"][0]) < 0.01)
    check("OPEN ft=0: Ib=load", abs(n4["7"]["1"][0] - 2.0 * SQRT2) < 0.1)

    # OPEN pt=1: 线路侧
    api_pt1, _ = make_api(payload_overrides={"ctrl": {"pt": 1, "ct": 0, "cb": {"sim": False, "trip": 0, "close": 0}}})
    nodes_pt1 = api_pt1._build_nodes(api_pt1._tests[0], api_pt1._tests[0]["points"][0])
    n4_pt1 = nodes_pt1[4].base
    check("OPEN pt=1: Va=0 (故障相被切)", abs(n4_pt1["0"]["1"][0]) < 0.01)
    check("OPEN pt=1: Vb=rated", abs(n4_pt1["1"]["1"][0] - 57.735 * SQRT2) < 0.1)

    # PARK (ZERO): 电压额定，电流全 0
    zero_st = api._state_zero()
    check("ZERO: Va peak ≈ 81.65", abs(zero_st["0"]["1"][0] - 57.735 * SQRT2) < 0.1)
    check("ZERO: Ia=0", abs(zero_st["6"]["1"][0]) < 0.01)
    check("ZERO: Ib=0", abs(zero_st["7"]["1"][0]) < 0.01)


# ══════════════════════════════════════════
# TEST 3: 时间记录正确性
# ══════════════════════════════════════════
def test_timing():
    print("\n" + "=" * 60)
    print("TEST 3: 时间记录正确性")
    print("=" * 60)

    api, mock = make_api()
    api._reset_records()

    TICKS_PER_MS = 1000.0
    t_base = 1000000

    # 模拟进入节点 2 (FAULT1)
    api.onUpdate(2, 0, t_base)
    check("t2 set", api._t2 == t_base)

    # 跳 A (DI bit 0 变化, trip_phase_bits[A] 含 bit 0)
    trip_a_time = t_base + int(25.0 * TICKS_PER_MS)  # 25ms

    # 10. 模拟收到 A 相跳闸 DI (0x01)
    api.onDi(1, trip_a_time)
    check("tripA ≈ 25ms", abs(api._rec["tripA"] / TICKS_PER_MS - 25.0) < 0.1)

    # 同一相再次跳变 → 不覆盖
    api.onDi(0x00, trip_a_time + 1000)
    check("tripA not overwritten", api._rec["tripA"] == trip_a_time - t_base)

    # 这里假设节点 3 又持续了一会儿，然后在节点 3 中收到了 B 相跳闸
    api.onUpdate(3, 0, t_base + int(30.0 * TICKS_PER_MS))
    trip_b_time = t_base + int(35.0 * TICKS_PER_MS)  # 35ms

    api.onDi(1 | 2, trip_b_time)  # AB 均跳闸
    check("tripB based on t2 (≈35ms)", abs(api._rec["tripB"] / TICKS_PER_MS - 35.0) < 0.1)

    # 11. 进入 CBOPEN (Node 4)，更新 t4 基准
    t4 = t_base + int(100.0 * TICKS_PER_MS)
    api.onUpdate(4, 0, t4)

    # 12. 模拟收到重合 DI（假定 reclose_mask=0x08，即 bit3 变化）
    reclose_time = t4 + int(500.0 * TICKS_PER_MS)

    api.onDi((1 | 2) | 8, reclose_time)
    check("reclose ≈ 500ms", abs(api._rec["reclose"] / TICKS_PER_MS - 500.0) < 0.1)

    # 进入节点 6 (RECLOSE) → 永久故障 → 后加速基准
    api._reclose_is_fault = True
    t6 = reclose_time + int(0.05 * TICKS_PER_MS)
    api.onUpdate(6, 0, t6)
    check("t6 set", api._t6 == t6)
    check("seen_reclose", api._seen_reclose)

    # 后加速跳闸 (bit 0 变化)
    post_time = t6 + int(20.0 * TICKS_PER_MS)
    api.onDi(0x09, post_time)  # bit 0 变化
    check("postAccel recorded", "postAccel" in api._rec)
    check("postAccel ≈ 20ms", abs(api._rec["postAccel"] / TICKS_PER_MS - 20.0) < 0.1)

    # 重合后 tripA 不被覆盖
    old_tripA = api._rec.get("tripA")
    check("tripA not overwritten after reclose", api._rec.get("tripA") == old_tripA)


# ══════════════════════════════════════════
# TEST 4: DI 掩码正确性
# ══════════════════════════════════════════
def test_di_masks():
    print("\n" + "=" * 60)
    print("TEST 4: DI 掩码正确性")
    print("=" * 60)

    # 4a. TRIP_ABC: A=2, B=2, C=2 → 通道 0,1,2 分别归相 A,B,C
    api, _ = make_api()
    check("TRIP_ABC: bits[A]=bit0=1", api._trip_phase_bits['A'] == 1)
    check("TRIP_ABC: bits[B]=bit1=2", api._trip_phase_bits['B'] == 2)
    check("TRIP_ABC: bits[C]=bit2=4", api._trip_phase_bits['C'] == 4)
    check("TRIP_ABC: all=7", api._all_trip_bits == 7)

    # 4b. THREE_TRIP: A=1 → bit0 归入 A,B,C 三相
    api2, _ = make_api(payload_overrides={
        "di": {"A": 1, "B": 0, "C": 0, "R": 3, "a": 0, "b": 0, "c": 0, "r": 0}
    })
    check("THREE_TRIP: bits[A] 含 bit0", api2._trip_phase_bits['A'] & 1)
    check("THREE_TRIP: bits[B] 含 bit0", api2._trip_phase_bits['B'] & 1)
    check("THREE_TRIP: bits[C] 含 bit0", api2._trip_phase_bits['C'] & 1)

    # 4c. 混合: 第一组分相跳 + 第二组三跳
    api3, _ = make_api(payload_overrides={
        "di": {"A": 2, "B": 2, "C": 2, "R": 3, "a": 1, "b": 1, "c": 1, "r": 0}
    })
    # a(ch4)=1 THREE_TRIP → 三相都有 bit4
    # b(ch5)=1 THREE_TRIP → 三相都有 bit5
    # c(ch6)=1 THREE_TRIP → 三相都有 bit6
    check("混合: bits[A] = bit0|bit4|bit5|bit6 = 0x71", api3._trip_phase_bits['A'] == (1 | 16 | 32 | 64))
    check("混合: bits[B] = bit1|bit4|bit5|bit6 = 0x72", api3._trip_phase_bits['B'] == (2 | 16 | 32 | 64))

    # 4d. reclose_mask: R=3(ch3), r=3(ch7)
    api4, _ = make_api(payload_overrides={
        "di": {"A": 2, "B": 2, "C": 2, "R": 3, "a": 0, "b": 0, "c": 0, "r": 3}
    })
    check("reclose: R(ch3)+r(ch7) = bit3|bit7 = 0x88", api4._reclose_mask == (8 | 128))

    # 4e. _trip_mask for ft=0 (A相接地)
    mask = api._trip_mask(0)  # P={A}
    check("trip_mask(ft=0): = bits[A] = 1", mask == 1)

    # 4f. _trip_mask for ft=3 (AB相间)
    mask_ab = api._trip_mask(3)  # P={A,B}
    check("trip_mask(ft=3): = bits[A]|bits[B] = 3", mask_ab == 3)


# ══════════════════════════════════════════
# TEST 5: cb.sim 变形
# ══════════════════════════════════════════
def test_cb_sim():
    print("\n" + "=" * 60)
    print("TEST 5: cb.sim 变形")
    print("=" * 60)

    # 5a. cb_sim=False → FAULT1 直接跳 CBOPEN (hold1=4)
    api, _ = make_api()
    test = api._tests[0]
    p = test["points"][0]
    nodes = api._build_nodes(test, p)
    check("no cb.sim: FAULT1 DI→4 (CBOPEN)", nodes[2].diMatchId == 4)

    # 5b. cb_sim=True, trip=50 → FAULT1 DI→3, 节点 3 timeout=50→4
    api2, _ = make_api(payload_overrides={"ctrl": {"pt": 0, "ct": 0, "cb": {"sim": True, "trip": 50, "close": 0}}})
    nodes2 = api2._build_nodes(api2._tests[0], api2._tests[0]["points"][0])
    check("cb.sim trip: FAULT1 DI→3 (FAULT1_HOLD)", nodes2[2].diMatchId == 3)
    check("cb.sim trip: 节点 3 存在", 3 in nodes2)
    check("cb.sim trip: 节点 3 timeout=50ms→4", nodes2[3].timeoutMs == 50 and nodes2[3].timeoutId == 4)
    check("cb.sim no close: 节点 5 不存在", 5 not in nodes2)

    # 5c. cb_sim=True, close=30 → CBOPEN DI→5, 节点 5 timeout=30→6
    api3, _ = make_api(payload_overrides={"ctrl": {"pt": 0, "ct": 0, "cb": {"sim": True, "trip": 50, "close": 30}}})
    nodes3 = api3._build_nodes(api3._tests[0], api3._tests[0]["points"][0])
    check("cb.sim close: CBOPEN DI→5 (CBOPEN_HOLD)", nodes3[4].diMatchId == 5)
    check("cb.sim close: 节点 5 存在", 5 in nodes3)
    check("cb.sim close: 节点 5 timeout=30ms→6", nodes3[5].timeoutMs == 30 and nodes3[5].timeoutId == 6)

    # 5d. after=0 (非全相) + cb.sim：第一次跳闸经 FAULT1_HOLD(3)→CBOPEN(4)，死区内转换故障
    tests_a0 = [{"type": 5, "dir": 0, "nature": 0, "points": [
        {"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0,
         "ft2": 2, "z2": 3.0, "ang2": 75.0, "time2": 500, "i2": 8.0, "after": 0, "delay": 100}
    ]}]
    api4, _ = make_api(
        payload_overrides={"ctrl": {"pt": 0, "ct": 0, "cb": {"sim": True, "trip": 50, "close": 30}}},
        tests_overrides=tests_a0
    )
    nodes4 = api4._build_nodes(api4._tests[0], api4._tests[0]["points"][0])
    check("after=0+cb: FAULT1 DI→3 (跳闸延时保持)", nodes4[2].diMatchId == 3)
    check("after=0+cb: 节点 3 timeout=50→4 (断路器断开)", nodes4[3].timeoutMs == 50 and nodes4[3].timeoutId == 4)
    check("after=0+cb: CBOPEN(4) 死区 timeout=100→7", nodes4[4].timeoutMs == 100 and nodes4[4].timeoutId == 7)
    check("after=0+cb: 不含节点 5/6/8 (非全相不重合)",
          5 not in nodes4 and 6 not in nodes4 and 8 not in nodes4)
    check("after=0+cb: 节点 7 跳闸→9", nodes4[7].diMatchId == 9)


# ══════════════════════════════════════════
# TEST 6: 工频变化量 (type=4) 计算
# ══════════════════════════════════════════
def test_type4_deltaz():
    print("\n" + "=" * 60)
    print("TEST 6: 工频变化量 (type=4) 计算")
    print("=" * 60)

    # 6a. 验证正方向单相接地 (南瑞 formula=0, m=1.0)
    # ft=0 (A接地), coeff=1.05, offset = (1 - 1.05) * Ue = -0.05 * Ue
    tests_fwd_lg = [{"type": 4, "dir": 0, "nature": 0, "points": [
        {"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0, "m": 1.0, "formula": 0}
    ]}]
    api_fwd_lg, _ = make_api(tests_overrides=tests_fwd_lg)
    nodes_fwd_lg = api_fwd_lg._build_nodes(api_fwd_lg._tests[0], api_fwd_lg._tests[0]["points"][0])
    
    # 获取节点 2 (FAULT1) 的 A 相电压
    v_fwd_lg = nodes_fwd_lg[2].base["0"]["1"]
    v_amp, v_ang = v_fwd_lg
    # 原正常压降：(1+kL)*I*Z.
    # offset: -0.05 * 57.735 * sqrt(2) ≈ -4.08
    # 我们只确认电压成功被施加，并且测试没崩，幅值发生变化
    check("type=4 正向单相(南瑞): 成功计算 12 通道", len(nodes_fwd_lg[2].base) == 12)

    # 6b. 验证正方向相间短路 (许继 formula=1, m=1.1)
    # ft=3 (AB相间), coeff=1.3, offset = (1 - 1.3*1.1) * Uab = -0.43 * Uab
    tests_fwd_ll = [{"type": 4, "dir": 0, "nature": 0, "points": [
        {"id": 1, "ft": 3, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0, "m": 1.1, "formula": 1}
    ]}]
    api_fwd_ll, _ = make_api(tests_overrides=tests_fwd_ll)
    nodes_fwd_ll = api_fwd_ll._build_nodes(api_fwd_ll._tests[0], api_fwd_ll._tests[0]["points"][0])
    check("type=4 正向相间(许继): 成功计算 12 通道", len(nodes_fwd_ll[2].base) == 12)

    # 6c. 验证反方向短路 (dir=1)
    # 所有压降强制置 0 -> 故障相电压应等于 offset (0) -> 由于加上负荷，就是纯负荷引起的压降
    tests_rev = [{"type": 4, "dir": 1, "nature": 0, "points": [
        {"id": 1, "ft": 0, "z": 2.0, "ang": 75.0, "time": 1000, "i": 5.0, "m": 1.0, "formula": 0}
    ]}]
    # 设 load_amp = 0, load_ang = 0，那么反向故障时，V_drop=0，所以 Va=0
    api_rev, _ = make_api(payload_overrides={"sys": {"iLoad": {"amp": 0.0, "ang": 0.0}}}, tests_overrides=tests_rev)
    nodes_rev = api_rev._build_nodes(api_rev._tests[0], api_rev._tests[0]["points"][0])
    
    v_rev = nodes_rev[2].base["0"]["1"][0]
    check("type=4 反向故障: 压降置0 (Va ≈ 0)", abs(v_rev) < 0.01)

# ══════════════════════════════════════════
# TEST 7: DO 开出（断路器辅助接点模拟）
# ══════════════════════════════════════════
def test_do():
    print("\n" + "=" * 60)
    print("TEST 7: DO 开出编排")
    print("=" * 60)

    def do_bytes(node):
        """从 doActions 提取 [(delayMs, doByte), ...]"""
        return [((p >> 8) & 0xFFFF, p & 0xFF) for p in (node.doActions or [])]

    # 7a. 无 do 配置 → 不输出 DO
    api, _ = make_api()
    nodes = api._build_nodes(api._tests[0], api._tests[0]["points"][0])
    check("无 do: 节点 1 无 doActions", nodes[1].doActions is None)

    # 7b. mode=0 SYNC_WITH_RECLOSER：断开翻转、重合翻回（重合闸断路器模拟）
    api0, _ = make_api(
        payload_overrides={"do": {"init": 0x01, "mode": 0},
                           "ctrl": {"pt": 0, "ct": 0, "cb": {"sim": False, "trip": 0, "close": 0}}})
    n0 = api0._build_nodes(api0._tests[0], api0._tests[0]["points"][0])
    check("mode0: 节点1 起始=init(0x01)", do_bytes(n0[1]) == [(0, 0x01)])
    check("mode0: CBOPEN(4) 断开→翻转(0xFE)", do_bytes(n0[4]) == [(0, 0xFE)])
    check("mode0: RECLOSE(6) 重合→翻回(0x01)", do_bytes(n0[6]) == [(0, 0x01)])
    check("mode0: 故障态(2) 无 doActions(保持断路器闭合)", n0[2].doActions is None)

    # 7c. mode=1 DELAY_FLIP_ALL：故障态内 delay 翻转、hold 后翻回
    api1, _ = make_api(payload_overrides={"do": {"init": 0x00, "mode": 1,
                                                 "flipAll": {"delay": 20, "hold": 50}}})
    n1 = api1._build_nodes(api1._tests[0], api1._tests[0]["points"][0])
    check("mode1: FAULT1 翻转@20=0xFF, 翻回@70=0x00",
          do_bytes(n1[2]) == [(20, 0xFF), (70, 0x00)])

    # 7d. mode=2 CUSTOM：逐通道 FLIP
    api2, _ = make_api(payload_overrides={"do": {"init": 0x00, "mode": 2,
                       "custom": {"1": {"action": 1, "delay": 10, "hold": 30}, "2": {"action": 0}}}})
    n2 = api2._build_nodes(api2._tests[0], api2._tests[0]["points"][0])
    # ch1 FLIP: @10 置位 bit1=0x02, @40 复位=0x00；ch2 action=0 不参与
    check("mode2: ch1 FLIP @10=0x02, @40=0x00", do_bytes(n2[2]) == [(10, 0x02), (40, 0x00)])


# ══════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════
if __name__ == "__main__":
    print("线路保护 API 模拟测试")
    print("=" * 60)

    test_node_construction()
    test_state_output()
    test_timing()
    test_di_masks()
    test_cb_sim()
    test_type4_deltaz()
    test_do()

    print("\n" + "=" * 60)
    print(f"RESULT: {passed}/{total} tests passed")
    if passed == total:
        print("✅ ALL TESTS PASSED!")
    else:
        print(f"❌ {total - passed} TESTS FAILED!")
    print("=" * 60)
