"""Diagnostics support for the Nikobus integration - Platinum Edition."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

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

    # 1. Safely retrieve the coordinator
    # In some HA versions or states, runtime_data might be None
    coordinator: NikobusDataCoordinator | None = getattr(config_entry, "runtime_data", None)
    
    # 2. Safely retrieve the device registry
    device_registry = dr.async_get(hass)

    # 3. Gather devices with a fallback for older HA versions
    nikobus_devices = []
    if device_registry:
        # Utilisation de la méthode moderne de HA (2024.11 et +)
        if hasattr(device_registry, "async_get_devices_for_config_entry"):
            devices = device_registry.async_get_devices_for_config_entry(config_entry.entry_id)
        else:
            # Fallback manuel pour les versions plus anciennes ou si la méthode change
            devices = [
                dev for dev in device_registry.devices.values()
                if config_entry.entry_id in dev.config_entries
            ]
        
        nikobus_devices = [
            device for device in devices
            if any(ident for ident in device.identifiers if ident[0] == DOMAIN)
        ]

    # Redact sensitive parts of the connection string
    raw_connection = config_entry.options.get(
        CONF_CONNECTION_STRING,
        config_entry.data.get(CONF_CONNECTION_STRING, "Unknown"),
    )
    
    connection_info = raw_connection
    if "://" in str(raw_connection):
        proto, _ = str(raw_connection).split("://", 1)
        connection_info = f"{proto}://[REDACTED]"

    # Capture raw hex states if coordinator is available
    raw_module_states = {}
    if coordinator and hasattr(coordinator, "nikobus_module_states"):
        raw_module_states = {
            addr: state.hex() for addr, state in coordinator.nikobus_module_states.items()
        }

    # Build the diagnostic report
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
            "state": config_entry.state,
        },
        "coordinator_status": "Online" if coordinator else "Offline",
        "devices": [device_entry_diagnostics(dev) for dev in nikobus_devices],
    }

    # Add detailed coordinator data if it's running
    if coordinator:
        diagnostics_data["coordinator_data"] = {
            "module_count": len(getattr(coordinator, "dict_module_data", {})),
            "button_count": len(getattr(coordinator, "dict_button_data", {}).get("nikobus_button", {})),
            "scene_count": len(getattr(coordinator, "dict_scene_data", {}).get("scene", [])),
            "raw_hex_states": raw_module_states,
        }

    return diagnostics_data