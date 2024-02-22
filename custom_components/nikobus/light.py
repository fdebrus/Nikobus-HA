import logging
import select
import asyncio
import re
import os
import json
import textwrap

from .helpers import int_to_hex, hex_to_int, int_to_dec, dec_to_int, calc_crc1, calc_crc2, append_crc1, append_crc2, make_pc_link_command, calculate_group_output_number, calculate_group_number

_LOGGER = logging.getLogger(__name__)

class Nikobus:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self.json_config_data = {}
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None

    @classmethod
    async def create(cls, host: str, port: str):
        instance = cls(host, port)
        await instance.connect()
        return instance

    async def load_json_config_data(self):
        # Open the JSON file and load its contents
        current_file_path = os.path.abspath(__file__)
        current_directory = os.path.dirname(current_file_path)
        config_file_path = os.path.join(current_directory, "nikobus_config.json")
        with open(config_file_path, 'r') as file:
            self.json_config_data = json.load(file)

    async def connect(self):
        _LOGGER.debug("----- Nikobus.connect() enter-----")
        try:
            self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(self._host, self._port)
        except OSError as err:
            _LOGGER.error(f"Unable to connect to {self._host} on port {self._port}: {err}")
            return
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            try:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
            except OSError as err:
                _LOGGER.error(f"Unable to send payload {command!r} to {self._host} on port {self._port}: {err}")
                return
        try:
            self._answer = await asyncio.wait_for(self._nikobus_reader.read(28), timeout=3)
            self._answer = self._answer.decode('utf-8').rstrip()
        except asyncio.TimeoutError:
            _LOGGER.warning(f"Timeout (5 second(s)) waiting for a response after {self._host} on port {self._port}")

    async def refresh_nikobus_data(self):
        result_dict = {} 
        state_group = []
        state_group2 = []
        await self.load_json_config_data()
        for module_type in ['dimmer_modules_addresses', 'switch_modules_addresses', 'roller_modules_addresses']:
            for entry in self.json_config_data[module_type]:
                actual_address = entry.get("address")
                _LOGGER.debug('*** REFRESH for %s ***', actual_address)
                state_group = await self.get_output_state(address=actual_address, group=1)
                _LOGGER.debug("state_group: %s", state_group)       
                if len(entry.get('channels', [])) == 12:
                    state_group2 = await self.get_output_state(address=actual_address, group=2)
                    _LOGGER.debug("state_group2: %s", state_group2)  
                if state_group is not None and state_group2 is not None:
                    state_group += state_group2
                if state_group is not None:
                    state_group_array = {index: item for index, item in enumerate(textwrap.wrap(state_group, width=2))}
                else:
                    return False
                result_dict[actual_address] = state_group_array
        self.json_state_data = result_dict
        _LOGGER.debug("json: %s",self.json_state_data)
        return True 

    async def send_command(self, command):
        _LOGGER.debug('----- Nikobus.send_command() enter -----')
        _LOGGER.debug(f'command = {command}')
        try:
            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
        except Exception as err:
            _LOGGER.debug('SerialPort.write() %s', err)
        _LOGGER.debug('Nikobus.sendCommand() leave')

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
        values = self.json_state_data[address]
        _LOGGER.debug('JSON %s', self.json_state_data)
        _LOGGER.debug('JSON ADDRESS %s', self.json_state_data[address])
        if group_number == 1:
            new_value = ''.join(values[i] for i in range(6))
        elif group_number == 2:
            new_value = ''.join(values[i] for i in range(6, 12))
        _LOGGER.debug('Setting value %s for %s', new_value, address)
        await self.set_output_state(address, group_number, new_value)

#### SWITCHES
    def get_switch_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        if _state == "FF":
            return True
        else:
            return False

    async def turn_on_switch(self, address, channel):
        _LOGGER.debug('CHANNEL %s', channel)
        self.json_state_data.setdefault(address, {})[channel] = 'FF'
        await self.set_value_at_address(address, channel)

    async def turn_off_switch(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

#### DIMMERS
    def get_light_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug("get_light_state: %s %s %s",address, channel, _state)
        if _state == "00":
            return False
        else:
            return True
    
    def get_light_brightness(self, address, channel):
        return int(self.json_state_data.get(address, {}).get(channel),16)

    async def turn_on_light(self, address, channel, brightness):
        self.json_state_data.setdefault(address, {})[channel] = format(brightness, '02X')
        await self.set_value_at_address(address, channel)

    async def turn_off_light(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

""" 
    COVERS
    async def open_cover(self, address, channel):

    async def close_cover(self, address, channel):

    async def stop_cover(self, address, channel):

    async def get_cover_state(self, address, channel):
"""
