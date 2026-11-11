"""The actions: what a button on the gateway cannot take a parameter for."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .hub import NikobusHub

ATTR_ADDRESS = "address"
ATTR_MODULES = "modules"

SERVICE_PRESS_KEY = "press_key"
SERVICE_READ_MODULE = "read_module"
SERVICE_VERIFY = "verify_programming"
SERVICE_BACKUP = "backup_programming"
SERVICE_DISCOVER = "discover"

_KEY_ADDRESS = vol.All(cv.string, vol.Upper, vol.Match(r"^[0-9A-F]{6}$"))
_MODULE_ADDRESS = vol.All(cv.string, vol.Upper, vol.Match(r"^[0-9A-F]{4}$"))
_MODULES = vol.Schema({vol.Optional(ATTR_MODULES): vol.All(cv.ensure_list, [_MODULE_ADDRESS])})


def _hub(hass: HomeAssistant) -> NikobusHub:
    """The hub of the one entry the integration allows."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            hub: NikobusHub = entry.runtime_data
            return hub
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions once, whatever the state of the entry."""

    async def press_key(call: ServiceCall) -> None:
        await _hub(hass).async_press(call.data[ATTR_ADDRESS])

    async def read_module(call: ServiceCall) -> None:
        hub = _hub(hass)
        wanted = call.data.get(ATTR_ADDRESS)
        groups = [key for key in hub.inventory.output_groups() if wanted in (None, key[0])]
        if not groups:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_module",
                translation_placeholders={"address": str(wanted)},
            )
        for address, group in groups:
            await hub.async_read_group(address, group)

    async def verify(call: ServiceCall) -> ServiceResponse:
        report: dict[str, Any] = await _hub(hass).maintenance.async_verify(call.data.get(ATTR_MODULES))
        return report

    async def backup(call: ServiceCall) -> ServiceResponse:
        report: dict[str, Any] = await _hub(hass).maintenance.async_backup(call.data.get(ATTR_MODULES))
        return report

    async def discover(call: ServiceCall) -> None:
        hub = _hub(hass)
        module = call.data.get(ATTR_ADDRESS)
        if module is not None and module not in hub.inventory.addresses():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_module",
                translation_placeholders={"address": module},
            )
        hub.discovery.start(module)

    hass.services.async_register(
        DOMAIN, SERVICE_PRESS_KEY, press_key, vol.Schema({vol.Required(ATTR_ADDRESS): _KEY_ADDRESS})
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_DISCOVER, discover, vol.Schema({vol.Optional(ATTR_ADDRESS): _MODULE_ADDRESS})
    )
    hass.services.async_register(
        DOMAIN, SERVICE_READ_MODULE, read_module, vol.Schema({vol.Optional(ATTR_ADDRESS): _MODULE_ADDRESS})
    )
    async_register_admin_service(hass, DOMAIN, SERVICE_VERIFY, verify, _MODULES, SupportsResponse.OPTIONAL)
    async_register_admin_service(hass, DOMAIN, SERVICE_BACKUP, backup, _MODULES, SupportsResponse.OPTIONAL)
