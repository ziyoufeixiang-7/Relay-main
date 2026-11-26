"""
[实验名称] Handler 模板
=======================

使用方法:
1. 复制此文件到 api/ 目录下，命名为 XxxHdl.py (Xxx = 实验名)
2. 修改 MODULE_KEYS, 类名, CompileStart, InterceptWsEvent
3. 参考 docs/skill_hdl_development.md 了解完整接口契约
4. 参考 docs/api/api_xxx.md 了解前端协议
"""

import logging
from typing import Dict, Any, Optional, List

from logic.FPGACodec import HWConfig

logger = logging.getLogger("XxxHdl")


class XxxHdl:
    MODULE_KEYS = ["xxx"]  # 路由注册标识，匹配前端 {"module": "xxx"}

    def __init__(self):
        # ── 通道配置 ──
        self.vChs = [0, 1, 2, 3, 4, 5]
        self.iChs = [6, 7, 8, 9, 10, 11]

        # ── 运行时状态 ──
        self.isActive = False
        self.lastValueUpdateHwTs = 0
        # TODO: 添加实验特有的状态变量

    def ResetState(self):
        """stop 时被 CmdRouter 调用，必须重置所有运行时状态"""
        self.isActive = False
        self.lastValueUpdateHwTs = 0
        # TODO: 重置所有实验状态变量

    # ═══════════════════════════════════════════════════
    #  物理值 → 寄存器值 转换
    # ═══════════════════════════════════════════════════

    def _phys_to_reg_dict(self, phys_dict: dict) -> dict:
        """将前端物理值字典转换为 FPGA 寄存器值字典"""
        reg_d = {}
        for ch_str, layers in phys_dict.items():
            ch_idx = int(ch_str)
            is_cur = ch_idx in self.iChs
            reg_d[ch_str] = {}
            for l_idx_str, vals in layers.items():
                l_idx = int(l_idx_str)
                v_phys = vals[0]

                # Layer 0 用绝对校准, 其他层用增量校准
                if l_idx == 0:
                    v_calib = self.mgr.ApplyAbsolute(ch_idx, v_phys)
                else:
                    v_calib = self.mgr.ApplyDelta(ch_idx, v_phys)

                if abs(v_calib) < 0.000001:
                    v_calib = 0.0
                a_reg = HWConfig.ConvertAmpToReg(v_calib, is_cur)
                p_reg = (HWConfig.ConvertFreqToReg(vals[1]) if l_idx == 0
                         else HWConfig.ConvertPhaseToReg(vals[1]))
                reg_d[ch_str][l_idx_str] = [a_reg, p_reg]
        return reg_d

    # ═══════════════════════════════════════════════════
    #  编译入口
    # ═══════════════════════════════════════════════════

    async def ProcessStart(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """统一入口：编译前端 start 配置为引擎配置"""
        return self.CompileStart(params)

    def CompileStart(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        核心编译函数：将前端物理参数转为 USEEngine 节点配置。
        
        返回值格式:
        {
            "sys": { "vChannels": [...], "iChannels": [...], "debounce": N },
            "nodes": [ { Node Object }, ... ]
        }
        """
        self.ResetState()
        self.isActive = True

        sys_cfg = params.get("sys") or {}
        debounce = sys_cfg.get("debounce", 61)

        # ── 编译节点 ──
        engine_nodes = []

        # TODO: 根据前端 params 构建 Node Object 列表
        # 重要: 未配置的通道必须强制补全为 [0, freq_50_reg] (DC 层)
        freq_50_reg = HWConfig.ConvertFreqToReg(50.0)

        # 示例: 一个静态节点
        node_statics_reg = {}  # TODO: 调用 _phys_to_reg_dict() 转换
        for ch_idx in range(16):
            ch_s = str(ch_idx)
            if ch_s not in node_statics_reg:
                node_statics_reg[ch_s] = {"0": [0, freq_50_reg]}

        engine_nodes.append({
            "id": 0,
            "doActions": [],
            "static": node_statics_reg,
            "triggers": [
                {"condition": "TIME_OUT", "timeout": 5000, "nextId": -2}
            ]
        })

        return {
            "sys": {
                "vChannels": self.vChs,
                "iChannels": self.iChs,
                "debounce": debounce
            },
            "nodes": engine_nodes
        }

    # ═══════════════════════════════════════════════════
    #  遥测事件拦截
    # ═══════════════════════════════════════════════════

    def InterceptWsEvent(self, data: dict) -> list:
        """
        拦截并转译 USEEngine 的原始遥测事件。
        返回需要发送给前端的消息列表。
        
        data["type"] 可能的值:
        - "value_update": 包含 tick 时是引擎遥测，不包含 tick 时是 DI SOE
        - "trigger": FSM 触发事件
        - "terminal": FSM 自然结束
        """
        t_type = data.get("type", "")
        outMsgs = []

        if not self.isActive:
            return [data]

        if t_type == "value_update":
            if "tick" not in data and "di" in data:
                # ── DI SOE 事件 (无 tick): 来自硬件中断 ──
                hw_ts = data.get("hw_ts", 0)
                di = data["di"]
                # TODO: 处理 DI 变化逻辑 (计时器启停等)

                outMsgs.append({
                    "module": self.MODULE_KEY,
                    "type": "value_update",
                    "di": di
                })
                return outMsgs
            else:
                # ── 引擎遥测 (有 tick): 来自 USEEngine ──
                self.lastValueUpdateHwTs = data.get("hw_ts", self.lastValueUpdateHwTs)
                # TODO: 处理引擎遥测 (节点 ID、DO 状态等)

                outMsgs.append({
                    "module": self.MODULE_KEY,
                    "type": "value_update",
                    # TODO: 添加前端需要的字段
                })
                return outMsgs

        elif t_type == "trigger":
            cond = data.get("condition", "")
            next_id = data.get("next_id")
            hw_ts = data.get("hw_ts", 0)

            # 计算动作时间 (hw_ts 差值, U32 溢出安全)
            elapsed_ms = ((hw_ts - self.lastValueUpdateHwTs) & 0xFFFFFFFF) / 1000.0

            # TODO: 根据 condition 和状态机阶段处理
            # - 记录动作时间
            # - 生成 Hot Update (如需要)

            outMsgs.append({
                "module": self.MODULE_KEY,
                "type": "trigger",
                "condition": cond,
                "next_id": next_id
            })
            return outMsgs

        elif t_type == "terminal":
            self.isActive = False

            # TODO: 构建最终测量报告
            outMsgs.append({
                "module": self.MODULE_KEY,
                "type": "report",
                "data": {
                    # TODO: 报告字段
                }
            })
            outMsgs.append({
                "module": self.MODULE_KEY,
                "type": "stop"
            })
            return outMsgs

        return [data]
