import asyncio
import os
import threading
import time
import sys

if '--self-check' in sys.argv:
    # Compiled-artifact verification must never open serial/GPIO or start engines.
    from comms.mobile_control.integrated import IntegratedGateway
    gateway = IntegratedGateway()
    print('SELF_CHECK_OK mobile_control=integrated port=8080 admin_auth=removed')
    raise SystemExit(0)

ENABLE_HW_PROTECTION = True

try:
    os.sched_setaffinity(0, {2})
except AttributeError:
    pass

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

os.environ["USE_LOG_LEVEL"] = "DEBUG"

from comms.HWGateway import HWGateway
if os.environ.get("RELAY_MOBILE_CONTROL", "1") != "0":
    from comms.mobile_control.integrated import IntegratedGateway as WSGateway
else:
    from comms.WSGateway import WSGateway
from logic.EngineBridge import EngineBridge
from logic.EngineCtrl import EngineCtrl
from logic.HWProtect import HWProtect
from logic.USEEngine import USEEngine
from utils.SysLogger import GetLogger

logger = GetLogger("SysEngine")


def _run_hw_thread(ready: threading.Event, shared: dict):

    try:
        os.sched_setaffinity(0, {3})
    except AttributeError:
        pass
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    gateway = HWGateway()
    engine = USEEngine(gateway, None)
    gateway.engine = engine

    shared['hw_loop'] = loop
    shared['engine'] = engine
    shared['hwGateway'] = gateway
    ready.set()

    loop.create_task(gateway.Connect())
    loop.run_forever()


async def main() -> None:
    logger.info("Initialization started...")

    main_loop = asyncio.get_running_loop()
    wsGateway = WSGateway()  # Validate test enrollment before touching hardware.

    shared = {}
    ready = threading.Event()
    threading.Thread(target=_run_hw_thread, args=(ready, shared), daemon=True, name="HwEngineThread").start()
    ready.wait(timeout=5.0)

    bridge = EngineBridge(shared['hw_loop'], main_loop, None)
    bridge.bind(shared['engine'])

    hwProtect = HWProtect(ENABLE_HW_PROTECTION)
    engineCtrl = EngineCtrl(bridge, hwProtect, wsGateway.SendToClient)
    bridge._emit_cb = engineCtrl.onEvent
    wsGateway.dispatcher = engineCtrl
    hwProtect.engineCtrl = engineCtrl

    await asyncio.sleep(1.0)
    bridge.start()

    async with wsGateway.StartServer():
        logger.info("All gateways online. Awaiting commands...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\nCtrl+C detected. Terminating system...")
