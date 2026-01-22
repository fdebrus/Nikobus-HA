# Nikobus Protocol Specification

> **Scope & provenance**: This document describes the exact behavior implemented in this repository. Every statement is traceable to code or constants in the repo. If a detail is not present in the code, it is explicitly marked as “Not specified in this repo.”

## 1) Implementation map

**Serial connection / transport**
- `custom_components/nikobus/nkbconnect.py`: IP/Serial transport, CR-terminated read, CR-terminated send, handshake sequence, socket options. (e.g., `read()`, `send()`, `_connect_serial`, `_connect_ip`, `_perform_handshake`) [`custom_components/nikobus/nkbconnect.py:1-215`]
- `custom_components/nikobus/const.py`: baud rate and handshake commands. [`custom_components/nikobus/const.py:21-41`]

**RX buffering + framing (start/end markers, newline behavior, resync rules)**
- `custom_components/nikobus/nkbconnect.py`: `read()` uses `readuntil(b"\r")`, i.e., the frame delimiter is CR (\r). [`custom_components/nikobus/nkbconnect.py:62-90`]
- `custom_components/nikobus/nkblistener.py`: decoding via `data.decode("Windows-1252").strip()`, then dispatch. [`custom_components/nikobus/nkblistener.py:133-150`]

**Message parsing and validation**
- `custom_components/nikobus/nkblistener.py`: `validate_crc()` (length check + CRC8) and dispatch logic for button events, feedback, ACKs, refresh, discovery. [`custom_components/nikobus/nkblistener.py:76-258`]

**CRC8/CRC algorithm**
- `custom_components/nikobus/nkbprotocol.py`: `calc_crc1` (CRC-16/ANSI X3.28, poly 0x1021, init 0xFFFF), `calc_crc2` (CRC-8 with poly 0x99 over ASCII). [`custom_components/nikobus/nkbprotocol.py:9-38`]

**Message type dispatch (feedback, button events, state updates, discovery)**
- `custom_components/nikobus/nkblistener.py`: dispatch rules for button frames (`#N`), feedback refresh, feedback answers, command ACKs, manual refresh, discovery responses. [`custom_components/nikobus/nkblistener.py:157-258`]
- `custom_components/nikobus/coordinator.py`: feedback parsing updates state and raises `nikobus_refreshed`. [`custom_components/nikobus/coordinator.py:287-327`]
- `custom_components/nikobus/nkbactuator.py`: button press event handling and subsequent refresh. [`custom_components/nikobus/nkbactuator.py:52-420`]
- `custom_components/nikobus/discovery/discovery.py`: inventory query/response handling. [`custom_components/nikobus/discovery/discovery.py:27-260`]

**Command building/encoding (switch/dimmer/cover + batch)**
- `custom_components/nikobus/nkbprotocol.py`: `make_pc_link_command()` builds `$` frames with CRC16 + CRC8. [`custom_components/nikobus/nkbprotocol.py:40-60`]
- `custom_components/nikobus/nkbcommand.py`: command codes 0x12/0x17 (get state), 0x15/0x16 (set state, groups 1/2), multi-output batch for modules. [`custom_components/nikobus/nkbcommand.py:86-329`]
- `custom_components/nikobus/nkbAPI.py`: switch/dimmer/cover commands; covers use values 0x01 (open), 0x02 (close), 0x00 (stop). [`custom_components/nikobus/nkbAPI.py:69-214`]
- `custom_components/nikobus/scene.py`: LED/button command queuing (`#N...\r#E1`). [`custom_components/nikobus/scene.py:154-177`]

**Coordinator/state tracking logic**
- `custom_components/nikobus/coordinator.py`: module state cache, refresh, feedback update, state getters/setters. [`custom_components/nikobus/coordinator.py:120-380`]
- `custom_components/nikobus/cover.py`: cover position estimation based on operation time and button events. [`custom_components/nikobus/cover.py:39-744`]

---

## 2) Protocol Overview

