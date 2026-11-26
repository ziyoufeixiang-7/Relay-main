"""Explicit offline UI harness: no hardware imports, network manager or upstream sockets."""
import asyncio
import contextlib
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from comms.mobile_control.integrated import IntegratedGateway
from websockets.asyncio.server import serve

class FakeEngine:
    def __init__(self,gateway):
        self.gateway=gateway
        self.running=False
        self._stopEvent=asyncio.Event();self._stopEvent.set()
    async def HandleCommand(self,data):
        if data.get('cmd')=='start':
            self.running=True;self._stopEvent.clear()
        elif data.get('cmd')=='stop':
            self.running=False;self._stopEvent.set()
            await self.gateway.SendToClient({'type':'stop'})
        await self.publish()
    async def publish(self):
        await self.gateway.SendToClient({'type':'load','active':self.running,'thermal':0})

async def main():
    if os.environ.get('RELAY_OFFLINE_UI')!='1':
        raise RuntimeError('Explicit RELAY_OFFLINE_UI=1 is required')
    g=IntegratedGateway(require_enrollment=False,host='127.0.0.1',port=8181)
    g.origins={'http://127.0.0.2:8181'}
    g.frontend_path=os.environ['RELAY_OFFLINE_FRONTEND']
    original=g.state
    g.state=lambda c:{**original(c),'simulation':True}
    engine=FakeEngine(g);g.dispatcher=engine
    async def offline_monitor():await asyncio.Future()
    g.address_loop=g.hardware_loop=offline_monitor
    async def pulse():
        while True:
            await engine.publish();await asyncio.sleep(.2)
    async with g.StartServer(), serve(g._commands,'127.0.0.2',8181,process_request=g.http_request):
        task=asyncio.create_task(pulse())
        print('OFFLINE integrated UI harness ready (no hardware)',flush=True)
        try:await asyncio.Future()
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)

if __name__=='__main__':
    with contextlib.suppress(KeyboardInterrupt):asyncio.run(main())
