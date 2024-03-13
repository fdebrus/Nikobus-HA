[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# Nikobus Integration (v2024.3.13)

Home Assistant integration for Nikobus.

# Functionality

Supported and tested

	Nikobus switch module 05-000-02 and 05-002-02.
 
	Nikobus dimmer module 05-007-02.
 
	Nikobus shutter module 05-001-02.
 
	Nikobus buttons.

Switches can be operated on/off.
Dimmers can be operated on/off/set brightness.
Covers can be operated open/close/set position.
Button press event can be added as a trigger in Home Assistant, eg (Automation)
Virtual button can be created in HA and defined in Nikobus (virtual button)

Support direct connectivity eg /dev/ttyUSB0 or IP:Port eg 192.168.2.1:123

## Automation example 

```
alias: "React to Nikobus Button Push"
description: "Perform actions when a Nikobus button is reported as pushed"
trigger:
  - platform: event
    event_type: nikobus_button_pressed
    event_data:
      address: "specific_button_address"  # Optional: Specify if you want to react to a specific button
action:
  - service: homeassistant.toggle
    entity_id: light.example_light
```

# Setup process 

One you have installed the custom integration using HACS, go to the custom_repository/nikobus
Copy nikobus_conf.json.default to your HA config directory / nikobus_conf.json
Copy nikobus_button_conf.json.default to your HA config directory / nikobus_button_conf.json
Update the files to reflect your installation.

The nikobus installation in refreshed every 2 minutes and update the various states on Home Assistant.

Buttons are discovered when pushed and registered in the nikobus_button_conf.json, they will need manual update as below :

For each button, you will have to define with module address and which module group is impacted
	On a 12 outputs module, 1-6 is module group 1 and 7-12 module group 2
	On a 6 outputs module, only module group 1 exist

List the impacted modules as show below for each button of your installation.

            "impacted_module": [
                {
                    "address": "0E6C",
                    "group": "1"
                }
            ]

* Note: I button can have multiple impacted_module address and group.
** Note: If you do not plan to use your button as HA trigger, and you can accomodate to wait for the next refresh cycle to have Nikobus in sync with HA, you do not need to create buttons


![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/d0e82ca4-9a75-4a15-b471-a747b3abda1f)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/ec3e56de-5b9e-404a-b97f-341c4c96331a)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/4c0eb84a-0187-418a-aa9e-24650214998b)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/6d154d91-ac59-4f44-b3c4-e7714005d15e)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/a5cbb377-9274-42e6-bee7-abe58c62ca82)


