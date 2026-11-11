"""The outputs of a switch module."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ChannelEntity, relay_outputs, remove_relay_entities
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One switch per relay the options do not show as a light."""
    hub = entry.runtime_data
    remove_relay_entities(hass, Platform.SWITCH, relay_outputs(hub, as_light=True))
    async_add_entities(
        NikobusSwitch(hub, module, channel) for module, channel in relay_outputs(hub, as_light=False)
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
