"""Coordinator for Nikobus."""
from typing import Any
from datetime import timedelta

import logging
from .nikobus import Nikobus

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

CONF_CONNECTION_STRING = "connection_string"

class NikobusDataCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.api = None
        self.connection_string = entry.data.get(CONF_CONNECTION_STRING)
        
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=120),
        )

    async def connect(self):
        self.api = await Nikobus.create(self.hass, self.connection_string, self.async_event_handler)

    async def async_config_entry_first_refresh(self):
        """Perform the initial data refresh."""
        try:
            _LOGGER.debug("Calling initial REFRESH")
            return await self.api.refresh_nikobus_data()
        except Exception as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching data: {e}")

    async def async_update_data(self):
        try:
            _LOGGER.debug("calling REFRESH")
            return await self.api.refresh_nikobus_data()
        except Exception as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching data: {e}")

    async def async_event_handler(self, event, data):
        if "ha_button_pressed" in event:
            await self.api.queue_command(f'#N{data}\r#E1')
        elif "nikobus_button_pressed" in event:
            self.hass.bus.async_fire('nikobus_button_pressed', {'address': data})
        self.async_update_listeners()