### Physical layer assumptions
- **Transport**: Either TCP socket (`<ip>:<port>`) or Serial (e.g., `/dev/ttyUSB0`). [`custom_components/nikobus/nkbconnect.py:31-190`]
- **Baud rate**: 9600 baud for serial connections. [`custom_components/nikobus/const.py:21-27`]
- **Parity/stop bits**: **Not specified in this repo.** (Serial connection is opened with only `baudrate` set.) [`custom_components/nikobus/nkbconnect.py:158-166`]
- **Frame delimiter**: CR (`\r`) terminated frames. [`custom_components/nikobus/nkbconnect.py:62-90`]
- **Encoding**: Incoming bytes decoded as Windows-1252 and stripped. [`custom_components/nikobus/nkblistener.py:133-150`]

### High-level message flow
- **TX (HA → Nikobus)**: commands are queued and sent with a pacing delay; ACK + response is awaited for certain commands. [`custom_components/nikobus/nkbcommand.py:49-190`]
- **RX (Nikobus → HA)**: listener parses incoming frames and dispatches button presses, feedback module answers, ACKs, or discovery responses. [`custom_components/nikobus/nkblistener.py:157-258`]

### Limitations / assumptions implemented here
- **Cover position**: estimated by elapsed time using `operation_time`; state is updated via button events and timers, not via absolute position feedback. [`custom_components/nikobus/cover.py:39-744`]
- **Button refresh**: after a button press, a delayed refresh is performed (0.5s / 1s for dimmers) to re-read output state. [`custom_components/nikobus/nkbactuator.py:317-420`]
- **CRC validation**: only CRC8 is checked; CRC16 is **not** validated in RX frames. [`custom_components/nikobus/nkblistener.py:76-125`]

---

## 3) On-Wire Frame Format

### 3.1 Generic `$` frame (PC-Link)

All PC-Link messages use ASCII hex with this structure:

```
$ LL PAYLOAD CRC16 CRC8
| |  |       |     |
| |  |       |     +-- 2 hex chars (1 byte) CRC8 over ASCII "$" + LL + PAYLOAD + CRC16
| |  |       +-------- 4 hex chars (2 bytes) CRC16 over PAYLOAD (hex bytes)
| |  +---------------- PAYLOAD as ASCII hex (variable length)
| +------------------- Length (LL) in hex = len(PAYLOAD) + 10
+--------------------- Literal '$'
```

**Rules implemented**:
- `LL` is **two hex digits**. `data_len = LL - 10`. [`custom_components/nikobus/nkblistener.py:88-99`]
- Expected message length = `1 + 2 + data_len + 4 + 2`. [`custom_components/nikobus/nkblistener.py:96-104`]
- CRC8 is computed over the ASCII string `"$" + LL + PAYLOAD + CRC16`. [`custom_components/nikobus/nkblistener.py:110-118`]
- CRC16 is computed over PAYLOAD hex bytes (see CRC section). [`custom_components/nikobus/nkbprotocol.py:9-16`]

**Strictness / rejection rules**:
- Non-hex length field → **error + drop**. [`custom_components/nikobus/nkblistener.py:85-93`]
- Length mismatch → **error + drop**. [`custom_components/nikobus/nkblistener.py:96-107`]
- CRC8 mismatch → **error + drop**. [`custom_components/nikobus/nkblistener.py:110-121`]
- Nested `$` frames: the **inner** `$...` portion is CRC-validated. [`custom_components/nikobus/nkblistener.py:81-98`]

### 3.2 Button command frame (`#N`)

Button events and LED commands use plain ASCII commands starting with `#N` (no `$`, no CRC). The listener triggers on the `#N` prefix and extracts the 6-hex button address. [`custom_components/nikobus/nkblistener.py:165-181`]

```
#N AAAAAA
|  |
|  +-- 6 hex chars (button address)
+----- literal "#N"
```

### 3.3 Byte/nybble indexing conventions used in code

