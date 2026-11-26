# -*- coding: utf-8 -*-
"""
FPGA 自动化校准仪表盘 (Gradio WebUI)
===================================
1. 支持单通道选择（API 通道 0-11，内部自动映射物理通道 0-11）。
2. 提供自动和手动交互采样机制，通过长时间采样求平均过滤功放输出的低频抖动和温漂。
3. 自动折算 75mR 采样电阻上的电压为电流（电流通道）。
4. 直流三点拟合法计算 factor_dc 与 bias，交流 RMS 对折 $\sqrt{2}$ 倍峰值计算 factor_ac。
5. 集成高颜值的 Matplotlib 实时波形绘制与三点线性拟合可视化。
"""
import os
import sys
import time
import math
import json
import threading
import serial
import pyvisa
import matplotlib
matplotlib.use('Agg')  # 无头模式运行 Matplotlib 防止 GUI 冲突
import matplotlib.pyplot as plt
import gradio as gr

# 将根目录添加到 python 搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from logic.FPGACodec import HWCodec, HWConfig
from api.ApiHdlMgr import ApiHdlMgr

# ==============================================================================
#  全局状态管理
# ==============================================================================
class CalibrationState:
    def __init__(self):
        self.ser = None          # 串口句柄
        self.dmm = None          # 万用表句柄
        self.rm = None           # VISA 资源管理器
        self.calib_mgr = None    # ApiHdlMgr 实例用于加载/保存 json
        
        # 当前校准通道配置
        self.current_ch_api = 0
        self.is_current = False
        
        # 实时采样线程控制
        self.is_sampling = False
        self.sample_list = []
        self.sample_times = []
        self.sample_start_time = 0
        self.lock = threading.Lock()
        
        # 四步采样记录
        self.measured_p100 = None  # +100V / +0.5A
        self.measured_n100 = None  # -100V / -0.5A
        self.measured_zero = None  # 0V / 0A
        self.measured_ac = None    # 100V AC / 0.5A AC RMS
        
        # 直流三点采样方差
        self.variance_p100 = None
        self.variance_n100 = None
        self.variance_zero = None
        
        # 实时校验运行状态
        self.is_verifying = False
        
        # 电流通道测量模式: "shunt" (采样电阻) 或 "direct" (电流挡直接测)
        self.curr_meas_mode = "shunt"

global_state = CalibrationState()

# 加载校准配置文件管理器
global_state.calib_mgr = ApiHdlMgr()

# ==============================================================================
#  高精度量化截断等效真实物理值反算数学函数
# ==============================================================================
def get_quantized_phys_target(value, is_ac, is_cur):
    """
    计算输入目标值在经过 FPGA 寄存器高16位截断(I16)后，所对应的真实理想物理值
    """
    scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
    if is_ac:
        # 交流：目标值转换成峰值，再到寄存器，再进行高16位截断，再算回RMS
        peak_val = value * math.sqrt(2.0)
        reg_val = int(round(peak_val * scale))
        high_16 = (reg_val >> 16) & 0xFFFF
        # 转为 I16 有符号整型
        high_16_signed = high_16 if high_16 < 32768 else high_16 - 65536
        actual_reg = high_16_signed * 65536
        actual_peak = actual_reg / scale
        actual_rms = actual_peak / math.sqrt(2.0)
        return actual_rms
    else:
        # 直流：直接到寄存器，高16位截断，再算回直流物理值
        reg_val = int(round(value * scale))
        high_16 = (reg_val >> 16) & 0xFFFF
        high_16_signed = high_16 if high_16 < 32768 else high_16 - 65536
        actual_reg = high_16_signed * 65536
        actual_phys = actual_reg / scale
        return actual_phys

# ==============================================================================
#  底层串口与万用表控制函数
# ==============================================================================
def find_multimeters():
    """自动寻找 VISA 资源中的 Rigol 或其他万用表设备"""
    try:
        if not global_state.rm:
            global_state.rm = pyvisa.ResourceManager('@py')
        resources = global_state.rm.list_resources()
        target_list = [r for r in resources if "6833" in r or "0x1AB1" in r or "USB" in r or "GPIB" in r]
        if not target_list:
            return resources if resources else ["未发现任何 VISA 资源"]
        return target_list
    except Exception as e:
        return [f"寻找设备出错: {e}"]

def connect_devices(serial_port, multimeter_address):
    """一键连接 FPGA 串口和万用表"""
    status_msg = []
    
    # 1. 连接串口
    try:
        if global_state.ser:
            global_state.ser.close()
        global_state.ser = serial.Serial(serial_port, 230400, timeout=1.0)
        status_msg.append(f"成功连接串口: {serial_port}")
    except Exception as e:
        global_state.ser = None
        status_msg.append(f"串口连接失败: {e}")
        
    # 2. 连接万用表
    try:
        if not global_state.rm:
            global_state.rm = pyvisa.ResourceManager('@py')
        if global_state.dmm:
            global_state.dmm.close()
        global_state.dmm = global_state.rm.open_resource(multimeter_address)
        global_state.dmm.timeout = 5000
        idn = global_state.dmm.query('*IDN?').strip()
        status_msg.append(f"成功连接万用表: {idn}")
    except Exception as e:
        global_state.dmm = None
        status_msg.append(f"万用表连接失败: {e}")
        
    # 3. 硬件复位初始化
    if global_state.ser:
        try:
            global_state.ser.write(HWCodec.FRAME_SYS_RESET)
            time.sleep(0.1)
            # 发送 SYS_START 开启 DDS 调制器运行
            global_state.ser.write(HWCodec.FRAME_SYS_START)
            # 全物理通道 Layer 0, 1 静默清零
            clear_all_channels()
            status_msg.append("已向 FPGA 发送硬复位并启动 DDS 调制器，全通道默认静默归零。")
        except Exception as e:
            status_msg.append(f"FPGA 初始化通信失败: {e}")
            
    return "\n".join(status_msg)

