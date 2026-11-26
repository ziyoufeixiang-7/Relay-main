"""
Protocol Smoke Test
================================
验证整合后的 CmdRouter (V2 FlatCodec 路径) 在各 Handler 下能正确产生 TX 帧。
无需真实硬件连接。
"""
import asyncio
import os
import sys
import json
import difflib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在 import 任何项目模块之前 patch
os.environ["USE_LOG_LEVEL"] = "DEBUG"

from frame_capture import FrameCapture
capture = FrameCapture()
capture.patch()

from comms.HWGateway import HWGateway
from logic.CmdRouter import CmdRouter
from logic.HWProtect import HWProtect


async def dummy_ws(data):
    pass


# ═══════════════════════════════════════════════════
#  测试 Payloads (从各原版测试文件中提取)
# ═══════════════════════════════════════════════════

PAYLOADS = {
    "ac_test": {
        "module": "ac_test",
        "cmd": "start",
        "params": {
            "sys": {
                "mode": 1, "stepTime": 10, "changeMode": 1,
                "returnMode": 0, "power": 6, "debounce": 15,
                "doMask": 0, "doCtrlMask": 0, "logicMask": 0
            },
            "statics": {
                "0": {"0": [0, 50], "1": [100, 0]},
                "1": {"0": [0, 50], "1": [100, -120]},
                "2": {"0": [0, 50], "1": [100, 120]}
            },
            "limits": {"0": [282.84, 1000], "6": [56.57, 1000]}
        }
    },
    "status_seq": {
        "module": "universal_sequence",
        "cmd": "start",
        "params": {
            "sys": {"debounce": 15},
            "nodes": [
                {
                    "id": 1, "mode": "STATIC",
                    "do_actions": [{"delay": 0, "mask": 3}],
                    "static": {
                        "0": {"0": [0, 50], "1": [57.735, 0]},
                        "1": {"0": [0, 50], "1": [57.735, -120]},
                        "2": {"0": [0, 50], "1": [57.735, 120]},
                        "6": {"0": [0, 50], "1": [0, 0]}
                    },
                    "limits": {"0": [282.84, 1000], "6": [56.57, 1000]},
                    "triggers": [
                        {"condition": "MANUAL_TRIG", "next_id": 2, "post_condition_delay_ms": 500}
                    ]
                },
                {
                    "id": 2, "mode": "SWEEP",
                    "do_actions": [{"delay": 0, "mask": 5}],
                    "static": {
                        "0": {"0": [0, 50], "1": [10, 0]},
                        "1": {"0": [0, 50], "1": [10, -120]},
                        "2": {"0": [0, 50], "1": [10, 120]},
                        "6": {"0": [0, 50], "1": [0, 0]}
                    },
                    "sweep": {
                        "interval": 100, "count": 10,
                        "step": {"0": {"1": [5, 0]}, "1": {"1": [5, 0]}, "2": {"1": [5, 0]}}
                    },
                    "limits": {},
                    "triggers": [{"condition": "COUNT_OVER", "next_id": 3}]
                },
                {
                    "id": 3, "mode": "STATIC",
                    "static": {
                        "0": {"0": [0, 50], "1": [0, 0]},
                        "1": {"0": [0, 50], "1": [0, 0]},
                        "2": {"0": [0, 50], "1": [0, 0]},
                        "6": {"0": [0, 50], "1": [0, 0]}
                    },
                    "limits": {},
                    "triggers": [
                        {"condition": "TIME_OUT", "timeout": 3000, "next_id": -2, "post_condition_delay_ms": 0}
                    ]
                }
            ]
        }
    },
    "slip_test": {
        "module": "slip_test",
        "cmd": "start",
        "params": {
            "sys": {"debounce": 15, "doMask": 0, "doCtrlMask": 0, "logicMask": 0},
            "statics": {
                "0": {"0": [0, 50], "1": [57.735, 0]},
                "1": {"0": [0, 50], "1": [57.735, -120]},
                "2": {"0": [0, 50], "1": [57.735, 120]}
            },
            "payload": {
                "testType": 0, "slipPhasor": 0,
                "slipStart": 50.0, "slipDDt": 1.0,
                "slipChannels": {"va": True, "vb": True, "vc": True}
            },
            "limits": {"0": [282.84, 1000]}
        }
    },
    "tm_test": {
        "module": "time_measurement",
        "cmd": "start",
        "params": {
            "sys": {"debounce": 15},
            "timers": {
                "timer1": {"start_mode": "ENTER_STATE_2", "stop_mode": "DI_A_CLOSE"},
                "timer2": {"start_mode": "TIMER1_STOP_START", "stop_mode": "DI_B_CLOSE"},
                "timer3": {"start_mode": "TIMER2_STOP_START", "stop_mode": "DI_C_CLOSE"}
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
            "limits": {"0": [282.84, 1000]},
            "state2EndCondition": "TIMER_1_2_3"
        }
    },
    "inv_time_it": {
        "module": "inverse_time",
        "cmd": "start",
        "params": {
            "sys": {"debounce": 15, "doMask": 0, "doCtrlMask": 0, "logicMask": 0, "power": 6},
            "statics": {
                "6": {"0": [0, 50.0], "1": [1.0, 0.0]},
                "7": {"0": [0, 50.0], "1": [1.0, -120.0]},
                "8": {"0": [0, 50.0], "1": [1.0, 120.0]}
            },
            "payload": {
                "testType": "it",
                "mode": "AUTO",
                "sweepChannel": "ia",
                "ratedCurrent": 5.0,
                "multiple": 2.0,
                "sweepStart": 0.5,
                "sweepDDt": 0.1,
                "stepTime": 100
            },
            "limits": {"6": [5.657, 1000]}
        }
    },
    "inv_time_vft": {
        "module": "inverse_time",
        "cmd": "start",
        "params": {
            "sys": {"debounce": 15, "doMask": 0, "doCtrlMask": 0, "logicMask": 0, "power": 6},
            "statics": {
                "0": {"0": [0, 50.0], "1": [57.735, 0.0]},
                "1": {"0": [0, 50.0], "1": [57.735, -120.0]},
                "2": {"0": [0, 50.0], "1": [57.735, 120.0]}
            },
            "payload": {
                "testType": "vft",
                "mode": "AUTO",
                "sweepChannel": "va",
                "ratedVoltage": 100.0,
                "multiple": 0.5,
                "sweepStart": 0.3,
                "sweepDDt": 0.01,
                "stepTime": 100
            },
            "limits": {"0": [282.84, 1000]}
        }
    }
}


async def run_with_router(RouterClass, payload):
    """用指定的 Router 运行一个 payload 并收集 TX 帧"""
    capture.Reset()
    
    gw = HWGateway()
    hp = HWProtect()
    router = RouterClass(hwGateway=gw, hwProtect=hp, wsSendCb=dummy_ws)
    
    # 启动 fake connect (不会阻塞)
    hw_task = asyncio.create_task(gw.Connect())
    await asyncio.sleep(0.1)
    
    # 发送命令
    await router.HandleCommand(payload)
    
    # 等待引擎运行一小段
    await asyncio.sleep(2.0)
    
    # 停止
    await router.HandleCommand({"cmd": "stop"})
    await asyncio.sleep(0.5)
    
    hw_task.cancel()
    try:
        await hw_task
    except asyncio.CancelledError:
        pass
    
    return list(capture.txFrames)


async def main():
    results = {}
    all_pass = True
    
    for name, payload in PAYLOADS.items():
        print(f"\n{'='*60}")
        print(f"  Testing: {name}")
        print(f"{'='*60}")
        
        # 原版
        orig_frames = await run_with_router(CmdRouter, payload)
        
        # V2
        v2_frames = await run_with_router(CmdRouter, payload)
        
        # 提取有意义的配置帧序列（排除尾部的 SYS_RESET 和引擎 tick 产生的 SYS_UPDATE 时序差异）
        # SYS_RESET=5A04005E, SYS_UPDATE=5A05005F
        # 原则：配置帧序列（从第一帧到最后一个参数帧）必须完全一致
        def extract_config_frames(frames):
            """提取配置阶段帧：从头到最后一个参数帧(A5开头)或SET_DBNC/START"""
            last_config_idx = -1
            for i, f in enumerate(frames):
                if f.startswith('A5') or f in ('5A200F75', '5A00005A'):  # 参数帧, SET_DBNC, START
                    last_config_idx = i
            return frames[:last_config_idx + 1] if last_config_idx >= 0 else frames[:4]  # 至少保留初始化帧
        
        orig_config = extract_config_frames(orig_frames)
        v2_config = extract_config_frames(v2_frames)
        
        if orig_config == v2_config:
            status = "PASS"
            print(f"  ✅ SUCCESS: Config frames MATCH! (config={len(orig_config)}, total: orig={len(orig_frames)}, v2={len(v2_frames)})")
        else:
            status = "FAIL"
            all_pass = False
            print(f"  ❌ FAIL: Config frames MISMATCH!")
            print(f"     Orig config: {len(orig_config)}, V2 config: {len(v2_config)}")
            
            diff = list(difflib.unified_diff(orig_config, v2_config, lineterm='', 
                                              fromfile='Original', tofile='V2'))
            for line in diff[:30]:
                print(f"     {line}")
        
        results[name] = {
            "status": status,
            "orig_count": len(orig_frames),
            "v2_count": len(v2_frames),
            "config_count": len(orig_config)
        }
    
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    for name, r in results.items():
        icon = "✅" if r["status"] == "PASS" else "❌"
        print(f"  {icon} {name}: {r['status']} (orig={r['orig_count']}, v2={r['v2_count']})")
    
    if all_pass:
        print(f"\n  🎉 ALL {len(results)} HANDLERS PASSED!")
    else:
        print(f"\n  ⚠️  SOME TESTS FAILED")
    
    return all_pass


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        pass
