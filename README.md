# Nikobus Integration for Home Assistant (2024.11.10)

This integration enables the control of Nikobus systems via Home Assistant, allowing you to manage various Nikobus modules directly from your Home Assistant platform.

## Supported Modules

- **Switch Module**: `05-000-02` and **Compact Switch Module** `05-002-02`
  - Commands: Operate switches on/off.
- **Dimmer Module**: `05-007-02`
  - Commands: Operate dimmers on/off and set brightness.
- **Shutter Module**: `05-001-02`
  - Commands: Operate covers open/close and set position.
- **Modules with Digital Interfaces**  PC-Logic: `05-201` - Audio Distribution: `05-205` - Digital Interface: `05-206`
  - All digital entries will be detected as button (when triggered the first time) and corresponding entities (button and sensor) will be created in HA after restart.
- **PC-Link Module**: `05-200`
  - Could be used to connect Nikobus to HomeAssistant, with a customizable refresh interval set within the integration configuration.
- **Feedback Module**: `05-207`
  - Could be used to connect Nikobus to HomeAssistant, with a customizable refresh interval set within the integration configuration.
  - The Feedback module's internal refresh mechanism can be utilized for integration modules status updates instead of relying on user-defined periodic polling by the Nikobus integration. **! ONLY IF PC-Link is present and used for connectivity !**. if not, use a user defined refresh interval in the integration configuration.
- **Nikobus Buttons**: Physical switches, IR, Feedback, Remote
  - Button press events can be used as triggers in Home Assistant automations.
    
    The following events are available
      -  **nikobus_button_pressed**
        
    The following are available after release
      -  **nikobus_button_released**
      -  **nikobus_short_button_pressed**
      -  **nikobus_long_button_pressed**
      -  **nikobus_button_pressed_0** (button press detected after release for less than 1 second)
      -  **nikobus_button_pressed_1** (button press detected after release for 1 second)
      -  **nikobus_button_pressed_2** (button press detected after release for 2 seconds)
      -  **nikobus_button_pressed_3** (button press detected after release for 3 seconds)
        
    The  following events are fired as soon as the respective timer is reached
      -  **nikobus_button_timer_1** (Button press detected for 1 second)
      -  **nikobus_button_timer_2** (Button press detected for 2 seconds)
      -  **nikobus_button_timer_3** (Button press detected for 3 seconds)
   
    - A button with a feedback LED requires an additional argument to be added to each module output. You need to include the address of the button that turns the LED on and the address of the button that turns the LED off. These addresses can be the same, depending on how you configure your button action in Nikobus. The button address can be found in the nikobus_button_config.json file. After the first press of the button, the address will be discovered and added to the file.
  - Virtual buttons can be created within Home Assistant and mapped to Nikobus.

- **HomeAssistant Scenes**: This integration supports HomeAssistant Scenes, which allow you to trigger multiple changes across different modules (switch, dimmer, and shutter) using one command.

  Scenes can be defined with specific modules and channels to be controlled, including the state or value for each module. States for dimmers and shutters can be expressed as 0-255 / shutters 1 (open) or 2 (close), while switches can be set to "on" or "off".

  Example Scene Configuration:
