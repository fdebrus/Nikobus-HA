"""Diagnostics support for the Nikobus integration."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceEntry,
    DeviceRegistry,
    async_get as async_get_device_registry,
)

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    _LOGGER.debug(
        "Generating diagnostics for single-instance Nikobus entry: %s",
        config_entry.entry_id,
    )

    # Retrieve the device registry
    device_registry: DeviceRegistry = async_get_device_registry(hass)

    # Gather all Nikobus devices
    nikobus_devices: list[DeviceEntry] = [
        device
        for device in device_registry.devices.values()
        if any(ident for ident in device.identifiers if DOMAIN in ident)
    ]

    _LOGGER.debug(
        "Found %d Nikobus devices in the registry for entry %s",
        len(nikobus_devices),
        config_entry.entry_id,
    )

    # Build device info list
    devices_info: list[dict[str, Any]] = [
        {
            "id": dev.id,
            "name": dev.name,
            "model": dev.model,
            "manufacturer": dev.manufacturer,
            "sw_version": dev.sw_version,
        }
        for dev in nikobus_devices
    ]

    # Build config entry diagnostics
    diagnostics_data: dict[str, Any] = {
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
        "devices_info": devices_info,  # Return a list of all Nikobus devices
    }

    _LOGGER.debug(
        "Diagnostics generated (single-instance) for entry %s: %s",
        config_entry.entry_id,
        diagnostics_data,
    )
    return diagnostics_data
