"""Local maintenance identity on one WS connection; no shell or system password."""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import stat
import time


KEY_PATH = "/etc/relay-ws-admin.key"  # deliberately outside the OTA installation
_HEX = re.compile(r"[0-9a-f]{64}")


def _device_key():
    try:
        fd = os.open(KEY_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                    stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                return None
            raw = stream.read(66).removesuffix(b"\n").decode("ascii")
            return bytes.fromhex(raw) if _HEX.fullmatch(raw) else None
    except (OSError, ValueError):
        return None


def local_kiosk(websocket):
    try:
        peer = ipaddress.ip_address(websocket.remote_address[0])
        # A file: kiosk page has an opaque origin. This is a filter, NOT an identity.
        origins = websocket.request.headers.get_all("Origin")
        return peer.is_loopback and origins in (["null"], ["file://"])
    except (AttributeError, IndexError, TypeError, ValueError):
        return False


class AdminSession:
    CHALLENGE_SECONDS = 30
    SESSION_SECONDS = 300

    def __init__(self, key_reader=_device_key, clock=time.monotonic):
        self._read_key = key_reader
        self._clock = clock
        self.clear()

    def clear(self):
        self._nonce = None
        self._deadline = self._expires = 0
        self._key_id = None

    def authorized(self):
        if self._key_id is not None and self._clock() < self._expires:
            key = self._read_key()
            if key and hmac.compare_digest(hashlib.sha256(key).digest(), self._key_id):
                return True
        self._key_id = None
        return False

    def reply(self, msg):
        action = msg.get("action")
        res = {"type": "adminAuthResult", "success": False}
        if action == "logout":
            self.clear()
            return {**res, "success": True, "authenticated": False}
        if action == "identity":
            if self.authorized():
                return {**res, "success": True, "authenticated": True, "user": "admin"}
            return {**res, "error": "not_authenticated"}
        if action == "challenge":
            self.clear()
            if not self._read_key():
                return {**res, "error": "not_configured"}
            self._nonce = secrets.token_hex(32)
            self._deadline = self._clock() + self.CHALLENGE_SECONDS
            return {"type": "adminAuthChallenge", "nonce": self._nonce,
                    "expiresIn": self.CHALLENGE_SECONDS}
        if action == "login":
            nonce, deadline = self._nonce, self._deadline
            self.clear()  # one attempt, including malformed proofs
            key, proof = self._read_key(), msg.get("proof")
            if (not nonce or self._clock() >= deadline or not key or msg.get("user") != "admin"
                    or not isinstance(proof, str) or not _HEX.fullmatch(proof)):
                return {**res, "error": "authentication_failed"}
            challenge = ("relay-ws-admin:v1:admin:" + nonce).encode("ascii")
            if not hmac.compare_digest(proof, hmac.digest(key, challenge, "sha256").hex()):
                return {**res, "error": "authentication_failed"}
            self._key_id = hashlib.sha256(key).digest()
            self._expires = self._clock() + self.SESSION_SECONDS
            return {**res, "success": True, "authenticated": True, "user": "admin",
                    "expiresIn": self.SESSION_SECONDS}
        return {**res, "error": "unknown_action"}
