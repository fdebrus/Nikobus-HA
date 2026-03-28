"""Sensor platform for the Nikobus integration — connection status."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusDataCoordinator

_CONNECTED = "connected"
_RECONNECTING = "reconnecting"
_DISCONNECTED = "disconnected"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Nikobus connection status sensor."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    async_add_entities([NikobusConnectionSensor(coordinator)])


class NikobusConnectionSensor(CoordinatorEntity[NikobusDataCoordinator], SensorEntity):
    """Sensor that exposes the live Nikobus connection status."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_connection_status"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, HUB_IDENTIFIER)},
            name="Nikobus Bridge",
            manufacturer=BRAND,
            model="PC-Link Bridge",
        )

    @property
    def native_value(self) -> str:
        """Return the current connection status."""
        return self.coordinator.connection_status

    @property
    def icon(self) -> str:
        """Return an icon that reflects the current state."""
        return {
            _CONNECTED: "mdi:lan-connect",
            _RECONNECTING: "mdi:lan-pending",
            _DISCONNECTED: "mdi:lan-disconnect",
        }.get(self.coordinator.connection_status, "mdi:lan-disconnect")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        last = self.coordinator._last_connected
        return {
            "last_connected": last.isoformat() if last else None,
            "reconnect_attempts": self.coordinator._reconnect_attempts,
            "connection_string": self.coordinator.connection_string,
        }
