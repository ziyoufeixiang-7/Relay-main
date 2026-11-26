import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENABLE_DEBUG_LOG = True
os.environ["USE_LOG_LEVEL"] = "DEBUG" if ENABLE_DEBUG_LOG else "INFO"

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from logic.HWProtect import HWProtect
from utils.SysLogger import GetLogger

logger = GetLogger("TestAcdc")

async def purePythonEventCallback(data: dict):
    """
    Simulates the WebSocket server sending JSON directly to the Frontend UI.
    """
    try:
        t = data.get("type", "")
        
        # 只打印前120个字符避免终端刷屏太夸张，重点打印 Report 和 Error
        if t == "value_update":
            # For value update, we can print a truncated version to just see physical floats
            payload_str = json.dumps(data)
            print(f">>> [WS -> Frontend] VALUE_UPDATE: {payload_str[:150]}...")
        else:
            print(f"\n====================== [WS -> Frontend] {t.upper()} EVENT ======================")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("======================================================================\n")
            
    except Exception as e:
        print(f"Failed to process payload: {data}. Error: {e}")


async def runAcdcTest():
    logger.info("启动 ACDC 纯 Python 通道协议打通压测...")
    
    hwGateway = HWGateway()
    hwProtect = HWProtect()
    dispatcher = CmdRouter(hwGateway=hwGateway, hwProtect=hwProtect, wsSendCb=purePythonEventCallback)

    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(2.0)
    
    logger.info("下发前端 JSON -> 呼叫 ACDC Handler 生成指令...")
    
    payload = {
        "module": "ac_test",
        "cmd": "start",
        "params": {
            "sys": {
                "mode": 1,               # Auto mode
                "stepTime": 4,        # 200ms per step
                "changeMode": 0,         # Measure return
                "returnMode": 0,         # Stop on trip & recover
                "debounce": 10,
                "logicMask": 1,
                "doMask": 3,
                "doCtrlMask": 5
            },
            "payload": {
                "enablePreTestReset": True,
                "preTestResetTime": 500,
                "enableStepReset": True,
                "stepResetMode": 1,
                "stepResetTime": 4,
                "resetTableData": {
                    "1": {"0": [0, 50.0]}
                }
            },
            "limits": {
                "0": [200.0, 1000.0],
                "1": [200.0, 1000.0],
                "2": [200.0, 1000.0],
                "3": [200.0, 1000.0],
                "4": [200.0, 1000.0],
                "5": [200.0, 1000.0],
                "6": [5.0, 1000.0],
                "7": [5.0, 1000.0],
                "8": [5.0, 1000.0],
                "9": [5.0, 1000.0],
                "10": [5.0, 1000.0],
                "11": [5.0, 1000.0]
            },
            "statics": {
                "0": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "1": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "2": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "3": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "4": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "5": {"0": [0, 50.0], "1": [1.4142*10, 0.0]},
                "6": {"0": [0, 50.0], "1": [1.4142*1, 0.0]},
                "7": {"0": [0, 50.0], "1": [1.4142*1, 0.0]},
                "8": {"0": [0, 50.0], "1": [1.4142*1, 0.0]},
                "9": {"0": [0, 50.0], "1": [1.4142*1, 0.0]},
                "10":{"0": [0, 50.0], "1": [1.4142*1, 0.0]},
                "11":{"0": [0, 50.0], "1": [1.4142*1, 0.0]}
            },
            "steps": {
                "0": {"1": [1.41415926, 0.0]}   # Step harmonic wave by 1.0 each tick
            },
            "count": 500
        }
    }
    
    
    print(f"\n<<< [Frontend -> WS] SEND (Test started!)")
    await dispatcher.HandleCommand(payload)
    await asyncio.sleep(45)
    
    logger.info("模拟器挂机时间到，强制全局清理...")
    await dispatcher.HandleCommand({"cmd": "reset"})
    await asyncio.sleep(1)
    
    hw_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(runAcdcTest())
    except KeyboardInterrupt:
        pass
