import sys, os, asyncio, json

os.environ["USE_LOG_LEVEL"] = "INFO"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from utils.SysLogger import GetLogger

logger = GetLogger("TestInvTimeIt")

async def runTest():
    hwGateway = HWGateway()

    results = []

    async def wsCallback(data):
        if isinstance(data, str):
            data = json.loads(data)
        t = data.get("type", "")

        if t == "value_update":
            tp_id = data.get("id")
            if tp_id is not None:
                print(f">>> [VALUE] id={tp_id} currentI={data.get('currentI')} "
                      f"actualTime={data.get('actualTime')} di={data.get('di')}")
                results.append(data)
            else:
                print(f">>> [DI_SOE] di={data.get('di')} hw_ts={data.get('hw_ts')}")
        elif t == "trigger":
            print(f">>> [TRIGGER] {data.get('condition')} -> next_id={data.get('next_id')} "
                  f"hw_ts={data.get('hw_ts')}")
        elif t == "report":
            print(f">>> [REPORT]\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        elif t == "stop":
            print(f">>> [STOP] 实验结束")
        elif t == "load":
            pass  # 温度遥测，忽略
        elif t == "error":
            print(f">>> [ERROR] {data.get('msg')}")

    class MockHWProtect:
        def __init__(self):
            self.channel_temperatures = {str(i): 25.0 for i in range(12)}
            self.pin_amp_enable = type('obj', (object,), {'value': 1})
        def SetAmplifierEnable(self, enable): pass
        def SetPower(self, a, b): pass

    dispatcher = CmdRouter(hwGateway=hwGateway, hwProtect=MockHWProtect(), wsSendCb=wsCallback)
    hwGateway.dispatcher = dispatcher
    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(1)

    # ════════════════════════════════════════════════════
    # 测试1: FAULT_CURRENT + A_GROUND + 方向性禁用 (最简配置)
    # ════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(" 测试1: FAULT_CURRENT + A_GROUND (2个测试点)")
    print("=" * 60)

    payload = {
        "module": "inverse_time_it_test",
        "cmd": "start",
        "params": {
            "sys": {
                "debounce": 15,
                "logicMask": 1
            },
            "statics": {
                "0": {"0": [0, 50], "1": [57.735, 0]},
                "1": {"0": [0, 50], "1": [57.735, -120]},
                "2": {"0": [0, 50], "1": [57.735, 120]}
            },
            "payload": {
                "iDefinition": "FAULT_CURRENT",
                "faultType": "A_GROUND",
                "ratedFrequency": 50,
                "directionality": {
                    "enabled": False,
                    "vBeforeFault": 57.735,
                    "vFault": 20,
                    "lineAngle": 70,
                    "ctPolarity": "TOWARD_COMPONENT"
                },
                "outputMode": {
                    "voltage": "GROUP1_UABC",
                    "current": "GROUP1_IABC"
                },
                "iBeforeFault": 0,
                "tBeforeFault": 1,
                "faultLimitTime": 60
            },
            "testPoints": [
                {"id": 1, "value": 2.0},
                {"id": 2, "value": 3.0}
            ]
        }
    }

    await dispatcher.HandleCommand(payload)
    print(">>> 已下发, 等待 DI 触发...")

    # 等待故障前驻留 + 故障态
    # 实际测试需要硬件 DI 触发, 这里等待一段时间后手动停止
    await asyncio.sleep(15)

    # 停止实验
    print("\n>>> 发送 stop")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(1)

    # ════════════════════════════════════════════════════
    # 测试2: NEGATIVE_SEQUENCE(I2) + AB_PHASE + 方向性启用
    # ════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(" 测试2: NEGATIVE_SEQUENCE(I2) + AB_PHASE + 方向性")
    print("=" * 60)

    payload2 = {
        "module": "inverse_time_it_test",
        "cmd": "start",
        "params": {
            "sys": {
                "debounce": 15,
                "logicMask": 1
            },
            "statics": {
                "0": {"0": [0, 50], "1": [57.735, 0]},
                "1": {"0": [0, 50], "1": [57.735, -120]},
                "2": {"0": [0, 50], "1": [57.735, 120]}
            },
            "payload": {
                "iDefinition": "NEGATIVE_SEQUENCE",
                "negativeSequenceChannel": "I2",
                "faultType": "AB_PHASE",
                "ratedFrequency": 50,
                "directionality": {
                    "enabled": True,
                    "vBeforeFault": 57.735,
                    "vFault": 20,
                    "lineAngle": 70,
                    "ctPolarity": "TOWARD_COMPONENT"
                },
                "outputMode": {
                    "voltage": "GROUP1_UABC",
                    "current": "GROUP1_IABC"
                },
                "iBeforeFault": 0,
                "tBeforeFault": 1,
                "faultLimitTime": 50
            },
            "testPoints": [
                {"id": 1, "value": 2.0}
            ]
        }
    }

    await dispatcher.HandleCommand(payload2)
    print(">>> 已下发, 等待...")
    await asyncio.sleep(10)

    print("\n>>> 发送 stop")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(1)

    hw_task.cancel()
    print("\n>>> 测试完成")

if __name__ == "__main__":
    try:
        asyncio.run(runTest())
    except KeyboardInterrupt:
        pass
    finally:
        # 紧急硬件复归
        try:
            import serial
            from logic.FPGACodec import HWCodec
            from comms.HWGateway import HWGateway as HWG
            with serial.Serial(HWG.PORT, HWG.BAUDRATE, timeout=1) as ser:
                ser.write(HWCodec.FRAME_SYS_RESET)
        except:
            pass
