"""Nikobus Config Flow."""
from typing import Any, Optional
import voluptuous as vol
from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import logging

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

AUTH_ERROR = 'auth_error'

CONF_CONNECTION_STRING = "connection_string"

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nikobus config flow."""

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            # Extract the connection string from user input
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            if not connection_string:
                # If the connection string is missing or invalid, report an error
                errors['base'] = 'invalid_connection_string'
            else:
                return await self._create_entry({CONF_CONNECTION_STRING: connection_string})

        user_input_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_STRING): str,
        })

        # Show the configuration form to the user
        return self.async_show_form(
            step_id="user", 
            data_schema=user_input_schema, 
            errors=errors
        )

    async def _create_entry(self, data: dict[str, Any]):
        """Helper function to determine the title based on connection string and create the entry."""
        # Extract the connection string from the data, defaulting to "Unknown" if not found
        connection_string = data.get(CONF_CONNECTION_STRING, "Unknown Connection")
        
        # Create a title that includes the connection string
        title = f"Nikobus PC-Link - {connection_string}"
        
        # Call the base class's async_create_entry method to actually create/update the entry
        return super().async_create_entry(title=title, data=data)

    async def async_create_entry(self, title: str, data: dict) -> dict:
        """Create an entry, update if exists based on the connection string."""
        # Find an existing entry based on the connection string
        existing_entry = next(
            (entry for entry in self._async_current_entries()
            if entry.data.get(CONF_CONNECTION_STRING) == data.get(CONF_CONNECTION_STRING)), None)

        if existing_entry:
            # Update the existing entry if found
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            _LOGGER.info("Existing Nikobus entry updated with new connection string.")
            return self.async_abort(reason="reauth_successful")

        # Proceed to create a new entry if no existing entry matches
        return super().async_create_entry(title=title, data=data)
