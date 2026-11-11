"""The gateway's diagnostic sensors: discovery, clock, programming."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

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
        async_add_entities([NikobusClockDriftSensor(hub)])


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


class NikobusClockDriftSensor(GatewayEntity, SensorEntity):
    """How far the PC-Link's clock is ahead of Home Assistant's.

    The PC-Link keeps local time and does not follow summer time, so its
    calendar runs an hour off twice a year until the clock is set.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 0

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "clock_drift")

    @property
    def native_value(self) -> float | None:
        """Seconds ahead (positive) or behind; unknown until read, or when never set."""
        return self.hub.maintenance.clock_drift

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When the clock was last read."""
        read_at = self.hub.maintenance.clock_read_at
        return {"read_at": read_at.isoformat() if read_at else None}


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
