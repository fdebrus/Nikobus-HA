"""Nikobus Event Listener Updated."""

from __future__ import annotations
import logging
import asyncio
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.nikobus.exceptions import NikobusDataError
from .nkbprotocol import int_to_hex, calc_crc2
from .discovery.base import InventoryQueryType
from .const import (
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    BUTTON_COMMAND_PREFIX,
    IGNORE_ANSWER,
    FEEDBACK_REFRESH_COMMAND,
    MANUAL_REFRESH_COMMAND,
    FEEDBACK_MODULE_ANSWER,
    COMMAND_PROCESSED,
    DEVICE_ADDRESS_INVENTORY,
    DEVICE_INVENTORY,
)

_LOGGER = logging.getLogger(__name__)


class NikobusEventListener:
    """Listener to handle events from the Nikobus system."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        coordinator: Any,
        nikobus_actuator: Any,
        nikobus_connection: Any,
        nikobus_discovery: Any,
        feedback_callback: Callable[[int, str], None],
    ) -> None:
        """Initialize the Nikobus event listener."""
        self._hass = hass
        self._config_entry = config_entry
        self._coordinator = coordinator
        self._listener_task: asyncio.Task | None = None
        self._running = False
        self._feedback_callback = feedback_callback
        self._has_feedback_module: bool = config_entry.data.get(
            CONF_HAS_FEEDBACK_MODULE, False
        )
        self._prior_gen3: bool = config_entry.data.get(
            CONF_PRIOR_GEN3, False
        )
        self._module_group = 1
        self._actuator = nikobus_actuator

        self.nikobus_connection = nikobus_connection
        self.nikobus_discovery = nikobus_discovery
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._frame_buffer = ""

    async def start(self) -> None:
        """Start the event listener."""
        self._running = True
        self._listener_task = self._hass.loop.create_task(self.listen_for_events())
        _LOGGER.info("Nikobus Event Listener started.")

    async def stop(self) -> None:
        """Stop the event listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                _LOGGER.info("Nikobus event listener has been stopped.")
            self._listener_task = None

    def validate_crc(self, message: str) -> bool:
        """
        Validate the CRC of a Nikobus message.
        """
        # If the message contains a nested message, extract and validate the inner message.
        if message.count("$") > 1:
            second_dollar = message.find("$", 1)
            inner_message = message[second_dollar:]
            return self.validate_crc(inner_message)

        # Extract the two-digit length field after the '$'
        len_field = message[1:3]
        try:
            total_length = int(len_field, 16)
        except ValueError:
            _LOGGER.error("Invalid length field in message: %s", message)
            return False

        data_len = total_length - 10
        # Expected total length: 1 ('$') + 2 (length) + data_len + 4 (CRC16) + 2 (CRC8)
        expected_total_length = 1 + 2 + data_len + 4 + 2
        if len(message) != expected_total_length:
            _LOGGER.error(
                "Message length mismatch: got %d, expected %d (based on length field %s). message %s",
                len(message),
                expected_total_length,
                len_field,
                message,
            )
            return False

        # Calculate CRC8 over: "$" + <length field> + <payload> + <CRC16>
        intermediate_string = message[: 3 + data_len + 4]
        calc_crc8_val = int_to_hex(calc_crc2(intermediate_string), 2)
        crc8_str = message[3 + data_len + 4 :]
        if calc_crc8_val != crc8_str:
            _LOGGER.error(
                "CRC8 mismatch: calculated %s, expected %s, message %s",
                calc_crc8_val,
                crc8_str,
                message,
            )
            return False
        _LOGGER.debug(
            "CRC8 match: calculated %s, expected %s, message %s",
            calc_crc8_val,
            crc8_str,
            message,
        )
        return True

    async def listen_for_events(self) -> None:
        """Continuously listen for and handle events from the Nikobus system."""
        _LOGGER.info("Nikobus Event Listener is running.")
        while self._running:
            try:
                data = await asyncio.wait_for(
                    self.nikobus_connection.read(), timeout=10
                )
                if not data:
                    _LOGGER.warning("Nikobus connection closed unexpectedly.")
                    break

                raw = data.decode("Windows-1252", errors="ignore")
                for message in self._extract_frames(raw):
                    _LOGGER.debug("Received message: %s", message)
                    self._hass.async_create_task(self.dispatch_message(message))

            except asyncio.TimeoutError:
                _LOGGER.debug("Read operation timed out. Waiting for next data...")
            except asyncio.CancelledError:
                _LOGGER.info("Event listener was cancelled.")
                break
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error in event listener: %s", err, exc_info=True
                )
                break

    async def dispatch_message(self, message: str) -> None:
        if not message:
            return
        discovery_running = self._coordinator.discovery_running
        if DEVICE_ADDRESS_INVENTORY in message:
            _LOGGER.debug("Device address inventory: %s", message)
            if discovery_running:
                await self.nikobus_discovery.query_module_inventory(message[3:7])
            else:
                if hasattr(self.nikobus_discovery, "process_mode_button_press"):
                    await self.nikobus_discovery.process_mode_button_press(message)
                else:
                    _LOGGER.debug(
                        "No process_mode_button_press handler; ignoring %s", message
                    )
            return

        if not discovery_running:
            if BUTTON_COMMAND_PREFIX in message:
                # Find the position where '#N' starts
                index = message.find("#N")
                if index != -1:
                    # Extract the 6-character button address following "#N"
                    button_address = message[index + 2:index + 8]
                    _LOGGER.debug(
                        "Button command received: %s, extracted address: %s",
                        message,
                        button_address,
                    )
                    await self._actuator.handle_button_press(button_address)
                    return
                _LOGGER.debug("Button command without '#N' prefix: %s", message)

            if message.startswith(IGNORE_ANSWER) or any(
                message.startswith(refresh + BUTTON_COMMAND_PREFIX)
                for refresh in MANUAL_REFRESH_COMMAND
            ):
                _LOGGER.debug("Ignored message: %s", message)
                return

            if any(message.startswith(command) for command in COMMAND_PROCESSED):
                # Example message: "$0515$0EFF6C0E0060"
                if not self.validate_crc(message):
                    return

                # Use the inner message for error checking only
                inner_msg = self._extract_inner_message(message)

                if len(inner_msg) >= 11:
                    error_field = inner_msg[3:5]
                    module_address = inner_msg[5:9]
                    status_field = inner_msg[9:11]
                    if (error_field, status_field) in [("FF", "01"), ("FE", "00")]:
                        _LOGGER.error(
                            "Command failed with error codes: %s %s",
                            error_field,
                            status_field,
                        )
                        raise NikobusDataError(
                            f"Command failed with error codes: {error_field} {status_field}"
                        )
                    else:
                        _LOGGER.debug(
                            "Command acknowledged with module address: %s",
                            module_address,
                        )
                        # Queue the full message, not just the inner message
                        await self.response_queue.put(message)
                        return
                else:
                    _LOGGER.debug("Command acknowledged: %s", message)
                    await self.response_queue.put(message)
                    return

            if any(message.startswith(refresh) for refresh in FEEDBACK_REFRESH_COMMAND):
                if not self.validate_crc(message):
                    return
                if self._has_feedback_module:
                    _LOGGER.debug("Feedback module refresh command: %s", message)
                    self._handle_feedback_refresh(message)
                else:
                    _LOGGER.debug("Dropping Feedback refresh command: %s", message)
                return

            if message.startswith(FEEDBACK_MODULE_ANSWER):
                if not self.validate_crc(message):
                    return
                if self._has_feedback_module:
                    _LOGGER.debug("Feedback module answer: %s", message)
                    await self._feedback_callback(self._module_group, message)
                else:
                    _LOGGER.debug("Dropping Feedback module answer: %s", message)
                return

            if any(message.startswith(refresh) for refresh in MANUAL_REFRESH_COMMAND):
                _LOGGER.debug("Manual refresh command answer: %s", message)
                if not self.validate_crc(message):
                    return
                if not message.startswith(BUTTON_COMMAND_PREFIX):
                    await self.response_queue.put(message)
                return

        else:

            if any(message.startswith(inventory) for inventory in DEVICE_INVENTORY):
                _LOGGER.debug(
                    "Device inventory (discovery): %s (module=%s)",
                    message,
                    self._coordinator.discovery_module_address,
                )
                if self._should_use_pclink_inventory_parser():
                    _LOGGER.info(
                        "PCLINK inventory response parsed with parse_inventory_response"
                    )
                    await self.nikobus_discovery.parse_inventory_response(message)
                else:
                    _LOGGER.info(
                        "Module inventory response parsed with parse_module_inventory_response"
                    )
                    await self.nikobus_discovery.parse_module_inventory_response(message)
                return

        _LOGGER.debug("Adding unknown message to response queue: %s", message)
        await self.response_queue.put(message)

    def _extract_frames(self, raw: str) -> list[str]:
        """Normalize incoming data and return complete frames."""
        if not raw:
            return []
        cleaned = raw.replace("\x02", "").replace("\x03", "")
        cleaned = cleaned.replace("\n", "\r")
        self._frame_buffer += cleaned
        if "\r" not in self._frame_buffer:
            return []
        parts = self._frame_buffer.split("\r")
        self._frame_buffer = parts.pop()
        frames = [part.strip() for part in parts if part.strip()]
        if frames:
            _LOGGER.debug("Normalized frames: %s", frames)
        return frames

    @staticmethod
    def _extract_inner_message(message: str) -> str:
        """Return the inner message if a nested payload exists."""
        if message.count("$") > 1:
            second_dollar = message.find("$", 1)
            return message[second_dollar:]
        return message

    def _handle_feedback_refresh(self, message: str) -> None:
        """Handle feedback refresh commands using a mapping for module group identifiers."""
        group_mapping = {"17": 2, "12": 1}
        module_group_identifier = message[3:5]
        new_group = group_mapping.get(module_group_identifier)
        if new_group:
            self._module_group = new_group
        else:
            _LOGGER.warning(
                "Unknown module group identifier: %s", module_group_identifier
            )

    def _should_use_pclink_inventory_parser(self) -> bool:
        query_type = getattr(self._coordinator, "inventory_query_type", None)
        if query_type == InventoryQueryType.MODULE:
            return False
        if query_type == InventoryQueryType.PC_LINK:
            return True
        return self._coordinator.discovery_module_address is None
