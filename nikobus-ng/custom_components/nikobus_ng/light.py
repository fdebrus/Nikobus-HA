"""The outputs of a dimmer module."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DIMMER_MODULE
from .entity import ChannelEntity, OnOffEntity, outputs_of
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A light per dimmer output, and per relay or roller output the options show as one."""
    hub = entry.runtime_data
    async_add_entities(
        (NikobusLight if module.module_type == DIMMER_MODULE else NikobusOnOffLight)(hub, module, channel)
        for module, channel in outputs_of(hub, Platform.LIGHT)
    )


class NikobusLight(ChannelEntity, LightEntity):
    """A dimmed output; its byte is its level."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        """Whether the level is above zero."""
        value = self._value
        return None if value is None else value > 0

    @property
    def brightness(self) -> int | None:
        """The level, 0-255."""
        return self._value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the level; full when none is given."""
        level = max(1, min(255, int(kwargs.get(ATTR_BRIGHTNESS, 255))))
        await self.hub.async_set_output(self._address, self._channel, level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the level to zero."""
        await self.hub.async_set_output(self._address, self._channel, 0x00)


class NikobusOnOffLight(OnOffEntity, LightEntity):
    """A relay that switches a lamp."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}