The code uses **string slicing indexes** over the ASCII hex string, e.g.:
- `message[3:7]` means **payload characters 0..3** (2 bytes) for a `$` frame. [`custom_components/nikobus/coordinator.py:287-300`]
- `message[9:21]` means **payload characters 6..17** (6 bytes) for module state. [`custom_components/nikobus/coordinator.py:287-300`]

When reading this spec, treat indexes as **0-based character positions** within the full message string (not bytes on the wire).

---

## 4) CRC Details

### CRC16 (CRC-16/ANSI X3.28)
- **Polynomial**: 0x1021
- **Initial value**: 0xFFFF
- **Input**: PAYLOAD bytes decoded from hex string (two hex characters per byte)
- **Reflected?**: **No** (bitwise shift left in implementation).
- **XOR out**: none

Implementation: `calc_crc1` in `custom_components/nikobus/nkbprotocol.py`. [`custom_components/nikobus/nkbprotocol.py:9-16`]

### CRC8 (CRC-8-ATM variant as coded)
- **Polynomial**: 0x99 (as implemented)
- **Initial value**: 0x00
- **Input**: ASCII characters of the full string `"$" + LL + PAYLOAD + CRC16`
- **Reflected?**: **No** (bitwise shift left in implementation).
- **XOR out**: none

Implementation: `calc_crc2` in `custom_components/nikobus/nkbprotocol.py`. [`custom_components/nikobus/nkbprotocol.py:30-37`]

### Worked example (from repo constants)
Frame: `$10110000B8CF9D` (handshake command). [`custom_components/nikobus/const.py:24-35`]

- `LL = 0x10`, so `data_len = 0x10 - 10 = 6` hex chars → PAYLOAD = `110000`
- CRC16 over payload `110000` = `B8CF`
- CRC8 over ASCII `"$10" + "110000" + "B8CF"` = `9D`

This yields the full frame: `$10110000B8CF9D` (matches constant). [`custom_components/nikobus/nkbprotocol.py:9-38`]

---

## 5) Message Catalog (RX + TX)

### 5.1 TX: PC-Link `$` commands (state read/write)

**Direction**: TX (HA → Nikobus)

**Trigger**:
- `get_output_state(address, group)` → `0x12` (group 1) / `0x17` (group 2). [`custom_components/nikobus/nkbcommand.py:86-109`]
- `set_output_state(address, channel, value)` → `0x15` (group 1) / `0x16` (group 2). [`custom_components/nikobus/nkbcommand.py:206-259`]
- `set_output_states(address)` batch → sends both groups if module has > 6 channels. [`custom_components/nikobus/nkbcommand.py:294-329`]

**Payload format** (built by `make_pc_link_command`):
```
PAYLOAD = FUNC(1 byte) + ADDR_LO(1 byte) + ADDR_HI(1 byte) + [ARGS...]
```
Address bytes are **little-endian** (low byte first). [`custom_components/nikobus/nkbprotocol.py:40-60`]

**Side effects**:
- For set commands, coordinator state is updated in-memory via `set_bytearray_state`. [`custom_components/nikobus/nkbAPI.py:59-68`]
- ACK/answer is awaited and parsed; if missing, retries occur. [`custom_components/nikobus/nkbcommand.py:94-190`]

**Examples (raw + parsed + HA effect)**
1) **Switch output ON (group 1)**
   - Raw frame: `$1E150747FF0000000000FF8C3D0A`
   - Parsed: `FUNC=0x15`, `ADDR=0x4707`, `ARGS=FF0000000000FF` (channel 1 set to 0xFF)
   - HA effect: `NikobusAPI.turn_on_switch()` updates channel 1 state to `0xFF` for module `4707`. [`custom_components/nikobus/nkbcommand.py:206-259`, `custom_components/nikobus/nkbAPI.py:69-90`]

2) **Dimmer brightness to 0x80 (group 2)**
   - Raw frame: `$1E16A5C9000080000000FF07EAE2`
   - Parsed: `FUNC=0x16`, `ADDR=0xC9A5`, `ARGS=000080000000FF` (channel 3 set to 0x80)
   - HA effect: `NikobusAPI.turn_on_light()` updates brightness to `0x80`. [`custom_components/nikobus/nkbcommand.py:206-259`, `custom_components/nikobus/nkbAPI.py:95-132`]

