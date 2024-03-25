"""Nikobus Config Flow."""

from typing import Any, Optional
import voluptuous as vol
from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import logging

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

# Define constants for error handling
AUTH_ERROR = 'auth_error'
CONF_CONNECTION_STRING = "connection_string"

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the configuration flow for Nikobus integration.

    This class manages the dialogue between the user and the integration
    for setting up Nikobus within Home Assistant. It handles initial setup,
    input validation, and updating of connection settings.
    """

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user.

        This step is invoked when the user initiates the configuration process.
        It asks the user for the connection string to communicate with Nikobus.
        """
        errors = {}
        if user_input is not None:
            # Attempt to process the user-provided connection string
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            if not connection_string:
                # Report back to the user if the connection string is missing or invalid
                errors['base'] = 'invalid_connection_string'
            else:
                # Proceed to create an entry with the valid connection string
                return await self._create_entry({CONF_CONNECTION_STRING: connection_string})

        # Schema defining the configuration options the user can set, currently only the connection string
        user_input_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_STRING): str,
        })

        # Render the configuration form in the UI with any validation errors
        return self.async_show_form(
            step_id="user", 
            data_schema=user_input_schema, 
            errors=errors
        )

    async def _create_entry(self, data: dict[str, Any]):
        """Helper function to create the entry with a meaningful title.

        The title is derived from the connection string, making it easier for the user
        to identify their Nikobus connection in the Home Assistant UI.
        """
        # Default title includes the connection string for clarity
        title = f"Nikobus PC-Link - {data.get(CONF_CONNECTION_STRING, 'Unknown Connection')}"
        
        # Complete the creation or update of the configuration entry
        return super().async_create_entry(title=title, data=data)

    async def async_create_entry(self, title: str, data: dict) -> dict:
        """Override the creation of a config entry to handle duplicates.

        If an entry with the same connection string already exists, update it instead of creating a new one.
        This prevents duplicate entries for the same Nikobus connection.
        """
        # Search for an existing entry with the same connection string
        existing_entry = next(
            (entry for entry in self._async_current_entries()
            if entry.data.get(CONF_CONNECTION_STRING) == data.get(CONF_CONNECTION_STRING)), None)

        if existing_entry:
            # Update the existing entry and reload its configuration
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            _LOGGER.info("Existing Nikobus entry updated with new connection string.")
            # Stop the flow as the entry has been updated
            return self.async_abort(reason="reauth_successful")

        # If no existing entry is found, proceed to create a new one
        return super().async_create_entry(title=title, data=data)
