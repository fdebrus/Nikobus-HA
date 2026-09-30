# Nikobus PC-Link Protocol

A description of the serial protocol spoken between a host and a Nikobus
PC-Link (05-200 / feature module 05-0A), and of the memory layouts of the
output and feedback modules it can address. Everything here is observable
on the wire of a working installation and has been validated against real
modules.

Addresses are 4 hex characters (a 16-bit module address). On the wire the
two address bytes are sent **little-endian** (low byte first). Multi-byte
integers inside replies are noted per field.

---

## 1. Transport and framing

The PC-Link presents a serial port (typically `/dev/ttyUSB0`) at the usual
Nikobus line settings. Two frame styles share the line:

- **ASCII control frames** — lines beginning with `#`, terminated by CR
  (`\r`). Used to arm the bus event relay and to inject button presses.
- **Structured command frames** — beginning with `$`, hex-encoded.

### 1.1 Structured frame format

```
$  <LEN>  <PAYLOAD-hex>  <CRC16>  <CRC8>
```

| Field     | Size (chars) | Meaning                                                             |
|-----------|--------------|---------------------------------------------------------------------|
| `$`       | 1            | Frame start                                                         |
| `LEN`     | 2            | `10 + len(PAYLOAD-hex)`, as two hex digits                          |
| `PAYLOAD` | n            | Function byte, address bytes, arguments — all hex                   |
| `CRC16`   | 4            | CRC-16/CCITT over the payload **bytes**, big-endian in the frame    |
| `CRC8`    | 2            | CRC-8 over the whole preceding **ASCII** of the frame               |

The `LEN` byte doubles as the frame's declared length and is what the
receiver checks first.

### 1.2 CRC-16 (payload)

CRC-16/CCITT, polynomial `0x1021`, initial value `0xFFFF`, computed over
the raw payload bytes (not the ASCII), result appended big-endian.

```python
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
```

### 1.3 CRC-8 (frame)

CRC-8, polynomial `0x99`, initial value `0x00`, computed over the ASCII
characters of the frame up to (not including) the CRC-8 itself.

```python
def crc8(text: str) -> int:
    crc = 0
    for ch in text.encode("ascii"):
        crc ^= ch
        for _ in range(8):
            crc = ((crc << 1) ^ 0x99) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
```

### 1.4 Acknowledgements

Every function is acknowledged by the PC-Link with a short frame
`$05<func>` before (or instead of) any data reply. A function that only
changes state (link mode, set clock, memory-valid flag) is complete on the
`$05xx` acknowledgement alone; no data frame follows.

---

## 2. Function codes

| Func | Direction | Purpose                                   | Reply                                   |
|------|-----------|-------------------------------------------|-----------------------------------------|
| 0x10 | → module  | Read one 16-byte memory block             | `$2E` + addr + 16 data bytes            |
| 0x11 | → module  | Module status / identity                  | `$18` + 7-byte status (see §3)          |
| 0x12 | → module  | Get outputs 1–6                           | `$1C` state frame                       |
| 0x13 | → module  | Module memory CRC-16                       | `$18 FF` + addr + CRC (see §5)          |
| 0x14 | → module  | Write one 16-byte block (frame len 0x34)  | short ack `$0514` + `$0EFF` done        |
| 0x15 | → module  | Set outputs 1–6                           | short ack                               |
| 0x16 | → module  | Set outputs 7–12                           | short ack                               |
| 0x17 | → module  | Get outputs 7–12                           | `$1C` state frame                       |
| 0x18 | → module  | Link (programming) mode ON                | short ack `$0518`                       |
| 0x19 | → module  | Link mode OFF                             | short ack `$0519`                       |
| 0x1B | → module  | Set memory-valid (end of a full rewrite)  | short ack `$051B` + `$0EFF` done        |
| 0x1C | → module  | Set memory-invalid (start of a rewrite)   | short ack `$051C` + `$0EFF` done        |
| 0x1D | → PC-Link | Get clock                                 | `$1C FF` + addr + 6-byte date/time      |
| 0x1E | → PC-Link | Set clock                                 | short ack                               |
| 0x21 | → module  | Write one 8-byte block (frame len 0x24)   | short ack `$0521` + `$0EFF` done        |
| 0x22 | → module  | Read one 8-byte block (dimmer-class)      | `$1E` + addr + 8 data bytes             |
| 0x23 | → module  | Clear (erase) module memory               | short ack (long timeout, ~20 s)         |

