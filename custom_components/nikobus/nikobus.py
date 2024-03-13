import logging
import select
import asyncio
import serial_asyncio
import time
import ipaddress
import re
import os
import json
import textwrap
from pathlib import Path
import aiofiles

from .const import DOMAIN

from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.entity_registry import async_get

from .helpers import (
    make_pc_link_command, 
    calculate_group_number
)

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "2024.3.14"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    def __init__(self, hass, connection_string, async_event_handler):
        self._hass = hass
        self._connection_string = connection_string
        self._async_event_handler = async_event_handler
        self._response_queue = asyncio.Queue()
        self._command_queue = asyncio.Queue()
        self._event_listener_task = None
        self._last_nikobus_command_received_timestamp = 0
        self._button_address_occurrences = {}
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None
        self.nikobus_module_states = {}
        self.json_config_data = {}
        self.json_button_data = {}

    @classmethod
    async def create(cls, hass, connection_string, async_event_handler):
        _LOGGER.debug(f"Creating Nikobus instance with connection string: {connection_string}")
    
        # Instantiate the class with the provided Home Assistant instance and connection string.
        instance = cls(hass, connection_string, async_event_handler)
    
        if await instance.connect():
            _LOGGER.info("Nikobus instance created and connected successfully.")
            return instance
        else:
            _LOGGER.error("Nikobus instance could not be created.")
            return None

#### CONNECT NIKOBUS
    async def connect(self):
        """Establish a connection to the Nikobus system based on the provided connection string."""
    
        def validate_string(input_string):
            """Validate the format of the connection string to determine connection type."""
            try:
                ipaddress.ip_address(input_string.split(':')[0])
                return "IP"
            except ValueError:
                pass
            if re.match(r'^/dev/tty(USB|S)\d+$', input_string):
                return "Serial"
            return "Unknown"
    
        connection_type = validate_string(self._connection_string)
    
        try:
            if connection_type == "IP":
                host, port_str = self._connection_string.split(":")
                port = int(port_str)
                self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(host, port)
                _LOGGER.info(f"Connected to Nikobus over IP at {host}:{port}")
            elif connection_type == "Serial":
                self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url=self._connection_string, baudrate=9600)
                _LOGGER.info(f"Connected to Nikobus over serial at {self._connection_string}")
            else:
                raise ValueError(f"Invalid Nikobus connection string: {self._connection_string}")
        except Exception as err:
            _LOGGER.error(f"Nikobus connection error with {self._connection_string}: {err}")
            return False
    
        # Load JSON config data and button data
        try:
            await self.load_json_config_data()
            await self.load_json_button_data()
        except Exception as err:
            _LOGGER.error(f"Nikobus configuration file load error - {err}")
            return False
    
        # Perform handshake with Nikobus
        if await self.perform_handshake():
            return True
        else:
            return False

    async def perform_handshake(self):
        """Perform the handshake with the Nikobus system."""

        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        try:
            for command in commands:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
                _LOGGER.debug(f"COMMAND {command.encode()}")
        except OSError as err:
            _LOGGER.error(f"Handshake error {command!r} to {self._host}:{self._port} - {err}")
            return False
        return True

    async def listen_for_events(self):
        """Listen for events from the Nikobus system and handle them accordingly."""
        _LOGGER.debug("Nikobus Event Listener started")

        try:
            while True:
                try:
                    data = await asyncio.wait_for(self._nikobus_reader.readuntil(b'\r'), timeout=5)
                    if not data:
                        _LOGGER.warning("Nikobus connection closed")
                        break
                    message = data.decode('utf-8').strip()
                    _LOGGER.debug(f"Listener - Receiving message: {message}")

                    asyncio.create_task(self.handle_message(message))
                    
                except asyncio.TimeoutError:
                    _LOGGER.debug("Listener - Read operation timed out. Waiting for next data...")
        except asyncio.CancelledError:
            _LOGGER.info("Event listener was cancelled.")
        except Exception as e:
            _LOGGER.error(f"Error in event listener: {e}", exc_info=True)

    async def handle_message(self, message):
        """Handle incoming messages from the Nikobus system."""
        _LOGGER.debug(f"Handler got message: {message}")

        _button_command_prefix = '#N'
        _ignore_answer = '$0E'

        if message.startswith(_button_command_prefix):
            address = message[2:8]
            _LOGGER.debug(f"Handling button press for address: {address}")

            # Only process the 2nd button press message
            self._button_address_occurrences[address] = self._button_address_occurrences.get(address, 0) + 1

            if self._button_address_occurrences[address] == 2:

                # Skip button press if time between 2 commands < 150ms
                current_time_millis = int(time.time() * 1000)

                if current_time_millis - self._last_nikobus_command_received_timestamp > 150:
                    self._last_nikobus_command_received_timestamp = current_time_millis
                    await self.button_discovery(address)
                    self._button_address_occurrences[address] = 0
                else:
                    _LOGGER.debug("Skipping command processing due to time constraint.")

        elif not message.startswith(_ignore_answer):
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self._response_queue.put(message)

    async def load_json_config_data(self):
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

    async def load_json_button_data(self):
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
    async def refresh_nikobus_data(self):
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
                    # Query the state for each group. asyncio.gather ?
                    group_state = await self.get_output_state_nikobus(address, group) or ""
                    _LOGGER.debug(f'*** State for group {group}: {group_state} address : {address} ***')
                    state += group_state  # Concatenate states from each group.

                self.nikobus_module_states[address] = bytearray.fromhex(state)
                _LOGGER.debug(f'*** {self.nikobus_module_states[address]}')

        return True
        
