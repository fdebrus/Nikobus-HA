"""The discovery status."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .discovery import PHASES
from .entity import GatewayEntity
from .hub import NikobusHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The gateway's diagnostic sensor."""
    async_add_entities([NikobusDiscoverySensor(entry.runtime_data)])


class NikobusDiscoverySensor(GatewayEntity, SensorEntity):
    """Which pass of a discovery is running, and how far it is."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = PHASES

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "discovery")

    @property
    def available(self) -> bool:
        """The last status stays readable while disconnected."""
        return True

    @property
    def native_value(self) -> str:
        """The pass in progress."""
        return self.hub.discovery.status.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Progress, the module being read, and why a discovery stopped."""
        status = self.hub.discovery.status
        return {"progress": status.progress, "module": status.module, "error": status.error}
