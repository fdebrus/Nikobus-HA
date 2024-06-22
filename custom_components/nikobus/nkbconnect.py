"""Nikobus Connect"""

import logging
import asyncio
import serial_asyncio
import ipaddress
import re

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

BAUD_RATE = 9600
COMMANDS_HANDSHAKE = ["++++", "ATH0", "ATZ", "$10110000B8CF9D", "#L0", "#E0", "#L0", "#E1"]
COMMAND_WITH_ACK = COMMANDS_HANDSHAKE[3]
EXPECTED_HANDSHAKE_RESPONSE = "$0511"
HANDSHAKE_TIMEOUT = 60

class NikobusConnect:
    
    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._connection_type = self._validate_connection_string()
        self._nikobus_reader, self._nikobus_writer = None, None

    async def connect(self):
        try:
            if self._connection_type == "IP":
                await self._connect_ip()
            elif self._connection_type == "Serial":
                await self._connect_serial()
            else:
                _LOGGER.error(f"Invalid connection string: {self._connection_string}")
                return False

            if await self._perform_handshake():
                _LOGGER.info("Nikobus handshake successful")
                return True
            return False
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.error(f"Connection error with {self._connection_string}: {err}")
            return False

    async def _connect_ip(self):
        try:
            host, port_str = self._connection_string.split(":")
            port = int(port_str)
            self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(host, port)
            _LOGGER.info(f"Connected to bridge {host}:{port}")
        except (OSError, ValueError) as err:
            _LOGGER.error(f"Failed to connect to bridge {self._connection_string} - {err}")
            self._nikobus_reader = None
            self._nikobus_writer = None

    async def _connect_serial(self):
        try:
            self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url=self._connection_string, baudrate=BAUD_RATE)
            _LOGGER.info(f"Connected to serial port {self._connection_string}")
        except (OSError, serial_asyncio.SerialException) as err:
            _LOGGER.error(f"Failed to connect to serial port {self._connection_string} - {err}")
            self._nikobus_reader = None
            self._nikobus_writer = None

    def _validate_connection_string(self) -> str:
        try:
            ipaddress.ip_address(self._connection_string.split(':')[0])
            return "IP"
        except ValueError:
            if re.match(r'^/dev/tty(USB|S)\d+$', self._connection_string):
                return "Serial"
        return "Unknown"

    async def _perform_handshake(self) -> bool:
        for command in COMMANDS_HANDSHAKE:
            try:
                await self.send(command)

                # if command == COMMAND_WITH_ACK:
                #        response = await asyncio.wait_for(self._nikobus_reader.readuntil(b'\r'), timeout=HANDSHAKE_TIMEOUT)
                #        if EXPECTED_HANDSHAKE_RESPONSE.encode() not in response:
                #            _LOGGER.error(f"Unexpected handshake response: {response}")
                #            return False

            except asyncio.TimeoutError as err:
                _LOGGER.error(f'Handshake timeout error: cannot communicate with Nikobus on {self._connection_string}')
                return False
            except OSError as err:
                _LOGGER.error(f'Handshake OS error: cannot communicate with Nikobus on {self._connection_string} - {err}')
                return False
            except Exception as e:
                _LOGGER.exception(f'Unhandled exception during handshake: {e}')
                return False
        return True

    async def read(self):
        return await self._nikobus_reader.readuntil(b'\r')

    async def send(self, s: str):
        if not self._nikobus_writer:
            _LOGGER.error("Writer is not available for sending commands.")
            return
        self._nikobus_writer.write(s.encode() + b'\r')
        await self._nikobus_writer.drain()

    async def close(self):
        if self._nikobus_writer:
            self._nikobus_writer.close()
            await self._nikobus_writer.wait_closed()
