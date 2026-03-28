"""Nikobus Event Listener - Platinum Edition (Corrected CRC)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

_FRAME_SPLIT_RE = re.compile(r'(?=[$#])')

from .const import (
    BUTTON_COMMAND_PREFIX,
    COMMAND_PROCESSED,
    CONF_HAS_FEEDBACK_MODULE,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY_ANSWER,
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
        self.response_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self.on_connection_lost: Callable[[], Any] | None = None
        self._frame_buffer = ""
        # Per-address dict: maps module address → last queried group (1 or 2).
        # Replaces the single _module_group int that was vulnerable to corruption
        # when a foreign device on the same bus (e.g. a second PC-Link or another
        # controller) sent its own $1012/$1017 echoes — those would overwrite the
        # shared variable before HA's matching $1C response arrived, causing
        # process_feedback_data to be called with the wrong group number.
        self._last_query_group: dict[str, int] = {}
        self._has_feedback_module: bool = config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False)

    def set_pending_query_group(self, addr: str, group: int) -> None:
        """Record which group is about to be queried for an address.

        Called by the command layer immediately before it sends a $1012/$1017
        GET command so that the feedback callback can attribute the matching
        $1C response to the correct group.  This is necessary because many
        PC-Link firmware variants do NOT echo HA's own bus commands back on
        the serial port, leaving _last_query_group empty so it would fall back
        to the default group 1 for every response — corrupting state for any
        module with more than 6 channels when a Group 2 query is made.
        """
        self._last_query_group[addr] = group

    def _enqueue_response(self, message: str) -> None:
        """Add a message to the response queue, dropping the oldest if full."""
        try:
            self.response_queue.put_nowait(message)
        except asyncio.QueueFull:
            try:
                self.response_queue.get_nowait()
                self.response_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self.response_queue.put_nowait(message)
            _LOGGER.warning("Response queue was full — dropped oldest message to make room")

    async def start(self) -> None:
        """Start the background listening task."""
        self._running = True
        self._listener_task = self._hass.async_create_background_task(
            self._listen_loop(), name="nikobus_listen_loop"
        )
        _LOGGER.info("Nikobus Event Listener started.")

    async def stop(self) -> None:
        """Stop the listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
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
                if not self._connection.is_connected:
                    _LOGGER.warning("Connection lost — listener loop exiting.")
                    self._running = False
                    if self.on_connection_lost:
                        self._hass.async_create_task(self.on_connection_lost())
                    break
                await asyncio.sleep(1)

    def _extract_frames(self, raw: str) -> list[str]:
        """Normalize and extract frames from serial data."""
        self._frame_buffer += raw.replace("\x02", "").replace("\x03", "").replace("\n", "\r")
        
        if "\r" not in self._frame_buffer: 
            return []
        
        *frames, self._frame_buffer = self._frame_buffer.split("\r")
        
        extracted = []
        for frame in frames:
            if frame := frame.strip():
                # FIX: Split at every '$' OR '#' to handle collisions
                extracted.extend(f for f in _FRAME_SPLIT_RE.split(frame) if f)
                
        return extracted

    async def dispatch_message(self, message: str) -> None:
        """Route messages based on frame content."""
        if not message: return
        discovery_running = self._coordinator.discovery_running

        if DEVICE_ADDRESS_INVENTORY in message:
            await self._handle_inventory(message, discovery_running)
            return

        if not discovery_running:
            # Handle button press and return immediately.
            # _extract_frames already splits the raw stream at every '$' and '#',
            # so a button frame (#N…) dispatched here never contains embedded
            # command-response data. Enqueuing #N frames in the response queue
            # adds noise that blocks ACK waits in send_command_get_answer for
            # as long as the button is held (each #N frame consumes one iteration
            # of the wait loop, delaying the real $05xx ACK by up to 5 seconds).
            if BUTTON_COMMAND_PREFIX in message:
                idx = message.find(BUTTON_COMMAND_PREFIX)
                if idx != -1:
                    await self._actuator.handle_button_press(message[idx+2:idx+8])
                return

            if any(message.startswith(cmd) for cmd in COMMAND_PROCESSED):
                self._enqueue_response(message)
                return

            # $1012/$1017 are GET-state command echoes — never a response HA waits for.
            # Always discard them to prevent flooding the response queue, regardless of
            # whether a feedback module is present.
            if any(message.startswith(r) for r in FEEDBACK_REFRESH_COMMAND):
                if self._has_feedback_module:
                    gid = message[3:5]
                    group = {"12": 1, "17": 2}.get(gid, 1)
                    # Extract address from the GET-command echo (little-endian at [5:9])
                    # and store the group so the matching $1C response is attributed
                    # to the correct group even if foreign bus traffic intervenes.
                    if len(message) >= 9:
                        addr = (message[7:9] + message[5:7]).upper()
                        self._last_query_group[addr] = group
                return

            if message.startswith(FEEDBACK_MODULE_ANSWER):
                # $1C… frames are GET-state responses — from HA's own queries
                # or from other controllers on the same bus.  Only enqueue frames
                # from modules HA knows about; foreign ones are noise that can
                # delay the ACK/ANSWER wait loop by a full COMMAND_ANSWER_WAIT_TIMEOUT.
                if self.validate_crc(message):
                    if len(message) >= 7:
                        addr = (message[5:7] + message[3:5]).upper()
                        if self._has_feedback_module:
                            # Look up which group was last queried for this specific
                            # address.  Defaults to 1 so single-group modules still
                            # work if the echo was never seen (e.g. missed frame).
                            group = self._last_query_group.get(addr, 1)
                            await self._feedback_callback(group, message)
                        if addr in self._coordinator.nikobus_module_states:
                            self._enqueue_response(message)
                return

            if any(message.startswith(r) for r in MANUAL_REFRESH_COMMAND):
                if self.validate_crc(message) and not message.startswith(BUTTON_COMMAND_PREFIX):
                    self._enqueue_response(message)
                return
        else:
            if any(message.startswith(inv) for inv in DEVICE_INVENTORY_ANSWER):
                await self._handle_discovery_frame(message)
                return

        self._enqueue_response(message)

    def validate_crc(self, message: str) -> bool:
        # Iteratively advance to the last '$' in the frame to handle collisions
        while message.count("$") > 1:
            message = message[message.find("$", 1):]

        # ACKs ($05xx) do not have a CRC or payload to validate
        if len(message) == 5 and message.startswith("$05"):
            return True

        try:
            total_len_hex = message[1:3]
            expected_total = int(total_len_hex, 16)
            
            # Nikobus length field encodes len(frame_including_$) + 1,
            # so a valid frame satisfies: len(message) == expected_total - 1.
            if len(message) != expected_total - 1:
                _LOGGER.error(
                    "Length mismatch: expected %d chars, got %d (frame: %s)",
                    expected_total - 1, len(message), message,
                )
                return False

            payload_with_crc16 = message[:-2]
            expected_crc8 = message[-2:]
            calculated_crc8 = int_to_hex(calc_crc2(payload_with_crc16), 2)
            
            return calculated_crc8.upper() == expected_crc8.upper()
        except (ValueError, IndexError, AttributeError):
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