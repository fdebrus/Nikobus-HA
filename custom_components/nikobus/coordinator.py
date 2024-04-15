"""Coordinator for Nikobus"""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry

from .nikobus import Nikobus, NikobusConnectionError, NikobusDataError

_LOGGER = logging.getLogger(__name__)

CONF_CONNECTION_STRING = "connection_string"
CONF_REFRESH_INTERVAL = "refresh_interval"

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for asynchronous management of Nikobus updates"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.api = None
        self.connection_string = entry.data.get(CONF_CONNECTION_STRING)
        self.refresh_interval = entry.options.get(CONF_REFRESH_INTERVAL, 120)
        
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=self.async_update_data,
            update_interval=timedelta(seconds=self.refresh_interval),
        )

    async def connect(self):
        """Connect to the Nikobus system"""
        try:
            self.api = await Nikobus.create(self.hass, self.connection_string, self.async_event_handler)
            await self.api.listen_for_events()
            await self.api.command_handler()
        except NikobusConnectionError as e:
            _LOGGER.error("Failed to connect to Nikobus: %s", e)
            raise NikobusConnectError("Failed to connect to Nikobus.", original_exception=e)

    async def async_update_data(self):
        """Fetch the latest data from Nikobus"""
        try:
            _LOGGER.debug("Refreshing Nikobus data")
            return await self.api.refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def async_event_handler(self, event, data):
        """Handle events from Nikobus."""
        if "ha_button_pressed" in event:
            await self.api.nikobus_command_handler.queue_command(f'#N{data}\r#E1')
        self.async_update_listeners()

    async def async_config_entry_updated(self, entry: ConfigEntry) -> None:
        """Handle updates to the configuration entry."""
        new_refresh_interval = entry.options.get(CONF_REFRESH_INTERVAL, 120)
        if new_refresh_interval != self.refresh_interval:
            self.refresh_interval = new_refresh_interval
            self.update_interval = timedelta(seconds=self.refresh_interval)
            _LOGGER.info("Updated the Nikobus refresh interval to %s seconds", self.refresh_interval)

class NikobusConnectError(Exception):
    def __init__(self, message="Failed to connect to Nikobus system", original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception
