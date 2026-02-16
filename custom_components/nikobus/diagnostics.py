"""Diagnostics support for the Nikobus integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceRegistry, async_get as async_get_dev_reg

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .coordinator import NikobusDataCoordinator
from .entity import device_entry_diagnostics

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Nikobus config entry."""
    _LOGGER.debug("Generating diagnostics for Nikobus entry: %s", config_entry.entry_id)

    coordinator: NikobusDataCoordinator = config_entry.runtime_data
    device_registry: DeviceRegistry = async_get_dev_reg(hass)

    # Gather all Nikobus devices tied to this config entry
    nikobus_devices = [
        device
        for device in device_registry.async_entries_for_config_entry(config_entry.entry_id)
        if any(ident for ident in device.identifiers if ident[0] == DOMAIN)
    ]

    # Redact sensitive parts of the connection string (e.g., potential passwords or IPs)
    raw_connection = config_entry.options.get(
        CONF_CONNECTION_STRING,
        config_entry.data.get(CONF_CONNECTION_STRING, "Unknown"),
    )
    
    # Simple redaction: keep the protocol, hide the rest if it looks like a URL
    connection_info = raw_connection
    if "://" in raw_connection:
        proto, _ = raw_connection.split("://", 1)
        connection_info = f"{proto}://[REDACTED]"

    # Capture raw hex states from the coordinator for deep debugging
    raw_module_states = {
        addr: state.hex() for addr, state in coordinator.nikobus_module_states.items()
    }

    diagnostics_data = {
        "config_entry": {
            "connection_type": connection_info,
            "has_feedback_module": config_entry.options.get(
                CONF_HAS_FEEDBACK_MODULE,
                config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
            ),
            "refresh_interval": config_entry.options.get(
                CONF_REFRESH_INTERVAL,
                config_entry.data.get(CONF_REFRESH_INTERVAL, 0),
            ),
        },
        "coordinator": {
            "module_count": len(coordinator.dict_module_data),
            "button_count": len(coordinator.dict_button_data.get("nikobus_button", {})),
            "scene_count": len(coordinator.dict_scene_data.get("scene", [])),
            "raw_hex_states": raw_module_states,
        },
        "devices": [device_entry_diagnostics(dev) for dev in nikobus_devices],
    }

    return diagnostics_data