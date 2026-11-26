import sys, os, asyncio, json

os.environ["USE_LOG_LEVEL"] = "DEBUG"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from utils.SysLogger import GetLogger

logger = GetLogger("TestSlipTest")

async def runTest():
    hwGateway = HWGateway()
    iteration_count = [0]

    async def wsCallback(data):
        if isinstance(data, str):
            data = json.loads(data)
        t = data.get("type", "")
        mod = data.get("module", "")
        if mod != "slip_test" and t not in ("report", "stop", "error"):
            return
        if t == "value_update":
            static = data.get("static")
            if static:
                ch0 = static.get("0", {}).get("1", [None])[0]
                print(f">>> [VALUE] ch0_amp={ch0}")
        elif t == "report":
            print(f">>> [REPORT]\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        elif t == "stop":
            print(f">>> [STOP]")
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

    # testType=0: 动作值搜索 (相电压Ua幅值)
    print("\n" + "=" * 60)
    print(" testType=0: 动作值搜索 (Ua 幅值)")
    print(" searchStart=70.71, searchEnd=56.57, step=-0.1")
    print(" slipStart=statics ch0=81.65, ddt=69V/s")
    print("=" * 60)

    payload = {
        "module": "slip_test",
        "cmd": "start",
        "params": {
            "sys": {
                "debounce": 15,
                "doMask": 0,
                "doCtrlMask": 0,
                "logicMask": 0
            },
            "limits": {
                "0": [282.8428, 1000], "1": [282.8428, 1000],
                "2": [282.8428, 1000], "3": [282.8428, 1000],
                "4": [282.8428, 1000], "5": [282.8428, 1000],
                "6": [56.5686, 1000], "7": [56.5686, 1000],
                "8": [56.5686, 1000], "9": [56.5686, 1000],
                "10": [56.5686, 1000], "11": [56.5686, 1000]
            },
            "statics": {
                "0": {"0": [0, 50], "1": [81.6497, 0]},
                "1": {"0": [0, 50], "1": [81.6497, -120]},
                "2": {"0": [0, 50], "1": [81.6497, 120]},
                "3": {"0": [0, 50], "1": [81.6497, 0]},
                "4": {"0": [0, 50], "1": [81.6497, -120]},
                "5": {"0": [0, 50], "1": [81.6497, 120]},
                "6": {"0": [0, 50], "1": [0, 0]},
                "7": {"0": [0, 50], "1": [0, -120]},
                "8": {"0": [0, 50], "1": [0, 120]},
                "9": {"0": [0, 50], "1": [0, 0]},
                "10": {"0": [0, 50], "1": [0, -120]},
                "11": {"0": [0, 50], "1": [0, 120]}
            },
            "payload": {
                "testType": 0,
                "slipVarType": "0_0",
                "slipPhasor": 0,
                "slipPhase": "0",
                "actionSearchStart": 70.7107,
                "actionSearchEnd": 56.5686,
                "actionSearchStep": -0.1,
                "slipDDt": 69,
                "returnTime": 1000,
                "waitTime": 1000
            }
        }
    }

    await dispatcher.HandleCommand(payload)
    print(">>> 已下发, 等待搜索循环 (最多30s)...")
    await asyncio.sleep(30)

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
