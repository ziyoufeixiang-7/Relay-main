"""
calculation_engine.py — 线路保护统一相量计算引擎

涵盖四种状态的 12 通道相量计算：
  - calculate_fault()  : 故障态（10 种 ft）
  - calculate_normal() : 正常/故障前态
  - calculate_open()   : 断路器断开特殊正常态
  - pack_channels()    : 通道映射打包（vMode/iMode/Ux/并联/频率层）

输入均为 RMS 值，calculate_* 输出 RMS 复数元组。
pack_channels 负责 ×√2 峰值转换 + 12 通道字典组装，
输出直接匹配 BaseApi.physDictToReg 的输入格式。
"""

import cmath
import math

# ── 常量 ──
SQRT2 = math.sqrt(2.0)
PHASE_SHIFTS = [0.0, -120.0, 120.0]

# 故障类型 → 故障相索引集合
FT_PHASE_IDX = {
    0: {0}, 1: {1}, 2: {2},
    3: {0, 1}, 4: {1, 2}, 5: {2, 0},
    6: {0, 1}, 7: {1, 2}, 8: {2, 0},
    9: {0, 1, 2},
}
SINGLE_PHASE_GROUND = {0, 1, 2}


# ══════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════

def to_complex(mag, ang_deg):
    """幅值和角度（度）→ 复数"""
    return cmath.rect(mag, math.radians(ang_deg))


def normalize_angle(deg):
    """角度标准化到 -180° ~ 180°"""
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg


def to_polar_deg(c):
    """复数 → (幅值, 角度°)"""
    if abs(c) < 1e-9:
        return 0.0, 0.0
    return abs(c), normalize_angle(math.degrees(cmath.phase(c)))


# ══════════════════════════════════════════
# 1. 故障态计算 (FAULT)
# ══════════════════════════════════════════