Presence probe: `11 00 00` (a status query to the null address) returns the
PC-Link's own `$0511` acknowledgement only, and is used to confirm a live
link.

### 2.1 Answer-frame prefixes

The data reply of a query is matched on a per-function prefix, echoing the
address little-endian:

| Query func | Answer prefix        |
|------------|----------------------|
| 0x10       | `$2E` + addr         |
| 0x11       | `$18` + addr         |
| 0x13       | `$18FF` + addr       |
| 0x1D       | `$1CFF` + addr       |
| 0x22       | `$1E` + addr         |

Note that the clock reply (`$1CFF…`) shares the `$1C` frame code of an
output-state answer; a host must only treat a `$1C` frame as a clock reply
while it is actually waiting for one.

---

## 3. Module status (function 0x11)

Command payload: `11 <lo> <hi>`.

Reply payload (7 bytes):

```
b0 b1   address echo (little-endian)
b2      status flags   — bit 0 = EEPROM error
b3      type signature (family, see table)
b4      device-type code on output modules (same code as byte 4 of a
        registry record, §10.4); a varying state byte on the PC-Link
b5      record count A  (first link table)
b6      record count B  (second table; 0xFF = none)
```

Signatures observed on real hardware (status replies of a full vendor
read-out session plus direct queries):

| b3     | b4   | Module                                   | Example reply           |
|--------|------|------------------------------------------|-------------------------|
| `0x10` | `01` | Switch module 05-000-02 (12 ch)          | `$180747 00 10 01 1E FF`|
| `0x20` | `02` | Roller-shutter module 05-001-02          | `$180591 00 20 02 21 FF`|
| `0x30` | `03` | Dim controller 05-007-02                 | `$186C0E 00 30 03 1F 0A`|
| `0x90` | `09` | Compact switch module 05-002-02 (4 ch)   | `$18055B 00 90 09 13 FF`|
| `0x50` | var. | PC-Link 05-200                           | `$18F586 00 50 0B 3F FF`|
| `0xA0` | `01` | Feedback module 05-207                   | `$186C96 00 A0 01 3F FF`|
| `0x40` | —    | PC-Logic 05-201 (from the `#A` answer)   |                         |

The high nibble of `b3` is the product family; for output modules `b4`
equals the device-type code the PC-Link registry uses for the same
module, so a status reply alone identifies a module's family without
the registry. On the PC-Link `b4` changed between consecutive queries
(`0F` in the `#A` answer, `0B` then `08` one second apart), so it is a
state byte there, not a type. The broadcast identity answer
`$18 <addr> 00 50 0F 3F FF` has the same shape.

The dimmer's two counts (`1F 0A`) are its two link banks; every other
module reports `FF` for the second.

The record counts drive a bounded read: a host reads exactly `count`
records from the module's start offset rather than scanning blindly.

---

## 4. Memory reads and writes

- **16-byte block** — `10 <lo> <hi> <blk_lo> <blk_hi>`; reply carries 16
  data bytes after the 2-byte address echo. Block index = byte offset / 16.
- **8-byte block** — `22 …`; used by dimmer-class modules, reply carries 8
  data bytes. Block index = byte offset / 8.
- **Write** — `14`/`21` with the block index and the block data; a block
  whose data is entirely `0xFF` is not written (erased memory already reads
  `0xFF`).

### 4.1 Programming sequence (from a full vendor write capture)

A serial capture of the PC software **writing** an installation
(2026-06-02, 555 commands, all eight output modules plus the PC-Link's
own calendar block) gives the real sequence. It is not the simple
"clear then write" that was assumed.

**Per module, the shape is:**

1. `19` link mode OFF, `18` link mode ON, `23` **clear** — the erase is
   wrapped in a link-mode toggle (off, on, clear).
2. `19`, `18` again, then `11` status and `1C` **set memory-invalid**:
   the module is flagged invalid *before* its memory is touched.
