import logging
import select
import asyncio
import ipaddress
import re
import os
import json
import textwrap
from pathlib import Path
import aiofiles

from .const import DOMAIN
UPDATE_SIGNAL = "update_signal"

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
    def __init__(self, hass, host, port):
        self._hass = hass
        self._host = host
        self._port = port
        self._response_queue = asyncio.Queue()
        self._event_listener_task = None
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None
        self.json_config_data = {}
        self.json_state_data = {}
        self.json_button_data = {}
        self._nikobus_writer_lock = asyncio.Lock()
        self._command_queue = asyncio.Queue()
        self._managing_button = False

    @classmethod
    async def create(cls, hass, host: str, port: str):
        # Instantiate the class with the provided host and port arguments
        instance = cls(hass, host, port)
        # Await the connection establishment
        await instance.connect()
        # Return the instantiated and connected instance
        return instance

    async def load_json_config_data(self):
        # Get the path to the JSON config file
        config_file_path = self._hass.config.path("nikobus_config.json")
        # Read the file asynchronously
        async with aiofiles.open(config_file_path, mode='r') as file:
            # Load JSON data
            self.json_config_data = json.loads(await file.read())

#### CONNECT NIKOBUS
    async def connect(self):
        
        def validate_string(input_string):
            try:
                ipaddress.ip_address(input_string)
                return "IP"
            except ValueError:
                pass
            serial_pattern = re.compile(r'^/dev/tty(USB|S)\d+$')
            if serial_pattern.match(input_string):
                return "Serial"
            return "Unknown"

        result = validate_string(self._host)

        try:
            if result == "IP":
                self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(self._host, self._port)
                connection_info = f"{self._host}:{self._port}"
            elif result == "Serial":
                self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url='/dev/ttyUSB0', baudrate=9600)
                connection_info = {self._host}
            else:
                _LOGGER.error(f"Invalid connection string: {self.connection_string}")
                return
            self._event_listener_task = asyncio.create_task(self.listen_for_events())
        except OSError as err:
            _LOGGER.error(f"Connection error to {self._host}:{self._port} - {err}")
            return
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            try:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
            except OSError as err:
                _LOGGER.error(f"Send error {command!r} to {self._host}:{self._port} - {err}")
                return
        try:
            raw_response = await asyncio.wait_for(self._response_queue.get(), timeout=5)
            _LOGGER.error(f"Connection status {raw_response}")
        except asyncio.TimeoutError:
            _LOGGER.warning(f"Timeout waiting for response from {self._host}:{self._port}")
        # Load configuration and button data
        await self.load_json_config_data()
        await self.load_json_button_data()

#### REFRESH DATA FROM THE NIKOBUS
    async def refresh_nikobus_data(self, specific_address=None, specific_group=None):
        result_dict = {}
        # Process each module based on the filtered criteria
        for module_type, entries in self.json_config_data.items():
            for entry in entries:
                address = entry.get("address")
                # Skip entries that do not match the specific_address, if provided
                if specific_address and address != specific_address:
                    continue
                _LOGGER.debug(f'Refreshing data for module address {address}')
                state = ""
                # Determine the number of groups to query
                channel_count = len(entry.get("channels", []))
                groups_to_query = [1] if channel_count <= 6 else [1, 2]
                # Limit to specific_group if provided
                if specific_group:
                    groups_to_query = [specific_group]
                for group in groups_to_query:
                    group_state = await self.get_output_state(address=address, group=group) or ""
                    _LOGGER.debug(f'*** State for group {group}: {group_state}')
                    state += group_state  # Corrected line to concatenate group states

                # If specific_group is defined, merge with existing state data
                if specific_group and address in self.json_state_data:
                    existing_state = self.json_state_data[address]
                    _LOGGER.debug(f'*** Existing state {existing_state}')
                    # Define start and end indexes based on specific_group
                    if int(specific_group) == 1:
                        start_index, end_index = 1, 6
                    elif int(specific_group) == 2:
                        start_index, end_index = 7, 12
                    else:
                        _LOGGER.warning(f'Invalid specific_group {specific_group} provided.')
                        continue  # Continue with the next entry
                
                    # Update only the specified range
                    new_state = {index + start_index - 1: state[i:i + 2] for index, i in enumerate(range(0, len(state), 2), start=start_index) if index + start_index - 1 <= end_index}
                    # Merge and update the state
                    merged_state = {**existing_state, **new_state}
                    self.json_state_data[address] = merged_state
                    _LOGGER.debug(f'*** Updated state {self.json_state_data[address]}')
                elif state:
                    # If no specific_group, or address not in existing data, create a new state dict
                    state_dict = {index + 1: state[i:i + 2] for index, i in enumerate(range(0, len(state), 2))}
                    result_dict[address] = state_dict
                else:
                    _LOGGER.warning(f'No state data received for module address {address}. Skipping state dictionary creation.')

        # Update the entire json_state_data if no specific address or group is provided, or add to it
        self.json_state_data.update(result_dict)
        _LOGGER.debug(f'JSON state data: {self.json_state_data}')
        return True

