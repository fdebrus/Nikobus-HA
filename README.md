# Nikobus Integration for Home Assistant

This custom integration connects Home Assistant to your Nikobus installation so you can control switches, dimmers, shutters, and respond to button presses directly from Home Assistant.

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

The integration emits the following Home Assistant bus events:

- `nikobus_button_pressed`
- `nikobus_button_released`
- `nikobus_short_button_pressed`
- `nikobus_long_button_pressed`
- `nikobus_button_pressed_0` (press detected after release < 1s)
- `nikobus_button_pressed_1` (press detected after release for 1s)
- `nikobus_button_pressed_2` (press detected after release for 2s)
- `nikobus_button_pressed_3` (press detected after release for 3s)
- `nikobus_button_timer_1` (press held for 1s)
- `nikobus_button_timer_2` (press held for 2s)
- `nikobus_button_timer_3` (press held for 3s)

Press duration above 500ms is treated as a long press. You can adjust the threshold in `custom_components/nikobus/const.py` and restart Home Assistant:

```python
LONG_PRESS_THRESHOLD_MS = 500  # Time in ms to detect a long press
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
- For buttons with feedback LEDs, set `led_on` and `led_off` addresses (case-sensitive, format like `8AA8FA`). Leave blank if unused.
- For roller outputs, add `operation_time` (seconds to fully open/close) so the integration can simulate shutter positioning.
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
        {"description": "R1 Output 1", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 2", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 3", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 4", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 5", "operation_time": "40", "led_on": "", "led_off": ""},
        {"description": "R1 Output 6", "operation_time": "40", "led_on": "", "led_off": ""}
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