def clear_all_channels():
    """发送 16 个物理通道 Layer0 与 Layer1 归零的 DDS 指令"""
    if not global_state.ser:
        return
    # 物理通道 Layer 0 (DDS_WR_SHADOW, regIndex=255, chMask=0xFFFF, Amp=0, Freq=50Hz)
    f_freq = HWConfig.ConvertFreqToReg(50.0)
    frm_l0 = HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 255, 0xFFFF, 0, f_freq)
    # 物理通道 Layer 1 (DDS_WR_SHADOW, regIndex=0, chMask=0xFFFF, Amp=0, Phase=0)
    frm_l1 = HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, 0xFFFF, 0, 0)
    
    global_state.ser.write(frm_l0)
    global_state.ser.write(frm_l1)
    global_state.ser.write(HWCodec.FRAME_SYS_UPDATE)

def set_fpga_output_raw(ch_api, is_ac, value):
    """
    直接控制 FPGA 输出，不通过校准因子（测试原始输出响应）
    is_ac: False - 直流有效值(Layer 0), True - 交流有效值(转换为 Layer 1 峰值幅度)
    value: 期望的物理输出有效值 (V 或 A)
    """
    if not global_state.ser:
        return "错误：串口未连接"
    
    # 1. 通道与极性判断
    ch_hw = HWConfig.MapChannel(ch_api)
    mask = 1 << ch_hw
    is_cur = ch_api >= 6
    
    # 2. 静默清空所有通道，避免干扰
    clear_all_channels()
    time.sleep(0.05)
    
    # 3. 计算寄存器值并构建帧
    frames = []
    
    if not is_ac:
        # DC 输出：Layer 0 (reg 255) 写入目标 DC 值，Layer 1 (reg 0) 保持 0
        amp_reg = HWConfig.ConvertAmpToReg(value, is_cur)
        freq_reg = HWConfig.ConvertFreqToReg(50.0)
        
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 255, mask, amp_reg, freq_reg))
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, mask, 0, 0))
    else:
        # AC 输出：Layer 0 (reg 255) 写入 0 直流，Layer 1 (reg 0) 写入峰值幅值 (有效值 * 根号2)
        peak_val = value * math.sqrt(2.0)
        amp_reg = HWConfig.ConvertAmpToReg(peak_val, is_cur)
        phase_reg = HWConfig.ConvertPhaseToReg(0.0)
        freq_reg = HWConfig.ConvertFreqToReg(50.0)
        
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 255, mask, 0, freq_reg))
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, mask, amp_reg, phase_reg))
        
    # 4. 串行写入并发送更新
    for f in frames:
        global_state.ser.write(f)
    global_state.ser.write(HWCodec.FRAME_SYS_UPDATE)
    
    type_str = "交流" if is_ac else "直流"
    unit_str = "A" if is_cur else "V"
    peak_str = f" (峰值 {peak_val:.4f}{unit_str})" if is_ac else ""
    return f"[DDS OK] 物理通道 {ch_hw} (API {ch_api}) 已输出{type_str} {value}{unit_str}{peak_str}"

# ==============================================================================
#  设备辅助控制函数（提前切换与复位）
# ==============================================================================
def reset_fpga_and_clear():
    """发送 FPGA 系统复位并清空通道输出，确保功放处于安全状态"""
    if not global_state.ser:
        return "错误：串口未连接"
    try:
        global_state.ser.write(HWCodec.FRAME_SYS_RESET)
        time.sleep(0.05)
        global_state.ser.write(HWCodec.FRAME_SYS_START)
        time.sleep(0.05)
        clear_all_channels()
        return "FPGA 系统已复位并归零全通道输出。"
    except Exception as e:
        return f"FPGA 复位失败: {e}"

def configure_multimeter(is_ac, is_cur=False, target_val=None):
    """在输出电压前，提前将万用表切换至正确挡位并配置好量程参数"""
    if not global_state.dmm:
        return "错误：万用表未连接"
    try:
        # 判断是否为零点测量 (0A 或 0V)
        is_zero = (target_val is not None and abs(target_val) < 1e-4)
        
        if is_ac:
            if is_cur:
                if getattr(global_state, 'curr_meas_mode', 'shunt') == 'shunt':
                    # 电流通道测试 Shunt 两端小电压，恒定使用 100mV 挡，无 10mV/1mV 挡
                    global_state.dmm.write(':CONF:VOLT:AC 0.1')
                else:
                    # 使用电流挡直接测量电流，0.5A 采用 1A 挡位测量
                    global_state.dmm.write(':CONF:CURR:AC 1')
            else:
                # 电压通道测试 57.735V AC，手动设置 100V 量程，防止自动挡卡在低量程超量程
                global_state.dmm.write(':CONF:VOLT:AC 100')
        else:
            if is_cur:
                if getattr(global_state, 'curr_meas_mode', 'shunt') == 'shunt':
                    # 电流通道直流测试，恒定使用 100mV 挡以获得高精度与稳定性，无 10mV/1mV 挡
                    global_state.dmm.write(':CONF:VOLT:DC 0.1')
                    global_state.dmm.write(':SENS:VOLT:DC:NPLC 10')
                else:
                    # 使用电流挡直接测量电流
                    if is_zero:
                        # 0输出采用 100mA (0.1A) 挡位测量
                        global_state.dmm.write(':CONF:CURR:DC 0.1')
                    else:
                        # 0.5A 采用 1A 挡位测量
                        global_state.dmm.write(':CONF:CURR:DC 1')
                    global_state.dmm.write(':SENS:CURR:DC:NPLC 10')
            else:
                # 电压通道直流测试，手动配置量程
                if is_zero:
                    global_state.dmm.write(':CONF:VOLT:DC 0.001')  # 0V 直流设置至最小量程 (1mV / MIN)
                else:
                    global_state.dmm.write(':CONF:VOLT:DC 100')    # 直流电压测试使用 100V 挡
                global_state.dmm.write(':SENS:VOLT:DC:NPLC 10')
                
        time.sleep(0.5)  # 等待仪表模式切换稳定
        type_str = "交流 (AC)" if is_ac else "直流 (DC)"
        
        if is_cur:
            if getattr(global_state, 'curr_meas_mode', 'shunt') == 'shunt':
                range_str = "100mV Shunt量程"
            else:
                range_str = "100mA直接测量挡" if is_zero else "1A直接测量挡"
        else:
            range_str = "最小(1mV)量程" if is_zero else "100V量程"
            
        return f"万用表已成功切换至 {type_str} {range_str}测量挡位。"
    except Exception as e:
        return f"万用表切换挡位失败: {e}"

