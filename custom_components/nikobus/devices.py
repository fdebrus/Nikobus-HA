"""Device-registry registration of Nikobus devices.

One device per physical wall button (grouping its operation points),
per input module (PC-Logic, Modular Interface), per opaque module
(Audio Distribution), and the synthesized children the library adds
(PC-Logic inputs, remote-transmitter codes, PC-Link calendar channels).
Shared by the button, binary-sensor and scene platforms; moved out of
the button platform so that module holds entities only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    BRAND,
    CATEGORY_INTERFACES,
    CATEGORY_REMOTES,
    CATEGORY_SYSTEM_MODULES,
    CATEGORY_WALL_BUTTONS,
    DOMAIN,
)
from .coordinator import NikobusConfigEntry
from .nkbdevices import parent_device_id
from .router import (
    INPUT_MODULE_TYPES,
    OPAQUE_MODULE_TYPES,
    calendar_channel_naming,
    input_label_prefix,
    pc_logic_input_naming,
)

_LOGGER = logging.getLogger(__name__)


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


def _remote_transmitter_naming(
    phys: dict[str, Any],
) -> tuple[str, tuple[str, str]] | None:
    """Return ``(device_name, via_device_identifier)`` if ``phys`` is a
    synthesised remote-transmitter child entry; else ``None``.

    The library's cluster-detection pass synthesises a virtual
    transmitter parent for any cluster of 8+ unmatched bus addresses
    sharing a 4-hex suffix (typical for multi-page Easywave remotes
    emitting dozens of distinct codes). Each child carries
    ``remote_transmitter_address`` (the synthetic parent ID, e.g.
    ``RT-E31C``) and ``remote_transmitter_bus_address`` (the original
    observed bus event). HA renders each child as a
    ``Remote <bus>`` device parented under the transmitter.
    """

    parent_id = phys.get("remote_transmitter_address")
    bus_addr = phys.get("remote_transmitter_bus_address")
    if not isinstance(parent_id, str) or not isinstance(bus_addr, str):
        return None
    return f"Remote {bus_addr}", (DOMAIN, parent_id)


def register_wall_button_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    buttons: dict[str, Any],
    dict_module_data: dict[str, Any] | None = None,
) -> None:
    """Register one device per physical wall button (top-level address).

    Groups the N operation-points of a keypad/IR remote under a single parent
    device in the device registry. The default name is taken straight from
    the discovery metadata (``{type} ({address})``) so it is identical for
    every installation; HA preserves any user rename via ``name_by_user``
    across reloads. Idempotent: safe to call from multiple platforms.

    The button's ``via_device`` parent is one of the category devices —
    Wall buttons / Remotes / Interfaces — chosen by
    ``_category_for_button_type`` so the integration's device list
    nests by class rather than dumping everything under the bridge.

    Synthesized input-module children (the library's
    ``_synthesize_pc_logic_inputs`` adds these with ``pc_logic_*``
    provenance fields) are routed differently: the device is parented
    directly under the owning module, and the name follows the Niko
    ``LM-INPUT N`` (PC-Logic) / ``MI-INPUT N`` (Modular Interface)
    convention.
    """
    device_registry = dr.async_get(hass)
    pc_logic_parents_registered: set[str] = set()
    remote_transmitter_parents_registered: set[str] = set()
    for physical_addr, phys in buttons.items():
        if not isinstance(phys, dict):
            continue

        calendar_naming = calendar_channel_naming(phys)
        if calendar_naming is not None:
            name, via_device = calendar_naming
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, physical_addr)},
                manufacturer=BRAND,
                name=str(phys.get("nkb_name") or name),
                model="PC-Link calendar channel",
                via_device_id=parent_device_id(device_registry, entry.entry_id, via_device),
            )
            continue

        pc_logic_naming = pc_logic_input_naming(phys)
        if pc_logic_naming is not None:
            name, via_device = pc_logic_naming
            parent_addr = via_device[1]
            # HA 2025.12 enforces via_device referencing an existing device.
            # The PC-Logic module is normally registered by
            # register_input_module_devices, but that runs from a different
            # call site (and possibly after this one) — pre-register the
            # parent here so the child's via_device always resolves.
            if parent_addr not in pc_logic_parents_registered:
                _ensure_pc_logic_parent_device(
                    device_registry, entry, parent_addr, dict_module_data
                )
                pc_logic_parents_registered.add(parent_addr)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, physical_addr)},
                manufacturer=BRAND,
                name=name,
                model=str(phys.get("model") or "PC-Logic Logical Input"),
                via_device_id=parent_device_id(device_registry, entry.entry_id, via_device),
            )
            continue

        remote_naming = _remote_transmitter_naming(phys)
        if remote_naming is not None:
            name, via_device = remote_naming
            parent_id = via_device[1]
            if parent_id not in remote_transmitter_parents_registered:
                _ensure_remote_transmitter_parent_device(
                    device_registry, entry, parent_id, phys
                )
                remote_transmitter_parents_registered.add(parent_id)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, physical_addr)},
                manufacturer=BRAND,
                name=name,
                model=str(phys.get("model") or "Remote Code"),
                via_device_id=parent_device_id(device_registry, entry.entry_id, via_device),
            )
            continue

        type_str = str(phys.get("type") or phys.get("model") or "Wall Button")
        model = str(phys.get("model") or phys.get("type") or "Wall Button")
        category = _category_for_button_type(type_str)
        # Generated default carries the Niko software's index when the
        # PC-Link registry provided one (``component_number``, library
        # 0.33.0) — "7: Bus push button, ... (1843B4)" — so the HA
        # device list cross-references with the Nikobus application
        # even on installs without an .nkb project file. The persisted
        # .nkb-import name still wins over the default — the registry's
        # ``name`` field is re-asserted from here on every restart, so
        # this is what makes a non-overwrite import stick.
        number = phys.get("component_number")
        default_name = (
            f"{number}: {type_str} ({physical_addr})"
            if number
            else f"{type_str} ({physical_addr})"
        )
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, physical_addr)},
            manufacturer=BRAND,
            name=str(phys.get("nkb_name") or default_name),
            model=model,
            via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, category)),
        )


def _ensure_remote_transmitter_parent_device(
    device_registry: dr.DeviceRegistry,
    entry: NikobusConfigEntry,
    transmitter_id: str,
    sample_child: dict[str, Any],
) -> None:
    """Register a synthetic remote-transmitter parent device.

    Unlike PC-Logic / interface_module parents (which are real
    Nikobus modules with an enrolled bus address and a record in
    ``dict_module_data``), the transmitter parent is a purely
    HA-side construct synthesised from a cluster of unmatched bus
    references. The identifier is ``(DOMAIN, "RT-<suffix>")`` and
    the device is parented under the Remotes category so it
    appears grouped with other RF transmitters in the HA device
    list.
    """

    suffix = sample_child.get("remote_transmitter_suffix") or transmitter_id
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, transmitter_id)},
        manufacturer=BRAND,
        name=f"Remote Transmitter ({suffix})",
        model="RF Remote (synthesized)",
        via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, CATEGORY_REMOTES)),
    )


def _ensure_pc_logic_parent_device(
    device_registry: dr.DeviceRegistry,
    entry: NikobusConfigEntry,
    parent_addr: str,
    dict_module_data: dict[str, Any] | None,
) -> None:
    """Register the input-module device that a synthesised input-child
    points to via ``via_device``.

    Looks the parent module up in ``dict_module_data`` under both the
    ``pc_logic`` and ``interface_module`` buckets — both module types
    use the same synthesis path and provenance shape, so the parent
    can live in either bucket. Falls back to a placeholder when module
    data isn't available — a later call to ``register_input_module_devices``
    will update fields on the same identifier.
    """
    module_data: dict[str, Any] | None = None
    found_module_type: str | None = None
    for module_type in ("pc_logic", "interface_module"):
        bucket = (dict_module_data or {}).get(module_type) or {}
        if not isinstance(bucket, dict):
            continue
        for addr, data in bucket.items():
            if str(addr).upper() == parent_addr and isinstance(data, dict):
                module_data = data
                found_module_type = module_type
                break
        if module_data is not None:
            break

    if module_data is not None:
        default_name = (
            f"PC-Logic ({parent_addr})"
            if found_module_type == "pc_logic"
            else f"Modular Interface ({parent_addr})"
        )
        name = str(module_data.get("description") or default_name)
        model = str(module_data.get("model") or found_module_type)
    else:
        name = f"Input Module ({parent_addr})"
        model = "input_module"

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, parent_addr)},
        manufacturer=BRAND,
        name=name,
        model=model,
        via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, CATEGORY_SYSTEM_MODULES)),
    )


def _iter_module_records(
    dict_module_data: dict[str, Any], module_types: frozenset[str]
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(module_type, address, module_data)`` for the requested buckets."""
    for module_type in module_types:
        bucket = dict_module_data.get(module_type)
        if not isinstance(bucket, dict):
            continue
        for address, module_data in bucket.items():
            if isinstance(module_data, dict):
                yield module_type, str(address).upper(), module_data


