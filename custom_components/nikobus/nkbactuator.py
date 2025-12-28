"""Nikobus Button Press Events Handling"""

import asyncio
import time
import logging
from typing import Dict, List, Optional

from homeassistant.core import HomeAssistant
from custom_components.nikobus.exceptions import NikobusTimeoutError

from .const import REFRESH_DELAY, DIMMER_DELAY, SHORT_PRESS, LONG_PRESS

_LOGGER = logging.getLogger(__name__)
TIMER_DURATIONS = (1, 2, 3)


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
        self._last_release_times: Dict[str, float] = {}
        self._press_task: Optional[asyncio.Task] = None
        self._press_task_active = False
        self._timer_tasks: List[asyncio.Task] = []
        self._fired_timers: Dict[int, bool] = {}

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events while tracking continuous presses and debounce."""
        _LOGGER.debug("Handling button press for address: %s", address)
        current_time = time.monotonic()

        if self._last_address != address:
            self._fire_press_interval_event(address, current_time)
            self._last_address = address
            self._last_press_time = current_time
            self._start_press_task(address)
            self._start_timer_tasks(address)
        else:
            self._last_press_time = current_time

    def _fire_press_interval_event(self, address: str, current_time: float) -> None:
        """Fire an event describing how long it has been since the last release."""

        last_release_time = self._last_release_times.get(address)

        if last_release_time is None:
            _LOGGER.debug(
                "No previous release time recorded for address %s; skipping interval event",
                address,
            )
            return

        time_since_release = current_time - last_release_time
        if time_since_release < 1:
            event_type = "nikobus_button_pressed_0"
        elif time_since_release < 2:
            event_type = "nikobus_button_pressed_1"
        elif time_since_release < 3:
            event_type = "nikobus_button_pressed_2"
        else:
            event_type = "nikobus_button_pressed_3"

        _LOGGER.debug(
            "Firing press interval event %s for address %s (%.2fs since release)",
            event_type,
            address,
            time_since_release,
        )
        self._hass.bus.async_fire(
            event_type,
            {"address": address, "time_since_last_release": time_since_release},
        )

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
        self._fired_timers = {duration: False for duration in TIMER_DURATIONS}

        for duration in TIMER_DURATIONS:
            task = self._hass.async_create_task(
                self._fire_event_after_duration(address, duration)
            )
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int) -> None:
        """Fire an event after the specified duration, ensuring it fires only once."""
        await asyncio.sleep(duration)
        if not self._fired_timers.get(duration, False):
            event_type = f"nikobus_button_timer_{duration}"
            _LOGGER.debug(
                "Firing event %s for %d seconds for address: %s",
                event_type,
                duration,
                address,
            )
            self._hass.bus.async_fire(
                event_type, {"address": address}
            )
            self._fired_timers[duration] = True

    async def _wait_for_release(self, address: str) -> None:
        """Wait for button release and process the press duration."""
        try:
            start_time = self._last_press_time
            if start_time is None:
                _LOGGER.debug(
                    "Ignoring wait_for_release because press time is missing for %s",
                    address,
                )
                return
            while True:
                await asyncio.sleep(0.05)
                current_time = time.monotonic()
                last_press_time = self._last_press_time
                if last_press_time is None:
                    break
                time_diff = (current_time - last_press_time) * 1000
                if time_diff >= self._debounce_time_ms:
                    press_duration = current_time - start_time
                    _LOGGER.debug(
                        "Button released for %s, duration: %.2fs",
                        address,
                        press_duration,
                    )

                    # Trigger discovery process after release
                    self._hass.async_create_task(self.button_discovery(address))

                    _LOGGER.debug(
                        "Firing event nikobus_button_released for address: %s", address
                    )
                    self._hass.bus.async_fire(
                        "nikobus_button_released", {"address": address}
                    )

                    self._cancel_unneeded_timers(press_duration)
                    self._fire_duration_event(address, press_duration)
                    self._last_release_times[address] = current_time
                    break
        except asyncio.CancelledError:
            _LOGGER.warning("Press task for address %s was cancelled", address)
        finally:
            self._reset_state()

    def _cancel_unneeded_timers(self, press_duration: float) -> None:
        """Cancel timer tasks that exceed the actual press duration."""
        for task, duration in zip(self._timer_tasks, TIMER_DURATIONS):
            if duration > press_duration or self._fired_timers.get(duration, False):
                task.cancel()
        self._timer_tasks.clear()

    def _fire_duration_event(self, address: str, press_duration: float) -> None:
        """Fire events based on press duration."""
        if press_duration <= SHORT_PRESS:
            event_type = "nikobus_short_button_pressed"
            _LOGGER.debug("Firing event %s for address: %s", event_type, address)
            self._hass.bus.async_fire(event_type, {"address": address})
        elif press_duration >= LONG_PRESS:
            _LOGGER.debug(
                "Firing event nikobus_long_button_pressed for address: %s", address
            )
            self._hass.bus.async_fire(
                "nikobus_long_button_pressed", {"address": address}
            )

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
            try:
                await self._coordinator.nikobus_config.write_json_data(
                    "nikobus_button_config.json", "button", self._dict_button_data
                )
            except Exception as e:
                _LOGGER.error("Error writing new button config: %s", e, exc_info=True)
                # Optionally, re-raise a custom exception if needed

    async def process_button_modules(
        self, button_data: Dict[str, Optional[str]], button_address: str
    ) -> None:
        """Process actions for each module impacted by the button press."""
        last_release_time = self._last_release_times.get(button_address)
        time_since_last_release: Optional[float] = None
        if last_release_time is not None:
            time_since_last_release = time.monotonic() - last_release_time
        try:
            button_operation_time = float(button_data.get("operation_time", 0))
        except ValueError as e:
            _LOGGER.error(
                "Invalid operation time for button %s: %s",
                button_address,
                e,
                exc_info=True,
            )
            button_operation_time = 0.0

        button_description = button_data.get("description", "Unknown Button")
        _LOGGER.debug(
            "Processing button press for %s with operation time: %.2f",
            button_description,
            button_operation_time,
        )

        event_fired = False

        # Build a list of modules to process.
        modules_to_process = []
        impacted_modules = button_data.get("impacted_module", [])
        # Check if any impacted_module entry is missing address or group.
        incomplete = any(
            not mod.get("address") or not mod.get("group") for mod in impacted_modules
        )
        if impacted_modules and not incomplete:
            modules_to_process.extend(impacted_modules)
        else:
            _LOGGER.debug(
                "impacted_module is incomplete; falling back to discovered_link data."
            )
            # Use discovered_link entries as fallback.
            for link in button_data.get("discovered_link", []):
                module_addr = link.get("module_address")
                channel_str = link.get("channel", "")
                # Attempt to extract the channel number from a string like "Channel 1"
                try:
                    # Assume the number is the last token
                    channel_number = int(channel_str.split()[-1])
                except (ValueError, IndexError, AttributeError):
                    _LOGGER.debug("Unable to parse channel number from %s", channel_str)
                    continue

                fallback_group = "1" if channel_number <= 6 else "2"
                modules_to_process.append(
                    {"address": module_addr, "group": fallback_group}
                )

        for module_info in modules_to_process:
            impacted_module_address = module_info.get("address")
            impacted_group = module_info.get("group")

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

                try:
                    value = await self._coordinator.nikobus_command.get_output_state(
                        impacted_module_address, impacted_group
                    )
                except NikobusTimeoutError as error:
                    _LOGGER.error(
                        "Timeout getting output state for module %s: %s",
                        impacted_module_address,
                        error,
                    )
                    value = None

                if value is not None:
                    self._coordinator.set_bytearray_group_state(
                        impacted_module_address, impacted_group, value
                    )

                event_data = {
                    "address": button_address,
                    "button_operation_time": button_operation_time,
                    "impacted_module_address": impacted_module_address,
                    "impacted_module_group": impacted_group,
                    "time_since_last_release": time_since_last_release,
                }

                # if button_data.get("led_on") or button_data.get("led_off"):
                #     event_data["virtual"] = True

                _LOGGER.debug(
                    "Firing event: nikobus_button_pressed with data: %s", event_data
                )
                self._hass.bus.async_fire("nikobus_button_pressed", event_data)
                event_fired = True

            except Exception as e:
                _LOGGER.error(
                    "Error processing button press for module %s: %s",
                    impacted_module_address,
                    e,
                    exc_info=True,
                )

        if not event_fired:
            minimal_event_data = {
                "address": button_address,
                "time_since_last_release": time_since_last_release,
            }
            _LOGGER.debug(
                "Firing minimal event: nikobus_button_pressed with data: %s",
                minimal_event_data,
            )
            self._hass.bus.async_fire("nikobus_button_pressed", minimal_event_data)

        self._coordinator.async_update_listeners()
