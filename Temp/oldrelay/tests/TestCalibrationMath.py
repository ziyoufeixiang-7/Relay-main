# -*- coding: utf-8 -*-
"""
FPGA 自动化校准数学公式与 API 兼容性单元测试
==========================================
1. 验证直流三点法与交流 RMS 法的数学计算公式（包含反相偏置与极性修正）。
2. 验证电流通道 75mR 采样电阻物理值折算。
3. 验证 ApiHdlMgr 中升级后的 ApplyAbsolute, ApplyDelta 对双增益系数的感知和无缝向后兼容性。
4. 验证 PhysToReg 和 MakeChannelReg 方法在各层级下正确应用双通道增益。
"""
import sys
import os
import math
import json
import unittest

# 将根目录添加到 python 搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.ApiHdlMgr import ApiHdlMgr
from logic.FPGACodec import HWConfig

class TestCalibrationMath(unittest.TestCase):
    def setUp(self):
        # 创建临时配置文件路径，避免污染现有的 config/calibration.json
        self.test_dir = os.path.dirname(os.path.abspath(__file__))
        self.temp_config_path = os.path.join(self.test_dir, "temp_calibration.json")
        
        # 备份并替换 ApiHdlMgr 的配置文件路径
        self.original_filepath = ApiHdlMgr().calib_filepath
        
        # 初始化一个新的 ApiHdlMgr，指向临时文件
        self.mgr = ApiHdlMgr()
        self.mgr.calib_filepath = self.temp_config_path
        self.mgr.calib_data = {}
        self.mgr.SaveCalib()

        # 备份并替换 global_state.calib_mgr 的路径与数据，避免 WLS 拟合测试写回主配置
        from calibration.autocal import global_state
        self.original_global_filepath = global_state.calib_mgr.calib_filepath
        self.original_global_calib_data = global_state.calib_mgr.calib_data.copy() if hasattr(global_state.calib_mgr, 'calib_data') else {}
        global_state.calib_mgr.calib_filepath = self.temp_config_path
        global_state.calib_mgr.calib_data = {}

    def tearDown(self):
        # 恢复 global_state.calib_mgr
        from calibration.autocal import global_state
        global_state.calib_mgr.calib_filepath = self.original_global_filepath
        global_state.calib_mgr.calib_data = self.original_global_calib_data

        # 清理临时配置文件
        if os.path.exists(self.temp_config_path):
            try:
                os.remove(self.temp_config_path)
            except Exception:
                pass

    def test_dc_and_ac_calibration_math_voltage_positive(self):
        """测试电压通道正向增益校准数学计算"""
        # 模拟 100V 电压通道 (API 通道 0)
        ch_api = 0
        target_val_dc = 100.0
        target_val_ac = 57.735
        
        # 假设硬件有 5% 的正向增益误差 (输出为 输入的 1.05 倍) 和 +2.0V 零偏
        # 使得：
        # +100V 对应测得 100 * 1.05 + 2.0 = 107.0V
        # -100V 对应测得 -100 * 1.05 + 2.0 = -103.0V
        # 0V 对应测得 2.0V
        # 57.735V AC RMS 对应测得 57.735 * 1.03 = 59.46705V RMS (交流增益独立为 1.03)
        
        measured_p100 = 107.0
        measured_n100 = -103.0
        measured_zero = 2.0
        measured_ac = 57.735 * 1.03
        
        # 1. 拟合直流参数
        G_dc = (measured_p100 - measured_n100) / (2.0 * target_val_dc)
        O_dc = measured_zero
        
        self.assertAlmostEqual(G_dc, 1.05)
        self.assertAlmostEqual(O_dc, 2.0)
        
        factor_dc = -1.0 / G_dc
        bias = O_dc * factor_dc
        
        self.assertAlmostEqual(factor_dc, -1.0 / 1.05)
        self.assertAlmostEqual(bias, 2.0 * factor_dc)
        
        # 2. 拟合交流参数 (继承直流极性)
        G_ac = measured_ac / target_val_ac
        G_unified_ac = math.copysign(G_ac, G_dc)
        factor_ac = -1.0 / G_unified_ac
        
        self.assertAlmostEqual(G_ac, 1.03)
        self.assertAlmostEqual(G_unified_ac, 1.03)
        self.assertAlmostEqual(factor_ac, -1.0 / 1.03)

    def test_dc_and_ac_calibration_math_voltage_negative(self):
        """测试电压通道反相（负增益）校准数学计算"""
        # 模拟 100V 电压通道 (API 通道 0)
        ch_api = 0
        target_val_dc = 100.0
        target_val_ac = 57.735
        
        # 假设功放为反相放大器，增益为 -0.95 (即输出为 输入的 -0.95 倍) 和 +1.5V 零偏
        # 使得：
        # +100V 输入时，物理实测值 = 100 * (-0.95) + 1.5 = -93.5V
        # -100V 输入时，物理实测值 = -100 * (-0.95) + 1.5 = 96.5V
        # 0V 输入时，物理实测值 = 1.5V
        # 57.735V AC 对应测得 57.735 * 0.98 = 56.5803V RMS (交流增益独立为 0.98)
        
        measured_p100 = -93.5
        measured_n100 = 96.5
        measured_zero = 1.5
        measured_ac = 57.735 * 0.98
        
        # 拟合直流
        G_dc = (measured_p100 - measured_n100) / (2.0 * target_val_dc)
        O_dc = measured_zero
        
        self.assertAlmostEqual(G_dc, -0.95)
        self.assertAlmostEqual(O_dc, 1.5)
        
        factor_dc = -1.0 / G_dc
        bias = O_dc * factor_dc
        
        # factor_dc 应该是正数: -1 / -0.95 = 1.05263
        self.assertAlmostEqual(factor_dc, 1.0526315789473684)
        self.assertAlmostEqual(bias, 1.5 * factor_dc)
        
        # 拟合交流 (极性应修正为负)
        G_ac = measured_ac / target_val_ac
        G_unified_ac = math.copysign(G_ac, G_dc)
        factor_ac = -1.0 / G_unified_ac
        
        self.assertAlmostEqual(G_unified_ac, -0.98)
        self.assertAlmostEqual(factor_ac, -1.0 / -0.98)

    def test_current_channel_shunt_conversion(self):
        """测试电流通道通过 75mR 采样电阻物理测值转换"""
        # API 通道 6 为 IA 电流通道
        # 电流通道测试中，万用表实际上是跨测在 75mR 采样电阻两端，测得的是电压值 (V)
        # 物理采样转换公式: I = V_dmm / 0.075
        
        # 假设输出 0.5A DC
        # 采样电阻上测得的理想电压应该是 0.5 * 0.075 = 0.0375 V
        # 假设实测电压为 0.0380 V
        volt_dmm = 0.0380
        phys_val = volt_dmm / 0.075
        
        self.assertAlmostEqual(phys_val, 0.0380 / 0.075)

    def test_api_hdl_mgr_dual_factor_compatibility(self):
        """测试 ApiHdlMgr 的双增益读取和传统兼容性兜底"""
        ch_idx = 2
        
        # 1. 写入双增益系数
        factor_dc = -0.9555
        factor_ac = -0.9529
        bias = 0.0182
        self.mgr.UpdateCalibDual(ch_idx, factor_dc, factor_ac, bias)
        
        # 重新加载确认加载正常
        self.mgr.LoadCalib()
        
        # 验证 ApplyAbsolute (用于 DC 0层绝对值) 优先采用 factor_dc
        # Command = bias - value * factor_dc
        val = 100.0
        expected_abs = bias - val * factor_dc
        actual_abs = self.mgr.ApplyAbsolute(ch_idx, val)
        self.assertAlmostEqual(actual_abs, expected_abs)
        
        # 验证 ApplyDelta 在 l_idx=0 (DC层) 时采用 factor_dc
        # CommandDelta = - value * factor_dc
        expected_delta_dc = - val * factor_dc
        actual_delta_dc = self.mgr.ApplyDelta(ch_idx, val, l_idx=0)
        self.assertAlmostEqual(actual_delta_dc, expected_delta_dc)
        
        # 验证 ApplyDelta 在 l_idx=1 (AC层) 时采用 factor_ac
        # CommandDelta = - value * factor_ac
        expected_delta_ac = - val * factor_ac
        actual_delta_ac = self.mgr.ApplyDelta(ch_idx, val, l_idx=1)
        self.assertAlmostEqual(actual_delta_ac, expected_delta_ac)
        
        # 2. 测试向后兼容性 (只有传统 factor 字段时)
        self.mgr.calib_data[str(ch_idx)] = {
            "factor": -0.9500,
            "bias": 0.0100
        }
        self.mgr.SaveCalib()
        self.mgr.LoadCalib()
        
        # ApplyAbsolute 应退回采用 factor
        expected_legacy_abs = 0.0100 - val * (-0.9500)
        self.assertAlmostEqual(self.mgr.ApplyAbsolute(ch_idx, val), expected_legacy_abs)
        
        # ApplyDelta 也应退回采用 factor
        expected_legacy_delta = - val * (-0.9500)
        self.assertAlmostEqual(self.mgr.ApplyDelta(ch_idx, val, l_idx=0), expected_legacy_delta)
        self.assertAlmostEqual(self.mgr.ApplyDelta(ch_idx, val, l_idx=1), expected_legacy_delta)

    def test_phys_to_reg_and_make_channel_reg(self):
        """测试 PhysToReg 和 MakeChannelReg 方法在各层级下正确应用双通道增益"""
        ch_idx = 4  # 电压通道 UY
        
        factor_dc = -0.9500
        factor_ac = -0.9200
        bias = 0.0200
        self.mgr.UpdateCalibDual(ch_idx, factor_dc, factor_ac, bias)
        
        # --- 测试 PhysToReg ---
        # 构造物理输入值字典：Layer 0 (DC) 输入 50.0V，Layer 1 (AC) 输入 10.0V
        phys_dict = {
            str(ch_idx): {
                "0": [50.0, 50.0],  # [幅值, 频率]
                "1": [10.0, 30.0]   # [幅值, 相位]
            }
        }
        
        # 转换并验证
        reg_dict = self.mgr.PhysToReg(phys_dict, is_delta=False)
        
        # 计算 Layer 0 DC 寄存器期望值
        # v_calib = bias - 50.0 * factor_dc = 0.02 - 50.0 * (-0.95) = 47.52
        expected_dc_v = bias - 50.0 * factor_dc
        expected_dc_amp = HWConfig.ConvertAmpToReg(expected_dc_v, isCurrent=False)
        
        # 计算 Layer 1 AC 寄存器期望值
        # v_calib = - 10.0 * factor_ac = -10.0 * (-0.92) = 9.20
        expected_ac_v = - 10.0 * factor_ac
        expected_ac_amp = HWConfig.ConvertAmpToReg(expected_ac_v, isCurrent=False)
        
        self.assertEqual(reg_dict[str(ch_idx)]["0"][0], expected_dc_amp)
        self.assertEqual(reg_dict[str(ch_idx)]["1"][0], expected_ac_amp)
        
        # --- 测试 MakeChannelReg ---
        # MakeChannelReg 专用于生成 Layer 0 (DC层，输入恒为 0V) 和 Layer 1 (AC层) 的联合值
        reg_channel = self.mgr.MakeChannelReg(ch_idx, amplitude=15.0, angle_deg=45.0, freq_hz=50.0)
        
        # DC Layer 0 校验:
        # amplitude = 0, ApplyAbsolute(0.0) = bias - 0 * factor_dc = bias
        expected_dc_ch_v = bias
        expected_dc_ch_amp = HWConfig.ConvertAmpToReg(expected_dc_ch_v, isCurrent=False)
        
        # AC Layer 1 校验:
        # amplitude = 15.0, ApplyDelta(15.0, l_idx=1) = - 15.0 * factor_ac = -15 * (-0.92) = 13.80
        expected_ac_ch_v = - 15.0 * factor_ac
        expected_ac_ch_amp = HWConfig.ConvertAmpToReg(expected_ac_ch_v, isCurrent=False)
        
        self.assertEqual(reg_channel["0"][0], expected_dc_ch_amp)
        self.assertEqual(reg_channel["1"][0], expected_ac_ch_amp)

    def test_wls_linear_fitting_equal_variance(self):
        """测试直流 WLS 拟合在均等方差下与 OLS 一致"""
        from calibration.autocal import global_state, calculate_and_save_coefficients
        
        # 模拟电压通道 (API 通道 0)
        ch_api = 0
        global_state.measured_p100 = 105.0
        global_state.measured_n100 = -95.0
        global_state.measured_zero = 5.0
        global_state.measured_ac = 57.735
        
        # 均等方差 (e.g. 1e-6)
        global_state.variance_p100 = 1e-6
        global_state.variance_n100 = 1e-6
        global_state.variance_zero = 1e-6
        
        # 运行 WLS 计算并保存
        res_summary, factor_dc_str, factor_ac_str, bias_str = calculate_and_save_coefficients(ch_api)
        
        # 检查是否成功
        self.assertNotIn("错误", res_summary)
        
        # 期望值：
        # G_dc = 1.0 -> factor_dc = -1 / G_dc = -1.0
        # O_dc = 5.0 -> bias = O_dc * factor_dc = -5.0
        factor_dc = float(factor_dc_str)
        bias = float(bias_str)
        self.assertAlmostEqual(factor_dc, -1.0)
        self.assertAlmostEqual(bias, -5.0)

    def test_wls_linear_fitting_unbalanced_variance(self):
        """测试直流 WLS 拟合在不平衡方差下成功过滤零点高噪波动"""
        from calibration.autocal import global_state, calculate_and_save_coefficients
        
        # 模拟电压通道 (API 通道 0)
        ch_api = 0
        global_state.measured_p100 = 105.0
        global_state.measured_n100 = -95.0
        global_state.measured_zero = 20.0  # 极大偏差扰动！
        global_state.measured_ac = 57.735
        
        # 不平衡方差：正负点极高置信度 (1e-6)，零点极低置信度 (10.0)
        global_state.variance_p100 = 1e-6
        global_state.variance_n100 = 1e-6
        global_state.variance_zero = 10.0
        
        # 运行 WLS 计算并保存
        res_summary, factor_dc_str, factor_ac_str, bias_str = calculate_and_save_coefficients(ch_api)
        
        # 检查是否成功
        self.assertNotIn("错误", res_summary)
        
        # 期望值：WLS 应该近乎忽略零点偏置(20V)，利用高置信度的正负点进行拟合，
        # G_dc 应该保持为 1.0，O_dc 应该在 ~5.0 左右而非 OLS 的 10.0
        factor_dc = float(factor_dc_str)
        bias = float(bias_str)
        self.assertAlmostEqual(factor_dc, -1.0, places=3)
        self.assertAlmostEqual(bias, -5.0, places=3)

    def test_format_live_data_log(self):
        """测试实时采样日志格式化输出"""
        from calibration.autocal import global_state, format_live_data_log
        
        # 1. 空采样列表状态
        with global_state.lock:
            global_state.sample_list = []
        res = format_live_data_log()
        self.assertIn("等待", res)
        
        # 2. 填充数据测试 (电流通道)
        global_state.is_current = True
        with global_state.lock:
            global_state.sample_list = [1.0, 1.1, 0.9]
        res = format_live_data_log()
        self.assertIn("已采集点数: 3", res)
        self.assertIn("最新测值: 0.900000 A", res)
        self.assertIn("实时平均值: 1.000000 A", res)
        self.assertIn("实时方差", res)

        # 3. 填充数据测试 (电压通道)
        global_state.is_current = False
        with global_state.lock:
            global_state.sample_list = [100.0, 100.2, 99.8]
        res = format_live_data_log()
        self.assertIn("已采集点数: 3", res)
        self.assertIn("最新测值: 99.800000 V", res)
        self.assertIn("实时平均值: 100.000000 V", res)
        self.assertIn("实时方差", res)

if __name__ == "__main__":
    unittest.main()

