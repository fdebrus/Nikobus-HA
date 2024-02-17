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

    # async def async_updated_data(self, data) -> None:
    #    """Update data."""
    #    super().async_set_updated_data(data)

    def set_updated_data(self, data) -> None:
        """Receive Data."""
        asyncio.run_coroutine_threadsafe(self.async_updated_data(data), self.hass.loop).result()

    async def getOutputState(self, address, channel) -> Any:
        """Return status of address channel."""
        return await self.api.getOutputState(address, channel)

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