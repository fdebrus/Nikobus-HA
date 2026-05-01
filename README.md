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

This custom integration connects Home Assistant to your Nikobus installation so you can control switches, dimmers, shutters, and respond to button presses directly from Home Assistant.

## Prerequisites

Before installing:

- Home Assistant connects to a **PC-Link (05-200)** — that's the required bus interface. Module addresses are discovered automatically once the integration is connected, so there's no need to collect them up front.
- Ensure only **one** client is connected to the Nikobus bus at any time (do not run the Nikobus PC software in parallel).
- Decide how HA will reach the PC-Link: directly over a USB/serial adapter (e.g. `/dev/ttyUSB0`) or via a TCP bridge (e.g. `192.168.2.50:9999`).
- If you have a **Feedback Module (05-207)** wired to the PC-Link, note it: you'll enable a toggle during setup so state changes are pushed in real time instead of polled. Without one, HA polls the bus on a configurable interval.

## Supported Modules and Features

- **Switch Module** `05-000-02`, **Compact Switch Module** `05-002-02`
  - On/off switching.
- **Dimmer Module** `05-007-02`
  - On/off and brightness setting.
- **Shutter Module** `05-001-02`
  - Open/close and position (simulated using operation time).
- **Modules with Digital Interfaces** (PC-Logic `05-201`, Audio Distribution `05-205`, Digital Interface `05-206`)
  - Each digital entry is discovered as a button; HA entities are created after restart.
- **PC-Link Module** `05-200`
  - The bus interface Home Assistant connects to (serial or via a TCP bridge). Required — all communication goes through the PC-Link.
- **Feedback Module** `05-207`
  - Optional companion to the PC-Link. When present and wired to the PC-Link, it pushes module state changes to Home Assistant in real time, so no polling is needed. Without one, HA polls the bus on a configurable interval (60–3600 s).
- **Nikobus Buttons** (physical switches, IR, Feedback, Remote)
  - Populated automatically via the **Discover modules & buttons** and **Scan all module links** buttons on the Nikobus Bridge device.
  - Button press events can be used as triggers in Home Assistant automations.
  - Buttons with LEDs require LED on/off addresses in each module output configuration.
  - Virtual / IR-scene button addresses that aren't on the bus can be fired from scripts via the `nikobus.send_button_press` service.
- **Home Assistant Scenes**
  - Trigger multiple module/channel updates from one command.

## Events Fired by the Integration

The integration emits structured Home Assistant bus events for every button press lifecycle:

- Base events: `nikobus_button_pressed` and `nikobus_button_released`.
- Classification: `nikobus_short_button_pressed` (press duration < 1s) and `nikobus_long_button_pressed` (press duration ≥ 1s). The 1-second threshold is defined as `SHORT_PRESS` in `custom_components/nikobus/const.py`.
- Release-duration buckets (rounded down): `nikobus_button_pressed_0` (< 1s), `nikobus_button_pressed_1` (1–<2s), `nikobus_button_pressed_2` (2–<3s), and `nikobus_button_pressed_3` (≥ 3s).
- Hold milestones (emitted while still pressed): `nikobus_button_timer_1`, `_2`, and `_3` at 1s, 2s, and 3s respectively.
- Post-refresh notification: `nikobus_button_operation` when the integration refreshes impacted modules after the press, including metadata such as the impacted module address/group and configured operation time.

All events share the same payload keys so automations can rely on a consistent schema:

```yaml
address: "004E2C"        # Button address (uppercase hex without 0x)
module_address: "9105"   # Module address if available, otherwise null
channel: 1                # Channel number if known
ts: "2024-05-01T12:00:00Z"  # UTC timestamp at emission time
press_id: "004E2C-..."    # Unique identifier for this press cycle
state: "pressed"|"released"|"timer"
duration_s: 1.2           # Seconds between press and release (null for initial press)
bucket: 1                 # 0/1/2/3 matching duration buckets, otherwise null
threshold_s: 2            # Timer milestone that fired (1/2/3), otherwise null
source: "nikobus"
```

You can trigger automations with or without specifying the button address. If you include the address, the automation reacts only to that button (addresses come from the button discovery run).

### Example Automations

#### Short press: toggle a light