def calculate_fault(
    ft, sys_v, i_load_amp, i_load_ang,
    dir_val, i_f_amp, z_mag, z_ang,
    formula, p1, p2, ct_val,
    test_type=0, delta_m=1.0, delta_formula=0
):
    """
    统一线路保护短路相量计算引擎（10 种故障类型）。

    参数:
        ft       : 故障类型 0-9
        sys_v    : 额定相电压 RMS (V)
        i_load_amp, i_load_ang : 负荷电流幅值(A)和角度(°)
        dir_val  : 方向 0=正向, 1=反向
        i_f_amp  : 短路电流幅值 RMS (A)
        z_mag, z_ang : 线路阻抗幅值(Ω)和角度(°)
        formula  : KL 公式 0=直接, 1=分量kR/kX, 2=阻抗比Z0/Z1
        p1, p2   : KL 参数（含义由 formula 决定）
        ct_val   : CT 极性 0=指向线路, 1=指向母线
        test_type: 试验类型 0=常规, 4=工频变化量
        delta_m  : 变化量系数
        delta_formula: 变化量公式 0=整定, 1=其他

    返回: ((Va,Vb,Vc), (Ia,Ib,Ic))  复数元组 RMS
    """
    E_sys = [to_complex(sys_v, shift) for shift in PHASE_SHIFTS]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in PHASE_SHIFTS]

    Z_line = to_complex(z_mag, z_ang)
    R_line, X_line = Z_line.real, Z_line.imag

    V_out = list(E_sys)
    I_fwd = [complex(0, 0)] * 3
    I_actual = [complex(0, 0)] * 3

    # ==================================================
    # 1. 单相接地故障 (LG)  ft = 0(A), 1(B), 2(C)
    # ==================================================
    if ft in (0, 1, 2):
        KL_actual = complex(0, 0)
        if formula == 1:
            kr, kx = p1, p2
            Z_loop = complex((1.0 + kr) * R_line, (1.0 + kx) * X_line)
        else:
            if formula == 0:
                KL_actual = to_complex(p1, p2)
            elif formula == 2:
                z0z1_ratio = to_complex(p1, p2)
                KL_actual = (z0z1_ratio - 1.0) / 3.0
            Z_loop = Z_line * (1.0 + KL_actual)

        z_loop_mag = abs(Z_loop)
        z_loop_ang = math.degrees(cmath.phase(Z_loop)) if z_loop_mag > 1e-6 else 0.0

        fault_phase_base_ang = PHASE_SHIFTS[ft]
        i_f_fwd_ang = fault_phase_base_ang - z_loop_ang
        I_f_fwd_phase = to_complex(i_f_amp, i_f_fwd_ang)
        
        v_drop = I_f_fwd_phase * Z_loop
        if test_type == 4:
            if dir_val == 1:
                v_drop = complex(0, 0)
            else:
                coeff = 1.05 if delta_formula == 0 else 1.3
                offset = (1.0 - coeff * delta_m) * E_sys[ft]
                v_drop += offset

        V_out[ft] = I_load[ft] * Z_line + v_drop
        I_fwd[ft] = I_f_fwd_phase

    # ==================================================
    # 2. 两相接地故障 (LLG)  ft = 6(AB), 7(BC), 8(CA)
    # ==================================================
    elif ft in (6, 7, 8):
        if ft == 6:   x, y, z_unf = 0, 1, 2
        elif ft == 7: x, y, z_unf = 1, 2, 0
        elif ft == 8: x, y, z_unf = 2, 0, 1

        if formula == 1:
            kr, kx = p1, p2
            Z_0_drop_coeff = complex(kr * R_line, kx * X_line)
            KL = Z_0_drop_coeff / Z_line if abs(Z_line) > 1e-6 else complex(0, 0)
        else:
            if formula == 0:
                KL = to_complex(p1, p2)
            elif formula == 2:
                z0z1 = to_complex(p1, p2)
                KL = (z0z1 - 1.0) / 3.0
            Z_0_drop_coeff = KL * Z_line

        U_x_target = to_complex(1.0, PHASE_SHIFTS[x])
        U_y_target = to_complex(1.0, PHASE_SHIFTS[y])

        N_x = U_x_target * (1.0 + KL) - U_y_target * KL
        N_y = U_y_target * (1.0 + KL) - U_x_target * KL

        theta_Z = z_ang
        theta_corr = math.degrees(cmath.phase(1.0 + 2.0 * KL))

        alpha = math.degrees(cmath.phase(N_x)) - theta_Z - theta_corr
        beta = math.degrees(cmath.phase(N_y)) - theta_Z - theta_corr

        I_fwd[x] = to_complex(i_f_amp, alpha)
        I_fwd[y] = to_complex(i_f_amp, beta)
        
        V_f_phase1 = I_fwd[x] * Z_line + (I_fwd[x] + I_fwd[y]) * Z_0_drop_coeff
        V_f_phase2 = I_fwd[y] * Z_line + (I_fwd[x] + I_fwd[y]) * Z_0_drop_coeff
        
        if test_type == 4:
            if dir_val == 1:
                V_f_phase1 = complex(0, 0)
                V_f_phase2 = complex(0, 0)
            else:
                coeff = 1.05 if delta_formula == 0 else 1.3
                if ft == 6: u_rated_ll = E_sys[0] - E_sys[1]
                elif ft == 7: u_rated_ll = E_sys[1] - E_sys[2]
                else: u_rated_ll = E_sys[2] - E_sys[0]
                offset = (1.0 - coeff * delta_m) * u_rated_ll
                V_f_phase1 += offset / 2.0
                V_f_phase2 -= offset / 2.0

        V_out[x] = V_f_phase1 + I_load[x] * Z_line
        V_out[y] = V_f_phase2 + I_load[y] * Z_line
        V_out[z_unf] = E_sys[z_unf]

    # ==================================================
    # 3. 相间短路 (LL)  ft = 3(AB), 4(BC), 5(CA)
    # ==================================================
    elif ft in (3, 4, 5):
        if ft == 3:   x, y, delta_xy = 0, 1, 30.0
        elif ft == 4: x, y, delta_xy = 1, 2, -90.0
        elif ft == 5: x, y, delta_xy = 2, 0, 150.0

        I_fwd[x] = to_complex(i_f_amp, delta_xy - z_ang)
        I_fwd[y] = -I_fwd[x]

        V_xy_drop = (I_fwd[x] - I_fwd[y]) * Z_line
        V_center = (E_sys[x] + E_sys[y]) / 2.0

        V_out[x] = V_center + V_xy_drop / 2.0 + I_load[x] * Z_line
        V_out[y] = V_center - V_xy_drop / 2.0 + I_load[y] * Z_line

        z_unf = [i for i in (0, 1, 2) if i not in (x, y)][0]
        V_out[z_unf] = E_sys[z_unf]

    # ==================================================
    # 4. 三相短路 (LLL)  ft = 9
    # ==================================================
    elif ft == 9:
        for p in range(3):
            I_fwd[p] = to_complex(i_f_amp, PHASE_SHIFTS[p] - z_ang)
            V_out[p] = I_fwd[p] * Z_line + I_load[p] * Z_line

    # ==================================================
    # 后期处理：方向与 CT 极性合成
    # ==================================================
    for p in range(3):
        I_actual[p] = -I_fwd[p] if dir_val == 1 else I_fwd[p]

    I_out = [complex(0, 0)] * 3
    for p in range(3):
        I_out[p] = I_load[p] + I_actual[p]

    if ct_val == 1:
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out)