3. Write the records in **short bursts**, each burst re-asserting `18`
   link mode first (the software re-enters link mode every ~10–19
   blocks rather than holding it — the same "module goes quiet, re-enter
   link mode" behaviour seen on reads, done here pre-emptively).
   - Each block is **read-modify-write**: a `10` / `22` read of the
     block precedes its `14` / `21` write, and the software writes back
     the whole 16- or 8-byte block with only the changed record altered
     (confirmed: unchanged blocks are written back byte-for-byte
     identical). Neighbouring records in a block are preserved.
4. `19` link OFF, `18` ON, `13` read the module CRC to verify, `19` OFF.

**At the very end of the whole session**, a single `1B` **set
memory-valid** is issued to the PC-Link (`86F5`) — the last command
before the `++++` / `ATH0` close.

So the valid/invalid pair is: `1C` invalidate at the *start* of a
module's rewrite, `1B` validate once *everything* has been written and
CRC-checked. A module interrupted between the two is left flagged
invalid.

**Write-frame layout** (confirmed from the capture):

```
16-byte block  $34 14 <lo><hi> <reg> 00 <16 data bytes> <crc16> <crc8>
 8-byte block  $24 21 <lo><hi> <reg> 00 <8 data bytes>  <crc16> <crc8>
```

The `$34` / `$24` length prefix is the total frame length in bytes, as
for every other frame; the sub-byte after the register is `00`. Reply
to a write is the short ack `$0514` / `$0521` followed by the module's
`$0EFF<addr>…` "operation done" frame. Clear (`23`), set-invalid
(`1C`) and set-valid (`1B`) are `$10`-length frames (`$10 <fn> <lo><hi>
<crc>`), each acknowledged the same way.

> This sequence is documented for completeness. This library does not
> implement it: it never enters link mode and never writes to a module
> (§11 warning). The clock write (`0x1E`) is the sole exception and does
> not use link mode.

---

## 5. Module memory CRC (function 0x13)

Command payload: `13 <lo> <hi> 00`. Reply payload:

```
FF b1 b2   FF + address echo
...
crc_lo crc_hi   CRC-16 the module computes over its own memory, little-endian
```

The coverage is per module family:

- **Switch / roller** — the whole image.
- **Dimmer** — both link banks but **skipping the six bytes between them**
  (`0x7FA..0x7FF`, a version/flags word): the CRC is computed over
  `0x000..0x7F9` and `0x800..0xFCF`. Validated against a real dimmer, where
  only this coverage reproduces the module-reported value.

---

## 6. Output-module memory layout

### 6.1 Switch and roller modules

- Image size `0x700` bytes, read in 16-byte blocks (`0x10`).
- `0x000..0x0FF` — 256-byte hash index.
- `0x100..` — 6-byte link records; count at `0x6FA`.

### 6.2 Dimmer modules

- Image size `0xFD0` bytes, read in 8-byte blocks (`0x22`).
- Bank 0 records from `0x100`, count at `0x7C8`.
- Configuration block `0x7CA..0x7F9`.
- Bank 1 records from `0x900`.
- The six bytes `0x7FA..0x7FF` sit between the banks and are excluded from
  the module CRC (§5).

Each dimmer link record carries a ramp/fade time (T2) alongside the mode
timer (T1); T2 is the low nibble of the record's third byte and maps to a
16-entry table from 1 s to 5 minutes.

---

## 7. Clock (functions 0x1D / 0x1E)

The PC-Link keeps a real-time clock used by calendar/timer programs.

- **Get** — `1D <lo> <hi>`. Reply data: `FF <lo> <hi> YY MM DD hh mm ss`,
  where `YY` is the year minus 2000.
- **Set** — `1E <lo> <hi> YY MM DD hh mm ss FF`; acknowledged only.

The clock is naive local time; it does not track daylight-saving changes on
its own.

---

## 8. Outputs and events

- **Get state** — `12` (channels 1–6) or `17` (channels 7–12); the module
  answers with a `$1C` state frame carrying the six channel bytes.
- **Set state** — `15 <lo> <hi> d1..d6 FF` / `16 <lo> <hi> d7..d12 FF`;
  roller-type channel values are masked to two bits.
- **Interface settings** — the ASCII commands `#L<n>` and `#E<n>` are
  sent to the interface itself, not to the bus. Their exact meaning is
  not established. What is established: the vendor software runs its
  whole read-out session with `#L0` + `#E0` and still receives every
  `$`-frame relayed from the bus (a feedback module's `$1012`/`$1017`
  queries and the `$1C` answers were relayed throughout); `#L1`, `#L2`
  and `#L3` relay exactly like `#L0`; and no value of `#L` makes the
  interface echo its own transmissions. A host that wants physical
  button presses (`#N…`) sends `#L0` + `#E1`, which is the only
  difference between the two set-ups, so `#E` most plausibly gates the
  relay of button events specifically. Treat that last sentence as a
  working hypothesis.
- **A gateway never relays its own transmissions.** Frames the interface
  itself puts on the bus — including a feedback module's own state
  queries when the host is attached to the feedback module's serial
  port — do not appear on the serial side; only what it receives from
  the bus does. See §8.2.

### 8.2 Attributing a `$1C` answer to an output group

A `$1C` state answer carries the module address and six output bytes and
nothing else; byte 7–8 is `00` for both groups. Which six outputs they are
is known only from the function code of the query that caused them
(`0x12` → outputs 1–6, `0x17` → 7–12). A host connected to a PC-Link sees
the feedback module's queries relayed and can pair them. A host connected
to the feedback module's own serial port sees only the answers (previous
point) and must infer: a module with six outputs or fewer only has group
1; for a twelve-output module an unattributed answer whose bytes equal
both stored halves changed nothing, and one that differs from either
should be resolved by the host sending its own `0x12` and `0x17`.

