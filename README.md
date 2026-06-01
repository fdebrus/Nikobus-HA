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

Control your **Nikobus** installation from Home Assistant — switches, dimmers, and shutters as native entities, plus button presses as automation triggers. Modules and buttons are discovered automatically from the bus; there are no address lists to maintain by hand.

---

## Contents

- [Quick start](#quick-start)
- [Prerequisites](#prerequisites)
- [Supported hardware](#supported-hardware)
- [Installation & setup](#installation--setup)
- [Discovery workflow](#discovery-workflow)
- [Buttons, inputs & the entity model](#buttons-inputs--the-entity-model)
- [Events & automations](#events--automations)
- [Scenes](#scenes)
- [Connectivity](#connectivity)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [How the integration works](#how-the-integration-works)
- [Issues, license & trademark](#issues-discussion--legal)

---

## Quick start

1. **Install** via HACS (custom repository) and restart Home Assistant.
2. **Add the integration**: *Settings → Devices & Services → Add Integration → Nikobus*. Enter your PC-Link connection — a serial path (`/dev/ttyUSB0`) or TCP bridge (`192.168.2.50:9999`).
3. Open the **Nikobus Bridge** device and press **Discover modules & buttons** — this enumerates the bus via the PC-Link.
4. Press **Scan all module links** — this maps which button drives which output, and extracts scenes.
5. Done. Your modules appear as lights / switches / covers; buttons appear as devices you can trigger automations from.

> A large install takes a few minutes to scan. Progress is shown on the Bridge device's **Discovery status** / **Discovery progress** sensors.

---

## Prerequisites

- **A PC-Link (05-200) on the bus.** Home Assistant connects *through* the PC-Link — it is the required interface and the source of the device inventory. (See [Troubleshooting](#troubleshooting) if your install also has a PC-Logic.)
- **One client at a time.** Only one program may talk to the bus. Stop the Nikobus PC software (and any other bridge) before starting Home Assistant.
- **A connection path**: a USB/serial adapter (`/dev/ttyUSB0`) or a TCP-to-serial bridge (`host:port`).
- **(Optional) a Feedback Module (05-207)** wired to the PC-Link. If present, enable the toggle during setup and module states are pushed in real time; without one, HA polls on a configurable interval.

---

## Supported hardware

### Output modules

| Module | Ref | In Home Assistant |
|---|---|---|
| Switch Module | `05-000-02` | `switch` (or `light`) per channel |
| Compact Switch Module | `05-002-02` | `switch` / `light` |
| Dimmer Module | `05-007-02` | `light` with brightness |
| Compact Dim Controller | `05-008-02` | `light` |
| Roller Shutter Module | `05-001-02` | `cover` with simulated position |

### Interface / system modules

| Module | Ref | Notes |
|---|---|---|
| PC-Link | `05-200` | Required bus interface; all traffic goes through it |
| PC-Logic | `05-201` | Logic controller; its 6 inputs surface as `LM-INPUT 1–6` |
| Modular Interface, 6 inputs | `05-206` | Its 6 inputs surface as `MI-INPUT 1–6` |
| Feedback Module | `05-207` | Optional; pushes real-time state when wired to the PC-Link |
| Audio Distribution Module | `05-205` | Registered for visibility; I/O mapping not yet decoded |

### Buttons & transmitters

One HA **device** is created per physical button, with one button-entity + binary-sensor per key (see [the entity model](#buttons-inputs--the-entity-model)).

- **Bus push buttons** — 2-, 4-, and 8-control-point variants across the supported series (`4*-072` / `4*-074` / `4*-078`, the `05-06x` graphite/feedback-LED variants, and the `4*-082` / `4*-084` / `4*-088` series).
- **Push-button & switch interfaces** (`05-056`, `05-057`, `05-058`).
- **IR receivers** — each learned IR code becomes its own op-point.
- **Motion detectors** with Nikobus interface (`05-7*5`).
- **RF transmitters** — `05-302` / `05-304` / `05-311` / `05-314`, and the **05-312 Easywave 52-key** hand-held (all 52 sub-codes as op-points).
- **Virtual / clustered remotes** — multi-page handhelds that emit many bus codes but aren't in PC-Link inventory are clustered into a single `Remote Transmitter (<suffix>)` device at the end of a link scan.

---

## Installation & setup

1. Install through HACS using the button below (or clone into `config/custom_components/nikobus`):

   [![Add to HACS](https://img.shields.io/badge/HACS-Add%20Custom%20Repository-blue.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fdebrus&repository=Nikobus-HA&category=integration)

2. Restart Home Assistant.
3. *Settings → Devices & Services → Add Integration → Nikobus*.
4. Enter the serial path or `IP:port`. The connection is tested immediately.
5. On **Hardware Configuration**, enable a toggle if it applies:
   - **Feedback Module (05-207) installed** — real-time pushed state, no polling.
   - **PC-Link older than Gen 3** — compatibility tweaks for 1st/2nd-gen PC-Link hardware.
6. If neither toggle is set, choose a **polling interval** (60–3600 s, default 120).
7. Finish, then run [discovery](#discovery-workflow).

Module and button data live in Home Assistant's own storage (`.storage/nikobus.modules`, `.storage/nikobus.buttons`, `.storage/nikobus.cfs`). You don't hand-edit these — they're populated by discovery. (Scenes are the one optional JSON file; see [Scenes](#scenes).)

---

## Discovery workflow

Everything is driven from the **Nikobus Bridge** device page.

### 1. Discover modules & buttons

Press **Discover modules & buttons**. The integration probes the PC-Link, walks its inventory, and creates a device for every module and every physical button on the bus.

Two diagnostic sensors track progress:
- **Discovery status** — live per-register message (with a coarse `phase` attribute for automations).
- **Discovery progress** — 0–100 %.

### 2. Scan all module links

Press **Scan all module links**. This reads each output module's link table and records which button drives which channel (the `linked_modules` metadata). It also extracts **Central Function (CF)** scenes found in those tables. The button is disabled until an inventory has run.

### 3. Customize a module *(optional)*

*Configure → Customize a module* lets you, per channel:
- **Description** → the entity name.
- **Entity type** → how HA exposes it (switch modules: `switch`/`light`/`none`; dimmers: `light`/`none`; rollers: `cover`/`switch`/`light`/`none`).
- **LED on / off addresses** → feedback-LED bus addresses (blank if unused).
- **Travel time up / down** (rollers) → seconds to open/close, used by the position calculator.

Changes persist in `.storage/nikobus.modules` and survive re-discovery.

### Installs without a PC-Link

If no PC-Link answers the discovery probe, the integration falls back to importing inventory from optional `nikobus_module_config.json` / `nikobus_button_config.json` files in `/config` (Feedback-module-only installs). These files are **only** consulted by the *Discover modules* action as a fallback — they are never imported automatically at startup, and never overwrite a PC-Link-discovered inventory. If you keep them, their descriptions are also overlaid onto discovered devices as friendly names.

---

## Buttons, inputs & the entity model

### The model: device → op-points → (button + sensor)

Every physical button is one **device**. Each *operation point* on it — a key on a keypad, an IR code on a receiver, an A/B state on an input — gets **two entities**:

| Entity | Direction | Use |
|---|---|---|
| **Button** | HA → bus | Press it to emit the same bus frame a physical press would (simulate the press). |
| **Binary sensor** *(disabled by default)* | bus → HA | Turns `on` briefly when the real button is pressed; use it in state-based automations. |

> Binary sensors are **disabled by default** — enable them on the device page if you want to monitor presses.

### PC-Logic & Modular Interface inputs

The PC-Logic (`05-201`) and Modular Interface (`05-206`) each expose **6 inputs**. Each input is rendered as its own child device under the owning module:

- PC-Logic inputs → **`LM-INPUT 1`…`LM-INPUT 6`** (LM = Logic Module).
- Modular Interface inputs → **`MI-INPUT 1`…`MI-INPUT 6`** (MI = Modular Interface).

Each input has two keys — **A** and **B** — because the firmware emits one telegram when the contact closes (A) and another when it releases (B). Each key has the usual button + binary-sensor pair:

- Use the **A / B buttons** to simulate the input going on/off *from* HA.
- Use the **A / B sensors** to monitor the input's real state on the bus.

Input addresses are computed by firmware from the module's own address (not stored in inventory) and synthesised automatically — no configuration needed.

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.lm_input_1_key_a
    to: "on"
```

### What each button exposes

After a link scan, button entities carry their wiring as attributes:

```yaml
linked_outputs:
  - module_address: "0E6C"
    channel: 1
    mode: "M01 (Dim on/off (2 buttons))"
wall_button_address: "0D1C80"
wall_button_model: "05-348"
wall_button_type: "IR Button with 4 Operation Points"
wall_button_key: "1C"
```

Conversely, every light / switch / cover exposes a **`controlled_by`** attribute listing the buttons that drive it — so you can answer "which wall button turns on this light?" from the entity page.

### Renaming

Discovered devices get generic names like `Bus push button, 4 control buttons (1843B4)`. Rename them in the HA UI (*device → ⋮ → Rename*); HA stores this as `name_by_user` and **preserves it across reloads, restarts, and re-discovery** (names are keyed by the entity's stable `unique_id`).

### IR codes across multiple receivers

When the same IR code is learned by several receivers, each gets its own op-point with a distinct bus address, and the name is qualified with the receiver (`IR 30A on 0D1C80`) so duplicates stay distinguishable.

### Virtual / off-bus buttons

Addresses not present on the physical bus (IR scene triggers, hand-added codes) aren't created as entities. Fire them from scripts:

```yaml
service: nikobus.send_button_press
data:
  address: "84DFFC"
```

---

## Events & automations

The integration fires structured HA bus events for the full press lifecycle:

- **Base**: `nikobus_button_pressed`, `nikobus_button_released`.
- **Classified**: `nikobus_short_button_pressed` (< 1 s), `nikobus_long_button_pressed` (≥ 1 s). The threshold is `SHORT_PRESS` in `const.py`.
- **Release buckets**: `nikobus_button_pressed_0` (< 1 s) … `_3` (≥ 3 s).
- **Hold milestones** (while still held): `nikobus_button_timer_1` / `_2` / `_3` at 1/2/3 s. These count wire frames (40 ms cadence), not wall-clock, so a bridge that buffers and bursts frames still classifies correctly.
- **Post-refresh**: `nikobus_button_operation` after impacted modules are refreshed.

All events share one payload schema:

```yaml
address: "004E2C"        # button address (uppercase hex, no 0x)
module_address: "9105"   # impacted module, or null
channel: 1
ts: "2024-05-01T12:00:00Z"
press_id: "004E2C-..."
state: "pressed" | "released" | "timer"
duration_s: 1.2          # null on initial press
bucket: 1                # 0/1/2/3, else null
threshold_s: 2           # timer milestone, else null
source: "nikobus"
```

> **Always quote button addresses in YAML.** A value like `25E952` parses as scientific notation and becomes `null`. Use a leading space inside the quotes (`address: " 25E952"`). Addresses with any letter other than `E` are unaffected.

### Examples

**Toggle a light on a press**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "004E2C"
action:
  - service: homeassistant.toggle
    target:
      entity_id: light.example_light
```

**Hold-to-dim (fires after 2 s held)**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_timer_2
    event_data:
      address: "004E2C"
action:
  - service: light.turn_on
    target:
      entity_id: light.living_room_dimmer
    data:
      brightness_pct: 30
      transition: 1
```

**Drive a scene from a physical button (after sunset)**

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: " 25E952"   # leading space avoids YAML scientific-notation
condition:
  - condition: sun
    after: sunset
action:
  - service: scene.turn_on
    target:
      entity_id: scene.scene_close_all_shutters
```

**Move a cover to an exact position** (the virtual travel calculator stops it at the target):

```yaml
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "C9A5"
action:
  - service: cover.set_cover_position
    target:
      entity_id: cover.living_room_blind
    data:
      position: 60
```

---

## Scenes

Two independent sources produce scene entities. Most installs only need the first.

### CF broadcasts (auto-extracted)

A Nikobus **Central Function (CF)** is a multi-output activation that already lives on the bus — every "scene" key on a keypad is a CF. **Scan all module links** reads them from the module link tables, classifies them (`switch_pair` / `roller_pair`), and persists them to `.storage/nikobus.cfs`. They appear as scene entities automatically, e.g.:

- `Nikobus switch CF 384102 (22 ch)`
- `Nikobus roller CF 3880CB (6 ch)`

Metadata is exposed as attributes:

```yaml
bus_address: "3880CB"
pattern: "roller_pair"
member_count: 6
outputs:
  - { module_address: "8B9C", channel: 5, mode: "M02 (Open)" }
```

**Activating a CF from HA is indistinguishable from a physical press** — the integration emits the same `#N<address>\r#E1` frame, so modules fire atomically on the bus (no per-channel fan-out, no partial-activation risk). Roller travel uses each module's stored M01/M02/M03 timings. No configuration needed; CFs are re-extracted on every scan.

> CF scenes get generic names (`Nikobus switch CF …`). The friendly names from your Nikobus "Groups" are not imported — rename the scene entities in HA if you want.

### User-authored scenes (`nikobus_scene_config.json`)

For HA-side per-channel groupings that **don't** exist as a CF on the bus. Loaded from `/config` at startup; a missing/empty file is fine. Dimmers/shutters use 0–255, switches `"on"`/`"off"`, shutters `"open"`/`"close"`.

```json
{
  "scene": [
    {
      "id": "scene_close_all_shutters",
      "description": "Close all shutters",
      "channels": [
        {"module_id": "9105", "channel": "1", "state": "close"},
        {"module_id": "9105", "channel": "2", "state": "close"}
      ]
    }
  ]
}
```

Activation sends one command per channel (HA-driven fan-out), touching only the channels you list.

**Which one?** A scene that already exists in Nikobus → it's a CF, no config. A new cross-module grouping → write it in the JSON file.

---

## Connectivity

**Only one client may connect to the bus at a time.**

- Direct serial/USB: `/dev/ttyUSB0`
- Network bridge: `192.168.2.50:9999`

On a dropped connection the integration reconnects with exponential back-off (5 s → 10 s → 20 s → … capped at 60 s). Entities go unavailable until the link is restored, then resume without an HA restart.

![TCP bridge example 1](https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1)
![TCP bridge example 2](https://github.com/fdebrus/Nikobus-HA/assets/33791533/9c0b11ad-0a1c-4728-ab5e-5e68be6452a8)

---

## Troubleshooting

### Discovery says "No PC-Link" although I have one (PC-Logic installs)

If your install has a **PC-Logic (05-201) configured as master**, the PC-Logic answers the bus address-inquiry, not the PC-Link. The integration (like Niko's own software) reads the device inventory **only from a PC-Link**. Make sure Home Assistant's connection terminates at the **PC-Link**, then re-run discovery. A PC-Logic that answers instead is reported as "connect to a PC-Link module" — the same message the Nikobus PC software shows.

### Buttons missing, or a button shows the wrong number of control points

Run **Discover modules & buttons** followed by **Scan all module links** while connected to the PC-Link. If a specific physical button type isn't recognised at all, its device-type byte may be uncatalogued — open an issue with the diagnostics download and the model number printed on the button.

### Entities show as `Manual button`, or some entities are missing after an upgrade

This is **stale data** left in storage from an older release (e.g. an install that previously fell back to manual config). Symptoms: entries typed `Manual button`, and HA logging *"Platform nikobus does not generate unique IDs … already exists"* (duplicate addresses cause real entities to be dropped).

To clear it:

1. **Stop Home Assistant completely** (not just reload — `.storage` is rewritten on shutdown, so deleting while running won't stick).
2. Delete `.storage/nikobus.buttons` and `.storage/nikobus.modules`.
   *(Keep any `nikobus_*_config.json` files if you rely on them for descriptions.)*
3. Start HA, then run **Discover modules & buttons** → **Scan all module links**.

The store rebuilds cleanly with proper device types and no duplicates. Names you set in the HA UI are keyed by `unique_id` and reattach automatically.

### Custom channel/button names didn't carry over

Friendly names you set in Home Assistant live in HA's entity/device registry (keyed by `unique_id`), not in the integration's store — so a clean re-discovery preserves them as long as the bus addresses are unchanged. Descriptions authored only in a legacy `nikobus_button_config.json` are overlaid where the discovered op-point addresses match.

### State is slow to update

Without a Feedback Module, external changes (manual relay actuation, another client) are only seen on the next poll. Physical button presses still trigger immediate targeted refreshes. Add a 05-207 Feedback Module for real-time updates, or lower the polling interval.

### Services for advanced cleanup

- `nikobus.detect_stale_inventory` — probes modules for bus presence and returns which are absent.
- `nikobus.purge_stale_inventory` — removes the given module addresses from storage.
- `nikobus.query_module_inventory` — low-level register read (diagnostics).
- `nikobus.send_button_press` — emit a button frame for an off-bus / virtual address.

---

## Known limitations

- **Quote button addresses in YAML** (scientific-notation trap — see [Events](#events--automations)).
- **No bus-level auto-discovery.** The serial path / `host:port` is entered manually; the PC-Link doesn't advertise over mDNS/SSDP/USB.
- **One client per bus.** Stop any other Nikobus software before starting HA.
- **Polling latency without a Feedback Module** (60–3600 s; presses still refresh immediately).
- **One config entry per HA instance.** Two separate Nikobus buses can't share one Home Assistant.
- **Inventory comes from the PC-Link.** A PC-Logic cannot serve the device inventory (see [Troubleshooting](#troubleshooting)).

---

## How the integration works

The code is split into two packages.

**[nikobus-connect](https://github.com/fdebrus/nikobus-connect)** — pip-installed, pinned in `manifest.json`. The low-level layer:

- `NikobusConnect` — serial/TCP transport + PC-Link handshake.
- `NikobusEventListener` — parses CR-terminated ASCII frames; dispatches presses and feedback.
- `NikobusCommandHandler` — queued, retrying command processor that throttles bursts.
- `NikobusAPI` — high-level operations (read/set output state, cover start/stop).
- `NikobusDiscovery` — PC-Link inventory + module register scan; reverse-engineers button→output mappings and classifies CF broadcasts.

**This integration (`custom_components/nikobus/`)** — the Home Assistant glue:

- `coordinator.py` — wires the library together; owns polling, discovery lifecycle, and state signals.
- `nkbstorage.py` — the three HA Stores (`nikobus.modules`, `nikobus.buttons`, `nikobus.cfs`).
- `nkbmanual.py` — optional fallback import of `nikobus_*_config.json` for no-PC-Link installs, plus the friendly-name overlay.
- `nkbactuator.py` — turns incoming button frames into HA events with debounce + duration tracking.
- `nkbconfig.py` — scene-file loader/writer.
- `nkbtravelcalculator.py` — virtual cover-position tracking.
- `router.py` — maps module channels to HA entity types; builds the `controlled_by` reverse index.
- `config_flow.py` — config flow (connection → hardware → polling) and the Configure options menu.
- `repairs.py` — the "No Nikobus buttons configured" repair flow.
- `diagnostics.py` — the diagnostics download for bug reports.
- `entity.py` + the platforms (`light` / `switch` / `cover` / `button` / `binary_sensor` / `sensor` / `scene`).

### Staying in sync

1. **Button-driven refresh** — each button carries its `linked_modules`; a press immediately refreshes the impacted module group(s).
2. **Periodic refresh** — the polling interval, or the Feedback Module's push when present.

### Interoperability

The integration talks to Nikobus hardware over its serial bus. It was developed independently, solely for interoperability between Home Assistant and Nikobus hardware the user already owns, in line with Article 6 of Directive 2009/24/EC.

---

## Issues, discussion & legal

**Questions / support:** the [Home Assistant community thread](https://community.home-assistant.io/t/custom-component-nikobus/732832).

**Trademark:** Nikobus is a trademark of Niko NV. This project is an independent community effort, not affiliated with, endorsed by, or sponsored by Niko NV.

**License:** provided for personal and non-commercial use. You may view, copy, modify, and share the code and documentation for non-commercial purposes. Commercial use requires prior written permission from the maintainers. Provided "as is" without warranties of any kind.
