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

logger = GetLogger("TestSS")


async def purePythonEventCallback(data: dict):
    """模拟 WebSocket 向前端发送 JSON 事件。"""
    try:
        t = data.get("type", "")
        module = data.get("module", "")

        if module not in ("universal_sequence", "") and t != "load":
            return  # 过滤非本模块消息

        if t == "value_update":
            payload_str = json.dumps(data, ensure_ascii=False)
            print(f">>> [WS -> Frontend] VALUE_UPDATE: {payload_str[:200]}...")
        elif t == "report":
            print(f"\n==================== [TEST REPORT] ====================")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print(f"=======================================================\n")
        elif t == "trigger":
            print(f"\n>>> [TRIGGER] {data.get('condition')} -> next_id={data.get('next_id')}")
        elif t == "stop":
            print(f"\n>>> [STOP] 实验结束")
        elif t == "load":
            pass  # 静默忽略心跳
        else:
            print(f"\n[WS -> Frontend] {t.upper()}: {json.dumps(data, indent=2, ensure_ascii=False)}\n")

    except Exception as e:
        print(f"Failed to process payload: {data}. Error: {e}")


async def runStatusSequenceTest():
    logger.info("启动状态序列 (Universal Sequence) 测试...")

    hwGateway = HWGateway()
    hwProtect = HWProtect()
    dispatcher = CmdRouter(hwGateway=hwGateway, hwProtect=hwProtect, wsSendCb=purePythonEventCallback)
    hwGateway.dispatcher = dispatcher

    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(2.0)

    logger.info("下发状态序列 JSON -> 呼叫 StatusSeqHdl 生成指令...")

    # ═══════════════════════════════════════════════════════
    #  测试场景:
    #  Node 1: STATIC, MANUAL_TRIG, post_condition_delay_ms=500
    #           输出 Ua=57.735V/50Hz, DO=3
    #  Node 2: SWEEP, 10步 x 100ms, COUNT_OVER
    #           Ua 从 10V 每步增加 5V
    #  Node 3: STATIC, TIME_OUT=3000ms
    #           输出 Ua=0V, 3秒后结束
    # ═══════════════════════════════════════════════════════

    payload = {
        "module": "universal_sequence",
        "cmd": "start",
        "params": {
            "sys": { "debounce": 15 },
            "nodes": [
                {
                    "id": 1,
                    "mode": "STATIC",
                    "do_actions": [
                        { "delay": 0, "mask": 3 }
                    ],
                    "static": {
                        "0": { "0": [0, 50], "1": [57.735, 0] },
                        "1": { "0": [0, 50], "1": [57.735, -120] },
                        "2": { "0": [0, 50], "1": [57.735, 120] },
                        "6": { "0": [0, 50], "1": [0, 0] }
                    },
                    "limits": {
                        "0": [282.84, 1000],
                        "6": [56.57, 1000]
                    },
                    "triggers": [
                        {
                            "condition": "MANUAL_TRIG",
                            "next_id": 2,
                            "post_condition_delay_ms": 500
                        }
                    ]
                },
                {
                    "id": 2,
                    "mode": "SWEEP",
                    "do_actions": [
                        { "delay": 0, "mask": 5 }
                    ],
                    "static": {
                        "0": { "0": [0, 50], "1": [10, 0] },
                        "1": { "0": [0, 50], "1": [10, -120] },
                        "2": { "0": [0, 50], "1": [10, 120] },
                        "6": { "0": [0, 50], "1": [0, 0] }
                    },
                    "limits": {
                        "0": [282.84, 1000],
                        "6": [56.57, 1000]
                    },
                    "triggers": [
                        {
                            "condition": "COUNT_OVER",
                            "next_id": 3,
                            "post_condition_delay_ms": 0
                        }
                    ],
                    "dynamic": {
                        "stepTime": 100,
                        "count": 10,
                        "steps": {
                            "0": { "1": [5, 0] }
                        }
                    }
                },
                {
                    "id": 3,
                    "mode": "STATIC",
                    "do_actions": [
                        { "delay": 0, "mask": 0 }
                    ],
                    "static": {
                        "0": { "0": [0, 50], "1": [0, 0] },
                        "1": { "0": [0, 50], "1": [0, 0] },
                        "2": { "0": [0, 50], "1": [0, 0] },
                        "6": { "0": [0, 50], "1": [0, 0] }
                    },
                    "limits": {},
                    "triggers": [
                        {
                            "condition": "TIME_OUT",
                            "timeout": 3000,
                            "next_id": -2,
                            "post_condition_delay_ms": 0
                        }
                    ]
                }
            ]
        }
    }

    print(f"\n<<< [Frontend -> WS] SEND (Status Sequence start!)")
    await dispatcher.HandleCommand(payload)

    # 等待 2 秒后发送按键触发, 从 Node 1 → (500ms 延时桩) → Node 2
    await asyncio.sleep(2.0)
    logger.info("模拟前端按键 next_node → 从状态 1 跳转 (含 500ms 延时)")
    await dispatcher.HandleCommand({"module": "universal_sequence", "cmd": "next_node"})

    # 等待 Node 2 扫频 (10步 x 100ms = 1s) + Node 3 超时 (3s)
    logger.info("等待 Node 2 扫频 + Node 3 超时...")
    await asyncio.sleep(10)

    logger.info("测试完成，强制停止...")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(1)

    hw_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(runStatusSequenceTest())
    except KeyboardInterrupt:
        pass
