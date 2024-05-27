import voluptuous as vol
import ipaddress
import re
import os
import socket

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import logging
from typing import Any, Dict

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CONNECTION_STRING = "connection_string"
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE = "has_feedback_module"

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nikobus integration."""
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def _validate_connection_string(self, connection_string) -> bool:
        try:
            # Split connection string into IP and port
            ip, port = connection_string.split(':')
            ipaddress.ip_address(ip)  # Validate IP address
            port = int(port)  # Convert port to integer

            # Check if the port is in the valid range
            if port < 1 or port > 65535:
                return False

            # Try to establish a connection to the IP and port
            with socket.create_connection((ip, port), timeout=5):
                pass
            return True
        except (ValueError, socket.error) as e:
            _LOGGER.error("IP/Port validation error: %s", e)
            if re.match(r'^/dev/tty(USB|S)\d+$', connection_string):
                if os.path.exists(connection_string) and os.access(connection_string, os.R_OK | os.W_OK):
                    return True
                else:
                    return False
        return False

    async def async_step_user(self, user_input=None):
        """Handle a flow initiated by the user."""
        _LOGGER.debug("Starting user step with input: %s", user_input)
        errors = {}
        if user_input is not None:
            connection_string = user_input.get(CONF_CONNECTION_STRING, "")
            has_feedback_module = user_input.get(CONF_HAS_FEEDBACK_MODULE, False)

            try:
                if not self._validate_connection_string(connection_string):
                    if re.match(r'^/dev/tty(USB|S)\d+$', connection_string):
                        errors[CONF_CONNECTION_STRING] = 'device_not_found_or_no_access'
                    else:
                        errors[CONF_CONNECTION_STRING] = 'invalid_connection'
                else:
                    # Store the user input and proceed to the next step
                    self.connection_string = connection_string
                    self.has_feedback_module = has_feedback_module
                    return await self.async_step_options()
            except Exception as e:
                _LOGGER.error("Error validating connection string: %s", e)
                errors["base"] = "unknown_error"

        user_input_schema = vol.Schema({
            vol.Required(CONF_CONNECTION_STRING): str,
            vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=False): bool,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=user_input_schema,
            errors=errors,
            description_placeholders=None,
            last_step=False,
        )

    async def async_step_options(self, user_input=None):
        """Handle the options step of the flow."""
        _LOGGER.debug("Starting options step with input: %s", user_input)
        errors = {}
        if user_input is not None:
            refresh_interval = user_input.get(CONF_REFRESH_INTERVAL, 120)
            data = {
                CONF_CONNECTION_STRING: self.connection_string,
                CONF_HAS_FEEDBACK_MODULE: self.has_feedback_module,
                CONF_REFRESH_INTERVAL: refresh_interval
            }
            return await self._create_entry(data)

        # Default values for the form
        default_refresh_interval = 120

        user_input_schema = vol.Schema({
            vol.Optional(CONF_REFRESH_INTERVAL, default=default_refresh_interval): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
        })

        if self.has_feedback_module:
            return await self._create_entry({
                CONF_CONNECTION_STRING: self.connection_string,
                CONF_HAS_FEEDBACK_MODULE: self.has_feedback_module
            })

        return self.async_show_form(
            step_id="options",
            data_schema=user_input_schema,
            errors=errors,
            description_placeholders=None,
            last_step=True,
        )

    async def _create_entry(self, data: Dict[str, Any]):
        """Create entry for configuration."""
        _LOGGER.debug("Creating entry with data: %s", data)
        title = f"Nikobus PC-Link - {data.get(CONF_CONNECTION_STRING, 'Unknown Connection')}"
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Nikobus integration."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Handle the initial step."""
        return await self.async_step_config(user_input)

    async def async_step_config(self, user_input=None):
        """Handle the configuration step in options flow."""
        errors = {}
        options = self.config_entry.options
        has_feedback_module = options.get(CONF_HAS_FEEDBACK_MODULE, False)
        refresh_interval = options.get(CONF_REFRESH_INTERVAL, 120)

        options_schema = {
            vol.Optional(CONF_HAS_FEEDBACK_MODULE, default=has_feedback_module): bool,
        }

        if user_input is None or not user_input.get(CONF_HAS_FEEDBACK_MODULE):
            options_schema[vol.Optional(CONF_REFRESH_INTERVAL, default=refresh_interval)] = vol.All(
                cv.positive_int, 
                vol.Range(min=60, max=3600)
            )

        if user_input is not None:
            has_feedback_module = user_input.get(CONF_HAS_FEEDBACK_MODULE, False)
            refresh_interval = user_input.get(CONF_REFRESH_INTERVAL, 120)

            data = {
                CONF_HAS_FEEDBACK_MODULE: has_feedback_module,
                CONF_REFRESH_INTERVAL: refresh_interval
            }

            if has_feedback_module:
                data.pop(CONF_REFRESH_INTERVAL, None)

            return self.async_create_entry(title="Options Configured", data=data)

        return self.async_show_form(
            step_id="config",
            data_schema=vol.Schema(options_schema),
            errors=errors,
            description_placeholders=None,
            last_step=True,
        )