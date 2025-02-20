"""Nikobus Event Listener."""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Callable


from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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
        """Handle and route incoming messages from the Nikobus system."""
        if message.startswith(BUTTON_COMMAND_PREFIX):
            _LOGGER.debug("Button command received: %s", message)
            await self._actuator.handle_button_press(message[2:8])
            return

        if message.startswith(IGNORE_ANSWER):
            _LOGGER.debug("Ignored message: %s", message)
            return

        if any(message.startswith(command) for command in COMMAND_PROCESSED):
            _LOGGER.debug("Command acknowledged: %s", message)
            await self.response_queue.put(message)
            return

        if any(message.startswith(refresh) for refresh in FEEDBACK_REFRESH_COMMAND):
            if self._has_feedback_module:
                _LOGGER.debug("Feedback module refresh command: %s", message)
                self._handle_feedback_refresh(message)
            else:
                _LOGGER.debug("Dropping Feedback refresh command: %s", message)
            return

        if message.startswith(FEEDBACK_MODULE_ANSWER):
            if self._has_feedback_module:
                _LOGGER.debug("Feedback module answer: %s", message)
                await self._feedback_callback(self._module_group, message)
            else:
                _LOGGER.debug("Dropping Feedback module answer: %s", message)
            return

        if any(message.startswith(refresh) for refresh in MANUAL_REFRESH_COMMAND):
            _LOGGER.debug("Manual refresh command answer: %s", message)
            if not message.startswith(BUTTON_COMMAND_PREFIX):
                await self.response_queue.put(message)
            return

        if message.startswith(DEVICE_INVENTORY):  # Receive $0510
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
            self._coordinator.discovery_running = False
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
