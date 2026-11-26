"""Single-upstream relay control gateway. Never replay hardware commands."""
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
import signal
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.http11 import Response
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed

LOG = logging.getLogger('relay.control_gateway')
CLIENT_PING_INTERVAL = 10
CLIENT_PING_TIMEOUT = 20
TEST_CLIENT_PING_INTERVAL = 30
TEST_CLIENT_PING_TIMEOUT = 60
TEST_CLIENT_SEND_TIMEOUT = 8
MAX_CLIENT_BACKLOG_BYTES = 16 * 1024 * 1024
TEST_COMMANDS = {'start', 'stop', 'update_static', 'next_node'}
REMOTE_SYSTEM_ACTIONS = {'wifiScan', 'wifiStatus', 'wifiSavedList', 'wifiConnect', 'wifiConnectSaved', 'wifiForget'}
CRITICAL_EVENTS = {'state', 'rejected', 'accepted', 'ui_seed'}
CRITICAL_TYPES = {'stop', 'error', 'load', 'value_update', 'report'}
_MIRROR_FILE = Path(__file__).with_name('mirror-modules.json')
MIRROR_DEFINITIONS = json.loads(_MIRROR_FILE.read_text(encoding='utf-8')) if _MIRROR_FILE.exists() else {}
MIRROR_MODULES = {route: definition['module'] for route, definition in MIRROR_DEFINITIONS.items()}
MIRROR_ROUTES = {'/home', *MIRROR_MODULES}

@dataclass
class Client:
    ws: object
    role: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lease: str | None = None
    heartbeat: float = field(default_factory=time.monotonic)
    requests: OrderedDict = field(default_factory=OrderedDict)
    outbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    queued_bytes: int = 0
    sender: asyncio.Task | None = None
    closing: bool = False
    blocked: bool = False
    last_state_payload: str | None = None
    last_state_sent: float = 0.0
    transport_sequence: int = 0