#### SEND A COMMAND AND GET THE ANSWER
    async def send_command_get_answer(self, command, address, max_attempts=3):
        _LOGGER.debug('Entering send_command_get_answer()')
        _LOGGER.debug(f'Command: {command}, Address: {address}')
        # Define the expected acknowledgment and answer signals based on the command and address.
        _wait_command_ack = '$05' + command[3:5]
        _wait_command_answer = '$1C' + address[2:] + address[:2]
        ack_received = False
        answer_received = False
        state = None

        for attempt in range(max_attempts):
            _LOGGER.debug(f'Attempt {attempt + 1} of {max_attempts}')

            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
            _LOGGER.debug(f'Sent command {command} waiting for ACK: {_wait_command_ack} and ANSWER: {_wait_command_answer}')

            end_time = asyncio.get_event_loop().time() + 10  # Set a 10-second timeout

            while asyncio.get_event_loop().time() < end_time:
                try:
                    timeout = end_time - asyncio.get_event_loop().time()  # Calculate remaining time for dynamic timeout
                    message = await asyncio.wait_for(self._response_queue.get(), timeout=5)
                    _LOGGER.debug(f'Message received: {message}')
                
                    # Check for ACK and answer in the received message.
                    if _wait_command_ack in message and not ack_received:
                        _LOGGER.debug('ACK received.')
                        ack_received = True

                    if _wait_command_answer in message and not answer_received:
                        _LOGGER.debug('Answer received.')
                        # Extract the state information from the message based on the expected format.
                        state = message[message.find(_wait_command_answer) + len(_wait_command_answer) + 2:][:12]
                        answer_received = True

                    if ack_received and answer_received:
                        break  # Break out of the loop if both ACK and answer have been received
                
                except asyncio.TimeoutError:
                    _LOGGER.debug('Timeout waiting for ACK/Answer.')
                    break  # Exit the while loop and potentially retry if the timeout is reached

            if ack_received and answer_received:
                _LOGGER.debug('Both ACK and Answer received successfully.')
                break  # Exit the for loop if both ACK and answer have been received

        # Log the outcome of receiving the expected responses.
        if not ack_received:
            _LOGGER.debug('ACK not received within timeout period after maximum attempts.')
        if not answer_received:
            _LOGGER.debug('Answer not received within timeout period after maximum attempts.')

        return state

#### SEND A COMMAND
    async def send_command(self, command):
        _LOGGER.debug('Entering send_command()')
        _LOGGER.debug(f'Command to send: {command}')
    
        try:
            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
            _LOGGER.debug('Command sent successfully')
            return True
        except Exception as e:
            _LOGGER.error(f'Error sending command: {e}')
            return False
        

