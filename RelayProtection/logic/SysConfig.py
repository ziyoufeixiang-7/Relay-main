import asyncio
import json
import os
import re
from utils.SysLogger import GetLogger

logger = GetLogger("SysConfig")

# Paths — work for both deployed (compiled) and dev (source) mode.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)  # RelayProtection/ or /opt/RelayProtection

VERSION_FILE = os.path.join(_PROJECT_DIR, "version.json")
UPDATER_SCRIPT = os.path.join(_PROJECT_DIR, "Updater.py")
def _getLocalProduct() -> str:
    try:
        with open(UPDATER_SCRIPT, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRODUCT") and "=" in line:
                    return line.split("=")[1].strip().strip('\'"')
    except Exception:
        pass
    return "relay12"

PRODUCT = _getLocalProduct()
UPDATE_BASE_URL = f"http://47.98.48.17/update/{PRODUCT}"
MEDIA_BASE = "/media/pi"


def _localVersion() -> str:
    """Read and return the local version from version.json."""
    try:
        with open(VERSION_FILE, "r") as f:
            return json.load(f).get("version", "0.0.0")
    except (FileNotFoundError, json.JSONDecodeError):
        return "0.0.0"


def _findUsbUpdateDir() -> str:
    """Find and return the path of the first USB drive containing the update folder."""
    if not os.path.isdir(MEDIA_BASE):
        return ""
    for device in os.listdir(MEDIA_BASE):
        usb_path = os.path.join(MEDIA_BASE, device, PRODUCT)
        real = os.path.realpath(usb_path)
        if real.startswith("/media/") and os.path.isdir(usb_path):
            return usb_path
    return ""


def _updaterArgs(params: dict):
    """Construct command-line arguments for Updater.py based on the requested source."""
    source = params.get("source", "web")
    if source == "usb":
        usb_path = _findUsbUpdateDir()
        if not usb_path:
            return None, "No USB update drive found"
        return ["--source", "usb", "--usb-dir", usb_path], ""
    return ["--source", "web"], ""


class SysConfig:
    def __init__(self, send_func):
        """Initialize SysConfig with a WebSocket send function and empty task tracking."""
        self._send = send_func
        self._updating = False
        self._tasks: set = set()

    # ===== Dispatch / replies =====

    def handle_command(self, msg: dict):
        """Dispatch incoming WebSocket commands to their corresponding async handler methods."""
        action = msg.get("action") or ""
        handler = getattr(self, "_handle" + action[:1].upper() + action[1:], None)
        if handler:
            task = asyncio.create_task(handler(msg))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        else:
            self._sendError(f"Unknown SysConfig action: {action}")

    def _sendError(self, msg: str):
        """Send a generic error message over WebSocket."""
        self._send({"type": "error", "msg": msg})

    def _fail(self, res_type: str, msg: str):
        """Send a typed error response over WebSocket."""
        self._send({"type": res_type, "status": "error", "msg": msg})

    @staticmethod
    def _versionResp(res_type: str, current: str, *, latest: str = None,
                     changelog: str = "", error: str = None) -> dict:
        """Format a standardized version comparison response."""
        if error is not None:
            return {"type": res_type, "current": current, "hasUpdate": False, "error": error}
        resp = {"type": res_type, "current": current, "latest": latest,
                "hasUpdate": latest != current}
        if resp["hasUpdate"]:
            resp["changelog"] = changelog
        return resp

    # ===== Subprocess helpers =====

    async def _run(self, *cmd, timeout: float = 20.0):
        """Execute a shell command asynchronously and return its exit code, stdout, and stderr."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return 127, "", f"{cmd[0]} not found"
        except Exception as e:
            return 1, "", str(e)

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return 124, "", f"{cmd[0]} timeout"

    # ===== OTA Update =====

    async def _handleCheckUpdate(self, msg: dict):
        """Fetch the latest version info from the remote server and compare it with the local version."""
        current = _localVersion()
        try:
            rc, out, err = await self._run(
                "curl", "-s", "--connect-timeout", "10", "--max-time", "15",
                f"{UPDATE_BASE_URL}/version.json", timeout=20)
            if rc != 0:
                raise RuntimeError(f"Server unreachable: {err}")
            remote = json.loads(out)
        except json.JSONDecodeError:
            self._send(self._versionResp("updateInfo", current, error="Invalid server response"))
        except Exception as e:
            self._send(self._versionResp("updateInfo", current, error=str(e)))
        else:
            self._send(self._versionResp(
                "updateInfo", current,
                latest=remote.get("version", "0.0.0"),
                changelog=remote.get("changelog", "")))

    async def _runUpdater(self, args: list, res_type: str) -> bool:
        """Run Updater.py as a separate process and stream its JSON progress back to the client."""
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/python3", UPDATER_SCRIPT, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        while line := await proc.stdout.readline():
            try:
                self._send({"type": res_type, **json.loads(line)})
            except json.JSONDecodeError:
                continue
        await proc.wait()
        if proc.returncode != 0:
            err = (await proc.stderr.read()).decode(errors="replace")
            logger.error(f"Updater.py failed (rc={proc.returncode}): {err}")
        return proc.returncode == 0

    async def _handleStartUpdate(self, msg: dict):
        """Start the OTA update process, preventing concurrent update attempts."""
        if self._updating:
            self._fail("updateStatus", "Update already in progress")
            return
        args, error = _updaterArgs(msg.get("params", {}))
        if error:
            self._fail("updateStatus", error)
            return

        logger.info(f"Starting update: {args}")
        self._updating = True
        try:
            if await self._runUpdater(args, "updateStatus"):
                logger.info("Update completed successfully")
        except Exception as e:
            logger.error(f"Update process error: {e}")
            self._fail("updateStatus", str(e))
        finally:
            self._updating = False

    async def _handleRollback(self, msg: dict):
        """Initiate the rollback process to restore the previous system version."""
        logger.info("Starting rollback...")
        try:
            if await self._runUpdater(["--rollback"], "rollbackStatus"):
                logger.info("Rollback completed")
        except Exception as e:
            logger.error(f"Rollback error: {e}")
            self._fail("rollbackStatus", str(e))

    # ===== USB scan =====

    async def _autoMountUsb(self):
        """Detect unmounted USB partitions and automatically mount them to /media/pi."""
        rc, out, _ = await self._run(
            "lsblk", "-nrpo", "NAME,TYPE,FSTYPE,MOUNTPOINT,LABEL", timeout=10)
        if rc != 0:
            return
        for line in out.splitlines():
            parts = line.split(" ", 4)
            if len(parts) < 4:
                continue
            dev, dtype, fstype, mountpoint = parts[:4]
            label = parts[4].strip() if len(parts) > 4 else ""

            if not (dtype in ("part", "disk") and dev.startswith("/dev/sd")
                    and fstype and not mountpoint):
                continue
            mount_dir = os.path.join(MEDIA_BASE, label or os.path.basename(dev))
            try:
                os.makedirs(mount_dir, exist_ok=True)
                for opts in (["-o", "uid=1000,gid=1000"], []):
                    rc, _, _ = await self._run("sudo", "mount", *opts, dev,
                                               mount_dir, timeout=10)
                    if rc == 0:
                        break
                logger.info(f"Auto-mounted {dev} -> {mount_dir}")
            except Exception as e:
                logger.warning(f"Auto-mount failed for {dev}: {e}")

    async def _handleUsbScan(self, msg: dict):
        """Scan mounted USB drives for local update packages and report the found version."""
        try:
            await self._autoMountUsb()
        except Exception as e:
            logger.warning(f"USB detection error: {e}")

        current = _localVersion()
        usb_path = _findUsbUpdateDir()
        if not usb_path:
            self._send(self._versionResp("usbScanResults", current,
                                         error="No USB update drive found"))
            return

        ver_file = os.path.join(usb_path, "version.json")
        try:
            with open(ver_file, "r") as f:
                info = json.load(f)
            latest = info.get("version", "?")
            changelog = info.get("changelog", "")
        except Exception:
            latest, changelog = "?", ""

        self._send(self._versionResp("usbScanResults", current,
                                     latest=latest, changelog=changelog))

    # ===== WiFi (NetworkManager) =====

    async def _nmcli(self, *args, timeout: float = 20.0):
        """Wrapper for executing NetworkManager (nmcli) commands."""
        return await self._run("nmcli", *args, timeout=timeout)

    @staticmethod
    def _nmcliRows(out: str):
        """Parse tabular output from nmcli into rows of fields, handling escaped colons."""
        for line in out.split("\n"):
            if line:
                yield [f.replace(r"\:", ":") for f in re.split(r"(?<!\\):", line)]

    @classmethod
    def _findRow(cls, out: str, pred):
        """Find the first row in nmcli output that satisfies the given predicate."""
        return next((p for p in cls._nmcliRows(out) if pred(p)), None)

    async def _handleWifiScan(self, msg: dict):
        """Scan for nearby WiFi networks and return their SSID, signal strength, and saved status."""
        rc, out, err = await self._nmcli("-t", "-f", "IN-USE,SSID,SIGNAL", "dev", "wifi", "list", timeout=15)
        if rc != 0:
            self._sendError(f"WiFi scan failed: {err}")
            return
        rc_saved, out_saved, _ = await self._nmcli("-t", "-f", "NAME,TYPE", "connection", "show")
        saved_names = {p[0] for p in self._nmcliRows(out_saved)
                       if len(p) >= 2 and p[1] in ("802-11-wireless", "wifi")} if rc_saved == 0 else set()

        nets = {}
        for p in self._nmcliRows(out):
            if len(p) < 3 or not p[1] or p[1] == "--":
                continue
            in_use, ssid, signal = p[0], p[1], p[2]
            active = in_use.strip() == "*"
            cur = nets.get(ssid)
            if not cur:
                nets[ssid] = {"ssid": ssid, "signal": signal, "active": active, "saved": ssid in saved_names}
            else:
                if signal.isdigit() and str(cur["signal"]).isdigit() and int(cur["signal"]) < int(signal):
                    cur["signal"] = signal
                cur["active"] = cur["active"] or active
        self._send({"type": "wifiScanResults", "data": list(nets.values())})

    async def _handleWifiConnect(self, msg: dict):
        """Attempt to connect to a specific WiFi network, automatically handling existing corrupted profiles."""
        params = msg.get("params", {})
        ssid = params.get("ssid", "")
        password = params.get("password", "")
        if not ssid:
            self._fail("wifiConnectResult", "SSID cannot be empty")
            return
        if not password and await self._needsPassword(ssid):
            self._fail("wifiConnectResult", "Secrets were required, but not provided.")
            return
        try:
            args = ["dev", "wifi", "connect", ssid]
            if password:
                args += ["password", password]
            rc, out, err = await self._nmcli(*args, timeout=60.0)
            if rc != 0:
                logger.warning(f"WiFi connect failed initially. Triggering auto-cleanup for '{ssid}'. Err: {err}")
                await self._nmcli("connection", "delete", ssid, timeout=10.0)
                if password:
                    logger.info(f"Retrying connection for '{ssid}' with password after cleanup...")
                    rc, out, err = await self._nmcli(*args, timeout=60.0)
            if rc == 0:
                self._send({"type": "wifiConnectResult", "status": "success", "msg": out})
            else:
                self._fail("wifiConnectResult", err or out)
        except Exception as e:
            logger.error(f"WiFi connect error: {e}")
            self._fail("wifiConnectResult", str(e))

    async def _needsPassword(self, ssid: str) -> bool:
        """Check if a WiFi network requires a password to prevent nmcli from hanging on auth prompts."""
        rc, out, _ = await self._nmcli("-t", "-f", "SSID,SECURITY", "dev", "wifi", "list", timeout=10.0)
        if rc != 0:
            return False
        row = self._findRow(out, lambda p: len(p) >= 2 and p[0] == ssid)
        sec = row[1].strip() if row else ""
        if sec and sec != "--":
            logger.warning(f"Prevented system popup: Blocked empty password attempt for secured network '{ssid}' ({sec})")
            return True
        return False

    async def _handleWifiSavedList(self, msg: dict):
        """Retrieve a list of all previously saved WiFi connection profiles."""
        rc, out, err = await self._nmcli("-t", "-f", "NAME,UUID,TYPE,ACTIVE,AUTOCONNECT", "connection", "show")
        if rc != 0:
            self._sendError(f"WiFi saved list failed: {err}")
            return
        saved = [
            {"name": p[0], "uuid": p[1], "active": p[3] == "yes", "autoconnect": p[4] == "yes"}
            for p in self._nmcliRows(out)
            if len(p) >= 5 and p[2] in ("802-11-wireless", "wifi")
        ]
        self._send({"type": "wifiSavedResults", "data": saved})

    async def _handleWifiConnectSaved(self, msg: dict):
        """Connect to a previously saved WiFi network using its UUID."""
        await self._connAction(msg, "up", "wifiConnectResult", timeout=30.0)

    async def _handleWifiForget(self, msg: dict):
        """Delete a saved WiFi network profile."""
        await self._connAction(msg, "delete", "wifiForgetResult")

    async def _connAction(self, msg: dict, action: str, res_type: str, timeout: float = 20.0):
        """Execute a generic connection action (like connect or delete) on a specific UUID."""
        cid = msg.get("params", {}).get("uuid", "")
        if not cid:
            self._fail(res_type, "uuid cannot be empty")
            return
        rc, out, err = await self._nmcli("connection", action, cid, timeout=timeout)
        self._send({"type": res_type, "status": "success" if rc == 0 else "error", "msg": out if rc == 0 else err})

    async def _handleWifiStatus(self, msg: dict):
        """Check the current active WiFi connection status, including SSID, signal strength, and IP address."""
        cur, iface, ip = None, None, None
        rc, out, _ = await self._nmcli("-t", "-f", "IN-USE,SSID,SIGNAL", "dev", "wifi")
        if rc == 0:
            row = self._findRow(out, lambda p: len(p) >= 3 and p[0].strip() == "*")
            if row:
                cur = {"ssid": row[1], "signal": row[2]}
        rc2, out2, _ = await self._nmcli("-t", "-f", "DEVICE,TYPE", "device")
        if rc2 == 0:
            row = self._findRow(out2, lambda p: len(p) >= 2 and p[1] == "wifi")
            if row:
                iface = row[0]
        if iface:
            rc3, ipout, _ = await self._nmcli("-g", "IP4.ADDRESS", "device", "show", iface)
            if rc3 == 0 and ipout:
                ip = ipout.split("\n")[0].split("/")[0] or None
        res = {"type": "wifiStatus", "connected": cur is not None, "ip": ip}
        if cur:
            res.update(cur)
        self._send(res)
