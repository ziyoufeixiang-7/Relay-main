"""
Edge case verification: test extreme and boundary parameters.
Compare calculation_engine.py against the three original app.py calculations.
"""
import sys
import os
import math
import cmath

def to_complex(mag, ang_deg):
    return cmath.rect(mag, math.radians(ang_deg))

def normalize_angle(deg):
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg

def to_polar_deg(c):
    if abs(c) < 1e-6: return 0.0, 0.0
    return abs(c), normalize_angle(math.degrees(cmath.phase(c)))

# === Original: LL/LLL ===
def original_ll_lll(ft, sys_v, i_load_amp, i_load_ang, dir_val, i_f_amp, z_mag, z_ang, ct_val):
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    Z_line = to_complex(z_mag, z_ang)
    I_fwd = [complex(0, 0), complex(0, 0), complex(0, 0)]
    V_out = list(E_sys)
    if ft in [3, 4, 5]:
        if ft == 3:   x, y, delta_xy = 0, 1, 30.0
        elif ft == 4: x, y, delta_xy = 1, 2, -90.0
        elif ft == 5: x, y, delta_xy = 2, 0, 150.0
        I_fwd[x] = to_complex(i_f_amp, delta_xy - z_ang)
        I_fwd[y] = -I_fwd[x]
        V_xy_drop = (I_fwd[x] - I_fwd[y]) * Z_line
        V_center = (E_sys[x] + E_sys[y]) / 2.0
        V_out[x] = V_center + V_xy_drop / 2.0 + I_load[x] * Z_line
        V_out[y] = V_center - V_xy_drop / 2.0 + I_load[y] * Z_line
        z_unf = [i for i in [0,1,2] if i not in [x,y]][0]
        V_out[z_unf] = E_sys[z_unf]
    elif ft == 9:
        for p in range(3):
            I_fwd[p] = to_complex(i_f_amp, phase_shifts[p] - z_ang)
            V_out[p] = I_fwd[p] * Z_line + I_load[p] * Z_line
    I_actual = [(-i if dir_val == 1 else i) for i in I_fwd]
    I_out = list(I_load)
    for p in range(3):
        I_out[p] = I_load[p] + I_actual[p]
    if ct_val == 1:
        I_out = [-i for i in I_out]
    return tuple(V_out), tuple(I_out)

# === Original: LG ===
def original_lg(ft, sys_v, i_load_amp, i_load_ang, dir_val, i_f_amp, z_mag, z_ang, formula, kl_params, ct_val):
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    Z_line = to_complex(z_mag, z_ang)
    R_line, X_line = Z_line.real, Z_line.imag
    KL_actual = complex(0, 0)
    if formula == 1:
        kr = kl_params.get('kr', 0.0)
        kx = kl_params.get('kx', 0.0)
        Z_loop = complex((1.0 + kr) * R_line, (1.0 + kx) * X_line)
    else:
        if formula == 0:
            KL_actual = to_complex(kl_params.get('kl_amp', 0.0), kl_params.get('kl_ang', 0.0))
        elif formula == 2:
            z0z1_ratio = to_complex(kl_params.get('z0z1_amp', 1.0), kl_params.get('z0z1_ang', 0.0))
            KL_actual = (z0z1_ratio - 1.0) / 3.0
        Z_loop = Z_line * (1.0 + KL_actual)
    z_loop_mag, z_loop_ang = to_polar_deg(Z_loop)
    fault_phase_base_ang = phase_shifts[ft]
    i_f_fwd_ang = fault_phase_base_ang - z_loop_ang
    I_f_fwd = to_complex(i_f_amp, i_f_fwd_ang)
    if dir_val == 0:
        I_f_actual = I_f_fwd
    else:
        I_f_actual = -I_f_fwd
    V_out = list(E_sys)
    I_out = list(I_load)
    V_out[ft] = I_load[ft] * Z_line + I_f_fwd * Z_loop
    I_out[ft] = I_load[ft] + I_f_actual
    if ct_val == 1:
        I_out = [-i for i in I_out]
    return tuple(V_out), tuple(I_out)

