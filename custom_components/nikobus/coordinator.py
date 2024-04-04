"""Coordinator for Nikobus."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .nikobus import Nikobus, NikobusConnectionError, NikobusDataError

_LOGGER = logging.getLogger(__name__)
CONF_CONNECTION_STRING = "connection_string"

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for asynchronous management of Nikobus updates."""

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
        """Connect to the Nikobus system."""
        try:
            self.api = await Nikobus.create(self.hass, self.connection_string, self.async_event_handler)
            await self.api.listen_for_events()
            await self.api.command_handler()
        except NikobusConnectionError as e:
            _LOGGER.error("Failed to connect to Nikobus: %s", e)
            raise NikobusConnectError from e

    async def async_update_data(self):
        """Fetch the latest data from Nikobus."""
        try:
            _LOGGER.debug("Refreshing Nikobus data")
            return await self.api.refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching data: {e}")

    async def async_event_handler(self, event, data):
        """Handle events from Nikobus."""
        if "ha_button_pressed" in event:
            await self.api.nikobus_command_handler.queue_command(f'#N{data}\r#E1')
        self.async_update_listeners()

class NikobusConnectError(Exception):
    pass
