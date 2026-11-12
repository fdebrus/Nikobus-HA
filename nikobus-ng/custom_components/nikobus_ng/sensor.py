"""The gateway's diagnostic sensors: discovery, clock, programming."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .discovery import PHASES
from .entity import GatewayEntity
from .hub import NikobusHub
from .maintenance import HEALTH

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The gateway's diagnostic sensors; the clock only with a PC-Link."""
    hub = entry.runtime_data
    async_add_entities([NikobusDiscoverySensor(hub), NikobusProgrammingSensor(hub)])
    if hub.maintenance.pc_link_address is not None:
        async_add_entities([NikobusClockSensor(hub)])
    # Up to 0.7.0 the clock was shown as its distance from Home Assistant's.
    registry = er.async_get(hass)
    if former := registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{entry.entry_id}_clock_drift"):
        registry.async_remove(former)


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


class NikobusClockSensor(GatewayEntity, SensorEntity):
    """The PC-Link's clock, as it stood when it was last read.

    The PC-Link keeps local time and does not follow summer time, so its
    calendar runs an hour off twice a year until the clock is set. It is
    read once an hour; how far it was off then is an attribute.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "clock")

    @property
    def native_value(self) -> datetime | None:
        """The clock at the last read; unknown until read, or when it was never set."""
        return self.hub.maintenance.clock

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """How far ahead of Home Assistant's clock it was, and when it was read."""
        maintenance = self.hub.maintenance
        read_at = maintenance.clock_read_at
        return {
            "seconds_ahead": maintenance.clock_drift,
            "read_at": read_at.isoformat() if read_at else None,
        }


class NikobusProgrammingSensor(GatewayEntity, SensorEntity):
    """The outcome of the last check of the modules' programming."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = HEALTH

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "programming")

    @property
    def available(self) -> bool:
        """The last result stays readable while disconnected."""
        return True

    @property
    def native_value(self) -> str:
        """``ok``, ``problem``, or ``unknown`` before every module was checked."""
        return self.hub.maintenance.health

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Each module's status, the modules reprogrammed since their links were read, the last backup."""
        maintenance = self.hub.maintenance
        checked_at = maintenance.checked_at
        return {
            "checked_at": checked_at.isoformat() if checked_at else None,
            "modules": {address: check.status for address, check in sorted(maintenance.checks.items())},
            "reprogrammed": list(maintenance.reprogrammed),
            "backup": maintenance.backup_path,
        }
