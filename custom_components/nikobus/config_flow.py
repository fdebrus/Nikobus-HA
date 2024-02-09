"""Config flow for Nikobus integration."""
from __future__ import annotations

import logging
from typing import Any

# from nikobus import real_time_api
# from nikobus.discovery import DiscoveryError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_IP_ADDRESS, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9999

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IP_ADDRESS): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    }
)


async def validate_api(data) -> str:
    """Validate the credentials."""

    api = await real_time_api(
        data[CONF_IP_ADDRESS], data[CONF_PORT]
    )
    response = await api.get_data()
    return response.serial_number


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nikobus."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, Any] = {}
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
            )

        try:
            serial_number = await validate_api(user_input)
        except (ConnectionError, DiscoveryError):
            errors["base"] = "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            await self.async_set_unique_id(serial_number)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=serial_number, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
