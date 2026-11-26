import streamlit as st
import cmath
import math

# ==========================================
# 1. 基础数学工具函数
# ==========================================
def to_complex(mag, ang_deg):
    """幅值和角度转复数"""
    return cmath.rect(mag, math.radians(ang_deg))

def normalize_angle(deg):
    """角度标准化到 -180 ~ +180"""
    deg = (deg + 180) % 360 - 180
    return 180.0 if deg == -180.0 else deg

def to_polar_deg(c):
    """复数转幅值和角度"""
    if abs(c) < 1e-6: return 0.0, 0.0
    return abs(c), normalize_angle(math.degrees(cmath.phase(c)))

# ==========================================
# 2. 商业级核心算法引擎 (矩阵逆解相角投影法)
# ==========================================
def calculate_llg_commercial_standard(
    ft, sys_v, i_load_amp, i_load_ang, 
    dir_val, i_f_amp, z_mag, z_ang, 
    formula, kl_params, ct_val
):
    """
    针对两相接地 (LLG) 的终极商业级恒流仿真模型。
    无论KL角度如何，保证电流幅值绝对等于 If。
    电压角度偏移完全由反解投影自然生成。
    """
    # 1. 系统三相基准
    phase_shifts = [0.0, -120.0, 120.0]
    E_sys = [to_complex(sys_v, shift) for shift in phase_shifts]
    I_load = [to_complex(i_load_amp, i_load_ang + shift) for shift in phase_shifts]
    
    Z_line = to_complex(z_mag, z_ang)
    R_line, X_line = Z_line.real, Z_line.imag

    # 根据 ft 映射故障相 (x, y) 和非故障相 (z_unf)
    if ft == 6:   x, y, z_unf = 0, 1, 2  # AB接地
    elif ft == 7: x, y, z_unf = 1, 2, 0  # BC接地
    elif ft == 8: x, y, z_unf = 2, 0, 1  # CA接地

    # 2. 解析真实的复数 KL
    if formula == 1:
        # 分量法：单独计算零序压降算子，并推导等效 KL 用于角度投影
        kr, kx = kl_params.get('kr', 0.0), kl_params.get('kx', 0.0)
        Z_0_drop = complex(kr * R_line, kx * X_line)
        KL = Z_0_drop / Z_line if abs(Z_line) > 1e-6 else complex(0, 0)
    else:
        # 复数法
        if formula == 0:
            KL = to_complex(kl_params.get('kl_amp', 0.0), kl_params.get('kl_ang', 0.0))
        elif formula == 2:
            z0z1 = to_complex(kl_params.get('z0z1_amp', 1.0), kl_params.get('z0z1_ang', 0.0))
            KL = (z0z1 - 1.0) / 3.0
        Z_0_drop = KL * Z_line

    # 3. 商业核心机密：构建理想目标电压相量
    U_x_target = to_complex(1.0, phase_shifts[x])
    U_y_target = to_complex(1.0, phase_shifts[y])

    # 4. 矩阵逆解算分子的复数向量：N_x = U_x(1+KL) - U_y(KL)
    N_x = U_x_target * (1.0 + KL) - U_y_target * KL
    N_y = U_y_target * (1.0 + KL) - U_x_target * KL

    # 5. 提取理想相角，扣除阻抗角和公共偏转角，强制赋给 5A 恒流源
    theta_Z = z_ang
    theta_corr = math.degrees(cmath.phase(1.0 + 2.0 * KL)) # 公共修正角

    alpha = math.degrees(cmath.phase(N_x)) - theta_Z - theta_corr
    beta  = math.degrees(cmath.phase(N_y)) - theta_Z - theta_corr

    # 生成正向纯故障电流
    I_x_fwd = to_complex(i_f_amp, alpha)
    I_y_fwd = to_complex(i_f_amp, beta)
    I_0_3 = I_x_fwd + I_y_fwd

    # 6. 计算真正的零序压降与叠加电压 (恒定正向压降模型)
    V_0_drop = I_0_3 * Z_0_drop
    V_out = list(E_sys)
    V_out[x] = I_x_fwd * Z_line + V_0_drop + I_load[x] * Z_line
    V_out[y] = I_y_fwd * Z_line + V_0_drop + I_load[y] * Z_line
    V_out[z_unf] = E_sys[z_unf] # 非故障相

    # 7. 处理故障方向 (反向则翻转纯短路电流分量)
    I_x_actual = -I_x_fwd if dir_val == 1 else I_x_fwd
    I_y_actual = -I_y_fwd if dir_val == 1 else I_y_fwd

    # 8. 叠加负荷电流，处理 CT 极性
    I_out = list(I_load)
    I_out[x] = I_x_actual + I_load[x]
    I_out[y] = I_y_actual + I_load[y]

    if ct_val == 1: # 指向母线，全部倒相
        I_out = [-i for i in I_out]

    return tuple(V_out), tuple(I_out), I_0_3

