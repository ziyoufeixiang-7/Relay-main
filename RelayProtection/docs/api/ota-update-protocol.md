# OTA Update WebSocket Protocol

All messages are JSON over the existing WebSocket connection.

## 1. Check Update

**Request:**
```json
{ "cmd": "sysConfig", "action": "checkUpdate" }
```

**Response — has update:**
```json
{ "type": "updateInfo", "current": "1.0.0", "latest": "1.1.0", "hasUpdate": true, "changelog": "..." }
```

**Response — no update:**
```json
{ "type": "updateInfo", "current": "1.0.0", "latest": "1.0.0", "hasUpdate": false }
```

**Response — error:**
```json
{ "type": "updateInfo", "current": "1.0.0", "hasUpdate": false, "error": "Server unreachable: ..." }
```

## 2. Start Update

**Request:**
```json
{ "cmd": "sysConfig", "action": "startUpdate", "params": { "source": "web" } }
{ "cmd": "sysConfig", "action": "startUpdate", "params": { "source": "usb" } }
```

**Status flow:**
```
downloading (0~100%) → installing → success → [service restart, WS disconnect]
```

**Response — progress:**
```json
{ "type": "updateStatus", "status": "downloading", "progress": 45 }
{ "type": "updateStatus", "status": "installing" }
```

**Response — success:**
```json
{ "type": "updateStatus", "status": "success", "msg": "Update completed successfully" }
```

**Response — error:**
```json
{ "type": "updateStatus", "status": "error", "msg": "SHA-256 mismatch..." }
{ "type": "updateStatus", "status": "error", "msg": "Update already in progress" }
{ "type": "updateStatus", "status": "error", "msg": "No USB update drive found" }
```

## 3. USB Scan

USB drive root must contain `relay12/` folder (same structure as cloud).

**Request:**
```json
{ "cmd": "sysConfig", "action": "usbScan" }
```

**Response — has update:**
```json
{ "type": "usbScanResults", "current": "1.0.0", "latest": "1.1.0", "hasUpdate": true, "changelog": "..." }
```

**Response — no update:**
```json
{ "type": "usbScanResults", "current": "1.0.0", "latest": "1.0.0", "hasUpdate": false }
```

**Response — no USB:**
```json
{ "type": "usbScanResults", "current": "1.0.0", "hasUpdate": false, "error": "No USB update drive found" }
```

## 4. Rollback

**Request:**
```json
{ "cmd": "sysConfig", "action": "rollback" }
```

**Response — success:**
```json
{ "type": "rollbackStatus", "status": "success", "msg": "Rollback completed successfully" }
```

**Response — error:**
```json
{ "type": "rollbackStatus", "status": "error", "msg": "No backup found" }
```

## Status Reference

| status | Description |
|--------|-------------|
| `downloading` | Downloading/copying, `progress` = 0~100 |
| `installing` | Extracting and installing |
| `success` | Completed (service will restart, WS disconnect) |
| `error` | Failed, see `msg` |
