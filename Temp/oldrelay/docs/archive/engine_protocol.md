# USE Engine Protocol

Strict definition of internal communication payload between FSM Engine Backend and frontend Interpreter Layer.
All physical properties (Amplitude, Frequency, Phase) MUST be pre-calculated by the Interpreter Layer directly into primitive FPGA `reg` [U32] values. The engine executes standard FSM logic and blind data forwarding. Timing-related delays remain in [ms].

---

## 1. Data Models

### 1.1 Node Object
Defines a specific state, output configuration, and behavior over time.
```json
{
  "id": 1,
  "doActions": [
    { "delay": 0,   "mask": 0 },  // delay(ms) since entry, mask: parallel output [U8] Range: 0x00-0xFF
    { "delay": 50,  "mask": 1 },
    { "delay": 100, "mask": 2 }
  ],
  
  "static": {
    "0": {
      "0": [0, 0],         // [U32, U32] "Channel ID": { "Layer ID": [Amp Reg, Freq/Phase Reg] } Range: 0x00000000-0xFFFFFFFF
      "2": [0, 0]          // Layer 0 = DC; Layer 1 = fundamental; Layer 2+ = harmonics
    }
  },
  
  "dynamic": {
    "interval": 100,    // (ms) Wait time per sweep step heart-beat
    "count": 100,       // Max execution loops upper limit

    // Mode A: Uniform increment (standard sweep)
    "step": {           
      "0": { "1": [100, 0] } // [U32, U32] "Channel ID": { "Layer ID": [Amp Delta, Freq/Phase Delta] }
    },

    // Mode B: Pre-computed per-step increments (dcComp, non-uniform sweep)
    // Mutually exclusive with "step". "steps" takes priority when both present.
    "steps": [
      { "0": { "0": [100, 0] } },   // Step 0 increment (St_dc[0])
      { "0": { "0": [-5, 0] } },    // Step 1 increment (St_dc[1] = DC[1]-DC[0])
      { "0": { "0": [-4, 0] } }     // Step 2 increment ...
      // ... length = count
    ],

    // Optional: Phase-gated switching (replaces SYS_UPDATE with SYS_PHASE_GATE)
    "phaseGate": {
      "channel": 0,           // [INT] Gate reference channel (0-11)
      "phase": 1073741824     // [U32] Gate trigger phase register value
    },

    // Optional: Inter-step recovery (Reset phase)
    "reset": {
      "time": 100,                   // (ms) Post-step recovery time
      "static": {   
         "0": { "0": [0, 0] }        // [U32, U32] State during recovery time
      },
      "resetDo": 3,                  // [INT] DO mask to output when entering recovery phase
      "overDo": 5                    // [INT] DO mask to output when exiting recovery and entering action phase
    }
  },
  
  "triggers": [
    // Array of <Trigger Object>
  ]
}
```

### 1.2 Trigger Object
Defines condition handlers array evaluated sequentially each logic tick.
```json
{
  "condition": "DI_MATCH",    // [ENUM] "DI_MATCH", "TIME_OUT", "COUNT_OVER", "MANUAL_TRIG"
  "timeout": 1000,            // (ms) Timeout limit. Applies only when condition == TIME_OUT
  "diMask": 0,               // [INT] Filter Mask: 
                              // Bit 0~7: [U8] Range 0x00-0xFF, Channel filter mask target
                              // Bit 8 (0x100): Logic mode (0=OR, 1=AND)
                              // Bit 9 (0x200): DI snapshot ref (0=Since system boot, 1=Since entering node)
                              // Bit 10 (0x400): Match mode (0=Detect change/flip, 1=Detect recover/return to ref)
  "nextId": null             // Target: [Int+]->ID, [-1]->Exit FSM loop, [-2]->Abort terminate, [null]->Emit event, no FSM state disruption
}
```

---

## 2. Engine Initialization Frame (Interpreter -> Engine)
Used to initialize FSM sequence and nodes definition.

```json
{
  "cmd": "start",
  "params": {
    "sys": {
      "vChannels": [0, 1, 2, 3, 4, 5],     // Voltage channel logic mapping
      "iChannels": [6, 7, 8, 9, 10, 11],   // Current channel logic mapping
      "debounce": 61                // [U8] Range: 0x00-0xFF. Target reg value
    },
    "nodes": [
      // Array of <Node Object>
    ]
  }
}
```

---

## 3. Active Interruption Frames (Interpreter -> Engine)
Asynchronous hardware operations invoked while FSM is actively looping.

```json
// manual_trig
{
    "cmd": "manual_trig",
    "id": 1                     // (Optional) Target specific node behavior predefined inside trigger blocks
}

// upsert_nodes
{
    "cmd": "upsert_nodes",
    "params": {
        "nodes": [
            // Array of <Node Object>
        ]
    }
}

// stop
{
    "cmd": "stop"               // Stops FSM loop, resets FPGA, disables amplifier
}
```

---

## 4. Emitted Event Telemetry (Engine -> Interpreter)
Raw transparent payload emitted outward by FSM engine upon state transitions. 

### 4.1 Event: value_update
Broadcasted when wave variables alter or when a physical board DI pin switch flips.
```json
{
    "type": "value_update",
    "id": 1,                    // Current Node ID Execution context
    "tick": 2,                  // Current logic tick. (0 = STATIC mode or Sweep start. >0 = Step index. -1 = Currently in reset_time phase)
    "di": 255,                  // Snapshot integer array containing DI board status
    "do": 0,                    // Snapshot integer array containing DO board status
    "hw_ts": 123456             // [U32] FPGA hardware SOE timestamp (μs). Used by Interpreter for precision timer calculations
}
```

### 4.2 Event: trigger
Broadcasted sequentially upon FSM trigger evaluating to true.
```json
{
    "type": "trigger",
    "condition": "DI_MATCH",    // Event type causing the hit
    "next_id": 2,               // Intended route target Node ID where machine diverts to
    "hw_ts": 123456             // [U32] FPGA hardware SOE timestamp (μs)
}
```
