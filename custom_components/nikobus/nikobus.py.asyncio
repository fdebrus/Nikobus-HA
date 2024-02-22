import logging
import select
import asyncio
import re

from .helpers import int_to_hex, hex_to_int, int_to_dec, dec_to_int, calc_crc1, calc_crc2, append_crc1, append_crc2, make_pc_link_command, calculate_group_output_number, calculate_group_number

_LOGGER = logging.getLogger(__name__)

class Nikobus:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None

    @classmethod
    async def create(cls, host: str, port: str):
        instance = cls(host, port)
        await instance.connect()
        return instance

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
            self._answer = await asyncio.wait_for(self._nikobus_reader.read(28), timeout=5)
            self._answer = self._answer.decode('utf-8').rstrip()
        except asyncio.TimeoutError:
            _LOGGER.warning(f"Timeout (5 second(s)) waiting for a response after {self._host} on port {self._port}")

    async def send_command(self, command):
        _LOGGER.debug('----- Nikobus.send_command() enter -----')
        _LOGGER.debug(f'command = {command}')
        try:
            self._nikobus_writer.write(command.encode() + b'\r')
            await self._nikobus_writer.drain()
        except Exception as err:
            _LOGGER.debug('SerialPort.write() %s', err)
        _LOGGER.debug('Nikobus.sendCommand() leave')

    async def send_command_get_answer(self, command, timeout):
        _LOGGER.debug('----- Nikobus.send_command_get_answer() enter -----')
        _LOGGER.debug(f'command = {command}, timeout = {timeout}')
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

    async def get_output_state(self, address, group, timeout):
        _LOGGER.debug('----- NikobusApi.get_output_state() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group}, timeout = {timeout}')
        if group == 1:
            cmd = make_pc_link_command(0x12, address)
        elif group == 2:
            cmd = make_pc_link_command(0x17, address)
        else:
            raise ValueError("Invalid group number")
        return await self.send_command_get_answer(cmd, timeout)

    async def set_output_state(self, address, group, value, timeout):
        _LOGGER.debug('----- NikobusApi.setOutputState() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group}, value = {value}, timeout = {timeout}')
        if group == 1:
            cmd = make_pc_link_command(0x15, address, value + 'FF')
        elif group == 2:
            cmd = make_pc_link_command(0x16, address, value + 'FF')
        else:
            raise ValueError("Invalid group number")
        _LOGGER.debug('SET OUTPUT STATE command %s',cmd)
        await self.send_command(cmd)

    async def set_value_at_address(self, address, channel, value):
        channel += 1
        _LOGGER.debug('address %s channel %s', address, channel)
        group_number = calculate_group_number(channel)
        group_output_number = calculate_group_output_number(channel)
        _LOGGER.debug('group_number %s group_output_number %s', group_number, group_output_number)
        old_value = await self.get_output_state(address, group_number, timeout=5)
        _LOGGER.debug('old old_value %s', old_value)
        if old_value:
            new_value = old_value[:group_output_number * 2] + value + old_value[(group_output_number + 1) * 2:]
            _LOGGER.debug('new_value %s', new_value)
            await self.set_output_state(address, group_number, new_value, 5)
        else:
            _LOGGER.error('Invalid address %s channel %s group_number %s group_output_number %s', address, channel, group_number, group_output_number)

#### SWITCHES
    async def turn_on_switch(self, address, channel):
        await self.set_value_at_address(address, channel, 'FF')

    async def turn_off_switch(self, address, channel):
        await self.set_value_at_address(address, channel, '00')
#####

#### DIMMERS
    async def turn_on_light(self, address, channel, brightness):
        await self.set_value_at_address(address, channel, format(brightness, '02X'))

    async def turn_off_light(self, address, channel):
        await self.set_value_at_address(address, channel, '00')
#####

""" 
    COVERS
    async def open_cover(self, address, channel):

    async def close_cover(self, address, channel):

    async def stop_cover(self, address, channel):

    async def get_cover_state(self, address, channel):
"""
