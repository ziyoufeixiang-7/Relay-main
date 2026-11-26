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

logger = GetLogger("TestTM")

async def purePythonEventCallback(data: dict):
    """
    模拟 WebSocket 向前端发送 JSON 事件。
    """
    try:
        t = data.get("type", "")
        module = data.get("module", "")

        if t == "value_update":
            payload_str = json.dumps(data)
            print(f">>> [WS -> Frontend] VALUE_UPDATE: {payload_str[:200]}...")
        elif t == "report":
            print(f"\n==================== [TEST REPORT] ====================")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print(f"=======================================================\n")
        elif t == "trigger":
            print(f"\n>>> [TRIGGER] {data.get('condition')} -> next_id={data.get('next_id')} (hw_ts={data.get('hw_ts')})")
        elif t == "stop":
            print(f"\n>>> [STOP] 实验结束")
        elif t == "load":
            pass  # 静默忽略心跳
        else:
            print(f"\n[WS -> Frontend] {t.upper()}: {json.dumps(data, indent=2, ensure_ascii=False)}\n")

    except Exception as e:
        print(f"Failed to process payload: {data}. Error: {e}")


async def runTimeMeasurementTest():
    logger.info("启动时间测量 (Time Measurement) 纯 Python 通道协议测试...")

    hwGateway = HWGateway()
    hwProtect = HWProtect()
    dispatcher = CmdRouter(hwGateway=hwGateway, hwProtect=hwProtect, wsSendCb=purePythonEventCallback)
    hwGateway.dispatcher = dispatcher

    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(2.0)

    logger.info("下发时间测量 JSON -> 呼叫 TMTestHdl 生成指令...")

    # 时间测量配置:
    # 状态 1: 按键触发进入状态 2
    # 状态 2: 计时器 1 停时后进入状态 3
    # 状态 3: 10 秒超时后结束实验
    # 计时器 1: 进入状态 2 启时, 开入 A 闭合停时
    # 计时器 2: 计时器 1 停时启动, 开入 B 闭合停时
    # 计时器 3: 计时器 2 停时启动, 开入 C 闭合停时
    payload = {
        "module": "time_measurement",
        "cmd": "start",
        "params": {
            "sys": { "debounce": 15 },
            "timers": {
                "timer1": {
                    "start_mode": "ENTER_STATE_2",
                    "stop_mode": "DI_A_CLOSE"
                },
                "timer2": {
                    "start_mode": "TIMER1_STOP_START",
                    "stop_mode": "DI_B_CLOSE"
                },
                "timer3": {
                    "start_mode": "TIMER2_STOP_START",
                    "stop_mode": "DI_C_CLOSE"
                }
            },
            "statics": {
                "1": {
                    "0": {"0": [0, 50.0], "1": [10.0, 0.0]},
                    "1": {"0": [0, 50.0], "1": [10.0, 0.0]}
                },
                "2": {
                    "0": {"0": [0, 50.0], "1": [50.0, 0.0]},
                    "1": {"0": [0, 50.0], "1": [50.0, 0.0]}
                },
                "3": {
                    "0": {"0": [0, 50.0]},
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
            "nodes": [
                {
                    "id": 1,
                    "mode": "STATIC",
                    "do_actions": [{ "delay": 0, "mask": 3 }],
                    "static": {},
                    "limits": {},
                    "next_id": 2,
                    "triggers": [
                        { "condition": "MANUAL_TRIG" },
                        { "condition": "TIME_OUT", "timeout": 30000 }
                    ]
                },
                {
                    "id": 2,
                    "mode": "STATIC",
                    "do_actions": [{ "delay": 0, "mask": 5 }],
                    "static": {},
                    "limits": {},
                    "next_id": 3,
                    "triggers": [
                        { "condition": "TIMER_1" },
                        { "condition": "TIME_OUT", "timeout": 60000 }
                    ]
                },
                {
                    "id": 3,
                    "mode": "STATIC",
                    "do_actions": [{ "delay": 0, "mask": 0 }],
                    "static": {},
                    "limits": {},
                    "next_id": -2,
                    "triggers": [
                        { "condition": "MANUAL_TRIG" },
                        { "condition": "TIME_OUT", "timeout": 10000 }
                    ]
                }
            ]
        }
    }

    print(f"\n<<< [Frontend -> WS] SEND (Time Measurement start!)")
    await dispatcher.HandleCommand(payload)

    # 2 秒后模拟按键触发，从状态 1 → 状态 2
    await asyncio.sleep(2.0)
    logger.info("模拟前端按键 next_node → 从状态 1 跳到状态 2")
    await dispatcher.HandleCommand({"module": "time_measurement", "cmd": "next_node"})

    # 等待 DI 触发计时器停时 (需要真实硬件)
    # 如果 30 秒内没有 DI 触发，状态 2 的 60s 超时会兜底
    logger.info("等待 DI 变化触发计时器停时... (操作开入端子)")
    await asyncio.sleep(30)

    logger.info("测试时间到，强制停止...")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(1)

    hw_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(runTimeMeasurementTest())
    except KeyboardInterrupt:
        pass
