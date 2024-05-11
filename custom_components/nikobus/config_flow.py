"""Nikobus Config Flow"""

from typing import Any
import voluptuous as vol
import ipaddress
import re

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import logging


from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CONNECTION_STRING = "connection_string"
CONF_REFRESH_INTERVAL = "refresh_interval"

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nikobus integration."""
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def _validate_connection_string(self) -> str:
        try:
            ipaddress.ip_address(self._connection_string.split(':')[0])
            return True
        except ValueError:
            if re.match(r'^/dev/tty(USB|S)\d+$', self._connection_string):
                return True
        return False

    async def async_step_user(self, user_input=None):
        """Handle a flow initiated by the user."""
        return await self.async_step_configure(user_input)

    async def async_step_configure(self, user_input=None):
        """Handle the 'configure' step of the flow."""
        errors = {}
        if user_input is not None:
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            refresh_interval = user_input.get(CONF_REFRESH_INTERVAL)

            data = {
                CONF_CONNECTION_STRING: connection_string,
                CONF_REFRESH_INTERVAL: refresh_interval
            }

            if not self._validate_connection_string(connection_string):
                errors['connection_string'] = 'invalid_connection'
            else:    
                return await self._create_entry(data)

        user_input_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_STRING): str,
            vol.Optional(CONF_REFRESH_INTERVAL, default=120): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
        })

        return self.async_show_form(
            step_id="configure", 
            data_schema=user_input_schema, 
            errors=errors
        )

    async def _create_entry(self, data: dict[str, Any]):
        """Create entry for configuration."""
        title = f"Nikobus PC-Link - {data.get(CONF_CONNECTION_STRING, 'Unknown Connection')}"
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Nikobus integration."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Handle the initial step by redirecting to the 'config' step."""
        return await self.async_step_config(user_input)

    async def async_step_config(self, user_input=None):
        """Handle the 'config' step in options flow."""
        errors = {}
        options = self.config_entry.options

        options_schema = vol.Schema({
            vol.Optional(CONF_REFRESH_INTERVAL, default=options.get(CONF_REFRESH_INTERVAL, 120)): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
        })

        if user_input is not None:
            try:
                valid_input = options_schema(user_input)
                return self.async_create_entry(title="Options Configured", data=valid_input)
            except vol.Invalid as e:
                _LOGGER.error(f"Validation error in options step: {e}")
                errors['base'] = 'invalid_input'

        return self.async_show_form(
            step_id="config",
            data_schema=options_schema,
            errors=errors
        )
