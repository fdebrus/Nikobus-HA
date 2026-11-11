"""The outputs of a dimmer module."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DIMMER_MODULE
from .entity import ChannelEntity, relay_outputs, remove_relay_entities
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One light per dimmer output, and per relay the options show as a light."""
    hub = entry.runtime_data
    remove_relay_entities(hass, Platform.LIGHT, relay_outputs(hub, as_light=False))
    async_add_entities(
        NikobusLight(hub, module, channel)
        for module in hub.inventory.output_modules()
        if module.module_type == DIMMER_MODULE
        for channel in range(1, module.channels + 1)
    )
    async_add_entities(
        NikobusRelayLight(hub, module, channel) for module, channel in relay_outputs(hub, as_light=True)
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


class NikobusRelayLight(ChannelEntity, LightEntity):
    """A relay that switches a lamp."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        """Whether the relay is closed."""
        value = self._value
        return None if value is None else value != 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Close the relay."""
        await self.hub.async_set_output(self._address, self._channel, 0xFF)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Open the relay."""
        await self.hub.async_set_output(self._address, self._channel, 0x00)
