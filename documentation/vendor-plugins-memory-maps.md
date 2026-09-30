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

Record, as written: `[addr 23:16] [addr 15:8] [addr 7:0 & ~3 | p4 bits]
[T1 << 4 | link_id] [p4 << 6 | key << 4 | channel] [check]`. The
library decodes the reversed block, so it sees the check byte first
(its unused T2 nibble), then key and channel, then T1 and mode, then the
address — every field where the plugin puts it. `link_id` is
`product.mdb`'s `LinkIDNumber`: M01–M08 are 0–7, M11 8, M12 9, **M13 10**
(sequencer, which the plugin's own upload decoder flags), **M14 11, M15
12**. The library's switch table had 10 = M14 and 11 = M15; corrected in
nikobus-connect PR #160, pending a real scene link to confirm.

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
| 1, 2 | 998 | 2 + count × 6 | links whose output is the PC-Logic, one 24-bit address each, `IsFiltered = NO` | block 0x3E only: four records at most |
| 3 | 11000 | 640 fixed | | sub 02 0xAF–0xEE covers 0xAF–0xD7 |
| 4 | 16000 | 192 fixed | | sub 03 0xE8–0xF4, exact |
| 5, 6 | 12000 | 2 + count × 5 | up to 64 `PhysicalObjectAddressOut` entries, one 24-bit address each | block 0x2ED–0x2EE only |

The fixed blocks are all inside the library's plan. The two count-driven
tables are the PC-Logic's own link lists; the plan reads their first
block only. Nothing the integration builds today needs them.

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

## What the database adds

`product.mdb` (`DatabaseVersion` 21008, the version the library's
parameter fixture came from) carries no bus device-type byte; its
`TypeInfo` is a UI class. The catalogue's gaps can only be filled by
observing a module. Products in the database with no catalogue entry:
the SMS module 05-203, the remotes 05-081 and 05-085, the RF plates
05-305 (410-00003), 05-310 and 05-313, the RF boxes 05-315, the modular
interface 05-055, the old PIR 05-045, the outdoor sensor 430-00502 and
the smoke detector 420-00005, and the plinth light 340-00111. The
340-00112's mono variant is the same product row family and is covered
by device type 0x46.
