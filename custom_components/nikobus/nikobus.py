import logging
import select
import asyncio
import time
import serial_asyncio
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
    int_to_hex, 
    hex_to_int, 
    int_to_dec, 
    dec_to_int, 
    calc_crc1, 
    calc_crc2, 
    append_crc1, 
    append_crc2, 
    make_pc_link_command, 
    calculate_group_output_number, 
    calculate_group_number
)

_LOGGER = logging.getLogger(__name__)

__title__ = "Nikobus"
__version__ = "2024.2.x"
__author__ = "Frederic Debrus"
__license__ = "MIT"

class Nikobus:
    def __init__(self, hass, connection_string, async_event_handler):
        self._hass = hass
        self.connection_string = connection_string
        self._async_event_handler = async_event_handler
        self._response_queue = asyncio.Queue()
        self._event_listener_task = None
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None
        self.json_config_data = {}
        self.json_state_data = {}
        self.json_button_data = {}
        self._command_queue = asyncio.Queue()

    @classmethod
    async def create(cls, hass, connection_string, async_event_handler):
        _LOGGER.debug(f"Creating NikobusSystem instance with connection string: {connection_string}")
        # Instantiate the class with the provided Home Assistant instance and connection string.
        instance = cls(hass, connection_string, async_event_handler)
        # Await the connection establishment to the Nikobus system.
        await instance.connect()
        _LOGGER.info("NikobusSystem instance created and connected successfully.")
        # Return the instantiated and connected instance.
        return instance

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

        connection_type = validate_string(self.connection_string)

        try:
            if connection_type == "IP":
                host, port_str = self.connection_string.split(":")
                port = int(port_str)
                self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(host, port)
                _LOGGER.info(f"Connected to Nikobus over IP at {host}:{port}")
            elif connection_type == "Serial":
                self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url=self.connection_string, baudrate=9600)
                _LOGGER.info(f"Connected to Nikobus over serial at {self.connection_string}")
            else:
                _LOGGER.error(f"Invalid Nikobus connection string: {self.connection_string}")
                return False
        except Exception as err:
            _LOGGER.error(f"Connection error with {self.connection_string}: {err}")
            return False

        # Start listening for events
        self._event_listener_task = asyncio.create_task(self.listen_for_events())

        # Load JSON config data and button data
        await self.load_json_config_data()
        _LOGGER.debug(f"JSON CONFIG {self.json_config_data}")
        await self.load_json_button_data()

        # Perform handshake with Nikobus
        await self.perform_handshake()

        return True

    async def perform_handshake(self):
        """Perform the handshake with the Nikobus system."""
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            try:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
                _LOGGER.debug(f"COMMAND {command.encode()}")
            except OSError as err:
                _LOGGER.error(f"Send error {command!r} to {self._host}:{self._port} - {err}")
                return

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
                    # await self.handle_message(message)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Listener - Read operation timed out. Waiting for next data...")
        except asyncio.CancelledError:
            _LOGGER.info("Event listener was cancelled.")
        except Exception as e:
            _LOGGER.error(f"Error in event listener: {e}", exc_info=True)

    async def handle_message(self, message):
        """Handle incoming messages from the Nikobus system."""
        _LOGGER.debug(f"GOT MESSAGE : {message}")
        _button_command_prefix = '#N'
        _ignore_answer = '$0E'
        if message.startswith(_button_command_prefix):
            await asyncio.sleep(0.4)
            address = message[2:8]
            _LOGGER.debug(f"Handling button press for address: {address}")
            await self.button_discovery(address)
        elif not message.startswith(_button_command_prefix) and not message.startswith(_ignore_answer):
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self._response_queue.put(message)

    async def load_json_config_data(self):
        config_file_path = self._hass.config.path("nikobus_config.json")
        _LOGGER.debug(f'Loading Nikobus configuration data from {config_file_path}')
        try:
            async with aiofiles.open(config_file_path, mode='r') as file:
                self.json_config_data = json.loads(await file.read())
            _LOGGER.info('Nikobus configuration data successfully loaded.')
        except Exception as e:
            _LOGGER.error(f'Failed to load Nikobus configuration data: {e}')

    async def load_json_button_data(self):
        config_file_path = self._hass.config.path("nikobus_button_config.json")
        _LOGGER.debug(f'Loading Nikobus button configuration data from {config_file_path}')
        try:
            async with aiofiles.open(config_file_path, 'r') as file:
                self.json_button_data = json.loads(await file.read())
            _LOGGER.info('Nikobus button configuration data successfully loaded.')
        except Exception as e:
            _LOGGER.error(f'Failed to load Nikobus button configuration data: {e}')

