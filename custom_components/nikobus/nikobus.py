import logging
import select
import asyncio
import re
import os
import json
import textwrap
from pathlib import Path
import aiofiles

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

class Nikobus:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._json_config_data = {}
        self._json_state_data = {}
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None
        self._nikobus_writer_lock = asyncio.Lock()

    @classmethod
    async def create(cls, host: str, port: str):
        # Instantiate the class with the provided host and port arguments
        instance = cls(host, port)
        
        # Await the connection establishment
        await instance.connect()
        
        # Return the instantiated and connected instance
        return instance

    async def load_json_config_data(self):
        # Get the path to the JSON config file
        config_file_path = Path(__file__).resolve().parent / "nikobus_config.json"
    
        # Read the file asynchronously
        async with aiofiles.open(config_file_path, mode='r') as file:
            # Load JSON data
            self.json_config_data = json.loads(await file.read())

    async def connect(self):
        _LOGGER.debug("----- Nikobus.connect() enter -----")
        try:
            # Attempt to establish a connection
            self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(self._host, self._port)
        except OSError as err:
            # Handle connection failure
            _LOGGER.error(f"Unable to connect to {self._host} on port {self._port}: {err}")
            return
    
        # Define commands to be sent after connection
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
    
        # Send each command
        for command in commands:
            try:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
            except OSError as err:
                # Handle payload sending failure
                _LOGGER.error(f"Unable to send payload {command!r} to {self._host} on port {self._port}: {err}")
                return

        try:
            # Attempt to read response with a timeout
            self._answer = await asyncio.wait_for(self._nikobus_reader.read(28), timeout=3)
            self._answer = self._answer.decode('utf-8').rstrip()
        except asyncio.TimeoutError:
            # Handle timeout waiting for a response
            _LOGGER.warning(f"Timeout (5 second(s)) waiting for a response after {self._host} on port {self._port}")

    async def refresh_nikobus_data(self):
        # Load JSON configuration data
        await self.load_json_config_data()
    
        # Initialize an empty dictionary to store the result
        result_dict = {}
    
        # Iterate over module types
        for module_type in json_data:
            # Iterate over entries in the current module type
            for entry in self.json_config_data.get(module_type, []):
                # Get the actual address from the entry
                actual_address = entry.get("address")
            
                # Log a debug message indicating refresh for the current address
                _LOGGER.debug('*** Refreshing data for module address %s ***', actual_address)
            
                # Get the output state for group 1
                state_group = await self.get_output_state(address=actual_address, group=1)
                _LOGGER.debug("state_group: %s", state_group)
            
                # If the number of channels is greater than 6, get the output state for group 2 as well
                if len(entry.get('channels', [])) > 6:
                    state_group += await self.get_output_state(address=actual_address, group=2)
                    _LOGGER.debug("state_group2: %s", state_group)
                
                # Split the state group into a dictionary with index as keys and items as values
                state_group_array = {index: item for index, item in enumerate(textwrap.wrap(state_group, width=2))}
            
                # Store the state group array in the result dictionary with the actual address as key
                result_dict[actual_address] = state_group_array
                
        # Update the JSON state data attribute with the result dictionary
        self._json_state_data = result_dict
    
        # Log a debug message indicating the JSON state data
        _LOGGER.debug("json: %s", self._json_state_data)
    
        # Return True to indicate successful refresh
        return True

    async def send_command(self, command):
        try:
            # Acquire the lock to ensure exclusive access to _nikobus_writer
            async with self._nikobus_writer_lock:
                # Write the encoded command and wait for the writer to drain
                self._nikobus_writer.write(command.encode() + b'\r')
                await self._nikobus_writer.drain()
        except Exception as err:
            # Log an error message if any exception occurs during the process
            _LOGGER.error('Error occurred while sending command: %s', err)

    async def send_command_get_answer(self, command):
        _LOGGER.debug('----- Nikobus.send_command_get_answer() enter -----')
        _LOGGER.debug(f'command = {command}')
        try:
            _wait_command_ack = '$05' + command[3:5]
            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
            received_data = b''
            while True:
                try:
                    chunk = await asyncio.wait_for(self._nikobus_reader.read(64), timeout=3)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                received_data += chunk
            combined_data = received_data.decode('utf-8').rstrip()
            received_data_split = re.split(r'(?=\$)', combined_data)
            _LOGGER.debug(f"Received response: {received_data_split}")
            for index, data in enumerate(received_data_split):
                if _wait_command_ack in data:
                    _LOGGER.debug(f"Found ACK at index {index}: {data}")
                    if index < len(received_data_split) - 1:
                        next_data_index = index + 1
                        next_data = received_data_split[next_data_index]
                        _LOGGER.debug(f"Posting as answer {index + 1} {next_data}")
                        _answer = next_data[9:21]
                        _LOGGER.debug(f"Final response: '{_answer}'")
                        return _answer
                    else:
                        _LOGGER.debug(f"No data available after ACK in the same message at index {index}")
                        return None
            _LOGGER.warning("No ACK found in the received data")
            return None
        except Exception as e:
            _LOGGER.error(f"Error during command execution: {e}")

    async def get_output_state(self, address, group):
        _LOGGER.debug('----- NikobusApi.get_output_state() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group}')
        if group == 1:
            cmd = make_pc_link_command(0x12, address)
        elif group == 2:
            cmd = make_pc_link_command(0x17, address)
        else:
            raise ValueError("Invalid group number")
        return await self.send_command_get_answer(cmd)

    async def set_output_state(self, address, group_number, value):
        _LOGGER.debug('----- NikobusApi.setOutputState() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group_number}, value = {value}')
        if group_number == 1:
            cmd = make_pc_link_command(0x15, address, value + 'FF')
        elif group_number == 2:
            cmd = make_pc_link_command(0x16, address, value + 'FF')
        else:
            raise ValueError("Invalid group number")
        _LOGGER.debug('SET OUTPUT STATE command %s',cmd)
        await self.send_command(cmd)

    async def set_value_at_address(self, address, channel):
        channel += 1
        group_number = calculate_group_number(channel)
        group_output_number = calculate_group_output_number(channel)
        values = self._json_state_data[address]
        _LOGGER.debug('JSON %s', self._json_state_data)
        _LOGGER.debug('JSON ADDRESS %s', self._json_state_data[address])
        if group_number == 1:
            new_value = ''.join(values[i] for i in range(6))
        elif group_number == 2:
            new_value = ''.join(values[i] for i in range(6, 12))
        _LOGGER.debug('Setting value %s for %s', new_value, address)
        await self.set_output_state(address, group_number, new_value)

    async def set_value_at_address_shutter(self, address, channel, value):
        group_number = 1
        original_string = '000000000000'
        new_value = original_string[:channel*2] + value + original_string[channel*2:-2]
        _LOGGER.debug('Shutters - Setting value %s for %s', new_value, address)
        await self.set_output_state(address, group_number, new_value)