### 8.1 Button / key addresses

A physical key transmits a 24-bit address. Reversing its bit order gives
`module_address_22 << 2 | key_index`, where the key index is:

| Index | Key |
|-------|-----|
| 1     | A   |
| 3     | B   |
| 0     | C   |
| 2     | D   |

On 8-key plates the two rows (`1A..1D`, `2A..2D`) differ in the low address
bit. A press arrives on the bus as `#N<addr>`.

---

## 9. Discovery / inventory

Discovery is a two-stage process: first learn which modules exist, then
read each one's link table.

### 9.1 Stage 1 — inventory of addresses

The PC-Link keeps a registry of the modules it knows about. The registry
is read as a sequence of 16-byte records (function `0x10`, incrementing
block index). A registry block is prefixed by a header marker

```
<ver> 55 AA AA <count u32 LE>
```

with `ver` in the range `0x49..0x5E`, and `count` giving the number of
records that follow. `ver` outside that range, or a marker that does not
match, means the block is not a registry header.

Two record shapes appear in the registry sweep, both with bytes 1–3 always
`00 00 00` (the invariant used to reject counter-dump and partial-empty
noise the link emits at low register indices):

- **Module registry record** — metadata for a known module:

  ```
  <marker> 00 00 00 <type> 00 00 00 <addr_lo> <addr_hi> 00 00 <slot> 00 00 00
  ```

  `type` is the module's device-type code (see §11.3); `addr_lo addr_hi`
  is its 16-bit address. The `marker` byte has been seen as both `0x03`
  and `0x04` on different installs, so records are keyed on the stable
  shape — a Module device-type at byte 4 **and** a known address at bytes
  8–9 — not on the marker alone.

- **Link record** — a button→output routing entry (see §11):

  ```
  <chan> 00 00 00 <mode> 00 00 <flag> <p0> <p1> <p2> 00 <slot> 00 00 00
  ```

### 9.2 Stage 2 — per-module register scan

For each output module found in Stage 1, its link table is read. Two
strategies:

- **Count-driven (preferred).** Query the module status (`0x11`, §3) for
  its record counts, then read exactly that many records from the module's
  start offset. Switch/roller records begin at block `0x10`; dimmer bank 0
  at block `0x20`, bank 1 from a second sub-pass, plus the configuration
  blocks.
- **Fixed band (fallback).** A module that does not answer `0x11` is read
  over a fixed register range.

Either way the scan is bounded: a module signals the end of its table with
an all-`FF` trailer block, and a run of consecutive all-`FF` data registers
(three, to tolerate a mid-table gap) also ends the pass. An isolated empty
register is a gap, not the end.

---

## 10. Decoding link records

A link record ties a **button key** to an **output channel**, with a
**mode** and one or two **timers**. The records read during the Stage 2
scan are 16-byte register records; the meaningful fields sit at fixed
nibble offsets, and the low three bytes carry the button's bus address.

Bytes are numbered 0–7 as pairs of hex nibbles; `[n][0]` is the high
nibble of byte `n`, `[n][1]` the low nibble.

### 10.1 Switch and roller records

| Field       | Location   | Meaning                                        |
|-------------|------------|------------------------------------------------|
| key         | `[1][0]`   | key index on the plate (see §8.1)              |
| channel     | `[1][1]`   | output channel, **0-based** (add 1)            |
| T1          | `[2][0]`   | mode timer (meaning depends on mode)           |
| mode        | `[2][1]`   | link mode (M01…; per switch/roller mode table) |
| bus address | last 3 B   | 24-bit button address (recover key via §8.1)   |