# ==========================================
# 3. Streamlit UI 交互界面
# ==========================================
def main():
    st.set_page_config(page_title="线路保护 LLG 商业完全体", layout="wide")
    st.title("⚡ LLG 两相接地短路引擎 (商业级完全体)")
    st.markdown("🎯 集成了所有完整参数配置，采用逆矩阵投影算法完美对标各大商用测试仪的数据。")

    # --- 侧边栏：完整参数组 ---
    st.sidebar.header("1. 系统与负荷")
    sys_v = st.sidebar.number_input("相电压 Vnom (V)", value=57.735, step=1.0)
    i_load_amp = st.sidebar.number_input("负荷幅值 (A)", value=0.0, step=0.1)
    i_load_ang = st.sidebar.number_input("负荷角度 (°)", value=0.0, step=1.0)

    st.sidebar.header("2. 故障设定 (LLG)")
    ft_map = {"AB两相接地 (ft=6)": 6, "BC两相接地 (ft=7)": 7, "CA两相接地 (ft=8)": 8}
    ft_str = st.sidebar.selectbox("选择故障相", options=list(ft_map.keys()))
    ft = ft_map[ft_str]

    dir_val = st.sidebar.radio("故障方向", options=[0, 1], format_func=lambda x: "0: 正向" if x==0 else "1: 反向")
    i_val = st.sidebar.number_input("短路电流 If (A)", value=5.0, step=0.1)
    z_val = st.sidebar.number_input("阻抗 Z (Ω)", value=2.0, step=0.1)
    ang_val = st.sidebar.number_input("阻抗角 (°)", value=75.0, step=1.0)

    st.sidebar.header("3. 零序补偿 (KL)")
    formula = st.sidebar.radio("KL计算公式", options=[0, 1, 2], format_func=lambda x: ["0: 直接KL", "1: 分量Kr/Kx", "2: 阻抗比Z0/Z1"][x])
    kl_params = {}
    if formula == 0:
        kl_params['kl_amp'] = st.sidebar.number_input("KL幅值", value=0.667, step=0.001)
        kl_params['kl_ang'] = st.sidebar.number_input("KL角度 (°)", value=33.0, step=1.0)
    elif formula == 1:
        kl_params['kr'] = st.sidebar.number_input("Kr", value=0.667, step=0.001) 
        kl_params['kx'] = st.sidebar.number_input("Kx", value=0.667, step=0.001) 
    elif formula == 2:
        kl_params['z0z1_amp'] = st.sidebar.number_input("Z0/Z1幅值", value=3.0, step=0.1)
        kl_params['z0z1_ang'] = st.sidebar.number_input("Z0/Z1角度 (°)", value=0.0, step=1.0)

    st.sidebar.header("4. 硬件配置")
    ct_val = st.sidebar.radio("CT极性", options=[0, 1], format_func=lambda x: "0: 指向线路" if x==0 else "1: 指向母线")

    # --- 执行终极计算 ---
    V_out, I_out, I_0_3 = calculate_llg_commercial_standard(
        ft, sys_v, i_load_amp, i_load_ang, dir_val, i_val, z_val, ang_val, formula, kl_params, ct_val
    )

    def format_out(c):
        mag, ang = to_polar_deg(c)
        return f"**{mag:7.4f}**", f"{ang:7.4f}°"

    # --- 展示计算结果 ---
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

    st.divider()

    # --- 闭环验证区 (距离保护元件视角) ---
    st.subheader("🔍 闭环验证 (保护装置视角)")
    
    x, y = [(0,1), (1,2), (2,0)][ft-6]
    ph_names = ["A", "B", "C"]
    
    try:
        # 1. 验证相间距离元件
        Z_xy = (V_out[x] - V_out[y]) / (I_out[x] - I_out[y])
        m_xy, a_xy = to_polar_deg(Z_xy)
        st.success(f"📌 **{ph_names[x]}{ph_names[y]} 相间距离元件** 测量阻抗: **{m_xy:.4f} Ω ∠ {a_xy:.4f}°**")

        # 2. 验证接地距离元件
        if formula != 1:
            KL_act = (to_complex(kl_params['z0z1_amp'], kl_params['z0z1_ang']) - 1)/3 if formula==2 else to_complex(kl_params['kl_amp'], kl_params['kl_ang'])
            
            # 使用保护装置通常获取的实际 3I0 (三相电流和)
            I0_relay = I_out[0] + I_out[1] + I_out[2]
            
            Z_x_relay = V_out[x] / (I_out[x] + I0_relay * KL_act)
            m_x, a_x = to_polar_deg(Z_x_relay)
            st.success(f"📌 **{ph_names[x]} 相接地距离元件** 测量阻抗: **{m_x:.4f} Ω ∠ {a_x:.4f}°**")
            
            Z_y_relay = V_out[y] / (I_out[y] + I0_relay * KL_act)
            m_y, a_y = to_polar_deg(Z_y_relay)
            st.success(f"📌 **{ph_names[y]} 相接地距离元件** 测量阻抗: **{m_y:.4f} Ω ∠ {a_y:.4f}°**")

    except Exception as e:
        st.warning(f"无法校验: {e}")

if __name__ == '__main__':
    main()