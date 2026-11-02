# Nikobus Integration for Home Assistant

<p align="left">
  <a href="https://www.buymeacoffee.com/fdebrus"><img src="https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00?style=flat&logo=buymeacoffee" alt="Buy Me a Coffee"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-Nikobus-blue?style=flat&logo=homeassistant" alt="Nikobus for Home Assistant">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-orange?style=flat" alt="HACS Custom"></a>
  <a href="https://github.com/fdebrus/Nikobus-HA"><img src="https://img.shields.io/badge/Maintained%20by-fdebrus-green?style=flat" alt="Maintainer"></a>
  <a href="https://github.com/fdebrus/Nikobus-HA/releases"><img src="https://img.shields.io/github/v/release/fdebrus/Nikobus-HA?style=flat&label=Release" alt="Latest release"></a>
  <a href="https://github.com/fdebrus/Nikobus-HA/issues"><img src="https://img.shields.io/github/issues/fdebrus/Nikobus-HA?style=flat&label=Issues" alt="Open issues"></a>
  <a href="https://github.com/fdebrus/Nikobus-HA/stargazers"><img src="https://img.shields.io/github/stars/fdebrus/Nikobus-HA?style=flat&label=Stars" alt="GitHub stars"></a>
</p>

Control your **Nikobus** installation from Home Assistant — switches, dimmers, and shutters as native entities, plus button presses as automation triggers. Modules and buttons are **discovered automatically from the bus**, and your real names, rooms, and scenes can be **imported from your `.nkb` project file**. There are no address lists to maintain by hand.

### Highlights

- 🔌 **Automatic discovery** — modules and physical buttons are enumerated straight from the PC-Link; no manual address tables.
- 🔊 **Audio zones** — an Audio Distribution module (05-205) becomes one media player per zone, with on/off, volume steps and source selection; its trigger keys, Power included, are press buttons under the module.
- 🌈 **RGB controller** — a 340-00112 becomes a light with a colour readout, switched through the keys your `.nkb` links to it, since the bus offers no direct command.
- 💡 **Native entities** — switches, dimmers (with brightness), shutters (with simulated position) and, for a dimmer output that drives a variable-speed fan, a `fan` with a speed slider — one entity per channel, type chosen per channel.
- 🎛️ **Buttons as triggers, and as actuators** — every keypad key, IR code, and input becomes an event source for automations, and a press-simulation button whose press the bus cannot tell from a real one.
- 🧹 **Only the press entities you want** — press buttons and press sensors come in six classes (wall buttons, interfaces, remotes and IR codes, input modules, audio triggers, virtual buttons); tick the ones you use and the rest get no entities and no devices. Automations keep working either way: the `nikobus_button_pressed` events fire for every key.
- 📥 **Import from `.nkb`** — upload your Nikobus project export and pull in device names (numbered like in the Nikobus software), **per-channel names**, **Areas** (from rooms), and **scenes** — pick exactly what to apply. Imported names persist across restarts and re-discovery.
- 🎬 **Scenes that fire atomically** — Central Function scenes are activated on the bus the same way a physical scene key would, with no per-channel fan-out.
- ⚡ **Pushed or polled** — every press is followed by a read of the modules it drives; on top of that, a Feedback Module's own polling is picked up for free, or a configurable poll runs without one.
- 🛡️ **Backup, verify & watch your modules' programming** — read every module's memory image into a backup folder, check each module's status, family and checksum, and get a Repair issue when a module is reprogrammed behind Home Assistant's back. Read-only on the bus: nothing is ever written to a module.
- 🩺 **Says what is wrong** — a wrong port, a serial cable on the Feedback Module instead of the PC-Link, a module stored as the wrong type, a corrupt link table: each is a Repair issue that names the cause and the fix, and clears itself once fixed.
- 🕒 **PC-Link clock** — see the controller's clock and its drift, and set it from Home Assistant (the only write the integration performs).

---

## Quick start