# ==============================================================================
#  数据采样记录核心线程
# ==============================================================================
def multimeter_sampling_loop():
    """万用表循环采样工作线程（万用表挡位在主线程中提前配置完成）"""
    if not global_state.dmm:
        global_state.is_sampling = False
        return

    global_state.sample_start_time = time.time()
    while global_state.is_sampling:
        try:
            # 依据通道切换计算 Shunt 电流
            raw_v_str = global_state.dmm.query(':READ?')
            raw_v = float(raw_v_str.strip())
            
            if global_state.is_current:
                if getattr(global_state, 'curr_meas_mode', 'shunt') == 'shunt':
                    # 测电流实质是测 75mR 采样电阻两端毫伏电压，折合成物理电流值
                    # I = U / R = U_dmm / 0.075
                    phys_val = raw_v / 0.075
                else:
                    # 直接测量模式，万用表返回的值即为物理电流值 A
                    phys_val = raw_v
            else:
                phys_val = raw_v
                
            elapsed = time.time() - global_state.sample_start_time
            
            with global_state.lock:
                global_state.sample_list.append(phys_val)
                global_state.sample_times.append(elapsed)
                
            # 每次采样休眠 150ms ~ 200ms
            time.sleep(0.15)
        except Exception:
            time.sleep(0.05)

def start_sampling(ch_api, is_ac):
    """开启数据采集工作"""
    global_state.current_ch_api = ch_api
    global_state.is_current = ch_api >= 6
    
    with global_state.lock:
        global_state.sample_list = []
        global_state.sample_times = []
        
    global_state.is_sampling = True
    t = threading.Thread(target=multimeter_sampling_loop)
    t.daemon = True
    t.start()
    return "已启动实时万用表记录..."

def stop_sampling():
    """停止实时采样并计算均值与方差"""
    global_state.is_sampling = False
    time.sleep(0.3)  # 等待线程安全退出
    
    with global_state.lock:
        if not global_state.sample_list:
            return 0.0, 0.0, "未采集到任何有效数据。"
        
        # 计算均值
        avg_val = sum(global_state.sample_list) / len(global_state.sample_list)
        pts_count = len(global_state.sample_list)
        
        # 计算方差 (variance) 用于加权拟合
        variance = sum((x - avg_val) ** 2 for x in global_state.sample_list) / len(global_state.sample_list)
        
    unit_str = "A" if global_state.is_current else "V"
    sample_msg = f"成功采集 {pts_count} 组数据，计算平均值: {avg_val:.6f} {unit_str}，采样方差: {variance:.10e}"
    return avg_val, variance, sample_msg

def format_live_data_log():
    """格式化当前采集到的数据，用于在界面上实时显示"""
    with global_state.lock:
        if not global_state.sample_list:
            return "⏳ 正在等待第一组万用表数据..."
        
        pts_count = len(global_state.sample_list)
        last_val = global_state.sample_list[-1]
        avg_val = sum(global_state.sample_list) / pts_count
        variance = sum((x - avg_val) ** 2 for x in global_state.sample_list) / pts_count
        
    unit_str = "A" if global_state.is_current else "V"
    log_str = (
        f"📊 [实时记录详情]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 已采集点数: {pts_count}\n"
        f"🔔 最新测值: {last_val:.6f} {unit_str}\n"
        f"📐 实时平均值: {avg_val:.6f} {unit_str}\n"
        f"📉 实时方差 (置信度评估): {variance:.10e}"
    )
    return log_str

# ==============================================================================
#  自动化校准数据处理引擎 (直流三点 WLS 加权最小二乘回归法)
# ==============================================================================


def output_and_auto_sample(ch_api, step_index, wait_sec, sample_sec):
    """
    控制 FPGA 物理输出，并在功放稳定后，万用表自动连续采样并过滤低频波动与偏置。
    """
    is_cur = ch_api >= 6
    unit_str = "A" if is_cur else "V"
    
    # 1. 映射目标名称、测试信号值与信号类型
    if step_index == 0:
        val = 0.5 if is_cur else 100.0
        is_ac = False
        name = "+0.5A" if is_cur else "+100V"
    elif step_index == 1:
        val = -0.5 if is_cur else -100.0
        is_ac = False
        name = "-0.5A" if is_cur else "-100V"
    elif step_index == 2:
        val = 0.0
        is_ac = False
        name = "0A" if is_cur else "0V"
    else:
        val = 0.5 if is_cur else 57.735
        is_ac = True
        name = "0.5A AC" if is_cur else "57.735V AC"
        
    # 获取并维护已测得的数值字符串，防止 UI 文本框在倒计时期间被清空
    def get_current_ui_outputs(live_log_str=None):
        p100_str = f"{global_state.measured_p100:.6f} {unit_str}" if global_state.measured_p100 is not None else "未测试"
        n100_str = f"{global_state.measured_n100:.6f} {unit_str}" if global_state.measured_n100 is not None else "未测试"
        zero_str = f"{global_state.measured_zero:.6f} {unit_str}" if global_state.measured_zero is not None else "未测试"
        ac_str = f"{global_state.measured_ac:.6f} {unit_str}" if global_state.measured_ac is not None else "未测试"
        if live_log_str is None:
            live_log_str = "⏳ 等待采样数据启动..."
        return live_log_str, p100_str, n100_str, zero_str, ac_str
        
    # 2. 提前发送 FPGA 系统复位并清空通道，确保安全
    reset_msg = reset_fpga_and_clear()
    time.sleep(0.05)
    
    # 3. 提前将万用表切换至正确挡位并配置好量程参数
    dmm_msg = configure_multimeter(is_ac, is_cur, target_val=val)
    time.sleep(0.05)
    
    # 4. FPGA 物理控制输出
    out_msg = set_fpga_output_raw(ch_api, is_ac, val)
    
    # 5. 等待功放稳定
    msg = out_msg + f"\n[{dmm_msg}]\n[功放稳定等待] 功放处于开启输出状态，强制等待稳定 {wait_sec}s..."
    yield (msg,) + get_current_ui_outputs()
    
    for sec in range(int(wait_sec), 0, -1):
        time.sleep(1)
        msg = out_msg + f"\n[{dmm_msg}]\n[功放稳定等待] 强制稳定倒计时: {sec}s..."
        yield (msg,) + get_current_ui_outputs()
        
    # 6. 自动采集
    msg = out_msg + f"\n[万用表记录] 已经稳定！正在连续记录 {sample_sec}s 数据以过滤低频波动..."
    yield (msg,) + get_current_ui_outputs()
    
    start_sampling(ch_api, is_ac)
    for sec in range(int(sample_sec), 0, -1):
        time.sleep(1)
        live_str = format_live_data_log()
        msg = out_msg + f"\n[万用表记录] 数据读取中，倒计时: {sec}s..."
        yield (msg,) + get_current_ui_outputs(live_str)
        
    # 7. 采集结束并计算平均值与方差
    avg_val, variance, sample_msg = stop_sampling()
    final_str = format_live_data_log()
    
    # 8. 测量完成之后，发送 reset 停止输出
    reset_fpga_and_clear()
    
    # 9. 保存对应测量的有效值与方差
    if step_index == 0:
        global_state.measured_p100 = avg_val
        global_state.variance_p100 = variance
    elif step_index == 1:
        global_state.measured_n100 = avg_val
        global_state.variance_n100 = variance
    elif step_index == 2:
        global_state.measured_zero = avg_val
        global_state.variance_zero = variance
    else:
        global_state.measured_ac = avg_val
        
    status_summary = (
        f"测量点: {name}\n"
        f"输出控制: {out_msg}\n"
        f"万用表状态: {dmm_msg}\n"
        f"稳定平均读数: {avg_val:.6f} {unit_str}\n"
        f"采样详情: {sample_msg}\n"
        f"安全状态: 已发送 FPGA 系统复位并清零通道输出。"
    )
    
    yield (status_summary,) + get_current_ui_outputs(final_str)