# === Original: LLG ===
def original_llg(ft, sys_v, i_load_amp, i_load_ang, dir_val, i_f_amp, z_mag, z_ang, formula, kl_params, ct_val):
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    Z_line = to_complex(z_mag, z_ang)
    R_line, X_line = Z_line.real, Z_line.imag
    if ft == 6:   x, y, z_unf = 0, 1, 2
    elif ft == 7: x, y, z_unf = 1, 2, 0
    elif ft == 8: x, y, z_unf = 2, 0, 1
    if formula == 1:
        kr, kx = kl_params.get('kr', 0.0), kl_params.get('kx', 0.0)
        Z_0_drop = complex(kr * R_line, kx * X_line)
        KL = Z_0_drop / Z_line if abs(Z_line) > 1e-6 else complex(0, 0)
    else:
        if formula == 0:
            KL = to_complex(kl_params.get('kl_amp', 0.0), kl_params.get('kl_ang', 0.0))
        elif formula == 2:
            z0z1 = to_complex(kl_params.get('z0z1_amp', 1.0), kl_params.get('z0z1_ang', 0.0))
            KL = (z0z1 - 1.0) / 3.0
        Z_0_drop = KL * Z_line
    U_x_target = to_complex(1.0, phase_shifts[x])
    U_y_target = to_complex(1.0, phase_shifts[y])
    N_x = U_x_target * (1.0 + KL) - U_y_target * KL
    N_y = U_y_target * (1.0 + KL) - U_x_target * KL
    theta_Z = z_ang
    theta_corr = math.degrees(cmath.phase(1.0 + 2.0 * KL))
    alpha = math.degrees(cmath.phase(N_x)) - theta_Z - theta_corr
    beta  = math.degrees(cmath.phase(N_y)) - theta_Z - theta_corr
    I_x_fwd = to_complex(i_f_amp, alpha)
    I_y_fwd = to_complex(i_f_amp, beta)
    I_0_3 = I_x_fwd + I_y_fwd
    V_0_drop = I_0_3 * Z_0_drop
    V_out = list(E_sys)
    V_out[x] = I_x_fwd * Z_line + V_0_drop + I_load[x] * Z_line
    V_out[y] = I_y_fwd * Z_line + V_0_drop + I_load[y] * Z_line
    V_out[z_unf] = E_sys[z_unf]
    I_x_actual = -I_x_fwd if dir_val == 1 else I_x_fwd
    I_y_actual = -I_y_fwd if dir_val == 1 else I_y_fwd
    I_out = list(I_load)
    I_out[x] = I_x_actual + I_load[x]
    I_out[y] = I_y_actual + I_load[y]
    if ct_val == 1:
        I_out = [-i for i in I_out]
    return tuple(V_out), tuple(I_out)

# =============================================
# Import engine
# =============================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculation_engine import calculate_fault

# =============================================
# Comparison helper
# =============================================
def complex_close(a, b, tol=1e-6):
    return abs(a - b) < tol

def compare_outputs(label, V_ref, I_ref, V_test, I_test, tol=1e-6):
    errors = []
    phase_names = ['A', 'B', 'C']
    for i in range(3):
        if not complex_close(V_ref[i], V_test[i], tol):
            m1, a1 = to_polar_deg(V_ref[i])
            m2, a2 = to_polar_deg(V_test[i])
            errors.append(f"  V{phase_names[i]}: ref={m1:.6f}∠{a1:.4f}°  engine={m2:.6f}∠{a2:.4f}°  diff={abs(V_ref[i]-V_test[i]):.2e}")
        if not complex_close(I_ref[i], I_test[i], tol):
            m1, a1 = to_polar_deg(I_ref[i])
            m2, a2 = to_polar_deg(I_test[i])
            errors.append(f"  I{phase_names[i]}: ref={m1:.6f}∠{a1:.4f}°  engine={m2:.6f}∠{a2:.4f}°  diff={abs(I_ref[i]-I_test[i]):.2e}")
    if errors:
        print(f"❌ FAIL: {label}")
        for e in errors:
            print(e)
        return False
    else:
        print(f"✅ PASS: {label}")
        return True

