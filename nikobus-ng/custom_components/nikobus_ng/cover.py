"""The outputs of a roller shutter module.

A roller output says whether its motor runs up, runs down or rests,
never where the shutter is. The position is therefore estimated from
how long the motor has run, with the run times the module's own links
hold. A move started at the wall is followed from the state the module
reports; a move started here is ended here, at the position asked for.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from nikobus_connect.travel import CLOSING, OPENING, TravelCalculator

from .const import (
    CONF_CLOSE_TIME,
    CONF_OPEN_TIME,
    COVER_CLOSING,
    COVER_DEFAULT_RUN_TIME,
    COVER_END_MARGIN,
    COVER_OPENING,
    COVER_STOPPED,
)
from .entity import ChannelEntity, outputs_of
from .hub import NikobusHub
from .inventory import OutputModule

PARALLEL_UPDATES = 0

_TICK = timedelta(seconds=1)
# The motor rests this long between two directions.
_REVERSE_PAUSE = 0.5
_VALUES = {OPENING: COVER_OPENING, CLOSING: COVER_CLOSING}
_DIRECTIONS = {COVER_OPENING: OPENING, COVER_CLOSING: CLOSING}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One cover per roller output that drives a shutter."""
    hub = entry.runtime_data
    async_add_entities(
        NikobusCover(hub, module, channel) for module, channel in outputs_of(hub, Platform.COVER)
    )


class NikobusCover(ChannelEntity, CoverEntity, RestoreEntity):
    """A shutter with an estimated position."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, hub: NikobusHub, module: OutputModule, channel: int) -> None:
        super().__init__(hub, module, channel)
        # The times set in the options, else the ones the module's links hold.
        settings = hub.shutters.get(f"{module.address}:{channel}", {})
        up = (
            settings.get(CONF_OPEN_TIME)
            or hub.inventory.run_time(module.address, channel, "up")
            or COVER_DEFAULT_RUN_TIME
        )
        down = settings.get(CONF_CLOSE_TIME) or hub.inventory.run_time(module.address, channel, "down") or up
        self._travel = TravelCalculator(up, down)
        self._moving: str | None = None
        self._target: float | None = None
        # Whether the move was started here, so that it is ended here.
        self._commanded = False
        self._end_at: float | None = None
        self._untrack: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Take the last known position back."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (position := last.attributes.get(ATTR_CURRENT_POSITION)) is not None:
            self._travel.set_position(float(position))
        self.async_on_remove(self._stop_ticking)

    @property
    def current_cover_position(self) -> int:
        """The estimated position, 0 closed to 100 open."""
        return round(self._travel.current_position())

    @property
    def is_opening(self) -> bool:
        """Whether the motor runs up."""
        return self._moving == OPENING

    @property
    def is_closing(self) -> bool:
        """Whether the motor runs down."""
        return self._moving == CLOSING

    @property
    def is_closed(self) -> bool:
        """Whether the estimate is fully closed."""
        return self.current_cover_position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Run up to the top."""
        await self._async_move(OPENING, 100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Run down to the bottom."""
        await self._async_move(CLOSING, 0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Run to a position and stop there."""
        target = float(kwargs[ATTR_POSITION])
        current = self._travel.current_position()
        if round(target) == round(current):
            return
        await self._async_move(OPENING if target > current else CLOSING, target)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the motor, whoever started it."""
        self._settle()
        await self.hub.async_set_output(self._address, self._channel, COVER_STOPPED)

    async def _async_move(self, direction: str, target: float) -> None:
        if self._moving is not None and self._moving != direction:
            await self.async_stop_cover()
            await asyncio.sleep(_REVERSE_PAUSE)
        self._begin(direction, target, commanded=True)
        try:
            await self.hub.async_set_output(self._address, self._channel, _VALUES[direction])
        except HomeAssistantError:
            self._settle()
            raise

    def _begin(self, direction: str, target: float | None, *, commanded: bool) -> None:
        """The motor started: follow it."""
        self._travel.start_travel(direction)
        self._moving = direction
        self._target = target
        self._commanded = commanded
        self._end_at = None
        if self._untrack is None:
            self._untrack = async_track_time_interval(self.hass, self._async_tick, _TICK)
        self.async_write_ha_state()

    def _settle(self) -> None:
        """The motor stopped: keep the position reached."""
        self._travel.stop()
        self._moving = None
        self._target = None
        self._commanded = False
        self._end_at = None
        self._stop_ticking()
        self.async_write_ha_state()

    def _stop_ticking(self) -> None:
        if self._untrack is not None:
            self._untrack()
            self._untrack = None

    async def _async_tick(self, _now: datetime) -> None:
        """Once a second while moving: publish, and stop where asked."""
        position = self._travel.current_position()
        at_end = position <= 0 or position >= 100
        reached = self._target is not None and (
            position >= self._target if self._moving == OPENING else position <= self._target
        )
        if not at_end and not reached:
            self.async_write_ha_state()
            return
        if not self._commanded:
            # Started at the wall: the module ends it by its own run time.
            if at_end:
                self._settle()
            else:
                self.async_write_ha_state()
            return
        if at_end:
            # Let the motor reach its end stop before the relay is released.
            if self._end_at is None:
                self._end_at = time.monotonic() + COVER_END_MARGIN
                self.async_write_ha_state()
                return
            if time.monotonic() < self._end_at:
                return
        self._settle()
        try:
            await self.hub.async_set_output(self._address, self._channel, COVER_STOPPED)
        except HomeAssistantError:
            # The module ends the run by itself; the estimate stands.
            return

    def _on_module_update(self) -> None:
        """The module reported: adopt a move started elsewhere, or its end."""
        value = self._value
        direction = _DIRECTIONS.get(value) if value is not None else None
        if direction is not None and direction != self._moving:
            self._begin(direction, None, commanded=False)
        elif direction is None and value is not None and self._moving is not None:
            commanded = self._commanded
            self._settle()
            if value != COVER_STOPPED and commanded:
                # 0x03: both relays were asked for at once. Release them.
                self.hass.async_create_task(
                    self.hub.async_set_output(self._address, self._channel, COVER_STOPPED)
                )
        else:
            self.async_write_ha_state()
