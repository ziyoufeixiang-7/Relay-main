# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Firmware for a **relay-protection test instrument** (继电保护测试仪) that runs on a Raspberry Pi driving an FPGA waveform generator. The Pi outputs precise 12-channel voltage/current phasors (6 V + 6 I) into a protection relay under test, watches the relay's trip contacts (DI), and measures action times from hardware timestamps. A web UI (single `deploy/web/index.html`) talks to the backend over WebSocket. The product line is "relay12".

The codebase is bilingual: code and identifiers are English; comments, docs, and commit messages are Chinese.

## Commands

All Python runs through the repo-root venv (Python 3.13): `.venv/bin/python`. There is **no** pytest config, requirements file, or test runner — most tests are standalone scripts.

```bash
# Run the backend locally (must cwd into RelayProtection/ — imports are top-level: `from logic...`)
cd RelayProtection && ../.venv/bin/python SysEngine.py

# Run a single test (each test does its own sys.path insert + __main__ block)
cd RelayProtection && ../.venv/bin/python tests/test_ApiImpedanceTest.py

# Run every test
cd RelayProtection && for t in tests/test_*.py; do ../.venv/bin/python "$t"; done
```

Tests print their own `✅/❌` lines and a `RESULT: passed/total` summary; they are not asserted by an external runner (one exception, `tests/test_diff_solver.py`, uses `unittest`). There is no lint/format config in the repo.

### Build / deploy (see [deploy/README.md](deploy/README.md))

```bash
./deploy/build.sh               # Nuitka-compile backend + pack output/backend.tar.gz (15–30 min)
./deploy/build.sh --pack-only   # repack without recompiling (seconds)
./deploy/publish.sh             # publish backend+frontend to Aliyun (47.98.48.17, relay12)
./deploy/publish-web.sh         # publish frontend only
./deploy/deploy.sh              # install output/dist/ to local /opt/RelayProtection/ (dev box)
```

The backend ships as a Nuitka standalone build. **Consequence for code:** dynamic discovery via `pkgutil` does not see compiled `.so` modules, so every API class must also be explicitly imported at the bottom of [api/BaseApi.py](RelayProtection/api/BaseApi.py) and listed in `BaseApi._REGISTRY`.

## Architecture

Three layers, frontend → controller → engine → FPGA:

```
Web UI (WebSocket JSON, port 8080)
   ▼
EngineCtrl ─ dispatches start/stop, compiles ApiNodeData → USENode binary frames
   ▼
USEEngine ─ permanent FSM, 4 waveform modes, node-to-node jumps (zero runtime compilation)
   ▼  serial /dev/ttyAMA0 @ 230400, frames + ACK (carries SOE hardware timestamps)
FPGA ─ per-channel DDS, Base→Shadow→Active triple-buffer ping-pong → DAC output
```

### Process / threading model (READ THIS — the docs are stale here)

[SysEngine.py](RelayProtection/SysEngine.py) is the entry point. It runs **one process with two asyncio event loops**:

- **Main loop** (pinned to CPU core 2): `WSGateway` (WebSocket), `EngineCtrl`, `HWProtect`.
- **HW thread** `HwEngineThread` (daemon, pinned to core 3): its own asyncio loop running `USEEngine` + `HWGateway` (the serial link).
- [logic/EngineBridge.py](RelayProtection/logic/EngineBridge.py) marshals between the two loops with `call_soon_threadsafe`. Control calls (`manualTrig`, `upsertNodes`, `sendBytes`…) hop main→HW loop; hardware events hop HW→main loop into `EngineCtrl.onEvent`.

> ⚠️ [RelayProtection/docs/architecture_overview.md](RelayProtection/docs/architecture_overview.md) describes an **older/aspirational** design: a separate child process, an `EngineProxy` shadow-state class, and TCP 8081 IPC, referencing `EngineProxy.py` and `HwEngineProcess.py`. **Those files do not exist.** The current implementation is the in-process thread + `EngineBridge` above. The doc is still accurate on FPGA frame semantics and the four waveform modes; trust it there, not on the process topology.

### Key directories

