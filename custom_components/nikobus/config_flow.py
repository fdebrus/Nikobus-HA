"""Config flow for Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .exceptions import NikobusConnectionError
from nikobusconnect import NikobusConnect

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reusable schema fragments
# ---------------------------------------------------------------------------

def _hardware_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(
            CONF_HAS_FEEDBACK_MODULE,
            default=defaults.get(CONF_HAS_FEEDBACK_MODULE, False),
        ): bool,
        vol.Optional(
            CONF_PRIOR_GEN3,
            default=defaults.get(CONF_PRIOR_GEN3, False),
        ): bool,
    })


def _polling_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(
            CONF_REFRESH_INTERVAL,
            default=defaults.get(CONF_REFRESH_INTERVAL, 120),
        ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
    })


def _needs_polling(user_input: dict[str, Any]) -> bool:
    """Return True when the selected hardware requires a polling interval."""
    return not (
        user_input.get(CONF_HAS_FEEDBACK_MODULE) or user_input.get(CONF_PRIOR_GEN3)
    )


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

async def _test_connection(hass: HomeAssistant, connection_string: str) -> None:
    """Open a connection, complete the PC-Link handshake, then close it."""
    conn = NikobusConnect(connection_string)
    try:
        await conn.connect()
    except NikobusConnectionError as err:
        raise ValueError("cannot_connect") from err
    finally:
        await conn.disconnect()


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow: connection → hardware type → (optional) polling."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    # --- Options flow hook --------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NikobusOptionsFlow:
        return NikobusOptionsFlow(config_entry)

    # --- Step 1: connection string ------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect and validate the connection string."""
        errors: dict[str, str] = {}

        if user_input is not None:
            conn_str = user_input[CONF_CONNECTION_STRING].strip()
            await self.async_set_unique_id(conn_str.lower())
            self._abort_if_unique_id_configured()

            try:
                await _test_connection(self.hass, conn_str)
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Nikobus connection test")
                errors["base"] = "unknown"
            else:
                self._data[CONF_CONNECTION_STRING] = conn_str
                return await self.async_step_hardware()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_CONNECTION_STRING,
                    default=self._data.get(CONF_CONNECTION_STRING, ""),
                ): str,
            }),
            errors=errors,
        )

    # --- Step 2: hardware capabilities -------------------------------------

    async def async_step_hardware(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Determine whether a Feedback Module or legacy PC-Link is present."""
        if user_input is not None:
            self._data.update(user_input)
            if _needs_polling(user_input):
                return await self.async_step_polling()
            self._data.setdefault(CONF_REFRESH_INTERVAL, 120)
            return self._finish()

        return self.async_show_form(
            step_id="hardware",
            data_schema=_hardware_schema(self._data),
        )

    # --- Step 3: polling interval (only without feedback module) -----------

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose how often to poll the bus when no Feedback Module is present."""
        if user_input is not None:
            self._data.update(user_input)
            return self._finish()

        return self.async_show_form(
            step_id="polling",
            data_schema=_polling_schema(self._data),
        )

    def _finish(self) -> config_entries.FlowResult:
        return self.async_create_entry(
            title=f"Nikobus ({self._data[CONF_CONNECTION_STRING]})",
            data=self._data,
        )

    # --- Reconfigure (single step, all fields) -----------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Re-test the connection and update all settings in one step."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        defaults = {**entry.data, **entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _test_connection(
                    self.hass, user_input[CONF_CONNECTION_STRING].strip()
                )
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Nikobus reconfigure test")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_CONNECTION_STRING,
                    default=defaults.get(CONF_CONNECTION_STRING, ""),
                ): str,
                vol.Optional(
                    CONF_HAS_FEEDBACK_MODULE,
                    default=defaults.get(CONF_HAS_FEEDBACK_MODULE, False),
                ): bool,
                vol.Optional(
                    CONF_PRIOR_GEN3,
                    default=defaults.get(CONF_PRIOR_GEN3, False),
                ): bool,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=defaults.get(CONF_REFRESH_INTERVAL, 120),
                ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
            }),
            errors=errors,
        )

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> config_entries.FlowResult:
        return await self.async_step_user(import_config)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class NikobusOptionsFlow(config_entries.OptionsFlow):
    """Change hardware/polling settings without re-entering the connection string."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._options: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        """Merge entry data + options so defaults reflect the live settings."""
        return {**self._entry.data, **self._entry.options}

    # --- Step 1: hardware capabilities -------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            self._options.update(user_input)
            if _needs_polling(user_input):
                return await self.async_step_polling()
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="init",
            data_schema=_hardware_schema(self._current()),
        )

    # --- Step 2: polling interval ------------------------------------------

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="polling",
            data_schema=_polling_schema(self._current()),
        )
