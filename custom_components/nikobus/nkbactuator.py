import asyncio
import time
import logging

_LOGGER = logging.getLogger(__name__)

from .const import LONG_PRESS_THRESHOLD_MS, DIMMER_DELAY, SHORT_PRESS, MEDIUM_PRESS, LONG_PRESS

class NikobusActuator:
    """Handles button press events for the Nikobus system."""

    def __init__(self, hass, button_discovery_callback):
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._button_discovery_callback = button_discovery_callback
        self._debounce_time_ms = 150
        self._long_press_threshold_ms = LONG_PRESS_THRESHOLD_MS
        self._last_address = None
        self._last_press_time = None
        self._press_task = None
        self._press_task_active = False
        self._timer_tasks = []

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events."""
        _LOGGER.debug(f"Handling button press for address: {address}")

        # Fire event for button press
        # self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})

        current_time = time.monotonic()

        if self._last_address != address:
            self._last_address = address
            self._last_press_time = current_time
            self._start_press_task(address)
            self._start_timer_tasks(address)
        else:
            self._last_press_time = current_time

    def _start_press_task(self, address: str):
        """Start the task that waits for button release."""
        if self._press_task_active:
            return 
        
        self._press_task_active = True

        if self._press_task is not None:
            self._press_task.cancel()

        self._press_task = self._hass.async_create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str):
        """Start timer tasks that fire events after specific durations."""
        for duration in [SHORT_PRESS, MEDIUM_PRESS, LONG_PRESS]:
            task = self._hass.async_create_task(self._fire_event_after_duration(address, duration))
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int):
        """Fire an event after the specified duration."""
        await asyncio.sleep(duration)
        _LOGGER.debug(f"Timer event detected for {duration} seconds for address: {address}")
        self._hass.bus.async_fire(f'nikobus_timer_{duration}', {'address': address})

    async def _wait_for_release(self, address: str):
        """Wait for the button to be released and handle the press duration."""
        try:
            start_time = self._last_press_time
            while True:
                await asyncio.sleep(self._debounce_time_ms / 1000)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000
                
                if time_diff >= self._debounce_time_ms:
                    press_duration = (current_time - start_time)
                    _LOGGER.debug(f"Button release detected for address: {address} - duration: {press_duration:.2f} seconds")

                    if press_duration < SHORT_PRESS:
                        self._handle_short_press(address, press_duration)
                    elif press_duration < MEDIUM_PRESS:
                        self._handle_medium_press(address, press_duration)
                    elif press_duration < LONG_PRESS:
                        _LOGGER.debug(f"Button press detected for 3 seconds for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_3', {'address': address})
                    elif press_duration >= LONG_PRESS:
                        # Fire both 3-second press and long press events
                        _LOGGER.debug(f"Button press detected for 3 seconds for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_3', {'address': address})

                        _LOGGER.debug(f"Button long press detected for address: {address}")
                        self._hass.bus.async_fire('nikobus_long_button_pressed', {'address': address})
                    
                    await self._button_discovery_callback(address)

                    # Reset state and cancel timer tasks
                    self._reset_state()
                    break
                
        except asyncio.CancelledError:
            _LOGGER.debug("Press task cancelled")

    def _handle_short_press(self, address: str, duration: float):
        """Handle a short button press."""
        _LOGGER.debug(f"Button short press detected for address: {address}, duration: {duration:.2f} seconds")
        self._hass.bus.async_fire('nikobus_short_button_pressed', {'address': address})

    def _handle_medium_press(self, address: str, duration: float):
        """Handle a medium button press."""
        if duration < MEDIUM_PRESS:
            _LOGGER.debug(f"Button press detected for 1 second for address: {address}")
            self._hass.bus.async_fire('nikobus_button_pressed_1', {'address': address})
        elif duration < LONG_PRESS:
            _LOGGER.debug(f"Button press detected for 2 seconds for address: {address}")
            self._hass.bus.async_fire('nikobus_button_pressed_2', {'address': address})
        else:
            _LOGGER.debug(f"Button press detected for 3 seconds for address: {address}")
            self._hass.bus.async_fire('nikobus_button_pressed_3', {'address': address})

    def _handle_long_press(self, address: str, duration: float):
        """Handle a long button press."""
        _LOGGER.debug(f"Button long press detected for address: {address}, duration: {duration:.2f} seconds")
        self._hass.bus.async_fire('nikobus_long_button_pressed', {'address': address})

    def _reset_state(self):
        """Reset the state after a button press is handled."""
        self._last_address = None
        self._press_task_active = False
        self._press_task = None

        # Cancel all timer tasks
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()
