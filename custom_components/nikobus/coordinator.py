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

    def is_on(self, module, channel) -> Any:
        """Return status of module channel."""
        return self.data.get(module, channel)

    async def turn_on_switch(self, module, channel) -> None:
        """Turn on module channel."""
        await self.api.turn_on_switch(module, channel)

    async def turn_off_switch(self, module, channel) -> None:
        """Turn off module channel."""
        await self.api.turn_off_switch(module, channel)
