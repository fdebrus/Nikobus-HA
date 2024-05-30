"""Listener for Nikobus"""

import logging
import asyncio
import time

_LOGGER = logging.getLogger(__name__)

__version__ = '0.1'

BUTTON_COMMAND_PREFIX = '#N'
IGNORE_ANSWER = '$0E'
FEEDBACK_MODULE_COMMAND = '$101' # not 10 so we make sure it's followed by 17 or 12 
FEEDBACK_MODULE_ANSWER = '$1C'
CONTROLLER_ADDRESS = '$18'

class NikobusEventListener:

    def __init__(self, hass, nikobus_connection, button_discovery_callback, feedback_callback):
        self._hass = hass
        self._listener_task = None
        self._running = False
        self._button_discovery_callback = button_discovery_callback
        self._feedback_callback = feedback_callback
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

        if message.startswith(BUTTON_COMMAND_PREFIX):
            await self._handle_button_press(message[2:8])

        elif message.startswith(CONTROLLER_ADDRESS):
            controller_address = message[3:7]
            _LOGGER.info(f"Nikobus Controller Address: {controller_address}")

        elif message.startswith(FEEDBACK_MODULE_COMMAND):
            _LOGGER.debug(f"Feedback Module refresh command: {message}")
            module_group_identifier = message[3:5]
            self._module_group = 2 if module_group_identifier == '17' else 1 if module_group_identifier == '12' else None

        elif message.startswith(FEEDBACK_MODULE_ANSWER):
            _LOGGER.debug(f"Feedback Module refresh command answer: {message}")
            await self._feedback_callback(self._module_group, message)

        elif not message.startswith(IGNORE_ANSWER):
            _LOGGER.debug(f"Adding message to response queue: {message}")
            await self.response_queue.put(message)
        else:
            _LOGGER.info(f"Ignored message: {message}")

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