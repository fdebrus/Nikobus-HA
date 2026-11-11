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
- **Outputs.** A relay is a `switch` (use *Show as* for a light), a dimmer
  output a `light` with brightness, a roller output a `cover` with a
  position estimated from the run times in the module's own links.
- **Keys.** One `event` entity per key with `pressed`, `hold`,
  `short_release` and `long_release`, one device per plate, and a bus event
  `nikobus_ng_press` for any address. A button per key presses it from Home
  Assistant; those are created disabled.
- **Scenes.** The central functions the link scan classifies become scenes,
  activated by their own telegram.
- **Motion detectors** get an occupancy and a light-level sensor.
- **Names** come from the `.nkb` project file, uploaded in the options.
- **Stays in step.** A press is followed by a read of what the key drives; a
  Feedback Module's reads are picked up; otherwise every group is read every
  two minutes. A command reads the module first only where the state may be
  behind (the library's steady groups).

## How it differs

| | Nikobus (this repository) | Nikobus NG |
|---|---|---|
| Python | 15 036 lines | 2 158 lines |
| Output state | buffer, plus an optimistic copy per entity | the buffer; entities are views |
| Structure | one coordinator of 3 600 lines | hub, discovery, presses, inventory |
| Discovery | two buttons, in order | one action, automatic on first setup |
| Keys | up to seven kinds of bus event, binary sensors, six classes | `event` entities, one bus event |
| Relay as light | options flow | Home Assistant's *Show as* |
| Unnamed outputs | no entity | an entity; disabled when no key drives it |
| Tests | 923, against a stub of Home Assistant | 44, against the real core and a simulated bus |

## What it does not do

Everything below exists in the Nikobus integration and is absent here.

| Feature | |
|---|---|
| Installations without a PC-Link (manual files) | not supported |
| LED keys: driving an output by pressing its key so plate LEDs follow | not supported |
| Audio distribution zones, RGB controller | not supported |
| PC-Logic and Modular Interface inputs as their own devices | plain keys only |
| Backup and verify of module programming, PC-Link clock | not supported |
| Fan entities for dimmer outputs | not supported |
| Shutter groups from central functions | skipped |
| Removing modules or plates that are gone (stale inventory) | not supported |
| Repairs, services (`send_button_press` and others) | none |
| Travel time and end-stop margin per shutter | from the links only |
| Migration from the Nikobus integration | none: new domain, new entities |

## What is verified

| Verified against the real core and the simulator | Not verified |
|---|---|
| Config flow, reconfigure, options, `.nkb` names | Commands and presses on real hardware |
| Setup, unload, removal, reconnection | A Feedback Module's pushed frames |
| Reading and writing all three module types | Timing on a serial line |
| The link pass of a discovery, its save and reload | Shutter run times from real link tables |
| Presses: events, reads, the write guard | Motion detectors on real hardware |
| Shutter position, scenes, motion sensors, diagnostics | |

On the one real installation: setup, the PC-Link inventory pass and the
link pass end to end, and the first read of every output group. That run
created no scene where the installation has light scenes: the scenes were
read after the library had cleared them, fixed in 0.1.1.

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
| `hub.py` | 400 | the connection, output state, commands, polling, reconnection |
| `inventory.py` | 272 | the store, and every question asked of it |
| `cover.py` | 224 | shutters and their position |
| `discovery.py` | 187 | one discovery run |
| `config_flow.py` | 180 | setup, reconfigure, options, `.nkb` upload |
| `presses.py` | 146 | keys, from first frame to release |
| `entity.py` | 119 | base classes and devices |
| the other platforms | 630 | switch, light, event, button, scene, sensor, binary sensor, diagnostics |