def calculate_and_save_coefficients(ch_api):
    """根据测量的四步读数计算并加权最小二乘(WLS)拟合双增益校准参数"""
    is_cur = ch_api >= 6
    target_val_dc_raw = 0.5 if is_cur else 100.0
    target_val_ac_raw = 0.5 if is_cur else 57.735
    unit_str = "A" if is_cur else "V"
    
    # 校验直流数据完整度
    if (global_state.measured_p100 is None or 
        global_state.measured_n100 is None or 
        global_state.measured_zero is None):
        return "错误：直流三点测量数据不完整，无法拟合斜率和零偏！", "", "", ""
        
    # 校验交流数据完整度
    if global_state.measured_ac is None:
        return "错误：交流测量数据未采集，无法独立校准 AC 增益！", "", "", ""
        
    try:
        # 计算经过 FPGA 寄存器高 16 位离散截断后的等效真实物理目标值
        target_p = get_quantized_phys_target(target_val_dc_raw, False, is_cur)
        target_n = get_quantized_phys_target(-target_val_dc_raw, False, is_cur)
        target_z = get_quantized_phys_target(0.0, False, is_cur)
        target_val_ac = get_quantized_phys_target(target_val_ac_raw, True, is_cur)
        
        # 提取直流测量值
        y1 = global_state.measured_p100
        y2 = global_state.measured_n100
        y3 = global_state.measured_zero
        
        # 对应目标值
        x1 = target_p
        x2 = target_n
        x3 = target_z
        
        # 提取直流采样方差 (若为 None 则默认 0.0)
        v1 = global_state.variance_p100 if global_state.variance_p100 is not None else 0.0
        v2 = global_state.variance_n100 if global_state.variance_n100 is not None else 0.0
        v3 = global_state.variance_zero if global_state.variance_zero is not None else 0.0
        
        # 计算权重：加一个极小稳定项以防止除零 (1e-12)
        eps = 1e-12
        w1 = 1.0 / (v1 + eps)
        w2 = 1.0 / (v2 + eps)
        w3 = 1.0 / (v3 + eps)
        
        # 加权求和
        sum_w = w1 + w2 + w3
        sum_wx = w1 * x1 + w2 * x2 + w3 * x3
        sum_wy = w1 * y1 + w2 * y2 + w3 * y3
        sum_wxx = w1 * (x1 ** 2) + w2 * (x2 ** 2) + w3 * (x3 ** 2)
        sum_wxy = w1 * x1 * y1 + w2 * x2 * y2 + w3 * x3 * y3
        
        # 行列式 D
        D = sum_w * sum_wxx - (sum_wx ** 2)
        
        if abs(D) < 1e-12:
            # D 过小，退回到普通最小二乘 (OLS) 三点法，避免除以零
            G_dc = (y1 - y2) / (x1 - x2)
            O_dc = y3 - x3
        else:
            # WLS 公式求解直流斜率 (G_dc) 和截距 (O_dc)
            G_dc = (sum_w * sum_wxy - sum_wx * sum_wy) / D
            O_dc = (sum_wxx * sum_wy - sum_wx * sum_wxy) / D
            
        if abs(G_dc) < 1e-4:
            return f"错误：直流实测增益过小 (G_dc={G_dc:.6f})，请检查系统连线或阻抗！", "", "", ""
            
        # 计算直流 factor_dc 和 bias
        factor_dc = -1.0 / G_dc
        bias = O_dc * factor_dc
        
        # 2. 交流拟合增益 (不包含零偏)
        x_m_ac = global_state.measured_ac
        G_ac = x_m_ac / target_val_ac
        
        if abs(G_ac) < 1e-4:
            return f"错误：交流实测增益过小 (G_ac={G_ac:.6f})，请检查系统波形输出！", "", "", ""
            
        # 交流统一继承直流的极性(反相标志)
        G_unified_ac = math.copysign(G_ac, G_dc)
        factor_ac = -1.0 / G_unified_ac
        
        # 3. 保存更新到 calibration.json 中
        global_state.calib_mgr.UpdateCalibDual(ch_api, factor_dc, factor_ac, bias)
        
        # 4. 组装结果日志
        result_summary = (
            f"🎉 恭喜！API 通道 {ch_api} 校准参数计算完毕并已自动保存！\n\n"
            f"--- 校准系数摘要 (WLS 拟合) ---\n"
            f"🔹 直流拟合增益 G_dc: {G_dc:.8f}\n"
            f"🔹 直流零偏 O_dc: {O_dc:.8f} {unit_str}\n"
            f"🔹 直流采样方差 [P/N/Z]: [{v1:.10e} / {v2:.10e} / {v3:.10e}]\n"
            f"🔹 拟合使用权重 [P/N/Z]: [{w1:.4e} / {w2:.4e} / {w3:.4e}]\n"
            f"🔥 最终直流增益系数 [factor_dc]: {factor_dc:.8f}\n"
            f"🔥 最终零偏偏置系数 [bias]: {bias:.8f}\n"
            f"🔹 交流有效增益 G_ac: {G_ac:.8f}\n"
            f"🔥 最终交流增益系数 [factor_ac]: {factor_ac:.8f}\n\n"
            f"配置已持久化写入：{global_state.calib_mgr.calib_filepath}"
        )
        
        return (
            result_summary,
            f"{factor_dc:.8f}",
            f"{factor_ac:.8f}",
            f"{bias:.8f}"
        )
    except Exception as e:
        return f"校准数学拟合出错: {e}", "", "", ""

