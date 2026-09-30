# Nikobus PC software: what each plugin writes, and what the library reads

The Nikobus PC software (`nikobus.exe` 4.3.1, 2010) programs each product
family through a plugin DLL named in `product.mdb`'s `ProductBase.DLLName`.
Every plugin exports `GetDLLReadInfo`, which tells the software the byte
address, block size and length of each block of a module's image, and
`CalcMemoryMap`, which builds the block. Six plugins and the product
database were decompiled in September 2026 (Nikobus-HA #519 and after);
this is what they settle about the memory the integration reads. The
RGB family has its own document, `rgb-controller-memory-map.md`.

Addresses are byte addresses in the module's image. A 16-byte block index
is the byte address divided by 16; the library addresses a block by its
high byte as the sub-byte and its low byte as the register.

## Plugin to product

| Plugin | EEPROMtype | Products |
|---|---|---|
| `Niko_05_000_01.dll` | 1, 9, 2 | switch 05-000-02, compact switch 05-002-02, roller 05-001-02 |
| `Niko_05_007.dll` | 3, 8 | dim controller 05-007-02, compact dim controller 05-008-02 |
| `Niko_05_010.dll` | 11, 12 | plinth light 340-00111, colour controller 340-00112 (colour and mono profiles) |
| `Niko_05_100.dll` | 5 | PC-Link 05-200 |
| `Niko_05_200.dll` | 4 | PC-Logic 05-201 |
| `Niko_05_201a.dll` | 4 | SMS module 05-203 |
| `Niko_05_202.dll` | 7 | audio distribution 05-205 |
| `Niko_05_207.dll` | 10 | feedback module 05-207 |
| `Niko_05_207a.dll` | – | feedback-LED plates 05-060-02 / 064-02 / 078-02: a report only, no image |

Wall buttons, interfaces, remotes and sensors have no EEPROMtype and no
plugin: nothing is programmed into them.

## Switch and roller, `Niko_05_000_01`

One block: byte 0x100, 6-byte records, read back count-driven (the
status query's record count × 6), in 16-byte blocks. This is the
library's plan for both families.

Record, as the plugin composes it: `[addr 23:16] [addr 15:8]
[addr 7:2 << 2 | p4 bits 3:2] [T1 << 4 | link_id] [p4 bits 1:0 << 6 |
addr 1:0 << 4 | channel] [chain]`, where `addr` is `plate << 2 |
key_code` and `p4` the link's fourth parameter, which selects the key
half. The plugin also computes the byte sum of the three address bytes:
that is the hash the image's index at `0x000` is keyed by, and the
sixth byte is the index of the next record with the same hash, filled
when the table is built (not a check byte, as an earlier revision of
this page said). The library decodes the reversed block, so it sees
the chain byte first (its unused T2 nibble), then key and channel, then
T1 and mode, then the address — every field where the plugin puts it.
`link_id` is `product.mdb`'s `LinkIDNumber`: M01–M08 are 0–7, M11 8,
M12 9, **M13 10**, **M14 11, M15 12**. M13 is the sequencer: in the
database it is a link to the module's sequencer object rather than to
an output channel, and the plugin's upload decoder flags a record as a
sequencer exactly when its mode nibble is 10; what the channel nibble
of such a record designates has not been seen on a real module. The
library's switch table had 10 = M14 and 11 = M15; corrected in
nikobus-connect 0.43.0.

## Dimmer, `Niko_05_007`

| Block | Byte | Length | Content |
|---|---|---|---|
| 0 | 0x100 | count A × 8 | bank 0 link records, 8-byte blocks |
| 1 | 0x7CA | 48 fixed | per-channel configuration: 12 level bytes, 12 bytes of two nibbles each, 24 more |
| 2 | 0x900 | count B × 8 | bank 1 link records |

Exactly the library's plan: banks at 0x100 and 0x900, counts A and B
from the status query, 8-byte records, and the configuration block left
alone (the vendor reads 0x7CA–0x7FA; the library's note says 0x7C0).
Record: `[addr 23:16] [addr 15:8] [addr 7:0 | p3 bits] [T1 << 4 |
link_id] [p3 << 6 | key << 4 | channel] [T2 nibble] [..] [..]`, again
what the library decodes after reversal. The mode byte is the
`LinkIDNumber`; the compact dimmer adds M13 = 10 and M14 = 11, which the
library has.

## PC-Logic, `Niko_05_200`

| Block | Byte | Length | Content | Library plan |
|---|---|---|---|---|
| 0 | 99 | 385 fixed | logic programme | sub 00 0x06–0x3F covers 0x06–0x1E |
| 1, 2 | 998 | 2 + count × 6 | **input table**: the links whose output is the PC-Logic, `[addr 3] [input] [slot] [mode]` each — the address in the software's record form (`plate << 2 \| key_code`, link parameter 4 adding 4), then the link's input index written one less than the software counts it, a slot in a twelve-wide grid, and a mode byte; sorted; room for 1536 | block 0x3E, then to the last record (nikobus-connect 0.44.0: `PcLogicDecoder.extension_passes`) |
| 3 | 11000 | 640 fixed | the CF trigger-address grid the library recognises (`is_cf_address_table_chunk`) | sub 02 0xAF–0xEE covers 0xAF–0xD7 |
| 4 | 16000 | 192 fixed | | sub 03 0xE8–0xF4, exact |
| 5, 6 | 12000 | 2 + count × 5 | up to 64 `PhysicalObjectAddressOut` entries, one 24-bit address each | block 0x2ED–0x2EE only |

The fixed blocks are all inside the library's plan. Of the two
count-driven tables, the input table is now read in full and decoded —
the address as stored, as the plate address the library files buttons
under, and as the key's `#N` frame, with the input index, slot and mode
byte — and reported (`NikobusDiscovery.pc_logic_input_links`, one INFO
line per module). Bytes 3–5 are read from the plugin's composer alone,
no programmed table having been captured, so the records are not merged
into the button store until one install confirms them. The output list
at 12000 is still read to its first block only; nothing the integration
builds needs it.

## PC-Link, `Niko_05_100`

| Block | Byte | Length | Content | Library plan |
|---|---|---|---|---|
| 0 | 90 | 46 fixed | header; byte 0x28 a mode flag (0x78 / 0xF0 / 0) | sub 00 0x05–0x09 |
| 1 | 150 | 4 fixed | | sub 00 0x09 |
| 2, 3 | 995 | 2 + count × 8 | presence simulation: on and off events per address, minutes × 12 | sub 00 0x3E only |
| 4 | 5900 | 561 fixed | the 100 calendar channels, count at +0x230 | sub 01 0x70–0x93, exact |
| 5 | 6496 | 3 fixed | | sub 01 0x96 |
| 6 | 18000 | 65 fixed | | sub 04 0x65–0x69, exact |
| 7 | 6498 | 2 + count × 21 | calendar appointments, one 21-byte record each, count trailer at +0x2CEE | sub 01 0x96 only |
| 8 | 19000 | 13268 | **registry**: header `5E 55 AA AA` + count, then 16-byte records, one per component except the project location, ordered by location and component | sub 04 0xA0–0xFF, full sweep |

Block 8 is the one the plugin never reads back (`GetDLLReadInfo` returns
nothing for it); the library reads it anyway, and its full-range sweep
with the `5E55AAAA` header recognition matches how the vendor writes it.
The presence-simulation table and the appointments are the two things the
plan does not read past their first block; neither is needed for an
entity, since Home Assistant fires calendar channels by pressing them.

## Feedback module, `Niko_05_207`

| Block | Byte | Length |
|---|---|---|
| 0 | 0 | count × 8, up to 0x4000 |
| 1 | 0x4000 | 0x2000 fixed |
| 2 | 0x6000 | 0x100 |
| 3 | 0x6100 | 0x100 |
| 4 | 0x6200 | 0x1700 |

An image of 0x7900 bytes, 16-byte blocks. The module answers no block
read outside link mode and the library does not read it, by decision
(nikobus-connect 0.37.0). The layout is recorded for reading a capture.
The write path keeps EEPROMtype 10 in link mode for the whole write,
like the two colour products.

## Audio distribution, `Niko_05_202`

| Block | Byte | Length | Content | Library plan |
|---|---|---|---|---|
| 0 | 998 | 2 | link count | sub 00 0x3E |
| 1 | 1000 | count × 2 | an index table, entry n = n | |
| 2 | 5000 | count × 6, up to 11184 | **link records** | sub 01 0x38–0x5F |
| 3 | 4998 | 2 | link count again | sub 01 0x38 |
| 4 | 100 | 241 | settings; byte 240 a flag | sub 00 0x06 |

Byte 4998 is sub 01 register 0x38, offset 6: the two-byte little-endian
count the library looks for (the validating module's `23 00` is 35),
followed by the records, exactly as the validating module's dump has
it. The plugin's upload decoder takes a record only when its sixth byte
is 1, the address from bytes 0–2 as stored, the function from byte 3
and the object from the low nibble of byte 4 — the library's layout.
The plugin allows 1864 records; the library's fixed band holds about
105, and since nikobus-connect 0.44.0 the decoder takes the count from the head of the band and reads on to the last
record (`AudioDecoder.extension_passes`), where before it silently
stopped at the band's end.

## The serial layer, `serial.dll`

The executable hands `serial.dll` binary frames (function, address,
arguments, CRC-16) and the DLL wraps them for the PC-Link: a `$`
prefix, a length byte as two hex digits, the payload as hex, a CRC-8
over the frame text with polynomial 0x99, and a carriage return. That
CRC-8 is the library's `calc_crc2`, and the CRC-16 the executable
computes first, polynomial 0x1021 from 0xFFFF, is its `calc_crc1`. The
retries are the executable's, not the DLL's: up to five sends per
attempt, a wait of 50 ms × 5 × the caller's factor with a floor of one
second, and up to the caller's number of attempts with a growing pause
between them.

## What the database adds

`product.mdb` (`DatabaseVersion` 21008, the version the library's
parameter fixture came from) carries the bus device-type byte as the
primary key of its `ProductBase` table: the type a component is filed
under in the PC-Link registry is `KeyProductBase`. Checked over the
library's whole catalogue on 2026-09-30, 28 of 33 bytes name the
product row with that key, the five others being the library's own
aliases. (An earlier revision of this page said the database carried no
such byte, and that the 340-00112's mono variant was covered by 0x46;
both were wrong.) So the colour family is keys 69, 70 and 71: **0x45
the RGB plinth light 340-00111** (`S_DB_DIM_PLINT`, first reported the
same day at a 16-bit address), 0x46 the colour controller, **0x47 the
same controller in its mono profile** (`S_DB_DIM_MONOCTRL`). Both new
bytes are catalogued in nikobus-connect as modules, inventory only,
until a state reply from one is captured. The other products with no
catalogue entry now have a known byte too — the outdoor sensor
430-00502 is 0x48, the smoke detector 420-00005 0x49, the SMS module
05-203 0x2E, the RF plates 05-310 0x24 and 05-305 0x36, the RF boxes
05-315 0x38 and 0x3C, the remotes 05-313 0x3E and 05-081 0x27, the
modular interface 05-055 0x29, the old PIR 05-045 0x20 — and wait for a
real install to supply a channel count or a state reply. The table is
in nikobus-connect's `PROTOCOL.md` §11. `TypeInfo` is a UI class.
