"""Listener for Nikobus."""

import logging
import asyncio
import time

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

class NikobusEventListener:
    def __init__(self, hass, nikobus_connection, button_discovery_callback):
        self._hass = hass
        self._listener_task = None
        self._running = False
        self.nikobus_connection = nikobus_connection
        self.response_queue = asyncio.Queue()
        self._button_discovery_callback = button_discovery_callback
        self._last_nikobus_command_received_timestamp = 0
        self._continuous_press_detected = False

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
                _LOGGER.info("Nikobus event listener has been stopped.")
            self._listener_task = None

    async def listen_for_events(self) -> None:
        """Continuously listen for and handle events from the Nikobus system."""
        _LOGGER.info("Nikobus Event Listener started")
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
                _LOGGER.debug("Listener - Read operation timed out. Waiting for next data...")
            except asyncio.CancelledError:
                _LOGGER.info("Event listener was cancelled.")
                break
            except Exception as e:
                _LOGGER.error(f"Error in event listener: {e}", exc_info=True)
                break

    async def handle_message(self, message: str) -> None:
        """Handle incoming messages from the Nikobus system."""
        _LOGGER.debug(f"Handler got message: {message}")

        _button_command_prefix = '#N'
        _ignore_answer = '$0E'

        if message.startswith(_button_command_prefix):
            address = message[2:8]
            _LOGGER.debug(f"Handling button press for address: {address}")

            self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})

            current_time = time.monotonic()

            # Calculate the time difference since the last command
            time_diff = (current_time - self._last_nikobus_command_received_timestamp) * 1000

            if time_diff > 150:
                # If more than 150ms have passed, treat as a new command or end of a continuous press
                self._last_nikobus_command_received_timestamp = current_time
                if self._continuous_press_detected:
                    self._continuous_press_detected = False
                    _LOGGER.debug("End of Continuous Press Detected")
                else:
                    _LOGGER.debug("Single Press Detected")
                await asyncio.sleep(1)
                await self._button_discovery_callback(address)
            elif time_diff < 100:
                if not self._continuous_press_detected:
                    self._continuous_press_detected = True
                    _LOGGER.debug("Continuous Press Detected - Skipping Processing")
                else:
                    _LOGGER.debug("Continuous Press Ongoing - Skipping Processing")
            else:
                _LOGGER.debug("Command Ignored - Too Close to Previous Command")

        elif not message.startswith(_ignore_answer):
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self.response_queue.put(message)