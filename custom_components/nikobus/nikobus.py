"""API for Nikobus"""

import asyncio
import logging
import json

from .const import DOMAIN
from .nkbconnect import NikobusConnect
from .nkbconfig import NikobusConfig
from .nkblistener import NikobusEventListener
from .nkbcommand import NikobusCommandHandler

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "2024.5.27"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    def __init__(self, hass, connection_string, async_event_handler, coordinator):
        self._hass = hass
        self._async_event_handler = async_event_handler
        self._coordinator = coordinator 
        self.controller_address = None
        self.nikobus_module_states = {}
        self.dict_module_data = {}
        self.dict_button_data = {}
        self.nikobus_connection = NikobusConnect(connection_string)
        self.nikobus_config = NikobusConfig(self._hass)
        self.nikobus_listener = NikobusEventListener(self._hass, self.nikobus_connection, self.button_discovery, self.process_feedback_data)
        self.nikobus_command_handler = NikobusCommandHandler(self._hass, self.nikobus_connection, self.nikobus_listener, self.nikobus_module_states)

    @classmethod
    async def create(cls, hass, connection_string, async_event_handler, coordinator):
        _LOGGER.debug(f"Creating Nikobus instance with connection string: {connection_string}")
        instance = cls(hass, connection_string, async_event_handler, coordinator)
        if await instance.connect():
            _LOGGER.info("Nikobus instance created and connected successfully")
            return instance
        _LOGGER.error("Failed to create Nikobus instance")
        return None

#### CONNECT TO NIKOBUS
    async def connect(self) -> bool:
        if await self.nikobus_connection.connect():
            try:
                self.dict_module_data = await self.nikobus_config.load_json_data("nikobus_module_config.json", "module")
                self.dict_button_data = await self.nikobus_config.load_json_data("nikobus_button_config.json", "button")
                return True
            except Exception as err:
                _LOGGER.error(f"Configuration load error: {err}")
        return False

#### EVENT AND COMMAND LOOPS
    async def listen_for_events(self):
        await self.nikobus_listener.start()

    async def command_handler(self):
        await self.nikobus_command_handler.start()

#### Nikobus Discovery
    async def nikobus_discovery(self):
        # Ask the Nikobus for the controller address
        await self.nikobus_command_handler.send_command("#A")
        
#### REFRESH DATA FROM NIKOBUS
    async def refresh_nikobus_data(self) -> bool:
        
        await self.nikobus_discovery()

        if 'switch_module' in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data['switch_module'])

        if 'dimmer_module' in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data['dimmer_module'])

        if 'roller_module' in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data['roller_module'])

        return True

    async def _refresh_module_type(self, modules_dict):
        for address, module_data in modules_dict.items():
            _LOGGER.debug(f'Refreshing data for module address: {address}')
            state = ""
            channel_count = len(module_data.get("channels", []))
            groups_to_query = [1] if channel_count <= 6 else [1, 2]

            for group in groups_to_query:
                group_state = await self.nikobus_command_handler.get_output_state(address, group) or ""
                _LOGGER.debug(f'State for group {group}: {group_state} address : {address} ***')
                state += group_state

            self.nikobus_module_states[address] = bytearray.fromhex(state)
            _LOGGER.debug(f'{self.nikobus_module_states[address]}')

    async def process_feedback_data(self, module_group, event):
        """Process feedback data from Nikobus"""
        try:
            module_address_raw = event[3:7]
            module_address = module_address_raw[2:] + module_address_raw[:2]

            module_state_raw = event[9:21]

            _LOGGER.debug(f"Processing feedback module data: module_address={module_address}, group={module_group}, module_state={module_state_raw}")

            if module_group == 1:
                self.nikobus_module_states[module_address][:6] = bytearray.fromhex(module_state_raw)
            elif module_group == 2:
                self.nikobus_module_states[module_address][6:] = bytearray.fromhex(module_state_raw)

            _LOGGER.debug(f'Full state of module {module_address} - {self.nikobus_module_states[module_address]}')
            self._coordinator.async_update_listeners()

        except Exception as e:
            _LOGGER.error(f"Error processing feedback data: {e}")

