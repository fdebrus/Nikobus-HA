"""Fan platform for the Nikobus integration.

A dimmer-module output (05-007) that drives a PWM / variable-speed fan —
a bathroom or dressing extractor — is the same 0-255 channel as a
dimmed light; only its presentation differs. Choosing *Fan* for the
channel under *Customize a module* creates this entity instead of the
light, so Home Assistant (and HomeKit) offer a speed, not a brightness.
Same bus commands, same state buffer, same optimistic behaviour as the
dimmer light.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import operation_signal
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import NikobusEntity, command_error
from .router import build_unique_id, get_routing, register_output_module_devices

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

_MAX_LEVEL = 255


def percentage_to_level(percentage: int) -> int:
    """0-100 % to the channel's 0-255 level; any non-zero speed is at least 1."""
    pct = max(0, min(100, int(percentage)))
    if pct == 0:
        return 0
    return max(1, round(pct * _MAX_LEVEL / 100))


def level_to_percentage(level: int) -> int:
    """The channel's 0-255 level to 0-100 %; any non-zero level is at least 1 %."""
    lvl = max(0, min(_MAX_LEVEL, int(level)))
    if lvl == 0:
        return 0
    return max(1, round(lvl * 100 / _MAX_LEVEL))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Nikobus fan entities from a config entry."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    routing = get_routing(hass, entry, coordinator.dict_module_data)
    specs = routing.get("fan", [])
    register_output_module_devices(hass, entry, specs)

    async_add_entities(
        NikobusDimmerFanEntity(
            coordinator, spec.address, spec.channel,
            spec.channel_description, spec.module_desc, spec.module_model,
        )
        for spec in specs
        if spec.kind == "dimmer_fan"
    )


class NikobusDimmerFanEntity(NikobusEntity, FanEntity, RestoreEntity):
    """A Nikobus dimmer output presented as a variable-speed fan."""

    _attr_supported_features = FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    _attr_speed_count = 100

    def __init__(
        self, coordinator: NikobusDataCoordinator, address: str, channel: int,
        description: str, module_name: str, module_model: str
    ) -> None:
        """Initialize the fan."""
        super().__init__(coordinator, address, module_name, module_model)
        self._address = address
        self._channel = channel
        self._channel_description = description
        self._module_description = module_name
        self._module_model = module_model
        self._attr_name = description
        self._attr_unique_id = build_unique_id("fan", "dimmer_fan", self._address, self._channel)
        # Optimistic state between a command and the bus confirming it,
        # exactly as the dimmer light keeps them.
        self._is_on: bool | None = None
        self._optimistic_level: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        parent_attrs = super().extra_state_attributes or {}
        return {
            **parent_attrs,
            "nikobus_address": self._address,
            "channel": self._channel,
            "channel_description": self._channel_description,
            "module_description": self._module_description,
            "module_model": self._module_model,
            "controlled_by": self.coordinator.get_controlled_by(self._address, self._channel),
        }

    async def async_added_to_hass(self) -> None:
        """Register listeners and restore state."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            self._is_on = last_state.state == "on"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                operation_signal(self._address),
                self._handle_button_operation,
            )
        )

    def _invalidate_optimistic(self) -> None:
        """Drop the optimistic state so the real hardware state is read."""
        self._is_on = None
        self._optimistic_level = None

    def _render_state(self) -> Any:
        """Diff on on/off + speed so an unchanged poll skips the write."""
        return (self.is_on, self.percentage)

    @callback
    def _handle_button_operation(self) -> None:
        """A press impacted this module — drop optimistic state."""
        self._invalidate_optimistic()
        self.async_write_ha_state()

    def _level(self) -> int:
        if self._optimistic_level is not None:
            return self._optimistic_level
        return self.coordinator.get_light_brightness(self._address, self._channel)

    @property
    def is_on(self) -> bool:
        """Return optimistic state if set, else whether the channel is above zero."""
        if self._is_on is not None:
            return self._is_on
        return self._level() > 0

    @property
    def percentage(self) -> int | None:
        """Current speed, 0-100 %."""
        return level_to_percentage(self._level())

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        """Turn the fan on, at ``percentage`` or full speed."""
        target = _MAX_LEVEL if percentage is None else percentage_to_level(percentage)
        if target == 0:
            await self.async_turn_off()
            return
        previous = self._level()
        self._is_on = True
        self._optimistic_level = target
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_on_light(
                self._address, self._channel, target, current_brightness=previous,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._invalidate_optimistic()
            self.async_write_ha_state()
            raise command_error(err) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        previous = self._level()
        self._is_on = False
        self._optimistic_level = None
        self.async_write_ha_state()
        try:
            await self.coordinator.api.turn_off_light(
                self._address, self._channel, current_brightness=previous,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._invalidate_optimistic()
            self.async_write_ha_state()
            raise command_error(err) from err

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed; 0 % turns the fan off."""
        if percentage <= 0:
            await self.async_turn_off()
        else:
            await self.async_turn_on(percentage=percentage)
