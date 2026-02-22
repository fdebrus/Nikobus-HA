"""Nikobus Event Listener - Platinum Edition (Corrected CRC)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

from .const import (
    BUTTON_COMMAND_PREFIX,
    COMMAND_PROCESSED,
    CONF_HAS_FEEDBACK_MODULE,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY,
    FEEDBACK_MODULE_ANSWER,
    FEEDBACK_REFRESH_COMMAND,
    MANUAL_REFRESH_COMMAND,
)
from .discovery.base import InventoryQueryType
from .nkbprotocol import calc_crc2, int_to_hex

_LOGGER = logging.getLogger(__name__)


class NikobusEventListener:
    """Listens to the PC-Link serial stream and dispatches decoded Nikobus frames."""

    def __init__(
        self,
        hass: Any,
        config_entry: Any,
        coordinator: Any,
        nikobus_actuator: Any,
        nikobus_connection: Any,
        nikobus_discovery: Any,
        feedback_callback: Callable[[int, str], Any],
    ) -> None:
        """Initialize the listener."""
        self._hass = hass
        self._coordinator = coordinator
        self._actuator = nikobus_actuator
        self._connection = nikobus_connection
        self._discovery = nikobus_discovery
        self._feedback_callback = feedback_callback

        self._running = False
        self._listener_task: asyncio.Task | None = None
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._frame_buffer = ""
        self._module_group = 1
        self._has_feedback_module: bool = config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False)

    async def start(self) -> None:
        """Start the background listening task."""
        self._running = True
        self._listener_task = self._hass.loop.create_task(self._listen_loop())
        _LOGGER.info("Nikobus Event Listener started.")

    async def stop(self) -> None:
        """Stop the listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            self._listener_task = None

    async def _listen_loop(self) -> None:
        """Continuous loop to read from the Nikobus connection."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._connection.read(), timeout=10)
                if not data: continue

                raw_text = data.decode("Windows-1252", errors="ignore")
                for frame in self._extract_frames(raw_text):
                    _LOGGER.debug("Bus Frame: %s", frame)
                    self._hass.async_create_task(self.dispatch_message(frame))
            except asyncio.TimeoutError:
                continue
            except Exception as err:
                _LOGGER.error("Listener loop error: %s", err)
                await asyncio.sleep(1)

    def _extract_frames(self, raw: str) -> list[str]:
        """Normalize and extract frames from serial data."""
        self._frame_buffer += raw.replace("\x02", "").replace("\x03", "").replace("\n", "\r")
        
        if "\r" not in self._frame_buffer: 
            return []
        
        # Cleanly unpack: everything except the last item goes to 'frames', last item goes to 'buffer'
        *frames, self._frame_buffer = self._frame_buffer.split("\r")
        
        extracted = []
        for frame in frames:
            if frame := frame.strip():
                # Split right before every '$' and keep non-empty chunks
                extracted.extend(f for f in re.split(r'(?=\$)', frame) if f)
                
        return extracted

    async def dispatch_message(self, message: str) -> None:
        """Route messages based on frame content."""
        if not message: return
        discovery_running = self._coordinator.discovery_running

        if DEVICE_ADDRESS_INVENTORY in message:
            await self._handle_inventory(message, discovery_running)
            return

        if not discovery_running:
            if BUTTON_COMMAND_PREFIX in message:
                idx = message.find(BUTTON_COMMAND_PREFIX)
                if idx != -1:
                    await self._actuator.handle_button_press(message[idx+2:idx+8])
                return

            if any(message.startswith(cmd) for cmd in COMMAND_PROCESSED):
                # ACKs ($05xx) do not have a CRC or length field, queue them directly
                await self.response_queue.put(message)
                return

            if self._has_feedback_module:
                if any(message.startswith(r) for r in FEEDBACK_REFRESH_COMMAND):
                    gid = message[3:5]
                    self._module_group = {"12": 1, "17": 2}.get(gid, 1)
                    return
                if message.startswith(FEEDBACK_MODULE_ANSWER):
                    if self.validate_crc(message):
                        await self._feedback_callback(self._module_group, message)
                        # Ensure commands awaiting this answer can see it
                        await self.response_queue.put(message)
                    return

            if any(message.startswith(r) for r in MANUAL_REFRESH_COMMAND):
                if self.validate_crc(message) and not message.startswith(BUTTON_COMMAND_PREFIX):
                    await self.response_queue.put(message)
                return
        else:
            if any(message.startswith(inv) for inv in DEVICE_INVENTORY):
                await self._handle_discovery_frame(message)
                return

        await self.response_queue.put(message)

    def validate_crc(self, message: str) -> bool:
        if message.count("$") > 1:
            return self.validate_crc(message[message.find("$", 1):])

        # ACKs ($05xx) do not have a CRC or payload to validate
        if len(message) == 5 and message.startswith("$05"):
            return True

        try:
            total_len_hex = message[1:3]
            expected_total = int(total_len_hex, 16)
            
            # Nikobus length field is total chars after '$' + 1
            if len(message) != expected_total - 1:
                _LOGGER.error("Length mismatch: %s vs actual %d (frame: %s)", total_len_hex, len(message)-1, message)
                return False

            payload_with_crc16 = message[:-2]
            expected_crc8 = message[-2:]
            calculated_crc8 = int_to_hex(calc_crc2(payload_with_crc16), 2)
            
            return calculated_crc8.upper() == expected_crc8.upper()
        except Exception:
            return False

    async def _handle_inventory(self, msg: str, discovery: bool) -> None:
        """Handle hardware identification."""
        if discovery:
            if self._coordinator.inventory_query_type == InventoryQueryType.PC_LINK:
                self._discovery.handle_device_address_inventory(msg)
            else:
                await self._discovery.query_module_inventory(msg[3:7])
        elif hasattr(self._discovery, "process_mode_button_press"):
            await self._discovery.process_mode_button_press(msg)

    async def _handle_discovery_frame(self, msg: str) -> None:
        """Route discovery frames."""
        is_module = (self._coordinator.inventory_query_type == InventoryQueryType.MODULE)
        if is_module:
            await self._discovery.parse_module_inventory_response(msg)
        else:
            await self._discovery.parse_inventory_response(msg)