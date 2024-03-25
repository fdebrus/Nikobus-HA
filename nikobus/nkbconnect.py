import logging
import asyncio
import serial_asyncio
import ipaddress
import re

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

# Version-access:
def get_version():
    return __version__

class NikobusConnect:
    
    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._connection_type = self.validate_connection_string()
        self._nikobus_reader, self._nikobus_writer = None, None

    async def connect(self):
        try:
            if self._connection_type == "IP":
                host, port_str = self._connection_string.split(":")
                port = int(port_str)
                self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(host, port)
                _LOGGER.info(f"Connected to Nikobus over IP at {host}:{port}")
            elif self._connection_type == "Serial":
                self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(url=self._connection_string, baudrate=9600)
                _LOGGER.info(f"Connected to Nikobus over serial at {self._connection_string}")
            else:
                raise ValueError(f"Invalid Nikobus connection string: {self._connection_string}")
        except Exception as err:
            _LOGGER.error(f"Nikobus connection error with {self._connection_string}: {err}")
            return False

        handshake = await self.perform_handshake()
        return handshake

    def validate_connection_string(self) -> str:
        try:
            ipaddress.ip_address(self._connection_string.split(':')[0])
            return "IP"
        except ValueError:
            pass
        if re.match(r'^/dev/tty(USB|S)\d+$', self._connection_string):
            return "Serial"
        return "Unknown"

    async def perform_handshake(self) -> bool:
        commands = ["++++", "ATH0", "ATZ", "$10110000B8CF9D", "#L0", "#E0", "#L0", "#E1"]
        try:
            for command in commands:
                await self.send(command)
                _LOGGER.debug(f"COMMAND {command}")
        except OSError as err:
            _LOGGER.error(f"Handshake error '{command}' to {self._connection_string} - {err}")
            return False
        return True

    async def read(self):
        return await self._nikobus_reader.readuntil(b'\r')

    async def send(self, s: str):
        self._nikobus_writer.write(s.encode() + b'\r')
        await self._nikobus_writer.drain()

    async def close(self):
        self._nikobus_writer.close()
        await self._nikobus_writer.wait_closed()
