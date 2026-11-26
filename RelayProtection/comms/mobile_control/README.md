# Integrated mobile control

The control core and capability manifest were imported from frontend repository
commit `4593686652592cfaeb585eb82ee0831f1b7668ad`,
`services/control_gateway/service.py` and `src/config/mirror-modules.json`.
This backend directory is the source of truth for the integrated test deployment.
Legacy frontend gateway scripts remain compatible with older deployed binaries;
they must not run alongside the integrated gateway on the same device.

SysEngine uses the integrated controller by default on port 8080. It serves the
single-file page for the selected product (3phase, 6phase or mcu) and its
WebSocket protocol without an upstream socket or another control process.
`RELAY_MOBILE_CONTROL=0` explicitly selects the legacy direct protocol.
Neither path implements `adminAuth`; calibration's own authentication is unchanged.

Production instances do not need a test enrollment file. When
`RELAY_TEST_DEVICE_CONFIG` is explicitly set, the test configuration must be enabled
and match the Pi's serial. This validation precedes hardware initialization.

Remote controllers can edit system configuration. The gateway accepts the known
Wi-Fi system actions while idle, blocks self-calibration and every update action,
and prevents new trials during a pending network write. The local device retains
maintenance access. Previously acknowledged idle trials do not need a fabricated
stop acknowledgement when a network change disconnects the controller.

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