# ══════════════════════════════════════════
# 2. 正常态计算 (NORMAL)
# ══════════════════════════════════════════

def calculate_normal(sys_v, i_load_amp, i_load_ang, ct_val):
    """
    正常/故障前态：三相额定电压 + 三相对称负荷电流。

    参数:
        sys_v    : 额定相电压 RMS (V)
        i_load_amp, i_load_ang : 负荷电流幅值(A)和角度(°)
        ct_val   : CT 极性 0=指向线路, 1=指向母线

    返回: ((Va,Vb,Vc), (Ia,Ib,Ic))  复数元组 RMS
    """
    V_out = [to_complex(sys_v, shift) for shift in PHASE_SHIFTS]
    I_out = [to_complex(i_load_amp, i_load_ang + shift) for shift in PHASE_SHIFTS]

    if ct_val == 1:
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out)


# ══════════════════════════════════════════
# 3. 断路器断开态计算 (OPEN)
# ══════════════════════════════════════════

def calculate_open(ft, sys_v, i_load_amp, i_load_ang, ct_val, pt_val):
    """
    断路器断开特殊正常态。

    电压规则（由 PT 安装侧决定）：
      pt=0（母线侧）: 三相始终额定
      pt=1（线路侧）: 单相接地→故障相=0、余相额定；其他→三相均=0

    电流规则（与 PT 无关）：
      单相接地(ft=0/1/2): 仅故障相=0，余相保持负荷
      其他(ft=3-9):       三相全=0

    参数:
        ft       : 故障类型 0-9
        sys_v    : 额定相电压 RMS (V)
        i_load_amp, i_load_ang : 负荷电流幅值(A)和角度(°)
        ct_val   : CT 极性 0=指向线路, 1=指向母线
        pt_val   : PT 安装侧 0=母线侧, 1=线路侧

    返回: ((Va,Vb,Vc), (Ia,Ib,Ic))  复数元组 RMS
    """
    faulted_idx = FT_PHASE_IDX.get(ft, set())
    single = ft in SINGLE_PHASE_GROUND

    # ── 电压 ──
    if pt_val == 0:
        # 母线侧 PT：三相始终额定
        V_out = [to_complex(sys_v, shift) for shift in PHASE_SHIFTS]
    else:
        # 线路侧 PT：被切除相电压=0
        tripped_idx = faulted_idx if single else {0, 1, 2}
        V_out = [
            complex(0, 0) if i in tripped_idx
            else to_complex(sys_v, PHASE_SHIFTS[i])
            for i in range(3)
        ]

    # ── 电流 ──
    if single:
        # 单相接地：仅故障相电流=0，余相保持负荷
        I_out = [
            complex(0, 0) if i in faulted_idx
            else to_complex(i_load_amp, i_load_ang + PHASE_SHIFTS[i])
            for i in range(3)
        ]
    else:
        # 多相故障：三相电流全 0
        I_out = [complex(0, 0)] * 3

    if ct_val == 1:
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out)


# ══════════════════════════════════════════
# 4. Ux 通道计算
# ══════════════════════════════════════════

