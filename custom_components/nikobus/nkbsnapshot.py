"""Keep what a user did to press entities across a class being switched off.

Deselecting a press-entity class (``CONF_BUTTON_CLASSES``) removes its
entities and devices from the registries — that is what "not created"
means, and it is the only way a device leaves the device list. It also
means Home Assistant forgets the entity ids, names, icons and areas the
user gave them. Before the orphan cleanup removes such an entry, the
customisations are copied into the button store, next to the op point
or the plate they belong to; when the class is selected again and the
entity or device exists once more, they are put back and the copy is
dropped.

Only entries that still have an op point in the store are snapshotted:
an entity whose hardware is gone is an orphan, not a choice.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .const import DOMAIN
from .router import entry_button_classes, iter_operation_points

_LOGGER = logging.getLogger(__name__)

#: On an op point: ``{"button": {...}, "binary_sensor": {...}}``.
ENTITY_SNAPSHOT_KEY = "ha_entity"
#: On a store entry: ``{"name_by_user": ..., "area_id": ...}``.
DEVICE_SNAPSHOT_KEY = "ha_device"

_ENTITY_FIELDS = ("name", "icon", "area_id")
_DEVICE_FIELDS = ("name_by_user", "area_id")


def _unique_ids(bus_address: str) -> dict[str, str]:
    """The registry unique_id of each press entity of one op point."""
    return {
        "button": f"{DOMAIN}_push_button_{bus_address}",
        "binary_sensor": f"{DOMAIN}_button_{bus_address}",
    }


def unique_id_index(buttons: Mapping[str, Any] | None) -> dict[str, tuple[str, dict[str, Any]]]:
    """``{unique_id: (domain, op_point)}`` for every op point in the store."""
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for _addr, _key, op_point, _phys in iter_operation_points(buttons):
        for domain, unique_id in _unique_ids(str(op_point["bus_address"])).items():
            index[unique_id] = (domain, op_point)
    return index


def snapshot_entity(
    index: Mapping[str, tuple[str, dict[str, Any]]], entry: Any
) -> bool:
    """Copy a registry entry's customisations onto its op point.

    Returns whether anything was written — ``False`` for an entity that
    no op point claims (a true orphan, nothing to keep).
    """
    hit = index.get(getattr(entry, "unique_id", None))
    if hit is None:
        return False
    domain, op_point = hit
    snapshot: dict[str, Any] = {"entity_id": entry.entity_id}
    for field in _ENTITY_FIELDS:
        value = getattr(entry, field, None)
        if value:
            snapshot[field] = value
    op_point.setdefault(ENTITY_SNAPSHOT_KEY, {})[domain] = snapshot
    return True


def snapshot_device(buttons: Mapping[str, Any] | None, device: Any) -> bool:
    """Copy a device's user name and area onto its store entry, if any."""
    phys = None
    for domain, identifier in getattr(device, "identifiers", ()) or ():
        if domain == DOMAIN and isinstance((buttons or {}).get(identifier), dict):
            phys = buttons[identifier]
            break
    if phys is None:
        return False
    snapshot = {
        field: value
        for field in _DEVICE_FIELDS
        if (value := getattr(device, field, None))
    }
    if not snapshot:
        return False
    phys[DEVICE_SNAPSHOT_KEY] = snapshot
    return True


def restore_entities(
    entity_registry: Any, buttons: Mapping[str, Any] | None, classes: frozenset[str]
) -> int:
    """Put saved customisations back on entities that exist again.

    Only op points in a selected class are considered, and only once
    their entity is in the registry; a snapshot whose entity is not
    back yet stays for a later pass. Returns how many were restored.
    """
    restored = 0
    for _addr, _key, op_point, _phys in iter_operation_points(buttons, classes):
        saved = op_point.get(ENTITY_SNAPSHOT_KEY)
        if not isinstance(saved, dict):
            continue
        for domain, unique_id in _unique_ids(str(op_point["bus_address"])).items():
            snapshot = saved.get(domain)
            if not isinstance(snapshot, dict):
                continue
            current = entity_registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if current is None:
                continue
            changes: dict[str, Any] = {
                field: snapshot[field] for field in _ENTITY_FIELDS if snapshot.get(field)
            }
            wanted = snapshot.get("entity_id")
            if (
                wanted
                and wanted != current
                and entity_registry.async_get(wanted) is None
            ):
                changes["new_entity_id"] = wanted
            if changes:
                entity_registry.async_update_entity(current, **changes)
            del saved[domain]
            restored += 1
        if not saved:
            op_point.pop(ENTITY_SNAPSHOT_KEY, None)
    return restored


def restore_devices(
    device_registry: Any, buttons: Mapping[str, Any] | None, classes: frozenset[str]
) -> int:
    """Put saved names and areas back on plate devices that exist again."""
    restored = 0
    for physical_addr, phys in (buttons or {}).items():
        if not isinstance(phys, dict) or DEVICE_SNAPSHOT_KEY not in phys:
            continue
        if not (entry_button_classes(phys) & classes):
            continue
        device = device_registry.async_get_device(identifiers={(DOMAIN, str(physical_addr))})
        if device is None:
            continue
        snapshot = phys.get(DEVICE_SNAPSHOT_KEY)
        if isinstance(snapshot, dict) and snapshot:
            device_registry.async_update_device(device.id, **snapshot)
        phys.pop(DEVICE_SNAPSHOT_KEY, None)
        restored += 1
    return restored
