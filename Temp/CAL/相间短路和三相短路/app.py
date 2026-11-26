import streamlit as st
import cmath
import math

# ==========================================
# 1. 基础数学工具函数
# ==========================================
def to_complex(mag, ang_deg):
    return cmath.rect(mag, math.radians(ang_deg))

def normalize_angle(deg):
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg

def to_polar_deg(c):
    if abs(c) < 1e-6: return 0.0, 0.0
    return abs(c), normalize_angle(math.degrees(cmath.phase(c)))

# ==========================================
# 2. 相间 (LL) 与 三相 (LLL) 商用对标引擎
# ==========================================
def calculate_ll_lll_fault(
    ft, sys_v, i_load_amp, i_load_ang, 
    dir_val, i_f_amp, z_mag, z_ang, ct_val
):
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    
    Z_line = to_complex(z_mag, z_ang)

    I_fwd = [complex(0, 0), complex(0, 0), complex(0, 0)]
    V_out = list(E_sys)

    # ==================================================
    # 逻辑分支 A：相间短路 (LL)
    # ==================================================
    if ft in [3, 4, 5]:
        if ft == 3:   x, y, delta_xy = 0, 1, 30.0    # AB 相间
        elif ft == 4: x, y, delta_xy = 1, 2, -90.0   # BC 相间
        elif ft == 5: x, y, delta_xy = 2, 0, 150.0   # CA 相间
        
        # 1. 纯短路电流分配
        I_fwd[x] = to_complex(i_f_amp, delta_xy - z_ang)
        I_fwd[y] = -I_fwd[x]

        # 2. 线电压总压降
        V_xy_drop = (I_fwd[x] - I_fwd[y]) * Z_line

        # 3. 几何中心对称跌落
        V_center = (E_sys[x] + E_sys[y]) / 2.0

        # 4. 故障相叠加负荷压降
        V_out[x] = V_center + V_xy_drop / 2.0 + I_load[x] * Z_line
        V_out[y] = V_center - V_xy_drop / 2.0 + I_load[y] * Z_line
        
        # 5. 【核心修正】非故障相绝对钳位！(舍弃负荷压降)
        z_unf = [i for i in [0,1,2] if i not in [x,y]][0]
        V_out[z_unf] = E_sys[z_unf] # <--- 之前这里加了 + I_load[z_unf]*Z_line，现在严格等于电源电压

    # ==================================================
    # 逻辑分支 B：三相短路 (LLL)
    # ==================================================
    elif ft == 9:
        for p in range(3):
            # 三相都故障，三相都要叠加负荷压降
            I_fwd[p] = to_complex(i_f_amp, phase_shifts[p] - z_ang)
            V_out[p] = I_fwd[p] * Z_line + I_load[p] * Z_line

    # ==================================================
    # 后期处理：方向控制与 CT极性
    # ==================================================
    I_actual = [(-i if dir_val == 1 else i) for i in I_fwd]

    I_out = list(I_load)
    for p in range(3):
        I_out[p] = I_load[p] + I_actual[p]

    if ct_val == 1:
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out)

# ==========================================
# 3. Streamlit UI 交互界面
# ==========================================
def main():
    st.set_page_config(page_title="线路保护 LL/LLL (非故障相钳位版)", layout="wide")
    st.title("⚡ 相间短路 (LL) - 非故障相钳位修正版")
    st.markdown("🎯 **终极修复**：引入了商用仪器的“非故障相电压绝对钳位法则”。无论加多大的负荷电流，未发生故障的相（如 AB相间的 C相）的电压永远被强行锁死在额定值（$57.735V \\angle 120^\\circ$ 等），以防保护误动！")

    st.sidebar.header("1. 系统与负荷")
    sys_v = st.sidebar.number_input("相电压 Vnom (V)", value=57.735, step=1.0)
    i_load_amp = st.sidebar.number_input("负荷幅值 (A)", value=0.0, step=0.1)
    i_load_ang = st.sidebar.number_input("负荷角度 (°)", value=0.0, step=1.0)

    st.sidebar.header("2. 故障设定 (无零序)")
    ft_map = {"AB相间 (ft=3)": 3, "BC相间 (ft=4)": 4, "CA相间 (ft=5)": 5, "ABC三相短路 (ft=9)": 9}
    ft_str = st.sidebar.selectbox("选择故障类型", options=list(ft_map.keys()))
    ft = ft_map[ft_str]

    dir_val = st.sidebar.radio("故障方向", options=[0, 1], format_func=lambda x: "0: 正向" if x==0 else "1: 反向")
    i_val = st.sidebar.number_input("短路电流 If (A)", value=5.0, step=0.1)
    z_val = st.sidebar.number_input("阻抗 Z (Ω)", value=2.0, step=0.1)
    ang_val = st.sidebar.number_input("阻抗角 (°)", value=75.0, step=1.0)

    st.sidebar.header("3. 硬件配置")
    ct_val = st.sidebar.radio("CT极性", options=[0, 1], format_func=lambda x: "0: 指向线路" if x==0 else "1: 指向母线")

    # --- 执行计算 ---
    V_out, I_out = calculate_ll_lll_fault(
        ft, sys_v, i_load_amp, i_load_ang, dir_val, i_val, z_val, ang_val, ct_val
    )

    def format_out(c):
        mag, ang = to_polar_deg(c)
        return f"**{mag:7.4f}**", f"{ang:7.4f}°"

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("⚡ 硬件通道: 电压相量 (V)")
        st.markdown(f"- **Va:** 幅值 {format_out(V_out[0])[0]} V, 角度 {format_out(V_out[0])[1]}")
        st.markdown(f"- **Vb:** 幅值 {format_out(V_out[1])[0]} V, 角度 {format_out(V_out[1])[1]}")
        st.markdown(f"- **Vc:** 幅值 {format_out(V_out[2])[0]} V, 角度 {format_out(V_out[2])[1]}")
    with col2:
        st.subheader("🔌 硬件通道: 电流相量 (A)")
        st.markdown(f"- **Ia:** 幅值 {format_out(I_out[0])[0]} A, 角度 {format_out(I_out[0])[1]}")
        st.markdown(f"- **Ib:** 幅值 {format_out(I_out[1])[0]} A, 角度 {format_out(I_out[1])[1]}")
        st.markdown(f"- **Ic:** 幅值 {format_out(I_out[2])[0]} A, 角度 {format_out(I_out[2])[1]}")

if __name__ == '__main__':
    main()