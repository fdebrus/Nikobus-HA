[![HACS Badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# Nikobus Integration for Home Assistant (v2.2024.4.5)

This integration enables the control of Nikobus systems via Home Assistant, allowing you to manage various Nikobus modules directly from your Home Assistant setup.

## Supported Modules

- **Switch Modules**: `05-000-02` and `05-002-02`
  - Operate switches on/off.
- **Dimmer Module**: `05-007-02`
  - Operate dimmers on/off and set brightness.
- **Shutter Module**: `05-001-02`
  - Operate covers open/close and set position.
- **Nikobus Buttons**:
  - Button press events can be used as triggers in Home Assistant automations.
  - Virtual buttons can be created within Home Assistant and mapped to Nikobus.

Connectivity is supported via direct connections such as `/dev/ttyUSB0` or over the network using an IP and port, e.g., `192.168.2.1:123`.

## Automation Example

```yaml
alias: "React to Nikobus Button Push"
description: "Perform actions when a Nikobus button is reported as pushed."
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "specific_button_address"  # Optional: Specify to react to a specific button
action:
  - service: homeassistant.toggle
    entity_id: light.example_light


