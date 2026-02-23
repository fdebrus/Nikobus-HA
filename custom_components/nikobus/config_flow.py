"""Config flow for Nikobus integration - Platinum Edition."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

# Import from the new PyPI library
from nikobusconnect import NikobusConnect

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
)

_LOGGER = logging.getLogger(__name__)

def _get_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Standardized schema for both setup and reconfiguration."""
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_CONNECTION_STRING, default=defaults.get(CONF_CONNECTION_STRING, "")): str,
        vol.Optional(CONF_REFRESH_INTERVAL, default=defaults.get(CONF_REFRESH_INTERVAL, 120)): vol.All(
            cv.positive_int, vol.Range(min=60, max=3600)
        ),
        vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=defaults.get(CONF_HAS_FEEDBACK_MODULE, False)): bool,
        vol.Optional(CONF_PRIOR_GEN3, default=defaults.get(CONF_PRIOR_GEN3, False)): bool,
    })

async def validate_nikobus_connection(hass: HomeAssistant, connection_string: str) -> None:
    """
    Perform validation of the Nikobus hardware.
    Checks connection and performs the mandatory handshake.
    """
    connection = NikobusConnect(connection_string)
    try:
        # connect() in 1.0.0 handles the full handshake logic
        await connection.connect()
    except Exception as err:
        _LOGGER.error("Nikobus connection failed during validation: %s", err)
        raise ValueError("cannot_connect") from err
    finally:
        await connection.disconnect()

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nikobus."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Prevent duplicate entries
            await self.async_set_unique_id(user_input[CONF_CONNECTION_STRING].lower())
            self._abort_if_unique_id_configured()

            try:
                await validate_nikobus_connection(self.hass, user_input[CONF_CONNECTION_STRING])
                return self.async_create_entry(
                    title=f"Nikobus ({user_input[CONF_CONNECTION_STRING]})",
                    data=user_input
                )
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during Nikobus validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=_get_schema(),
            errors=errors
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Handle reconfiguration of an existing Nikobus entry."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_nikobus_connection(self.hass, user_input[CONF_CONNECTION_STRING])
                return self.async_update_reload_and_abort(entry, data=user_input)
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_get_schema(dict(entry.data)),
            errors=errors
        )