#### SEND A COMMAND AND GET THE ANSWER
    async def send_command_get_answer(self, command, address, max_attempts=3):
        _LOGGER.debug('----- Entering send_command_get_answer() -----')
        _LOGGER.debug(f'*** Command: {command} Address: {address}')
        _wait_command_ack = '$05' + command[3:5]
        _wait_command_answer = '$1C' + address[2:] + address[:2]
        ack_received = False
        answer_received = False
        state = None

        for attempt in range(max_attempts):
            _LOGGER.debug(f'Attempt {attempt + 1} of {max_attempts}')

            async with self._nikobus_writer_lock:
                self._nikobus_writer.write(command.encode() + b'\r')
                await self._nikobus_writer.drain()
            _LOGGER.debug(f'*** Sent command, waiting for ACK: {_wait_command_ack} and ANSWER: {_wait_command_answer}')

            end_time = asyncio.get_event_loop().time() + 10  # Set timeout for 10 seconds from now

            while asyncio.get_event_loop().time() < end_time:
                try:
                    # Calculate remaining time to adjust timeout dynamically
                    timeout = end_time - asyncio.get_event_loop().time()
                    message = await asyncio.wait_for(self._response_queue.get(), timeout=max(timeout, 0.1))
                    _LOGGER.debug(f'*** Message in queue: {message}')
                
                    # Check for ACK and answer in the message
                    if _wait_command_ack in message and not ack_received:
                        _LOGGER.debug(f'*** ACK received: {_wait_command_ack}')
                        ack_received = True

                    if _wait_command_answer in message and not answer_received:
                        _LOGGER.debug(f'*** ANSWER received: {_wait_command_answer}')
                        state = message[message.find(_wait_command_answer) + len(_wait_command_answer) + 2:][:12]
                        answer_received = True

                    if ack_received and answer_received:
                        break  # Exit loop if both ACK and answer have been received
                
                except asyncio.TimeoutError:
                    _LOGGER.debug('Timeout waiting for ACK/Answer.')
                    break  # Exit while loop to retry sending the command if necessary

            if ack_received and answer_received:
                _LOGGER.debug('Both ACK and Answer received successfully.')
                break  # Exit for loop if both ACK and answer have been received

        if not ack_received:
            _LOGGER.debug('ACK not received within timeout period after maximum attempts.')
        if not answer_received:
            _LOGGER.debug('Answer not received within timeout period after maximum attempts.')

        return state

#### SEND A COMMAND
    async def send_command(self, command):
        _LOGGER.debug('----- Entering send_command -----')
        _LOGGER.debug(f'*** Command: {command}')
        async with self._nikobus_writer_lock:
            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
        return None

