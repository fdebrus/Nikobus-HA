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
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    BooleanSelector,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from nikobus_connect import NikobusConnect, NikobusConnectionError
from nikobus_connect.nkb import NkbData, parse_nkb

from .const import (
    CONF_CLOSE_TIME,
    CONF_CONNECTION,
    CONF_FEEDBACK_MODULE,
    CONF_LIGHT_OUTPUTS,
    CONF_OPEN_TIME,
    CONF_OUTPUT,
    CONF_SHUTTERS,
    CONF_USE_AS,
    DOMAIN,
    ROLLER_MODULE,
    SWITCH_MODULE,
    USE_AS,
    USE_AS_COVER,
)
from .inventory import Inventory

_LOGGER = logging.getLogger(__name__)

CONF_PROJECT_FILE = "project_file"
CONF_ROOMS_AS_AREAS = "rooms_as_areas"


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


def _output_options(inventory: Inventory, module_type: str) -> list[SelectOptionDict]:
    """Every output of one kind of module, labelled the way its entity is named."""
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
        if module.module_type == module_type
        for channel in range(1, module.channels + 1)
    ]


_SECONDS = NumberSelector(
    NumberSelectorConfig(min=1, max=600, step=0.5, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
)


class NikobusOptionsFlow(OptionsFlowWithReload):
    """A menu: the gateway, the relays that are lights, a shutter, the project file.

    Every step saves the options it changes and leaves the others as they
    are. Home Assistant reloads the entry when an option changed.
    """

    _shutter: str

    def _inventory(self) -> Inventory:
        inventory: Inventory = self.config_entry.runtime_data.inventory
        return inventory

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer what this installation has."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        inventory = self._inventory()
        steps = ["gateway"]
        if _output_options(inventory, SWITCH_MODULE):
            steps.append("lights")
        if _output_options(inventory, ROLLER_MODULE):
            steps.append("shutter")
        steps.append("names")
        return self.async_show_menu(step_id="init", menu_options=steps)

    async def async_step_gateway(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Whether a Feedback Module reads the modules too."""
        entry = self.config_entry
        if user_input is not None:
            return self._save(**{CONF_FEEDBACK_MODULE: user_input[CONF_FEEDBACK_MODULE]})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_FEEDBACK_MODULE,
                    default=entry.options.get(CONF_FEEDBACK_MODULE, entry.data[CONF_FEEDBACK_MODULE]),
                ): BooleanSelector()
            }
        )
        return self.async_show_form(step_id="gateway", data_schema=schema)

    async def async_step_lights(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which relays switch a lamp."""
        if user_input is not None:
            return self._save(**{CONF_LIGHT_OUTPUTS: sorted(user_input.get(CONF_LIGHT_OUTPUTS, []))})
        relays = _output_options(self._inventory(), SWITCH_MODULE)
        known = {option["value"] for option in relays}
        current = [o for o in self.config_entry.options.get(CONF_LIGHT_OUTPUTS, []) if o in known]
        schema = vol.Schema(
            {
                vol.Optional(CONF_LIGHT_OUTPUTS, default=current): SelectSelector(
                    SelectSelectorConfig(options=relays, multiple=True, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="lights", data_schema=schema)

    async def async_step_shutter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the roller output to set up."""
        if user_input is not None:
            self._shutter = user_input[CONF_OUTPUT]
            return await self.async_step_shutter_settings()
        schema = vol.Schema(
            {
                vol.Required(CONF_OUTPUT): SelectSelector(
                    SelectSelectorConfig(
                        options=_output_options(self._inventory(), ROLLER_MODULE),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="shutter", data_schema=schema)

    async def async_step_shutter_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """What the output drives, and how long the shutter takes each way.

        A time left empty is the one the module's own links hold.
        """
        shutters: dict[str, dict[str, Any]] = dict(self.config_entry.options.get(CONF_SHUTTERS, {}))
        if user_input is not None:
            settings = {key: value for key, value in user_input.items() if value not in (None, "")}
            if settings == {CONF_USE_AS: USE_AS_COVER}:
                shutters.pop(self._shutter, None)
            else:
                shutters[self._shutter] = settings
            return self._save(**{CONF_SHUTTERS: shutters})
        current = shutters.get(self._shutter, {})
        address, _, channel = self._shutter.partition(":")
        inventory = self._inventory()
        schema = vol.Schema(
            {
                vol.Required(CONF_USE_AS, default=current.get(CONF_USE_AS, USE_AS_COVER)): SelectSelector(
                    SelectSelectorConfig(
                        options=list(USE_AS), translation_key=CONF_USE_AS, mode=SelectSelectorMode.LIST
                    )
                ),
                vol.Optional(
                    CONF_OPEN_TIME, description={"suggested_value": current.get(CONF_OPEN_TIME)}
                ): _SECONDS,
                vol.Optional(
                    CONF_CLOSE_TIME, description={"suggested_value": current.get(CONF_CLOSE_TIME)}
                ): _SECONDS,
            }
        )
        from_links = {
            direction: inventory.run_time(address, int(channel), direction) for direction in ("up", "down")
        }
        return self.async_show_form(
            step_id="shutter_settings",
            data_schema=schema,
            description_placeholders={
                "open": f"{from_links['up']:g} s" if from_links["up"] else "-",
                "close": f"{from_links['down']:g} s" if from_links["down"] else "-",
            },
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Take the names of a project file, and its rooms as areas."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                project = await self.hass.async_add_executor_job(
                    _read_names, self.hass, user_input[CONF_PROJECT_FILE]
                )
            except (ValueError, FileNotFoundError):
                errors["base"] = "invalid_project_file"
            else:
                inventory = self._inventory()
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
                    "areas": user_input[CONF_ROOMS_AS_AREAS],
                }
                await inventory.async_save()
                if user_input[CONF_ROOMS_AS_AREAS]:
                    self._apply_rooms(inventory)
                # Names change no option, so the entry is reloaded from here.
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self._save()
        schema = vol.Schema(
            {
                vol.Required(CONF_PROJECT_FILE): FileSelector(FileSelectorConfig(accept=".nkb")),
                vol.Required(CONF_ROOMS_AS_AREAS, default=True): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="names", data_schema=schema, errors=errors)

    def _apply_rooms(self, inventory: Inventory) -> None:
        """Put the devices that have no area in the room the project gives them."""
        areas = ar.async_get(self.hass)
        devices = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(devices, self.config_entry.entry_id):
            if device.area_id is not None:
                continue
            rooms = [
                room
                for domain, address in device.identifiers
                if domain == DOMAIN and (room := inventory.device_area(address))
            ]
            if rooms:
                devices.async_update_device(device.id, area_id=areas.async_get_or_create(rooms[0]).id)
