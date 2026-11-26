import sys
import os
import asyncio
import json


ENABLE_DEBUG_LOG = False
os.environ["USE_LOG_LEVEL"] = "DEBUG" if ENABLE_DEBUG_LOG else "INFO"

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(current_dir))

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from utils.SysLogger import GetLogger

logger = GetLogger("TestRawFSM")

# ==========================================

async def runRawFsmTest():
    logger.info("启动纯异步引擎底层协议 (Raw FSM) 压测...")
    
    hwGateway = HWGateway()
    
    
    async def purePythonEventCallback(payload_str):
        data = payload_str if isinstance(payload_str, dict) else json.loads(payload_str)
        t = data.get("type", "")
        
        if t == "value_update":
            nid = data.get('id')
            tick = data.get('tick')
            di = data.get('di', '')
            do = data.get('do', '')
            logger.info(f"[TLM] Node={nid} Tick={tick} DI={di} DO={do}")
            
        elif t == "trigger":
            cnd = data.get('condition')
            nxt = data.get('next_id')
            hw_ts = data.get('hw_ts', 0)
            logger.info(f"[TRG] {cnd} → Node {nxt} | hw_ts={hw_ts}")
            
        elif t == "error":
            logger.error(f"[ERR] {data.get('msg', '')}")
            
    class MockHWProtect:
        def __init__(self):
            self.channel_temperatures = {str(i): 25.0 for i in range(12)}
            self.pin_amp_enable = type('obj', (object,), {'value': 1})
        def SetAmplifierEnable(self, enable: bool): pass
        def SetPower(self, currentState=None): pass

    dispatcher = CmdRouter(hwGateway=hwGateway, hwProtect=MockHWProtect(), wsSendCb=purePythonEventCallback)
    hwGateway.dispatcher = dispatcher
    
    hw_task = asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(1)



    # 依据 engine_internal_protocol 完全手搓的 REG 级裸词典
    # 测试流：Node 1 (静态驻留 2s) -> Node 2 (Sweep+Reset) -> Node 4 (DcComp) -> Node 3 (终止)
    payload = {
        "module": "useEngine",
        "cmd": "start",
        "params": {
            "sys": {
                "vChannels": [0, 1, 2, 3, 4, 5],
                "iChannels": [6, 7, 8, 9, 10, 11],
                "debounce": 61
            },
            "nodes": [
                {
                    "id": 0,
                    # 静态阶段
                    "mode": "STATIC",
                    "doActions": [
                        { "delay": 0, "mask": 255 },
                        { "delay": 1000, "mask": 1 }
                    ],
                    "static": {
                        # "0": {
                        #     # Channel 0, Layer 0 => [Amp 50V (0x2AAAAAAB), Freq 50Hz (0x0020C49B)]
                        #     "0": [0,  5497558] 
                        # }
                    },
                    "triggers": [
                        # 2000毫秒后超时强制切跳转节点 2
                        { "condition": "TIME_OUT", "timeout": 2000, "nextId": 2 }
                    ]
                },
                {
                    "id": 2,
                    # 常规扫描阶段
                    "mode": "SWEEP",
                    "doActions": [
                        { "delay": 0, "mask": 255 },
                        { "delay": 500, "mask": 1 }
                    ],
                    "static": {
                        "0": { "1": [0x2AAAAAAB-100000000, 0] }
                    },
                    "dynamic": {
                        "interval":5,
                        "count": 50,
                        "step": {
                            "0": {
                              "1": [
                                2552300,
                                0
                              ]
                            }
                        },
                        "resetTime": 5,
                        "resetStatic": {
                            "0": { "1": [0, 0] }
                        }
                    },
                    "triggers": [
                        {
                            "condition": "COUNT_OVER",
                            "nextId": 4
                        }
                    ]
                },
                {
                    "id": 4,
                    # DcComp 扫描阶段：模拟 DC 补偿衰减
                    # static 已含 DC comp[0] (由 API 预算)，Layer 0 = DC 层
                    "mode": "DC_COMP",
                    "doActions": [
                        { "delay": 0, "mask": 170 },     # 0xAA pattern at start
                        { "delay": 100, "mask": 85 }     # 0x55 after 100ms (verify doActions fire during sweep)
                    ],
                    "static": {
                        "0": {
                            # DC layer (0): amp = target + comp[0], freq = 50Hz
                            "0": [- 100000000, 0x0020C49B],
                            # AC layer (1): fundamental wave
                            "1": [0x2AAAAAAB, 0]
                        }
                    },
                    "dynamic": {
                        "interval": 100,
                        "count": 20,
                        # Pre-computed per-step St increments (simulating exponential decay)
                        # comp[0]=100M, comp[1]≈82M (δ=-18M), comp[2]≈67M (δ=-15M), ...
                        # decay curve: ΔV × e^(-i×10/50)
                        "steps": [
                            { "0": { "0": [18126925, 0] } },   # step 1
                            { "0": { "0": [14816364, 0] } },   # step 2
                            { "0": { "0": [12109732, 0] } },   # step 3
                            { "0": { "0": [9900498, 0] } },    # step 4
                            { "0": { "0": [8093480, 0] } },    # step 5
                            { "0": { "0": [6616841, 0] } },    # step 6
                            { "0": { "0": [5410019, 0] } },    # step 7
                            { "0": { "0": [4423187, 0] } },    # step 8
                            { "0": { "0": [3616310, 0] } },    # step 9
                            { "0": { "0": [2956682, 0] } },    # step 10
                            { "0": { "0": [2417591, 0] } },    # step 11
                            { "0": { "0": [1976665, 0] } },    # step 12
                            { "0": { "0": [1616233, 0] } },    # step 13
                            { "0": { "0": [1321509, 0] } },    # step 14
                            { "0": { "0": [1080555, 0] } },    # step 15
                            { "0": { "0": [883542, 0] } },     # step 16
                            { "0": { "0": [722413, 0] } },     # step 17
                            { "0": { "0": [590630, 0] } },     # step 18
                            { "0": { "0": [482934, 0] } },     # step 19
                            { "0": { "0": [394904, 0] } }      # step 20 → comp ≈ 0
                        ],
                        "phaseGate": {
                            "channel": 0,
                            "phase": 0x40000000   # Gate at 90° phase
                        }
                    },
                    "triggers": [
                        { "condition": "TIME_OUT", "timeout": 1000, "nextId": 3 }
                    ]
                },
                {
                    "id": 3,
                    "mode": "STATIC",
                    "doActions": [],
                    "static": {
                        "0": {
                            "0": [0, 0],
                            "1": [0, 0]
                        }
                    },
                    "triggers": [
                        { "condition": "DI_MATCH", "diMask": 255, "nextId": -2 },
                        { "condition": "TIME_OUT", "timeout": 1500, "nextId": -2 }
                    ]
                }
            ]
        }
    }
    
    logger.info("\n========= 开始直接下发底层协议 (RAW Protocol) =========")
    print("====== [Engine Input Payload] ======")
    print(json.dumps(payload, indent=2))
    print("====================================")
    await dispatcher.HandleCommand(payload)
    

    await asyncio.sleep(30)  # Node0(2s) + Sweep(100×0.2=20s) + DcComp(~4s) + Node3(1.5s)
    
    logger.info("\n================ 自动运行到达大限尾部 ==================")
    logger.info("呼叫中止销毁指令清扫战局 (使用 reset 彻底重置机器状态)...")
    await dispatcher.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(0.5)
    
    hw_task.cancel()
    logger.info("裸机协议直通驱动防撕裂压测 -> 圆满通过！")

if __name__ == "__main__":
    try:
        asyncio.run(runRawFsmTest())
    except KeyboardInterrupt:
        logger.info("已退出压测。")
    finally:
        logger.info("触发硬件保护性强制复归...")
        try:
            import serial
            from logic.FPGACodec import HWCodec
            from comms.HWGateway import HWGateway
            with serial.Serial(HWGateway.PORT, HWGateway.BAUDRATE, timeout=1) as ser:
                ser.write(HWCodec.FRAME_SYS_RESET)
            logger.info("硬件已安全复归。")
        except Exception as e:
            logger.error(f"紧急复归失败: {e}")