#### UTILS
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel"""
        return self.nikobus_module_states.get(address, bytearray())[channel - 1]

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel"""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module')

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group"""
        byte_value = bytearray.fromhex(value)
        if address in self.nikobus_module_states:
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(f'New value set for array {self.nikobus_module_states[address]}.')
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module')

#### SWITCHES
    def get_switch_state(self, address: str, channel: int) -> bool:
        """Get the state of a switch based on its address and channel"""
        return self.get_bytearray_state(address, channel) == 0xFF

    async def turn_on_switch(self, address: str, channel: int) -> None:
        """Turn on a switch specified by its address and channel"""
        _LOGGER.debug(f"{address} - {channel}")
        self.set_bytearray_state(address, channel, 0xFF)
        # led_address = self.dict_module_data["switch_module"][address]["channels"][channel]["led_address"]
        # _LOGGER.debug(f"{led_address}")
        await self.nikobus_command_handler.set_output_state(address, channel, 0xFF)

    async def turn_off_switch(self, address: str, channel: int) -> None:
        """Turn off a switch specified by its address and channel"""
        _LOGGER.debug(f"{address} - {channel}")
        self.set_bytearray_state(address, channel, 0x00)
        # led_address = self.dict_module_data["switch_module"][address]["channels"][channel]["led_address"]
        # _LOGGER.debug(f"{led_address}")
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

#### DIMMERS
    def get_light_state(self, address: str, channel: int) -> bool:
        """Get the state of a light based on its address and channel"""
        return self.get_bytearray_state(address, channel) != 0x00
    
    def get_light_brightness(self, address: str, channel: int) -> int:
        """Get the brightness of a light based on its address and channel"""
        return self.get_bytearray_state(address, channel)

    async def turn_on_light(self, address: str, channel: int, brightness: int) -> None:
        """Turn on a light specified by its address and channel with the given brightness"""
        self.set_bytearray_state(address, channel, brightness)
        await self.nikobus_command_handler.set_output_state(address, channel, brightness)

    async def turn_off_light(self, address: str, channel: int) -> None:
        """Turn off a light specified by its address and channel"""
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

#### COVERS
    def get_cover_state(self, address: str, channel: int) -> int:
        """Get the state of a cover based on its address and channel"""
        return self.get_bytearray_state(address, channel)

    async def stop_cover(self, address: str, channel: int) -> None:
        """Stop a cover specified by its address and channel"""
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

    async def open_cover(self, address: str, channel: int) -> None:
        """Open a cover specified by its address and channel"""
        self.set_bytearray_state(address, channel, 0x01)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x01)

    async def close_cover(self, address: str, channel: int) -> None:
        """Close a cover specified by its address and channel"""
        self.set_bytearray_state(address, channel, 0x02)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x02)

#### BUTTONS
    async def button_discovery(self, address: str) -> None:
        _LOGGER.debug(f"Discovering button at address: {address}.")

        if self.dict_button_data is None:
            self.dict_button_data = {}

        if address in self.dict_button_data.get("nikobus_button", {}):
            _LOGGER.debug(f"Button at address {address} found in configuration. Processing...")
            await self.process_button_modules(self.dict_button_data["nikobus_button"][address], address)
        else:
            _LOGGER.info(f"No existing configuration found for button at address {address}. Adding new configuration")
            new_button = {
                "description": f"DISCOVERED - Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}]
            }
            if "nikobus_button" not in self.dict_button_data:
                self.dict_button_data["nikobus_button"] = {}
            self.dict_button_data["nikobus_button"][address] = new_button
            await self.nikobus_config.write_json_data("nikobus_button_config.json", "button", self.dict_button_data)
            _LOGGER.debug(f"New button configuration added for address {address}.")

    async def process_button_modules(self, button: dict, address: str) -> None:
        """Process actions for each module impacted by the button press."""
        button_description = button.get('description')
        _LOGGER.debug(f"Processing button press for {button_description}")

        for impacted_module_info in button.get('impacted_module', []):
            impacted_module_address = impacted_module_info.get('address')
            impacted_group = impacted_module_info.get('group')

            if not (impacted_module_address and impacted_group):
                _LOGGER.debug("Skipping module due to missing address or group")
                continue
            try:
                _LOGGER.debug(f'*** Refreshing status for module {impacted_module_address} for group {impacted_group}')

                if impacted_module_address in self.dict_module_data.get('dimmer_module', {}):
                    _LOGGER.debug("Dimmer DETECTED")
                    await asyncio.sleep(1)

                value = await self.nikobus_command_handler.get_output_state(impacted_module_address, impacted_group)
                self.set_bytearray_group_state(impacted_module_address, impacted_group, value)
                await self._async_event_handler("nikobus_button_pressed", address)

            except Exception as e:
                _LOGGER.error(f"Error processing button press for module {impacted_module_address} group {impacted_group} value {value} error {e}")

class NikobusConnectionError(Exception):
    pass

class NikobusDataError(Exception):
    pass
