
[![HACS Badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# Nikobus Integration for Home Assistant (v2.2024.5.27)

This integration enables the control of Nikobus systems via Home Assistant, allowing you to manage various Nikobus modules directly from your Home Assistant setup.

**Only one client on the Nikobus at a time, do not connect anything else in parallel of this integration.**

## Supported Modules

- **Switch Module**: `05-000-02` and **Compact Switch Module** `05-002-02`
  - Commands: Operate switches on/off.
- **Dimmer Module**: `05-007-02`
  - Commands: Operate dimmers on/off and set brightness.
- **Shutter Module**: `05-001-02`
  - Commands: Operate covers open/close and set position.
- **Feedback Module**: `05-207`
  - The Feedback module's internal refresh mechanism can be utilized for integration modules status updates instead of relying on user-defined periodic polling by the Nikobus integration. **It is highly recommended to use the Feedback module instead of a custom refresh interval when available, to prevent excessive bus traffic.**
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
3. Copy `nikobus_module_conf.json.default` to your Home Assistant configuration directory, remove the .default extension.
4. Update the file to reflect your specific installation settings.

### Button Configuration

Upon button press, buttons will be discovered and registered in `nikobus_button_conf.json` in your home assistant /config folder. 
If the file does not exist, it will be created. Next the file needs manual updates:

- Add to each button the corresponding module address and group that is impacted. So the corresponding module state is refreshed.
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

## How does it work... 

**nkbconnect**

Connects Home Assistant to Nikobus over a user-defined TCP/IP socket or USB port and performs a handshake to instruct Nikobus to echo commands on the bus..

**nkbconfig** 

It is responsible for reading configuration files created by the user. Since the inventory cannot be directly obtained from the bus yet, the user must define files for all modules and buttons. The configuration is then stored for further processing by the integration.

**nkblistener** 

It creates a continuous loop to listen for messages on the Nikobus.
Upon receiving a message, it submits the message to the handle_message function for processing.
The handle_message function determines the appropriate action based on the initial characters of the message and identifies whether:
- A physical button has been pushed
- The feedback module has sent a command
- A module is responding to a feedback module command
- Any other messages, except those flagged to be ignored, are placed in a response queue for further processing
It also includes a draft logic to handle long button presses, which is under review and subject to change with the introduction of support for buttons with feedback LEDs [WIP].

**nkbcommand**

xxxxxxxxxxxxxx (further documentation to come)

# Issues/Discussion

For discussions/general requests, please refer to [this](https://community.home-assistant.io/t/custom-component-nikobus/732832) thread in HA community.

## Gallery

During the integration setup, you will be asked to provide your connection string, which can be either an IP
or a USB port. If your setup includes a Nikobus Feedback Module, check the box. In this case, the integration will refresh based on the Feedback Module's refresh cycle and feedback data instead of the custom refresh rate.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/bc32fa94-2e3c-4d25-aa0f-0f964f539d37)

If you do not have a Nikobus Feedback Module, leave the box unchecked. You will then be presented with another screen to set up your custom integration refresh rate. Avoid setting the refresh interval too low to prevent excessive traffic on the bus.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/f68b85fe-7f1d-4b8d-b9c2-9048705ef8dd)

You can always revisit these options after setup by selecting the "CONFIGURE" option from the integration menu. If you need to change your connectivity method, delete and recreate the integration with the new parameters.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/850f151a-72a9-47a3-8514-bab665849377)
![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/c6ae485a-a969-48d2-99e4-5687c02c5a85)


<a href="https://buymeacoffee.com/fdebrus" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/black_img.png" alt="Buy Me A Coffee" style="height: auto !important;width: auto !important;" ></a><br>
