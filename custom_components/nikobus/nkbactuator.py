"""nkbactuator - Handles Nikobus Button Press"""

import asyncio
import time
import logging
from .const import (
    REFRESH_DELAY,
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
        self._debounce_time_ms = 100
        self._last_address = None
        self._last_press_time = None
        self._press_start_time = None
        self._press_task = None
        self._press_task_active = False
        self._timer_tasks = []
        self._fired_timers = {}  # Track fired timers

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events while ensuring correct duration tracking."""
        _LOGGER.debug(f"Handling button press for address: {address}")

        current_time = time.monotonic()

        if self._last_address != address:
            # First press: set start time and start timers
            self._last_address = address
            self._last_press_time = current_time
            self._press_start_time = current_time  # Reset press start time for every press
            _LOGGER.debug(f"New press detected, setting _press_start_time to {self._press_start_time:.4f}")

            self._start_press_task(address)
            self._start_timer_tasks(address)
    
        else:
            # If the same button is pressed again, reset timers and track it as a new press
            _LOGGER.debug(f"Button {address} was pressed again, resetting press tracking")
            self._last_press_time = current_time
            self._press_start_time = current_time  # Reset on every new press
            self._start_timer_tasks(address)  # Restart timers

    def _start_press_task(self, address: str):
        """Start the task that waits for button release."""
        if self._press_task_active:
            return
        
        self._press_task_active = True
        
        if self._press_task is not None:
            self._press_task.cancel()

        self._press_task = self._hass.async_create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str):
        """Cancel previous timer tasks and start new ones, ensuring only one event per duration."""
        # Cancel all previous timer tasks
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()

        # Reset fired timers tracking
        self._fired_timers = {1: False, 2: False, 3: False}

        # Start new timer tasks
        for duration in [1, 2, 3]:
            task = self._hass.async_create_task(
                self._fire_event_after_duration(address, duration)
            )
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int):
        """Fire an event after the specified duration, ensuring it fires only once."""
        await asyncio.sleep(duration)

        # Ensure we haven't already fired this event during the press
        if not self._fired_timers[duration]:
            _LOGGER.debug(f"Firing event: nikobus_button_timer_{duration} with data: address: {address}")
            self._hass.bus.async_fire(f"nikobus_button_timer_{duration}", {"address": address})
            self._fired_timers[duration] = True  # Mark this event as fired

    async def _wait_for_release(self, address: str):
        """Wait for the button to be released and handle the press duration correctly."""
        self._hass.async_create_task(self.button_discovery(address))
        try:
            start_time = self._press_start_time  # Use the original press start time

            while True:
                await asyncio.sleep(0.05)  # Keep checking for release
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000  # Time since last press

                if time_diff >= self._debounce_time_ms:
                    press_duration = current_time - start_time  # Compute from original press start

                    _LOGGER.debug(f"🔹 Button Released: {address}, Press Duration: {press_duration:.2f}s")

                    self._hass.bus.async_fire("nikobus_button_released", {"address": address})

                    # Cancel only timers that exceed the press duration
                    self._cancel_unneeded_timers(press_duration)

                    # 🔹 Fix: Ensure only correct button event fires
                    if press_duration < SHORT_PRESS:
                        _LOGGER.debug(f"Firing event: nikobus_short_button_pressed with data: address: {address}")
                        self._hass.bus.async_fire("nikobus_short_button_pressed", {"address": address})

                    elif press_duration < MEDIUM_PRESS:
                        _LOGGER.debug(f"Firing event: nikobus_button_pressed_1 with data: address: {address}")
                        self._hass.bus.async_fire("nikobus_button_pressed_1", {"address": address})

                    elif press_duration < LONG_PRESS:
                        _LOGGER.debug(f"Firing event: nikobus_button_pressed_2 with data: address: {address}")
                        self._hass.bus.async_fire("nikobus_button_pressed_2", {"address": address})

                    else:
                        _LOGGER.debug(f"Firing event: nikobus_button_pressed_3 with data: address: {address}")
                        self._hass.bus.async_fire("nikobus_button_pressed_3", {"address": address})
                        _LOGGER.debug(f"Firing event: nikobus_long_button_pressed with data: address: {address}")
                        self._hass.bus.async_fire("nikobus_long_button_pressed", {"address": address})

                    break

        except asyncio.CancelledError:
            pass
        finally:
            self._reset_state()


    def _cancel_unneeded_timers(self, press_duration: float):
        """Cancel only timers that exceed the press duration and haven't fired yet."""
        remaining_timers = []
    
        for task, duration in zip(self._timer_tasks, [1, 2, 3]):  # Map timers to their durations
            if press_duration < duration and not self._fired_timers[duration]:  
                _LOGGER.debug(f"Cancelling timer {duration}s because press duration was {press_duration:.2f}s")
                task.cancel()
            else:
                remaining_timers.append(task)  # Keep timers that should still run

        self._timer_tasks = remaining_timers  # Keep only the valid timers

    def _reset_state(self):
        """Reset the state after a button press is handled."""
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
            _LOGGER.debug(f"Button found in config.")
            await self.process_button_modules(
                self._dict_button_data["nikobus_button"][address], address
            )
        else:
            _LOGGER.debug(f"Creating new button in config.")
            new_button = {
                "description": f"DISCOVERED - Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}],
            }
            self._dict_button_data.setdefault("nikobus_button", {})[address] = new_button
            await self._coordinator.nikobus_config.write_json_data(
                "nikobus_button_config.json", "button", self._dict_button_data
            )

    async def process_button_modules(self, button_data: dict, button_address: str) -> None:
        """Process actions for each module impacted by the button press."""

        button_operation_time = float(button_data.get("operation_time", 0))
        button_description = button_data.get("description")

        _LOGGER.debug(f"Processing button press for {button_description} with operation time: {button_operation_time}")
    
        for impacted_module_info in button_data.get("impacted_module", []):
            impacted_module_address = impacted_module_info.get("address")
            impacted_group = impacted_module_info.get("group")

            if not impacted_module_address or not impacted_group:
                _LOGGER.debug("Skipping module refresh due to missing address or group")
                continue
            try:
                await asyncio.sleep(0.25) # is it enough for Dimmer ?? 
                value = await self._coordinator.nikobus_command_handler.get_output_state(
                    impacted_module_address, impacted_group
                )
                if value is not None:
                    self._coordinator.set_bytearray_group_state(
                        impacted_module_address, impacted_group, value
                    )
                    _LOGGER.debug(f"Firing event: nikobus_button_pressed with data: address: {button_address} button_operation_time: {button_operation_time} impacted_module_address: {impacted_module_address} impacted_module_group: {impacted_group}")
                    self._hass.bus.async_fire(
                        "nikobus_button_pressed",
                        {
                            "address": button_address,
                            "button_operation_time": button_operation_time,
                            "impacted_module_address": impacted_module_address,
                            "impacted_module_group": impacted_group
                        },
                    )
            except Exception as e:
                _LOGGER.error(f"Error processing button press: {e}")