3) **Read module state (group 1)**
   - Raw frame: `$10120747402BFC`
   - Parsed: `FUNC=0x12`, `ADDR=0x4707`, `ARGS=none`
   - HA effect: response is parsed and cached by coordinator refresh. [`custom_components/nikobus/nkbcommand.py:86-109`, `custom_components/nikobus/coordinator.py:226-280`]

> Note: All frames above are computed using `make_pc_link_command` in this repo. [`custom_components/nikobus/nkbprotocol.py:40-60`]

---

### 5.2 TX: Button/LED command frames (`#N...\r#E1`)

**Direction**: TX (HA → Nikobus)

**Trigger**:
- Used for LED or button-addressed commands; queued as two CR-separated commands: `#N<ADDR>` then `#E1`. [`custom_components/nikobus/nkbAPI.py:33-47`, `custom_components/nikobus/scene.py:154-177`]

**Frame format**:
```
#N AAAAAA\r#E1
```

**Examples (raw + parsed + HA effect)**
1) Raw: `#N4ECB1A\r#E1` → HA fires a button-equivalent command for address `4ECB1A`. [`custom_components/nikobus/nkbAPI.py:33-47`, `README.md:305-338`]
2) Raw: `#NC86C4E\r#E1` → used for shutter button entry in config. [`custom_components/nikobus/nkbAPI.py:33-47`, `README.md:341-357`]
3) Raw: `#N4ECB1A\r#E1` via scene handler (queues LED commands). [`custom_components/nikobus/scene.py:154-177`, `README.md:305-338`]

---

### 5.3 RX: Button press events (`#N...`)

**Direction**: RX (Nikobus → HA)

**Trigger**:
- Listener scans for `#N` in incoming frames; if present, it extracts the 6-hex button address and triggers `NikobusActuator.handle_button_press()`. [`custom_components/nikobus/nkblistener.py:165-181`]

**Fields**:
- `#N` literal prefix
- `AAAAAA`: 6 hex chars button address

**Side effects**:
- Fires `nikobus_button_pressed`, `nikobus_button_released`, and derived timing events. [`custom_components/nikobus/nkbactuator.py:52-207`]
- Optional state refresh for impacted modules; emits `nikobus_button_operation` with module/group metadata. [`custom_components/nikobus/nkbactuator.py:317-420`]

**Examples (raw + parsed + HA effect)**
1) Raw: `#N4ECB1A` → button address `4ECB1A` → triggers button press events, then refreshes impacted modules if configured. [`custom_components/nikobus/nkblistener.py:165-181`, `custom_components/nikobus/nkbactuator.py:52-420`, `README.md:305-338`]
2) Raw: `#NC86C4E` → button address `C86C4E` → used for shutter button handling (operation_time if configured). [`custom_components/nikobus/nkblistener.py:165-181`, `custom_components/nikobus/nkbactuator.py:317-420`, `README.md:341-357`]
3) Raw: `#N4ECB1A` → button address `4ECB1A` → emits timing buckets (e.g., `nikobus_button_pressed_1`) based on duration. [`custom_components/nikobus/nkbactuator.py:93-200`, `README.md:86-93`]

---

### 5.4 RX: Feedback module answer (`$1C...`)

**Direction**: RX (Nikobus → HA)

**Trigger**:
- Listener matches `$1C` prefix and validates CRC8, then forwards to `process_feedback_data()`. [`custom_components/nikobus/nkblistener.py:212-231`]

**Parsing (as implemented)**:
- `module_address_raw = message[3:7]` (two bytes)
- `module_address = module_address_raw[2:] + module_address_raw[:2]` (byte swap)
- `module_state_raw = message[9:21]` (6 bytes / 12 hex chars)
- Group for this feedback is tracked by `_handle_feedback_refresh()` based on `$1012`/`$1017` refresh commands. [`custom_components/nikobus/coordinator.py:287-327`, `custom_components/nikobus/nkblistener.py:236-258`, `custom_components/nikobus/nkblistener.py:266-276`]