#### REFRESH DATA FROM THE NIKOBUS
    async def refresh_nikobus_data(self):
        result_dict = {}

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
                    # Query the state for each group.
                    group_state = await self.get_output_state_nikobus(address=address, group=group) or ""
                    _LOGGER.debug(f'State for group {group}: {group_state} address : {address}')
                    state += group_state  # Concatenate states from each group.

                if state:
                    # Create a new state dictionary.
                    state_dict = {index + 1: state[i:i + 2] for index, i in enumerate(range(0, len(state), 2))}
                    result_dict[address] = state_dict
                else:
                    _LOGGER.warning(f'No state data received for module address: {address}.')

        # Update the entire JSON state data with the new results, if applicable.
        if result_dict:
            self.json_state_data.update(result_dict)
            _LOGGER.debug('JSON state data updated.')
        
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
        self._nikobus_writer.write(command.encode() + b'\r')
        await self._nikobus_writer.drain()
        _LOGGER.debug('Command sent successfully')
        return None

#### SET's AND GET's
    async def get_output_state_nikobus(self, address, group):
        """Retrieve the current state of an output based on its address and group."""
        _LOGGER.debug('Entering get_output_state_nikobus()')
        _LOGGER.debug(f'Address: {address}, Group: {group}')
        if int(group) in [1, 2]:
            command_code = 0x12 if int(group) == 1 else 0x17
            command = make_pc_link_command(command_code, address)
        else:
            _LOGGER.error(f'Invalid group number: {group}')
            return
        result = await self.send_command_get_answer(command, address)
        return result

    async def set_output_state_nikobus(self, address, group_number, value):
        """Set the state of an output based on its address, group number, and desired state value."""
        _LOGGER.debug('Entering set_output_state_nikobus()')
        _LOGGER.debug(f'Address: {address}, Group: {group_number}, Value: {value}')
        if int(group_number) in [1, 2]:
            command_code = 0x15 if int(group_number) == 1 else 0x16
            command = make_pc_link_command(command_code, address, value + 'FF')
        else:
            _LOGGER.error(f'Invalid group number: {group_number}')
            return
        _LOGGER.debug(f'Sending command: {command}')
        await self.queue_command(command)

    async def set_value_at_address(self, address, channel):
        """Set a specific value at an address based on the channel and updated state data."""
        group_number = calculate_group_number(channel)
        values = self.json_state_data[address]
        _LOGGER.debug(f'Updating JSON state for address {address}: {values}')
        start_index = 1 if group_number == 1 else 7
        new_value = ''.join(values[i] for i in range(start_index, start_index + 6))
        _LOGGER.debug(f'Setting new value {new_value} for address {address}, channel {channel}')
        await self.set_output_state_nikobus(address, group_number, new_value)

#### QUEUE FOR COMMANDS
    async def queue_command(self, command):
        _LOGGER.debug(f'Queueing command for execution: {command}')
        await self._command_queue.put(command)

    async def process_commands(self):
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

#### UTILS
    async def update_json_state(self, address, channel, value):
        _LOGGER.debug(f"Updating JSON state for address {address}, channel {channel} to value {value}.")
        # Use setdefault to initialize the address key if not present, then update the channel with the new value.
        self.json_state_data.setdefault(address, {})[channel] = value

    async def update_json_group_state(self, address, group, value):
        _LOGGER.debug(f"Updating JSON state for address {address} group {group} to value {value}.")
        start_index, end_index = (1, 6) if int(group) == 1 else (7, 12)
        new_state = {index + start_index - 1: value[i:i + 2] for index, i in enumerate(range(0, len(value), 2), start=start_index) if index + start_index - 1 <= end_index}
        merged_state = {**existing_state, **new_state}
        self.json_state_data[address] = merged_state

