"""Nikobus Button Press Events Handling."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from homeassistant.core import HomeAssistant
from custom_components.nikobus.exceptions import NikobusTimeoutError

from .const import BUTTON_TIMER_THRESHOLDS, DIMMER_DELAY, REFRESH_DELAY, SHORT_PRESS

_LOGGER = logging.getLogger(__name__)


@dataclass
class PressState:
    """Track the state of an in-flight button press."""

    address: str
    press_start: float
    last_press_time: float
    press_id: str
    module_address: Optional[str]
    channel: Optional[int]
    release_task: Optional[asyncio.Task] = None
    timer_tasks: Dict[int, asyncio.Task] = field(default_factory=dict)
    last_timer_threshold: int = 0


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
        self._press_states: Dict[str, PressState] = {}
        self._last_press_context: Dict[str, Dict[str, Optional[float | str | int]]] = {}

    async def handle_button_press(self, address: str) -> None:
        """Handle button press events while tracking continuous presses and debounce."""
        normalized_address = address.upper()
        _LOGGER.debug("Handling button press for address: %s", normalized_address)
        current_time = time.monotonic()

        if normalized_address in self._press_states:
            state = self._press_states[normalized_address]
            state.last_press_time = current_time
            _LOGGER.debug(
                "Duplicate press frame received for %s; maintaining press_id=%s",
                normalized_address,
                state.press_id,
            )
            return

        module_address, channel = self._derive_button_context(normalized_address)
        press_id = f"{normalized_address}-{current_time:.3f}-{uuid.uuid4().hex[:8]}"
        state = PressState(
            address=normalized_address,
            press_start=current_time,
            last_press_time=current_time,
            press_id=press_id,
            module_address=module_address,
            channel=channel,
        )
        self._press_states[normalized_address] = state

        state.release_task = self._hass.async_create_task(self._wait_for_release(state))
        self._start_timer_tasks(state)
        self._fire_event(
            "nikobus_button_pressed",
            state,
            state_value="pressed",
            duration=None,
        )

    def _start_timer_tasks(self, state: PressState) -> None:
        """Start timer tasks for press durations."""
        for duration in BUTTON_TIMER_THRESHOLDS:
            task = self._hass.async_create_task(
                self._fire_timer_event(state.address, state.press_id, duration)
            )
            state.timer_tasks[duration] = task

    async def _fire_timer_event(
        self, address: str, press_id: str, duration: int
    ) -> None:
        """Fire timer milestone events when the press crosses a threshold."""
        try:
            remaining = duration
            state = self._press_states.get(address)
            if state and state.press_id == press_id:
                elapsed = time.monotonic() - state.press_start
                remaining = max(duration - elapsed, 0)

            await asyncio.sleep(remaining)
            state = self._press_states.get(address)
            if not state or state.press_id != press_id:
                return
            if state.last_timer_threshold >= duration:
                return

            state.last_timer_threshold = duration
            self._fire_event(
                f"nikobus_button_timer_{duration}",
                state,
                state_value="timer",
                duration=time.monotonic() - state.press_start,
                threshold=duration,
            )
        except asyncio.CancelledError:
            _LOGGER.debug(
                "Timer task cancelled for address %s at threshold %s", address, duration
            )

    async def _wait_for_release(self, state: PressState) -> None:
        """Wait for button release and process the press duration."""
        try:
            while True:
                await asyncio.sleep(0.05)
                active_state = self._press_states.get(state.address)
                if not active_state or active_state.press_id != state.press_id:
                    return

                time_diff = (time.monotonic() - active_state.last_press_time) * 1000
                if time_diff >= self._debounce_time_ms:
                    press_duration = time.monotonic() - active_state.press_start
                    _LOGGER.debug(
                        "Button released for %s, duration: %.2fs", state.address, press_duration
                    )
                    await self._handle_release(active_state, press_duration)
                    return
        except asyncio.CancelledError:
            _LOGGER.warning("Press task for address %s was cancelled", state.address)

    async def _handle_release(self, state: PressState, press_duration: float) -> None:
        """Handle logic executed once a release is detected."""
        for task in state.timer_tasks.values():
            task.cancel()
        state.timer_tasks.clear()

        self._fire_event(
            "nikobus_button_released",
            state,
            state_value="released",
            duration=press_duration,
        )

        press_context: Dict[str, Optional[float | str | int]] = {
            "press_id": state.press_id,
            "duration_s": press_duration,
            "module_address": state.module_address,
            "channel": state.channel,
            "bucket": self._get_bucket(press_duration),
        }
        self._last_press_context[state.address] = press_context

        classification_event = (
            "nikobus_short_button_pressed"
            if press_duration < SHORT_PRESS
            else "nikobus_long_button_pressed"
        )
        _LOGGER.debug(
            "Classification for %s (press_id=%s): %s", state.address, state.press_id, classification_event
        )
        self._fire_event(
            classification_event,
            state,
            state_value="released",
            duration=press_duration,
        )

        bucket = press_context["bucket"]
        if bucket is not None:
            bucket_event_type = f"nikobus_button_pressed_{bucket}"
            _LOGGER.debug(
                "Bucket %s event for %s (press_id=%s)", bucket, state.address, state.press_id
            )
            self._fire_event(
                bucket_event_type,
                state,
                state_value="released",
                duration=press_duration,
                bucket=bucket,
            )

        self._hass.async_create_task(
            self.button_discovery(state.address, press_context=press_context)
        )

        self._press_states.pop(state.address, None)

    def _build_event_payload(
        self,
        state: PressState,
        *,
        state_value: str,
        duration: Optional[float],
        bucket: Optional[int] = None,
        threshold: Optional[int] = None,
        extra: Optional[Dict[str, Optional[str | int | float]]] = None,
    ) -> Dict[str, Optional[str | int | float]]:
        """Construct a consistent event payload."""

        event_data: Dict[str, Optional[str | int | float]] = {
            "address": state.address,
            "module_address": state.module_address,
            "channel": state.channel,
            "ts": datetime.now(timezone.utc).isoformat(),
            "press_id": state.press_id,
            "state": state_value,
            "duration_s": duration,
            "bucket": bucket,
            "threshold_s": threshold,
            "source": "nikobus",
        }

        if extra:
            event_data.update(extra)

        return event_data

    def _fire_event(
        self,
        event_type: str,
        state: PressState,
        *,
        state_value: str,
        duration: Optional[float],
        bucket: Optional[int] = None,
        threshold: Optional[int] = None,
        extra: Optional[Dict[str, Optional[str | int | float]]] = None,
    ) -> None:
        """Fire a Home Assistant event with debug logging."""

        event_data = self._build_event_payload(
            state,
            state_value=state_value,
            duration=duration,
            bucket=bucket,
            threshold=threshold,
            extra=extra,
        )
        _LOGGER.debug("Firing event %s with data: %s", event_type, event_data)
        self._hass.bus.async_fire(event_type, event_data)

    def _derive_button_context(self, address: str) -> Tuple[Optional[str], Optional[int]]:
        """Attempt to derive module address and channel information for the button."""

        button_data = self._dict_button_data.get("nikobus_button", {}).get(address, {})
        module_address = None
        channel = None

        impacted_modules = button_data.get("impacted_module") or []
        for module_info in impacted_modules:
            mod_address = (module_info.get("address") or "").strip()
            if mod_address:
                module_address = mod_address.upper()
                break

        discovered_links = button_data.get("discovered_link") or []
        for link in discovered_links:
            module_address = module_address or (link.get("module_address") or "").upper() or None
            channel_str = link.get("channel") or ""
            try:
                channel = int(channel_str.split()[-1])
                break
            except (ValueError, IndexError, AttributeError):
                continue

        return module_address, channel

    def _get_bucket(self, press_duration: float) -> int:
        """Map the press duration to the correct bucket."""

        if press_duration < 1:
            return 0
        if press_duration < 2:
            return 1
        if press_duration < 3:
            return 2
        return 3

    async def button_discovery(
        self, address: str, press_context: Optional[Dict[str, Optional[float | str | int]]] = None
    ) -> None:
        """Discover a button and process it if configured."""
        _LOGGER.debug("Discovering button at address: %s", address)
        button_data = self._dict_button_data.get("nikobus_button", {}).get(address)

        if button_data:
            _LOGGER.debug("Button found in config.")
            await self.process_button_modules(button_data, address, press_context)
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
        self._last_press_context.pop(address, None)

    async def process_button_modules(
        self,
        button_data: Dict[str, Optional[str]],
        button_address: str,
        press_context: Optional[Dict[str, Optional[float | str | int]]],
    ) -> None:
        """Process actions for each module impacted by the button press."""
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

        press_id = None
        duration_s = None
        bucket = None
        context_module_address = None
        context_channel = None
        if press_context:
            press_id = press_context.get("press_id")
            duration_s = press_context.get("duration_s")
            bucket = press_context.get("bucket")
            context_module_address = press_context.get("module_address")
            context_channel = press_context.get("channel")

        press_id = press_id or f"{button_address}-{uuid.uuid4().hex[:8]}"

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
                    {
                        "address": module_addr,
                        "group": fallback_group,
                        "channel": channel_number,
                    }
                )

        for module_info in modules_to_process:
            impacted_module_address = module_info.get("address")
            impacted_group = module_info.get("group")
            impacted_channel = module_info.get("channel") or context_channel

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

                state = PressState(
                    address=button_address.upper(),
                    press_start=time.monotonic(),
                    last_press_time=time.monotonic(),
                    press_id=press_id,
                    module_address=(impacted_module_address or context_module_address),
                    channel=impacted_channel,
                )

                extra_data = {
                    "button_operation_time": button_operation_time,
                    "impacted_module_address": impacted_module_address,
                    "impacted_module_group": impacted_group,
                }

                self._fire_event(
                    "nikobus_button_pressed",
                    state,
                    state_value="released",
                    duration=duration_s,
                    bucket=bucket if isinstance(bucket, int) else None,
                    extra=extra_data,
                )
                event_fired = True

            except Exception as e:
                _LOGGER.error(
                    "Error processing button press for module %s: %s",
                    impacted_module_address,
                    e,
                    exc_info=True,
                )

        if not event_fired:
            state = PressState(
                address=button_address.upper(),
                press_start=time.monotonic(),
                last_press_time=time.monotonic(),
                press_id=press_id,
                module_address=context_module_address,
                channel=context_channel,
            )
            _LOGGER.debug(
                "Firing minimal event: nikobus_button_pressed for %s with press_id=%s",
                button_address,
                press_id,
            )
            self._fire_event(
                "nikobus_button_pressed",
                state,
                state_value="released",
                duration=duration_s,
                bucket=bucket if isinstance(bucket, int) else None,
            )

        self._coordinator.async_update_listeners()
