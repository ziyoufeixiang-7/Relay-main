import hashlib
import hmac
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orjson
from websockets.datastructures import Headers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comms.AdminAuth import AdminSession, _device_key
from comms.WSGateway import WSGateway


def proof(key, nonce):
    return hmac.new(key, f"relay-ws-admin:v1:admin:{nonce}".encode(), hashlib.sha256).hexdigest()


class AuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.key = bytes(range(32))
        self.s = AdminSession(lambda: self.key, lambda: self.now)

    def challenge(self):
        return self.s.reply({"action": "challenge"})["nonce"]

    def login(self, nonce):
        return self.s.reply({"action": "login", "user": "admin", "proof": proof(self.key, nonce)})

    def test_disconnect_expiry_logout_and_rotation(self):
        for revoke in ("disconnect", "expiry", "logout", "rotate", "delete", "new_challenge"):
            with self.subTest(revoke=revoke):
                self.setUp()
                self.assertTrue(self.login(self.challenge())["success"])
                self.assertTrue(self.s.reply({"action": "identity"})["authenticated"])
                if revoke == "disconnect":
                    self.s.clear()
                elif revoke == "expiry":
                    self.now += self.s.SESSION_SECONDS
                elif revoke == "logout":
                    self.s.reply({"action": "logout"})
                elif revoke == "rotate":
                    self.key = b"b" * 32
                elif revoke == "delete":
                    self.key = None
                else:
                    self.challenge()
                self.assertEqual(self.s.reply({"action": "identity"})["error"], "not_authenticated")

    def test_proof_only_once_and_on_original_connection(self):
        n = self.challenge()
        other = AdminSession(lambda: self.key)
        other.reply({"action": "challenge"})
        msg = {"action": "login", "user": "admin", "proof": proof(self.key, n)}
        self.assertFalse(other.reply(msg)["success"])
        self.assertTrue(self.login(n)["success"])
        self.assertFalse(self.login(n)["success"])
        for field in ({"proof": "0" * 64}, {"proof": {}}, {"user": "root"}):
            n = self.challenge()
            self.assertFalse(self.s.reply(dict(msg, proof=proof(self.key, n)) | field)["success"])
            self.assertFalse(self.login(n)["success"])
        n = self.challenge()
        self.now += self.s.CHALLENGE_SECONDS
        self.assertFalse(self.login(n)["success"])
        self.key = None
        self.assertEqual(self.s.reply({"action": "challenge"})["error"], "not_configured")

    def test_missing_unsafe_or_malformed_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "key"
            with patch("comms.AdminAuth.KEY_PATH", str(path)):
                self.assertIsNone(_device_key())
                path.write_text(self.key.hex() + "\n")
                path.chmod(0o644)
                self.assertIsNone(_device_key())
                path.chmod(0o600)
                self.assertEqual(_device_key(), self.key if os.getuid() == 0 else None)
                path.write_text("wrong\n")
                self.assertIsNone(_device_key())
                path.unlink()
                path.symlink_to(path.with_name("other"))
                self.assertIsNone(_device_key())


class Socket:
    def __init__(self, peer, origin, conversation):
        self.remote_address = (peer, 30000)
        self.request = SimpleNamespace(headers=Headers({"Origin": origin}))
        self.conversation = conversation
        self.replies = []

    async def send(self, message):
        self.replies.append(orjson.loads(message))

    async def __aiter__(self):
        for make in self.conversation:
            yield orjson.dumps(make(self.replies))


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_session_direct_replies_and_legacy_dispatch(self):
        gw = WSGateway()
        gw.dispatcher = AsyncMock()
        key = bytes(range(32))
        auth = {"cmd": "adminAuth"}
        challenge = lambda _: dict(auth, action="challenge")
        identity = lambda _: dict(auth, action="identity")
        ordinary = {"cmd": "sysConfig", "action": "wifiStatus"}
        ws = Socket("127.0.0.1", "null", [
            challenge, lambda replies: dict(auth, action="login", user="admin",
                                            proof=proof(key, replies[0]["nonce"])),
            identity, lambda _: ordinary,
        ])
        with patch("comms.WSGateway.AdminSession", lambda: AdminSession(lambda: key)):
            with self.assertLogs("WSGateway", level="DEBUG") as rx:
                await gw.Handler(ws)
            self.assertTrue(ws.replies[-1]["authenticated"])
            self.assertNotIn(proof(key, ws.replies[0]["nonce"]), str(rx.output))
            self.assertTrue(gw.txQueue.empty())
            self.assertEqual([a.args[0] for a in gw.dispatcher.HandleCommand.await_args_list],
                             [ordinary, {"cmd": "stop"}])
            for addr, origin, err in [("127.0.0.1", "null", "not_authenticated"),
                                      ("192.0.2.2", "null", "local_kiosk_only"),
                                      ("127.0.0.1", "https://example.org", "local_kiosk_only")]:
                ws = Socket(addr, origin, [identity])
                await gw.Handler(ws)
                self.assertEqual(ws.replies[-1]["error"], err)