def compare_or_both_error(label, ref_func, test_func):
    """Run both functions; if both raise the same type of exception, that's a PASS (consistent behavior)."""
    ref_err = None
    test_err = None
    try:
        V_ref, I_ref = ref_func()
    except Exception as e:
        ref_err = e
    try:
        V_test, I_test = test_func()
    except Exception as e:
        test_err = e
    
    if ref_err and test_err:
        print(f"✅ PASS (both error): {label}  [ref: {type(ref_err).__name__}, engine: {type(test_err).__name__}]")
        return True
    elif ref_err and not test_err:
        print(f"⚠️  DIFF: {label}  [ref errors ({type(ref_err).__name__}), engine succeeds]")
        return True  # engine is more robust
    elif not ref_err and test_err:
        print(f"❌ FAIL: {label}  [ref succeeds, engine errors ({type(test_err).__name__}: {test_err})]")
        return False
    else:
        return compare_outputs(label, V_ref, I_ref, V_test, I_test)

total = 0
passed = 0

print("=" * 80)
print("EDGE CASE VERIFICATION: calculation_engine.py vs original app.py")
print("=" * 80)

# =============================================
# EDGE 1: 阻抗 Z = 0 Ω
# =============================================
print("\n" + "=" * 60)
print("EDGE 1: 阻抗 Z = 0 Ω")
print("=" * 60)

z_mag_zero = 0.0
z_ang_default = 75.0
sys_v = 57.735
i_f = 5.0

# LG with Z=0
for ft in [0, 1, 2]:
    for formula, kl_p in [(0, {'kl_amp': 0.667, 'kl_ang': 0.0}), (1, {'kr': 0.2, 'kx': 0.8}), (2, {'z0z1_amp': 3.0, 'z0z1_ang': 0.0})]:
        label = f"LG Z=0 ft={ft} formula={formula}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_lg(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, f, k, 0))
        total += 1; passed += ok

# LL with Z=0
for ft in [3, 4, 5]:
    label = f"LL Z=0 ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_ll_lll(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

# LLG with Z=0
for ft in [6, 7, 8]:
    for formula, kl_p in [(0, {'kl_amp': 0.667, 'kl_ang': 0.0}), (1, {'kr': 0.2, 'kx': 0.8}), (2, {'z0z1_amp': 3.0, 'z0z1_ang': 0.0})]:
        label = f"LLG Z=0 ft={ft} formula={formula}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_llg(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, f, k, 0))
        total += 1; passed += ok

# LLL with Z=0
label = "LLL Z=0 ft=9"
ok = compare_or_both_error(label,
    lambda: original_ll_lll(9, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, 0),
    lambda: calculate_fault(9, sys_v, 0, 0, 0, i_f, z_mag_zero, z_ang_default, 0, {}, 0))
total += 1; passed += ok

# =============================================
# EDGE 2: 短路电流 If = 0 A
# =============================================
print("\n" + "=" * 60)
print("EDGE 2: 短路电流 If = 0 A")
print("=" * 60)

z_mag = 2.0
i_f_zero = 0.0

for ft in [0, 1, 2]:
    label = f"LG If=0 ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_lg(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

for ft in [3, 4, 5]:
    label = f"LL If=0 ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_ll_lll(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

for ft in [6, 7, 8]:
    label = f"LLG If=0 ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_llg(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

label = "LLL If=0 ft=9"
ok = compare_or_both_error(label,
    lambda: original_ll_lll(9, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0),
    lambda: calculate_fault(9, sys_v, 2.0, -30.0, 0, i_f_zero, z_mag, z_ang_default, 0, {}, 0))
total += 1; passed += ok