- **`RelayProtection/api/`** — experiment classes (`Api*Test.py`), each a `BaseApi` subclass keyed by `MODULE_KEY`/`MODULE_KEYS` matched against the frontend's `module` field. Existing keys: `impedance_characteristic_test`, `differential_test`, `lp_test` (line protection), `raw_test`, and `ac_test`/`dc_test`/`harmonic_test`/`steps_gradient_test`/`acdc_test` (all `ApiSweepTest`). **This is where new tests are written** — use the `relay-test-api` skill, which is the authoritative guide.
- **`RelayProtection/logic/`** — `EngineCtrl` (controller/compiler), `USEEngine` (FSM), `FPGACodec` (frame encode + `HWConfig` channel map), `Calibration`, `HWProtect`, `AutoCal`/`Calibration` (auto channel calibration), `SysConfig`.
- **`RelayProtection/calc/`** — pure phasor math (stdlib `cmath`/`math`, **no numpy**). `calculation_engine.py` produces 12-channel phasors in the exact dict shape `BaseApi.physDictToReg` expects; `diff.py`/`diff_solver` handle differential-protection equation solving.
- **`RelayProtection/comms/`** — `WSGateway` (websockets + orjson, single-client, new connection preempts old) and `HWGateway` (serial).
- **`deploy/`** — all build/publish/device-init scripts and the frontend. **All build artifacts and the frontend live here, not in `RelayProtection/`.**
- **`Temp/`** — scratch/archive. Per project convention, deprecated files are **moved to `Temp/`, not deleted.**

## Concepts that span multiple files

**Channels.** The canonical identifier everywhere is the **physical channel index (hwCh)**: voltages are even (0,2,4,6,8,10), currents odd (1,3,5,7,9,11). The API layer uses a contiguous 0–15 index and maps with `HWConfig.MapChannel`. In API phasor dicts, 0/1/2 = Ua/Ub/Uc, 3/4/5 = Ux/Uy/Uz, 6/7/8 = Ia/Ib/Ic, 9/10/11 = Ix/Iy/Iz.

**Channel/layer phasor format.** Per channel you specify "layers": layer `"0"` is DC `[dc_amplitude, fundamental_freq_Hz]`; layer `"N"` is the N-th harmonic `[amplitude, phase_deg]`. Frequency is given only on layer 0; harmonic frequencies are N × fundamental. Pure 50 Hz can omit layer 0 and give only layer `"1"`. Amplitudes are written as **peak** values, 16-bit signed, hard ceiling 32767 (overflow wraps to negative → must clamp). `BaseApi.physDictToReg` runs RMS→peak and calibration.

**Calibration.** Linear model, single shared instance `calib = Calibration()`. Forward `reg = amp*factor + bias`, inverse `amp = (reg-bias)/factor`. Amplifier inversion is encoded as a **negative `factor`** in `config/calibration.json` (`{channel: [bias, factorDc, factorAc]}`); the code is sign-transparent.

**The 0xFFFF trap (most common bug).** `USEEngine` always sits parked on a built-in node: `0x0000` (idle: power-on zero-cal + `SYS_START`) or `0xFFFF` (terminate: `SYS_RESET` cuts output). **Any node whose exit jumps to `0x0000`/`0xFFFF` triggers node-table GC + `_doStop()` — it ends the whole experiment and sends report+stop.** Single-shot tests should exit to `0xFFFF`. Multi-point tests must park between points on a *real* idle node, never `0xFFFF`, or they terminate after the first point. `tests/test_ApiImpedanceTest.py` exists specifically to catch a regression of this bug, so its `MockCtrl` faithfully replicates the 0xFFFF semantics rather than calling `onUpdate(0xFFFF)`.

**Action timing.** Always derive action/operate times from **SOE hardware timestamps** carried in FPGA ACKs and DI-change events — never from host-side wall-clock timing.

**OTA updater.** [RelayProtection/utils/updater.py](RelayProtection/utils/updater.py) must remain a **standalone pure-stdlib script** and is deployed to the fixed path `/opt/RelayProtection/updater.py` (the device sudoers whitelist pins that path). It cannot be merged into the main program because it replaces and restarts `SysEngine` itself.

## Reference docs

`RelayProtection/docs/` holds deep design notes: `fpga_protocol.md`, `engine_execution_model.md`, `line-protection-*` (fault math / API / state-sequence), `autocal_*`, and per-API specs under `docs/api/`. The `relay-test-api` skill summarizes the experiment-authoring workflow and is the place to start for new `Api*Test.py` code.
