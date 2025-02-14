"""Config flow for Nikobus integration."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from typing import Any

import voluptuous as vol
from homeassistant import config_entries, core
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_validate_input(
    hass: core.HomeAssistant, user_input: dict[str, Any]
) -> dict[str, str]:
    """Validate the connection string asynchronously."""
    connection_string: str = user_input[CONF_CONNECTION_STRING]

    # IP:Port validation
    if ":" in connection_string:
        try:
            ip_str, port_str = connection_string.split(":")
            ipaddress.ip_address(ip_str)
            port = int(port_str)
            if not (1 <= port <= 65535):
                return {"error": "invalid_port"}

            def test_connection() -> None:
                try:
                    with socket.create_connection((ip_str, port), timeout=5):
                        pass
                except (socket.timeout, ConnectionRefusedError):
                    raise ValueError("connection_unreachable")

            await hass.async_add_executor_job(test_connection)
            return {"title": f"Nikobus ({connection_string})"}

        except ValueError as exc:
            if str(exc) == "connection_unreachable":
                return {"error": "connection_unreachable"}

    # Serial device validation
    serial_regex = r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$"
    if re.match(serial_regex, connection_string):
        if os.path.exists(connection_string) and os.access(
            connection_string, os.R_OK | os.W_OK
        ):
            return {"title": f"Nikobus ({connection_string})"}
        return {"error": "device_not_found_or_no_access"}

    return {"error": "invalid_connection"}


class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Nikobus integration."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle initial configuration of Nikobus."""
        if self._get_existing_entry():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            validation = await async_validate_input(self.hass, user_input)
            if "error" in validation:
                errors["base"] = validation["error"]
            else:
                return self.async_create_entry(
                    title=validation["title"], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_STRING): str,
                    vol.Optional(CONF_REFRESH_INTERVAL, default=120): vol.All(
                        cv.positive_int, vol.Range(min=60, max=3600)
                    ),
                    vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_import(
        self, import_config: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle YAML import of Nikobus configuration."""
        if self._get_existing_entry():
            return self.async_abort(reason="already_configured")

        return await self.async_step_user(user_input=import_config)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle reconfiguration of the integration."""
        existing_entry = self._get_existing_entry()
        if not existing_entry:
            return self.async_abort(reason="no_existing_entry")

        errors: dict[str, str] = {}
        if user_input is not None:
            validation = await async_validate_input(self.hass, user_input)
            if "error" in validation:
                errors["base"] = validation["error"]
            else:
                return self.async_update_reload_and_abort(
                    existing_entry,
                    data={
                        CONF_CONNECTION_STRING: user_input[CONF_CONNECTION_STRING],
                        CONF_HAS_FEEDBACK_MODULE: user_input.get(
                            CONF_HAS_FEEDBACK_MODULE,
                            existing_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
                        ),
                        CONF_REFRESH_INTERVAL: user_input.get(
                            CONF_REFRESH_INTERVAL,
                            existing_entry.data.get(CONF_REFRESH_INTERVAL, 120),
                        ),
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_STRING,
                        default=existing_entry.data.get(CONF_CONNECTION_STRING, ""),
                    ): str,
                    vol.Optional(
                        CONF_REFRESH_INTERVAL,
                        default=existing_entry.data.get(CONF_REFRESH_INTERVAL, 120),
                    ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
                    vol.Optional(
                        CONF_HAS_FEEDBACK_MODULE,
                        default=existing_entry.data.get(
                            CONF_HAS_FEEDBACK_MODULE, False
                        ),
                    ): bool,
                }
            ),
            errors=errors,
        )

    def _get_existing_entry(self) -> config_entries.ConfigEntry | None:
        """Get the existing Nikobus config entry if present."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None
