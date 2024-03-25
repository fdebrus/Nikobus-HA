import logging
import select
import asyncio
import serial_asyncio
import time
import os
import textwrap
import json
from pathlib import Path
import aiofiles

from .const import DOMAIN

from .nkbconnect import NikobusConnect
from .nkblistener import NikobusEventListener
from .nkbcommand import NikobusCommandHandler

from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.entity_registry import async_get

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "2024.3.14"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    def __init__(self, hass, connection_string, async_event_handler):
        self._hass = hass
        self._async_event_handler = async_event_handler
        self.nikobus_module_states = {}
        self.json_config_data = {}
        self.json_button_data = {}

        self.nikobus_connection = None
        self.nikobus_listener = None
        self.nikobus_command_handler = None

    @classmethod
    async def create(cls, hass, connection_string, async_event_handler):
        _LOGGER.debug(f"Creating Nikobus instance with connection string: {connection_string}")
    
        instance = cls(hass, connection_string, async_event_handler)
    
        if await instance.connect(connection_string):
            _LOGGER.info("Nikobus instance created and connected successfully.")
            return instance
        else:
            _LOGGER.error("Nikobus instance could not be created.")
            return None

    async def connect(self, connection_string: str) -> bool:
        self.nikobus_connection = NikobusConnect(connection_string)
        connected = await self.nikobus_connection.connect()

        try:
            await self.load_json_config_data()
            await self.load_json_button_data()
        except Exception as err:
            _LOGGER.error(f"Nikobus configuration file load error - {err}")
            return False
        return True

    async def listen_for_events(self):
        self.nikobus_listener = NikobusEventListener(self.nikobus_connection, self.button_discovery)
        await self.nikobus_listener.listen_for_events()

    async def command_handler(self):
        self.nikobus_command_handler = NikobusCommandHandler(self.nikobus_connection, self.nikobus_listener, self.nikobus_module_states)
        await self.nikobus_command_handler.process_commands()

#### CONFIG FILES
    async def load_json_config_data(self) -> bool:
        config_file_path = self._hass.config.path("nikobus_config.json")
        _LOGGER.debug(f'Loading Nikobus configuration data from {config_file_path}')
        try:
            async with aiofiles.open(config_file_path, mode='r') as file:
                self.json_config_data = json.loads(await file.read())
            _LOGGER.info('Nikobus module configuration data successfully loaded.')
            return True
        except FileNotFoundError:
            _LOGGER.error(f'Nikobus configuration file not found: {config_file_path}')
        except json.JSONDecodeError as e:
            _LOGGER.error(f'Failed to decode JSON data in Nikobus configuration file: {e}')
        except Exception as e:
            _LOGGER.error(f'Failed to load Nikobus module configuration data: {e}')
    
        return False

    async def load_json_button_data(self) -> bool:
        config_file_path = self._hass.config.path("nikobus_button_config.json")
        _LOGGER.debug(f'Loading Nikobus button configuration data from {config_file_path}')
    
        try:
            async with aiofiles.open(config_file_path, 'r') as file:
                self.json_button_data = json.loads(await file.read())
            _LOGGER.info('Nikobus button configuration data successfully loaded.')
            return True
        except FileNotFoundError:
            _LOGGER.error(f'Nikobus button configuration file not found: {config_file_path}')
        except json.JSONDecodeError as e:
            _LOGGER.error(f'Failed to decode JSON data in Nikobus button configuration file: {e}')
        except Exception as e:
            _LOGGER.error(f'Failed to load Nikobus button configuration data: {e}')
    
        return False

#### REFRESH DATA FROM THE NIKOBUS
    async def refresh_nikobus_data(self) -> bool:
        # Iterate through each module in the configuration data.
        for module_type, entries in self.json_config_data.items():
            for entry in entries:
                address = entry.get("address")
                _LOGGER.debug(f'Refreshing data for module address: {address}')
                state = ""
                # Determine how many groups need to be queried based on channel count.
                channel_count = len(entry.get("channels", []))
                groups_to_query = [1] if channel_count <= 6 else [1, 2]

                for group in groups_to_query:
                    group_state = await self.nikobus_command_handler.get_output_state(address, group) or ""
                    _LOGGER.debug(f'*** State for group {group}: {group_state} address : {address} ***')
                    state += group_state  # Concatenate states from each group.

                self.nikobus_module_states[address] = bytearray.fromhex(state)
                _LOGGER.debug(f'{self.nikobus_module_states[address]}')
        return True

