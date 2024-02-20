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

    async def send_command_get_answer(self, command, timeout):
        _LOGGER.debug('----- Nikobus.send_command_get_answer() enter -----')
        _LOGGER.debug(f'command = {command}, timeout = {timeout}')
        _wait_command_ack = '$05' + command[3:5]
        try:
            received_data=[]
            self._nikobus_socket.send(command.encode() + b'\r')
            while True:
                readable, _, _ = select.select([self._nikobus_socket], [], [], 2)
                if not readable:
                    # Timeout occurred
                    break
                data = self._nikobus_socket.recv(28).decode('utf-8')
                if not data:
                    # No more data
                    break
                received_data.append(data)
            _LOGGER.debug("Received response: ACK %s vs %s with %s", received_data[0], _wait_command_ack, received_data[1] )
            if received_data and received_data[0] == _wait_command_ack:
                # check pc-link checksum
                _answer = received_data[1]
                crc1 = hex_to_int(_answer[25:])
                crc2 = calc_crc2(_answer[:25])
                if (crc1 != crc2):
                    _LOGGER.error("Checksum error step 1")
                _answer = _answer[3:25]
                # check pc-link checksum
                crc1 = hex_to_int(_answer[-4:])
                crc2 = calc_crc1(_answer[:-4])
                if (crc1 != crc2):
                    _LOGGER.error("Checksum error step 2")
                _answer = _answer[6:18]
                _LOGGER.debug("Final response: '%s'", _answer)
                return _answer
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
        _LOGGER.debug('NikobusApi.setOutputState() enter')
        _LOGGER.debug(f'address = {address}, group = {group}, value = {value}, timeout = {timeout}')
        if group == 1:
            cmd = make_pc_link_command(0x15, address, value + 'FF')
        elif group == 2:
            cmd = make_pc_link_command(0x16, address, value + 'FF')
        else:
            raise ValueError("Invalid group number")
        await self.send_command_get_answer(cmd, timeout)

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
