"""Nikobus Config Flow."""
from typing import Any, Optional
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
import homeassistant.helpers.config_validation as cv
import logging

from .const import DOMAIN
from .nikobus import Nikobus

_LOGGER = logging.getLogger(__name__)

AUTH_ERROR = 'auth_error'
DEFAULT_HOST = '192.168.2.40'
DEFAULT_PORT = 9999

USER_INPUT_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST, default=DEFAULT_HOST): cv.string,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.positive_int,
})

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nikobus config flow."""

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return await self._create_entry(user_input)

        return self.async_show_form(step_id="user", data_schema=USER_INPUT_SCHEMA, errors={})

    async def async_create_entry(self, title: str, data: dict) -> dict:
        """Create an entry, update if exists."""
        existing_entry = next((entry for entry in self._async_current_entries()
                            if entry.data[CONF_HOST] == data[CONF_HOST]
                            and entry.data[CONF_PORT] == data[CONF_PORT]), None)

        if existing_entry:
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            _LOGGER.info("Existing Nikobus bridge entry updated")
            return self.async_abort(reason="reauth_successful")

        return super().async_create_entry(title=title, data=data)

    async def _create_entry(self, data: dict[str, Any]):
        return await self.async_create_entry(title="Nikobus Bridge", data=data)