```yaml
alias: "React to Nikobus Button Push"
description: "Toggle a light when a specific Nikobus button is pressed."
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "004E2C"  # Address taken from the button entity attributes after discovery
action:
  - service: homeassistant.toggle
    target:
      entity_id: light.example_light
```

#### Long press: dim a light progressively

`nikobus_button_timer_2` fires once the button has been held for ≥ 2 s, allowing you to differentiate a sustained press from a tap. Combine it with `nikobus_button_pressed_3` (released after ≥ 3 s) to build a hold-to-dim behaviour.

```yaml
alias: "Hold Nikobus button to dim"
description: "While the button is held past 2 s, dim the target light to 30 %."
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

#### Drive a scene from a physical Nikobus button

```yaml
alias: "All shutters down at sunset"
description: "Close every roller via a Nikobus scene whenever button 25E952 is pressed after dusk."
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: " 25E952"   # leading space prevents YAML scientific-notation parsing
condition:
  - condition: sun
    after: sunset
action:
  - service: scene.turn_on
    target:
      entity_id: scene.scene_close_all_shutters
```

#### Move a cover to an exact position

`set_cover_position` works against the integration's virtual travel calculator, so the cover stops automatically when the requested position is reached.

```yaml
alias: "Living room cover at 60 % when 'TV' button pressed"
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

Place any of these YAML blocks in a Home Assistant automation (UI or YAML) as you would for any other event trigger.

## Scenes

States for dimmers and shutters use 0–255; switches accept `"on"` or `"off"`; shutters accept `"open"` or `"close"`. Channels belong to group 1 (1–6) or group 2 (7–12); the integration updates the relevant group automatically.

```json
{
  "scene": [
    {
      "id": "scene_turn_on_living_dimmer_lights",
      "description": "Turn on living dimmer lights",
      "channels": [
        {"module_id": "0E6C", "channel": "1", "state": "150"},
        {"module_id": "0E6C", "channel": "2", "state": "200"}
      ]
    }
  ]
}
```

```json
{
  "scene": [
    {
      "id": "scene_close_all_shutters",
      "description": "Close all shutters",
      "channels": [
        {"module_id": "9105", "channel": "1", "state": "close"},
        {"module_id": "9105", "channel": "2", "state": "close"},
        {"module_id": "9105", "channel": "3", "state": "close"},
        {"module_id": "9105", "channel": "4", "state": "close"},
        {"module_id": "9105", "channel": "5", "state": "close"},
        {"module_id": "9105", "channel": "6", "state": "close"}
      ]
    },
    {
      "id": "scene_open_all_shutters",
      "description": "Open all shutters",
      "channels": [
        {"module_id": "9105", "channel": "1", "state": "open"},
        {"module_id": "9105", "channel": "2", "state": "open"},
        {"module_id": "9105", "channel": "3", "state": "open"},
        {"module_id": "9105", "channel": "4", "state": "open"},
        {"module_id": "9105", "channel": "5", "state": "open"},
        {"module_id": "9105", "channel": "6", "state": "open"}
      ]
    }
  ]
}
```

Scene activation only changes the channels you define; other channels remain untouched. Scenes can be triggered directly in Home Assistant, through automations, or linked to Nikobus buttons.

## Staying in Sync with Nikobus

The integration keeps Home Assistant synchronized with Nikobus using two complementary methods:

1. **Button-driven refresh**: Every discovered button carries its `linked_modules` mapping. When the button fires on the bus, the integration refreshes the impacted module group(s) immediately.
2. **Periodic refresh**: Choose either the integration's custom refresh interval or the Feedback Module's internal refresh (when connected via PC-Link).

If you rely solely on periodic refresh, Home Assistant may briefly be out of sync between refresh cycles. Running discovery after adding new hardware keeps button-driven refreshes complete for the most accurate, immediate state updates.

## Connectivity Options

**Only one client should connect to Nikobus at a time.**

- Direct serial/USB connection, e.g., `/dev/ttyUSB0`.
- Network bridge, e.g., `192.168.2.50:9999`, if the Nikobus installation is remote from the HA host.

If the connection drops for any reason, the integration will automatically attempt to reconnect using exponential back-off (retrying after 5 s, 10 s, 20 s, up to a maximum of 60 s between attempts). Entities will appear unavailable until the connection is restored, then return to their current state without requiring an HA restart.

