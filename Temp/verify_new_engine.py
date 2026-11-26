"""
验证新版 calculation_engine.py（统一 p1/p2 格式）。
1. 对比 calculate_fault 与原始 CAL 版本（320 组参数）
2. 测试 calculate_normal
3. 测试 calculate_open (pt=0/1 × 单相/多相)
4. 测试 pack_channels (vMode/iMode/Ux/并联/频率)
"""
import sys, os, math, cmath

# 添加新引擎路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "Relay", "RelayProtection"))
from calc.calculation_engine import (
    calculate_fault, calculate_normal, calculate_open,
    pack_channels, _calc_ux, to_complex, to_polar_deg, normalize_angle,
    SQRT2, PHASE_SHIFTS
)

# 原始引擎 (用于交叉对比 calculate_fault)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "CAL"))
import calculation_engine as old_engine

total = 0
passed = 0

def check(label, ok):
    global total, passed
    total += 1
    if ok:
        passed += 1
        print(f"✅ PASS: {label}")
    else:
        print(f"❌ FAIL: {label}")
    return ok

def complex_close(a, b, tol=1e-6):
    return abs(a - b) < tol

def tuples_close(ref, test, tol=1e-6):
    return all(complex_close(r, t, tol) for r, t in zip(ref, test))

# ══════════════════════════════════════════
# TEST 1: calculate_fault 与原始引擎对比
# ══════════════════════════════════════════
print("=" * 70)
print("TEST 1: calculate_fault — p1/p2 格式 vs 原始 kl_params 格式")
print("=" * 70)

sys_v = 57.735
z_mag, z_ang = 2.0, 75.0

# (formula, p1, p2, old_kl_params)
kl_sets = [
    (0, 0.667, 0.0,   {'kl_amp': 0.667, 'kl_ang': 0.0}),
    (0, 0.667, 33.0,  {'kl_amp': 0.667, 'kl_ang': 33.0}),
    (1, 0.2, 0.8,     {'kr': 0.2, 'kx': 0.8}),
    (1, 0.667, 0.667, {'kr': 0.667, 'kx': 0.667}),
    (2, 3.0, 0.0,     {'z0z1_amp': 3.0, 'z0z1_ang': 0.0}),
    (2, 3.0, 15.0,    {'z0z1_amp': 3.0, 'z0z1_ang': 15.0}),
]

param_sets = [
    (0.0, 0.0, 0, 5.0, 0),
    (0.0, 0.0, 1, 5.0, 0),
    (2.0, -30.0, 0, 5.0, 0),
    (2.0, -30.0, 0, 5.0, 1),
]

# LG
for ft in [0, 1, 2]:
    for il_amp, il_ang, dv, if_amp, ct in param_sets:
        for formula, p1, p2, old_kl in kl_sets:
            V_new, I_new = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, p1, p2, ct)
            V_old, I_old = old_engine.calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, old_kl, ct)
            ok = tuples_close(V_old, V_new) and tuples_close(I_old, I_new)
            check(f"LG ft={ft} f={formula} p=({il_amp},{dv},{ct})", ok)

# LL / LLL (no KL)
for ft in [3, 4, 5, 9]:
    for il_amp, il_ang, dv, if_amp, ct in param_sets:
        V_new, I_new = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, 0, 0.0, 0.0, ct)
        V_old, I_old = old_engine.calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, 0, {}, ct)
        ok = tuples_close(V_old, V_new) and tuples_close(I_old, I_new)
        check(f"LL/LLL ft={ft} p=({il_amp},{dv},{ct})", ok)

# LLG
for ft in [6, 7, 8]:
    for il_amp, il_ang, dv, if_amp, ct in param_sets:
        for formula, p1, p2, old_kl in kl_sets:
            V_new, I_new = calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, p1, p2, ct)
            V_old, I_old = old_engine.calculate_fault(ft, sys_v, il_amp, il_ang, dv, if_amp, z_mag, z_ang, formula, old_kl, ct)
            ok = tuples_close(V_old, V_new) and tuples_close(I_old, I_new)
            check(f"LLG ft={ft} f={formula} p=({il_amp},{dv},{ct})", ok)

# ══════════════════════════════════════════
# TEST 2: calculate_normal
# ══════════════════════════════════════════
print("\n" + "=" * 70)
print("TEST 2: calculate_normal")
print("=" * 70)