1. **Install** via HACS (custom repository) and restart Home Assistant.
2. **Add the integration**: *Settings → Devices & Services → Add Integration → Nikobus*. Enter your PC-Link connection — a serial path (`/dev/ttyUSB0`) or TCP bridge (`192.168.2.50:9999`).
3. Open the **Nikobus Bridge** device and press **1. Load Project Overview** — this enumerates the bus via the PC-Link.
4. Press **2. Load Existing Installation** — this maps which button drives which output, and extracts scenes.
5. *(Optional)* [Upload your `.nkb`](docs/nkb-import.md) and press **3. Import Names from .nkb** to pull in your real names, rooms, and scenes.
6. Press **Backup module programming** once and keep the folder somewhere safe — it is the only copy of your modules' programming outside the modules themselves. See [Maintenance](docs/maintenance.md).
7. Done. Your modules appear as lights / switches / covers; buttons appear as devices you can trigger automations from.

> A large install takes a few minutes to scan. Progress is shown on the Bridge device's **Discovery status** / **Discovery progress** sensors.

---

## Prerequisites

- **A serial gateway to the bus.** Normally a **PC-Link (05-200)**: it is the source of the device inventory that discovery reads, it relays everything on the bus, and it is the only port on which pushed state from a Feedback Module can be used. Installations that connect through a **Feedback Module (05-207)** or a **PC-Logic (05-201)** serial port work too, with polling and inventory from the fallback files — see [Which port is Home Assistant connected to?](docs/connectivity.md#which-port-is-home-assistant-connected-to).
- **One client at a time.** Only one program may talk to the bus. Stop the Nikobus PC software (and any other bridge) before starting Home Assistant.
- **A connection path**: a USB/serial adapter (`/dev/ttyUSB0`) or a TCP-to-serial bridge (`host:port`).
- **(Optional) a Feedback Module (05-207)** wired to the PC-Link. If present **and Home Assistant is connected to the PC-Link's port**, enable the toggle during setup and module states are pushed in real time; without one, HA polls on a configurable interval. If Home Assistant is connected to the Feedback Module's own serial port, leave the toggle off (see [Installs without a PC-Link](docs/manual-config.md)).
- **(Optional) your `.nkb` project file** — the export from the Nikobus PC software. Not required to run, but it's the only place your friendly names, rooms, and scene groupings live. See [Importing from your `.nkb` project](docs/nkb-import.md).

---

## Supported hardware

### Output modules

| Module | Ref | In Home Assistant |
|---|---|---|
| Switch Module | `05-000-02` | `switch` (or `light`) per channel |
| Compact Switch Module | `05-002-02` | `switch` / `light` |
| Dimmer Module | `05-007-02` | `light` with brightness, or `fan` with speed (per channel) |
| Compact Dim Controller | `05-008-02` | `light` |
| Roller Shutter Module | `05-001-02` | `cover` with simulated position |

### Interface / system modules

| Module | Ref | Notes |
|---|---|---|
| PC-Link | `05-200` | The bus interface; source of the inventory, relays all bus traffic, keeps the clock and the 100 calendar channels |
| PC-Logic | `05-201` | Logic controller; its 6 inputs surface as `LM-INPUT 1–6`; usable as gateway (polling only, no inventory) |
| Modular Interface, 6 inputs | `05-206` | Its 6 inputs surface as `MI-INPUT 1–6` |
| Feedback Module | `05-207` | Optional; drives plate LEDs, and its own polling of the modules is picked up as pushed state — through the PC-Link's port. Usable as gateway (polling only) |
| Audio Distribution Module | `05-205` | One `media_player` per zone: on/off, volume, source select; its trigger keys as press buttons under the module |
| RGB / LED controller | `340-00112` | One `light`: on/off and the colour its outputs mix to, switched through the keys linked to it (from your `.nkb`); see [RGB controller](docs/buttons-and-entities.md#rgb-controller) |

### Buttons & transmitters

One HA **device** is created per physical button, with one button-entity + binary-sensor per key (see [the entity model](docs/buttons-and-entities.md)).

- **Bus push buttons** — 2-, 4-, and 8-control-point variants across the supported series (`4*-072` / `4*-074` / `4*-078`, the classic `05-060` / `05-064` and the feedback-LED `05-061` (recognised since 3.11.0), and the `4*-082` / `4*-084` / `4*-088` series).
- **Push-button & switch interfaces** (`05-056`, `05-057`, `05-058`).
- **IR receivers** — each learned IR code becomes its own op-point.
- **Motion detectors** with Nikobus interface (`05-7*5` / `430-00500`) — an occupancy and a light-level sensor each, plus their four keys ([details](docs/buttons-and-entities.md#motion-detectors)).
- **RF transmitters** — `05-302` / `05-304` / `05-311` / `05-314`, and the **05-312 Easywave 52-key** hand-held (all 52 sub-codes as op-points).
- **Virtual / clustered remotes** — multi-page handhelds that emit many bus codes but aren't in PC-Link inventory are clustered into a single `Remote Transmitter (<suffix>)` device at the end of a link scan.

---

## Installation & setup

**Requires Home Assistant 2026.8.0 or later** (releases since 3.15.5 use the device registry's `via_device_id`, introduced in 2026.8; HACS refuses the installation on an older core).

1. Install through HACS using the button below (or clone into `config/custom_components/nikobus`):

   [![Add to HACS](https://img.shields.io/badge/HACS-Add%20Custom%20Repository-blue.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fdebrus&repository=Nikobus-HA&category=integration)

2. Restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → Nikobus*.
4. Enter the serial path or `IP:port`. The connection is tested immediately.
5. On **Hardware Configuration**, enable a toggle if it applies:
   - **Feedback Module (05-207) installed, Home Assistant connected to the PC-Link's port** — the integration picks up the answers to the Feedback Module's own polling (one read about every 33 seconds, walking its own list; on a ten-module installation a group with LED keys came up every 12 to 30 minutes) instead of polling itself, and only reads a group the module has not reported for 5 minutes. A press is still followed by an immediate read; a change without a press (a timer, PC-Logic, another client) is seen when the Feedback Module next polls that module, which can take minutes. Leave it off when the serial port is the Feedback Module's own: that port does not relay the module's queries, so pushed state cannot be attributed to an output group and the integration raises a Repair issue asking you to turn it off.
   - **PC-Link older than Gen 3** — disables state polling (early PC-Links don't answer module state queries reliably), leaving push-only updates. **Leave this off unless you need it**: it has no effect on discovery, but with polling off Home Assistant only learns an output's state from what it sees happen, so these stay stale until the next press on a key linked to that module:
     - a timed output switching itself off (*Off + operating time*, *Delayed off*);
     - an output switched by a PC-Logic program, a central function or the PC-Link's calendar;
     - the state after a power cut.

     If your presses update the entities, your PC-Link answers state queries and you don't need the option: Home Assistant reads the modules with the same query after every press. If the log shows `Failed to receive ACK and state for command '$1012…'` with the option off, turn it on.
6. **Simulated press repeats** (1–10, default 3): how many times a press sent by Home Assistant is repeated on the bus, back to back in one write. A real key repeats its telegram while held and modules act on a telegram seen at least twice; leave the default unless a press is reliably ignored.
7. If neither toggle is set, choose a **polling interval** (60–3600 s, default 120).
8. Finish, then run [discovery](docs/discovery.md).
9. A fresh installation creates **no press buttons or press sensors**: lights, covers and switches do not need them, and automations trigger on events. If you want them for wall keys, remotes or inputs, tick their classes under *Configure → Choose which press buttons and press sensors exist* (see [Choosing which press entities exist](docs/buttons-and-entities.md#choosing-which-press-entities-exist)).

All of this can be changed later under *Configure* (*Change hardware settings*, *Choose which press buttons and press sensors exist*); the integration reloads with the new values. To change the connection itself — another serial port, a TCP bridge — use **Reconfigure** on the integration's entry. It works while the integration runs: an unchanged connection is kept as it is; a new one is tested after the integration has released the port, and if the test fails the old connection is set up again.

Module and button data live in Home Assistant's own storage (`.storage/nikobus.modules`, `.storage/nikobus.buttons`, `.storage/nikobus.cfs`). You don't hand-edit these — they're populated by discovery.

---

## Documentation

The guides live in [`docs/`](docs/README.md):

- [Discovery workflow](docs/discovery.md) — the Bridge device's buttons and sensors, the three discovery steps, and *Customize a module*.
- [Installs without a PC-Link](docs/manual-config.md) — the two JSON files that replace the PC-Link inventory, field by field.
- [Maintenance: backup, verify & clock](docs/maintenance.md) — backing up and verifying the modules' programming, the reprogramming watch, the PC-Link clock.
- [Importing from your `.nkb` project](docs/nkb-import.md) — uploading the project file and importing names, rooms and scenes from it.
- [Buttons, inputs & the entity model](docs/buttons-and-entities.md) — devices and entities per key, press-entity classes, inputs, renaming, audio zones, motion detectors, the RGB controller.
- [Commands on the bus](docs/commands-on-the-bus.md) — switching an output directly or by pressing its key, and what each does to the plate LEDs.
- [Events & automations](docs/events.md) — the `nikobus_*` events, their payload, and automation examples.
- [Scenes](docs/scenes.md) — central-function scenes from the bus, named scenes from the `.nkb`, and your own scene file.
- [Connectivity](docs/connectivity.md) — serial and TCP connections, reconnects, the presence probe, and which port Home Assistant should be on.
- [Services](docs/services.md) — the eight `nikobus.*` actions with their fields.
- [Troubleshooting](docs/troubleshooting.md) — every Repair issue, and the symptoms users report most, with their fix.
- [How the integration works](docs/how-it-works.md) — the library and the integration's modules, how state stays in sync, interoperability.

Protocol notes, the vendor manuals and the code audits are in [`documentation/`](documentation/).

---

## Known limitations

- **Quote button addresses in YAML** (scientific-notation trap — see [Events](docs/events.md)).
- **No bus-level auto-discovery.** The serial path / `host:port` is entered manually; the PC-Link doesn't advertise over mDNS/SSDP/USB.
- **One client per bus.** Stop any other Nikobus software before starting HA.
- **Polling latency without a Feedback Module** (60–3600 s; presses still refresh immediately).
- **One config entry per HA instance.** Two separate Nikobus buses can't share one Home Assistant.
- **Inventory comes from the PC-Link.** A PC-Logic cannot serve the device inventory (see [Troubleshooting](docs/troubleshooting.md)).
- **Friendly names, rooms, and named scenes come from the `.nkb`.** The bus carries wiring, not labels — without the project file, devices keep their generic discovered names.
- **Read-only by design.** The integration never writes to a module's programming: no restore from backup, no Feedback Module memory read (both need the module's programming mode). The PC-Link clock sync is the only write.
- **Pushed state needs the PC-Link's port.** On a Feedback Module's or PC-Logic's serial port the integration polls (see [Connectivity](docs/connectivity.md)).
- **Calendar programs stay in the PC-Link.** Calendar channels that drive an output are shown in `controlled_by`, but the programs behind them are neither read nor fired.
- **A press sent by Home Assistant is invisible to itself.** The PC-Link does not relay the host's own telegram, so no `nikobus_button_*` event is fired for it; the impacted modules are read instead.
- **The RGB controller's colour and brightness cannot be set.** The bus has no command for it; the light switches by pressing its linked keys, which your `.nkb` must supply (see [RGB controller](docs/buttons-and-entities.md#rgb-controller)).

---

## Issues, discussion & legal

**Questions / support:** the [Home Assistant community thread](https://community.home-assistant.io/t/custom-component-nikobus/732832).

**Trademark:** Nikobus is a trademark of Niko NV. This project is an independent community effort, not affiliated with, endorsed by, or sponsored by Niko NV.

**License:** [PolyForm Noncommercial License 1.0.0](LICENSE) — you may use, copy, modify, and share the software for any **noncommercial** purpose (personal use, education, research, charities and the like). **Any commercial use requires a separate license from the maintainers** — get in touch. Provided "as is" without warranties of any kind (see the license for the exact terms).