def set_fpga_output_calibrated(ch_api, is_ac, value):
    """
    直接控制 FPGA 输出，且应用已保存的校准因子进行校准输出，方便直接检验校准效果
    is_ac: False - 直流, True - 交流
    value: 期望输出的真实物理有效值 (V 或 A)
    """
    if not global_state.ser:
        return "错误：串口未连接，请先进行硬件设备连接。"
    
    # 1. 通道与极性判断
    ch_hw = HWConfig.MapChannel(ch_api)
    mask = 1 << ch_hw
    is_cur = ch_api >= 6
    
    if is_cur and abs(value) >= 1.0:
        return "错误：安全限制，手动校验电流输出必须严格小于 1.0 A。"
    
    # 2. 静默清空所有通道，确保安全
    clear_all_channels()
    time.sleep(0.05)
    
    # 3. 动态加载最新的校准参数
    global_state.calib_mgr.LoadCalib()
    ch_str = str(ch_api)
    calib_info = global_state.calib_mgr.calib_data.get(ch_str, {})
    
    # 提取校准增益与偏置
    factor_dc = calib_info.get("factor_dc", calib_info.get("factor", 1.0))
    factor_ac = calib_info.get("factor_ac", calib_info.get("factor", 1.0))
    bias = calib_info.get("bias", 0.0)
    
    # 4. 计算寄存器值并构建帧
    frames = []
    
    if not is_ac:
        # 直流输出：应用 ApplyAbsolute 校准
        v_calib = global_state.calib_mgr.ApplyAbsolute(ch_api, value)
        amp_reg = HWConfig.ConvertAmpToReg(v_calib, is_cur)
        freq_reg = HWConfig.ConvertFreqToReg(50.0)
        
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 255, mask, amp_reg, freq_reg))
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, mask, 0, 0))
    else:
        # 交流输出：应用 ApplyDelta(..., l_idx=1) 校准峰值
        peak_val = value * math.sqrt(2.0)
        calibrated_peak = global_state.calib_mgr.ApplyDelta(ch_api, peak_val, l_idx=1)
        amp_reg = HWConfig.ConvertAmpToReg(calibrated_peak, is_cur)
        phase_reg = HWConfig.ConvertPhaseToReg(0.0)
        freq_reg = HWConfig.ConvertFreqToReg(50.0)
        
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 255, mask, 0, freq_reg))
        frames.append(HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, mask, amp_reg, phase_reg))
        
    # 5. 串行写入并发送更新
    for f in frames:
        global_state.ser.write(f)
    global_state.ser.write(HWCodec.FRAME_SYS_UPDATE)
    
    return "OK"

