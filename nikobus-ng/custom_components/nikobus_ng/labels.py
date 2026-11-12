"""A label per kind of entity, to filter by in Home Assistant's tables and pickers.

The same three labels the Nikobus integration applies, under the same
names, so an installation that has them keeps one set.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

LABEL_OUTPUT = "Nikobus Output"
LABEL_BUTTON = "Nikobus Button"
LABEL_SCENE = "Nikobus Scene"

_OUTPUT_PLATFORMS = (Platform.LIGHT, Platform.COVER, Platform.FAN, Platform.SWITCH)


def _kind(entity: er.RegistryEntry) -> str | None:
    """The label of an entity; none for the gateway's own and for a logical input's state."""
    if entity.domain == Platform.SCENE:
        return LABEL_SCENE
    if entity.domain == Platform.EVENT or entity.unique_id.endswith("_press"):
        return LABEL_BUTTON
    if entity.domain in _OUTPUT_PLATFORMS and not entity.unique_id.endswith("_input"):
        return LABEL_OUTPUT
    return None


def apply_labels(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Give every entity of the entry the label of its kind; returns how many got one.

    Labels are only ever added, so those a person gave stay.
    """
    registry = er.async_get(hass)
    labels = lr.async_get(hass)
    ids: dict[str, str] = {}
    added = 0
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        name = _kind(entity)
        if name is None:
            continue
        if name not in ids:
            ids[name] = (labels.async_get_label_by_name(name) or labels.async_create(name)).label_id
        if ids[name] not in entity.labels:
            registry.async_update_entity(entity.entity_id, labels=entity.labels | {ids[name]})
            added += 1
    return added