def register_input_module_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    dict_module_data: dict[str, Any],
) -> None:
    """Register one device per PC-Logic / Modular Interface module.

    Each input module groups its N input channels (LM01–LM06 on PC-Logic,
    six inputs on the Modular Interface) under a single parent device in
    the registry, mirroring the wall-button device layout.
    """
    device_registry = dr.async_get(hass)
    for module_type, address, module_data in _iter_module_records(
        dict_module_data, INPUT_MODULE_TYPES
    ):
        description = str(
            module_data.get("description") or f"{module_type} ({address})"
        )
        model = str(module_data.get("model") or module_type)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, address)},
            manufacturer=BRAND,
            name=description,
            model=model,
            via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, CATEGORY_SYSTEM_MODULES)),
        )


def register_opaque_module_devices(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    dict_module_data: dict[str, Any],
) -> None:
    """Register a placeholder device per Audio Distribution module.

    Audio modules surface no entities yet (input/output schema not
    validated), but registering the device keeps them visible in the HA
    device registry so users can confirm discovery saw them.
    """
    device_registry = dr.async_get(hass)
    for module_type, address, module_data in _iter_module_records(
        dict_module_data, OPAQUE_MODULE_TYPES
    ):
        description = str(
            module_data.get("description") or f"{module_type} ({address})"
        )
        model = str(module_data.get("model") or module_type)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, address)},
            manufacturer=BRAND,
            name=description,
            model=model,
            via_device_id=parent_device_id(device_registry, entry.entry_id, (DOMAIN, CATEGORY_SYSTEM_MODULES)),
        )


def op_point_display_name(
    physical_address: str,
    key_label: str,
    op_point: dict[str, Any],
    *,
    parent_phys: dict[str, Any] | None = None,
) -> str:
    """Build a UI-visible name for an op-point's device entry.

    IR op-points (storage keys starting with ``IR:``) get the receiver's
    bus address appended so the same IR code registered on different
    receivers remains distinguishable in the device list — the
    library-generated description ("IR code 30A #I30A") is identical for
    every receiver that learned the same code. Wall keys keep the
    library description verbatim; it already carries the channel label.

    Input-module keys (the parent button carries
    ``pc_logic_parent_address``) render as ``Key A on LM-INPUT N`` /
    ``Key B on LM-INPUT N`` for PC-Logic, or ``... on MI-INPUT N`` for
    the Modular Interface, mirroring the IR ``<key> on <parent>``
    pattern so the device list disambiguates each slot's keys.
    """
    if key_label.startswith("IR:"):
        ir_code = key_label[len("IR:"):]
        return f"IR {ir_code} on {physical_address}"
    if isinstance(parent_phys, dict) and parent_phys.get("pc_logic_parent_address"):
        # ``1A`` → "Key A on {LM,MI}-INPUT N", ``1B`` → "Key B on …".
        slot = parent_phys.get("pc_logic_slot_index")
        if (
            len(key_label) == 2
            and key_label[1].isalpha()
            and isinstance(slot, int)
        ):
            prefix = input_label_prefix(parent_phys)
            return f"Key {key_label[1].upper()} on {prefix}-INPUT {slot}"
    # ``nkb_name`` is the persisted .nkb-import name ("<plate> Key 1A") —
    # written by async_import_nkb_names so the imported name survives
    # restarts (DeviceInfo re-asserts whatever this function returns).
    return (
        op_point.get("nkb_name")
        or op_point.get("description")
        or f"Push button {key_label}"
    )
