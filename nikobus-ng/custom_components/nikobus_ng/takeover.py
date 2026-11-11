"""Taking over the entities of the Nikobus integration, and giving them back.

Both integrations describe the same outputs. Dashboards, automations
and voice assistants know them by the entity ids, names, areas and
aliases they have under the Nikobus integration. A take-over moves
those onto this integration's entities: the Nikobus entity is parked
under another id, and this integration's entity takes its id, the name
it is shown under, its area, aliases, labels, icon and what it is
exposed to.
History follows, since it is kept by entity id.

An entity id belongs to one entity, so this is a change of hands, not a
copy. It is recorded, and giving back undoes it.

Keys are not taken over: they are binary sensors and buttons there and
event entities here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_FAN_OUTPUTS,
    CONF_LIGHT_OUTPUTS,
    CONF_SHUTTERS,
    CONF_USE_AS,
    DIMMER_MODULE,
    DOMAIN,
    ROLLER_MODULE,
    SWITCH_MODULE,
)

if TYPE_CHECKING:
    from .hub import NikobusHub

_LOGGER = logging.getLogger(__name__)

OLD_DOMAIN = "nikobus"
#: What a parked Nikobus entity's id ends in.
PARKED = "_nikobus"

# The unique ids the Nikobus integration gives: an output, a shutter
# group, a central function, a logical input's switch.
_OUTPUT = re.compile(r"^nikobus_(light|switch|cover|fan)_[a-z_]+_([0-9A-Fa-f]{4})_(\d+)$")
_OTHERS = (
    (re.compile(r"^nikobus_cf_cover_([0-9A-Fa-f]{6})$"), "cover", "{}_group"),
    (re.compile(r"^nikobus_cf_([0-9A-Fa-f]{6})$"), "scene", "{}_scene"),
    (re.compile(r"^nikobus_input_switch_([0-9A-Fa-f]{6})$"), "switch", "{}_input"),
)


@dataclass(slots=True)
class Plan:
    """What a take-over would do."""

    #: ``{"entity_id", "domain", "unique_id", "name"}`` per entity to take over.
    entities: list[dict[str, str]] = field(default_factory=list)
    #: The options that make this integration's outputs the same kind of entity.
    options: dict[str, Any] = field(default_factory=dict)
    #: Nikobus entities with no counterpart here: keys, sensors, its own buttons.
    left: int = 0


def old_entry_blocks(hass: HomeAssistant) -> bool:
    """Whether the Nikobus integration is running: its entities are then in use."""
    return any(
        entry.state is ConfigEntryState.LOADED for entry in hass.config_entries.async_entries(OLD_DOMAIN)
    )


def has_old_entities(hass: HomeAssistant) -> bool:
    """Whether the Nikobus integration left entities to take over."""
    return any(entry.platform == OLD_DOMAIN for entry in er.async_get(hass).entities.values())


def _shown_name(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    """The name the Nikobus entity is shown under, as Home Assistant composes it."""
    return er.async_get_legacy_friendly_name(hass, entry)


def make_plan(hass: HomeAssistant, hub: NikobusHub) -> Plan:
    """Match every Nikobus entity to this integration's, and say what would change."""
    inventory = hub.inventory
    modules = {module.address: module for module in inventory.output_modules()}
    colour = {address for address, _ in inventory.rgb_modules()}
    lights = set(hub.light_outputs)
    fans = set(hub.fan_outputs)
    shutters = {output: dict(settings) for output, settings in hub.shutters.items()}
    plan = Plan()
    for entry in sorted(er.async_get(hass).entities.values(), key=lambda e: e.entity_id):
        if entry.platform != OLD_DOMAIN:
            continue
        target: tuple[str, str] | None = None
        if match := _OUTPUT.match(entry.unique_id):
            address, channel = match[2].upper(), int(match[3])
            output = f"{address}:{channel}"
            if address in colour:
                target = ("light", f"{address}_rgb")
            elif (module := modules.get(address)) and channel <= module.channels:
                target = (entry.domain, f"{address}_{channel}")
                # The same kind of entity here as there.
                if module.module_type == SWITCH_MODULE:
                    (lights.add if entry.domain == "light" else lights.discard)(output)
                elif module.module_type == DIMMER_MODULE:
                    (fans.add if entry.domain == "fan" else fans.discard)(output)
                elif module.module_type == ROLLER_MODULE:
                    settings = shutters.setdefault(output, {})
                    settings.pop(CONF_USE_AS, None)
                    if entry.domain in ("light", "switch"):
                        settings[CONF_USE_AS] = entry.domain
                    if not settings:
                        del shutters[output]
        else:
            for pattern, domain, unique_id in _OTHERS:
                if match := pattern.match(entry.unique_id):
                    target = (domain, unique_id.format(match[1].upper()))
                    break
        if target is None:
            plan.left += 1
            continue
        plan.entities.append(
            {
                "entity_id": entry.entity_id,
                "domain": target[0],
                "unique_id": target[1],
                "name": _shown_name(hass, entry),
            }
        )
    plan.options = {
        CONF_LIGHT_OUTPUTS: sorted(lights),
        CONF_FAN_OUTPUTS: sorted(fans),
        CONF_SHUTTERS: shutters,
    }
    return plan