# 基础：三相额定电压 + 负荷电流
V, I = calculate_normal(57.735, 0.0, 0.0, 0)
check("normal no-load: Va=57.735∠0", complex_close(V[0], to_complex(57.735, 0.0)))
check("normal no-load: Vb=57.735∠-120", complex_close(V[1], to_complex(57.735, -120.0)))
check("normal no-load: Vc=57.735∠120", complex_close(V[2], to_complex(57.735, 120.0)))
check("normal no-load: Ia=0", complex_close(I[0], complex(0, 0)))

V, I = calculate_normal(57.735, 2.0, -30.0, 0)
check("normal with-load: Ia=2∠-30", complex_close(I[0], to_complex(2.0, -30.0)))
check("normal with-load: Ib=2∠-150", complex_close(I[1], to_complex(2.0, -150.0)))
check("normal with-load: Ic=2∠90", complex_close(I[2], to_complex(2.0, 90.0)))

# CT 极性
V, I = calculate_normal(57.735, 2.0, -30.0, 1)
check("normal ct=1: Ia=-2∠-30", complex_close(I[0], -to_complex(2.0, -30.0)))

# ══════════════════════════════════════════
# TEST 3: calculate_open
# ══════════════════════════════════════════
print("\n" + "=" * 70)
print("TEST 3: calculate_open")
print("=" * 70)

# pt=0 母线侧：三相始终额定
V, I = calculate_open(0, 57.735, 2.0, -30.0, 0, 0)
check("open pt=0 ft=0: Va=rated", complex_close(V[0], to_complex(57.735, 0.0)))
check("open pt=0 ft=0: Ia=0 (故障相)", complex_close(I[0], complex(0, 0)))
check("open pt=0 ft=0: Ib=load (非故障)", complex_close(I[1], to_complex(2.0, -150.0)))

V, I = calculate_open(3, 57.735, 2.0, -30.0, 0, 0)
check("open pt=0 ft=3: Va=rated", complex_close(V[0], to_complex(57.735, 0.0)))
check("open pt=0 ft=3: Ia=0 (多相全0)", complex_close(I[0], complex(0, 0)))
check("open pt=0 ft=3: Ib=0 (多相全0)", complex_close(I[1], complex(0, 0)))

# pt=1 线路侧 + 单相接地
V, I = calculate_open(0, 57.735, 2.0, -30.0, 0, 1)
check("open pt=1 ft=0: Va=0 (故障相)", complex_close(V[0], complex(0, 0)))
check("open pt=1 ft=0: Vb=rated (非故障)", complex_close(V[1], to_complex(57.735, -120.0)))
check("open pt=1 ft=0: Vc=rated (非故障)", complex_close(V[2], to_complex(57.735, 120.0)))

# pt=1 线路侧 + 多相
V, I = calculate_open(3, 57.735, 2.0, -30.0, 0, 1)
check("open pt=1 ft=3: Va=0 (全跳)", complex_close(V[0], complex(0, 0)))
check("open pt=1 ft=3: Vb=0 (全跳)", complex_close(V[1], complex(0, 0)))
check("open pt=1 ft=3: Vc=0 (全跳)", complex_close(V[2], complex(0, 0)))

# pt=1 + 三相短路
V, I = calculate_open(9, 57.735, 2.0, -30.0, 0, 1)
check("open pt=1 ft=9: all V=0", all(complex_close(v, complex(0,0)) for v in V))
check("open pt=1 ft=9: all I=0", all(complex_close(i, complex(0,0)) for i in I))

# B 相接地 pt=1
V, I = calculate_open(1, 57.735, 2.0, -30.0, 0, 1)
check("open pt=1 ft=1: Va=rated", complex_close(V[0], to_complex(57.735, 0.0)))
check("open pt=1 ft=1: Vb=0", complex_close(V[1], complex(0, 0)))
check("open pt=1 ft=1: Ia=load", complex_close(I[0], to_complex(2.0, -30.0)))
check("open pt=1 ft=1: Ib=0", complex_close(I[1], complex(0, 0)))
check("open pt=1 ft=1: Ic=load", complex_close(I[2], to_complex(2.0, 90.0)))

# CT 极性
V, I = calculate_open(0, 57.735, 2.0, -30.0, 1, 0)
check("open ct=1: Ib=-load", complex_close(I[1], -to_complex(2.0, -150.0)))

# ══════════════════════════════════════════
# TEST 4: pack_channels
# ══════════════════════════════════════════
print("\n" + "=" * 70)
print("TEST 4: pack_channels")
print("=" * 70)

Va = to_complex(57.735, 0.0)
Vb = to_complex(57.735, -120.0)
Vc = to_complex(57.735, 120.0)
Ia = to_complex(5.0, -75.0)
Ib = to_complex(5.0, -195.0)
Ic = to_complex(5.0, 45.0)

