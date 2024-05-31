
[![HACS Badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# Nikobus Integration for Home Assistant (2024.5.29)

This integration enables the control of Nikobus systems via Home Assistant, allowing you to manage various Nikobus modules directly from your Home Assistant setup.

**Only one client on the Nikobus at a time, do not connect anything else in parallel of this integration.**

Connectivity is supported through direct connections, such as /dev/ttyUSB0, or over the network using an IP address and port, for example, 192.168.2.1:123. Modules like PC-Link, Feedback-Module, and PC-Logic can be utilized for establishing these connections.

Network connectivity can also be achieved by adding a bridge, such as...

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1)

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

### Module Configuration

You can begin by locating the example file in the integration directory after installation via HACS. This file will be available in your HA installation at /config/custom_components/nikobus/nikobus_module_config.json.default. Copy this file to the /config directory and rename it to nikobus_module_config.json.

The description field is free text and can include anything that helps you identify the module.
For example: "description": "Switch Module S1"

The model should reflect the Nikobus reference of the module.
For example: "model": "05-000-02"

Each channel can have a free text description to help you identify them. Ensure that these descriptions are unique both within the module and across different modules to avoid duplicates in the integration entities.

  ```json
{
    "switch_module": [
        {
            "description": "Switch Module S1",
            "model": "05-000-02",
            "address": "C9A5",
            "channels": [
                {"description": "S1 Output 1"},
                {"description": "S1 Output 2"},
                {"description": "S1 Output 3"},
                {"description": "S1 Output 4"},
                {"description": "S1 Output 5"},
                {"description": "S1 Output 6"},
                {"description": "S1 Output 7"},
                {"description": "S1 Output 8"},
                {"description": "S1 Output 9"},
                {"description": "S1 Output 10"},
                {"description": "S1 Output 11"},
                {"description": "S1 Output 12"}
            ]
        }
    ],
```

Entries that define roller output include an additional argument, operation_time, which specifies the total time (in seconds) that a shutter takes to fully open or close. Update this value to reflect your shutter's actual operation time. This parameter is crucial as it allows the integration to simulate setting the shutter position, a feature not natively supported by Nikobus, by operating the shutter for a calculated period.

```json
    "roller_module": [
        {
            "description": "Rollershutter Module R1",
            "model": "05-001-02",
            "address": "9105",
            "channels": [
                {"description": "R1 Output 1", "operation_time": "40"},
                {"description": "R1 Output 2", "operation_time": "40"},
                {"description": "R1 Output 3", "operation_time": "40"},
                {"description": "R1 Output 4", "operation_time": "40"},
                {"description": "R1 Output 5", "operation_time": "40"},
                {"description": "R1 Output 6", "operation_time": "40"}
            ]
        }
    ]
```

To avoid setting up entries and entities for unused module outputs, prefix any output description with "not_in_use" so it will not be imported into the integration.
For example:{"description": "**not_in_use** output_10"} 

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

It is responsible for reading configuration files created by the user. Since the inventory cannot be directly obtained from the bus yet, the user must define files for all modules and buttons. The configuration is then stored for further processing by the integration. Button file does not need to be created but updated, each time a button is physically pressed, its address will be added to the button file (file will be created by first button pressed and be placed in your HA /config directory).

Next you need to update the name to your preference, and list the impacted module(s) so the integration will refresh status of this(those) module(s) to reflect the change of state in HA wuthout having to wait for the next module(s) refresh cycle.

Discovered button will look like

  ```json
        {
            "description": "DISCOVERED - Nikobus Button #N4ECB1A",
            "address": "4ECB1A",
            "impacted_module": [
                {
                    "address": "",
                    "group": ""
                }
            ]
        }
  ```

You have to update it to

  ```json
        {
            "description": "Kitchen Light On",
            "address": "4ECB1A",
            "impacted_module": [
                {
                    "address": "4707",
                    "group": "1"
                }
            ]
        }
  ```

or if multiples modules impacted by the same button

  ```json
        {
            "description": "Kitchen Light On",
            "address": "4ECB1A",
            "impacted_module": [
                {
                    "address": "4707",
                    "group": "1"
                },
                {
                    "address": "C9A5",
                    "group": "2"
                }
            ]
        }
  ```

For the changes to be reflected, re-start HA

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

It established a command processing queue, which is essential for handling a rapid sequence of commands. For instance, if you use HomeKit to send a command to close all shutters, this generates a rapid series of commands to close each shutter individually. Without queuing, Nikobus cannot manage the speed at which these commands are sent. The queue will introduce a 0.3-second pause between every consecutive commands.

To read from the bus, it employs a three-strike approach, waiting for the expected data from the bus. Since the bus can be busy, it may be necessary to wait for the signal that corresponds to our command.

It has also all logic needed to send and receive data on Nikobus.

# Issues/Discussion

For discussions/general requests, please refer to [this](https://community.home-assistant.io/t/custom-component-nikobus/732832) thread in HA community.

## Gallery

During the integration setup, you will be asked to provide your connection string, which can be either an IP
or a USB port. If your setup includes a Nikobus Feedback Module, check the box. In this case, the integration will refresh based on the Feedback Module's refresh cycle and feedback data instead of the custom refresh rate.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/60625a74-1965-4af3-883a-f06713eb6fcb)

If you do not have a Nikobus Feedback Module, leave the box unchecked. You will then be presented with another screen to set up your custom integration refresh rate. Avoid setting the refresh interval too low to prevent excessive traffic on the bus.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/4f3a894b-5a39-4dd3-bdd9-f9b628e547b3)

You can always revisit these options after setup by selecting the "CONFIGURE" option from the integration menu. If you need to change your connectivity method (IP vs USB), delete and recreate the integration with the new parameters.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/e985517a-ccb5-49f9-9938-e6a4594764f4)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/c42ddd69-08ce-4c1e-966f-f2a2607d190a)


<a href="https://buymeacoffee.com/fdebrus" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/black_img.png" alt="Buy Me A Coffee" style="height: auto !important;width: auto !important;" ></a><br>