#### SWITCHES
    def get_switch_state(self, address, channel):
        _state = self._json_state_data.get(address, {}).get(channel)
        if _state == "FF":
            return True
        else:
            return False

    async def turn_on_switch(self, address, channel):
        _LOGGER.debug('CHANNEL %s', channel)
        self._json_state_data.setdefault(address, {})[channel] = 'FF'
        await self.set_value_at_address(address, channel)

    async def turn_off_switch(self, address, channel):
        self._json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

#### DIMMERS
    def get_light_state(self, address, channel):
        _state = self._json_state_data.get(address, {}).get(channel)
        _LOGGER.debug("get_light_state: %s %s %s",address, channel, _state)
        if _state == "00":
            return False
        else:
            return True
    
    def get_light_brightness(self, address, channel):
        return int(self._json_state_data.get(address, {}).get(channel),16)

    async def turn_on_light(self, address, channel, brightness):
        self._json_state_data.setdefault(address, {})[channel] = format(brightness, '02X')
        await self.set_value_at_address(address, channel)

    async def turn_off_light(self, address, channel):
        self._json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

#### COVERS
    async def stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.set_value_at_address_shutter(address, channel, '00')

    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.set_value_at_address_shutter(address, channel, '01')

    async def close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.set_value_at_address_shutter(address, channel, '02')
#####
