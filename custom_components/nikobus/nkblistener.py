"""Nikobus Event Listener."""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Callable


from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.nikobus.exceptions import NikobusDataError

from .nkbprotocol import int_to_hex, calc_crc1_ack, calc_crc1, calc_crc2

from .const import (
    CONF_HAS_FEEDBACK_MODULE,
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
        coordinator,
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
        self._module_group = 1
        self._actuator = nikobus_actuator

        self.nikobus_connection = nikobus_connection
        self.nikobus_discovery = nikobus_discovery
        self.response_queue: asyncio.Queue[str] = asyncio.Queue()

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
        # If the message contains a nested message (e.g. "$0512$1C059100000000000000E858B3"),
        # extract and validate the inner message.
        if message.count('$') > 1:
            second_dollar = message.find('$', 1)
            inner_message = message[second_dollar:]
            return self.validate_crc(inner_message)

        # Extract the two-digit length field right after the '$'
        len_field = message[1:3]
        try:
            total_length = int(len_field, 16)
        except ValueError:
            _LOGGER.error("Invalid length field in message: %s", message)
            return False

        data_len = total_length - 10
        # Total message length should be: 1 (for '$') + 2 (length field) + data_len + 4 (CRC16) + 2 (CRC8)
        expected_total_length = 1 + 2 + data_len + 4 + 2
        if len(message) != expected_total_length:
            _LOGGER.error(
                "Message length mismatch: got %d, expected %d (based on length field %s). message %s",
                len(message), expected_total_length, len_field, message
            )
            return False

        # Extract the payload (the part over which the CRC16 is calculated)
        payload = message[3: 3 + data_len]
        # The next 4 characters are the CRC16
        crc16_str = message[3 + data_len: 3 + data_len + 4]
        # The last 2 characters are the CRC8
        crc8_str = message[3 + data_len + 4:]

        # The CRC8 is calculated over the string that contains:
        #   "$" + <length field> + <payload> + <CRC16>
        intermediate_string = message[: 3 + data_len + 4]
        calc_crc8_val = int_to_hex(calc_crc2(intermediate_string), 2)
        if calc_crc8_val != crc8_str:
            _LOGGER.error("CRC8 mismatch: calculated %s, expected %s, message %s", calc_crc8_val, crc8_str, message)
            raise NikobusDataError("CRC8 mismatch: calculated %s, expected %s, message %s" % (calc_crc8_val, crc8_str, message))
            return False
        else:
            _LOGGER.debug("CRC8 match: calculated %s, expected %s, message %s", calc_crc8_val, crc8_str, message)
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

                message = data.decode("Windows-1252").strip()
                _LOGGER.debug("Received message: %s", message)
                self._hass.async_create_task(self.dispatch_message(message))

            except asyncio.TimeoutError:
                _LOGGER.debug("Read operation timed out. Waiting for next data...")
                pass
            except asyncio.CancelledError:
                _LOGGER.info("Event listener was cancelled.")
                break
            except Exception as err:
                _LOGGER.error(
                    "Unexpected error in event listener: %s", err, exc_info=True
                )
                break

    async def dispatch_message(self, message: str) -> None:

        if not self._coordinator.discovery_running:

            """Handle and route incoming messages from the Nikobus system."""
            if message.startswith(BUTTON_COMMAND_PREFIX):
                _LOGGER.debug("Button command received: %s", message)
                await self._actuator.handle_button_press(message[2:8])
                return

            if message.startswith(IGNORE_ANSWER):
                _LOGGER.debug("Ignored message: %s", message)
                return

            if any(message.startswith(command) for command in COMMAND_PROCESSED):
                # eg $0515$0EFF6C0E0060 (expected length: 18 characters)
                if not self.validate_crc(message):
                    return
                _LOGGER.debug("Command acknowledged: %s", message)
                await self.response_queue.put(message)
                return

            if any(message.startswith(refresh) for refresh in FEEDBACK_REFRESH_COMMAND):
                # eg $10170747ABDBF7 (expected length: 15 characters)
                if not self.validate_crc(message):
                    return
                if self._has_feedback_module:
                    _LOGGER.debug("Feedback module refresh command: %s", message)
                    self._handle_feedback_refresh(message)
                else:
                    _LOGGER.debug("Dropping Feedback refresh command: %s", message)
                return

            if message.startswith(FEEDBACK_MODULE_ANSWER):
                # eg $1C6C0E000000FF00000080F51A (expected length: 27 characters)
                if not self.validate_crc(message):
                    return
                if self._has_feedback_module:
                    _LOGGER.debug("Feedback module answer: %s", message)
                    await self._feedback_callback(self._module_group, message)
                else:
                    _LOGGER.debug("Dropping Feedback module answer: %s", message)
                return

            if any(message.startswith(refresh) for refresh in MANUAL_REFRESH_COMMAND):
                # eg $0512$1C059100000000000000E858B3 (expected length: 32 characters)
                _LOGGER.debug("Manual refresh command answer: %s", message)
                if not self.validate_crc(message):
                    return
                if not message.startswith(BUTTON_COMMAND_PREFIX):
                    await self.response_queue.put(message)
                return
        else:
            if any(message.startswith(inventory) for inventory in DEVICE_INVENTORY):
                _LOGGER.debug("Device inventory: %s", message)
                if self._coordinator.discovery_module_address:
                    # if module address exists at coordinator
                    await self.nikobus_discovery.parse_module_inventory_response(message)
                else:
                    # For PCLink or Yellow Button
                    await self.nikobus_discovery.parse_inventory_response(message)
                return

            if message.startswith(DEVICE_ADDRESS_INVENTORY):  # Receive $18
                _LOGGER.debug("Device address inventory: %s", message)
                if self._coordinator.discovery_running:
                    await self.nikobus_discovery.query_module_inventory(message[3:7])
                else:
                    await self.nikobus_discovery.process_mode_button_press(message)
                return

        _LOGGER.debug("Adding unknown message to response queue: %s", message)
        await self.response_queue.put(message)

    def _handle_feedback_refresh(self, message: str) -> None:
        """Handle feedback refresh commands."""
        module_group_identifier = message[3:5]
        if module_group_identifier == "17":
            self._module_group = 2
        elif module_group_identifier == "12":
            self._module_group = 1
        else:
            _LOGGER.warning(
                "Unknown module group identifier: %s", module_group_identifier
            )