def run_calibrated_verification_loop(ch_api, is_ac, value):
    """
    运行校准校验的实时循环：
    1. 安全切断并复位。
    2. 自动配置并切换万用表直流/交流物理量程。
    3. 输出校准后的物理信号。
    4. 持续读取并更新万用表测值，直到用户点击停止。
    """
    is_cur = ch_api >= 6
    unit_str = "A" if is_cur else "V"
    
    # 0. 增加安全电流范围限制 (必须严格小于 1A)
    if is_cur and abs(value) >= 1.0:
        global_state.is_verifying = False
        yield (
            f"❌ [安全限制被触发]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"警告：手动校验电流通道时，输出目标值必须严格小于 1.0 A！\n"
            f"您输入的当前目标为: {value:.6f} A\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"提示：为保护设备安全，系统已拒绝开启高电流输出。",
            "限制输出", "已拒绝", "安全限制"
        )
        return
        
    global_state.is_verifying = True
    
    # 1. 自动切换配置万用表挡位
    dmm_msg = "万用表未连接"
    if global_state.dmm:
        try:
            dmm_msg = configure_multimeter(is_ac, is_cur, target_val=value)
        except Exception as e:
            dmm_msg = f"切换万用表挡位失败: {e}"
        time.sleep(0.05)
        
    # 2. FPGA 开启校准输出
    try:
        out_status = set_fpga_output_calibrated(ch_api, is_ac, value)
        if out_status != "OK":
            yield out_status, "读取失败", "已停止", "错误"
            return
    except Exception as e:
        yield f"❌ 开启输出异常: {e}", "读取失败", "已停止", "错误"
        return
        
    # 3. 提取校准系数详情以展示
    ch_str = str(ch_api)
    calib_info = global_state.calib_mgr.calib_data.get(ch_str, {})
    factor_dc = calib_info.get("factor_dc", calib_info.get("factor", 1.0))
    factor_ac = calib_info.get("factor_ac", calib_info.get("factor", 1.0))
    bias = calib_info.get("bias", 0.0)
    
    # 计算实际写入 FPGA 的寄存器及经过高16位截断等效的反算物理值 (即实际reg值对应的真实物理值 actual_target)
    scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
    if not is_ac:
        v_calib = global_state.calib_mgr.ApplyAbsolute(ch_api, value)
        amp_reg = HWConfig.ConvertAmpToReg(v_calib, is_cur)
        
        # 高16位截断，视为 I16 有符号整型
        high_16 = (amp_reg >> 16) & 0xFFFF
        high_16_signed = high_16 if high_16 < 32768 else high_16 - 65536
        actual_reg = high_16_signed * 65536
        
        # v_calib_actual = actual_reg / scale
        # v_calib_actual = bias - actual_target * factor_dc => actual_target = (bias - v_calib_actual) / factor_dc
        v_calib_actual = actual_reg / scale
        if abs(factor_dc) > 1e-9:
            actual_target = (bias - v_calib_actual) / factor_dc
        else:
            actual_target = bias - v_calib_actual
    else:
        peak_val = value * math.sqrt(2.0)
        calibrated_peak = global_state.calib_mgr.ApplyDelta(ch_api, peak_val, l_idx=1)
        amp_reg = HWConfig.ConvertAmpToReg(calibrated_peak, is_cur)
        
        # 高16位截断，视为 I16 有符号整型
        high_16 = (amp_reg >> 16) & 0xFFFF
        high_16_signed = high_16 if high_16 < 32768 else high_16 - 65536
        actual_reg = high_16_signed * 65536
        
        # calib_peak_actual = actual_reg / scale
        # calib_peak_actual = - peak_actual * factor_ac => peak_actual = - calib_peak_actual / factor_ac
        calib_peak_actual = actual_reg / scale
        if abs(factor_ac) > 1e-9:
            peak_actual = - calib_peak_actual / factor_ac
        else:
            peak_actual = - calib_peak_actual
        actual_target = abs(peak_actual) / math.sqrt(2.0)
        
    actual_target_str = f"{actual_target:.6f} {unit_str}"
    
    # 4. 连续读取万用表并 yield 更新
    while global_state.is_verifying:
        dmm_val_str = "万用表未连接"
        accuracy_val_str = "未启动"
        if global_state.dmm:
            try:
                raw_v = float(global_state.dmm.query('READ?'))
                if is_cur:
                    if getattr(global_state, 'curr_meas_mode', 'shunt') == 'shunt':
                        phys_val = raw_v / 0.075
                    else:
                        phys_val = raw_v
                else:
                    phys_val = raw_v
                dmm_val_str = f"{phys_val:.6f} {unit_str}"
                
                # 计算相对精度 / 相对误差，以实际寄存器等效值 actual_target 为分母
                if abs(actual_target) > 1e-6:
                    rel_err = ((phys_val - actual_target) / actual_target) * 100.0
                    accuracy_val_str = f"{rel_err:+.4f} %"
                else:
                    # 目标值为 0 时，显示绝对误差
                    abs_err = phys_val - actual_target
                    accuracy_val_str = f"零偏误差: {abs_err:+.6f} {unit_str}"
            except Exception as e:
                dmm_val_str = f"读取超时/错误: {e}"
                accuracy_val_str = "错误"
        else:
            dmm_val_str = "未检测到万用表"
            accuracy_val_str = "未连接"
            
        type_str = "交流 (AC)" if is_ac else "直流 (DC)"
        
        feedback = (
            f"✅ [校准输出持续运行中]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 目标物理通道: 物理通道 {HWConfig.MapChannel(ch_api)} (API 通道 {ch_api})\n"
            f"📊 期望物理幅值: {value:.6f} {unit_str} ({type_str})\n"
            f"🎯 实际等效物理值: {actual_target:.6f} {unit_str} (根据高16位寄存器及校准反算)\n"
            f"⚙️ 当前校准参数: factor_dc={factor_dc:.8f}, bias={bias:.8f}, factor_ac={factor_ac:.8f}\n"
            f"📡 万用表量程配置: {dmm_msg}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"提示：此时硬件处于高压/大电流测试输出状态。点击 [停止输出] 以安全清空通道并归零信号。"
        )
        yield feedback, dmm_val_str, actual_target_str, accuracy_val_str
        time.sleep(0.5)

def stop_calibrated_output():
    """停止校准输出，归零全物理通道并停止读取"""
    global_state.is_verifying = False
    if not global_state.ser:
        return "错误：串口未连接", "已停止", "已停止", "已停止"
    try:
        reset_msg = reset_fpga_and_clear()
        return f"🛑 [输出已停止]\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{reset_msg}", "已关闭", "已停止", "已停止"
    except Exception as e:
        return f"❌ 停止输出时发生错误: {e}", "错误", "错误", "错误"

def force_stop_generation():
    """紧急停止硬件输出并归零物理通道"""
    if global_state.ser:
        try:
            clear_all_channels()
            return "成功发送紧急停止指令！全物理通道已被安全切断并归零。"
        except Exception as e:
            return f"发送紧急停止指令失败: {e}"
    return "错误：串口未连接"