``` json
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

other example with shutters
``` json
{
    "scene": [
        {
            "id": "scene_close_all_shutters",
            "description": "Close all shutters",
            "channels": [
                {"module_id": "9105", "channel":"1", "state":"close"},
                {"module_id": "9105", "channel":"2", "state":"close"},
                {"module_id": "9105", "channel":"3", "state":"close"},
                {"module_id": "9105", "channel":"4", "state":"close"},
                {"module_id": "9105", "channel":"5", "state":"close"},
                {"module_id": "9105", "channel":"6", "state":"close"}
            ]
        },
        {
            "id": "scene_open_all_shutters",
            "description": "Open all shutters",
            "channels": [
                {"module_id": "9105", "channel":"1", "state":"open"},
                {"module_id": "9105", "channel":"2", "state":"open"},
                {"module_id": "9105", "channel":"3", "state":"open"},
                {"module_id": "9105", "channel":"4", "state":"open"},
                {"module_id": "9105", "channel":"5", "state":"open"},
                {"module_id": "9105", "channel":"6", "state":"open"}
            ]
        }
    ]
}
```

  - Scene activation will only modify the channels that are included in the scene configuration, leaving others unaffected.
  - Channels may belong to group 1 (channels 1-6) or group 2 (channels 7-12), and the integration updates the appropriate group based on the channels defined in the scene.
  - Once defined, a scene can be triggered directly from Home Assistant used in automations or linked to a Nikobus button, etc...

Acceptable states for outputs

  - Outputs for switch module accept "on" or "off" as values
  - Outputs for dimmer module accept anything between "0" OFF and "255" 100% ON as values
  - Outputs for shutter module accept "close" and "open" as values

**Important Note:** 

The integration maintains in sync with Nikobus using two methods:
    
**a**. Any physical button must be included in the button_config file. This ensures that when the button is pressed, it triggers a refresh of the impacted module(s) and immediately updates Home Assistant (HA).
    
**b**. Refresh mechanism, which can be either integration-based with a custom refresh rate or Feedback Module-based with Nikobus's internal refresh rate.
    
  The later might introduce a delay, meaning the integration will not retrieve the module status until the next refresh cycle. As a result, HA and Nikobus might be out of sync until the subsequent refresh cycle. By accurately defining all physical buttons in method (a), HA will remain consistently synchronized; otherwise, delays might occur if relying solely on method (b).

  Both methods are complementary, but for the best experience, ensure your button configuration file is fully completed.

**Connectivity**

**Only one client on the Nikobus at a time, do not connect anything else in parallel of this integration.**

It is supported through direct connections, such as **/dev/ttyUSB0**

or over the network using an IP address and port, for example, **192.168.2.50:9999**.

Network connectivity can be achieved by adding a bridge. This could come handy is your Nikobus installation is distant from your HA server.

<div style="display: flex; justify-content: space-between;">
    <img src="https://github.com/fdebrus/Nikobus-HA/assets/33791533/10c79eaf-3362-4891-b5da-1b827faae8d1" alt="TCP Server" style="width: 48%;">  
</div>

<div style="display: flex; justify-content: space-between;">
    <img src="https://github.com/fdebrus/Nikobus-HA/assets/33791533/9c0b11ad-0a1c-4728-ab5e-5e68be6452a8" alt="TCP Server" style="width: 48%;">    
    <img src="https://github.com/fdebrus/Nikobus-HA/assets/33791533/498e5a0f-ab75-4d29-9988-884015fbf05a" alt="TCP Server" style="width: 48%;">
</div>

## Automation Example

The integration will emit different messages on the Home Assistant bus:

- **nikobus_button_pressed** 
- **nikobus_button_released**
- **nikobus_long_button_pressed**
- **nikobus_short_button_pressed**
- **nikobus_button_pressed_0** Button press detected after release for less than 1 second
- **nikobus_button_pressed_1** Button press detected after release for 1 second
- **nikobus_button_pressed_2** Button press detected after release for 2 seconds
- **nikobus_button_pressed_3** Button press detected after release for 3 seconds
-  **nikobus_button_timer_1** Button press detected for 1 second
-  **nikobus_button_timer_2** Button press detected for 2 seconds
-  **nikobus_button_timer_3** Button press detected for 3 seconds

Any press duration above 500ms will be considered long press, you can adapt to your needs by updating the value in the const.py file from the integration custom directory and restart HA.
```
LONG_PRESS_THRESHOLD_MS = 500 # Time in ms to detect a long press (>= LONG_PRESS_THRESHOLD_MS)
```

You can choose to use these events with or without specifying the button address. Without the button address, the automation will trigger for any button press. With the address, the automation will be specific to the button associated with that address.

Address shall be the one referenced in your nikobus_button_config.json, **004E2C** in this example

``` json
    "nikobus_button": [
        {
            "description": "BT_GF_Living_Sofa_Wall_Light_Up",
            "address": "004E2C",
            "impacted_module": [
                {
                    "address": "0E6C",
                    "group": "1"
                }
            ]
        }
  ...
```

If the button interacts with a shutter, you can set the "operation_time." The shutter will then move toward the target position for the specified "operation_time" and stop.
"operation_time" is expressed in seconds.

``` json
    "nikobus_button": [
        {
            "description": "BT_GF_Office_Shutter_Close",
            "address": "C86C4E",
            "operation_time": "5",
            "impacted_module": [
                {
                    "address": "8394",
                    "group": "1"
                }
            ]
        }
  ...
```


```yaml
alias: "React to Nikobus Button Push"
description: "Perform actions when a Nikobus button is reported as pressed."
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

1. Install the custom integration using HACS. Use the custom link below, or copy the repository to custom_repository/nikobus

[![Add to HACS](https://img.shields.io/badge/HACS-Add%20Custom%20Repository-blue.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fdebrus&repository=Nikobus-HA&category=integration)

2. Navigate to `custom_repository/nikobus`.
3. Copy `nikobus_module_conf.json.default` to your Home Assistant configuration directory, remove the .default extension.
4. Update the file to reflect your specific installation settings.
5. Add Nikobus under Settings/Devices & services - Add Integration
6. You will be asked for a USB port or IP:PORT
7. "Has Feedback Module installed and connected over PC-Link ?" If you have a feedback module installed AND you connect over PC-Link, check this option. If you connect to feedback module without PC-Link or you have no feedback module, leave this unchecked and specify a custom refresh rate on the next screen.
8. Depending on previous selected option, you will get a screen to specify a custom refrresh rate.

### Module Configuration

Make sure to record your modules addresses as they are displayed in the nikobus software.

You can begin by locating the example file in the integration directory after installation via HACS. This file will be available in your HA installation at /config/custom_components/nikobus/nikobus_module_config.json.default. Copy this file to the /config directory and rename it to nikobus_module_config.json.

The description field is free text and can include anything that helps you identify the module.
For example: "description": "Switch Module S1"

The model should reflect the Nikobus reference of the module.
For example: "model": "05-000-02"

Each channel can have a free text description to help you identify them. Ensure that these descriptions are unique both within the module and across different modules to avoid duplicates in the integration entities.

If you are using the Feedback Module with an LED button, register a button address for each entry to turn the LED on or off, respectively. The button address reference is case-sensitive and should follow this format: "8AA8FA". If you do not have a Feedback Module or no LED status to link with a particular module output, leave both the led_on and led_off values blank.

  ```json
{
    "switch_module": [
        {
            "description": "Switch Module S1",
            "model": "05-000-02",
            "address": "C9A5",
            "channels": [
                {"description": "S1 Output 1", "led_on":"259B02", "led_off":"659B02"},
                {"description": "S1 Output 2", "led_on":"", "led_off":""},
                {"description": "S1 Output 3", "led_on":"", "led_off":""},,
                {"description": "S1 Output 4", "led_on":"", "led_off":""},
                {"description": "S1 Output 5", "led_on":"", "led_off":""},
                {"description": "S1 Output 6", "led_on":"", "led_off":""},
                {"description": "S1 Output 7", "led_on":"", "led_off":""},
                {"description": "S1 Output 8", "led_on":"", "led_off":""},...
```

```json
    "dimmer_module": [
        {
            "description": "Dimmer Module D1",
            "model": "05-007-02",
            "address": "0E6C",
            "channels": [
                {"description": "D1 Output 1", "led_on":"", "led_off":""},
                {"description": "D1 Output 2", "led_on":"", "led_off":""},
                {"description": "D1 Output 3", "led_on":"", "led_off":""},
                {"description": "D1 Output 4", "led_on":"", "led_off":""},
                {"description": "D1 Output 5", "led_on":"", "led_off":""},...
```

Entries that define roller output include an additional argument, operation_time, which specifies the total time (in seconds) that a shutter takes to fully open or close. Update this value to reflect your shutter's actual operation time. This parameter is crucial as it allows the integration to simulate setting the shutter position, a feature not natively supported by Nikobus, by operating the shutter for a calculated period.

```json
    "roller_module": [
        {
            "description": "Rollershutter Module R1",
            "model": "05-001-02",
            "address": "9105",
            "channels": [
                {"description": "R1 Output 1", "operation_time": "40", "led_on":"", "led_off":""},
                {"description": "R1 Output 2", "operation_time": "40", "led_on":"", "led_off":""},
                {"description": "R1 Output 3", "operation_time": "40", "led_on":"", "led_off":""},
                {"description": "R1 Output 4", "operation_time": "40", "led_on":"", "led_off":""},
                {"description": "R1 Output 5", "operation_time": "40", "led_on":"", "led_off":""},
                {"description": "R1 Output 6", "operation_time": "40", "led_on":"", "led_off":""}
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
or a USB port. If your setup includes a Nikobus Feedback Module and you are connected to Nikobus over the PC-Link interface, check the box. In this case, the integration will refresh based on the Feedback Module's refresh cycle and feedback data instead of the custom refresh rate.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/218966e0-6c51-42e1-b29b-858c0601b84a)

If you do not have a Nikobus Feedback Module, leave the box unchecked. You will then be presented with another screen to set up your custom integration refresh rate. Avoid setting the refresh interval too low to prevent excessive traffic on the bus.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/4f3a894b-5a39-4dd3-bdd9-f9b628e547b3)

You can always revisit these options after setup by selecting the "CONFIGURE" option from the integration menu.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/e985517a-ccb5-49f9-9938-e6a4594764f4)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/062ac6ea-27c6-433b-ab48-be0c562c5cff)

<a href="https://buymeacoffee.com/fdebrus" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/black_img.png" alt="Buy Me A Coffee" style="height: auto !important;width: auto !important;" ></a><br>
