"""Diagnostics support for Nikobus integration."""

from __future__ import annotations
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device_registry = async_get_device_registry(hass)
    devices = device_registry.devices

    # Get Nikobus device details
    nikobus_device = None
    for device in devices.values():
        if DOMAIN in device.identifiers:
            nikobus_device = device
            break

    diagnostics_data = {
        "connection_string": config_entry.data.get(CONF_CONNECTION_STRING, "Unknown"),
        "has_feedback_module": config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
        "refresh_interval": config_entry.data.get(CONF_REFRESH_INTERVAL, 120),
        "device_info": {
            "name": nikobus_device.name if nikobus_device else "Unknown",
            "model": nikobus_device.model if nikobus_device else "Unknown",
            "manufacturer": nikobus_device.manufacturer if nikobus_device else "Unknown",
            "sw_version": nikobus_device.sw_version if nikobus_device else "Unknown",
        } if nikobus_device else "Nikobus device not found in registry",
    }

    return diagnostics_data
