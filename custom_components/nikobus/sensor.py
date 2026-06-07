"""Sensor platform for the Nikobus integration — connection + discovery status."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_DISCOVERY_STATE
from .coordinator import NikobusConfigEntry, NikobusDataCoordinator
from .entity import hub_device_info

PARALLEL_UPDATES = 0

_CONNECTED = "connected"
_RECONNECTING = "reconnecting"
_DISCONNECTED = "disconnected"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NikobusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nikobus connection + discovery sensors."""
    coordinator: NikobusDataCoordinator = entry.runtime_data
    async_add_entities([
        NikobusConnectionSensor(coordinator),
        NikobusDiscoveryStatusSensor(coordinator),
        NikobusDiscoveryProgressSensor(coordinator),
    ])


class NikobusConnectionSensor(CoordinatorEntity[NikobusDataCoordinator], SensorEntity):
    """Sensor that exposes the live Nikobus connection status."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [_CONNECTED, _RECONNECTING, _DISCONNECTED]

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_connection_status"
        self._attr_device_info = hub_device_info()

    @property
    def native_value(self) -> str:
        """Return the current connection status."""
        return self.coordinator.connection_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        last = self.coordinator._last_connected
        return {
            "last_connected": last.isoformat() if last else None,
            "reconnect_attempts": self.coordinator._reconnect_attempts,
            "connection_string": self.coordinator.connection_string,
        }


class _DiscoverySignalEntity(SensorEntity):
    """Mixin: subscribe to the discovery state dispatcher signal."""

    _attr_should_poll = False

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_device_info = hub_device_info()

    async def async_added_to_hass(self) -> None:
        """Register dispatcher listener."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_DISCOVERY_STATE, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class NikobusDiscoveryStatusSensor(_DiscoverySignalEntity):
    """Discovery status sensor: coarse phase as the state, live detail as
    attributes.

    The **state** is the coarse phase (``idle|pc_link|module_scan|
    finished|error``) — low cardinality, so the history is a handful of
    clean transitions rather than hundreds of distinct per-register
    strings. The live line (e.g. ``"Scanning module 0E6C (2/10) —
    register 0x87 of 0xFF (145 records)"``) and the fine-grained detail
    are attributes, all in ``_unrecorded_attributes`` so the per-register
    churn during a scan never reaches the recorder.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "discovery_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Transient progress detail — visible live, never worth recording.
    _unrecorded_attributes = frozenset(
        {
            "message", "phase", "sub_phase", "current_module", "modules_done",
            "modules_total", "register_current", "registers_done",
            "registers_total", "decoded_records", "last_error",
        }
    )

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_discovery_status"

    @property
    def native_value(self) -> str:
        return self._coordinator.discovery_phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self._coordinator
        return {
            "message": c.discovery_status_message,
            "phase": c.discovery_phase,
            "sub_phase": c.discovery_sub_phase,
            "current_module": c.discovery_current_module,
            "modules_done": c.discovery_modules_done,
            "modules_total": c.discovery_modules_total,
            "register_current": c.discovery_register_current,
            "registers_done": c.discovery_registers_done,
            "registers_total": c.discovery_registers_total,
            "decoded_records": c.discovery_decoded_records,
            "last_error": c.discovery_last_error,
        }


class NikobusDiscoveryProgressSensor(_DiscoverySignalEntity):
    """Numeric sensor showing discovery progress 0-100%."""

    _attr_has_entity_name = True
    _attr_translation_key = "discovery_progress"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: NikobusDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_discovery_progress"

    @property
    def native_value(self) -> float:
        return self._coordinator.discovery_progress_percent
