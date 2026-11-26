import cmath
import math

def to_complex(mag, ang_deg):
    return cmath.rect(mag, math.radians(ang_deg))

def normalize_angle(deg):
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg

def calculate_fault(
    ft, sys_v, i_load_amp, i_load_ang, 
    dir_val, i_f_amp, z_mag, z_ang, 
    formula, kl_params, ct_val
):
    """
    统一线路保护短路相量计算引擎 (涵盖单相接地、两相接地、相间短路、三相短路)
    """
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    
    Z_line = to_complex(z_mag, z_ang)
    R_line, X_line = Z_line.real, Z_line.imag

    V_out = list(E_sys)
    I_fwd = [complex(0,0)] * 3
    I_actual = [complex(0,0)] * 3

    # ==================================================
    # 1. 单相接地故障 (LG)
    # ft = 0(A), 1(B), 2(C)
    # ==================================================
    if ft in [0, 1, 2]:
        KL_actual = complex(0, 0)
        if formula == 1:
            kr, kx = kl_params.get('kr', 0.0), kl_params.get('kx', 0.0)
            Z_loop = complex((1.0 + kr) * R_line, (1.0 + kx) * X_line)
        else:
            if formula == 0:
                KL_actual = to_complex(kl_params.get('kl_amp', 0.0), kl_params.get('kl_ang', 0.0))
            elif formula == 2:
                z0z1_ratio = to_complex(kl_params.get('z0z1_amp', 1.0), kl_params.get('z0z1_ang', 0.0))
                KL_actual = (z0z1_ratio - 1.0) / 3.0
            Z_loop = Z_line * (1.0 + KL_actual)

        z_loop_mag = abs(Z_loop)
        z_loop_ang = math.degrees(cmath.phase(Z_loop)) if z_loop_mag > 1e-6 else 0.0
        
        fault_phase_base_ang = phase_shifts[ft]
        i_f_fwd_ang = fault_phase_base_ang - z_loop_ang
        I_f_fwd_phase = to_complex(i_f_amp, i_f_fwd_ang)

        V_out[ft] = I_load[ft] * Z_line + I_f_fwd_phase * Z_loop
        I_fwd[ft] = I_f_fwd_phase

    # ==================================================
    # 2. 两相接地故障 (LLG)
    # ft = 6(AB), 7(BC), 8(CA)
    # ==================================================
    elif ft in [6, 7, 8]:
        if ft == 6:   x, y, z_unf = 0, 1, 2
        elif ft == 7: x, y, z_unf = 1, 2, 0
        elif ft == 8: x, y, z_unf = 2, 0, 1

        if formula == 1:
            kr, kx = kl_params.get('kr', 0.0), kl_params.get('kx', 0.0)
            Z_0_drop_coeff = complex(kr * R_line, kx * X_line)
            KL = Z_0_drop_coeff / Z_line if abs(Z_line) > 1e-6 else complex(0, 0)
        else:
            if formula == 0:
                KL = to_complex(kl_params.get('kl_amp', 0.0), kl_params.get('kl_ang', 0.0))
            elif formula == 2:
                z0z1 = to_complex(kl_params.get('z0z1_amp', 1.0), kl_params.get('z0z1_ang', 0.0))
                KL = (z0z1 - 1.0) / 3.0
            Z_0_drop_coeff = KL * Z_line

        U_x_target = to_complex(1.0, phase_shifts[x])
        U_y_target = to_complex(1.0, phase_shifts[y])

        N_x = U_x_target * (1.0 + KL) - U_y_target * KL
        N_y = U_y_target * (1.0 + KL) - U_x_target * KL

        theta_Z = z_ang
        theta_corr = math.degrees(cmath.phase(1.0 + 2.0 * KL))

        alpha = math.degrees(cmath.phase(N_x)) - theta_Z - theta_corr
        beta  = math.degrees(cmath.phase(N_y)) - theta_Z - theta_corr

        I_fwd[x] = to_complex(i_f_amp, alpha)
        I_fwd[y] = to_complex(i_f_amp, beta)
        I_0_3 = I_fwd[x] + I_fwd[y]

        V_0_drop = I_0_3 * Z_0_drop_coeff
        V_out[x] = I_fwd[x] * Z_line + V_0_drop + I_load[x] * Z_line
        V_out[y] = I_fwd[y] * Z_line + V_0_drop + I_load[y] * Z_line
        V_out[z_unf] = E_sys[z_unf]

    # ==================================================
    # 3. 相间短路 (LL)
    # ft = 3(AB), 4(BC), 5(CA)
    # ==================================================
    elif ft in [3, 4, 5]:
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

    # ==================================================
    # 4. 三相短路 (LLL)
    # ft = 9
    # ==================================================
    elif ft == 9:
        for p in range(3):
            I_fwd[p] = to_complex(i_f_amp, phase_shifts[p] - z_ang)
            V_out[p] = I_fwd[p] * Z_line + I_load[p] * Z_line

    # ==================================================
    # 后期处理：方向与 CT 极性合成
    # ==================================================
    for p in range(3):
        I_actual[p] = -I_fwd[p] if dir_val == 1 else I_fwd[p]

    I_out = [complex(0,0)] * 3
    for p in range(3):
        I_out[p] = I_load[p] + I_actual[p]

    if ct_val == 1:
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out)