#### SWITCHES
    def get_switch_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug(f"Getting switch state for address {address}, channel {channel}: {'on' if _state == 'FF' else 'off'}")
        return _state == "FF"

    async def turn_on_switch(self, address, channel):
        _LOGGER.debug(f"Turning on switch at address {address}, channel {channel}.")
        self.json_state_data.setdefault(address, {})[channel] = 'FF'
        await self.set_value_at_address(address, channel)

    async def turn_off_switch(self, address, channel):
        _LOGGER.debug(f"Turning off switch at address {address}, channel {channel}.")
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)

#### DIMMERS
    def get_light_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug(f"Getting light state for address {address}, channel {channel}: {'on' if _state != '00' else 'off'}")
        return _state != "00"
    
    def get_light_brightness(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug(f"Getting light brightness for address {address}, channel {channel}: {int(_state, 16)}")
        return int(_state, 16)

    async def turn_on_light(self, address, channel, brightness):
        _LOGGER.debug(f"Turning on light at address {address}, channel {channel} to brightness {brightness}.")
        self.json_state_data.setdefault(address, {})[channel] = format(brightness, '02X')
        await self.set_value_at_address(address, channel)

    async def turn_off_light(self, address, channel):
        _LOGGER.debug(f"Turning off light at address {address}, channel {channel}.")
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)

#### COVERS
    def get_cover_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug(f"Getting cover state for address {address}, channel {channel}")
        return _state

    async def stop_cover(self, address, channel) -> None:
        """Stop the movement of a cover."""
        _LOGGER.debug(f"Stopping cover at address {address}, channel {channel}.")
        await self.update_json_state(address, channel, '00')
        await self.set_value_at_address(address, channel)

    async def open_cover(self, address, channel) -> None:
        """Open a cover to its maximum extent."""
        _LOGGER.debug(f"Opening cover at address {address}, channel {channel}.")
        await self.update_json_state(address, channel, '01')
        await self.set_value_at_address(address, channel)

    async def close_cover(self, address, channel) -> None:
        """Close a cover completely."""
        _LOGGER.debug(f"Closing cover at address {address}, channel {channel}.")
        await self.update_json_state(address, channel, '02')
        await self.set_value_at_address(address, channel)

#### BUTTONS
    async def write_json_button_data(self):
        """Write the current state of button configurations to a JSON file asynchronously."""
        button_config_file_path = self._hass.config.path("nikobus_button_config.json")
        async with aiofiles.open(button_config_file_path, 'w') as file:
            await file.write(json.dumps(self.json_button_data, indent=4))
            _LOGGER.debug("Button configuration data successfully written to JSON file.")

    async def send_button_press(self, address) -> None:
        """Simulate a button press by sending the appropriate command to the Nikobus system."""
        _LOGGER.debug(f"Sending button press command for address: {address}")
        await self.queue_command(f'#N{address}\r#E1')

    async def button_discovery(self, address):
        """Discover a button by its address and update configuration if it's new, or process it if it exists."""
        _LOGGER.debug(f"Discovering button at address: {address}")
        for button in self.json_button_data.get('nikobus_button', []):
            if button['address'] == address:
                _LOGGER.debug(f"Button at address {address} found in configuration. Processing...")
                await self._async_event_handler("nikobus_button_pressed", address)
                await self.process_button_modules(button)
                return
        _LOGGER.warning(f"No existing configuration found for button at address {address}. Adding new configuration.")
        new_button = {
            "description": f"Nikobus Button #N{address}",
            "address": address,
            "impacted_module": [{"address": "", "group": ""}]
        }
        self.json_button_data["nikobus_button"].append(new_button)
        await self.write_json_button_data()
        _LOGGER.debug(f"New button configuration added for address {address}.")

    async def process_button_modules(self, button):
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
                value = await self.get_output_state_nikobus(impacted_module_address, impacted_group)
                await self.update_json_group_state(impacted_module_address, impacted_group, value)
                await self._async_event_handler("nikobus_button_pressed", address)
            except Exception as e:
                _LOGGER.error(f"Error processing button press for module {impacted_module_address}: {e}")
