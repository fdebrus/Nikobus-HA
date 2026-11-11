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
  one button or by the first setup, with one progress sensor. The
  `nikobus_ng.discover` action can read the links of one module only, which
  is what a reprogrammed module needs.
- **Outputs.** A relay is a `switch`, or a `light` when it is ticked in the
  options as switching a lamp; a dimmer output is a `light` with brightness,
  or a `fan` with a speed when ticked as driving one;
  a roller output a `cover` with a position estimated from its run times:
  the ones the module's own links hold, or the ones set in the options,
  with the end margin. A roller output wired to a load can be shown as a
  `switch` or a `light`.
- **LED keys.** An output given a key in the options is switched by
  pressing that key, so the plate's LED follows. The module is read first
  and the key left alone when the output is already there.
- **Shutter groups.** A central function that only moves shutters is a
  `cover` that moves its members, each keeping its own position.
- **Audio zones.** Each zone an audio distribution module holds is a
  `media_player`: on, off, volume steps, source.
- **Colour controllers.** A `light` that shows on or off and the colour,
  switched by the keys the project file links to it.
- **Without a PC-Link.** The `.nkb` project file can stand in for the
  PC-Link's inventory: its modules and plates are taken, and the links are
  then read from the modules. Without a project file either, the two
  hand-written files of the Nikobus integration can be uploaded instead;
  what they say of an output becomes the matching option here.
- **A module of the wrong type.** The options can say what a module is
  when the inventory has it as another type; its entities change and its
  links are read again by themselves. The choice stays through later
  discoveries. When a check finds a module answering as another output
  module, the repair offers that type.
- **An old PC-Link** (before generation 3), as an option: nothing is read
  before a write and nothing periodically, and what Home Assistant wrote
  is kept across restarts.
- **A scene activated at the wall** records the activation on its entity,
  whichever of its keys was pressed.
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
  (its checksum moved) offers to read its links again; a module that stops
  answering while the others do offers its removal; a port on which
  nothing answers, a link table that could not be read, a memory error, a
  checksum that does not match and a module of another type than the
  inventory says are reported with what to do; a cable on the Feedback
  Module's own port offers to turn the Feedback Module option off.
- **Actions**: `nikobus_ng.discover`, `press_key`, `read_module`,
  `verify_programming` and `backup_programming` (the last two return their
  result).
- **Every output names what drives it**, in its `controlled_by`
  attribute: the keys, and the PC-Link calendar channels.
- **Deleting a device** of a module or a plate leaves it out from then
  on, through later discoveries too: a PC-Link goes on listing what was
  once in its project. The discover action with its address brings it
  back. A device the inventory no longer holds is removed at the next
  start. A module that stays silent is asked once an hour only, since
  each read of it is a timeout on the bus.
- **For a language model.** What Home Assistant cannot show of an
  installation is its wiring. The `nikobus_ng.describe` action returns it
  as data: every output with the keys that drive it, every key with what
  it drives, the scenes with their outputs. Each link is said in words
  (`does`: "Switches the output on at once, and off again after 2 min.")
  and carries its values under what they are (`off_after`, `on_after`,
  `hold_time`, `run_time`, `level`, `dim_time`) instead of the vendor's
  T1 and T2, whose meaning changes with the module and the mode. The
  wording is the library's, taken from the vendor's manuals.
  The same is offered to Home Assistant's conversation agents as an LLM
  API, "Nikobus NG", with six tools: an overview, one output, one key,
  the scenes, the health of the bus, and pressing a key. Five only read;
  none touches a module's programming. Home Assistant's MCP server passes
  them on.
- **Taking over from the Nikobus integration.** An options step moves
  the entity ids, shown names, areas, aliases, labels, icons and
  voice-assistant exposure of the Nikobus integration's lights, switches,
  covers, fans, scenes and input switches onto this integration's
  entities, and makes relays lights where they were. The Nikobus entities
  are parked, not deleted, and "Give back" undoes it. Keys are not taken
  over: they are other kinds of entity here.
- **Names as in the Nikobus integration.** A module or a plate the
  project file names is shown as `Name (Room)`, a plate with the number
  the vendor software shows it under in front: `11: Boudoir (Hall)`. The
  gateway's device is *Nikobus Bridge*. A light scene and a shutter group
  each have a device of their own, so they are shown under their own
  name and nothing in front of it.
- **A blueprint**, `blueprints/automation/fdebrus/nikobus_ng_key_controls_entity.yaml`:
  a key switches an entity of any integration.
