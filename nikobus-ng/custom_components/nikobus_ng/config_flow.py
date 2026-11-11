"""Setting up the connection, and importing names from a project file."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    FileSelector,
    FileSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from nikobus_connect import NikobusConnect, NikobusConnectionError
from nikobus_connect.nkb import NkbData, parse_nkb

from .const import (
    CONF_CONNECTION,
    CONF_FEEDBACK_MODULE,
    CONF_LIGHT_OUTPUTS,
    DOMAIN,
    SWITCH_MODULE,
)
from .inventory import Inventory

_LOGGER = logging.getLogger(__name__)

CONF_PROJECT_FILE = "project_file"


def _connection_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CONNECTION, default=defaults.get(CONF_CONNECTION, vol.UNDEFINED)): TextSelector(),
            vol.Required(
                CONF_FEEDBACK_MODULE, default=defaults.get(CONF_FEEDBACK_MODULE, False)
            ): BooleanSelector(),
        }
    )


async def _async_test(connection: str) -> str | None:
    """Open the connection and close it again; the error key when it fails."""
    gateway = NikobusConnect(connection)
    try:
        await gateway.connect()
    except NikobusConnectionError:
        return "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error while testing the Nikobus connection")
        return "unknown"
    finally:
        await gateway.disconnect()
    return None


class NikobusConfigFlow(ConfigFlow, domain=DOMAIN):
    """One step: where the gateway is."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the serial port or the bridge address, and try it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            connection = user_input[CONF_CONNECTION].strip()
            await self.async_set_unique_id(connection.lower())
            self._abort_if_unique_id_configured()
            if (error := await _async_test(connection)) is None:
                return self.async_create_entry(
                    title="Nikobus",
                    data={
                        CONF_CONNECTION: connection,
                        CONF_FEEDBACK_MODULE: user_input[CONF_FEEDBACK_MODULE],
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=_connection_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the connection. A connection left as it is is not tried again."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            connection = user_input[CONF_CONNECTION].strip()
            unchanged = connection.lower() == entry.data[CONF_CONNECTION].lower()
            error = None if unchanged else await _async_test(connection)
            if error is None:
                await self.async_set_unique_id(connection.lower())
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=connection.lower(),
                    data={
                        CONF_CONNECTION: connection,
                        CONF_FEEDBACK_MODULE: user_input[CONF_FEEDBACK_MODULE],
                    },
                    options={
                        key: value for key, value in entry.options.items() if key != CONF_FEEDBACK_MODULE
                    },
                )
            errors["base"] = error
        defaults = user_input or {
            CONF_CONNECTION: entry.data[CONF_CONNECTION],
            CONF_FEEDBACK_MODULE: entry.options.get(
                CONF_FEEDBACK_MODULE, entry.data[CONF_FEEDBACK_MODULE]
            ),
        }
        return self.async_show_form(
            step_id="reconfigure", data_schema=_connection_schema(defaults), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NikobusOptionsFlow:
        """The options: the Feedback Module, and names from a project file."""
        return NikobusOptionsFlow()


def _read_names(hass: HomeAssistant, upload_id: str) -> NkbData:
    with process_uploaded_file(hass, upload_id) as path:
        return parse_nkb(Path(path))


def _relay_options(inventory: Inventory) -> list[SelectOptionDict]:
    """Every relay output, labelled the way its entity is named."""
    return [
        SelectOptionDict(
            value=f"{module.address}:{channel}",
            label=" ".join(
                (
                    inventory.device_name(module.address) or f"{module.description} {module.address}",
                    inventory.channel_name(module.address, channel) or f"{module.address}:{channel}",
                )
            ),
        )
        for module in inventory.output_modules()
        if module.module_type == SWITCH_MODULE
        for channel in range(1, module.channels + 1)
    ]


class NikobusOptionsFlow(OptionsFlowWithReload):
    """One form: the Feedback Module, the relays that are lights, a ``.nkb`` upload.

    Home Assistant reloads the entry when an option changed.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Save the option; when a project file is given, take its names."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        inventory: Inventory = entry.runtime_data.inventory
        relays = _relay_options(inventory)
        errors: dict[str, str] = {}
        if user_input is not None:
            if upload_id := user_input.get(CONF_PROJECT_FILE):
                try:
                    project = await self.hass.async_add_executor_job(_read_names, self.hass, upload_id)
                except (ValueError, FileNotFoundError):
                    errors["base"] = "invalid_project_file"
                else:
                    inventory.names = {
                        "devices": {
                            address.upper(): {"name": name, "room": room}
                            for address, (name, room) in project.addresses.items()
                        },
                        "channels": {
                            f"{module.upper()}:{channel}": name
                            for (module, channel), name in project.outputs.items()
                        },
                        "scenes": [
                            {"name": scene.name, "members": sorted(map(list, scene.members))}
                            for scene in project.scenes
                        ],
                    }
                    await inventory.async_save()
            if not errors:
                options = {
                    CONF_FEEDBACK_MODULE: user_input[CONF_FEEDBACK_MODULE],
                    CONF_LIGHT_OUTPUTS: sorted(user_input.get(CONF_LIGHT_OUTPUTS, [])),
                }
                # Names alone change no option, so they need a reload of their own.
                if options == entry.options and user_input.get(CONF_PROJECT_FILE):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data=options)
        fields: dict[vol.Marker, Any] = {
            vol.Required(
                CONF_FEEDBACK_MODULE,
                default=entry.options.get(CONF_FEEDBACK_MODULE, entry.data[CONF_FEEDBACK_MODULE]),
            ): BooleanSelector(),
        }
        if relays:
            known = {option["value"] for option in relays}
            fields[
                vol.Optional(
                    CONF_LIGHT_OUTPUTS,
                    default=[output for output in entry.options.get(CONF_LIGHT_OUTPUTS, []) if output in known],
                )
            ] = SelectSelector(
                SelectSelectorConfig(options=relays, multiple=True, mode=SelectSelectorMode.DROPDOWN)
            )
        fields[vol.Optional(CONF_PROJECT_FILE)] = FileSelector(FileSelectorConfig(accept=".nkb"))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)
