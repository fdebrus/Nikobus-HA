import logging
import select
import socket

from .helpers import int_to_hex, hex_to_int, int_to_dec, dec_to_int, calc_crc1, calc_crc2, append_crc1, append_crc2, make_pc_link_command, calculate_group_output_number, calculate_group_number

_LOGGER = logging.getLogger(__name__)

class Nikobus:
    def __init__(self, host: str, port: str) -> None:
        self._host = host
        self._port = int(port)
        self._nikobus_socket = None
        self._answer = None

        self.logger = _LOGGER

    @classmethod
    async def create(cls, host: str, port: str):
        instance = cls(host, port)
        await instance.connect()
        return instance

    async def connect(self):
        _LOGGER.debug("----- Nikobus.connect() enter-----")
        self._nikobus_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        self._nikobus_socket.settimeout(10)
        try:
            self._nikobus_socket.connect((self._host, self._port))
        except OSError as err:
            _LOGGER.error(
                "Unable to connect to %s on port %s: %s",
                self._host,
                self._port,
                err,
            )
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
        for command in commands:
            try:
                self._nikobus_socket.send(command.encode())
            except OSError as err:
                _LOGGER.error(
                    "Unable to send payload %r to %s on port %s: %s",
                    command,
                    self._host,
                    self._port,
                    err,
                )
                return
        readable, _, _ = select.select([self._nikobus_socket], [], [], 10)
        if not readable:
            _LOGGER.warning(
                (
                    "Timeout (%s second(s)) waiting for a response after "
                    "%s on port %s"
                ),
                5,
                self._host,
                self._port,
            )
            return
        self._answer = self._nikobus_socket.recv(28).decode('utf-8').rstrip()

    async def send_command(self, command):
        self.logger.debug('----- Nikobus.send_command() enter -----')
        self.logger.debug(f'command = {command}')
        try:
            self._nikobus_socket.send(command.encode() + b'\r')
        except Exception as err:
            self.logger.debug('SerialPort.write() %s', err)
        self.logger.debug('Nikobus.sendCommand() leave')

    async def send_command_get_answer(self, command, timeout):
        _LOGGER.debug('----- Nikobus.send_command_get_answer() enter -----')
        _LOGGER.debug(f'command = {command}, timeout = {timeout}')
        try:
            _wait_command_ack = '$05' + command[3:5]
            received_data = []
            self._nikobus_socket.send(command.encode() + b'\r')
            while True:
                readable, _, _ = select.select([self._nikobus_socket], [], [], 3)
                if not readable:
                    # Timeout occurred
                    break
                data = self._nikobus_socket.recv(64).decode()
                if not data:
                    # No more data
                    break
                received_data.append(data)
            combined_data = ''.join(received_data)
            received_data_split = re.split(r'(?=\$)', combined_data)
            _LOGGER.debug(f"Received response: {received_data_split}")
            for index, data in enumerate(received_data_split):
                if _wait_command_ack in data:
                    _LOGGER.debug(f"Found ACK at index {index}: {data}")
                    if index < len(received_data_split) - 1:
                        next_data_index = index + 1
                        next_data = received_data_split[next_data_index]
                        _LOGGER.debug(f"Posting as answer {index + 1} {next_data}")
                        _answer = next_data[6:18]
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
            cmd = make_pc_link_command(0x15, address, value)
        elif group == 2:
            cmd = make_pc_link_command(0x16, address, value)
        else:
            raise ValueError("Invalid group number")
        _LOGGER.debug('SET OUTPUT STATE command %s',cmd)
        await self.send_command(cmd)

    async def turn_on_switch(self, address, channel):
        channel += 1
        _LOGGER.debug('TURNON address %s channel %s', address, channel)
        group_number = calculate_group_number(channel)
        group_output_number = calculate_group_output_number(channel)
        _LOGGER.debug('TURNON group_number %s group_output_number %s', group_number, group_output_number)
        old_value = await self.get_output_state(address, group_number, timeout=5)
        _LOGGER.debug('old old_value %s', old_value)
        if old_value:
            new_value = old_value[:group_output_number * 2] + 'FF' + old_value[(group_output_number + 1) * 2:]
            _LOGGER.debug('TURNON new_value %s', new_value)
            await self.set_output_state(address, group_number, new_value, 5)
        else:
            _LOGGER.error('TURNON Invalid address %s channel %s group_number %s group_output_number %s', address, channel, group_number, group_output_number)