def apply_pending(hass: HomeAssistant, hub: NikobusHub) -> int:
    """Move the ids and what goes with them. Run once the entities exist; returns how many moved."""
    inventory = hub.inventory
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    pending, inventory.takeover["pending"] = inventory.takeover["pending"], []
    for item in pending:
        old = registry.async_get(item["entity_id"])
        mine_id = registry.async_get_entity_id(item["domain"], DOMAIN, item["unique_id"])
        parked = f"{item['entity_id']}{PARKED}"
        if old is None or old.platform != OLD_DOMAIN or mine_id is None or registry.async_get(parked):
            _LOGGER.warning("%s was not taken over: no counterpart here, or its parking id is taken", item["entity_id"])
            continue
        mine = registry.async_get(mine_id)
        device = devices.async_get(old.device_id) if old.device_id else None
        registry.async_update_entity(old.entity_id, new_entity_id=parked)
        changes: dict[str, Any] = {
            "new_entity_id": item["entity_id"],
            # A name set in the registry is shown as it is, without the module's in front.
            "name": item["name"] or None,
            "area_id": old.area_id or (device.area_id if device else None),
            "aliases": old.aliases,
            "labels": old.labels,
            "icon": old.icon,
            "hidden_by": old.hidden_by,
        }
        if old.disabled_by is None and mine and mine.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            # Used there, so wanted here, whatever the links say.
            changes["disabled_by"] = None
        registry.async_update_entity(mine_id, **changes)
        for domain, options in old.options.items():
            registry.async_update_entity_options(item["entity_id"], domain, dict(options))
        inventory.takeover["done"].append({**item, "previous_entity_id": mine_id, "parked": parked})
    return len(inventory.takeover["done"])


def give_back(hass: HomeAssistant, hub: NikobusHub) -> int:
    """Undo a take-over: each entity gets the id it had before. Returns how many were given back."""
    inventory = hub.inventory
    registry = er.async_get(hass)
    given = 0
    for item in reversed(inventory.takeover["done"]):
        mine_id = registry.async_get_entity_id(item["domain"], DOMAIN, item["unique_id"])
        if mine_id == item["entity_id"]:
            free = registry.async_get(item["previous_entity_id"]) is None
            registry.async_update_entity(
                mine_id,
                new_entity_id=item["previous_entity_id"] if free else f"{item['entity_id']}_ng",
            )
        parked = registry.async_get(item["parked"])
        if parked is not None and parked.platform == OLD_DOMAIN and registry.async_get(item["entity_id"]) is None:
            registry.async_update_entity(item["parked"], new_entity_id=item["entity_id"])
            given += 1
    inventory.takeover = {"pending": [], "done": []}
    return given