Switch modes include M01 on/off, M02 on + operating time, M03 off +
operating time, M05 impulse, M06 delayed off. Roller modes include M01
open-stop-close, M02 open, M03 close, M05 interface/RF control, M07 close
with operating time. The T1 label depends on the mode (an operating time,
a delay, or "turned off").

### 10.2 Dimmer records

| Field       | Location   | Meaning                                        |
|-------------|------------|------------------------------------------------|
| T2 (ramp)   | `[2][1]`   | fade time, 16-entry table (1 s … 5 min)        |
| key         | `[3][0]`   | key index on the plate                         |
| channel     | `[3][1]`   | output channel, 0-based (add 1)                |
| T1          | `[4][0]`   | per-mode parameter (preset level, push time…)  |
| mode        | `[4][1]`   | dimmer link mode (M01, M04–M07, M12, …)        |
| bus address | last 3 B   | 24-bit button address                          |

The T1 parameter table is per mode: a preset level for M11/M12, a push
time for M05/M06, a delayed-off duration for M07, an on/off-step config
for M01–M03. T2 is the ramp/fade time common to every dimmer mode.

### 10.3 Recovering the button and key

The low three bytes of a record hold the key's 24-bit bus address. As in
§8.1, reversing the bit order yields `plate_address_22 << 2 | key_index`.
The key index also appears directly in the record's `key` nibble, and the
two agree. A plate's key address is derived from the plate address plus a
per-key nibble offset (by plate size: 1-, 2-, 4- or 8-key).

### 10.4 Device-type codes (byte 4 of a registry record)

| Code | Module                                   | Drives outputs |
|------|------------------------------------------|:--------------:|
| 0x01 | Switch module (12 ch)                    | yes            |
| 0x02 | Roller-shutter module (6 ch)             | yes            |
| 0x03 | Dimmer module (12 ch)                    | yes            |
| 0x09 | Compact switch module (4 ch)             | yes            |
| 0x31 | Compact switch module variant (4 ch)     | yes            |
| 0x32 | Compact dim controller (4 ch)            | yes            |
| 0x08 | PC-Logic                                 | no (controller)|
| 0x0A | PC-Link                                  | no (self)      |
| 0x2B | Audio distribution                       | no             |
| 0x37 | Modular interface (6 inputs)             | no (inputs)    |
| 0x42 | Feedback module                          | no (LEDs)      |

---

## 11. Feedback module (05-207)

The feedback module drives the LEDs on push-button plates. It answers the
status query (`0x11`) but serves its memory only in **link mode**: a block
read outside link mode is not acknowledged. The read sequence is therefore
link mode ON, read, link mode OFF, with a short settle after each
transition. The module can also fall silent partway through a long read;
leaving and re-entering link mode resumes it.

> **Warning — do not use link mode on this module for read-only access.**
> On one installation, several link-mode sessions (`0x18` … `0x19`) that
> contained only block reads, some of them interrupted and resumed by
> toggling link mode, ended with the module's entire memory reading as
> `0xFF` and the module no longer polling the bus or driving any LED. The
> status query still reported no EEPROM error, and `0x1B` (memory valid)
> did not restore it. No write, clear or memory-flag function had been
> sent. The exact mechanism is unknown; the only known recovery is to
> reprogram the module from the project file with the vendor software.
> Treat `0x18` on a 05-207 as the start of a full programming sequence
> (§4.1) and nothing else.

Memory image, `0x7900` bytes, read in 16-byte blocks:

| Offset   | Length   | Content                                                 |
|----------|----------|---------------------------------------------------------|
| `0x0000` | `0x4000` | input-event records, 8 bytes each, `FF`-terminated      |
| `0x4000` | `0x2000` | LED-slot → tracked-output lists (byte stream)           |
| `0x6000` | `0x0100` | tracked output modules, 8 bytes each                    |
| `0x6100` | `0x0100` | push-button module (plate) addresses, 3 bytes per group |
| `0x6200` | `0x1700` | touch-button records, tab names, LED modes              |

### 11.1 Decoding the image

1. **Tracked output modules** (`0x6000`, 8 bytes each, `FF`-terminated):

   ```
   [EEPROM type][addr hi][addr lo][first output index][mask hi][mask lo][FF][FF]
   ```

   Enumerate the set bits of `mask` (LSB first) as 1-based channels; the
   module's outputs occupy consecutive **output indices** starting at
   `first output index` (a running count over the preceding records). This
   builds `output_index → (module address, channel)`.

