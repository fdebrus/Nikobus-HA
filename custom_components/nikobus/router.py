"""Entity routing and domain mapping for Nikobus module channels."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import BRAND, CATEGORY_OUTPUT_MODULES, DOMAIN

_LOGGER = logging.getLogger(__name__)


def register_output_module_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    specs: Iterable["EntitySpec"],
) -> None:
    """Register one device per physical output module address.

    Called from each output platform's ``async_setup_entry``. Deduplicates
    by address so multi-channel modules only register once. The
    ``via_device`` parent is the ``Output modules`` category device so the
    integration UI nests this module under that group (PR #338).

    Idempotent — ``device_registry.async_get_or_create`` returns the
    existing record on subsequent calls with the same identifiers.
    """
    device_registry = dr.async_get(hass)
    registered: set[str] = set()
    for spec in specs:
        if spec.address in registered:
            continue
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, spec.address)},
            manufacturer=BRAND,
            name=spec.module_desc,
            model=spec.module_model,
            via_device=(DOMAIN, CATEGORY_OUTPUT_MODULES),
        )
        registered.add(spec.address)


_ROUTING_CACHE_KEY = "routing"

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


# ---------------------------------------------------------------------------
# Synthesized input-module children (PC-Logic / Modular Interface inputs)
#
# These live in the button store (``nikobus_button``) as synthesized
# entries carrying ``pc_logic_parent_*`` provenance, not as output-module
# channels — so they bypass ``build_routing``. The helpers below are the
# single source of truth for *recognising* such a child, *naming* it, and
# the *unique_id* of its A/B latch switch, shared by the button platform,
# the switch platform, and the orphan-cleanup known-id set so those three
# can't drift apart.
# ---------------------------------------------------------------------------

def input_label_prefix(phys: Mapping[str, Any]) -> str:
    """Return the input-naming prefix for a synthesized input child.

    PC-Logic (05-201) inputs use ``LM`` (Logic Module — matches Niko's
    own terminology); Modular Interface (05-206) inputs use ``MI``
    (Modular Interface). Both share the ``pc_logic_parent_*`` provenance
    fields, so ``pc_logic_parent_type`` is the discriminator. Anything
    that isn't explicitly the Modular Interface stays ``LM`` (covers the
    PC-Logic value and missing/legacy entries — back-compat).
    """
    return "MI" if phys.get("pc_logic_parent_type") == "interface_module" else "LM"


def pc_logic_input_naming(
    phys: Mapping[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """Return ``(device_name, via_device_identifier)`` if ``phys`` is a
    synthesized input-module child (PC-Logic or Modular Interface);
    else ``None``.

    The library sets ``pc_logic_parent_address`` (the owning module
    address), ``pc_logic_parent_type`` (``pc_logic`` /
    ``interface_module``) and ``pc_logic_slot_index`` (1..N) on the
    button-store entry when it synthesizes virtual buttons for module
    inputs. HA parents the device directly under the owning module
    device (instead of the wall-buttons category) and names it
    ``LM-INPUT N`` for PC-Logic or ``MI-INPUT N`` for the Modular
    Interface, matching each product's terminology.
    """
    parent_addr = phys.get("pc_logic_parent_address")
    slot = phys.get("pc_logic_slot_index")
    if not isinstance(parent_addr, str) or not isinstance(slot, int):
        return None
    return f"{input_label_prefix(phys)}-INPUT {slot}", (DOMAIN, parent_addr.upper())


def is_input_module_child(phys: Any) -> bool:
    """True if a button-store entry is a synthesized PC-Logic / Modular
    Interface input child (vs a real wall button / remote)."""
    return (
        isinstance(phys, Mapping)
        and phys.get("pc_logic_parent_type") in INPUT_MODULE_TYPES
    )


def input_latch_switch_unique_id(physical_addr: str) -> str:
    """Unique_id for an input's A/B latch switch (switch platform)."""
    return f"nikobus_input_switch_{str(physical_addr).lower()}"


def iter_input_module_children(
    buttons: Mapping[str, Any] | None,
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield ``(physical_addr, phys)`` for every synthesized input child
    in the button store — the single enumerator the switch platform and
    the known-id set both build on."""
    for addr, phys in (buttons or {}).items():
        if is_input_module_child(phys):
            yield str(addr), phys


def iter_operation_points(
    buttons: Mapping[str, Any] | None,
) -> Iterator[tuple[str, str, Mapping[str, Any], Mapping[str, Any]]]:
    """Yield ``(physical_addr, key_label, op_point, phys)`` for every
    button operation point carrying a ``bus_address``.

    Single source of the guard ladder (entry is a dict → has a dict
    ``operation_points`` → op-point is a dict → has a truthy
    ``bus_address``) so the button platform, the binary-sensor platform
    and the orphan-cleanup known-id set agree. (binary_sensor.py and the
    known-id loop previously skipped the ``operation_points`` dict check,
    which would raise ``AttributeError`` on a malformed list-shaped
    entry.)"""
    for physical_addr, phys in (buttons or {}).items():
        if not isinstance(phys, dict):
            continue
        op_points = phys.get("operation_points")
        if not isinstance(op_points, dict):
            continue
        for key_label, op_point in op_points.items():
            if not isinstance(op_point, dict):
                continue
            if op_point.get("bus_address"):
                yield str(physical_addr), str(key_label), op_point, phys

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
        if entity_type == "light":
            return "light", "cover_binary"
        return "switch", "cover_binary"

    if module_type == "switch_module":
        if entity_type == "light":
            return "light", "relay_switch"
        return "switch", "relay_switch"

    return "switch", "relay_switch"