###################################################






    async def switch_get_output_state(self, number, timeout):
        _LOGGER.debug('NikobusModuleSwitch.getOutputState() enter')
        _LOGGER.debug(f'number = {number}, timeout = {timeout}')
        group_number = calculate_group_number(number)
        group_output_number = calculate_group_output_number(number)
        try:
            err, answer = await self.get_output_state(self.address, group_number, timeout)
            _LOGGER.error(f'NikobusApi.getOutputState() (err = %s, answer = %s)', err, answer)
            if err:
                _LOGGER.error('Error %s', err)
                return
            answer = answer[group_output_number * 2:group_output_number * 2 + 2]
            _LOGGER.error('Answer %s', answer)
        except Exception as e:
            _LOGGER.error('Error: %s',e)
        _LOGGER.debug('NikobusModuleSwitch.getOutputState() leave', 9)

    async def switch_set_output_state(self, number, value, timeout):
        _LOGGER.debug('NikobusModuleSwitch.setOutputState() enter')
        _LOGGER.debug(f'number = {number}, value = {value}, timeout = {timeout}')
        group_number = calculate_group_number(number)
        group_output_number = calculate_group_output_number(number)
        try:
            err, answer = await self.get_output_state(self.address, group_number, timeout)
            _LOGGER.debug('NikobusApi.getOutputState() err = %s, answer = %s', err, answer)
            if err:
                _LOGGER.error('err %s', err)
                return
            old_value = answer
            new_value = old_value[:group_output_number * 2] + value + old_value[(group_output_number + 1) * 2:(6 - group_output_number - 1) * 2]
            if old_value == new_value:
                _LOGGER.debug('no need to change output state', 9)
                return
            err, answer = await self.api.set_output_state(self.address, group_number, new_value, timeout)
            _LOGGER.debug('NikobusApi.setOutputState() (err = %s, answer = %s)', err, answer)
            if err:
                _LOGGER.error('error %s', err)
                return
            if answer != 'FF00':
                _LOGGER.error('unexpected answer %s', answer)
                return
        except Exception as e:
            _LOGGER.debug(f'Error: {e}')
        _LOGGER.debug('NikobusModuleSwitch.setOutputState() leave', 9)

    async def switch_get_group_output_state(self, group, timeout):
        _LOGGER.debug('NikobusModuleSwitch.getGroupOutputState() enter', 5)
        _LOGGER.debug(f'group = {group}, timeout = {timeout}', 7)
        try:
            err, answer = await self.get_output_state(self.address, group, timeout)
            _LOGGER.debug('NikobusApi.getOutputState() (err = %s, answer = %s)', err, answer)
        except Exception as e:
            _LOGGER.debug(f'Error: {e}')
        _LOGGER.debug('NikobusModuleSwitch.getGroupOutputState() leave', 9)

    async def switch_set_group_output_state(self, group, value, timeout):
        _LOGGER.debug('NikobusModuleSwitch.setGroupOutputState() enter', 5)
        _LOGGER.debug(f'group = {group}, value = {value}, timeout = {timeout}', 7)
        try:
            err, answer = await self.api.set_output_state(self.address, group, value, timeout)
            _LOGGER.debug(f'NikobusApi.setOutputState() (err = {err}, answer = {answer})')
        except Exception as e:
            _LOGGER.debug(f'Error: {e}')
        _LOGGER.debug('NikobusModuleSwitch.setGroupOutputState() leave', 9)
