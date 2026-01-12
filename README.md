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

## Entity unique IDs and dashboard impact

Recent releases standardized **unique ID formats** for module-based entities (lights, switches, and covers) to explicitly include the entity type, for example:

```yaml
nikobus_light_<address>_<channel>
nikobus_switch_<address>_<channel>
nikobus_cover_<address>_<channel>
```


### Impact when upgrading

If you are upgrading from an older version of the integration, Home Assistant will detect these updated unique IDs as **new entities**. As a result:

- Existing Lovelace cards, automations, scripts, and voice assistants referencing the old entity IDs may no longer work.
- The integration will automatically clean up orphaned entities that no longer match the current configuration, preventing duplicates.

### Recommended recovery approach

Rather than manually fixing each reference, Home Assistant provides a built-in way to remap entity IDs:

1. Go to **Settings → Devices & Services → Integrations**
2. Open **Nikobus**
3. Click **Entities**
4. Select all Entities
5. Use **Recreate entity IDs** from the 3 dots top right

This will regenerate entity IDs using the new standardized format while preserving history and minimizing dashboard disruption where possible.

### After upgrade checklist

- Review **Settings → Devices & Services → Entities** to confirm the new entity IDs.
- Verify and update any remaining automations, scripts, or dashboards that explicitly reference old entity IDs.
- If you rely on voice assistants (HomeKit, Google Assistant, Alexa), re-sync entities if required.

This change ensures consistent, predictable entity identification across all module types going forward.

## Prerequisites

Before installing:

- Collect the module addresses from the Nikobus software (needed to build the config files).
- Ensure only **one** client is connected to the Nikobus bus at any time (do not run the Nikobus PC software in parallel).
- Decide how you will connect: directly to a USB/serial adapter (e.g., `/dev/ttyUSB0`) or via a TCP bridge (e.g., `192.168.2.50:9999`).
- If you have a Feedback Module and a PC-Link, confirm you can connect through it; otherwise plan to use polling with a custom refresh interval.

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
  - Can be used to connect Home Assistant with a configurable refresh interval.
- **Feedback Module** `05-207`
  - Can be used for connectivity and for status refresh based on its internal mechanism when combined with PC-Link. Without PC-Link, use a custom refresh interval.
- **Nikobus Buttons** (physical switches, IR, Feedback, Remote)
  - Button press events can be used as triggers in Home Assistant automations.
  - Buttons with LEDs require LED on/off addresses in each module output configuration.
  - Virtual buttons can be created in Home Assistant and mapped to Nikobus.
- **Home Assistant Scenes**
  - Trigger multiple module/channel updates from one command.

## Events Fired by the Integration

The integration emits structured Home Assistant bus events for every button press lifecycle:

- Base events: `nikobus_button_pressed` and `nikobus_button_released`.
- Classification: `nikobus_short_button_pressed` (press duration < 3s) and `nikobus_long_button_pressed` (press duration ≥ 3s). The 3-second threshold is defined as `LONG_PRESS` in `custom_components/nikobus/const.py`.
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

You can trigger automations with or without specifying the button address. If you include the address, the automation reacts only to that button (addresses are recorded in `nikobus_button_config.json`).

### Example Automation

```yaml
alias: "React to Nikobus Button Push"
description: "Perform actions when a specific Nikobus button is pressed."
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "004E2C"  # Address from nikobus_button_config.json
action:
  - service: homeassistant.toggle
    target:
      entity_id: light.example_light
```

Place this YAML in a Home Assistant automation (UI or YAML) as you would for any other event trigger.

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

1. **Button-driven refresh**: Make sure every physical button is listed in the button config file. When the button is pressed, the integration refreshes the impacted module(s) immediately.
2. **Periodic refresh**: Choose either the integration’s custom refresh interval or the Feedback Module’s internal refresh (when connected via PC-Link).

If you rely solely on periodic refresh, Home Assistant may briefly be out of sync between refresh cycles. Keeping the button configuration complete provides the most accurate, immediate state updates.

## Connectivity Options

**Only one client should connect to Nikobus at a time.**

- Direct serial/USB connection, e.g., `/dev/ttyUSB0`.
- Network bridge, e.g., `192.168.2.50:9999`, if the Nikobus installation is remote from the HA host.

