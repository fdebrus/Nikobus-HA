"""Coordinator for Nikobus."""
from typing import Any
from datetime import timedelta

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Nikobus custom coordinator."""

    def __init__(self, hass: HomeAssistant, api) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method = api.refresh_nikobus_data,
            update_interval = timedelta(seconds=120)
        )
        self.api = api

#### GENERAL
    async def get_output_state(self, address, channel, timeout) -> Any:
        """Return status of address channel."""
        _state = self.api.get_output_state(address, channel, timeout)
        _LOGGER.debug("get_output_state:%s %s %s",address, channel, _state)
        return _state
####

#### SWITCHES
    def get_switch_state(self, address, channel) -> Any:
        """Get State on address / channel."""
        return self.api.get_switch_state(address, channel)

    async def turn_on_switch(self, address, channel) -> None:
        """Turn on address address / channel"""
        await self.api.turn_on_switch(address, channel)

    async def turn_off_switch(self, address, channel) -> None:
        """Turn off address address / Channel"""
        await self.api.turn_off_switch(address, channel)
####

#### DIMMERS
    def get_light_state(self, address, channel):
        return self.api.get_light_state(address, channel)
        
    def get_light_brightness(self, address, channel):
        return self.api.get_light_brightness(address, channel)

    async def turn_on_light(self, address, channel, brightness) -> None:
        """Turn on address / channel with brightness"""
        await self.api.turn_on_light(address, channel, brightness)

    async def turn_off_light(self, address, channel) -> None:
        """Turn off address / channel"""
        await self.api.turn_off_light(address, channel)
####

#### COVERS
    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.api.open_cover(address, channel)

    async def async_close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.api.close_cover(address, channel)

    async def async_stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.api.stop_cover(address, channel)

    async def get_cover_state(self, address, channel) -> None:
        """Update the state of the cover."""
        await self.api.get_cover_state(address, channel)
#### 
