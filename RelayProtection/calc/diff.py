import math

def _solve_quadratic(a: float, b: float, c: float) -> float:
    """
    求解一元二次方程 a*x^2 + b*x + c = 0，并返回正实根。
    """
    if a == 0:
        if b == 0:
            return 0.0
        return -c / b
    discriminant = b**2 - 4 * a * c
    if discriminant < 0:
        return 0.0
    return (-b + math.sqrt(discriminant)) / (2 * a)

def _solve_sum_diff(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    精确求解器：
    对于大多数常用的差动方程组合 (例如 id_sum + ir_diff_k 或 id_diff + ir_sum_k)，
    内部变量满足:
      i1 - i2 = id
      i1 + i2 = ir * k
    解方程得：
      i1 = (ir * k + id) / 2
      i2 = (ir * k - id) / 2
      
    这样可以使得测试点准确落在要求的 (Id, Ir) 坐标上，而不是近似值。
    """
    sum_val = ir_val * k
    diff_val = id_val
    i1 = (sum_val + diff_val) / 2.0
    i2 = (sum_val - diff_val) / 2.0
    return i1, i2

def _solve_max(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    求解器 B：最大值型
    max(i1', i2') = ir / K
    假设 i1' >= i2' => i1' = ir / K
    i1' - i2' = id  => i2' = ir / K - id
    """
    val_max = ir_val / k if k != 0 else 0.0
    i1 = val_max
    i2 = val_max - id_val
    return i1, i2

def _solve_const(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    求解器 C：定值型 (合并 ir_i2_k 与 ir_id_abs_diff)
    i2' = ir / K
    i1' = id + i2'
    注：对于 ir_id_abs_diff，前端下发的 K 恒等于 2。两者公式完全一致。
    """
    i2 = ir_val / k if k != 0 else 0.0
    i1 = id_val + i2
    return i1, i2

def _solve_product(id_val: float, ir_val: float) -> tuple[float, float]:
    """
    求解器 D：乘积型
    i1' * i2' = ir^2
    i1' - i2' = id  => (id + i2') * i2' = ir^2  => i2'^2 + id*i2' - ir^2 = 0
    """
    i2 = _solve_quadratic(1.0, id_val, -(ir_val**2))
    i1 = id_val + i2
    return i1, i2

def _solve_product_sq(id_val: float, ir_val: float) -> tuple[float, float]:
    """
    路由 2 乘积型 (id_sum_sq / id_diff_sq)
    i1' * i2' = ir
    i1' - i2' = sqrt(id) = d  => (d + i2') * i2' = ir  => i2'^2 + d*i2' - ir = 0
    """
    d = math.sqrt(id_val) if id_val >= 0 else 0.0
    i2 = _solve_quadratic(1.0, d, -ir_val)
    i1 = d + i2
    return i1, i2

def solve(id_val: float, ir_val: float, k: float, kp1: float, kp2: float,
          id_eq: str, ir_eq: str) -> tuple[float, float, float]:
    """
    解算差动保护方程式，并返回物理输出电流幅值与侧 2 基准相位。
    
    参数:
        id_val: 动作电流 Id 试验值
        ir_val: 制动电流 Ir 试验值
        k: 制动系数 K
        kp1: 侧 1 变比/接线补偿系数
        kp2: 侧 2 变比/接线补偿系数
        id_eq: 动作方程 API 类型 (id_i1, id_sum, id_diff, id_sum_sq, id_diff_sq)
        ir_eq: 制动方程 API 类型
    
    返回:
        (i1_out, i2_out, theta2) -> 侧 1 物理幅值, 侧 2 物理幅值, 侧 2 最终相位偏角
    """
    # 1. 确定初始 theta2
    theta2 = 180.0 if id_eq in ("id_sum", "id_sum_sq") else 0.0

    # 2. 一级路由
    if id_eq == "id_i1":
        # 直通型
        i1, i2 = id_val, ir_val
    elif id_eq in ("id_sum_sq", "id_diff_sq"):
        # 路由 2 乘积型
        i1, i2 = _solve_product_sq(id_val, ir_val)
    else:
        # 路由 3 (id_sum / id_diff) -> 二级路由
        if ir_eq in ("ir_diff_k", "ir_sum_k", "ir_sum_div_k", "ir_imax_sum_k"):
            i1, i2 = _solve_sum_diff(id_val, ir_val, k)
        elif ir_eq == "ir_max_k":
            i1, i2 = _solve_max(id_val, ir_val, k)
        elif ir_eq in ("ir_i2_k", "ir_id_abs_diff"):
            i1, i2 = _solve_const(id_val, ir_val, k)
        elif ir_eq == "ir_sqrt_cos":
            i1, i2 = _solve_product(id_val, ir_val)
        else:
            # 默认后退到直通，以保证鲁棒性
            i1, i2 = id_val, ir_val

    # 3. 后处理：极性处理（负幅值修正）
    if i2 < 0:
        i2 = abs(i2)
        theta2 = 0.0 if theta2 == 180.0 else 180.0

    # 4. Kp 补偿除法
    i1_out = i1 / kp1 if kp1 != 0 else i1
    i2_out = i2 / kp2 if kp2 != 0 else i2

    return i1_out, i2_out, theta2

import cmath

def determine_theta(side: int, l_type: str, phase_correction: str,
                    zero_seq: bool, clock: int) -> float | None:
    """
    确定某侧的 M 矩阵补偿角 θ。

    返回:
        None  → 无补偿 (M = E, 单位矩阵)
        float → 使用 M(θ) 矩阵

    判定逻辑（两步）：
      Step 1: 相位校正 — y-side 匹配 y 侧，delta-side 匹配 d 侧
      Step 2: 零序校正 — 仅对 Step1 中仍未校正的 Y 侧生效
    """
    # Step 1: 相位校正
    if phase_correction == "y-side" and l_type == "y":
        raw = (12 - clock) * 30
        theta = raw if side == 1 else -raw
        return ((theta + 180) % 360) - 180  # 映射到 [-180, 180]

    if phase_correction == "delta-side" and l_type == "d":
        raw = (12 - clock) * 30
        theta = raw if side == 1 else -raw
        return ((theta + 180) % 360) - 180

    # Step 2: Y 侧零序校正（仅 Step1 未命中的 Y 侧）
    if zero_seq and l_type == "y":
        return 0.0  # M(0°)

    # 无补偿
    return None  # M = E


def build_M_matrix(theta_deg: float) -> list[list[float]]:
    """
    生成 M(θ) 补偿矩阵 (3×3 实数)。

    M(θ) = (2/3) * [[cos θ,        cos(θ+120°), cos(θ-120°)],
                     [cos(θ-120°),  cos θ,       cos(θ+120°)],
                     [cos(θ+120°),  cos(θ-120°), cos θ      ]]
    """
    theta = math.radians(theta_deg)
    c0 = math.cos(theta)
    c_p120 = math.cos(theta + math.radians(120))
    c_m120 = math.cos(theta - math.radians(120))
    f = 2.0 / 3.0
    return [
        [f * c0,    f * c_p120, f * c_m120],
        [f * c_m120, f * c0,    f * c_p120],
        [f * c_p120, f * c_m120, f * c0   ],
    ]


def solve_M_equation(theta: float | None, target: list[complex],
                     is_single_phase: bool) -> list[complex]:
    """
    求解 M · I = T，返回物理电流 I。

    三种情况：
    1. theta is None (M=E): I = T 直接输出
    2. theta == 0° (零序校正):
       - 多相: I = T（T 天然零和，M(0°)·T = T）
       - 单相: 对消法（故障相 + 邻相反相 180°）
    3. theta != 0° (相位校正): I = k · M(-θ)^T · T
       - k = 3/2 (单相), k = 1 (多相)
    """
    # 情况 1：无补偿，直接输出
    if theta is None:
        return list(target)

    # 情况 2：零序校正 (θ = 0°)
    if abs(theta) < 1e-6:
        if not is_single_phase:
            # 多相：T 天然零和，直接输出
            return list(target)
        else:
            # 单相：对消法 — 故障相 + 邻相反相(+180°)
            idx = next(i for i, x in enumerate(target) if abs(x) > 1e-6)
            res = [0j, 0j, 0j]
            res[idx] = target[idx]
            # 邻相取反（通过相位 +180° 实现）
            amp = abs(target[idx])
            phase = cmath.phase(target[idx]) + math.pi  # +180°
            res[(idx + 1) % 3] = cmath.rect(amp, phase)
            return res

    # 情况 3：相位校正 — I = k · M(-θ) · T
    # M(-θ) = M(θ)^T（转置）
    M_neg = build_M_matrix(-theta)
    k = 1.5 if is_single_phase else 1.0

    result = [0j, 0j, 0j]
    for i in range(3):
        s = 0j
        for j in range(3):
            s += M_neg[i][j] * target[j]
        result[i] = k * s

    return result

def _empirical_6i_single_phase(
    c1: complex, c2: complex,
    L: int, L1: str, L2: str,
    PC: str, ZS: bool,
    test_phase: str
) -> tuple[list[complex], list[complex]]:
    """
    根据成品仪器穷举归纳的单相差动经验公式，支持 A/B/C 相。
    核心逻辑以 A 相为准，B/C 相通过通道循环移位实现。
    """
    SQRT3 = math.sqrt(3)
    
    def clock_channel(N, c_val):
        ch_idx = (N // 2) % 3
        sign_flip = (-1) ** (N // 2)
        return ch_idx, c_val * sign_flip

    def correction_channel_positive(clock):
        alpha = clock * 30
        return ((alpha + 60) // 120) % 3

    def correction_channel_negative(clock):
        alpha = (360 - clock * 30) % 360
        return ((alpha + 60) // 120) % 3

    def comp_channel(clock, correction_type):
        k = (clock // 2) % 2
        if correction_type == "y-side":
            return 1 if k == 0 else 2
        else:
            return 2 if k == 0 else 1

    def apply_zero_seq(channels):
        for i in range(3):
            if abs(channels[i]) > 1e-6:
                channels[(i + 1) % 3] = -channels[i]
                break

    high = [0j, 0j, 0j]
    low  = [0j, 0j, 0j]
    
    same_pair = (L1 == L2)
    mixed_pair = not same_pair
    
    high_matched = (PC == "y-side" and L1 == "y") or (PC == "delta-side" and L1 == "d")
    low_matched  = (PC == "y-side" and L2 == "y") or (PC == "delta-side" and L2 == "d")
    
    # DY 镜像: comp_channel 需要用 12-L
    is_dy = (L1 == "d" and L2 == "y")
    effective_L = (12 - L) if is_dy else L
    
    if PC == "none" or (not high_matched and not low_matched):
        high[0] = c1
        if same_pair:
            ch, val = clock_channel(L, c2)
            low[ch] = val
        else:
            low[0] = c2
    
    elif same_pair:
        high[0] = c1 * SQRT3
        ch, val = clock_channel(L, c2)
        low[ch] = val * SQRT3
    
    elif mixed_pair and high_matched:
        main_ch = correction_channel_positive(L)
        high[main_ch] = c1 * SQRT3
        low[0] = c2
        c = comp_channel(effective_L, PC)
        low[c] = -c2
    
    elif mixed_pair and low_matched:
        main_ch = correction_channel_negative(L)
        low[main_ch] = c2 * SQRT3
        high[0] = c1
        c = comp_channel(effective_L, PC)
        high[c] = -c1
    
    if ZS and (L1 == "y" or L2 == "y"):
        if not high_matched:
            apply_zero_seq(high)
        if not low_matched:
            apply_zero_seq(low)
            
    # 2. 根据测试相别进行通道循环移位
    phase_offset = {"A": 0, "B": 1, "C": 2}[test_phase]
    
    if phase_offset == 0:
        return high, low
        
    high_shifted = [0j, 0j, 0j]
    low_shifted  = [0j, 0j, 0j]
    for i in range(3):
        high_shifted[(i + phase_offset) % 3] = high[i]
        low_shifted[(i + phase_offset) % 3] = low[i]
        
    return high_shifted, low_shifted

def calculate_physical_currents(
    i1_prime: float, i2_prime: float, theta2: float,
    test_phase: str,
    phase_correction: str,
    zero_seq_correction: bool,
    high_clock: int,
    low_clock: int,
    l1_type: str,
    l2_type: str
) -> tuple[list[complex], list[complex]]:
    """
    根据 M 矩阵框架，将内部目标电流映射到物理发波电流。

    流程:
      1. 构建目标向量 T（由 solve 输出 + testPhase 决定）
      2. 确定两侧补偿角 θ（determine_theta）
      3. 求解 M·I = T（solve_M_equation）
    """
    # 1. 构建目标向量 T
    c1 = complex(i1_prime, 0)
    rad2 = math.radians(theta2)
    c2 = cmath.rect(i2_prime, rad2)

    t1 = [0j, 0j, 0j]
    t2 = [0j, 0j, 0j]

    if test_phase == "A":
        t1[0], t2[0] = c1, c2
    elif test_phase == "B":
        t1[1], t2[1] = c1, c2
    elif test_phase == "C":
        t1[2], t2[2] = c1, c2
    elif test_phase == "AB":
        t1[0], t1[1] = c1, -c1
        t2[0], t2[1] = c2, -c2
    elif test_phase == "BC":
        t1[1], t1[2] = c1, -c1
        t2[1], t2[2] = c2, -c2
    elif test_phase == "CA":
        t1[2], t1[0] = c1, -c1
        t2[2], t2[0] = c2, -c2
    elif test_phase == "ABC":
        rad_120 = math.radians(120)
        a1 = cmath.rect(1.0, rad_120)
        a2 = cmath.rect(1.0, -rad_120)
        t1 = [c1, c1 * a2, c1 * a1]
        t2 = [c2, c2 * a2, c2 * a1]

    # 2. 对于单相 A/B/C，使用经验公式；否则使用 M 矩阵
    if test_phase in ("A", "B", "C"):
        return _empirical_6i_single_phase(
            c1, c2, low_clock, l1_type, l2_type, phase_correction, zero_seq_correction, test_phase
        )

    # 3. 确定两侧补偿角 θ（用于 M 矩阵框架）
    theta_side1 = determine_theta(1, l1_type, phase_correction, zero_seq_correction, high_clock)
    theta_side2 = determine_theta(2, l2_type, phase_correction, zero_seq_correction, low_clock)

    # 4. 求解 M·I = T
    non_zeros = sum(1 for x in t1 if abs(x) > 1e-6)
    is_single = (non_zeros == 1)

    phys1 = solve_M_equation(theta_side1, t1, is_single)
    phys2 = solve_M_equation(theta_side2, t2, is_single)

    return phys1, phys2
