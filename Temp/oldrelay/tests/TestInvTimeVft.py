import sys, os, asyncio, json

os.environ["USE_LOG_LEVEL"] = "INFO"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from utils.SysLogger import GetLogger

logger = GetLogger("TestInvTimeVft")

async def runTest():
    hwGateway = HWGateway()

    async def wsCallback(data):
        if isinstance(data, str):
            data = json.loads(data)
        t = data.get("type", "")

        if t == "value_update":
            tp_id = data.get("id")
            if tp_id is not None:
                print(f">>> [VALUE] id={tp_id} vOrFValue={data.get('vOrFValue')} "
                      f"actualTime={data.get('actualTime')} di={data.get('di')}")
            else:
                print(f">>> [DI_SOE] di={data.get('di')} hw_ts={data.get('hw_ts')}")
        elif t == "trigger":
            print(f">>> [TRIGGER] {data.get('condition')} -> next_id={data.get('next_id')}")
        elif t == "report":
            print(f">>> [REPORT]\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        elif t == "stop":
            print(f">>> [STOP] 实验结束")
        elif t == "load":
            pass
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
    # 测试1: V_T + UA (电压-时间, 单相A)
    # ════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(" 测试1: V_T + UA (2个测试点)")
    print("=" * 60)

    payload = {
        "module": "inverse_time_vft_test",
        "cmd": "start",
        "params": {
            "sys": {
                "debounce": 15,
                "logicMask": 1
            },
            "statics": {
                "6": {"0": [0, 50], "1": [0, 0]},
                "7": {"0": [0, 50], "1": [0, -120]},
                "8": {"0": [0, 50], "1": [0, 120]}
            },
            "payload": {
                "testProject": "V_T",
                "vOutputMode": "UA",
                "angle": 0,
                "vbValue": 57.735,
                "fbValue": 50,
                "vBeforeFault": 57.735,
                "fBeforeFault": 50,
                "tBeforeFault": 1,
                "faultLimitTime": 60
            },
            "testPoints": [
                {"id": 1, "value": 63.5},
                {"id": 2, "value": 69.3}
            ]
        }
    }

    await dispatcher.HandleCommand(payload)
    print(">>> 已下发, 等待 DI 触发...")
    await asyncio.sleep(10)

    print("\n>>> 发送 stop")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(1)

    # ════════════════════════════════════════════════════
    # 测试2: VF_T + UABC_POS (电压频率-时间, 三相正序)
    # ════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(" 测试2: VF_T + UABC_POS (变电压, 1个测试点)")
    print("=" * 60)

    payload2 = {
        "module": "inverse_time_vft_test",
        "cmd": "start",
        "params": {
            "sys": {
                "debounce": 15,
                "logicMask": 1
            },
            "statics": {
                "6": {"0": [0, 50], "1": [0, 0]},
                "7": {"0": [0, 50], "1": [0, -120]},
                "8": {"0": [0, 50], "1": [0, 120]}
            },
            "payload": {
                "testProject": "VF_T",
                "vOutputMode": "UABC_POS",
                "angle": 0,
                "vbValue": 57.735,
                "fbValue": 50,
                "vfVariable": "VOLTAGE_AMPLITUDE_V",
                "vBeforeFault": 57.735,
                "fBeforeFault": 50,
                "tBeforeFault": 1,
                "faultLimitTime": 30
            },
            "testPoints": [
                {"id": 1, "value": 1.1}
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
        try:
            import serial
            from logic.FPGACodec import HWCodec
            from comms.HWGateway import HWGateway as HWG
            with serial.Serial(HWG.PORT, HWG.BAUDRATE, timeout=1) as ser:
                ser.write(HWCodec.FRAME_SYS_RESET)
        except:
            pass
