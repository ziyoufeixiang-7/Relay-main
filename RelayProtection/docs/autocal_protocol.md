# AutoCal WebSocket Protocol

All calibration commands use `cmd: "autoCal"`.
**Auth required**: Must send `auth` first. Without auth, all other actions return an error.

---

## Frontend → Backend

### 1. Auth
```json
{
  "cmd": "autoCal",
  "action": "auth",
  "password": "xxx"
}
```
* **password**: `string` plain text password (default: "100200").

### 2. Start Calibration
```json
{
  "cmd": "autoCal",
  "action": "start",
  "ch": 0,
  "currMode": 0
}
```
* **ch**: `int` Frontend logical channel. `0-5` = voltage, `6-11` = current.
* **currMode**: `int` **(current channels only, ignored for voltage)**.
  * `0` = measure voltage across 75mΩ shunt resistor, backend converts to current.
  * `1` = DMM directly measures current.

### 3. Start Verification
```json
{
  "cmd": "autoCal",
  "action": "verify",
  "ch": 0,
  "currMode": 0
}
```
* **ch**: `int` Same as calibration. `0-5` = voltage, `6-11` = current.
* **currMode**: `int` Same as calibration.

Verification outputs calibrated physical values at multiple test points and measures actual output with DMM. Reports target value, measured value, and error percentage for each point.

**Voltage test points**: AC 10/60/110V, DC ±10/±60/±110V (9 points)
**Current test points**: AC 0.2/1.1/2A, DC ±0.2/±1.1/±2A (9 points)

### 4. Stop
```json
{
  "cmd": "autoCal",
  "action": "stop"
}
```

---

## Backend → Frontend

All messages include `"module": "autoCal"`.

### 1. Auth Result
```json
{
  "module": "autoCal",
  "type": "authResult",
  "success": 1
}
```
* **success**: `int` `1` = password correct, `0` = wrong password.

### 2. Calibration Status (calStatus)
Sent every 0.2s during calibration. Also sent with `progress: 100` when calibration or verification completes.
```json
{
  "module": "autoCal",
  "type": "calStatus",
  "progress": 35,
  "value": -104.982
}
```
* **progress**: `int` `0`-`100`. `100` = complete.
* **value**: `float | null` Current DMM reading. May be `null` during wait phases.

### 3. Verification Point (verifyPoint)
Sent after each test point measurement during verification.
```json
{
  "module": "autoCal",
  "type": "verifyPoint",
  "target": 59.997,
  "measured": 59.87,
  "error": 0.21
}
```
* **target**: `float` Actual physical value corresponding to the register sent to FPGA (after calibration correction).
* **measured**: `float` Average DMM reading.
* **error**: `float` Error percentage: `|measured - target| / |target| * 100`.

### 4. Error (calError)
```json
{
  "module": "autoCal",
  "type": "calError",
  "msg": "Unauthorized, please enter the calibration password first"
}
```
* **msg**: `string` Error description (English).
