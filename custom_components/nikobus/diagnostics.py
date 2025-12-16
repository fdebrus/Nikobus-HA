"""Diagnostics support for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
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

TO_REDACT = {CONF_CONNECTION_STRING}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Nikobus config entry."""
    _LOGGER.debug(
        "Generating diagnostics for Nikobus entry: %s",
        config_entry.entry_id,
    )

    device_registry: DeviceRegistry = async_get_device_registry(hass)

    # Gather all Nikobus devices that belong to this config entry
    nikobus_devices: list[DeviceEntry] = [
        device
        for device in device_registry.devices.values()
        if config_entry.entry_id in device.config_entries
        and any(
            identifier[0] == DOMAIN
            for identifier in device.identifiers
        )
    ]

    _LOGGER.debug(
        "Found %d Nikobus devices in the registry for entry %s",
        len(nikobus_devices),
        config_entry.entry_id,
    )

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

    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": config_entry.entry_id,
            "title": config_entry.title,
            "version": config_entry.version,
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
        "devices_info": devices_info,
    }

    redacted = async_redact_data(diagnostics_data, TO_REDACT)

    _LOGGER.debug(
        "Diagnostics generated for entry %s (devices: %d)",
        config_entry.entry_id,
        len(devices_info),
    )
    return redacted
