import asyncio
import time
import logging

_LOGGER = logging.getLogger(__name__)

from .const import LONG_PRESS_THRESHOLD_MS

class NikobusActuator:

    def __init__(self, hass, button_discovery_callback):
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
        self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})

        current_time = time.monotonic()

        if self._last_address != address:
            self._last_address = address
            self._last_press_time = current_time
            self._start_press_task(address)
            self._start_timer_tasks(address)
        else:
            self._last_press_time = current_time

    def _start_press_task(self, address: str):
        if self._press_task_active:
            return 
        
        self._press_task_active = True

        if self._press_task is not None:
            self._press_task.cancel()

        self._press_task = asyncio.create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str):
        for duration in [1, 2, 3]:
            task = asyncio.create_task(self._fire_event_after_duration(address, duration))
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int):
        await asyncio.sleep(duration)
        _LOGGER.debug(f"Timer event detected for {duration} seconds for address: {address}")
        self._hass.bus.async_fire(f'nikobus_timer_{duration}_seconds', {'address': address})

    async def _wait_for_release(self, address: str):
        try:
            start_time = self._last_press_time
            while True:    
                await asyncio.sleep(self._debounce_time_ms / 1000)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000
                
                if time_diff >= self._debounce_time_ms:
                    # Calculate press duration
                    press_duration = (current_time - start_time)

                    _LOGGER.debug(f"Button release detected for address: {address} - {press_duration}")

                    if press_duration < 1:
                        _LOGGER.debug(f"Button press detected for less than 1 second for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_0', {'address': address})
                    elif press_duration < 2:
                        _LOGGER.debug(f"Button press detected for 1 second for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_1', {'address': address})
                    elif press_duration < 3:
                        _LOGGER.debug(f"Button press detected for 2 seconds for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_2', {'address': address})
                    elif press_duration >= 3:
                        _LOGGER.debug(f"Button press detected for 3 seconds for address: {address}")
                        self._hass.bus.async_fire('nikobus_button_pressed_3', {'address': address})
                    
                    if press_duration >= self._long_press_threshold_ms / 1000:
                        _LOGGER.debug(f"Button long press detected for address: {address}")
                        self._hass.bus.async_fire('nikobus_long_button_pressed', {'address': address})
                    else:
                        _LOGGER.debug(f"Button short press detected for address: {address}")
                        self._hass.bus.async_fire('nikobus_short_button_pressed', {'address': address})
                    
                    await self._button_discovery_callback(address)

                    self._last_address = None
                    self._press_task_active = False
                    self._press_task = None

                    # Cancel all timer tasks
                    for task in self._timer_tasks:
                        task.cancel()
                    self._timer_tasks.clear()

                    break
                
        except asyncio.CancelledError:
            _LOGGER.debug("Press task cancelled")