# vMode=0, iMode=0
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, f=50.0)
check("pack vMode=0: ch0=Va peak", abs(d["0"]["1"][0] - 57.735*SQRT2) < 0.01)
check("pack vMode=0: ch3=0 (unused)", abs(d["3"]["1"][0]) < 0.01)
check("pack iMode=0: ch6=Ia peak", abs(d["6"]["1"][0] - 5.0*SQRT2) < 0.01)
check("pack iMode=0: ch9=0 (unused)", abs(d["9"]["1"][0]) < 0.01)

# vMode=1
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=1, iMode=0, f=50.0)
check("pack vMode=1: ch0=0 (unused)", abs(d["0"]["1"][0]) < 0.01)
check("pack vMode=1: ch3=Va peak", abs(d["3"]["1"][0] - 57.735*SQRT2) < 0.01)

# iMode=1
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=1, f=50.0)
check("pack iMode=1: ch6=0", abs(d["6"]["1"][0]) < 0.01)
check("pack iMode=1: ch9=Ia peak", abs(d["9"]["1"][0] - 5.0*SQRT2) < 0.01)

# iMode=2 并联 (各÷2)
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=2, f=50.0)
check("pack parallel: ch6=Ia/2 peak", abs(d["6"]["1"][0] - 2.5*SQRT2) < 0.01)
check("pack parallel: ch9=Ia/2 peak", abs(d["9"]["1"][0] - 2.5*SQRT2) < 0.01)
check("pack parallel: ch7=Ib/2 peak", abs(d["7"]["1"][0] - 2.5*SQRT2) < 0.01)
check("pack parallel: ch10=Ib/2 peak", abs(d["10"]["1"][0] - 2.5*SQRT2) < 0.01)

# f≠50: 有 layer 0
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, f=60.0)
check("pack f=60: layer 0 present", "0" in d["0"])
check("pack f=60: layer 0 = [0, 60]", d["0"]["0"] == [0.0, 60.0])
check("pack f=60: layer 1 present", "1" in d["0"])

# f=50: 无 layer 0
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, f=50.0)
check("pack f=50: no layer 0", "0" not in d["0"])

# Ux vMode=0 → ch3
ux_cfg = {"en": True, "type": 4, "syncV": 0, "syncAng": 0, "is_open": False}
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, ux_cfg=ux_cfg, f=50.0)
check("pack Ux vMode=0: ch3=Va (检同期A)", abs(d["3"]["1"][0] - 57.735*SQRT2) < 0.01)

# Ux vMode=1 → ch0
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=1, iMode=0, ux_cfg=ux_cfg, f=50.0)
check("pack Ux vMode=1: ch0=Va (检同期A)", abs(d["0"]["1"][0] - 57.735*SQRT2) < 0.01)

# Ux type=0 (3U0) — 正常态三相对称 → U0≈0
ux_cfg_u0 = {"en": True, "type": 0, "is_open": False}
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, ux_cfg=ux_cfg_u0, f=50.0)
check("pack Ux type=0 normal: 3U0≈0", abs(d["3"]["1"][0]) < 0.1)

# Ux type=4 OPEN → syncV
ux_cfg_open = {"en": True, "type": 4, "syncV": 30.0, "syncAng": 45.0, "is_open": True}
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, ux_cfg=ux_cfg_open, f=50.0)
check("pack Ux type=4 open: syncV", abs(d["3"]["1"][0] - 30.0*SQRT2) < 0.01)

# Ux type=7 (检同期AB = Va-Vb)
ux_cfg_ab = {"en": True, "type": 7, "is_open": False}
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, ux_cfg=ux_cfg_ab, f=50.0)
expected_ab = abs(Va - Vb) * SQRT2
check("pack Ux type=7: |Va-Vb| peak", abs(d["3"]["1"][0] - expected_ab) < 0.01)

# 12 通道完整性
d = pack_channels((Va,Vb,Vc), (Ia,Ib,Ic), vMode=0, iMode=0, f=50.0)
check("pack: all 12 channels present", all(str(ch) in d for ch in range(12)))

# ══════════════════════════════════════════
# Summary
# ══════════════════════════════════════════
print("\n" + "=" * 70)
print(f"RESULT: {passed}/{total} tests passed")
if passed == total:
    print("✅ ALL TESTS PASSED!")
else:
    print(f"❌ {total - passed} TESTS FAILED!")
print("=" * 70)
