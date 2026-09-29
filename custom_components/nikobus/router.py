"""Entity routing and domain mapping for Nikobus module channels."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    BRAND,
    BUTTON_CLASS_AUDIO_TRIGGERS,
    BUTTON_CLASS_INPUT_MODULES,
    BUTTON_CLASS_INTERFACES,
    BUTTON_CLASS_REMOTES,
    BUTTON_CLASS_VIRTUAL_BUTTONS,
    BUTTON_CLASS_WALL_BUTTONS,
    BUTTON_CLASSES,
    CATEGORY_INTERFACES,
    CATEGORY_OUTPUT_MODULES,
    CATEGORY_REMOTES,
    CATEGORY_WALL_BUTTONS,
    CONF_BUTTON_CLASSES,
    DOMAIN,
    HUB_IDENTIFIER,
)
from .nkbdevices import parent_device_id

_LOGGER = logging.getLogger(__name__)


def register_output_module_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    specs: Iterable[EntitySpec],
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
            via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, CATEGORY_OUTPUT_MODULES)),
        )
        registered.add(spec.address)


_ROUTING_CACHE_KEY = "routing"

_CAPABILITIES = {
    "roller_module": {"cover", "switch", "light"},
    "switch_module": {"switch", "light"},
    "dimmer_module": {"light", "fan"},
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


def calendar_channel_naming(
    phys: Mapping[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """``(device_name, via_device_identifier)`` if ``phys`` is a PC-Link
    calendar channel the library synthesized from a link record; else
    ``None``.

    The library files a link whose button address is one of the
    PC-Link's 100 calendar channels (CH001 … CH100, halves A and B)
    under an entry carrying ``calendar_channel``. HA parents it under
    the bridge — the PC-Link is the bridge — and names it after the
    channel as the Nikobus software does.
    """
    label = phys.get("calendar_channel") if isinstance(phys, Mapping) else None
    if not isinstance(label, str) or not label:
        return None
    return f"PC-Link calendar {label}", (DOMAIN, HUB_IDENTIFIER)


def audio_trigger_naming(
    phys: Mapping[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """``(device_name, via_device_identifier)`` if ``phys`` is an audio
    trigger the library read out of an Audio Distribution module; else
    ``None``.

    A 05-205 is driven by virtual buttons no wall plate owns, so the
    trigger's device belongs under the module it drives rather than
    among the wall buttons — which is also what keeps the module's own
    device alive: Home Assistant drops a device that ends up with no
    entities, and until 3.20.1 the audio module had none.
    """
    if not isinstance(phys, Mapping) or not phys.get("audio_function"):
        return None
    module = str(phys.get("audio_module_address") or "").upper()
    if not module:
        return None
    description = str(phys.get("description") or "Audio trigger")
    return description, (DOMAIN, module)


def op_point_parent_device(
    physical_address: str,
    bus_address: str,
    parent_phys: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """The device an op point's entity hangs under, or ``None`` for none.

    A wall key's entity gets a device of its own, keyed by the key's bus
    address, parented under the plate (``physical_address``). An audio
    trigger has no plate: the store entry *is* the bus address, so the
    plate rule would name the same identifier as both the device and
    its parent — which Home Assistant refuses ("a device can not be its
    own via device") and drops the entity with it (3.21.0, #310). Its
    parent is the audio module the trigger drives; any other entry
    whose op point is itself gets no parent rather than itself.
    """
    audio = audio_trigger_naming(parent_phys) if parent_phys is not None else None
    if audio is not None:
        return audio[1]
    if bus_address.upper() == physical_address.upper():
        return None
    return (DOMAIN, physical_address)


def _category_for_button_type(type_str: str) -> str:
    """Return the category device identifier appropriate for a button's type.

    Classification rule based on the discovery-supplied ``type`` field:

      * ``Interface`` anywhere → Interfaces (push-button / switch /
        universal input interfaces — non-keypad input sources)
      * ``RF`` anywhere → Remotes (RF hand-held / RF wall transmitters)
      * everything else → Wall buttons (physical bus push buttons)

    Interface is matched before RF because ``"rf"`` is a substring of
    ``"interface"`` — checking RF first would route every Universal /
    Modular / push-button interface into Remotes.
    """
    lowered = type_str.lower()
    if "interface" in lowered:
        return CATEGORY_INTERFACES
    if "rf" in lowered:
        return CATEGORY_REMOTES
    return CATEGORY_WALL_BUTTONS


_CLASS_BY_CATEGORY = {
    CATEGORY_REMOTES: BUTTON_CLASS_REMOTES,
    CATEGORY_INTERFACES: BUTTON_CLASS_INTERFACES,
}


def button_class(phys: Mapping[str, Any] | None, key_label: str = "") -> str:
    """The class (``BUTTON_CLASSES``) of the press entities for one op point.

    Decided from the store entry, and for one case from the op point:
    an IR receiver is a wall plate whose ``IR:`` op points are remote
    codes, so those go with the remotes while its keys stay with the
    wall buttons.
    """
    if not isinstance(phys, Mapping):
        return BUTTON_CLASS_WALL_BUTTONS
    if phys.get("audio_function"):
        return BUTTON_CLASS_AUDIO_TRIGGERS
    if phys.get("virtual_button"):
        return BUTTON_CLASS_VIRTUAL_BUTTONS
    if is_input_module_child(phys):
        return BUTTON_CLASS_INPUT_MODULES
    if str(key_label).startswith("IR:") or phys.get("remote_transmitter_address"):
        return BUTTON_CLASS_REMOTES
    category = _category_for_button_type(
        str(phys.get("type") or phys.get("model") or "")
    )
    return _CLASS_BY_CATEGORY.get(category, BUTTON_CLASS_WALL_BUTTONS)


def entry_button_classes(phys: Mapping[str, Any] | None) -> frozenset[str]:
    """Every class among a store entry's op points (one plate can span two)."""
    if not isinstance(phys, Mapping):
        return frozenset()
    op_points = phys.get("operation_points")
    if isinstance(op_points, Mapping) and op_points:
        return frozenset(button_class(phys, str(key)) for key in op_points)
    return frozenset({button_class(phys)})


def enabled_button_classes(options: Mapping[str, Any] | None) -> frozenset[str]:
    """The classes the entry's options select.

    No ``button_classes`` key at all means every class — what every
    release before 3.22.0 did, and what the migration writes out for an
    existing entry. An empty list means none.
    """
    if not isinstance(options, Mapping) or CONF_BUTTON_CLASSES not in options:
        return frozenset(BUTTON_CLASSES)
    raw = options.get(CONF_BUTTON_CLASSES) or []
    return frozenset(str(c) for c in raw if c in BUTTON_CLASSES)


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
    classes: frozenset[str] | None = None,
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield ``(physical_addr, phys)`` for every synthesized input child
    in the button store — the single enumerator the switch platform and
    the known-id set both build on. ``classes`` (the entry's selected
    press-entity classes) yields nothing when input modules are not
    among them; ``None`` means no filtering."""
    if classes is not None and BUTTON_CLASS_INPUT_MODULES not in classes:
        return
    for addr, phys in (buttons or {}).items():
        if is_input_module_child(phys):
            yield str(addr), phys


def iter_operation_points(
    buttons: Mapping[str, Any] | None,
    classes: frozenset[str] | None = None,
) -> Iterator[tuple[str, str, dict[str, Any], dict[str, Any]]]:
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
            if not op_point.get("bus_address"):
                continue
            if classes is not None and button_class(phys, str(key_label)) not in classes:
                continue
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
# Since 3.20.0 an Audio Distribution module does surface entities — a
# media player per zone, built from the triggers discovery reads out of
# the module (nikobus-connect 0.39.0) rather than from output channels.
# It stays here because it has no channels for the channel router to map.
#
#   * ``rgb_module`` — the 340-00112 RGB controller (device type 0x46),
#     catalogued in nikobus-connect 0.40.0 from an install's project
#     file. Identity only: nobody has read its registers, so neither its
#     programming nor how its output is driven is known, and it declares
#     no channels. The device is registered so the install shows what is
#     on the bus; entities wait for a register dump.
OPAQUE_MODULE_TYPES: frozenset[str] = frozenset({"audio_module", "rgb_module"})

# Audio functions a zone's media player drives, by the library's label.
AUDIO_FUNCTION_ON = "M16 (On)"
AUDIO_FUNCTION_OFF = "M17 (Off)"
AUDIO_FUNCTION_VOLUME_UP = "M13 (Volume up)"
AUDIO_FUNCTION_VOLUME_DOWN = "M14 (Volume down)"
AUDIO_SOURCE_FUNCTIONS: tuple[str, ...] = (
    "M03 (Source 1)",
    "M04 (Source 2)",
    "M05 (Source 3)",
    "M06 (Source 4)",
    "M07 (Source 5)",
    "M08 (Source 6)",
    "M09 (Source 7)",
    "M10 (Source 8)",
)


def audio_zones(buttons: Mapping[str, Any] | None) -> dict[tuple[str, int], dict[str, str]]:
    """``{(module address, zone): {function label: bus address}}``.

    Built from the audio triggers discovery filed in the button store:
    each entry carries the address the module listens for, its zone and
    the function it drives. A trigger that drives no zone — the module's
    Power object — is returned under zone ``0``; it is a button, not a
    player, so the media-player platform skips it.
    """
    zones: dict[tuple[str, int], dict[str, str]] = {}
    for address, entry in (buttons or {}).items():
        if not isinstance(entry, dict) or not entry.get("audio_function"):
            continue
        zone = entry.get("audio_zone") or 0
        for link in entry.get("operation_points", {}).get("AUD", {}).get("linked_modules", []):
            module = str(link.get("module_address") or "").upper()
            if module:
                zones.setdefault((module, int(zone)), {})[
                    str(entry["audio_function"])
                ] = str(address).upper()
    return zones


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
    end_stop_margin: str | None = None


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
    routing: dict[str, list[EntitySpec]] = {"cover": [], "switch": [], "light": [], "fan": []}

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
            # ``nkb_name`` is the persisted .nkb-import name — preferred
            # so the imported module name survives restarts (DeviceInfo
            # re-asserts this value on every setup).
            module_desc = module_data.get("nkb_name") or module_data.get(
                "description", f"Module {address}"
            )
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
                        end_stop_margin=channel_info.get("end_stop_margin"),
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
        # A dimmer output driving a PWM / variable-speed fan (extractor
        # fans on a 05-007): same 0-255 channel, presented as a fan so
        # HA and HomeKit offer speed instead of brightness.
        if entity_type == "fan":
            return "fan", "dimmer_fan"
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