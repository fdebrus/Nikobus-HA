"""Nikobus Connection Handler - Platinum Edition."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Optional, Tuple

import serial_asyncio
from .exceptions import NikobusConnectionError, NikobusSendError, NikobusReadError

_LOGGER = logging.getLogger(__name__)


class NikobusConnect:
    """Manages the asynchronous connection (Serial or TCP) to the Nikobus PC-Link."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the connection handler."""
        self._connection_string = connection_string
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """Return True if the connection is active."""
        return self._is_connected

    async def connect(self) -> None:
        """Establish the connection."""
        _LOGGER.debug("Attempting to connect to Nikobus: %s", self._connection_string)
        
        try:
            if ":" in self._connection_string and not self._connection_string.startswith("/"):
                # TCP/IP Connection (Host:Port)
                host, port = self._connection_string.split(":", 1)
                self._reader, self._writer = await asyncio.open_connection(host, int(port))
            else:
                # Serial Connection
                self._reader, self._writer = await serial_asyncio.open_serial_connection(
                    url=self._connection_string,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False
                )
            
            self._is_connected = True
            _LOGGER.info("Connected to Nikobus on %s", self._connection_string)
            
        except (OSError, asyncio.TimeoutError) as err:
            self._is_connected = False
            _LOGGER.error("Failed to connect to %s: %s", self._connection_string, err)
            raise NikobusConnectionError(f"Connection failed: {err}") from err

    async def disconnect(self) -> None:
        """Close the connection and cleanup resources."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as err:
                _LOGGER.debug("Error during close: %s", err)
        
        self._reader = None
        self._writer = None
        self._is_connected = False
        _LOGGER.info("Nikobus connection closed.")

    async def ping(self) -> bool:
        """Perform a connection handshake with the PC-Link."""
        from .const import COMMANDS_HANDSHAKE, EXPECTED_HANDSHAKE_RESPONSE
        
        if not self._is_connected:
            await self.connect()
        
        try:
            _LOGGER.debug("Starting full Nikobus handshake...")
            for cmd in COMMANDS_HANDSHAKE:
                await self.send(cmd)
                # Small delay to let the PC-Link process each command
                await asyncio.sleep(0.2) 
            
            _LOGGER.info("Nikobus handshake completed successfully.")
            return True
        except Exception as err:
            _LOGGER.error("Handshake failed: %s", err)
            raise NikobusConnectionError(f"Handshake failed: {err}") from err

    async def ping(self) -> bool:
        if not self._is_connected:
            await self.connect()
        
        try:
            # Send #E1 command (enable feedback) 
            # This is the standard command to verify PC-Link responsiveness.
            await self.send("#E1")
            _LOGGER.debug("Nikobus handshake (#E1) successful.")
            return True
        except Exception as err:
            _LOGGER.error("Nikobus handshake failed: %s", err)
            raise NikobusConnectionError(f"Hardware not responding: {err}") from err

    async def send(self, command: str) -> None:
        """Send a command string to the bus with thread-safe locking."""
        if not self._is_connected or not self._writer:
            raise NikobusConnectionError("Cannot send: Not connected.")

        async with self._lock:
            try:
                # Nikobus expects CR as delimiter.
                # strip() ensures we don't accidentally send \r\r
                payload = command.strip() + "\r"
                data = payload.encode("ascii")
                
                self._writer.write(data)
                await self._writer.drain()
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Write failed: %s", err)
                await self.disconnect()
                raise NikobusSendError(f"Write error: {err}") from err

    async def read(self) -> bytes:
        """Read a single frame (CR-terminated) from the bus."""
        if not self._is_connected or not self._reader:
            raise NikobusConnectionError("Cannot read: Not connected.")

        try:
            # readuntil(b'\r') reads until the delimiter is found
            data = await self._reader.readuntil(b'\r')
            return data
        except asyncio.LimitOverrunError:
            # Buffer full, read whatever is there to clear it
            await self._reader.read(1024)
            raise NikobusReadError("Buffer overrun")
        except (OSError, asyncio.IncompleteReadError) as err:
            _LOGGER.error("Read failed: %s", err)
            await self.disconnect()
            raise NikobusReadError(f"Read error: {err}") from err