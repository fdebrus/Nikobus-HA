"""The connection, and what a motion detector reports."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from nikobus_connect.discovery import motion_detector_bus_addresses

from .entity import GatewayEntity, NikobusEntity, plate_device
from .hub import NikobusHub
from .presses import PRESSED

PARALLEL_UPDATES = 0

# What a detector sends, per sensor: the frame that turns it on, and off.
_MOTION_SENSORS = (
    ("occupancy", BinarySensorDeviceClass.OCCUPANCY, "on", "off"),
    ("light_level", BinarySensorDeviceClass.LIGHT, "light", "dark"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[NikobusHub],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The gateway's connection, and two sensors per motion detector."""
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = [NikobusConnectionSensor(hub)]
    for plate, plate_entry in hub.inventory.motion_detectors():
        frames = motion_detector_bus_addresses(plate)
        entities.extend(
            NikobusMotionSensor(hub, plate, plate_entry, key, device_class, frames[on], frames[off])
            for key, device_class, on, off in _MOTION_SENSORS
            if on in frames and off in frames
        )
    async_add_entities(entities)


class NikobusConnectionSensor(GatewayEntity, BinarySensorEntity):
    """Whether the gateway is connected."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: NikobusHub) -> None:
        super().__init__(hub, "connection")

    @property
    def available(self) -> bool:
        """Always: its state is the answer."""
        return True

    @property
    def is_on(self) -> bool:
        """Connected or not."""
        return self.hub.connected


class NikobusMotionSensor(NikobusEntity, BinarySensorEntity, RestoreEntity):
    """One of the two things a motion detector tells the bus.

    The detector cannot be asked: the state is what it last sent, kept
    over a restart, and unknown until its first frame.
    """

    def __init__(
        self,
        hub: NikobusHub,
        plate: str,
        plate_entry: dict[str, Any],
        key: str,
        device_class: BinarySensorDeviceClass,
        on_address: str,
        off_address: str,
    ) -> None:
        super().__init__(hub)
        self._frames = {on_address: True, off_address: False}
        self._attr_unique_id = f"{plate}_{key}"
        self._attr_translation_key = key
        self._attr_device_class = device_class
        self._attr_device_info = plate_device(hub, plate, plate_entry)
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Take the last state back and listen for both frames."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == STATE_ON
        for address, state in self._frames.items():
            self.async_on_remove(
                self.hub.presses.subscribe(address, lambda event_type, _data, state=state: self._on_frame(event_type, state))
            )
        self.async_on_remove(self.hub.subscribe_status(self.async_write_ha_state))

    def _on_frame(self, event_type: str, state: bool) -> None:
        if event_type == PRESSED:
            self._attr_is_on = state
            self.async_write_ha_state()
