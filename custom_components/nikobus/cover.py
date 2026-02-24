"""Cover platform for the Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_CURRENT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
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
from .nkbtravelcalculator import NikobusTravelCalculator

_LOGGER = logging.getLogger(__name__)

HUB_IDENTIFIER = "nikobus_hub"

# Nikobus Internal States
STATE_STOPPED = 0x00
STATE_OPENING = 0x01
STATE_CLOSING = 0x02

# Configuration Constants
COVER_MOVEMENT_BUFFER = 3.0
DEBOUNCE_DELAY = 0.3
DEFAULT_OPERATION_TIME = 30.0

# Event Constants
EVENT_BUTTON_OPERATION = "nikobus_button_operation"
EVENT_BUTTON_PRESS = "nikobus_button_pressed"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
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

        # Parse UP time, fallback to default
        op_time_up = float(spec.operation_time or DEFAULT_OPERATION_TIME)
        
        # Safely parse DOWN time, fallback to UP time if not provided
        op_time_down_raw = getattr(spec, "operation_time_down", None)
        op_time_down = float(op_time_down_raw) if op_time_down_raw else op_time_up

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
        
        # Delegate math to the Travel Calculator helper
        self._calculator = NikobusTravelCalculator(op_time_up, op_time_down)
        
        self._position: float = 100.0
        self._state = STATE_STOPPED
        self._target_position: int | None = None
        self._motion_task: asyncio.Task | None = None
        self._coalesce_task: asyncio.Task | None = None
        
        self._movement_source = "ha"
        self._current_run_limit: float = op_time_up

    @property
    def assumed_state(self) -> bool:
        """Return True because covers can be stopped midway and position is calculated via time."""
        return True

    @property
    def current_cover_position(self) -> int:
        """Return the current position of the cover."""
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
        """Return if the cover is closed."""
        return self.current_cover_position == 0

    @property
    def is_open(self) -> bool:
        """Return if the cover is open."""
        return self.current_cover_position == 100

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            if (pos := last_state.attributes.get(ATTR_CURRENT_POSITION)) is not None:
                self._position = float(pos)
                self._calculator.set_position(self._position)

        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_OPERATION, self._handle_nikobus_event)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_BUTTON_PRESS, self._handle_button_pressed)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle targeted background refresh."""
        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)

        if new_bus_state != STATE_STOPPED and self._state == STATE_STOPPED:
            _LOGGER.info("External movement detected for %s, starting sync", self.name)
            direction = "opening" if new_bus_state == STATE_OPENING else "closing"
            self._movement_source = "nikobus"
            self._start_motion_logic(direction)
        
        elif new_bus_state == STATE_STOPPED and self._state != STATE_STOPPED:
            if self._movement_source == "nikobus":
                _LOGGER.info("External stop detected for %s", self.name)
                self.hass.async_create_task(self._stop(send_stop=False))

        super()._handle_coordinator_update()

    async def _handle_button_pressed(self, event: Any) -> None:
        """Optimistically freeze motion position when any linked physical button is pressed."""
        if str(event.data.get("module_address")) != str(self._address):
            return
        
        if self._state != STATE_STOPPED:
            if self._motion_task:
                self._motion_task.cancel()
                self._motion_task = None
            
            self._calculator.stop()
            self._position = self._calculator.current_position()
            self._state = STATE_STOPPED
            self._target_position = None
            self.coordinator.set_bytearray_state(self._address, self._channel, STATE_STOPPED)
            self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._request_move("opening")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._request_move("closing")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._stop(send_stop=True)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        target = kwargs[ATTR_POSITION]
        if self._coalesce_task:
            self._coalesce_task.cancel()

        async def _debounced_move() -> None:
            await asyncio.sleep(DEBOUNCE_DELAY)
            direction = "opening" if target > self._position else "closing"
            await self._request_move(direction, target)

        self._coalesce_task = self.hass.async_create_task(_debounced_move())

    async def _request_move(self, direction: str, target: int | None = None) -> None:
        """Send movement command."""
        self._movement_source = "ha"
        self._target_position = target
        
        async def on_sent() -> None:
            self._start_motion_logic(direction)

        if direction == "opening":
            await self.coordinator.api.open_cover(self._address, self._channel, on_sent)
        else:
            await self.coordinator.api.close_cover(self._address, self._channel, on_sent)

    def _start_motion_logic(self, direction: str, limit_time: float | None = None) -> None:
        """Initialize the position calculation task."""
        if self._motion_task:
            self._motion_task.cancel()

        self._state = STATE_OPENING if direction == "opening" else STATE_CLOSING
        self._calculator.start_travel(direction)
        
        # Determine the correct limit
        active_op_time = self._calculator.time_up if direction == "opening" else self._calculator.time_down
        self._current_run_limit = float(limit_time) if limit_time else (active_op_time + COVER_MOVEMENT_BUFFER)
        
        self._motion_task = self.hass.async_create_task(self._motion_loop())
        self.async_write_ha_state()

    async def _motion_loop(self) -> None:
        """Loop to calculate position based on time."""
        try:
            start_time = time.monotonic()
            while self._state in (STATE_OPENING, STATE_CLOSING):
                elapsed = time.monotonic() - start_time
                
                self._position = self._calculator.current_position()

                if elapsed >= self._current_run_limit or self._should_stop():
                    await self._stop(send_stop=(self._movement_source == "ha"))
                    break

                self.async_write_ha_state()
                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            pass

    def _should_stop(self) -> bool:
        """Check if target reached."""
        if self._state == STATE_OPENING:
            return self._target_position is not None and self._position >= self._target_position
        if self._state == STATE_CLOSING:
            return self._target_position is not None and self._position <= self._target_position
        return False

    async def _stop(self, send_stop: bool = False) -> None:
        """Stop movement."""
        if self._motion_task:
            self._motion_task.cancel()
            self._motion_task = None

        self._calculator.stop()
        self._position = self._calculator.current_position()

        if send_stop and self._state != STATE_STOPPED:
            dir_cmd = "opening" if self._state == STATE_OPENING else "closing"
            await self.coordinator.api.stop_cover(self._address, self._channel, dir_cmd, lambda: None)

        self._state = STATE_STOPPED
        self._target_position = None
        self.coordinator.set_bytearray_state(self._address, self._channel, STATE_STOPPED)
        self.async_write_ha_state()

    async def _handle_nikobus_event(self, event: Any) -> None:
        """Handle physical button events."""
        if str(event.data.get("impacted_module_address")) != str(self._address):
            return

        new_bus_state = self.coordinator.get_cover_state(self._address, self._channel)
        if new_bus_state == self._state and self._state == STATE_STOPPED:
            return

        if new_bus_state in (STATE_OPENING, STATE_CLOSING):
            if self._state == STATE_STOPPED:
                self._movement_source = "nikobus"
                direction = "opening" if new_bus_state == STATE_OPENING else "closing"
                self._start_motion_logic(direction, event.data.get("button_operation_time"))
        elif new_bus_state == STATE_STOPPED and self._state != STATE_STOPPED:
            await self._stop(send_stop=False)