"""Nikobus config flow"""

import voluptuous as vol
import ipaddress
import re
import os
import socket

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import logging
from typing import Any, Dict

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_validate_input(hass, user_input):
    """Validate the connection string asynchronously."""
    connection_string = user_input[CONF_CONNECTION_STRING]

    try:
        # Validate IP connection
        ip, port = connection_string.split(":")
        ipaddress.ip_address(ip)
        port = int(port)

        if port < 1 or port > 65535:
            raise ValueError("invalid_connection")

        # Test IP connection asynchronously
        def test_connection():
            with socket.create_connection((ip, port), timeout=5):
                pass

        await hass.async_add_executor_job(test_connection)
        return {"title": f"Nikobus ({connection_string})"}

    except (ValueError, socket.error):
        # Validate Serial connection
        if re.match(r"^(/dev/tty(USB|S)\d+|/dev/serial/by-id/.+)$", connection_string):
            if os.path.exists(connection_string) and os.access(
                connection_string, os.R_OK | os.W_OK
            ):
                return {"title": f"Nikobus ({connection_string})"}
            else:
                raise ValueError("device_not_found_or_no_access")

    raise ValueError("invalid_connection")


class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Nikobus integration."""

    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """Handle user setup, enforcing unique instance."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors = {}

        if user_input is not None:
            try:
                info = await async_validate_input(self.hass, user_input)
                return self.async_create_entry(title=info["title"], data=user_input)
            except ValueError as err:
                errors["base"] = str(err)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_STRING): str,
                    vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, user_input=None):
        """Handle import from YAML configuration."""
        existing_entry = await self.async_set_unique_id(DOMAIN)
        if existing_entry:
            return self.async_abort(reason="already_configured")

        return await self.async_step_user(user_input)

    async def async_step_reauth(self, user_input=None):
        """Handle reauthentication request."""
        errors = {}

        if user_input is not None:
            try:
                await async_validate_input(self.hass, user_input)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=user_input
                )
                return self.async_create_entry(title="Reauthenticated", data={})
            except ValueError as err:
                errors["base"] = str(err)

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({vol.Required(CONF_CONNECTION_STRING): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for the Nikobus integration."""

    def __init__(self, config_entry):
        """Initialize the options flow handler."""
        super().__init__(config_entry)

    async def async_step_init(self, user_input=None):
        """Handle the initial step of the options flow."""
        return await self.async_step_config(user_input)

    async def async_step_config(self, user_input=None):
        """Handle the configuration step in options flow."""
        errors = {}

        # Retrieve current settings from config entry
        data = self.config_entry.data
        options = self.options

        # Retrieve current values or defaults
        connection_string = options.get(CONF_CONNECTION_STRING, data.get(CONF_CONNECTION_STRING, ""))
        has_feedback_module = options.get(CONF_HAS_FEEDBACK_MODULE, data.get(CONF_HAS_FEEDBACK_MODULE, False))
        refresh_interval = options.get(CONF_REFRESH_INTERVAL, data.get(CONF_REFRESH_INTERVAL, 120))

        # Schema for options form
        options_schema = vol.Schema(
            {
                vol.Required(CONF_CONNECTION_STRING, default=connection_string): str,
                vol.Optional(CONF_REFRESH_INTERVAL, default=refresh_interval): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
                vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=has_feedback_module): bool,
            }
        )

        if user_input is not None:
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            has_feedback_module = user_input.get(CONF_HAS_FEEDBACK_MODULE, False)
            refresh_interval = user_input.get(CONF_REFRESH_INTERVAL, 120)

            try:
                await async_validate_input(self.hass, user_input)

                return self.async_create_entry(
                    title="Reconfigured Successfully",
                    data={
                        CONF_CONNECTION_STRING: connection_string,
                        CONF_HAS_FEEDBACK_MODULE: has_feedback_module,
                        CONF_REFRESH_INTERVAL: refresh_interval,
                    },
                )
            except ValueError as err:
                errors["base"] = str(err)

        return self.async_show_form(
            step_id="config",
            data_schema=options_schema,
            errors=errors,
            description_placeholders=None,
            last_step=True,
        )