![TCP bridge example 1](https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1)
![TCP bridge example 2](https://github.com/fdebrus/Nikobus-HA/assets/33791533/9c0b11ad-0a1c-4728-ab5e-5e68be6452a8)
![TCP bridge example 3](https://github.com/fdebrus/Nikobus-HA/assets/33791533/498e5a0f-ab75-4d29-9988-884015fbf05a)

## Known Limitations

- **Always quote button addresses in YAML.** A Nikobus button address is a six-character hex string (e.g. `25E952`). Addresses that contain only digits and the letter `E` happen to look like scientific notation to YAML 1.1 parsers — `25E952` is read as `25 × 10⁹⁵²`, overflows, and Home Assistant persists the value back as `null`. Home Assistant's automation editor may even strip your quotes on save. Workarounds: prefix the address with a leading space inside the quotes (`address: " 25E952"`), or use the integration's `button_id`-style identifiers in your trigger conditions when matching is critical. Addresses containing any letter `A`–`F` other than `E` (e.g. `9A93EE`) are unaffected.
- **No bus-level discovery.** The PC-Link bridge does not advertise itself over mDNS, SSDP, or USB vendor-specific descriptors. The serial device path or TCP `host:port` must be entered manually.
- **One client per bus.** Only one client may talk to the PC-Link at a time. Stop any other Nikobus software (the official PC tool, ioBroker, OpenHAB, etc.) before starting Home Assistant.
- **Polling latency without a Feedback Module.** When no 05-207 Feedback Module is present, module states are read on the configured polling interval (60–3600 s, default 120 s). Physical button presses still trigger immediate targeted refreshes for the impacted modules, so day-to-day responsiveness is unaffected — but external changes (manual relay actuation, scenes triggered by another client) are only picked up on the next poll cycle.
- **Single config entry per HA instance.** Two physically separate Nikobus installations cannot be paired with the same Home Assistant; the integration is designed for one bus per HA host.

## Setup

1. Install the custom integration using HACS. Use the custom link below, or clone the repository into `config/custom_components/nikobus` on your Home Assistant host.

[![Add to HACS](https://img.shields.io/badge/HACS-Add%20Custom%20Repository-blue.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fdebrus&repository=Nikobus-HA&category=integration)

2. Restart Home Assistant so the integration is picked up.
3. Go to **Settings → Devices & Services → Add Integration → Nikobus**.
4. Enter your serial port path or `IP:port` (e.g. `/dev/ttyUSB0` or `192.168.2.50:9999`). The connection is tested immediately.
5. On the **Hardware Configuration** step, enable either toggle if it applies:
   - **Feedback Module (05-207) installed and connected via PC-Link** — state changes are pushed by the Feedback Module, no polling needed.
   - **PC-Link is older than Gen 3** — enables compatibility tweaks for first- and second-generation PC-Link hardware.
6. If neither toggle is enabled you'll be asked for a **Polling interval** (60–3600 s, default 120). Lower values mean faster updates, more bus traffic.
7. The integration starts up. Module and button data are populated from the UI — see **Discovery & Configuration** below. There are no hand-edited JSON files; if you're upgrading from a pre-2.0 release with `nikobus_module_config.json`, it is auto-migrated (see [Upgrading](#upgrading-from-pre-20-releases)).

## Discovery & Configuration

All module and button data lives in Home Assistant's own storage (`.storage/nikobus.modules` and `.storage/nikobus.buttons`). There are no hand-edited JSON files for modules or buttons — everything is populated from the UI. (Scenes are the one exception — see the [Scenes](#scenes) section.)

### 1. Discover modules and buttons

Open the **Nikobus Bridge** device page and press **Discover modules & buttons** (or trigger the same from **Configure → Discover modules & buttons (PC Link inventory)**). This walks the PC Link registry, identifies every module (switch / dimmer / shutter / feedback / PC-logic), and creates every physical wall button found on the bus as its own HA device.

A large install takes several minutes end-to-end — the per-register ACK timeout in the library is 1.5 s, so slow modules stretch the scan. Two diagnostic sensors on the Bridge device track progress:

- **Discovery status** — the live per-register message, updating every ~0.5 s (e.g. `Scanning module 0E6C (2/10) — register 0x87 of 0xFF (145 records)`). The former coarse enum (`idle` / `pc_link` / `module_scan` / `finished` / `error`) is exposed as the `phase` attribute for automations that grouped on it.
- **Discovery progress** — 0–100 % with 0.1 precision so sub-percent updates are visible.

### 2. Customize a module (optional)

Most users skip this step. But if you want to rename a channel, change what HA exposes it as, configure feedback LEDs, or set a roller's travel times, go to **Configure → Customize a module (description, entity type, LED triggers, travel time)**:

- Pick a module — you'll see its description plus a list of channels.
- Pick a channel — you can edit:
  - **Description** — becomes the entity name.
  - **Entity type** — how HA exposes the channel:
    - Switch modules: `switch` (default), `light`, or `none` to disable.
    - Dimmer modules: `light` (default) or `none`.
    - Roller modules: `cover` (default), `switch`, `light`, or `none`.
  - **LED on / LED off addresses** — six-hex bus addresses driving a feedback LED on the wall button. Leave blank if unused.
  - **Travel time up / down** (roller modules only) — seconds to fully open/close. Used by the virtual position calculator so `cover.set_cover_position` can aim for an exact value. If only `up` is set, it is reused for `down` with reduced accuracy.

Changes persist in `.storage/nikobus.modules` and survive re-discovery.

### 3. Scan module links

Press **Scan all module links** on the Bridge device (or **Configure → Scan all modules for button links**). This walks every output module and records which button addresses drive which channels, populating the `linked_modules` metadata on each button entity.

The button is greyed out until a PC Link inventory has run — scanning links against zero known modules does nothing. Likewise the options-flow item aborts with a "Run a PC-Link inventory first" message.

### Repair issues

If the integration loads with no buttons configured yet, it surfaces a **No Nikobus buttons configured** notice in **Settings → Repairs** that links straight to a PC Link inventory run. The notice clears automatically once any button lands in storage.

## Buttons

Every physical Nikobus button found during a PC Link inventory becomes a **device** in HA, parented to the Nikobus Bridge. Within each device, every *operation point* (each key on a keypad, each IR code on an IR receiver, …) gets:

- A **button** entity — pressable from the UI; fires the same bus frame a physical press would.
- A **binary sensor** entity (disabled by default) — turns `on` briefly when the physical button is pressed, resetting to `idle` after 1 s. Useful for state-based automations.

Button data lives in `.storage/nikobus.buttons`; there is no user-editable JSON. See [Discovery & Configuration](#discovery--configuration) for the discovery workflow, and [Upgrading](#upgrading-from-pre-20-releases) if migrating from `nikobus_button_config.json`.

After discovery, each button entity exposes its linkage as attributes:

```yaml
linked_outputs:
  - module_address: "0E6C"
    channel: 1
    mode: "M01 (Dim on/off (2 buttons))"
    t1: null
    t2: null
wall_button_address: "0D1C80"
wall_button_model: "05-348"
wall_button_type: "IR Button with 4 Operation Points"
wall_button_key: "1C"
```

Every light, cover, and switch entity mirrors this with a `controlled_by` attribute that lists the buttons triggering it — so you can answer "which wall button turns on this light?" from the entity page without parsing config.

Impacted module groups are derived automatically from `linked_modules` (channels 1–6 → group 1, 7–12 → group 2), so state refreshes after a press work out of the box.

### Renaming buttons

Discovered devices get names like `Button with 2 Operation Points (1E584C)`. To give them a friendlier name, rename the device in the HA UI (**Settings → Devices & Services → Nikobus → ⋮ → Rename**). HA stores that as `name_by_user` and preserves it across reloads, restarts, and re-runs of discovery.

### IR op-points across multiple receivers

When the same IR code is learned by several receivers (common with scene remotes mapped onto multiple rooms), each receiver gets its own op-point entity with a distinct bus address. The visible device name is qualified with the receiver address — e.g. `IR 30A on 0D1C80` — so duplicates remain distinguishable at a glance.

### Virtual / IR-scene buttons

Button addresses that are not present on the physical bus (IR scene triggers, Harmony plug codes, hand-added entries from older releases) are no longer exposed as entities. Fire them from scripts or automations instead:

```yaml
service: nikobus.send_button_press
data:
  address: "84DFFC"
```

This emits a `#N<address>` frame on the bus just as a physical press would, and every automation listening on the corresponding `nikobus_button_pressed` event fires normally.

### Upgrading from pre-2.0 releases

Older versions of the integration persisted module and button data in two files under `/config`:

- `nikobus_module_config.json` — modules and channels.
- `nikobus_button_config.json` — buttons and their `impacted_module` entries.

As of 2.0 both files are **auto-migrated** into HA's own storage on first load:

- `nikobus_module_config.json` → `.storage/nikobus.modules`; the source file is renamed to `<name>.migrated` (never deleted) as an escape hatch. Channel-level fields — `description`, `entity_type`, `led_on` / `led_off`, and roller `operation_time_up` / `_down` — are preserved verbatim. A legacy single `operation_time` is split into both directions.
- `nikobus_button_config.json` → per-button descriptions are lifted into each device's `name_by_user` in the HA device registry; the rest is ignored. After discovery populates the new Store, the source file is no longer read.

After the migration runs:

1. Full HA restart after upgrading the integration and the pinned `nikobus-connect` library.
2. (Optional) Run **Discover modules & buttons** again to refresh any hardware that's been added or replaced since the last export.
3. Rebuild any virtual / scene buttons as scripts calling `nikobus.send_button_press`.
4. The renamed `.migrated` sidecar files are safe to leave on disk or delete. The migrations are gated on the Store being empty and won't re-run.

## Protocol

The integration communicates with Nikobus hardware over its serial bus (directly via USB/serial or through a TCP bridge). The implementation details are contained within the source code of this repository. This work was developed independently for the sole purpose of interoperability between Home Assistant and Nikobus hardware that the user already owns, in line with Article 6 of Directive 2009/24/EC.

## How the Integration Works

The code is split into two packages:

- **[nikobus-connect](https://github.com/fdebrus/nikobus-connect)** — pip-installed, pinned via `manifest.json`. Owns the low-level work:
  - `NikobusConnect` — serial/TCP transport + PC-Link handshake.
  - `NikobusEventListener` — parses CR-terminated ASCII frames, dispatches button presses and feedback updates.
  - `NikobusCommandHandler` — queued, retrying command processor that throttles bursts (e.g. "close all shutters").
  - `NikobusAPI` — high-level operations (read/set output state, cover start/stop).
  - `NikobusDiscovery` — PC Link inventory + module register scan, reverse-engineers button-to-output mappings and populates the operation-point / `linked_modules` metadata.

- **This integration (`custom_components/nikobus/`)** — the Home Assistant glue:
  - `coordinator.py` — wires the library together, owns polling + discovery lifecycle, dispatches state signals.
  - `nkbstorage.py` — the two HA Stores (`.storage/nikobus.modules` and `.storage/nikobus.buttons`) with their load/save adapters.
  - `nkbmigration.py` — one-shot import of legacy `nikobus_module_config.json` into the Module Store.
  - `nkbactuator.py` — routes incoming button frames into HA bus events (`nikobus_button_pressed` etc.) with debounce and duration tracking.
  - `nkbconfig.py` — scene-file loader/writer (scenes still live in JSON).
  - `nkbtravelcalculator.py` — virtual cover-position tracking from `operation_time_up` / `_down`.
  - `router.py` — translates module channels into HA entity types and builds the `controlled_by` reverse index exposed on light / switch / cover entities.
  - `entity.py` and the entity platforms (`{light,switch,cover,button,binary_sensor,sensor,scene}.py`).
  - `config_flow.py` — initial config flow (connection → hardware → polling) and the four-item Configure options menu.
  - `repairs.py` — the "No Nikobus buttons configured" repair flow.
  - `diagnostics.py` — the "Download diagnostics" payload for bug reports.

## Issues and Discussion

For questions or general requests, please visit the [Home Assistant community thread](https://community.home-assistant.io/t/custom-component-nikobus/732832).

## Trademark Notice

Nikobus is a trademark of Niko NV. This project is an independent community effort and is not affiliated with, endorsed by, or sponsored by Niko NV.

## License

This project is provided for personal and other non-commercial use only. You may
view, copy, modify, and share the code and documentation for non-commercial
purposes. Commercial use of this software is not permitted without prior written
permission from the maintainers. The software is provided "as is" without
warranties of any kind.
