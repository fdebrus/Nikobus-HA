"""Nikobus Coordinator"""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry

from .nikobus import Nikobus, NikobusConnectionError, NikobusDataError
from .const import (
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE
)

_LOGGER = logging.getLogger(__name__)

class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for managing asynchronous updates and connections to the Nikobus system."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator with Home Assistant and configuration entry."""
        self.hass = hass
        self.api = None
        self._config_entry = config_entry
        self.connection_string = config_entry.options.get(CONF_CONNECTION_STRING, config_entry.data.get(CONF_CONNECTION_STRING))
        self.refresh_interval = config_entry.options.get(CONF_REFRESH_INTERVAL, config_entry.data.get(CONF_REFRESH_INTERVAL, 120))
        self.has_feedback_module = config_entry.options.get(CONF_HAS_FEEDBACK_MODULE, config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False))

        # Set update_interval to None if feedback module is present, disabling periodic updates
        update_interval = None if self.has_feedback_module else timedelta(seconds=self.refresh_interval)
        
        super().__init__(
            hass,
            _LOGGER,
            name="Nikobus",
            update_method=self._get_update_method(),
            update_interval=update_interval,
        )
        self._unsub_update_listener = None

    def _get_update_method(self):
        """Return the appropriate update method based on the presence of the feedback module."""
        return self.initial_update_data if self.has_feedback_module else self.async_update_data

    async def async_config_entry_first_refresh(self):
        """Handle the first data refresh and set up the update listener."""
        await self.async_refresh()
        self._unsub_update_listener = self._config_entry.add_update_listener(self.async_config_entry_updated)

    async def connect(self):
        """Connect to the Nikobus system."""
        try:
            self.api = await Nikobus.create(self.hass, self._config_entry, self.connection_string, self.async_event_handler)
            self.hass.async_create_task(self.api.command_handler())
            self.hass.async_create_task(self.api.listen_for_events())
        except NikobusConnectionError as e:
            _LOGGER.error("Failed to connect to Nikobus: %s", e)
            raise NikobusConnectError("Failed to connect to Nikobus.", original_exception=e)

    async def async_update_data(self):
        """Fetch the latest data from the Nikobus system."""
        try:
            _LOGGER.debug("Refreshing Nikobus data")
            return await self.api.refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def async_event_handler(self, event, data):
        """Handle events received from the Nikobus system."""
        if event == "ha_button_pressed":
            await self.api.nikobus_command_handler.queue_command(f'#N{data}\r#E1')
        elif event == "nikobus_button_pressed":
            self.hass.bus.async_fire('nikobus_button_pressed', {'address': data})
        self.async_update_listeners()

    async def initial_update_data(self):
        """Perform the initial data update from the Nikobus system."""
        try:
            _LOGGER.debug("Performing initial data refresh for Nikobus")
            return await self.api.refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def async_config_entry_updated(self, entry: ConfigEntry) -> None:
        """Handle updates to the configuration entry."""
        connection_string = entry.options.get(CONF_CONNECTION_STRING, self.connection_string)
        refresh_interval = entry.options.get(CONF_REFRESH_INTERVAL, 120)
        has_feedback_module = entry.options.get(CONF_HAS_FEEDBACK_MODULE, False)

        connection_changed = connection_string != self.connection_string
        refresh_interval_changed = refresh_interval != self.refresh_interval
        feedback_module_changed = has_feedback_module != self.has_feedback_module

        if connection_changed or refresh_interval_changed or feedback_module_changed:
            self.connection_string = connection_string
            self.refresh_interval = refresh_interval
            self.has_feedback_module = has_feedback_module

            await self._async_update_coordinator_settings()

            if connection_changed:
                await self.connect()
                title = f"Nikobus - {connection_string}"
                self.hass.config_entries.async_update_entry(entry, title=title)

            _LOGGER.info(f'Configuration updated: connection_string={self.connection_string}, '
                         f'refresh_interval={self.refresh_interval}, has_feedback_module={self.has_feedback_module}')

    async def _async_update_coordinator_settings(self):
        """Update the coordinator's update method and interval."""
        self.update_method = self._get_update_method()
        self.update_interval = None if self.has_feedback_module else timedelta(seconds=self.refresh_interval)
        await self.async_refresh()

class NikobusConnectError(Exception):
    """Custom exception for handling Nikobus connection errors."""
    
    def __init__(self, message="Failed to connect to Nikobus system", original_exception=None):
        super().__init__(message)
        self.original_exception = original_exception
