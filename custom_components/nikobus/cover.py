"""Cover platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    BRAND,
    DEFAULT_COVER_DEBOUNCE_DELAY,
    DEFAULT_COVER_MOVEMENT_BUFFER,
    DEFAULT_COVER_OPERATION_TIME,
    DOMAIN,
    EVENT_BUTTON_PRESSED,
    HUB_IDENTIFIER,
)
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity
from .nkbtravelcalculator import NikobusTravelCalculator
from .router import build_unique_id, get_routing

_LOGGER = logging.getLogger(__name__)


def _parse_operation_time(value: Any, fallback: float, label: str, address: str) -> float:
    """Parse and validate a cover operation time value.

    ``None`` means the field was not configured — silently returns ``fallback``.
    Any other value that is not a positive number logs a warning before falling back.
    """
    if value is None:
        return fallback
    try:
        t = float(value)
        if t > 0:
            return t
    except (TypeError, ValueError):
        pass
    _LOGGER.warning(
        "Cover %s: invalid %s %r — must be a positive number. Using default %.1fs.",
        address,
        label,
        value,
        fallback,
    )
    return fallback


# Nikobus Internal States
STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03  # Catches logic engine conflicts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus cover entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    routing = get_routing(hass, entry, coordinator.dict_module_data)

    entities = []
    for spec in routing["cover"]:
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, spec.address)},
            manufacturer=BRAND,
            name=spec.module_desc,
            model=spec.module_model,
            via_device=(DOMAIN, HUB_IDENTIFIER),
        )

        op_time_up = _parse_operation_time(
            spec.operation_time_up,
            DEFAULT_COVER_OPERATION_TIME,
            "operation_time_up",
            spec.address,
        )
        op_time_down = _parse_operation_time(
            spec.operation_time_down,
            op_time_up,
            "operation_time_down",
            spec.address,
        )

        entities.append(
            NikobusCoverEntity(
                coordinator,
                spec.address,
                spec.channel,
                spec.channel_description,
                spec.module_desc,
                spec.module_model,
                op_time_up,
                op_time_down,
            )
        )

    async_add_entities(entities)


class NikobusCoverEntity(NikobusEntity, CoverEntity, RestoreEntity):
    """Representation of a Nikobus cover entity."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        channel: int,
        description: str,
        module_desc: str,
        model: str,
        op_time_up: float,
        op_time_down: float,
    ) -> None:
        """Initialize the cover entity."""
        super().__init__(coordinator, address, module_desc, model)
        self._address = address
        self._channel = channel
        self._channel_description = description

        self._attr_name = description
        self._attr_unique_id = build_unique_id("cover", "cover", address, channel)

        self._calculator = NikobusTravelCalculator(op_time_up, op_time_down)

        self._position: float = 100.0
        self._state = STATE_STOPPED
        self._target_position: int | None = None
        self._motion_task: asyncio.Task | None = None
        self._coalesce_task: asyncio.Task | None = None
        self._error_recovery_task: asyncio.Task | None = None

        self._movement_source = "ha"
        self._current_run_limit: float = 0.0

        # Set by _handle_button_pressed when a physical button starts a new move.
        # Used by _handle_coordinator_update to backdate the travel calculator so
        # that the in-transit position reflects actual elapsed travel time.
        self._last_button_press_monotonic: float | None = None

    @property
    def current_cover_position(self) -> int:
        """Return the current position of the cover (0-100)."""
        return int(round(self._position))

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._state == STATE_OPENING and self._position < 100

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._state == STATE_CLOSING and self._position > 0

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed (0)."""
        return self.current_cover_position == 0

    @property
    def is_open(self) -> bool:
        """Return if the cover is open (100)."""
        return self.current_cover_position == 100

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes, merging with parent attributes."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "operation_time_up": self._calculator.time_up,
            "operation_time_down": self._calculator.time_down,
            "movement_source": self._movement_source,
            "controlled_by": self.coordinator.get_controlled_by(self._address, self._channel),
        }

    async def async_added_to_hass(self) -> None:
        """Restore state and listen for Nikobus bus events."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            if (pos := last_state.attributes.get(ATTR_CURRENT_POSITION)) is not None:
                self._position = float(pos)
                self._calculator.set_position(self._position)

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESSED, self._handle_button_pressed)
        )

        def _cancel_cover_tasks() -> None:
            for task_attr in ("_motion_task", "_coalesce_task", "_error_recovery_task"):
                task = getattr(self, task_attr, None)
                if task:
                    task.cancel()
                    setattr(self, task_attr, None)

        self.async_on_remove(_cancel_cover_tasks)

    @callback
    def _handle_coordinator_update(self) -> None:
        """React to state changes reported by the Nikobus bus."""
        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)

        if new_bus_state == self._state:
            super()._handle_coordinator_update()
            return

        # 0x03 = hardware motor-protection (both relay outputs active simultaneously).
        # Cause depends on who initiated movement:
        #   "ha"      — HA wrote the relay directly; Nikobus didn't track the motion.
        #               Actively send VALUE=0 to clear the braking state quickly.
        #   "nikobus" — Nikobus initiated; hardware auto-clears cleanly. Observer-only;
        #               schedule a follow-up refresh to catch the 0x00 transition.
        if new_bus_state == STATE_ERROR:
            if self._movement_source == "ha":
                _LOGGER.debug(
                    "Cover %s ch%d: motor-protection (0x03) after HA move — sending VALUE=0.",
                    self._address, self._channel,
                )
                self.hass.async_create_task(self._stop(send_stop=True, force_api=True))
            else:
                _LOGGER.debug(
                    "Cover %s ch%d: motor-protection (0x03) after Nikobus move — waiting for auto-clear.",
                    self._address, self._channel,
                )
                self.hass.async_create_task(self._stop(send_stop=False))
                if not self._error_recovery_task or self._error_recovery_task.done():
                    async def _await_error_clear() -> None:
                        await asyncio.sleep(2.5)
                        await self.coordinator.async_request_refresh()
                    self._error_recovery_task = self.hass.async_create_task(_await_error_clear())

        elif new_bus_state == STATE_STOPPED:
            self.hass.async_create_task(self._stop(send_stop=False))

        else:
            direction = "opening" if new_bus_state == STATE_OPENING else "closing"
            self._movement_source = "nikobus"

            # Calculate how long the cover has already been moving before HA
            # detected the state change.  The timestamp is set by
            # _handle_button_pressed when the physical button fires — it
            # captures the actual press time, not the detection time.
            detection_latency = 0.0
            if self._last_button_press_monotonic is not None:
                age = time.monotonic() - self._last_button_press_monotonic
                if age < 10.0:
                    detection_latency = age
                self._last_button_press_monotonic = None

            self._start_motion_logic(direction, detection_latency=detection_latency)

        super()._handle_coordinator_update()

    async def _handle_button_pressed(self, event: Any) -> None:
        """Handle a physical Nikobus button press event.

        Records the press timestamp (for detection-latency calculation) only
        when the cover is currently stopped — meaning this press is starting a
        new move.  Presses while moving are stop commands; we don't timestamp
        those because they are not the start of a new Nikobus-initiated move.

        Channel filtering prevents covers on the same roller module from
        sharing timestamps when unrelated buttons are pressed.
        """
        if str(event.data.get("module_address", "")).upper() != str(self._address).upper():
            return

        # Only process events for this specific channel to avoid cross-channel
        # timestamp pollution on multi-channel roller modules.
        event_channel = event.data.get("channel")
        if event_channel is not None and int(event_channel) != self._channel:
            return

        if self._state == STATE_STOPPED:
            # Cover is about to start moving — record now for detection latency.
            self._last_button_press_monotonic = time.monotonic()
        else:
            # Cover is moving — this press is a stop command.
            send_stop = (self._movement_source == "ha")
            await self._stop(send_stop=send_stop)

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open cover command."""
        await self._request_move("opening")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close cover command."""
        await self._request_move("closing")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop cover command."""
        await self._stop(send_stop=True)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move cover to a specific percentage."""
        target = kwargs[ATTR_POSITION]
        if target == round(self._position):
            return
        if self._coalesce_task:
            self._coalesce_task.cancel()

        async def _debounced_move() -> None:
            try:
                await asyncio.sleep(DEFAULT_COVER_DEBOUNCE_DELAY)
                direction = "opening" if target > self._position else "closing"
                await self._request_move(direction, target)
            except asyncio.CancelledError:
                pass
            finally:
                self._coalesce_task = None

        self._coalesce_task = self.hass.async_create_task(_debounced_move())

    async def _request_move(self, direction: str, target: int | None = None) -> None:
        """Execute movement via the API."""
        # Stop before reversing to protect the motor and sync Nikobus state.
        if self._state != STATE_STOPPED:
            current_direction = "opening" if self._state == STATE_OPENING else "closing"
            if current_direction != direction:
                await self._stop(send_stop=True)
                await asyncio.sleep(0.5)

        self._movement_source = "ha"
        self._target_position = target

        async def on_sent() -> None:
            self._start_motion_logic(direction)

        if direction == "opening":
            await self.coordinator.api.open_cover(self._address, self._channel, on_sent)
        else:
            await self.coordinator.api.close_cover(self._address, self._channel, on_sent)

    def _start_motion_logic(
        self,
        direction: str,
        detection_latency: float = 0.0,
    ) -> None:
        """Initialize the virtual travel tracker."""
        if self._motion_task:
            self._motion_task.cancel()

        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING

        active_op_time = (
            self._calculator.time_up if direction == "opening" else self._calculator.time_down
        )

        # Cap latency so a stale timestamp can't skip more than 90% of travel.
        detection_latency = min(detection_latency, active_op_time * 0.9)

        self._calculator.start_travel(direction, latency=detection_latency)
        # Sync position immediately so the UI reflects in-transit state.
        self._position = self._calculator.current_position()

        # Full-travel run limit, shortened by already-elapsed detection time.
        self._current_run_limit = max(
            DEFAULT_COVER_MOVEMENT_BUFFER,
            active_op_time - detection_latency + DEFAULT_COVER_MOVEMENT_BUFFER,
        )

        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _motion_loop(self) -> None:
        """Update position periodically while moving."""
        try:
            start_time = time.monotonic()
            # Capture source at loop start; _movement_source must not change
            # the stop decision mid-iteration after an await.
            movement_source = self._movement_source
            while self._state in (STATE_OPENING, STATE_CLOSING):
                elapsed = time.monotonic() - start_time
                self._position = self._calculator.current_position()

                if elapsed >= self._current_run_limit or self._should_stop():
                    if elapsed >= self._current_run_limit and self._target_position is None:
                        # Reached mechanical end-stop — snap to exact value.
                        self._position = 100.0 if self._state == STATE_OPENING else 0.0
                        self._calculator.set_position(self._position)
                    elif self._target_position is not None:
                        # Snap to exact target to eliminate 0.5 s tick overshoot.
                        self._position = float(self._target_position)
                        self._calculator.set_position(self._position)
                    await self._stop(send_stop=(movement_source == "ha"))
                    break

                self.async_write_ha_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            _LOGGER.error(
                "Cover %s ch%d: motion loop error — forcing stop: %s",
                self._address,
                self._channel,
                err,
                exc_info=True,
            )
            await self._stop(send_stop=False)

    def _should_stop(self) -> bool:
        """Check if cover reached the target position."""
        if self._state == STATE_OPENING:
            return self._target_position is not None and self._position >= self._target_position
        if self._state == STATE_CLOSING:
            return self._target_position is not None and self._position <= self._target_position
        return False

    async def _stop(self, send_stop: bool = False, force_api: bool = False) -> None:
        """Stop movement and finalize position."""
        if self._motion_task:
            self._motion_task.cancel()
            self._motion_task = None

        stopped_state = self._state
        self._calculator.stop()
        self._position = self._calculator.current_position()

        # Commit STATE_STOPPED immediately so any coordinator events that fire
        # during the async stop-command round-trip see a consistent stopped state,
        # preventing spurious _start_motion_logic calls from stale bus reads.
        self._state = STATE_STOPPED
        self._target_position = None
        self.coordinator.set_bytearray_state(self._address, self._channel, STATE_STOPPED)

        if send_stop and (stopped_state != STATE_STOPPED or force_api):
            dir_cmd = "opening" if stopped_state == STATE_OPENING else "closing"
            await self.coordinator.api.stop_cover(self._address, self._channel, dir_cmd)

        self.async_write_ha_state()
