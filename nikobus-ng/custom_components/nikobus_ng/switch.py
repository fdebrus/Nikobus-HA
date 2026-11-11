"""The outputs of a switch module."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SWITCH_MODULE
from .entity import ChannelEntity
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One switch per relay. *Show as* makes it a light in Home Assistant."""
    hub = entry.runtime_data
    async_add_entities(
        NikobusSwitch(hub, module, channel)
        for module in hub.inventory.output_modules()
        if module.module_type == SWITCH_MODULE
        for channel in range(1, module.channels + 1)
    )


class NikobusSwitch(ChannelEntity, SwitchEntity):
    """A relay."""

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
