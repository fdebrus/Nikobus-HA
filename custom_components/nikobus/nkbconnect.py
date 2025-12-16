"""Nikobus Connection Manager."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Literal, Optional

import serial_asyncio

from .const import BAUD_RATE, COMMANDS_HANDSHAKE
from .exceptions import (
    NikobusConnectionError,
    NikobusReadError,
    NikobusSendError,
)

_LOGGER = logging.getLogger(__name__)


class NikobusConnect:
    """Manages connection to a Nikobus system via IP or Serial."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the connection handler with the given connection string."""
        self._connection_string = connection_string
        self._connection_type: Literal["IP", "Serial", "Unknown"] = (
            self._validate_connection_string()
        )
        self._nikobus_reader: Optional[asyncio.StreamReader] = None
        self._nikobus_writer: Optional[asyncio.StreamWriter] = None

    # -------------------------
    # Public API
    # -------------------------
    async def connect(self) -> None:
        """Connect to the Nikobus system using the connection string and perform handshake."""
        if self._connection_type == "IP":
            await self._connect_ip()
        elif self._connection_type == "Serial":
            await self._connect_serial()
        else:
            msg = f"Invalid connection string: {self._connection_string}"
            _LOGGER.error(msg)
            raise NikobusConnectionError(msg)

        # Small settle right after transport is up
        await asyncio.sleep(0.10)

        if not await self._perform_handshake():
            msg = "Handshake failed"
            _LOGGER.error(msg)
            await self.disconnect()
            raise NikobusConnectionError(msg)

        _LOGGER.info("Nikobus handshake successful.")

    async def ping(self) -> None:
        """Open the port briefly and close it again – used to ‘wake’ the PC-Link."""
        await self.connect()
        await self.disconnect()

    async def read(self, timeout: Optional[float] = 35.0) -> bytes:
        """Read one CR-terminated frame from the Nikobus system."""
        if not self._nikobus_reader:
            msg = "Reader is not available for reading data."
            _LOGGER.error(msg)
            raise NikobusReadError(msg)

        try:
            if timeout is None:
                data = await self._nikobus_reader.readuntil(b"\r")
            else:
                data = await asyncio.wait_for(
                    self._nikobus_reader.readuntil(b"\r"), timeout=timeout
                )
            return data
        except asyncio.TimeoutError as err:
            await self._safe_close()
            raise NikobusReadError(f"Read timeout after {timeout}s") from err
        except Exception as err:
            await self._safe_close()
            raise NikobusReadError(f"Failed to read data: {err}") from err

    async def send(self, command: str, timeout: Optional[float] = 3.0) -> None:
        """Send a CR-terminated command to the Nikobus system."""
        if not self._nikobus_writer:
            msg = "Writer is not available for sending commands."
            _LOGGER.error(msg)
            raise NikobusSendError(msg)

        try:
            self._nikobus_writer.write(command.encode() + b"\r")
            if timeout is None:
                await self._nikobus_writer.drain()
            else:
                await asyncio.wait_for(self._nikobus_writer.drain(), timeout=timeout)
        except asyncio.TimeoutError as err:
            await self._safe_close()
            raise NikobusSendError(
                f"Timeout while sending command '{command}'"
            ) from err
        except Exception as err:
            await self._safe_close()
            raise NikobusSendError(
                f"Failed to send command '{command}': {err}"
            ) from err

    async def disconnect(self) -> None:
        """Disconnect the connection to the Nikobus system."""
        await self._safe_close()
        _LOGGER.info("Nikobus connection disconnected.")

    # -------------------------
    # Internals
    # -------------------------
    async def _connect_ip(self) -> None:
        """Establish an IP connection (precreate + connect the socket correctly)."""
        try:
            host, port_str = self._connection_string.split(":", 1)
            port = int(port_str)

            # Precreate socket to apply options, then explicitly connect it
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            try:
                # Flush small telegrams immediately
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            try:
                # Keepalives to detect half-open sessions
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass

            loop = asyncio.get_running_loop()
            await loop.sock_connect(sock, (host, port))  # <-- crucial: actually connect

            reader, writer = await asyncio.open_connection(sock=sock)
            self._nikobus_reader = reader
            self._nikobus_writer = writer

            _LOGGER.info("Connected to bridge %s:%d", host, port)
        except (OSError, ValueError) as err:
            await self._safe_close()
            msg = f"Failed to connect to bridge {self._connection_string} - {err}"
            _LOGGER.error(msg)
            raise NikobusConnectionError(msg) from err

    async def _connect_serial(self) -> None:
        """Establish a serial connection to the Nikobus system."""
        try:
            reader, writer = await serial_asyncio.open_serial_connection(
                url=self._connection_string, baudrate=BAUD_RATE
            )
            self._nikobus_reader = reader
            self._nikobus_writer = writer
            _LOGGER.info("Connected to serial port %s", self._connection_string)
        except (OSError, serial_asyncio.SerialException) as err:
            await self._safe_close()
            msg = f"Failed to connect to serial port {self._connection_string} - {err}"
            _LOGGER.error(msg)
            raise NikobusConnectionError(msg) from err

    def _validate_connection_string(self) -> Literal["IP", "Serial", "Unknown"]:
        """Validate the connection string to determine the type (IP or Serial)."""
        parts = self._connection_string.split(":", 1)
        ip_candidate = parts[0]
        try:
            ipaddress.ip_address(ip_candidate)
            return "IP"
        except ValueError:
            # Common serial device patterns
            import re

            if re.match(
                r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$", self._connection_string
            ):
                return "Serial"
        return "Unknown"

    async def _perform_handshake(self) -> bool:
        """Perform a handshake with the Nikobus system to verify the connection.

        Uses COMMANDS_HANDSHAKE exactly as provided by your integration.
        """
        for command in COMMANDS_HANDSHAKE:
            _LOGGER.debug("Handshake: %s", command)
            if not await self._send_with_retry(command):
                return False
            # tiny pacing avoids packet coalescing quirks
            await asyncio.sleep(0.05)
        return True

    async def _send_with_retry(self, command: str) -> bool:
        """Send a command once; return True on success, False on failure."""
        try:
            await self.send(command)
            return True
        except NikobusSendError as err:
            _LOGGER.error("Failed to send command: %s", err)
            return False
        except (asyncio.TimeoutError, OSError) as err:
            _LOGGER.error("Error during send command: %s", err)
            return False
        except Exception as err:
            _LOGGER.exception("Unhandled exception during send command: %s", err)
            return False

    async def _safe_close(self) -> None:
        """Close streams safely (idempotent)."""
        writer = self._nikobus_writer
        self._nikobus_writer = None
        self._nikobus_reader = None

        if writer is None:
            return

        try:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception:
            pass
