"""Diagnostics support for the Nikobus integration (single-instance)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the single Nikobus config entry."""
    _LOGGER.debug(
        "Generating diagnostics for single-instance Nikobus entry: %s",
        config_entry.entry_id,
    )


    coordinator = config_entry.runtime_data

    device_registry = async_get_device_registry(hass)
    if not device_registry:
        _LOGGER.warning("Device registry unavailable.")
        return {
            "error": "Device registry unavailable",
            "entry_id": config_entry.entry_id,
        }

    nikobus_devices = [
        dev
        for dev in device_registry.devices.values()
        if any(ident for ident in dev.identifiers if DOMAIN in ident)
    ]

    _LOGGER.debug(
        "Found %d Nikobus devices in the registry for entry %s",
        len(nikobus_devices),
        config_entry.entry_id,
    )

    devices_info = [
        {
            "id": dev.id,
            "name": dev.name,
            "model": dev.model,
            "manufacturer": dev.manufacturer,
        }
        for dev in nikobus_devices
    ]

    diagnostics_data = {
        "config_entry": {
            "connection_string": config_entry.options.get(
                CONF_CONNECTION_STRING,
                config_entry.data.get(CONF_CONNECTION_STRING, "Unknown"),
            ),
            "has_feedback_module": config_entry.options.get(
                CONF_HAS_FEEDBACK_MODULE,
                config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, "Unknown"),
            ),
            "refresh_interval": config_entry.options.get(
                CONF_REFRESH_INTERVAL,
                config_entry.data.get(CONF_REFRESH_INTERVAL, "Unknown"),
            ),
        },

        "runtime_data": coordinator.get_diagnostics_data() if coordinator else "Unavailable",
        "devices_info": devices_info,
    }

    _LOGGER.debug(
        "Diagnostics generated (single-instance) for entry %s: %s",
        config_entry.entry_id,
        diagnostics_data,
    )
    return diagnostics_data
