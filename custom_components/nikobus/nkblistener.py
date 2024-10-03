"""Listener for Nikobus"""

import logging
import asyncio

from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_HAS_FEEDBACK_MODULE, 
    BUTTON_COMMAND_PREFIX, 
    IGNORE_ANSWER, 
    FEEDBACK_REFRESH_COMMAND,
    MANUAL_REFRESH_COMMAND, 
    FEEDBACK_MODULE_ANSWER, 
    COMMAND_PROCESSED, 
    CONTROLLER_ADDRESS
)

from .nkbactuator import NikobusActuator

class NikobusEventListener:
    """Listener to handle events from the Nikobus system."""

    def __init__(self, hass, config_entry: ConfigEntry, nikobus_connection, button_discovery_callback, feedback_callback):
        """Initialize the Nikobus event listener."""
        self._hass = hass
        self._config_entry = config_entry
        self._listener_task = None
        self._running = False
        self._button_discovery_callback = button_discovery_callback
        self._feedback_callback = feedback_callback
        self._has_feedback_module = config_entry.options.get(CONF_HAS_FEEDBACK_MODULE, config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))
        self._module_group = 1
        self.nikobus_connection = nikobus_connection
        self.response_queue = asyncio.Queue()
        self._actuator = NikobusActuator(hass, button_discovery_callback)

    async def start(self):
        """Start the event listener."""
        self._running = True
        self._listener_task = self._hass.loop.create_task(self.listen_for_events())

    async def stop(self):
        """Stop the event listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                _LOGGER.info("Nikobus event listener has been stopped")
            self._listener_task = None

    async def listen_for_events(self) -> None:
        """Continuously listen for and handle events from the Nikobus system."""
        _LOGGER.info("Nikobus Event Listener starting")
        while self._running:
            try:
                data = await asyncio.wait_for(self.nikobus_connection.read(), timeout=10)
                if not data:
                    _LOGGER.warning("Nikobus connection closed unexpectedly")
                    break
                message = data.decode('utf-8').strip()
                _LOGGER.debug(f"Received message: {message}")
                self._hass.async_create_task(self.dispatch_message(message))
            except asyncio.TimeoutError:
                _LOGGER.debug("Read operation timed out. Waiting for next data...")
                pass
            except asyncio.CancelledError:
                _LOGGER.info("Event listener was cancelled")
                break
            except Exception as e:
                _LOGGER.error(f"Unexpected error in event listener: {e}", exc_info=True)
                break

    async def dispatch_message(self, message: str) -> None:
        """Handle and route incoming messages from the Nikobus system."""
        if message.startswith(BUTTON_COMMAND_PREFIX):
            await self._actuator.handle_button_press(message[2:8])
            return

        if message.startswith(IGNORE_ANSWER):
            _LOGGER.debug(f"Ignored message: {message}")
            return

        if message.startswith(COMMAND_PROCESSED):
            _LOGGER.debug(f"Command acknowledged: {message}")
            return

        if message.startswith(CONTROLLER_ADDRESS):
            _LOGGER.debug(f"Nikobus Controller Address: {message[3:7]}")
            return

        if message.startswith(FEEDBACK_REFRESH_COMMAND) and self._has_feedback_module:
            _LOGGER.debug(f"Feedback module refresh command: {message}")
            self._handle_feedback_refresh(message)
            return

        if message.startswith(FEEDBACK_MODULE_ANSWER) and self._has_feedback_module:
            _LOGGER.debug(f"Feedback module answer: {message}")
            await self._feedback_callback(self._module_group, message)
            return

        if any(refresh in message for refresh in MANUAL_REFRESH_COMMAND):
            _LOGGER.debug(f"Manual refresh command answer: {message}")
            if BUTTON_COMMAND_PREFIX not in message:
                await self.response_queue.put(message)
            return

        _LOGGER.debug(f"Adding unknown message to response queue: {message}")
        await self.response_queue.put(message)

    def _handle_feedback_refresh(self, message: str):
        """Handle feedback refresh commands."""
        module_group_identifier = message[3:5]
        if module_group_identifier == '17':
            self._module_group = 2
        elif module_group_identifier == '12':
            self._module_group = 1
        else:
            _LOGGER.warning(f"Unknown module group identifier: {module_group_identifier}")
