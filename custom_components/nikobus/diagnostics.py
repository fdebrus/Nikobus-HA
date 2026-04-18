"""Diagnostics support for the Nikobus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .coordinator import NikobusConfigEntry
from .entity import device_entry_diagnostics

TO_REDACT = {CONF_CONNECTION_STRING}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NikobusConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Nikobus config entry."""
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)

    nikobus_devices = [
        device
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if any(ident[0] == DOMAIN for ident in device.identifiers)
    ]

    raw_module_states = {
        addr: state.hex() for addr, state in coordinator.nikobus_module_states.items()
    }

    return {
        "config_entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "state": entry.state.value,
            "feedback_module": entry.options.get(
                CONF_HAS_FEEDBACK_MODULE,
                entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
            ),
            "prior_gen3": entry.options.get(
                CONF_PRIOR_GEN3, entry.data.get(CONF_PRIOR_GEN3, False)
            ),
            "refresh_interval": entry.options.get(
                CONF_REFRESH_INTERVAL,
                entry.data.get(CONF_REFRESH_INTERVAL, 0),
            ),
        },
        "coordinator": {
            "connection_status": coordinator.connection_status,
            "reconnect_attempts": coordinator._reconnect_attempts,
            "last_connected": (
                coordinator._last_connected.isoformat()
                if coordinator._last_connected
                else None
            ),
            "module_count": len(coordinator.dict_module_data),
            "button_count": len(
                coordinator.dict_button_data.get("nikobus_button", {})
            ),
            "scene_count": len(coordinator.dict_scene_data.get("scene", [])),
            "discovery_phase": coordinator.discovery_phase,
            "raw_hex_states": raw_module_states,
        },
        "devices": [device_entry_diagnostics(dev) for dev in nikobus_devices],
    }