def _calc_ux(V_abc, ux_cfg):
    """
    计算 Ux 通道的复数相量 (RMS)。

    ux_cfg 字段:
        type    : 0-9（见 API v2 §3.7）
        syncV   : 跳闸后线路侧抽取电压幅值 (V)
        syncAng : 跳闸后线路侧抽取电压角度 (°)
        is_open : 当前是否处于 OPEN 态 (CBOPEN / CBOPEN_HOLD)

    type 0-3（U0 类）：始终用实际 Va+Vb+Vc 计算（任何状态）
    type 4-9（检同期）：NORMAL/FAULT → 镜像相应电压；OPEN → syncV∠syncAng
    """
    t = ux_cfg.get("type", 0)
    is_open = ux_cfg.get("is_open", False)
    Va, Vb, Vc = V_abc

    # type 0-3: U0 相关 — 始终用实际电压
    if t == 0:
        return Va + Vb + Vc                          # 3U0
    elif t == 1:
        return -(Va + Vb + Vc)                       # -3U0
    elif t == 2:
        return (Va + Vb + Vc) * math.sqrt(3.0)       # 3√3·U0
    elif t == 3:
        return -(Va + Vb + Vc) * math.sqrt(3.0)      # -3√3·U0

    # type 4-9: 检同期
    if is_open:
        # OPEN 态 → 跳闸后线路侧抽取电压
        return to_complex(
            ux_cfg.get("syncV", 0.0),
            ux_cfg.get("syncAng", 0.0)
        )

    # NORMAL / FAULT 态 → 镜像相应电压
    if t == 4:
        return Va              # 检同期A
    elif t == 5:
        return Vb              # 检同期B
    elif t == 6:
        return Vc              # 检同期C
    elif t == 7:
        return Va - Vb         # 检同期AB
    elif t == 8:
        return Vb - Vc         # 检同期BC
    elif t == 9:
        return Vc - Va         # 检同期CA

    return complex(0, 0)


# ══════════════════════════════════════════
# 5. 通道映射与打包 (pack_channels)
# ══════════════════════════════════════════

def _polar_peak(c):
    """复数 RMS → [峰值幅值, 角度°]"""
    mag = abs(c) * SQRT2
    ang = normalize_angle(math.degrees(cmath.phase(c))) if mag > 1e-9 else 0.0
    return [mag, ang]


def _layer_data(c, f):
    """单通道: 根据频率决定 layer 结构"""
    if abs(f - 50.0) < 0.01:
        return {"1": _polar_peak(c)}
    else:
        # f≠50Hz: layer 0 = [DC幅度=0, 频率=f], layer 1 = 交流
        return {"0": [0.0, f], "1": _polar_peak(c)}


def _zero_layer(f):
    """零值通道"""
    return _layer_data(complex(0, 0), f)


def pack_channels(V_abc, I_abc, *, vMode=0, iMode=0, ux_cfg=None, f=50.0):
    """
    将 ABC 三相 RMS 复数相量打包为 12 通道字典。

    参数:
        V_abc   : (Va, Vb, Vc) 复数元组 (RMS)
        I_abc   : (Ia, Ib, Ic) 复数元组 (RMS)
        vMode   : 0=ABC(通道0-2), 1=XYZ(通道3-5)
        iMode   : 0=ABC(通道6-8), 1=XYZ(通道9-11), 2=并联(6-8+9-11各÷2)
        ux_cfg  : None 或 {"en":bool, "type":int, "syncV":float,
                           "syncAng":float, "is_open":bool}
        f       : 频率 (Hz)

    返回: {"ch_str": {"layer_str": [peak_amp, phase_deg]}}
           直接匹配 BaseApi.physDictToReg 输入格式
    """
    result = {}

    # ── 初始化所有 12 通道为 0 ──
    for ch in range(12):
        result[str(ch)] = _zero_layer(f)

    # ── 电压通道 ──
    v_offset = 0 if vMode == 0 else 3
    for i in range(3):
        result[str(v_offset + i)] = _layer_data(V_abc[i], f)

    # ── 电流通道 ──
    if iMode == 0:       # ABC → 6,7,8
        for i in range(3):
            result[str(6 + i)] = _layer_data(I_abc[i], f)
    elif iMode == 1:     # XYZ → 9,10,11
        for i in range(3):
            result[str(9 + i)] = _layer_data(I_abc[i], f)
    elif iMode == 2:     # 并联：6-8 和 9-11 各 ÷2
        for i in range(3):
            half = I_abc[i] / 2.0
            data = _layer_data(half, f)
            result[str(6 + i)] = data
            result[str(9 + i)] = _layer_data(half, f)  # 独立创建避免引用共享

    # ── Ux 特殊通道 ──
    if ux_cfg and ux_cfg.get("en"):
        ux_ch = 3 if vMode == 0 else 0   # 占用"另一组"电压的第一个通道
        ux_val = _calc_ux(V_abc, ux_cfg)
        result[str(ux_ch)] = _layer_data(ux_val, f)

    return result
