# Nikobus NG

A second, independent Home Assistant integration for Nikobus, written from
scratch on top of the same protocol library
([nikobus-connect](https://github.com/fdebrus/nikobus-connect)). It is not a
new version of the Nikobus integration in this repository: it is a
clean-slate design, kept in its own folder under its own domain,
`nikobus_ng`, so the two can be compared.

**Status: experimental.** It is tested against the real Home Assistant
(2026.10.4) and against `nikobus-simulator`, the simulated PC-Link shipped
with the library, and has been set up on one real installation (a serial
PC-Link, six output modules, 44 plates, 2026-11-11): the discovery ran to
its end and every output was read. Commands, presses and shutters were not
observed there. See [What is verified](#what-is-verified).

The reasoning behind the design is in [REVIEW.md](REVIEW.md).

## What it does

- **Connects** to a PC-Link on a serial port or a TCP bridge, reconnects by
  itself, and reports the connection as a sensor.
- **Discovers the installation in one action.** The PC-Link inventory and
  every output module's link table are read one after the other, started by
  one button or by the first setup, with one progress sensor.
- **Outputs.** A relay is a `switch`, or a `light` when it is ticked in the
  options as switching a lamp; a dimmer output is a `light` with brightness,
  a roller output a `cover` with a position estimated from its run times:
  the ones the module's own links hold, or the ones set in the options. A
  roller output wired to a load can be shown as a `switch` or a `light`.
- **Keys.** One `event` entity per key with `pressed`, `hold`,
  `short_release` and `long_release`, one device per plate, and a bus event
  `nikobus_ng_press` for any address. A button per key presses it from Home
  Assistant; those are created disabled.
- **Scenes.** The central functions the link scan classifies become scenes,
  activated by their own telegram. A light scene on a wall key or an IR
  code is a scene only under the name the project file gives it; unnamed,
  it stays that key.
- **Motion detectors** get an occupancy and a light-level sensor.
- **Logical inputs** of a PC-Logic or a Modular Interface get a `switch`
  that is on after the input's closing frame and off after its opening
  frame, and sends them when it is turned on or off.
- **Names** of modules, plates, outputs and light scenes come from the
  `.nkb` project file, uploaded in the options; its rooms become the areas
  of the modules and plates that have none.
- **Maintenance**, read-only: how far the PC-Link's clock is from Home
  Assistant's, with a button that sets it (the one thing ever written); a
  check of every module's status and memory against its checksum; a backup
  of every module's memory to `nikobus_ng_backup/<date>` in the
  configuration directory.
- **Repairs.** A module that was reprogrammed since its links were read
  (its checksum moved) offers a new discovery; a module that stops
  answering while the others do offers its removal; a memory error, a
  checksum that does not match and a module of another type than the
  inventory says are reported with what to do.
- **Actions**: `nikobus_ng.press_key`, `read_module`,
  `verify_programming` and `backup_programming` (the last two return their
  result).
- **Deleting a device** of a module or a plate forgets it; a device the
  inventory no longer holds is removed at the next start.
- **A blueprint**, `blueprints/automation/fdebrus/nikobus_ng_key_controls_entity.yaml`:
  a key switches an entity of any integration.
- **Stays in step.** A press is followed by a read of what the key drives; a
  Feedback Module's reads are picked up; otherwise every group is read every
  two minutes. A command reads the module first only where the state may be
  behind (the library's steady groups).

## How it differs

| | Nikobus (this repository) | Nikobus NG |
|---|---|---|
| Python | 15 036 lines | 3 339 lines |
| Output state | buffer, plus an optimistic copy per entity | the buffer; entities are views |
| Structure | one coordinator of 3 600 lines | hub, discovery, presses, inventory |
| Discovery | two buttons, in order | one action, automatic on first setup |
| Keys | up to seven kinds of bus event, binary sensors, six classes | `event` entities, one bus event |
| Relay as light | module customisation, 17 flow steps | one tick list in the options |
| Unnamed outputs | no entity | an entity; disabled when no key drives it |
| Gone modules and plates | two actions: detect, then purge | a repair when a module stops answering; delete the device |
| PC-Link clock | a timestamp sensor that changes every hour | the drift in seconds, and a button |
| Settings | 17 options steps | a menu of four: gateway, lights, a roller output, names |
| Tests | 923, against a stub of Home Assistant | 69, against the real core and a simulated bus |

## What it does not do

Everything below exists in the Nikobus integration and is absent here.

| Feature | |
|---|---|
| Installations without a PC-Link (manual files) | not supported |
| LED keys: driving an output by pressing its key so plate LEDs follow | not supported |
| Audio distribution zones, RGB controller | not supported |
| Fan entities for dimmer outputs | not supported |
| Shutter groups from central functions | skipped |
| End-stop margin per shutter | one margin for all (3 s) |
| Scenes made in Home Assistant's options | use Home Assistant's own scenes |
| Migration from the Nikobus integration | none: new domain, new entities |

## What is verified

| Verified against the real core and the simulator | Not verified |
|---|---|
| Config flow, reconfigure, options, `.nkb` names | Commands and presses on real hardware |
| Setup, unload, removal, reconnection | A Feedback Module's pushed frames |
| Reading and writing all three module types | Timing on a serial line |
| The link pass of a discovery, its save and reload | Shutter run times from real link tables |
| Presses: events, reads, the write guard | Motion detectors on real hardware |
| Shutter position, scenes, motion sensors, diagnostics | Logical inputs on real hardware |
| Logical inputs, actions, repairs, device removal | The PC-Link clock, read or set |
| Module status and memory read; the backup files | A module's checksum (the simulator has none) |
| The blueprint, in a real automation | |

On the one real installation: setup, the PC-Link inventory pass and the
link pass end to end, and the first read of every output group. That run
created no scene where the installation has light scenes: the scenes were
read after the library had cleared them, fixed in 0.1.1. With 0.2.0 the
same installation got its three light scenes back under the names of its
project file.

## Trying it

Only one program may talk to the bus. **Disable the Nikobus entry first.**

1. Copy `custom_components/nikobus_ng` into your Home Assistant
   `config/custom_components` folder and restart.
2. *Settings → Devices & services → Add integration → Nikobus NG*, and give
   the serial port or `host:port`.
3. The first setup starts a discovery. Follow it on the *Discovery* sensor
   of the *Nikobus* device. Entities appear when it ends.

It uses its own domain and its own store, so it changes nothing of the
Nikobus integration, and removing its entry removes everything it created.
It never writes to a module's programming.

## Tests

```bash
pip install -r requirements_test.txt     # Python 3.14, Home Assistant 2026.10
python -m pytest -q                      # from this folder
```

The fixtures start a simulated installation on a local socket, seed the
store the way an inventory leaves it, and set the entry up in a real Home
Assistant. Nothing of Home Assistant or of the library is replaced.

## Layout

| File | Lines | |
|---|---|---|
| `hub.py` | 463 | the connection, output state, commands, polling, reconnection |
| `maintenance.py` | 415 | the clock, the programming checks, the backup |
| `config_flow.py` | 357 | setup, reconfigure, the options menu, `.nkb` upload |
| `inventory.py` | 339 | the store, and every question asked of it |
| `cover.py` | 229 | shutters and their position |
| `discovery.py` | 192 | one discovery run |
| `entity.py` | 184 | base classes, devices, which platform an output belongs to |
| `presses.py` | 146 | keys, from first frame to release |
| the rest | 1 014 | the platforms, repairs, actions, diagnostics |
