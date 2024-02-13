"""Config Flow."""
from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
import homeassistant.helpers.config_validation as cv

from simple_socket.tcp_client import SimpleTCPClient

from .const import DOMAIN
from .nikobus import Nikobus, UnauthorizedException

STEP_USER_CONNECTIVITY = vol.Schema(
    {vol.Required(CONF_HOST): cv.string, vol.Required(CONF_PORT): cv.port}
)

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nikobus config flow."""

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None):
        """Handle a flow initialized by the user."""
        errors = {}
        if user_input is not None:
            try:
                TcpSocket = await Nikobus.create(SimpleTCPClient, user_input[CONF_HOST], user_input[CONF_PORT])
                self.data = user_input
                self.data['bridge'] = TcpSocket.Connect()
                return self.async_create_entry(title="Nikobus Bridge", data=self.data)
            except UnauthorizedException:
                errors["base"] = "auth_error"
        return self.async_show_form(step_id="user", data_schema=STEP_USER_CONNECTIVITY, errors=errors)

    async def async_create_entry(self, title: str, data: dict) -> dict:
        """Create an entry."""
        existing_entry = self.hass.config_entries.async_entries(DOMAIN)
        if existing_entry:
            self.hass.config_entries.async_update_entry(existing_entry[0], data=data)
            await self.hass.config_entries.async_reload(existing_entry[0].entry_id)
            return self.async_abort(reason="reauth_successful")
        return super().async_create_entry(title=title, data=data)
