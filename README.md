
[![HACS Badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# Nikobus Integration for Home Assistant (v2.2024.4.5)

This integration enables the control of Nikobus systems via Home Assistant, allowing you to manage various Nikobus modules directly from your Home Assistant setup.

## Supported Modules

- **Switch Modules**: `05-000-02` and `05-002-02`
  - Commands: Operate switches on/off.
- **Dimmer Module**: `05-007-02`
  - Commands: Operate dimmers on/off and set brightness.
- **Shutter Module**: `05-001-02`
  - Commands: Operate covers open/close and set position.
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
```

## Setup Process

1. Install the custom integration using HACS.
2. Navigate to `custom_repository/nikobus`.
3. Copy `nikobus_module_conf.json.default` and `nikobus_button_conf.json.default` to your Home Assistant configuration directory.
4. Update the files to reflect your specific installation settings.

### Button Configuration

Upon button press, buttons are discovered and can be registered in `nikobus_button_conf.json`. For manual updates:

- Define each button with the corresponding module address and group.
- For a 12 outputs module, groups 1-6 correspond to module group 1, and 7-12 to module group 2.
- For a 6 outputs module, only module group 1 exists.
- Example configuration:

  ```json
  "impacted_module": [
    {
      "address": "0E6C",
      "group": "1"
    }
  ]
  ```

- **Note**: A button can affect multiple modules. If you do not plan to use your button as an HA trigger, updates will sync during the next refresh cycle.

## Gallery

![Control Interface](https://github.com/fdebrus/Nikobus-HA/assets/33791533/d0e82ca4-9a75-4a15-b471-a747b3abda1f)
![Setup Example](https://github.com/fdebrus/Nikobus-HA/assets/33791533/ec3e56de-5b9e-404a-b97f-341c4c96331a)
![Module Overview](https://github.com/fdebrus/Nikobus-HA/assets/33791533/4eb7a4e5-0789-45c0-bd80-1c8af84d6bd0)
![Device Management](https://github.com/fdebrus/Nikobus-HA/assets/33791533/0e92763a-cfbd-4b9c-ae97-b06d317f9544)
![Home Assistant Integration](https://github.com/fdebrus/Nikobus-HA/assets/33791533/a5cbb377-9274-42e6-bee7-abe58c62ca82)
