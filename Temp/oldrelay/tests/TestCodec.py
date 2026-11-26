"""
FlatCodec 单元测试 — ADR-001 位运算打包规范验证

测试范围:
  1. DDS word pack/unpack 往返 (§5.3)
  2. 有符号 Amp 的 22-bit 补码正确性
  3. DoAction pack/unpack (§5.1)
  4. ResetDo pack/unpack (§5.2)
  5. Trigger pack/unpack (§5.4)
  6. PhaseGate pack/unpack (§5.5)
  7. CompileNode 端到端 (JSON → Flat Node)
  8. step + count → Steps 统一展平
  9. 负数 nextId (-1, -2) 的 U16 编码
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.FlatCodec import FlatCodec, NodeKey, TrigCond
from api.ApiHdlMgr import ApiHdlMgr

PASS = 0
FAIL = 0

def check(name: str, actual, expected):
    global PASS, FAIL
    if actual == expected:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")
        print(f"    Expected: {expected}")
        print(f"    Actual:   {actual}")


def test_dds_word_roundtrip():
    """§5.3 DDS word: 正数 Amp 往返"""
    print("\n[Test] DDS Word Roundtrip (positive amp)")
    ch, layer, amp, freq = 3, 1, 100, 0x53E2D6D3
    word = FlatCodec.PackDdsWord(ch, layer, amp, freq)
    ch2, l2, a2, f2 = FlatCodec.UnpackDdsWord(word)
    check("channel", ch2, ch)
    check("layer", l2, layer)
    check("amp", a2, 100 & ~0x3FF)  # 底10位被截断
    check("freq", f2, freq)


def test_dds_word_negative_amp():
    """§5.3 DDS word: 负数 Amp 22-bit 有符号"""
    print("\n[Test] DDS Word Negative Amp")
    ch, layer, amp, freq = 0, 1, -100, 0
    word = FlatCodec.PackDdsWord(ch, layer, amp, freq)
    ch2, l2, a2, f2 = FlatCodec.UnpackDdsWord(word)
    check("channel", ch2, ch)
    check("layer", l2, layer)
    check("amp (negative)", a2, -1024)  # -100 截断底10位 → -1024
    check("freq", f2, 0)


def test_dds_word_max_values():
    """§5.3 DDS word: 极限边界值"""
    print("\n[Test] DDS Word Max Values")
    ch, layer, amp, freq = 15, 63, 0x7FFFFFFF, 0xFFFFFFFF  # Max positive 32-bit
    word = FlatCodec.PackDdsWord(ch, layer, amp, freq)
    ch2, l2, a2, f2 = FlatCodec.UnpackDdsWord(word)
    check("max channel", ch2, 15)
    check("max layer", l2, 63)
    check("max positive amp (32-bit)", a2, 0x7FFFFC00)  # 底10位截断
    check("max freq", f2, 0xFFFFFFFF)


def test_dds_word_min_negative():
    """§5.3 DDS word: 最小负数 32-bit"""
    print("\n[Test] DDS Word Min Negative Amp")
    amp = -0x80000000  # Min negative 32-bit
    word = FlatCodec.PackDdsWord(0, 0, amp, 0)
    _, _, a2, _ = FlatCodec.UnpackDdsWord(word)
    check("min negative amp", a2, -0x80000000)


def test_do_action():
    """§5.1 DoAction pack/unpack"""
    print("\n[Test] DoAction")
    delay, mask = 500, 0x05
    packed = FlatCodec.PackDoAction(delay, mask)
    check("packed value", packed, (500 << 16) | 5)
    d2, m2 = FlatCodec.UnpackDoAction(packed)
    check("delay", d2, 500)
    check("mask", m2, 5)


def test_reset_do():
    """§5.2 ResetDo pack/unpack"""
    print("\n[Test] ResetDo")
    exit_m, enter_m = 5, 3
    packed = FlatCodec.PackResetDo(exit_m, enter_m)
    check("packed value", packed, (5 << 16) | 3)
    e2, n2 = FlatCodec.UnpackResetDo(packed)
    check("exit_mask", e2, 5)
    check("enter_mask", n2, 3)


def test_trigger_di_match():
    """§5.4 Trigger: DI_MATCH"""
    print("\n[Test] Trigger DI_MATCH")
    packed = FlatCodec.PackTrigger(TrigCond.ConDiMatch, 2, 0x101)
    cond, nid, union = FlatCodec.UnpackTrigger(packed)
    check("condition", cond, 0)
    check("next_id", nid, 2)
    check("diMask", union, 0x101)


def test_trigger_timeout():
    """§5.4 Trigger: TIME_OUT"""
    print("\n[Test] Trigger TIME_OUT")
    packed = FlatCodec.PackTrigger(TrigCond.ConTimeout, 0xFFFE, 150000)
    cond, nid, union = FlatCodec.UnpackTrigger(packed)
    check("condition", cond, 1)
    check("next_id (0xFFFE = -2)", nid, 0xFFFE)
    check("timeout_ms", union, 150000)


def test_trigger_count_over():
    """§5.4 Trigger: COUNT_OVER"""
    print("\n[Test] Trigger COUNT_OVER")
    packed = FlatCodec.PackTrigger(TrigCond.ConCountOver, 0xFFFF, 0)
    cond, nid, union = FlatCodec.UnpackTrigger(packed)
    check("condition", cond, 2)
    check("next_id (0xFFFF = -1)", nid, 0xFFFF)


def test_phase_gate():
    """§5.5 PhaseGate pack/unpack"""
    print("\n[Test] PhaseGate")
    ch, phase = 0, 1073741824
    packed = FlatCodec.PackPhaseGate(ch, phase)
    c2, p2 = FlatCodec.UnpackPhaseGate(packed)
    check("channel", c2, 0)
    check("phase", p2, 1073741824)


def test_compile_node_static_only():
    """CompileNode: 纯静态节点"""
    print("\n[Test] CompileNode Static Only")
    node = {
        "id": 0,
        "static": {"0": {"0": [100, 0x53E2D6D3]}},
        "triggers": [{"condition": "TIME_OUT", "timeout": 5000, "nextId": 1}]
    }
    flat = ApiHdlMgr.CompileNode(node)
    check("Id", flat[NodeKey.Id], 0)
    check("Static has 1 word", len(flat[NodeKey.Static]), 1)
    check("Triggers has 1 entry", len(flat[NodeKey.Triggers]), 1)
    # Verify trigger
    cond, nid, union = FlatCodec.UnpackTrigger(flat[NodeKey.Triggers][0])
    check("trigger condition", cond, TrigCond.ConTimeout)
    check("trigger nextId", nid, 1)
    check("trigger timeout", union, 5000)


def test_compile_node_step_count_to_steps():
    """CompileNode: step + count → Steps 统一展平"""
    print("\n[Test] CompileNode step+count → Steps")
    node = {
        "id": 1,
        "static": {"0": {"1": [100, 0]}},
        "dynamic": {
            "interval": 200,
            "count": 3,
            "step": {"0": {"1": [50, 0]}}
        },
        "triggers": [{"condition": "COUNT_OVER", "nextId": -2}]
    }
    flat = ApiHdlMgr.CompileNode(node)
    check("Interval", flat[NodeKey.Interval], 200)
    check("Steps length = count", len(flat[NodeKey.Steps]), 3)
    # All steps should be identical (uniform)
    check("Steps[0] == Steps[1]", flat[NodeKey.Steps][0], flat[NodeKey.Steps][1])
    check("Steps[1] == Steps[2]", flat[NodeKey.Steps][1], flat[NodeKey.Steps][2])
    # Verify step DDS word
    ch, layer, amp, freq = FlatCodec.UnpackDdsWord(flat[NodeKey.Steps][0][0])
    check("step ch", ch, 0)
    check("step layer", layer, 1)
    check("step amp", amp, 50 & ~0x3FF)  # 底10位截断 → 0


def test_compile_node_steps_nonuniform():
    """CompileNode: steps (Mode B non-uniform, e.g. DcComp)"""
    print("\n[Test] CompileNode Steps non-uniform")
    node = {
        "id": 10,
        "static": {"0": {"0": [1000, 0x53E2D6D3]}},
        "dynamic": {
            "interval": 10,
            "steps": [
                {"0": {"0": [100, 0]}},
                {"0": {"0": [-5, 0]}},
                {"0": {"0": [-4, 0]}}
            ],
            "phaseGate": {"channel": 0, "phase": 1073741824}
        },
        "triggers": [{"condition": "TIME_OUT", "timeout": 500, "nextId": 2}]
    }
    flat = ApiHdlMgr.CompileNode(node)
    check("Steps length", len(flat[NodeKey.Steps]), 3)
    # Verify non-uniform: step 0 and step 1 differ
    check("Steps[0] != Steps[1]", flat[NodeKey.Steps][0] != flat[NodeKey.Steps][1], True)
    # Verify PhaseGate
    pg_ch, pg_phase = FlatCodec.UnpackPhaseGate(flat[NodeKey.PhaseGate])
    check("PhaseGate channel", pg_ch, 0)
    check("PhaseGate phase", pg_phase, 1073741824)


def test_compile_node_with_reset():
    """CompileNode: 节点带 Reset 配置"""
    print("\n[Test] CompileNode with Reset")
    node = {
        "id": 1,
        "doActions": [{"delay": 0, "mask": 5}],
        "static": {"0": {"1": [100, 0]}},
        "dynamic": {
            "interval": 1000,
            "count": 10,
            "step": {"0": {"1": [50, 0]}},
            "reset": {
                "time": 100,
                "static": {"0": {"0": [0, 0x53E2D6D3]}},
                "resetDo": 3,
                "overDo": 5
            }
        },
        "triggers": [
            {"condition": "DI_MATCH", "diMask": 1, "nextId": 2},
            {"condition": "COUNT_OVER", "nextId": -2}
        ]
    }
    flat = ApiHdlMgr.CompileNode(node)
    check("has DoAction", NodeKey.DoAction in flat, True)
    check("DoAction count", len(flat[NodeKey.DoAction]), 1)
    d, m = FlatCodec.UnpackDoAction(flat[NodeKey.DoAction][0])
    check("DoAction delay", d, 0)
    check("DoAction mask", m, 5)
    check("ResetTime", flat[NodeKey.ResetTime], 100)
    check("has ResetStatic", NodeKey.ResetStatic in flat, True)
    exit_m, enter_m = FlatCodec.UnpackResetDo(flat[NodeKey.ResetDo])
    check("ResetDo exit_mask (overDo)", exit_m, 5)
    check("ResetDo enter_mask (resetDo)", enter_m, 3)


def test_negative_next_id():
    """CompileNode: 负数 nextId (-1 → 0xFFFF, -2 → 0xFFFE)"""
    print("\n[Test] Negative nextId encoding")
    node = {
        "id": 0,
        "triggers": [
            {"condition": "COUNT_OVER", "nextId": -1},
            {"condition": "DI_MATCH", "diMask": 1, "nextId": -2}
        ]
    }
    flat = ApiHdlMgr.CompileNode(node)
    _, nid1, _ = FlatCodec.UnpackTrigger(flat[NodeKey.Triggers][0])
    _, nid2, _ = FlatCodec.UnpackTrigger(flat[NodeKey.Triggers][1])
    check("nextId -1 → 0xFFFF", nid1, 0xFFFF)
    check("nextId -2 → 0xFFFE", nid2, 0xFFFE)


# ═══════════════════════════════════════════════════
#  Runner
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    test_dds_word_roundtrip()
    test_dds_word_negative_amp()
    test_dds_word_max_values()
    test_dds_word_min_negative()
    test_do_action()
    test_reset_do()
    test_trigger_di_match()
    test_trigger_timeout()
    test_trigger_count_over()
    test_phase_gate()
    test_compile_node_static_only()
    test_compile_node_step_count_to_steps()
    test_compile_node_steps_nonuniform()
    test_compile_node_with_reset()
    test_negative_next_id()

    print(f"\n{'='*50}")
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    if FAIL > 0:
        sys.exit(1)