# ==============================================================================
#  Gradio 生成器辅助包装函数 (避免 lambda 阻断 yield 机制)
# ==============================================================================
def run_auto_calibration_flow(ch_api, wait_sec, sample_sec):
    """一键全自动校准流程：按顺序测试交流(3) -> 0V/0A(2) -> 正直流(0) -> 负直流(1) -> 自动计算拟合并写入 JSON"""
    is_cur = ch_api >= 6
    unit_str = "A" if is_cur else "V"
    
    # 辅助函数，实时提取并维护4个测试点的状态显示
    def get_ui_tuple(msg, live_log_str=None, res_dc="", res_ac="", res_bias="", final_log="⌛ 自动序列测量进行中..."):
        p100_str = f"{global_state.measured_p100:.6f} {unit_str}" if global_state.measured_p100 is not None else "等待测量"
        n100_str = f"{global_state.measured_n100:.6f} {unit_str}" if global_state.measured_n100 is not None else "等待测量"
        zero_str = f"{global_state.measured_zero:.6f} {unit_str}" if global_state.measured_zero is not None else "等待测量"
        ac_str = f"{global_state.measured_ac:.6f} {unit_str}" if global_state.measured_ac is not None else "等待测量"
        if live_log_str is None:
            live_log_str = "⏳ 准备开始..."
        return (msg, live_log_str, p100_str, n100_str, zero_str, ac_str, res_dc, res_ac, res_bias, final_log)

    # 1. 预先清除之前的所有测值与缓存，确保全新开始
    global_state.measured_p100 = None
    global_state.measured_n100 = None
    global_state.measured_zero = None
    global_state.measured_ac = None
    global_state.variance_p100 = None
    global_state.variance_n100 = None
    global_state.variance_zero = None

    steps = [
        (3, "1/4 交流校准测量"),
        (2, "2/4 零偏直流测量"),
        (0, "3/4 正向直流测量"),
        (1, "4/4 反向直流测量")
    ]
    
    for step_idx, step_name in steps:
        # 迭代调用各步采样，并把最新的测量均值实时显示到界面
        for step_yield in output_and_auto_sample(ch_api, step_idx, wait_sec, sample_sec):
            # step_yield = (status_summary, live_data_log, p100_str, n100_str, zero_str, ac_str)
            status_msg, live_log, _, _, _, _ = step_yield
            yield get_ui_tuple(
                msg=f"🔄 [正在执行 {step_name}]\n{status_msg}", 
                live_log_str=live_log
            )
            time.sleep(0.01)
            
    # 所有步骤测试完成，执行一键拟合计算与 JSON 保存
    yield get_ui_tuple(
        msg="🏁 所有测试步已完成，正在执行高精度 WLS 最小二乘法拟合...",
        final_log="⌛ 正在拟合系数并写入 JSON 文件..."
    )
    
    try:
        res_summary, factor_dc_str, factor_ac_str, bias_str = calculate_and_save_coefficients(ch_api)
        yield get_ui_tuple(
            msg="🎉 全自动一键校准与拟合保存全部成功！",
            res_dc=factor_dc_str,
            res_ac=factor_ac_str,
            res_bias=bias_str,
            final_log=res_summary
        )
    except Exception as e:
        yield get_ui_tuple(
            msg=f"❌ 计算系数或保存失败: {e}",
            final_log=f"❌ 错误详情: {e}"
        )


