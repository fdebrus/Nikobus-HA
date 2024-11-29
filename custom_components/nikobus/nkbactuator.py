"""nkbactuator - Handles Nikobus Button Press"""

import asyncio
import time
import logging
from .const import (
    DOMAIN,
    DIMMER_DELAY,
    SHORT_PRESS,
    MEDIUM_PRESS,
    LONG_PRESS,
)

_LOGGER = logging.getLogger(__name__)

__version__ = "1.0"


class NikobusActuator:
    """Handles button press events for the Nikobus system."""

    def __init__(self, hass, coordinator, dict_button_data, dict_module_data, async_event_handler):
        """Initialize the Nikobus actuator."""
        self._hass = hass
        self._coordinator = coordinator
        self._async_event_handler = async_event_handler
        self._dict_button_data = dict_button_data
        self._dict_module_data = dict_module_data
        self._debounce_time_ms = 150
        self._last_address = None
        self._last_press_time = None
        self._press_task = None
        self._press_task_active = False
        self._timer_tasks = []

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events."""
        _LOGGER.debug(f"Handling button press for address: {address}")

        current_time = time.monotonic()

        # Log debounce and state information
        _LOGGER.debug(
            f"Debounce: last_address={self._last_address}, current_address={address}"
        )

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
            _LOGGER.debug("Press task is already active; skipping task start.")
            return

        self._press_task_active = True

        if self._press_task is not None:
            _LOGGER.debug("Canceling previous press task.")
            self._press_task.cancel()

        _LOGGER.debug("Starting new press task.")
        self._press_task = self._hass.async_create_task(self._wait_for_release(address))

    def _start_timer_tasks(self, address: str):
        """Start timer tasks that fire events after specific durations."""
        _LOGGER.debug(f"Starting timer tasks for address: {address}")
        for duration in [SHORT_PRESS, MEDIUM_PRESS, LONG_PRESS]:
            task = self._hass.async_create_task(
                self._fire_event_after_duration(address, duration)
            )
            self._timer_tasks.append(task)

    async def _fire_event_after_duration(self, address: str, duration: int):
        """Fire an event after the specified duration."""
        await asyncio.sleep(duration)
        _LOGGER.debug(
            f"Timer event detected for {duration} seconds for address: {address}"
        )
        self._hass.bus.async_fire(f"nikobus_timer_{duration}", {"address": address})

    async def _wait_for_release(self, address: str):
        """Wait for the button to be released and handle the press duration."""
        try:
            start_time = self._last_press_time
            while True:
                await asyncio.sleep(self._debounce_time_ms / 1000)
                current_time = time.monotonic()
                time_diff = (current_time - self._last_press_time) * 1000

                if time_diff >= self._debounce_time_ms:
                    press_duration = current_time - start_time
                    _LOGGER.debug(
                        f"Button release detected for address: {address} - duration: {press_duration:.2f} seconds"
                    )

                    if press_duration < SHORT_PRESS:
                        self._handle_short_press(address, press_duration)
                    elif press_duration < MEDIUM_PRESS:
                        self._handle_medium_press(address, press_duration)
                    elif press_duration >= LONG_PRESS:
                        _LOGGER.debug(
                            f"Button press detected for 3 seconds for address: {address}"
                        )
                        self._hass.bus.async_fire(
                            "nikobus_button_pressed_3", {"address": address}
                        )

                    await self.button_discovery(address)
                    break

        except asyncio.CancelledError:
            _LOGGER.debug("Press task cancelled")

        finally:
            self._reset_state()

    def _handle_short_press(self, address: str, duration: float):
        """Handle a short button press."""
        _LOGGER.debug(
            f"Button short press detected for address: {address}, duration: {duration:.2f} seconds"
        )
        self._hass.bus.async_fire("nikobus_short_button_pressed", {"address": address})

    def _handle_medium_press(self, address: str, duration: float):
        """Handle a medium button press."""
        if duration < MEDIUM_PRESS:
            _LOGGER.debug(f"Button press detected for 1 second for address: {address}")
            self._hass.bus.async_fire("nikobus_button_pressed_1", {"address": address})
        elif duration < LONG_PRESS:
            _LOGGER.debug(f"Button press detected for 2 seconds for address: {address}")
            self._hass.bus.async_fire("nikobus_button_pressed_2", {"address": address})
        else:
            _LOGGER.debug(f"Button press detected for 3 seconds for address: {address}")
            self._hass.bus.async_fire("nikobus_button_pressed_3", {"address": address})

    def _reset_state(self):
        """Reset the state after a button press is handled."""
        _LOGGER.debug("Resetting button press state.")
        self._last_address = None
        self._press_task_active = False
        self._press_task = None

        # Cancel all timer tasks
        for task in self._timer_tasks:
            task.cancel()
        self._timer_tasks.clear()

    async def button_discovery(self, address: str) -> None:
        """Discover a button and process it if configured."""
        _LOGGER.debug(f"Discovering button at address: {address}.")

        if address in self._dict_button_data.get("nikobus_button", {}):
            _LOGGER.debug(
                f"Button at address {address} found in configuration. Processing..."
            )
            await self.process_button_modules(
                self._dict_button_data["nikobus_button"][address], address
            )
        else:
            _LOGGER.info(
                f"No existing configuration found for button at address {address}. Adding new configuration"
            )
            new_button = {
                "description": f"DISCOVERED - Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}],
            }
            if "nikobus_button" not in self._dict_button_data:
                self._dict_button_data["nikobus_button"] = {}
            self._dict_button_data["nikobus_button"][address] = new_button
            await self._coordinator.nikobus_config.write_json_data(
                "nikobus_button_config.json", "button", self._dict_button_data
            )
            _LOGGER.debug(f"New button configuration added for address {address}.")

    async def process_button_modules(self, button: dict, address: str) -> None:
        """Process actions for each module impacted by the button press."""
        button_description = button.get("description")
        _LOGGER.debug(f"Processing button press for {button_description}")

        operation_time = float(button.get("operation_time", 0))

        for impacted_module_info in button.get("impacted_module", []):
            impacted_module_address = impacted_module_info.get("address")
            impacted_group = impacted_module_info.get("group")

            if not impacted_module_info or not (
                impacted_module_address or impacted_group
            ):
                _LOGGER.debug("Skipping module refresh due to missing address or group")
                operation_time = float(
                    self._dict_button_data.get("nikobus_button", {})
                    .get(address, {})
                    .get("operation_time", 0)
                )

                _LOGGER.debug(f"Firing event with {address} {operation_time} ")

                self._hass.bus.async_fire(
                    "nikobus_button_pressed",
                    {"address": address, "operation_time": operation_time},
                )
                continue

            try:
                _LOGGER.debug(
                    f"*** Refreshing status for module {impacted_module_address} for group {impacted_group}"
                )

                if impacted_module_address in self._dict_module_data.get(
                    "dimmer_module", {}
                ):
                    _LOGGER.debug("Dimmer DETECTED - pausing to get final status")
                    await asyncio.sleep(DIMMER_DELAY)

                value = await self._coordinator.nikobus_command_handler.get_output_state(
                    impacted_module_address, impacted_group
                )

                _LOGGER.debug(f"VALUE RECEIVED: {value}")

                if value is not None:
                    self._coordinator.set_bytearray_group_state(
                        impacted_module_address, impacted_group, value
                    )
                    await self._async_event_handler(
                        "nikobus_button_pressed",
                        {
                            "address": address,
                            "operation_time": operation_time,
                            "impacted_module_address": impacted_module_address,
                        },
                    )

            except Exception as e:
                _LOGGER.error(
                    f"Error processing button press for module {impacted_module_address} group {impacted_group} error: {e}"
                )