![TCP bridge example 1](https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1)
![TCP bridge example 2](https://github.com/fdebrus/Nikobus-HA/assets/33791533/9c0b11ad-0a1c-4728-ab5e-5e68be6452a8)
![TCP bridge example 3](https://github.com/fdebrus/Nikobus-HA/assets/33791533/498e5a0f-ab75-4d29-9988-884015fbf05a)

## Setup Process

1. Install the custom integration using HACS. Use the custom link below, or clone the repository into `config/custom_components/nikobus` on your Home Assistant host.

[![Add to HACS](https://img.shields.io/badge/HACS-Add%20Custom%20Repository-blue.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fdebrus&repository=Nikobus-HA&category=integration)

2. Navigate to `config/custom_components/nikobus` on your Home Assistant installation.
3. Copy `nikobus_module_config.json.default` to your Home Assistant `/config` directory and remove the `.default` extension.
4. Update the copied file to match your modules and buttons (see configuration sections below).
5. In Home Assistant, go to **Settings → Devices & Services → Add Integration**, and select **Nikobus**.
6. Enter your USB port or IP:PORT.
7. If you have a Feedback Module **and** connect through PC-Link, enable the “Has Feedback Module installed and connected over PC-Link?” option. Otherwise, leave it unchecked and continue to the next step.
8. If prompted, set a custom refresh rate. Typical values are 5–30 seconds depending on how quickly you need updates when not using a Feedback Module. Longer intervals reduce bus traffic but may delay state updates between refreshes.

## Module Configuration

After installation, an example file is available at `/config/custom_components/nikobus/nikobus_module_config.json.default`. Copy it to `/config/nikobus_module_config.json` and adjust it to your setup.

- `description`: Free text to identify the module (e.g., "Switch Module S1").
- `model`: The Nikobus reference (e.g., "05-000-02").
- `channels`: Each channel can have a description; keep descriptions unique across modules to avoid duplicate entity names.

**Required vs optional fields**

- **Required (module level)**: `description`, `model`, `address`, and `channels`.
- **Required (per channel)**: `description`.
- **Optional (per channel)**:
  - `led_on` / `led_off`: Feedback LED addresses (case-sensitive, format like `8AA8FA`).
  - `operation_time`: For roller outputs, the time in seconds to fully open/close. If omitted, the cover will still work but uses a default timing, which may reduce position accuracy.
  - `entity_type`: Override the default entity type (see matrix below).

**Entity type by module**

`entity_type` controls how Home Assistant exposes each channel. If you omit it, the integration uses the module default.

| Module key | Default entity_type | Allowed entity_type values | Notes |
| --- | --- | --- | --- |
| `switch_module` | `switch` | `switch`, `light` | Useful when you want a switch output to show up as a light. |
| `dimmer_module` | `light` | `light` | Dimmers are always exposed as lights. |
| `roller_module` | `cover` | `cover`, `switch`, `light` | `switch` maps to open on "on" and stop on "off". |

- Prefix an unused output description with `not_in_use` to skip creating entities for it.

### Switch Module Example

```json
{
  "switch_module": [
    {
      "description": "Switch Module S1",
      "model": "05-000-02",
      "address": "C9A5",
      "channels": [
        {"description": "S1 Output 1", "led_on": "259B02", "led_off": "659B02"},
        {"description": "S1 Output 2", "led_on": "", "led_off": ""},
        {"description": "S1 Output 3", "led_on": "", "led_off": ""},
        {"description": "S1 Output 4", "led_on": "", "led_off": ""},
        {"description": "S1 Output 5", "led_on": "", "led_off": ""},
        {"description": "S1 Output 6", "led_on": "", "led_off": ""},
        {"description": "S1 Output 7", "led_on": "", "led_off": ""},
        {"description": "S1 Output 8", "led_on": "", "led_off": ""}
      ]
    }
  ]
}
```

### Dimmer Module Example

```json
{
  "dimmer_module": [
    {
      "description": "Dimmer Module D1",
      "model": "05-007-02",
      "address": "0E6C",
      "channels": [
        {"description": "D1 Output 1", "led_on": "", "led_off": ""},
        {"description": "D1 Output 2", "led_on": "", "led_off": ""},
        {"description": "D1 Output 3", "led_on": "", "led_off": ""},
        {"description": "D1 Output 4", "led_on": "", "led_off": ""},
        {"description": "D1 Output 5", "led_on": "", "led_off": ""}
      ]
    }
  ]
}
```

### Roller (Shutter) Module Example

```json
{
  "roller_module": [
    {
      "description": "Rollershutter Module R1",
      "model": "05-001-02",
      "address": "9105",
      "channels": [
        {"description": "R1 Output 1", "operation_time": "40", "led_on": "", "led_off": "", "entity_type": "switch"},
        {"description": "R1 Output 2", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 3", "operation_time": "40"},
        {"description": "R1 Output 4", "operation_time": "40"},
        {"description": "R1 Output 5", "operation_time": "40"},
        {"description": "R1 Output 6", "operation_time": "40"}
      ]
    }
  ]
}
```

## Button Configuration

When you press a Nikobus button for the first time, the integration discovers it and creates/updates `nikobus_button_config.json` in your Home Assistant `/config` directory. After discovery, manually edit each button entry to list the impacted modules so state refreshes immediately after a press.

- For 12-output modules, groups 1–6 map to module group 1; groups 7–12 map to module group 2. Six-output modules use group 1 only.
- You can map a single button to multiple modules.
- Restart Home Assistant after edits so the integration reloads the mappings.

### Discovered Button Example

```json
{
  "description": "DISCOVERED - Nikobus Button #N4ECB1A",
  "address": "4ECB1A",
  "impacted_module": [
    {"address": "", "group": ""}
  ]
}
```

### Updated Button Example

```json
{
  "description": "Kitchen Light On",
  "address": "4ECB1A",
  "impacted_module": [
    {"address": "4707", "group": "1"},
    {"address": "C9A5", "group": "2"}
  ]
}
```

If a button controls a shutter, set `operation_time` (in seconds) on the button entry to match the time needed to move fully so the integration can stop the shutter after the desired duration.

```json
{
  "description": "BT_GF_Office_Shutter_Close",
  "address": "C86C4E",
  "operation_time": "5",
  "impacted_module": [
    {"address": "8394", "group": "1"}
  ]
}
```

## How the Integration Works

- **nkbconnect**: Connects Home Assistant to Nikobus over TCP/IP or USB and performs the handshake so commands are echoed on the bus.
- **nkbconfig**: Reads and validates the user-provided configuration files. Because the inventory is not discoverable from the bus, you must define all modules and buttons. The button file is created automatically on first discovery but should be edited to add descriptions and impacted modules.
- **nkblistener**: Listens for messages on the Nikobus bus and hands them off for processing (button press, feedback module command, module responses, etc.). Includes logic for handling long button presses.
- **nkbcommand**: Provides a queued command processor to throttle bursts of commands (e.g., closing all shutters), adding a short pause between consecutive commands and implementing a retry strategy when reading from a busy bus.

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