# ==============================================================================
#  构建高颜值 Gradio Web 界面
# ==============================================================================
def create_ui():
    theme = gr.themes.Monochrome(
        primary_hue="cyan",
        secondary_hue="blue",
        neutral_hue="slate"
    )
    
    with gr.Blocks(theme=theme, title="FPGA 自动化高精度双增益校准仪表盘") as ui:
        gr.Markdown(
            """
            # 🚀 FPGA 自动化高精度双增益校准仪表盘
            本界面专用于高精度的电压和电流通道校准。直流采用三点拟合法消除零偏（偏置），交流折合根号 2 倍幅度峰值消除频响差。支持自动等待与交互式长周期平均记录，能够完美过滤功放的物理波动。
            """
        )
        
        # 1. 仪表与连接配置行
        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("### 📡 硬件设备连接")
                with gr.Group():
                    default_p = 'COM3' if os.name == 'nt' else '/dev/ttyAMA0'
                    serial_port = gr.Textbox(label="FPGA 串口地址", value=default_p, visible=False)
                    
                    # 万用表地址自动寻找与选择
                    multimeter_choices = find_multimeters()
                    with gr.Row():
                        multimeter_addr = gr.Dropdown(
                            choices=multimeter_choices, 
                            label="万用表 VISA 资源选择", 
                            value=multimeter_choices[0] if multimeter_choices else "", 
                            allow_custom_value=True,
                            scale=3
                        )
                        btn_scan = gr.Button("🔍 扫描万用表设备", scale=1)
                    btn_conn = gr.Button("🔌 一键初始化连接设备", variant="primary")
                    
                    # 电流通道测试模式选择
                    curr_meas_mode_ui = gr.Radio(
                        choices=[("使用采样电阻测电流 (75mΩ Shunt)", "shunt"), ("使用电流挡直接测电流", "direct")],
                        value="shunt",
                        label="⚡ 电流通道测量模式"
                    )
            
            with gr.Column(scale=3):
                gr.Markdown("### 📋 通信与连接状态日志")
                log_conn = gr.Textbox(label="系统日志", interactive=False, lines=5, placeholder="等待初始化连接...")

        # 2. 校准通道选择
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 🎯 校准通道选择")
                ch_choices = [
                    ("UA 电压 (通道 0)", 0),
                    ("UB 电压 (通道 1)", 1),
                    ("UC 电压 (通道 2)", 2),
                    ("UX 电压 (通道 3)", 3),
                    ("UY 电压 (通道 4)", 4),
                    ("UZ 电压 (通道 5)", 5),
                    ("IA 电流 (通道 6)", 6),
                    ("IB 电流 (通道 7)", 7),
                    ("IC 电流 (通道 8)", 8),
                    ("IX 电流 (通道 9)", 9),
                    ("IY 电流 (通道 10)", 10),
                    ("IZ 电流 (通道 11)", 11)
                ]
                ch_select = gr.Radio(choices=ch_choices, value=0, label="🎯 选择校准物理通道")
                
        def update_ch_topology(ch_val):
            is_c = (ch_val >= 6)
            
            # 清空所有历史采样缓存、方差记录
            global_state.measured_p100 = None
            global_state.measured_n100 = None
            global_state.measured_zero = None
            global_state.measured_ac = None
            global_state.variance_p100 = None
            global_state.variance_n100 = None
            global_state.variance_zero = None
            
            # 手动校验输入默认值自适应：电流默认 0.5A，电压默认 57.735V
            new_val = 0.5 if is_c else 57.735
            
            return (
                new_val, 
                "未测试", 
                "未测试", 
                "未测试", 
                "未测试", 
                "", 
                "", 
                "", 
                "", 
                "🔄 校准通道已切换，所有历史采样缓存、方差记录及系数显示已自动清空重置。"
            )


        # 3. 实时采样波动监控 (数据过滤)
        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### 📈 实时采样波动监控 (数据过滤)")
                live_data_log = gr.Textbox(
                    label="万用表实时采样波动统计与数据列表",
                    interactive=False,
                    lines=8,
                    max_lines=10,
                    placeholder="等待万用表首个采样数据..."
                )
                
            with gr.Column(scale=2):
                with gr.Group():
                    gr.Markdown("### ⚙️ 校准采样参数配置")
                    slider_wait = gr.Slider(minimum=1, maximum=60, value=5, step=1, label="等待时间 (秒)", info="功放输出开启后的硬件稳定等待时间 (默认5s，最大60s)")
                    slider_sample = gr.Slider(minimum=1, maximum=60, value=5, step=1, label="采集时间 (秒)", info="万用表连续数据采样以计算长周期均值的时长 (默认5s，最大60s)")
                
                log_sampling = gr.Textbox(label="采样反馈信息", interactive=False, lines=4)

        # 4. 自动化一键校准流程（左侧一键触发与采样均值，右侧结果系数与写入详情）
        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### ⚙️ 一键全自动校准工作流")
                with gr.Group():
                    btn_start_auto_cal = gr.Button("🚀 开始一键全自动校准 (交流 ➡️ 零偏 ➡️ 正直流 ➡️ 负直流)", variant="primary")
                    btn_emergency = gr.Button("🚨 紧急切断/硬件清零输出", variant="stop")
                
                gr.Markdown("#### 📊 实时记录各测试点均值")
                with gr.Group():
                    with gr.Row():
                        ac_val = gr.Textbox(label="1. 交流均值 (50Hz)", value="未测试", interactive=False)
                        zero_val = gr.Textbox(label="2. 零偏直流均值", value="未测试", interactive=False)
                    with gr.Row():
                        p100_val = gr.Textbox(label="3. 正向直流均值", value="未测试", interactive=False)
                        n100_val = gr.Textbox(label="4. 反向直流均值", value="未测试", interactive=False)
                
            with gr.Column(scale=2):
                gr.Markdown("### 🏁 线性回归与交流校准结果")
                
                with gr.Group():
                    res_factor_dc = gr.Textbox(label="直流增益系数 [factor_dc]", interactive=False)
                    res_factor_ac = gr.Textbox(label="交流增益系数 [factor_ac]", interactive=False)
                    res_bias = gr.Textbox(label="直流偏置偏移 [bias]", interactive=False)
                    
                log_final = gr.Textbox(label="校准与 JSON 写入结果详情", interactive=False, lines=6)

        # 5. 校准效果手动校验
        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### 🔍 校准效果手动校验")
                with gr.Group():
                    with gr.Row():
                        verify_val = gr.Number(label="目标物理值输入 (V / A)", value=57.735, precision=6)
                        verify_type = gr.Radio(choices=[("直流 (DC)", False), ("交流 (AC)", True)], value=True, label="信号类型")
                    with gr.Row():
                        btn_out_calib = gr.Button("🚀 开启已校准输出", variant="primary")
                        btn_stop_calib = gr.Button("🛑 停止输出", variant="stop")
                    with gr.Row():
                        dmm_verify_val = gr.Textbox(label="万用表实时测得值 (V / A)", value="未启动", interactive=False, text_align="center")
                        actual_target_val = gr.Textbox(label="实际寄存器等效值 (V / A)", value="未启动", interactive=False, text_align="center")
                        accuracy_verify_val = gr.Textbox(label="校准后相对误差 (%)", value="未启动", interactive=False, text_align="center")
            with gr.Column(scale=2):
                log_verify = gr.Textbox(label="校验输出反馈日志", interactive=False, lines=7, placeholder="此处将实时展示校准转换公式、系数应用、DDS寄存器计算和FPGA高16位截断实际等效输出。")

        # 6. 绑定 Gradio 前端页面控制逻辑
        
        def scan_multimeter_devices():
            """手动扫描并更新 VISA 万用表设备列表"""
            new_choices = find_multimeters()
            default_val = new_choices[0] if (new_choices and "未发现" not in new_choices[0] and "错误" not in new_choices[0]) else ""
            return gr.Dropdown(choices=new_choices, value=default_val)
        
        # 6.1 初始化连接与测量模式绑定
        btn_conn.click(fn=connect_devices, inputs=[serial_port, multimeter_addr], outputs=[log_conn])
        btn_scan.click(fn=scan_multimeter_devices, outputs=[multimeter_addr])
        curr_meas_mode_ui.change(
            fn=lambda m: setattr(global_state, 'curr_meas_mode', m),
            inputs=[curr_meas_mode_ui]
        )
        
        # 6.2 紧急切断逻辑
        btn_emergency.click(fn=force_stop_generation, outputs=[log_sampling])
        
        # 6.3 一键全自动校准流程事件绑定
        btn_start_auto_cal.click(
            fn=run_auto_calibration_flow,
            inputs=[ch_select, slider_wait, slider_sample],
            outputs=[
                log_sampling,
                live_data_log,
                p100_val,
                n100_val,
                zero_val,
                ac_val,
                res_factor_dc,
                res_factor_ac,
                res_bias,
                log_final
            ]
        )


        # 6.5 校准效果手动校验控制
        btn_out_calib.click(
            fn=run_calibrated_verification_loop,
            inputs=[ch_select, verify_type, verify_val],
            outputs=[log_verify, dmm_verify_val, actual_target_val, accuracy_verify_val]
        )
        btn_stop_calib.click(
            fn=stop_calibrated_output,
            outputs=[log_verify, dmm_verify_val, actual_target_val, accuracy_verify_val]
        )
        
        # 6.6 通道切换联动 (解决变量声明顺序 UnboundLocalError)
        ch_select.change(
            fn=update_ch_topology,
            inputs=[ch_select],
            outputs=[
                verify_val,
                p100_val,
                n100_val,
                zero_val,
                ac_val,
                res_factor_dc,
                res_factor_ac,
                res_bias,
                log_final,
                log_sampling
            ]
        )
        
    return ui

if __name__ == "__main__":
    print("[System] 正在初始化高精度 FPGA 双增益校准仪表盘 Web 服务...")
    app_ui = create_ui()
    # 启动在 7860 端口上
    app_ui.launch(server_name="0.0.0.0", server_port=7860, show_error=True)
