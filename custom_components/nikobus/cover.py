"""Cover platform for the Nikobus integration (optimized version)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Optional, Dict

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
    CoverDeviceClass,
    ATTR_POSITION,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03


class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds: float, start_position: Optional[float]):
        if duration_in_seconds <= 0:
            raise ValueError("operation_time must be greater than zero")

        self._duration_in_seconds = duration_in_seconds
        self._start_time: Optional[float] = None
        self._direction_value: Optional[int] = None
        self._initial_position: Optional[float] = start_position
        self._current_position: Optional[float] = start_position
        self._is_moving = False

        _LOGGER.debug(
            "PositionEstimator initialized with duration: %.2f seconds, start position: %s",
            duration_in_seconds,
            start_position,
        )

    def start(self, direction: str, position: Optional[float] = None) -> None:
        if direction not in ("opening", "closing"):
            _LOGGER.error("Invalid direction '%s' provided to PositionEstimator", direction)
            return

        direction_value = 1 if direction == "opening" else -1
        baseline_position = self.get_position() if self._is_moving else self._current_position

        if self._is_moving and self._direction_value == direction_value:
            _LOGGER.debug(
                "Estimator already moving %s; refreshing baseline without stopping.", direction
            )
        elif self._is_moving:
            _LOGGER.debug(
                "Estimator restarting for direction change: %s -> %s.",
                "opening" if self._direction_value == 1 else "closing",
                direction,
            )

        self._direction_value = direction_value
        self._start_time = time.monotonic()
        self._is_moving = True

        # Capture the initial position once at the start.
        if position is not None:
            self._initial_position = max(0.0, min(100.0, float(position)))
        elif baseline_position is not None:
            self._initial_position = baseline_position
        else:
            self._initial_position = 100.0 if self._direction_value == 1 else 0.0
        self._current_position = self._initial_position

        _LOGGER.debug(
            "Movement started in direction: %s, initial position set to: %s",
            direction,
            self._initial_position,
        )

    def get_position(self) -> Optional[float]:
        """Calculate and return the current position estimate."""
        if (
            not self._is_moving
            or self._start_time is None
            or self._direction_value is None
            or self._initial_position is None
        ):
            _LOGGER.debug(
                "Position estimation unavailable; ensure start() is called correctly."
            )
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (elapsed_time / self._duration_in_seconds) * 100 * self._direction_value
        # Always compute based on the fixed starting position.
        new_position = max(0.0, min(100.0, self._initial_position + progress))
        self._current_position = new_position
        return new_position

    def stop(self) -> None:
        """Stop the movement and finalize the position estimate."""
        if self._is_moving:
            final_position = self.get_position()
            if final_position is not None:
                self._current_position = final_position
            _LOGGER.debug(
                "Movement stopped. Final position estimated at: %s", self._current_position
            )
        else:
            _LOGGER.warning("Stop called without active movement; ignoring.")

        self._start_time = None
        self._direction_value = None
        self._is_moving = False

    @property
    def current_position(self) -> Optional[int]:
        if self._current_position is None:
            return None
        return int(round(self._current_position))

    @property
    def duration_in_seconds(self) -> float:
        return self._duration_in_seconds

    @property
    def is_active(self) -> bool:
        return self._is_moving


def _clamp_position(value: Optional[float]) -> Optional[int]:
    """Clamp a numeric position into the 0-100 range."""

    if value is None:
        return None
    return int(max(0, min(100, round(value))))


def _safe_cancel(task: Optional[asyncio.Task]) -> None:
    """Cancel a task without raising if it is already done."""

    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug("Setting up Nikobus cover entities.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    cover_entities: list[NikobusCoverEntity] = []
    routing = get_routing(hass, entry, coordinator.dict_module_data)

    for spec in routing["cover"]:
        _register_nikobus_roller_device(
            device_registry=device_registry,
            entry=entry,
            module_address=spec.address,
            module_name=spec.module_desc,
            module_model=spec.module_model,
        )

        operation_time = spec.operation_time or "30"
        cover_entities.append(
            NikobusCoverEntity(
                hass=hass,
                coordinator=coordinator,
                address=spec.address,
                channel=spec.channel,
                channel_description=spec.channel_description,
                module_desc=spec.module_desc,
                module_model=spec.module_model,
                operation_time=operation_time,
            )
        )

    async_add_entities(cover_entities)
    _LOGGER.debug("Added %d Nikobus cover entities.", len(cover_entities))


def _register_nikobus_roller_device(
    device_registry: dr.DeviceRegistry,
    entry: ConfigEntry,
    module_address: str,
    module_name: str,
    module_model: str,
) -> None:
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, module_address)},
        manufacturer=BRAND,
        name=module_name,
        model=module_model,
        via_device=(DOMAIN, HUB_IDENTIFIER),
    )


class NikobusCoverEntity(NikobusEntity, CoverEntity, RestoreEntity):
    """Optimized representation of a Nikobus cover entity with improved task management and state consistency."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        channel_description: str,
        module_desc: str,
        module_model: str,
        operation_time: str,
    ) -> None:
        super().__init__(
            coordinator=coordinator,
            address=address,
            name=module_desc,
            model=module_model,
        )
        self.hass = hass
        self._address = address
        self._channel = channel
        self._channel_description = channel_description
        self._description = module_desc
        self._model = module_model
        self._state = STATE_STOPPED
        self._position = 100  # Default to fully open
        self._previous_state: Optional[int] = None
        self._movement_source = "ha"
        self._direction: Optional[str] = None  # "opening" or "closing"
        self._target_position: Optional[int] = None
        self._button_operation_time: Optional[float] = None

        self._operation_time = float(operation_time)
        self._position_estimator = PositionEstimator(
            duration_in_seconds=self._operation_time, start_position=self._position
        )

        self._in_motion = False
        self._motion_task: Optional[asyncio.Task] = None
        # Debounce state publishing during motion (reduces HomeKit event flooding)
        self._last_published_position: Optional[int] = None
        self._last_publish_time: float = 0.0
        self._last_command_time: float = 0.0
        self._publish_min_interval: float = 1.0  # seconds
        self._publish_min_delta: int = 2         # percent points
        self._pending_target_position: int | None = None
        self._coalesce_task: asyncio.Task | None = None
        self._coalesce_delay = 0.3  # seconds, tune 0.2–0.4

        self._unsub_button_event: Optional[Any] = None

        self._attr_name = channel_description
        self._attr_unique_id = build_unique_id(
            "cover", "cover", self._address, self._channel
        )
        self._attr_device_class = CoverDeviceClass.SHUTTER

        _LOGGER.debug(
            "NikobusCoverEntity initialized for '%s' (address=%s, channel=%s, operation_time=%.2f seconds)",
            channel_description,
            address,
            channel,
            self._operation_time,
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = super().extra_state_attributes or {}
        attrs.update(
            {
                "address": self._address,
                "channel": self._channel,
                "channel_description": self._channel_description,
                "module_description": self._device_name,
                "module_model": self._device_model,
                "position": self._position,
                "state": self._state,
            }
        )
        return attrs

    @property
    def current_cover_position(self) -> Optional[int]:
        return self._position

    @property
    def is_open(self) -> bool:
        return self._position == 100

    @property
    def is_closed(self) -> bool:
        return self._position == 0

    @property
    def is_opening(self) -> bool:
        return self._state == STATE_OPENING

    @property
    def is_closing(self) -> bool:
        return self._state == STATE_CLOSING

    @property
    def available(self) -> bool:
        return self._state != STATE_ERROR

    @property
    def supported_features(self) -> int:
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    async def async_added_to_hass(self) -> None:
        """Register callbacks and restore state when added to Home Assistant."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            last_position = last_state.attributes.get(ATTR_POSITION)
            if last_position is not None:
                restored = _clamp_position(last_position)
                if restored is not None:
                    self._position = restored
                else:
                    self._position = 100
                _LOGGER.debug(
                    "Restored position for '%s' to %s", self._attr_name, self._position
                )
            else:
                _LOGGER.warning(
                    "No valid position found in the last state for '%s', defaulting to 100.",
                    self._attr_name,
                )
                self._position = 100
        else:
            _LOGGER.info(
                "No last state available for '%s', initializing position to default (100).",
                self._attr_name,
            )
            self._position = 100

        self._state = self.coordinator.get_cover_state(self._address, self._channel)
        self._previous_state = self._state
        _LOGGER.debug(
            "Initialized state for '%s' to %s (channel=%d, address=%s).",
            self._attr_name,
            self._state,
            self._channel,
            self._address,
        )

        self._unsub_button_event = self.hass.bus.async_listen(
            "nikobus_button_pressed", self._handle_nikobus_button_event
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners and running tasks when the entity is removed."""

        if self._unsub_button_event:
            self._unsub_button_event()
            self._unsub_button_event = None
    
        _safe_cancel(self._coalesce_task)
        self._coalesce_task = None
        self._pending_target_position = None

        _safe_cancel(self._motion_task)
        if self._motion_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._motion_task
            self._motion_task = None

    @callback
    def _handle_coordinator_update(self) -> None:
        new_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_state != self._previous_state:
            self.hass.async_create_task(
                self._process_state_change(new_state, source="ha")
            )

    async def _handle_nikobus_button_event(self, event: Any) -> None:
        """Handle the `nikobus_button_pressed` event and update the cover state."""
        if event.data.get("impacted_module_address") != self._address:
            return

        new_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_state != self._previous_state:
            _LOGGER.debug(
                "State changed for %s: %s -> %s",
                self._attr_name,
                self._previous_state,
                new_state,
            )
            if event.data.get("button_operation_time") is not None:
                self._button_operation_time = float(
                    event.data.get("button_operation_time")
                )
                _LOGGER.debug(
                    "Received button operation time for %s: %s",
                    self._attr_name,
                    self._button_operation_time,
                )
            await self._process_state_change(new_state, source="nikobus")
        else:
            if self._in_motion and new_state in (STATE_OPENING, STATE_CLOSING):
                _LOGGER.debug(
                    "Button press for %s detected without state change; stopping motion.",
                    self._attr_name,
                )
                await self._end_motion()
            else:
                _LOGGER.debug(
                    "No state change for %s; ignoring event.", self._attr_name
                )

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._request_cover_motion("opening")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._request_cover_motion("closing")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._end_motion(send_stop=True)

    async def _execute_coalesced_position(self) -> None:
        scheduled_at = time.monotonic()
        await asyncio.sleep(self._coalesce_delay)

        # Newer command arrived while we were waiting -> reschedule
        if self._last_command_time > scheduled_at:
            self._coalesce_task = self.hass.async_create_task(self._execute_coalesced_position())
            return

        target_position = self._pending_target_position
        self._pending_target_position = None
        if target_position is None or self._position == target_position:
            return

        direction = "opening" if target_position > self._position else "closing"
        await self._request_cover_motion(direction, target_position=target_position)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        target_position = kwargs.get(ATTR_POSITION)
        if target_position is None:
            return

        # Clamp just in case
        target_position = max(0, min(100, int(target_position)))

        # If already there, ignore
        if self._position == target_position:
            _LOGGER.debug("Cover %s already at target %s.", self._attr_name, target_position)
            return

        # Coalesce: keep the latest target and schedule execution shortly
        self._pending_target_position = target_position

        if self._coalesce_task and not self._coalesce_task.done():
            # Task already scheduled; it will pick up the latest pending target
            return

        self._coalesce_task = self.hass.async_create_task(self._execute_coalesced_position())

    async def _process_state_change(self, new_state: int, source: str = "ha") -> None:
        _LOGGER.debug(
            "State change detected for %s: %s -> %s",
            self._attr_name,
            self._previous_state,
            new_state,
        )

        if (new_state == STATE_OPENING and self._position == 100) or (
            new_state == STATE_CLOSING and self._position == 0
        ):
            _LOGGER.debug(
                "Cover %s already at intended position %d. No action needed.",
                self._attr_name,
                self._position,
            )
            self.coordinator.set_bytearray_state(
                self._address, self._channel, STATE_STOPPED
            )
            return

        self._previous_state = new_state
        self._movement_source = source

        if new_state in (STATE_OPENING, STATE_CLOSING):
            direction = "opening" if new_state == STATE_OPENING else "closing"
            if self._in_motion and self._direction == direction:
                _LOGGER.debug(
                    "Ignoring duplicate %s update for %s; already moving.",
                    direction,
                    self._attr_name,
                )
                self._previous_state = new_state
                self._movement_source = source
                return
            if source == "nikobus":
                self._target_position = None
            await self._begin_motion(
                direction,
                source,
                target_position=self._target_position,
                button_limit=self._button_operation_time,
            )
        elif new_state == STATE_STOPPED:
            await self._end_motion()
        elif new_state == STATE_ERROR:
            _LOGGER.warning("Error state encountered for %s.", self._attr_name)
            await self._end_motion(send_stop=True)
        else:
            _LOGGER.warning(
                "Unknown state '%s' encountered for %s.", new_state, self._attr_name
            )

    async def _request_cover_motion(
        self, direction: str, target_position: Optional[int] = None
    ) -> None:
        """Queue a cover command and start motion once executed."""

        if self._in_motion:
            await self._end_motion(send_stop=self._movement_source == "ha")

        self._movement_source = "ha"
        self._target_position = _clamp_position(target_position)

        async def completion_handler() -> None:
            await self._begin_motion(
                direction,
                source="ha",
                target_position=self._target_position,
            )

        await self._operate_cover(direction, completion_handler)

    async def _operate_cover(self, direction: str, completion_handler: Any) -> None:
        _LOGGER.debug("Operating cover %s in direction: %s", self._attr_name, direction)
        try:
            if direction == "opening":
                await self.coordinator.api.open_cover(
                    self._address, self._channel, completion_handler=completion_handler
                )
            elif direction == "closing":
                await self.coordinator.api.close_cover(
                    self._address, self._channel, completion_handler=completion_handler
                )
            else:
                _LOGGER.error(
                    "Invalid direction '%s' for cover %s", direction, self._attr_name
                )
        except Exception as exc:
            _LOGGER.error(
                "Failed to operate cover %s: %s", self._attr_name, exc, exc_info=True
            )

    async def _begin_motion(
        self,
        direction: str,
        source: str,
        target_position: Optional[int] = None,
        button_limit: Optional[float] = None,
    ) -> None:
        """Authoritative entrypoint for starting movement."""

        if self._in_motion:
            if self._direction == direction:
                _LOGGER.debug(
                    "Duplicate start for %s in direction %s; keeping current motion.",
                    self._attr_name,
                    direction,
                )
                if target_position is not None:
                    self._target_position = _clamp_position(target_position)
                if button_limit is not None:
                    self._button_operation_time = button_limit
                self._movement_source = source
                return

            _LOGGER.debug(
                "Reversing direction for %s: %s -> %s; stopping current motion first.",
                self._attr_name,
                self._direction,
                direction,
            )
            await self._end_motion(send_stop=source == "ha")

        self._direction = direction
        self._in_motion = True
        self._movement_source = source
        self._button_operation_time = button_limit
        self._target_position = _clamp_position(target_position)
        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING

        self._position_estimator.start(self._direction, self._position)

        _safe_cancel(self._motion_task)
        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _end_motion(
        self,
        final_position: Optional[int] = None,
        send_stop: bool = False,
    ) -> None:
        """Authoritative entrypoint for ending movement."""

        # Cancel any pending coalesced position update so STOP cannot be followed by a delayed move
        _safe_cancel(self._coalesce_task)
        self._coalesce_task = None
        self._pending_target_position = None

        if not self._in_motion and not send_stop:
            return

        direction_for_stop = self._direction
        self._in_motion = False

        async def _finalize_state() -> None:
            self._position_estimator.stop()
            _safe_cancel(self._motion_task)
            task = self._motion_task
            _safe_cancel(task)

            if task and task is not asyncio.current_task():
                with contextlib.suppress(asyncio.CancelledError):
                await task

            self._motion_task = None

            estimated_position = _clamp_position(
                final_position if final_position is not None else self._position_estimator.current_position
            )
            if estimated_position is not None:
                self._position = estimated_position

            self._direction = None
            self._target_position = None
            self._button_operation_time = None
            self._state = STATE_STOPPED
            self._previous_state = STATE_STOPPED

            self.coordinator.set_bytearray_state(
                self._address, self._channel, STATE_STOPPED
            )
            # Ensure final state is published even if debounce would suppress it
            self._last_published_position = None
            self._last_publish_time = 0.0

            self.async_write_ha_state()

        if send_stop and direction_for_stop:
            try:
                await self.coordinator.api.stop_cover(
                    self._address,
                    self._channel,
                    direction_for_stop,
                    completion_handler=_finalize_state,
                )
            except Exception as exc:
                _LOGGER.error(
                    "Failed to stop cover %s: %s", self._attr_name, exc, exc_info=True
                )
                await _finalize_state()
        else:
            await _finalize_state()

    async def _motion_loop(self) -> None:
        """Single loop responsible for motion lifecycle and estimation."""

        start_time = time.monotonic()
        try:
            while self._in_motion and self._direction:
                estimated_position = self._position_estimator.get_position()
                if estimated_position is not None:
                    clamped_position = _clamp_position(estimated_position)
                    if clamped_position is not None:
                        self._position = clamped_position

                elapsed = time.monotonic() - start_time
                if self._button_operation_time and elapsed >= self._button_operation_time:
                    _LOGGER.debug(
                        "Stopping %s due to button operation timeout.", self._attr_name
                    )
                    await self._end_motion(send_stop=self._movement_source == "ha")
                    break

                if self._target_position is not None:
                    if (
                        self._direction == "opening"
                        and self._position >= self._target_position
                        and self._target_position < 100
                    ) or (
                        self._direction == "closing"
                        and self._position <= self._target_position
                        and self._target_position > 0
                    ):
                        await self._end_motion(
                            final_position=self._target_position,
                            send_stop=self._movement_source == "ha",
                        )
                        break

                if (self._direction == "opening" and self._position >= 100) or (
                    self._direction == "closing" and self._position <= 0
                ):
                    await self._end_motion(
                        final_position=100 if self._direction == "opening" else 0,
                        send_stop=self._movement_source == "ha",
                    )
                    break

                now = time.monotonic()
                pos = int(self._position)

                # Publish only if enough time passed AND enough delta occurred
                if (
                    self._last_published_position is None
                    or (now - self._last_publish_time) >= self._publish_min_interval
                ):
                    if (
                        self._last_published_position is None
                        or abs(pos - self._last_published_position) >= self._publish_min_delta
                    ):
                        self._last_published_position = pos
                        self._last_publish_time = now
                        self.async_write_ha_state()

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _LOGGER.debug("Motion loop for %s was cancelled.", self._attr_name)
        except Exception as exc:
            _LOGGER.error(
                "Unexpected error in motion loop for %s: %s",
                self._attr_name,
                exc,
                exc_info=True,
            )
            await self._end_motion()
        finally:
            self._motion_task = None