2. **Plate table** (`0x6100`, 3 bytes big-endian per group, `FFFFFF` =
   unused): each group `k` holds a plate's 22-bit address as
   `plate_address >> 2`. The table starts at offset 0 of the region on one
   software build and at offset `0x60` on another; detect which by finding
   the first base whose span is not all-`FF`.

3. **LED lists** (`0x4000`, byte stream): a run of items per LED slot,
   ended by a terminator:

   | Bytes                | Meaning                                             |
   |----------------------|-----------------------------------------------------|
   | `00 idx` / `01 idx`  | tracks output `idx` (01 = inverted)                 |
   | `02 idx a2 a1 a0`    | tracks output `idx`, with the link's 24-bit input   |
   | `04 slot` / `05 slot`| end of slot's list (05 = inverted polarity)         |
   | `08 FF`              | switch to the list-less tail (OFF/ON-only LEDs)     |

   `slot` is 0–255. Slots `8k..8k+7` belong to plate group `k`; the row
   within the group (`slot mod 8`) maps to the key in order A, B, C, D
   (and `1A..1D`, `2A..2D` on 8-key plates). Slots ≥ 192 are the feedback
   module's own LEDs.

4. **LED modes** (`0x7800`, one byte per slot 0–191): AUTO (0), AUTO-inv
   (1), DIRECT (2), DIRECT-inv (3), OFF (4), ON (5).

5. **Input-event records** (`0x0000`, 8 bytes each, `FF`-terminated):
   which key press changes which tracked output — the key's 24-bit address
   (bytes 0–2 plus two bits from byte 4), a link mode, parameters, the
   output index, and a level.

The LED-mode table and the input-event records are the least essential
regions; a module that stops serving them still yields a complete
LED-to-output map from the first three.

### 11.2 Plate placement note

The `0x6100` plate table starts at offset 0 of the region on one software
build and at offset `0x60` on another; a decoder should detect the base
rather than assume one.

---

## Appendix — worked frame example

Read block `0x600` of module `966C`:

```
payload = 10 6C 96 00 06        (func, addr LE, block index LE)
CRC16(payload) = A9E9
frame body = "$14" + "106C960006" + "A9E9"
CRC8("$14106C960006A9E9") = E5
frame = $14106C960006A9E9E5
```

The module answers `$2E6C96 <16 data bytes> <crc16> <crc8>`.

---

## Appendix B — Timing and session shape of the vendor software

From a serial capture of the PC software reading an installation
(3 min 36 s, 321 commands, no writes):

| Step | Observed |
|---|---|
| Modem preamble | `ATZ`, `++++`, `ATH0`, **10 s pause**, `++++`, `ATH0`, `ATZ` (Hayes escape guard time; a PC-Link on USB does not need it) |
| Null-address probe | `$1011 0000` 1.2 s after `ATZ`; answered in 24 ms with `$0511` **immediately followed by the PC-Link's own status frame** `$18 F586 00 50 0B 3F FF` |
| Inventory | `#A`, then `$1011 F586`, then the registry (§9.1) from register `A3`, i.e. after the three header registers |
| Reply latency | 15–25 ms after the last byte of a command, for every module type |
| Spacing, PC-Link registry reads | ~110 ms between consecutive `0x10` reads |
| Spacing, output-module link reads | 350–700 ms between consecutive `0x10` reads (switch / roller), ~500 ms for `0x22` (dimmer) |
| Per-module read plan | `$1011 <module>` first, then exactly ⌈count × 6 / 16⌉ blocks from `0x10` (switch / roller), or `count + 1` blocks per dimmer bank; the status query is repeated before the dimmer's second bank |
| Interface settings | `#L0` + `#E0`, sent twice during the session; bus frames kept being relayed |
| Session close | `++++`, `ATH0` |

Hosts driving the bus at 150 ms spacing (this library's default) have
not shown read errors on the same modules; the vendor's slower module
spacing is noted, not required.

The status frame that follows the null-address probe in the vendor's
sequence would let a host identify its gateway and learn the PC-Link
address at connect time, before any discovery. It has not been observed
when the probe is sent after the `#L`/`#E` settings; whether the 1.2 s
pause after `ATZ` or the ordering matters is untested.
