"""Dimmer outputs that drive a fan.

A dimmer output wired to a variable-speed fan is the same byte as a
dimmed lamp; ticked as a fan in the options, it gets a speed instead of
a brightness.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .entity import ChannelEntity, outputs_of
from .hub import NikobusHub

PARALLEL_UPDATES = 0

_LEVELS = (1, 255)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One fan per dimmer output the options show as one."""
    hub = entry.runtime_data
    async_add_entities(NikobusFan(hub, module, channel) for module, channel in outputs_of(hub, Platform.FAN))


class NikobusFan(ChannelEntity, FanEntity):
    """A dimmed output; its byte is its speed."""

    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )

    @property
    def is_on(self) -> bool | None:
        """Whether the level is above zero."""
        value = self._value
        return None if value is None else value > 0

    @property
    def percentage(self) -> int | None:
        """The level as a percentage."""
        value = self._value
        if value is None:
            return None
        return ranged_value_to_percentage(_LEVELS, value) if value else 0

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed; zero switches off."""
        level = round(percentage_to_ranged_value(_LEVELS, percentage)) if percentage else 0
        await self.hub.async_set_output(self._address, self._channel, level)

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        """Run at the speed given, or at full speed."""
        await self.async_set_percentage(100 if percentage is None else percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the fan."""
        await self.hub.async_set_output(self._address, self._channel, 0x00)
