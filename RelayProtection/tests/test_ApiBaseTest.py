"""
test_ApiBaseTest.py — Test for ApiBaseTest (Integration)

Sets up a full EngineCtrl stack (with Mock HWGateway) and sends raw nodes
to ApiBaseTest to verify the whole stack runs end-to-end.
"""
import sys
import os
import asyncio
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logic.USEEngine import USEEngine
from logic.EngineCtrl import EngineCtrl

logging.basicConfig(level=logging.DEBUG)

# ── Mock classes ──

class MockHWGateway:
    def __init__(self):
        self.engine = None

    def SendBytes(self, frames):
        if self.engine:
            asyncio.create_task(self._ack())

    async def _ack(self):
        await asyncio.sleep(0.005)
        if self.engine:
            self.engine.HandleHwFeedback(int(time.monotonic() * 1000000), 0x0000)

class MockHWProtect:
    def SetAmplifierEnable(self, enable):
        pass

    def Beep(self, duration):
        print(f"[HWProtect] Beep for {duration}s")

class MockWsSend:
    def __init__(self):
        self.messages = []

    async def __call__(self, msg):
        self.messages.append(msg)
        t = msg.get("type", "")
        if t == "report":
            print(f"\n[WS] ═══ REPORT ═══")
            for k, v in msg.get("data", {}).items():
                print(f"  {k}: {v}")
            print(f"[WS] ═══════════════\n")
        elif t == "stop":
            print(f"[WS] STOP (module={msg.get('module', '?')})")
        elif t == "error":
            print(f"[WS] ERROR: {msg.get('msg', '')}")
        elif t == "value_update" and "static" in msg:
            s = msg["static"]
            ch0 = s.get("0", None)
            if ch0:
                print(f"[WS] ch0={ch0}")


# ── Engine setup ──

async def setup_engine():
    gateway = MockHWGateway()
    protect = MockHWProtect()
    ws_send = MockWsSend()
    engine = USEEngine(gateway, None)
    engine.sendBytes = gateway.SendBytes
    ctrl = EngineCtrl(engine, protect, ws_send)
    gateway.engine = engine

    engine._emit = ctrl.onEvent

    def _logged_send(data):
        if len(data) == 4 and data[0] == 0x5A:
            cmd = data[1]
            param = data[2]
            print(f"[TX] SYS 0x{cmd:02X} p={param}")
        elif len(data) == 12 and data[0] == 0xA5:
            cmd = data[1]
            layer = (data[2] + 1) & 0xFF
            ch_mask = (data[3] << 8) | data[4]
            amp = (data[5] << 8) | data[6]
            phase = (data[7] << 24) | (data[8] << 16) | (data[9] << 8) | data[10]
            print(f"[TX] DDS 0x{cmd:02X} L{layer} ch={ch_mask:04X} amp=0x{amp:04X} phase=0x{phase:08X}")
        else:
            print(f"[TX] RAW {data.hex().upper()}")

    gateway.SendBytes = _logged_send
    engine._send = _logged_send
    engine.sendBytes = _logged_send

    asyncio.create_task(engine.coreLoop())
    await asyncio.sleep(0.1)

    return engine, ctrl, ws_send

# ── Test cases ──

async def test_api_base_setup_static(engine, ctrl, ws):
    print("\n" + "=" * 60)
    print("  TEST: Static Node (mode=1) via ApiBaseTest")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "raw_test",
        "params": {
            "nodes": {
                "1": {
                    "mode": 1,
                    "base": {0: {1: [1000, 5000]}},
                    "timeoutMs": 1000,
                    "timeoutId": 0xFFFF
                }
            },
            "startNode": 1
        }
    }

    await ctrl.startTest(msg)
    print(">>> Running for 1.5s...")
    await asyncio.sleep(1.5)
    print(">>> Test done.\n")


async def main():
    engine, ctrl, ws = await setup_engine()
    try:
        await test_api_base_setup_static(engine, ctrl, ws)
    finally:
        ctrl.stopTest()
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(main())
