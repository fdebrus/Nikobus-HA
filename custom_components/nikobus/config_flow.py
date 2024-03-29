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

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            if not connection_string:
                errors['base'] = 'invalid_connection_string'
            else:
                return await self._create_entry({CONF_CONNECTION_STRING: connection_string})

        user_input_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_STRING): str,
        })

        return self.async_show_form(
            step_id="user", 
            data_schema=user_input_schema, 
            errors=errors
        )

    async def _create_entry(self, data: dict[str, Any]):
        title = f"Nikobus PC-Link - {data.get(CONF_CONNECTION_STRING, 'Unknown Connection')}"
        return super().async_create_entry(title=title, data=data)

    async def async_create_entry(self, title: str, data: dict) -> dict:
        existing_entry = next(
            (entry for entry in self._async_current_entries()
            if entry.data.get(CONF_CONNECTION_STRING) == data.get(CONF_CONNECTION_STRING)), None)

        if existing_entry:
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            _LOGGER.info("Existing Nikobus entry updated with new connection string.")
            return self.async_abort(reason="reauth_successful")

        return super().async_create_entry(title=title, data=data)
