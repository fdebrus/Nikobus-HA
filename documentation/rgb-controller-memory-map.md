# 340-00112 / 340-00111 RGB controller: memory map from the vendor plugin

What the Nikobus PC software writes into an RGB / LED controller, taken
from its own plugin for this product family, `Niko_05_010.dll` in this
folder (version 21.0.0.2, "Written by www.dekimo.com", Niko Belgium
2001–2007), decompiled in September 2026 for
[Nikobus-HA #519](https://github.com/fdebrus/Nikobus-HA/issues/519).
The library carries it as `nikobus_connect.rgb_memory`.

## What the plugin is, and is not

The PC software loads one such DLL per product family. Its six exports
build and decode a module's memory image from the project database
(the `.nkb`'s Access tables `Component`, `Connection`, `LinkModeBase`,
`Objecten`, `ObjectBase`, `ParamBase`):

| Export | Role |
|---|---|
| `CalcMemoryMap` | builds one block of the image for a component |
| `GetDLLReadInfo` | tells the software the address, length and block size of each block, and how much of it to read back |
| `GetDLLReadWriteInfo` | two further ranges, meaning unknown |
| `TranslateUpload` | decodes an upload — dimmer-plugin code, see below |
| `GetPreReport` | the HTML report of a component's links |
| `FreeMemory` | frees a block |

**It contains no bus traffic.** No function code, no frame builder, no
programming-session or lock logic: all of that lives in the main
executable, which has not been analysed. So the DLL settles *what* is
written and *where in the module's memory*, not *how*.

Nothing in the DLL distinguishes the 340-00111 plinth light from the
340-00112 controller, or the colour variant from the mono one; the
strings `340-00111` / `340-00112` and the memory classes 11 / 12 do not
appear in it.

## The image: 7968 bytes in four blocks

| Block | Byte address | Length | Content | As 16-byte blocks (sub-byte, register)* |
|---|---|---|---|---|
| 0 | `0x0000` | 400 | LED calibration profile (from the software's `leddata.txt`, picked by the component's parameter 10004) | `00`: `0x00`–`0x18` |
| 1 | `0x0190` | 2304 | **Link table**: 128 slots × 18 bytes | `00`: `0x19`–`0xA8` |
| 2 | `0x0A90` | 5248 | Colour paths: 512 points × 10 bytes + 32 descriptors × 4 bytes | `00`: `0xA9`–`0xFF`, `01`: `0x00`–`0xF0` |
| 3 | `0x1F10` | 16 | Settings record | `01`: `0xF1` |

\* Assuming block index = byte address / 16 with the high byte as the
sub-byte, the way the library reads other modules. The controller has
answered no read at all, so this column is a hypothesis.

Every range the plugin hands back uses a **16-byte block size**, which
is the switch and roller modules' read size, not the dimmer's 8.

### What the software reads back

`GetDLLReadInfo` returns, per block, a read-back length: **0 for the LED
profile, 0 for the link table**, 5248 for the colour paths and 16 for the
settings. The link table is write-only as far as the vendor software is
concerned. That agrees with the bus capture of an installation read on a
real install (#519): every other module was memory-read, the controller
only status-polled.

The DLL's `TranslateUpload` decodes 8-byte records, stops at all-`0xFF`
and caps at 217 records — exactly the dimmer's bank size — and its other
mode decodes twelve dimmer outputs. It is dimmer-plugin code carried
over; it cannot decode the 18-byte records this plugin writes.

## The link record: 18 bytes, 128 slots, an empty slot all `0xFF`

| Byte | Content |
|---|---|
| 0–2 | Button address, 24-bit big-endian, in the software's form: the plate's project address shifted left by two with the key's 3-bit code in the low bits — the bit-reversal of the `#N` address the key puts on the bus (see *The bus side*). An explicit `PhysicalObjectAddress` wins; a group input keeps the low two bits and takes the group's address; link parameter 4, when nonzero, adds 4. |
| 3 | `mode << 3 \| output channel` (mode 0–31, channel 0–7) |
| 4–5 | Parameter 1, 16-bit BE. Overridden to 15 for modes 3, 7, 9, 10 and 14 and up. For modes 6 and 12 a value 0–15 is replaced by timer table T1. |
| 6–7 | Parameter 2, 16-bit BE; a value 0–15 (default 15) is replaced by timer table T2. |
| 8–11 | Parameter 3, 32-bit BE: the colour as CIE x and y, 16 bits each over 65535. A mode-3 link without a colour stores `50 0D 54 3A`, D65 white (0.3127, 0.3290). Otherwise `FF FF FF FF` when absent. |
| 12–13 | Parameter 5 as `((p5 >> 16) × 2) & 0xFFFF`; `FF FE` in the mode-3 default, `FF FF` when absent. |
| 14–15 | Parameter 7, 16-bit BE; `(p7 \| 0x20000) >> 2` when above `0x7FFF`; `00 00` in the mode-3 default, `FF FF` when absent. |
| 16 | Index (bits 0–6) of the link's colour path in block 2's table, bit 7 from bit 15 of parameter 6; `FF` when absent. |
| 17 | `FF` |

T1, seconds: 10, 60, 120, 180, 240, 300, 360, 420, 480, 540, 900, 1800, 2700, 3600, 5400, 7200.
T2, seconds: 1, 2, 4, 6, 8, 10, 15, 20, 30, 40, 50, 60, 120, 180, 240, 300.

### The one link we can check against

The validating install has a single link on its controller: plate
`124A36`, key 1C (code 0), mode M19, output 1, wire address `#N1B1492`.
The plugin writes for it:

```
49 28 D8  98  00 0F  01 2C  FF FF FF FF  FF FF  FF FF  FF  FF
```

`4928D8` is `124A36 << 2 | 0`, whose bit-reversal is `1B1492`; `98` is
M19 on channel 0, `000F` the forced parameter 1, `012C` = 300 s from
T2's default. Key 1D of the same plate has code 2, `4928DA`, and was
seen on the bus as `#N5B1492` — the same reversal.

## Block 2: colour paths

5120 bytes of points, 10 bytes each, big-endian words: `Xpos`, `Ypos`
(CIE xy over 65535), the cumulative `RelSpeed` up to the point,
`RelLumi`, `FFFF`. Then 32 descriptors of 4 bytes: start point (16-bit
BE), a flags byte (`0x01` jump points, `0x02` closed loop), the point
count. The points come from the software's `data\%06d_*.clr` INI files,
one per colour path used by a link; at most 512 points in all. The
`Color_list_14_colors` loop the validating install runs is one of these.

## Block 3: settings, 16 bytes

| Byte | Content |
|---|---|
| 0–1 | Component parameter 10001, 16-bit BE |
| 2–3 | Component parameter 10002, 16-bit BE |
| 4 | `FF` |
| 5 | parameter 10005 ≠ 0 |
| 6 | `00` |
| 7 | 1 when the address derived from parameter 10003 differs from the component's |
| 8 | `00` |
| 9 | parameter 10003's low two bits (0–3) |
| 10–15 | `FF` |

The software shows threshold, DMAX, DMIN, stand-alone / follows another
module and the LED profile for this record; which byte is which has not
been tied down.

## Block 0: LED profile

400 bytes serialised from `leddata.txt` (`ProfileCount`, per profile
`X1_coord` … `YW_coord`, `P%d_pwm_min/max/start`, `F%d_flux`,
`D%d_correction`, `Freq`) plus six 3-value entries per component. Not
decoded further; nothing in it is a link.

## The bus side, from nikobus.exe

`nikobus.exe` 4.3.1 (2010, Dekimo for Niko) is the main executable; it
builds every frame itself and hands it to `serial.dll`. Its programming
routine is logged step by step (`SERIAL:Setting the module in Link
Mode`, `SERIAL:Asking for EEPROM info`, …) and the decompile confirms
each step's frame. Frames are `func, addr_lo, addr_hi, args…` plus the
CRC-CCITT the library already computes.

| Function | Meaning | Args |
|---|---|---|
| `0x11` | module status: EEPROM-error flag, type, record counts | none |
| `0x10` / `0x22` | read a 16- / 8-byte block | block index, LE |
| `0x14` / `0x21` | **write** a 16- / 8-byte block | block index, LE, then the data |
| `0x13` | module CRC16 over its whole image | `00` |
| `0x18` / `0x19` | link (programming) mode on / off | none |
| `0x1B` / `0x1C` | memory valid / invalid (PC-Logic and PC-Link only) | none |
| `0x23` | clear EEPROM | none |

**Writing a module**, in the software's order: link mode on (skipped for
EEPROM types 4 and 5, the PC-Logic and PC-Link, which get memory-invalid
instead), clear EEPROM, then each block of the image whose 16 bytes are not all
`FF`, with block index = byte address / 16; a first or last block the
image only partly covers is read back first and merged. Then link mode
on again, CRC fetched and compared with the CRC16 of the image, link
mode off (PC-Logic and PC-Link: memory-valid and no CRC check; the audio
module skips the CRC check too). Between blocks the software drops and
re-enters link mode — **except for EEPROM types 10, 11 and 12** (wall
buttons, the 340-00111, the 340-00112), which stay in link mode for the
whole write.

**Reading a module** (the installation read): the upload routine
**skips EEPROM types 10, 11 and 12 before asking the module anything**.
Independently, the plugin's read-back lengths would have ended the read
loop at block 0. Both agree with the capture on a real install: every
other module memory-read, the controller only status-polled.

**EEPROMtype is the memory class.** The product table column is named
`EEPROMtype`; the software dispatches on it and excludes type 10
(buttons) from its module queries. Its own message table gives the
numbering: 1 switch, 9 compact switch, 2 roller, 3 dimmer, 8 compact
dimmer, 4 PC-Logic, 5 PC-Link, 6 feedback, 7 audio, 10 buttons,
**11 colour plinth light, 12 colour controller**, 13 sensor.

**The software has no direct colour command either.** Its simulation
mode drives switch and roller outputs with the set-output functions
(`0x15` / `0x16`, the frames the integration already uses), but for an
output of object type 10 (dimmer) or 34 (colour) it simulates by
pressing the linked key on the bus — a `#N` frame every 200 ms while
the mouse is held — exactly what the integration does. The `#N` frame
it builds is the 24-bit bit-reversal of `plate_address << 2 | key_code`,
which is also the address form a link record holds.

So the only read the vendor ever makes of this family is the CRC, and
it makes it inside link mode. The controller answering no block read
*outside* link mode (the forensic scans on #519) says nothing about
inside — and there is a precedent that it may answer there: the
feedback module (05-207) behaves the same way, status query answered,
every block read ignored, and nikobus-connect 0.36.1 found that it does
answer block reads once put in link mode, unreliably (it falls silent
mid-read). That read was removed in 0.37.0 on principle: link mode is
the mode that gates clearing and writing a module, and the library and
the integration send no programming function. The same rule applies
here. The controller's link table therefore stays unread by design, not
for lack of a path.

## Open questions

1. Whether the controller answers block reads inside link mode, as the
   feedback module does. A bench question with a likely yes; not for the
   integration, by the rule above.
2. The exact meaning of the settings bytes and the LED profile block.
   Nothing in either is a link.

For the integration the picture is closed: state comes from the state
query the controller answers, control goes through the keys the `.nkb`
names, and the vendor software does the same.
