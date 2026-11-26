"""
[实验名称] 集成测试模板
=======================

使用方法:
1. 复制此文件到 tests/ 目录下，命名为 TestXxx.py
2. 修改 MODULE_NAME, start payload, 以及 DI 模拟逻辑
3. 参考 docs/skill_hdl_development.md §10 了解测试框架
"""

import sys
import os
import asyncio
import json

os.environ["USE_LOG_LEVEL"] = "INFO"

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(current_dir))

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from utils.SysLogger import GetLogger

logger = GetLogger("TestXxx")

MODULE_NAME = "xxx"  # 修改为实验模块名

# ═══════════════════════════════════════════════════
#  WebSocket 回调 (模拟前端接收)
# ═══════════════════════════════════════════════════

async def wsCallback(data):
    if isinstance(data, str):
        data = json.loads(data)
    t = data.get("type", "")
    module = data.get("module", "")

    if module != MODULE_NAME and t != "load":
        return  # 过滤非本模块消息

    if t == "value_update":
        print(f">>> [WS -> Frontend] VALUE_UPDATE: {json.dumps(data, ensure_ascii=False)[:120]}...")
    elif t == "trigger":
        print(f"\n>>> [TRIGGER] {data.get('condition')} -> next_id={data.get('next_id')} (hw_ts={data.get('hw_ts')})")
    elif t == "report":
        print(f"\n>>> [REPORT]")
        print(f"=======================================================")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"=======================================================\n")
    elif t == "stop":
        print(f"\n>>> [STOP] 实验结束")
    elif t == "error":
        print(f"\n>>> [ERROR] {data.get('msg')}")
    elif t == "load":
        pass  # 温度遥测，忽略

# ═══════════════════════════════════════════════════
#  MockHWProtect (硬件保护桩)
# ═══════════════════════════════════════════════════

class MockHWProtect:
    def __init__(self):
        self.channel_temperatures = {str(i): 25.0 for i in range(12)}
        self.pin_amp_enable = type('obj', (object,), {'value': 1})
    def SetAmplifierEnable(self, enable): pass
    def SetPower(self, a, b): pass

# ═══════════════════════════════════════════════════
#  DI SOE 模拟器
# ═══════════════════════════════════════════════════

async def simulate_di_soe(dispatcher, di_value, hw_ts):
    """模拟一个 DI SOE 事件 (硬件中断)"""
    if dispatcher.useEngine:
        dispatcher.useEngine.handle_soe(di_value, hw_ts)
    await dispatcher.DispatchWsEvent({
        "type": "value_update",
        "di": di_value,
        "hw_ts": hw_ts
    })

# ═══════════════════════════════════════════════════
#  主测试流程
# ═══════════════════════════════════════════════════

async def runTest():
    logger.info(f"启动 {MODULE_NAME} 测试...")

    hwGateway = HWGateway()
    dispatcher = CmdRouter(
        hwGateway=hwGateway,
        hwProtect=MockHWProtect(),
        wsSendCb=wsCallback
    )
    hwGateway.dispatcher = dispatcher
    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(1)

    # ══════════════════════════════════════════════
    #  构建 start payload (从前端 API 文档复制并修改)
    # ══════════════════════════════════════════════

    payload = {
        "module": MODULE_NAME,
        "cmd": "start",
        "params": {
            "sys": { "debounce": 15 },
            # TODO: 填写实验参数
        }
    }

    print(f"\n<<< [Frontend -> WS] SEND ({MODULE_NAME} start!)")
    await dispatcher.HandleCommand(payload)

    # ══════════════════════════════════════════════
    #  模拟实验执行 (DI 事件、按键触发等)
    # ══════════════════════════════════════════════

    await asyncio.sleep(2)

    # 示例: 模拟按键触发跳转
    # await dispatcher.HandleCommand({"module": MODULE_NAME, "cmd": "next_node"})

    # 示例: 模拟 DI SOE (DI_A 闭合, hw_ts=100000μs)
    # await simulate_di_soe(dispatcher, 0xFE, 100000)  # bit0=0 表示 DI_A 闭合 (DI_OPEN_IS_HIGH)

    await asyncio.sleep(5)

    # ══════════════════════════════════════════════
    #  停止实验
    # ══════════════════════════════════════════════

    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(0.5)
    hw_task.cancel()
    logger.info("测试完成。")

# ═══════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        asyncio.run(runTest())
    except KeyboardInterrupt:
        logger.info("已退出。")
    finally:
        logger.info("触发硬件保护性强制复归...")
        try:
            import serial
            from logic.FPGACodec import HWCodec
            from comms.HWGateway import HWGateway as HWG
            with serial.Serial(HWG.PORT, HWG.BAUDRATE, timeout=1) as ser:
                ser.write(HWCodec.FRAME_SYS_RESET)
            logger.info("硬件已安全复归。")
        except Exception as e:
            logger.error(f"紧急复归失败: {e}")
