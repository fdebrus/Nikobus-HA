import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry

from .nikobus import Nikobus, NikobusConnectionError, NikobusDataError

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for asynchronous management of Nikobus updates"""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self.hass = hass
        self.api = None
        self._config_entry = config_entry
        self.connection_string = self._config_entry.data.get(CONF_CONNECTION_STRING)
        self.refresh_interval = self._config_entry.options.get(CONF_REFRESH_INTERVAL, self._config_entry.data.get(CONF_REFRESH_INTERVAL, 120))
        self.has_feedback_module = self._config_entry.options.get(CONF_HAS_FEEDBACK_MODULE, self._config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))

        # Set update_interval to None if feedback module is present, disabling periodic updates
        update_interval = None if self.has_feedback_module else timedelta(seconds=self.refresh_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=self.async_update_data if not self.has_feedback_module else self.initial_update_data,
            update_interval=update_interval,
        )

    async def connect(self):
        """Connect to the Nikobus system"""
        try:
            self.api = await Nikobus.create(self.hass, self._config_entry, self.connection_string, self.async_event_handler, self)
            await self.api.command_handler()

            self.hass.async_create_task(self.api.listen_for_events())

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

    async def initial_update_data(self):
        """Fetch the latest data from Nikobus initially"""
        try:
            _LOGGER.debug("Performing initial data refresh for Nikobus")
            return await self.api.refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def async_config_entry_updated(self, entry: ConfigEntry) -> None:
        """Handle updates to the configuration entry."""
        new_refresh_interval = entry.options.get(CONF_REFRESH_INTERVAL, 120)
        new_has_feedback_module = entry.options.get(CONF_HAS_FEEDBACK_MODULE, False)

        if new_refresh_interval != self.refresh_interval or new_has_feedback_module != self.has_feedback_module:
            self.refresh_interval = new_refresh_interval
            self.has_feedback_module = new_has_feedback_module
            self.update_interval = None if self.has_feedback_module else timedelta(seconds=self.refresh_interval)
            self.update_method = self.async_update_data if not self.has_feedback_module else self.initial_update_data
            _LOGGER.info("Updated the Nikobus refresh interval to %s seconds", self.refresh_interval)
            _LOGGER.info("Feedback module status updated: %s", self.has_feedback_module)

class NikobusConnectError(Exception):
    def __init__(self, message="Failed to connect to Nikobus system", original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception
