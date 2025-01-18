"""Nikobus Button Press Events Handling"""

import asyncio
import time
import logging
from typing import Dict, List, Optional

from homeassistant.core import HomeAssistant

from .const import (
    REFRESH_DELAY,
    DIMMER_DELAY,
    SHORT_PRESS,
    MEDIUM_PRESS,
    LONG_PRESS,
)

_LOGGER = logging.getLogger(__name__)


class NikobusActuator:
    """Handles button press events for the Nikobus system."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        dict_button_data: dict,
        dict_module_data: dict,
    ) -> None:
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._coordinator = coordinator
        self._dict_button_data = dict_button_data
        self._dict_module_data = dict_module_data
        self._debounce_time_ms = 150
        self._last_address: Optional[str] = None
        self._last_press_time: Optional[float] = None
        self._press_task: Optional[asyncio.Task] = None
        self._press_task_active = False
        self._timer_tasks: List[asyncio.Task] = []
        self._fired_timers: Dict[int, bool] = {}

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events while tracking continuous presses and debounce."""
        _LOGGER.debug("Handling button press for address: %s", address)

        current_time = time.monotonic()

        if self._last_address != address:
            self._last_address = address
            self._last_press_time = current_time
            self._start_press_task(address)
            self._start_timer_tasks(address)
        else:
            self._last_press_time = current_time

    def _start_press_task(self, address: str) -> None:
        """Start the Nikobus physical button handling."""
        if self._press_task_active:
            return
        self._press_task_active = True

        if self._press_task:
            self._press_task.cancel()
        
        self._press_task = self._hass.async_create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str) -> None:
        """Start timer tasks for press durations."""
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()
        self._fired_timers = {1: False, 2: False, 3: False}

        for duration in [1, 2, 3]:
            task = self._hass.async_create_task(
                self._fire_event_after_duration(address, duration)
            )
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int) -> None:
        """Fire an event after the specified duration, ensuring it fires only once."""
        await asyncio.sleep(duration)
        if not self._fired_timers[duration]:
            _LOGGER.debug(
                "Firing timer event for %d seconds for address: %s", duration, address
            )
            self._hass.bus.async_fire(
                f"nikobus_button_timer_{duration}", {"address": address}
            )
            self._fired_timers[duration] = True

    async def _wait_for_release(self, address: str) -> None:
        """Wait for button release and process the press duration."""
        try:
            start_time = self._last_press_time
            while True:
                await asyncio.sleep(0.05)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000
                if time_diff >= self._debounce_time_ms:
                    press_duration = current_time - start_time
                    _LOGGER.debug(
                        "Button released for %s, duration: %.2fs",
                        address,
                        press_duration,
                    )

                    # Discover the button after release
                    self._hass.async_create_task(self.button_discovery(address))

                    _LOGGER.debug(
                        "Firing timer event nikobus_button_released for address: %s",
                        address,
                    )
                    self._hass.bus.async_fire(
                        "nikobus_button_released", {"address": address}
                    )

                    self._cancel_unneeded_timers(press_duration)
                    self._fire_duration_event(address, press_duration)
                    break
        except asyncio.CancelledError:
            _LOGGER.warning("Press task for address %s was cancelled", address)
        finally:
            self._reset_state()

    def _cancel_unneeded_timers(self, press_duration: float) -> None:
        """Cancel timers that exceed the actual button press duration."""
        cancel_threshold = (
            SHORT_PRESS
            if press_duration < SHORT_PRESS
            else MEDIUM_PRESS
            if press_duration < MEDIUM_PRESS
            else LONG_PRESS
        )

        for duration in [1, 2, 3]:
            if duration > cancel_threshold and not self._fired_timers[duration]:
                _LOGGER.debug("Cancelling unused timer: %d seconds", duration)
                self._timer_tasks[duration - 1].cancel()

    def _fire_duration_event(self, address: str, press_duration: float) -> None:
        """Fire events based on how long the button was pressed."""
        if press_duration < SHORT_PRESS:
            event_type = "nikobus_short_button_pressed"
        elif press_duration < MEDIUM_PRESS:
            event_type = "nikobus_medium_button_pressed"
        else:
            event_type = "nikobus_long_button_pressed"

        _LOGGER.debug("Firing event %s for address: %s", event_type, address)
        self._hass.bus.async_fire(event_type, {"address": address})

    def _reset_state(self) -> None:
        """Reset tracking state after button release."""
        self._press_task_active = False
        self._press_task = None
        self._last_address = None
        self._last_press_time = None

    def _cancel_unneeded_timers(self, press_duration: float) -> None:
        """Cancel timers that exceed the actual press duration."""
        for task, duration in zip(self._timer_tasks, [1, 2, 3]):
            if duration > press_duration or self._fired_timers[duration]:
                task.cancel()

        self._timer_tasks.clear()

    def _fire_duration_event(self, address: str, press_duration: float) -> None:
        """Fire events based on press duration."""
        event_mapping = {
            "short": "nikobus_short_button_pressed",
            "pressed_1": "nikobus_button_pressed_1",
            "pressed_2": "nikobus_button_pressed_2",
            "pressed_3": "nikobus_button_pressed_3",
            "long": "nikobus_long_button_pressed",
        }

        # Fire specific events based on press duration
        if press_duration <= SHORT_PRESS:
            _LOGGER.debug(f"Firing events {event_mapping['short']} for address: {address}")
            self._hass.bus.async_fire(event_mapping["short"], {"address": address})
        elif press_duration <= MEDIUM_PRESS:
            _LOGGER.debug(f"Firing events {event_mapping['pressed_1']} for address: {address}")
            self._hass.bus.async_fire(event_mapping["pressed_1"], {"address": address})
        elif press_duration <= LONG_PRESS:
            _LOGGER.debug(f"Firing events {event_mapping['pressed_2']} for address: {address}")
            self._hass.bus.async_fire(event_mapping["pressed_2"], {"address": address})
        else:
            _LOGGER.debug(f"Firing events {event_mapping['pressed_3']} for address: {address}")
            self._hass.bus.async_fire(event_mapping["pressed_3"], {"address": address})
            _LOGGER.debug(f"Firing event {event_mapping['long']} for address: {address}")
            self._hass.bus.async_fire(event_mapping["long"], {"address": address})

    def _reset_state(self) -> None:
        """Reset internal state after button release."""
        self._last_address = None
        self._press_task_active = False
        self._press_task = None

        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()

    async def button_discovery(self, address: str) -> None:
        """Discover a button and process it if configured."""
        _LOGGER.debug("Discovering button at address: %s", address)

        button_data = self._dict_button_data.get("nikobus_button", {}).get(address)

        if button_data:
            _LOGGER.debug("Button found in config.")
            await self.process_button_modules(button_data, address)
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
        self, button_data: Dict[str, Optional[str]], button_address: str
    ) -> None:
        """Process actions for each module impacted by the button press."""
        button_operation_time = float(button_data.get("operation_time", 0))
        button_description = button_data.get("description", "Unknown Button")

        _LOGGER.debug(
            "Processing button press for %s with operation time: %.2f",
            button_description,
            button_operation_time,
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

                    event_data = {
                        "address": button_address,
                        "button_operation_time": button_operation_time,
                        "impacted_module_address": impacted_module_address,
                        "impacted_module_group": impacted_group,
                    }

                    _LOGGER.debug(
                        "Firing event: nikobus_button_pressed with data: %s", event_data
                    )
                    self._hass.bus.async_fire("nikobus_button_pressed", event_data)

                    self._coordinator.async_update_listeners()

            except Exception as e:
                _LOGGER.error("Error processing button press: %s", e, exc_info=True)
