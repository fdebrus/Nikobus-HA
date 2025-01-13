"""nkbactuator - Handles Nikobus Button Press"""

import asyncio
import time
import logging
from .const import (
    REFRESH_DELAY,
    DIMMER_DELAY,
    SHORT_PRESS,
    MEDIUM_PRESS,
    LONG_PRESS,
)

_LOGGER = logging.getLogger(__name__)

__version__ = "1.1"

class NikobusActuator:
    """Handles button press events for the Nikobus system."""

    def __init__(self, hass, coordinator, dict_button_data, dict_module_data):
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._dict_module_data = dict_module_data
        self._debounce_time_ms = 150
        self._last_address = None
        self._last_press_time = None
        self._press_task = None
        self._press_task_active = False
        self._timer_tasks = []
        self._fired_timers = {}

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events while tracking continuous presses and debounce."""
        _LOGGER.debug(f"Handling button press for address: {address}")
        
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

        if self._press_task:
            self._press_task.cancel()

        self._press_task = self._hass.async_create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str):
        """Start timer tasks for press durations."""
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()
        self._fired_timers = {1: False, 2: False, 3: False}

        for duration in [1, 2, 3]:
            task = self._hass.async_create_task(self._fire_event_after_duration(address, duration))
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int):
        """Fire an event after the specified duration, ensuring it fires only once."""
        await asyncio.sleep(duration)
        if not self._fired_timers[duration]:
            _LOGGER.debug(f"Firing timer event for {duration} seconds for address: {address}")
            self._hass.bus.async_fire(f"nikobus_button_timer_{duration}", {"address": address})
            self._fired_timers[duration] = True

    async def _wait_for_release(self, address: str):
        """Wait for button release and process the press duration."""
        try:
            start_time = self._last_press_time
            while True:
                await asyncio.sleep(0.05)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000
                if time_diff >= self._debounce_time_ms:
                    press_duration = current_time - start_time
                    _LOGGER.debug(f"Button released for {address}, duration: {press_duration:.2f}s")
                    # Trigger the button discovery task only after release
                    self._hass.async_create_task(self.button_discovery(address))
                    self._hass.bus.async_fire("nikobus_button_released", {"address": address})
                    self._cancel_unneeded_timers(press_duration)
                    self._fire_duration_event(address, press_duration)
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._reset_state()

    def _cancel_unneeded_timers(self, press_duration: float):
        """Cancel timers that exceed the actual press duration."""
        self._timer_tasks = [task for task, duration in zip(self._timer_tasks, [1, 2, 3])
                            if duration > press_duration or self._fired_timers[duration]]

    def _fire_duration_event(self, address: str, press_duration: float):
        """Fire events based on press duration."""
        if press_duration < SHORT_PRESS:
            self._hass.bus.async_fire("nikobus_short_button_pressed", {"address": address})
        elif press_duration < MEDIUM_PRESS:
            self._hass.bus.async_fire("nikobus_button_pressed_1", {"address": address})
        elif press_duration < LONG_PRESS:
            self._hass.bus.async_fire("nikobus_button_pressed_2", {"address": address})
        else:
            self._hass.bus.async_fire("nikobus_button_pressed_3", {"address": address})
            self._hass.bus.async_fire("nikobus_long_button_pressed", {"address": address})

    def _reset_state(self):
        """Reset internal state after button release."""
        self._last_address = None
        self._press_task_active = False
        self._press_task = None
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()

    async def button_discovery(self, address: str) -> None:
        """Discover a button and process it if configured."""
        _LOGGER.debug(f"Discovering button at address: {address}.")
        if address in self._dict_button_data.get("nikobus_button", {}):
            _LOGGER.debug("Button found in config.")
            await self.process_button_modules(
                self._dict_button_data["nikobus_button"][address], address
            )
        else:
            _LOGGER.debug("Creating new button in config.")
            new_button = {
                "description": f"DISCOVERED - Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}],
            }
            self._dict_button_data.setdefault("nikobus_button", {})[address] = (
                new_button
            )
            await self._coordinator.nikobus_config.write_json_data(
                "nikobus_button_config.json", "button", self._dict_button_data
            )

    async def process_button_modules(
        self, button_data: dict, button_address: str
    ) -> None:
        """Process actions for each module impacted by the button press."""

        button_operation_time = float(button_data.get("operation_time", 0))
        button_description = button_data.get("description")

        _LOGGER.debug(
            f"Processing button press for {button_description} with operation time: {button_operation_time}"
        )

        for impacted_module_info in button_data.get("impacted_module", []):
            impacted_module_address = impacted_module_info.get("address")
            impacted_group = impacted_module_info.get("group")

            if not impacted_module_address or not impacted_group:
                _LOGGER.debug("Skipping module refresh due to missing address or group")
                continue
            try:

                if impacted_module_address in self._dict_module_data.get(
                    "dimmer_module", {}
                ):
                    _LOGGER.debug("Dimmer DETECTED - pausing to get final status")
                    await asyncio.sleep(DIMMER_DELAY)
                else:
                    await asyncio.sleep(REFRESH_DELAY)

                value = (
                    await self._coordinator.nikobus_command_handler.get_output_state(
                        impacted_module_address, impacted_group
                    )
                )
                if value is not None:
                    self._coordinator.set_bytearray_group_state(
                        impacted_module_address, impacted_group, value
                    )
                    _LOGGER.debug(
                        f"Firing event: nikobus_button_pressed with data: address: {button_address} button_operation_time: {button_operation_time} impacted_module_address: {impacted_module_address} impacted_module_group: {impacted_group}"
                    )
                    self._hass.bus.async_fire(
                        "nikobus_button_pressed",
                        {
                            "address": button_address,
                            "button_operation_time": button_operation_time,
                            "impacted_module_address": impacted_module_address,
                            "impacted_module_group": impacted_group,
                        },
                    )
            except Exception as e:
                _LOGGER.error(f"Error processing button press: {e}")