#### SET's AND GET's for Nikobus
    async def get_output_state_nikobus(self, address, group):
        """Retrieve the current state of an output based on its address and group."""
        
        _LOGGER.debug(f'Entering get_output_state_nikobus() - Address: {address}, Group: {group}')
    
        if int(group) not in [1, 2]:
            _LOGGER.error(f'Invalid group number: {group}')
            return None
    
        command_code = 0x12 if int(group) == 1 else 0x17
        command = make_pc_link_command(command_code, address)
    
        result = await self.send_command_get_answer(command, address)
        return result

    async def set_output_state_nikobus(self, address, channel, value):
        """Set the state of an output based on its address, channel, and value."""

        _LOGGER.debug(f'Entering set_output_state_nikobus() - Address: {address}, Channel: {channel}, Value: {value}')

        group = calculate_group_number(channel)
        if int(group) not in [1, 2]:
            _LOGGER.error(f'Invalid group number: {group} for channel {channel}')
            return None

        self.set_bytearray_state(address, channel, value)

        command_code = 0x15 if int(group) == 1 else 0x16

        if int(group) == 1:
            values = self.nikobus_module_states[address][:6] + bytearray([0xFF])
        elif int(group) == 2:
            values = self.nikobus_module_states[address][6:12] + bytearray([0xFF])

        command = make_pc_link_command(command_code, address, values)
        await self.queue_command(command)
        
#### QUEUE FOR COMMANDS
    async def queue_command(self, command):
        """Queue a command for execution."""

        _LOGGER.debug(f'Queueing command for execution: {command}')
        await self._command_queue.put(command)

    async def process_commands(self):
        """Continuously process commands from the command queue."""
        while True:
            command = await self._command_queue.get()
            _LOGGER.debug(f'Executing command from queue: {command}')
        
            try:
                result = await self.send_command(command)
                _LOGGER.debug(f'Command executed successfully: {command}')
            except Exception as e:
                _LOGGER.error(f"Failed to execute command '{command}': {e}")
        
            finally:
                self._command_queue.task_done()
            await asyncio.sleep(0.3) 

#### UTILS
    def get_bytearray_state(self, address, channel):
        """Get the state of a specific channel within the Nikobus module states."""
        return self.nikobus_module_states.get(address, bytearray())[channel - 1]

    def set_bytearray_state(self, address, channel, value):
        """Set the state of a specific channel within the Nikobus module states."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module states.')

    def set_bytearray_group_state(self, address, group, value):
        """Update the state of a specific group within the Nikobus module states."""
        byte_value = bytearray.fromhex(value)
        if address in self.nikobus_module_states:
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(f'New value set for array {self.nikobus_module_states[address]}')
        else:
            _LOGGER.error(f'Address {address} not found in Nikobus module states.')

#### SWITCHES
    def get_switch_state(self, address, channel):
        """Get the state of a switch based on its address and channel."""
        return self.get_bytearray_state(address, channel) == 0xFF

    async def turn_on_switch(self, address, channel):
        """Turn on a switch specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0xFF)

    async def turn_off_switch(self, address, channel):
        """Turn off a switch specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0x00)

#### DIMMERS
    def get_light_state(self, address, channel):
        """Get the state of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel) != 0x00
    
    def get_light_brightness(self, address, channel):
        """Get the brightness of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def turn_on_light(self, address, channel, brightness):
        """Turn on a light specified by its address and channel with the given brightness."""
        await self.set_output_state_nikobus(address, channel, brightness)

    async def turn_off_light(self, address, channel):
        """Turn off a light specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0x00)

#### COVERS
    def get_cover_state(self, address, channel):
        """Get the state of a cover based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    async def stop_cover(self, address, channel):
        """Stop a cover specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0x00)

    async def open_cover(self, address, channel):
        """Open a cover specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0x01)

    async def close_cover(self, address, channel):
        """Close a cover specified by its address and channel."""
        await self.set_output_state_nikobus(address, channel, 0x02)

#### BUTTONS
    async def write_json_button_data(self):
        """Write the current state of button configurations to a JSON file asynchronously."""
        button_config_file_path = self._hass.config.path("nikobus_button_config.json")
        async with aiofiles.open(button_config_file_path, 'w') as file:
            await file.write(json.dumps(self.json_button_data, indent=4))
            _LOGGER.debug("Button configuration data successfully written to JSON file.")

    async def button_discovery(self, address):
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

    async def process_button_modules(self, button, address):
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
                await asyncio.sleep(0.3)
                value = await self.get_output_state_nikobus(impacted_module_address, impacted_group)
                self.set_bytearray_group_state(impacted_module_address, impacted_group, value)
            except Exception as e:
                _LOGGER.error(f"Error processing button press for module {impacted_module_address}: {e}")
        await self._async_event_handler("nikobus_button_pressed", address)
