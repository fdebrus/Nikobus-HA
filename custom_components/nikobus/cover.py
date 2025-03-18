"""Cover platform for the Nikobus integration (optimized version)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

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
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02
STATE_ERROR = 0x03


class PositionEstimator:
    """Estimates the current position of the cover based on elapsed time and direction."""

    def __init__(self, duration_in_seconds: float, start_position: Optional[float]):
        self._duration_in_seconds = duration_in_seconds
        self._start_time: Optional[float] = None
        self._direction_value: Optional[int] = None
        self.position: Optional[float] = start_position
        self._is_moving = False

        _LOGGER.debug(
            "PositionEstimator initialized with duration: %.2f seconds, start position: %s",
            duration_in_seconds,
            start_position,
        )

    def start(self, direction: str, position: Optional[float] = None) -> None:
        """Start the movement in the specified direction."""
        if self._is_moving:
            _LOGGER.warning("Movement already started; ignoring redundant start call.")
            return

        self._direction_value = 1 if direction == "opening" else -1
        self._start_time = time.monotonic()
        self._is_moving = True
        self.position = (
            position
            if position is not None
            else (100 if self._direction_value == 1 else 0)
        )

        _LOGGER.debug(
            "Movement started in direction: %s, initial position set to: %s",
            direction,
            self.position,
        )

    def get_position(self) -> Optional[int]:
        """Calculate and return the current position estimate."""
        if (
            not self._is_moving
            or self._start_time is None
            or self._direction_value is None
            or self.position is None
        ):
            _LOGGER.debug(
                "Position estimation unavailable; ensure start() is called correctly."
            )
            return None

        elapsed_time = time.monotonic() - self._start_time
        progress = (
            (elapsed_time / self._duration_in_seconds) * 100 * self._direction_value
        )
        new_position = max(0, min(100, self.position + progress))
        return int(new_position)

    def stop(self) -> None:
        """Stop the movement and finalize the position estimate."""
        if self._is_moving:
            final_position = self.get_position()
            if final_position is not None:
                self.position = final_position
            _LOGGER.debug(
                "Movement stopped. Final position estimated at: %s", self.position
            )
        else:
            _LOGGER.warning("Stop called without active movement; ignoring.")

        self._start_time = None
        self._direction_value = None
        self._is_moving = False

    @property
    def duration_in_seconds(self) -> float:
        """Expose the duration in seconds for external use."""
        return self._duration_in_seconds

    @property
    def is_active(self) -> bool:
        """Return True if the estimator is actively tracking a movement."""
        return self._is_moving


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _LOGGER.debug("Setting up Nikobus cover entities.")

    coordinator: NikobusDataCoordinator = entry.runtime_data
    roller_modules: Dict[str, Any] = coordinator.dict_module_data.get(
        "roller_module", {}
    )

    device_registry = dr.async_get(hass)
    cover_entities: list[NikobusCoverEntity] = []
    switch_entities: list[Dict[str, Any]] = []  # Store switch info for switch.py

    for address, cover_module_data in roller_modules.items():
        module_desc = cover_module_data.get("description", f"Roller Module {address}")
        module_model = cover_module_data.get("model", "Unknown Roller Model")

        _register_nikobus_roller_device(
            device_registry=device_registry,
            entry=entry,
            module_address=address,
            module_name=module_desc,
            module_model=module_model,
        )

        for channel_idx, channel_info in enumerate(
            cover_module_data.get("channels", []), start=1
        ):
            if channel_info["description"].startswith("not_in_use"):
                continue

            use_as_switch = channel_info.get("use_as_switch", False)
            _LOGGER.debug(
                f"Processing {module_desc} channel {channel_idx}: use_as_switch={use_as_switch}"
            )

            if use_as_switch:
                switch_entities.append(
                    {
                        "coordinator": coordinator,
                        "address": address,
                        "channel": channel_idx,
                        "channel_description": channel_info["description"],
                        "module_desc": module_desc,
                        "module_model": module_model,
                    }
                )
            else:
                operation_time = channel_info.get("operation_time", "30")
                cover_entities.append(
                    NikobusCoverEntity(
                        hass=hass,
                        coordinator=coordinator,
                        address=address,
                        channel=channel_idx,
                        channel_description=channel_info["description"],
                        module_desc=module_desc,
                        module_model=module_model,
                        operation_time=operation_time,
                    )
                )

    async_add_entities(cover_entities)
    _LOGGER.debug("Added %d Nikobus cover entities.", len(cover_entities))
    hass.data.setdefault(DOMAIN, {})["switch_entities"] = switch_entities


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


class NikobusCoverEntity(CoordinatorEntity, CoverEntity, RestoreEntity):
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
        super().__init__(coordinator)
        self.hass = hass
        self._address = address
        self._channel = channel
        self._description = module_desc
        self._model = module_model
        self._state = STATE_STOPPED
        self._position = 100  # Default to fully open
        self._previous_state: Optional[int] = None
        self._movement_source = "ha"
        self._direction: Optional[str] = None  # "opening" or "closing"

        self._operation_time = float(operation_time)
        self._position_estimator = PositionEstimator(
            duration_in_seconds=self._operation_time, start_position=self._position
        )

        self._in_motion = False
        self._movement_task: Optional[asyncio.Task] = None
        self._last_position_change_time = time.monotonic()
        self._button_operation_time: Optional[float] = None

        self._attr_name = channel_description
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self._channel}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

        _LOGGER.debug(
            "NikobusCoverEntity initialized for '%s' (address=%s, channel=%s, operation_time=%.2f seconds)",
            channel_description,
            address,
            channel,
            self._operation_time,
        )

    @property
    def device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._address)},
            "manufacturer": BRAND,
            "name": self._description,
            "model": self._model,
        }

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        attrs = super().extra_state_attributes or {}
        attrs.update({"position": self._position, "state": self._state})
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
                self._position = float(last_position)
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

        self.hass.bus.async_listen(
            "nikobus_button_pressed", self._handle_nikobus_button_event
        )
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        new_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_state != self._previous_state:
            self.hass.async_create_task(self._process_state_change(new_state))
            self.async_write_ha_state()

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
            source = "ha" if event.data.get("virtual", False) else "nikobus"
            await self._process_state_change(new_state, source=source)
            self.async_write_ha_state()
        else:
            _LOGGER.debug("No state change for %s; ignoring event.", self._attr_name)

    async def async_open_cover(self, **kwargs: Any) -> None:
        try:
            await self._start_movement("opening")
        except Exception as exc:
            _LOGGER.error(
                "Failed to open cover %s: %s", self._attr_name, exc, exc_info=True
            )

    async def async_close_cover(self, **kwargs: Any) -> None:
        try:
            await self._start_movement("closing")
        except Exception as exc:
            _LOGGER.error(
                "Failed to close cover %s: %s", self._attr_name, exc, exc_info=True
            )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        try:

            async def completion_handler() -> None:
                await self._finalize_movement()

            await self.coordinator.api.stop_cover(
                self._address,
                self._channel,
                self._direction,
                completion_handler=completion_handler,
            )
        except Exception as exc:
            _LOGGER.error(
                "Failed to stop cover %s: %s", self._attr_name, exc, exc_info=True
            )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        target_position = kwargs.get(ATTR_POSITION)
        if target_position is None:
            return

        current_time = time.monotonic()
        if current_time - self._last_position_change_time < 1:
            _LOGGER.debug(
                "Skipping position update for %s due to rapid commands.",
                self._attr_name,
            )
            return

        self._last_position_change_time = current_time

        if self._position == target_position:
            _LOGGER.debug("Cover %s is already at target position.", self._attr_name)
            return

        direction = "opening" if target_position > self._position else "closing"
        try:
            await self._start_movement(direction, target_position=target_position)
        except Exception as exc:
            _LOGGER.error(
                "Failed to set position for cover %s: %s",
                self._attr_name,
                exc,
                exc_info=True,
            )

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
            if self._in_motion and self._state == new_state:
                return

            if self._in_motion:
                await self._finalize_movement()

            self._direction = "opening" if new_state == STATE_OPENING else "closing"
            self._in_motion = True
            self._state = new_state
            self._position_estimator.start(self._direction, self._position)
            self._movement_task = self.hass.async_create_task(self._update_position())
        elif new_state == STATE_STOPPED:
            if self._in_motion:
                await self._finalize_movement()
        elif new_state == STATE_ERROR:
            await self.async_stop_cover()
            _LOGGER.warning("Error state encountered for %s.", self._attr_name)
        else:
            _LOGGER.warning(
                "Unknown state '%s' encountered for %s.", new_state, self._attr_name
            )

    async def _start_movement(
        self, direction: str, target_position: Optional[int] = None
    ) -> None:
        if self._in_motion:
            await self._finalize_movement()

        async def completion_handler() -> None:
            self._direction = direction
            self._in_motion = True
            self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
            self._position_estimator.start(self._direction, self._position)
            await self._start_position_estimation(target_position=target_position)
            self.async_write_ha_state()

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

    async def _start_position_estimation(
        self, target_position: Optional[int] = None
    ) -> None:
        if self._movement_task and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "Cancelled existing movement task for %s", self._attr_name
                )
        self._movement_task = self.hass.async_create_task(
            self._update_position(target_position)
        )

    async def _update_position(self, target_position: Optional[int] = None) -> None:
        start_time = time.monotonic()
        try:
            while self._in_motion:
                if self._position is None:
                    _LOGGER.error(
                        "Position is None in _update_position for %s; defaulting based on direction.",
                        self._attr_name,
                    )
                    self._position = 0 if self._direction == "closing" else 100

                estimated_position = self._position_estimator.get_position()
                if estimated_position is not None:
                    self._position = estimated_position
                else:
                    _LOGGER.warning(
                        "Position estimator returned None in _update_position for %s",
                        self._attr_name,
                    )

                elapsed = time.monotonic() - start_time
                if (
                    self._button_operation_time
                    and elapsed >= self._button_operation_time
                ):
                    await self.async_stop_cover()
                    return

                if target_position is not None:
                    if (
                        self._direction == "opening"
                        and self._position >= target_position
                        and target_position < 100
                    ) or (
                        self._direction == "closing"
                        and self._position <= target_position
                        and target_position > 0
                    ):
                        _LOGGER.debug(
                            "Target position %d reached for %s",
                            target_position,
                            self._attr_name,
                        )
                        self._position = target_position
                        self._in_motion = False
                        self._direction = None
                        self._state = STATE_STOPPED
                        if self._movement_source == "ha":
                            await self.async_stop_cover()
                        else:
                            await self._finalize_movement()
                        return

                if (self._direction == "opening" and self._position >= 100) or (
                    self._direction == "closing" and self._position <= 0
                ):
                    _LOGGER.debug(
                        "Cover %s fully %s.", self._attr_name, self._direction
                    )
                    self._position = 100 if self._direction == "opening" else 0
                    self._in_motion = False
                    self._direction = None
                    self._state = STATE_STOPPED
                    self.async_write_ha_state()
                    if self._movement_source == "ha":
                        await asyncio.sleep(self._operation_time)
                        await self.async_stop_cover()
                    else:
                        await self._finalize_movement()
                    return

                self.async_write_ha_state()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _LOGGER.debug("Position update for %s was cancelled.", self._attr_name)
        except Exception as e:
            _LOGGER.error(
                "Unexpected error in _update_position for %s: %s",
                self._attr_name,
                e,
                exc_info=True,
            )
            await self._finalize_movement()
        finally:
            self._movement_task = None

    async def _finalize_movement(self) -> None:
        """Finalize cover movement, stop the estimator, and reset state."""
        _LOGGER.debug("Finalizing movement for %s", self._attr_name)
        self._position_estimator.stop()

        if self._movement_task and not self._movement_task.done():
            self._movement_task.cancel()
            try:
                await self._movement_task
            except asyncio.CancelledError:
                _LOGGER.debug("Cancelled movement task for %s.", self._attr_name)

        self._in_motion = False
        self._direction = None
        self._button_operation_time = None
        self._state = STATE_STOPPED
        self.async_write_ha_state()
        self.coordinator.set_bytearray_state(
            self._address, self._channel, STATE_STOPPED
        )

    async def _handle_nikobus_button_event(self, event: Any) -> None:
        """Handle the `nikobus_button_pressed` event and update the cover state."""
        if event.data.get("impacted_module_address") != self._address:
            _LOGGER.debug("Skipping event for %s (not impacted).", self._attr_name)
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
            self.async_write_ha_state()
        else:
            _LOGGER.debug("No state change for %s; ignoring event.", self._attr_name)
