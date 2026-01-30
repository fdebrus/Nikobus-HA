"""Entity routing for Nikobus module channels."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_ROUTING_CACHE_KEY = "routing"


@dataclass(frozen=True)
class EntitySpec:
    """Route a Nikobus channel to a specific Home Assistant domain and kind."""

    domain: str
    kind: str
    address: str
    channel: int
    channel_description: str
    module_desc: str
    module_model: str
    operation_time: str | None = None


def build_unique_id(domain: str, kind: str, address: str, channel: int) -> str:
    """Build a unique_id that includes domain and kind to avoid collisions."""

    return f"{DOMAIN}_{domain}_{kind}_{address}_{channel}"

def _modules_to_address_map(modules: Any) -> dict[str, Mapping[str, Any]]:
    """Normalize modules container to {address: module_data}.

    Supports:
        - dict: {address: module_data}
        - list: [ {"address": "...", ...}, ... ]
    """
    if isinstance(modules, dict):
        # Ensure values look like mappings; keep as-is for legacy files
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
    """Return cached routing data for the config entry."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_data = domain_data.setdefault(entry.entry_id, {})
    routing = entry_data.get(_ROUTING_CACHE_KEY)
    if routing is None:
        routing = build_routing(dict_module_data)
        entry_data[_ROUTING_CACHE_KEY] = routing
    return routing

def build_routing(
    dict_module_data: Mapping[str, Any],
) -> dict[str, list[EntitySpec]]:
    """Build routing decisions for all Nikobus modules.

    Routing decisions are centralized to ensure exactly one entity per channel,
    independent of platform setup order. The per-channel entity_type is resolved
    using explicit entity_type then module defaults.
    """

    routing: dict[str, list[EntitySpec]] = {"cover": [], "switch": [], "light": []}

    for module_type, modules in dict_module_data.items():
        modules_map = _modules_to_address_map(modules)

        for address, module_data in modules_map.items():
            module_desc = module_data.get("description", f"Module {address}")
            module_model = module_data.get("model", "Unknown Module Model")

            for channel_index, channel_info in enumerate(
                module_data.get("channels", []), start=1
            ):
                channel_description = channel_info.get("description", "")
                if channel_description.startswith("not_in_use"):
                    continue

                entity_type = _resolve_entity_type(module_type, channel_info)
                domain, kind = _map_entity_type(module_type, entity_type)

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
    """Resolve the desired entity_type for a channel."""

    explicit_type = channel_info.get("entity_type")
    if explicit_type:
        if _is_supported_entity_type(module_type, explicit_type):
            return explicit_type
        _LOGGER.warning(
            "Unsupported entity_type '%s' for module '%s'; falling back to defaults.",
            explicit_type,
            module_type,
        )

    if module_type == "roller_module":
        return "cover"
    if module_type == "switch_module":
        return "switch"
    if module_type == "dimmer_module":
        return "light"

    return "switch"


def _is_supported_entity_type(module_type: str, entity_type: str) -> bool:
    """Validate entity_type values against module capabilities."""

    if module_type == "roller_module":
        return entity_type in {"cover", "switch", "light"}
    if module_type == "switch_module":
        return entity_type in {"switch", "light"}
    if module_type == "dimmer_module":
        return entity_type == "light"
    return entity_type in {"switch", "light", "cover"}


def _map_entity_type(module_type: str, entity_type: str) -> tuple[str, str]:
    """Map module type + entity_type to domain and semantic control kind."""

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
