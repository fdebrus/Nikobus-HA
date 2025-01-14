import logging
import asyncio
import serial_asyncio
import ipaddress
import re

from .const import BAUD_RATE, COMMANDS_HANDSHAKE
from .exceptions import (
    NikobusSendError,
    NikobusConnectionError,
    NikobusReadError,
)

_LOGGER = logging.getLogger(__name__)

__version__ = "1.0"


class NikobusConnect:
    """Manages connection to a Nikobus system via IP or Serial."""

    def __init__(self, connection_string: str):
        """Initialize the connection handler with the given connection string."""
        self._connection_string = connection_string
        self._connection_type = self._validate_connection_string()
        self._nikobus_reader = None
        self._nikobus_writer = None

    async def connect(self):
        """Connect to the Nikobus system using the connection string."""
        _LOGGER.info(f"Attempting to connect using {self._connection_string}")

        if self._connection_type == "IP":
            await self._connect_ip()
        elif self._connection_type == "Serial":
            await self._connect_serial()
        else:
            error_msg = f"Invalid connection string: {self._connection_string}"
            _LOGGER.error(error_msg)
            raise NikobusConnectionError(error_msg)

        if not await self._perform_handshake():
            error_msg = "Handshake failed, terminating connection."
            _LOGGER.error(error_msg)
            raise NikobusConnectionError(error_msg)

        _LOGGER.info("Nikobus handshake successful, connection established.")

    async def _connect_ip(self):
        """Establish an IP connection to the Nikobus system."""
        try:
            host, port_str = self._connection_string.split(":")
            port = int(port_str)
            self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(
                host, port
            )
            _LOGGER.info(f"Connected to Nikobus bridge at {host}:{port}")
        except (OSError, ValueError) as err:
            error_msg = f"Failed to connect to Nikobus bridge {self._connection_string}: {err}"
            _LOGGER.error(error_msg)
            self._nikobus_reader = None
            self._nikobus_writer = None
            raise NikobusConnectionError(error_msg)

    async def _connect_serial(self):
        """Establish a serial connection to the Nikobus system."""
        try:
            self._nikobus_reader, self._nikobus_writer = await serial_asyncio.open_serial_connection(
                url=self._connection_string, baudrate=BAUD_RATE
            )
            _LOGGER.info(f"Connected to Nikobus serial port {self._connection_string}")
        except (OSError, serial_asyncio.SerialException) as err:
            error_msg = f"Failed to connect to serial port {self._connection_string}: {err}"
            _LOGGER.error(error_msg)
            self._nikobus_reader = None
            self._nikobus_writer = None
            raise NikobusConnectionError(error_msg)

    def _validate_connection_string(self) -> str:
        """Validate the connection string to determine the type (IP or Serial)."""
        try:
            ipaddress.ip_address(self._connection_string.split(":")[0])
            return "IP"
        except ValueError:
            serial_regex = r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$"
            if re.match(serial_regex, self._connection_string):
                return "Serial"
        return "Unknown"

    async def _perform_handshake(self) -> bool:
        """Perform a handshake with the Nikobus system to verify the connection."""
        _LOGGER.info("Performing Nikobus handshake...")
        for command in COMMANDS_HANDSHAKE:
            _LOGGER.debug(f"Sending handshake command: {command}")
            if not await self._send_with_retry(command):
                return False
        return True

    async def _send_with_retry(self, command: str) -> bool:
        """Send a command and handle potential errors with retries."""
        try:
            await self.send(command)
            return True
        except NikobusSendError as e:
            _LOGGER.error(f"Failed to send command during handshake: {e}")
            return False
        except (asyncio.TimeoutError, OSError) as err:
            _LOGGER.error(f"Error during send command: {err}")
            return False
        except Exception as e:
            _LOGGER.exception(f"Unhandled exception during send command: {e}")
            return False

    async def read(self):
        """Read data from the Nikobus system."""
        if not self._nikobus_reader:
            error_msg = "Attempted to read but no reader is available."
            _LOGGER.error(error_msg)
            raise NikobusReadError(error_msg)

        try:
            response = await self._nikobus_reader.readuntil(b"\r")
            _LOGGER.debug(f"Received data: {response.decode().strip()}")
            return response
        except Exception as e:
            _LOGGER.error(f"Failed to read data: {e}")
            raise NikobusReadError(f"Failed to read data: {e}")

    async def send(self, command: str):
        """Send data to the Nikobus system."""
        if not self._nikobus_writer:
            error_msg = "Attempted to send but no writer is available."
            _LOGGER.error(error_msg)
            raise NikobusSendError(error_msg)

        try:
            full_command = command.encode() + b"\r"
            self._nikobus_writer.write(full_command)
            await self._nikobus_writer.drain()
            _LOGGER.debug(f"Sent command: {command}")
        except Exception as e:
            _LOGGER.error(f"Failed to send command '{command}': {e}")
            raise NikobusSendError(f"Failed to send command '{command}': {e}")

    async def close(self):
        """Close the connection to the Nikobus system."""
        if self._nikobus_writer:
            self._nikobus_writer.close()
            await self._nikobus_writer.wait_closed()
            _LOGGER.info("Nikobus connection closed")
