"""Nikobus Connection Manager."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import re
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

# Re-use one of your harmless handshake commands if you later enable app heartbeats.
_COMMAND_WITH_ACK = COMMANDS_HANDSHAKE[3]


def _configure_tcp_socket(sock: socket.socket) -> None:
    """Harden a TCP socket for long-lived links to HF2211/USR gateways."""
    # Flush small Nikobus telegrams immediately
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass

    # OS-level keepalives to detect half-open sessions (e.g., idle NATs / HF2211 drops)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass

    # Platform-specific tunables (best-effort; silently ignore if not supported)
    # Linux constants: TCP_KEEPIDLE=4, TCP_KEEPINTVL=5, TCP_KEEPCNT=6
    # macOS/BSD use different names; on some platforms these are unsupported.
    for (level, optname, value) in (
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPIDLE", 4), 30),  # seconds idle
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPINTVL", 5), 10),  # probe interval
        (socket.IPPROTO_TCP, getattr(socket, "TCP_KEEPCNT", 6), 3),  # probe count
    ):
        try:
            sock.setsockopt(level, optname, value)
        except OSError:
            # Not supported on this OS; that's fine—baseline SO_KEEPALIVE still helps.
            pass

    # Non-blocking; asyncio will attach transports/streams
    sock.setblocking(False)


class NikobusConnect:
    """Manages connection to a Nikobus system via IP or Serial."""

    def __init__(self, connection_string: str) -> None:
        """Initialize the connection handler with the given connection string."""
        self._connection_string = connection_string
        self._connection_type: Literal["IP", "Serial", "Unknown"] = (
            self._validate_connection_string()
        )
        self._nikobus_reader: asyncio.StreamReader | None = None
        self._nikobus_writer: asyncio.StreamWriter | None = None

        # Reconnect/backoff control
        self._connect_lock = asyncio.Lock()
        self._is_connecting = False

    # -------------------------
    # Public API
    # -------------------------
    async def connect(self) -> None:
        """Connect and perform handshake (idempotent)."""
        async with self._connect_lock:
            if self._nikobus_writer is not None and not self._nikobus_writer.is_closing():
                return  # already connected

            self._is_connecting = True
            try:
                if self._connection_type == "IP":
                    await self._connect_ip()
                elif self._connection_type == "Serial":
                    await self._connect_serial()
                else:
                    msg = f"Invalid connection string: {self._connection_string}"
                    _LOGGER.error(msg)
                    raise NikobusConnectionError(msg)

                if not await self._perform_handshake():
                    msg = "Handshake failed"
                    _LOGGER.error(msg)
                    # Force close before raising, so next attempt starts clean
                    await self._safe_close()
                    raise NikobusConnectionError(msg)

                _LOGGER.info("Nikobus handshake successful.")
            finally:
                self._is_connecting = False

    async def ensure_connected(self) -> None:
        """Ensure the link is up; try to reconnect with jittered backoff if needed."""
        backoff = 1.0
        while True:
            try:
                await self.connect()
                return
            except NikobusConnectionError as err:
                _LOGGER.warning("Nikobus reconnect failed: %s", err)
                # Jitter avoids stampedes if multiple tasks trigger reconnect
                sleep_for = backoff + random.uniform(0, 0.5)
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, 30.0)

    async def ping(self) -> None:
        """Open the port briefly and close it again – used to ‘wake’ the PC-Link."""
        await self.connect()
        await self.disconnect()

    async def read(self, timeout: Optional[float] = 35.0) -> bytes:
        """Read one CR-terminated frame from the Nikobus system.

        A timeout guards against half-open sockets that never deliver EOF.
        """
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
        except (asyncio.TimeoutError,) as err:
            # Treat as dead session -> drop and let caller trigger reconnect.
            await self._mark_broken("read timeout")
            raise NikobusReadError(f"Read timeout after {timeout}s") from err
        except Exception as err:
            await self._mark_broken(f"read error: {err}")
            raise NikobusReadError(f"Failed to read data: {err}") from err

    async def send(self, command: str, timeout: Optional[float] = 3.0) -> None:
        """Send a command to the Nikobus system with an optional drain timeout."""
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
        except (asyncio.TimeoutError,) as err:
            await self._mark_broken("send drain timeout")
            raise NikobusSendError(
                f"Timeout while sending command '{command}'"
            ) from err
        except Exception as err:
            await self._mark_broken(f"send error: {err}")
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
        """Establish an IP connection to the Nikobus system with socket hardening."""
        try:
            host, port_str = self._connection_string.split(":", 1)
            port = int(port_str)

            # Build a pre-configured socket so we can set keepalives/TCP_NODELAY
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _configure_tcp_socket(sock)

            # Use asyncio streams over our prepared socket
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
            # Check for common serial port patterns.
            if re.match(
                r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$", self._connection_string
            ):
                return "Serial"
        return "Unknown"

    async def _perform_handshake(self) -> bool:
        """Perform a handshake with the Nikobus system to verify the connection."""
        for command in COMMANDS_HANDSHAKE:
            _LOGGER.debug("Handshake: %s", command)
            if not await self._send_with_retry(command):
                return False
        return True

    async def _send_with_retry(self, command: str) -> bool:
        """Send a command and handle potential errors with retries."""
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

    async def _mark_broken(self, reason: str) -> None:
        """Mark the current streams as broken so the caller can reconnect quickly."""
        _LOGGER.warning("Nikobus link marked broken: %s", reason)
        await self._safe_close()

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
                await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
            except Exception:
                pass
        except Exception:
            pass
