"""The keys of the plates, as event entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from nikobus_connect.discovery import is_motion_detector

from .entity import NikobusEntity, plate_device
from .hub import NikobusHub
from .inventory import Key
from .presses import EVENT_TYPES

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One event entity per key; a motion detector gets sensors instead."""
    hub = entry.runtime_data
    async_add_entities(
        NikobusKeyEvent(hub, key)
        for key in hub.inventory.iter_keys()
        if not is_motion_detector(key.plate_entry)
    )


class NikobusKeyEvent(NikobusEntity, EventEntity):
    """A key: pressed, held, released after a short or a long press."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "key"

    def __init__(self, hub: NikobusHub, key: Key) -> None:
        super().__init__(hub)
        self._bus_address = key.bus_address
        self._attr_unique_id = f"{key.bus_address}_event"
        self._attr_translation_placeholders = {"key": key.label}
        self._attr_device_info = plate_device(hub, key.plate, key.plate_entry)
        self._attr_extra_state_attributes = {"bus_address": key.bus_address}

    async def async_added_to_hass(self) -> None:
        """Listen for the key's presses."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.presses.subscribe(self._bus_address, self._on_press))
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    def _on_press(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
