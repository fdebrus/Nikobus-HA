"""Listener for Nikobus"""

import logging
import asyncio
import time

from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

from .const import (
        CONF_HAS_FEEDBACK_MODULE, 
        BUTTON_COMMAND_PREFIX, 
        IGNORE_ANSWER, 
        FEEDBACK_REFRESH_COMMAND,
        MANUAL_REFRESH_COMMAND, 
        FEEDBACK_MODULE_ANSWER, 
        COMMAND_PROCESSED, 
        CONTROLLER_ADDRESS )

class NikobusEventListener:

    def __init__(self, hass, config_entry: ConfigEntry, nikobus_connection, button_discovery_callback, feedback_callback):
        self._hass = hass
        self._config_entry = config_entry
        self._listener_task = None
        self._running = False
        self._button_discovery_callback = button_discovery_callback
        self._feedback_callback = feedback_callback
        self._has_feedback_module = None
        self._module_group = 1
        self.nikobus_connection = nikobus_connection
        self.response_queue = asyncio.Queue()
        self._debounce_time_ms = 150
        self._last_address = None
        self._last_press_time = None
        self._press_task = None

    async def start(self):
        self._running = True
        self._listener_task = self._hass.loop.create_task(self.listen_for_events())

    async def stop(self):
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                _LOGGER.info("Nikobus event listener has been stopped")
            self._listener_task = None

    async def listen_for_events(self) -> None:
        """Continuously listen for and handle events from the Nikobus system"""
        _LOGGER.info("Nikobus Event Listener starting")
        while self._running:
            try:
                data = await asyncio.wait_for(self.nikobus_connection.read(), timeout=10)
                if not data:
                    _LOGGER.warning("Nikobus connection closed")
                    break
                message = data.decode('utf-8').strip()
                _LOGGER.debug(f"Listener - Receiving message: {message}")
                self._hass.async_create_task(self.handle_message(message))
            except asyncio.TimeoutError:
                # _LOGGER.debug("Listener - Read operation timed out. Waiting for next data...")
                pass
            except asyncio.CancelledError:
                _LOGGER.info("Event listener was cancelled")
                break
            except Exception as e:
                _LOGGER.error(f"Error in event listener: {e}", exc_info=True)
                break

    async def handle_message(self, message: str) -> None:
        """Handle incoming messages from the Nikobus system"""

        self._has_feedback_module = self._config_entry.options.get(CONF_HAS_FEEDBACK_MODULE, self._config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))

        if message.startswith(BUTTON_COMMAND_PREFIX):
            await self._handle_button_press(message[2:8])
            return

        if message.startswith(IGNORE_ANSWER) or message.startswith(COMMAND_PROCESSED):
            _LOGGER.info(f"Ignored message: {message}")
            return

        if message.startswith(CONTROLLER_ADDRESS):
            _LOGGER.info(f"Nikobus Controller Address: {message[3:7]}")
            return

        elif message.startswith(FEEDBACK_REFRESH_COMMAND):
            if self._has_feedback_module:
                _LOGGER.debug(f"** Feedback module refresh command: {message}")
                module_group_identifier = message[3:5]
                self._module_group = 2 if module_group_identifier == '17' else 1 if module_group_identifier == '12' else None
            return

        elif message.startswith(FEEDBACK_MODULE_ANSWER):
            if self._has_feedback_module:
                _LOGGER.debug(f"** Feedback module refresh command answer: {message}")
                await self._feedback_callback(self._module_group, message)
            return

        elif any(refresh in message for refresh in MANUAL_REFRESH_COMMAND):
            _LOGGER.debug(f"Manual refresh command answer: {message}")
            await self.response_queue.put(message)

            if self._has_feedback_module:
                feedback_sequence = message[-27:]
                _LOGGER.debug(f"** Feedback led dedicated refresh: {feedback_sequence}")
                await self.nikobus_connection.send(feedback_sequence)
        else:
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self.response_queue.put(message)

    async def _handle_button_press(self, address: str) -> None:
        """Handle button press events."""
        _LOGGER.debug(f"Handling button press for address: {address}")

        # This is needed for the automation to catch a button press, we fire an event with the button address.
        self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})

        current_time = time.monotonic()
        
        if self._last_address != address:
            self._last_address = address
            self._last_press_time = current_time
            self._start_press_task(address)
        else:
            self._last_press_time = current_time

    def _start_press_task(self, address: str):
        """Start a task to wait for the button release."""
        if self._press_task is not None:
            self._press_task.cancel()

        self._press_task = asyncio.create_task(self._wait_for_release(address))

    async def _wait_for_release(self, address: str):
        """Wait for the button release by ensuring no new presses within the debounce time."""
        try:
            while True:
                await asyncio.sleep(self._debounce_time_ms / 1000)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000
                
                if time_diff >= self._debounce_time_ms:
                    _LOGGER.debug(f"Button release detected for address: {address}")
                    self._hass.bus.async_fire('nikobus_button_released', {'address': address})
                    await self._button_discovery_callback(address)
                    self._last_address = None
                    self._press_task = None
                    break
        except asyncio.CancelledError:
            _LOGGER.debug("Press task cancelled")