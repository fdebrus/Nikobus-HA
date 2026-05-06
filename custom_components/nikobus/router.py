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

# CLEANUP: Move static capabilities to module level so they are only created once in memory
_CAPABILITIES = {
    "roller_module": {"cover", "switch", "light"},
    "switch_module": {"switch", "light"},
    "dimmer_module": {"light"},
}

# Module types whose channels are *inputs* (presses on the bus), not
# output relays. Each channel of these modules surfaces as a button
# entity in ``custom_components/nikobus/button.py``; ``build_routing``
# below skips them so they don't get rendered as switch / light / cover
# entities by mistake.
#
#   * ``pc_logic`` — Master PC-Logic component (05-201). DEVICE_TYPES
#     entry gained ``Channels: 6`` in nikobus-connect 0.5.10; the six
#     channels correspond to the LM01–LM06 local inputs in the Niko
#     PC software.
#   * ``interface_module`` — Modular Interface 6 inputs (05-206).
#     Promoted out of ``other_module`` into its own bucket in 0.5.10.
INPUT_MODULE_TYPES: frozenset[str] = frozenset({"pc_logic", "interface_module"})

# Module types we recognise but for which no entity schema is validated
# yet — the inventory record alone makes the device visible in the HA
# device registry, but no platform creates entities for it.
#
#   * ``audio_module`` — Audio Distribution module (05-205). Promoted
#     out of ``other_module`` into its own bucket in nikobus-connect
#     0.5.10. Input/output schema not yet validated; creating switches
#     for it (the previous fall-through behaviour) was wrong, so the
#     router skips it explicitly.
OPAQUE_MODULE_TYPES: frozenset[str] = frozenset({"audio_module"})


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
    operation_time_up: str | None = None
    operation_time_down: str | None = None


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
        return {
            str(item.get("address")).upper(): item 
            for item in modules 
            if isinstance(item, Mapping) and item.get("address")
        }
        
    _LOGGER.warning(
        "_modules_to_address_map received unexpected type %s — no entities will be created for this module group",
        type(modules).__name__,
    )
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
        # Input-class modules (PC-Logic, Modular Interface) and opaque
        # modules (Audio Distribution) don't drive output relays; the
        # button platform handles input modules and audio modules don't
        # surface entities yet. Skip both so we don't create phantom
        # switch entities for their channels.
        if module_type in INPUT_MODULE_TYPES or module_type in OPAQUE_MODULE_TYPES:
            continue

        modules_map = _modules_to_address_map(modules)

        for address, module_data in modules_map.items():
            module_desc = module_data.get("description", f"Module {address}")
            module_model = module_data.get("model", "Unknown")

            for channel_index, channel_info in enumerate(
                module_data.get("channels", []), start=1
            ):
                if not isinstance(channel_info, Mapping):
                    _LOGGER.warning(
                        "Channel %d for module %s is not a dict — skipping",
                        channel_index, address,
                    )
                    continue
                channel_description = channel_info.get("description", "")

                # Skip channels explicitly marked as unused.
                #   * ``entity_type: "disabled"`` — set from the "Customize a
                #     module" options flow to hide a channel.
                #   * ``description`` prefixed with ``not_in_use`` — the
                #     legacy convention from hand-edited config files; still
                #     honoured for backwards compatibility.
                if channel_info.get("entity_type") == "disabled":
                    continue
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
                        operation_time_up=channel_info.get("operation_time_up"),
                        operation_time_down=channel_info.get("operation_time_down"),
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
    # Uses the module-level constant we defined at the top
    allowed = _CAPABILITIES.get(module_type, {"switch", "light"})
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