#### SET's AND GET's
    async def get_output_state(self, address, group):
        _LOGGER.debug('----- NikobusApi.get_output_state() enter -----')
        _LOGGER.debug(f'*** address = {address}, group = {group}')
        if int(group) in [1, 2]:
            command_code = 0x12 if int(group) == 1 else 0x17
            command = make_pc_link_command(command_code, address)
        else:
            _LOGGER.error(f'get_output_state - Invalid group number {group}')
        result = await self.send_command_get_answer(command, address)
        return result

    async def set_output_state(self, address, group_number, value):
        _LOGGER.debug('----- NikobusApi.setOutputState() enter -----')
        _LOGGER.debug(f'*** address = {address}, group = {group_number}, value = {value}')
        if int(group_number) in [1, 2]:
            command_code = 0x15 if int(group_number) == 1 else 0x16
            command = make_pc_link_command(command_code, address, value + 'FF')
        else:
            _LOGGER.error(f'set_out_state - Invalid group number {group_number}')
        _LOGGER.debug(f'*** set_out_state command {command}')
        await self.queue_command(command)

    async def set_value_at_address(self, address, channel):
        group_number = calculate_group_number(channel)
        values = self.json_state_data[address]
        _LOGGER.debug(f'*** set_value_at_address: new json {self.json_state_data[address]} for address {address}')
        start_index = 1 if group_number == 1 else 7
        new_value = ''.join(values[i] for i in range(start_index, start_index + 6))
        _LOGGER.debug(f'*** Setting value {new_value} for {address} {channel}')
        await self.set_output_state(address, group_number, new_value)

    async def set_value_at_address_shutter(self, address, channel, value):
        state = self.json_state_data[address]
        current_state = "".join(str(value) for value in self.json_state_data[address].values()) 
        _LOGGER.debug(f'*** Current states {current_state} for {address}')
        new_value = current_state[:(channel-1)*2] + value + current_state[(channel-1)*2+2:]
        _LOGGER.debug(f'*** Shutters - Setting value {new_value} for {address}')
        await self.set_output_state(address, 1, new_value)

#### QUEUE FOR COMMANDS
    async def queue_command(self, command):
        _LOGGER.debug(f'*** command in queue {command}')
        await self._command_queue.put((command))

    async def process_commands(self):
        while True:
            command = await self._command_queue.get()
            _LOGGER.debug(f'*** command task execute from queue {command}')
            try:
                result = await self.send_command(command)
            except Exception as e:
                _LOGGER.debug(f"*** Command task failed to execute command: {e}")
            self._command_queue.task_done()

#### LISTENER FOR NIKOBUS EVENTS
    async def listen_for_events(self):
        _LOGGER.debug("*** Nikobus Event Listener started")
        try:
            while True:
                try:
                    data = await asyncio.wait_for(self._nikobus_reader.readuntil(b'\r'), timeout=5)
                    if not data:
                        _LOGGER.warning("Nikobus connection closed")
                        break
                    _LOGGER.debug(f"*** Listener - Receiving RAW message: {data}")
                    # Decode and append new data to buffer
                    message = data.decode('utf-8').strip()
                    _LOGGER.debug(f"*** Listener - Receiving message: {message}")
                    await self.handle_message(message)
                except asyncio.TimeoutError:
                    _LOGGER.debug("*** Listener - Read operation timed out. Waiting for next data...")
        except asyncio.CancelledError:
            _LOGGER.info("Event listener was cancelled.")
        except Exception as e:
            _LOGGER.error(f"Error in event listener: {e}", exc_info=True)

    async def handle_message(self, message):
        _button_command_prefix = '#N'
        _ignore_answer = '$0E'
        if message.startswith(_button_command_prefix) and not self._managing_button:
            self._managing_button = True
            address = message[2:8]
            await self.button_discovery(address)
        elif not message.startswith(_button_command_prefix) and not message.startswith(_ignore_answer):
            _LOGGER.debug(f"*** Sending to queue - message: {message}")
            await self._response_queue.put(message)

#### UTILS
    async def update_json_state(self, address, channel, value):
        """Update the status in the json_state."""
        self.json_state_data.setdefault(address, {})[channel] = value
####

