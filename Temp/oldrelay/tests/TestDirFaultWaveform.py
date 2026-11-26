"""综合测试: InvTimeItHdl 方向性故障波形 — 电压+电流全覆盖"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.FPGACodec import HWConfig
from api.InvTimeItHdl import InvTimeItHdl, _PolarToRect, _RectToPolar

# 寄存器 → 物理值 反算
def reg_to_amp(reg, is_cur=False):
    scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
    return reg / scale

def reg_to_phase(reg):
    return reg / HWConfig.PHASE

# Mock 校准管理器 (透传)
class MockMgr:
    def ApplyAbsolute(self, ch, v): return v
    def ApplyDelta(self, ch, v): return v

def make_hdl():
    h = InvTimeItHdl()
    h.mgr = MockMgr()
    h.MODULE_KEY = "inverse_time_it_test"
    return h

def extract(result, ch, is_cur=False):
    """从寄存器结果中提取 (amplitude, phase_deg)"""
    d = result[str(ch)]
    amp = reg_to_amp(d["1"][0], is_cur)
    phase = reg_to_phase(d["1"][1])
    return amp, phase

def normalize(angle):
    while angle > 180: angle -= 360
    while angle <= -180: angle += 360
    return angle

def assert_close(actual, expected, tol, label):
    if abs(actual - expected) > tol:
        raise AssertionError(f"{label}: {actual:.4f} != {expected:.4f} (tol={tol})")

def check(result, ch, exp_amp, exp_phase, is_cur=False, label=""):
    amp, phase = extract(result, ch, is_cur)
    phase = normalize(phase)
    exp_phase = normalize(exp_phase)
    assert_close(amp, exp_amp, 0.15, f"{label} ch{ch} amp")
    if exp_amp > 0.01:  # 幅值为0时相位无意义
        assert_close(phase, exp_phase, 0.2, f"{label} ch{ch} phase")

# ═══════════════════════════════════════════════════════
#  电压测试
# ═══════════════════════════════════════════════════════

def base_payload(fault_type="A_GROUND", i_def="FAULT_CURRENT"):
    return {
        "iDefinition": i_def, "faultType": fault_type, "iBeforeFault": 0,
        "directionality": {
            "enabled": True, "vBeforeFault": 57.735, "vFault": 20.0,
            "lineAngle": 70.0, "ctPolarity": "TOWARD_COMPONENT", "preLoadAngle": 0
        }
    }

def test_voltage_prefault():
    h = make_hdl()
    p = base_payload()
    r = h._ComputeDirVoltages(p, False, (0,1,2), 50)
    check(r, 0, 57.735, 0, label="V_pre")
    check(r, 1, 57.735, -120, label="V_pre")
    check(r, 2, 57.735, 120, label="V_pre")
    print("  ✓ 电压: 故障前三相对称")

def test_voltage_a_ground():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("A_GROUND"), True, (0,1,2), 50)
    check(r, 0, 20.0, 0, label="V_AG")
    check(r, 1, 57.735, -120, label="V_AG")
    check(r, 2, 57.735, 120, label="V_AG")
    print("  ✓ 电压: A接地 (Ua=20V∠0°)")

def test_voltage_b_ground():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("B_GROUND"), True, (0,1,2), 50)
    check(r, 0, 57.735, 0, label="V_BG")
    check(r, 1, 20.0, -120, label="V_BG")
    check(r, 2, 57.735, 120, label="V_BG")
    print("  ✓ 电压: B接地 (Ub=20V∠-120°)")

def test_voltage_ab_phase():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("AB_PHASE"), True, (0,1,2), 50)
    check(r, 0, 30.55, -40.89, label="V_AB")
    check(r, 1, 30.55, -79.11, label="V_AB")
    check(r, 2, 57.735, 120, label="V_AB")
    print("  ✓ 电压: AB相间 (Ua=30.55V∠-40.89°, Ub=30.55V∠-79.11°)")

def test_voltage_bc_phase():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("BC_PHASE"), True, (0,1,2), 50)
    check(r, 0, 57.735, 0, label="V_BC")  # A不变
    # Ub, Uc 由线电压 Ubc=20∠-90° 解出
    uxy_r, uxy_i = _PolarToRect(20, -90)
    nz_r, nz_i = _PolarToRect(57.735, 0+180)  # -Ua
    ux_m, ux_a = _RectToPolar((uxy_r+nz_r)/2, (uxy_i+nz_i)/2)
    uy_m, uy_a = _RectToPolar((nz_r-uxy_r)/2, (nz_i-uxy_i)/2)
    check(r, 1, ux_m, ux_a, label="V_BC")
    check(r, 2, uy_m, uy_a, label="V_BC")
    print(f"  ✓ 电压: BC相间 (Ub={ux_m:.2f}V∠{ux_a:.2f}°, Uc={uy_m:.2f}V∠{uy_a:.2f}°)")

def test_voltage_ca_phase():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("CA_PHASE"), True, (0,1,2), 50)
    check(r, 1, 57.735, -120, label="V_CA")  # B不变
    uxy_r, uxy_i = _PolarToRect(20, 150)
    nz_r, nz_i = _PolarToRect(57.735, -120+180)  # -Ub
    ux_m, ux_a = _RectToPolar((uxy_r+nz_r)/2, (uxy_i+nz_i)/2)
    uy_m, uy_a = _RectToPolar((nz_r-uxy_r)/2, (nz_i-uxy_i)/2)
    check(r, 2, ux_m, ux_a, label="V_CA")
    check(r, 0, uy_m, uy_a, label="V_CA")
    print(f"  ✓ 电压: CA相间 (Uc={ux_m:.2f}V∠{ux_a:.2f}°, Ua={uy_m:.2f}V∠{uy_a:.2f}°)")

def test_voltage_abc():
    h = make_hdl()
    r = h._ComputeDirVoltages(base_payload("ABC_PHASE"), True, (0,1,2), 50)
    check(r, 0, 20.0, 0, label="V_ABC")
    check(r, 1, 20.0, -120, label="V_ABC")
    check(r, 2, 20.0, 120, label="V_ABC")
    print("  ✓ 电压: 三相短路 (全降到20V)")

# ═══════════════════════════════════════════════════════
#  电流测试 — FAULT_CURRENT 方向性
# ═══════════════════════════════════════════════════════

def test_current_fc_a_ground():
    h = make_hdl()
    p = base_payload("A_GROUND")
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, -70, True, "I_FC_AG")  # 0-70=-70
    check(r, 7, 0, 0, True, "I_FC_AG")
    check(r, 8, 0, 0, True, "I_FC_AG")
    print("  ✓ 电流FC: A接地 Ia=10A∠-70°")

def test_current_fc_ab():
    h = make_hdl()
    p = base_payload("AB_PHASE")
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, 30-70, True, "I_FC_AB")   # -40°
    check(r, 7, 10, 210-70, True, "I_FC_AB")  # 140°
    check(r, 8, 0, 0, True, "I_FC_AB")
    print("  ✓ 电流FC: AB相间 Ia=10A∠-40°, Ib=10A∠140°")

def test_current_fc_bc():
    h = make_hdl()
    p = base_payload("BC_PHASE")
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 0, 0, True, "I_FC_BC")
    check(r, 7, 10, -90-70, True, "I_FC_BC")  # -160°
    check(r, 8, 10, 90-70, True, "I_FC_BC")   # 20°
    print("  ✓ 电流FC: BC相间 Ib=10A∠-160°, Ic=10A∠20°")

def test_current_fc_ca():
    h = make_hdl()
    p = base_payload("CA_PHASE")
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, 330-70, True, "I_FC_CA")  # 260°→-100°
    check(r, 7, 0, 0, True, "I_FC_CA")
    check(r, 8, 10, 150-70, True, "I_FC_CA")  # 80°
    print("  ✓ 电流FC: CA相间 Ia=10A∠-100°, Ic=10A∠80°")

def test_current_fc_abc():
    h = make_hdl()
    p = base_payload("ABC_PHASE")
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, -70, True, "I_FC_ABC")
    check(r, 7, 10, -120-70, True, "I_FC_ABC")
    check(r, 8, 10, 120-70, True, "I_FC_ABC")
    print("  ✓ 电流FC: 三相 Ia=10A∠-70°, Ib∠-190°, Ic∠50°")

# ═══════════════════════════════════════════════════════
#  电流测试 — I2 方向性
# ═══════════════════════════════════════════════════════

def test_current_i2_a_ground():
    h = make_hdl()
    p = base_payload("A_GROUND", "NEGATIVE_SEQUENCE")
    r = h._ComputeCurrents(5, p, (6,7,8), 50)
    check(r, 6, 15, -70, True, "I_I2_AG")  # 3×5=15
    print("  ✓ 电流I2: A接地 scale=3 → Ia=15A∠-70°")

def test_current_i2_ab():
    h = make_hdl()
    p = base_payload("AB_PHASE", "NEGATIVE_SEQUENCE")
    r = h._ComputeCurrents(5, p, (6,7,8), 50)
    exp = 5 * math.sqrt(3)
    check(r, 6, exp, -40, True, "I_I2_AB")
    check(r, 7, exp, 140, True, "I_I2_AB")
    print(f"  ✓ 电流I2: AB相间 scale=√3 → Ia={exp:.2f}A∠-40°")

def test_current_i2_abc_neg():
    h = make_hdl()
    p = base_payload("ABC_PHASE", "NEGATIVE_SEQUENCE")
    r = h._ComputeCurrents(5, p, (6,7,8), 50)
    check(r, 6, 5, -70, True, "I_I2_ABC")
    check(r, 7, 5, 50, True, "I_I2_ABC")   # 120-70=50
    check(r, 8, 5, -190, True, "I_I2_ABC") # -120-70=-190
    print("  ✓ 电流I2: 纯负序ABC (ACB: ∠-70°, ∠50°, ∠-190°)")

# ═══════════════════════════════════════════════════════
#  电流测试 — I0 方向性
# ═══════════════════════════════════════════════════════

def test_current_i0_a_ground():
    h = make_hdl()
    p = base_payload("A_GROUND", "ZERO_SEQUENCE")
    r = h._ComputeCurrents(5, p, (6,7,8), 50)
    check(r, 6, 15, -70, True, "I_I0_AG")
    print("  ✓ 电流I0: A接地 scale=3 → Ia=15A∠-70°")

def test_current_i0_abc_zero():
    h = make_hdl()
    p = base_payload("ABC_PHASE", "ZERO_SEQUENCE")
    r = h._ComputeCurrents(5, p, (6,7,8), 50)
    check(r, 6, 5, -70, True, "I_I0_ABC0")
    check(r, 7, 5, -70, True, "I_I0_ABC0")
    check(r, 8, 5, -70, True, "I_I0_ABC0")
    print("  ✓ 电流I0: 纯零序ABC (同相: 全∠-70°)")

# ═══════════════════════════════════════════════════════
#  叠加 + CT极性测试
# ═══════════════════════════════════════════════════════

def test_superposition():
    """I_pre=2A, φ=30°, I_delta=10A AB → Ia = vector_add(2∠-30°, 10∠-40°)"""
    h = make_hdl()
    p = base_payload("AB_PHASE")
    p["iBeforeFault"] = 2.0
    p["directionality"]["preLoadAngle"] = 30.0
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    from api.InvTimeItHdl import _VectorAdd
    exp_a_m, exp_a_a = _VectorAdd(2, -30, 10, -40)
    exp_b_m, exp_b_a = _VectorAdd(2, -150, 10, 140)
    amp_a, ang_a = extract(r, 6, True)
    amp_b, ang_b = extract(r, 7, True)
    amp_c, _ = extract(r, 8, True)
    assert_close(amp_a, exp_a_m, 0.15, "superpos Ia amp")
    assert_close(normalize(ang_a), normalize(exp_a_a), 0.3, "superpos Ia ang")
    assert_close(amp_b, exp_b_m, 0.15, "superpos Ib amp")
    assert_close(amp_c, 2.0, 0.15, "superpos Ic amp (pre only)")
    print(f"  ✓ 叠加: Ia={amp_a:.2f}A∠{normalize(ang_a):.1f}°, Ic保持负荷={amp_c:.2f}A")

def test_ct_polarity():
    """CT=TOWARD_BUSBAR → 所有电流相位 +180°"""
    h = make_hdl()
    p = base_payload("A_GROUND")
    p["directionality"]["ctPolarity"] = "TOWARD_BUSBAR"
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, -70+180, True, "CT_BUS")  # 110°
    print("  ✓ CT极性: TOWARD_BUSBAR → Ia=10A∠110° (+180°)")

# ═══════════════════════════════════════════════════════
#  非方向性 (backward compatibility)
# ═══════════════════════════════════════════════════════

def test_non_directional():
    h = make_hdl()
    p = {"iDefinition": "FAULT_CURRENT", "faultType": "AB_PHASE",
         "directionality": {"enabled": False}}
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, 0, True, "NONDIR")    # FAULT_PHASE_MAP AB: 0°
    check(r, 7, 10, 180, True, "NONDIR")   # 180°
    print("  ✓ 非方向性: AB Ia=10A∠0°, Ib=10A∠180° (FAULT_PHASE_MAP)")

def test_single_path():
    h = make_hdl()
    p = {"iDefinition": "SINGLE_PATH", "faultType": "AB_PHASE",
         "directionality": {"enabled": True, "lineAngle": 70}}
    r = h._ComputeCurrents(10, p, (6,7,8), 50)
    check(r, 6, 10, 0, True, "SP")
    check(r, 7, 10, 0, True, "SP")
    amp_c, _ = extract(r, 8, True)
    assert_close(amp_c, 0, 0.01, "SP Ic=0")
    print("  ✓ SINGLE_PATH: AB → Ia=Ib=10A∠0°, Ic=0")

# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    print(" 电压测试")
    print("=" * 50)
    test_voltage_prefault()
    test_voltage_a_ground()
    test_voltage_b_ground()
    test_voltage_ab_phase()
    test_voltage_bc_phase()
    test_voltage_ca_phase()
    test_voltage_abc()

    print("\n" + "=" * 50)
    print(" 电流测试 — FAULT_CURRENT 方向性")
    print("=" * 50)
    test_current_fc_a_ground()
    test_current_fc_ab()
    test_current_fc_bc()
    test_current_fc_ca()
    test_current_fc_abc()

    print("\n" + "=" * 50)
    print(" 电流测试 — I2 方向性")
    print("=" * 50)
    test_current_i2_a_ground()
    test_current_i2_ab()
    test_current_i2_abc_neg()

    print("\n" + "=" * 50)
    print(" 电流测试 — I0 方向性")
    print("=" * 50)
    test_current_i0_a_ground()
    test_current_i0_abc_zero()

    print("\n" + "=" * 50)
    print(" 叠加 + CT极性")
    print("=" * 50)
    test_superposition()
    test_ct_polarity()

    print("\n" + "=" * 50)
    print(" 非方向性 + SINGLE_PATH")
    print("=" * 50)
    test_non_directional()
    test_single_path()

    print("\n" + "=" * 50)
    print(" ✅ 全部 18 项测试通过!")
    print("=" * 50)