**Side effects**:
- Updates the coordinator’s in-memory state cache and fires `nikobus_refreshed`. [`custom_components/nikobus/coordinator.py:287-327`]

**Examples (raw + parsed + HA effect)**
> The following examples are **constructed using the repository’s frame and CRC algorithms** to illustrate the parsing rules above.

1) Raw: `$1C074700FF0000000000CCAEA3`
   - Parsed module address raw `0747` → address `4707`
   - Parsed state `FF0000000000` (group 1)
   - HA effect: coordinator updates `nikobus_module_states["4707"][0:6]`. [`custom_components/nikobus/coordinator.py:287-327`]

2) Raw: `$1CA5C9000000008000001EF205`
   - Parsed module address raw `A5C9` → address `C9A5`
   - Parsed state `000000800000` (group 1)
   - HA effect: updates cached dimmer levels for group 1. [`custom_components/nikobus/coordinator.py:287-327`]

3) Raw: `$1C9483000000000000FF43D59B`
   - Parsed module address raw `9483` → address `8394`
   - Parsed state `0000000000FF` (group 1)
   - HA effect: updates cached channel state and triggers `nikobus_refreshed`. [`custom_components/nikobus/coordinator.py:287-327`]

---

### 5.5 Discovery / inventory (TX + RX)

**Direction**:
- TX: inventory queries sent by `NikobusDiscovery.query_module_inventory()`
- RX: inventory responses handled by `parse_inventory_response()` and `parse_module_inventory_response()`

**Notes**:
- Inventory messages are built using `make_pc_link_inventory_command()`, which also uses CRC16 + CRC8. [`custom_components/nikobus/nkbprotocol.py:63-76`, `custom_components/nikobus/discovery/discovery.py:56-121`]
- Response parsing uses header prefixes (`$0510$2E`, `$0522$1E`) and then slices payload chunks; chunk sizes differ by module type. [`custom_components/nikobus/const.py:56-57`, `custom_components/nikobus/discovery/discovery.py:131-222`]
- **Concrete RX inventory frames are not specified in this repo.** (No raw samples are present in code or logs.)

---

## 6) Edge cases & robustness

- **Framing errors**: read is CR-delimited only; if CR is missing, `readuntil` times out and the connection is closed. [`custom_components/nikobus/nkbconnect.py:62-90`]
- **Concatenated frames**: if a message contains multiple `$`, the CRC validator extracts the second `$` and validates the inner frame. [`custom_components/nikobus/nkblistener.py:81-98`]
- **Non-hex characters**: invalid length field or CRC mismatch is logged and the frame is dropped. [`custom_components/nikobus/nkblistener.py:85-121`]
- **CRC mismatch**: CRC8 mismatch is logged and the frame is dropped. [`custom_components/nikobus/nkblistener.py:110-121`]
- **Queueing delays**: commands are paced with a fixed delay; covers coalesce position changes and stop after a timer. [`custom_components/nikobus/nkbcommand.py:49-110`, `custom_components/nikobus/cover.py:447-744`]

---

## 7) Troubleshooting (repo-specific)

- **CRC8 mismatch**: check for missing/extra characters between `$` and CRC8; only CRC8 is validated here. [`custom_components/nikobus/nkblistener.py:76-125`]
- **Length mismatch**: verify the `LL` field equals `len(PAYLOAD) + 10`. [`custom_components/nikobus/nkblistener.py:88-104`]
- **No button events**: ensure incoming frames contain `#N` and are CR-terminated. [`custom_components/nikobus/nkblistener.py:165-181`, `custom_components/nikobus/nkbconnect.py:62-90`]
- **Cover position off**: operation-time based estimator; verify `operation_time` values in config. [`custom_components/nikobus/cover.py:39-744`]

