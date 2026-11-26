# Integrated mobile control

The control core and capability manifest were imported from frontend repository
commit `4593686652592cfaeb585eb82ee0831f1b7668ad`,
`services/control_gateway/service.py` and `src/config/mirror-modules.json`.
This backend directory is the source of truth for the integrated test deployment.
Legacy frontend gateway scripts remain compatible with older deployed binaries;
they must not run alongside the integrated gateway on the same device.

`RELAY_MOBILE_CONTROL=1` selects `IntegratedGateway` in SysEngine. It serves the
`6phase-test` single-file page and WebSocket protocol on 8080; there is no upstream
WebSocket, redirect rule or second control process. Without this opt-in SysEngine
retains the original direct 8080 protocol. Neither path implements `adminAuth`.
Calibration's own authentication is unchanged.

The integrated mode requires `RELAY_TEST_DEVICE_CONFIG` with an enabled test
configuration whose serial matches the physical Pi. Validation precedes hardware
initialization. Never use `require_enrollment=False` outside isolated unit tests.

The retained control protocol includes local enabling, exclusive remote leases,
six-second heartbeats, request deduplication, local stop/reclaim, per-client bounded
queues and hardware stop confirmation. Raw legacy commands cannot bypass it.
The adapter additionally refuses a new start while EngineCtrl's previous terminal
acknowledgement is pending. A stale feed stops the engine and disables control;
it never synthesizes a successful stop or replays commands.

Engine callbacks enter a FIFO before taking the control lock. Hardware execution
and timing remain in USEEngine/EngineBridge. Local-controller disconnection stops
a running trial when no local client remains; observer disconnection does not.

NetworkManager remains a separate restricted Unix-socket service. Its source is
under `RelayProtection/services/mobile_network`; the backend only calls the socket
when `RELAY_MIRROR_NETWORK=1` is configured. GPS and IEC103 services are not merged.

Offline regression (no real serial/GPIO imports):

```sh
PYTHONPATH=RelayProtection:RelayProtection/comms/mobile_control \
  python -m unittest discover -s RelayProtection/tests/mobile_control -q
```

`SysEngine.bin --self-check` validates packaged imports, capability data and test
enrollment without opening serial/GPIO or starting hardware engines. Run it with
`RELAY_TEST_DEVICE_CONFIG` set on the enrolled Pi before switching services.

Build using `bash deploy/build.sh`. It does not publish. The manifest must be
included as data in the standalone distribution. Preserve each device's existing
calibration files at deployment; packaged calibration files are templates only.
