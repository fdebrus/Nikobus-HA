import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT): cv.port,
})

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Nikobus config flow."""

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        _LOGGER.debug("Entered async_step_user")

        if user_input is not None:
            _LOGGER.debug("User input received: %s", user_input)
            return self.async_create_entry(title="NikobusBridge", data=user_input)

        _LOGGER.debug("Showing form to the user")
        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
        )
