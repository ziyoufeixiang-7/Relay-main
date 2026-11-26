"""Mobile control in SysEngine's main loop; no upstream socket or port routing."""
import asyncio
import contextlib
import ipaddress
import os
from pathlib import Path

from websockets.asyncio.server import serve

from .service import Gateway, TEST_CLIENT_PING_INTERVAL, TEST_CLIENT_PING_TIMEOUT
from .test_device_config import load, device_serial, public_config


class _EngineEndpoint:
    def __init__(self, gateway):
        self.gateway = gateway

    async def close(self):
        # A stale engine feed is a real fault, never manufacture an idle state.
        self.gateway.upstream = None
        if self.gateway.dispatcher:
            await self.gateway.dispatcher.HandleCommand({'cmd': 'stop'})


class IntegratedGateway(Gateway):
    PORT = 8181

    def __init__(self, *, require_enrollment=True, host='0.0.0.0', port=PORT):
        super().__init__(upstream=None, ui_mirror=True, independent_remote=True,
                         origins=[])
        self.host, self.port = host, port
        self.dispatcher = None
        self.events = asyncio.Queue(maxsize=4096)
        self.config = load() if require_enrollment else None
        if require_enrollment and (not self.config or
                self.config['device_serial'].lower() != device_serial()):
            raise RuntimeError('Integrated mobile backend requires an enrolled test device')
        self.frontend_path = os.environ.get('RELAY_FRONTEND_PATH',
                                            str(Path.cwd() / 'index.html'))

    def state(self, client):
        return {**super().state(client), 'simulation': False,
                'integratedBackend': True,
                **({'deviceConfig': public_config(self.config)} if self.config else {})}

    async def send_upstream(self, data):
        if not self.upstream or not self.dispatcher:
            raise ConnectionError('engine unavailable')
        if data.get('cmd') == 'start':
            terminal = getattr(self.dispatcher, '_stopEvent', None)
            if terminal is not None and not terminal.is_set():
                raise RuntimeError('Previous hardware terminal acknowledgement pending')
        # This stays on the main event loop. Hardware timing stays in EngineBridge.
        await asyncio.wait_for(self.dispatcher.HandleCommand(data), self.send_timeout)

    async def SendToClient(self, data):
        # EngineCtrl emits callbacks as tasks. Queue them before taking the control
        # lock so callbacks cannot deadlock the command that produced them.
        try:
            self.events.put_nowait(data)
        except asyncio.QueueFull:
            self.upstream = None
            self.phase, self.reason = 'fault', 'engine_event_overflow'
            if self.dispatcher:
                await self.dispatcher.HandleCommand({'cmd': 'stop'})
            raise RuntimeError('Engine event queue overflow; control disabled')

    async def _events(self):
        while True:
            data = await self.events.get()
            try:
                await self.receive_upstream(data)
            finally:
                self.events.task_done()

    async def _commands(self, ws):
        if ws.request.path not in {'/', '/remote'}:
            await ws.close(code=1008, reason='Unknown endpoint')
            return
        await self.handler(ws)

    async def handler(self, ws):
        local = (ipaddress.ip_address(ws.remote_address[0]).is_loopback and
                 ws.request.path != '/remote')
        try:
            await super().handler(ws)
        finally:
            # The original single-client backend stopped on local disconnection.
            # Preserve that protection, while observer phones never stop a trial.
            if (local and not self.owner and not self.closing and
                    not any(c.role == 'local' for c in self.clients.values()) and
                    self.active is True):
                async with self.lock:
                    await self.begin_stop('local_disconnected')

    @contextlib.asynccontextmanager
    async def StartServer(self):
        if not self.dispatcher:
            raise RuntimeError('Engine dispatcher is required')
        self.upstream = _EngineEndpoint(self)
        self.tasks = [asyncio.create_task(self._events()),
                      asyncio.create_task(self.watchdog()),
                      asyncio.create_task(self.address_loop()),
                      asyncio.create_task(self.hardware_loop())]
        if os.environ.get('RELAY_MIRROR_NETWORK') == '1':
            self.tasks.append(asyncio.create_task(self.network_loop()))
        try:
            async with serve(self._commands, self.host, self.port,
                             process_request=self.http_request,
                             compression=None, max_size=8 * 1024 * 1024,
                             ping_interval=TEST_CLIENT_PING_INTERVAL,
                             ping_timeout=TEST_CLIENT_PING_TIMEOUT) as server:
                yield server
        finally:
            await self.shutdown()
