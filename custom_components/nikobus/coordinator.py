"""Coordinator for Nikobus."""
import asyncio
import logging
from typing import Any

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
        )
        self.api = api

    async def async_updated_data(self, data) -> None:
        """Update data."""
        super().async_set_updated_data(data)

    def set_updated_data(self, data) -> None:
        """Receive Data."""
        asyncio.run_coroutine_threadsafe(self.async_updated_data(data), self.hass.loop).result()

    async def getState(self, address, channel) -> Any:
        """Return status of address channel."""
        return await self.api.getState(address, channel)

    async def turn_on_switch(self, address, channel) -> None:
        """Turn on address channel."""
        await self.api.turn_on_switch(address, channel)

    async def turn_off_switch(self, address, channel) -> None:
        """Turn off address channel."""
        await self.api.turn_off_switch(address, channel)

    async def turn_on_light(self, address, channel) -> None:
        """Turn on address channel."""
        await self.api.turn_on_light(address, channel)

    async def turn_off_light(self, address, channel) -> None:
        """Turn off address channel."""
        await self.api.turn_off_light(address, channel)