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

class NikobusConnect:
    
    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._connection_type = self._validate_connection_string()
        self._nikobus_reader, self._nikobus_writer = None, None
        self._is_connected = False

    async def connect(self):
        try:
            if self._connection_type == "IP":
                await self._connect_ip()
            elif self._connection_type == "Serial":
                await self._connect_serial()
            else:
                _LOGGER.error(f"Invalid connection string: {self._connection_string}")
                return False
            self._is_connected = True

            if await self._perform_handshake():
                _LOGGER.info("Handshake successful")
                return True
            return False
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.error(f"Connection error with {self._connection_string}: {err}")
            return False

    async def _connect_ip(self):
        host, port_str = self._connection_string.split(":")
        port = int(port_str)
        self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(host, port)
        _LOGGER.info(f"Connected to Nikobus over IP at {host}:{port}")

    async def _connect_serial(self):
        self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url=self._connection_string, baudrate=BAUD_RATE)
        _LOGGER.info(f"Connected to Nikobus over serial at {self._connection_string}")

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
            except OSError as err:
                _LOGGER.error(f"Handshake error '{command}' to {self._connection_string} - {err}")
                self._is_connected = False
                return False
        return True

    async def read(self):
        return await self._nikobus_reader.readuntil(b'\r')

    async def send(self, s: str):
        if not self._is_connected:
            _LOGGER.error("Attempting to send data on a closed or uninitialized connection.")
            return
        self._nikobus_writer.write(s.encode() + b'\r')
        await self._nikobus_writer.drain()

    async def close(self):
        if self._nikobus_writer:
            self._nikobus_writer.close()
            await self._nikobus_writer.wait_closed()
            self._is_connected = False
