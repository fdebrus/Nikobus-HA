"""Cover platform for the Nikobus integration (optimized version)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import BRAND, DOMAIN
from .coordinator import NikobusDataCoordinator
from .entity import NikobusEntity
from .router import build_unique_id, get_routing

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03

PHASE_IDLE = "idle"
PHASE_STARTING = "starting"
PHASE_MOVING = "moving"
PHASE_STOPPING = "stopping"

INTENT_DEBOUNCE_SECONDS = 0.25
REVERSE_DWELL_SECONDS = 0.2
FALLBACK_ESTIMATOR_DELAY = 0.2


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

    def start(self, direction: str, position: Optional[float] = None) -> None:
        if direction not in ("opening", "closing"):
            _LOGGER.error("Invalid direction '%s' provided to PositionEstimator", direction)
            return

        self._direction_value = 1 if direction == "opening" else -1
        self._start_time = time.monotonic()
        self._is_moving = True

        baseline = self._current_position if position is None else float(position)
        if baseline is None:
            baseline = 100.0 if self._direction_value == 1 else 0.0
        self._initial_position = max(0.0, min(100.0, baseline))
        self._current_position = self._initial_position

    def get_position(self) -> Optional[float]:
        """Calculate and return the current position estimate."""
        if (
            not self._is_moving
            or self._start_time is None
            or self._direction_value is None
            or self._initial_position is None
        ):
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (elapsed_time / self._duration_in_seconds) * 100 * self._direction_value
        new_position = max(0.0, min(100.0, self._initial_position + progress))
        self._current_position = new_position
        return new_position

    def stop(self) -> None:
        """Stop the movement and finalize the position estimate."""
        if self._is_moving:
            final_position = self.get_position()
            if final_position is not None:
                self._current_position = final_position

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


@dataclass
class CoverIntent:
    """Intent queued by HA services to serialize cover commands."""

    intent_type: str
    target_position: Optional[int] = None


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
    """Representation of a Nikobus cover with a small state machine and intent queue."""

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
        self._position = 100
        self._previous_state: Optional[int] = None
        self._movement_source = "ha"
        self._direction: Optional[str] = None
        self._target_position: Optional[int] = None
        self._button_operation_time: Optional[float] = None

        self._operation_time = float(operation_time)
        self._position_estimator = PositionEstimator(
            duration_in_seconds=self._operation_time, start_position=self._position
        )

        self._phase = PHASE_IDLE
        self._motion_task: Optional[asyncio.Task] = None
        self._fallback_estimator_task: Optional[asyncio.Task] = None
        self._intent_task: Optional[asyncio.Task] = None
        self._intent_queue: asyncio.Queue[CoverIntent] = asyncio.Queue()
        self._position_debounce_task: Optional[asyncio.Task] = None
        self._pending_position: Optional[int] = None
        self._intent_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
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
    def extra_state_attributes(self) -> dict[str, Any]:
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
                self._position = int(last_position)
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
        self._intent_task = self.hass.async_create_task(self._intent_worker())
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners and running tasks when the entity is removed."""

        if self._unsub_button_event:
            self._unsub_button_event()
            self._unsub_button_event = None

        _safe_cancel(self._intent_task)
        if self._intent_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._intent_task
            self._intent_task = None

        _safe_cancel(self._position_debounce_task)
        if self._position_debounce_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._position_debounce_task
            self._position_debounce_task = None

        _safe_cancel(self._fallback_estimator_task)
        if self._fallback_estimator_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._fallback_estimator_task
            self._fallback_estimator_task = None

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
            if event.data.get("button_operation_time") is not None:
                self._button_operation_time = float(
                    event.data.get("button_operation_time")
                )
            await self._process_state_change(new_state, source="nikobus")
        else:
            if self._phase in (PHASE_STARTING, PHASE_MOVING) and new_state in (
                STATE_OPENING,
                STATE_CLOSING,
            ):
                _LOGGER.debug(
                    "Button press for %s detected without state change; stopping motion.",
                    self._attr_name,
                )
                await self._stop_motion(send_stop=True)

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._enqueue_intent(CoverIntent("open"))

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._enqueue_intent(CoverIntent("close"))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._enqueue_intent(CoverIntent("stop"))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        target_position = kwargs.get(ATTR_POSITION)
        if target_position is None:
            return

        self._pending_position = _clamp_position(target_position)
        _safe_cancel(self._position_debounce_task)
        self._position_debounce_task = self.hass.async_create_task(
            self._debounce_position_intent()
        )

    async def _debounce_position_intent(self) -> None:
        try:
            await asyncio.sleep(INTENT_DEBOUNCE_SECONDS)
            if self._pending_position is None:
                return
            await self._enqueue_intent(
                CoverIntent("set_position", target_position=self._pending_position)
            )
        except asyncio.CancelledError:
            return
        finally:
            self._pending_position = None

    async def _enqueue_intent(self, intent: CoverIntent) -> None:
        await self._intent_queue.put(intent)

    async def _intent_worker(self) -> None:
        """Process intents sequentially while coalescing rapid position updates."""
        try:
            while True:
                intent = await self._intent_queue.get()
                async with self._intent_lock:
                    if intent.intent_type == "open":
                        await self._handle_direction_request("opening")
                    elif intent.intent_type == "close":
                        await self._handle_direction_request("closing")
                    elif intent.intent_type == "stop":
                        await self._stop_motion(send_stop=True)
                    elif intent.intent_type == "set_position":
                        await self._handle_set_position(intent.target_position)
        except asyncio.CancelledError:
            return
        finally:
            self._pending_position = None

    async def _handle_set_position(self, target_position: Optional[int]) -> None:
        if target_position is None:
            return

        if target_position == self._position:
            _LOGGER.debug("Cover %s is already at target position.", self._attr_name)
            return

        direction = "opening" if target_position > self._position else "closing"
        self._target_position = _clamp_position(target_position)
        await self._handle_direction_request(direction)

    async def _handle_direction_request(self, direction: str) -> None:
        """Apply sequencing rules for direction changes and estimator start."""
        async with self._state_lock:
            same_direction = self._direction == direction
            in_motion = self._phase in (PHASE_STARTING, PHASE_MOVING)
            if in_motion and same_direction:
                _LOGGER.debug(
                    "Cover %s already moving %s; updating target only.",
                    self._attr_name,
                    direction,
                )
                if self._target_position is not None:
                    self._target_position = _clamp_position(self._target_position)
                return

            if in_motion and not same_direction:
                await self._stop_motion(send_stop=True)
                await asyncio.sleep(REVERSE_DWELL_SECONDS)

            await self._start_motion(direction)

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
            self.coordinator.set_bytearray_state(
                self._address, self._channel, STATE_STOPPED
            )
            return

        self._previous_state = new_state
        self._movement_source = source

        if new_state in (STATE_OPENING, STATE_CLOSING):
            direction = "opening" if new_state == STATE_OPENING else "closing"
            async with self._state_lock:
                if self._direction == direction and self._phase == PHASE_MOVING:
                    return
                self._direction = direction
                if self._phase == PHASE_STARTING:
                    await self._confirm_motion_start(direction)
                elif self._phase == PHASE_IDLE:
                    await self._start_motion(direction, source=source, should_send=False)
        elif new_state == STATE_STOPPED:
            await self._stop_motion(send_stop=False)
        elif new_state == STATE_ERROR:
            _LOGGER.warning("Error state encountered for %s.", self._attr_name)
            await self._stop_motion(send_stop=True)
        else:
            _LOGGER.warning(
                "Unknown state '%s' encountered for %s.", new_state, self._attr_name
            )

    async def _start_motion(
        self, direction: str, source: str = "ha", should_send: bool = True
    ) -> None:
        """Enter STARTING and send hardware command if requested."""
        self._direction = direction
        self._movement_source = source
        self._phase = PHASE_STARTING
        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
        self._button_operation_time = (
            self._button_operation_time if source == "nikobus" else None
        )

        _safe_cancel(self._fallback_estimator_task)
        self._fallback_estimator_task = self.hass.async_create_task(
            self._fallback_start_estimator(direction)
        )

        if should_send:
            await self._operate_cover(direction)

        self.async_write_ha_state()

    async def _confirm_motion_start(self, direction: str) -> None:
        """Start estimator and motion loop once movement is confirmed."""
        if self._phase not in (PHASE_STARTING, PHASE_MOVING):
            return

        self._phase = PHASE_MOVING
        if not self._position_estimator.is_active:
            self._position_estimator.start(direction, self._position)
        _safe_cancel(self._fallback_estimator_task)
        self._ensure_motion_loop()
        self.async_write_ha_state()

    async def _fallback_start_estimator(self, direction: str) -> None:
        try:
            await asyncio.sleep(FALLBACK_ESTIMATOR_DELAY)
            if self._phase == PHASE_STARTING and not self._position_estimator.is_active:
                _LOGGER.debug(
                    "Fallback estimator start for %s due to missing feedback.",
                    self._attr_name,
                )
                await self._confirm_motion_start(direction)
        except asyncio.CancelledError:
            return

    async def _operate_cover(self, direction: str) -> None:
        try:
            if direction == "opening":
                await self.coordinator.api.open_cover(self._address, self._channel)
            elif direction == "closing":
                await self.coordinator.api.close_cover(self._address, self._channel)
            else:
                _LOGGER.error(
                    "Invalid direction '%s' for cover %s", direction, self._attr_name
                )
        except Exception as exc:
            _LOGGER.error(
                "Failed to operate cover %s: %s", self._attr_name, exc, exc_info=True
            )

    async def _stop_motion(
        self, final_position: Optional[int] = None, send_stop: bool = False
    ) -> None:
        """Stop movement, optionally sending a hardware STOP."""
        if self._phase == PHASE_IDLE and not send_stop:
            return

        self._phase = PHASE_STOPPING
        direction_for_stop = self._direction

        async def _finalize_state() -> None:
            if self._phase != PHASE_STOPPING or stop_token != self._motion_id:
                return
            self._position_estimator.stop()
            _safe_cancel(self._motion_task)
            if self._motion_task:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._motion_task
                self._motion_task = None

            estimated_position = _clamp_position(
                final_position
                if final_position is not None
                else self._position_estimator.current_position
            )
            if estimated_position is not None:
                self._position = estimated_position

            self._direction = None
            self._target_position = None
            self._button_operation_time = None
            self._state = STATE_STOPPED
            self._previous_state = STATE_STOPPED
            self._phase = PHASE_IDLE

            self.coordinator.set_bytearray_state(
                self._address, self._channel, STATE_STOPPED
            )
            self.async_write_ha_state()

        if send_stop and direction_for_stop:
            try:
                stop_done = loop.create_future()

                def _completion_handler() -> None:
                    async def _run() -> None:
                        await _finalize_state()
                        if stop_done and not stop_done.done():
                            stop_done.set_result(True)

                    self.hass.async_create_task(_run())

                await self.coordinator.api.stop_cover(
                    self._address,
                    self._channel,
                    direction_for_stop,
                    completion_handler=_completion_handler,
                )
                if stop_done:
                    try:
                        await asyncio.wait_for(stop_done, timeout=2)
                    except asyncio.TimeoutError:
                        _LOGGER.warning(
                            "Timeout waiting for stop completion on %s; finalizing.",
                            self._attr_name,
                        )
                        await _finalize_state()
            except Exception as exc:
                _LOGGER.error(
                    "Failed to stop cover %s: %s", self._attr_name, exc, exc_info=True
                )
                await _finalize_state()
        else:
            await _finalize_state()

    def _ensure_motion_loop(self) -> None:
        _safe_cancel(self._motion_task)
        self._motion_task = self.hass.async_create_task(self._motion_loop())

    async def _motion_loop(self) -> None:
        """Single loop responsible for motion lifecycle and estimation."""
        start_time = time.monotonic()
        try:
            while self._phase == PHASE_MOVING and self._direction:
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
                    await self._stop_motion(send_stop=self._movement_source == "ha")
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
                        await self._stop_motion(
                            final_position=self._target_position,
                            send_stop=self._movement_source == "ha",
                        )
                        break

                if (self._direction == "opening" and self._position >= 100) or (
                    self._direction == "closing" and self._position <= 0
                ):
                    await self._stop_motion(
                        final_position=100 if self._direction == "opening" else 0,
                        send_stop=self._movement_source == "ha",
                    )
                    break

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
            await self._stop_motion()
        finally:
            self._motion_task = None