class Gateway:
    def __init__(self, upstream='ws://127.0.0.1:8080', *, lease_timeout=6,
                 stop_timeout=5, state_timeout=3, send_timeout=2,
                 origins=None, reconnect_delay=1, ui_mirror=False, independent_remote=False):
        if ui_mirror and not MIRROR_MODULES:
            raise RuntimeError('Test module capability manifest is required')
        self.upstream_url = upstream
        self.transport_trace = ui_mirror and os.environ.get('RELAY_MIRROR_TRACE') == '1'
        self.lease_timeout = lease_timeout
        self.stop_timeout = stop_timeout
        self.state_timeout = state_timeout
        # AP clients may briefly stall while the phone renders a large page.
        # The independent test gateway tolerates this without extending the
        # separate six-second control lease; production retains its timeout.
        self.send_timeout = max(send_timeout, TEST_CLIENT_SEND_TIMEOUT) if ui_mirror else send_timeout
        self.origins = set(origins or ['http://47.98.48.17'])
        self.reconnect_delay = reconnect_delay
        self.clients = {}
        self.upstream = None
        self.upstream_guard = None
        self.owner = None
        self.ui_mirror = ui_mirror
        self.independent_remote = independent_remote
        self.remote_enabled_by = None
        self.remote_stop_pending = False
        self.stop_sent_at = 0
        self.mirror_epoch = None
        self.latest_ui = None
        self.ui_seq = 0
        self.local_ui = None
        self.local_ui_seen = 0.0
        self.local_ui_client = None
        self.ui_ready = False
        self.active = None
        self.module = None
        self.last_load = 0.0
        self.phase = 'connecting'
        self.reason = ''
        self.pending_start = 0.0
        self.stop_started = 0.0
        self.stop_seen = False
        self.latest_static = None
        self.lock = asyncio.Lock()
        self.clock_guard_until = 0.0
        self.clock_guard_token = None
        self.tasks = []
        self.close_tasks = set()
        self.closing = False
        self.addresses = []
        self.network_fast_until = 0.0
        self.frontend_path = None
        self.network = {'available': False}
        self.network_busy = False
        self.network_task = None
        self.last_address_refresh = 0.0
        self.hardware = {'usb': False, 'ppsFresh': False, 'ppsSequence': None, 'net': False, 'wifi': False}

    def fresh(self):
        return self.upstream is not None and time.monotonic() - self.last_load < self.state_timeout

    def idle(self):
        return time.monotonic() >= self.clock_guard_until and not self.network_busy and self.fresh() and self.active is False and not self.pending_start and self.phase not in {'stopping', 'fault', 'connecting'}

    def state(self, client):
        authorized = (client.id == self.owner) if self.owner else client.role == 'local'
        if self.independent_remote and self.remote_enabled_by and client.role == 'local':
            authorized = False
        if self.ui_mirror and not self.independent_remote and self.owner == client.id and not self.ui_ready:
            authorized = False
        return {
            'type': 'gateway', 'event': 'state', 'protocolVersion': 1,
            'sessionId': client.id, 'role': client.role, 'lease': client.lease,
            'owner': 'remote' if self.owner else 'local', 'phase': self.phase,
            'active': self.active if self.fresh() and not (self.independent_remote and self.pending_start) else None, 'module': self.module,
            'upstreamOnline': self.fresh(),
            'canControl': not self.network_busy and authorized and self.fresh() and self.phase in {'local', 'remote'},
            'canStart': authorized and self.idle(),
            'reason': self.reason, 'addresses': self.addresses,
            # A connection to the page is not control authority.  Publish the
            # number separately so the local panel can describe that distinction.
            'remoteClients': sum(1 for item in self.clients.values()
                                 if item.role == 'remote' and not item.closing),
            **({'independentRemote': True, 'remoteEnabled': bool(self.remote_enabled_by), 'remoteStopPending': self.remote_stop_pending, 'startPending': bool(self.pending_start)} if self.independent_remote else {}),
            'hardware': dict(self.hardware),
            'networkBusy': self.network_busy,
            **({'network': self.network} if client.role == 'local' else {}),
            **({'uiMirror': False} if self.independent_remote else {'uiMirror': True, 'mirrorEpoch': self.mirror_epoch, 'mirrorSeq': self.ui_seq, 'uiReady': self.ui_ready} if self.ui_mirror else {}),
        }

    async def close_client(self, client, reason):
        if client.closing:
            return
        client.closing = True
        LOG.warning('Client closing role=%s id=%s reason=%s backlog=%d',
                    client.role, client.id[:8], reason, client.outbox.qsize())
        # Never wait for a mobile close handshake while holding the control lock.
        async def close():
            try:
                await asyncio.wait_for(client.ws.close(code=1013, reason=reason), 1)
            except Exception:
                transport = getattr(client.ws, 'transport', None)
                if transport is not None:
                    transport.abort()
        task = asyncio.create_task(close())
        self.close_tasks.add(task)
        task.add_done_callback(self.close_tasks.discard)

    async def client_sender(self, client):
        size, event = 0, ''
        try:
            while not client.closing:
                payload, size = await client.outbox.get()
                if self.ui_mirror:
                    packet = json.loads(payload)
                    event = packet.get('event', packet.get('type', 'unknown'))
                client.queued_bytes -= size
                client.transport_sequence += 1
                if self.transport_trace:
                    LOG.info('Test transport t=%.6f client=%s seq=%d stage=send_begin event=%s bytes=%d queued=%d',
                             time.monotonic(), client.id[:8], client.transport_sequence, event, size, client.outbox.qsize())
                try:
                    await asyncio.wait_for(client.ws.send(payload), self.send_timeout)
                    if self.transport_trace:
                        # Completion only proves the transport accepted the data, not peer receipt.
                        LOG.info('Test transport t=%.6f client=%s seq=%d stage=send_complete event=%s bytes=%d',
                                 time.monotonic(), client.id[:8], client.transport_sequence, event, size)
                finally:
                    client.outbox.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.ui_mirror:
                LOG.warning('Test send failed peer=%s event=%s bytes=%d queued_bytes=%d error=%s',
                            getattr(client.ws, 'remote_address', None), event, size, client.queued_bytes, type(exc).__name__)
            await self.close_client(client, 'send_' + type(exc).__name__)

    def _critical_packet(self, data):
        if not isinstance(data, dict):
            return True
        return data.get('event') in CRITICAL_EVENTS | {'ui_snapshot', 'occupied'} or data.get('type') in CRITICAL_TYPES

    async def block_remote(self, client):
        if client.blocked or client.role != 'remote' or client.id == self.owner:
            return
        client.blocked = True
        await self.deliver(client, {'type': 'gateway', 'event': 'occupied', 'code': 'control_occupied'})
        # Drain outside the control lock. A slow rejected page cannot delay Stop.
        if client.sender is not None:
            async def finish():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.outbox.join(), .5)
                try:
                    await asyncio.wait_for(client.ws.close(code=4009, reason='control_occupied'), 1)
                except Exception:
                    transport = getattr(client.ws, 'transport', None)
                    if transport:
                        transport.abort()
            task = asyncio.create_task(finish())
            self.close_tasks.add(task)
            task.add_done_callback(self.close_tasks.discard)

    def coalesce_display(self, client, data, payload, size):
        event, kind = data.get('event'), data.get('type')
        # Engine value_update packets may contain sequence events or results;
        # only display snapshots and identical-phase load polls are replaceable.
        analog_display = (
            self.independent_remote and kind == 'value_update'
            and data.get('module') in {'ac_test', 'dc_test'}
            and isinstance(data.get('static'), dict)
            and data.keys() <= {'type', 'module', 'static', 'di', 'do'}
        )
        if event != 'ui_snapshot' and kind != 'load' and not analog_display:
            return False
        def matches(previous):
            if not isinstance(previous, dict):
                return False
            if event == 'ui_snapshot':
                return previous.get('event') == event and previous.get('epoch') == data.get('epoch')
            if analog_display:
                return (previous.get('type') == kind and previous.get('module') == data.get('module')
                        and previous.keys() == data.keys()
                        and previous.get('di') == data.get('di') and previous.get('do') == data.get('do')
                        and isinstance(previous.get('static'), dict)
                        and previous['static'].keys() == data['static'].keys())
            return previous.get('type') == kind and previous.get('module') == data.get('module') and (kind != 'load' or previous.get('active') == data.get('active'))
        pending = []
        while not client.outbox.empty():
            old, length = client.outbox.get_nowait()
            client.outbox.task_done()
            pending.append((old, length))
        # Never move new data across an authorization, Stop, result or seed.
        # Coalesce only a consecutive replaceable tail, preserving all barriers.
        found = bool(pending and matches(json.loads(pending[-1][0])))
        if found:
            pending[-1] = (payload, size)
        client.queued_bytes = sum(length for _, length in pending)
        for item in pending:
            client.outbox.put_nowait(item)
        return found

    async def deliver(self, client, data):
        payload = json.dumps(data, ensure_ascii=False)
        if self.ui_mirror and client.blocked and (not isinstance(data, dict) or data.get('event') != 'occupied'):
            return
        # Synthetic clients in unit tests have no transport worker.
        if client.sender is None:
            await asyncio.wait_for(client.ws.send(payload), self.send_timeout)
            return
        if client.closing:
            return
        size = len(payload.encode('utf-8'))
        if self.ui_mirror and isinstance(data, dict) and self.coalesce_display(client, data, payload, size):
            if client.queued_bytes > MAX_CLIENT_BACKLOG_BYTES:
                await self.close_client(client, 'slow_client_backlog')
            return
        if client.outbox.full() or client.queued_bytes + size > MAX_CLIENT_BACKLOG_BYTES:
            if self.ui_mirror and not self._critical_packet(data):
                LOG.warning('Test drop non-critical peer=%s event=%s queued=%d',
                            getattr(client.ws, 'remote_address', None),
                            data.get('event', data.get('type')) if isinstance(data, dict) else type(data).__name__,
                            client.outbox.qsize())
                return
            await self.close_client(client, 'slow_client_backlog')
            return
        client.queued_bytes += size
        client.outbox.put_nowait((payload, size))

    async def deliver_state(self, client, *, force=False):
        data = self.state(client)
        if not self.ui_mirror:
            await self.deliver(client, data)
            return
        # Test-only: changes are immediate; identical states remain a 1s keepalive.
        payload = json.dumps(data, ensure_ascii=False)
        now = time.monotonic()
        if not force and payload == client.last_state_payload and now - client.last_state_sent < 1:
            return
        await self.deliver(client, data)
        if self.transport_trace and not client.blocked:
            LOG.info('Test transport t=%.6f client=%s stage=state_submit queued=%d',
                     now, client.id[:8], client.outbox.qsize())
        client.last_state_payload, client.last_state_sent = payload, now

    async def states(self, *, force=False):
        await asyncio.gather(*(self.deliver_state(c, force=force) for c in list(self.clients.values())))

    async def broadcast(self, data):
        await asyncio.gather(*(self.deliver(c, data) for c in list(self.clients.values())))

    async def reject(self, client, code, request_id=None):
        await self.deliver(client, {'type': 'gateway', 'event': 'rejected', 'code': code,
                                    'requestId': request_id})

    async def send_upstream(self, data):
        if self.upstream is None:
            raise ConnectionError('upstream unavailable')
        await asyncio.wait_for(self.upstream.send(json.dumps(data, ensure_ascii=False)), self.send_timeout)

    async def revoke(self):
        if self.owner in self.clients:
            self.clients[self.owner].lease = None
        self.owner = None
        self.remote_enabled_by = None

    async def begin_stop(self, reason, *, release=True):
        # A second stop request must still revoke a departed controller immediately.
        # Keep the in-flight stop confirmation intact; do not send another stop.
        if self.independent_remote and release and self.owner and not self.idle():
            self.remote_stop_pending = True
        if self.ui_mirror and release:
            await self.revoke()
        if self.phase == 'stopping':
            if self.ui_mirror and not self.independent_remote and not self.remote_stop_pending and self.active is False and self.fresh() and not self.network_busy and not self.pending_start:
                self.phase, self.reason = ('remote' if self.owner else 'local'), reason
                self.stop_started = 0
                await self.states()
            elif self.ui_mirror and release:
                self.reason = reason
                await self.states()
            return
        if release and not self.ui_mirror:
            await self.revoke()
        self.pending_start = 0
        if not self.remote_stop_pending and (self.idle() or (self.ui_mirror and not self.independent_remote and self.active is False and self.fresh() and not self.network_busy)):
            self.phase, self.reason = ('remote' if self.owner else 'local'), reason
            self.stop_started = 0
            await self.states()
            return
        self.phase, self.reason = 'stopping', reason
        self.stop_started, self.stop_seen = time.monotonic(), False
        self.stop_sent_at = 0
        await self.states()
        try:
            await self.send_upstream({'cmd': 'stop'})
            self.stop_sent_at = time.monotonic()
            LOG.info('Stop requested: %s', reason)
        except Exception:
            self.phase, self.reason = 'fault', 'upstream_unavailable'
            await self.states()
            if self.upstream:
                await self.upstream.close()

    async def receive_upstream(self, data):
        async with self.lock:
            kind = data.get('type') if isinstance(data, dict) else None
            if self.ui_mirror and (self.pending_start or self.phase == 'stopping') and kind in {'load', 'stop', 'error', 'report', 'value_update'}:
                LOG.info('Trial confirmation kind=%s active=%s phase=%s pending_age=%.3f',
                         kind, data.get('active'), self.phase,
                         time.monotonic() - self.pending_start if self.pending_start else 0)
            if kind == 'stop':
                if self.independent_remote:
                    self.active = False
                    self.last_load = time.monotonic()
                    self.remote_stop_pending = False
                    self.phase = 'remote' if self.owner else 'local'
                self.stop_seen = True
                self.pending_start = 0
                if self.ui_mirror and self.stop_started and self.active is False and self.fresh():
                    self.phase, self.reason = ('remote' if self.owner else 'local'), (self.reason if self.independent_remote and self.reason == 'start_unconfirmed' else '')
                    self.stop_started = 0
                    self.remote_stop_pending = False
            if kind == 'error':
                self.pending_start = 0
            if kind == 'value_update' and 'static' in data:
                self.latest_static = data
                if self.ui_mirror:
                    previous = getattr(self, '_analog_diagnostic_at', 0)
                    now = time.monotonic()
                    if now - previous >= 1:
                        self._analog_diagnostic_at = now
                        static = data.get('static')
                        LOG.info('Analog relay received module=%s channels=%s owner_present=%s pending=%s',
                                 data.get('module'), len(static) if isinstance(static, dict) else 'invalid',
                                 self.owner in self.clients, bool(self.pending_start))
            if kind == 'load' and type(data.get('active')) is bool:
                self.last_load = time.monotonic()
                if data['active']:
                    self.pending_start = 0
                if not self.pending_start:
                    self.active = data['active']
                self.module = data.get('module') or (self.module if data['active'] else None)
                if self.independent_remote and self.remote_stop_pending:
                    if not data['active'] and self.active is False and (self.stop_seen or self.phase == 'connecting'):
                        self.remote_stop_pending = False
                    elif self.phase == 'connecting':
                        await self.begin_stop('remote_stop_recovery')
                if self.phase == 'connecting':
                    self.phase = 'local' if self.active is False else 'fault'
                    self.reason = '' if self.active is False else 'unexpected_running_state'
                elif self.phase in {'stopping', 'fault'} and self.stop_started:
                    if self.stop_seen and self.active is False:
                        self.phase, self.reason = ('remote' if self.owner else 'local'), (self.reason if self.independent_remote and self.reason == 'start_unconfirmed' else '')
                        self.stop_started = 0
            if not self.ui_mirror:
                await self.broadcast(data)
            else:
                for target in list(self.clients.values()):
                    if self.independent_remote and kind not in {'load', 'stop', 'error'}:
                        if self.owner and target.id != self.owner:
                            continue
                        if not self.owner and (self.remote_stop_pending or target.role != 'local'):
                            continue
                    if target.role == 'remote' and target.id != self.owner and kind == 'value_update':
                        continue
                    await self.deliver(target, data)
            if kind in {'load', 'stop', 'error'}:
                await self.states(force=kind in {'stop', 'error'})

    async def upstream_loop(self):
        while not self.closing:
            # Optional test-machine guard: yield to an existing legacy connection.
            # Production gateways do not install this hook.
            if self.upstream_guard and not await self.upstream_guard():
                if self.reason != 'upstream_in_use':
                    self.phase, self.reason = 'connecting', 'upstream_in_use'
                    await self.states()
                await asyncio.sleep(self.reconnect_delay)
                continue
            try:
                async with connect(self.upstream_url, origin='null', open_timeout=5,
                                   ping_interval=10, ping_timeout=10, max_size=8*1024*1024) as ws:
                    async with self.lock:
                        self.upstream = ws
                        self.phase, self.reason, self.active = 'connecting', '', None
                        self.last_load, self.pending_start, self.stop_started = 0, 0, 0
                        self.stop_seen, self.latest_static = False, None
                        await self.revoke()
                        await self.states()
                    LOG.info('Upstream connected')
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        await self.receive_upstream(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning('Upstream lost: %s: %s', type(exc).__name__, exc)
            finally:
                async with self.lock:
                    self.upstream = None
                    if self.independent_remote and self.owner and (self.active is not False or self.pending_start):
                        self.remote_stop_pending = True
                    self.active, self.phase, self.reason = None, 'connecting', 'upstream_unavailable'
                    self.last_load, self.pending_start = 0, 0
                    await self.revoke()
                    await self.states()
            if not self.closing:
                await asyncio.sleep(self.reconnect_delay)

    async def refresh_addresses(self):
        try:
            process = await asyncio.create_subprocess_exec('ip', '-j', '-4', 'address', 'show', 'scope', 'global', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(process.communicate(), 2)
            self.addresses = [{'host': a['local'], 'iface': row['ifname']} for row in json.loads(stdout) for a in row.get('addr_info', []) if a.get('family') == 'inet' and not ipaddress.ip_address(a['local']).is_loopback]
        except (OSError, ValueError, asyncio.TimeoutError):
            pass

    async def address_loop(self):
        while not self.closing:
            await self.refresh_addresses()
            await asyncio.sleep(15)

    async def watchdog(self):
        while not self.closing:
            await asyncio.sleep(min(.2, self.lease_timeout / 3))
            async with self.lock:
                now = time.monotonic()
                if self.owner:
                    client = self.clients.get(self.owner)
                    if client is None or now - client.heartbeat >= self.lease_timeout:
                        await self.begin_stop('heartbeat_timeout')
                if self.pending_start and now - self.pending_start >= self.stop_timeout:
                    await self.begin_stop('start_unconfirmed', release=not self.independent_remote)
                if self.phase == 'stopping' and now - self.stop_started >= self.stop_timeout:
                    if self.ui_mirror and not self.independent_remote and not self.remote_stop_pending and self.active is False and self.fresh() and not self.pending_start:
                        self.phase, self.reason = ('remote' if self.owner else 'local'), (self.reason if self.independent_remote and self.reason == 'start_unconfirmed' else '')
                        self.stop_started = 0
                    else:
                        self.phase, self.reason = 'fault', 'stop_unconfirmed'
                    await self.states()
                elif self.ui_mirror and not self.independent_remote and not self.remote_stop_pending and self.phase == 'fault' and self.active is False and self.fresh() and not self.pending_start:
                    self.phase, self.reason = ('remote' if self.owner else 'local'), (self.reason if self.independent_remote and self.reason == 'start_unconfirmed' else '')
                    self.stop_started = 0
                    await self.states()
                if self.upstream and self.last_load and not self.fresh():
                    LOG.warning('Upstream state stale age=%.3fs', now - self.last_load)
                    if self.independent_remote and self.owner and (self.active is not False or self.pending_start):
                        self.remote_stop_pending = True
                    self.phase, self.reason, self.active = 'fault', 'status_stale', None
                    await self.revoke()
                    await self.states()
                    await self.upstream.close()

                if self.ui_mirror:
                    # Keep the frontend's 4s status deadline alive even during an upstream fault.
                    await self.states()

    async def network_rpc(self, data):
        socket = os.environ.get('RELAY_TEST_NETWORK_SOCKET', '/run/relay-network/control.sock') if self.ui_mirror else '/run/relay-network/control.sock'
        reader, writer = await asyncio.open_unix_connection(socket)
        try:
            writer.write((json.dumps(data) + '\n').encode())
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), 100))
        finally:
            writer.close()
            await writer.wait_closed()

    async def network_change(self, client, data):
        confirmed = False
        if self.ui_mirror: self.network_fast_until = time.monotonic() + 20
        try:
            response = await self.network_rpc(data)
            confirmed = True
            if response.get('ok'):
                self.network = response['state']
                await self.deliver(client, {'type': 'gateway', 'event': 'network_result', 'ok': True})
            else:
                await self.reject(client, response.get('code', 'network_failed'))
        except (OSError, ValueError, NotImplementedError, asyncio.TimeoutError):
            await self.reject(client, 'network_unavailable')
        finally:
            async with self.lock:
                self.network_busy = not confirmed
                await self.refresh_addresses()
                if self.ui_mirror: self.network_fast_until = time.monotonic() + 20
                await self.states()

    async def network_loop(self):
        while not self.closing:
            if not self.network_busy or not self.network_task or self.network_task.done():
                observed_task = self.network_task
                try:
                    result = await self.network_rpc({'action': 'status'})
                    if result.get('ok'):
                        if self.ui_mirror: await self.refresh_addresses()
                        self.network = result['state']
                        if self.network_task is observed_task and (not observed_task or observed_task.done()):
                            self.network_busy = False
                    else:
                        self.network = {'available': False}
                except (OSError, ValueError, NotImplementedError, asyncio.TimeoutError):
                    self.network = {'available': False}
                await self.states()
            await asyncio.sleep((.5 if time.monotonic() < self.network_fast_until else 2) if self.ui_mirror else 10)

    async def handle(self, client, data):
        if self.ui_mirror and client.blocked:
            return
        if not isinstance(data, dict):
            await self.reject(client, 'invalid_message')
            return
        async with self.lock:
            if data.get('gateway') is True:
                action = data.get('action')
                if self.independent_remote and action == 'remote_enable':
                    if client.role != 'local':
                        await self.reject(client, 'local_only', 'remote-enable')
                    elif self.owner or not self.idle():
                        await self.reject(client, 'device_not_idle', 'remote-enable')
                    else:
                        self.remote_enabled_by = client.id
                        await self.states(force=True)
                    return
                if self.independent_remote and isinstance(action, str) and action.startswith('ui_'):
                    await self.reject(client, 'unsupported_action')
                    return
                if self.ui_mirror and action == 'ui_local_snapshot':
                    if client.role != 'local' or self.owner or not self.idle():
                        return
                    snapshot = data.get('snapshot')
                    if not isinstance(snapshot, dict) or snapshot.get('route') not in MIRROR_ROUTES or len(json.dumps(snapshot).encode()) > 256 * 1024:
                        await self.reject(client, 'ui_snapshot_invalid')
                        return
                    self.local_ui_seen, self.local_ui_client = time.monotonic(), client.id
                    if snapshot != self.local_ui:
                        self.local_ui = snapshot
                        self.mirror_epoch = self.mirror_epoch or uuid.uuid4().hex
                        self.ui_seq += 1
                        self.latest_ui = {'type': 'gateway', 'event': 'ui_snapshot', 'epoch': self.mirror_epoch,
                                          'seq': self.ui_seq, 'snapshot': snapshot, 'sourceId': client.id}
                        await self.states()
                        await self.broadcast(self.latest_ui)
                    return
                if self.ui_mirror and action == 'ui_ready':
                    if client.id == self.owner and data.get('lease') == client.lease and data.get('epoch') == self.mirror_epoch:
                        self.ui_ready = True
                        await self.states()
                    return
                if self.ui_mirror and action == 'ui_reset':
                    # A local page that could not import a snapshot may discard the
                    # cache only after ownership and device idle are confirmed.
                    if client.role != 'local':
                        await self.reject(client, 'local_only')
                    elif self.owner or not self.idle():
                        await self.reject(client, 'device_not_idle')
                    else:
                        self.local_ui = self.latest_ui = None
                        self.local_ui_seen, self.local_ui_client = 0, None
                        self.mirror_epoch, self.ui_seq, self.ui_ready = None, 0, False
                        await self.states()
                    return
                if action in {'ui_snapshot', 'ui_snapshot_get'}:
                    if not self.ui_mirror:
                        await self.reject(client, 'unsupported_action')
                    elif action == 'ui_snapshot_get':
                        if self.latest_ui:
                            await self.deliver(client, self.latest_ui)
                    else:
                        await self.receive_ui(client, data)
                    return
                if action == 'network':
                    if client.role != 'local':
                        await self.reject(client, 'local_only')
                    elif self.ui_mirror and not self.network.get('available'):
                        await self.reject(client, 'network_unavailable')
                    elif self.owner or self.remote_enabled_by or not self.idle():
                        await self.reject(client, 'device_not_idle')
                    elif data.get('operation') not in ({'switch', 'configure_hotspot', 'switch_wired'} if self.ui_mirror else {'switch', 'configure_hotspot'}):
                        await self.reject(client, 'invalid_network_input')
                    elif data.get('operation') == 'switch_wired' and data.get('mode') not in {'direct', 'auto'}:
                        await self.reject(client, 'invalid_network_input')
                    else:
                        self.network_busy = True
                        await self.states()
                        payload = {'action': data['operation'], 'profile': data.get('profile'),
                                   'ssid': data.get('ssid'), 'password': data.get('password')}
                        if data['operation'] == 'switch_wired':
                            payload = {'action': 'switch_wired', 'mode': data['mode']}
                        self.network_task = asyncio.create_task(self.network_change(client, payload))
                    return
                if action == 'heartbeat':
                    if client.id == self.owner and data.get('lease') == client.lease:
                        client.heartbeat = time.monotonic()
                    return
                if self.ui_mirror and action == 'state_get':
                    LOG.info('Test state probe client=%s peer=%s queued=%d sender_done=%s last_state_age=%.2f',
                             client.id[:8], getattr(client.ws, 'remote_address', None), client.outbox.qsize(),
                             client.sender.done() if client.sender else None, time.monotonic() - client.last_state_sent)
                    probe = data.get('probeId')
                    if isinstance(probe, str) and 0 < len(probe) <= 128:
                        await self.deliver(client, {**self.state(client), 'probeId': probe})
                    else:
                        await self.deliver_state(client, force=True)
                    return
                if action == 'claim':
                    if client.role != 'remote':
                        await self.reject(client, 'local_already_controls')
                    elif self.owner == client.id:
                        await self.states()
                    elif self.owner:
                        await self.reject(client, 'control_occupied')
                        if self.ui_mirror:
                            await self.block_remote(client)
                    elif not self.idle():
                        await self.reject(client, 'device_not_idle')
                    elif self.independent_remote and not self.remote_enabled_by:
                        await self.reject(client, 'remote_not_enabled')
                    elif self.ui_mirror and not self.independent_remote and (not self.local_ui or self.local_ui_client not in self.clients or time.monotonic() - self.local_ui_seen > 3):
                        await self.reject(client, 'ui_local_required')
                    elif self.ui_mirror and not self.independent_remote and self.local_ui.get('localToolOpen'):
                        await self.reject(client, 'local_tool_open')
                    else:
                        self.owner, client.lease = client.id, uuid.uuid4().hex
                        if self.independent_remote:
                            self.ui_ready = True
                        elif self.ui_mirror:
                            self.mirror_epoch = uuid.uuid4().hex
                            self.ui_seq, self.ui_ready = 1, False
                            self.latest_ui = {'type': 'gateway', 'event': 'ui_snapshot', 'epoch': self.mirror_epoch,
                                              'seq': 1, 'snapshot': self.local_ui}
                        client.heartbeat = time.monotonic()
                        self.phase, self.reason = 'remote', ''
                        LOG.info('Remote control acquired: %s', client.id[:8])
                        if self.ui_mirror:
                            for target in list(self.clients.values()):
                                if target.role == 'remote' and target.id != client.id:
                                    await self.block_remote(target)
                        await self.states()
                        if self.ui_mirror and not self.independent_remote:
                            for target in list(self.clients.values()):
                                await self.deliver(target, {**self.latest_ui, 'event': 'ui_seed' if target.id == client.id else 'ui_snapshot'})
                    return
                if action == 'release':
                    if client.id == self.owner:
                        await self.begin_stop('remote_released')
                    return
                if action == 'reclaim':
                    if client.role == 'local':
                        await self.begin_stop('local_reclaimed')
                    else:
                        await self.reject(client, 'local_only')
                    return
                if action == 'command':
                    if self.network_busy:
                        await self.reject(client, 'network_busy')
                        return
                    request_id, command = data.get('requestId'), data.get('command')
                    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or not isinstance(command, dict):
                        await self.reject(client, 'invalid_request')
                        return
                    if client.role == 'remote' and (not client.lease or data.get('lease') != client.lease):
                        await self.reject(client, 'session_expired', request_id)
                        return
                    if request_id in client.requests:
                        await self.deliver(client, {'type': 'gateway', 'event': 'accepted', 'requestId': request_id, 'duplicate': True})
                        return
                else:
                    await self.reject(client, 'unsupported_action')
                    return
            else:
                # Updated clients must use envelopes: no silent legacy bypass.
                await self.reject(client, 'gateway_protocol_required')
                return
            cmd = command.get('cmd')
            authorized = client.id == self.owner if self.owner else client.role == 'local'
            if cmd == 'stop':
                if client.role != 'local' and not authorized:
                    await self.reject(client, 'not_controller', request_id)
                    return
                await self.begin_stop('user_stop', release=False)
            else:
                if self.ui_mirror and not self.independent_remote and client.role == 'remote' and not self.ui_ready:
                    await self.reject(client, 'ui_handover_pending', request_id)
                    return
                if not authorized or (self.independent_remote and self.remote_enabled_by and client.role == 'local'):
                    await self.reject(client, 'not_controller', request_id)
                    return
                if not self.fresh() or self.phase not in {'local', 'remote'}:
                    await self.reject(client, 'device_unavailable', request_id)
                    return
                if client.role == 'remote' and cmd not in TEST_COMMANDS and not (cmd == 'sysConfig' and command.get('action') in REMOTE_SYSTEM_ACTIONS):
                    await self.reject(client, 'local_only', request_id)
                    return
                if self.ui_mirror and cmd == 'start' and any(entry.get('module') == command.get('module') and entry.get('canControlTrial') is False for entry in MIRROR_DEFINITIONS.values()):
                    await self.reject(client, 'display_only', request_id)
                    return
                if cmd == 'start' and not self.idle():
                    await self.reject(client, 'device_not_idle', request_id)
                    return
                if self.ui_mirror and client.role == 'remote' and cmd == 'start':
                    expected = MIRROR_MODULES
                    path = (self.latest_ui or {}).get('snapshot', {}).get('route')
                    module = command.get('module')
                    valid_module = module in expected.values() if self.independent_remote else expected.get(path) == module
                    if not valid_module:
                        await self.reject(client, 'ui_context_required', request_id)
                        return
                if cmd in {'update_static', 'next_node'} and (self.active is not True or self.pending_start):
                    await self.reject(client, 'device_not_running', request_id)
                    return
                if cmd not in TEST_COMMANDS and not self.idle():
                    await self.reject(client, 'device_not_idle', request_id)
                    return
                if cmd == 'start':
                    self.reason = ''
                    self.pending_start = time.monotonic()
                    self.active = True
                    self.module = command.get('module')
                    self.latest_static = None
                try:
                    await self.send_upstream(command)
                except Exception:
                    self.phase, self.reason = 'fault', 'send_failed'
                    await self.reject(client, 'send_failed', request_id)
                    if self.upstream:
                        await self.upstream.close()
                    await self.states()
                    return
            client.requests[request_id] = True
            while len(client.requests) > 256:
                client.requests.popitem(last=False)
            await self.deliver(client, {'type': 'gateway', 'event': 'accepted', 'requestId': request_id})
            await self.states()

    async def receive_ui(self, client, data):
        """Display only: validated snapshots must never enter send_upstream."""
        if client.id != self.owner or not client.lease or data.get('lease') != client.lease or data.get('epoch') != self.mirror_epoch:
            await self.reject(client, 'session_expired')
            return
        if not self.ui_ready:
            await self.reject(client, 'ui_handover_pending')
            return
        seq, snapshot = data.get('seq'), data.get('snapshot')
        if type(seq) is not int or not 0 < seq <= 9007199254740991 or seq <= self.ui_seq:
            await self.reject(client, 'ui_sequence_invalid')
            return
        if not isinstance(snapshot, dict) or snapshot.get('route') not in MIRROR_ROUTES or len(json.dumps(snapshot).encode()) > 256 * 1024:
            await self.reject(client, 'ui_snapshot_invalid')
            return
        if self.latest_ui and (self.active is not False or self.pending_start or self.phase == 'stopping') and snapshot['route'] != self.latest_ui['snapshot']['route']:
            await self.reject(client, 'ui_route_locked')
            return
        self.ui_seq = seq
        self.latest_ui = {'type': 'gateway', 'event': 'ui_snapshot', 'epoch': self.mirror_epoch,
                          'seq': seq, 'snapshot': snapshot}
        await self.broadcast(self.latest_ui)

    async def handler(self, ws):
        peer = ws.remote_address[0]
        local = ipaddress.ip_address(peer).is_loopback and ws.request.path != '/remote'
        origin = ws.request.headers.get('Origin', '')
        parsed_origin = urlsplit(origin)
        ports = (ws.local_address[1],) if self.ui_mirror else (8080, 8081)
        allowed_pages = {f"{a['host']}:{p}" for a in self.addresses for p in ports}
        if self.ui_mirror:
            # The accepted socket already identifies the current interface address,
            # even before the address monitor catches up after switching Wi-Fi/AP.
            allowed_pages.add(f'{ws.local_address[0]}:{ws.local_address[1]}')
        local_page = parsed_origin.scheme == 'http' and parsed_origin.netloc in allowed_pages
        if not local and origin not in self.origins and not local_page:
            await ws.close(code=1008, reason='Origin not allowed')
            return
        client = Client(ws, 'local' if local else 'remote')
        client.sender = asyncio.create_task(self.client_sender(client))
        self.clients[client.id] = client
        LOG.info('Client connected role=%s id=%s', client.role, client.id[:8])
        if self.ui_mirror:
            LOG.info('Test connection peer=%s local=%s path=%s origin=%s', ws.remote_address, ws.local_address, ws.request.path, origin)
        occupied = self.ui_mirror and client.role == 'remote' and self.owner and self.owner != client.id
        if not occupied:
            await self.deliver_state(client)
            # Existing local pages must learn that a mobile page has arrived,
            # even when that page has not requested control.
            await self.states(force=True)
            if self.ui_mirror and self.latest_ui:
                await self.deliver(client, self.latest_ui)
            if self.latest_static and not self.independent_remote:
                await self.deliver(client, self.latest_static)
        try:
            if self.ui_mirror and client.role == 'remote' and self.owner and self.owner != client.id:
                await self.block_remote(client)
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (ValueError, TypeError):
                    await self.reject(client, 'invalid_message')
                    continue
                await self.handle(client, data)
        except ConnectionClosed as exc:
            LOG.warning('Client disconnected role=%s id=%s detail=%s', client.role, client.id[:8], exc)
        finally:
            if self.transport_trace:
                LOG.info('Test transport t=%.6f client=%s stage=closed code=%s reason=%s',
                         time.monotonic(), client.id[:8], ws.close_code, ws.close_reason)
            client.sender.cancel()
            await asyncio.gather(client.sender, return_exceptions=True)
            async with self.lock:
                self.clients.pop(client.id, None)
                if self.owner == client.id:
                    await self.begin_stop('remote_disconnected')
                elif self.remote_enabled_by == client.id and not self.owner:
                    await self.revoke()
            # Also refresh the local status when a non-controlling mobile page
            # simply closes.
            await self.states(force=True)

    async def http_request(self, connection, request):
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return None
        path = request.path.split('?')[0]
        if self.ui_mirror and path.startswith('/clock-guard/'):
            if not ipaddress.ip_address(connection.remote_address[0]).is_loopback:
                return connection.respond(403, 'Local service only')
            async with self.lock:
                if path == '/clock-guard/acquire':
                    if not self.idle() or self.owner or self.remote_enabled_by:
                        return connection.respond(409, 'Tester must be idle under local control')
                    self.clock_guard_until = time.monotonic() + 30
                    self.clock_guard_token = uuid.uuid4().hex
                    body = json.dumps({'token': self.clock_guard_token}).encode()
                    return Response(200, 'OK', Headers({'Content-Type': 'application/json', 'Content-Length': str(len(body))}), body)
                if path == '/clock-guard/release' and request.headers.get('X-Clock-Token') == self.clock_guard_token:
                    self.clock_guard_until = 0.0
                    self.clock_guard_token = None
                    return connection.respond(200, 'Released')
                return connection.respond(403, 'Invalid clock guard')
        if path == '/health':
            body = json.dumps({'upstreamOnline': self.fresh(), 'phase': self.phase, 'active': self.active if self.fresh() else None}).encode()
            return Response(200, 'OK', Headers({'Content-Type': 'application/json', 'Content-Length': str(len(body)), 'Cache-Control': 'no-store'}), body)
        if path in {'/', '/index.html'} and self.frontend_path:
            try:
                body = await asyncio.to_thread(Path(self.frontend_path).read_bytes)
                return Response(200, 'OK', Headers({'Content-Type': 'text/html; charset=utf-8', 'Content-Length': str(len(body)), 'Cache-Control': 'no-store'}), body)
            except OSError:
                pass
        return connection.respond(404, 'Not found')

    @staticmethod
    def read_hardware():
        usb = any(Path('/dev').glob('ttyUSB*')) or any(Path('/dev').glob('ttyACM*'))
        if not usb:
            for disk in Path('/sys/class/block').glob('*'):
                try:
                    # Only USB disks, not SD cards or built-in storage.
                    if '/usb' in str(disk.resolve()) and not (disk / 'partition').exists():
                        usb = True
                        break
                except OSError:
                    pass
        sequences = {}
        for device in Path('/sys/class/pps').glob('pps*'):
            try:
                stamp, sequence = (device / 'assert').read_text().strip().split('#')
                if float(stamp) > 0 and int(sequence) > 0:
                    sequences[device.name] = sequence
            except (OSError, ValueError):
                pass
        return bool(usb), sequences

    @staticmethod
    def read_network():
        wired, wireless = False, False
        for interface in Path('/sys/class/net').glob('*'):
            if interface.name == 'lo' or not (interface / 'device').exists():
                continue
            try:
                carrier = (interface / 'carrier').read_text().strip() == '1'
                if (interface / 'wireless').exists():
                    wireless |= carrier and (interface / 'operstate').read_text().strip() == 'up'
                else:
                    wired |= carrier
            except OSError:
                pass
        return {'net': bool(wired), 'wifi': bool(wireless)}

    async def hardware_loop(self):
        previous, observed = {}, {}
        LOG.info("Hardware monitor started")
        while not self.closing:
            usb, sequences = await asyncio.to_thread(self.read_hardware)
            now = time.monotonic()
            for device, sequence in sequences.items():
                # Establish a baseline first; only changes prove a live pulse.
                if device in previous and previous[device] != sequence:
                    observed[device] = now
            previous = sequences
            live = sorted(device for device in sequences if now - observed.get(device, -100) < 2.5)
            hardware = {'usb': usb, 'ppsFresh': bool(live),
                        'ppsSequence': '|'.join(device + ':' + sequences[device] for device in live) or None,
                        **await asyncio.to_thread(self.read_network)}
            if hardware != self.hardware:
                self.hardware = hardware
                await self.states()
            await asyncio.sleep(.1)

    async def start(self):
        self.tasks = [asyncio.create_task(self.upstream_loop()), asyncio.create_task(self.watchdog()), asyncio.create_task(self.address_loop()), asyncio.create_task(self.hardware_loop()), asyncio.create_task(self.network_loop())]

    async def shutdown(self):
        self.closing = True
        if self.upstream and self.active is not False:
            with contextlib.suppress(Exception):
                await self.send_upstream({'cmd': 'stop'})
        if self.network_task:
            self.network_task.cancel()
            await asyncio.gather(self.network_task, return_exceptions=True)
        pending = [*self.tasks, *self.close_tasks]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.upstream:
            await self.upstream.close()

async def main():
    gateway = Gateway(os.getenv('RELAY_UPSTREAM', 'ws://127.0.0.1:8080'),
                      origins=os.getenv('RELAY_REMOTE_ORIGINS', 'http://47.98.48.17').split(','),
                      ui_mirror=os.getenv('RELAY_UI_MIRROR', '0') == '1')
    gateway.frontend_path = os.getenv('RELAY_FRONTEND_FILE', '/opt/RelayProtection/index.html')
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, done.set)
    await gateway.start()
    try:
        ping_interval = TEST_CLIENT_PING_INTERVAL if gateway.ui_mirror else CLIENT_PING_INTERVAL
        ping_timeout = TEST_CLIENT_PING_TIMEOUT if gateway.ui_mirror else CLIENT_PING_TIMEOUT
        async with serve(gateway.handler, os.getenv('RELAY_GATEWAY_HOST', '0.0.0.0'),
                         int(os.getenv('RELAY_GATEWAY_PORT', '8081')),
                         max_size=8*1024*1024, ping_interval=ping_interval, ping_timeout=ping_timeout, close_timeout=2,
                         process_request=gateway.http_request):
            await done.wait()
    finally:
        await gateway.shutdown()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(main())
