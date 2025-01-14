"""Config flow for Nikobus integration (Single Instance, Reconfigure Only)."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from typing import Any, Dict, Mapping

import voluptuous as vol

from homeassistant import config_entries, core
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_validate_input(
    hass: core.HomeAssistant,
    user_input: Dict[str, Any]
) -> Dict[str, str]:
    """
    Validate the connection string asynchronously.

    Checks both IP:port format and serial device paths. Returns a dict with:
      - {"title": "Some Title"} if validation succeeds,
      - {"error": "error_code"} if validation fails.
    """
    connection_string = user_input[CONF_CONNECTION_STRING]

    # If we detect a colon, try IP:port
    if ":" in connection_string:
        try:
            ip_str, port_str = connection_string.split(":")
            ipaddress.ip_address(ip_str)
            port = int(port_str)
            if not (1 <= port <= 65535):
                return {"error": "invalid_port"}

            def test_connection() -> None:
                with socket.create_connection((ip_str, port), timeout=5):
                    pass

            await hass.async_add_executor_job(test_connection)
            return {"title": f"Nikobus ({connection_string})"}
        except (ValueError, socket.error):
            # Fall back to checking if it might be a valid serial device
            pass

    # Check if it matches a known serial path pattern
    serial_regex = r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$"
    if re.match(serial_regex, connection_string):
        if os.path.exists(connection_string) and os.access(
            connection_string, os.R_OK | os.W_OK
        ):
            return {"title": f"Nikobus ({connection_string})"}
        return {"error": "device_not_found_or_no_access"}

    return {"error": "invalid_connection"}


class NikobusConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    Handle a config flow for the Nikobus integration.

    This flow enforces:
      - Single-instance setup (only one config entry),
      - Reconfigure flow (no OptionsFlow),
      - Optional import from YAML.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: Dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle initial user setup.

        Abort if the integration is already configured.
        """
        if any(entry.domain == DOMAIN for entry in self._async_current_entries()):
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: Dict[str, str] = {}

        if user_input is not None:
            validation = await async_validate_input(self.hass, user_input)
            if "error" in validation:
                errors["base"] = validation["error"]
            else:
                return self.async_create_entry(
                    title=validation["title"],
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_CONNECTION_STRING): str,
                vol.Optional(CONF_REFRESH_INTERVAL, default=120): vol.All(
                    cv.positive_int, vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_import(
        self, import_config: Dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle import from YAML configuration.

        If already configured, abort; otherwise use the same logic as 'user' step.
        """
        if any(entry.domain == DOMAIN for entry in self._async_current_entries()):
            return self.async_abort(reason="already_configured")

        return await self.async_step_user(user_input=import_config)

    async def async_step_reconfigure(
        self, user_input: Dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle reconfiguration of the Nikobus integration.

        This step is shown as "Reconfigure" in newer Home Assistant versions.
        """
        reconfigure_entry = self._get_existing_entry()
        if not reconfigure_entry:
            return self.async_abort(reason="no_existing_entry")

        errors: Dict[str, str] = {}

        if user_input is not None:
            # Validate the new user input
            validation = await async_validate_input(self.hass, user_input)
            if "error" in validation:
                errors["base"] = validation["error"]
            else:
                # If valid, we update and reload
                return await self._async_update_reload_and_abort(
                    entry=reconfigure_entry,
                    new_data=user_input,
                )

        # Show the form with defaults from the existing config entry
        data = dict(reconfigure_entry.data)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONNECTION_STRING,
                    default=data.get(CONF_CONNECTION_STRING, ""),
                ): str,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=data.get(CONF_REFRESH_INTERVAL, 120),
                ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
                vol.Optional(
                    CONF_HAS_FEEDBACK_MODULE,
                    default=data.get(CONF_HAS_FEEDBACK_MODULE, False),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    def _get_existing_entry(self) -> config_entries.ConfigEntry | None:
        """Return the existing single config entry or None if not found."""
        for entry in self._async_current_entries():
            if entry.domain == DOMAIN:
                return entry
        return None

    async def _async_update_reload_and_abort(
        self,
        entry: config_entries.ConfigEntry,
        new_data: Dict[str, Any],
    ) -> ConfigFlowResult:
        """Update the config entry, reload, and abort the flow."""
        # Merge old data with new data
        old_data = dict(entry.data)
        updated_data = {**old_data, **new_data}

        # Update the existing config entry
        self.hass.config_entries.async_update_entry(entry, data=updated_data)

        # Reload the entry so changes take effect
        await self.hass.config_entries.async_reload(entry.entry_id)

        return self.async_abort(reason="reconfigure_successful")