#### SWITCHES
    def get_switch_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        return _state == "FF"

    async def turn_on_switch(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = 'FF'
        await self.set_value_at_address(address, channel)

    async def turn_off_switch(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)

#### DIMMERS
    def get_light_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        return _state != "00"
    
    def get_light_brightness(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        return int(_state,16)

    async def turn_on_light(self, address, channel, brightness):
        self.json_state_data.setdefault(address, {})[channel] = format(brightness, '02X')
        await self.set_value_at_address(address, channel)

    async def turn_off_light(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)

#### COVERS
    async def stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.update_json_state(address, channel, '00')
        await self.set_value_at_address_shutter(address, channel, '00')

    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.update_json_state(address, channel, '01')
        await self.set_value_at_address_shutter(address, channel, '01')

    async def close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.update_json_state(address, channel, '02')
        await self.set_value_at_address_shutter(address, channel, '02')

    async def button_press_cover(self, address, impacted_group, cover_command):
        """Handle button press from Nikobus system for cover"""
        await async_dispatcher_send(self._hass, f"nikobus_cover_update_{address}{impacted_group}", {'command': cover_command})

#### BUTTONS
    async def load_json_button_data(self):
        # Define the JSON config file path
        config_file_path = self._hass.config.path("nikobus_button_config.json")
        # Asynchronously read and load JSON data
        async with aiofiles.open(config_file_path, 'r') as file:
            self.json_button_data = json.loads(await file.read())

    async def write_json_button_data(self):
        # Path to the JSON button config file
        button_config_file_path = self._hass.config.path("nikobus_button_config.json")
        # Asynchronously write updated JSON data
        async with aiofiles.open(button_config_file_path, 'w') as file:
            await file.write(json.dumps(self.json_button_data, indent=4))

    async def send_button_press(self, address) -> None:
        await self.queue_command(f'#N{address}\r#E1')

    async def button_discovery(self, address):
        _LOGGER.debug(f"*** Discovering button at {address}")
        # Search for the button in the existing configuration
        for button in self.json_button_data.get('nikobus_button', []):
            if button['address'] != address:
                continue
            # Handle the button press and send an event
            self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})
            await self.process_button_modules(button)
            return
        # If the button was not found, add a new configuration
        _LOGGER.warning(f"No configuration found for button {address}. Adding new configuration.")
        new_button = {
            "description": f"Nikobus Button #N{address}",
            "address": address,
            "impacted_module": [{"address": "", "group": ""}]
        }
        self.json_button_data["nikobus_button"].append(new_button)
        await self.write_json_button_data()
        self._managing_button = False
        _LOGGER.debug(f'*** New button configuration added: {new_button}')

    async def process_button_modules(self, button):
        """Process each module impacted by a button press."""
        button_description = button.get('description')
        _LOGGER.debug(f'*** Received {button_description} press')
        for module in button.get('impacted_module', []):
            impacted_module_address = module.get('address')
            impacted_group = module.get('group')
            if not (impacted_module_address and impacted_group):
                continue
            try: 
                if 'command' in module:
                    # WIP FOR COVERS 
                    # self.button_press_cover(impacted_module_address, impacted_group, module['command'])
                    pass
                else:
                    _LOGGER.debug(f'*** Refreshing status for module {impacted_module_address} for group {impacted_group}')
                    await self.refresh_nikobus_data(impacted_module_address, impacted_group)
                    await self.refresh_entities(impacted_module_address, impacted_group)
                    self._managing_button = False
            except Exception as e:
                _LOGGER.error(f'Error handling button press for address {impacted_module_address}: {e}')

    async def refresh_entities(self, impacted_module_address, impacted_group):
        if int(impacted_group) == 1:
            values_range = range(1, 7)
        elif int(impacted_group) == 2:
            values_range = range(7, 13)
        for value in values_range:
            _LOGGER.debug(f"*** Sending refresh request on {UPDATE_SIGNAL}_{impacted_module_address}{value}")
            async_dispatcher_send(self._hass, f"{UPDATE_SIGNAL}_{impacted_module_address}{value}")