- **Stays in step.** A press is followed by a read of what the key drives; a
  Feedback Module's reads are picked up; otherwise every group is read every
  two minutes. A command reads the module first only where the state may be
  behind (the library's steady groups).

## How it differs

| | Nikobus (this repository) | Nikobus NG |
|---|---|---|
| Python | 15 036 lines | 5 499 lines |
| Output state | buffer, plus an optimistic copy per entity | the buffer; entities are views |
| Structure | one coordinator of 3 600 lines | hub, discovery, presses, inventory |
| Discovery | two buttons, in order | one action, automatic on first setup |
| Keys | up to seven kinds of bus event, binary sensors, six classes | `event` entities, one bus event |
| Relay as light, dimmer as fan | module customisation, 17 flow steps | two tick lists in the options |
| Unnamed outputs | no entity | an entity; disabled when no key drives it |
| Gone modules and plates | two actions: detect, then purge | a repair when a module stops answering; delete the device |
| PC-Link clock | a timestamp sensor that changes every hour | the drift in seconds, and a button |
| Settings | 17 options steps | a menu: gateway, lights and fans, a roller output, LED keys, a module's type, names, files |
| Tests | 923, against a stub of Home Assistant | 125, against the real core and a simulated bus |

## What it does not do

What exists in the Nikobus integration and is absent here.

| Feature | |
|---|---|
| Scenes made in Home Assistant's options | use Home Assistant's own scenes |
| Choosing what a project file import takes, and which kinds of key get entities | everything is taken; entities are disabled one by one |
| A refresh interval to set | fixed |
| A device per PC-Link calendar channel | named in `controlled_by` only |
| Volume level and brightness of a colour controller | the bus offers neither |
| A migration of key entities and of automations on Nikobus bus events | outputs, scenes and input switches are taken over; the rest is by hand |

## What is verified

| Verified against the real core and the simulator | Not verified |
|---|---|
| Config flow, reconfigure, options, `.nkb` names | Switching an output and moving a shutter on real hardware |
| Setup, unload, removal, reconnection | A Feedback Module's pushed frames |
| Reading and writing all three module types | Timing on a serial line |
| The link pass of a discovery, its save and reload | Shutter run times from real link tables |
| Presses: events, reads, the write guard | Motion detectors on real hardware |
| Shutter position, scenes, motion sensors, diagnostics | Logical inputs on real hardware |
| Logical inputs, actions, repairs, device removal | The hour between two tries of a silent module, on real hardware |
| Module status and memory read; the backup files | A module's checksum (the simulator has none) |
| The blueprint, in a real automation | LED keys with a real Feedback Module |
| One module read again; a port nothing answers on; states pushed without their queries | A link table that does not line up (the library's flag is set by the test) |
| A module given another type, from the options and from the repair | A PC-Link older than generation 3: what it answers is taken from the Nikobus integration's handling of it |
| Two hand-written files as the inventory; press repeats; calendar channels; a scene at the wall | |
| The description and the LLM tools, called as Home Assistant calls them | The tools used by a real conversation agent |
| A take-over and its undo, on registry entries made like the Nikobus integration's | A take-over of a real installation |
| Fans, LED keys, shutter groups, a project file as inventory | An audio module and a colour controller, real ones |
| Audio zones, read out of the simulated module by the link scan | The link scan through a gateway that is not a PC-Link |
| A colour controller's state and keys (a stand-in answers its state query) | |

On the one real installation: setup, the PC-Link inventory pass and the
link pass end to end, and the first read of every output group. That run
created no scene where the installation has light scenes: the scenes were
read after the library had cleared them, fixed in 0.1.1. With 0.2.0 the
same installation got its three light scenes back under the names of its
project file, and with 0.3.0 its six logical inputs and the clock of its
PC-Link, an hour ahead. With 0.6.0 its PC-Link turned out to list a
colour controller that is not on the bus: every read of it was a
fifteen-second timeout, and with a Feedback Module it was never counted
as silent, fixed in 0.6.1. With 0.7.0 (2026-11-11): a light scene
activated from Home Assistant put its key on the bus and the dimmer
output it holds went to its preset, 4,0 V in the link and 0x66 of 0xFF
read back; the button that sets the PC-Link's clock took it from 47
minutes ahead to one second; the description returned 52 outputs, 154
keys, 4 scenes and 6 logical inputs. The silent colour controller was
asked at two polls and the entry was disabled before the third, so the
hour's pause was not seen.

## Trying it

Only one program may talk to the bus. **Disable the Nikobus entry first.**

1. Copy `custom_components/nikobus_ng` into your Home Assistant
   `config/custom_components` folder and restart.
2. *Settings → Devices & services → Add integration → Nikobus NG*, and give
   the serial port or `host:port`.
3. The first setup starts a discovery. Follow it on the *Discovery* sensor
   of the *Nikobus Bridge* device. Entities appear when it ends.

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
| `hub.py` | 661 | the connection, output state, commands, LED keys, polling, reconnection |
| `config_flow.py` | 612 | setup, reconfigure, the options menu, the uploads |
| `inventory.py` | 602 | the store, and every question asked of it |
| `maintenance.py` | 416 | the clock, the programming checks, the backup |
| `cover.py` | 364 | shutters, their position, shutter groups |
| `discovery.py` | 241 | one discovery run, of everything or of one module |
| `describe.py` | 246 | the installation as data, for a person or a model |
| `takeover.py` | 200 | taking over the Nikobus integration's entities, and giving them back |
| the rest | 2 157 | the platforms, presses, repairs, actions, the LLM tools, the hand-written files, diagnostics |
