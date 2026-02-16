"""Entity routing and domain mapping for Nikobus module channels."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_ROUTING_CACHE_KEY = "routing"


@dataclass(frozen=True)
class EntitySpec:
    """Specification for routing a Nikobus channel to a Home Assistant domain."""

    domain: str
    kind: str
    address: str
    channel: int
    channel_description: str
    module_desc: str
    module_model: str
    operation_time: str | None = None


def build_unique_id(domain: str, kind: str, address: str, channel: int) -> str:
    """Build a globally unique ID for a Nikobus entity.

    Includes domain and kind to prevent collisions when the same physical 
    channel is used differently.
    """
    return f"{DOMAIN}_{domain}_{kind}_{address}_{channel}"


def _modules_to_address_map(modules: Any) -> dict[str, Mapping[str, Any]]:
    """Normalize raw module data into a mapping keyed by uppercase address."""
    if isinstance(modules, dict):
        return {
            str(addr).upper(): data
            for addr, data in modules.items()
            if isinstance(data, Mapping)
        }

    if isinstance(modules, list):
        out: dict[str, Mapping[str, Any]] = {}
        for item in modules:
            if not isinstance(item, Mapping):
                continue
            addr = item.get("address")
            if not addr:
                continue
            out[str(addr).upper()] = item
        return out

    return {}


def get_routing(
    hass: HomeAssistant, entry: ConfigEntry, dict_module_data: Mapping[str, Any]
) -> dict[str, list[EntitySpec]]:
    """Retrieve or build the entity routing spec from the cache."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_data = domain_data.setdefault(entry.entry_id, {})
    routing = entry_data.get(_ROUTING_CACHE_KEY)
    
    if routing is None:
        _LOGGER.debug("Building entity routing for config entry %s", entry.entry_id)
        routing = build_routing(dict_module_data)
        entry_data[_ROUTING_CACHE_KEY] = routing
        
    return routing


def build_routing(
    dict_module_data: Mapping[str, Any],
) -> dict[str, list[EntitySpec]]:
    """Analyze all modules and assign channels to Home Assistant domains.

    This ensures that each channel results in exactly one entity type, 
    even if it belongs to a versatile module (like a roller module used for lights).
    """
    routing: dict[str, list[EntitySpec]] = {"cover": [], "switch": [], "light": []}

    for module_type, modules in dict_module_data.items():
        modules_map = _modules_to_address_map(modules)

        for address, module_data in modules_map.items():
            module_desc = module_data.get("description", f"Module {address}")
            module_model = module_data.get("model", "Unknown")

            for channel_index, channel_info in enumerate(
                module_data.get("channels", []), start=1
            ):
                channel_description = channel_info.get("description", "")
                
                # Skip channels explicitly marked as unused
                if channel_description.startswith("not_in_use"):
                    continue

                entity_type = _resolve_entity_type(module_type, channel_info)
                domain, kind = _map_entity_type(module_type, entity_type)

                if domain not in routing:
                    _LOGGER.error("Resolved unknown domain '%s' for channel %s", domain, address)
                    continue

                routing[domain].append(
                    EntitySpec(
                        domain=domain,
                        kind=kind,
                        address=address,
                        channel=channel_index,
                        channel_description=channel_description,
                        module_desc=module_desc,
                        module_model=module_model,
                        operation_time=channel_info.get("operation_time"),
                    )
                )

    return routing


def _resolve_entity_type(module_type: str, channel_info: Mapping[str, Any]) -> str:
    """Resolve the specific entity type for a channel based on configuration."""
    explicit_type = channel_info.get("entity_type")
    
    if explicit_type:
        if _is_supported_entity_type(module_type, explicit_type):
            return explicit_type
        _LOGGER.warning(
            "Unsupported type '%s' for %s; using hardware default",
            explicit_type,
            module_type,
        )

    # Hardware defaults based on module classification
    if module_type == "roller_module":
        return "cover"
    if module_type == "dimmer_module":
        return "light"

    return "switch"


def _is_supported_entity_type(module_type: str, entity_type: str) -> bool:
    """Verify if a module hardware is capable of supporting an entity type."""
    capabilities = {
        "roller_module": {"cover", "switch", "light"},
        "switch_module": {"switch", "light"},
        "dimmer_module": {"light"},
    }
    
    allowed = capabilities.get(module_type, {"switch", "light"})
    return entity_type in allowed


def _map_entity_type(module_type: str, entity_type: str) -> tuple[str, str]:
    """Map the Nikobus configuration to a Home Assistant domain and internal kind."""
    if module_type == "dimmer_module":
        return "light", "dimmer_light"

    if module_type == "roller_module":
        if entity_type == "cover":
            return "cover", "cover"
        return ("light", "cover_binary") if entity_type == "light" else (
            "switch",
            "cover_binary",
        )

    if module_type == "switch_module":
        return ("light", "relay_switch") if entity_type == "light" else (
            "switch",
            "relay_switch",
        )

    return "switch", "relay_switch"