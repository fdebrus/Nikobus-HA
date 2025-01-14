"""Diagnostics support for the Nikobus integration (single-instance)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import (
    DeviceEntry,
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
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> dict[str, Any]:
    """
    Return diagnostics for the single Nikobus config entry.

    Includes:
      - Config entry data/options,
      - All device entries in the registry that match Nikobus.
    """
    _LOGGER.debug(
        "Generating diagnostics for single-instance Nikobus entry: %s",
        config_entry.entry_id
    )

    # Retrieve the device registry
    device_registry = async_get_device_registry(hass)
    if not device_registry:
        _LOGGER.warning("Device registry unavailable.")
        return {
            "error": "Device registry unavailable",
            "entry_id": config_entry.entry_id,
        }

    # Gather ALL Nikobus devices (single-instance but possibly multiple modules)
    nikobus_devices = []
    for device in device_registry.devices.values():
        # If any identifier matches "nikobus"
        if any(ident for ident in device.identifiers if DOMAIN in ident):
            nikobus_devices.append(device)

    _LOGGER.debug(
        "Found %d Nikobus devices in the registry for entry %s",
        len(nikobus_devices),
        config_entry.entry_id
    )

    # Build device info for each found device
    devices_info = []
    for dev in nikobus_devices:
        devices_info.append(
            {
                "id": dev.id,
                "name": dev.name,
                "model": dev.model,
                "manufacturer": dev.manufacturer,
                "sw_version": dev.sw_version,
            }
        )

    # Build config entry data
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
        # Instead of a single device, we now return a list of all devices
        "devices_info": devices_info,
    }

    _LOGGER.debug(
        "Diagnostics generated (single-instance) for entry %s: %s",
        config_entry.entry_id,
        diagnostics_data
    )
    return diagnostics_data
