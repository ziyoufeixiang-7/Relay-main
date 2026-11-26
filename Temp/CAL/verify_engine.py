"""
Verification script: compare calculation_engine.py against the three original app.py files.
Tests all fault types with multiple parameter combinations.
"""
import sys
import os
import math
import cmath

# =============================================
# Embed the three original calculation functions
# =============================================

def to_complex(mag, ang_deg):
    return cmath.rect(mag, math.radians(ang_deg))

def normalize_angle(deg):
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg

def to_polar_deg(c):
    if abs(c) < 1e-6: return 0.0, 0.0
    return abs(c), normalize_angle(math.degrees(cmath.phase(c)))

# === Original: 相间短路和三相短路 (LL/LLL) from app.py ===
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

# === Original: 单相接地 (LG) from app.py ===
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

# === Original: 两相接地 (LLG) from app.py ===
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
# Import the unified engine
# =============================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculation_engine import calculate_fault

# =============================================
# Test infrastructure
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

# =============================================
# Test cases
# =============================================
total = 0
passed = 0

# --- Common parameters ---
sys_v = 57.735
z_mag = 2.0
z_ang = 75.0

# Parameter sets: (i_load_amp, i_load_ang, dir_val, i_f_amp, ct_val)
param_sets = [
    (0.0, 0.0, 0, 5.0, 0),      # No load, forward, CT=0
    (0.0, 0.0, 1, 5.0, 0),      # No load, reverse, CT=0
    (0.0, 0.0, 0, 5.0, 1),      # No load, forward, CT=1
    (2.0, -30.0, 0, 5.0, 0),    # With load, forward
    (2.0, -30.0, 1, 5.0, 0),    # With load, reverse
    (2.0, -30.0, 0, 5.0, 1),    # With load, forward, CT=1
    (1.0, 0.0, 0, 10.0, 0),     # Large fault current
    (3.0, -45.0, 0, 3.0, 0),    # Different load angle
]

# KL parameter sets for LG and LLG:
kl_param_sets = [
    (0, {'kl_amp': 0.667, 'kl_ang': 0.0}),       # formula 0, standard
    (0, {'kl_amp': 0.667, 'kl_ang': 33.0}),       # formula 0, with angle
    (1, {'kr': 0.2, 'kx': 0.8}),                   # formula 1
    (1, {'kr': 0.667, 'kx': 0.667}),               # formula 1, equal kr/kx
    (2, {'z0z1_amp': 3.0, 'z0z1_ang': 0.0}),       # formula 2, standard
    (2, {'z0z1_amp': 3.0, 'z0z1_ang': 15.0}),      # formula 2, with angle
]

print("=" * 70)
print("VERIFICATION: calculation_engine.py vs original app.py files")
print("=" * 70)

# =============================================
# Test 1: 单相接地 (LG) - ft = 0, 1, 2
# =============================================
print("\n--- 单相接地 (LG): ft = 0, 1, 2 ---")
for ft in [0, 1, 2]:
    for pi, (il_amp, il_ang, dv, if_amp, ct) in enumerate(param_sets):
        for ki, (formula, kl_p) in enumerate(kl_param_sets):
            label = f"LG ft={ft} params={pi} kl={ki} (formula={formula},dir={dv},ct={ct})"
            V_ref, I_ref = original_lg(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, kl_p, ct)
            V_test, I_test = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, kl_p, ct)
            ok = compare_outputs(label, V_ref, I_ref, V_test, I_test)
            total += 1
            if ok: passed += 1

# =============================================
# Test 2: 相间短路 (LL) - ft = 3, 4, 5
# =============================================
print("\n--- 相间短路 (LL): ft = 3, 4, 5 ---")
for ft in [3, 4, 5]:
    for pi, (il_amp, il_ang, dv, if_amp, ct) in enumerate(param_sets):
        label = f"LL ft={ft} params={pi} (dir={dv},ct={ct})"
        V_ref, I_ref = original_ll_lll(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, ct)
        # For LL/LLL, pass dummy formula/kl_params (not used)
        V_test, I_test = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, 0, {}, ct)
        ok = compare_outputs(label, V_ref, I_ref, V_test, I_test)
        total += 1
        if ok: passed += 1

# =============================================
# Test 3: 两相接地 (LLG) - ft = 6, 7, 8
# =============================================
print("\n--- 两相接地 (LLG): ft = 6, 7, 8 ---")
for ft in [6, 7, 8]:
    for pi, (il_amp, il_ang, dv, if_amp, ct) in enumerate(param_sets):
        for ki, (formula, kl_p) in enumerate(kl_param_sets):
            label = f"LLG ft={ft} params={pi} kl={ki} (formula={formula},dir={dv},ct={ct})"
            V_ref, I_ref = original_llg(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, kl_p, ct)
            V_test, I_test = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, kl_p, ct)
            ok = compare_outputs(label, V_ref, I_ref, V_test, I_test)
            total += 1
            if ok: passed += 1

# =============================================
# Test 4: 三相短路 (LLL) - ft = 9
# =============================================
print("\n--- 三相短路 (LLL): ft = 9 ---")
for pi, (il_amp, il_ang, dv, if_amp, ct) in enumerate(param_sets):
    label = f"LLL ft=9 params={pi} (dir={dv},ct={ct})"
    V_ref, I_ref = original_ll_lll(9, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, ct)
    V_test, I_test = calculate_fault(9, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, 0, {}, ct)
    ok = compare_outputs(label, V_ref, I_ref, V_test, I_test)
    total += 1
    if ok: passed += 1

# =============================================
# Summary
# =============================================
print("\n" + "=" * 70)
print(f"RESULT: {passed}/{total} tests passed")
if passed == total:
    print("✅ ALL TESTS PASSED - calculation_engine.py matches all original app.py files!")
else:
    print(f"❌ {total - passed} TESTS FAILED - differences found!")
print("=" * 70)
