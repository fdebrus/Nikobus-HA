"""The diagnostics download."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CONNECTION
from .hub import NikobusHub

_REDACT = {CONF_CONNECTION}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[NikobusHub]
) -> dict[str, Any]:
    """What is known of the installation, without names or the connection."""
    hub = entry.runtime_data
    inventory = hub.inventory
    status = hub.discovery.status
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), _REDACT),
            "options": dict(entry.options),
        },
        "gateway": {
            "connected": hub.connected,
            "answered_the_probe": hub.connection.device_answered,
            "feedback_queries_seen": hub.listener.feedback_queries_seen,
            "feedback_answers_seen": hub.listener.feedback_answers_seen,
            "address": hub.connection.gateway_address,
            "family": hub.connection.gateway_family,
            "feedback_module": hub.feedback_module,
        },
        "discovery": {"phase": status.phase, "progress": status.progress, "error": status.error},
        "modules": [
            {
                "address": module.address,
                "type": module.module_type,
                "model": module.model,
                "channels": module.channels,
                "links_read": inventory.links_read(module.address),
                "state": {
                    str(channel): hub.value(module.address, channel)
                    for channel in range(1, module.channels + 1)
                },
            }
            for module in inventory.output_modules()
        ],
        "links": inventory.link_statistics(),
        "keys": sum(1 for _ in inventory.iter_keys()),
        "logical_inputs": sum(1 for _ in inventory.logical_inputs()),
        "from_project": inventory.from_project,
        "removed": list(inventory.removed),
        "led_key_outputs": sorted(hub.led_keys),
        "shutter_groups": sum(1 for _ in inventory.shutter_groups()),
        "audio_zones": len(inventory.audio_zones()),
        "colour_controllers": sum(1 for _ in inventory.rgb_modules()),
        "scenes": len(inventory.scenes),
        "scenes_named": sum(1 for scene in inventory.scenes.values() if inventory.scene_name(scene)),
        "project_scene_names": len(inventory.names["scenes"]),
        "maintenance": {
            "pc_link": hub.maintenance.pc_link_address,
            "clock_drift": hub.maintenance.clock_drift,
            **hub.maintenance.report(),
            "reprogrammed": list(hub.maintenance.reprogrammed),
        },
        "written_without_a_read_first": [
            f"{address} g{group}" for address, group in sorted(hub.command.steady_groups)
        ],
    }
