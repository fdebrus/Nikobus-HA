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
| 0–2 | Button address, 24-bit big-endian. The plugin computes it from the project database: the plate's physical address plus 4 × the key object's address on the plate (an explicit `PhysicalObjectAddress` wins; PC-Link / PC-Logic virtual channels use `((ObjectAddress + 0x60000) << 5) + PhysicalAddress`; a group input keeps the low two bits and takes the group's address). Parameter 4 of the link, when nonzero, adds 4. |
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
`124A36`, key 1C (key index 0), mode M19, output 1, wire address
`#N1B1492`. The plugin writes for it:

```
12 4A 36  98  00 0F  01 2C  FF FF FF FF  FF FF  FF FF  FF  FF
```

`98` is M19 on channel 0, `000F` the forced parameter 1, `012C` = 300 s
from T2's default. The address bytes are the least certain part: the
plugin stores the plate's *physical* address `124A36`, where the switch,
roller and dimmer modules store the bit-reversed *wire* form (`1B1492`).
Whether the controller reverses it itself has not been observed. A
serial capture of the software programming the controller would settle
it: the write frames should carry either `124A3698000F012C` or
`1B149298000F012C`, and their function code and address bytes would
give the rest.

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

## Open questions, in order of value

1. The function code and frame the software writes the image with, and
   whether it opens a programming session first. Only a serial capture of
   the software programming a controller answers this.
2. Whether the module addresses its memory in 16-byte blocks at these
   byte addresses (register `0x19` for the first link record).
3. Whether the address bytes are the physical or the wire form.