#### UTILS
    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel within the Nikobus module."""
        return self.nikobus_module_states.get(address, bytearray())[channel - 1]

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel within the Nikobus module."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module.')

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group within the Nikobus module."""
        byte_value = bytearray.fromhex(value)
        if address in self.nikobus_module_states:
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(f'New value set for array {self.nikobus_module_states[address]}')
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module.')

#### SWITCHES
    def get_switch_state(self, address: str, channel: int) -> bool:
        """Get the state of a switch based on its address and channel."""
        return self.get_bytearray_state(address, channel) == 0xFF

    async def turn_on_switch(self, address: str, channel: int) -> None:
        """Turn on a switch specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0xFF)
        await self.nikobus_command_handler.set_output_state(address, channel, 0xFF)

    async def turn_off_switch(self, address: str, channel: int) -> None:
        """Turn off a switch specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

#### DIMMERS
    def get_light_state(self, address: str, channel: int) -> bool:
        """Get the state of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel) != 0x00
    
    def get_light_brightness(self, address: str, channel: int) -> int:
        """Get the brightness of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def turn_on_light(self, address: str, channel: int, brightness: int) -> None:
        """Turn on a light specified by its address and channel with the given brightness."""
        self.set_bytearray_state(address, channel, brightness)
        await self.nikobus_command_handler.set_output_state(address, channel, brightness)

    async def turn_off_light(self, address: str, channel: int) -> None:
        """Turn off a light specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

#### COVERS
    def get_cover_state(self, address: str, channel: int) -> int:
        """Get the state of a cover based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def stop_cover(self, address: str, channel: int) -> None:
        """Stop a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x00)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x00)

    async def open_cover(self, address: str, channel: int) -> None:
        """Open a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x01)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x01)

    async def close_cover(self, address: str, channel: int) -> None:
        """Close a cover specified by its address and channel."""
        self.set_bytearray_state(address, channel, 0x02)
        await self.nikobus_command_handler.set_output_state(address, channel, 0x02)

#### BUTTONS
    async def write_json_button_data(self) -> None:
        """Write the discovered button to a JSON file."""
        button_config_file_path = self._hass.config.path("nikobus_button_config.json")
        async with aiofiles.open(button_config_file_path, 'w') as file:
            await file.write(json.dumps(self.json_button_data, indent=4))
            _LOGGER.debug("Discovered button successfully written to JSON file.")

    async def button_discovery(self, address: str) -> None:
        """Discover a button by its address and update configuration if it's new, or process it if it exists."""
        _LOGGER.debug(f"Discovering button at address: {address}")

        for button in self.json_button_data.get('nikobus_button', []):
            if button['address'] == address:
                _LOGGER.debug(f"Button at address {address} found in configuration. Processing...")
                await self.process_button_modules(button, address)
                return
        _LOGGER.warning(f"No existing configuration found for button at address {address}. Adding new configuration.")
        new_button = {
            "description": f"DISCOVERED - Nikobus Button #N{address}",
            "address": address,
            "impacted_module": [{"address": "", "group": ""}]
        }
        self.json_button_data["nikobus_button"].append(new_button)
        await self.write_json_button_data()
        _LOGGER.debug(f"New button configuration added for address {address}.")

    async def process_button_modules(self, button: dict, address: str) -> None:
        """Process actions for each module impacted by the button press."""
        button_description = button.get('description')
        _LOGGER.debug(f"Processing button press for '{button_description}'")

        for module in button.get('impacted_module', []):
            impacted_module_address = module.get('address')
            impacted_group = module.get('group')
            if not (impacted_module_address and impacted_group):
                continue
            _LOGGER.debug(f"Refreshing status for module {impacted_module_address}, group {impacted_group}")
            try:
                _LOGGER.debug(f'*** Refreshing status for module {impacted_module_address} for group {impacted_group}')
                await asyncio.sleep(1)
                value = await self.nikobus_command_handler.get_output_state(impacted_module_address, impacted_group)
                self.set_bytearray_group_state(impacted_module_address, impacted_group, value)
            except Exception as e:
                _LOGGER.error(f"Error processing button press for module {impacted_module_address} group {impacted_group}: {e}")
        await self._async_event_handler("nikobus_button_pressed", address)