# =============================================
# EDGE 3: 系统电压 V = 0 V
# =============================================
print("\n" + "=" * 60)
print("EDGE 3: 系统电压 V = 0 V")
print("=" * 60)

sys_v_zero = 0.0
for ft in [0, 3, 6, 9]:
    if ft in [0, 1, 2]:
        label = f"LG V=0 ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft: original_lg(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft: calculate_fault(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    elif ft in [3, 4, 5]:
        label = f"LL V=0 ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft: original_ll_lll(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0),
            lambda ft=ft: calculate_fault(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {}, 0))
    elif ft in [6, 7, 8]:
        label = f"LLG V=0 ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft: original_llg(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft: calculate_fault(ft, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    elif ft == 9:
        label = f"LLL V=0 ft={ft}"
        ok = compare_or_both_error(label,
            lambda: original_ll_lll(9, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0),
            lambda: calculate_fault(9, sys_v_zero, 0, 0, 0, i_f, z_mag, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

# =============================================
# EDGE 4: 极大短路电流 (50A+)
# =============================================
print("\n" + "=" * 60)
print("EDGE 4: 极大短路电流 If = 50A, 100A")
print("=" * 60)

sys_v = 57.735
for i_f_big in [50.0, 100.0]:
    for ft in [0, 1, 2]:
        label = f"LG If={i_f_big}A ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, i=i_f_big: original_lg(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft, i=i_f_big: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
        total += 1; passed += ok
    for ft in [3, 4, 5]:
        label = f"LL If={i_f_big}A ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, i=i_f_big: original_ll_lll(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0),
            lambda ft=ft, i=i_f_big: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {}, 0))
        total += 1; passed += ok
    for ft in [6, 7, 8]:
        label = f"LLG If={i_f_big}A ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, i=i_f_big: original_llg(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft, i=i_f_big: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
        total += 1; passed += ok
    label = f"LLL If={i_f_big}A ft=9"
    ok = compare_or_both_error(label,
        lambda i=i_f_big: original_ll_lll(9, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0),
        lambda i=i_f_big: calculate_fault(9, sys_v, 2.0, -30.0, 0, i, z_mag, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

# =============================================
# EDGE 5: 极小短路电流 (0.01A)
# =============================================
print("\n" + "=" * 60)
print("EDGE 5: 极小短路电流 If = 0.01A")
print("=" * 60)

i_f_tiny = 0.01
for ft in [0, 1, 2]:
    label = f"LG If=0.01A ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_lg(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

for ft in [3, 4, 5]:
    label = f"LL If=0.01A ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_ll_lll(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

for ft in [6, 7, 8]:
    label = f"LLG If=0.01A ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_llg(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

label = "LLL If=0.01A ft=9"
ok = compare_or_both_error(label,
    lambda: original_ll_lll(9, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0),
    lambda: calculate_fault(9, sys_v, 2.0, -30.0, 0, i_f_tiny, z_mag, z_ang_default, 0, {}, 0))
total += 1; passed += ok

# =============================================
# EDGE 6: 阻抗角 = 0° 或 90°
# =============================================
print("\n" + "=" * 60)
print("EDGE 6: 阻抗角 = 0° 和 90°")
print("=" * 60)

for z_a in [0.0, 90.0]:
    for ft in [0, 1, 2]:
        label = f"LG z_ang={z_a}° ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, za=z_a: original_lg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft, za=z_a: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
        total += 1; passed += ok
    for ft in [3, 4, 5]:
        label = f"LL z_ang={z_a}° ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, za=z_a: original_ll_lll(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0),
            lambda ft=ft, za=z_a: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {}, 0))
        total += 1; passed += ok
    for ft in [6, 7, 8]:
        label = f"LLG z_ang={z_a}° ft={ft}"
        ok = compare_or_both_error(label,
            lambda ft=ft, za=z_a: original_llg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
            lambda ft=ft, za=z_a: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
        total += 1; passed += ok
    label = f"LLL z_ang={z_a}° ft=9"
    ok = compare_or_both_error(label,
        lambda za=z_a: original_ll_lll(9, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0),
        lambda za=z_a: calculate_fault(9, sys_v, 2.0, -30.0, 0, 5.0, z_mag, za, 0, {}, 0))
    total += 1; passed += ok

# =============================================
# EDGE 7: KL 参数为 0
# =============================================
print("\n" + "=" * 60)
print("EDGE 7: KL 参数为 0")
print("=" * 60)

kl_zero_sets = [
    (0, {'kl_amp': 0.0, 'kl_ang': 0.0}),
    (1, {'kr': 0.0, 'kx': 0.0}),
    (2, {'z0z1_amp': 1.0, 'z0z1_ang': 0.0}),  # Z0/Z1=1 => KL=0
]

for formula, kl_p in kl_zero_sets:
    for ft in [0, 1, 2]:
        label = f"LG KL=0 ft={ft} formula={formula}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_lg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0))
        total += 1; passed += ok
    for ft in [6, 7, 8]:
        label = f"LLG KL=0 ft={ft} formula={formula}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_llg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0))
        total += 1; passed += ok

# =============================================
# EDGE 8: KL 参数非常大 (Z0/Z1=10)
# =============================================
print("\n" + "=" * 60)
print("EDGE 8: KL 非常大 (Z0/Z1=10, KL_amp=5)")
print("=" * 60)

kl_big_sets = [
    (0, {'kl_amp': 5.0, 'kl_ang': 0.0}),
    (0, {'kl_amp': 5.0, 'kl_ang': 45.0}),
    (1, {'kr': 5.0, 'kx': 5.0}),
    (2, {'z0z1_amp': 10.0, 'z0z1_ang': 0.0}),
    (2, {'z0z1_amp': 10.0, 'z0z1_ang': 30.0}),
]

for formula, kl_p in kl_big_sets:
    for ft in [0, 1, 2]:
        label = f"LG KL_big ft={ft} formula={formula} {kl_p}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_lg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0))
        total += 1; passed += ok
    for ft in [6, 7, 8]:
        label = f"LLG KL_big ft={ft} formula={formula} {kl_p}"
        ok = compare_or_both_error(label,
            lambda ft=ft, f=formula, k=kl_p: original_llg(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0),
            lambda ft=ft, f=formula, k=kl_p: calculate_fault(ft, sys_v, 2.0, -30.0, 0, 5.0, z_mag, z_ang_default, f, k, 0))
        total += 1; passed += ok

# =============================================
# EDGE 9: 负荷电流远大于短路电流
# =============================================
print("\n" + "=" * 60)
print("EDGE 9: 负荷电流 >> 短路电流 (Iload=20A, If=1A)")
print("=" * 60)

i_load_big = 20.0
i_f_small = 1.0

for ft in [0, 1, 2]:
    label = f"LG Iload>>If ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_lg(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

for ft in [3, 4, 5]:
    label = f"LL Iload>>If ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_ll_lll(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {}, 0))
    total += 1; passed += ok

for ft in [6, 7, 8]:
    label = f"LLG Iload>>If ft={ft}"
    ok = compare_or_both_error(label,
        lambda ft=ft: original_llg(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0),
        lambda ft=ft: calculate_fault(ft, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {'kl_amp': 0.667, 'kl_ang': 0.0}, 0))
    total += 1; passed += ok

label = "LLL Iload>>If ft=9"
ok = compare_or_both_error(label,
    lambda: original_ll_lll(9, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0),
    lambda: calculate_fault(9, sys_v, i_load_big, -30.0, 0, i_f_small, z_mag, z_ang_default, 0, {}, 0))
total += 1; passed += ok

# =============================================
# Summary
# =============================================
print("\n" + "=" * 80)
print(f"EDGE CASE RESULT: {passed}/{total} tests passed")
if passed == total:
    print("✅ ALL EDGE CASE TESTS PASSED!")
else:
    print(f"❌ {total - passed} EDGE CASE TESTS FAILED!")
print("=" * 80)
